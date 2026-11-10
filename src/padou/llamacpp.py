"""Downloading llama.cpp for people who don't have it: the model server padou uses.

The llama.cpp team publishes ready-made builds for Windows, macOS and Linux on
GitHub (https://github.com/ggml-org/llama.cpp/releases). `padou setup` picks
the right one for this computer, checks its checksum, and unpacks it into
padou's own data folder: no administrator rights, nothing installed system-wide,
and `/server update` can replace it with a newer build later.

Which build:
* macOS: the Metal build (Apple Silicon) or the Intel build;
* Windows and Linux with a graphics chip: the Vulkan build, which works with
  NVIDIA, AMD and Intel graphics (Linux needs the Vulkan loader, usually there);
* no graphics chip (or no Vulkan on Linux): the CPU build.
"""

from __future__ import annotations

import ctypes.util
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from padou import __version__, compat, paths

REPO = "ggml-org/llama.cpp"
API = f"https://api.github.com/repos/{REPO}"


class InstallError(Exception):
    """A download or unpack that failed; the message says what to do instead."""


@dataclass
class Asset:
    tag: str
    name: str
    url: str
    size: int
    sha256: str = ""  # from GitHub's "digest", when it gives one


def home() -> Path:
    return paths.DATA_DIR / "llama.cpp"


def _arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def has_vulkan() -> bool:
    """Is the Vulkan loader there (Linux)? Without it the Vulkan build can't start."""
    if not compat.LINUX:
        return True
    return ctypes.util.find_library("vulkan") is not None


def variant(gpu_vendor: str) -> tuple[str, str]:
    """(asset pattern, plain-language name) of the build for this computer."""
    arch = _arch()
    if compat.MACOS:
        return rf"-bin-macos-{arch}\.(tar\.gz|zip)$", "macOS (Metal)" if arch == "arm64" else "macOS (Intel)"
    system = "win" if compat.WINDOWS else "ubuntu"
    gpu = gpu_vendor not in ("none", "apple")
    if gpu and has_vulkan():
        return rf"-bin-{system}-vulkan-{arch}\.(tar\.gz|zip)$", f"{'Windows' if compat.WINDOWS else 'Linux'} (Vulkan, for your graphics)"
    cpu = rf"-bin-win-cpu-{arch}\.zip$" if compat.WINDOWS else rf"-bin-ubuntu-{arch}\.(tar\.gz|zip)$"
    return cpu, f"{'Windows' if compat.WINDOWS else 'Linux'} (CPU only)"


def find_asset(pattern: str, transport: httpx.BaseTransport | None = None) -> Asset:
    """The newest release that has a build matching `pattern`."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"padou/{__version__}"}
    rx = re.compile(r"^llama-.*" + pattern)
    try:
        with httpx.Client(timeout=20, headers=headers, follow_redirects=True, transport=transport) as http:
            r = http.get(f"{API}/releases", params={"per_page": 10})
            r.raise_for_status()
            releases = r.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = " (GitHub limits how often it can be asked: try again in an hour)" if code in (403, 429) else ""
        raise InstallError(f"GitHub answered HTTP {code} when padou asked for the latest llama.cpp{hint}") from None
    except (httpx.HTTPError, ValueError) as exc:
        raise InstallError(f"couldn't ask GitHub for the latest llama.cpp ({type(exc).__name__}). Is this computer online?") from None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        for a in rel.get("assets") or []:
            name = str(a.get("name", ""))
            if rx.search(name) and not name.startswith("cudart"):
                digest = str(a.get("digest") or "")
                return Asset(str(rel.get("tag_name", "")), name, str(a.get("browser_download_url", "")),
                             int(a.get("size") or 0), digest.removeprefix("sha256:") if digest.startswith("sha256:") else "")
    raise InstallError("there's no ready-made llama.cpp build for this computer in the latest releases")


def _download(asset: Asset, dest: Path, progress=None, transport: httpx.BaseTransport | None = None) -> None:
    digest = hashlib.sha256()
    done = 0
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True, transport=transport,
                          headers={"User-Agent": f"padou/{__version__}"}) as http:
            with http.stream("GET", asset.url) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or asset.size or 0)
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
    except (httpx.HTTPError, ValueError) as exc:
        dest.unlink(missing_ok=True)
        raise InstallError(f"the download stopped ({type(exc).__name__}); try again") from None
    if asset.sha256 and digest.hexdigest() != asset.sha256.lower():
        dest.unlink(missing_ok=True)
        raise InstallError("the download doesn't match GitHub's checksum, so padou threw it away; try again")


def _safe_extract(archive: Path, dest: Path, is_zip: bool) -> None:
    """Unpack, refusing anything that would land outside `dest`."""
    root = dest.resolve()

    def check(name: str) -> None:
        target = (dest / name).resolve()
        if target != root and not str(target).startswith(str(root) + os.sep):
            raise InstallError(f"the archive contains an unsafe path ({name}); nothing was installed")

    if is_zip:
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                check(n)
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                check(m.name)
                if m.islnk() or m.issym():
                    check(os.path.join(os.path.dirname(m.name), m.linkname))
            if hasattr(tarfile, "data_filter"):
                t.extractall(dest, filter="data")  # also refuses device files and odd permissions
            else:
                t.extractall(dest)


def _find_server(folder: Path) -> Path | None:
    name = "llama-server.exe" if compat.WINDOWS else "llama-server"
    hits = sorted(folder.rglob(name), key=lambda p: len(p.parts))
    return hits[0] if hits else None


def install(asset: Asset, progress=None, transport: httpx.BaseTransport | None = None) -> Path:
    """Download and unpack `asset`; returns the path of llama-server. Older builds are removed afterwards."""
    base = home()
    base.mkdir(parents=True, exist_ok=True)
    target = base / asset.tag
    archive = base / f"{asset.name}.part"
    _download(asset, archive, progress, transport)
    tmp = base / f".{asset.tag}.unpacking"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        _safe_extract(archive, tmp, is_zip=asset.name.endswith(".zip"))
        found = _find_server(tmp)
        if found is None:
            raise InstallError("the download didn't contain llama-server; nothing was installed")
        if not compat.WINDOWS:
            found.chmod(found.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise InstallError(f"unpacking failed ({exc}); nothing was installed") from None
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
    server = _find_server(target)
    assert server is not None
    (base / "current.json").write_text(json.dumps({"tag": asset.tag, "asset": asset.name, "server": str(server)}), encoding="utf-8")
    for old in base.iterdir():
        if old.is_dir() and old.name != asset.tag and not old.name.startswith("."):
            shutil.rmtree(old, ignore_errors=True)
    return server


def current() -> dict:
    """{"tag", "asset", "server"} of the llama.cpp padou downloaded, or {} if none."""
    try:
        data = json.loads((home() / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and Path(str(data.get("server", ""))).exists() else {}


def same_build_pattern(asset_name: str) -> str:
    """'llama-b12207-bin-ubuntu-vulkan-x64.tar.gz' -> a pattern for the same build in any release."""
    rest = re.sub(r"^llama-[^-]+", "", asset_name)
    return re.escape(rest) + "$"


def with_server(start_command: str, server: str) -> str:
    """The same start command, using another llama-server."""
    argv = compat.split_command(start_command)
    argv[0] = server
    return compat.join_command(argv)


def env_for(binary: str) -> dict | None:
    """Environment for running a downloaded llama-server: its own folder holds the libraries it needs.
    None for any other llama-server (nothing to change)."""
    path = Path(binary)
    try:
        path.resolve().relative_to(home().resolve())
    except (ValueError, OSError):
        return None
    folder = str(path.resolve().parent)
    var = "DYLD_LIBRARY_PATH" if compat.MACOS else "LD_LIBRARY_PATH" if compat.LINUX else "PATH"
    old = os.environ.get(var, "")
    return dict(os.environ, **{var: folder + (os.pathsep + old if old else "")})


def managed(start_command: str) -> bool:
    """Does this start command use the llama.cpp padou downloaded?"""
    try:
        argv = compat.split_command(start_command)
    except ValueError:
        return False
    if not argv:
        return False
    try:
        Path(argv[0]).resolve().relative_to(home().resolve())
        return True
    except (ValueError, OSError):
        return False
