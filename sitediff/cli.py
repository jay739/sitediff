"""Command line entry point.

Exit codes follow the diff convention so this composes in CI:
    0  the two sides agree
    1  drift was found
    2  the run failed
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from rich.text import Text

from . import __version__
from .accept import AcceptStore, apply_accepted, fingerprints_of
from .capture import Capture, Kind, from_html, from_response
from .config import Profile, discover_profiles, load_profile, profile_search_path
from .engine import Status, compare, compare_structured, noise_baseline
from .errors import InputError, MixedPayloadError, PairingError, SiteDiffError
from .extract import Page, extract_html, read_html
from .render import (
    make_console,
    render_fields,
    render_probe,
    render_run,
    render_side_by_side,
    render_structure,
    render_summary,
    render_unified,
)
from .source import EgressPolicy, Source, fetch

SOURCE_REF = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+):(?P<path>/.*)?$")

# Short aliases per subcommand, unambiguous with one another. Registered in the
# handler map so dispatch never cares which spelling was typed.
COMMAND_ALIASES = {
    "compare": ["c", "diff"], "run": ["r"], "runs": ["rs"], "accept": ["a"],
    "probe": ["p"], "har": ["h"], "fetch": ["f"], "extract": ["x"],
    "profiles": ["pf"], "sources": ["s"], "help": ["?"],
}

EXIT_CLEAN, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sitediff",
        description="Compare the visible content of two renderings of the same page.",
    )
    parser.add_argument("--version", action="version", version=f"sitediff {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cmp_p = sub.add_parser(
        "compare",
        aliases=["c", "diff"],
        description="diff two pages, from files, URLs or configured sources",
        help="diff two saved HTML pages",
    )
    cmp_p.add_argument("left", help="saved HTML file, URL, or source:/path (reference side)")
    cmp_p.add_argument("right", help="saved HTML file, URL, or source:/path (candidate side)")
    cmp_p.add_argument("-p", "--profile", help="profile name or path to a .toml")
    cmp_p.add_argument("-s", "--side-by-side", action="store_true", help="two-column view")
    cmp_p.add_argument("-o", "--html", metavar="PATH",
                       help="also write a standalone HTML report")
    cmp_p.add_argument(
        "-b", "--baseline", nargs=2, metavar=("CAPTURE_A", "CAPTURE_B"),
        help="two captures of the SAME side; anything differing between them is "
             "treated as inherent noise and suppressed from the main diff",
    )
    cmp_p.add_argument("-C", "--context", type=int, metavar="N",
                       help="context lines around each change")
    cmp_p.add_argument("--allow-unrendered", action="store_true",
                       help="compare even if a page still shows a loading placeholder")
    cmp_p.add_argument("--no-color", action="store_true", help="disable colour output")
    cmp_p.add_argument("-q", "--quiet", action="store_true", help="summary only, no diff body")
    cmp_p.add_argument("-c", "--capture", choices=[c.value for c in Capture], default="",
                       help="override the capture strategy for both sides")
    cmp_p.add_argument("--no-structure", action="store_true",
                       help="skip the element comparison, compare text only")
    cmp_p.add_argument("-A", "--accept-file", metavar="PATH",
                       help="accepted-difference store (default .sitediff/accepted.toml)")

    ex_p = sub.add_parser(
        "extract",
        aliases=["x"],
        description="show the visible lines pulled from one page",
        help="show the visible lines pulled from one page",
    )
    ex_p.add_argument("page", help="saved HTML file, URL, or source:/path")
    ex_p.add_argument("-p", "--profile", help="profile name or path to a .toml")
    ex_p.add_argument("-n", "--numbers", action="store_true", help="prefix line numbers")
    ex_p.add_argument("--allow-unrendered", action="store_true",
                      help="extract even if the page shows a loading placeholder")

    f_p = sub.add_parser(
        "fetch",
        aliases=["f"],
        description="fetch one page and report how it was served",
        help="fetch one page and report how it was served",
    )
    f_p.add_argument("target", help="URL or source:/path")
    f_p.add_argument("-p", "--profile", help="profile name or path to a .toml")
    f_p.add_argument("-b", "--body", action="store_true", help="also print extracted lines")

    pr_p = sub.add_parser(
        "probe",
        aliases=["p"],
        description="work out how a page delivers its values, and what it is built with",
        help="work out how a page delivers its values, and what it is built with",
    )
    pr_p.add_argument("target", help="URL, source:/path, or a saved HTML file")
    pr_p.add_argument("-p", "--profile", help="profile name or path to a .toml")
    pr_p.add_argument("-s", "--suggest", action="store_true",
                      help="print a profile fragment you can paste and edit")

    run_p = sub.add_parser(
        "run",
        aliases=["r"],
        description="compare every paired entity on every page type, and aggregate",
        help="compare every paired entity and aggregate the outcome",
    )
    run_p.add_argument("-p", "--profile", required=True, help="profile name or path to a .toml")
    run_p.add_argument("-l", "--limit", type=int, default=0, metavar="N",
                       help="stop after N entities, for a smoke test")
    run_p.add_argument("-d", "--db", metavar="PATH",
                       help="results database (default .sitediff/runs.db)")
    run_p.add_argument("-A", "--accept-file", metavar="PATH",
                       help="accepted-difference store (default .sitediff/accepted.toml)")
    run_p.add_argument("-o", "--html", metavar="PATH",
                       help="write a run report")
    run_p.add_argument("-w", "--show", type=int, default=25, help="rows of detail to print")
    run_p.add_argument("--no-color", action="store_true", help="disable colour output")

    acc_p = sub.add_parser(
        "accept",
        aliases=["a"],
        description="record the current differences as intentional",
        help="record the current differences as intentional",
    )
    acc_p.add_argument("left", help="reference side")
    acc_p.add_argument("right", help="candidate side")
    acc_p.add_argument("-p", "--profile", help="profile name or path to a .toml")
    acc_p.add_argument("-c", "--capture", choices=[c.value for c in Capture], default="",
                       help="override the capture strategy for both sides")
    acc_p.add_argument("-A", "--accept-file", metavar="PATH",
                       help="accepted-difference store (default .sitediff/accepted.toml)")
    acc_p.add_argument("-r", "--reason", default="", help="why this difference is expected")
    acc_p.add_argument("-n", "--dry-run", action="store_true",
                       help="show what would be accepted, write nothing")

    runs_p = sub.add_parser(
        "runs",
        aliases=["rs"],
        description="list recent runs",
        help="list recent runs",
    )
    runs_p.add_argument("-d", "--db", metavar="PATH",
                        help="results database (default .sitediff/runs.db)")

    har_p = sub.add_parser(
        "har",
        aliases=["h"],
        description="read a browser HAR export and find the site's data endpoints",
        help="read a browser HAR export and find the site's data endpoints",
    )
    har_p.add_argument("file", help="path to a .har exported from devtools")
    har_p.add_argument("-a", "--all", action="store_true",
                       help="list every endpoint, not only those carrying data")
    har_p.add_argument("-s", "--suggest", action="store_true",
                       help="print a profile fragment wiring up the data endpoints")
    har_p.add_argument("-P", "--placeholder", action="append", default=[], metavar="VALUE=NAME",
                       help="replace a captured parameter value with {NAME} in the "
                            "suggested body, e.g. --placeholder 1234=key. Repeatable")

    sub.add_parser(
        "help",
        aliases=["?"],
        description="show every command and every option at once",
        help="show every command and every option at once",
    )
    sub.add_parser(
        "profiles",
        aliases=["pf"],
        description="list profiles and the directories searched for them",
        help="list the built-in profiles",
    )
    src_p = sub.add_parser(
        "sources",
        aliases=["s"],
        description="list a profile's sources and its egress allowlist",
        help="list sources and the egress allowlist",
    )
    src_p.add_argument("-p", "--profile", help="profile name or path to a .toml")
    return parser


def resolve_payload(token: str, profile: Profile, side: str,
                    override: str = "") -> object:
    """Turn one command line argument into a capture payload.

    Accepted forms, in order: an explicit URL, a `sourcename:/path` reference
    into the profile, or a path to a saved file.
    """
    headers = profile.compare_headers

    if token.startswith(("http://", "https://")):
        capture = Capture(override or Capture.STATIC.value)
        response = fetch_url(token, profile)
        return from_response(response, capture, token, headers=headers)

    match = SOURCE_REF.match(token)
    if match and match.group("name") in profile.sources:
        source = profile.source(match.group("name"))
        url = source.url_for(match.group("path") or "")
        capture = Capture(override or source.capture)
        response = fetch(url, source, profile.egress_policy())
        return from_response(response, capture, url,
                             marker=source.hydration_marker, headers=headers)

    capture = Capture(override or Capture.STATIC.value)
    return from_html(read_html(token), capture, token)


def fetch_url(url: str, profile: Profile):
    """Fetch a URL typed directly on the command line.

    Typing a URL is itself the authorisation for that one host, so it is added
    to the policy for this run. The allowlist still governs everything else,
    including every redirect hop, so an ad-hoc URL cannot bounce the tool
    somewhere it was never pointed at.
    """
    host = urlparse(url).hostname or ""
    policy = EgressPolicy([*profile.egress_policy().allowed, host])
    return fetch(url, Source(name="cli", base_url=url), policy)


def attach_structure(result, left_payload, right_payload, profile: Profile,
                     skip: bool = False) -> None:
    """Compare the elements as well as the words, when both sides gave markup.

    Text extraction discards the DOM, so a control that vanished or a table
    that lost a column never appears in a content diff. Both `compare` and
    `accept` need this, or accepting would record only half the findings and
    a run would never come back clean.
    """
    if skip or left_payload.kind is not Kind.TEXT or right_payload.kind is not Kind.TEXT:
        return
    if not profile.structure.enabled:
        return
    from . import structure as structure_mod

    result.structure_report = structure_mod.compare(
        structure_mod.build(left_payload.html, profile.structure,
                            profile.for_side("left").parser),
        structure_mod.build(right_payload.html, profile.structure,
                            profile.for_side("right").parser),
        Path(left_payload.label).name or "left",
        Path(right_payload.label).name or "right",
    )


def _as_page(payload, profile: Profile, side: str, allow_unrendered: bool) -> Page:
    if payload.kind is Kind.TEXT:
        return extract_html(payload.html, profile.for_side(side), side=side,
                            label=payload.label, allow_unrendered=allow_unrendered,
                            meta=payload.meta)
    # Structured payloads carry no lines; the Page exists to hold identity and
    # response metadata so both comparison paths report them the same way.
    return Page(path=payload.label, side=side, lines=[], meta=payload.meta)


def cmd_compare(args) -> int:
    profile = load_profile(args.profile).with_overrides(context_lines=args.context)
    console = make_console(no_color=args.no_color)

    left_payload = resolve_payload(args.left, profile, "left", args.capture)
    right_payload = resolve_payload(args.right, profile, "right", args.capture)
    if left_payload.kind is not right_payload.kind:
        raise MixedPayloadError(left_payload.kind.value, right_payload.kind.value)

    for payload in (left_payload, right_payload):
        for note in payload.notes:
            console.print(Text(f"  note: {note}", style="yellow"))

    left = _as_page(left_payload, profile, "left", args.allow_unrendered)
    right = _as_page(right_payload, profile, "right", args.allow_unrendered)

    noise = None
    if args.baseline:
        a = _as_page(resolve_payload(args.baseline[0], profile, "baseline A", args.capture),
                     profile, "baseline A", args.allow_unrendered)
        b = _as_page(resolve_payload(args.baseline[1], profile, "baseline B", args.capture),
                     profile, "baseline B", args.allow_unrendered)
        noise = noise_baseline(a, b)
        if noise:
            console.print(
                f"  Baseline found {len(noise)} unstable line(s); the capture is "
                f"non-deterministic and those lines are excluded.",
                style="yellow",
            )
        else:
            console.print("  Baseline is stable: the capture is reproducible.", style="green")

    if left_payload.kind is Kind.DATA:
        result = compare_structured(left, right, profile,
                                    left_payload.data, right_payload.data)
    else:
        result = compare(left, right, profile, noise=noise)

    attach_structure(result, left_payload, right_payload, profile,
                     skip=args.no_structure)

    store = AcceptStore.load(args.accept_file)
    if apply_accepted(result, store):
        console.print(
            f"  {result.accepted} finding(s) suppressed as accepted "
            f"({store.path})", style="dim")

    render_summary(result, console)
    if result.structure_report and not args.quiet:
        render_structure(result.structure_report, console)
    if not args.quiet:
        if left_payload.kind is Kind.DATA:
            render_fields(result, console)
        elif args.side_by_side:
            render_side_by_side(result, console)
        else:
            render_unified(result, console)

    if args.html:
        from .report import write_html_report
        path = write_html_report(result, args.html, profile)
        console.print(f"  HTML report written to {path}", style="cyan")

    return EXIT_CLEAN if result.status is Status.CLEAN else EXIT_DRIFT


def cmd_extract(args) -> int:
    profile = load_profile(args.profile)
    console = make_console()
    page = _as_page(resolve_payload(args.page, profile, "page"), profile, "page",
                    args.allow_unrendered)
    width = len(str(len(page.lines)))
    for i, line in enumerate(page.lines, 1):
        console.print(f"[dim cyan]{i:>{width}}[/] {line}" if args.numbers else line,
                      highlight=False)
    return EXIT_CLEAN


def cmd_fetch(args) -> int:
    profile = load_profile(args.profile)
    console = make_console()
    payload = resolve_payload(args.target, profile, "page")
    page = _as_page(payload, profile, "page", allow_unrendered=True)

    console.print()
    console.print(f"  [bold]{page.path}[/]")
    if page.meta:
        width = max(len(k) for k in page.meta)
        for key, value in page.meta.items():
            console.print(f"  [dim]{key:<{width}}[/]  {value}", highlight=False)
    console.print(f"  [dim]{'lines':<{max(5, len(max(page.meta, key=len, default='')))}}[/]"
                  f"  {len(page.lines)}", highlight=False)
    if args.body:
        console.print()
        for line in page.lines:
            console.print(f"  {line}", highlight=False)
    console.print()
    return EXIT_CLEAN


def cmd_run(args) -> int:
    from .run import execute

    profile = load_profile(args.profile)
    console = make_console(no_color=args.no_color)
    plan = profile.pair
    if not plan.configured:
        raise PairingError(
            f"Profile {profile.name!r} defines no [pair] section, so there is "
            f"nothing to run across.\n"
            f"Add [pair] with left, right, left_path and right_path, plus either "
            f"'keys' or a [pair.discover] block. Use 'sitediff compare' for a "
            f"single pair."
        )

    store = AcceptStore.load(args.accept_file)
    report = execute(profile, plan, store, limit=args.limit, db_path=args.db)
    render_run(report, console, show=args.show)

    if args.html:
        from .report import write_run_report
        path = write_run_report(report, args.html, profile)
        console.print(f"  Run report written to {path}", style="cyan")

    return EXIT_DRIFT if (report.drift or report.failed) else EXIT_CLEAN


def cmd_accept(args) -> int:
    profile = load_profile(args.profile).with_overrides()
    console = make_console()

    left_payload = resolve_payload(args.left, profile, "left", args.capture)
    right_payload = resolve_payload(args.right, profile, "right", args.capture)
    if left_payload.kind is not right_payload.kind:
        raise MixedPayloadError(left_payload.kind.value, right_payload.kind.value)

    left = _as_page(left_payload, profile, "left", True)
    right = _as_page(right_payload, profile, "right", True)

    if left_payload.kind is Kind.DATA:
        result = compare_structured(left, right, profile,
                                    left_payload.data, right_payload.data)
    else:
        result = compare(left, right, profile)

    attach_structure(result, left_payload, right_payload, profile)

    store = AcceptStore.load(args.accept_file)
    findings = fingerprints_of(result)
    new = {fp: desc for fp, desc in findings.items() if fp not in store}

    if not new:
        console.print("\n  Nothing new to accept; every difference is already "
                      "recorded.\n", style="status.clean")
        return EXIT_CLEAN

    console.print(f"\n  {len(new)} difference(s) to accept:\n", style="bold")
    for fp, description in new.items():
        console.print(Text(f"    {fp}  {description[:110]}", style="dim"))

    if args.dry_run:
        console.print("\n  Dry run, nothing written.\n", style="yellow")
        return EXIT_CLEAN

    for fp, description in new.items():
        store.add(fp, description, args.reason)
    path = store.save()
    console.print(f"\n  Written to {path}\n"
                  f"  Commit it: accepting a difference should be a reviewed "
                  f"change with an author.\n", style="cyan")
    return EXIT_CLEAN


def cmd_runs(args) -> int:
    from .run import recent_runs

    console = make_console()
    rows = recent_runs(args.db)
    if not rows:
        console.print("\n  No runs recorded yet.\n", style="yellow")
        return EXIT_CLEAN

    console.print("\n  Recent runs:\n", style="bold")
    console.print(f"  {'#':>4}  {'when (UTC)':<19} {'profile':<20} "
                  f"{'entities':>8} {'clean':>6} {'drift':>6} {'failed':>7}", style="dim")
    for row in rows:
        # Trim the timezone offset: every timestamp is stored in UTC, and the
        # column header says so, so repeating it on each row only misaligns.
        when = row["started_at"][:19].replace("T", " ")
        console.print(
            f"  {row['id']:>4}  {when:<19} {row['profile'][:20]:<20} "
            f"{row['entities']:>8} {row['clean']:>6} {row['drift']:>6} {row['failed']:>7}"
        )
    console.print()
    return EXIT_CLEAN


def cmd_har(args) -> int:
    from .har import load, suggest_profile

    console = make_console()
    report = load(args.file)

    console.print(f"\n  [bold]{report.path}[/]")
    console.print(f"  {len(report.endpoints)} endpoint(s) across "
                  f"{len(report.hosts)} host(s)", style="dim")
    if report.skipped_third_party or report.skipped_assets:
        console.print(f"  skipped {report.skipped_third_party} third-party and "
                      f"{report.skipped_assets} asset request(s)", style="dim")
    if report.missing_bodies:
        console.print(f"  [yellow]{report.missing_bodies} response(s) had no body; "
                      f"re-export with 'Save all as HAR with content'[/]")

    shown = report.endpoints if args.all else report.data_endpoints
    if not shown:
        console.print("\n  No response carried enough numbers to look like data.",
                      style="yellow")
        console.print("  Re-export with Preserve log on, after clicking through "
                      "every view.\n", style="dim")
        return EXIT_CLEAN

    heading = "Every endpoint" if args.all else "Endpoints carrying data"
    console.print(f"\n  [bold]{heading}[/]\n")
    for endpoint in shown:
        marker = "[green]data[/]" if endpoint.carries_data else "[dim]    [/]"
        console.print(f"  {marker} [bold]{endpoint.method}[/] {endpoint.path}")
        console.print(f"         {endpoint.response_bytes:>9,}b  "
                      f"{endpoint.decimals:>4} numbers  {endpoint.response_mime}"
                      f"  x{endpoint.calls}", style="dim")
        if endpoint.body:
            keys = ", ".join(list(endpoint.params)[:8])
            console.print(f"         payload ({endpoint.mime.split(';')[0]}): {keys}",
                          style="dim")
    console.print()

    if args.suggest:
        placeholders = {}
        for item in args.placeholder:
            if "=" not in item:
                raise InputError(
                    f"--placeholder expects VALUE=NAME, got {item!r}. "
                    f"For example: --placeholder 1234=key")
            value, _, name = item.partition("=")
            placeholders[value] = name
        console.print("  [bold]Suggested profile fragment[/]\n")
        for line in suggest_profile(report, placeholders).splitlines():
            console.print(Text(f"    {line}", style="dim"))
        console.print()
    return EXIT_CLEAN


def cmd_probe(args) -> int:
    from .probe import probe, suggested_profile

    profile = load_profile(args.profile)
    console = make_console()

    response = None
    if args.target.startswith(("http://", "https://")):
        response = fetch_url(args.target, profile)
        html, url = response.body, args.target
    else:
        match = SOURCE_REF.match(args.target)
        if match and match.group("name") in profile.sources:
            source = profile.source(match.group("name"))
            url = source.url_for(match.group("path") or "")
            response = fetch(url, source, profile.egress_policy())
            html = response.body
        else:
            url, html = args.target, read_html(args.target)

    report = probe(html, url, profile, response)
    render_probe(report, console)

    if args.suggest:
        console.print("\n  [bold]Suggested profile fragment[/]\n")
        # TOML section headers are square brackets, which the markup parser
        # would consume, so emit the fragment as literal text.
        for line in suggested_profile(report).splitlines():
            console.print(Text(f"    {line}", style="dim"))
        console.print()
    return EXIT_CLEAN


def cmd_sources(args) -> int:
    console = make_console()
    profile = load_profile(getattr(args, "profile", None))
    if not profile.sources:
        console.print("\n  This profile defines no sources.\n", style="yellow")
    else:
        console.print("\n  Sources:\n", style="bold")
        for name, src in profile.sources.items():
            console.print(f"  [bold]{name}[/]  {src.base_url}")
            if src.connect_to:
                console.print(f"      [dim]connect_to {src.connect_to} "
                              f"(Host header and SNI preserved)[/]")
    policy = profile.egress_policy()
    console.print("\n  Egress allowlist (nothing else is ever contacted):", style="bold")
    for host in policy.allowed or ["(empty: network access disabled)"]:
        console.print(f"    {host}", style="dim")
    console.print()
    return EXIT_CLEAN


def cmd_help(args) -> int:
    """Every command and every flag on one screen.

    Per-command --help exists, but discovering an option you did not know to
    look for means running it once per command. This prints the lot.
    """
    console = make_console()
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    console.print(f"\n  [bold]sitediff {__version__}[/]")
    console.print("  Compare the visible content of two renderings of the same "
                  "page, across environments.\n", style="dim")

    seen: set[int] = set()
    for name, subparser in subparsers.choices.items():
        if id(subparser) in seen:      # aliases share their parser object
            continue
        seen.add(id(subparser))
        summary = subparser.description or ""
        alias_note = ""
        if COMMAND_ALIASES.get(name):
            alias_note = f"  [dim](or {', '.join(COMMAND_ALIASES[name])})[/]"
        console.print(f"  [bold cyan]{name}[/]{alias_note}  [dim]{summary}[/]")
        rows = []
        for action in subparser._actions:
            if action.dest == "help":
                continue
            flags = ", ".join(action.option_strings) or f"<{action.dest}>"
            hint = (action.help or "").replace("\n", " ")
            if getattr(action, "choices", None):
                hint += f"  ({'|'.join(str(c) for c in action.choices)})"
            rows.append((flags, hint))
        if rows:
            width = min(34, max(len(f) for f, _ in rows))
            for flags, hint in rows:
                console.print(f"      {flags:<{width}}  [dim]{hint}[/]", highlight=False)
        console.print()

    console.print("  [bold]Exit codes[/]")
    console.print("      0  the two sides agree", style="dim")
    console.print("      1  drift was found", style="dim")
    console.print("      2  the run failed", style="dim")
    console.print("\n  [bold]Environment[/]")
    console.print("      SITEDIFF_PROFILES  extra profile directories", style="dim")
    console.print("      NO_COLOR           disable colour output", style="dim")
    console.print()
    return EXIT_CLEAN


def cmd_profiles(args) -> int:
    console = make_console()
    rows = discover_profiles()
    if not rows:
        console.print("No profiles found.", style="yellow")
    else:
        console.print("\n  Profiles:\n", style="bold")
        width = max(len(n) for n, _, _ in rows)
        for name, desc, path in rows:
            console.print(f"  [bold]{name:<{width}}[/]  [dim]{desc}[/]")
            console.print(f"  {'':<{width}}  [dim cyan]{path}[/]")
    console.print("\n  Searched, most specific first:", style="bold")
    for directory in profile_search_path():
        mark = "" if directory.is_dir() else "  [dim](absent)[/]"
        console.print(f"    [dim]{directory}[/]{mark}")
    console.print("\n  Site-specific profiles belong in ./.sitediff/ in your own "
                  "repo,\n  not in this tool.\n", style="dim")
    return EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    canonical = {
        "compare": cmd_compare, "extract": cmd_extract, "profiles": cmd_profiles,
        "fetch": cmd_fetch, "sources": cmd_sources, "probe": cmd_probe,
        "run": cmd_run, "accept": cmd_accept, "runs": cmd_runs, "har": cmd_har,
        "help": cmd_help,
    }
    # Aliases dispatch to the same handler, so nothing downstream needs to know
    # which spelling was typed.
    handlers = dict(canonical)
    for name, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            handlers[alias] = canonical[name]
    try:
        return handlers[args.command](args)
    except SiteDiffError as exc:
        console = make_console(stderr=True)
        # Error text carries profile keys such as [security], which rich would
        # otherwise consume as markup, so build it as literal Text.
        message = Text("\nerror: ", style="bold red")
        message.append(str(exc), style="")
        console.print(message)
        console.print()
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
