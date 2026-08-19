"""Comparison engine: turn two extracted pages into a structured drift report.

Kept deliberately free of any I/O or presentation so the same result object
feeds the terminal renderer, the HTML report, and any future API.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import StrEnum

from .config import Mode, Profile
from .extract import Page


class Op(StrEnum):
    EQUAL = "equal"
    ADD = "add"
    REMOVE = "remove"
    # Structured comparison only: the field exists on both sides but its value
    # moved. Text diffs express the same thing as a remove plus an add.
    ALTER = "alter"


class Status(StrEnum):
    CLEAN = "clean"
    DRIFT = "drift"


@dataclass(frozen=True)
class DiffLine:
    op: Op
    text: str
    left_no: int | None = None
    right_no: int | None = None


@dataclass
class Hunk:
    left_start: int
    right_start: int
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def left_len(self) -> int:
        return sum(1 for ln in self.lines if ln.op in (Op.EQUAL, Op.REMOVE))

    @property
    def right_len(self) -> int:
        return sum(1 for ln in self.lines if ln.op in (Op.EQUAL, Op.ADD))

    @property
    def header(self) -> str:
        return (
            f"@@ -{self.left_start},{self.left_len} "
            f"+{self.right_start},{self.right_len} @@"
        )


@dataclass
class MetaChange:
    key: str
    left: str
    right: str


@dataclass
class DiffResult:
    left: Page
    right: Page
    hunks: list[Hunk] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    ignored: int = 0
    noise_suppressed: int = 0
    similarity: float = 1.0
    mode: Mode = Mode.VALUES
    meta_changes: list[MetaChange] = field(default_factory=list)
    field_changes: list[FieldChange] = field(default_factory=list)
    altered: int = 0
    accepted: int = 0

    @property
    def structured(self) -> bool:
        return bool(self.field_changes) or self.altered > 0

    @property
    def status(self) -> Status:
        if self.added or self.removed or self.altered or self.meta_changes:
            return Status.DRIFT
        return Status.CLEAN

    @property
    def changed(self) -> int:
        return self.added + self.removed + self.altered


# In shape mode the two sides read different databases, so every value differs
# legitimately. Masking them leaves labels, fields and structure, which is what
# a staging-against-production check is actually asserting.
_VALUE = re.compile(
    r"\d[\d,.]*\s*%"          # percentages
    r"|[$\u00a3\u20ac]\s*\d[\d,.]*"  # currency
    r"|\b\d[\d,.:/-]*\b"      # bare numbers, dates, times
)


def shape_of(line: str) -> str:
    return _VALUE.sub("\u2022", line)


def _compare_meta(left: Page, right: Page) -> list[MetaChange]:
    changes = []
    for key in sorted(set(left.meta) | set(right.meta)):
        a, b = left.meta.get(key, ""), right.meta.get(key, "")
        if a != b:
            changes.append(MetaChange(key, a, b))
    return changes


def _filter(lines: list[str], profile: Profile) -> tuple[list[str], int]:
    """Drop lines the profile marks as never worth reporting."""
    if not profile.ignore_patterns:
        return list(lines), 0
    kept = [ln for ln in lines if not any(p.search(ln) for p in profile.ignore_patterns)]
    return kept, len(lines) - len(kept)


_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")

# A template is only trustworthy if enough literal text survives the masking.
# If nearly everything varied, the resulting pattern would match almost any
# line and would silently swallow real drift. This guards against degenerate
# templates only; a line whose own label is specific (an attendance figure that
# genuinely moves between two captures of the same page) is correctly
# suppressed, because such a value cannot be trusted in the first place.
MIN_SKELETON_CHARS = 8


# Inside a span that held still across both captures, a bare number or an
# alphanumeric identifier is still volatile in general: two captures taken
# seconds apart share a date and an hour, but the side being compared will not.
_VOLATILE = re.compile(r"(?P<alnum>\b(?=\w*\d)(?=\w*[A-Za-z])\w{2,}\b)|(?P<num>\d+)")


def _tokens(line: str) -> list[str]:
    return _TOKEN.findall(line)


def _widen(chunk: str) -> tuple[str, int]:
    """Turn a stable span into regex source, widening anything numeric.

    Returns the source plus the count of genuinely literal letters, which is
    what the skeleton guard is allowed to count.
    """
    out: list[str] = []
    literal = 0
    pos = 0
    for m in _VOLATILE.finditer(chunk):
        plain = chunk[pos:m.start()]
        out.append(re.escape(plain))
        literal += sum(1 for c in plain if c.isalpha())
        out.append(r"\w+" if m.lastgroup == "alnum" else r"\d+")
        pos = m.end()
    tail = chunk[pos:]
    out.append(re.escape(tail))
    literal += sum(1 for c in tail if c.isalpha())
    return "".join(out), literal


def _template(a: str, b: str) -> re.Pattern | None:
    """Build a matcher from two observed variants of the same line.

    The volatile spans are learned by aligning the two captures rather than
    guessed from what an identifier is assumed to look like, so this works for
    timestamps, hashes, counters and any format not thought of in advance.
    """
    ta, tb = _tokens(a), _tokens(b)
    matcher = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    parts: list[str] = []
    literal = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():  # noqa: B007
        if tag == "equal":
            source, count = _widen("".join(ta[i1:i2]))
            parts.append(source)
            literal += count
        else:
            parts.append(".*?")
    if literal < MIN_SKELETON_CHARS:
        return None
    try:
        return re.compile("^" + "".join(parts) + "$", re.DOTALL)
    except re.error:
        return None


@dataclass
class NoiseModel:
    """What one side varies by on its own, independent of the other side."""

    exact: set[str] = field(default_factory=set)
    templates: list[re.Pattern] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.exact or self.templates)

    def __len__(self) -> int:
        return len(self.exact)

    def matches(self, line: str) -> bool:
        if line in self.exact:
            return True
        return any(t.match(line) for t in self.templates)


def noise_baseline(first: Page, second: Page) -> NoiseModel:
    """Build a noise model from two captures of the SAME side.

    Anything differing here varies without the other version being involved, so
    it is inherent noise (timestamps, request ids, unstable ordering, or a
    capture taken before rendering settled) and must not be reported as drift.

    Exact strings alone are not enough: the side under comparison carries a
    third distinct value that was never in the baseline. So each pair of
    corresponding unstable lines also yields a template covering the spans that
    were observed to move.
    """
    matcher = difflib.SequenceMatcher(None, first.lines, second.lines, autojunk=False)
    model = NoiseModel()
    seen: set[str] = set()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        left, right = first.lines[i1:i2], second.lines[j1:j2]
        model.exact.update(left)
        model.exact.update(right)

        # Only lines that plainly correspond can teach us what varied. Unequal
        # run lengths mean content was added or removed, not merely changed.
        if len(left) != len(right):
            continue
        for a, b in zip(left, right, strict=True):
            key = f"{a}\x00{b}"
            if key in seen:
                continue
            seen.add(key)
            tpl = _template(a, b)
            if tpl is not None:
                model.templates.append(tpl)
    return model


def compare(
    left: Page,
    right: Page,
    profile: Profile,
    noise: NoiseModel | None = None,
) -> DiffResult:
    """Diff two extracted pages into hunks, applying ignore and noise rules."""
    left_lines, ignored_l = _filter(left.lines, profile)
    right_lines, ignored_r = _filter(right.lines, profile)

    suppressed = 0
    if noise:
        before = len(left_lines) + len(right_lines)
        left_lines = [ln for ln in left_lines if not noise.matches(ln)]
        right_lines = [ln for ln in right_lines if not noise.matches(ln)]
        suppressed = before - (len(left_lines) + len(right_lines))

    if profile.mode is Mode.SHAPE:
        left_lines = [shape_of(ln) for ln in left_lines]
        right_lines = [shape_of(ln) for ln in right_lines]

    if profile.ignore_case:
        left_lines = [ln.casefold() for ln in left_lines]
        right_lines = [ln.casefold() for ln in right_lines]

    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
    result = DiffResult(
        left=left,
        right=right,
        ignored=ignored_l + ignored_r,
        noise_suppressed=suppressed,
        similarity=matcher.ratio(),
        mode=profile.mode,
        meta_changes=_compare_meta(left, right),
    )

    context = max(0, profile.context_lines)
    groups = list(matcher.get_grouped_opcodes(context))

    for group in groups:
        first_op = group[0]
        hunk = Hunk(left_start=first_op[1] + 1, right_start=first_op[3] + 1)
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for offset, text in enumerate(left_lines[i1:i2]):
                    hunk.lines.append(
                        DiffLine(Op.EQUAL, text, i1 + offset + 1, j1 + offset + 1)
                    )
            else:
                for offset, text in enumerate(left_lines[i1:i2]):
                    hunk.lines.append(DiffLine(Op.REMOVE, text, i1 + offset + 1, None))
                    result.removed += 1
                for offset, text in enumerate(right_lines[j1:j2]):
                    hunk.lines.append(DiffLine(Op.ADD, text, None, j1 + offset + 1))
                    result.added += 1
        result.hunks.append(hunk)

    return result


# --- structured comparison ---------------------------------------------------
#
# When a capture yields data rather than markup, comparing field by field beats
# diffing a rendering of it. A finding then names the field that moved, which is
# what someone verifying a migration actually needs, instead of a hunk to read.


@dataclass(frozen=True)
class FieldChange:
    path: str
    op: Op
    left: str = ""
    right: str = ""


MAX_FLATTEN_DEPTH = 40


def flatten(data: object, prefix: str = "", depth: int = 0) -> dict[str, str]:
    """Reduce nested data to a flat map of dotted paths to scalar values.

    Lists are indexed positionally. That is deliberate: for two renderings of
    the same record, a row moving position is itself a finding worth reporting,
    not something to normalise away silently.
    """
    if depth > MAX_FLATTEN_DEPTH:
        return {prefix or "<root>": "<max depth exceeded>"}

    out: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(value, child, depth + 1))
    elif isinstance(data, (list, tuple)):
        for i, value in enumerate(data):
            out.update(flatten(value, f"{prefix}[{i}]", depth + 1))
    else:
        key = prefix or "<root>"
        out[key] = "" if data is None else str(data)
    return out


def compare_structured(
    left: Page,
    right: Page,
    profile: Profile,
    left_data: object,
    right_data: object,
) -> DiffResult:
    """Diff two structured payloads field by field."""
    left_flat = flatten(left_data)
    right_flat = flatten(right_data)

    ignored = 0
    if profile.ignore_patterns:
        def keep(path: str) -> bool:
            return not any(p.search(path) for p in profile.ignore_patterns)

        before = len(left_flat) + len(right_flat)
        left_flat = {k: v for k, v in left_flat.items() if keep(k)}
        right_flat = {k: v for k, v in right_flat.items() if keep(k)}
        ignored = before - (len(left_flat) + len(right_flat))

    shape = profile.mode is Mode.SHAPE
    changes: list[FieldChange] = []
    same = 0

    for path in sorted(set(left_flat) | set(right_flat)):
        in_left, in_right = path in left_flat, path in right_flat
        a, b = left_flat.get(path, ""), right_flat.get(path, "")

        if in_left and not in_right:
            changes.append(FieldChange(path, Op.REMOVE, a, ""))
        elif in_right and not in_left:
            changes.append(FieldChange(path, Op.ADD, "", b))
        elif shape or a == b or (profile.ignore_case and a.casefold() == b.casefold()):
            # In shape mode the sides hold different data on purpose, so a field
            # present on both is agreement regardless of its value.
            same += 1
        else:
            changes.append(FieldChange(path, Op.ALTER, a, b))

    total = len(changes) + same
    result = DiffResult(
        left=left,
        right=right,
        ignored=ignored,
        mode=profile.mode,
        meta_changes=_compare_meta(left, right),
        similarity=(same / total) if total else 1.0,
    )
    result.field_changes = changes
    result.removed = sum(1 for c in changes if c.op is Op.REMOVE)
    result.added = sum(1 for c in changes if c.op is Op.ADD)
    result.altered = sum(1 for c in changes if c.op is Op.ALTER)
    return result
