"""Phase 1 (speed): thinking on/off, prompt cache, live stats, Esc to stop, tuned setup."""
import json
from types import SimpleNamespace

import httpx

from padou import commands, detect, models, ui
from padou.client import Client
from padou.config import ServerConfig

LLAMA = "llama-server -hf bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M -ngl 99 --jinja --reasoning-budget 0 -c 8192 --port 8080"


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def client_with(body: str, seen: list | None = None) -> Client:
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    c = Client(ServerConfig())
    c.http = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def delta(**d):
    return {"choices": [{"delta": d}]}


def test_extra_fields_reach_the_server_and_reasoning_is_reported_not_shown():
    seen, progress, text = [], [], []
    body = sse(delta(reasoning_content="hmm"), delta(reasoning_content="ok"), delta(content="Hi"), delta(content="!"))
    reply = client_with(body, seen).chat([{"role": "user", "content": "hi"}], extra={"cache_prompt": True},
                                         on_text=text.append, on_progress=progress.append)
    assert seen[0]["cache_prompt"] is True
    assert reply.content == "Hi!" and "".join(text) == "Hi!"
    assert progress == ["thinking", "thinking"]


def test_esc_stops_the_stream_and_keeps_what_arrived():
    body = sse(*(delta(content=w) for w in ["one ", "two ", "three ", "four "]))
    text = []
    reply = client_with(body).chat([], on_text=text.append, should_stop=lambda: len(text) >= 2)
    assert reply.stopped and reply.content == "one two " and reply.finish_reason == "stopped"


def test_stop_drops_a_half_written_tool_call():
    body = sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "read_file", "arguments": "{\"pa"}}]}}]},
               delta(content="x"))
    calls = []
    reply = client_with(body).chat([], on_progress=calls.append, should_stop=lambda: bool(calls))
    assert reply.stopped and reply.tool_calls == []


def test_request_extra_per_server():
    llama = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    assert models.request_extra(llama, False) == {"cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}}
    assert models.request_extra(llama, True)["chat_template_kwargs"] == {"enable_thinking": True}
    ollama = ServerConfig(url="http://localhost:11434/v1", start_command="ollama serve", model="qwen3.5:4b")
    assert models.request_extra(ollama, False) == {"reasoning_effort": "none"}
    assert models.request_extra(ollama, True) == {}
    other = ServerConfig(url="http://192.168.1.9:8080/v1", start_command="")
    assert models.request_extra(other, False) == {}


def test_thinking_block_in_start_command():
    assert models.thinking_blocked(LLAMA)
    new = models.without_thinking_block(LLAMA)
    assert "--reasoning-budget" not in new and "-c 8192" in new and not models.thinking_blocked(new)
    assert not models.thinking_blocked("llama-server --reasoning-budget -1")


def test_setup_uses_16k_with_enough_ram_and_cache_reuse():
    gpu = detect.GPU("amd")
    assert detect.context_for(detect.System("arch", "Arch", 15.3, gpu)) == 16384
    assert detect.context_for(detect.System("arch", "Arch", 7.6, gpu)) == 8192
    info = detect.LlamaInfo(help_text="  --jinja\n  --reasoning-budget N\n  --no-mmproj\n  --cache-reuse N\n")
    cmd, _ = detect.build_start_command(detect.MODELS[0], info, True, context=16384)
    assert "-c 16384" in cmd and "--cache-reuse 256" in cmd


def test_escape_key_detection():
    assert ui.is_escape("\x1b")
    assert not ui.is_escape("\x1b[A")  # arrow up
    assert not ui.is_escape("abc")


def test_reply_view_stats():
    from padou import theme
    view = ui.ReplyView(theme.load(theme_dir=None), live=False)
    assert view.stats().endswith("s")
    view.tokens = 40
    view._first_token = view._started - 2
    assert "40 tokens" in view.stats() and "tok/s" in view.stats()


def _app(cfg):
    from padou import theme
    return SimpleNamespace(theme=theme.load(theme_dir=None), cfg=SimpleNamespace(server=cfg, source=None), thinking=False)


def test_think_offers_to_unblock_the_server(monkeypatch):
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    app = _app(cfg)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    commands.cmd_think(app, "on")
    assert app.thinking is False and models.thinking_blocked(cfg.start_command)

    restarted, saved = [], []
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(commands, "cmd_server", lambda a, arg: restarted.append(arg))
    monkeypatch.setattr(commands, "_save_model", lambda a, values: saved.append(values))
    commands.cmd_think(app, "on")
    assert app.thinking is True and restarted == ["restart"]
    assert "--reasoning-budget" not in saved[0]["start_command"]
    commands.cmd_think(app, "off")
    assert app.thinking is False


def test_doctor_tuning_tip(monkeypatch):
    from padou import doctor
    from padou.config import Config
    monkeypatch.setattr(detect, "_ram_gb", lambda: 16.0)
    cfg = Config()
    cfg.server = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    assert "16K context" in doctor.tuning_tip(cfg, 8192) and "prompt-cache" in doctor.tuning_tip(cfg, 8192)
    cfg.server.start_command = LLAMA + " --cache-reuse 256"
    assert doctor.tuning_tip(cfg, 16384) == ""


def test_status_line_shows_thinking():
    from padou import app
    a = object.__new__(app.App)
    a.last_speed, a.ctx_used, a.ctx_size, a.model_label = 0, 0, 0, "Qwen3.5 4B"
    a.cfg = SimpleNamespace(ui=SimpleNamespace(animation=False))
    a.update_available, a.write_roots, a.thinking = None, [], True
    assert "thinking" in "".join(t for _, t in a._toolbar())


def test_setup_restarts_a_running_server_so_new_settings_apply(monkeypatch):
    from padou import server, setup, theme
    srv = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA.replace("-c 8192", "-c 16384"))
    stopped, started = [], []
    monkeypatch.setattr(setup.Client, "health", lambda self: (not stopped, "ok"))
    monkeypatch.setattr(server, "find", lambda cfg: [server.Proc(42, "llama-server -c 8192", 900)])
    monkeypatch.setattr(server, "stop", lambda procs: stopped.append(procs) or [42])
    monkeypatch.setattr(server, "start", lambda cfg: started.append(cfg.start_command) or (None, "test: not really starting"))
    setup._start_now(theme.load(theme_dir=None), srv, None, auto=True)
    assert stopped and started == [srv.start_command]


def test_16gb_laptop_with_graphics_carve_out_gets_16k():
    assert detect.context_for(detect.System("arch", "Arch", 13.5, detect.GPU("amd"))) == 16384


def test_warns_when_the_running_server_has_another_model():
    cfg = ServerConfig(url="http://127.0.0.1:8080/v1", start_command=LLAMA)
    nine = SimpleNamespace(list_models=lambda: ["bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M"])
    four = SimpleNamespace(list_models=lambda: ["bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M"])
    assert models.loaded_mismatch(cfg, nine) == "bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M"
    assert models.loaded_mismatch(cfg, four) == ""


def test_clear_starts_fresh_and_redraws_the_header(monkeypatch):
    from rich.text import Text
    from padou import theme
    calls = []
    monkeypatch.setattr(ui, "clear", lambda scrollback=False: calls.append(scrollback))
    app = SimpleNamespace(theme=theme.load(theme_dir=None), header=Text("BANNER"), new_session=lambda: calls.append("new"))
    with ui.console.capture() as cap:
        commands.cmd_clear(app, "")
    assert calls == ["new", True] and "BANNER" in cap.get() and "fresh conversation" in cap.get()
