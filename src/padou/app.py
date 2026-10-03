"""The chat loop and the agent loop."""

from __future__ import annotations

import getpass
import os
import platform
import time
from datetime import date
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from padou import brand, commands, paths, sessions, tools, ui
from padou.client import Client, ServerError
from padou.config import Config
from padou.theme import Theme


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


class SlashCompleter(Completer):
    def __init__(self):
        self.paths = PathCompleter(only_directories=True, expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        for prefix in ("/folder add ", "/folder remove ", "/folders add "):
            if text.startswith(prefix):  # Tab-complete folder paths
                sub = Document(text[len(prefix):])
                yield from self.paths.get_completions(sub, complete_event)
                return
        if not text.startswith("/") or " " in text:
            return
        for name, cmd in commands.COMMANDS.items():
            if name.startswith(text[1:].lower()):
                yield Completion("/" + name, start_position=-len(text), display_meta=cmd.help)


class App:
    def __init__(self, cfg: Config, theme: Theme, client: Client, cwd: Path):
        self.cfg = cfg
        self.theme = theme
        self.client = client
        self.cwd = cwd
        self.model_label = cfg.server.model_label or cfg.server.model
        self.ctx_size = cfg.server.context_size
        self.ctx_used = 0
        self.last_speed: float | None = None
        self.last_reply = ""
        # folders the tools may read (None = anywhere); the start folder is always one
        self.folders: list[Path] | None = None
        if cfg.tools.scope == "folders":
            self.folders = [cwd.resolve()]
            for f in cfg.tools.folders:
                p = Path(os.path.expanduser(f)).resolve()
                if p.is_dir() and p not in self.folders:
                    self.folders.append(p)
        self.session = sessions.new(cfg.server.model, str(cwd))
        self.session.messages = [self.system_message()]
        self._last_interrupt = 0.0
        self.banner: brand.Banner | None = None  # set when the first prompt should pulse the star
        self.notes: list[tuple[str, str]] = []  # (kind, message) shown under that banner
        self._t0 = time.monotonic()

    # ------------------------------------------------------------- sessions

    def system_message(self) -> dict:
        context = (
            f"\n\nContext: user {getpass.getuser()} on {_os_name()}, "
            f"shell {os.path.basename(os.environ.get('SHELL', 'sh'))}, "
            f"current directory {self.cwd}, today is {date.today().isoformat()}."
        )
        if not self.cfg.tools.enabled:
            context += "\nTools are disabled in this session: answer from knowledge only."
        elif self.folders is None:
            context += "\nYour file tools may read anywhere on the system (except secret locations)."
        else:
            listed = ", ".join(str(f) for f in self.folders)
            context += (
                f"\nYour file tools may ONLY read inside these folders: {listed}. "
                "If the user asks about files elsewhere, tell them to allow it with /folder add <path>."
            )
        return {"role": "system", "content": self.cfg.chat.system_prompt + context}

    def refresh_system_prompt(self) -> None:
        """Tell the model about changes (like allowed folders) without a new session."""
        self.session.messages[0] = self.system_message()

    def new_session(self) -> None:
        self.session.save()
        self.session = sessions.new(self.client.model, str(self.cwd))
        self.session.messages = [self.system_message()]
        self.ctx_used = 0
        self.last_reply = ""

    def resume(self, old: sessions.Session) -> None:
        self.session.save()
        self.session = old
        if not old.messages or old.messages[0].get("role") != "system":
            old.messages.insert(0, self.system_message())
        else:
            old.messages[0] = self.system_message()  # refresh date/cwd
        self.ctx_used = 0
        ui.success(self.theme, f"Resumed: {old.title} ({old.turns} turns)")
        for m in old.messages[-4:]:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                who = "you" if m["role"] == "user" else "padou"
                text = " ".join(str(m["content"]).split())
                ui.info(self.theme, f"  {who}: {text[:110]}{'…' if len(text) > 110 else ''}")
        last = next((m for m in reversed(old.messages) if m.get("role") == "assistant" and m.get("content")), None)
        self.last_reply = last["content"] if last else ""

    # ----------------------------------------------------------- the agent

    def ask(self, user_text: str) -> None:
        messages = self.session.messages
        checkpoint = len(messages)
        messages.append({"role": "user", "content": user_text})
        tool_defs = tools.definitions(self.cfg.tools.commands) if self.cfg.tools.enabled else None
        try:
            for _ in range(self.cfg.chat.max_tool_rounds + 1):
                with ui.ReplyView(self.theme) as view:
                    reply = self.client.chat(
                        messages,
                        tools=tool_defs,
                        temperature=self.cfg.chat.temperature,
                        stream=self.cfg.chat.stream,
                        on_text=view.feed,
                    )
                self._record_stats(reply)
                if not reply.tool_calls:
                    messages.append({"role": "assistant", "content": reply.content})
                    self.last_reply = reply.content
                    if not reply.content.strip():
                        ui.info(self.theme, "(the model returned an empty reply)")
                    if reply.finish_reason == "length":
                        ui.info(self.theme, "(reply cut off: the model hit its length limit)")
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": reply.content or "",
                        "tool_calls": [
                            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                            for c in reply.tool_calls
                        ],
                    }
                )
                for call in reply.tool_calls:
                    result = tools.execute(
                        call.name,
                        call.arguments,
                        self.cwd,
                        self.cfg.tools.max_output_chars,
                        self.cfg.tools.command_timeout,
                        self.cfg.tools.commands,
                        self.folders,
                    )
                    ui.tool_call(self.theme, result.title, result.summary, result.ok, result.output, self.cfg.ui.show_tool_output)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result.output})
            else:
                ui.info(self.theme, f"(stopped after {self.cfg.chat.max_tool_rounds} rounds of tool calls)")
        except ServerError as exc:
            del messages[checkpoint:]
            ui.error(self.theme, str(exc))
            return
        except KeyboardInterrupt:
            del messages[checkpoint:]
            ui.info(self.theme, "(cancelled)")
            return
        self.session.save()
        self._context_warning()

    def _record_stats(self, reply) -> None:
        if reply.tokens_per_second:
            self.last_speed = reply.tokens_per_second
        if reply.prompt_tokens is not None:
            self.ctx_used = reply.prompt_tokens + (reply.completion_tokens or 0)
        else:  # rough estimate: about 4 characters per token
            chars = sum(len(str(m.get("content") or "")) for m in self.session.messages)
            self.ctx_used = chars // 4

    def _context_warning(self) -> None:
        if self.ctx_size and self.ctx_used > 0.8 * self.ctx_size:
            pct = int(100 * self.ctx_used / self.ctx_size)
            ui.console.print(f"[{self.theme.warn}]Context is {pct}% full. Use /compact to summarise or /clear to start over.[/]")

    def compact(self) -> None:
        msgs = self.session.messages
        if len(msgs) < 4:
            ui.info(self.theme, "Not much to compact yet.")
            return
        convo = [m for m in msgs[1:] if m.get("role") in ("user", "assistant") and m.get("content")]
        transcript = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in convo)
        request = [
            {"role": "system", "content": "You write short, factual summaries of conversations."},
            {
                "role": "user",
                "content": "Summarise this conversation in under 200 words. Keep facts, decisions, "
                "file paths, commands and open questions. No preamble.\n\n" + transcript,
            },
        ]
        try:
            with ui.ReplyView(self.theme, "compacting…") as view:
                reply = self.client.chat(request, temperature=0.2, stream=self.cfg.chat.stream, on_text=view.feed)
        except ServerError as exc:
            ui.error(self.theme, str(exc))
            return
        except KeyboardInterrupt:
            ui.info(self.theme, "(cancelled)")
            return
        before = len(msgs)
        self.session.messages = [
            msgs[0],
            {"role": "user", "content": "Summary of our conversation so far:\n" + reply.content},
            {"role": "assistant", "content": "Got it. I'll continue from that summary."},
        ]
        self.ctx_used = sum(len(str(m["content"])) for m in self.session.messages) // 4
        self.session.save()
        ui.success(self.theme, f"Compacted {before} messages into a summary.")

    # ------------------------------------------------------------ the loop

    def _toolbar(self):
        speed = f"{self.last_speed:.0f} tok/s" if self.last_speed else "– tok/s"
        pct = f" ({int(100 * self.ctx_used / self.ctx_size)}%)" if self.ctx_size and self.ctx_used else ""
        star = brand.mini(time.monotonic() - self._t0) if self.cfg.ui.animation else "✦"
        return [
            ("class:tb.accent", f" {star} {self.model_label}"),
            ("class:tb", f" · {speed} · ctx {self.ctx_used}/{self.ctx_size}{pct} · /help "),
        ]

    def _note_style(self, kind: str) -> tuple[str, str]:
        th = self.theme
        return {"error": (f"fg:{th.error}", "✗ "), "ok": (f"fg:{th.ok}", "✓ ")}.get(kind, (f"fg:{th.dim}", ""))

    def _first_message(self):
        """The first prompt carries the banner, so the star keeps pulsing while you think."""
        from prompt_toolkit.application import get_app

        t = time.monotonic() - self._t0
        try:
            done = get_app().is_done
        except Exception:
            done = False
        frame = self.banner.still() if done else self.banner.pulse((t % brand.PULSE_PERIOD) / brand.PULSE_PERIOD)
        out = ui.frame_to_ptk(frame)
        if self.notes:
            out.append(("", "\n"))
            for kind, msg in self.notes:
                style, mark = self._note_style(kind)
                out.append((style, f"{mark}{msg}\n"))
        out.append(("", "\n"))
        out.append(("class:prompt", "❯ "))
        return out

    def _record_first_screen(self) -> None:
        """Put the (still) banner into the /screenshot recording; it was drawn by prompt_toolkit."""
        import io

        real = ui.console.file
        try:
            ui.console.file = io.StringIO()
            ui.console.print(ui.frame_to_text(self.banner.still()))
            ui.console.print()
            for kind, msg in self.notes:
                {"error": ui.error, "ok": ui.success}.get(kind, ui.info)(self.theme, msg)
        finally:
            ui.console.file = real

    def run(self) -> int:
        paths.ensure_dirs()
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        style = Style.from_dict(
            {
                "prompt": f"bold {self.theme.accent}",
                "bottom-toolbar": "noreverse bg:default",
                "tb": self.theme.dim,
                "tb.accent": f"{self.theme.accent}",
                # autocomplete menu: terminal background instead of prompt_toolkit's gray
                "completion-menu": "bg:default",
                "completion-menu.completion": "bg:default fg:default",
                "completion-menu.completion.current": f"bg:default fg:{self.theme.accent} bold reverse",
                "completion-menu.meta.completion": f"bg:default fg:{self.theme.dim}",
                "completion-menu.meta.completion.current": f"bg:default fg:{self.theme.accent}",
                "scrollbar.background": "bg:default",
                "scrollbar.button": f"bg:{self.theme.dim}",
            }
        )
        prompt = PromptSession(
            history=FileHistory(str(paths.HISTORY_FILE)),
            completer=SlashCompleter(),
            complete_while_typing=True,
            key_bindings=kb,
            style=style,
            bottom_toolbar=self._toolbar,
        )
        while True:
            try:
                if self.banner is not None:
                    try:
                        line = prompt.prompt(self._first_message, refresh_interval=0.08).strip()
                    finally:
                        self._record_first_screen()
                        self.banner = None
                else:
                    ui.console.print()
                    line = prompt.prompt(
                        [("class:prompt", "❯ ")],
                        refresh_interval=0.25 if self.cfg.ui.animation else None,
                    ).strip()
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - self._last_interrupt < 2:
                    break
                self._last_interrupt = now
                ui.info(self.theme, "(press Ctrl+C again, Ctrl+D or type /exit to quit)")
                continue
            except EOFError:
                break
            if not line:
                continue
            if line.startswith("/"):
                if commands.dispatch(self, line) == commands.EXIT:
                    break
                continue
            ui.console.print()
            self.ask(line)
        self.session.save()
        ui.info(self.theme, "Goodbye!")
        return 0
