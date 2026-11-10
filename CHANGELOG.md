# Changelog

## 0.2.0 (2026-11)

Faster on laptops, a new admin mode, optional web search, coding mode, and more reliable answers. Now on PyPI (`uv tool install padou`) and the AUR (`yay -S padou`).

- Packaging: published to PyPI by GitHub Actions on each release (trusted publishing, no stored tokens); an AUR package; `/update` upgrades PyPI installs from PyPI
- CONTRIBUTING.md, SECURITY.md (private vulnerability reporting), issue and pull request templates
- A rewritten README with fresh screenshots
- `/web on|off`: optional web search for the session, off by default, shown as `web` in the status line. Providers: DuckDuckGo (no account), your own SearXNG, or the Brave Search API. Never reads pages on your computer or local network
- `man_page` tool: the model reads the real manuals installed on your computer (or just the parts about one option) instead of guessing options
- More honest answers: the instructions tell the model to check first, never invent paths, options or settings, and say when its knowledge may be out of date. The context now includes your desktop, session type (Wayland/X11) and terminal
- Recovering from tool mistakes: broken calls get an example of a correct one, calls written as text are run as real calls, and a call that keeps failing is stopped after two tries
- Long tool output keeps its start and end, with a note about what was skipped
- Auto-compact: near a full context, older messages are summarised automatically and the last exchange is kept (`auto_compact` under `[chat]`)
- A quiet speed line under each answer: `3.1s · 140 tokens · 22 tok/s` (`reply_stats` under `[ui]`)
- `padou bench`: score the current model on 17 fixed questions (two about code) in a practice folder, saved for comparison
- Coding mode, when padou starts in a project: `@path` attaches a file (Tab completes it); the project's own test, lint and build commands can be run by the model in edit/admin mode after you approve (or allow them for the session); `/review` reviews uncommitted git changes; `d` in the confirmation box opens the change as a diff in VS Code / Code OSS / VSCodium; `/tutor` explains and hints instead of writing the code; `git_diff` and `git_diff_staged` commands
- Enter on a suggestion (a file after `@`, a folder) takes the suggestion instead of sending the message; on a command name (`/he` → `/help`) it runs the command
- A misspelled `@file` is reported (`no such file; did you mean …?`) instead of silently ignored, and a failed tool call keeps a readable title (`Read src/main.py`)
- `/retry` asks the last question again; `/copy code [n]` copies just a code block; `/export [file]` saves the conversation as Markdown
- While a tool runs, the spinner says what it's doing (`running disk_usage…`, `reading ~/x.conf…`, `searching the web…`)
- `/exit && stop` (or `/exit stop`) quits and stops the model server
- `/help` in groups; `/undo list` (also `/changes`); `/tools`, `/system` and `/screenshot` still work but are no longer listed

- `/mode read | edit | admin` replaces `/write` (which still works, as the old name of `/mode edit`). `Shift+Tab` switches between read-only and edit, quietly: the status line shows the mode
- Admin mode: change files anywhere, outside the start folder too. System files go through `sudo` (you type your password into sudo, never into padou), get a copy kept for `/undo` instead of the Trash, and are marked `· sudo`. The first time, admin mode explains itself and asks; after that it just switches. Deletes need the name typed, also for files. Never secrets, `/proc`, `/sys`, `/dev`, `/run`, `/boot`, your home folder or a main system folder as a whole
- An audit log: every change and undo goes to `changes.log` in padou's state folder
- The status line shows `read-only`, `✎ edit: <folder>` or `✎ admin: anywhere`

- Live progress while padou works: elapsed time, then tokens and speed as the reply streams
- `Esc` stops a reply and keeps what was written so far (`Ctrl+C` still cancels it completely)
- `/think [on|off]`: let thinking models (Qwen3.5) reason before answering, per session; `thinking` under `[chat]` sets the default. Off by default, because it's much slower on a laptop
- Shows "thinking…" or "choosing a tool…" while the model works, instead of a silent spinner
- llama.cpp keeps the conversation in its prompt cache between questions (`cache_prompt`, and `--cache-reuse 256` in new setups), so replies start sooner
- `padou setup` gives a 16K context on machines with 16 GB of RAM (was 8K), and restarts an already-running model server so new settings take effect; `padou doctor` suggests re-running setup when an older config misses these
- `/clear` also clears the screen (and scrollback), then redraws the banner
- `/models remove <n>`: free the space of a downloaded model (to the Trash; Ollama models are deleted), never the one in use

## 0.1.0 (2026-10)

First release.

- Runs on Linux, macOS and Windows (native, PowerShell installer `install.ps1`), tested on all three by GitHub Actions
- Write mode can also rewrite whole files, create folders, move/rename, and delete to the system Trash; every change in a confirmation box, folders need their name typed, `/undo` for all of them
- A progress bar while the model downloads on the first start (and up to an hour to finish, instead of 5 minutes)
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
