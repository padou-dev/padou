import json
import shutil
import subprocess

import httpx
import pytest

from padou import paths, updates


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "UPDATE_CACHE", tmp_path / "update-check.json")


def test_versions():
    assert updates.parse_version("v0.2.0") == (0, 2, 0)
    assert updates.parse_version("1.4") == (1, 4, 0)
    assert updates.parse_version("nightly") is None
    assert updates.is_newer("0.10.0", "0.9.9")  # numbers, not text
    assert not updates.is_newer("v0.1.0", "0.1.0")
    assert not updates.is_newer("garbage", "0.1.0")


def github(routes: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, (status, body) in routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


def test_latest_release():
    t = github({"/releases/latest": (200, {"tag_name": "v0.3.1", "body": "- new things", "html_url": "https://x"})})
    r = updates.fetch_latest(transport=t)
    assert (r.version, r.tag, r.notes) == ("0.3.1", "v0.3.1", "- new things")


def test_falls_back_to_tags_and_picks_the_highest():
    t = github({"/releases/latest": (404, {}), "/tags": (200, [{"name": "v0.9.0"}, {"name": "v0.10.0"}, {"name": "test"}])})
    assert updates.fetch_latest(transport=t).tag == "v0.10.0"


def test_offline_or_private_repo_is_quietly_none():
    assert updates.fetch_latest(transport=github({})) is None

    def broken(request):
        raise httpx.ConnectError("offline")
    assert updates.fetch_latest(transport=httpx.MockTransport(broken)) is None


def test_once_a_day_cache():
    assert updates.check_due()
    updates._save_cache(updates.Release("99.0.0", "v99.0.0"))
    assert not updates.check_due()
    assert updates.cached_update().version == "99.0.0"
    updates._save_cache(None)  # offline: what we knew is kept
    assert updates.cached_update().version == "99.0.0"
    updates._save_cache(updates.Release("0.0.1", "v0.0.1"))
    assert updates.cached_update() is None  # older than what's installed
    paths.UPDATE_CACHE.write_text("not json")
    assert updates.check_due() and updates.cached_update() is None


def test_tag_is_validated_before_building_a_command():
    assert updates.git_tool_spec(updates.Release("1.2.3", "v1.2.3")).endswith("@v1.2.3")
    with pytest.raises(ValueError):
        updates.git_tool_spec(updates.Release("1.2.3", "v1.2.3; rm -rf ~"))


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_update_git_clone(tmp_path):
    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", cwd=origin)
    git("config", "user.email", "t@example.com", cwd=origin)
    git("config", "user.name", "t", cwd=origin)
    (origin / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    git("add", ".", cwd=origin)
    git("commit", "-qm", "one", cwd=origin)
    clone = tmp_path / "clone"
    git("clone", "-q", str(origin), str(clone), cwd=tmp_path)

    (origin / "README.md").write_text("new\n")
    git("add", ".", cwd=origin)
    git("commit", "-qm", "two", cwd=origin)
    out = updates.update_git_clone(clone)
    assert out.ok and out.restart and (clone / "README.md").exists()

    (clone / "pyproject.toml").write_text("[project]\nname = 'changed locally'\n")
    out = updates.update_git_clone(clone)
    assert not out.ok and "uncommitted" in out.message


class FakeApp:
    def __init__(self):
        from padou import theme
        self.theme = theme.load(theme_dir=None)
        self.update_available = None
        self.after_exit = None


def test_update_command_installs_and_offers_to_quit(monkeypatch):
    from padou import commands, ui
    monkeypatch.setattr(updates, "detect_install", lambda: updates.Install("uv-tool"))
    monkeypatch.setattr(updates, "check_now", lambda: updates.Release("9.9.9", "v9.9.9", "- shiny"))
    calls = []
    monkeypatch.setattr(updates, "update_uv_tool", lambda r: calls.append(r.tag) or updates.Outcome(True, "Installed.", restart=True))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    app = FakeApp()
    assert commands.cmd_update(app, "") == commands.EXIT
    assert calls == ["v9.9.9"] and app.update_available is None


def test_update_command_on_windows_finishes_after_exit(monkeypatch):
    from padou import commands, ui
    monkeypatch.setattr(updates, "detect_install", lambda: updates.Install("uv-tool"))
    monkeypatch.setattr(updates, "check_now", lambda: updates.Release("9.9.9", "v9.9.9"))
    later = ["uv", "tool", "install", "--force", "git+https://github.com/padou-dev/padou@v9.9.9"]
    monkeypatch.setattr(updates, "update_uv_tool", lambda r: updates.Outcome(True, "after you quit", restart=True, after_exit=later))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    app = FakeApp()
    assert commands.cmd_update(app, "") is None
    assert app.after_exit == later


def test_update_command_when_up_to_date(monkeypatch):
    from padou import __version__, commands, ui
    monkeypatch.setattr(updates, "detect_install", lambda: updates.Install("uv-tool"))
    monkeypatch.setattr(updates, "check_now", lambda: updates.Release(__version__, "v" + __version__))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: pytest.fail("shouldn't ask"))
    assert commands.cmd_update(FakeApp(), "") is None
