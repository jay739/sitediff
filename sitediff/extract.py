"""Turn an HTML page into the list of visible text lines a reader would see.

The output of this module is what actually gets diffed, so every decision here
is about making two independently-built pages line up when they say the same
thing, and diverge only when they do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .config import Profile
from .errors import (
    EmptyExtractionError,
    ExtractionError,
    PageNotFoundError,
    PageNotRenderedError,
    PageUnreadableError,
)


@dataclass
class Page:
    """An extracted page: its source path and the visible lines pulled from it."""

    path: str
    side: str
    lines: list[str]

    def __len__(self) -> int:
        return len(self.lines)


def read_html(path: str | Path) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise PageNotFoundError(str(p)) from exc
    except IsADirectoryError as exc:
        raise PageUnreadableError(str(p), "it is a directory, not a file") from exc
    except PermissionError as exc:
        raise PageUnreadableError(str(p), "permission denied") from exc
    except OSError as exc:
        raise PageUnreadableError(str(p), str(exc)) from exc


def _make_soup(raw: str, profile: Profile, path: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(raw, profile.parser)
    except Exception as exc:  # bs4 raises FeatureNotFound for a missing parser
        if profile.parser != "html.parser":
            return BeautifulSoup(raw, "html.parser")
        raise ExtractionError(
            f"Could not parse {path} with parser {profile.parser!r}: {exc}"
        ) from exc


def _is_hidden(tag: Tag, profile: Profile) -> bool:
    """Best-effort visibility check.

    Only inline attributes are inspected, since no stylesheet is available. A
    profile's hidden_classes list covers the common utility classes that hide
    content via CSS, which is how most real sites do it.
    """
    if tag.has_attr("hidden"):
        return True

    if profile.respect_aria_hidden:
        aria = tag.get("aria-hidden", "")
        if isinstance(aria, list):
            aria = " ".join(aria)
        if aria.strip().lower() == "true":
            return True

    style = tag.get("style", "")
    if isinstance(style, list):
        style = " ".join(style)
    if style:
        squashed = style.replace(" ", "").lower()
        if any(marker in squashed for marker in profile.hidden_styles):
            return True

    if profile.hidden_classes:
        classes = tag.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        if any(c in profile.hidden_classes for c in classes):
            return True

    return False


def _strip(soup: BeautifulSoup, profile: Profile) -> None:
    """Remove everything that is not visible page text, in place."""
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    for tag_name in profile.noise_tags:
        for tag in soup.find_all(tag_name):
            if not tag.decomposed:
                tag.decompose()

    for selector in profile.exclude_selectors:
        for tag in soup.select(selector):
            if not tag.decomposed:
                tag.decompose()

    # find_all returns a snapshot, so decomposing a parent leaves stale children
    # in the list. Touching a decomposed tag raises, hence the guard.
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        if _is_hidden(tag, profile):
            tag.decompose()


def _walk(root: Tag, block_tags: set[str]) -> list[str]:
    lines: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            text = " ".join(" ".join(current).split())
            if text:
                lines.append(text)
            current.clear()

    def visit(node) -> None:
        if isinstance(node, NavigableString):
            piece = str(node).strip()
            if piece:
                current.append(piece)
            return
        name = getattr(node, "name", None)
        is_block = name in block_tags
        if is_block:
            flush()
        for child in getattr(node, "children", []):
            visit(child)
        if is_block:
            flush()

    visit(root)
    flush()
    return lines


def extract(
    path: str | Path,
    profile: Profile,
    side: str = "page",
    allow_unrendered: bool = False,
) -> Page:
    """Extract visible text lines from an HTML file according to a profile."""
    path = str(path)
    raw = read_html(path)

    if not allow_unrendered:
        for sentinel in profile.render_sentinels:
            if sentinel in raw:
                raise PageNotRenderedError(path, sentinel, side)

    soup = _make_soup(raw, profile, path)
    _strip(soup, profile)

    block_tags = set(profile.block_tags)

    if profile.include_selectors:
        roots = []
        for selector in profile.include_selectors:
            roots.extend(t for t in soup.select(selector) if not t.decomposed)
    else:
        roots = [soup.body or soup]

    lines: list[str] = []
    for root in roots:
        lines.extend(_walk(root, block_tags))

    for rule in profile.normalize:
        lines = [rule.apply(line) for line in lines]
    lines = [ln for ln in (l.strip() for l in lines) if ln]

    if not lines:
        raise EmptyExtractionError(path, side)

    return Page(path=path, side=side, lines=lines)
