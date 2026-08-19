"""Profile loading. A profile is the whole site-specific part of a comparison.

Profiles are TOML so they need no dependency beyond the standard library, and
so a team can check one into their own repo and hand it to this tool.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from .capture import Capture
from .errors import InvalidProfileError, ProfileNotFoundError, SourceNotFoundError
from .source import DEFAULT_MAX_REDIRECTS, DEFAULT_TIMEOUT, EgressPolicy, Source

# Inside the package so the bundled templates ship with the wheel. Only generic
# starting points live here. Profiles describing a real site name real hosts and
# addresses, which is infrastructure detail that does not belong in this repo.
BUILTIN_PROFILE_DIR = Path(__file__).resolve().parent / "profiles"

PROFILE_ENV_VAR = "SITEDIFF_PROFILES"
LOCAL_PROFILE_DIR = ".sitediff"


def user_profile_dir() -> Path:
    """Per-user profile directory, following each platform's convention."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "sitediff"


def profile_search_path() -> list[Path]:
    """Where named profiles are looked up, most specific first.

    Site-specific profiles are expected to live in the consuming team's own
    repository (./.sitediff/) or on the machine, not inside this tool. That
    keeps real hostnames and addresses out of this repo while leaving the
    egress allowlist under the adopting team's own review.
    """
    paths: list[Path] = []
    env = os.environ.get(PROFILE_ENV_VAR, "")
    paths.extend(Path(part) for part in env.split(os.pathsep) if part.strip())
    paths.append(Path.cwd() / LOCAL_PROFILE_DIR)
    paths.append(user_profile_dir())
    paths.append(BUILTIN_PROFILE_DIR)
    return paths

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


class Mode(StrEnum):
    """What counts as a difference.

    VALUES  Both sides read the same data, so any disagreement is a defect.
            Legacy against rewrite, region against region, cache against origin.

    SHAPE   The sides hold different data on purpose (staging against
            production), so values are masked and only labels, fields and
            structure are compared.
    """

    VALUES = "values"
    SHAPE = "shape"


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
    mode: Mode = Mode.VALUES

    # Acquisition and safety
    sources: dict[str, Source] = field(default_factory=dict)
    allow_hosts: list[str] = field(default_factory=list)
    redact: list[Rule] = field(default_factory=list)
    compare_headers: list[str] = field(default_factory=list)

    def egress_policy(self) -> EgressPolicy:
        """Hosts this profile may contact.

        Defaults to exactly the hosts its own sources point at, so a profile
        never reaches anywhere it did not explicitly configure. allow_hosts
        only ever widens this, and only with what a human wrote down.
        """
        hosts = list(self.allow_hosts)
        for source in self.sources.values():
            host = urlparse(source.base_url).hostname
            if host:
                hosts.append(host)
            if source.connect_to:
                hosts.append(source.connect_to)
        return EgressPolicy(sorted(set(hosts)))

    def source(self, name: str) -> Source:
        try:
            return self.sources[name]
        except KeyError:
            raise SourceNotFoundError(name, sorted(self.sources)) from None

    def apply_redaction(self, text: str) -> str:
        for rule in self.redact:
            text = rule.apply(text)
        return text

    def with_overrides(self, **kwargs) -> Profile:
        """Return a copy with non-None overrides applied (used by the CLI)."""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean) if clean else self


PROFILE_KEYS = {"name", "description"}
EXTRACT_KEYS = {
    "noise_tags", "block_tags", "hidden_styles", "hidden_classes",
    "respect_aria_hidden", "include_selectors", "exclude_selectors", "parser",
    "render_sentinels",
}
COMPARE_KEYS = {"mode", "context_lines", "ignore_patterns", "headers"}
SECURITY_KEYS = {"allow_hosts"}
SOURCE_KEYS = {
    "name", "base_url", "connect_to", "headers", "timeout", "verify_tls",
    "max_redirects", "follow_redirects", "capture", "hydration_marker",
}
RULE_KEYS = {"pattern", "replacement", "note"}


def _reject_unknown(mapping: dict, allowed: set[str], path: str, where: str) -> None:
    """Fail on keys this tool does not recognise.

    Silently ignoring a typo is worse than erroring: a misspelled key in a
    profile looks configured but does nothing, and these keys include the
    egress allowlist and the redaction rules.
    """
    unknown = sorted(set(mapping) - allowed)
    if not unknown:
        return
    hints = []
    for key in unknown:
        close = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
        hints.append(f"{key!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
    raise InvalidProfileError(
        path,
        f"unknown key(s) in {where}: {', '.join(hints)}\n"
        f"Recognised keys: {', '.join(sorted(allowed))}",
    )


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
    searched: list[str] = []

    if not candidate.is_file():
        if candidate.suffix:
            searched.append(str(candidate.resolve()))
        found = None
        for directory in profile_search_path():
            by_name = directory / f"{source}.toml"
            searched.append(str(by_name))
            if by_name.is_file():
                found = by_name
                break
        if found is None:
            raise ProfileNotFoundError(str(source), searched)
        candidate = found

    path = str(candidate)
    try:
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise InvalidProfileError(path, f"not valid TOML: {exc}") from exc
    except OSError as exc:
        raise InvalidProfileError(path, f"could not be read: {exc}") from exc

    extract = data.get("extract", {})
    compare = data.get("compare", {})
    security = data.get("security", {})

    known_sections = {"profile", "extract", "compare", "normalize",
                      "redact", "security", "source"}
    unknown = set(data) - known_sections
    if unknown:
        raise InvalidProfileError(
            path,
            f"unknown section(s) {sorted(unknown)}; expected any of "
            f"{sorted(known_sections)}",
        )

    def parse_rules(section: str) -> list[Rule]:
        out = []
        for i, raw in enumerate(data.get(section, [])):
            _reject_unknown(raw, RULE_KEYS, path, f"[[{section}]] entry {i}")
            if "pattern" not in raw:
                raise InvalidProfileError(
                    path, f"[[{section}]] entry {i} needs a 'pattern'")
            if section == "normalize" and "replacement" not in raw:
                raise InvalidProfileError(
                    path, f"[[normalize]] entry {i} needs a 'replacement'")
            out.append(Rule(
                pattern=_compile([raw["pattern"]], path, f"{section}.pattern")[0],
                replacement=raw.get("replacement", "[REDACTED]"),
                note=raw.get("note", ""),
            ))
        return out

    sources: dict[str, Source] = {}
    for i, raw in enumerate(data.get("source", [])):
        _reject_unknown(raw, SOURCE_KEYS, path, f"[[source]] entry {i}")
        if "name" not in raw:
            raise InvalidProfileError(path, f"[[source]] entry {i} needs a 'name'")
        if raw["name"] in sources:
            raise InvalidProfileError(path, f"duplicate source name {raw['name']!r}")
        capture_raw = raw.get("capture", Capture.STATIC.value)
        try:
            capture_mode = Capture(capture_raw)
        except ValueError:
            raise InvalidProfileError(
                path,
                f"source {raw['name']!r}: capture must be one of "
                f"{[c.value for c in Capture]}, got {capture_raw!r}",
            ) from None

        sources[raw["name"]] = Source(
            name=raw["name"],
            capture=capture_mode,
            hydration_marker=raw.get("hydration_marker", ""),
            base_url=raw.get("base_url", ""),
            connect_to=raw.get("connect_to", ""),
            headers=raw.get("headers", {}),
            timeout=float(raw.get("timeout", DEFAULT_TIMEOUT)),
            verify_tls=raw.get("verify_tls", True),
            max_redirects=int(raw.get("max_redirects", DEFAULT_MAX_REDIRECTS)),
            follow_redirects=raw.get("follow_redirects", True),
        )

    mode_raw = compare.get("mode", Mode.VALUES.value)
    try:
        mode = Mode(mode_raw)
    except ValueError:
        raise InvalidProfileError(
            path,
            f"compare.mode must be one of {[m.value for m in Mode]}, got {mode_raw!r}",
        ) from None

    _reject_unknown(data.get("profile", {}), PROFILE_KEYS, path, "[profile]")
    _reject_unknown(extract, EXTRACT_KEYS, path, "[extract]")
    _reject_unknown(compare, COMPARE_KEYS, path, "[compare]")
    _reject_unknown(security, SECURITY_KEYS, path, "[security]")

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
            _require_list_of_str(
                compare.get("ignore_patterns", []), path, "compare.ignore_patterns",
            ),
            path, "compare.ignore_patterns",
        ),
        normalize=rules,
        context_lines=compare.get("context_lines", defaults.context_lines),
        mode=mode,
        sources=sources,
        allow_hosts=_require_list_of_str(
            security.get("allow_hosts", []), path, "security.allow_hosts"),
        redact=parse_rules("redact"),
        compare_headers=_require_list_of_str(
            compare.get("headers", []), path, "compare.headers",
        ),
    )


def discover_profiles() -> list[tuple[str, str, Path]]:
    """Every profile reachable on the search path, as (name, description, path).

    Earlier entries win, matching load_profile, so a local profile shadows a
    bundled template of the same name rather than appearing twice.
    """
    out: list[tuple[str, str, Path]] = []
    seen: set[str] = set()
    for directory in profile_search_path():
        if not directory.is_dir():
            continue
        for p in sorted(directory.glob("*.toml")):
            if p.stem in seen:
                continue
            seen.add(p.stem)
            try:
                meta = tomllib.loads(p.read_text(encoding="utf-8")).get("profile", {})
                out.append((p.stem, meta.get("description", ""), p))
            except (tomllib.TOMLDecodeError, OSError):
                out.append((p.stem, "(unreadable)", p))
    return out
