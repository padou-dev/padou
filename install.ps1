# padou installer for Windows (PowerShell). One line:
#
#   irm https://raw.githubusercontent.com/padou-dev/padou/main/install.ps1 | iex
#
# Or from a downloaded copy of padou:   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# What it does, asking before anything is installed:
#   1. uv, the tool that installs padou (and Python for it), if you don't have it yet;
#   2. padou itself, from PyPI (or from this folder, if you run it inside a copy of padou);
#   3. `padou setup`, which finds or downloads a model server (llama.cpp) and a model.
# It never needs administrator rights or changes system settings. Read it first if you like: it's short on purpose.

$ErrorActionPreference = "Stop"
function Say($msg)  { Write-Host ":: $msg" -ForegroundColor Magenta }
function Warn($msg) { Write-Host "!! $msg" -ForegroundColor Yellow }

# 1. uv (manages padou's Python environment, and Python itself)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Warn "uv is not installed. It's the tool padou uses to install itself (https://docs.astral.sh/uv)."
    $answer = Read-Host "Install uv now with its official installer (no administrator rights needed)? [Y/n]"
    if ($answer -match '^(n|no)$') {
        Say "Install it yourself with:  winget install --id=astral-sh.uv -e   then run this script again."
        return
    }
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"   # so this window can use uv straight away
}

# 2. padou itself, in its own isolated environment
$here = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if ($here -and (Test-Path (Join-Path $here "pyproject.toml"))) {
    if (Test-Path (Join-Path $here ".git")) {
        # A git clone: install it "editable", so `git pull` and /update take effect straight away
        Say "Installing padou from your clone in $here ..."
        uv tool install --force --reinstall --editable "$here"
    } else {
        Say "Installing padou from $here ..."
        uv tool install --force --reinstall "$here"
    }
} else {
    Say "Installing padou ..."
    uv tool install --force padou
}
if ($LASTEXITCODE -ne 0) { Warn "The install failed (see above)."; return }
uv tool update-shell | Out-Null

# 3. the setup wizard: picks a server and model, writes the config
$padou = Join-Path (uv tool dir --bin) "padou.exe"
$config = Join-Path $env:APPDATA "padou\config.toml"
if (Test-Path $config) {
    Say "Keeping your existing settings (run the wizard again any time: padou setup)."
} else {
    & $padou setup
}

Say "Done. Open a NEW terminal and type: padou"
