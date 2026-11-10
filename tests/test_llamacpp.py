"""0.2.1: padou downloads llama.cpp for people who don't have it."""
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from padou import compat, detect, llamacpp, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")


RELEASES = [
    {"tag_name": "v0.7.0", "draft": False, "assets": [{"name": "source.zip", "browser_download_url": "x", "size": 1}]},
    {"tag_name": "b12207", "draft": False, "prerelease": True, "assets": [
        {"name": "cudart-llama-b12207-bin-ubuntu-cuda-12.8-x64.tar.gz", "browser_download_url": "https://dl/cudart", "size": 9},
        {"name": "llama-b12207-bin-ubuntu-vulkan-x64.tar.gz", "browser_download_url": "https://dl/vulkan.tar.gz", "size": 1000,
         "digest": "sha256:" + "0" * 64},
        {"name": "llama-b12207-bin-ubuntu-x64.tar.gz", "browser_download_url": "https://dl/cpu.tar.gz", "size": 900},
        {"name": "llama-b12207-bin-win-vulkan-x64.zip", "browser_download_url": "https://dl/win.zip", "size": 800},
        {"name": "llama-b12207-bin-macos-arm64.tar.gz", "browser_download_url": "https://dl/mac.tar.gz", "size": 700},
    ]},
]


def api(handler=None):
    def default(request):
        return httpx.Response(200, json=RELEASES)
    return httpx.MockTransport(handler or default)


def test_variant_per_system(monkeypatch):
    monkeypatch.setattr(llamacpp.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(compat, "MACOS", False)
    monkeypatch.setattr(compat, "WINDOWS", False)
    monkeypatch.setattr(compat, "LINUX", True)
    monkeypatch.setattr(llamacpp, "has_vulkan", lambda: True)
    assert "ubuntu-vulkan-x64" in llamacpp.variant("amd")[0]
    assert llamacpp.variant("none")[0].startswith(r"-bin-ubuntu-x64")
    monkeypatch.setattr(llamacpp, "has_vulkan", lambda: False)
    assert "CPU" in llamacpp.variant("nvidia")[1]  # no Vulkan loader: the CPU build
    monkeypatch.setattr(compat, "LINUX", False)
    monkeypatch.setattr(compat, "WINDOWS", True)
    monkeypatch.setattr(llamacpp, "has_vulkan", lambda: True)
    assert "win-vulkan-x64" in llamacpp.variant("nvidia")[0] and "win-cpu-x64" in llamacpp.variant("none")[0]
    monkeypatch.setattr(compat, "WINDOWS", False)
    monkeypatch.setattr(compat, "MACOS", True)
    monkeypatch.setattr(llamacpp.platform, "machine", lambda: "arm64")
    assert "macos-arm64" in llamacpp.variant("apple")[0]


def test_find_asset_skips_releases_without_builds_and_cudart():
    a = llamacpp.find_asset(r"-bin-ubuntu-vulkan-x64\.(tar\.gz|zip)$", api())
    assert (a.tag, a.name, a.sha256) == ("b12207", "llama-b12207-bin-ubuntu-vulkan-x64.tar.gz", "0" * 64)
    with pytest.raises(llamacpp.InstallError, match="no ready-made"):
        llamacpp.find_asset(r"-bin-plan9\.zip$", api())
    with pytest.raises(llamacpp.InstallError, match="online"):
        llamacpp.find_asset("x", api(lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline"))))
    with pytest.raises(llamacpp.InstallError, match="try again in an hour"):
        llamacpp.find_asset("x", api(lambda r: httpx.Response(403)))


def tar_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def serve(data: bytes):
    return httpx.MockTransport(lambda r: httpx.Response(200, content=data, headers={"content-length": str(len(data))}))


@pytest.mark.skipif(compat.WINDOWS, reason="Linux/macOS build layout")
def test_install_unpacks_checks_and_remembers(tmp_path):
    data = tar_bytes({"build/bin/llama-server": b"#!/bin/sh\necho hi\n", "build/bin/libggml.so": b"lib"})
    asset = llamacpp.Asset("b1", "llama-b1-bin-ubuntu-vulkan-x64.tar.gz", "https://dl/x", len(data), hashlib.sha256(data).hexdigest())
    seen = []
    server = llamacpp.install(asset, progress=lambda done, total: seen.append((done, total)), transport=serve(data))
    assert server.name == "llama-server" and server.stat().st_mode & 0o100 and (server.parent / "libggml.so").exists()
    assert seen[-1] == (len(data), len(data))
    assert llamacpp.current()["tag"] == "b1" and llamacpp.managed(f"{server} -hf x --port 8080")
    assert not llamacpp.managed("llama-server -hf x")
    # a newer build replaces the old one
    asset2 = llamacpp.Asset("b2", "llama-b2-bin-ubuntu-vulkan-x64.tar.gz", "https://dl/y", len(data))
    server2 = llamacpp.install(asset2, transport=serve(data))
    assert not server.exists() and server2.exists() and llamacpp.current()["tag"] == "b2"


def test_install_refuses_a_bad_checksum_and_unsafe_paths():
    data = zip_bytes({"llama-server.exe": b"x", "llama-server": b"x"})
    bad = llamacpp.Asset("b1", "llama-b1-bin-win-vulkan-x64.zip", "https://dl/u", len(data), "f" * 64)
    with pytest.raises(llamacpp.InstallError, match="checksum"):
        llamacpp.install(bad, transport=serve(data))
    evil = zip_bytes({"../../escape.txt": b"x"})
    with pytest.raises(llamacpp.InstallError, match="unsafe"):
        llamacpp.install(llamacpp.Asset("b1", "llama-b1-bin-win-vulkan-x64.zip", "https://dl/u", len(evil)), transport=serve(evil))
    empty = zip_bytes({"readme.txt": b"x"})
    with pytest.raises(llamacpp.InstallError, match="didn't contain llama-server"):
        llamacpp.install(llamacpp.Asset("b1", "llama-b1-bin-win-vulkan-x64.zip", "https://dl/u", len(empty)), transport=serve(empty))
    assert llamacpp.current() == {}


def test_update_helpers():
    assert llamacpp.same_build_pattern("llama-b12207-bin-ubuntu-vulkan-x64.tar.gz") == r"\-bin\-ubuntu\-vulkan\-x64\.tar\.gz$"
    new = llamacpp.with_server("/old/llama-server -hf a:Q4 --port 8080", "/new dir/llama-server")
    assert compat.split_command(new)[0] == "/new dir/llama-server" and new.endswith("--port 8080")


def test_start_command_with_a_downloaded_server_path():
    cmd, _ = detect.build_start_command(detect.MODELS[0], detect.LlamaInfo(), True, binary="/home/u/.local/share/padou/llama.cpp/b1/llama-server")
    assert compat.split_command(cmd)[0].endswith("b1/llama-server") and "-hf" in cmd


def test_setup_downloads_when_nothing_is_installed(monkeypatch, tmp_path):
    from padou import setup, theme, ui

    asset = llamacpp.Asset("b9", "llama-b9-bin-ubuntu-vulkan-x64.tar.gz", "https://dl/u", 50_000_000)
    monkeypatch.setattr(llamacpp, "find_asset", lambda pattern, transport=None: asset)
    monkeypatch.setattr(llamacpp, "install", lambda a, progress=None, transport=None: tmp_path / "llama-server")
    system = detect.System("arch", "Arch", 16.0, detect.GPU("amd"))
    with ui.console.capture() as cap:
        binary = setup.offer_download(theme.load(theme_dir=None), system, auto=True)
    assert binary == str(tmp_path / "llama-server") and "50 MB" in cap.get() and "no administrator rights" in cap.get()


def test_setup_respects_no(monkeypatch):
    from padou import setup, theme, ui

    monkeypatch.setattr(llamacpp, "find_asset", lambda pattern, transport=None: llamacpp.Asset("b9", "n", "https://dl/u", 1))
    monkeypatch.setattr(llamacpp, "install", lambda *a, **k: pytest.fail("must not download"))
    monkeypatch.setattr(setup, "_yes", lambda q, default, auto: False)
    system = detect.System("arch", "Arch", 16.0, detect.GPU("amd"))
    with ui.console.capture():
        assert setup.offer_download(theme.load(theme_dir=None), system, auto=False) == ""


def test_install_from_a_zip_and_detection_finds_it(monkeypatch):
    name = "llama-server.exe" if compat.WINDOWS else "llama-server"
    data = zip_bytes({f"llama-b3/{name}": b"x", "llama-b3/ggml.dll": b"x"})
    server = llamacpp.install(llamacpp.Asset("b3", "llama-b3-bin-win-vulkan-x64.zip", "https://dl/z", len(data)),
                              transport=serve(data))
    assert server.exists()
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)  # llama-server isn't on PATH
    monkeypatch.setattr(detect, "_get", lambda url: None)  # and nothing is running
    llama = [b for b in detect.detect_backends() if b.kind == "llama.cpp"][0]
    assert llama.installed and llama.binary == str(server)


def test_env_for_points_at_the_downloaded_libraries(tmp_path):
    server = llamacpp.home() / "b1" / "bin" / "llama-server"
    server.parent.mkdir(parents=True)
    server.write_text("x")
    env = llamacpp.env_for(str(server))
    var = "DYLD_LIBRARY_PATH" if compat.MACOS else "LD_LIBRARY_PATH" if compat.LINUX else "PATH"
    assert env[var].split(__import__("os").pathsep)[0] == str(server.resolve().parent)
    assert llamacpp.env_for("/usr/bin/llama-server") is None
