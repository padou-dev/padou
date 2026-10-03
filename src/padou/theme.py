"""Colours: taken from the active Omarchy theme when there is one.

Omarchy keeps the current theme in ~/.config/omarchy/current/theme/. Different
Omarchy versions and themes ship different files, so we try several and fall
back to a built-in palette (padou must work on any Linux, not just Omarchy).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

OMARCHY_THEME = Path.home() / ".config/omarchy/current/theme"
HEX = re.compile(r"#?([0-9a-fA-F]{6})\b")


@dataclass
class Theme:
    accent: str = "#7aa2f7"
    text: str = "default"
    dim: str = "#7f849c"
    ok: str = "#9ece6a"
    warn: str = "#e0af68"
    error: str = "#f7768e"
    source: str = "built-in"


def _hex(value) -> str | None:
    if not isinstance(value, str):
        return None
    m = HEX.search(value.strip().strip("'\""))
    return f"#{m.group(1).lower()}" if m else None


def _from_colors_toml(d: Path) -> dict | None:
    f = d / "colors.toml"
    if not f.exists():
        return None
    data = tomllib.loads(f.read_text(encoding="utf-8"))
    flat = {k.lower(): v for k, v in data.items() if not isinstance(v, dict)}
    for table in data.values():
        if isinstance(table, dict):
            flat.update({k.lower(): v for k, v in table.items() if not isinstance(v, dict)})
    return {
        "accent": _hex(flat.get("accent")) or _hex(flat.get("color4")) or _hex(flat.get("blue")),
        "dim": _hex(flat.get("color8")),
        "ok": _hex(flat.get("color2")) or _hex(flat.get("green")),
        "warn": _hex(flat.get("color3")) or _hex(flat.get("yellow")),
        "error": _hex(flat.get("color1")) or _hex(flat.get("red")),
    }


def _from_alacritty(d: Path) -> dict | None:
    f = d / "alacritty.toml"
    if not f.exists():
        return None
    data = tomllib.loads(f.read_text(encoding="utf-8"))
    colors = data.get("colors", {})
    normal, bright = colors.get("normal", {}), colors.get("bright", {})
    return {
        "accent": _hex(normal.get("blue")) or _hex(bright.get("blue")),
        "dim": _hex(bright.get("black")),
        "ok": _hex(normal.get("green")),
        "warn": _hex(normal.get("yellow")),
        "error": _hex(normal.get("red")),
    }


def _from_key_value(f: Path, sep: str) -> dict | None:
    """kitty.conf ("color4 #xxxxxx") and ghostty ("palette = 4=#xxxxxx")."""
    if not f.exists():
        return None
    found: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?:palette\s*=\s*)(\d+)\s*=\s*(#?[0-9a-fA-F]{6})", line)
        if m:
            found[f"color{m.group(1)}"] = m.group(2)
            continue
        parts = line.split(sep, 1) if sep in line else line.split(None, 1)
        if len(parts) == 2:
            found[parts[0].strip().lower()] = parts[1].strip()
    return {
        "accent": _hex(found.get("color4")),
        "dim": _hex(found.get("color8")),
        "ok": _hex(found.get("color2")),
        "warn": _hex(found.get("color3")),
        "error": _hex(found.get("color1")),
    }


def load(accent_override: str = "", theme_dir: Path = OMARCHY_THEME) -> Theme:
    theme = Theme()
    if theme_dir.is_dir():
        readers = [
            ("colors.toml", lambda: _from_colors_toml(theme_dir)),
            ("alacritty.toml", lambda: _from_alacritty(theme_dir)),
            ("kitty.conf", lambda: _from_key_value(theme_dir / "kitty.conf", " ")),
            ("ghostty.conf", lambda: _from_key_value(theme_dir / "ghostty.conf", "=")),
        ]
        for label, reader in readers:
            try:
                found = reader()
            except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
                continue
            if found and found.get("accent"):
                for key, value in found.items():
                    if value:
                        setattr(theme, key, value)
                theme.source = f"Omarchy theme ({theme_dir.resolve().name}, {label})"
                break
    override = _hex(accent_override)
    if override:
        theme.accent = override
        theme.source += ", accent from config"
    return theme
