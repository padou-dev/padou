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
    if app.write_roots:
        rows.append(["edit_file", "Change one exact piece of text in a file (you approve it)"])
        rows.append(["write_file", "Create a new file (you approve it)"])
        ui.table(app.theme, "Write tools (write mode is on)", ["Tool", "What it does"], rows[-2:], styles=[app.theme.warn, ""])
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


def cmd_write(app: "App", arg: str):
    import os
    from pathlib import Path

    from padou import writes

    th = app.theme
    arg = arg.strip()
    if not arg:
        if not app.write_roots:
            ui.info(th, "Write mode is off: padou can't change any file.")
            ui.info(th, "Turn it on for one folder (and its subfolders), this session only: /write <folder>")
        else:
            rows = [[str(i), ui.short_path(r)] for i, r in enumerate(app.write_roots, 1)]
            ui.table(th, "padou may change files in", ["#", "Folder"], rows, styles=[th.warn, ""])
            ui.info(th, "Every change is shown first and needs your yes. /undo reverts · /write off stops")
        return
    if arg.lower() in ("off", "stop", "no"):
        app.write_roots.clear()
        app.refresh_system_prompt()
        ui.success(th, "Write mode is off. padou is read-only again.")
        return
    p = Path(os.path.expanduser(arg))
    if not p.is_absolute():
        p = app.cwd / p
    try:
        folder = writes.check_write_root(p, app.folders)
    except writes.WriteError as exc:
        ui.error(th, str(exc))
        return
    if folder not in app.write_roots:
        app.write_roots.append(folder)
    app.refresh_system_prompt()
    ui.console.print(Text.assemble(("✎ ", f"bold {th.warn}"), ("Write mode on for ", "bold"), (ui.short_path(folder), f"bold {th.warn}"),
                                   (" (and its subfolders), this session only.", "")))
    ui.info(th, "padou can edit existing text files and create new ones there. It can't delete, rename or run anything.")
    ui.info(th, "You'll see every change as a diff and approve it. Backups are kept: /undo, /changes. Stop with /write off.")


def cmd_undo(app: "App", arg: str):
    from padou import writes

    pending = [a for a in app.changes if not a.undone]
    if not pending:
        ui.info(app.theme, "Nothing to undo.")
        return
    last = pending[-1]
    try:
        msg = writes.undo(last, app.session.id)
    except (writes.WriteError, OSError) as exc:
        ui.error(app.theme, str(exc))
        return
    ui.success(app.theme, f"Undone: {msg}")
    app.session.messages.append({"role": "user", "content": f"(note: I undid your change to {last.change.path}; it's back as it was)"})
    app.session.messages.append({"role": "assistant", "content": "Understood."})


def cmd_changes(app: "App", arg: str):
    if not app.changes:
        ui.info(app.theme, "padou hasn't changed any files this session.")
        return
    rows = []
    for i, a in enumerate(app.changes, 1):
        added, removed = a.change.stats
        state = Text("undone", style=app.theme.dim) if a.undone else Text("applied", style=app.theme.ok)
        rows.append([str(i), a.when, "edited" if a.change.kind == "edit" else "created", ui.short_path(a.change.path), f"+{added} −{removed}", state])
    ui.table(app.theme, "Changes this session", ["#", "Time", "What", "File", "Lines", ""], rows,
             styles=[app.theme.dim, app.theme.dim, "", "bold", app.theme.dim, ""])
    ui.info(app.theme, f"Backups: {ui.short_path(writes_backup_dir(app))}  ·  /undo reverts the last one")


def writes_backup_dir(app: "App"):
    from padou import writes

    return writes.backup_dir(app.session.id)


def cmd_server(app: "App", arg: str):
    from padou import paths, server

    th, cfg = app.theme, app.cfg.server
    action = arg.strip().lower()
    local = server.is_local(cfg.url)
    if action in ("", "status"):
        up, detail = app.client.health()
        procs = server.find(cfg) if local else []
        rows = [["Address", cfg.url], ["Answering", "yes" if up else f"no ({detail})"]]
        if procs:
            for p in procs:
                mem = f"{p.rss_mb / 1024:.1f} GB RAM" if p.rss_mb and p.rss_mb > 1024 else (f"{p.rss_mb:.0f} MB RAM" if p.rss_mb else "?")
                rows.append([f"Process {p.pid}", f"{mem} · {p.command[:70]}"])
        elif local:
            rows.append(["Process", "none found for this user (it may be a system service)"])
        rows += [["Log", ui.short_path(paths.SERVER_LOG)], ["On quit", "stops it" if cfg.stop_on_exit else "keeps running"]]
        ui.table(th, "Model server", ["", ""], rows, styles=["bold", th.dim])
        ui.info(th, "/server stop · /server restart · /server log  (GPU memory isn't included in the RAM figure)")
        return
    if not local:
        ui.error(th, "That server isn't on this machine, so padou can't control it.")
        return
    if action == "log":
        text = server.tail_log(25)
        ui.console.print(Text(text or "(the log is empty: padou hasn't started a server yet)", style=th.dim))
        return
    if action in ("stop", "restart"):
        procs = server.find(cfg)
        if procs:
            stopped = server.stop(procs)
            ui.success(th, f"Stopped the model server (pid {', '.join(map(str, stopped)) or '?'}).")
        elif action == "stop":
            ui.info(th, "No model server process found for this user. If it runs as a service: sudo systemctl stop <name>")
            return
        if action == "stop":
            return
        proc, problem = server.start(cfg)
        if proc is None:
            ui.error(th, problem)
            return
        try:
            with ui.ReplyView(th, "starting the model server…"):
                up, detail = server.wait_ready(app.client, proc, cfg.startup_timeout)
        except KeyboardInterrupt:
            ui.info(th, "(stopped waiting; it keeps loading in the background)")
            return
        if up:
            app.started_pid = proc.pid
            ui.success(th, f"Model server restarted (pid {proc.pid}).")
        else:
            ui.error(th, detail)
        return
    ui.error(th, "Usage: /server [status | stop | restart | log]")


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
    "write": Command(cmd_write, "Let padou change files in one folder (you approve each change)", "[<folder>|off]"),
    "undo": Command(cmd_undo, "Undo padou's last file change"),
    "changes": Command(cmd_changes, "List the files padou changed this session"),
    "server": Command(cmd_server, "Model server: status, stop, restart, log", "[stop|restart|log]"),
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
