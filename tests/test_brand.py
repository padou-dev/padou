from padou import brand

COLORS = brand.Colors("#ff5fa2", "#7f849c", "#16161e")


def text(frame):
    return ["".join(span[0] for span in row) for row in frame]


def make():
    return brand.Banner(COLORS, "0.1.0", "Qwen3.5 4B", "~/Projects/padou", seed=1)


def test_still_has_full_star_and_text():
    rows = text(make().still())
    assert len(rows) == brand.ROWS
    assert "◆" in rows[brand.CR]
    assert "padou  v0.1.0 · παντού" in rows[2]
    assert "Qwen3.5 4B · local" in rows[3]
    assert "read-only" not in "".join(rows)  # the mode can change, so it lives in the status line
    assert rows[0].strip() == "│" and rows[6].strip() == "│"


def test_launch_ends_on_the_still_banner():
    b = make()
    assert text(b.launch(brand.LAUNCH_SECONDS)) == text(b.still())


def test_launch_starts_scattered():
    rows = text(make().launch(0.0))
    assert "◆" not in "".join(rows)  # nothing has arrived yet
    assert "padou" not in "".join(rows)


def test_pulse_grows_outward():
    b = make()
    lit = lambda phase: sum(ch not in " " for row in text(b.pulse(phase)) for ch in row[: brand.TEXT_COL])
    assert lit(0.0) == 1  # just the centre
    assert lit(0.3) < lit(0.6)  # rays reach further as the shot travels out
    assert lit(0.6) > 1


def test_all_rows_same_width():
    b = make()
    for frame in (b.still(), b.launch(0.4), b.pulse(0.5)):
        assert len({len(r) for r in text(frame)}) == 1


def test_mini_star_cycles():
    assert [brand.mini(i * 0.25) for i in range(5)] == ["·", "✦", "✶", "✸", "·"]


def test_long_paths_are_cut_not_crash():
    b = brand.Banner(COLORS, "0.1.0", "Qwen3.5 4B", "~/" + "x" * 300)
    assert len({len(r) for r in text(b.still())}) == 1
