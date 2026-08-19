"""Accepted differences.

Some differences are intentional. A rewrite changes the footer wording, renames
a field, drops a deprecated section. Without a way to record that decision, the
same intentional change is reported once per entity, and at a few hundred
entities it buries every finding that matters.

The store follows the baseline-file pattern that linters and SAST tools use:
each accepted finding is fingerprinted, and the file lives in the adopting
team's own repository so accepting a difference is a reviewed change with an
author and a date attached, not a click that disappears.

Fingerprints deliberately exclude the entity a finding came from. Accepting a
footer change once is meant to suppress it everywhere; that is the whole point.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .engine import DiffResult, Op
from .errors import InvalidProfileError

DEFAULT_ACCEPT_FILE = Path(".sitediff") / "accepted.toml"


def fingerprint_field(op: Op, path: str, left: str, right: str) -> str:
    raw = f"field|{op.value}|{path}|{left}|{right}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fingerprint_line(op: Op, text: str) -> str:
    raw = f"line|{op.value}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fingerprint_structure(severity: str, kind: str, label: str,
                          left: str, right: str) -> str:
    raw = f"structure|{severity}|{kind}|{label}|{left}|{right}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fingerprints_of(result: DiffResult) -> dict[str, str]:
    """Every finding in a result, as fingerprint -> human-readable description."""
    found: dict[str, str] = {}
    if result.structured:
        for change in result.field_changes:
            found[fingerprint_field(change.op, change.path, change.left, change.right)] = (
                f"{change.op.value} {change.path}: "
                f"{change.left or '(absent)'} -> {change.right or '(absent)'}"
            )
    else:
        for hunk in result.hunks:
            for line in hunk.lines:
                if line.op is Op.EQUAL:
                    continue
                found[fingerprint_line(line.op, line.text)] = (
                    f"{line.op.value} {line.text[:120]}"
                )

    # A structural change can be intentional too: a language selector that the
    # rewrite drops on purpose should be reviewed once, not on every page.
    if result.structure_report:
        for finding in result.structure_report.findings:
            key = fingerprint_structure(finding.severity, finding.kind,
                                        finding.label, finding.left, finding.right)
            found[key] = finding.message[:160]
    return found


@dataclass
class AcceptStore:
    path: Path
    entries: dict[str, dict[str, str]] = field(default_factory=dict)

    def __contains__(self, fingerprint: str) -> bool:
        return fingerprint in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def load(cls, path: str | Path | None) -> AcceptStore:
        target = Path(path) if path else DEFAULT_ACCEPT_FILE
        store = cls(path=target)
        if not target.is_file():
            return store
        try:
            data = tomllib.loads(target.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise InvalidProfileError(str(target), f"not valid TOML: {exc}") from exc

        for item in data.get("accepted", []):
            if "fingerprint" not in item:
                raise InvalidProfileError(
                    str(target), "every [[accepted]] entry needs a 'fingerprint'"
                )
            store.entries[item["fingerprint"]] = {
                "description": item.get("description", ""),
                "reason": item.get("reason", ""),
                "accepted_on": item.get("accepted_on", ""),
            }
        return store

    def add(self, fingerprint: str, description: str, reason: str) -> bool:
        if fingerprint in self.entries:
            return False
        self.entries[fingerprint] = {
            "description": description,
            "reason": reason,
            "accepted_on": date.today().isoformat(),
        }
        return True

    def render(self) -> str:
        lines = [
            "# Differences reviewed and accepted as intentional.",
            "# Written by 'sitediff accept'. Keep this file in version control:",
            "# accepting a difference should be a reviewed change with an author.",
            "",
        ]
        for fp, meta in sorted(self.entries.items(), key=lambda kv: kv[1].get(
                "accepted_on", "")):
            lines.append("[[accepted]]")
            lines.append(f'fingerprint = "{fp}"')
            lines.append(f'description = {_toml_str(meta.get("description", ""))}')
            if meta.get("reason"):
                lines.append(f'reason = {_toml_str(meta["reason"])}')
            lines.append(f'accepted_on = "{meta.get("accepted_on", "")}"')
            lines.append("")
        return "\n".join(lines)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(), encoding="utf-8")
        return self.path


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def apply_accepted(result: DiffResult, store: AcceptStore) -> int:
    """Drop accepted findings from a result, returning how many were suppressed."""
    if not store.entries:
        return 0

    suppressed = 0
    if result.structured:
        kept = []
        for change in result.field_changes:
            fp = fingerprint_field(change.op, change.path, change.left, change.right)
            if fp in store:
                suppressed += 1
            else:
                kept.append(change)
        result.field_changes = kept
        result.removed = sum(1 for c in kept if c.op is Op.REMOVE)
        result.added = sum(1 for c in kept if c.op is Op.ADD)
        result.altered = sum(1 for c in kept if c.op is Op.ALTER)
    else:
        for hunk in result.hunks:
            kept_lines = []
            for line in hunk.lines:
                if line.op is not Op.EQUAL and fingerprint_line(line.op, line.text) in store:
                    suppressed += 1
                    if line.op is Op.ADD:
                        result.added -= 1
                    else:
                        result.removed -= 1
                    continue
                kept_lines.append(line)
            hunk.lines = kept_lines
        # A hunk left with only context lines has nothing left to report.
        result.hunks = [h for h in result.hunks
                        if any(ln.op is not Op.EQUAL for ln in h.lines)]

    if result.structure_report:
        kept_findings = []
        for finding in result.structure_report.findings:
            key = fingerprint_structure(finding.severity, finding.kind,
                                        finding.label, finding.left, finding.right)
            if key in store:
                suppressed += 1
            else:
                kept_findings.append(finding)
        result.structure_report.findings = kept_findings

    result.accepted = suppressed
    return suppressed
