"""How a page's values are obtained.

Four strategies, chosen per source. Three of them need no browser, which is the
point: driving a headless browser is the expensive last resort, not the default.

    static      Values are already in the served HTML.
    hydration   Structured state is embedded in a script tag.
    json        The response body is the data.
    browser     The settled DOM after JavaScript runs. Optional extra.

`sitediff probe` recommends which one a site needs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from .errors import CaptureError, UnsupportedCaptureError
from .source import Response


class Capture(StrEnum):
    STATIC = "static"
    HYDRATION = "hydration"
    JSON = "json"
    BROWSER = "browser"


class Kind(StrEnum):
    TEXT = "text"
    DATA = "data"


@dataclass
class Payload:
    """What a capture produced, and how.

    Text payloads go through extraction and a line diff. Data payloads are
    compared field by field, which names the field that moved instead of
    handing back a hunk to interpret.
    """

    kind: Kind
    label: str
    capture: Capture
    html: str = ""
    data: object = None
    meta: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# Script tags whose contents are reliably JSON rather than a JS expression.
_JSON_SCRIPT = re.compile(
    r"""<script[^>]*\btype\s*=\s*['"]application/json['"][^>]*>(.*?)</script>""",
    re.I | re.S,
)
_IDENTIFIED_SCRIPT = re.compile(
    r"""<script[^>]*\bid\s*=\s*['"]([\w:.-]+)['"][^>]*>(.*?)</script>""", re.I | re.S
)
# window.__X__ = {...};  the right hand side is often, though not always, JSON.
_ASSIGNMENT = re.compile(
    r"""(?:window|self|globalThis)\.(__[A-Z_]+__)\s*=\s*(\{.*?\}|\[.*?\])\s*[;<]""",
    re.S,
)

PREFERRED_MARKERS = ("__NEXT_DATA__", "__INITIAL_STATE__", "__PRELOADED_STATE__",
                     "__NUXT__", "__APOLLO_STATE__")


def _try_json(text: str):
    try:
        return json.loads(text.strip())
    except (ValueError, TypeError):
        return None


def extract_hydration(html: str, marker: str = "") -> tuple[object, str]:
    """Pull embedded state out of the HTML.

    Returns the parsed value and the marker it came from. A specific marker can
    be named when a page carries several, or when the automatic order picks the
    wrong one.
    """
    candidates: list[tuple[str, str]] = []

    for found_id, body in _IDENTIFIED_SCRIPT.findall(html):
        candidates.append((found_id, body))
    for body in _JSON_SCRIPT.findall(html):
        candidates.append(("application/json", body))
    for name, body in _ASSIGNMENT.findall(html):
        candidates.append((name, body))

    if marker:
        candidates = [c for c in candidates if c[0] == marker]
        if not candidates:
            raise CaptureError(
                f"No embedded state named {marker!r} in this page.\n"
                f"Run 'sitediff probe' against it to see what it does carry."
            )

    def rank(item: tuple[str, str]) -> int:
        name = item[0]
        return PREFERRED_MARKERS.index(name) if name in PREFERRED_MARKERS else len(
            PREFERRED_MARKERS
        )

    for name, body in sorted(candidates, key=rank):
        parsed = _try_json(body)
        if parsed is not None:
            return parsed, name

    if not candidates:
        raise CaptureError(
            "No embedded state found in this page.\n"
            "Run 'sitediff probe' to see which capture strategy it needs; if it "
            "reported 'static', compare the HTML instead."
        )

    names = ", ".join(sorted({c[0] for c in candidates}))
    raise CaptureError(
        f"Found embedded state ({names}) but none of it parsed as JSON.\n"
        f"Framework payloads such as the Next.js app router RSC stream are a "
        f"JavaScript expression, not JSON. Name a specific marker with "
        f"hydration_marker, or capture this side with 'browser'."
    )


def from_response(response: Response, capture: Capture, label: str,
                  marker: str = "", headers: list[str] | None = None) -> Payload:
    """Turn a fetched response into a comparable payload."""
    meta = response.metadata(headers or [])

    if capture is Capture.STATIC:
        payload = Payload(Kind.TEXT, label, capture, html=response.body, meta=meta)
        payload.notes.extend(_static_misuse(response.body, label))
        return payload

    if capture is Capture.JSON:
        parsed = _try_json(response.body)
        if parsed is None:
            ctype = response.headers.get("content-type", "unknown")
            preview = response.body.strip()[:120].replace("\n", " ")
            raise CaptureError(
                f"{label} did not return JSON (content-type: {ctype}).\n"
                f"First bytes: {preview!r}\n"
                f"If this endpoint needs a session, fetch the page first so its "
                f"cookies are established, or set capture to 'static'."
            )
        return Payload(Kind.DATA, label, capture, data=parsed, meta=meta)

    if capture is Capture.HYDRATION:
        data, found = extract_hydration(response.body, marker)
        meta = {**meta, "hydration_marker": found}
        return Payload(Kind.DATA, label, capture, data=data, html=response.body, meta=meta)

    raise UnsupportedCaptureError(capture.value)


def from_browser(url: str, label: str, options=None,
                 meta: dict[str, str] | None = None) -> Payload:
    """Render a URL in a real browser and capture the settled DOM."""
    from .browser import render

    html = render(url, options)
    payload = Payload(Kind.TEXT, label, Capture.BROWSER, html=html,
                      meta={**(meta or {}), "capture": "browser"})
    return payload


def _static_misuse(body: str, label: str) -> list[str]:
    """Catch the obvious mistake: JSON captured as text.

    Text capture of a JSON body does not fail, it silently produces a diff of
    the serialised document, which looks like output but answers nothing. The
    tool cannot know the intent, so it says so rather than guessing.
    """
    stripped = body.lstrip()
    if stripped[:1] in ("{", "[") and _try_json(body) is not None:
        return [
            f"{label} parses as JSON but was captured as text. "
            f"Set capture = \"json\" on this source to compare it field by field."
        ]
    return []


def from_html(html: str, capture: Capture, label: str, marker: str = "") -> Payload:
    """Same, for HTML already on disk."""
    if capture is Capture.STATIC:
        payload = Payload(Kind.TEXT, label, capture, html=html)
        payload.notes.extend(_static_misuse(html, label))
        return payload
    if capture is Capture.HYDRATION:
        data, found = extract_hydration(html, marker)
        return Payload(Kind.DATA, label, capture, data=data, html=html,
                       meta={"hydration_marker": found})
    if capture is Capture.JSON:
        parsed = _try_json(html)
        if parsed is None:
            raise CaptureError(f"{label} is not JSON.")
        return Payload(Kind.DATA, label, capture, data=parsed)
    raise UnsupportedCaptureError(capture.value)
