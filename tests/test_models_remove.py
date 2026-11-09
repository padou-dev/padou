"""/models remove: frees the space a downloaded model uses, never the one in use."""
import os
from types import SimpleNamespace

import pytest

from padou import commands, models, theme, ui, writes
from padou.config import ServerConfig

LLAMA = "llama-server -hf bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M -ngl 99 --jinja --port 8080"
PHI = "bartowski/microsoft_Phi-4-mini-instruct-GGUF:Q4_K_M"


@pytest.fixture
def caches(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setenv("LLAMA_CACHE", str(tmp_path / "llama"))
    (tmp_path / "hub").mkdir()
    (tmp_path / "llama").mkdir()
    return tmp_path


@pytest.fixture
def trash(monkeypatch):
    moved = []

    def fake(path):
        moved.append(path.name)
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
        else:
            path.unlink()

    monkeypatch.setattr(writes, "move_to_trash", fake)
    return moved


def app_for(cfg):
    return SimpleNamespace(theme=theme.load(theme_dir=None), cfg=SimpleNamespace(server=cfg),
                           client=SimpleNamespace(list_models=lambda: []))


def test_plan_takes_the_whole_hub_folder(caches):
    snap = caches / "hub/models--bartowski--microsoft_Phi-4-mini-instruct-GGUF/snapshots/abc"
    snap.mkdir(parents=True)
    (snap / "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf").write_bytes(b"x" * 1000)
    plan = models.removal_plan(PHI)
    assert [p.name for p in plan.paths] == ["models--bartowski--microsoft_Phi-4-mini-instruct-GGUF"]
    assert plan.size == 1000


def test_plan_keeps_other_quantisations(caches):
    snap = caches / "hub/models--bartowski--microsoft_Phi-4-mini-instruct-GGUF/snapshots/abc"
    snap.mkdir(parents=True)
    (snap / "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf").write_bytes(b"x" * 10)
    (snap / "microsoft_Phi-4-mini-instruct-Q8_0.gguf").write_bytes(b"x" * 20)
    plan = models.removal_plan(PHI)
    assert [p.name for p in plan.paths] == ["microsoft_Phi-4-mini-instruct-Q4_K_M.gguf"]


def test_plan_finds_llama_cpp_cache_files_and_their_sidecars(caches):
    base = "bartowski_microsoft_Phi-4-mini-instruct-GGUF_microsoft_Phi-4-mini-instruct-Q4_K_M.gguf"
    for name in (base, base + ".json", base + ".etag"):
        (caches / "llama" / name).write_bytes(b"x" * 5)
    (caches / "llama" / "bartowski_Qwen_Qwen3.5-4B-GGUF_Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"keep")
    plan = models.removal_plan(PHI)
    assert sorted(p.name for p in plan.paths) == sorted([base, base + ".json", base + ".etag"])
    assert plan.size == 15


def test_remove_command_moves_the_model_to_the_trash(caches, trash, monkeypatch):
    snap = caches / "hub/models--bartowski--microsoft_Phi-4-mini-instruct-GGUF/snapshots/abc"
    snap.mkdir(parents=True)
    (snap / "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf").write_bytes(b"x")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    commands.cmd_models(app_for(cfg), f"remove {PHI}")
    assert trash == ["models--bartowski--microsoft_Phi-4-mini-instruct-GGUF"]
    assert not models.is_downloaded(PHI)


def test_remove_command_says_no_by_default(caches, trash, monkeypatch):
    snap = caches / "hub/models--bartowski--microsoft_Phi-4-mini-instruct-GGUF/snapshots/abc"
    snap.mkdir(parents=True)
    (snap / "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf").write_bytes(b"x")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    commands.cmd_models(app_for(cfg), f"remove {PHI}")
    assert trash == [] and models.is_downloaded(PHI)


def test_remove_command_refuses_the_model_in_use(caches, trash, monkeypatch, capsys):
    snap = caches / "hub/models--bartowski--Qwen_Qwen3.5-4B-GGUF/snapshots/abc"
    snap.mkdir(parents=True)
    (snap / "Qwen_Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"x")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: pytest.fail("must not even ask"))
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    commands.cmd_models(app_for(cfg), "remove bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M")
    assert trash == [] and models.is_downloaded("bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M")


def test_remove_needs_a_model(caches, trash, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: pytest.fail("must not ask"))
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    commands.cmd_models(app_for(cfg), "remove")
    commands.cmd_models(app_for(cfg), "remove 99")
    assert trash == []
