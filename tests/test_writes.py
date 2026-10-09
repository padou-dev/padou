import os
from pathlib import Path

import pytest

from padou import compat, paths, writes
from padou.writes import WriteError

posix_only = pytest.mark.skipif(compat.WINDOWS, reason="POSIX file permissions")


def put(path: Path, text: str) -> Path:
    """Write text exactly as given (no \\n -> \\r\\n translation on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def get(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")


@pytest.fixture
def trash(tmp_path, monkeypatch):
    """A pretend Trash, so tests never touch the real one."""
    bin_dir = tmp_path / "trash-bin"
    bin_dir.mkdir()

    def fake(path: Path) -> None:
        os.replace(path, bin_dir / path.name)

    monkeypatch.setattr(writes, "move_to_trash", fake)
    return bin_dir


@pytest.fixture(autouse=True)
def temp_folders_are_fine(monkeypatch):
    """On Windows the test folders live in AppData\\Local\\Temp, which write mode normally refuses."""
    monkeypatch.setattr(writes, "_HOME_APP_FOLDERS", set())


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "p"
    r.mkdir()
    return r.resolve()


# ----------------------------------------------------------- write roots


def test_refuses_drive_root_home_and_system_folders():
    candidates = [Path(Path.home().anchor), Path.home(), *writes.SYSTEM_DIRS]
    for bad in candidates:
        if bad.is_dir():
            with pytest.raises(WriteError):
                writes.check_write_root(bad, None)


def test_refuses_hidden_folders(tmp_path):
    hidden = tmp_path / ".config" / "app"
    hidden.mkdir(parents=True)
    with pytest.raises(WriteError):
        writes.check_write_root(hidden, None)


def test_write_root_must_be_readable(tmp_path, root):
    other = tmp_path / "other"
    other.mkdir()
    assert writes.check_write_root(root, [root]) == root
    with pytest.raises(WriteError, match="/folder add"):
        writes.check_write_root(other, [root])


# --------------------------------------------------------------- edits


def test_edit_needs_one_exact_match(root):
    f = put(root / "a.txt", "alpha\nbeta\nbeta\n")
    ch = writes.propose("edit_file", {"path": "a.txt", "old_text": "alpha", "new_text": "ALPHA"}, root, [root])
    assert ch.new == "ALPHA\nbeta\nbeta\n" and ch.stats == (1, 1)
    with pytest.raises(WriteError, match="2 times"):
        writes.propose("edit_file", {"path": "a.txt", "old_text": "beta", "new_text": "x"}, root, [root])
    with pytest.raises(WriteError, match="wasn't found"):
        writes.propose("edit_file", {"path": "a.txt", "old_text": "gamma", "new_text": "x"}, root, [root])
    assert get(f) == "alpha\nbeta\nbeta\n"  # proposing never writes


def test_edit_keeps_windows_line_endings(root, state):
    f = put(root / "win.txt", "one\r\ntwo\r\nthree\r\n")
    ch = writes.propose("edit_file", {"path": "win.txt", "old_text": "one\ntwo", "new_text": "one\n1.5\ntwo"}, root, [root])
    writes.apply(ch, "s0")
    assert get(f) == "one\r\n1.5\r\ntwo\r\nthree\r\n"


def test_rewrite_replaces_the_whole_file(root, state):
    f = put(root / "notes.md", "old\n")
    ch = writes.propose("rewrite_file", {"path": "notes.md", "content": "new\ncontent\n"}, root, [root])
    assert ch.kind == "rewrite" and ch.stats == (2, 1)
    applied = writes.apply(ch, "s1")
    assert get(f) == "new\ncontent\n"
    writes.undo(applied, "s1")
    assert get(f) == "old\n"
    with pytest.raises(WriteError, match="doesn't exist"):
        writes.propose("rewrite_file", {"path": "missing.md", "content": "x"}, root, [root])


def test_write_file_only_creates_and_makes_folders(root, state):
    put(root / "exists.txt", "hi")
    with pytest.raises(WriteError, match="already exists"):
        writes.propose("write_file", {"path": "exists.txt", "content": "x"}, root, [root])
    ch = writes.propose("write_file", {"path": "docs/guide/new.md", "content": "hello\n"}, root, [root])
    assert ch.kind == "create" and not (root / "docs").exists()
    assert ch.new_folders == [root / "docs", root / "docs" / "guide"]
    applied = writes.apply(ch, "s2")
    assert get(root / "docs/guide/new.md") == "hello\n"
    writes.undo(applied, "s2")
    assert not (root / "docs").exists()  # the empty folders padou made are gone again


def test_targets_outside_or_dangerous_are_refused(tmp_path, root):
    outside = put(tmp_path / "outside.txt", "x")
    put(root / ".git" / "config", "x")
    put(root / ".env", "TOKEN=1")
    cases = [
        {"path": str(outside), "old_text": "x", "new_text": "y"},
        {"path": "../outside.txt", "old_text": "x", "new_text": "y"},
        {"path": ".git/config", "old_text": "x", "new_text": "y"},
        {"path": ".env", "old_text": "TOKEN=1", "new_text": "TOKEN=2"},
    ]
    for args in cases:
        with pytest.raises(WriteError):
            writes.propose("edit_file", args, root, [root])
    with pytest.raises(WriteError, match="write mode is off"):
        writes.propose("edit_file", cases[0], root, [])


def test_symlinks_are_refused(tmp_path, root):
    outside = put(tmp_path / "outside.txt", "x")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("this system doesn't allow creating symlinks")
    for name, args in [("edit_file", {"path": "link.txt", "old_text": "x", "new_text": "y"}),
                       ("delete_path", {"path": "link.txt"})]:
        with pytest.raises(WriteError, match="symlink"):
            writes.propose(name, args, root, [root])


def test_binary_and_huge_files_are_refused(root):
    (root / "b.bin").write_bytes(b"\x00\x01abc")
    put(root / "big.txt", "a" * (writes.MAX_FILE_BYTES + 1))
    with pytest.raises(WriteError, match="binary"):
        writes.propose("edit_file", {"path": "b.bin", "old_text": "abc", "new_text": "x"}, root, [root])
    with pytest.raises(WriteError, match="too big"):
        writes.propose("edit_file", {"path": "big.txt", "old_text": "a", "new_text": "b"}, root, [root])


# ------------------------------------------------------ apply and undo


def test_apply_backs_up_and_undo_restores(root, state):
    f = put(root / "conf.txt", "port = 80\n")
    ch = writes.propose("edit_file", {"path": "conf.txt", "old_text": "80", "new_text": "8080"}, root, [root])
    applied = writes.apply(ch, "s1")
    assert get(f) == "port = 8080\n"
    assert get(applied.backup) == "port = 80\n"
    writes.undo(applied, "s1")
    assert get(f) == "port = 80\n"
    with pytest.raises(WriteError):
        writes.undo(applied, "s1")


@posix_only
def test_permissions_are_kept(root, state):
    f = put(root / "run.sh", "echo hi\n")
    os.chmod(f, 0o750)
    ch = writes.propose("edit_file", {"path": "run.sh", "old_text": "hi", "new_text": "hello"}, root, [root])
    writes.apply(ch, "s1")
    assert oct(f.stat().st_mode & 0o777) == "0o750"
    new = writes.propose("write_file", {"path": "new.txt", "content": "x"}, root, [root])
    writes.apply(new, "s1")
    assert oct((root / "new.txt").stat().st_mode & 0o777) == "0o644"


def test_undo_of_created_file_moves_not_deletes(root, state):
    ch = writes.propose("write_file", {"path": "notes.md", "content": "# hi\n"}, root, [root])
    applied = writes.apply(ch, "s2")
    assert get(root / "notes.md") == "# hi\n"
    writes.undo(applied, "s2")
    assert not (root / "notes.md").exists()
    assert any(p.name.endswith("notes.md") for p in writes.backup_dir("s2").iterdir())


def test_apply_refuses_if_file_changed_meanwhile(root, state):
    f = put(root / "a.txt", "one\n")
    ch = writes.propose("edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}, root, [root])
    put(f, "someone else edited this\n")
    with pytest.raises(WriteError, match="changed while"):
        writes.apply(ch, "s3")
    assert get(f) == "someone else edited this\n"


# ------------------------------------------------------ move


def test_move_and_rename(root, state):
    put(root / "a.txt", "A")
    (root / "docs").mkdir()
    rename = writes.propose("move_path", {"source": "a.txt", "destination": "b.txt"}, root, [root])
    assert rename.dest == root / "b.txt"
    into = writes.propose("move_path", {"source": "a.txt", "destination": "docs"}, root, [root])
    assert into.dest == root / "docs" / "a.txt"  # an existing folder means "into it"
    applied = writes.apply(into, "s4")
    assert get(root / "docs" / "a.txt") == "A" and not (root / "a.txt").exists()
    writes.undo(applied, "s4")
    assert get(root / "a.txt") == "A"


def test_move_never_overwrites_or_escapes(tmp_path, root):
    put(root / "a.txt", "A")
    put(root / "b.txt", "B")
    (root / "folder").mkdir()
    with pytest.raises(WriteError, match="already exists"):
        writes.propose("move_path", {"source": "a.txt", "destination": "b.txt"}, root, [root])
    with pytest.raises(WriteError, match="outside"):
        writes.propose("move_path", {"source": "a.txt", "destination": str(tmp_path / "x.txt")}, root, [root])
    with pytest.raises(WriteError, match="inside itself"):
        writes.propose("move_path", {"source": "folder", "destination": "folder/sub/folder"}, root, [root])
    with pytest.raises(WriteError, match="write folder itself"):
        writes.propose("move_path", {"source": ".", "destination": "elsewhere"}, root, [root])


# ------------------------------------------------------ delete


def test_delete_file_goes_to_trash_and_undo_restores(root, state, trash):
    put(root / "old.log", "bye")
    ch = writes.propose("delete_path", {"path": "old.log"}, root, [root])
    assert ch.kind == "delete" and not ch.is_dir and not ch.needs_typed_confirmation
    applied = writes.apply(ch, "s5")
    assert not (root / "old.log").exists() and (trash / "old.log").exists()
    writes.undo(applied, "s5")
    assert get(root / "old.log") == "bye"


def test_delete_folder_needs_typed_confirmation_and_reports_contents(root, state, trash):
    put(root / "build" / "a.o", "x" * 10)
    put(root / "build" / "sub" / "b.o", "y" * 5)
    put(root / "build" / ".env", "SECRET=1")
    ch = writes.propose("delete_path", {"path": "build"}, root, [root])
    assert ch.is_dir and ch.needs_typed_confirmation
    assert (ch.files, ch.folders, ch.size) == (3, 1, 23)
    assert any("secrets" in w for w in ch.warnings)
    applied = writes.apply(ch, "s6")
    assert not (root / "build").exists()
    writes.undo(applied, "s6")
    assert get(root / "build" / "sub" / "b.o") == "y" * 5


def test_write_folder_itself_cant_be_deleted(root):
    for path in (".", str(root)):
        with pytest.raises(WriteError, match="write folder itself"):
            writes.propose("delete_path", {"path": path}, root, [root])


def test_big_deletions_are_not_copied(root, state, trash, monkeypatch):
    monkeypatch.setattr(writes, "MAX_DELETE_BACKUP_BYTES", 3)
    put(root / "big.bin", "123456")
    applied = writes.apply(writes.propose("delete_path", {"path": "big.bin"}, root, [root]), "s7")
    assert applied.backup is None
    with pytest.raises(WriteError, match="Trash"):
        writes.undo(applied, "s7")


def test_a_failing_trash_deletes_nothing(root, state, monkeypatch):
    def broken(path):
        raise WriteError("no Trash here; nothing was deleted")

    monkeypatch.setattr(writes, "move_to_trash", broken)
    f = put(root / "keep.txt", "safe")
    with pytest.raises(WriteError, match="nothing was deleted"):
        writes.apply(writes.propose("delete_path", {"path": "keep.txt"}, root, [root]), "s8")
    assert get(f) == "safe"


@pytest.mark.skipif(not os.environ.get("CI"), reason="uses the real Trash: runs on GitHub Actions only")
def test_real_system_trash(tmp_path, state):
    """The real send2trash call, on this system's real Trash/Recycle Bin."""
    f = put(tmp_path / f"padou-trash-test-{os.getpid()}.txt", "bye")
    try:
        writes.move_to_trash(f)
    except WriteError as exc:
        pytest.skip(f"no usable Trash here: {exc}")
    assert not f.exists()


def test_every_tool_has_a_definition():
    names = {d["function"]["name"] for d in writes.definitions()}
    assert names == writes.TOOL_NAMES
