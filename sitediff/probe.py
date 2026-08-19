"""Reconnaissance: work out how a page delivers its values, without a browser.

Answers the question every adopting team hits first, and which is otherwise
answered by opening devtools and reading the Network tab by hand:

    Which capture strategy does this site need, and what is it built with?

Everything here is derived from a single plain GET. No JavaScript is executed,
so this is safe to point at anything already in the egress allowlist.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse

from .config import Profile
from .extract import extract_html
from .source import Response


class Strategy(StrEnum):
    STATIC = "static"
    HYDRATION = "hydration"
    JSON = "json"
    BROWSER = "browser"


@dataclass(frozen=True)
class Signature:
    name: str
    patterns: tuple[str, ...]
    note: str = ""


# Ordered most specific first: a Next.js page is also a React page, and naming
# the meta-framework is more useful than naming the view layer underneath it.
FRAMEWORKS: tuple[Signature, ...] = (
    Signature("Next.js", (r"__NEXT_DATA__", r"self\.__next_f", r"/_next/static/"),
              "React meta-framework"),
    Signature("Nuxt", (r"window\.__NUXT__", r"/_nuxt/"), "Vue meta-framework"),
    Signature("SvelteKit", (r"__sveltekit", r"/_app/immutable/"), "Svelte meta-framework"),
    Signature("Remix", (r"__remixContext", r"/build/_shared/"), "React meta-framework"),
    Signature("Gatsby", (r"___gatsby", r"window\.___chunkMapping"), "React static generator"),
    Signature("Astro", (r"astro-island", r"astro/client"), "islands architecture"),
    Signature("Angular", (r"ng-version=", r"_nghost-", r"zone\.js"), "component framework"),
    Signature("AngularJS", (r"ng-app[=\s]", r"ng-controller[=\s]", r"ng-repeat[=\s]"),
              "legacy 1.x, client-rendered"),
    Signature("Knockout", (r"data-bind\s*=", r"ko\.applyBindings", r"knockout"),
              "MVVM bindings, client-rendered"),
    Signature("Ember", (r"ember-view", r"data-ember-action"), "component framework"),
    Signature("Backbone", (r"backbone[\w.\-]*\.js",), "legacy MV*"),
    Signature("Vue", (r"data-v-[0-9a-f]{8}", r"__VUE__", r"vue[\w.\-]*\.js"),
              "component framework"),
    Signature("React", (r"data-reactroot", r"_reactListening", r"react-dom"), "view layer"),
    Signature("HTMX", (r"hx-get[=\s]", r"hx-post[=\s]", r"htmx(\.min)?\.js"),
              "server-driven fragments"),
    Signature("Alpine.js", (r"x-data[=\s]", r"alpinejs"), "lightweight bindings"),
    # Script filenames carry versions in practice (jquery-3.6.0.min.js), so the
    # patterns must tolerate anything between the library name and the suffix.
    Signature("jQuery", (r"jquery[\w.\-]*\.js", r"\$\.(?:ajax|getJSON|get|post)\("),
              "DOM library"),
)

# Structured state already present in the HTML. Every one of these is readable
# from a plain GET, which is why hydration beats driving a browser.
HYDRATION_MARKERS: tuple[Signature, ...] = (
    Signature("__NEXT_DATA__", (r'<script[^>]+id="__NEXT_DATA__"[^>]*>',), "Next.js pages router"),
    Signature("self.__next_f", (r"self\.__next_f\.push\(",), "Next.js app router, RSC stream"),
    Signature("window.__NUXT__", (r"window\.__NUXT__\s*=",), "Nuxt payload"),
    Signature("__remixContext", (r"window\.__remixContext\s*=",), "Remix loader data"),
    Signature("__INITIAL_STATE__", (r"window\.__INITIAL_STATE__\s*=",), "generic store preload"),
    Signature("__PRELOADED_STATE__", (r"window\.__PRELOADED_STATE__\s*=",), "Redux preload"),
    Signature("__APOLLO_STATE__", (r"window\.__APOLLO_STATE__\s*=",), "Apollo cache"),
    Signature("application/ld+json", (r'<script[^>]+type="application/ld\+json"',),
              "structured metadata, rarely the page data"),
)

# The Network-tab substitute: endpoints named in markup or inline script.
_ENDPOINT_PATTERNS = (
    re.compile(r"""fetch\(\s*['"]([^'"]{2,200})['"]"""),
    re.compile(r"""axios\.\w+\(\s*['"]([^'"]{2,200})['"]"""),
    re.compile(r"""\$\.(?:get|post|getJSON|ajax)\(\s*['"]([^'"]{2,200})['"]"""),
    re.compile(r"""url\s*:\s*['"]([^'"]{2,200})['"]"""),
    re.compile(r"""(?:data-(?:url|endpoint|api|src))\s*=\s*['"]([^'"]{2,200})['"]"""),
    re.compile(r"""['"](/(?:api|rest|graphql|services|data|json)/[^'"\s]{1,200})['"]""", re.I),
)

_SCRIPT_SRC = re.compile(r"""<script[^>]+src\s*=\s*['"]([^'"]+)['"]""", re.I)
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script>", re.I | re.S)
_LOADING = re.compile(
    r"(loading[.\s]*(?:please wait|\.\.\.)?|please wait|fetching data|one moment)", re.I
)


@dataclass
class ProbeReport:
    url: str
    status: int = 200
    frameworks: list[tuple[str, str]] = field(default_factory=list)
    hydration: list[tuple[str, str, int]] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    loading_markers: list[str] = field(default_factory=list)
    visible_lines: int = 0
    visible_chars: int = 0
    html_bytes: int = 0
    meta: dict[str, str] = field(default_factory=dict)
    strategy: Strategy = Strategy.STATIC
    reasons: list[str] = field(default_factory=list)
    alternatives: list[tuple[str, str]] = field(default_factory=list)

    @property
    def text_ratio(self) -> float:
        """Share of the payload that is readable text once scripts are gone.

        A page whose values are filled in later has plenty of markup and script
        but little to read, so a low ratio is the clearest no-JS signal of
        client-side rendering.
        """
        return self.visible_chars / self.html_bytes if self.html_bytes else 0.0


def _find(signatures, haystack: str):
    hits = []
    for sig in signatures:
        for pattern in sig.patterns:
            if re.search(pattern, haystack, re.I):
                hits.append(sig)
                break
    return hits


def _hydration_payload_size(html: str, marker: Signature) -> int:
    """Rough size of the embedded state, as a proxy for whether it holds data."""
    total = 0
    for block in _SCRIPT_BLOCK.findall(html):
        if any(re.search(p, block, re.I) for p in marker.patterns):
            total += len(block)
    if not total and marker.name == "__NEXT_DATA__":
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            total = len(m.group(1))
    return total


def _endpoints(html: str, base_url: str) -> list[str]:
    found: set[str] = set()
    origin = urlparse(base_url)
    for block in [*_SCRIPT_BLOCK.findall(html), html]:
        for pattern in _ENDPOINT_PATTERNS:
            for hit in pattern.findall(block):
                hit = hit.strip()
                if not hit or hit.startswith(("data:", "javascript:", "#", "mailto:")):
                    continue
                if hit.endswith((".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2")):
                    continue
                if hit.startswith("//"):
                    hit = f"{origin.scheme or 'https'}:{hit}"
                found.add(hit)
    return sorted(found)[:40]


# Structured data beats text for a verification tool, because a field diff
# names the field that moved instead of handing back a hunk to interpret.
# Hydration outranks a data endpoint because it needs no extra configuration
# and rides the same request.
_PREFERENCE = (Strategy.HYDRATION, Strategy.JSON, Strategy.STATIC, Strategy.BROWSER)

# Below this a payload is a config blob or a feature flag, not page data.
MIN_HYDRATION_BYTES = 100


def _decide(report: ProbeReport) -> tuple[Strategy, list[str], list[tuple[str, str]]]:
    """Rank every viable strategy rather than forcing a single answer.

    A server-rendered single page app genuinely supports more than one: the
    values are readable as text and also present as structured state. Saying so
    is more useful than picking one and hiding the other.
    """
    viable: list[tuple[Strategy, str]] = []

    payloads = [
        h for h in report.hydration
        if h[2] >= MIN_HYDRATION_BYTES and "ld+json" not in h[0]
    ]
    if payloads:
        names = ", ".join(f"{h[0]} (~{h[2]:,}b)" for h in payloads)
        viable.append((Strategy.HYDRATION,
                       f"embedded state in the served HTML: {names}"))

    thin_text = report.text_ratio < 0.05
    if report.endpoints and (thin_text or report.loading_markers or not report.visible_lines):
        viable.append((Strategy.JSON,
                       f"{len(report.endpoints)} candidate data endpoint(s) referenced"))

    # A loading placeholder means the visible text is a stand-in for values that
    # have not arrived, so text volume alone does not make static viable here.
    if report.visible_lines and not thin_text and not report.loading_markers:
        viable.append((Strategy.STATIC,
                       f"{report.visible_lines} lines of visible text already served "
                       f"({report.text_ratio:.1%} of the payload)"))

    if not viable:
        reasons = []
        if report.loading_markers:
            reasons.append(f"loading placeholder served: {report.loading_markers[0]!r}")
        reasons.append(
            f"only {report.visible_chars} chars of visible text in "
            f"{report.html_bytes} bytes ({report.text_ratio:.1%})"
        )
        reasons.append("no embedded state and no data endpoint found, so JS must run")
        return Strategy.BROWSER, reasons, []

    viable.sort(key=lambda item: _PREFERENCE.index(item[0]))
    primary, why = viable[0]

    reasons = [why]
    if report.loading_markers and primary is not Strategy.STATIC:
        reasons.append(f"loading placeholder served: {report.loading_markers[0]!r}")

    alternatives = [(s.value, r) for s, r in viable[1:]]
    return primary, reasons, alternatives


def probe(html: str, url: str, profile: Profile,
          response: Response | None = None) -> ProbeReport:
    """Analyse one page and recommend a capture strategy."""
    report = ProbeReport(url=url, html_bytes=len(html.encode("utf-8", "replace")))
    if response is not None:
        report.status = response.status
        report.meta = response.metadata(profile.compare_headers or ["server", "content-type"])

    report.frameworks = [(s.name, s.note) for s in _find(FRAMEWORKS, html)]
    report.hydration = [
        (s.name, s.note, _hydration_payload_size(html, s))
        for s in _find(HYDRATION_MARKERS, html)
    ]
    report.endpoints = _endpoints(html, url)
    report.scripts = sorted(set(_SCRIPT_SRC.findall(html)))[:25]

    try:
        page = extract_html(html, profile, side="probe", label=url, allow_unrendered=True)
        report.visible_lines = len(page.lines)
        report.visible_chars = sum(len(line) for line in page.lines)
    except Exception:
        report.visible_lines = 0
        report.visible_chars = 0

    report.loading_markers = sorted({m.strip() for m in _LOADING.findall(html)})[:5]
    report.strategy, report.reasons, report.alternatives = _decide(report)
    return report


def suggested_profile(report: ProbeReport) -> str:
    """A profile fragment the team can paste and edit."""
    parsed = urlparse(report.url)
    # Probing a saved file yields no scheme or host, so fall back to a
    # placeholder rather than emitting a malformed base_url.
    host = parsed.hostname or "example.com"
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    lines = [
        "[[source]]",
        'name = "candidate"',
        f'base_url = "{scheme}://{host}"',
        f'# capture = "{report.strategy.value}"',
    ]
    if report.loading_markers:
        rendered = json.dumps(report.loading_markers[:2])
        lines += ["", "[extract]", f"render_sentinels = {rendered}"]
    lines += ["", "[security]", f'allow_hosts = ["{host}"]']
    return "\n".join(lines)
