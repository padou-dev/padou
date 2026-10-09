"""Switching models from inside padou (/models).

llama.cpp's server runs one model at a time, so switching means: save the new
model in the config, restart llama-server with it, and wait while it loads (or
downloads, the first time). With Ollama, switching is instant once a model is
pulled. Any other server: padou just asks it for a different model name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from padou import compat, detect, server
from padou.config import ServerConfig

HF_FLAGS = ("-hf", "--hf-repo")


@dataclass
class Entry:
    label: str
    ident: str  # what the server calls it (llama.cpp -hf value, or Ollama tag)
    size_gb: float | None
    note: str
    downloaded: bool
    current: bool
    choice: detect.ModelChoice | None = None


# --------------------------------------------------------------------------- #
# Which kind of server is this?
# --------------------------------------------------------------------------- #


def kind(cfg: ServerConfig) -> str:
    """"llama.cpp" if padou manages a local llama-server, "ollama", or "other"."""
    try:
        argv = compat.split_command(cfg.start_command)
    except ValueError:
        argv = []
    program = compat.program_name(argv[0]) if argv else ""
    if not server.is_local(cfg.url):
        return "other"
    if program == "ollama" or ":11434" in cfg.url:
        return "ollama"
    if program == "llama-server" and any(f in argv for f in HF_FLAGS):
        return "llama.cpp"
    return "other"


def current_hf(cfg: ServerConfig) -> str:
    try:
        argv = compat.split_command(cfg.start_command)
    except ValueError:
        return ""
    for i, a in enumerate(argv):
        if a in HF_FLAGS and i + 1 < len(argv):
            return argv[i + 1]
    return ""


def with_model(start_command: str, hf: str) -> str:
    """The same start command, with its -hf model swapped."""
    argv = compat.split_command(start_command)
    for i, a in enumerate(argv):
        if a in HF_FLAGS and i + 1 < len(argv):
            argv[i + 1] = hf
            break
    else:
        argv[1:1] = ["-hf", hf]
    return compat.join_command(argv)


# --------------------------------------------------------------------------- #
# Is a llama.cpp model already downloaded?
# --------------------------------------------------------------------------- #


def _llama_cache() -> Path:
    """llama.cpp's own download folder, the same way llama.cpp works it out."""
    if os.environ.get("LLAMA_CACHE"):
        return Path(os.environ["LLAMA_CACHE"])
    home = Path.home()
    if compat.WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "llama.cpp"
    if compat.MACOS:
        return home / "Library/Caches/llama.cpp"
    return Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "llama.cpp"


def _cache_dirs() -> list[Path]:
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface"))
    dirs = [Path(os.environ.get("HF_HUB_CACHE", hf_home / "hub")), _llama_cache()]
    return [d for d in dirs if d.is_dir()]


def is_downloaded(hf: str) -> bool:
    repo, _, quant = hf.partition(":")
    quant = quant or "Q4_K_M"
    for d in _cache_dirs():
        # Hugging Face cache (newer llama.cpp): models--<org>--<repo>/snapshots/*/*.gguf
        hub = d / ("models--" + repo.replace("/", "--"))
        if hub.is_dir() and any(quant.lower() in p.name.lower() for p in hub.glob("snapshots/*/*.gguf")):
            return True
        # llama.cpp's own cache (older): <org>_<repo>_<file>.gguf
        flat = repo.replace("/", "_")
        if any(quant.lower() in p.name.lower() for p in d.glob(f"{flat}*.gguf")):
            return True
    return False


# --------------------------------------------------------------------------- #
# The list /models shows
# --------------------------------------------------------------------------- #


def entries(cfg: ServerConfig, running: list[str]) -> list[Entry]:
    k = kind(cfg)
    if k == "llama.cpp":
        cur = current_hf(cfg)
        out = [
            Entry(m.label, m.hf, m.size_gb, m.good_for, is_downloaded(m.hf), m.hf == cur or m.hf in running, m)
            for m in detect.MODELS
        ]
        if cur and not any(e.ident == cur for e in out):  # a model you configured yourself
            out.insert(0, Entry(cur.split("/")[-1], cur, None, "from your config", is_downloaded(cur), True))
        return out
    if k == "ollama":
        installed = [r.split(":latest")[0] for r in running]
        out = []
        for m in detect.MODELS:
            out.append(Entry(m.label, m.ollama, m.size_gb, m.good_for, m.ollama in installed, cfg.model in (m.ollama, m.ollama + ":latest"), m))
        for tag in installed:
            if not any(e.ident == tag for e in out):
                out.append(Entry(tag, tag, None, "installed in Ollama", True, cfg.model in (tag, tag + ":latest")))
        return out
    return [Entry(r, r, None, "offered by the server", True, r == cfg.model) for r in running]


def ollama_installed(cfg: ServerConfig) -> list[str]:
    try:
        r = httpx.get(cfg.url.rstrip("/").removesuffix("/v1") + "/api/tags", timeout=3)
        return [m.get("name", "") for m in r.json().get("models", [])] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []


def pick(items: list[Entry], arg: str) -> Entry | None:
    arg = arg.strip()
    if arg.isdigit() and 1 <= int(arg) <= len(items):
        return items[int(arg) - 1]
    low = arg.lower()
    for e in items:
        if low in (e.ident.lower(), e.label.lower()) or (e.choice and low == e.choice.key):
            return e
    return None
