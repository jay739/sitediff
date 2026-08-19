"""Acquiring pages over the network.

Built on the standard library alone. That is a deliberate trade: a reviewer can
read every byte this tool puts on the wire without auditing an HTTP stack, and
the dependency tree stays small enough to clear a security review cheaply.

Two capabilities exist here that an ordinary HTTP client does not give you:

  connect_to   Connect to a specific address while keeping the original Host
               header and TLS SNI. This is how you reach one region behind a
               geo-routed hostname, the origin behind a CDN, or a service by
               its tunnel address rather than its public name.

  EgressPolicy Refuse to contact any host the profile did not list, checked
               before every connection including each redirect hop.
"""

from __future__ import annotations

import gzip
import http.client
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

from .errors import EgressBlockedError, FetchError, TooManyRedirectsError

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_REDIRECTS = 5
MAX_BODY_BYTES = 64 * 1024 * 1024


class EgressPolicy:
    """Allowlist of hosts this tool may contact.

    Entries are exact hostnames, or a leading dot for a suffix match
    (".example.com" permits "a.example.com" but not "example.com.evil.net").
    An empty policy blocks everything, so a profile that configures no sources
    cannot reach the network at all.
    """

    def __init__(self, allowed: list[str] | None = None):
        # Entries and queried hosts must normalise identically. A trailing dot
        # is the valid absolute form of a hostname, so an entry carrying one
        # would otherwise fail to match its own host. Only the trailing dot is
        # stripped: a leading dot is the suffix-match marker and is meaningful.
        self.allowed = [n for n in (self._normalise(a) for a in (allowed or [])) if n]

    @staticmethod
    def _normalise(host: str) -> str:
        return host.strip().lower().rstrip(".")

    def permits(self, host: str) -> bool:
        host = self._normalise(host)
        for entry in self.allowed:
            if entry.startswith("."):
                if host.endswith(entry) or host == entry[1:]:
                    return True
            elif host == entry:
                return True
        return False

    def check(self, host: str, via: str = "") -> None:
        if not self.permits(host):
            raise EgressBlockedError(host, self.allowed, via)

    def __repr__(self) -> str:
        return f"EgressPolicy({self.allowed!r})"


@dataclass
class Source:
    """One named endpoint to fetch from."""

    name: str
    base_url: str = ""
    connect_to: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    verify_tls: bool = True
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    follow_redirects: bool = True
    # Str rather than the Capture enum, to keep this module free of imports
    # from the layers above it. config validates the value.
    capture: str = "static"
    hydration_marker: str = ""
    # Data endpoints on server-rendered stacks are frequently POST actions
    # taking a body rather than a query string, so GET alone cannot reach them.
    method: str = "GET"
    body: str = ""

    def url_for(self, path: str = "") -> str:
        if not path:
            return self.base_url
        if path.startswith(("http://", "https://")):
            return path
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")


@dataclass
class Response:
    """A fetched page plus everything about how it was fetched.

    For proxy and region checks the defect is often in this metadata rather
    than the body: the right content served from the wrong upstream, or behind
    an unexpected certificate.
    """

    url: str
    final_url: str
    status: int
    reason: str
    headers: dict[str, str]
    body: str
    resolved_ip: str = ""
    tls_issuer: str = ""
    tls_subject: str = ""
    tls_expires: str = ""
    redirects: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def metadata(self, include_headers: list[str] | None = None) -> dict[str, str]:
        """The subset worth diffing between two sides."""
        meta = {
            "status": f"{self.status} {self.reason}".strip(),
            "resolved_ip": self.resolved_ip,
        }
        if self.tls_issuer:
            meta["tls_issuer"] = self.tls_issuer
        if self.tls_expires:
            meta["tls_expires"] = self.tls_expires
        if self.redirects:
            meta["redirect_chain"] = " -> ".join(self.redirects)
        for name in include_headers or []:
            value = self.headers.get(name.lower())
            if value is not None:
                meta[f"header:{name.lower()}"] = value
        return meta


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a chosen address, with Host header and SNI left intact.

    http.client derives SNI from the connection host, so pointing it at an IP
    would break certificate validation. Overriding connect() keeps the real
    hostname for SNI and verification while the socket goes where we say.
    """

    def __init__(self, host, connect_addr, context, timeout, port=None):
        super().__init__(host, port=port, context=context, timeout=timeout)
        self._connect_addr = connect_addr
        self.peer_ip = ""
        self.peer_cert = None

    def connect(self):
        sock = socket.create_connection(
            (self._connect_addr, self.port), timeout=self.timeout
        )
        self.peer_ip = sock.getpeername()[0]
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        try:
            self.peer_cert = self.sock.getpeercert()
        except (ValueError, ssl.SSLError):
            self.peer_cert = None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, connect_addr, timeout, port=None):
        super().__init__(host, port=port, timeout=timeout)
        self._connect_addr = connect_addr
        self.peer_ip = ""
        self.peer_cert = None

    def connect(self):
        self.sock = socket.create_connection(
            (self._connect_addr, self.port), timeout=self.timeout
        )
        self.peer_ip = self.sock.getpeername()[0]


def _name_from_cert(cert: dict | None, key: str) -> str:
    if not cert:
        return ""
    parts = []
    for rdn in cert.get(key, ()):
        for attr, value in rdn:
            if attr in ("organizationName", "commonName"):
                parts.append(value)
    return ", ".join(dict.fromkeys(parts))


def _decode(raw: bytes, headers: dict[str, str]) -> str:
    encoding = headers.get("content-encoding", "").lower()
    try:
        if "gzip" in encoding:
            raw = gzip.decompress(raw)
        elif "deflate" in encoding:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        pass

    charset = "utf-8"
    ctype = headers.get("content-type", "")
    if "charset=" in ctype:
        charset = ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"')
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _single_request(url: str, source: Source, policy: EgressPolicy, via: str,
                    body: str = ""):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise FetchError(url, "no hostname in URL")

    policy.check(host, via)

    secure = parsed.scheme == "https"
    port = parsed.port or (443 if secure else 80)
    connect_addr = source.connect_to or host

    # An override is a second destination, so it needs its own authorisation.
    if source.connect_to and source.connect_to.lower() != host.lower():
        policy.check(source.connect_to, f"connect_to override for {host}")

    if secure:
        context = ssl.create_default_context()
        if not source.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        conn = _PinnedHTTPSConnection(host, connect_addr, context, source.timeout, port)
    else:
        conn = _PinnedHTTPConnection(host, connect_addr, source.timeout, port)

    path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    method = (source.method or "GET").upper()
    payload = (body or source.body or "").encode("utf-8")

    headers = {
        "Host": parsed.netloc,
        "User-Agent": "sitediff",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
        **source.headers,
    }
    if payload:
        headers.setdefault("Content-Type", "application/json")
        headers["Content-Length"] = str(len(payload))

    try:
        conn.request(method, path, body=payload or None, headers=headers)
        raw_response = conn.getresponse()
        raw_body = raw_response.read(MAX_BODY_BYTES)
        response_headers = {k.lower(): v for k, v in raw_response.getheaders()}
        status, reason = raw_response.status, raw_response.reason
        peer_ip, peer_cert = conn.peer_ip, conn.peer_cert
    except ssl.SSLCertVerificationError as exc:
        raise FetchError(
            url,
            f"TLS certificate verification failed ({exc.verify_message or exc}). "
            f"Set verify_tls = false on the source only if this endpoint is "
            f"expected to present an untrusted certificate.",
        ) from exc
    except TimeoutError as exc:
        raise FetchError(url, f"timed out after {source.timeout}s") from exc
    except (socket.gaierror, ConnectionError, OSError, http.client.HTTPException) as exc:
        raise FetchError(url, str(exc) or type(exc).__name__) from exc
    finally:
        conn.close()

    return status, reason, response_headers, raw_body, peer_ip, peer_cert


def fetch(url: str, source: Source, policy: EgressPolicy, body: str = "") -> Response:
    """Fetch a URL, following redirects with the egress policy applied to each hop."""
    started = time.monotonic()
    chain: list[str] = []
    current = url
    via = ""

    for _ in range(source.max_redirects + 1):
        status, reason, headers, raw, peer_ip, cert = _single_request(
            current, source, policy, via, body
        )

        if source.follow_redirects and status in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if location:
                chain.append(f"{status} {current}")
                nxt = urlparse(location)
                if not nxt.scheme:
                    base = urlparse(current)
                    location = urlunparse(
                        (base.scheme, base.netloc, location, "", "", "")
                        if location.startswith("/")
                        else (base.scheme, base.netloc, base.path.rsplit("/", 1)[0]
                              + "/" + location, "", "", "")
                    )
                via, current = current, location
                continue

        return Response(
            url=url,
            final_url=current,
            status=status,
            reason=reason or "",
            headers=headers,
            body=_decode(raw, headers),
            resolved_ip=peer_ip,
            tls_issuer=_name_from_cert(cert, "issuer"),
            tls_subject=_name_from_cert(cert, "subject"),
            tls_expires=(cert or {}).get("notAfter", ""),
            redirects=chain,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    raise TooManyRedirectsError(url, source.max_redirects, chain)
