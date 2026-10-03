"""Everything padou draws on screen, built with rich."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich.theme import Theme as RichTheme

from padou import __version__
from padou.theme import Theme

console = Console(highlight=False)


def configure(theme: Theme) -> None:
    """Swap rich's default styles (gray backgrounds) for the padou theme."""
    console.push_theme(
        RichTheme(
            {
                "markdown.code": f"bold {theme.accent}",  # `inline code`: no background
                "markdown.code_block": "",
                "markdown.h1": f"bold {theme.accent}",
                "markdown.h2": f"bold {theme.accent}",
                "markdown.h3": "bold",
                "markdown.link": f"underline {theme.accent}",
                "markdown.item.bullet": theme.accent,
                "markdown.block_quote": theme.dim,
                "markdown.hr": theme.dim,
                "table.header": "bold",
            }
        )
    )


def md(text: str) -> Markdown:
    # "ansi_dark" uses the terminal's own colours, so code blocks get no gray box
    return Markdown(text, code_theme="ansi_dark", inline_code_theme="ansi_dark")


def short_path(p: Path | str) -> str:
    p = Path(p)
    try:
        return "~/" + str(p.relative_to(Path.home())) if p != Path.home() else "~"
    except ValueError:
        return str(p)


# --------------------------------------------------------------------- banner


def banner(theme: Theme, model_label: str, cwd: Path, cursor: bool = True, compact: bool = False, steady_blink: bool = False) -> Text:
    info1 = Text.assemble((f"padou v{__version__}", "bold"), (" · παντού", theme.dim))
    info2 = Text(f"{model_label} · local · read-only", style=theme.dim)
    info3 = Text(short_path(cwd), style=theme.dim)
    if compact:
        return Text.assemble(("❯_ ", f"bold {theme.accent}"), info1, ("  ", ""), info2)

    frame = theme.accent
    blink = "▌" if cursor else " "
    # steady_blink: let the terminal itself blink the cursor forever (SGR 5).
    # Most terminals honour it; some (e.g. Alacritty) show a solid block instead.
    cursor_style = f"{theme.accent} blink" if steady_blink else theme.accent
    rows = [
        (Text("  ┌───────┐", style=frame), info1),
        (Text.assemble(("  │ ", frame), (">_", "bold"), ("  ", ""), (blink, cursor_style), (" │", frame)), info2),
        (Text("  └──┬─┬──┘", style=frame), info3),
        (Text("   ──┴─┴──", style=frame), Text("")),
    ]
    out = Text()
    for i, (art, info) in enumerate(rows):
        out.append_text(art)
        out.append("  ")
        out.append_text(info)
        if i < len(rows) - 1:
            out.append("\n")
    return out


def show_banner(theme: Theme, model_label: str, cwd: Path, compact: bool, check) -> tuple[bool, str]:
    """Draw the banner; the cursor blinks while `check()` (the health check) runs."""
    result: list = []
    worker = threading.Thread(target=lambda: result.append(check()), daemon=True)
    worker.start()
    if compact or not console.is_terminal:
        worker.join()
        console.print(banner(theme, model_label, cwd, compact=compact, steady_blink=True))
    else:
        with Live(banner(theme, model_label, cwd), console=console, refresh_per_second=10, transient=False) as live:
            on = True
            started = time.monotonic()
            while worker.is_alive() or time.monotonic() - started < 0.6:
                time.sleep(0.35)
                on = not on
                live.update(banner(theme, model_label, cwd, cursor=on))
            live.update(banner(theme, model_label, cwd, cursor=True, steady_blink=True))
    console.print()
    return result[0] if result else (False, "health check failed")


# --------------------------------------------------------------- streaming


class ReplyView:
    """Spinner until the first token, then live-rendered Markdown."""

    def __init__(self, theme: Theme, label: str = "thinking…"):
        self.theme = theme
        self.text = ""
        self._status: Status | None = Status(Text(label, style=theme.dim), console=console, spinner="dots", spinner_style=theme.accent)
        self._live: Live | None = None
        self._last = 0.0

    def __enter__(self):
        if self._status:
            self._status.start()
        return self

    def feed(self, chunk: str) -> None:
        self.text += chunk
        if self._status is not None:
            self._status.stop()
            self._status = None
            self._live = Live(md(self.text), console=console, refresh_per_second=12, vertical_overflow="visible")
            self._live.start()
        now = time.monotonic()
        if self._live and now - self._last > 0.06:
            self._live.update(md(self.text))
            self._last = now

    def __exit__(self, *exc):
        if self._status is not None:
            self._status.stop()
        if self._live is not None:
            self._live.update(md(self.text))
            self._live.stop()
        return False


# ------------------------------------------------------------------- tools


def tool_call(theme: Theme, title: str, summary: str, ok: bool, output: str = "", show_output: bool = False) -> None:
    dot_style = theme.accent if ok else theme.error
    console.print(Text.assemble(("● ", dot_style), (title, "bold")))
    console.print(Text.assemble(("  ⎿ ", theme.dim), (summary, theme.dim if ok else theme.error)))
    if show_output and output:
        lines = output.splitlines()
        preview = "\n".join(lines[:40]) + (f"\n… {len(lines) - 40} more lines" if len(lines) > 40 else "")
        console.print(Panel(Text(preview, style=theme.dim), border_style=theme.dim, padding=(0, 1)))


# ---------------------------------------------------------------- messages


def info(theme: Theme, msg: str) -> None:
    console.print(Text(msg, style=theme.dim))


def error(theme: Theme, msg: str) -> None:
    console.print(Text.assemble(("✗ ", f"bold {theme.error}"), (msg, theme.error)))


def success(theme: Theme, msg: str) -> None:
    console.print(Text.assemble(("✓ ", f"bold {theme.ok}"), (msg, "")))


def rule(theme: Theme) -> None:
    console.rule(style=theme.dim)


def table(theme: Theme, title: str, columns: list[str], rows: list[list], styles: list[str] | None = None) -> None:
    t = Table(title=title, title_style=f"bold {theme.accent}", border_style=theme.dim, header_style="bold", show_lines=False)
    for i, col in enumerate(columns):
        t.add_column(col, style=(styles[i] if styles else None))
    for row in rows:
        t.add_row(*[r if isinstance(r, Text) else str(r) for r in row])
    console.print(t)


def markdown(text: str) -> None:
    console.print(md(text))


def clear() -> None:
    if console.is_terminal:
        console.clear()


def group(*items) -> Group:
    return Group(*items)


def terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80
