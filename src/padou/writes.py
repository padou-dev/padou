"""Write mode: the only part of padou that can change files.

Every rule about writing lives here:

* off by default; `/write <folder>` turns it on for that folder (and its
  subfolders) for this session only, and never for /, your home folder,
  system folders, hidden folders or secret locations;
* two small tools: `edit_file` (replace one exact piece of text) and
  `write_file` (create a NEW file); nothing can delete, rename, chmod or run;
* every change is shown as a diff and needs a "y" from the user;
* every change is backed up first, so `/undo` can put it back.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from padou import paths, tools

MAX_FILE_BYTES = 300_000  # don't let a small model rewrite something huge
MAX_NEW_FILE_BYTES = 100_000

SYSTEM_DIRS = [Path(p) for p in (
    "/bin", "/boot", "/dev", "/efi", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/proc",
    "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var",
)]


class WriteError(Exception):
    """A write that padou refuses; the message is shown to the model and the user."""


# --------------------------------------------------------------------------- #
# Which folders may be written
# --------------------------------------------------------------------------- #


def check_write_root(folder: Path, readable: list[Path] | None) -> Path:
    """Validate a folder for `/write`. Returns it resolved, or raises WriteError."""
    folder = folder.expanduser()
    if not folder.is_absolute():
        raise WriteError("use an absolute path or one relative to where padou started")
    folder = folder.resolve()
    home = Path.home().resolve()
    if not folder.is_dir():
        raise WriteError(f"{folder} is not a folder")
    if folder == Path("/") or folder == home:
        raise WriteError("writing to / or your whole home folder is never allowed; pick a project folder")
    for sys_dir in SYSTEM_DIRS:
        if folder == sys_dir or folder.is_relative_to(sys_dir):
            raise WriteError(f"{folder} is a system folder; padou never writes there")
    rel = folder.relative_to(home) if folder.is_relative_to(home) else folder
    if any(part.startswith(".") for part in rel.parts):
        raise WriteError(f"{folder} is (inside) a hidden folder such as ~/.config; padou never writes there")
    if tools.is_blocked(folder):
        raise WriteError(f"{folder} may hold secrets; padou never writes there")
    if readable is not None and not tools.inside(folder, readable):
        raise WriteError(f"{folder} isn't one of the folders padou may read. Allow it first: /folder add {folder}")
    return folder


def _check_target(path: Path, write_roots: list[Path]) -> None:
    if not write_roots:
        raise WriteError("write mode is off. The user can turn it on with /write <folder>")
    if path.is_symlink():
        raise WriteError(f"{path} is a symlink; padou doesn't write through links")
    real = path.resolve()
    if not any(real == r or real.is_relative_to(r) for r in write_roots):
        allowed = ", ".join(str(r) for r in write_roots)
        raise WriteError(f"{real} is outside the write folders ({allowed})")
    if ".git" in real.parts:
        raise WriteError("padou never writes inside .git")
    if tools.is_blocked(real):
        raise WriteError(f"{real} may hold secrets; padou never writes it")


# --------------------------------------------------------------------------- #
# Proposed changes
# --------------------------------------------------------------------------- #


@dataclass
class Change:
    kind: str  # "edit" or "create"
    path: Path
    old: str  # current content ("" for a new file)
    new: str  # content after the change
    reason: str = ""  # the model's short explanation, if it gave one

    def diff(self, context: int = 3) -> list[str]:
        return list(
            difflib.unified_diff(
                self.old.splitlines(),
                self.new.splitlines(),
                fromfile=str(self.path) if self.kind == "edit" else "/dev/null",
                tofile=str(self.path),
                n=context,
                lineterm="",
            )
        )

    @property
    def stats(self) -> tuple[int, int]:
        added = sum(1 for l in self.diff() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in self.diff() if l.startswith("-") and not l.startswith("---"))
        return added, removed


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise WriteError(f"{path} is too big for padou to edit safely ({len(data) // 1024} KB)")
    if b"\x00" in data[:4096]:
        raise WriteError(f"{path} looks like a binary file; padou only edits text")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise WriteError(f"{path} isn't UTF-8 text; padou won't risk editing it") from None


def propose(name: str, raw_args: str | dict, cwd: Path, write_roots: list[Path]) -> Change:
    """Turn a tool call into a Change (nothing is written yet)."""
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            raise WriteError("the tool arguments weren't valid JSON") from None
    path_text = args.get("path", "")
    if not isinstance(path_text, str) or not path_text.strip():
        raise WriteError("a path is required")
    p = Path(os.path.expanduser(path_text.strip()))
    path = p if p.is_absolute() else cwd / p
    path = path.parent.resolve() / path.name  # resolve the folder, keep the file name as given
    _check_target(path, write_roots)
    reason = str(args.get("reason", ""))[:200]

    if name == "edit_file":
        old_text, new_text = args.get("old_text"), args.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise WriteError("edit_file needs old_text and new_text")
        if not path.is_file():
            raise WriteError(f"{path} doesn't exist; use write_file to create a new file")
        content = _read_text(path)
        if not old_text:
            raise WriteError("old_text can't be empty; quote the exact lines to change")
        count = content.count(old_text)
        if count == 0:
            raise WriteError("old_text wasn't found in the file. Read the file again and copy the text exactly")
        if count > 1:
            raise WriteError(f"old_text appears {count} times; include more surrounding lines so it's unique")
        if old_text == new_text:
            raise WriteError("old_text and new_text are the same; nothing to change")
        return Change("edit", path, content, content.replace(old_text, new_text, 1), reason)

    if name == "write_file":
        content = args.get("content")
        if not isinstance(content, str):
            raise WriteError("write_file needs content")
        if path.exists():
            raise WriteError(f"{path} already exists; use edit_file to change it (write_file only creates new files)")
        if not path.parent.is_dir():
            raise WriteError(f"the folder {path.parent} doesn't exist")
        if len(content.encode()) > MAX_NEW_FILE_BYTES:
            raise WriteError("that new file is too big for padou to write in one go")
        return Change("create", path, "", content, reason)

    raise WriteError(f"unknown write tool {name!r}")


# --------------------------------------------------------------------------- #
# Applying, backups and undo
# --------------------------------------------------------------------------- #


@dataclass
class Applied:
    change: Change
    backup: Path | None  # the previous version (None for a created file)
    when: str
    undone: bool = False


def backup_dir(session_id: str) -> Path:
    return paths.STATE_DIR / "backups" / session_id


def apply(change: Change, session_id: str) -> Applied:
    # re-check: the file might have changed while the user was reading the diff
    if change.kind == "edit":
        if _read_text(change.path) != change.old:
            raise WriteError(f"{change.path} changed while you were deciding; nothing was written")
    elif change.path.exists():
        raise WriteError(f"{change.path} appeared in the meantime; nothing was written")
    stamp = datetime.now().strftime("%H%M%S-%f")
    backup = None
    if change.kind == "edit":
        folder = backup_dir(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / f"{stamp}-{change.path.name}"
        shutil.copy2(change.path, backup)  # keeps the file's permissions and times
    tmp = change.path.with_name(f".{change.path.name}.padou-tmp")
    tmp.write_text(change.new, encoding="utf-8")
    if backup is not None:
        shutil.copymode(backup, tmp)
    tmp.replace(change.path)  # atomic: the file is never half-written
    return Applied(change, backup, datetime.now().strftime("%H:%M:%S"))


def undo(applied: Applied, session_id: str) -> str:
    """Put a file back the way it was. Never deletes: a created file is moved to the backups."""
    c = applied.change
    if applied.undone:
        raise WriteError("that change was already undone")
    if c.kind == "edit":
        if applied.backup is None or not applied.backup.exists():
            raise WriteError("the backup for that change is missing")
        shutil.copy2(applied.backup, c.path)
        applied.undone = True
        return f"restored {c.path}"
    if not c.path.exists():
        applied.undone = True
        return f"{c.path} was already gone"
    folder = backup_dir(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    parked = folder / f"undone-{datetime.now():%H%M%S}-{c.path.name}"
    shutil.move(c.path, parked)
    applied.undone = True
    return f"moved the new file {c.path.name} to {parked}"


# --------------------------------------------------------------------------- #
# Tool definitions sent to the model while write mode is on
# --------------------------------------------------------------------------- #


def definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Change an existing text file by replacing ONE exact piece of text. Read the file first "
                    "and copy old_text exactly, with enough lines to be unique. The user must approve every change."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string", "description": "Exact text currently in the file."},
                        "new_text": {"type": "string", "description": "What it should become."},
                        "reason": {"type": "string", "description": "One short sentence: why."},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create a NEW text file (never overwrites). The user must approve it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "reason": {"type": "string", "description": "One short sentence: why."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]
