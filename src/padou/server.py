"""Starting llama-server in the background when padou needs it.

Only for servers on this machine (localhost). The server is started in its own
session, so it keeps running after padou exits and the next launch is instant.
Stop it with:  pkill -f llama-server
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from urllib.parse import urlparse

from padou import paths
from padou.client import Client
from padou.config import ServerConfig

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def start(cfg: ServerConfig) -> tuple[subprocess.Popen | None, str]:
    """Launch the configured start_command. Returns (process, problem)."""
    try:
        argv = shlex.split(cfg.start_command)
    except ValueError as exc:
        return None, f"start_command can't be parsed: {exc}"
    if not argv:
        return None, "start_command is empty"
    if shutil.which(argv[0]) is None:
        return None, f"{argv[0]} is not installed (on Arch: yay -S llama.cpp-vulkan-git)"
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(paths.SERVER_LOG, "ab")
    log.write(f"\n===== padou started: {' '.join(argv)} =====\n".encode())
    log.flush()
    proc = subprocess.Popen(
        argv,  # a list and no shell: nothing in the command is interpreted
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # survives padou exiting, and Ctrl+C in padou
    )
    log.close()
    return proc, ""


def wait_ready(client: Client, proc: subprocess.Popen, timeout: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return False, f"llama-server exited with code {code}; see {paths.SERVER_LOG}"
        up, detail = client.health()
        if up:
            return True, "ok"
        time.sleep(1.0)
    return False, f"llama-server didn't become ready within {timeout:.0f}s; see {paths.SERVER_LOG}"


def tail_log(lines: int = 8) -> str:
    try:
        return "\n".join(paths.SERVER_LOG.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Finding, inspecting and stopping the server (for /server)
# --------------------------------------------------------------------------- #

import os  # noqa: E402
import signal  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402


@dataclass
class Proc:
    pid: int
    command: str
    rss_mb: float | None  # memory in RAM; GPU memory isn't visible from here


def find(cfg: ServerConfig) -> list[Proc]:
    """Running server processes owned by this user that match start_command's program."""
    try:
        program = os.path.basename(shlex.split(cfg.start_command)[0])
    except (ValueError, IndexError):
        program = "llama-server"
    found = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            if uid is not None and entry.stat().st_uid != uid:
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if not argv or not argv[0]:
                continue
            if os.path.basename(argv[0].decode(errors="replace")) != program:
                continue
            if program == "ollama" and b"serve" not in argv:
                continue  # `ollama run` etc. are clients, not the server
            rss = None
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) / 1024
            cmd = " ".join(a.decode(errors="replace") for a in argv if a)
            found.append(Proc(int(entry.name), cmd, rss))
        except (OSError, ValueError):
            continue
    return found


def stop(procs: list[Proc], wait: float = 8.0) -> list[int]:
    """Ask the processes to quit (SIGTERM), wait, then force (SIGKILL). Returns the PIDs stopped."""
    stopped = []
    for p in procs:
        try:
            os.kill(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            stopped.append(p.pid)
        except PermissionError:
            continue
    deadline = time.monotonic() + wait
    pending = [p.pid for p in procs if p.pid not in stopped]
    while pending and time.monotonic() < deadline:
        time.sleep(0.25)
        still = []
        for pid in pending:
            try:
                os.kill(pid, 0)
                still.append(pid)
            except ProcessLookupError:
                stopped.append(pid)
            except PermissionError:
                pass
        pending = still
    for pid in pending:
        try:
            os.kill(pid, signal.SIGKILL)
            stopped.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    return stopped
