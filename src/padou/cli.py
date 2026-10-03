"""Command-line entry point: `padou`, `padou doctor`, `padou --version`..."""

from __future__ import annotations

import argparse
from pathlib import Path

from padou import __version__, config, detect, doctor, paths, server, sessions, theme, ui
from padou import setup as setup_wizard
from padou.app import App
from padou.client import Client
from rich.text import Text


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="padou", description="A lightweight, local-first AI assistant for your terminal.")
    p.add_argument("command", nargs="?", choices=["chat", "doctor", "setup"], default="chat", help="chat (default), doctor or setup")
    p.add_argument("-V", "--version", action="version", version=f"padou {__version__} · παντού")
    p.add_argument("-c", "--continue", dest="resume_last", action="store_true", help="continue the most recent session")
    p.add_argument("--compact", action="store_true", help="one-line banner")
    p.add_argument("--no-tools", action="store_true", help="disable all tools for this run")
    p.add_argument("-y", "--yes", action="store_true", help="setup: accept the recommended choices")
    p.add_argument("--no-autostart", action="store_true", help="don't start llama-server automatically")
    p.add_argument("--url", help="model server URL (overrides the config)")
    p.add_argument("--model", help="model name to request (overrides the config)")
    p.add_argument("--config", type=Path, help="use a different config file")
    p.add_argument("--init-config", action="store_true", help="write an example config to ~/.config/padou/config.toml")
    return p.parse_args(argv)


NOTICE_FILE = paths.DATA_DIR / "notice-accepted"

NOTICE = """padou is free, open-source software provided AS IS, without warranty of any kind.

• AI answers can be wrong, even when they sound confident. Review every
  command before you run it: you are responsible for what you run.
• Don't rely on padou for medical, legal, financial or safety decisions.
• Its tools are designed to be read-only and to skip secret locations,
  but that is a safeguard, not a guarantee.
• Models and model servers are third-party software with their own licences.

Full text: DISCLAIMER.md in the project, or https://github.com/padou-dev/padou"""


def _accept_notice() -> bool:
    """Show the disclaimer once; returns False if the user declines."""
    if NOTICE_FILE.exists():
        return True
    th = theme.load()
    ui.configure(th)
    from rich.panel import Panel
    from rich.prompt import Confirm

    ui.console.print(Panel(Text(NOTICE), title="Before you start", border_style=th.accent, padding=(1, 2)))
    if detect.is_tty():
        try:
            ok = Confirm.ask("I understand and accept", default=True, console=ui.console)
        except (KeyboardInterrupt, EOFError):
            ok = False
        if not ok:
            ui.info(th, "No problem. padou won't start until the notice is accepted.")
            return False
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOTICE_FILE.write_text("accepted\n", encoding="utf-8")
    return True


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.init_config:
        try:
            path = config.write_example(args.config)
            print(f"Wrote {path}")
            return 0
        except FileExistsError as exc:
            print(f"{exc} already exists; not overwriting.")
            return 1

    if args.command in ("chat", "setup") and not _accept_notice():
        return 1

    if args.command == "setup":
        th = theme.load()
        ui.configure(th)
        setup_wizard.run(th, auto=args.yes)
        return 0

    first_run = args.command == "chat" and args.config is None and not paths.CONFIG_FILE.exists()
    if first_run and detect.is_tty() and not args.url:
        th = theme.load()
        ui.configure(th)
        ui.clear()
        ui.info(th, "First run: let's set padou up. (Skip this any time with Ctrl+C; re-run with `padou setup`.)\n")
        try:
            setup_wizard.run(th)
        except (KeyboardInterrupt, EOFError):
            ui.info(th, "\nSetup skipped; using the defaults.")
        try:
            ui.console.input(Text("\nPress Enter to start padou…", style=th.dim))
        except (KeyboardInterrupt, EOFError):
            return 0

    cfg = config.load(args.config)
    if args.url:
        cfg.server.url = args.url
    if args.model:
        cfg.server.model = cfg.server.model_label = args.model
    if args.no_tools:
        cfg.tools.enabled = False
    if args.no_autostart:
        cfg.server.autostart = False
    th = theme.load(cfg.ui.accent)
    ui.configure(th)
    client = Client(cfg.server)
    try:
        if args.command == "doctor":
            return 0 if doctor.run(cfg, th, client) else 1

        cwd = Path.cwd()
        app = App(cfg, th, client, cwd)
        if cfg.ui.clear_on_start:
            ui.clear()
        up, detail = ui.show_banner(th, app.model_label, cwd, args.compact or cfg.ui.banner == "compact", client.health)
        for w in cfg.warnings:
            ui.error(th, f"config: {w}")
        if not up and cfg.server.autostart and server.is_local(cfg.server.url) and "nothing is listening" in detail:
            up, detail = _autostart(cfg, th, client)
        if up:
            n_ctx = client.context_size()
            if n_ctx:
                app.ctx_size = n_ctx
        else:
            ui.error(th, f"Model server not ready ({detail}). Start llama-server, then just type; run /doctor for details.")
        if args.resume_last:
            last = sessions.recent(1)
            if last:
                app.resume(last[0])
            else:
                ui.info(th, "No previous session to continue.")
        ui.info(th, "Type a message, or /help for commands.")
        return app.run()
    finally:
        client.close()


def _autostart(cfg, th, client) -> tuple[bool, str]:
    proc, problem = server.start(cfg.server)
    if proc is None:
        ui.error(th, f"Couldn't start llama-server: {problem}")
        return False, problem
    try:
        with ui.ReplyView(th, "starting llama-server… (first run downloads the model)"):
            up, detail = server.wait_ready(client, proc, cfg.server.startup_timeout)
    except KeyboardInterrupt:
        ui.info(th, "(stopped waiting; llama-server keeps loading in the background)")
        return False, "still starting"
    if up:
        ui.success(th, f"llama-server started (pid {proc.pid}). It keeps running after padou exits.")
    else:
        ui.error(th, detail)
        log = server.tail_log()
        if log:
            ui.info(th, log)
    return up, detail
