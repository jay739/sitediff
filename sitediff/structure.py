"""Comparing what a page is made of, not just what it says.

Text extraction flattens a page into lines and throws the markup away, so a
missing search box, a table that lost a column, or a heading that changed level
are all invisible to it. Those are exactly the regressions a migration
introduces, and they are what a person notices first.

This builds an inventory of the things a reader would name, headings, tables,
form controls, images, landmarks, then reports the differences in plain
language rather than as a diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

# Only elements a person would actually name. An anonymous <div> is not a
# finding; a search box that vanished is.
CONTROL_TYPES = {
    "text": "Text input", "search": "Search box", "email": "Email input",
    "number": "Number input", "tel": "Phone input", "url": "URL input",
    "password": "Password input", "checkbox": "Checkbox", "radio": "Radio button",
    "date": "Date input", "file": "File upload", "submit": "Submit button",
    "button": "Button", "hidden": "", "reset": "Reset button",
}

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Element:
    """One nameable thing on the page."""

    kind: str
    label: str
    detail: str = ""

    @property
    def identity(self) -> tuple[str, str]:
        return (self.kind, self.label.casefold())

    def describe(self) -> str:
        name = f"{self.kind} {self.label!r}" if self.label else self.kind
        return f"{name} ({self.detail})" if self.detail else name


@dataclass
class Inventory:
    side: str = ""
    elements: list[Element] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def by_identity(self) -> dict[tuple[str, str], Element]:
        return {e.identity: e for e in self.elements}


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip()


def _accessible_name(tag: Tag, soup: BeautifulSoup) -> str:
    """Best-effort label, following roughly what a screen reader would announce."""
    for attr in ("aria-label", "title", "alt", "placeholder"):
        value = tag.get(attr)
        if isinstance(value, str) and value.strip():
            return _clean(value)

    labelled_by = tag.get("aria-labelledby")
    if isinstance(labelled_by, str):
        parts = [
            _clean(found.get_text())
            for ref in labelled_by.split()
            if (found := soup.find(id=ref)) is not None
        ]
        if any(parts):
            return _clean(" ".join(p for p in parts if p))

    element_id = tag.get("id")
    if isinstance(element_id, str) and element_id:
        label = soup.find("label", attrs={"for": element_id})
        if label is not None:
            return _clean(label.get_text())

    parent_label = tag.find_parent("label")
    if parent_label is not None:
        return _clean(parent_label.get_text())

    name = tag.get("name")
    return _clean(name) if isinstance(name, str) else ""


def _table_shape(table: Tag) -> tuple[str, str]:
    caption = table.find("caption")
    label = _clean(caption.get_text()) if caption else ""
    if not label:
        first_header = table.find("th")
        label = _clean(first_header.get_text()) if first_header else ""

    rows = table.find_all("tr")
    widest = 0
    for row in rows:
        widest = max(widest, len(row.find_all(["td", "th"], recursive=False)))
    return label, f"{len(rows)} rows x {widest} columns"


def build(html: str, config=None, parser: str = "lxml") -> Inventory:
    """Inventory the nameable elements of a page.

    What counts as nameable is profile-driven, like every other decision in
    this tool. The defaults are ordinary HTML semantics, which are not a
    per-site choice, but a team can narrow the set, silence labels that are
    pure chrome, or register components this tool has never heard of.
    """
    from .config import StructureConfig

    cfg = config if config is not None else StructureConfig()
    try:
        soup = BeautifulSoup(html, parser)
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for junk in soup.find_all(["script", "style", "noscript", "template"]):
        junk.decompose()

    inventory = Inventory()
    root = soup.body or soup

    def add(kind: str, label: str, detail: str = "") -> None:
        if not cfg.wants(kind) or (label and cfg.silenced(label)):
            return
        inventory.elements.append(Element(kind, label, detail))

    for level in range(1, 7):
        for heading in root.find_all(f"h{level}"):
            text = _clean(heading.get_text())
            if text:
                add("Heading", text, f"level {level}")

    for table in root.find_all("table"):
        label, shape = _table_shape(table)
        add("Table", label, shape)

    for control in root.find_all(["input", "select", "textarea"]):
        if control.name == "input":
            kind = CONTROL_TYPES.get((control.get("type") or "text").lower(), "Input")
            if not kind:  # hidden inputs are not visible to a reader
                continue
        elif control.name == "select":
            kind = "Dropdown"
        else:
            kind = "Text area"
        detail = ""
        if control.name == "select":
            detail = f"{len(control.find_all('option'))} options"
        add(kind, _accessible_name(control, soup), detail)

    for button in root.find_all("button"):
        add("Button", _clean(button.get_text()) or _accessible_name(button, soup))

    for image in root.find_all("img"):
        alt = image.get("alt")
        described = isinstance(alt, str) and alt.strip() != ""
        add("Image", _clean(alt) if described else "",
            "" if described else "no alt text")

    for landmark in cfg.landmarks:
        for found in root.find_all(landmark):
            add("Landmark", landmark, _accessible_name(found, soup))

    for tag in ("ul", "ol"):
        for listing in root.find_all(tag):
            items = len(listing.find_all("li", recursive=False))
            if items:
                add("List", _accessible_name(listing, soup), f"{items} items")

    # Components this tool has never heard of, registered by the profile.
    for custom in cfg.custom:
        try:
            matches = root.select(custom.selector)
        except Exception:
            continue
        for found in matches:
            label = (found.get(custom.label_attr, "") if custom.label_attr
                     else _clean(found.get_text()))
            add(custom.kind, _clean(label if isinstance(label, str) else ""))

    counts: dict[str, int] = {}
    for element in inventory.elements:
        counts[element.kind] = counts.get(element.kind, 0) + 1
    counts["Link"] = len(root.find_all("a", href=True))
    inventory.counts = counts
    return inventory


SEVERITY_ORDER = {"missing": 0, "added": 1, "changed": 2, "count": 3}


@dataclass(frozen=True)
class Finding:
    """One structural difference, phrased for a reader rather than a diff tool."""

    severity: str
    kind: str
    label: str
    message: str
    left: str = ""
    right: str = ""

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 9)


@dataclass
class StructureReport:
    left_name: str = "left"
    right_name: str = "right"
    findings: list[Finding] = field(default_factory=list)
    left_counts: dict[str, int] = field(default_factory=dict)
    right_counts: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.findings

    def of(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def summary(self) -> list[str]:
        """A few sentences a person can read without decoding anything."""
        lines = []
        missing, added = len(self.of("missing")), len(self.of("added"))
        changed, counts = len(self.of("changed")), len(self.of("count"))
        if missing:
            lines.append(f"{missing} element(s) on {self.left_name} are absent "
                         f"from {self.right_name}.")
        if added:
            lines.append(f"{added} element(s) appear on {self.right_name} that are "
                         f"not on {self.left_name}.")
        if changed:
            lines.append(f"{changed} element(s) exist on both but differ in shape.")
        if counts:
            lines.append(f"{counts} element type(s) appear a different number of times.")
        if not lines:
            lines.append("Both pages are built from the same elements.")
        return lines


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def compare(left: Inventory, right: Inventory,
            left_name: str = "left", right_name: str = "right") -> StructureReport:
    """Diff two inventories into findings written for a person."""
    from collections import Counter

    report = StructureReport(left_name=left_name, right_name=right_name,
                             left_counts=dict(left.counts), right_counts=dict(right.counts))

    left_ids = Counter(e.identity for e in left.elements)
    right_ids = Counter(e.identity for e in right.elements)
    left_lookup, right_lookup = left.by_identity(), right.by_identity()

    for identity in sorted(set(left_ids) | set(right_ids)):
        kind, _ = identity
        in_left, in_right = left_ids.get(identity, 0), right_ids.get(identity, 0)
        sample = left_lookup.get(identity) or right_lookup[identity]
        name = f"{sample.label!r}" if sample.label else "(unlabelled)"

        if in_left and not in_right:
            report.findings.append(Finding(
                "missing", kind, sample.label,
                f"{kind} {name} is on {left_name} but not on {right_name}."))
        elif in_right and not in_left:
            report.findings.append(Finding(
                "added", kind, sample.label,
                f"{kind} {name} was added on {right_name}; "
                f"{left_name} has no such element."))
        elif in_left != in_right:
            report.findings.append(Finding(
                "count", kind, sample.label,
                f"{kind} {name} appears {in_left} time(s) on {left_name} and "
                f"{in_right} time(s) on {right_name}.",
                str(in_left), str(in_right)))
        else:
            a, b = left_lookup.get(identity), right_lookup.get(identity)
            if a and b and a.detail != b.detail:
                report.findings.append(Finding(
                    "changed", kind, sample.label,
                    f"{kind} {name} is {a.detail or 'unspecified'} on {left_name} "
                    f"but {b.detail or 'unspecified'} on {right_name}.",
                    a.detail, b.detail))

    for kind in sorted(set(left.counts) | set(right.counts)):
        a, b = left.counts.get(kind, 0), right.counts.get(kind, 0)
        if a != b:
            report.findings.append(Finding(
                "count", kind, "",
                f"{left_name} has {_plural(a, kind.lower())}, "
                f"{right_name} has {b}.", str(a), str(b)))

    report.findings.sort(key=lambda f: (f.rank, f.kind, f.label))
    return report
