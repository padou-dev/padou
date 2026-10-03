# Changelog

## 0.1.0 (2026-10)

First release.

- Terminal chat with streaming Markdown, a Claude Code-style banner and status line
- Read-only tools: `read_file`, `list_dir`, `search`, and 20 approved system commands
- Safety rules: no shell, no sudo, validated arguments, secrets blocked (symlinks included), capped output
- Slash commands: `/help`, `/clear`, `/models`, `/status`, `/copy`, `/tools`, `/resume`, `/compact`, `/doctor`, `/system`, `/exit`
- Saved sessions with `/resume` and `padou -c`
- `padou setup`: first-run wizard that detects OS, GPU, RAM and model servers, recommends a model and writes the config
- Starts llama-server (or `ollama serve`) automatically when nothing is running on localhost
- Works with llama.cpp, Ollama and LM Studio
- Omarchy theme colours (Omarchy 4 and older), with a built-in palette elsewhere
