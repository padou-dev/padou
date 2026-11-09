"""`padou bench`: a quick, repeatable test of how well the current model works with padou.

It asks a fixed set of questions in a small practice folder (made fresh each
time, with known files), lets the model use padou's read-only tools, and scores
each answer: did it use the right tool, and does the answer contain what it
should? Nothing is changed on your computer, and nothing goes online.

Use it to compare models (`/models`), settings or padou versions: the score
and the times are saved, so you can see whether a change made things better.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from rich.text import Text

from padou import compat, models, paths, tools, ui
from padou.client import Client, ServerError, text_tool_calls
from padou.config import Config

FILES = {
    "README.md": "# Lantern\n\nLantern is a tiny web server for photo galleries.\nIt is written in Go and released under the MIT licence.\n"
                 "Start it with `lantern serve`. The default port is set in config.ini.\n",
    "config.ini": "[server]\nport = 8471\nhost = 0.0.0.0\n\n[storage]\npath = /srv/photos\nmax_upload_mb = 25\n",
    "notes/todo.txt": "TODO: add dark mode\nfix the upload bug\nTODO: write tests for storage\nTODO: update the README\n",
    "notes/meeting.md": "# Meeting 12 March\n\nDecided: move the release to April.\nOwner of the release checklist: Dana.\n",
    "logs/app.log": "\n".join(
        [f"2026-03-01 10:{i:02d}:00 INFO request served in {12 + i}ms" for i in range(40)]
        + ["2026-03-01 10:41:07 ERROR storage: disk quota exceeded for /srv/photos"]
        + [f"2026-03-01 10:{42 + i:02d}:00 INFO request served in {9 + i}ms" for i in range(10)]
    ) + "\n",
    "src/main.go": "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(\"lantern ready\")\n}\n",
    "src/stats.py": "def average(numbers):\n    \"\"\"The mean of a list of numbers.\"\"\"\n    total = 0\n    for n in numbers:\n"
                    "        total += n\n    return total / (len(numbers) - 1)\n",
}


@dataclass
class Task:
    key: str
    question: str
    tools: tuple[str, ...] = ()  # one of these must be used (empty: none needed)
    expect: tuple[str, ...] = ()  # each must appear in the answer (any one of "a|b" alternatives)
    note: str = ""


TASKS = [
    Task("read-port", "Which port does this project's config.ini use?", ("read_file", "search"), ("8471",)),
    Task("read-language", "What programming language is Lantern written in? Check the README.", ("read_file",), ("go",)),
    Task("read-licence", "Which licence does this project use?", ("read_file", "search"), ("mit",)),
    Task("list-folders", "Which folders are in the current directory?", ("list_dir",), ("notes", "logs", "src")),
    Task("search-todos", "In the notes folder, how many lines are marked with TODO?", ("search", "read_file"), ("3|three",)),
    Task("search-error", "Is there an ERROR in logs/app.log? What does it say?", ("search", "read_file"), ("quota",)),
    Task("read-owner", "Who owns the release checklist, according to the meeting notes?", ("read_file", "search"), ("dana",)),
    Task("read-upload", "What's the maximum upload size in the config?", ("read_file", "search"), ("25",)),
    Task("cmd-disk", "How much free disk space do I have?", ("run_command",), ()),
    Task("cmd-memory", "How much RAM does this computer have?", ("run_command",), ()),
    Task("cmd-uptime", "How long has this computer been running?", ("run_command",), ()),
    Task("man-option", "Which option of the ls command shows hidden files? Check its manual if you can.", (), ("-a|--all",),
         "the manual helps, but knowing it is fine too"),
    Task("code-explain", "What does src/main.go print when it runs?", ("read_file",), ("lantern ready",)),
    Task("code-bug", "The average function in src/stats.py gives wrong results. What's the bug?", ("read_file",),
         ("len(numbers) - 1|- 1|minus one|minus 1|off-by-one|off by one|len(numbers)",)),
    Task("math", "What is 17 multiplied by 23?", (), ("391",)),
    Task("no-tool-needed", "In one sentence: what is a symbolic link?", (), ("link|points|refer",)),
    Task("honest-missing", "What does the file notes/budget.xlsx say?", ("read_file", "list_dir", "search"),
         ("not|doesn't|does not|no such|couldn't|can't|cannot",), "the file doesn't exist: it must say so, not invent"),
]


@dataclass
class Outcome:
    key: str
    passed: bool
    used: list[str] = field(default_factory=list)
    seconds: float = 0.0
    answer: str = ""
    why: str = ""


def _practice_folder() -> Path:
    root = Path(tempfile.mkdtemp(prefix="padou-bench-"))
    for name, text in FILES.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root.resolve()


def score(task: Task, used: list[str], answer: str) -> tuple[bool, str]:
    low = answer.lower()
    if task.tools and not any(t in used for t in task.tools):
        return False, f"didn't use {' or '.join(task.tools)}"
    for want in task.expect:
        if not any(alt.lower() in low for alt in want.split("|")):
            return False, f"answer lacks {want!r}"
    if not answer.strip():
        return False, "empty answer"
    return True, ""


def run_task(task: Task, client: Client, cfg: Config, folder: Path, system: str, extra: dict) -> Outcome:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task.question}]
    defs = tools.definitions(cfg.tools.commands)
    used: list[str] = []
    started = time.monotonic()
    answer = ""
    for _ in range(cfg.chat.max_tool_rounds + 1):
        reply = client.chat(messages, tools=defs, temperature=0.2, stream=False, extra=extra)
        if not reply.tool_calls:
            reply.content, reply.tool_calls = text_tool_calls(reply.content)
        if not reply.tool_calls:
            answer = reply.content
            break
        messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls]})
        for call in reply.tool_calls:
            used.append(call.name)
            result = tools.execute(call.name, call.arguments, folder, cfg.tools.max_output_chars,
                                   cfg.tools.command_timeout, cfg.tools.commands, [folder])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result.output})
    passed, why = score(task, used, answer)
    return Outcome(task.key, passed, used, round(time.monotonic() - started, 1), answer.strip()[:300], why)


def run(cfg: Config, client: Client, th, only: list[str] | None = None) -> int:
    from padou.app import App

    tasks = [t for t in TASKS if not only or t.key in only]
    up, detail = client.health()
    if not up:
        ui.error(th, f"The model server isn't answering ({detail}). Start padou once, or: padou doctor")
        return 1
    folder = _practice_folder()
    try:
        app = App(cfg, th, client, folder)  # the same instructions padou uses in a chat
        system = app.system_message()["content"]
        extra = models.request_extra(cfg.server, cfg.chat.thinking)
        ui.console.print(Text.assemble(("padou bench", "bold"), (f" · {cfg.server.model_label or cfg.server.model} · "
                                                               f"{len(tasks)} questions in a practice folder", "dim")))
        outcomes: list[Outcome] = []
        for task in tasks:
            with ui.ReplyView(th, f"{task.key}: {task.question[:60]}"):
                try:
                    outcome = run_task(task, client, cfg, folder, system, extra)
                except ServerError as exc:
                    outcome = Outcome(task.key, False, why=str(exc)[:120])
            mark = Text("✓", style=f"bold {th.ok}") if outcome.passed else Text("✗", style=f"bold {th.error}")
            ui.console.print(Text.assemble(mark, (f" {task.key:<16}", "bold"), (f"{outcome.seconds:>5.1f}s  ", "dim"),
                                           (", ".join(outcome.used) or "no tools", "dim"),
                                           (f"  {outcome.why}" if outcome.why else "", th.warn)))
            outcomes.append(outcome)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    passed = sum(o.passed for o in outcomes)
    total_time = sum(o.seconds for o in outcomes)
    ui.console.print(Text.assemble(("\nScore: ", "bold"), (f"{passed}/{len(outcomes)}", "bold"),
                                   (f" in {total_time:.0f}s (average {total_time / max(1, len(outcomes)):.1f}s per question)", "dim")))
    saved = _save(cfg, outcomes)
    ui.console.print(Text(f"Saved to {ui.short_path(saved)}. Run it again after changing the model or settings to compare.", style="dim"))
    return 0  # a low score isn't an error; it's the result


def _save(cfg: Config, outcomes: list[Outcome]) -> Path:
    folder = paths.STATE_DIR / "bench"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    data = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "model": cfg.server.model_label or cfg.server.model,
        "system": compat.OS_NAME,
        "score": f"{sum(o.passed for o in outcomes)}/{len(outcomes)}",
        "results": [asdict(o) for o in outcomes],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
