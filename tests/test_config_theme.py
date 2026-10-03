from padou import config, theme


def test_defaults_without_file(tmp_path):
    cfg = config.load(tmp_path / "missing.toml")
    assert cfg.source is None
    assert cfg.server.url.startswith("http")


def test_example_config_round_trips(tmp_path):
    path = config.write_example(tmp_path / "config.toml")
    cfg = config.load(path)
    assert cfg.warnings == []
    assert cfg.server.timeout == 180.0
    assert cfg.ui.banner == "full"


def test_bad_values_warn(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[server]\ntimeout = "slow"\nnope = 1\n[ui]\nbanner = "huge"\n[extra]\n')
    cfg = config.load(p)
    assert len(cfg.warnings) == 4
    assert cfg.server.timeout == 180.0
    assert cfg.ui.banner == "full"


def test_theme_fallback(tmp_path):
    th = theme.load(theme_dir=tmp_path / "nothing")
    assert th.source == "built-in"


def test_theme_from_colors_toml(tmp_path):
    (tmp_path / "colors.toml").write_text('accent = "#ff5fa2"\ncolor1 = "#ff0000"\n')
    th = theme.load(theme_dir=tmp_path)
    assert th.accent == "#ff5fa2"
    assert th.error == "#ff0000"


def test_theme_from_alacritty(tmp_path):
    (tmp_path / "alacritty.toml").write_text('[colors.normal]\nblue = "0x7aa2f7"\nred = "#f7768e"\n')
    th = theme.load(theme_dir=tmp_path)
    assert th.accent == "#7aa2f7"


def test_accent_override(tmp_path):
    th = theme.load("#123456", theme_dir=tmp_path)
    assert th.accent == "#123456"


def test_add_folder_keeps_comments(tmp_path):
    import tomllib
    p = config.write_example(tmp_path / "config.toml")
    config.add_folder_to_file("~/Projects", p)
    config.add_folder_to_file("/etc", p)
    config.add_folder_to_file("/etc", p)  # no duplicates
    text = p.read_text()
    assert tomllib.loads(text)["tools"]["folders"] == ["~/Projects", "/etc"]
    assert "# padou configuration" in text and "[ui]" in text
    cfg = config.load(p)
    assert cfg.warnings == [] and cfg.tools.folders == ["~/Projects", "/etc"]


def test_add_folder_to_old_config_without_line(tmp_path):
    import tomllib
    p = tmp_path / "c.toml"
    p.write_text("[tools]\nenabled = true\n")
    config.add_folder_to_file("/srv", p)
    assert tomllib.loads(p.read_text())["tools"]["folders"] == ["/srv"]
