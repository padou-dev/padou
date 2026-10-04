import tomllib

from padou import config, detect, models
from padou.config import ServerConfig

LLAMA = "llama-server -hf bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M -ngl 99 --jinja --port 8080"


def test_kind():
    assert models.kind(ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)) == "llama.cpp"
    assert models.kind(ServerConfig(url="http://localhost:11434/v1", start_command="")) == "ollama"
    assert models.kind(ServerConfig(url="http://192.168.1.9:8080/v1", start_command=LLAMA)) == "other"
    assert models.kind(ServerConfig(url="http://127.0.0.1:8080/v1", start_command="llama-server -m x.gguf")) == "other"


def test_with_model_swaps_only_the_model():
    new = models.with_model(LLAMA, "unsloth/Ministral-3-3B-Instruct-2512-GGUF:Q4_K_M")
    assert new == LLAMA.replace("bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M", "unsloth/Ministral-3-3B-Instruct-2512-GGUF:Q4_K_M")
    assert models.current_hf(ServerConfig(start_command=new)).startswith("unsloth/")


def test_is_downloaded_reads_the_hf_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setenv("LLAMA_CACHE", str(tmp_path / "llama"))
    hf = "bartowski/microsoft_Phi-4-mini-instruct-GGUF:Q4_K_M"
    (tmp_path / "llama").mkdir()
    assert not models.is_downloaded(hf)
    snap = tmp_path / "hub/models--bartowski--microsoft_Phi-4-mini-instruct-GGUF/snapshots/abc"
    snap.mkdir(parents=True)
    (snap / "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf").write_text("x")
    assert models.is_downloaded(hf)
    assert not models.is_downloaded("bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M")


def test_entries_and_pick_for_llama(monkeypatch):
    monkeypatch.setattr(models, "is_downloaded", lambda hf: "4B" in hf)
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    items = models.entries(cfg, [])
    assert len(items) == len(detect.MODELS)
    assert [e.label for e in items if e.current] == ["Qwen3.5 4B"]
    assert models.pick(items, "1").label == "Qwen3.5 2B"
    assert models.pick(items, "phi").label == "Phi-4-mini"
    assert models.pick(items, "ministral 3 8b").label == "Ministral 3 8B"
    assert models.pick(items, "99") is None and models.pick(items, "nope") is None


def test_custom_model_in_config_is_listed_first(monkeypatch):
    monkeypatch.setattr(models, "is_downloaded", lambda hf: True)
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command="llama-server -hf me/My-GGUF:Q5_K_M")
    items = models.entries(cfg, [])
    assert items[0].current and items[0].ident == "me/My-GGUF:Q5_K_M"


def test_entries_for_ollama():
    cfg = ServerConfig(url="http://localhost:11434/v1", model="granite4:micro")
    items = models.entries(cfg, ["granite4:micro", "llama3.2:latest"])
    cur = [e for e in items if e.current]
    assert len(cur) == 1 and cur[0].label == "Granite 4.0 Micro" and cur[0].downloaded
    assert any(e.ident == "llama3.2" and e.downloaded for e in items)


def test_update_section_keeps_comments(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('# top\n[server]\nurl = "http://127.0.0.1:8080/v1"  # address\nmodel = "a"\n\n[chat]\ntemperature = 0.6\n')
    config.update_section("server", {"model": "b", "model_label": 'Phi "mini"'}, p)
    text = p.read_text()
    data = tomllib.loads(text)
    assert data["server"]["model"] == "b" and data["server"]["model_label"] == 'Phi "mini"'
    assert "# top" in text and "# address" in text and data["chat"]["temperature"] == 0.6
