from padou import models, paths, server
from padou.config import ServerConfig


def test_progress_shows_latest_line_of_this_start(tmp_path, monkeypatch):
    log = tmp_path / "llama-server.log"
    monkeypatch.setattr(paths, "SERVER_LOG", log)
    log.write_bytes(
        b"old run line\n===== padou started: llama-server -hf x/y =====\n"
        b"common_download_file: downloading model\n"
        b"[=====     ]  40%\r[========  ]  80%   3.1 GB / 6.2 GB\r"
    )
    assert server.progress() == "downloading " + "━" * 19 + "─" * 5 + "  80%  3.1 GB / 6.2 GB"


def test_progress_is_empty_before_the_server_writes_anything(tmp_path, monkeypatch):
    log = tmp_path / "llama-server.log"
    monkeypatch.setattr(paths, "SERVER_LOG", log)
    assert server.progress() == ""
    log.write_bytes(b"===== padou started: llama-server =====\n")
    assert server.progress() == ""


def test_progress_shortens_long_lines(tmp_path, monkeypatch):
    log = tmp_path / "llama-server.log"
    monkeypatch.setattr(paths, "SERVER_LOG", log)
    log.write_bytes(b"===== padou started: x =====\n" + b"a" * 200 + b"\n")
    assert len(server.progress(width=40)) == 40


def test_first_download_gets_an_hour(monkeypatch):
    cfg = ServerConfig(start_command="llama-server -hf org/model-GGUF:Q4_K_M", startup_timeout=300)
    monkeypatch.setattr(models, "is_downloaded", lambda hf: False)
    assert models.ready_timeout(cfg) == 3600
    monkeypatch.setattr(models, "is_downloaded", lambda hf: True)
    assert models.ready_timeout(cfg) == 300


def test_progress_bar_without_sizes_and_plain_lines():
    assert server.progress_bar("50%") == "downloading " + "━" * 12 + "─" * 12 + "  50%"
    assert server.progress_bar("load_tensors: loading model") == ""
    assert server.progress_bar("done 100.0 %").endswith("100%")
