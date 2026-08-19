#!/usr/bin/env python3
"""
compare_pages.py

Compares the VISIBLE TEXT content of two saved HTML files (e.g. legacy vs
optimised page renders) and prints/saves a readable diff.

Why this works even for CSR/SSR pages:
- Legacy (CSR/Knockout): save the POST-RENDER DOM from browser Console:
      copy(document.documentElement.outerHTML)
  then paste into a .html file.
- Optimised (SSR/Next.js): "view-source" or a plain fetch already has the
  real values baked in, so just save that HTML directly.

Usage:
    python3 compare_pages.py legacy.html optimised.html
    python3 compare_pages.py legacy.html optimised.html --html-report report.html

What it does:
    1. Strips <script>, <style>, <noscript>, comments, and hidden elements.
    2. Extracts visible text, one "block" per line (so structure roughly
       lines up between the two versions).
    3. Normalizes whitespace so formatting differences don't create noise.
    4. Runs a line-by-line diff and prints only the differences with
       context, plus an optional standalone HTML report you can open in
       a browser (color-coded, side-by-side).

This catches:
    - Missing/blank DB values (an empty cell in one version, a number in
      the other)
    - Footer / static text mismatches (different wording between the two
      codebases)
    - Anything else visible-text-related

It will NOT catch:
    - Pure layout/visual (CSS-only) differences — use a screenshot diff
      tool (e.g. Playwright's screenshot comparison) for that.
"""

import sys
import argparse
import difflib
from pathlib import Path
from bs4 import BeautifulSoup, Comment

# Tags whose content is never meant to be "visible text" on the page
NOISE_TAGS = ["script", "style", "noscript", "template", "svg", "path", "iframe"]

# Block-level tags: we insert a line break after these so the diff roughly
# aligns section-by-section instead of being one giant wall of text.
BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "nav", "main",
    "table", "tr", "td", "th", "thead", "tbody", "tfoot", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "caption", "dt", "dd",
    "form", "fieldset", "label", "button", "a", "span"
}


def is_hidden(tag) -> bool:
    """Best-effort check for elements that are hidden and shouldn't count
    as 'visible' text (display:none, hidden attribute, aria-hidden=true)."""
    if tag.has_attr("hidden"):
        return True
    if tag.get("aria-hidden", "").lower() == "true":
        return True
    style = tag.get("style", "")
    if "display:none" in style.replace(" ", "").lower():
        return True
    if "display: none" in style.lower():
        return True
    return False


def extract_visible_text(html_path: str) -> list[str]:
    """Parse an HTML file and return a list of normalized, non-empty text
    lines representing what a user would actually see on the page."""
    raw = Path(html_path).read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")

    # Remove comments
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # Remove noise tags entirely
    for tag_name in NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove hidden elements
    for tag in soup.find_all(True):
        if is_hidden(tag):
            tag.decompose()

    # Walk the tree, inserting line breaks at block boundaries
    lines: list[str] = []
    current: list[str] = []

    def flush():
        text = " ".join(current).strip()
        text = " ".join(text.split())  # collapse whitespace
        if text:
            lines.append(text)
        current.clear()

    def walk(node):
        from bs4 import NavigableString
        if isinstance(node, NavigableString):
            piece = str(node).strip()
            if piece:
                current.append(piece)
            return
        name = getattr(node, "name", None)
        if name in BLOCK_TAGS:
            flush()
        for child in getattr(node, "children", []):
            walk(child)
        if name in BLOCK_TAGS:
            flush()

    body = soup.body or soup
    walk(body)
    flush()

    return lines


def print_text_diff(legacy_lines: list[str], optimised_lines: list[str]) -> list[str]:
    diff = list(difflib.unified_diff(
        legacy_lines, optimised_lines,
        fromfile="legacy", tofile="optimised",
        lineterm="", n=2
    ))
    if not diff:
        print("No visible-text differences found. 🎉")
    else:
        print("\n".join(diff))
    return diff


def write_html_report(legacy_lines: list[str], optimised_lines: list[str], out_path: str):
    hd = difflib.HtmlDiff(wrapcolumn=80)
    html = hd.make_file(
        legacy_lines, optimised_lines,
        fromdesc="Legacy", todesc="Optimised",
        context=True, numlines=2
    )
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"\nHTML report written to: {out_path}")


def main():
    ap = argparse.ArgumentParser(description="Diff visible text of two HTML pages.")
    ap.add_argument("legacy_html", help="Path to legacy page's saved HTML")
    ap.add_argument("optimised_html", help="Path to optimised page's saved HTML")
    ap.add_argument("--html-report", help="Optional path to write a color-coded HTML diff report")
    args = ap.parse_args()

    legacy_lines = extract_visible_text(args.legacy_html)
    optimised_lines = extract_visible_text(args.optimised_html)

    print(f"Legacy: {len(legacy_lines)} visible text lines")
    print(f"Optimised: {len(optimised_lines)} visible text lines\n")

    print_text_diff(legacy_lines, optimised_lines)

    if args.html_report:
        write_html_report(legacy_lines, optimised_lines, args.html_report)


if __name__ == "__main__":
    main()
