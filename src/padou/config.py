"""Loading padou's configuration from ~/.config/padou/config.toml."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from padou import paths

DEFAULT_SYSTEM_PROMPT = """\
You are padou, a concise assistant running locally in the user's Linux terminal.
You help with general questions, system administration and coding.

You are READ-ONLY. Through your tools you can read files, list directories,
search text in files and run a small set of approved read-only system commands.
You cannot edit files, install software or run arbitrary commands. When a change
is needed, show the exact command for the user to run themselves.

Use a tool whenever the answer depends on the user's actual system instead of
guessing. Never invent file contents or command output. Keep answers short and
practical, and format them in Markdown."""


@dataclass
class ServerConfig:
    url: str = "http://127.0.0.1:8080"
    model: str = "qwen3.5-4b"
    model_label: str = "Qwen3.5 4B"
    api_key: str = ""
    timeout: float = 180.0
    context_size: int = 8192
    autostart: bool = True  # start llama-server if nothing answers on localhost
    start_command: str = (
        "llama-server -hf bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M -ngl 99 --jinja "
        "--reasoning-budget 0 -c 8192 --host 127.0.0.1 --port 8080 --no-mmproj"
    )
    startup_timeout: float = 300.0  # seconds; the first start may download the model


@dataclass
class ChatConfig:
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    temperature: float = 0.7
    stream: bool = True
    max_tool_rounds: int = 8


@dataclass
class ToolsConfig:
    enabled: bool = True
    commands: bool = True
    max_output_chars: int = 8000
    command_timeout: float = 15.0


@dataclass
class UIConfig:
    banner: str = "full"  # "full" or "compact"
    clear_on_start: bool = True
    accent: str = ""  # hex colour override, e.g. "#ff5fa2"; empty = from theme
    show_tool_output: bool = False


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    source: Path | None = None
    warnings: list[str] = field(default_factory=list)


def _apply(section_obj, values: dict, section: str, warnings: list[str]) -> None:
    known = {f.name: f for f in fields(section_obj)}
    for key, value in values.items():
        if key not in known:
            warnings.append(f"unknown setting [{section}] {key}")
            continue
        current = getattr(section_obj, key)
        expected = type(current)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected):
            warnings.append(
                f"[{section}] {key} should be {expected.__name__}, got {type(value).__name__}"
            )
            continue
        setattr(section_obj, key, value)


def load(path: Path | None = None) -> Config:
    path = path or paths.CONFIG_FILE
    cfg = Config()
    if not path.exists():
        return cfg
    cfg.source = path
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        cfg.warnings.append(f"could not read {path}: {exc}")
        return cfg
    for name in ("server", "chat", "tools", "ui"):
        section = data.pop(name, None)
        if section is None:
            continue
        if not isinstance(section, dict):
            cfg.warnings.append(f"[{name}] should be a table")
            continue
        _apply(getattr(cfg, name), section, name, cfg.warnings)
    for leftover in data:
        cfg.warnings.append(f"unknown section [{leftover}]")
    if cfg.ui.banner not in ("full", "compact"):
        cfg.warnings.append('[ui] banner should be "full" or "compact"')
        cfg.ui.banner = "full"
    return cfg


def _q(value: str) -> str:
    """A TOML string. JSON string escaping is valid TOML for basic strings."""
    return json.dumps(value, ensure_ascii=False)


def render(server: ServerConfig | None = None, banner: str = "full") -> str:
    """The full, commented config file, with the [server] values filled in."""
    s = server or ServerConfig()
    return f"""\
# padou configuration
# Location: ~/.config/padou/config.toml   (re-create it any time with: padou setup)

[server]
# Any OpenAI-compatible server: llama.cpp (llama-server), Ollama, LM Studio...
url = {_q(s.url)}
model = {_q(s.model)}
model_label = {_q(s.model_label)}
api_key = {_q(s.api_key)}
timeout = {s.timeout:g}                           # seconds to wait for a reply
context_size = {s.context_size}                     # fallback when the server doesn't report it
autostart = {str(s.autostart).lower()}                        # start the server if nothing answers (localhost only)
start_command = {_q(s.start_command)}
startup_timeout = {s.startup_timeout:g}                   # the first start may download the model

[chat]
temperature = 0.7
stream = true                           # show replies as they are written
max_tool_rounds = 8                     # tool calls allowed per question
# system_prompt = \"\"\"You are padou...\"\"\"

[tools]
enabled = true                          # read-only file tools
commands = true                         # approved read-only system commands
max_output_chars = 8000                 # tool output is cut to this size
command_timeout = 15

[ui]
banner = {_q(banner)}                         # "full" or "compact"
clear_on_start = true                   # clear the terminal before the banner
accent = ""                             # e.g. "#ff5fa2"; empty = Omarchy theme accent
show_tool_output = false                # print full tool output, not just a summary
"""


EXAMPLE_CONFIG = render()


def write_example(path: Path | None = None, overwrite: bool = False, text: str | None = None) -> Path:
    path = path or paths.CONFIG_FILE
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.replace(path.with_suffix(".toml.bak"))  # keep the old one, just in case
    path.write_text(text or EXAMPLE_CONFIG, encoding="utf-8")
    return path
