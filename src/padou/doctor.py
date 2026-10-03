"""`padou doctor`: check that everything padou needs is in place."""

from __future__ import annotations

import os
import shutil
import sys

from rich.text import Text

from padou import paths, ui
from padou.client import Client, ServerError
from padou.config import Config
from padou.theme import Theme


def run(cfg: Config, theme: Theme, client: Client) -> bool:
    checks: list[tuple[str, bool | None, str]] = []  # None = just information

    checks.append(("Python", sys.version_info >= (3, 11), sys.version.split()[0]))

    if cfg.source:
        checks.append(("Config file", not cfg.warnings, f"{ui.short_path(cfg.source)}" + (f" ({len(cfg.warnings)} problems)" if cfg.warnings else "")))
    else:
        checks.append(("Config file", None, "none, using defaults (create one: padou setup)"))
    for w in cfg.warnings:
        checks.append(("  config", False, w))

    up, detail = client.health()
    checks.append(("Model server", up, f"{client.base}: {detail}"))
    if up:
        try:
            models = client.list_models()
            checks.append(("Models offered", bool(models), ", ".join(models[:4]) or "none"))
        except ServerError as exc:
            checks.append(("Models offered", False, str(exc)))
        n_ctx = client.context_size()
        checks.append(("Context window", None, f"{n_ctx} tokens (from server)" if n_ctx else f"{cfg.server.context_size} tokens (from config)"))
        try:
            reply = client.chat([{"role": "user", "content": "Reply with the single word: ready"}], stream=False, temperature=0)
            speed = f", {reply.tokens_per_second:.1f} tok/s" if reply.tokens_per_second else ""
            checks.append(("Test reply", bool(reply.content.strip()), f"{reply.content.strip()[:40]!r} in {reply.elapsed:.1f}s{speed}"))
        except ServerError as exc:
            checks.append(("Test reply", False, str(exc)))
    else:
        if cfg.server.autostart:
            checks.append(("Autostart", None, "on: running `padou` will start llama-server for you"))
        checks.append(("Start command", None, cfg.server.start_command))

    checks.append(("Theme", None, theme.source))
    try:
        paths.ensure_dirs()
        writable = os.access(paths.SESSIONS_DIR, os.W_OK)
        checks.append(("Sessions folder", writable, ui.short_path(paths.SESSIONS_DIR)))
    except OSError as exc:
        checks.append(("Sessions folder", False, str(exc)))
    clip = next((c for c in ("wl-copy", "xclip", "pbcopy") if shutil.which(c)), None)
    checks.append(("Clipboard (/copy)", bool(clip), clip or "install wl-clipboard"))

    rows = []
    all_ok = True
    for name, ok, detail in checks:
        if ok is None:
            mark = Text("·", style=theme.dim)
        elif ok:
            mark = Text("✓", style=f"bold {theme.ok}")
        else:
            mark = Text("✗", style=f"bold {theme.error}")
            all_ok = False
        rows.append([mark, name, Text(detail, style=theme.dim)])
    ui.table(theme, "padou doctor", ["", "Check", "Details"], rows)
    return all_ok
