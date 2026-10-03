"""Slash commands: a dictionary that maps each name to a function.

Adding a command means writing one function and adding one entry to COMMANDS.
Every command function receives the running App and whatever text followed the
command name (e.g. "/resume 2" -> argument "2").
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from rich.text import Text

from padou import doctor, sessions, tools, ui
from padou.client import ServerError

if TYPE_CHECKING:
    from padou.app import App

EXIT = "exit"


@dataclass
class Command:
    func: Callable[["App", str], str | None]
    help: str
    usage: str = ""


def cmd_help(app: "App", arg: str):
    rows = [[f"/{name}" + (f" {c.usage}" if c.usage else ""), c.help] for name, c in COMMANDS.items() if name not in ALIASES]
    ui.table(app.theme, "Commands", ["Command", "What it does"], rows, styles=[app.theme.accent, ""])
    ui.info(app.theme, "Keys: Enter send · Alt+Enter new line · ↑/↓ history · Tab complete · Ctrl+C cancel · Ctrl+D quit")


def cmd_exit(app: "App", arg: str):
    return EXIT


def cmd_clear(app: "App", arg: str):
    app.new_session()
    ui.success(app.theme, "Started a fresh conversation (the previous one is saved, see /resume).")


def cmd_models(app: "App", arg: str):
    try:
        models = app.client.list_models()
    except ServerError as exc:
        ui.error(app.theme, str(exc))
        return
    if arg:
        choice = arg.strip()
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            choice = models[int(choice) - 1]
        app.client.model = choice
        app.model_label = choice
        ui.success(app.theme, f"Now using model: {choice}")
        if len(models) == 1 and choice != models[0]:
            ui.info(app.theme, "Note: this server offers only one model; llama-server answers with whatever it has loaded.")
        return
    rows = [[str(i), m, "●" if m == app.client.model else ""] for i, m in enumerate(models, 1)]
    ui.table(app.theme, "Models on the server", ["#", "Model", "In use"], rows, styles=[app.theme.dim, "", app.theme.accent])
    ui.info(app.theme, "Switch with /models <number or name>. llama-server serves one model at a time; Ollama can switch freely.")


def cmd_status(app: "App", arg: str):
    up, detail = app.client.health()
    speed = f"{app.last_speed:.1f} tok/s" if app.last_speed else "–"
    rows = [
        ["Model", f"{app.model_label} ({app.client.model})"],
        ["Server", f"{app.client.base} · {'up' if up else 'down'} ({detail})"],
        ["Context", f"{app.ctx_used} / {app.ctx_size} tokens"],
        ["Last speed", speed],
        ["Messages", str(len(app.session.messages))],
        ["Session", app.session.id],
        ["Tools", "on" if app.cfg.tools.enabled else "off"],
        ["Theme", app.theme.source],
    ]
    ui.table(app.theme, "Status", ["", ""], rows, styles=["bold", app.theme.dim])


def cmd_copy(app: "App", arg: str):
    if not app.last_reply:
        ui.info(app.theme, "Nothing to copy yet.")
        return
    for tool, argv in (("wl-copy", ["wl-copy"]), ("xclip", ["xclip", "-selection", "clipboard"]), ("pbcopy", ["pbcopy"])):
        if shutil.which(tool):
            subprocess.run(argv, input=app.last_reply, text=True, check=False)
            ui.success(app.theme, f"Copied the last reply ({len(app.last_reply)} characters).")
            return
    ui.error(app.theme, "No clipboard tool found. On Arch: sudo pacman -S wl-clipboard")


def cmd_tools(app: "App", arg: str):
    if not app.cfg.tools.enabled:
        ui.info(app.theme, "Tools are disabled in the config ([tools] enabled = false).")
        return
    rows = [
        ["read_file", "Read a text file"],
        ["list_dir", "List a directory"],
        ["search", "Search text in files"],
    ]
    if app.cfg.tools.commands:
        for c in tools.APPROVED_COMMANDS.values():
            rows.append([f"run: {c.name}", c.description + (f"  [{c.arg_name}]" if c.arg_name else "")])
    ui.table(app.theme, "Read-only tools", ["Tool", "What it does"], rows, styles=[app.theme.accent, ""])
    ui.info(app.theme, "Blocked for safety: ~/.ssh, ~/.gnupg, keyrings, password stores, browser profiles, .env files, private keys.")


def cmd_resume(app: "App", arg: str):
    found = [s for s in sessions.recent(15) if s.id != app.session.id]
    if not found:
        ui.info(app.theme, "No saved sessions yet.")
        return
    if not arg:
        rows = [[str(i), s.created.replace("T", " ")[:16], str(s.turns), s.title] for i, s in enumerate(found, 1)]
        ui.table(app.theme, "Saved sessions", ["#", "Started", "Turns", "First message"], rows, styles=[app.theme.accent, app.theme.dim, app.theme.dim, ""])
        ui.info(app.theme, "Resume one with /resume <number>.")
        return
    if not arg.strip().isdigit() or not 1 <= int(arg) <= len(found):
        ui.error(app.theme, f"Pick a number from 1 to {len(found)}.")
        return
    app.resume(found[int(arg) - 1])


def cmd_doctor(app: "App", arg: str):
    doctor.run(app.cfg, app.theme, app.client)


def cmd_compact(app: "App", arg: str):
    app.compact()


def cmd_system(app: "App", arg: str):
    ui.markdown("```text\n" + app.session.messages[0]["content"] + "\n```")


COMMANDS: dict[str, Command] = {
    "help": Command(cmd_help, "Show commands and keys"),
    "clear": Command(cmd_clear, "Start a fresh conversation"),
    "models": Command(cmd_models, "List models on the server, or switch", "[n|name]"),
    "status": Command(cmd_status, "Model, server, context use and speed"),
    "copy": Command(cmd_copy, "Copy the last reply to the clipboard"),
    "tools": Command(cmd_tools, "List the tools padou may use"),
    "resume": Command(cmd_resume, "List saved sessions, or continue one", "[n]"),
    "compact": Command(cmd_compact, "Summarise the conversation to free up context"),
    "doctor": Command(cmd_doctor, "Check server, model and config"),
    "system": Command(cmd_system, "Show the system prompt"),
    "exit": Command(cmd_exit, "Quit padou (also Ctrl+D)"),
    # aliases
    "quit": Command(cmd_exit, "Quit padou"),
    "model": Command(cmd_models, "Alias of /models", "[n|name]"),
}
ALIASES = {"quit", "model"}


def dispatch(app: "App", line: str) -> str | None:
    name, _, arg = line[1:].partition(" ")
    cmd = COMMANDS.get(name.lower())
    if cmd is None:
        close = [n for n in COMMANDS if n.startswith(name.lower()[:2])] if name else []
        hint = f" Did you mean /{close[0]}?" if close else ""
        ui.console.print(Text(f"Unknown command /{name}.{hint} Type /help for the list.", style=app.theme.warn))
        return None
    return cmd.func(app, arg.strip())
