# Changelog

## 0.1.0 (2026-10)

First release.

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
- `padou setup`: first-run wizard that detects OS, GPU, RAM and model servers, recommends a model and writes the config
- Starts llama-server (or `ollama serve`) automatically when nothing is running on localhost
- Works with llama.cpp, Ollama and LM Studio
- Omarchy theme colours (Omarchy 4 and older), with a built-in palette elsewhere
