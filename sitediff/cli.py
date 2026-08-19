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

from . import __version__
from .config import Profile, list_builtin_profiles, load_profile
from .engine import Status, compare, noise_baseline
from .errors import SiteDiffError
from .extract import Page, extract, extract_html
from rich.text import Text

from .render import make_console, render_side_by_side, render_summary, render_unified
from .source import Source, fetch

SOURCE_REF = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+):(?P<path>/.*)?$")

EXIT_CLEAN, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sitediff",
        description="Compare the visible content of two renderings of the same page.",
    )
    parser.add_argument("--version", action="version", version=f"sitediff {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cmp_p = sub.add_parser("compare", help="diff two saved HTML pages")
    cmp_p.add_argument("left", help="saved HTML file, URL, or source:/path (reference side)")
    cmp_p.add_argument("right", help="saved HTML file, URL, or source:/path (candidate side)")
    cmp_p.add_argument("-p", "--profile", help="built-in profile name or path to a .toml")
    cmp_p.add_argument("-s", "--side-by-side", action="store_true", help="two-column view")
    cmp_p.add_argument("--html", metavar="PATH", help="also write a standalone HTML report")
    cmp_p.add_argument(
        "--baseline", nargs=2, metavar=("CAPTURE_A", "CAPTURE_B"),
        help="two captures of the SAME side; anything differing between them is "
             "treated as inherent noise and suppressed from the main diff",
    )
    cmp_p.add_argument("--context", type=int, metavar="N", help="context lines around each change")
    cmp_p.add_argument("--allow-unrendered", action="store_true",
                       help="compare even if a page still shows a loading placeholder")
    cmp_p.add_argument("--no-color", action="store_true", help="disable colour output")
    cmp_p.add_argument("-q", "--quiet", action="store_true", help="summary only, no diff body")

    ex_p = sub.add_parser("extract", help="show the visible lines pulled from one page")
    ex_p.add_argument("page")
    ex_p.add_argument("-p", "--profile")
    ex_p.add_argument("-n", "--numbers", action="store_true", help="prefix line numbers")
    ex_p.add_argument("--allow-unrendered", action="store_true")

    f_p = sub.add_parser("fetch", help="fetch one page and report how it was served")
    f_p.add_argument("target", help="URL or source:/path")
    f_p.add_argument("-p", "--profile")
    f_p.add_argument("--body", action="store_true", help="also print extracted lines")

    sub.add_parser("profiles", help="list the built-in profiles")
    src_p = sub.add_parser("sources", help="list sources and the egress allowlist")
    src_p.add_argument("-p", "--profile")
    return parser


def resolve_side(token: str, profile: Profile, side: str, allow_unrendered: bool) -> Page:
    """Turn one command line argument into an extracted page.

    Accepted forms, in order: an explicit URL, a `sourcename:/path` reference
    into the profile, or a path to a saved HTML file.
    """
    if token.startswith(("http://", "https://")):
        source = Source(name=side, base_url=token)
        policy = profile.egress_policy()
        response = fetch(token, source, policy)
        return _page_from_response(response, token, profile, side, allow_unrendered)

    match = SOURCE_REF.match(token)
    if match and match.group("name") in profile.sources:
        source = profile.source(match.group("name"))
        url = source.url_for(match.group("path") or "")
        response = fetch(url, source, profile.egress_policy())
        return _page_from_response(response, url, profile, side, allow_unrendered)

    return extract(token, profile, side=side, allow_unrendered=allow_unrendered)


def _page_from_response(response, url, profile, side, allow_unrendered) -> Page:
    return extract_html(
        response.body, profile, side=side, label=url,
        allow_unrendered=allow_unrendered,
        meta=response.metadata(profile.compare_headers),
    )


def cmd_compare(args) -> int:
    profile = load_profile(args.profile).with_overrides(context_lines=args.context)
    console = make_console(no_color=args.no_color)

    left = resolve_side(args.left, profile, "left", args.allow_unrendered)
    right = resolve_side(args.right, profile, "right", args.allow_unrendered)

    noise = None
    if args.baseline:
        a = extract(args.baseline[0], profile, side="baseline A",
                    allow_unrendered=args.allow_unrendered)
        b = extract(args.baseline[1], profile, side="baseline B",
                    allow_unrendered=args.allow_unrendered)
        noise = noise_baseline(a, b)
        if noise:
            console.print(
                f"  Baseline found {len(noise)} unstable line(s); the capture is "
                f"non-deterministic and those lines are excluded.",
                style="yellow",
            )
        else:
            console.print("  Baseline is stable: the capture is reproducible.", style="green")

    result = compare(left, right, profile, noise=noise)

    render_summary(result, console)
    if not args.quiet:
        if args.side_by_side:
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
    page = extract(args.page, profile, side="page", allow_unrendered=args.allow_unrendered)
    width = len(str(len(page.lines)))
    for i, line in enumerate(page.lines, 1):
        console.print(f"[dim cyan]{i:>{width}}[/] {line}" if args.numbers else line,
                      highlight=False)
    return EXIT_CLEAN


def cmd_fetch(args) -> int:
    profile = load_profile(args.profile)
    console = make_console()
    page = resolve_side(args.target, profile, "page", allow_unrendered=True)

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


def cmd_profiles(args) -> int:
    console = make_console()
    rows = list_builtin_profiles()
    if not rows:
        console.print("No built-in profiles found.", style="yellow")
        return EXIT_CLEAN
    console.print("\n  Built-in profiles:\n", style="bold")
    width = max(len(n) for n, _ in rows)
    for name, desc in rows:
        console.print(f"  [bold]{name:<{width}}[/]  [dim]{desc}[/]")
    console.print("\n  Use with: sitediff compare LEFT RIGHT -p <name>\n", style="dim")
    return EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "compare": cmd_compare, "extract": cmd_extract, "profiles": cmd_profiles,
        "fetch": cmd_fetch, "sources": cmd_sources,
    }
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
