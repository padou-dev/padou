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
    ui.info(app.theme, "Answers can be wrong: review commands before you run them. See DISCLAIMER.md.")


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
        ["Folders", "anywhere" if app.folders is None else ", ".join(ui.short_path(f) for f in app.folders)],
        ["Theme", app.theme.source],
    ]
    ui.table(app.theme, "Status", ["", ""], rows, styles=["bold", app.theme.dim])


def cmd_copy(app: "App", arg: str):
    if not app.last_reply:
        ui.info(app.theme, "Nothing to copy yet.")
        return
    for tool, argv in (
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("pbcopy", ["pbcopy"]),
        ("clip.exe", ["clip.exe"]),  # Windows clipboard, reachable from WSL
    ):
        if shutil.which(tool):
            subprocess.run(argv, input=app.last_reply, text=True, check=False)
            ui.success(app.theme, f"Copied the last reply ({len(app.last_reply)} characters).")
            return
    ui.error(app.theme, "No clipboard tool found. Linux (Wayland): install wl-clipboard; X11: xclip")


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


def cmd_screenshot(app: "App", arg: str):
    from datetime import datetime
    from pathlib import Path

    pictures = Path.home() / "Pictures"
    folder = (pictures if pictures.is_dir() else Path.cwd()) / "padou"
    name = arg.strip() or f"padou-{datetime.now():%Y%m%d-%H%M%S}"
    path = ui.save_screenshot(folder / (name if name.endswith(".svg") else name + ".svg"))
    ui.success(app.theme, f"Saved {ui.short_path(path)} (everything shown since padou started)")


def cmd_folder(app: "App", arg: str):
    import os
    from pathlib import Path

    from padou import config as config_mod

    action, _, rest = arg.partition(" ")
    action, rest = action.lower(), rest.strip()
    th = app.theme

    if action in ("", "list", "ls"):
        if app.folders is None:
            ui.console.print(Text("padou may read anywhere on this system (except secret locations).", style=th.warn))
            ui.info(th, "Limit it again with /folder restrict.")
        else:
            rows = [[str(i), ui.short_path(f), "start folder" if i == 1 else ""] for i, f in enumerate(app.folders, 1)]
            ui.table(th, "Folders padou may read", ["#", "Folder", ""], rows, styles=[th.accent, "", th.dim])
            ui.info(th, "/folder add <path> [--always] · /folder remove <n> · /folder anywhere")
        return

    if action == "add":
        always = "--always" in rest.split()
        target = " ".join(w for w in rest.split() if w != "--always").strip()
        if not target:
            ui.error(th, "Usage: /folder add <path> [--always]   (Tab completes the path)")
            return
        p = Path(os.path.expanduser(target))
        p = (app.cwd / p if not p.is_absolute() else p).resolve()
        if not p.is_dir():
            ui.error(th, f"{p} is not a folder.")
            return
        if tools.is_blocked(p):
            ui.error(th, f"{ui.short_path(p)} holds secrets (keys, passwords, browser data); padou never reads it.")
            return
        if p in (Path("/"), Path.home().resolve()):
            ui.console.print(Text(f"Note: {ui.short_path(p)} gives padou access to a lot. Secret folders stay blocked.", style=th.warn))
        if app.folders is None:
            ui.info(th, "padou can already read anywhere; this folder is now remembered for /folder restrict.")
            app.folders_backup = getattr(app, "folders_backup", [app.cwd.resolve()]) + [p]
        elif p not in app.folders:
            app.folders.append(p)
        app.refresh_system_prompt()
        ui.success(th, f"padou may now read {ui.short_path(p)}" + (" (this session)" if not always else ""))
        if always:
            stored = "~/" + str(p.relative_to(Path.home())) if p.is_relative_to(Path.home()) else str(p)
            path = config_mod.add_folder_to_file(stored)
            ui.success(th, f"Saved to {ui.short_path(path)}, so it's allowed every time.")
        return

    if action in ("remove", "rm"):
        if app.folders is None:
            ui.info(th, "Access is set to anywhere. Use /folder restrict first.")
            return
        idx = None
        if rest.isdigit():
            idx = int(rest) - 1
        else:
            p = Path(os.path.expanduser(rest or ".")).resolve()
            idx = app.folders.index(p) if p in app.folders else None
        if idx is None or not 0 <= idx < len(app.folders):
            ui.error(th, "Pick a folder number from /folder.")
            return
        if idx == 0:
            ui.error(th, "The start folder can't be removed. Start padou somewhere else, or use --no-tools.")
            return
        gone = app.folders.pop(idx)
        app.refresh_system_prompt()
        ui.success(th, f"Removed {ui.short_path(gone)} (for this session; edit [tools] folders to remove it for good).")
        return

    if action == "anywhere":
        if app.folders is not None:
            app.folders_backup = list(app.folders)
            app.folders = None
            app.refresh_system_prompt()
        ui.console.print(Text("padou may now read anywhere on this system for this session. Secret locations stay blocked.", style=th.warn))
        ui.info(th, "Back to folders only: /folder restrict")
        return

    if action == "restrict":
        if app.folders is None:
            app.folders = getattr(app, "folders_backup", None) or [app.cwd.resolve()]
            app.refresh_system_prompt()
        ui.success(th, "padou may only read its allowed folders again (see /folder).")
        return

    ui.error(th, "Usage: /folder [list | add <path> [--always] | remove <n> | anywhere | restrict]")


def cmd_system(app: "App", arg: str):
    ui.markdown("```text\n" + app.session.messages[0]["content"] + "\n```")


COMMANDS: dict[str, Command] = {
    "help": Command(cmd_help, "Show commands and keys"),
    "clear": Command(cmd_clear, "Start a fresh conversation"),
    "models": Command(cmd_models, "List models on the server, or switch", "[n|name]"),
    "status": Command(cmd_status, "Model, server, context use and speed"),
    "copy": Command(cmd_copy, "Copy the last reply to the clipboard"),
    "tools": Command(cmd_tools, "List the tools padou may use"),
    "folder": Command(cmd_folder, "Folders padou may read: list, add, remove", "[add <path>]"),
    "resume": Command(cmd_resume, "List saved sessions, or continue one", "[n]"),
    "compact": Command(cmd_compact, "Summarise the conversation to free up context"),
    "doctor": Command(cmd_doctor, "Check server, model and config"),
    "system": Command(cmd_system, "Show the system prompt"),
    "screenshot": Command(cmd_screenshot, "Save the screen as an SVG image", "[name]"),
    "exit": Command(cmd_exit, "Quit padou (also Ctrl+D)"),
    # aliases
    "quit": Command(cmd_exit, "Quit padou"),
    "model": Command(cmd_models, "Alias of /models", "[n|name]"),
    "folders": Command(cmd_folder, "Alias of /folder"),
}
ALIASES = {"quit", "model", "folders"}


def dispatch(app: "App", line: str) -> str | None:
    name, _, arg = line[1:].partition(" ")
    cmd = COMMANDS.get(name.lower())
    if cmd is None:
        close = [n for n in COMMANDS if n.startswith(name.lower()[:2])] if name else []
        hint = f" Did you mean /{close[0]}?" if close else ""
        ui.console.print(Text(f"Unknown command /{name}.{hint} Type /help for the list.", style=app.theme.warn))
        return None
    return cmd.func(app, arg.strip())
