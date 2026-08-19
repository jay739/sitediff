"""Integration tests for acquisition, run against a real local HTTP server."""

import pathlib
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.config import Profile, Rule
from sitediff.engine import compare
from sitediff.errors import EgressBlockedError, FetchError, TooManyRedirectsError
from sitediff.extract import extract_html
from sitediff.source import EgressPolicy, Source, fetch

PAGE = "<html><body><main><p>Value {v}</p></main></body></html>"


class Handler(BaseHTTPRequestHandler):
    pages: ClassVar[dict] = {}

    def do_GET(self):
        if self.path.startswith("/redirect"):
            n = int(self.path.rsplit("/", 1)[-1] or 0)
            self.send_response(302)
            self.send_header("Location", f"/redirect/{n + 1}")
            self.end_headers()
            return
        if self.path == "/away":
            self.send_response(302)
            self.send_header("Location", "http://evil.example.net/steal")
            self.end_headers()
            return
        body = self.pages.get(self.path, PAGE.format(v="default")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Server", "test-origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    Handler.pages = {
        "/": PAGE.format(v="42"),
        "/other": PAGE.format(v="99"),
        "/secret": "<html><body><main><p>token: abc123XYZ deadbeef</p></main></body></html>",
    }
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address
    srv.shutdown()


def local(server, path="/"):
    host, port = server
    return f"http://{host}:{port}{path}"


def allow(server):
    return EgressPolicy([server[0]])


# --- egress ------------------------------------------------------------------

def test_unlisted_host_is_refused(server):
    with pytest.raises(EgressBlockedError) as exc:
        fetch(local(server), Source("s"), EgressPolicy(["example.com"]))
    assert "not in the egress allowlist" in str(exc.value)


def test_empty_policy_blocks_everything(server):
    with pytest.raises(EgressBlockedError):
        fetch(local(server), Source("s"), EgressPolicy([]))


def test_redirect_to_unlisted_host_is_refused(server):
    """The sneaky exfiltration path: an allowed host redirecting elsewhere."""
    with pytest.raises(EgressBlockedError) as exc:
        fetch(local(server, "/away"), Source("s"), allow(server))
    assert "evil.example.net" in str(exc.value)
    assert "reached via" in str(exc.value)


def test_connect_to_override_needs_its_own_authorisation(server):
    src = Source("s", connect_to="10.99.99.99")
    with pytest.raises(EgressBlockedError):
        fetch(local(server), src, allow(server))


def test_suffix_match_does_not_allow_lookalike_domains():
    """A leading dot matches subdomains and the apex, never a lookalike that
    merely starts with the allowed name."""
    p = EgressPolicy([".example.com"])
    assert p.permits("app.example.com")
    assert p.permits("deep.nested.example.com")
    assert p.permits("example.com")
    assert not p.permits("example.com.attacker.net")
    assert not p.permits("notexample.com")
    assert not p.permits("example.com.evil")


# --- fetching and metadata ---------------------------------------------------

def test_fetch_returns_body_and_metadata(server):
    r = fetch(local(server), Source("s"), allow(server))
    assert r.status == 200
    assert "Value 42" in r.body
    assert r.resolved_ip == "127.0.0.1"
    meta = r.metadata(["server"])
    assert meta["status"] == "200 OK"
    assert meta["header:server"] == "test-origin"


def test_connect_to_preserves_host_header(server):
    """Pinning must keep the original Host, which is what makes region and
    origin-behind-CDN fetches address the right virtual host."""
    host, port = server
    src = Source("s", connect_to=host)
    r = fetch(f"http://{host}:{port}/other", src, EgressPolicy([host]))
    assert "Value 99" in r.body


def test_redirect_limit(server):
    src = Source("s", max_redirects=3)
    with pytest.raises(TooManyRedirectsError) as exc:
        fetch(local(server, "/redirect/0"), src, allow(server))
    assert "exceeded 3 redirects" in str(exc.value)


def test_unreachable_port_reports_actionable_error():
    with pytest.raises(FetchError) as exc:
        fetch("http://127.0.0.1:1/", Source("s", timeout=2), EgressPolicy(["127.0.0.1"]))
    assert "Could not fetch" in str(exc.value)


# --- redaction ---------------------------------------------------------------

def test_redaction_strips_secrets_before_they_reach_a_report(server):
    profile = Profile(redact=[Rule(re.compile(r"token:\s*\S+"), "[REDACTED]")])
    r = fetch(local(server, "/secret"), Source("s"), allow(server))
    page = extract_html(r.body, profile, meta=r.metadata())
    assert not any("abc123XYZ" in line for line in page.lines)
    assert any("[REDACTED]" in line for line in page.lines)


# --- metadata drift ----------------------------------------------------------

def test_identical_content_from_different_upstream_is_drift(server):
    """The reverse proxy failure that every status check reports as green."""
    profile = Profile()
    body = "<html><body><main><p>same</p></main></body></html>"
    left = extract_html(body, profile, side="left", meta={"resolved_ip": "10.0.0.101"})
    right = extract_html(body, profile, side="right", meta={"resolved_ip": "10.0.0.7"})
    result = compare(left, right, profile)
    assert result.status.value == "drift"
    assert not result.hunks
    assert [(c.key, c.left, c.right) for c in result.meta_changes] == [
        ("resolved_ip", "10.0.0.101", "10.0.0.7")
    ]
