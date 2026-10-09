# padou installer for Windows (PowerShell).
# Installs the padou command with uv, then runs `padou setup`.
# It never installs llama.cpp or changes system settings; the wizard shows you the command.
# Read it before running it: it's short on purpose.
#
#   From a downloaded copy of padou:   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   Or straight from GitHub:           irm https://raw.githubusercontent.com/padou-dev/padou/main/install.ps1 | iex

$ErrorActionPreference = "Stop"
function Say($msg)  { Write-Host ":: $msg" -ForegroundColor Magenta }
function Warn($msg) { Write-Host "!! $msg" -ForegroundColor Yellow }

# 1. uv (manages padou's Python environment, and Python itself)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Warn "uv is not installed. It's the tool padou uses to install itself (https://docs.astral.sh/uv)."
    $answer = Read-Host "Install uv now with its official installer? [y/N]"
    if ($answer -notmatch '^(y|yes)$') {
        Say "Install it yourself with:  winget install --id=astral-sh.uv -e   then run this script again."
        exit 1
    }
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"   # so this window can use uv straight away
}

# 2. padou itself, in its own isolated environment
$here = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if ($here -and (Test-Path (Join-Path $here "pyproject.toml"))) {
    Say "Installing padou from $here ..."
    uv tool install --force --reinstall "$here"
} else {
    Say "Installing padou from GitHub ..."
    uv tool install --force "git+https://github.com/padou-dev/padou"
}
if ($LASTEXITCODE -ne 0) { Warn "The install failed (see above)."; exit 1 }
uv tool update-shell | Out-Null

# 3. the setup wizard: picks a server and model, writes the config
$padou = Join-Path (uv tool dir --bin) "padou.exe"
$config = Join-Path $env:APPDATA "padou\config.toml"
if (Test-Path $config) {
    Say "Keeping your existing config (re-run the wizard any time: padou setup)."
} else {
    & $padou setup
}

if (-not (Get-Command llama-server -ErrorAction SilentlyContinue)) {
    Warn "llama-server isn't installed yet. The easiest way:  winget install llama.cpp"
}
Say "Done. Open a NEW terminal, run 'padou doctor' to check everything, then 'padou' to chat."
