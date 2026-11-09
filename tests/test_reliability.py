"""Phase 2: trimming, man pages, text tool calls, retries, auto-compact, speed line, web search, bench, /help."""
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from padou import bench, client as client_mod, commands, compat, paths, tools, ui, web
from padou.client import Reply, ToolCall
from padou.config import Config, WebConfig


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "data" / "sessions")


# ------------------------------------------------------------ trimming


def test_squeeze_keeps_start_and_end():
    text = "\n".join(f"line {i}" for i in range(1000))
    out = tools.squeeze(text, 400)
    assert out.startswith("line 0") and out.endswith("line 999") and "lines skipped" in out and len(out) < 520


def test_squeeze_leaves_short_text_alone():
    assert tools.squeeze("short", 100) == "short"


# ------------------------------------------------------------ man pages


@pytest.mark.skipif(compat.WINDOWS, reason="fake man is a shell script")
def test_man_page_reads_strips_overstrike_and_finds(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "man"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nprintf 'J\\bJOURNALCTL(1)\\n\\nOPTIONS\\n  -S, --since=DATE\\n    Start showing entries on or newer than DATE.\\n  -n, --lines\\n    Show the most recent lines.\\n'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    full = tools.man_page("journalctl", tmp_path, 5000, 10)
    assert "JOURNALCTL(1)" in full and "\b" not in full
    part = tools.man_page("journalctl", tmp_path, 5000, 10, find="--since")
    assert "--since=DATE" in part and "parts mentioning" in part
    with pytest.raises(tools.ToolError):
        tools.man_page("rm -rf /", tmp_path, 5000, 10)
    result = tools.execute("man_page", {"name": "journalctl", "find": "--lines"}, tmp_path, 5000, 10)
    assert result.ok and result.title == "Manual journalctl (find '--lines')"


def test_bad_json_arguments_get_an_example():
    result = tools.execute("read_file", "{path: README}", Path("."), 1000, 5)
    assert not result.ok and '{"path": "README.md"}' in result.output


# ------------------------------------------------------------ tool calls written as text


def test_text_tool_calls_become_real_calls():
    content = 'Let me check.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.txt"}}\n</tool_call>'
    rest, calls = client_mod.text_tool_calls(content)
    assert rest == "Let me check." and calls[0].name == "read_file" and json.loads(calls[0].arguments) == {"path": "a.txt"}
    assert client_mod.text_tool_calls("no calls here") == ("no calls here", [])


# ------------------------------------------------------------ the agent loop with a scripted model


class ScriptedClient:
    """Plays back replies, records what padou sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.model = "test"

    def chat(self, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        return self.replies.pop(0)


@pytest.fixture
def make_app(tmp_path):
    from padou import app as app_mod, theme

    def make(replies, **cfg_changes):
        cfg = Config()
        for k, v in cfg_changes.items():
            section, key = k.split("__")
            setattr(getattr(cfg, section), key, v)
        proj = tmp_path / "proj"
        proj.mkdir(exist_ok=True)
        (proj / "a.txt").write_text("hello\n")
        a = app_mod.App(cfg, theme.load(theme_dir=None), ScriptedClient(replies), proj.resolve())
        return a

    return make


def bad_call(i):
    return Reply(tool_calls=[ToolCall(f"c{i}", "read_file", '{"path": "missing.txt"}')])


def test_the_same_failing_call_is_stopped_after_two_tries(make_app):
    app = make_app([bad_call(1), bad_call(2), bad_call(3), Reply(content="It doesn't exist.")])
    app.ask("read missing.txt")
    tool_msgs = [m for m in app.session.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 3 and "already failed twice" in tool_msgs[-1]["content"]
    assert app.last_reply == "It doesn't exist."


def test_text_tool_call_in_a_reply_is_run(make_app):
    app = make_app([Reply(content='<tool_call>{"name": "read_file", "arguments": {"path": "a.txt"}}</tool_call>'),
                    Reply(content="It says hello.")])
    app.ask("what's in a.txt?")
    tool_msgs = [m for m in app.session.messages if m["role"] == "tool"]
    assert "hello" in tool_msgs[0]["content"] and app.last_reply == "It says hello."


def test_speed_line_under_the_answer(make_app):
    app = make_app([Reply(content="Hi!", completion_tokens=12, tokens_per_second=21.0)])
    with ui.console.capture() as cap:
        app.ask("hi")
    assert "12 tokens · 21 tok/s" in cap.get()
    app2 = make_app([Reply(content="Hi!")], ui__reply_stats=False)
    with ui.console.capture() as cap:
        app2.ask("hi")
    assert "tok/s" not in cap.get()


def test_auto_compact_keeps_the_last_exchange(make_app):
    app = make_app([Reply(content="SUMMARY"), Reply(content="answer 3")])
    msgs = app.session.messages
    for i in (1, 2):
        msgs += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    app.ctx_size, app.ctx_used = 1000, 900
    app.ask("q3")
    contents = [m.get("content") for m in app.session.messages]
    assert any("SUMMARY" in str(c) for c in contents) and "q2" in contents and "a2" in contents and "q1" not in contents
    assert contents[-1] == "answer 3"


def test_system_prompt_mentions_honesty_and_desktop(make_app, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    app = make_app([])
    text = app.system_message()["content"]
    assert "If you're not\nsure, say so" in text or "not sure" in text
    if compat.LINUX:
        assert "desktop Hyprland (Wayland)" in text


# ------------------------------------------------------------ web search


DDG_PAGE = """
<div class="result results_links web-result"><h2 class="result__title">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fubuntuhandbook.org%2Fptyxis&amp;rut=x">Ubuntu&#x27;s Default Terminal <b>Ptyxis</b></a></h2>
<a class="result__snippet" href="x">Ptyxis replaced GNOME Terminal in <b>25.10</b>.</a></div>
<div class="result results_links web-result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
<div class="result results_links web-result"><a href="https://example.org/b" class="result__a">Second</a>
<div class="result__snippet">Two</div></div>
"""


def test_duckduckgo_results_are_parsed():
    results = web.parse_duckduckgo(DDG_PAGE, 5)
    assert [r.url for r in results] == ["https://ubuntuhandbook.org/ptyxis", "https://example.org/b"]
    assert results[0].title == "Ubuntu's Default Terminal Ptyxis" and "25.10" in results[0].snippet


def _transport(handler):
    return httpx.MockTransport(handler)


def test_search_providers(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if "duckduckgo" in request.url.host:
            return httpx.Response(200, text=DDG_PAGE)
        if request.url.host == "search.local":
            return httpx.Response(200, json={"results": [{"title": "S", "url": "https://s.org", "content": "snip"}]})
        return httpx.Response(200, json={"web": {"results": [{"title": "B", "url": "https://b.org", "description": "d"}]}})

    assert web.search("ptyxis", WebConfig(), _transport(handler))[0].url.endswith("ptyxis")
    assert web.search("x", WebConfig(provider="searxng", url="http://search.local"), _transport(handler))[0].title == "S"
    assert web.search("x", WebConfig(provider="brave", api_key="k"), _transport(handler))[0].url == "https://b.org"
    assert seen[-1].headers["X-Subscription-Token"] == "k"
    with pytest.raises(web.WebError, match="api_key"):
        web.search("x", WebConfig(provider="brave"), _transport(handler))


def test_duckduckgo_block_is_explained():
    blocked = _transport(lambda r: httpx.Response(403, text="anomaly"))
    with pytest.raises(web.WebError, match="searxng"):
        web.search("x", WebConfig(), blocked)


def test_read_page_text_and_local_addresses_refused():
    page = "<html><head><title>Ptyxis</title><script>evil()</script></head><body><nav>menu</nav>" \
           "<h1>Ptyxis</h1><p>The default terminal.</p><ul><li>Tabs</li></ul></body></html>"
    t = _transport(lambda r: httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"}))
    title, text = web.read("https://example.org/p", 5000, t, check_host=lambda url: None)
    assert title == "Ptyxis" and "The default terminal." in text and "- Tabs" in text and "evil" not in text and "menu" not in text
    for bad in ("http://localhost:8080/", "http://127.0.0.1/", "http://192.168.1.150/", "file:///etc/passwd", "http://zeus.local/"):
        with pytest.raises(web.WebError):
            web._public(bad)


def test_web_command_and_status_line(make_app):
    app = make_app([Reply(content="ok")])
    commands.cmd_web(app, "on")
    assert app.web_on and "· web " in [t for _, t in app._toolbar()]
    assert "WEB is on" in app.session.messages[0]["content"]
    app.ask("news?")
    sent_tools = app.client.sent  # the tools went along with the request
    commands.cmd_web(app, "off")
    assert not app.web_on and "WEB is on" not in app.session.messages[0]["content"]


def test_web_tools_are_only_offered_when_on(make_app, monkeypatch):
    offered = []
    app = make_app([Reply(content="a"), Reply(content="b")])
    real = app.client.chat
    app.client.chat = lambda messages, **kw: offered.append([d["function"]["name"] for d in kw.get("tools") or []]) or real(messages, **kw)
    app.ask("x")
    app.web_on = True
    app.ask("y")
    assert "web_search" not in offered[0] and "web_search" in offered[1]


# ------------------------------------------------------------ bench


def test_bench_scoring():
    t = bench.Task("k", "q", ("read_file",), ("8471", "go|golang"))
    assert bench.score(t, ["read_file"], "Port 8471, written in Go") == (True, "")
    assert not bench.score(t, ["list_dir"], "Port 8471, written in Go")[0]
    assert "8471" in bench.score(t, ["read_file"], "written in Go")[1]


def test_bench_task_runs_tools_in_the_practice_folder():
    folder = bench._practice_folder()
    try:
        fake = ScriptedClient([Reply(tool_calls=[ToolCall("1", "read_file", '{"path": "config.ini"}')]),
                               Reply(content="It uses port 8471.")])
        outcome = bench.run_task(bench.TASKS[0], fake, Config(), folder, "system", {})
        assert outcome.passed and outcome.used == ["read_file"]
        assert "8471" in fake.sent[1][-1]["content"]  # the real file was read
    finally:
        import shutil
        shutil.rmtree(folder)


# ------------------------------------------------------------ /help


def test_help_groups_cover_every_visible_command():
    visible = [n for n in commands.COMMANDS if n not in commands.ALIASES]
    grouped = [n for _, names in commands.HELP_GROUPS for n in names]
    assert sorted(visible) == sorted(grouped)
    with ui.console.capture() as cap:
        commands.cmd_help(SimpleNamespace(theme=__import__("padou.theme").theme.load(theme_dir=None)), "")
    assert "/models [n|remove n]" in cap.get() and "/undo [list]" in cap.get()
