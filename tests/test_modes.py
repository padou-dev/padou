"""Phase 3: /mode read | edit | admin, sudo for system files, audit log."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from padou import commands, compat, paths, ui, writes
from padou.writes import WriteError

posix_only = pytest.mark.skipif(compat.WINDOWS, reason="sudo is Linux/macOS")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "data" / "sessions")
    monkeypatch.setattr(writes, "_HOME_APP_FOLDERS", set())


@pytest.fixture
def trash(tmp_path, monkeypatch):
    bin_dir = tmp_path / "trash-bin"
    bin_dir.mkdir()
    monkeypatch.setattr(writes, "move_to_trash", lambda p: os.replace(p, bin_dir / p.name))
    return bin_dir


@pytest.fixture
def fake_sudo(monkeypatch):
    """Pretend every change needs sudo, and run the sudo commands without sudo, recording them."""
    ran = []

    def runner(argv):
        ran.append(argv)
        return subprocess.run(argv).returncode

    monkeypatch.setattr(writes, "SUDO_RUNNER", runner)
    monkeypatch.setattr(writes, "_needs_elevation", lambda change: True)
    return ran


def audit(tmp_path):
    return [json.loads(l) for l in (tmp_path / "state" / "changes.log").read_text().splitlines()]


# ------------------------------------------------------------ where admin may write


def test_admin_reaches_outside_the_edit_folders(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "a.txt").write_text("hi\n")
    with pytest.raises(WriteError, match="outside the edit folders"):
        writes.propose("edit_file", {"path": str(other / "a.txt"), "old_text": "hi", "new_text": "yo"}, tmp_path, [tmp_path / "p"])
    c = writes.propose("edit_file", {"path": str(other / "a.txt"), "old_text": "hi", "new_text": "yo"}, tmp_path, [], admin=True)
    assert c.admin and c.kind == "edit"


def test_read_only_refuses_every_change(tmp_path):
    with pytest.raises(WriteError, match="read-only right now"):
        writes.propose("write_file", {"path": "x.txt", "content": "x"}, tmp_path, [])


@pytest.mark.skipif(not compat.LINUX, reason="Linux paths")
def test_admin_never_touches_proc_boot_or_secrets(tmp_path):
    for bad in ("/proc/sys/kernel/hostname", "/boot/loader.conf", "/sys/power/state"):
        with pytest.raises(WriteError, match="never changes"):
            writes.propose("write_file", {"path": bad, "content": "x"}, tmp_path, [], admin=True)
    with pytest.raises(WriteError, match="secrets"):
        writes.propose("write_file", {"path": str(Path.home() / ".ssh" / "new_key"), "content": "x"}, tmp_path, [], admin=True)


def test_admin_never_deletes_home_or_a_main_system_folder(tmp_path):
    for essential in [Path.home(), *[d for d in writes.SYSTEM_DIRS if d.is_dir()][:3]]:
        with pytest.raises(WriteError):
            writes.propose("delete_path", {"path": str(essential)}, tmp_path, [], admin=True)


def test_admin_deletes_need_the_name_typed_even_for_files(tmp_path, trash):
    f = tmp_path / "notes.txt"
    f.write_text("x")
    assert writes.propose("delete_path", {"path": str(f)}, tmp_path, [tmp_path / "x"], admin=True).needs_typed_confirmation
    (tmp_path / "w").mkdir()
    g = tmp_path / "w" / "n.txt"
    g.write_text("x")
    assert not writes.propose("delete_path", {"path": str(g)}, tmp_path, [(tmp_path / "w").resolve()]).needs_typed_confirmation


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write anywhere")
def test_files_you_cant_write_need_sudo(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "conf").write_text("a=1\n")
    os.chmod(locked / "conf", 0o444)
    os.chmod(locked, 0o555)
    try:
        c = writes.propose("edit_file", {"path": str(locked / "conf"), "old_text": "a=1", "new_text": "a=2"}, tmp_path, [], admin=True)
        assert c.elevated
        mine = tmp_path / "mine.txt"
        mine.write_text("x")
        assert not writes.propose("delete_path", {"path": str(mine)}, tmp_path, [], admin=True).elevated
    finally:
        os.chmod(locked, 0o755)


def test_windows_asks_for_an_admin_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(compat, "WINDOWS", True)
    monkeypatch.setattr(writes, "_needs_elevation", lambda change: True)
    with pytest.raises(WriteError, match="Run as administrator"):
        writes.propose("write_file", {"path": str(tmp_path / "x.txt"), "content": "x"}, tmp_path, [], admin=True)


# ------------------------------------------------------------ sudo changes and undo


@posix_only
def test_sudo_edit_and_undo(tmp_path, fake_sudo):
    f = tmp_path / "hosts"
    f.write_text("127.0.0.1 localhost\n")
    os.chmod(f, 0o640)
    c = writes.propose("edit_file", {"path": str(f), "old_text": "localhost", "new_text": "localhost zeus"}, tmp_path, [], admin=True)
    assert c.elevated
    applied = writes.apply(c, "s1")
    assert f.read_text() == "127.0.0.1 localhost zeus\n"
    assert oct(f.stat().st_mode & 0o777) == "0o640"  # cp onto the file keeps its permissions
    assert fake_sudo[0][0] == "cp"
    writes.undo(applied, "s1")
    assert f.read_text() == "127.0.0.1 localhost\n"
    log = audit(tmp_path)
    assert [e["action"] for e in log] == ["apply", "undo"] and log[0]["sudo"] and log[0]["mode"] == "admin"


@posix_only
def test_sudo_create_with_new_folders_and_undo(tmp_path, fake_sudo):
    target = tmp_path / "etc" / "app" / "app.conf"
    c = writes.propose("write_file", {"path": str(target), "content": "x=1\n"}, tmp_path, [], admin=True)
    applied = writes.apply(c, "s1")
    assert target.read_text() == "x=1\n" and ["mkdir", "-p", str(target.parent)] in fake_sudo
    writes.undo(applied, "s1")
    assert not target.exists() and not (tmp_path / "etc").exists()


@posix_only
def test_sudo_move_and_undo(tmp_path, fake_sudo):
    src = tmp_path / "a.conf"
    src.write_text("x")
    c = writes.propose("move_path", {"source": str(src), "destination": str(tmp_path / "b.conf")}, tmp_path, [], admin=True)
    applied = writes.apply(c, "s1")
    assert (tmp_path / "b.conf").exists() and not src.exists()
    writes.undo(applied, "s1")
    assert src.exists() and not (tmp_path / "b.conf").exists()


@posix_only
def test_sudo_delete_keeps_a_copy_and_undo_restores(tmp_path, fake_sudo):
    folder = tmp_path / "old-configs"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "x.conf").write_text("keep me")
    c = writes.propose("delete_path", {"path": str(folder)}, tmp_path, [], admin=True)
    assert c.elevated and c.needs_typed_confirmation and "padou kept a copy" in c.summary()
    applied = writes.apply(c, "s1")
    assert not folder.exists()
    assert fake_sudo[-1][:2] == ["rm", "-rf"]
    writes.undo(applied, "s1")
    assert (folder / "sub" / "x.conf").read_text() == "keep me"


@posix_only
def test_failed_sudo_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(writes, "SUDO_RUNNER", lambda argv: 1)  # wrong password
    monkeypatch.setattr(writes, "_needs_elevation", lambda change: True)
    f = tmp_path / "x.conf"
    f.write_text("a")
    c = writes.propose("edit_file", {"path": str(f), "old_text": "a", "new_text": "b"}, tmp_path, [], admin=True)
    with pytest.raises(WriteError, match="wrong password"):
        writes.apply(c, "s1")
    assert f.read_text() == "a"
    assert audit(tmp_path)[-1]["failed"]


def test_too_big_system_deletes_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(writes, "_needs_elevation", lambda change: True)
    monkeypatch.setattr(writes, "MAX_DELETE_BACKUP_BYTES", 10)
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)
    with pytest.raises(WriteError, match="too big"):
        writes.propose("delete_path", {"path": str(f)}, tmp_path, [], admin=True)


def test_normal_changes_are_logged_too(tmp_path, trash):
    root = (tmp_path / "proj")
    root.mkdir()
    c = writes.propose("write_file", {"path": "n.txt", "content": "x"}, root, [root.resolve()])
    writes.apply(c, "s1")
    assert audit(tmp_path)[0] == {**audit(tmp_path)[0], "action": "apply", "kind": "create", "mode": "edit"}


# ------------------------------------------------------------ /mode


@pytest.fixture
def app(tmp_path):
    from padou import app as app_mod, config, theme
    from padou.client import Client

    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = config.Config()
    return app_mod.App(cfg, theme.load(theme_dir=None), Client(cfg.server), proj.resolve())


def test_mode_edit_read_and_shift_tab(app):
    commands.cmd_mode(app, "edit")
    assert app.mode == "edit" and app.write_roots == [app.cwd]
    assert "EDIT MODE" in app.session.messages[0]["content"]
    commands.cycle_mode(app)
    assert app.mode == "read" and app.write_roots == []
    commands.cycle_mode(app)
    assert app.mode == "edit"
    commands.cmd_mode(app, "read")
    assert app.mode == "read" and "read-only mode" in app.session.messages[0]["content"]


def test_admin_asks_once_ever_then_just_switches(app, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    commands.cmd_mode(app, "admin")
    assert app.mode == "read"
    before = app.folders
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    commands.cmd_mode(app, "admin")
    assert app.mode == "admin" and app.folders is None and "ADMIN MODE" in app.session.messages[0]["content"]
    commands.cycle_mode(app)  # Shift+Tab leaves admin mode
    assert app.mode == "read" and app.folders == before
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: pytest.fail("asked again"))
    commands.cmd_mode(app, "admin")
    assert app.mode == "admin"


def test_shift_tab_is_quiet_and_explains_in_the_status_line(app, monkeypatch, tmp_path):
    with ui.console.capture() as cap:
        commands.cycle_mode(app)
    assert app.mode == "edit" and cap.get() == ""
    commands.cycle_mode(app)
    app.cwd = Path.home()  # edit mode isn't allowed in the home folder
    commands.cycle_mode(app)
    assert app.mode == "read" and "/mode admin" in app._flash[0]
    assert "/mode admin" in "".join(t for _, t in app._toolbar())


def test_old_write_command_still_works(app):
    commands.cmd_write(app, ".")
    assert app.mode == "edit"
    commands.cmd_write(app, "off")
    assert app.mode == "read"


def test_edit_refuses_the_home_folder_and_points_to_admin(app, capsys):
    with ui.console.capture() as cap:
        commands.cmd_mode(app, f"edit {Path.home()}")
    assert app.mode == "read" and "/mode admin" in cap.get()
