"""padou's brand mark: a compass star.

παντού means "everywhere", in every direction. On launch, dots gather from
everywhere into the star while παντού decodes into padou; afterwards the star's
rays keep shooting outward (small > medium > large).

Everything here is pure data (no terminal code): a frame is a list of rows and
a row is a list of (text, colour or None, bold) spans. ui.py draws them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

GREEK = "παντού"
LATIN = "padou"
GLYPHS = "αβγδεζηθλμξπρστφχψω"
ROWS = 7
CR, CC = 3, 7  # the star's centre
TEXT_COL = 19  # where the name and info lines start

# rays: direction, glyph, cells from the centre outward (step 1, 2, 3)
RAYS = [
    ("N", "│", [(2, 7), (1, 7), (0, 7)]),
    ("S", "│", [(4, 7), (5, 7), (6, 7)]),
    ("E", "─", [(3, 9), (3, 11), (3, 13)]),
    ("W", "─", [(3, 5), (3, 3), (3, 1)]),
    ("NE", "╱", [(2, 9), (1, 11)]),
    ("NW", "╲", [(2, 5), (1, 3)]),
    ("SE", "╲", [(4, 9), (5, 11)]),
    ("SW", "╱", [(4, 5), (5, 3)]),
]
WIDE = {"E": 1, "W": -1}  # horizontal rays are two characters per step

MINI = ["·", "✦", "✶", "✸"]  # the status-line star: small > large
PULSE_PERIOD = 1.2  # seconds per "shot" of the rays
LAUNCH_SECONDS = 1.3

Span = tuple[str, "str | None", bool]


@dataclass
class Colors:
    accent: str
    dim: str
    bg: str


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1: str, c2: str, t: float) -> str:
    try:
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return c2
    t = max(0.0, min(1.0, t))
    return "#" + "".join(f"{round(lerp(x, y, t)):02x}" for x, y in zip(a, b))


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def star_cells() -> list[tuple[int, int, str, int]]:
    """Every cell of the full star: (row, col, glyph, step); step 0 is the centre."""
    cells = [(CR, CC, "◆", 0)]
    for name, glyph, steps in RAYS:
        for s, (r, c) in enumerate(steps, 1):
            cells.append((r, c, glyph, s))
            if name in WIDE:
                cells.append((r, c + WIDE[name], glyph, s))
    return cells


class Banner:
    def __init__(self, colors: Colors, version: str, model_label: str, path_text: str, seed: int | None = None):
        self.c = colors
        if len(path_text) > 48:
            path_text = "…" + path_text[-47:]  # keep the end of a long path, where the folder name is
        self.lines = [f"{model_label[:40]} · local · read-only", path_text]
        self.version = version
        self.width = TEXT_COL + max(len(LATIN) + len(version) + 12, *(len(x) for x in self.lines)) + 1
        self.rnd = random.Random(seed)
        self._targets = star_cells()
        self._starts = [(self.rnd.uniform(-2, ROWS + 1), self.rnd.uniform(0, self.width - 1)) for _ in self._targets]
        self._delays = [self.rnd.uniform(0, 0.2) for _ in self._targets]

    # ------------------------------------------------------------ helpers

    def _blank(self) -> list[list[Span]]:
        return [[(" ", None, False) for _ in range(self.width)] for _ in range(ROWS)]

    def _text(self, grid, name: list[Span] | None, info_alpha: float) -> None:
        row = 2
        if name is None:
            name = [(ch, None, True) for ch in LATIN]
            tail = f"  v{self.version} · παντού"
            for j, ch in enumerate(tail):
                grid[row][TEXT_COL + len(LATIN) + j] = (ch, self.c.dim, False)
        for i, cell in enumerate(name):
            grid[row][TEXT_COL + i] = cell
        if info_alpha > 0:
            col = self.c.dim if info_alpha >= 1 else mix(self.c.bg, self.c.dim, info_alpha)
            for k, line in enumerate(self.lines):
                for j, ch in enumerate(line[: self.width - TEXT_COL]):
                    grid[row + 1 + k][TEXT_COL + j] = (ch, col, False)

    @staticmethod
    def _merge(row: list[Span]) -> list[Span]:
        spans: list[list] = []
        for ch, col, bold in row:
            if spans and spans[-1][1] == col and spans[-1][2] == bold:
                spans[-1][0] += ch
            else:
                spans.append([ch, col, bold])
        return [tuple(s) for s in spans]

    # ------------------------------------------------------------- frames

    def launch(self, t: float) -> list[list[Span]]:
        """The launch at t seconds (0 .. LAUNCH_SECONDS)."""
        grid = self._blank()
        acc, dim = self.c.accent, self.c.dim
        for (tr, tc, glyph, _), (sr, sc), d in zip(self._targets, self._starts, self._delays):
            p = min(1.0, max(0.0, (t - d) / 0.6))
            e = ease_out(p)
            r, c = round(lerp(sr, tr, e)), round(lerp(sc, tc, e))
            if 0 <= r < ROWS and 0 <= c < self.width:
                grid[r][c] = (glyph, acc, True) if p >= 1 else ("·" if p < 0.5 else "•", mix(dim, acc, e), False)
        resolve_at = [0.5 + i * 0.09 for i in range(len(LATIN))]
        if t >= 0.2:
            name = None
            if t < resolve_at[-1]:
                name = []
                for i in range(len(LATIN)):
                    if t >= resolve_at[i]:
                        name.append((LATIN[i], None, True))
                    elif t >= 0.2 + i * 0.03:
                        name.append((GREEK[i] if t < 0.38 else self.rnd.choice(GLYPHS), acc, False))
                    else:
                        name.append((" ", None, False))
            self._text(grid, name, 0.0 if t < 0.95 else min(1.0, (t - 0.95) / 0.3))
        return [self._merge(r) for r in grid]

    def pulse(self, phase: float) -> list[list[Span]]:
        """The rays mid-shot; phase 0..1 is one shot outward."""
        grid = self._blank()
        acc, bg = self.c.accent, self.c.bg
        wave = (phase % 1.0) * 4.0
        for r, c, glyph, s in self._targets:
            if s == 0:
                grid[r][c] = ("◆", acc, True)
                continue
            d = wave - s
            if 0 <= d < 1:
                grid[r][c] = (glyph, acc, True)  # the bright head, shooting outward
            elif 1 <= d < 2:
                grid[r][c] = (glyph, mix(bg, acc, 0.55), False)
            elif 2 <= d < 3:
                grid[r][c] = (glyph, mix(bg, acc, 0.25), False)
        self._text(grid, None, 1.0)
        return [self._merge(r) for r in grid]

    def still(self) -> list[list[Span]]:
        """The complete star, not moving: for logs, screenshots and no-animation mode."""
        grid = self._blank()
        for r, c, glyph, _ in self._targets:
            grid[r][c] = (glyph, self.c.accent, True)
        self._text(grid, None, 1.0)
        return [self._merge(r) for r in grid]


def mini(t: float) -> str:
    return MINI[int(t / 0.25) % len(MINI)]
