"""Running a comparison across many entities and aggregating the result.

Results go to SQLite, on disk, next to the profile. A few thousand rows needs
no server, and keeping storage local preserves the property the whole security
posture rests on: this tool talks to the endpoints you configured and nothing
else.
"""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .accept import AcceptStore, apply_accepted
from .capture import Capture, Kind, from_response
from .config import Profile
from .engine import Status, compare, compare_structured
from .errors import CaptureError, MixedPayloadError, SiteDiffError
from .extract import Page, extract_html
from .pair import PairPlan, discover_keys
from .source import fetch

DEFAULT_DB = Path(".sitediff") / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  profile TEXT NOT NULL,
  entities INTEGER NOT NULL DEFAULT 0,
  clean INTEGER NOT NULL DEFAULT 0,
  drift INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  accepted INTEGER NOT NULL DEFAULT 0,
  seconds REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS results (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  key TEXT NOT NULL,
  status TEXT NOT NULL,
  added INTEGER NOT NULL DEFAULT 0,
  removed INTEGER NOT NULL DEFAULT 0,
  altered INTEGER NOT NULL DEFAULT 0,
  accepted INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS results_status ON results (run_id, status);
"""


@dataclass
class EntityResult:
    key: str
    status: str
    added: int = 0
    removed: int = 0
    altered: int = 0
    accepted: int = 0
    detail: str = ""

    @property
    def changed(self) -> int:
        return self.added + self.removed + self.altered


@dataclass
class RunReport:
    profile: str
    keys: list[str] = field(default_factory=list)
    results: list[EntityResult] = field(default_factory=list)
    discovery_failures: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0
    run_id: int | None = None
    db_path: str = ""

    @property
    def clean(self) -> int:
        return sum(1 for r in self.results if r.status == Status.CLEAN.value)

    @property
    def drift(self) -> int:
        return sum(1 for r in self.results if r.status == Status.DRIFT.value)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def accepted(self) -> int:
        return sum(r.accepted for r in self.results)

    @property
    def drifted(self) -> list[EntityResult]:
        return [r for r in self.results if r.status != Status.CLEAN.value]


def _compare_one(key: str, plan: PairPlan, profile: Profile,
                 store: AcceptStore) -> EntityResult:
    left_path, right_path = plan.url_paths(key)
    policy = profile.egress_policy()
    sides = []

    for name, path, side in ((plan.left, left_path, "left"),
                             (plan.right, right_path, "right")):
        source = profile.source(name)
        url = source.url_for(path)
        # The key is templated into the body too, so POST endpoints that take
        # the entity id in a payload rather than a query string still pair.
        body = source.body.replace("{key}", key) if source.body else ""
        response = fetch(url, source, policy, body=body)
        # A non-2xx is not a comparable page. Left unchecked, two 404 bodies
        # compare equal and an entity that exists on neither side is reported
        # as matching, which is the opposite of what a verification run means.
        if not 200 <= response.status < 300:
            raise CaptureError(
                f"{side} side returned {response.status} {response.reason} for {url}. "
                f"The entity could not be compared."
            )
        payload = from_response(response, Capture(source.capture), url,
                                marker=source.hydration_marker,
                                headers=profile.compare_headers)
        sides.append((payload, side, url))

    (left_payload, _, left_url), (right_payload, _, right_url) = sides
    if left_payload.kind is not right_payload.kind:
        raise MixedPayloadError(left_payload.kind.value, right_payload.kind.value)

    def as_page(payload, side, url):
        if payload.kind is Kind.TEXT:
            return extract_html(payload.html, profile.for_side(side), side=side,
                                label=url, meta=payload.meta)
        return Page(path=url, side=side, lines=[], meta=payload.meta)

    left = as_page(left_payload, "left", left_url)
    right = as_page(right_payload, "right", right_url)

    if left_payload.kind is Kind.DATA:
        result = compare_structured(left, right, profile,
                                    left_payload.data, right_payload.data)
    else:
        result = compare(left, right, profile)

    apply_accepted(result, store)

    detail = ""
    if result.structured and result.field_changes:
        detail = "; ".join(
            f"{c.op.value} {c.path}: {c.left or '(absent)'} -> {c.right or '(absent)'}"
            for c in result.field_changes[:10]
        )
    elif result.hunks:
        detail = "; ".join(
            f"{ln.op.value} {ln.text[:80]}"
            for h in result.hunks for ln in h.lines
            if ln.op.value in ("add", "remove")
        )[:2000]

    return EntityResult(
        key=key, status=result.status.value, added=result.added,
        removed=result.removed, altered=result.altered,
        accepted=result.accepted, detail=detail,
    )


def execute(profile: Profile, plan: PairPlan, store: AcceptStore,
            limit: int = 0, db_path: str | Path | None = None,
            on_result=None) -> RunReport:
    """Compare every paired entity and aggregate the outcome."""
    started = time.monotonic()
    report = RunReport(profile=profile.name)

    discovered = discover_keys(plan, profile.sources, profile.egress_policy())
    report.discovery_failures = discovered.failures
    keys = discovered.keys
    if limit:
        keys = keys[:limit]
    report.keys = keys

    def one(key: str) -> EntityResult:
        if plan.delay_ms:
            time.sleep(plan.delay_ms / 1000)
        try:
            return _compare_one(key, plan, profile, store)
        except SiteDiffError as exc:
            return EntityResult(key=key, status="error",
                                detail=str(exc).splitlines()[0])
        except Exception as exc:
            # One unexpected failure must not discard a run of thousands. The
            # exception type is recorded so a real defect stays visible rather
            # than blending into ordinary fetch failures.
            return EntityResult(key=key, status="error",
                                detail=f"{type(exc).__name__}: {exc}"[:300])

    if plan.workers > 1:
        with ThreadPoolExecutor(max_workers=plan.workers) as pool:
            for result in pool.map(one, keys):
                report.results.append(result)
                if on_result:
                    on_result(result)
    else:
        for key in keys:
            result = one(key)
            report.results.append(result)
            if on_result:
                on_result(result)

    report.seconds = time.monotonic() - started
    report.db_path = str(store_run(report, db_path))
    return report


def store_run(report: RunReport, db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        cursor = conn.execute(
            "INSERT INTO runs (started_at, profile, entities, clean, drift, failed,"
            " accepted, seconds) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(UTC).isoformat(timespec="seconds"), report.profile,
             len(report.results), report.clean, report.drift, report.failed,
             report.accepted, round(report.seconds, 2)),
        )
        run_id = cursor.lastrowid
        report.run_id = run_id
        conn.executemany(
            "INSERT OR REPLACE INTO results (run_id, key, status, added, removed,"
            " altered, accepted, detail) VALUES (?,?,?,?,?,?,?,?)",
            [(run_id, r.key, r.status, r.added, r.removed, r.altered, r.accepted,
              r.detail) for r in report.results],
        )
    return path


def recent_runs(db_path: str | Path | None = None, limit: int = 10) -> list[dict]:
    path = Path(db_path) if db_path else DEFAULT_DB
    if not path.is_file():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
