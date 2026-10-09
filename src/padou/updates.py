"""Checking for a newer padou, and installing it with /update.

The check is small and polite:
* at most once a day, in the background, so it never slows padou's start;
* one request to GitHub's public API, asking for the latest release; nothing
  about you or your conversations is sent;
* off with `[updates] check = false` in the config, or PADOU_NO_UPDATE_CHECK=1.

/update then installs it the same way padou was installed:
* from a git clone (`uv tool install --editable .`): `git pull`, plus a
  reinstall if the dependencies changed;
* with `uv tool install git+https://...`: the same command, pinned to the new tag.
On Windows a running program can't replace itself, so there the install runs
in a new window right after padou exits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from padou import __version__, compat, paths

REPO = "padou-dev/padou"
API = f"https://api.github.com/repos/{REPO}"
CHECK_EVERY = 24 * 3600  # seconds
TAG_RE = re.compile(r"^v?\d+\.\d+(\.\d+)?$")


@dataclass
class Release:
    version: str  # "0.2.0"
    tag: str  # "v0.2.0"
    notes: str = ""  # the release notes (Markdown)
    url: str = ""


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", text.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def enabled(cfg) -> bool:
    return bool(cfg.updates.check) and not os.environ.get("PADOU_NO_UPDATE_CHECK")


# --------------------------------------------------------------------------- #
# Asking GitHub
# --------------------------------------------------------------------------- #


def fetch_latest(timeout: float = 5.0, transport: httpx.BaseTransport | None = None) -> Release | None:
    """The newest published release (or, if there are none, the highest version tag)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"padou/{__version__}"}
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True, transport=transport) as http:
            r = http.get(f"{API}/releases/latest")
            if r.status_code == 200:
                data = r.json()
                tag = str(data.get("tag_name", ""))
                if parse_version(tag):
                    return Release(".".join(map(str, parse_version(tag))), tag, str(data.get("body") or ""), str(data.get("html_url") or ""))
            r = http.get(f"{API}/tags", params={"per_page": 50})
            if r.status_code == 200:
                tags = [str(t.get("name", "")) for t in r.json() if isinstance(t, dict)]
                tags = [t for t in tags if parse_version(t)]
                if tags:
                    best = max(tags, key=parse_version)
                    return Release(".".join(map(str, parse_version(best))), best, "", f"https://github.com/{REPO}/releases/tag/{best}")
    except (httpx.HTTPError, ValueError):
        return None
    return None


# --------------------------------------------------------------------------- #
# The once-a-day cache
# --------------------------------------------------------------------------- #


def _load_cache() -> dict:
    try:
        return json.loads(paths.UPDATE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(release: Release | None) -> None:
    """Remember when we last asked. If asking failed (offline), keep what we knew before."""
    try:
        paths.UPDATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        latest = asdict(release) if release else _load_cache().get("latest")
        data = {"checked_at": time.time(), "latest": latest}
        paths.UPDATE_CACHE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def cached_update() -> Release | None:
    """A newer release found by an earlier check, if any (no network)."""
    latest = _load_cache().get("latest")
    if isinstance(latest, dict):
        try:
            release = Release(**latest)
        except TypeError:
            return None
        if is_newer(release.version):
            return release
    return None


def check_due() -> bool:
    checked = _load_cache().get("checked_at", 0)
    return not isinstance(checked, (int, float)) or time.time() - checked > CHECK_EVERY


def check_in_background(on_found) -> threading.Thread | None:
    """If a check is due, ask GitHub in a background thread; call on_found(release) if newer."""
    if not check_due():
        return None

    def work():
        release = fetch_latest()
        _save_cache(release)
        if release and is_newer(release.version):
            on_found(release)

    thread = threading.Thread(target=work, name="padou-update-check", daemon=True)
    thread.start()
    return thread


def check_now() -> Release | None:
    release = fetch_latest()
    _save_cache(release)
    return release


# --------------------------------------------------------------------------- #
# How was padou installed, and how do we update it?
# --------------------------------------------------------------------------- #


@dataclass
class Install:
    kind: str  # "git" (a clone, installed --editable), "uv-tool" or "other"
    folder: Path | None = None  # the clone, for "git"


def detect_install() -> Install:
    package = Path(__file__).resolve().parent
    for parent in list(package.parents)[:4]:
        if (parent / ".git").exists() and (parent / "pyproject.toml").exists():
            return Install("git", parent)
    if (Path(sys.prefix) / "uv-receipt.toml").exists():
        return Install("uv-tool")
    return Install("other")


def _run(argv: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace",
                           stdin=subprocess.DEVNULL, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _run_visible(argv: list[str], cwd: Path | None = None) -> int:
    """Run a command in the open, so its progress and any questions (like an SSH
    key's passphrase) appear in the terminal and can be answered. Ctrl+C stops it."""
    print(f"$ {compat.join_command(argv)}", flush=True)
    try:
        return subprocess.run(argv, cwd=cwd).returncode
    except OSError as exc:
        print(exc)
        return 1


def _fingerprint(folder: Path) -> str:
    """Changes when the dependencies change (then the tool needs reinstalling, not just a pull)."""
    try:
        return hashlib.sha256((folder / "pyproject.toml").read_bytes()).hexdigest()
    except OSError:
        return ""


def git_tool_spec(release: Release) -> str:
    if not TAG_RE.match(release.tag):
        raise ValueError(f"unexpected release tag {release.tag!r}")
    return f"git+https://github.com/{REPO}@{release.tag}"


@dataclass
class Outcome:
    ok: bool
    message: str
    restart: bool = False  # quit and start padou again to use the new version
    after_exit: list[str] | None = None  # (Windows) a command to run once padou has exited


def update_git_clone(folder: Path) -> Outcome:
    if shutil.which("git") is None:
        return Outcome(False, "git isn't installed, so padou can't update its clone")
    code, out = _run(["git", "status", "--porcelain", "--untracked-files=no"], folder)
    if code != 0:
        return Outcome(False, f"git status failed: {out}")
    if out.strip():
        return Outcome(False, f"{folder} has uncommitted changes. Commit or stash them, then /update again.")
    before = _fingerprint(folder)
    if _run_visible(["git", "pull", "--ff-only"], folder) != 0:
        return Outcome(False, "git pull didn't finish (see its message above). Nothing was changed.")
    if _fingerprint(folder) == before:
        return Outcome(True, "Pulled the latest code.", restart=True)
    reinstall = ["uv", "tool", "install", "--force", "--editable", str(folder)]
    if compat.WINDOWS:
        return Outcome(True, "Pulled the latest code. Its dependencies changed, so the tool is reinstalled after you quit.",
                       restart=True, after_exit=reinstall)
    if _run_visible(reinstall) != 0:
        return Outcome(False, "Pulled the code, but reinstalling failed (see above). Run: " + compat.join_command(reinstall))
    return Outcome(True, "Pulled the latest code and reinstalled (the dependencies changed).", restart=True)


def update_uv_tool(release: Release) -> Outcome:
    if shutil.which("uv") is None:
        return Outcome(False, "uv isn't on your PATH, so padou can't reinstall itself")
    command = ["uv", "tool", "install", "--force", git_tool_spec(release)]
    if compat.WINDOWS:
        return Outcome(True, f"padou {release.version} will be installed in a new window as soon as you quit.",
                       restart=True, after_exit=command)
    if _run_visible(command) != 0:
        return Outcome(False, "The install didn't finish (see above). Your current padou is unchanged.")
    return Outcome(True, f"Installed padou {release.version}.", restart=True)


def run_after_exit(command: list[str]) -> None:
    """Windows: open a new window that waits for padou to exit, then runs `command`.

    The command is built by padou itself (never from model or network text, and
    the release tag is checked against a strict pattern), so passing it to
    PowerShell is safe.
    """
    import psutil

    me = psutil.Process()
    pids = [me.pid]
    try:
        parent = me.parent()
        if parent and compat.program_name(parent.name()).lower() == "padou":
            pids.append(parent.pid)  # the padou.exe launcher holds the file lock too
    except psutil.Error:
        pass
    quoted = " ".join("'" + part.replace("'", "''") + "'" for part in command)
    script = (
        f"Wait-Process -Id {','.join(map(str, pids))} -ErrorAction SilentlyContinue; "
        f"& {quoted}; "
        "Write-Host ''; Write-Host 'Done. Start padou again. Press Enter to close this window.'; Read-Host"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", script],
        creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
    )
