"""Talking to an OpenAI-compatible server (llama-server, Ollama, LM Studio...)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

import httpx

from padou.config import ServerConfig


class ServerError(Exception):
    """A problem reaching or using the model server, with a human message."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, as the model wrote it


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_second: float | None = None
    elapsed: float = 0.0
    stopped: bool = False  # the user pressed Esc: content is what arrived until then


_TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def text_tool_calls(content: str) -> tuple[str, list[ToolCall]]:
    """Small models sometimes write a tool call as text (<tool_call>{...}</tool_call>) instead of
    making a real one. Turn those into real calls; returns the remaining text and the calls."""
    calls = []
    for i, m in enumerate(_TEXT_CALL.finditer(content or "")):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name:
            continue
        args = data.get("arguments", data.get("parameters", {}))
        calls.append(ToolCall(f"text_call_{i}", name, args if isinstance(args, str) else json.dumps(args)))
    if not calls:
        return content, []
    return _TEXT_CALL.sub("", content).strip(), calls


class Client:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.base = cfg.url.rstrip("/")
        if self.base.endswith("/v1"):
            self.base = self.base[: -len("/v1")]
        self.model = cfg.model
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        self.http = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(cfg.timeout, connect=5.0),
        )

    # ------------------------------------------------------------------ info

    def health(self) -> tuple[bool, str]:
        """Is the server up? llama-server has /health; others answer /v1/models."""
        try:
            r = self.http.get(f"{self.base}/health", timeout=3)
            if r.status_code == 200:
                return True, "ok"
            if r.status_code == 503:
                return False, "server is up but still loading the model"
        except httpx.HTTPError:
            pass
        try:
            r = self.http.get(f"{self.base}/v1/models", timeout=3)
            return (r.status_code == 200), f"HTTP {r.status_code}"
        except httpx.ConnectError:
            return False, f"nothing is listening at {self.base}"
        except httpx.HTTPError as exc:
            return False, type(exc).__name__

    def list_models(self) -> list[str]:
        try:
            r = self.http.get(f"{self.base}/v1/models", timeout=5)
            r.raise_for_status()
            return [m.get("id", "?") for m in r.json().get("data", [])]
        except httpx.HTTPError as exc:
            raise ServerError(self._explain(exc)) from None

    def context_size(self) -> int | None:
        """llama-server reports its real context window on /props."""
        try:
            r = self.http.get(f"{self.base}/props", timeout=3)
            if r.status_code != 200:
                return None
            data = r.json()
            n = data.get("default_generation_settings", {}).get("n_ctx") or data.get("n_ctx")
            return int(n) if n else None
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ chat

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        stream: bool = True,
        on_text: Callable[[str], None] | None = None,
        extra: dict | None = None,
        on_progress: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Reply:
        """Send the conversation, return the reply.

        extra: server-specific fields (thinking on/off, prompt cache).
        on_progress("thinking" | "tool"): the model is reasoning or writing a tool call.
        should_stop(): checked as the reply streams in; True stops it (Esc).
        """
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools
        if extra:
            body.update(extra)
        started = time.monotonic()
        try:
            if stream:
                body["stream"] = True
                body["stream_options"] = {"include_usage": True}
                reply = self._stream(body, on_text, on_progress, should_stop)
            else:
                r = self.http.post(f"{self.base}/v1/chat/completions", json=body)
                r.raise_for_status()
                reply = self._parse_full(r.json())
                if on_text and reply.content:
                    on_text(reply.content)
        except httpx.HTTPError as exc:
            raise ServerError(self._explain(exc)) from None
        reply.elapsed = time.monotonic() - started
        if reply.tokens_per_second is None and reply.completion_tokens and reply.elapsed:
            reply.tokens_per_second = reply.completion_tokens / reply.elapsed
        return reply

    def _stream(self, body: dict, on_text, on_progress=None, should_stop=None) -> Reply:
        reply = Reply()
        calls: dict[int, dict] = {}
        with self.http.stream("POST", f"{self.base}/v1/chat/completions", json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            for chunk in self._events(r.iter_lines()):
                if should_stop is not None and should_stop():
                    reply.stopped = True
                    reply.finish_reason = "stopped"
                    calls.clear()  # a half-written tool call is no use
                    break  # leaving the `with` closes the connection; the server stops generating
                self._read_usage(chunk, reply)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    # thinking models stream their reasoning separately (llama.cpp: reasoning_content,
                    # Ollama: reasoning); it isn't shown or kept, only reported as progress
                    if (delta.get("reasoning_content") or delta.get("reasoning")) and on_progress:
                        on_progress("thinking")
                    text = delta.get("content")
                    if text:
                        reply.content += text
                        if on_text:
                            on_text(text)
                    if delta.get("tool_calls") and on_progress:
                        on_progress("tool")
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        reply.finish_reason = choice["finish_reason"]
        for i in sorted(calls):
            c = calls[i]
            if c["name"]:
                reply.tool_calls.append(ToolCall(c["id"] or f"call_{i}", c["name"], c["arguments"] or "{}"))
        return reply

    @staticmethod
    def _events(lines: Iterator[str]) -> Iterator[dict]:
        for line in lines:
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue

    @staticmethod
    def _read_usage(chunk: dict, reply: Reply) -> None:
        usage = chunk.get("usage") or {}
        if usage.get("prompt_tokens") is not None:
            reply.prompt_tokens = usage["prompt_tokens"]
        if usage.get("completion_tokens") is not None:
            reply.completion_tokens = usage["completion_tokens"]
        timings = chunk.get("timings") or {}
        if timings.get("predicted_per_second"):
            reply.tokens_per_second = float(timings["predicted_per_second"])

    def _parse_full(self, data: dict) -> Reply:
        reply = Reply()
        self._read_usage(data, reply)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        reply.content = msg.get("content") or ""
        reply.finish_reason = choice.get("finish_reason")
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments", "{}")
            if not isinstance(args, str):
                args = json.dumps(args)
            reply.tool_calls.append(ToolCall(tc.get("id") or f"call_{i}", fn.get("name", ""), args))
        return reply

    def _explain(self, exc: httpx.HTTPError) -> str:
        if isinstance(exc, httpx.ConnectError):
            return f"Can't reach the model server at {self.base}. Is llama-server running? (try: padou doctor)"
        if isinstance(exc, httpx.TimeoutException):
            return f"The server took longer than {self.cfg.timeout:.0f}s to answer. Raise [server] timeout in the config."
        if isinstance(exc, httpx.HTTPStatusError):
            detail = ""
            try:
                detail = exc.response.json().get("error", {}).get("message", "")
            except Exception:
                detail = exc.response.text[:300] if exc.response is not None else ""
            hint = ""
            if "tool" in detail.lower() or "jinja" in detail.lower():
                hint = " (tool calling needs llama-server started with --jinja)"
            return f"Server error {exc.response.status_code}: {detail or 'no details'}{hint}"
        return f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        self.http.close()
