"""Element comparison: what a page is made of, not only what it says."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff import structure
from sitediff.accept import AcceptStore, apply_accepted, fingerprints_of
from sitediff.config import Profile
from sitediff.engine import Status, compare
from sitediff.extract import Page

LEGACY = """<html><body><main>
<h1>School Report</h1>
<label for="q">Find a school</label><input id="q" type="text">
<button>Download CSV</button>
<table><caption>Indicators</caption>
<tr><th>Metric</th><th>Value</th><th>Target</th></tr>
<tr><td>Proficiency</td><td>45.2</td><td>50.0</td></tr></table>
<img src="a.png" alt="State seal">
</main></body></html>"""

REWRITE = """<html><body><main>
<h1>School Report</h1>
<label for="q">Find a school</label><input id="q" type="text">
<table><caption>Indicators</caption>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Proficiency</td><td>45.2</td></tr></table>
<img src="a.png">
<select id="year"><option>2025</option><option>2024</option></select>
<label for="year">Year</label>
</main></body></html>"""


def report():
    return structure.compare(structure.build(LEGACY), structure.build(REWRITE),
                             "legacy", "rewrite")


# --- inventory ---------------------------------------------------------------

def test_controls_are_named_by_their_label():
    inventory = structure.build(LEGACY)
    inputs = [e for e in inventory.elements if e.kind == "Text input"]
    assert [e.label for e in inputs] == ["Find a school"]


def test_hidden_inputs_are_not_inventoried():
    """A reader cannot see a hidden input, so it is not a finding."""
    html = '<html><body><input type="hidden" name="token"><p>x</p></body></html>'
    assert not [e for e in structure.build(html).elements if "input" in e.kind.lower()]


def test_tables_record_their_shape():
    table = next(e for e in structure.build(LEGACY).elements if e.kind == "Table")
    assert table.label == "Indicators"
    assert table.detail == "2 rows x 3 columns"


def test_images_without_alt_text_are_flagged():
    image = next(e for e in structure.build(REWRITE).elements if e.kind == "Image")
    assert image.detail == "no alt text"


def test_headings_carry_their_level():
    heading = next(e for e in structure.build(LEGACY).elements if e.kind == "Heading")
    assert heading.detail == "level 1"


# --- findings ----------------------------------------------------------------

def test_a_removed_control_is_reported_in_plain_language():
    messages = [f.message for f in report().of("missing")]
    assert any("Download CSV" in m and "not on rewrite" in m for m in messages)


def test_an_added_control_is_reported():
    messages = [f.message for f in report().of("added")]
    assert any("Dropdown" in m and "Year" in m for m in messages)


def test_a_reshaped_table_is_reported_with_both_shapes():
    changed = next(f for f in report().of("changed") if f.kind == "Table")
    assert changed.left == "2 rows x 3 columns"
    assert changed.right == "2 rows x 2 columns"
    assert "3 columns" in changed.message and "2 columns" in changed.message


def test_elements_present_on_both_are_not_reported():
    assert not any(f.label == "Find a school" for f in report().findings)


def test_summary_reads_as_sentences():
    lines = report().summary()
    assert all(line.endswith(".") for line in lines)
    assert any("absent from rewrite" in line for line in lines)


def test_identical_pages_report_no_structural_findings():
    same = structure.compare(structure.build(LEGACY), structure.build(LEGACY))
    assert same.clean
    assert same.summary() == ["Both pages are built from the same elements."]


def test_findings_are_ordered_most_serious_first():
    order = [f.severity for f in report().findings]
    assert order == sorted(order, key=lambda s: structure.SEVERITY_ORDER[s])


# --- integration with the rest of the pipeline -------------------------------

def test_structural_findings_alone_make_a_result_drift():
    """Identical text with a missing control is still a regression."""
    result = compare(Page("l", "left", ["same"]), Page("r", "right", ["same"]), Profile())
    assert result.status is Status.CLEAN
    result.structure_report = report()
    assert result.status is Status.DRIFT
    assert result.structural_findings > 0


def test_structural_findings_can_be_accepted(tmp_path):
    """A control dropped on purpose should be reviewed once, like any other
    intentional difference."""
    result = compare(Page("l", "left", ["same"]), Page("r", "right", ["same"]), Profile())
    result.structure_report = report()
    store = AcceptStore(path=tmp_path / "acc.toml")
    for fp, desc in fingerprints_of(result).items():
        store.add(fp, desc, "expected")

    again = compare(Page("l", "left", ["same"]), Page("r", "right", ["same"]), Profile())
    again.structure_report = report()
    assert apply_accepted(again, store) > 0
    assert again.status is Status.CLEAN


@pytest.mark.parametrize("markup", ["", "<html></html>", "not html at all"])
def test_degenerate_input_does_not_raise(markup):
    assert structure.build(markup).counts.get("Link", 0) == 0
