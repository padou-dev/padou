from pathlib import Path

from padou import app, compat, tools


def test_padou_subcommands_typed_inside_padou_get_a_hint():
    assert "/doctor" in app.shell_command_hint("padou doctor")
    assert "/exit" in app.shell_command_hint("Padou setup")
    assert app.shell_command_hint("padou is great") == ""
    assert app.shell_command_hint("how do I run padou doctor?") == ""


def test_powershell_commands_get_a_short_title(monkeypatch):
    monkeypatch.setattr(compat, "WINDOWS", True)
    monkeypatch.setattr(compat, "MACOS", False)
    argv = next(c.argv for c in tools._windows_commands() if c.name == "uptime")
    monkeypatch.setattr(tools, "run_command", lambda *a, **k: "$ " + compat.join_command(list(argv)) + "\nBooted: 2026-10-09 17:55  (up 0 days, 2 hours, 3 minutes)")
    result = tools.execute("run_command", {"name": "uptime"}, Path.cwd(), 10000, 10)
    assert result.title == "Run uptime · When Windows last started"


def test_short_commands_keep_their_real_title(monkeypatch):
    monkeypatch.setattr(compat, "WINDOWS", True)
    monkeypatch.setattr(tools, "run_command", lambda *a, **k: "$ ipconfig\nWindows IP Configuration")
    assert tools.execute("run_command", {"name": "ip_addresses"}, Path.cwd(), 10000, 10).title == "Run ipconfig"


def test_status_line_shows_the_current_mode(tmp_path):
    from types import SimpleNamespace

    a = object.__new__(app.App)
    a.last_speed, a.ctx_used, a.ctx_size, a.model_label = 0, 0, 0, "Qwen3.5 9B"
    a.cfg = SimpleNamespace(ui=SimpleNamespace(animation=False))
    a.update_available, a.write_roots = None, []
    assert "read-only" in "".join(t for _, t in a._toolbar())
    a.write_roots = [tmp_path]
    bar = "".join(t for _, t in a._toolbar())
    assert "✎ write:" in bar and "read-only" not in bar
