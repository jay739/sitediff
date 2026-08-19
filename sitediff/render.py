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
from .engine import DiffResult, Op, Status, pair_runs

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
    if result.structured:
        body.append(" / ")
        body.append(f"~{result.altered}", style="status.drift")
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
        pairs = pair_runs(hunk)
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

        pairs = pair_runs(hunk)
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


def render_probe(report, console: Console) -> None:
    """Present a probe as the answer to 'what do I put in my profile'."""
    from .probe import Strategy

    blurb = {
        Strategy.STATIC: "Values are already in the served HTML. No browser needed.",
        Strategy.HYDRATION: "Structured state is embedded in the HTML. No browser needed.",
        Strategy.JSON: "Values arrive separately. Call the data endpoint directly.",
        Strategy.BROWSER: "Values require JavaScript execution. A browser is needed.",
    }[report.strategy]

    head = Text()
    head.append(f"{report.url}\n\n", style="meta.value")
    head.append("capture   ", style="meta.key")
    head.append(report.strategy.value, style="status.drift"
                if report.strategy.value == "browser" else "status.clean")
    head.append(f"\n          {blurb}\n", style="meta.key")
    for reason in report.reasons:
        head.append(f"          - {reason}\n", style="dim")
    if report.alternatives:
        head.append("\nalso    ", style="meta.key")
        head.append("viable, in preference order\n", style="meta.key")
        for name, why in report.alternatives:
            head.append(f"          {name}", style="status.clean")
            head.append(f"  {why}\n", style="dim")
    console.print(Panel(head, title="probe", border_style="cyan", expand=False))

    def section(title: str, rows: list[str], style: str = "") -> None:
        if not rows:
            return
        console.print(f"\n  [bold]{title}[/]")
        for row in rows:
            console.print(f"    {row}", style=style, highlight=False)

    section("Frameworks detected",
            [f"{name}  [dim]{note}[/]" for name, note in report.frameworks]
            or ["[dim]none recognised[/]"])

    section("Embedded state",
            [f"{name}  [dim]{note}, ~{size:,} bytes"
             + ("" if ok else ", NOT JSON-parseable") + "[/]"
             for name, note, size, ok in report.hydration])

    section("Candidate data endpoints  [dim](what the Network tab would show)[/]",
            report.endpoints[:15])
    if len(report.endpoints) > 15:
        console.print(f"    [dim]... and {len(report.endpoints) - 15} more[/]")

    section("Loading placeholders  [dim](use as render_sentinels)[/]",
            [repr(m) for m in report.loading_markers])

    section("Response", [f"{k:<16} {v}" for k, v in report.meta.items()])

    console.print("\n  [bold]Content[/]")
    console.print(f"    {'visible lines':<16} {report.visible_lines}")
    console.print(f"    {'visible text':<16} {report.visible_chars:,} chars "
                  f"of {report.html_bytes:,} bytes ({report.text_ratio:.1%})")
    console.print(f"    {'external scripts':<16} {len(report.scripts)}")
    console.print()


def render_fields(result: DiffResult, console: Console) -> None:
    """Present a structured diff as the fields that moved."""
    from .engine import Op

    if not result.field_changes:
        console.print("\n  No field differences.\n", style="status.clean")
        return

    table = Table(show_lines=False, expand=True, padding=(0, 1), box=None)
    table.add_column("", width=3)
    table.add_column("field", overflow="fold", ratio=2)
    table.add_column("left", overflow="fold", ratio=2)
    table.add_column("right", overflow="fold", ratio=2)

    symbols = {Op.REMOVE: ("-", "diff.remove"), Op.ADD: ("+", "diff.add"),
               Op.ALTER: ("~", "status.drift")}

    for change in result.field_changes:
        symbol, style = symbols[change.op]
        left = change.left if change.op is not Op.ADD else ""
        right = change.right if change.op is not Op.REMOVE else ""
        table.add_row(
            Text(symbol, style=style),
            Text(change.path, style=style),
            Text(left or ("(absent)" if change.op is Op.ADD else "(empty)"),
                 style="diff.remove" if left else "dim"),
            Text(right or ("(absent)" if change.op is Op.REMOVE else "(empty)"),
                 style="diff.add" if right else "dim"),
        )

    console.print()
    console.print(table)
    console.print()


def render_run(report, console: Console, show: int = 25) -> None:
    """Present a run across many entities: the verdict, then what moved."""
    total = len(report.results)
    body = Text()
    body.append("profile   ", style="meta.key")
    body.append(f"{report.profile}\n", style="meta.value")
    body.append("entities  ", style="meta.key")
    body.append(f"{total}", style="meta.value")
    body.append(f"  in {report.seconds:.1f}s\n\n", style="meta.key")

    body.append("clean  ", style="meta.key")
    body.append(f"{report.clean}", style="status.clean")
    body.append("   drift  ", style="meta.key")
    body.append(f"{report.drift}", style="status.drift" if report.drift else "meta.value")
    body.append("   failed  ", style="meta.key")
    body.append(f"{report.failed}", style="diff.remove" if report.failed else "meta.value")
    if report.accepted:
        body.append(f"\n{report.accepted} finding(s) suppressed as accepted",
                    style="meta.key")
    if report.discovery_failures:
        body.append(f"\n{len(report.discovery_failures)} discovery page(s) failed",
                    style="diff.remove")
    if report.run_id is not None:
        body.append(f"\n\nrun #{report.run_id} stored in {report.db_path}",
                    style="meta.key")

    console.print(Panel(body, title="sitediff run", border_style="cyan", expand=False))

    drifted = report.drifted
    if not drifted:
        console.print("\n  Every entity matched.\n", style="status.clean")
        return

    table = Table(show_lines=False, expand=True, padding=(0, 1), box=None)
    table.add_column("entity", ratio=1, overflow="fold")
    table.add_column("status", width=8)
    table.add_column("+/-/~", width=12)
    table.add_column("detail", ratio=4, overflow="fold")

    for result in drifted[:show]:
        style = "diff.remove" if result.status == "error" else "status.drift"
        counts = ("" if result.status == "error"
                  else f"+{result.added} -{result.removed} ~{result.altered}")
        table.add_row(
            Text(result.key, style=style),
            Text(result.status, style=style),
            Text(counts, style="meta.key"),
            Text(result.detail[:400] or "-", style="dim"),
        )

    console.print()
    console.print(table)
    if len(drifted) > show:
        console.print(f"\n  ... and {len(drifted) - show} more. "
                      f"Full results in {report.db_path}", style="dim")
    console.print()
