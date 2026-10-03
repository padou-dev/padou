"""Read-only tools the model may use, and the safety rules around them.

The model can only *ask* for a tool. Everything here decides whether the
request is allowed and what actually runs:

* files: sensitive locations (keys, secrets, password stores) are refused,
  after resolving symlinks so a link can't sneak around the check;
* commands: only exact entries from an approved list, run without a shell,
  with simple validated arguments, never with sudo;
* output: always capped, so a big log can't flood a small model's context.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Sensitive paths
# --------------------------------------------------------------------------- #

_HOME = Path.home()

BLOCKED_DIRS = [
    _HOME / ".ssh",
    _HOME / ".gnupg",
    _HOME / ".password-store",
    _HOME / ".local/share/keyrings",
    _HOME / ".aws",
    _HOME / ".kube",
    _HOME / ".docker",
    _HOME / ".config/gh",
    _HOME / ".mozilla",
    _HOME / ".config/BraveSoftware",
    _HOME / ".config/chromium",
    _HOME / ".config/google-chrome",
    Path("/etc/ssl/private"),
]
if _HOME != Path("/root"):
    BLOCKED_DIRS.append(Path("/root"))  # another user's home: never browse it

BLOCKED_FILES = [
    Path("/etc/shadow"),
    Path("/etc/gshadow"),
    Path("/etc/sudoers"),
    _HOME / ".netrc",
    _HOME / ".git-credentials",
    _HOME / ".pgpass",
]

BLOCKED_NAME_PATTERNS = [
    re.compile(r"^\.env(\..*)?$"),  # .env, .env.local, ...
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(_sk)?$"),  # private SSH keys
    re.compile(r".*\.(pem|key|p12|pfx|kdbx)$", re.IGNORECASE),
    re.compile(r"^(credentials|secrets?)(\.\w+)?$", re.IGNORECASE),
]

SKIP_DIRS_IN_SEARCH = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache"}


class ToolError(Exception):
    """A tool request that was refused or failed; the message goes to the model."""


def resolve(path_text: str, cwd: Path) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise ToolError("a path is required")
    p = Path(os.path.expanduser(path_text.strip()))
    if not p.is_absolute():
        p = cwd / p
    return p.resolve()


def is_blocked(path: Path) -> bool:
    path = path.resolve()
    for d in BLOCKED_DIRS:
        try:
            if path == d.resolve() or path.is_relative_to(d.resolve()):
                return True
        except OSError:
            continue
    if any(path == f for f in BLOCKED_FILES):
        return True
    return any(pat.match(path.name) for pat in BLOCKED_NAME_PATTERNS)


def inside(path: Path, roots: list[Path] | None) -> bool:
    """Is `path` within one of the allowed folders? roots=None means anywhere."""
    if roots is None:
        return True
    path = path.resolve()  # follow symlinks first, so a link can't lead outside
    return any(path == r or path.is_relative_to(r) for r in roots)


def _check(path: Path, roots: list[Path] | None = None) -> None:
    if is_blocked(path):
        raise ToolError(f"access to {path} is blocked: it may contain secrets")
    if not inside(path, roots):
        allowed = ", ".join(str(r) for r in roots or [])
        raise ToolError(
            f"{path} is outside the folders padou may read ({allowed}). "
            f"Tell the user they can allow it with: /folder add {path if path.is_dir() else path.parent}"
        )


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... output cut: showed {limit} of {len(text)} characters]"


def _tail(text: str, limit: int) -> str:
    """Keep the END of the output (where errors usually are)."""
    if len(text) <= limit:
        return text
    return f"[... output cut: showing the last {limit} of {len(text)} characters]\n" + text[-limit:]


# --------------------------------------------------------------------------- #
# File tools
# --------------------------------------------------------------------------- #


def read_file(path: str, cwd: Path, limit: int, start_line: int = 1, max_lines: int = 400, roots: list[Path] | None = None) -> str:
    p = resolve(path, cwd)
    _check(p, roots)
    if not p.exists():
        raise ToolError(f"{p} does not exist")
    if p.is_dir():
        raise ToolError(f"{p} is a directory; use list_dir instead")
    try:
        raw = p.read_bytes()
    except PermissionError:
        raise ToolError(f"permission denied reading {p}") from None
    if b"\x00" in raw[:4096]:
        raise ToolError(f"{p} looks like a binary file, not text")
    lines = raw.decode("utf-8", errors="replace").splitlines()
    start = max(1, int(start_line))
    count = max(1, min(int(max_lines), 2000))
    chunk = lines[start - 1 : start - 1 + count]
    body = "\n".join(f"{n:>5}  {line}" for n, line in enumerate(chunk, start))
    header = f"{p} ({len(lines)} lines total, showing {start}-{start + len(chunk) - 1})\n"
    return _cap(header + body, limit)


def list_dir(path: str, cwd: Path, limit: int, roots: list[Path] | None = None) -> str:
    p = resolve(path or ".", cwd)
    _check(p, roots)
    if not p.is_dir():
        raise ToolError(f"{p} is not a directory")
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        raise ToolError(f"permission denied listing {p}") from None
    rows = []
    for e in entries[:300]:
        if e.is_dir():
            rows.append(f"{e.name}/")
        else:
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            rows.append(f"{e.name}  ({_human(size)})")
    more = f"\n[... {len(entries) - 300} more entries]" if len(entries) > 300 else ""
    return _cap(f"{p} ({len(entries)} entries)\n" + "\n".join(rows) + more, limit)


def search(pattern: str, cwd: Path, limit: int, path: str = ".", max_results: int = 80, roots: list[Path] | None = None) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("a search pattern is required")
    root = resolve(path or ".", cwd)
    _check(root, roots)
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
    files = [root] if root.is_file() else _walk(root)
    hits: list[str] = []
    scanned = 0
    for f in files:
        if is_blocked(f) or not inside(f, roots):  # skips symlinks pointing outside
            continue
        scanned += 1
        if scanned > 5000:
            hits.append("[... stopped after scanning 5000 files]")
            break
        try:
            if f.stat().st_size > 2_000_000:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{f}:{n}: {line.strip()[:200]}")
                if len(hits) >= max_results:
                    break
        if len(hits) >= max_results:
            hits.append(f"[... stopped at {max_results} matches]")
            break
    if not hits:
        return f"no matches for {pattern!r} in {root}"
    return _cap("\n".join(hits), limit)


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS_IN_SEARCH]
        for name in filenames:
            yield Path(dirpath) / name


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --------------------------------------------------------------------------- #
# Approved commands
# --------------------------------------------------------------------------- #

SAFE_ARG = re.compile(r"^[A-Za-z0-9@._:+-]{1,64}$")


@dataclass(frozen=True)
class ApprovedCommand:
    name: str
    description: str
    argv: tuple[str, ...]
    arg_name: str | None = None  # None = takes no argument
    arg_optional: bool = False


APPROVED_COMMANDS: dict[str, ApprovedCommand] = {
    c.name: c
    for c in [
        ApprovedCommand("disk_usage", "Disk space per filesystem (df -h)", ("df", "-h")),
        ApprovedCommand("memory", "RAM and swap usage (free -h)", ("free", "-h")),
        ApprovedCommand("uptime", "Uptime and load average", ("uptime",)),
        ApprovedCommand("kernel", "Kernel and system info (uname -a)", ("uname", "-a")),
        ApprovedCommand("ip_addresses", "Network interfaces and addresses (ip addr)", ("ip", "addr")),
        ApprovedCommand("routes", "Routing table (ip route)", ("ip", "route")),
        ApprovedCommand("block_devices", "Disks and partitions (lsblk)", ("lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS")),
        ApprovedCommand("processes", "Top processes by CPU", ("ps", "-eo", "pid,user,%cpu,%mem,comm", "--sort=-%cpu")),
        ApprovedCommand("failed_services", "systemd units that failed", ("systemctl", "--failed", "--no-pager")),
        ApprovedCommand("service_status", "Status of one systemd service", ("systemctl", "status", "--no-pager"), "service"),
        ApprovedCommand("user_service_status", "Status of one systemd user service", ("systemctl", "--user", "status", "--no-pager"), "service"),
        ApprovedCommand("service_logs", "Last 100 journal lines for one service", ("journalctl", "--no-pager", "-n", "100", "-u"), "service"),
        ApprovedCommand("recent_errors", "Recent error-level journal messages from this boot", ("journalctl", "--no-pager", "-b", "-p", "err", "-n", "100")),
        ApprovedCommand("package_info", "Installed pacman package version (pacman -Q)", ("pacman", "-Q"), "package"),
        ApprovedCommand("package_details", "Details of an installed pacman package (pacman -Qi)", ("pacman", "-Qi"), "package"),
        ApprovedCommand("which", "Where a command lives on PATH", ("which",), "program"),
        ApprovedCommand("gpu_vulkan", "GPUs visible to Vulkan (vulkaninfo --summary)", ("vulkaninfo", "--summary")),
        ApprovedCommand("sensors", "Temperatures and fans (lm_sensors)", ("sensors",)),
        ApprovedCommand("git_status", "git status of the current directory", ("git", "status", "--short", "--branch")),
        ApprovedCommand("git_log", "Last 20 commits of the current repo", ("git", "log", "--oneline", "-n", "20")),
    ]
}


def build_argv(name: str, argument: str | None) -> list[str]:
    cmd = APPROVED_COMMANDS.get(name)
    if cmd is None:
        raise ToolError(
            f"{name!r} is not an approved command. Approved: {', '.join(APPROVED_COMMANDS)}"
        )
    argv = list(cmd.argv)
    if cmd.arg_name is None:
        if argument:
            raise ToolError(f"{name} takes no argument")
        return argv
    if not argument:
        if cmd.arg_optional:
            return argv
        raise ToolError(f"{name} needs a {cmd.arg_name} argument")
    argument = argument.strip()
    if not SAFE_ARG.match(argument) or argument.startswith("-"):
        raise ToolError(f"invalid {cmd.arg_name}: {argument!r}")
    return argv + [argument]


def run_command(name: str, argument: str | None, cwd: Path, limit: int, timeout: float) -> str:
    argv = build_argv(name, argument)
    if shutil.which(argv[0]) is None:
        raise ToolError(f"{argv[0]} is not installed on this system")
    env = dict(os.environ, LC_ALL="C", SYSTEMD_COLORS="0", NO_COLOR="1", PAGER="cat")
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{' '.join(argv)} timed out after {timeout:.0f}s") from None
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    out = out.strip() or "(no output)"
    header = f"$ {' '.join(argv)}  (exit code {proc.returncode})\n"
    return header + _tail(out, limit)


# --------------------------------------------------------------------------- #
# Tool definitions sent to the model, and the dispatcher
# --------------------------------------------------------------------------- #


def definitions(commands_enabled: bool = True) -> list[dict]:
    defs = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file (with line numbers). Use for configs, logs, code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, absolute or relative to the current directory. ~ allowed."},
                        "start_line": {"type": "integer", "description": "First line to show (default 1)."},
                        "max_lines": {"type": "integer", "description": "How many lines to show (default 400)."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List the files and folders in a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Directory path (default: current directory)."}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search for text (case-insensitive, regex allowed) in a file or recursively in a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Text or regular expression to find."},
                        "path": {"type": "string", "description": "File or directory to search (default: current directory)."},
                    },
                    "required": ["pattern"],
                },
            },
        },
    ]
    if commands_enabled:
        lines = []
        for c in APPROVED_COMMANDS.values():
            arg = f" (argument: {c.arg_name})" if c.arg_name else ""
            lines.append(f"- {c.name}: {c.description}{arg}")
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run one approved READ-ONLY system command. Available:\n" + "\n".join(lines),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": list(APPROVED_COMMANDS)},
                            "argument": {"type": "string", "description": "Only for commands that take one (service, package or program name)."},
                        },
                        "required": ["name"],
                    },
                },
            }
        )
    return defs


@dataclass
class ToolResult:
    ok: bool
    output: str  # sent to the model
    title: str  # e.g. "Read ~/.config/hypr/hyprland.conf"
    summary: str  # e.g. "212 lines"


def _short(path_text: str) -> str:
    try:
        p = Path(os.path.expanduser(path_text))
        return "~/" + str(p.relative_to(_HOME)) if p.is_absolute() and p.is_relative_to(_HOME) else path_text
    except (ValueError, TypeError):
        return str(path_text)


def execute(name: str, raw_args: str | dict, cwd: Path, limit: int, command_timeout: float, commands_enabled: bool = True, roots: list[Path] | None = None) -> ToolResult:
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return ToolResult(False, f"error: arguments were not valid JSON: {raw_args!r}", name, "bad arguments")
        if not isinstance(args, dict):
            return ToolResult(False, "error: arguments must be a JSON object", name, "bad arguments")

    try:
        if name == "read_file":
            path = args.get("path", "")
            out = read_file(path, cwd, limit, args.get("start_line", 1) or 1, args.get("max_lines", 400) or 400, roots)
            total = out.split(" lines total", 1)[0].rsplit("(", 1)[-1]
            return ToolResult(True, out, f"Read {_short(path)}", f"{total} lines")
        if name == "list_dir":
            path = args.get("path") or "."
            out = list_dir(path, cwd, limit, roots)
            count = out.split(" entries)", 1)[0].rsplit("(", 1)[-1]
            return ToolResult(True, out, f"List {_short(path)}", f"{count} entries")
        if name == "search":
            pattern = args.get("pattern", "")
            path = args.get("path") or "."
            out = search(pattern, cwd, limit, path, roots=roots)
            n = 0 if out.startswith("no matches") else out.count("\n") + 1
            return ToolResult(True, out, f"Search {pattern!r} in {_short(path)}", f"{n} matches")
        if name == "run_command":
            if not commands_enabled:
                raise ToolError("system commands are disabled in the config")
            cmd_name = args.get("name", "")
            argument = args.get("argument") or None
            out = run_command(cmd_name, argument, cwd, limit, command_timeout)
            shown = out.splitlines()[0].removeprefix("$ ").split("  (exit", 1)[0]
            body_lines = max(0, out.count("\n"))
            return ToolResult(True, out, f"Run {shown}", f"{body_lines} lines")
        raise ToolError(f"unknown tool {name!r}")
    except ToolError as exc:
        return ToolResult(False, f"error: {exc}", f"{name}", str(exc))
    except Exception as exc:  # never let a tool crash padou
        return ToolResult(False, f"error: {type(exc).__name__}: {exc}", f"{name}", f"{type(exc).__name__}")
