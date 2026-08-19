"""Capture strategies, structured comparison, and the HTML report."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.capture import Capture, Kind, extract_hydration, from_html
from sitediff.config import Mode, Profile
from sitediff.engine import Op, Status, compare_structured, flatten
from sitediff.errors import CaptureError, UnsupportedCaptureError
from sitediff.extract import Page
from sitediff.report import build_html_report

NEXT_DATA = ('<html><body><main><h1>School</h1></main>'
             '<script id="__NEXT_DATA__" type="application/json">'
             '{"props":{"school":{"id":"1234","pct":45.2}}}</script></body></html>')


def page(side="left"):
    return Page(f"{side}.json", side, [])


# --- flatten -----------------------------------------------------------------

def test_flatten_produces_dotted_paths():
    assert flatten({"a": {"b": 1}, "c": [10, 20]}) == {
        "a.b": "1", "c[0]": "10", "c[1]": "20",
    }


def test_flatten_renders_null_as_empty_not_the_string_none():
    """A null value is the missing-data finding, so it must not read as 'None'."""
    assert flatten({"pct": None}) == {"pct": ""}


def test_flatten_is_depth_limited():
    deep = current = {}
    for _ in range(80):
        current["n"] = {}
        current = current["n"]
    assert "<max depth exceeded>" in flatten(deep).values()


# --- structured comparison ---------------------------------------------------

def test_missing_value_is_reported_as_an_altered_field():
    result = compare_structured(
        page("left"), page("right"), Profile(),
        {"id": "1234", "pct": 45.2}, {"id": "1234", "pct": None},
    )
    assert result.status is Status.DRIFT
    assert result.altered == 1
    change = result.field_changes[0]
    assert (change.path, change.left, change.right) == ("pct", "45.2", "")


def test_absent_field_is_distinguished_from_a_changed_one():
    result = compare_structured(
        page(), page("right"), Profile(),
        {"a": 1, "b": 2}, {"a": 9, "c": 3},
    )
    ops = {c.path: c.op for c in result.field_changes}
    assert ops == {"a": Op.ALTER, "b": Op.REMOVE, "c": Op.ADD}


def test_identical_structures_are_clean():
    data = {"a": [1, 2], "b": {"c": "x"}}
    result = compare_structured(page(), page("right"), Profile(), data, dict(data))
    assert result.status is Status.CLEAN
    assert result.similarity == 1.0


def test_shape_mode_ignores_values_but_not_missing_fields():
    """Staging and production hold different data, so only structure matters."""
    profile = Profile(mode=Mode.SHAPE)
    result = compare_structured(
        page(), page("right"), profile,
        {"a": 1, "b": 2}, {"a": 999, "c": 3},
    )
    assert result.altered == 0
    assert {c.path for c in result.field_changes} == {"b", "c"}


def test_ignore_patterns_apply_to_field_paths():
    import re
    profile = Profile(ignore_patterns=[re.compile(r"^meta\.")])
    result = compare_structured(
        page(), page("right"), profile,
        {"meta.x": 1, "v": 1}, {"meta.x": 2, "v": 1},
    )
    assert result.status is Status.CLEAN


def test_list_reordering_is_reported_not_hidden():
    """Two renderings of one record disagreeing on row order is a finding."""
    result = compare_structured(
        page(), page("right"), Profile(), {"r": ["a", "b"]}, {"r": ["b", "a"]},
    )
    assert result.altered == 2


# --- capture strategies ------------------------------------------------------

def test_hydration_reads_embedded_state():
    payload = from_html(NEXT_DATA, Capture.HYDRATION, "p.html")
    assert payload.kind is Kind.DATA
    assert payload.data["props"]["school"]["pct"] == 45.2
    assert payload.meta["hydration_marker"] == "__NEXT_DATA__"


def test_hydration_can_target_a_named_marker():
    html = (NEXT_DATA.replace("</body>", '<script id="other" type="application/json">'
                              '{"v":1}</script></body>'))
    assert from_html(html, Capture.HYDRATION, "p", marker="other").data == {"v": 1}


def test_hydration_without_state_explains_what_to_do():
    with pytest.raises(CaptureError) as exc:
        from_html("<html><body><p>plain</p></body></html>", Capture.HYDRATION, "p")
    assert "probe" in str(exc.value)


def test_hydration_names_the_payload_it_could_not_parse():
    """A Next.js RSC stream is a JS expression, not JSON. Say so."""
    html = '<html><body><script>self.__next_f.push([1,"x"])</script></body></html>'
    with pytest.raises(CaptureError) as exc:
        from_html(html, Capture.HYDRATION, "p")
    assert "probe" in str(exc.value) or "JSON" in str(exc.value)


def test_json_capture_parses_the_body():
    payload = from_html(json.dumps({"a": 1}), Capture.JSON, "d.json")
    assert payload.kind is Kind.DATA and payload.data == {"a": 1}


def test_static_capture_of_json_warns_rather_than_silently_diffing_text():
    """Text capture of JSON does not fail, it produces output that answers nothing."""
    payload = from_html('{"a": 1}', Capture.STATIC, "d.json")
    assert payload.kind is Kind.TEXT
    assert any('capture = "json"' in note for note in payload.notes)


def test_static_capture_of_html_has_no_note():
    assert from_html(NEXT_DATA, Capture.STATIC, "p.html").notes == []


def test_browser_capture_points_at_the_optional_extra():
    with pytest.raises(UnsupportedCaptureError) as exc:
        from_html(NEXT_DATA, Capture.BROWSER, "p.html")
    assert "sitediff[browser]" in str(exc.value)


def test_named_marker_that_is_absent_is_an_error_not_a_fallback():
    with pytest.raises(CaptureError) as exc:
        extract_hydration(NEXT_DATA, marker="__NUXT__")
    assert "__NUXT__" in str(exc.value)


# --- report ------------------------------------------------------------------

def test_report_is_self_contained_for_both_result_kinds():
    structured = compare_structured(page(), page("right"), Profile(), {"a": 1}, {"a": 2})
    out = build_html_report(structured, Profile())
    assert 'src="http' not in out and 'href="http' not in out
    assert "<style>" in out and "a" in out


def test_report_escapes_page_content():
    """Report content comes from the compared pages, so it must not inject."""
    payload = {"x": "<script>alert(1)</script>"}
    result = compare_structured(page(), page("right"), Profile(), payload, {"x": "safe"})
    out = build_html_report(result, Profile())
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


# --- report assets -----------------------------------------------------------

def test_report_inlines_its_templates():
    """The stylesheet and script are separate files for editing, but a report
    must remain one file that opens on a machine with no network."""
    result = compare_structured(page(), page("right"), Profile(), {"a": 1}, {"a": 2})
    out = build_html_report(result, Profile())
    assert "<style>" in out and "<script>" in out
    assert 'src="http' not in out and 'href="http' not in out
    assert "--add-bg" in out          # from report.css
    assert "sitediff-theme" in out    # from report.js


def test_report_templates_ship_with_the_package():
    import sitediff
    templates = pathlib.Path(sitediff.__file__).parent / "templates"
    for name in ("report.css", "report.js"):
        assert (templates / name).is_file(), name


def test_report_marks_every_row_for_filtering():
    """The filter and search UI works off data-op, so every row needs one."""
    result = compare_structured(page(), page("right"), Profile(),
                                {"a": 1, "b": 2}, {"a": 9, "c": 3})
    out = build_html_report(result, Profile())
    assert out.count('data-op="alter"') == 1
    assert out.count('data-op="remove"') == 1
    assert out.count('data-op="add"') == 1


def test_report_honours_reduced_motion():
    result = compare_structured(page(), page("right"), Profile(), {"a": 1}, {"a": 2})
    assert "prefers-reduced-motion" in build_html_report(result, Profile())


def test_run_report_renders_a_sortable_table():
    from sitediff.report import build_run_report
    from sitediff.run import EntityResult, RunReport
    report = RunReport(profile="t", seconds=1.0)
    report.results = [EntityResult("4001", "clean"),
                      EntityResult("4002", "drift", altered=1, detail="x")]
    out = build_run_report(report, Profile())
    assert "data-sortable" in out
    assert "4002" in out
