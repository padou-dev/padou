"""Write mode: the only part of padou that can change files.

Every rule about changing files lives here:

* off by default; `/write <folder>` turns it on for that folder (and its
  subfolders) for this session only, and never for a drive root, your home
  folder, system folders, hidden folders or secret locations;
* the model can only *propose* a change. Nothing is touched until you approve
  it in a confirmation box, and the answer defaults to "no";
* tools: edit_file (replace one exact piece of text), rewrite_file (replace a
  whole file), write_file (create a new file, plus any missing folders),
  move_path (move or rename) and delete_path (to the system Trash);
* deleting a folder needs you to type its name, and the write folder itself
  can never be deleted;
* everything is backed up or recoverable, so `/undo` can put it back.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from padou import compat, paths, tools

MAX_FILE_BYTES = 300_000  # don't let a small model rewrite something huge
MAX_NEW_FILE_BYTES = 100_000
MAX_DELETE_BACKUP_BYTES = 20 * 1024 * 1024  # deletions up to this size are also copied, so /undo works
MAX_SCAN_ENTRIES = 100_000

TOOL_NAMES = {"edit_file", "rewrite_file", "write_file", "move_path", "delete_path"}


def _system_dirs() -> list[Path]:
    if compat.WINDOWS:
        names = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
        return [Path(os.environ[n]) for n in names if os.environ.get(n)]
    if compat.MACOS:
        return [Path(p) for p in ("/System", "/Library", "/Applications", "/bin", "/sbin", "/usr", "/opt", "/cores", "/dev",
                                  "/private/etc", "/private/var/db", "/private/var/root")]
    return [Path(p) for p in (
        "/bin", "/boot", "/dev", "/efi", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/proc",
        "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var",
    )]


SYSTEM_DIRS = _system_dirs()

# Folders inside your home that hold settings and app data, like hidden folders do.
_HOME_APP_FOLDERS = {"appdata", "library"} if (compat.WINDOWS or compat.MACOS) else set()


class WriteError(Exception):
    """A write that padou refuses; the message is shown to the model and the user."""


# --------------------------------------------------------------------------- #
# Which folders and paths may be written
# --------------------------------------------------------------------------- #


def _hidden(parts: tuple[str, ...]) -> bool:
    return any(p.startswith(".") for p in parts) or (bool(parts) and parts[0].lower() in _HOME_APP_FOLDERS)


def check_write_root(folder: Path, readable: list[Path] | None) -> Path:
    """Validate a folder for `/write`. Returns it resolved, or raises WriteError."""
    folder = folder.expanduser()
    if not folder.is_absolute():
        raise WriteError("use an absolute path or one relative to where padou started")
    folder = folder.resolve()
    home = Path.home().resolve()
    if not folder.is_dir():
        raise WriteError(f"{folder} is not a folder")
    if folder == Path(folder.anchor) or compat.same_path(str(folder), str(home)):
        raise WriteError("writing to a whole drive or your whole home folder is never allowed; pick a project folder")
    for sys_dir in SYSTEM_DIRS:
        if tools.within(folder, sys_dir):
            raise WriteError(f"{folder} is a system folder; padou never writes there")
    rel = folder.relative_to(home).parts if tools.within(folder, home) else folder.parts[1:]
    if _hidden(rel):
        raise WriteError(f"{folder} is (inside) a hidden or settings folder; padou never writes there")
    if tools.is_blocked(folder):
        raise WriteError(f"{folder} may hold secrets; padou never writes there")
    if readable is not None and not tools.inside(folder, readable):
        raise WriteError(f"{folder} isn't one of the folders padou may read. Allow it first: /folder add {folder}")
    return folder


def _target(path_text, cwd: Path, write_roots: list[Path], what: str = "path") -> Path:
    """Turn the model's path into an absolute path that write mode may touch."""
    if not write_roots:
        raise WriteError("write mode is off. The user can turn it on with /write <folder>")
    if not isinstance(path_text, str) or not path_text.strip():
        raise WriteError(f"a {what} is required")
    p = Path(os.path.expanduser(path_text.strip()))
    path = p if p.is_absolute() else cwd / p
    path = path.parent.resolve() / path.name  # resolve the folder, keep the last name as given
    if path.is_symlink():
        raise WriteError(f"{path} is a symlink; padou doesn't change links or write through them")
    real = path.resolve()
    if not any(tools.within(real, r) for r in write_roots):
        allowed = ", ".join(str(r) for r in write_roots)
        raise WriteError(f"{real} is outside the write folders ({allowed})")
    if any(part.lower() == ".git" for part in real.parts):
        raise WriteError("padou never changes anything inside .git")
    if tools.is_blocked(real):
        raise WriteError(f"{real} may hold secrets; padou never changes it")
    return path


def _is_write_root(path: Path, write_roots: list[Path]) -> bool:
    real = path.resolve()
    return any(compat.same_path(str(real), str(r)) for r in write_roots)


def _missing_folders(folder: Path) -> list[Path]:
    """The folders that would have to be created for `folder` to exist, outermost first."""
    missing = []
    while not folder.exists():
        missing.append(folder)
        folder = folder.parent
    if not folder.is_dir():
        raise WriteError(f"{folder} is a file, not a folder")
    return list(reversed(missing))


# --------------------------------------------------------------------------- #
# Proposed changes
# --------------------------------------------------------------------------- #


@dataclass
class Change:
    kind: str  # "edit", "rewrite", "create", "move" or "delete"
    path: Path
    old: str = ""  # current text (edit/rewrite)
    new: str = ""  # text after the change (edit/rewrite/create)
    reason: str = ""  # the model's short explanation, if it gave one
    dest: Path | None = None  # move: where it goes
    new_folders: list[Path] = field(default_factory=list)  # create/move: folders that will be made
    is_dir: bool = False  # delete/move: a folder rather than a file
    files: int = 0  # delete of a folder: how much is inside
    folders: int = 0
    size: int = 0  # bytes
    warnings: list[str] = field(default_factory=list)

    def diff(self, context: int = 3) -> list[str]:
        if self.kind not in ("edit", "rewrite", "create"):
            return []
        return list(
            difflib.unified_diff(
                self.old.splitlines(),
                self.new.splitlines(),
                fromfile="/dev/null" if self.kind == "create" else str(self.path),
                tofile=str(self.path),
                n=context,
                lineterm="",
            )
        )

    @property
    def stats(self) -> tuple[int, int]:
        lines = self.diff()
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        return added, removed

    @property
    def needs_typed_confirmation(self) -> bool:
        """Deleting a folder needs the user to type its name, not just "y"."""
        return self.kind == "delete" and self.is_dir

    def summary(self) -> str:
        """A few words for /changes and messages to the model."""
        if self.kind == "move":
            return f"moved to {self.dest}"
        if self.kind == "delete":
            return f"moved to the Trash ({_count(self.files, self.folders)}, {human_size(self.size)})" if self.is_dir else "moved to the Trash"
        added, removed = self.stats
        return {"edit": "edited", "rewrite": "rewritten", "create": "created"}[self.kind] + f" (+{added} −{removed})"


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _count(files: int, folders: int) -> str:
    return f"{files} file{'s' * (files != 1)}, {folders} folder{'s' * (folders != 1)}"


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise WriteError(f"can't read {path}: {exc.strerror or exc}") from None
    if len(data) > MAX_FILE_BYTES:
        raise WriteError(f"{path} is too big for padou to edit safely ({len(data) // 1024} KB)")
    if b"\x00" in data[:4096]:
        raise WriteError(f"{path} looks like a binary file; padou only edits text")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise WriteError(f"{path} isn't UTF-8 text; padou won't risk editing it") from None


def _match_line_endings(text: str, like: str) -> str:
    """Use Windows line endings (\\r\\n) in `text` if the file `like` uses them."""
    if "\r\n" in like and "\r\n" not in text:
        return text.replace("\n", "\r\n")
    return text


def _scan(folder: Path) -> tuple[int, int, int, list[str]]:
    """Files, folders, total bytes and warnings for a folder about to be deleted."""
    files = folders = size = 0
    warnings = set()
    for dirpath, dirnames, filenames in os.walk(folder):
        folders += len(dirnames)
        if ".git" in dirnames:
            warnings.add("it contains a Git repository (.git): its history goes too")
        for name in filenames:
            files += 1
            p = Path(dirpath) / name
            try:
                size += p.lstat().st_size
            except OSError:
                pass
            if tools.is_blocked(p):
                warnings.add(f"it contains files padou treats as secrets (like {name})")
        if files + folders > MAX_SCAN_ENTRIES:
            warnings.add(f"it's very large (stopped counting after {MAX_SCAN_ENTRIES:,} items)")
            break
    return files, folders, size, sorted(warnings)


def _args(raw_args: str | dict) -> dict:
    if isinstance(raw_args, dict):
        return raw_args
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        raise WriteError("the tool arguments weren't valid JSON") from None
    if not isinstance(args, dict):
        raise WriteError("the tool arguments must be a JSON object")
    return args


def propose(name: str, raw_args: str | dict, cwd: Path, write_roots: list[Path]) -> Change:
    """Turn a tool call into a Change. Nothing is written or moved yet."""
    args = _args(raw_args)
    reason = str(args.get("reason", ""))[:200]

    if name == "edit_file":
        path = _target(args.get("path"), cwd, write_roots)
        old_text, new_text = args.get("old_text"), args.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise WriteError("edit_file needs old_text and new_text")
        if not path.is_file():
            raise WriteError(f"{path} doesn't exist; use write_file to create a new file")
        content = _read_text(path)
        if not old_text:
            raise WriteError("old_text can't be empty; quote the exact lines to change")
        old_text, new_text = _match_line_endings(old_text, content), _match_line_endings(new_text, content)
        count = content.count(old_text)
        if count == 0:
            raise WriteError("old_text wasn't found in the file. Read the file again and copy the text exactly")
        if count > 1:
            raise WriteError(f"old_text appears {count} times; include more surrounding lines so it's unique")
        if old_text == new_text:
            raise WriteError("old_text and new_text are the same; nothing to change")
        return Change("edit", path, content, content.replace(old_text, new_text, 1), reason)

    if name == "rewrite_file":
        path = _target(args.get("path"), cwd, write_roots)
        content = args.get("content")
        if not isinstance(content, str):
            raise WriteError("rewrite_file needs content")
        if not path.is_file():
            raise WriteError(f"{path} doesn't exist; use write_file to create a new file")
        current = _read_text(path)
        content = _match_line_endings(content, current)
        if len(content.encode()) > MAX_FILE_BYTES:
            raise WriteError("that content is too big for padou to write in one go")
        if content == current:
            raise WriteError("the new content is the same as the file; nothing to change")
        return Change("rewrite", path, current, content, reason)

    if name == "write_file":
        path = _target(args.get("path"), cwd, write_roots)
        content = args.get("content")
        if not isinstance(content, str):
            raise WriteError("write_file needs content")
        if path.exists():
            raise WriteError(f"{path} already exists; use edit_file or rewrite_file to change it")
        if len(content.encode()) > MAX_NEW_FILE_BYTES:
            raise WriteError("that new file is too big for padou to write in one go")
        return Change("create", path, "", content, reason, new_folders=_missing_folders(path.parent))

    if name == "move_path":
        source = _target(args.get("source"), cwd, write_roots, "source")
        dest = _target(args.get("destination"), cwd, write_roots, "destination")
        if not source.exists():
            raise WriteError(f"{source} doesn't exist")
        if _is_write_root(source, write_roots):
            raise WriteError("the write folder itself can't be moved")
        if dest.exists() and dest.is_dir():
            dest = dest / source.name  # "move a.txt into docs/" means docs/a.txt
            _target(str(dest), cwd, write_roots)
        if dest.exists():
            raise WriteError(f"{dest} already exists; padou never overwrites when moving")
        if source.is_dir() and tools.within(dest.parent.resolve(), source.resolve()):
            raise WriteError("a folder can't be moved inside itself")
        return Change("move", source, reason=reason, dest=dest, is_dir=source.is_dir(),
                      new_folders=_missing_folders(dest.parent))

    if name == "delete_path":
        path = _target(args.get("path"), cwd, write_roots)
        if not path.exists():
            raise WriteError(f"{path} doesn't exist")
        if _is_write_root(path, write_roots):
            raise WriteError("the write folder itself can't be deleted; delete things inside it")
        if path.is_dir():
            files, folders, size, warnings = _scan(path)
            return Change("delete", path, reason=reason, is_dir=True, files=files, folders=folders, size=size, warnings=warnings)
        return Change("delete", path, reason=reason, size=path.stat().st_size)

    raise WriteError(f"unknown write tool {name!r}")


# --------------------------------------------------------------------------- #
# Applying, backups and undo
# --------------------------------------------------------------------------- #


@dataclass
class Applied:
    change: Change
    backup: Path | None  # a copy of what was there before (edit/rewrite, and small deletions)
    when: str
    undone: bool = False
    created_folders: list[Path] = field(default_factory=list)


def backup_dir(session_id: str) -> Path:
    return paths.STATE_DIR / "backups" / session_id


def _backup_path(session_id: str, name: str) -> Path:
    folder = backup_dir(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{datetime.now():%H%M%S-%f}-{name}"


def _write_atomic(path: Path, text: str, like: Path | None = None) -> None:
    """Write to a temporary file next to `path`, then swap it in, so a file is never half-written.

    Bytes are written exactly as given (no newline translation, which on
    Windows would turn every \\n into \\r\\n).
    """
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".padou-tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        if like is not None and like.exists():
            shutil.copymode(like, tmp)  # keep the original file's permissions
        else:
            os.chmod(tmp, 0o644)  # a new file: readable by everyone, writable by you
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def move_to_trash(path: Path) -> None:
    """Send a file or folder to the system Trash (Recycle Bin on Windows)."""
    from send2trash import send2trash
    from send2trash.exceptions import TrashPermissionError

    try:
        send2trash(str(path))
    except TrashPermissionError as exc:
        raise WriteError(f"the system Trash can't take {path} ({exc}); nothing was deleted") from None
    except OSError as exc:
        raise WriteError(f"couldn't move {path} to the Trash ({exc.strerror or exc}); nothing was deleted") from None


def apply(change: Change, session_id: str) -> Applied:
    """Make an approved change. Everything is re-checked first, in case things changed meanwhile."""
    stamp = datetime.now().strftime("%H:%M:%S")
    kind = change.kind

    if kind in ("edit", "rewrite"):
        if _read_text(change.path) != change.old:
            raise WriteError(f"{change.path} changed while you were deciding; nothing was written")
        backup = _backup_path(session_id, change.path.name)
        shutil.copy2(change.path, backup)  # keeps the file's permissions and times
        _write_atomic(change.path, change.new, like=change.path)
        return Applied(change, backup, stamp)

    if kind == "create":
        if change.path.exists():
            raise WriteError(f"{change.path} appeared in the meantime; nothing was written")
        made = [f for f in change.new_folders if not f.exists()]
        change.path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(change.path, change.new)
        return Applied(change, None, stamp, created_folders=made)

    if kind == "move":
        assert change.dest is not None
        if not change.path.exists():
            raise WriteError(f"{change.path} is gone; nothing was moved")
        if change.dest.exists():
            raise WriteError(f"{change.dest} appeared in the meantime; nothing was moved")
        made = [f for f in change.new_folders if not f.exists()]
        change.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(change.path), str(change.dest))
        return Applied(change, None, stamp, created_folders=made)

    if kind == "delete":
        if not change.path.exists():
            raise WriteError(f"{change.path} is already gone")
        backup = None
        if change.size <= MAX_DELETE_BACKUP_BYTES:
            backup = _backup_path(session_id, change.path.name)
            if change.is_dir:
                shutil.copytree(change.path, backup, symlinks=True)
            else:
                shutil.copy2(change.path, backup)
        try:
            move_to_trash(change.path)
        except WriteError:
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True) if backup.is_dir() else backup.unlink(missing_ok=True)
            raise
        return Applied(change, backup, stamp)

    raise WriteError(f"unknown change {kind!r}")


def _remove_empty(folders: list[Path]) -> None:
    """Remove folders padou created, innermost first, but only if they're empty."""
    for f in reversed(folders):
        try:
            f.rmdir()
        except OSError:
            break  # not empty (or already gone): leave it and everything above it


def undo(applied: Applied, session_id: str) -> str:
    """Put things back the way they were. Never deletes your data: a created file is moved to the backups."""
    c = applied.change
    if applied.undone:
        raise WriteError("that change was already undone")

    if c.kind in ("edit", "rewrite"):
        if applied.backup is None or not applied.backup.exists():
            raise WriteError("the backup for that change is missing")
        shutil.copy2(applied.backup, c.path)
        message = f"restored {c.path}"

    elif c.kind == "create":
        if c.path.exists():
            parked = _backup_path(session_id, f"undone-{c.path.name}")
            shutil.move(str(c.path), str(parked))
            message = f"moved the new file {c.path.name} to {parked}"
        else:
            message = f"{c.path} was already gone"
        _remove_empty(applied.created_folders)

    elif c.kind == "move":
        assert c.dest is not None
        if not c.dest.exists():
            raise WriteError(f"{c.dest} isn't there any more, so it can't be moved back")
        if c.path.exists():
            raise WriteError(f"something new is at {c.path} now; padou won't overwrite it")
        c.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(c.dest), str(c.path))
        _remove_empty(applied.created_folders)
        message = f"moved {c.dest.name} back to {c.path}"

    elif c.kind == "delete":
        if c.path.exists():
            raise WriteError(f"something new is at {c.path} now; padou won't overwrite it")
        if applied.backup is None or not applied.backup.exists():
            raise WriteError(f"{c.path.name} is in your Trash (Recycle Bin on Windows): restore it from there. "
                             f"It was too big ({human_size(c.size)}) for padou to keep its own copy")
        if applied.backup.is_dir():
            shutil.copytree(applied.backup, c.path, symlinks=True)
        else:
            shutil.copy2(applied.backup, c.path)
        message = f"restored {c.path} (a copy is still in your Trash)"

    else:
        raise WriteError(f"unknown change {c.kind!r}")

    applied.undone = True
    return message


# --------------------------------------------------------------------------- #
# Tool definitions sent to the model while write mode is on
# --------------------------------------------------------------------------- #


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    properties = dict(properties, reason={"type": "string", "description": "One short sentence: why."})
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


def definitions() -> list[dict]:
    path = {"type": "string", "description": "Path, absolute or relative to the current directory."}
    return [
        _tool("edit_file",
              "Change an existing text file by replacing ONE exact piece of text. Read the file first and copy "
              "old_text exactly, with enough lines to be unique. Best for small changes. The user approves every change.",
              {"path": path,
               "old_text": {"type": "string", "description": "Exact text currently in the file."},
               "new_text": {"type": "string", "description": "What it should become."}},
              ["path", "old_text", "new_text"]),
        _tool("rewrite_file",
              "Replace the WHOLE content of an existing text file. Use for large changes; for small ones prefer "
              "edit_file. Read the file first. The user sees the full diff and must approve it.",
              {"path": path, "content": {"type": "string", "description": "The complete new content."}},
              ["path", "content"]),
        _tool("write_file",
              "Create a NEW text file (never overwrites). Missing folders on the way are created too. The user must approve it.",
              {"path": path, "content": {"type": "string"}},
              ["path", "content"]),
        _tool("move_path",
              "Move or rename a file or folder. Never overwrites: the destination must not exist (if it's an "
              "existing folder, the item goes inside it). The user must approve it.",
              {"source": path, "destination": path},
              ["source", "destination"]),
        _tool("delete_path",
              "Delete a file or folder by moving it to the system Trash. Only when the user asked for it. "
              "The user must approve it (for folders, by typing the folder's name).",
              {"path": path},
              ["path"]),
    ]
