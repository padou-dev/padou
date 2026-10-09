"""Starting, finding and stopping the model server (llama-server or Ollama).

Only for servers on this machine (localhost). The server is started detached
from padou, so it keeps running after padou exits and the next launch is
instant. /server stop shuts it down. Works the same on Linux, macOS and
Windows: processes are found and stopped through psutil.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import psutil

from padou import compat, paths
from padou.client import Client
from padou.config import ServerConfig

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def _install_hint(program: str) -> str:
    if compat.WINDOWS:
        return f"{program} is not installed. Try: winget install llama.cpp"
    if compat.MACOS:
        return f"{program} is not installed. Try: brew install llama.cpp"
    return f"{program} is not installed (Arch: yay -S llama.cpp-vulkan-git; others: brew install llama.cpp)"


def start(cfg: ServerConfig) -> tuple[subprocess.Popen | None, str]:
    """Launch the configured start_command. Returns (process, problem)."""
    try:
        argv = compat.split_command(cfg.start_command)
    except ValueError as exc:
        return None, f"start_command can't be parsed: {exc}"
    if not argv:
        return None, "start_command is empty"
    exe = shutil.which(argv[0])
    if exe is None:
        return None, _install_hint(argv[0])
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(paths.SERVER_LOG, "ab") as log:
        log.write(f"\n===== padou started: {compat.join_command(argv)} =====\n".encode())
        log.flush()
        proc = subprocess.Popen(
            [exe, *argv[1:]],  # a list and no shell: nothing in the command is interpreted
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **compat.detached_process_options(),  # survives padou exiting, ignores Ctrl+C in padou
        )
    try:
        paths.SERVER_PID.write_text(str(proc.pid))
    except OSError:
        pass
    return proc, ""


def wait_ready(client: Client, proc: subprocess.Popen, timeout: float, on_progress=None) -> tuple[bool, str]:
    """Wait until the server answers. on_progress(text) gets the server's latest
    log line every second, so a long first download doesn't look stuck."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return False, f"the server exited with code {code}; see {paths.SERVER_LOG}"
        up, detail = client.health()
        if up:
            return True, "ok"
        if on_progress is not None:
            line = progress()
            if line:
                on_progress(line)
        time.sleep(1.0)
    return False, f"the server didn't become ready within {timeout:.0f}s; see {paths.SERVER_LOG}"


def progress(width: int = 72) -> str:
    """The server's latest log line from this start (download progress, loading...), shortened.

    Download progress is redrawn on one line with carriage returns, so both
    "\r" and "\n" count as line breaks.
    """
    try:
        with open(paths.SERVER_LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    tail = tail.rsplit("===== padou started", 1)[-1]
    lines = [" ".join(x.split()) for x in tail.replace("\r", "\n").split("\n")]
    lines = [x for x in lines[1:] if x]  # [0] is the rest of the "started" marker
    if not lines:
        return ""
    last = lines[-1]
    bar = progress_bar(last)
    if bar:
        return bar
    return last if len(last) <= width else last[: width - 1] + "…"


_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SIZES = re.compile(r"(\d+(?:\.\d+)?\s*[KMGT]i?B)\s*/\s*(\d+(?:\.\d+)?\s*[KMGT]i?B)", re.IGNORECASE)


def progress_bar(line: str, cells: int = 24) -> str:
    """'[=====>   ] 45% 2.8 GB / 6.2 GB' -> 'downloading ━━━━━━━━━━━──────────── 45%  2.8 GB / 6.2 GB'.

    llama.cpp's own progress output differs between versions, so only the
    percentage (and the sizes, if shown) are taken from it. "" if there's none.
    """
    m = _PERCENT.search(line)
    if not m:
        return ""
    pct = min(100.0, float(m.group(1)))
    filled = round(cells * pct / 100)
    text = f"downloading {'━' * filled}{'─' * (cells - filled)} {pct:3.0f}%"
    sizes = _SIZES.search(line)
    if sizes:
        text += f"  {' '.join(sizes.group(1).split())} / {' '.join(sizes.group(2).split())}"
    return text


def tail_log(lines: int = 8) -> str:
    try:
        return "\n".join(paths.SERVER_LOG.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Finding, inspecting and stopping the server (for /server)
# --------------------------------------------------------------------------- #


@dataclass
class Proc:
    pid: int
    command: str
    rss_mb: float | None  # memory in RAM; GPU memory isn't visible from here


def _program(cfg: ServerConfig) -> str:
    try:
        return compat.program_name(compat.split_command(cfg.start_command)[0])
    except (ValueError, IndexError):
        return "llama-server"


def _me() -> str | None:
    try:
        return psutil.Process().username()
    except psutil.Error:
        return None


def find(cfg: ServerConfig) -> list[Proc]:
    """Running server processes owned by this user that match start_command's program."""
    program = _program(cfg)
    me = _me()
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "username", "memory_info"]):
        try:
            info = p.info
            argv = info.get("cmdline") or []
            name = compat.program_name(argv[0]) if argv else compat.program_name(info.get("name") or "")
            if not compat.same_path(name, program):
                continue
            if me is not None and info.get("username") not in (None, me):
                continue  # another user's server: not ours to touch
            if program == "ollama" and "serve" not in argv:
                continue  # `ollama run` etc. are clients, not the server
            mem = info.get("memory_info")
            rss = mem.rss / 1024 / 1024 if mem else None
            found.append(Proc(int(info["pid"]), compat.join_command(argv) if argv else name, rss))
        except (psutil.Error, KeyError, TypeError):
            continue
    return found


def stop(procs: list[Proc], wait: float = 8.0) -> list[int]:
    """Ask the processes to quit, wait, then force them. Returns the PIDs stopped."""
    handles = []
    stopped = []
    for p in procs:
        try:
            h = psutil.Process(p.pid)
            h.terminate()  # SIGTERM on Linux/macOS, TerminateProcess on Windows
            handles.append(h)
        except psutil.NoSuchProcess:
            stopped.append(p.pid)
        except psutil.AccessDenied:
            continue
    gone, alive = psutil.wait_procs(handles, timeout=wait)
    stopped += [h.pid for h in gone]
    for h in alive:
        try:
            h.kill()
            stopped.append(h.pid)
        except psutil.Error:
            pass
    if stopped:
        try:
            paths.SERVER_PID.unlink(missing_ok=True)
        except OSError:
            pass
    return stopped
