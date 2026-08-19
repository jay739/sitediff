"""Reading a browser HAR export to find a site's data endpoints.

Working out how an application fetches its values is the slowest part of
onboarding a site, and it is usually done by hand: open devtools, click every
tab, read each request, guess which response carries the data. A HAR already
contains all of that. This reads one and answers the questions directly.

Nothing here touches the network. A HAR is a local file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .errors import InputError

# A response carrying real values looks different from one carrying chrome:
# it has numbers in it. This is a heuristic, and it is reported as evidence
# rather than as a verdict.
_DECIMAL = re.compile(r"\d+\.\d+")
_NOISE_HOSTS = ("google-analytics.com", "googletagmanager.com", "gstatic.com",
                "doubleclick.net", "fontawesome.com", "cloudflare.com")
_ASSET = re.compile(r"\.(js|css|png|jpe?g|gif|svg|woff2?|ico|map)(\?|$)", re.I)


class InvalidHarError(InputError):
    def __init__(self, path: str, problem: str):
        super().__init__(
            f"{path} is not a usable HAR file: {problem}\n"
            f"Export one from the browser's Network panel: right-click the "
            f"request list, then 'Save all as HAR with content'. The 'with "
            f"content' part matters, since response bodies are the point."
        )


@dataclass
class Endpoint:
    """One distinct request seen in the capture."""

    method: str
    url: str
    mime: str = ""
    body: str = ""
    response_mime: str = ""
    response_bytes: int = 0
    decimals: int = 0
    calls: int = 1
    sample: str = ""

    @property
    def path(self) -> str:
        return urlparse(self.url).path

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    @property
    def carries_data(self) -> bool:
        """Evidence that this response holds values rather than layout."""
        return self.decimals >= 10

    @property
    def params(self) -> dict[str, str]:
        if not self.body:
            return {}
        if self.mime and "json" in self.mime:
            try:
                parsed = json.loads(self.body)
                return {k: str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
            except ValueError:
                return {}
        return {k: v[0] for k, v in parse_qs(self.body, keep_blank_values=True).items()}


@dataclass
class HarReport:
    path: str = ""
    endpoints: list[Endpoint] = field(default_factory=list)
    skipped_assets: int = 0
    skipped_third_party: int = 0
    missing_bodies: int = 0

    @property
    def hosts(self) -> list[str]:
        return sorted({e.host for e in self.endpoints if e.host})

    @property
    def data_endpoints(self) -> list[Endpoint]:
        return sorted([e for e in self.endpoints if e.carries_data],
                      key=lambda e: -e.decimals)


def load(path: str | Path) -> HarReport:
    """Parse a HAR and summarise the endpoints worth caring about."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        raise InvalidHarError(str(p), f"not valid JSON ({exc})") from exc
    except OSError as exc:
        raise InvalidHarError(str(p), str(exc)) from exc

    entries = (raw.get("log") or {}).get("entries")
    if not isinstance(entries, list):
        raise InvalidHarError(str(p), "no log.entries array")

    report = HarReport(path=str(p))
    seen: dict[tuple[str, str, str], Endpoint] = {}

    for entry in entries:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        url = request.get("url") or ""
        if not url:
            continue
        host = urlparse(url).hostname or ""
        if any(noise in host for noise in _NOISE_HOSTS):
            report.skipped_third_party += 1
            continue
        if _ASSET.search(url):
            report.skipped_assets += 1
            continue

        content = response.get("content") or {}
        text = content.get("text") or ""
        if not text:
            report.missing_bodies += 1

        post = request.get("postData") or {}
        body = post.get("text") or ""
        # Keyed by endpoint rather than by payload: the same view fetched twice
        # with slightly different parameters is one endpoint, and listing it
        # twice buries the others. The richest response is kept, since that is
        # the one that demonstrates what the endpoint returns.
        key = (request.get("method", "GET"), url.split("?")[0])

        if key in seen:
            existing = seen[key]
            existing.calls += 1
            if len(text) <= existing.response_bytes:
                continue

        endpoint = Endpoint(
            method=request.get("method", "GET"),
            url=url,
            mime=post.get("mimeType", ""),
            body=body,
            response_mime=(content.get("mimeType") or "").split(";")[0],
            response_bytes=len(text),
            decimals=len(_DECIMAL.findall(text)),
            sample=text[:200].replace("\n", " "),
        )
        if key in seen:
            endpoint.calls = seen[key].calls
        seen[key] = endpoint

    report.endpoints = sorted(seen.values(), key=lambda e: -e.decimals)
    return report


def suggest_profile(report: HarReport, template_keys: dict[str, str] | None = None) -> str:
    """A profile fragment wiring up the endpoints that appear to carry data."""
    data = report.data_endpoints
    if not data:
        return ("# No response in this capture carried enough numbers to look\n"
                "# like data. Re-export with 'Save all as HAR with content', and\n"
                "# make sure Preserve log was on while you clicked through.\n")

    host = data[0].host
    replacements = template_keys or {}
    lines = [
        "[[source]]",
        'name = "legacy"',
        f'base_url = "https://{host}"',
        'capture = "static"   # these endpoints return HTML, not JSON',
        'method = "POST"',
        "",
        "[pair]",
        'left = "legacy"',
        'right = "rewrite"',
        "",
    ]
    for endpoint in data[:8]:
        name = endpoint.path.strip("/").replace("/", "-").lower() or "page"
        body = endpoint.body
        for value, placeholder in replacements.items():
            body = body.replace(value, placeholder)
        lines += [
            "[[pair.page]]",
            f'name = "{name}"',
            f'left_path = "{endpoint.path}"',
            'right_path = "/CHANGE-ME/{key}"',
            f"left_body = '{body}'",
            "",
        ]
    lines += ["[security]", f'allow_hosts = ["{host}"]']
    return "\n".join(lines)
