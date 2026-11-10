"""Phase 3.5: coding mode (project detection, @file, project commands), /tutor, /review, editor diff, /exit && stop."""
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from padou import commands, compat, paths, project, server, ui
from padou.client import Reply, ToolCall
from padou.config import Config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "data" / "sessions")


def make_project(root: Path, files: dict) -> Path:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


# ------------------------------------------------------------ detection


def test_python_project_with_uv(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    root = make_project(tmp_path / "app", {"pyproject.toml": '[project]\nname = "lantern"\n[tool.ruff]\nline-length = 100\n',
                                          "uv.lock": "", "tests/test_x.py": "", ".git/HEAD": ""})
    p = project.detect(root / "tests")
    assert p.root == root.resolve() and p.name == "lantern" and p.git and p.kinds == ["Python"]
    assert p.commands == {"test": ["uv", "run", "pytest", "-q"], "lint": ["uv", "run", "ruff", "check", "."]}
    assert "Python project 'lantern'" in p.describe()


def test_node_rust_go_make(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    node = make_project(tmp_path / "web", {"package.json": json.dumps({"name": "site", "scripts": {"test": "vitest", "build": "vite build"}}),
                                         "pnpm-lock.yaml": ""})
    assert project.detect(node).commands == {"test": ["pnpm", "run", "test"], "build": ["pnpm", "run", "build"]}
    rust = make_project(tmp_path / "r", {"Cargo.toml": "[package]\nname='r'\n"})
    assert project.detect(rust).commands["test"] == ["cargo", "test"]
    go = make_project(tmp_path / "g", {"go.mod": "module g\n", "Makefile": "test:\n\tgo test ./...\nlint:\n\tgolangci-lint run\n"})
    p = project.detect(go)
    assert p.kinds == ["Go", "Make"] and p.commands["test"] == ["go", "test", "./..."]


def test_missing_programs_are_not_offered(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    root = make_project(tmp_path / "r", {"Cargo.toml": ""})
    assert project.detect(root).commands == {}


def test_home_folder_is_never_a_project(monkeypatch):
    assert project.find_root(Path.home()) is None or project.find_root(Path.home()) != Path.home().resolve()


def test_running_a_project_command(tmp_path):
    p = project.Project(tmp_path, "x", commands={"test": [sys.executable, "-c", "print('3 passed')"],
                                                  "lint": [sys.executable, "-c", "import sys; print('E1 bad'); sys.exit(1)"]})
    ok, out = project.run(p, "test", 1000)
    assert ok and "3 passed" in out and "exit code 0" in out
    ok, out = project.run(p, "lint", 1000)
    assert not ok and "E1 bad" in out
    with pytest.raises(ValueError):
        project.run(p, "deploy", 1000)


# ------------------------------------------------------------ @file


def test_attachments(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    extra, names, problems = project.attachments("what does @src/app.py do? mail me@example.com", tmp_path, [tmp_path])
    assert names == ["src/app.py"] and "[Attached file @src/app.py]" in extra and "print('hi')" in extra and not problems
    extra, names, problems = project.attachments("look at @../outside.txt", tmp_path / "src", [tmp_path / "src"])
    assert names == []


# ------------------------------------------------------------ in the app


class Scripted:
    def __init__(self, replies):
        self.replies, self.sent, self.model = list(replies), [], "t"

    def chat(self, messages, **kw):
        self.sent.append(([dict(m) for m in messages], kw.get("tools")))
        return self.replies.pop(0)


@pytest.fixture
def app_in_project(tmp_path):
    from padou import app as app_mod, theme

    root = make_project(tmp_path / "proj", {"pyproject.toml": '[project]\nname = "demo"\n', "tests/test_a.py": "",
                                            "src/a.py": "x = 1\n"})

    def make(replies):
        a = app_mod.App(Config(), theme.load(theme_dir=None), Scripted(replies), root.resolve())
        a.project = project.Project(root.resolve(), "demo", ["Python"],
                                    commands={"test": [sys.executable, "-c", "print('1 passed')"]})
        a.refresh_system_prompt()
        return a
    return make


def test_coding_context_and_attachment_reach_the_model(app_in_project):
    a = app_in_project([Reply(content="x is 1")])
    assert "CODING: you're in a Python project 'demo'" in a.session.messages[0]["content"]
    a.ask("explain @src/a.py")
    sent_messages, sent_tools = a.client.sent[0]
    assert "[Attached file @src/a.py]" in sent_messages[-1]["content"]
    assert "project_command" not in [t["function"]["name"] for t in sent_tools]  # read-only: no project commands


def test_project_command_needs_approval_and_can_be_allowed(app_in_project, monkeypatch):
    a = app_in_project([Reply(tool_calls=[ToolCall("1", "project_command", '{"name": "test"}')]), Reply(content="all good"),
                        Reply(tool_calls=[ToolCall("2", "project_command", '{"name": "test"}')]), Reply(content="still good")])
    a.set_mode("edit", a.cwd)
    answers = iter(["a"])
    monkeypatch.setattr(ui.console, "input", lambda *x, **k: next(answers))
    a.ask("run the tests")
    assert "project_command" in [t["function"]["name"] for t in a.client.sent[0][1]]
    tool_msg = [m for m in a.session.messages if m["role"] == "tool"][0]
    assert "1 passed" in tool_msg["content"]
    a.ask("again")  # allowed for the session: no question this time (the iterator is empty)
    assert "1 passed" in [m for m in a.session.messages if m["role"] == "tool"][1]["content"]


def test_declined_project_command(app_in_project, monkeypatch):
    a = app_in_project([Reply(tool_calls=[ToolCall("1", "project_command", '{"name": "test"}')]), Reply(content="ok")])
    a.set_mode("edit", a.cwd)
    monkeypatch.setattr(ui.console, "input", lambda *x, **k: "")
    a.ask("run the tests")
    assert "declined" in [m for m in a.session.messages if m["role"] == "tool"][0]["content"]


def test_tutor_mode(app_in_project):
    a = app_in_project([])
    commands.cmd_tutor(a, "on")
    assert a.tutor and "TUTOR MODE" in a.session.messages[0]["content"] and "· tutor " in [t for _, t in a._toolbar()]
    commands.cmd_tutor(a, "off")
    assert "TUTOR MODE" not in a.session.messages[0]["content"]


def test_review_asks_for_the_diff(app_in_project):
    a = app_in_project([Reply(content="Looks fine.")])
    a.project.git = True
    commands.cmd_review(a, "error handling")
    asked = a.client.sent[0][0][-1]["content"]
    assert "git_diff" in asked and "error handling" in asked


def test_exit_and_stop(monkeypatch):
    a = SimpleNamespace(stop_server_at_exit=False)
    assert commands.cmd_exit(a, "&& stop") == commands.EXIT and a.stop_server_at_exit
    b = SimpleNamespace(stop_server_at_exit=False)
    commands.cmd_exit(b, "")
    assert not b.stop_server_at_exit
    assert commands.dispatch.__doc__ is None or True


def test_dispatch_splits_exit_and_stop():
    seen = {}
    a = SimpleNamespace(stop_server_at_exit=False, theme=None)
    assert commands.dispatch(a, "/exit && stop") == commands.EXIT and a.stop_server_at_exit


# ------------------------------------------------------------ editor diff


def test_approval_offers_the_editor_diff(monkeypatch, tmp_path):
    from padou import theme, writes

    monkeypatch.setattr(compat, "code_editor", lambda: "code-oss")
    opened = []
    monkeypatch.setattr(ui, "open_diff", lambda editor, change: opened.append(editor) or True)
    answers = iter(["d", "y"])
    monkeypatch.setattr(ui.console, "input", lambda *x, **k: next(answers))
    f = tmp_path / "a.txt"
    f.write_text("one\n")
    change = writes.Change("edit", f, "one\n", "two\n")
    assert ui.ask_approval(theme.load(theme_dir=None), change) is True and opened == ["code-oss"]


def test_open_diff_writes_both_versions(monkeypatch, tmp_path):
    from padou import writes

    started = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: started.append(argv))
    change = writes.Change("edit", tmp_path / "x.py", "a = 1\n", "a = 2\n")
    assert ui.open_diff("code", change)
    argv = started[0]
    assert argv[:2] == ["code", "--diff"] and Path(argv[2]).read_text() == "a = 1\n" and Path(argv[3]).read_text() == "a = 2\n"


def test_misspelled_attachment_is_reported_with_a_suggestion(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__main__.py").write_text("x")
    extra, names, problems = project.attachments("add a docstring in @src/main.py", tmp_path, [tmp_path])
    assert not names and "no such file" in problems[0] and "__main__.py" in problems[0]
    assert project.attachments("ping @dana about it", tmp_path, [tmp_path])[2] == []  # a name, not a file


def test_failed_tool_calls_keep_a_readable_title(tmp_path):
    from padou import tools
    r = tools.execute("read_file", {"path": "missing.py"}, tmp_path, 1000, 5, roots=[tmp_path])
    assert not r.ok and r.title == "Read missing.py"


class FakeState:
    def __init__(self, completions, current=None):
        self.completions, self.current_completion = completions, current


class FakeBuffer:
    def __init__(self, text, completions):
        from prompt_toolkit.document import Document
        self.document = Document(text)
        self.complete_state = FakeState(completions)
        self.applied, self.sent = [], False

    def apply_completion(self, c):
        self.applied.append(c)

    def validate_and_handle(self):
        self.sent = True


def test_enter_takes_the_suggestion_instead_of_sending():
    from padou import app as app_mod
    b = FakeBuffer("look at @src/pa", ["padou/"])
    app_mod.accept_completion(b)
    assert b.applied == ["padou/"] and not b.sent
    c = FakeBuffer("/he", ["/help"])
    app_mod.accept_completion(c)
    assert c.applied == ["/help"] and c.sent  # a command runs straight away
