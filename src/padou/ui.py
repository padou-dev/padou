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

console = Console(highlight=False, record=True)  # record=True lets /screenshot save the screen


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


def frame_to_text(frame) -> Text:
    """A brand frame as rich Text."""
    out = Text()
    for i, row in enumerate(frame):
        for text, color, bold in row:
            style = " ".join(x for x in ("bold" if bold else "", color or "") if x)
            out.append(text, style=style or None)
        if i < len(frame) - 1:
            out.append("\n")
    return out


def frame_to_ptk(frame) -> list[tuple[str, str]]:
    """A brand frame as prompt_toolkit formatted text."""
    out: list[tuple[str, str]] = []
    for row in frame:
        for text, color, bold in row:
            style = " ".join(x for x in ("bold" if bold else "", f"fg:{color}" if color else "") if x)
            out.append((style, text))
        out.append(("", "\n"))
    return out


def compact_banner(theme: Theme, model_label: str) -> Text:
    return Text.assemble(
        ("✦ ", f"bold {theme.accent}"), (f"padou v{__version__}", "bold"), (" · παντού", theme.dim),
        ("  ", ""), (f"{model_label} · local · read-only", theme.dim),
    )


class _Keys:
    """Notice a keypress (to skip the launch) without echoing it. POSIX terminals only."""

    def __init__(self):
        self.old = None
        try:
            import termios
            import tty

            if os.isatty(0):
                self.old = termios.tcgetattr(0)
                tty.setcbreak(0)
        except Exception:
            self.old = None

    def pressed(self) -> bool:
        if self.old is None:
            return False
        import select

        if select.select([0], [], [], 0)[0]:
            os.read(0, 1024)  # swallow it, so it doesn't land in the prompt
            return True
        return False

    def close(self) -> None:
        if self.old is not None:
            import termios

            termios.tcsetattr(0, termios.TCSADRAIN, self.old)


def launch(banner, theme: Theme, worker, status=lambda: "") -> object:
    """Play the launch animation while `worker()` runs (health check, autostart...).

    The star keeps pulsing until the worker is done; `status()` returns a line
    shown under the star (e.g. "starting llama-server…"). Any key skips the
    launch part. The drawing is transient: the first prompt redraws the banner
    in the same place and keeps it pulsing.
    """
    from padou import brand

    result: list = []
    errors: list = []

    def run():
        try:
            result.append(worker())
        except BaseException as exc:  # handed back to the main thread
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    keys = _Keys()
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    started = time.monotonic()
    skipped = False
    try:
        with Live(console=console, refresh_per_second=30, transient=True) as live:
            while True:
                t = time.monotonic() - started
                if not skipped and keys.pressed():
                    skipped = True
                if t < brand.LAUNCH_SECONDS and not skipped:
                    frame = banner.launch(t)
                else:
                    frame = banner.pulse((t % brand.PULSE_PERIOD) / brand.PULSE_PERIOD)
                body = frame_to_text(frame)
                line = status()
                if line:
                    body.append("\n\n")
                    body.append(f"  {spinner[int(t * 12) % len(spinner)]} {line}", style=theme.dim)
                live.update(body)
                done = not thread.is_alive()
                if done and (t >= brand.LAUNCH_SECONDS or skipped):
                    break
                time.sleep(1 / 30)
    finally:
        keys.close()
    if errors:
        raise errors[0]
    return result[0] if result else None


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


def show_change(theme: Theme, change, max_lines: int = 80) -> None:
    """A proposed file change as a coloured diff."""
    added, removed = change.stats
    verb = "Edit" if change.kind == "edit" else "Create"
    console.print(Text.assemble(("✎ ", f"bold {theme.warn}"), (f"{verb} {short_path(change.path)}", "bold"),
                                (f"  +{added} −{removed}", theme.dim)))
    if change.reason:
        console.print(Text(f"  {change.reason}", style=theme.dim))
    lines = change.diff()[2:]  # skip the ---/+++ header lines
    for line in lines[:max_lines]:
        if line.startswith("+"):
            style = theme.ok
        elif line.startswith("-"):
            style = theme.error
        elif line.startswith("@@"):
            style = theme.accent
        else:
            style = theme.dim
        console.print(Text("  " + line, style=style), soft_wrap=True)
    if len(lines) > max_lines:
        console.print(Text(f"  … {len(lines) - max_lines} more diff lines", style=theme.dim))


def confirm(question: str, default: bool = False) -> bool:
    from rich.prompt import Confirm

    try:
        return Confirm.ask(question, default=default, console=console)
    except (KeyboardInterrupt, EOFError):
        return False


def markdown(text: str) -> None:
    console.print(md(text))


def clear() -> None:
    if console.is_terminal:
        console.clear()


def save_screenshot(path: Path, title: str = "padou") -> Path:
    """Everything padou has printed this session, as an SVG image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(path), title=title, clear=False)
    return path


def group(*items) -> Group:
    return Group(*items)


def terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80
