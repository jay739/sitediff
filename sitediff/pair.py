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
from dataclasses import dataclass, field

from .errors import InvalidProfileError, PairingError
from .source import EgressPolicy, Source, fetch

MAX_KEYS = 100_000


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
             "delay_ms", "workers"}
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

    if not section.get("keys") and discovery is None:
        raise InvalidProfileError(
            path, "[pair] needs either 'keys' or a [pair.discover] block")

    return PairPlan(
        left=section["left"], right=section["right"],
        left_path=section["left_path"], right_path=section["right_path"],
        keys=tuple(str(k) for k in section.get("keys", [])),
        discover=discovery,
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
