import os
from pathlib import Path

import pytest

from padou import paths, writes
from padou.writes import WriteError


def setup_state(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")


# ----------------------------------------------------------- write roots


def test_refuses_root_home_and_system_folders(tmp_path):
    for bad in (Path("/"), Path.home(), Path("/etc"), Path("/usr/local"), Path("/boot")):
        if bad.is_dir():
            with pytest.raises(WriteError):
                writes.check_write_root(bad, None)


def test_refuses_hidden_folders(tmp_path):
    hidden = tmp_path / ".config" / "app"
    hidden.mkdir(parents=True)
    with pytest.raises(WriteError):
        writes.check_write_root(hidden, None)


def test_write_root_must_be_readable(tmp_path):
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir(); other.mkdir()
    assert writes.check_write_root(project, [project.resolve()]) == project.resolve()
    with pytest.raises(WriteError, match="/folder add"):
        writes.check_write_root(other, [project.resolve()])


# --------------------------------------------------------------- edits


def test_edit_needs_one_exact_match(tmp_path):
    root = tmp_path / "p"; root.mkdir()
    f = root / "a.txt"; f.write_text("alpha\nbeta\nbeta\n")
    roots = [root.resolve()]
    ch = writes.propose("edit_file", {"path": "a.txt", "old_text": "alpha", "new_text": "ALPHA"}, root, roots)
    assert ch.new == "ALPHA\nbeta\nbeta\n" and ch.stats == (1, 1)
    with pytest.raises(WriteError, match="2 times"):
        writes.propose("edit_file", {"path": "a.txt", "old_text": "beta", "new_text": "x"}, root, roots)
    with pytest.raises(WriteError, match="wasn't found"):
        writes.propose("edit_file", {"path": "a.txt", "old_text": "gamma", "new_text": "x"}, root, roots)
    assert f.read_text() == "alpha\nbeta\nbeta\n"  # proposing never writes


def test_write_file_only_creates(tmp_path):
    root = tmp_path / "p"; root.mkdir()
    (root / "exists.txt").write_text("hi")
    roots = [root.resolve()]
    with pytest.raises(WriteError, match="already exists"):
        writes.propose("write_file", {"path": "exists.txt", "content": "x"}, root, roots)
    ch = writes.propose("write_file", {"path": "new.txt", "content": "hello\n"}, root, roots)
    assert ch.kind == "create" and not (root / "new.txt").exists()


def test_targets_outside_or_dangerous_are_refused(tmp_path):
    root = tmp_path / "p"; root.mkdir()
    outside = tmp_path / "outside.txt"; outside.write_text("x")
    (root / ".git").mkdir(); (root / ".git" / "config").write_text("x")
    (root / "link.txt").symlink_to(outside)
    (root / ".env").write_text("TOKEN=1")
    roots = [root.resolve()]
    cases = [
        {"path": str(outside), "old_text": "x", "new_text": "y"},
        {"path": "../outside.txt", "old_text": "x", "new_text": "y"},
        {"path": ".git/config", "old_text": "x", "new_text": "y"},
        {"path": "link.txt", "old_text": "x", "new_text": "y"},
        {"path": ".env", "old_text": "TOKEN=1", "new_text": "TOKEN=2"},
    ]
    for args in cases:
        with pytest.raises(WriteError):
            writes.propose("edit_file", args, root, roots)
    with pytest.raises(WriteError, match="write mode is off"):
        writes.propose("edit_file", cases[0], root, [])


def test_binary_and_huge_files_are_refused(tmp_path):
    root = tmp_path / "p"; root.mkdir()
    (root / "b.bin").write_bytes(b"\x00\x01abc")
    (root / "big.txt").write_text("a" * (writes.MAX_FILE_BYTES + 1))
    roots = [root.resolve()]
    with pytest.raises(WriteError, match="binary"):
        writes.propose("edit_file", {"path": "b.bin", "old_text": "abc", "new_text": "x"}, root, roots)
    with pytest.raises(WriteError, match="too big"):
        writes.propose("edit_file", {"path": "big.txt", "old_text": "a", "new_text": "b"}, root, roots)


# ------------------------------------------------------ apply and undo


def test_apply_backs_up_and_undo_restores(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch)
    root = tmp_path / "p"; root.mkdir()
    f = root / "conf.txt"; f.write_text("port = 80\n")
    os.chmod(f, 0o640)
    ch = writes.propose("edit_file", {"path": "conf.txt", "old_text": "80", "new_text": "8080"}, root, [root.resolve()])
    applied = writes.apply(ch, "s1")
    assert f.read_text() == "port = 8080\n"
    assert applied.backup.read_text() == "port = 80\n"
    assert oct(f.stat().st_mode & 0o777) == "0o640"  # permissions kept
    writes.undo(applied, "s1")
    assert f.read_text() == "port = 80\n"
    with pytest.raises(WriteError):
        writes.undo(applied, "s1")


def test_undo_of_created_file_moves_not_deletes(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch)
    root = tmp_path / "p"; root.mkdir()
    ch = writes.propose("write_file", {"path": "notes.md", "content": "# hi\n"}, root, [root.resolve()])
    applied = writes.apply(ch, "s2")
    assert (root / "notes.md").read_text() == "# hi\n"
    writes.undo(applied, "s2")
    assert not (root / "notes.md").exists()
    assert any(p.name.endswith("notes.md") for p in writes.backup_dir("s2").iterdir())


def test_apply_refuses_if_file_changed_meanwhile(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch)
    root = tmp_path / "p"; root.mkdir()
    f = root / "a.txt"; f.write_text("one\n")
    ch = writes.propose("edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}, root, [root.resolve()])
    f.write_text("someone else edited this\n")
    with pytest.raises(WriteError, match="changed while"):
        writes.apply(ch, "s3")
    assert f.read_text() == "someone else edited this\n"
