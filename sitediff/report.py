"""Standalone HTML report.

Self-contained: no external stylesheet, script or font, so it can be attached
to a CI run and opened from anywhere. Handles both comparison kinds, because a
structured result and a text result answer the same question differently.
"""

from __future__ import annotations

import html
from pathlib import Path

from .config import Profile
from .engine import DiffResult, Op, Status

_CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --muted:#6b7280; --line:#e5e7eb; --panel:#f9fafb;
  --add:#166534; --add-bg:#dcfce7; --del:#991b1b; --del-bg:#fee2e2;
  --alt:#854d0e; --alt-bg:#fef3c7; --accent:#0369a1; }
@media (prefers-color-scheme: dark) { :root {
  --bg:#0f1115; --fg:#e6e6e6; --muted:#9aa3af; --line:#262b33; --panel:#161a21;
  --add:#86efac; --add-bg:#052e16; --del:#fca5a5; --del-bg:#450a0a;
  --alt:#fde68a; --alt-bg:#422006; --accent:#7dd3fc; } }
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1.25rem; background:var(--bg); color:var(--fg);
  font:14px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
main { max-width:1100px; margin:0 auto; }
h1 { font-size:1.3rem; margin:0 0 .3rem; }
.sub { color:var(--muted); margin:0 0 1.5rem; font-size:.85rem; }
.badge { display:inline-block; padding:.15rem .55rem; border-radius:999px;
  font-weight:600; font-size:.75rem; letter-spacing:.03em; }
.clean { background:var(--add-bg); color:var(--add); }
.drift { background:var(--alt-bg); color:var(--alt); }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:1rem 1.15rem; margin-bottom:1.5rem; }
.kv { display:grid; grid-template-columns:auto 1fr; gap:.3rem 1.25rem; font-size:.85rem; }
.kv dt { color:var(--muted); }
.kv dd { margin:0; word-break:break-all; }
.scroll { overflow-x:auto; border:1px solid var(--line); border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:.85rem;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
th { text-align:left; padding:.5rem .7rem; border-bottom:1px solid var(--line);
  color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--panel); }
td { padding:.3rem .7rem; vertical-align:top; border-bottom:1px solid var(--line); }
td.num { color:var(--muted); text-align:right; user-select:none; width:1%;
  white-space:nowrap; }
tr.add td { background:var(--add-bg); color:var(--add); }
tr.remove td { background:var(--del-bg); color:var(--del); }
tr.alter td { background:var(--alt-bg); color:var(--alt); }
tr.context td { color:var(--muted); }
.hunk td { background:transparent; color:var(--accent); font-weight:600;
  padding-top:.9rem; }
.empty { color:var(--muted); font-style:italic; }
footer { color:var(--muted); font-size:.75rem; margin-top:2rem; }
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _meta_panel(result: DiffResult) -> str:
    rows = [
        ("left", result.left.path),
        ("right", result.right.path),
        ("mode", result.mode.value),
        ("similarity", f"{result.similarity:.1%}"),
    ]
    if result.ignored:
        rows.append(("ignored by profile", str(result.ignored)))
    if result.noise_suppressed:
        rows.append(("suppressed as noise", str(result.noise_suppressed)))
    for change in result.meta_changes:
        rows.append((f"metadata: {change.key}",
                     f"{change.left or '(absent)'} -> {change.right or '(absent)'}"))
    items = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in rows)
    return f'<div class="panel"><dl class="kv">{items}</dl></div>'


def _fields_table(result: DiffResult) -> str:
    symbols = {Op.ADD: ("+", "add"), Op.REMOVE: ("-", "remove"), Op.ALTER: ("~", "alter")}
    rows = []
    for change in result.field_changes:
        symbol, css = symbols[change.op]
        left = _esc(change.left) if change.left else '<span class="empty">absent</span>'
        right = _esc(change.right) if change.right else '<span class="empty">absent</span>'
        rows.append(
            f'<tr class="{css}"><td class="num">{symbol}</td>'
            f"<td>{_esc(change.path)}</td><td>{left}</td><td>{right}</td></tr>"
        )
    body = "".join(rows) or '<tr><td colspan="4">No field differences.</td></tr>'
    return (
        '<div class="scroll"><table><thead><tr><th></th><th>field</th>'
        f"<th>left</th><th>right</th></tr></thead><tbody>{body}</tbody></table></div>"
    )


def _hunks_table(result: DiffResult) -> str:
    css = {Op.ADD: "add", Op.REMOVE: "remove", Op.EQUAL: "context"}
    marks = {Op.ADD: "+", Op.REMOVE: "-", Op.EQUAL: " "}
    rows = []
    for hunk in result.hunks:
        rows.append(f'<tr class="hunk"><td colspan="4">{_esc(hunk.header)}</td></tr>')
        for line in hunk.lines:
            rows.append(
                f'<tr class="{css[line.op]}">'
                f'<td class="num">{line.left_no or ""}</td>'
                f'<td class="num">{line.right_no or ""}</td>'
                f'<td class="num">{marks[line.op]}</td>'
                f"<td>{_esc(line.text)}</td></tr>"
            )
    body = "".join(rows) or '<tr><td colspan="4">No differences in visible content.</td></tr>'
    return (
        '<div class="scroll"><table><thead><tr><th>left</th><th>right</th>'
        f"<th></th><th>content</th></tr></thead><tbody>{body}</tbody></table></div>"
    )


def build_html_report(result: DiffResult, profile: Profile) -> str:
    clean = result.status is Status.CLEAN
    badge = "CLEAN" if clean else "DRIFT"
    counts = f"+{result.added} / -{result.removed}"
    if result.structured:
        counts += f" / ~{result.altered}"
    table = _fields_table(result) if result.structured else _hunks_table(result)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>sitediff report</title><style>{_CSS}</style></head>
<body><main>
<h1>sitediff <span class="badge {'clean' if clean else 'drift'}">{badge}</span></h1>
<p class="sub">{_esc(counts)} &middot; profile: {_esc(profile.name)}</p>
{_meta_panel(result)}
{table}
<footer>Generated by sitediff. Values shown have passed the profile's
redaction rules.</footer>
</main></body></html>
"""


def write_html_report(result: DiffResult, out_path: str, profile: Profile) -> str:
    path = Path(out_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html_report(result, profile), encoding="utf-8")
    return str(path)


def build_run_report(report, profile: Profile) -> str:
    """Index page for a run across many entities."""
    total = len(report.results)
    clean = report.clean
    badge = "CLEAN" if not (report.drift or report.failed) else "DRIFT"
    css_class = "clean" if badge == "CLEAN" else "drift"

    rows = []
    for result in report.drifted:
        css = "remove" if result.status == "error" else "alter"
        counts = ("" if result.status == "error"
                  else f"+{result.added} -{result.removed} ~{result.altered}")
        rows.append(
            f'<tr class="{css}"><td>{_esc(result.key)}</td>'
            f"<td>{_esc(result.status)}</td><td>{_esc(counts)}</td>"
            f"<td>{_esc(result.detail[:600])}</td></tr>"
        )
    body = "".join(rows) or '<tr><td colspan="4">Every entity matched.</td></tr>'

    stats = [
        ("entities", str(total)),
        ("clean", str(clean)),
        ("drift", str(report.drift)),
        ("failed", str(report.failed)),
        ("elapsed", f"{report.seconds:.1f}s"),
    ]
    if report.accepted:
        stats.append(("suppressed as accepted", str(report.accepted)))
    if report.discovery_failures:
        stats.append(("discovery pages failed", str(len(report.discovery_failures))))
    if report.run_id is not None:
        stats.append(("run", f"#{report.run_id} in {report.db_path}"))
    items = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in stats)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>sitediff run</title><style>{_CSS}</style></head>
<body><main>
<h1>sitediff run <span class="badge {css_class}">{badge}</span></h1>
<p class="sub">profile: {_esc(profile.name)}</p>
<div class="panel"><dl class="kv">{items}</dl></div>
<div class="scroll"><table><thead><tr><th>entity</th><th>status</th>
<th>+/-/~</th><th>detail</th></tr></thead><tbody>{body}</tbody></table></div>
<footer>Generated by sitediff. Values shown have passed the profile's
redaction rules.</footer>
</main></body></html>
"""


def write_run_report(report, out_path: str, profile: Profile) -> str:
    path = Path(out_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_run_report(report, profile), encoding="utf-8")
    return str(path)
