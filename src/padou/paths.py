"""Where padou keeps its files.

* Linux and macOS: the XDG locations (~/.config/padou, ~/.local/share/padou,
  ~/.local/state/padou, ~/.cache/padou), each movable with its XDG_* variable.
* Windows: %APPDATA%\\padou for settings, %LOCALAPPDATA%\\padou for the rest.
"""

import os
from pathlib import Path

from padou.compat import WINDOWS


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def _win(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


if WINDOWS:
    CONFIG_DIR = _win("APPDATA", "AppData/Roaming") / "padou"
    _LOCAL = _win("LOCALAPPDATA", "AppData/Local") / "padou"
    DATA_DIR = _LOCAL / "data"
    CACHE_DIR = _LOCAL / "cache"
    STATE_DIR = _LOCAL / "state"
else:
    CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "padou"
    DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "padou"
    CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "padou"
    STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "padou"

CONFIG_FILE = CONFIG_DIR / "config.toml"
SESSIONS_DIR = DATA_DIR / "sessions"
HISTORY_FILE = DATA_DIR / "history"
SERVER_LOG = STATE_DIR / "llama-server.log"
SERVER_PID = STATE_DIR / "llama-server.pid"
UPDATE_CACHE = STATE_DIR / "update-check.json"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, SESSIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)
