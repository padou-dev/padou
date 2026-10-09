import io

from padou import compat


def test_split_and_join_round_trip():
    argv = ["llama-server", "-hf", "a/b:Q4_K_M", "--alias", "two words"]
    assert compat.split_command(compat.join_command(argv)) == argv


def test_program_name():
    assert compat.program_name("/usr/bin/llama-server") == "llama-server"
    assert compat.program_name("C:\\llama\\llama-server.exe") == "llama-server"


def test_utf8_output_never_crashes_on_greek(monkeypatch):
    """Like Windows output going to a pipe: a cp1252 stream can't hold παντού."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
    monkeypatch.setattr(compat, "WINDOWS", True)
    compat.utf8_output(stream)
    stream.write("padou 0.1.0 · παντού\n")
    stream.flush()
    assert raw.getvalue().decode("utf-8") == "padou 0.1.0 · παντού\n"


def test_every_system_list_has_service_status():
    from padou import tools
    for builder in (tools._linux_commands, tools._macos_commands, tools._windows_commands):
        assert "service_status" in {c.name for c in builder()}
