from pathlib import Path

import pytest

from padou import compat, tools

linux_only = pytest.mark.skipif(not compat.LINUX, reason="Linux command list")


def symlink(link: Path, target: Path) -> None:
    """Make a symlink, or skip the test where that isn't allowed (Windows without developer mode)."""
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("this system doesn't allow creating symlinks")


def test_blocks_ssh_and_secrets(tmp_path):
    home = Path.home()
    assert tools.is_blocked(home / ".ssh" / "id_ed25519")
    assert tools.is_blocked(home / ".gnupg")
    if compat.LINUX:
        assert tools.is_blocked(Path("/etc/shadow"))
    assert tools.is_blocked(tmp_path / ".env")
    assert tools.is_blocked(tmp_path / ".env.local")
    assert tools.is_blocked(tmp_path / "server.pem")
    assert tools.is_blocked(tmp_path / "id_rsa")
    assert not tools.is_blocked(tmp_path / "id_rsa.pub")
    assert not tools.is_blocked(tmp_path / "main.py")
    assert not tools.is_blocked(tmp_path / "environment.md")


def test_symlink_to_secret_is_blocked(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=abc")
    link = tmp_path / "harmless.txt"
    symlink(link, secret)
    result = tools.execute("read_file", {"path": str(link)}, tmp_path, 1000, 5)
    assert not result.ok
    assert "blocked" in result.output


def test_read_file_numbers_and_caps(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 501)))
    out = tools.read_file("a.txt", tmp_path, limit=10_000, max_lines=5)
    assert "500 lines total" in out
    assert "    1  line 1" in out and "line 6" not in out
    capped = tools.read_file("a.txt", tmp_path, limit=50)
    assert "output cut" in capped


def test_read_file_rejects_binary(tmp_path):
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00\x01\x02")
    with pytest.raises(tools.ToolError):
        tools.read_file(str(f), tmp_path, 1000)


def test_search_skips_venv(tmp_path):
    (tmp_path / "main.py").write_text("print('hello padou')\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.py").write_text("hello padou\n")
    out = tools.search("padou", tmp_path, 10_000)
    assert "main.py:1" in out
    assert ".venv" not in out


@linux_only
@pytest.mark.parametrize(
    "name,arg,expected",
    [
        ("disk_usage", None, ["df", "-h"]),
        ("service_status", "NetworkManager", ["systemctl", "status", "--no-pager", "NetworkManager"]),
        ("service_logs", "sshd.service", ["journalctl", "--no-pager", "-n", "100", "-u", "sshd.service"]),
    ],
)
def test_build_argv_ok(name, arg, expected):
    assert tools.build_argv(name, arg) == expected


def test_every_system_has_the_basics():
    for name in ("disk_usage", "memory", "uptime", "processes", "ip_addresses", "service_status", "which", "git_status"):
        assert name in tools.APPROVED_COMMANDS, name
    # the argument always goes at the very end, as one separate item
    cmd = tools.APPROVED_COMMANDS["which"]
    assert tools.build_argv("which", "python3") == [*cmd.argv, "python3"]


def test_command_lists_of_other_systems_are_well_formed():
    for builder in (tools._linux_commands, tools._macos_commands, tools._windows_commands):
        cmds = builder()
        names = [c.name for c in cmds]
        assert len(names) == len(set(names))
        assert all(c.argv and all(isinstance(a, str) for a in c.argv) for c in cmds)


@pytest.mark.parametrize(
    "name,arg",
    [
        ("rm", None),  # not approved
        ("service_status", None),  # missing argument
        ("disk_usage", "/"),  # takes no argument
        ("service_status", "foo; rm -rf ~"),  # shell tricks
        ("service_status", "$(reboot)"),
        ("service_status", "--force"),  # option injection
        ("which", "a b"),
        ("which", "a|b"),
    ],
)
def test_build_argv_refused(name, arg):
    with pytest.raises(tools.ToolError):
        tools.build_argv(name, arg)


def test_execute_bad_json_does_not_crash(tmp_path):
    result = tools.execute("read_file", "{not json", tmp_path, 1000, 5)
    assert not result.ok


def test_definitions_shape():
    defs = tools.definitions(True)
    names = [d["function"]["name"] for d in defs]
    assert names == ["read_file", "list_dir", "search", "run_command"]
    assert "run_command" not in [d["function"]["name"] for d in tools.definitions(False)]


def test_folder_scope(tmp_path):
    inside_dir = tmp_path / "project"
    outside_dir = tmp_path / "elsewhere"
    inside_dir.mkdir(); outside_dir.mkdir()
    (inside_dir / "a.txt").write_text("hello padou\n")
    (outside_dir / "b.txt").write_text("secret-ish padou\n")
    roots = [inside_dir.resolve()]
    assert tools.execute("read_file", {"path": "a.txt"}, inside_dir, 1000, 5, roots=roots).ok
    refused = tools.execute("read_file", {"path": str(outside_dir / "b.txt")}, inside_dir, 1000, 5, roots=roots)
    assert not refused.ok and "/folder add" in refused.output
    assert not tools.execute("list_dir", {"path": ".."}, inside_dir, 1000, 5, roots=roots).ok
    # anywhere mode
    assert tools.execute("read_file", {"path": str(outside_dir / "b.txt")}, inside_dir, 1000, 5, roots=None).ok


def test_symlink_cannot_escape_folder(tmp_path):
    inside_dir = tmp_path / "project"
    outside_dir = tmp_path / "elsewhere"
    inside_dir.mkdir(); outside_dir.mkdir()
    (outside_dir / "b.txt").write_text("padou outside\n")
    symlink(inside_dir / "link.txt", outside_dir / "b.txt")
    roots = [inside_dir.resolve()]
    assert not tools.execute("read_file", {"path": "link.txt"}, inside_dir, 1000, 5, roots=roots).ok
    out = tools.search("padou", inside_dir, 10_000, roots=roots)
    assert "outside" not in out
