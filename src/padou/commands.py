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

from padou import compat, doctor, sessions, tools, ui
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
    ui.clear(scrollback=True)
    if getattr(app, "header", None) is not None:
        ui.console.print(app.header)
        ui.console.print()
    ui.success(app.theme, "Started a fresh conversation (the previous one is saved, see /resume).")


def _show_models(app: "App", items, k: str) -> None:
    th = app.theme
    rows = []
    for i, e in enumerate(items, 1):
        size = f"{e.size_gb:g} GB" if e.size_gb else ""
        maker = e.choice.maker if e.choice else ""
        have = "✓" if e.downloaded else ""
        rows.append([str(i), ("● " if e.current else "  ") + e.label, maker, size, have, e.note])
    title = {"llama.cpp": "Models (llama.cpp)", "ollama": "Models (Ollama)"}.get(k, "Models on the server")
    ui.table(th, title, ["#", "Model", "Made by", "Size", "Have it", "Good for"], rows,
             styles=[th.dim, "bold", th.dim, th.dim, th.ok, ""])
    if k == "llama.cpp":
        ui.info(th, "● in use · ✓ downloaded. Switch with /models <number>; a new model downloads once, then starts.")
        ui.info(th, "Free the space a downloaded model uses: /models remove <number>.")
    elif k == "ollama":
        ui.info(th, "● in use · ✓ pulled. Switch with /models <number>; padou offers to pull it if needed.")
        ui.info(th, "Delete a pulled model: /models remove <number>.")
    else:
        ui.info(th, "Switch with /models <number or name>. This server is managed outside padou.")


def _remove_model(app: "App", items, k: str, arg: str) -> None:
    """/models remove <n|name>: free the disk space a downloaded model uses."""
    from rich.panel import Panel
    from rich.console import Group
    from rich.text import Text

    from padou import models, writes

    th = app.theme
    if not arg.strip():
        ui.error(th, "Which one? Type /models to see the numbers, then /models remove <number>.")
        return
    if k == "other":
        ui.error(th, "padou can only remove models that llama.cpp downloaded or that Ollama pulled. "
                     "This server is managed outside padou.")
        return
    entry = models.pick(items, arg)
    if entry is None:
        ui.error(th, f"No model '{arg.strip()}'. Type /models to see the list.")
        return
    if entry.current:
        ui.error(th, f"{entry.label} is the model you're using. Switch to another one first (/models <number>), then remove it.")
        return
    if not entry.downloaded:
        ui.info(th, f"{entry.label} isn't downloaded, so there's nothing to remove.")
        return

    if k == "ollama":
        body = [Text(f"Ollama deletes {entry.ident} for good (Ollama has no Trash).", style=th.dim),
                Text("You can pull it again later with /models.", style=th.dim)]
        ui.console.print(Panel(Group(*body), title=Text(f" ✖ Remove model {entry.label} ", style=f"bold {th.warn}"),
                               title_align="left", border_style=th.warn, padding=(0, 1)))
        if not ui.confirm("  Remove it?", default=False):
            ui.info(th, "  Kept it.")
            return
        problem = models.remove_ollama(app.cfg.server, entry.ident)
        if problem:
            ui.error(th, f"Couldn't remove it: {problem}")
        else:
            ui.success(th, f"Removed {entry.label}.")
        return

    plan = models.removal_plan(entry.ident)
    if not plan.paths:
        ui.error(th, f"Couldn't find {entry.label}'s files. They may be somewhere padou doesn't look (see /models).")
        return
    body = [Text.assemble((f"{writes.human_size(plan.size)}", "bold"), (f" in {len(plan.paths)} item" + "s" * (len(plan.paths) != 1), th.dim))]
    for p in plan.paths[:8]:
        body.append(Text("  " + ui.short_path(p), style=th.dim))
    if len(plan.paths) > 8:
        body.append(Text(f"  … and {len(plan.paths) - 8} more", style=th.dim))
    body.append(Text(f"It goes to the {compat.TRASH}. The space is freed once you empty the {compat.TRASH}.", style=th.dim))
    body.append(Text("You can download it again any time with /models.", style=th.dim))
    ui.console.print(Panel(Group(*body), title=Text(f" ✖ Remove model {entry.label} ", style=f"bold {th.warn}"),
                           title_align="left", border_style=th.warn, padding=(0, 1)))
    if not ui.confirm("  Remove it?", default=False):
        ui.info(th, "  Kept it.")
        return
    moved = 0
    for p in plan.paths:
        if not p.exists() and not p.is_symlink():
            continue  # already gone with its folder
        try:
            writes.move_to_trash(p)
            moved += 1
        except writes.WriteError as exc:
            ui.error(th, f"Stopped: {exc}")
            if moved:
                ui.info(th, f"  {moved} item(s) were already moved to the {compat.TRASH}; restore them from there if you like.")
            return
    ui.success(th, f"Removed {entry.label}: {writes.human_size(plan.size)} moved to the {compat.TRASH}. "
                   f"Empty the {compat.TRASH} to free the space.")


def _save_model(app: "App", values: dict) -> None:
    from padou import config

    try:
        config.update_section("server", values, app.cfg.source)
    except (OSError, ValueError) as exc:
        ui.error(app.theme, f"Switched for this session, but couldn't save it to the config: {exc}")


def _switch_llama(app: "App", entry) -> None:
    from padou import models, server

    th, cfg = app.theme, app.cfg.server
    if entry.current:
        ui.info(th, f"Already using {entry.label}.")
        return
    if not entry.downloaded:
        size = f" ({entry.size_gb:g} GB)" if entry.size_gb else ""
        ui.info(th, f"{entry.label} isn't downloaded yet{size}. llama-server will download it once, from Hugging Face.")
        if not ui.confirm(f"Download and switch to {entry.label}?", default=False):
            ui.info(th, "Kept the current model.")
            return
    old = (cfg.start_command, cfg.model, cfg.model_label)
    cfg.start_command = models.with_model(cfg.start_command, entry.ident)
    cfg.model, cfg.model_label = entry.ident, entry.label
    procs = server.find(cfg)
    if procs:
        server.stop(procs)
    proc, problem = server.start(cfg)
    if proc is None:
        cfg.start_command, cfg.model, cfg.model_label = old
        ui.error(th, problem)
        return
    timeout = cfg.startup_timeout if entry.downloaded else max(cfg.startup_timeout, 3600)
    status = f"loading {entry.label}…" if entry.downloaded else f"downloading {entry.label} (see /server log)…"
    try:
        with ui.ReplyView(th, status) as view:
            up, detail = server.wait_ready(app.client, proc, timeout, on_progress=lambda line: view.set_label(f"{status} {line}"))
    except KeyboardInterrupt:
        up, detail = False, "waiting"
    _save_model(app, {"model": cfg.model, "model_label": cfg.model_label, "start_command": cfg.start_command})
    app.client.model, app.model_label, app.started_pid = cfg.model, cfg.model_label, proc.pid
    if up:
        n_ctx = app.client.context_size()
        if n_ctx:
            app.ctx_size = n_ctx
        ui.success(th, f"Now using {entry.label}. Saved as your default.")
    elif detail == "waiting":
        ui.info(th, f"Stopped waiting; {entry.label} keeps loading in the background. Saved as your default.")
    else:
        ui.error(th, f"{detail}  (Saved as your default anyway; pick another with /models.)")


def _switch_ollama(app: "App", entry) -> None:
    th, cfg = app.theme, app.cfg.server
    if entry.current:
        ui.info(th, f"Already using {entry.label}.")
        return
    if not entry.downloaded:
        exe = shutil.which("ollama")
        if not exe:
            ui.error(th, f"{entry.ident} isn't pulled, and the ollama command wasn't found. Run: ollama pull {entry.ident}")
            return
        size = f" ({entry.size_gb:g} GB)" if entry.size_gb else ""
        if not ui.confirm(f"Pull {entry.ident}{size} with Ollama now?", default=False):
            ui.info(th, "Kept the current model.")
            return
        try:
            code = subprocess.run([exe, "pull", entry.ident]).returncode
        except KeyboardInterrupt:
            ui.info(th, "Pull cancelled. Kept the current model.")
            return
        if code != 0:
            ui.error(th, f"ollama pull failed (exit {code}). Is your Ollama new enough for this model?")
            return
    cfg.model, cfg.model_label = entry.ident, entry.label
    app.client.model, app.model_label = entry.ident, entry.label
    _save_model(app, {"model": cfg.model, "model_label": cfg.model_label})
    ui.success(th, f"Now using {entry.label}. Saved as your default.")


def cmd_models(app: "App", arg: str):
    from padou import models

    cfg = app.cfg.server
    k = models.kind(cfg)
    try:
        running = models.ollama_installed(cfg) if k == "ollama" else app.client.list_models()
    except ServerError as exc:
        if k == "other":
            ui.error(app.theme, str(exc))
            return
        running = []  # llama.cpp: the server may be down, and switching starts it anyway
    items = models.entries(cfg, running)
    if not arg.strip():
        _show_models(app, items, k)
        return
    words = arg.split(None, 1)
    if words[0].lower() in ("remove", "rm", "delete"):
        _remove_model(app, items, k, words[1] if len(words) > 1 else "")
        return
    entry = models.pick(items, arg)
    if entry is None:
        ui.error(app.theme, f"No model '{arg.strip()}'. Type /models to see the list.")
        return
    if k == "llama.cpp":
        _switch_llama(app, entry)
    elif k == "ollama":
        _switch_ollama(app, entry)
    else:
        app.client.model = app.model_label = entry.ident
        ui.success(app.theme, f"Now using model: {entry.ident} (this session only)")


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
    for argv in compat.clipboard_commands():
        if shutil.which(argv[0]):
            if compat.program_name(argv[0]) == "clip":
                # Windows' clip.exe reads UTF-16 when the text starts with a byte-order mark
                data = ("\ufeff" + app.last_reply).encode("utf-16-le")
            else:
                data = app.last_reply.encode("utf-8")
            subprocess.run(argv, input=data, check=False)
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
    if app.mode != "read":
        write_rows = [
            ["edit_file", "Change one exact piece of text in a file"],
            ["rewrite_file", "Replace a whole file (you see the full diff)"],
            ["write_file", "Create a new file, and any missing folders"],
            ["move_path", "Move or rename a file or folder (never overwrites)"],
            ["delete_path", f"Move a file or folder to the {compat.TRASH} (folders: you type the name)"],
        ]
        ui.table(app.theme, f"Change tools ({app.mode} mode is on; you approve every change)", ["Tool", "What it does"], write_rows,
                 styles=[app.theme.warn, ""])
    ui.info(app.theme, "Blocked for safety: SSH and GPG keys, keychains and credential stores, password stores, browser profiles, .env files, private keys.")


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


MODE_HELP = {
    "read": "padou reads files and runs approved read-only commands. It can't change anything.",
    "edit": "padou may change files in one folder and its subfolders. Every change needs your yes.",
    "admin": "padou may change files anywhere (never secrets). System files go through sudo. Deletes need the name typed.",
}


def _mode_table(app: "App") -> None:
    th = app.theme
    rows = []
    for name, text in MODE_HELP.items():
        mark = "●" if app.mode == name else " "
        extra = ""
        if name == "edit" and app.mode == "edit":
            extra = "  (" + ", ".join(ui.short_path(r) for r in app.write_roots) + ")"
        rows.append([mark, Text(name, style=f"bold {th.error if name == 'admin' else th.warn if name == 'edit' else th.accent}"),
                     text + extra])
    ui.table(th, "Modes", ["", "Mode", "What padou may do"], rows, styles=[th.accent, "", ""])
    ui.info(th, "/mode read · /mode edit [folder] · /mode admin   ·   Shift+Tab switches read ↔ edit")


def _edit_folder(app: "App", folder_text: str, quiet: bool = False):
    """The folder for edit mode, checked; or None after explaining why not (or, quiet, a short reason)."""
    import os
    from pathlib import Path

    from padou import writes

    th = app.theme
    p = Path(os.path.expanduser(folder_text or "."))
    if not p.is_absolute():
        p = app.cwd / p
    readable = app._folders_before_admin if app.mode == "admin" else app.folders
    try:
        return writes.check_write_root(p, readable)
    except writes.WriteError as exc:
        if quiet:
            return str(exc)
        ui.error(th, str(exc))
        ui.info(th, "Start padou in a project folder, or use /mode edit <folder>. /mode admin allows changes anywhere.")
        return None


def _edit_on(app: "App", folder) -> None:
    th = app.theme
    app.set_mode("edit", folder)
    ui.console.print(Text.assemble(("✎ ", f"bold {th.warn}"), ("Edit mode for ", "bold"),
                                   (", ".join(ui.short_path(r) for r in app.write_roots), f"bold {th.warn}"),
                                   (" (and subfolders), this session only.", "")))
    ui.info(th, f"padou can edit, create, move and delete (to the {compat.TRASH}) files there. Every change is shown "
                "first and needs your yes. /undo reverts · /mode read stops")


def _admin_intro_seen():
    from padou import paths

    return paths.DATA_DIR / "admin-intro-seen"


def _admin_on(app: "App") -> None:
    """Admin mode. The first time ever, a short explanation and a yes; after that, it just switches."""
    from rich.console import Group
    from rich.panel import Panel

    th = app.theme
    if app.mode == "admin":
        ui.info(th, "Admin mode is already on. /mode read (or Shift+Tab) switches it off.")
        return
    seen = _admin_intro_seen()
    if not seen.exists():
        body = Group(
            Text("padou may change files anywhere on this computer, not just one folder:", style="bold"),
            Text("  • you still see every change first and approve it; deletes need the name typed"),
            Text("  • system files (like /etc/hosts) need your sudo password: you type it into sudo, padou never sees it"),
            Text("  • /undo puts changes back, and /changes lists them"),
            Text("Keys, passwords and the core system folders stay off limits. It lasts until you leave it or quit.",
                 style=th.dim),
        )
        ui.console.print(Panel(body, title=Text(" Admin mode ", style=f"bold {th.warn}"), title_align="left",
                               border_style=th.warn, padding=(0, 1)))
        if not ui.confirm("  Switch it on?", default=False):
            ui.info(th, "  OK, the mode didn't change.")
            return
        try:
            seen.parent.mkdir(parents=True, exist_ok=True)
            seen.touch()
        except OSError:
            pass
    app.set_mode("admin")
    ui.console.print(Text.assemble(("✎ ", f"bold {th.warn}"), ("Admin mode: ", "bold"),
                                   ("padou may change files anywhere; you approve each change. ", ""),
                                   ("/mode read or Shift+Tab to leave.", th.dim)))


def cmd_mode(app: "App", arg: str):
    """read-only, edit (one folder) or admin (anywhere)."""
    th = app.theme
    action, _, rest = arg.strip().partition(" ")
    action = action.lower()
    if not action:
        _mode_table(app)
        return
    if action in ("read", "read-only", "readonly", "off"):
        was = app.mode
        app.set_mode("read")
        ui.success(th, "Read-only: padou can't change any file." if was != "read" else "Already read-only.")
        return
    if action == "edit":
        folder = _edit_folder(app, rest.strip())
        if folder is not None:
            _edit_on(app, folder)
        return
    if action == "admin":
        _admin_on(app)
        return
    ui.error(th, "Usage: /mode [read | edit [folder] | admin]")


def cycle_mode(app: "App") -> None:
    """Shift+Tab: read-only <-> edit in the start folder. Quiet: the status line shows the mode.
    Admin mode is never one keypress away."""
    if app.mode != "read":
        app.set_mode("read")
        return
    folder = _edit_folder(app, "", quiet=True)
    if isinstance(folder, str):  # can't edit here: say why, briefly, in the status line
        app.flash("edit mode isn't allowed in this folder: /mode edit <folder> or /mode admin")
        return
    app.set_mode("edit", folder)


def cmd_write(app: "App", arg: str):
    """Old name: /write is now /mode edit."""
    a = arg.strip()
    ui.info(app.theme, "/write is now /mode edit (and /mode read to stop).")
    if a.lower() in ("off", "stop", "no"):
        return cmd_mode(app, "read")
    return cmd_mode(app, f"edit {a}" if a else "")


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
    app.session.messages.append({"role": "user", "content": f"(note: I undid your last change ({last.change.kind} {last.change.path}); it's back as it was)"})
    app.session.messages.append({"role": "assistant", "content": "Understood."})


def cmd_changes(app: "App", arg: str):
    if not app.changes:
        ui.info(app.theme, "padou hasn't changed any files this session.")
        return
    rows = []
    for i, a in enumerate(app.changes, 1):
        state = Text("undone", style=app.theme.dim) if a.undone else Text("done", style=app.theme.ok)
        rows.append([str(i), a.when, ui.short_path(a.change.path), a.change.summary(), state])
    ui.table(app.theme, "Changes this session", ["#", "Time", "File or folder", "What happened", ""], rows,
             styles=[app.theme.dim, app.theme.dim, "bold", "", ""])
    from padou import writes

    ui.info(app.theme, f"Backups: {ui.short_path(writes_backup_dir(app))}  ·  /undo reverts the last one")
    ui.info(app.theme, f"Every change padou makes is also listed in {ui.short_path(writes.audit_log())}")


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
            ui.info(th, "No model server process found for this user. If it runs as a system service, stop it there.")
            return
        if action == "stop":
            return
        proc, problem = server.start(cfg)
        if proc is None:
            ui.error(th, problem)
            return
        try:
            with ui.ReplyView(th, "starting the model server…") as view:
                up, detail = server.wait_ready(app.client, proc, cfg.startup_timeout,
                                               on_progress=lambda line: view.set_label(f"starting the model server… {line}"))
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


def cmd_think(app: "App", arg: str):
    """Switch the model's thinking (reasoning before it answers) on or off for this session."""
    from padou import models

    th, cfg = app.theme, app.cfg.server
    action = arg.strip().lower()
    if action not in ("", "on", "off"):
        ui.error(th, "Usage: /think [on|off]")
        return
    if not action:
        state = "on" if app.thinking else "off"
        ui.info(th, f"Thinking is {state}. With it on, the model reasons before answering: better on hard "
                    "questions, but much slower on a laptop. /think on · /think off")
        return
    want = action == "on"
    k = models.kind(cfg)
    if want and k == "other":
        ui.info(th, "This server is managed outside padou, so padou can't switch its thinking. "
                    "It thinks (or not) the way the server is set up.")
        return
    if want and k == "llama.cpp" and models.thinking_blocked(cfg.start_command):
        ui.info(th, "Your model server starts with thinking switched off for good (--reasoning-budget 0).")
        ui.info(th, "padou can remove that from the start command, so it can switch thinking per question.")
        if not ui.confirm("Update the start command and restart the model server now?", default=False):
            ui.info(th, "Kept it as it is (thinking stays off).")
            return
        new = models.without_thinking_block(cfg.start_command)
        cfg.start_command = new
        _save_model(app, {"start_command": new})
        cmd_server(app, "restart")
    app.thinking = want
    if want:
        ui.success(th, "Thinking on for this session: slower, better for hard questions. /think off switches it back.")
        ui.info(th, "(Only some models can think, Qwen3.5 among them. Default for new sessions: thinking under [chat] in the config.)")
    else:
        ui.success(th, "Thinking off: quick, direct answers.")


def cmd_system(app: "App", arg: str):
    ui.markdown("```text\n" + app.session.messages[0]["content"] + "\n```")


def cmd_update(app: "App", arg: str):
    from padou import __version__, updates

    th = app.theme
    install = updates.detect_install()
    with ui.ReplyView(th, "checking GitHub for a newer padou…"):
        release = updates.check_now()
    newer = release is not None and updates.is_newer(release.version)
    app.update_available = release if newer else None

    if install.kind == "git":
        ui.info(th, f"padou runs from your git clone at {ui.short_path(install.folder)}; /update pulls the latest code there.")
        if newer:
            ui.success(th, f"padou {release.version} has been released (you have {__version__}).")
    elif release is None:
        ui.error(th, "Couldn't ask GitHub about new versions (offline, or no releases published yet).")
        return
    elif not newer:
        ui.success(th, f"You have the latest padou ({__version__}).")
        return
    else:
        ui.console.print(Text.assemble(("⬆ ", f"bold {th.accent}"), (f"padou {__version__} → {release.version}", "bold")))
    if newer and release.notes.strip():
        notes = release.notes.strip().splitlines()
        ui.markdown("\n".join(notes[:25]) + ("\n\n…" if len(notes) > 25 else ""))
    if newer and release.url:
        ui.info(th, release.url)

    if install.kind == "other":
        ui.info(th, "padou wasn't installed with uv, so update it the way you installed it, for example:")
        ui.console.print(Text(f"  pip install --upgrade {updates.git_tool_spec(release)}", style="bold"))
        return
    if not ui.confirm("Update now?", default=True):
        return
    if install.kind == "git":
        ui.info(th, "Running git pull. If your SSH key has a passphrase, git asks for it here. Ctrl+C cancels.")
    try:
        outcome = updates.update_git_clone(install.folder) if install.kind == "git" else updates.update_uv_tool(release)
    except KeyboardInterrupt:
        ui.info(th, "Update cancelled. Nothing was changed.")
        return
    if not outcome.ok:
        ui.error(th, outcome.message)
        return
    ui.success(th, outcome.message)
    app.update_available = None
    if outcome.after_exit:
        app.after_exit = outcome.after_exit
        ui.info(th, "Quit padou (/exit) to finish the update.")
        return
    if outcome.restart and ui.confirm("Quit now, so the next start uses the new version?", default=True):
        return EXIT


COMMANDS: dict[str, Command] = {
    "help": Command(cmd_help, "Show commands and keys"),
    "clear": Command(cmd_clear, "Clear the screen and start a fresh conversation"),
    "models": Command(cmd_models, "List models, switch to one (downloads it if needed), or remove one", "[n|remove n]"),
    "status": Command(cmd_status, "Model, server, context use and speed"),
    "copy": Command(cmd_copy, "Copy the last reply to the clipboard"),
    "tools": Command(cmd_tools, "List the tools padou may use"),
    "folder": Command(cmd_folder, "Folders padou may read: list, add, remove", "[add <path>]"),
    "mode": Command(cmd_mode, "Read-only, edit (one folder) or admin (anywhere); Shift+Tab: read ↔ edit", "[read|edit|admin]"),
    "write": Command(cmd_write, "Old name of /mode edit"),
    "undo": Command(cmd_undo, "Undo padou's last change (edit, new file, move or delete)"),
    "changes": Command(cmd_changes, "List everything padou changed this session"),
    "server": Command(cmd_server, "Model server: status, stop, restart, log", "[stop|restart|log]"),
    "update": Command(cmd_update, "Check for a newer padou and install it"),
    "resume": Command(cmd_resume, "List saved sessions, or continue one", "[n]"),
    "compact": Command(cmd_compact, "Summarise the conversation to free up context"),
    "think": Command(cmd_think, "Let the model reason before answering (slower, smarter)", "[on|off]"),
    "doctor": Command(cmd_doctor, "Check server, model and config"),
    "system": Command(cmd_system, "Show the system prompt"),
    "screenshot": Command(cmd_screenshot, "Save the screen as an SVG image", "[name]"),
    "exit": Command(cmd_exit, "Quit padou (also Ctrl+D)"),
    # aliases
    "quit": Command(cmd_exit, "Quit padou"),
    "folders": Command(cmd_folder, "Alias of /folder"),
}
ALIASES = {"quit", "folders", "write"}  # still work when typed, but hidden from /help and Tab


def dispatch(app: "App", line: str) -> str | None:
    name, _, arg = line[1:].partition(" ")
    cmd = COMMANDS.get(name.lower())
    if cmd is None:
        close = [n for n in COMMANDS if n.startswith(name.lower()[:2])] if name else []
        hint = f" Did you mean /{close[0]}?" if close else ""
        ui.console.print(Text(f"Unknown command /{name}.{hint} Type /help for the list.", style=app.theme.warn))
        return None
    return cmd.func(app, arg.strip())
