"""Terminal rendering.

Colour, box-drawing and width handling all go through rich, which enables VT
mode on Windows consoles, falls back to ASCII boxes when the code page cannot
render Unicode, and honours NO_COLOR and non-tty output on every platform.
"""

from __future__ import annotations

import difflib
import re

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .config import Mode
from .engine import DiffResult, Hunk, Op, Status

THEME = Theme(
    {
        "diff.add": "green",
        "diff.add.emph": "bold black on green",
        "diff.remove": "red",
        "diff.remove.emph": "bold black on red",
        "diff.context": "dim",
        "diff.gutter": "dim cyan",
        "diff.hunk": "cyan",
        "status.clean": "bold green",
        "status.drift": "bold yellow",
        "meta.key": "dim",
        "meta.value": "bold",
    }
)

_WORD = re.compile(r"\s+|\w+|[^\w\s]")


def make_console(
    force_terminal: bool | None = None,
    no_color: bool = False,
    stderr: bool = False,
) -> Console:
    return Console(
        theme=THEME, force_terminal=force_terminal, no_color=no_color,
        soft_wrap=False, stderr=stderr,
    )


def _tokens(line: str) -> list[str]:
    return _WORD.findall(line)


def _inline(old: str, new: str, base: str, emph: str, which: str) -> Text:
    """Render one line with the words that actually changed highlighted."""
    a, b = _tokens(old), _tokens(new)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    text = Text()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if which == "left":
            chunk = "".join(a[i1:i2])
            style = base if tag == "equal" else emph
        else:
            chunk = "".join(b[j1:j2])
            style = base if tag == "equal" else emph
        if chunk:
            text.append(chunk, style=style)
    return text


def _pair_runs(hunk: Hunk) -> dict[int, int]:
    """Map index of a REMOVE line to the index of its matching ADD line.

    Only pairs runs of equal length, where line N of the removals plainly
    corresponds to line N of the additions. Anything else is left unpaired
    rather than guessed at.
    """
    pairs: dict[int, int] = {}
    i = 0
    lines = hunk.lines
    while i < len(lines):
        if lines[i].op is not Op.REMOVE:
            i += 1
            continue
        r_start = i
        while i < len(lines) and lines[i].op is Op.REMOVE:
            i += 1
        a_start = i
        while i < len(lines) and lines[i].op is Op.ADD:
            i += 1
        removes, adds = a_start - r_start, i - a_start
        if removes and removes == adds:
            for k in range(removes):
                pairs[r_start + k] = a_start + k
    return pairs


def _gutter(left_no: int | None, right_no: int | None, width: int) -> Text:
    left = str(left_no) if left_no else ""
    right = str(right_no) if right_no else ""
    return Text(f"{left:>{width}} {right:>{width}} ", style="diff.gutter")


def render_summary(result: DiffResult, console: Console) -> None:
    clean = result.status is Status.CLEAN
    badge = Text("CLEAN", style="status.clean") if clean else Text("DRIFT", style="status.drift")

    body = Text()
    body.append("left   ", style="meta.key")
    body.append(f"{result.left.path}\n", style="meta.value")
    body.append("right  ", style="meta.key")
    body.append(f"{result.right.path}\n\n", style="meta.value")

    body.append("status ", style="meta.key")
    body.append_text(badge)
    body.append("   ")
    body.append(f"+{result.added}", style="diff.add")
    body.append(" / ")
    body.append(f"-{result.removed}", style="diff.remove")
    body.append(f"   {result.similarity:.1%} similar", style="meta.key")

    if result.mode is Mode.SHAPE:
        body.append("\n")
        body.append("shape mode: values masked, structure compared", style="meta.key")

    if result.meta_changes:
        body.append("\n\n")
        body.append("response metadata differs\n", style="status.drift")
        width = max(len(c.key) for c in result.meta_changes)
        for change in result.meta_changes:
            body.append(f"  {change.key:<{width}}  ", style="meta.key")
            body.append(change.left or "(absent)", style="diff.remove")
            body.append("  ->  ")
            body.append(change.right or "(absent)", style="diff.add")
            body.append("\n")

    extras = []
    if result.ignored:
        extras.append(f"{result.ignored} ignored by profile")
    if result.noise_suppressed:
        extras.append(f"{result.noise_suppressed} suppressed as noise")
    if extras:
        body.append("\n")
        body.append(" and ".join(extras), style="meta.key")

    console.print(Panel(body, title="sitediff", border_style="cyan", expand=False))


def render_unified(result: DiffResult, console: Console) -> None:
    if not result.hunks and result.meta_changes:
        console.print("\n  Content matches; only response metadata differs.\n",
                      style="status.drift")
        return
    if result.status is Status.CLEAN:
        console.print("\n  No differences in visible content.\n", style="status.clean")
        return

    width = max(3, len(str(max(len(result.left.lines), len(result.right.lines)))))

    for hunk in result.hunks:
        console.print()
        console.print(Text(f"  {hunk.header}", style="diff.hunk"))
        pairs = _pair_runs(hunk)
        partners = {v: k for k, v in pairs.items()}

        for idx, line in enumerate(hunk.lines):
            row = Text("  ")
            row.append_text(_gutter(line.left_no, line.right_no, width))

            if line.op is Op.EQUAL:
                row.append("  ", style="diff.context")
                row.append(line.text, style="diff.context")
            elif line.op is Op.REMOVE:
                row.append("- ", style="diff.remove")
                if idx in pairs:
                    row.append_text(
                        _inline(line.text, hunk.lines[pairs[idx]].text,
                                "diff.remove", "diff.remove.emph", "left")
                    )
                else:
                    row.append(line.text, style="diff.remove")
            else:
                row.append("+ ", style="diff.add")
                if idx in partners:
                    row.append_text(
                        _inline(hunk.lines[partners[idx]].text, line.text,
                                "diff.add", "diff.add.emph", "right")
                    )
                else:
                    row.append(line.text, style="diff.add")

            console.print(row)
    console.print()


def render_side_by_side(result: DiffResult, console: Console) -> None:
    if result.status is Status.CLEAN:
        console.print("\n  No differences in visible content.\n", style="status.clean")
        return

    for hunk in result.hunks:
        table = Table(
            title=hunk.header,
            title_style="diff.hunk",
            title_justify="left",
            show_lines=False,
            expand=True,
            padding=(0, 1),
        )
        table.add_column("", style="diff.gutter", width=5, justify="right")
        table.add_column("left", overflow="fold", ratio=1)
        table.add_column("", style="diff.gutter", width=5, justify="right")
        table.add_column("right", overflow="fold", ratio=1)

        pairs = _pair_runs(hunk)
        consumed: set[int] = set()

        for idx, line in enumerate(hunk.lines):
            if idx in consumed:
                continue
            if line.op is Op.EQUAL:
                table.add_row(
                    str(line.left_no), Text(line.text, style="diff.context"),
                    str(line.right_no), Text(line.text, style="diff.context"),
                )
            elif line.op is Op.REMOVE and idx in pairs:
                other = hunk.lines[pairs[idx]]
                consumed.add(pairs[idx])
                table.add_row(
                    str(line.left_no),
                    _inline(line.text, other.text, "diff.remove", "diff.remove.emph", "left"),
                    str(other.right_no),
                    _inline(line.text, other.text, "diff.add", "diff.add.emph", "right"),
                )
            elif line.op is Op.REMOVE:
                table.add_row(str(line.left_no), Text(line.text, style="diff.remove"), "", "")
            else:
                table.add_row("", "", str(line.right_no), Text(line.text, style="diff.add"))

        console.print()
        console.print(table)
    console.print()
