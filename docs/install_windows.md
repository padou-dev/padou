# Installing padou on Windows

padou runs natively on Windows 10 and 11, in PowerShell or Windows Terminal. No WSL needed. (If you already use WSL, the [WSL guide](install_windows_wsl.md) works too.)

> [!TIP]
> Use **Windows Terminal** (built into Windows 11, free in the Microsoft Store for Windows 10). The old console window can't draw padou's star, boxes and colours properly.

## 1. Install uv

[uv](https://docs.astral.sh/uv/) installs padou, and Python with it. Open **PowerShell** and run:

```powershell
winget install --id=astral-sh.uv -e
```

Close PowerShell and open a new window, so it finds `uv`. Check with `uv --version`.

> [!NOTE]
> The installer in step 3 can also install uv for you, using uv's official install script.

## 2. Install llama.cpp (the model server)

```powershell
winget install llama.cpp
```

Open a new window and check: `llama-server --version`.

> [!TIP]
> **Graphics card:** for the fastest answers, llama.cpp should use your GPU. If `llama-server --list-devices` doesn't show your graphics card, download a ready-made build from the [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) instead: the `win-cuda` zip for NVIDIA, or `win-vulkan` for AMD and Intel. Unzip it to a folder (for example `C:\llama.cpp`) and add that folder to your PATH, or put the full path in padou's `start_command`.

Prefer something simpler? [Ollama](https://ollama.com) works too: `winget install Ollama.Ollama`. padou's setup finds it.

## 3. Install padou

**Straight from GitHub**, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/padou-dev/padou/main/install.ps1 | iex
```

**Or from a copy you downloaded** (or cloned with Git): open PowerShell in that folder and run

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

If the folder is a git clone, padou is installed **editable**: it runs from the clone, so `git pull` or `/update` takes effect the next time you start padou. The installer is short; read it first if you like. It installs the `padou` command with uv, then starts `padou setup`. The setup wizard looks at your system, finds llama.cpp or Ollama, recommends a model and writes the config. It never changes system settings.

## 4. Start padou

Open a **new** terminal window (so it finds the `padou` command) and run:

```powershell
padou doctor     # checks the server, the model and the config
padou            # start chatting
```

The first start downloads the model (about 3 GB), so give it a few minutes.

> [!TIP]
> Start padou inside a project folder (`cd $HOME\Projects\my-app`, then `padou`). Its tools can read the folder you start it in; `/folder add` allows more.

## What's different on Windows

- **System commands:** the model's approved commands use Windows tools: `ipconfig`, `netstat`, `sc.exe query`, `where.exe`, `winget list`, and a few short, fixed PowerShell one-liners (disk space, memory, uptime, processes, recent errors). The model can't add to them.
- **Deleting in write mode** sends things to the **Recycle Bin**.
- **`/update`** installs a new version in a new window right after you quit padou, because Windows doesn't let a running program replace itself.
- **Where padou keeps things:** settings in `%APPDATA%\padou`, everything else (sessions, history, server log, backups) in `%LOCALAPPDATA%\padou`.
- **Protected locations** include Windows' credential stores and browser profiles, as well as `C:\Windows`, `C:\Program Files` and `C:\ProgramData` for write mode.

## Troubleshooting

> [!bug] `running scripts is disabled on this system`
> PowerShell blocks script files by default. Run the installer with `powershell -ExecutionPolicy Bypass -File .\install.ps1`; that allows it for this one run only.

> [!bug] `padou` or `uv` is not recognized
> Open a **new** terminal window. If it still isn't found, run `uv tool update-shell` and open another new window.

> [!bug] `llama-server is not installed`
> Install it (step 2), or put the full path to `llama-server.exe` at the start of `start_command` in `%APPDATA%\padou\config.toml`.

> [!bug] Answers are very slow
> llama.cpp is probably running on the CPU. Check `llama-server --list-devices`, and use the CUDA or Vulkan build (step 2).

> [!bug] Strange characters instead of the star or the boxes
> Use Windows Terminal, and a font with good Unicode coverage (Cascadia Code, the default, is fine).

## Uninstall

```powershell
uv tool uninstall padou
Remove-Item -Recurse "$env:APPDATA\padou", "$env:LOCALAPPDATA\padou"
```
