# Installing padou on Windows

padou runs natively on Windows 10 and 11, in PowerShell or Windows Terminal. No WSL needed. (If you already use WSL, the [WSL guide](install_windows_wsl.md) works too.)

> [!TIP]
> Use **Windows Terminal** (built into Windows 11, free in the Microsoft Store for Windows 10). The old console window can't draw padou's star, boxes and colours properly.

## 1. Install padou

Open **PowerShell** (or Windows Terminal) and paste this one line:

```powershell
irm https://raw.githubusercontent.com/padou-dev/padou/main/install.ps1 | iex
```

It asks before it installs anything, and needs no administrator rights:

1. **uv**, the tool that installs padou and Python for it, if you don't have it yet (with uv's official installer).
2. **padou**, from [PyPI](https://pypi.org/project/padou/).
3. **The setup wizard.** It looks at your graphics card and RAM and finds any model server you already have. If you have none, it **offers to download llama.cpp** (the Vulkan build, which works with NVIDIA, AMD and Intel graphics) into padou's own folder. Then it recommends a model and starts the server.

The installer is short; read it first if you like. If you'd rather run it from a downloaded copy of padou: `powershell -ExecutionPolicy Bypass -File .\install.ps1` in that folder. A git clone is installed **editable**: `git pull` or `/update` takes effect the next time you start padou.

> [!NOTE]
> **Prefer your own model server?** `winget install llama.cpp` or `winget install Ollama.Ollama` before running the installer; the wizard finds them. For the fastest answers on NVIDIA you can also use llama.cpp's `win-cuda` build from the [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), unzipped to a folder on your PATH.

## 2. Start padou

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
- **Deleting** in edit and admin mode sends things to the **Recycle Bin**. Admin mode can't ask for administrator rights itself: for protected locations, start padou in a terminal opened with *Run as administrator*.
- **`/update`** installs a new version in a new window right after you quit padou, because Windows doesn't let a running program replace itself.
- **Where padou keeps things:** settings in `%APPDATA%\padou`, everything else (sessions, history, server log, backups) in `%LOCALAPPDATA%\padou`.
- **Protected locations** include Windows' credential stores and browser profiles, as well as `C:\Windows`, `C:\Program Files` and `C:\ProgramData` for write mode.

## Troubleshooting

> [!bug] `running scripts is disabled on this system`
> PowerShell blocks script files by default. Run the installer with `powershell -ExecutionPolicy Bypass -File .\install.ps1`; that allows it for this one run only.

> [!bug] `padou` or `uv` is not recognized
> Open a **new** terminal window. If it still isn't found, run `uv tool update-shell` and open another new window.

> [!bug] `llama-server is not installed`
> Run `padou setup` again and let it download llama.cpp, or install your own (`winget install llama.cpp`), or put the full path to `llama-server.exe` at the start of `start_command` in `%APPDATA%\padou\config.toml`.

> [!bug] Answers are very slow
> llama.cpp is probably running on the CPU. `/server log` shows which devices it found. Update your graphics driver (Vulkan comes with it), or use the CUDA build from the llama.cpp releases on NVIDIA.

> [!bug] Strange characters instead of the star or the boxes
> Use Windows Terminal, and a font with good Unicode coverage (Cascadia Code, the default, is fine).

## Uninstall

```powershell
uv tool uninstall padou
Remove-Item -Recurse "$env:APPDATA\padou", "$env:LOCALAPPDATA\padou"   # settings, sessions, downloaded llama.cpp
```
