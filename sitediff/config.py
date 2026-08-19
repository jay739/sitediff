"""Profile loading. A profile is the whole site-specific part of a comparison.

Profiles are TOML so they need no dependency beyond the standard library, and
so a team can check one into their own repo and hand it to this tool.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .errors import InvalidProfileError, ProfileNotFoundError

BUILTIN_PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"

DEFAULT_NOISE_TAGS = [
    "script", "style", "noscript", "template", "svg", "path", "iframe",
    "canvas", "object", "embed",
]

# Block-level tags only. Inline tags such as span and a are deliberately absent:
# treating them as blocks splits "Total score is 72.4 out of 100" into five
# lines and divorces every value from its label.
DEFAULT_BLOCK_TAGS = [
    "p", "div", "section", "article", "header", "footer", "nav", "main",
    "table", "tr", "td", "th", "thead", "tbody", "tfoot", "caption",
    "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "hr", "form", "fieldset", "legend", "blockquote", "pre",
]

DEFAULT_HIDDEN_STYLES = ["display:none", "visibility:hidden"]

DEFAULT_HIDDEN_CLASSES = [
    "hidden", "hide", "d-none", "sr-only", "visually-hidden", "screen-reader-only",
]


@dataclass(frozen=True)
class Rule:
    """A regex substitution applied to every extracted line before diffing."""

    pattern: re.Pattern
    replacement: str
    note: str = ""

    def apply(self, line: str) -> str:
        return self.pattern.sub(self.replacement, line)


@dataclass(frozen=True)
class Profile:
    name: str = "default"
    description: str = ""

    # Extraction
    noise_tags: list[str] = field(default_factory=lambda: list(DEFAULT_NOISE_TAGS))
    block_tags: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCK_TAGS))
    hidden_styles: list[str] = field(default_factory=lambda: list(DEFAULT_HIDDEN_STYLES))
    hidden_classes: list[str] = field(default_factory=lambda: list(DEFAULT_HIDDEN_CLASSES))
    respect_aria_hidden: bool = True
    include_selectors: list[str] = field(default_factory=list)
    exclude_selectors: list[str] = field(default_factory=list)
    parser: str = "lxml"

    # Render guard
    render_sentinels: list[str] = field(default_factory=list)

    # Comparison
    ignore_patterns: list[re.Pattern] = field(default_factory=list)
    normalize: list[Rule] = field(default_factory=list)
    context_lines: int = 3

    def with_overrides(self, **kwargs) -> "Profile":
        """Return a copy with non-None overrides applied (used by the CLI)."""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean) if clean else self


def _compile(patterns, path: str, key: str) -> list[re.Pattern]:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p))
        except re.error as exc:
            raise InvalidProfileError(
                path, f"{key} contains an invalid regex {p!r}: {exc}"
            ) from exc
    return out


def _require_list_of_str(value, path: str, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidProfileError(path, f"{key} must be a list of strings")
    return value


def load_profile(source: str | Path | None) -> Profile:
    """Load a profile by built-in name or filesystem path.

    Passing None returns the built-in defaults, which are deliberately usable
    against any site with no configuration at all.
    """
    if source is None:
        return Profile()

    candidate = Path(source)
    searched = [str(candidate.resolve())] if candidate.suffix else []

    if not candidate.is_file():
        by_name = BUILTIN_PROFILE_DIR / f"{source}.toml"
        searched.append(str(by_name))
        if not by_name.is_file():
            raise ProfileNotFoundError(str(source), searched)
        candidate = by_name

    path = str(candidate)
    try:
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise InvalidProfileError(path, f"not valid TOML: {exc}") from exc
    except OSError as exc:
        raise InvalidProfileError(path, f"could not be read: {exc}") from exc

    extract = data.get("extract", {})
    compare = data.get("compare", {})

    known_sections = {"profile", "extract", "compare", "normalize"}
    unknown = set(data) - known_sections
    if unknown:
        raise InvalidProfileError(
            path,
            f"unknown section(s) {sorted(unknown)}; expected any of "
            f"{sorted(known_sections)}",
        )

    rules = []
    for i, raw in enumerate(data.get("normalize", [])):
        if "pattern" not in raw or "replacement" not in raw:
            raise InvalidProfileError(
                path, f"[[normalize]] entry {i} needs both 'pattern' and 'replacement'"
            )
        rules.append(
            Rule(
                pattern=_compile([raw["pattern"]], path, "normalize.pattern")[0],
                replacement=raw["replacement"],
                note=raw.get("note", ""),
            )
        )

    defaults = Profile()
    meta = data.get("profile", {})

    return Profile(
        name=meta.get("name", candidate.stem),
        description=meta.get("description", ""),
        noise_tags=_require_list_of_str(
            extract.get("noise_tags", defaults.noise_tags), path, "extract.noise_tags"),
        block_tags=_require_list_of_str(
            extract.get("block_tags", defaults.block_tags), path, "extract.block_tags"),
        hidden_styles=_require_list_of_str(
            extract.get("hidden_styles", defaults.hidden_styles), path, "extract.hidden_styles"),
        hidden_classes=_require_list_of_str(
            extract.get("hidden_classes", defaults.hidden_classes), path, "extract.hidden_classes"),
        respect_aria_hidden=extract.get("respect_aria_hidden", defaults.respect_aria_hidden),
        include_selectors=_require_list_of_str(
            extract.get("include_selectors", []), path, "extract.include_selectors"),
        exclude_selectors=_require_list_of_str(
            extract.get("exclude_selectors", []), path, "extract.exclude_selectors"),
        parser=extract.get("parser", defaults.parser),
        render_sentinels=_require_list_of_str(
            extract.get("render_sentinels", []), path, "extract.render_sentinels"),
        ignore_patterns=_compile(
            _require_list_of_str(compare.get("ignore_patterns", []), path, "compare.ignore_patterns"),
            path, "compare.ignore_patterns"),
        normalize=rules,
        context_lines=compare.get("context_lines", defaults.context_lines),
    )


def list_builtin_profiles() -> list[tuple[str, str]]:
    if not BUILTIN_PROFILE_DIR.is_dir():
        return []
    out = []
    for p in sorted(BUILTIN_PROFILE_DIR.glob("*.toml")):
        try:
            meta = tomllib.loads(p.read_text(encoding="utf-8")).get("profile", {})
            out.append((p.stem, meta.get("description", "")))
        except (tomllib.TOMLDecodeError, OSError):
            out.append((p.stem, "(unreadable)"))
    return out
