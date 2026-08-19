"""Pairing entities across two sites.

Every existing tool keys its comparison on URL, which fails the moment the two
sides disagree about URL structure, and a legacy site and its rewrite almost
always do. Pairing on an entity key instead is what makes a cross-site
comparison possible at all.

Keys are either listed outright or discovered by scraping a listing endpoint,
because a hash-routed or JavaScript-driven site often cannot be crawled at all
while still exposing a perfectly ordinary index.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .errors import InvalidProfileError, PairingError
from .source import EgressPolicy, Source, fetch

MAX_KEYS = 100_000


def _compile_one(pattern: str, path: str, label: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise InvalidProfileError(path, f"{label} is not a valid regex: {exc}") from exc


@dataclass(frozen=True)
class Discovery:
    source: str
    path: str
    pattern: re.Pattern
    params: tuple[str, ...] = ()

    def paths(self) -> list[str]:
        if not self.params:
            return [self.path]
        return [self.path.replace("{param}", p) for p in self.params]


@dataclass(frozen=True)
class PairPlan:
    left: str = ""
    right: str = ""
    left_path: str = ""
    right_path: str = ""
    keys: tuple[str, ...] = ()
    discover: Discovery | None = None
    crawl: Crawl | None = None
    delay_ms: int = 0
    workers: int = 4

    @property
    def configured(self) -> bool:
        return bool(self.left and self.right)

    def url_paths(self, key: str) -> tuple[str, str]:
        if "{key}" not in self.left_path or "{key}" not in self.right_path:
            raise PairingError(
                "left_path and right_path must each contain '{key}', which is "
                "substituted with the entity key for every pair."
            )
        return self.left_path.replace("{key}", key), self.right_path.replace("{key}", key)


def parse_pair(data: dict, path: str) -> PairPlan:
    section = data.get("pair")
    if not section:
        return PairPlan()

    known = {"left", "right", "left_path", "right_path", "keys", "discover",
             "crawl", "delay_ms", "workers"}
    unknown = sorted(set(section) - known)
    if unknown:
        raise InvalidProfileError(
            path, f"unknown key(s) in [pair]: {unknown}; recognised: {sorted(known)}")

    for required in ("left", "right", "left_path", "right_path"):
        if required not in section:
            raise InvalidProfileError(path, f"[pair] needs '{required}'")

    discovery = None
    raw = section.get("discover")
    if raw is not None:
        for required in ("source", "path", "pattern"):
            if required not in raw:
                raise InvalidProfileError(path, f"[pair.discover] needs '{required}'")
        try:
            compiled = re.compile(raw["pattern"])
        except re.error as exc:
            raise InvalidProfileError(
                path, f"[pair.discover] pattern is not a valid regex: {exc}") from exc
        if compiled.groups != 1:
            raise InvalidProfileError(
                path,
                f"[pair.discover] pattern must have exactly one capture group, "
                f"which is the entity key; this one has {compiled.groups}",
            )
        discovery = Discovery(
            source=raw["source"], path=raw["path"], pattern=compiled,
            params=tuple(raw.get("params", [])),
        )

    crawl = None
    raw_crawl = section.get("crawl")
    if raw_crawl is not None:
        for required in ("source", "key_pattern"):
            if required not in raw_crawl:
                raise InvalidProfileError(path, f"[pair.crawl] needs {required!r}")
        compiled = _compile_one(raw_crawl["key_pattern"], path, "[pair.crawl] key_pattern")
        if compiled.groups != 1:
            raise InvalidProfileError(
                path,
                f"[pair.crawl] key_pattern must have exactly one capture group, "
                f"which is the entity key; this one has {compiled.groups}",
            )
        follow = (_compile_one(raw_crawl["follow"], path, "[pair.crawl] follow")
                  if raw_crawl.get("follow") else None)
        crawl = Crawl(
            source=raw_crawl["source"],
            seeds=tuple(raw_crawl.get("seeds", ["/"])),
            key_pattern=compiled,
            follow=follow,
            max_pages=int(raw_crawl.get("max_pages", 500)),
            delay_ms=int(raw_crawl.get("delay_ms", 200)),
            respect_robots=raw_crawl.get("respect_robots", True),
        )

    if not section.get("keys") and discovery is None and crawl is None:
        raise InvalidProfileError(
            path,
            "[pair] needs 'keys', a [pair.discover] block, or a [pair.crawl] block",
        )

    return PairPlan(
        left=section["left"], right=section["right"],
        left_path=section["left_path"], right_path=section["right_path"],
        keys=tuple(str(k) for k in section.get("keys", [])),
        discover=discovery,
        crawl=crawl,
        delay_ms=int(section.get("delay_ms", 0)),
        workers=max(1, int(section.get("workers", 4))),
    )


@dataclass
class DiscoveryResult:
    keys: list[str] = field(default_factory=list)
    fetched: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def discover_keys(plan: PairPlan, sources: dict[str, Source],
                  policy: EgressPolicy) -> DiscoveryResult:
    """Scrape entity keys from a listing endpoint."""
    if plan.crawl:
        return crawl_keys(plan.crawl, sources, policy)
    if not plan.discover:
        return DiscoveryResult(keys=list(plan.keys))

    spec = plan.discover
    source = sources.get(spec.source)
    if source is None:
        raise PairingError(
            f"[pair.discover] names source {spec.source!r}, which this profile "
            f"does not define. Known sources: {sorted(sources) or '(none)'}"
        )

    result = DiscoveryResult()
    seen: set[str] = set()
    for path in spec.paths():
        url = source.url_for(path)
        try:
            response = fetch(url, source, policy)
        except Exception as exc:
            result.failures.append((url, str(exc).splitlines()[0]))
            continue
        result.fetched += 1
        for match in spec.pattern.finditer(response.body):
            key = match.group(1)
            if key not in seen:
                seen.add(key)
                result.keys.append(key)
                if len(result.keys) >= MAX_KEYS:
                    return result

    if not result.keys:
        raise PairingError(
            f"Discovery matched no keys across {result.fetched} page(s).\n"
            f"Check that the pattern's capture group matches the entity id as it "
            f"appears in the listing markup, and that the listing itself is "
            f"server-rendered. Run 'sitediff fetch {spec.source}:{spec.paths()[0]}' "
            f"to see what came back."
        )
    return result


@dataclass(frozen=True)
class Crawl:
    """Discover entity keys by crawling one side from a seed.

    Points at whichever side actually has crawlable URLs, then derives the
    other side's URLs from the key. That asymmetry is the normal case: a
    rewrite is usually server-rendered with real paths while the legacy app is
    hash-routed and cannot be crawled at all.
    """

    source: str
    seeds: tuple[str, ...]
    key_pattern: re.Pattern
    follow: re.Pattern | None = None
    max_pages: int = 500
    delay_ms: int = 200
    respect_robots: bool = True


_HREF = re.compile(r"""<a[^>]+href\s*=\s*["']([^"'#]+)""", re.I)


def _robots_for(base: str, source: Source, policy: EgressPolicy):
    """Fetch and parse robots.txt. A tool that crawls should say who it is and
    honour what it is told, which is also what keeps it welcome on a site."""
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    try:
        response = fetch(urljoin(base, "/robots.txt"), source, policy)
        if 200 <= response.status < 300:
            parser.parse(response.body.splitlines())
        else:
            parser.parse([])
    except Exception:  # an unreachable robots.txt must not stop a run
        parser.parse([])
    return parser


def crawl_keys(spec: Crawl, sources: dict[str, Source],
               policy: EgressPolicy) -> DiscoveryResult:
    """Breadth-first crawl of one origin, collecting entity keys from URLs."""
    source = sources.get(spec.source)
    if source is None:
        raise PairingError(
            f"[pair.crawl] names source {spec.source!r}, which this profile does "
            f"not define. Known sources: {sorted(sources) or '(none)'}"
        )

    base = source.base_url.rstrip("/")
    origin = urlparse(base)
    robots = _robots_for(base, source, policy) if spec.respect_robots else None

    result = DiscoveryResult()
    seen_keys: set[str] = set()
    visited: set[str] = set()
    queue: list[str] = [source.url_for(seed) for seed in spec.seeds]
    blocked = 0

    while queue and result.fetched < spec.max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        if robots is not None and not robots.can_fetch("sitediff", url):
            blocked += 1
            continue

        try:
            if spec.delay_ms:
                time.sleep(spec.delay_ms / 1000)
            response = fetch(url, source, policy)
        except Exception as exc:  # one bad page must not end a crawl
            result.failures.append((url, str(exc).splitlines()[0]))
            continue
        result.fetched += 1

        for match in spec.key_pattern.finditer(url):
            key = match.group(1)
            if key not in seen_keys:
                seen_keys.add(key)
                result.keys.append(key)

        for href in _HREF.findall(response.body):
            target = urljoin(url, href.strip())
            parsed = urlparse(target)
            if parsed.scheme not in ("http", "https"):
                continue
            if (parsed.hostname or "").lower() != (origin.hostname or "").lower():
                continue
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            if spec.follow and not spec.follow.search(path):
                continue
            if target not in visited and len(visited) + len(queue) < spec.max_pages * 4:
                queue.append(target)

    if blocked:
        result.failures.append(("robots.txt", f"{blocked} URL(s) disallowed"))
    if not result.keys:
        raise PairingError(
            f"Crawl visited {result.fetched} page(s) and matched no keys.\n"
            f"key_pattern is matched against the URL, not the page body, so check "
            f"it against a real URL from this site. Widen 'follow' if the crawl "
            f"never reached the pages that carry keys."
        )
    return result
