"""Standard Linux (XDG) locations for padou's files."""

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "padou"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "padou"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "padou"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "padou"

CONFIG_FILE = CONFIG_DIR / "config.toml"
SESSIONS_DIR = DATA_DIR / "sessions"
HISTORY_FILE = DATA_DIR / "history"
SERVER_LOG = STATE_DIR / "llama-server.log"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, SESSIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)
