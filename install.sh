#!/usr/bin/env bash
# padou installer: installs the padou command and a starter config.
# It does NOT install llama.cpp or download a model; it tells you how.
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

# 3. a starter config (never overwrites an existing one)
if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/padou/config.toml" ]; then
    say "Keeping your existing config."
else
    padou --init-config
fi

# 4. model server
if command -v llama-server >/dev/null 2>&1; then
    say "llama-server found: $(command -v llama-server)"
else
    warn "llama-server not found."
    if command -v yay >/dev/null 2>&1; then
        say "On Arch: yay -S llama.cpp-vulkan-git   (AMD/Intel GPU)"
    fi
    say "Or use Ollama / LM Studio and set [server] url in the config."
fi

# 5. optional: start llama-server on login
if command -v llama-server >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
    read -r -p "Start llama-server automatically on login (systemd user service)? [y/N] " ans
    if [[ "${ans,,}" == y* ]]; then
        mkdir -p "$HOME/.config/systemd/user"
        sed "s|/usr/bin/llama-server|$(command -v llama-server)|" contrib/llama-server.service \
            > "$HOME/.config/systemd/user/llama-server.service"
        systemctl --user daemon-reload
        systemctl --user enable --now llama-server
        say "Enabled. Logs: journalctl --user -u llama-server -f"
        say "The first start downloads the model (~2.5 GB), so give it a few minutes."
    fi
fi

say "Done. Run 'padou doctor' to check everything, then 'padou' to chat."
if ! command -v padou >/dev/null 2>&1; then
    warn "'padou' isn't on your PATH yet. Run: uv tool update-shell   (then open a new terminal)"
fi
