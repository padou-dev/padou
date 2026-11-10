"""Coding mode: what kind of project padou was started in, and how to test, lint and build it.

padou never guesses a command: it only offers the ones it finds in the project's
own files (pyproject.toml, package.json, Cargo.toml, go.mod, a Makefile...).
The model can ask to run one; you approve each run (or allow it for the session).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from padou import compat
from padou.tools import squeeze

MARKERS = ("pyproject.toml", "setup.py", "package.json", "Cargo.toml", "go.mod", "CMakeLists.txt", "Makefile", ".git")


@dataclass
class Project:
    root: Path
    name: str
    kinds: list[str] = field(default_factory=list)  # "Python", "Node.js", "Rust", "Go", "C/C++", "Make"
    git: bool = False
    commands: dict[str, list[str]] = field(default_factory=dict)  # "test" -> ["uv", "run", "pytest", "-q"]

    def describe(self) -> str:
        kinds = " + ".join(self.kinds) or "unknown"
        cmds = ", ".join(f"{k} ({' '.join(v)})" for k, v in self.commands.items()) or "none found"
        return f"{kinds} project '{self.name}' at {self.root}{' (git)' if self.git else ''}. Project commands: {cmds}"


def find_root(cwd: Path) -> Path | None:
    """The project folder: the start folder, or the nearest folder above it with a project file (up to 4 levels)."""
    home = Path.home().resolve()
    here = cwd.resolve()
    for folder in [here, *list(here.parents)[:4]]:
        if folder == home or folder == Path(folder.anchor):
            return None  # never treat the whole home folder or a drive as one project
        if any((folder / m).exists() for m in MARKERS):
            return folder
    return None


def _runner(root: Path) -> list[str]:
    """How to run Python tools in this project: through uv or poetry if the project uses them."""
    if (root / "uv.lock").exists() and shutil.which("uv"):
        return ["uv", "run"]
    if (root / "poetry.lock").exists() and shutil.which("poetry"):
        return ["poetry", "run"]
    venv = root / ".venv" / ("Scripts/python.exe" if compat.WINDOWS else "bin/python")
    if venv.exists():
        return [str(venv), "-m"]  # the project's own virtual environment
    return ["python3" if not compat.WINDOWS else "python", "-m"]


def _node_runner(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def _make_targets(root: Path) -> set[str]:
    try:
        text = (root / "Makefile").read_text(errors="replace")
    except OSError:
        return set()
    return set(re.findall(r"^([A-Za-z][\w-]*)\s*:(?!=)", text, re.MULTILINE))


def detect(cwd: Path) -> Project | None:
    root = find_root(cwd)
    if root is None:
        return None
    p = Project(root, root.name, git=(root / ".git").exists())

    pyproject = root / "pyproject.toml"
    if pyproject.exists() or (root / "setup.py").exists():
        p.kinds.append("Python")
        data: dict = {}
        try:
            data = tomllib.loads(pyproject.read_text()) if pyproject.exists() else {}
        except (OSError, tomllib.TOMLDecodeError):
            pass
        p.name = str(data.get("project", {}).get("name") or p.name)
        text = json.dumps(data).lower()
        run = _runner(root)
        if "pytest" in text or (root / "tests").is_dir() or (root / "pytest.ini").exists():
            p.commands["test"] = run + ["pytest", "-q"]
        if "ruff" in text or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
            p.commands["lint"] = run + ["ruff", "check", "."]

    pkg = root / "package.json"
    if pkg.exists():
        p.kinds.append("Node.js")
        try:
            data = json.loads(pkg.read_text())
        except (OSError, ValueError):
            data = {}
        p.name = str(data.get("name") or p.name)
        tool = _node_runner(root)
        for script in ("test", "lint", "build", "typecheck"):
            if script in (data.get("scripts") or {}):
                p.commands.setdefault(script, [tool, "run", script])

    if (root / "Cargo.toml").exists():
        p.kinds.append("Rust")
        p.commands.setdefault("test", ["cargo", "test"])
        p.commands.setdefault("lint", ["cargo", "clippy"])
        p.commands.setdefault("build", ["cargo", "build"])

    if (root / "go.mod").exists():
        p.kinds.append("Go")
        p.commands.setdefault("test", ["go", "test", "./..."])
        p.commands.setdefault("lint", ["go", "vet", "./..."])
        p.commands.setdefault("build", ["go", "build", "./..."])

    if (root / "CMakeLists.txt").exists():
        p.kinds.append("C/C++")

    targets = _make_targets(root) if (root / "Makefile").exists() else set()
    if targets:
        p.kinds.append("Make")
        for t in ("test", "lint", "build"):
            if t in targets:
                p.commands.setdefault(t, ["make", t])
    # only commands whose program is actually installed
    p.commands = {k: v for k, v in p.commands.items() if shutil.which(v[0])}
    return p


# --------------------------------------------------------------------------- #
# Running a project command (after the user approved it)
# --------------------------------------------------------------------------- #


def run(project: Project, name: str, limit: int, timeout: float = 300) -> tuple[bool, str]:
    argv = project.commands.get(name)
    if argv is None:
        raise ValueError(f"no {name!r} command in this project. Available: {', '.join(project.commands) or 'none'}")
    env = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0", CI="1", PAGER="cat")
    try:
        proc = subprocess.run(argv, cwd=project.root, capture_output=True, text=True, errors="replace",
                              timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"$ {' '.join(argv)}\n(stopped after {timeout:.0f}s)"
    except OSError as exc:
        return False, f"$ {' '.join(argv)}\ncouldn't run it: {exc}"
    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip() or "(no output)"
    return proc.returncode == 0, f"$ {' '.join(argv)}  (exit code {proc.returncode})\n" + squeeze(out, limit)


def definition(project: Project) -> dict:
    return {"type": "function", "function": {
        "name": "project_command",
        "description": "Run one of this project's own commands (tests, linter, build) and get the output. "
                       "Use it after a change to check it works. The user approves each run. Available: "
                       + ", ".join(f"{k} = {' '.join(v)}" for k, v in project.commands.items()),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": list(project.commands)}}, "required": ["name"]}}}


# --------------------------------------------------------------------------- #
# @file mentions: attach a file to your message
# --------------------------------------------------------------------------- #

MENTION = re.compile(r"(?<![\w/@.])@((?:~|\.{1,2})?/?[\w.\-/~]+)")
MAX_ATTACH_CHARS = 12_000


def _suggest(path: Path) -> str:
    """'; did you mean @src/padou/__main__.py?' from the files next to where it should be."""
    import difflib

    folder = path.parent
    try:
        names = [p.name for p in folder.iterdir() if p.is_file()]
    except OSError:
        return ""
    close = difflib.get_close_matches(path.name, names, n=2, cutoff=0.5)
    return ("; did you mean " + " or ".join(close) + "?") if close else ""


def attachments(text: str, cwd: Path, readable, limit: int = MAX_ATTACH_CHARS) -> tuple[str, list[str], list[str]]:
    """Find @path mentions; returns (extra text for the model, attached names, problems)."""
    from padou import tools

    extra, names, problems = [], [], []
    budget = limit
    for m in dict.fromkeys(MENTION.findall(text)):
        path = tools.resolve(m, cwd)
        if not path.is_file():
            if not path.exists() and ("/" in m or "." in m.lstrip(".")):  # meant as a file, but there's none
                problems.append(f"@{m}: no such file{_suggest(path)} (not attached)")
            continue  # an e-mail, a handle or a folder: leave it as text
        try:
            body = tools.read_file(str(path), cwd, max(1000, budget), roots=readable)
        except tools.ToolError as exc:
            problems.append(f"@{m}: {exc}")
            continue
        if budget <= 1000:
            problems.append(f"@{m}: not attached, the message is already long; ask about it separately")
            continue
        extra.append(f"\n\n[Attached file @{m}]\n{body}")
        names.append(m)
        budget -= len(body)
    return "".join(extra), names, problems
