#!/bin/sh
# padou installer for Linux and macOS. One line:
#
#   curl -fsSL https://raw.githubusercontent.com/padou-dev/padou/main/install.sh | sh
#
# What it does, asking before anything is installed:
#   1. uv, the tool that installs padou (and Python for it), if you don't have it yet;
#   2. padou itself, from PyPI (or from this folder, if you run it inside a copy of padou);
#   3. `padou setup`, which finds or downloads a model server (llama.cpp) and a model.
# It never uses sudo and never changes system settings. Read it first if you like: it's short on purpose.
set -eu

say()  { printf '\033[1;35m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# Questions are read from the terminal, so they work even when this script arrives through a pipe.
has_tty() { (exec < /dev/tty > /dev/tty) 2>/dev/null; }

ask() {  # ask "Question?" default(y|n)
    answer=""
    if has_tty; then
        printf '%s ' "$1" > /dev/tty
        read -r answer < /dev/tty || answer=""
    fi
    case "$answer" in
        [Yy]*) return 0 ;;
        [Nn]*) return 1 ;;
        "") [ "$2" = y ] ;;
        *) return 1 ;;
    esac
}

fetch() {  # fetch URL: print it to stdout
    if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"
    elif command -v wget >/dev/null 2>&1; then wget -qO- "$1"
    else warn "Neither curl nor wget is installed."; return 1
    fi
}

# Run from a downloaded copy of padou (./install.sh)? Then install that copy.
case "$0" in *install.sh) cd "$(dirname "$0")" ;; esac

# 1. uv
export PATH="$HOME/.local/bin:$PATH"   # where uv puts itself and padou
if ! command -v uv >/dev/null 2>&1; then
    say "padou installs itself with uv (https://docs.astral.sh/uv), which also takes care of Python."
    if ask "Install uv now with its official installer (into ~/.local/bin, no sudo)? [Y/n]" y; then
        fetch https://astral.sh/uv/install.sh | sh
    else
        if command -v pacman >/dev/null 2>&1; then say "Or install it with: sudo pacman -S uv"
        elif command -v brew >/dev/null 2>&1; then say "Or install it with: brew install uv"
        fi
        say "Then run this installer again."
        exit 1
    fi
    command -v uv >/dev/null 2>&1 || { warn "uv didn't install (see above)."; exit 1; }
fi

# 2. padou
if [ -f pyproject.toml ] && grep -q '^name = "padou"' pyproject.toml; then
    if [ -e .git ]; then
        # a git clone: install it "editable", so `git pull` and /update take effect straight away
        say "Installing padou from your clone..."
        uv tool install --force --reinstall --editable .
    else
        say "Installing padou from this folder..."
        uv tool install --force --reinstall .
    fi
else
    say "Installing padou..."
    uv tool install --force padou
fi
uv tool update-shell >/dev/null 2>&1 || true   # so new terminals find the padou command
padou_bin="$(uv tool dir --bin)/padou"

# 3. the setup wizard: finds (or downloads) a model server, picks a model, writes the config
if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/padou/config.toml" ]; then   # same place on Linux and macOS
    say "Keeping your existing settings (run the wizard again any time: padou setup)."
elif has_tty; then
    "$padou_bin" setup < /dev/tty || true
else
    say "Run 'padou setup' in a terminal to choose a model."
fi

say "Done. Open a new terminal and type: padou"
