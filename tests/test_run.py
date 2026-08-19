"""Entity pairing, run aggregation and the accepted-difference store."""

import json
import pathlib
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.accept import AcceptStore, apply_accepted, fingerprints_of
from sitediff.config import Profile, load_profile
from sitediff.engine import Status, compare_structured
from sitediff.errors import InvalidProfileError, PairingError
from sitediff.extract import Page
from sitediff.pair import PairPlan, discover_keys, parse_pair
from sitediff.run import execute, recent_runs

SCHOOLS = {f"40{n:02d}": 40 + n for n in range(1, 7)}
FOOTER_OLD = "(c) 2025 Example State Department of Education"
FOOTER_NEW = "(c) 2025 ESDE"


class Handler(BaseHTTPRequestHandler):
    rewritten: ClassVar[bool] = False

    def do_GET(self):
        if self.path.startswith("/list"):
            body = "".join(
                f'<a href="/Graphs/#/RecordDetail/1/E/1/01/{k}/0">s</a>' for k in SCHOOLS
            ).encode()
            self._send(body, "text/html")
            return
        match = re.search(r"id=(\d+)", self.path)
        if not match or match.group(1) not in SCHOOLS:
            self._send(b"{}", "application/json", 404)
            return
        sid = match.group(1)
        data = {"recordId": sid, "pct": SCHOOLS[sid], "footer": FOOTER_OLD}
        if self.rewritten:
            data["footer"] = FOOTER_NEW
            if sid == "4003":
                data["pct"] = None
        self._send(json.dumps(data).encode(), "application/json")

    def _send(self, body, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve(rewritten):
    handler = type("H", (Handler,), {"rewritten": rewritten})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def sites():
    left, right = serve(False), serve(True)
    yield left.server_address, right.server_address
    left.shutdown()
    right.shutdown()


@pytest.fixture
def profile(sites, tmp_path):
    (lh, lp), (rh, rp) = sites
    path = tmp_path / "p.toml"
    path.write_text(f"""
[profile]
name = "t"
[[source]]
name = "legacy"
base_url = "http://{lh}:{lp}"
capture = "json"
[[source]]
name = "rewrite"
base_url = "http://{rh}:{rp}"
capture = "json"
[pair]
left = "legacy"
right = "rewrite"
left_path = "/api?id={{key}}"
right_path = "/api?id={{key}}"
workers = 2
[pair.discover]
source = "legacy"
path = "/list"
pattern = 'RecordDetail/1/\\w/1/\\d+/(\\d{{4}})/0'
[security]
allow_hosts = ["127.0.0.1"]
""", encoding="utf-8")
    return load_profile(str(path))


# --- pairing config ----------------------------------------------------------

def test_pair_requires_a_single_capture_group():
    with pytest.raises(InvalidProfileError) as exc:
        parse_pair({"pair": {
            "left": "a", "right": "b", "left_path": "/{key}", "right_path": "/{key}",
            "discover": {"source": "a", "path": "/l", "pattern": r"(\d+)/(\w+)"},
        }}, "p.toml")
    assert "exactly one capture group" in str(exc.value)


def test_pair_requires_keys_or_discovery():
    with pytest.raises(InvalidProfileError) as exc:
        parse_pair({"pair": {"left": "a", "right": "b",
                             "left_path": "/{key}", "right_path": "/{key}"}}, "p.toml")
    assert "'keys' or a [pair.discover]" in str(exc.value)


def test_url_templates_must_carry_the_key_placeholder():
    plan = PairPlan(left="a", right="b", left_path="/fixed", right_path="/{key}",
                    keys=("1",))
    with pytest.raises(PairingError) as exc:
        plan.url_paths("1")
    assert "{key}" in str(exc.value)


# --- discovery ---------------------------------------------------------------

def test_keys_are_discovered_from_a_listing_page(profile):
    found = discover_keys(profile.pair, profile.sources, profile.egress_policy())
    assert sorted(found.keys) == sorted(SCHOOLS)


def test_discovery_naming_an_unknown_source_is_actionable(profile):
    plan = PairPlan(left="a", right="b", left_path="/{key}", right_path="/{key}",
                    discover=profile.pair.discover.__class__(
                        source="nope", path="/l", pattern=re.compile(r"(\d+)")))
    with pytest.raises(PairingError) as exc:
        discover_keys(plan, profile.sources, profile.egress_policy())
    assert "nope" in str(exc.value)


# --- run ---------------------------------------------------------------------

def test_run_compares_every_discovered_entity(profile, tmp_path):
    store = AcceptStore(path=tmp_path / "acc.toml")
    report = execute(profile, profile.pair, store, db_path=tmp_path / "r.db")
    assert len(report.results) == len(SCHOOLS)
    assert report.failed == 0
    # Every entity carries the intentional footer change, which is the problem.
    assert report.drift == len(SCHOOLS)


def test_limit_stops_early_for_a_smoke_test(profile, tmp_path):
    store = AcceptStore(path=tmp_path / "acc.toml")
    report = execute(profile, profile.pair, store, limit=2, db_path=tmp_path / "r.db")
    assert len(report.results) == 2


def test_run_is_persisted_and_listable(profile, tmp_path):
    store = AcceptStore(path=tmp_path / "acc.toml")
    db = tmp_path / "r.db"
    execute(profile, profile.pair, store, db_path=db)
    execute(profile, profile.pair, store, limit=1, db_path=db)
    runs = recent_runs(db)
    assert len(runs) == 2
    assert runs[0]["entities"] == 1 and runs[1]["entities"] == len(SCHOOLS)


def test_one_broken_entity_does_not_discard_the_run(profile, tmp_path):
    plan = PairPlan(left="legacy", right="rewrite", left_path="/api?id={key}",
                    right_path="/api?id={key}", keys=("4001", "9999"), workers=1)
    store = AcceptStore(path=tmp_path / "acc.toml")
    report = execute(profile, plan, store, db_path=tmp_path / "r.db")
    assert len(report.results) == 2
    assert report.failed == 1


# --- accepted store ----------------------------------------------------------

def left_right(pct_left, pct_right, footer_right=FOOTER_NEW):
    return ({"pct": pct_left, "footer": FOOTER_OLD},
            {"pct": pct_right, "footer": footer_right})


def result_for(a, b):
    return compare_structured(Page("l", "left", []), Page("r", "right", []),
                              Profile(), a, b)


def test_accepting_once_suppresses_the_same_finding_everywhere(tmp_path):
    """The whole point: an intentional change is reviewed once, not per entity."""
    store = AcceptStore(path=tmp_path / "acc.toml")
    first = result_for(*left_right(40, 40))
    for fp, desc in fingerprints_of(first).items():
        store.add(fp, desc, "footer shortened by design")

    other = result_for(*left_right(55, 55))
    assert apply_accepted(other, store) == 1
    assert other.status is Status.CLEAN


def test_a_real_difference_survives_acceptance_of_an_unrelated_one(tmp_path):
    store = AcceptStore(path=tmp_path / "acc.toml")
    for fp, desc in fingerprints_of(result_for(*left_right(40, 40))).items():
        store.add(fp, desc, "expected")

    broken = result_for(*left_right(42, None))
    apply_accepted(broken, store)
    assert broken.status is Status.DRIFT
    assert [c.path for c in broken.field_changes] == ["pct"]


def test_fingerprints_exclude_the_entity_so_they_generalise():
    a = fingerprints_of(result_for(*left_right(40, 40)))
    b = fingerprints_of(result_for(*left_right(99, 99)))
    assert set(a) == set(b)


def test_store_round_trips_through_toml(tmp_path):
    store = AcceptStore(path=tmp_path / "acc.toml")
    store.add("abc123", 'alter footer: "old" -> "new"', "by design")
    store.save()
    reloaded = AcceptStore.load(tmp_path / "acc.toml")
    assert "abc123" in reloaded
    assert reloaded.entries["abc123"]["reason"] == "by design"


def test_missing_store_is_empty_not_an_error(tmp_path):
    assert len(AcceptStore.load(tmp_path / "nope.toml")) == 0


def test_accepted_text_lines_are_dropped_and_empty_hunks_removed(tmp_path):
    from sitediff.engine import compare
    left = Page("l", "left", ["Header", "Old footer text here"])
    right = Page("r", "right", ["Header", "New footer text here"])
    result = compare(left, right, Profile())
    store = AcceptStore(path=tmp_path / "acc.toml")
    for fp, desc in fingerprints_of(result).items():
        store.add(fp, desc, "expected")

    again = compare(left, right, Profile())
    assert apply_accepted(again, store) == 2
    assert again.status is Status.CLEAN
    assert again.hunks == []


def test_structured_and_text_fingerprints_do_not_collide():
    field_fp = set(fingerprints_of(result_for({"a": "1"}, {"a": "2"})))
    from sitediff.engine import compare
    text = compare(Page("l", "left", ["1"]), Page("r", "right", ["2"]), Profile())
    assert not (field_fp & set(fingerprints_of(text)))


def test_accepted_entry_without_a_fingerprint_is_rejected(tmp_path):
    path = tmp_path / "acc.toml"
    path.write_text('[[accepted]]\ndescription = "x"\n', encoding="utf-8")
    with pytest.raises(InvalidProfileError) as exc:
        AcceptStore.load(path)
    assert "fingerprint" in str(exc.value)


# --- browser -----------------------------------------------------------------

def test_browser_capture_is_absent_but_honest_about_it():
    from sitediff.browser import available
    from sitediff.capture import Capture, from_html
    from sitediff.errors import UnsupportedCaptureError

    if available():
        pytest.skip("playwright installed; the unavailable path cannot be exercised")
    with pytest.raises(UnsupportedCaptureError) as exc:
        from_html("<html></html>", Capture.BROWSER, "p.html")
    assert "sitediff[browser]" in str(exc.value)
