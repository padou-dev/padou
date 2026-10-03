"""Saving conversations to ~/.local/share/padou/sessions/ and loading them back."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from padou import paths


@dataclass
class Session:
    id: str
    created: str
    messages: list[dict] = field(default_factory=list)
    model: str = ""
    cwd: str = ""

    @property
    def path(self) -> Path:
        return paths.SESSIONS_DIR / f"{self.id}.json"

    @property
    def title(self) -> str:
        for m in self.messages:
            if m.get("role") == "user" and m.get("content"):
                text = " ".join(str(m["content"]).split())
                return text[:60] + ("…" if len(text) > 60 else "")
        return "(empty)"

    @property
    def turns(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    def save(self) -> None:
        if self.turns == 0:
            return  # don't litter the folder with empty sessions
        paths.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "id": self.id,
            "created": self.created,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "model": self.model,
            "cwd": self.cwd,
            "messages": self.messages,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic: a crash never leaves half a file


def new(model: str, cwd: str) -> Session:
    now = datetime.now()
    return Session(id=now.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2), created=now.isoformat(timespec="seconds"), model=model, cwd=cwd)


def load(path: Path) -> Session:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Session(
        id=data["id"],
        created=data.get("created", ""),
        messages=data.get("messages", []),
        model=data.get("model", ""),
        cwd=data.get("cwd", ""),
    )


def recent(limit: int = 10) -> list[Session]:
    if not paths.SESSIONS_DIR.is_dir():
        return []
    files = sorted(paths.SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        try:
            out.append(load(f))
        except (OSError, ValueError, KeyError):
            continue
    return out
