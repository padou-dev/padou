"""Small differences between Linux, macOS and Windows, kept in one place.

The rest of padou asks this module instead of checking the operating system
itself, so there is exactly one place to look when something behaves
differently on another system.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")
OS_NAME = "windows" if WINDOWS else "macos" if MACOS else "linux" if LINUX else sys.platform

# Windows and macOS file systems usually ignore upper/lower case ("Readme.md" is
# "README.md"), so safety checks must compare names case-insensitively there.
CASE_INSENSITIVE_FS = WINDOWS or MACOS

# What this system calls the place deleted files go.
TRASH = "Recycle Bin" if WINDOWS else "Trash"


def is_wsl() -> bool:
    """True inside Windows Subsystem for Linux."""
    if not LINUX:
        return False
    try:
        with open("/proc/sys/kernel/osrelease") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def split_command(command: str) -> list[str]:
    """Split a command line into a list, the way this system's shell would.

    On Windows, POSIX rules would eat the backslashes in C:\\paths, so quotes
    are handled by hand instead. No shell is ever involved.
    """
    if not WINDOWS:
        return shlex.split(command)
    parts = shlex.split(command, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def join_command(argv: list[str]) -> str:
    """The opposite of split_command: a list back into one command line."""
    return subprocess.list2cmdline(argv) if WINDOWS else shlex.join(argv)


def program_name(path: str) -> str:
    """'C:\\llama\\llama-server.exe' or '/usr/bin/llama-server' -> 'llama-server'."""
    name = os.path.basename(path.replace("\\", "/"))
    return name[:-4] if name.lower().endswith(".exe") else name


def detached_process_options() -> dict:
    """Popen options for a background server that outlives padou and ignores its Ctrl+C."""
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        return {"creationflags": flags}
    return {"start_new_session": True}


def clipboard_commands() -> list[list[str]]:
    """Clipboard programs to try, best first."""
    if WINDOWS:
        return [["clip"]]
    if MACOS:
        return [["pbcopy"]]
    return [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["clip.exe"]]


def utf8_output(*streams) -> None:
    """Make printing never crash on characters the console's code page lacks.

    When Windows output goes to a pipe or a file (GitHub Actions, `padou > log`),
    Python uses the old code page (often cp1252), which has no Greek letters or
    box-drawing characters. Switch those streams to UTF-8; anything a stream
    still can't take becomes "?" instead of an error.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        try:
            if WINDOWS and (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
            elif stream.errors == "strict":
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # not a normal text stream (tests, IDEs): leave it alone


def same_path(a: str, b: str) -> bool:
    return a.lower() == b.lower() if CASE_INSENSITIVE_FS else a == b
