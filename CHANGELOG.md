# Changelog

## 0.1.0 (2026-10)

First release.

- Runs on Linux, macOS and Windows (native, PowerShell installer `install.ps1`), tested on all three by GitHub Actions
- Write mode can also rewrite whole files, create folders, move/rename, and delete to the system Trash; every change in a confirmation box, folders need their name typed, `/undo` for all of them
- Update check once a day in the background, and `/update` to install (`[updates] check = false` turns the check off)
- The compass star: a launch animation (dots gather from everywhere, παντού decodes to padou), rays that keep pulsing, and a pulsing star in the status line
- Terminal chat with streaming Markdown and a status line
- Read-only tools: `read_file`, `list_dir`, `search`, and 20 approved system commands
- Safety rules: no shell, no sudo, validated arguments, secrets blocked (symlinks included), capped output
- Slash commands: `/help`, `/clear`, `/models`, `/status`, `/copy`, `/tools`, `/resume`, `/compact`, `/doctor`, `/system`, `/exit`
- Saved sessions with `/resume` and `padou -c`
- `/screenshot` saves the screen as an SVG image
- Folder access: tools read only the start folder by default; `/folder add|remove|anywhere|restrict`, `[tools] scope` and `folders`
- Disclaimer (DISCLAIMER.md) and a one-time notice on first run
- Windows (WSL) install guide; `/copy` uses `clip.exe` under WSL
- Write mode: `/write <folder>`, `edit_file`/`write_file` with a diff and approval for every change, backups, `/undo`, `/changes`
- `/server` status, stop, restart and log; `[server] stop_on_exit`
- `/models` switches models in one step: restarts llama-server with the new one (downloading it once if needed) or pulls it with Ollama, and saves it as your default
- Eight models to choose from, by Alibaba (Qwen3.5 2B/4B/9B), IBM (Granite 4.0 Micro), Mistral AI (Ministral 3 3B/8B), Microsoft (Phi-4-mini) and Google (Gemma 4 E4B)
- `padou setup`: first-run wizard that detects OS, GPU, RAM and model servers, recommends a model and writes the config
- Starts llama-server (or `ollama serve`) automatically when nothing is running on localhost
- Works with llama.cpp, Ollama and LM Studio
- Omarchy theme colours (Omarchy 4 and older), with a built-in palette elsewhere
