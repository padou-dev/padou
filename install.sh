#!/usr/bin/env bash
# padou installer: installs the padou command, then runs `padou setup`.
# It never installs llama.cpp or uses sudo; the wizard shows you the command.
# Read it before running it: it's short on purpose.
set -euo pipefail

cd "$(dirname "$0")"

say()  { printf '\033[1;35m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# 1. uv (manages padou's Python environment)
if ! command -v uv >/dev/null 2>&1; then
    warn "uv is not installed."
    if command -v pacman >/dev/null 2>&1; then
        say "Install it with: sudo pacman -S uv   (then run this script again)"
    else
        say "See https://docs.astral.sh/uv/getting-started/installation/"
    fi
    exit 1
fi

# 2. padou itself, in its own isolated environment
say "Installing padou with uv tool install..."
uv tool install --force --reinstall .

# 3. the setup wizard: picks a server and model, writes the config
if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/padou/config.toml" ]; then
    say "Keeping your existing config (re-run the wizard any time: padou setup)."
else
    "$(uv tool dir --bin)/padou" setup || true   # full path: ~/.local/bin may not be on PATH yet
fi

say "Done. Run 'padou doctor' to check everything, then 'padou' to chat."
if ! command -v padou >/dev/null 2>&1; then
    warn "'padou' isn't on your PATH yet. Run: uv tool update-shell   (then open a new terminal)"
fi
