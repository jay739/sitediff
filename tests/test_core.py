"""Regression tests. The first two cover bugs that made the original
prototype crash or silently mangle output on real pages."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.config import Profile, load_profile
from sitediff.engine import NoiseModel, Status, compare, noise_baseline
from sitediff.errors import PageNotFoundError, PageNotRenderedError, ProfileNotFoundError
from sitediff.extract import Page, extract

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def write(tmp_path, name, html):
    p = tmp_path / name
    p.write_text(html, encoding="utf-8")
    return str(p)


def lines_of(tmp_path, html, profile=None):
    return extract(write(tmp_path, "p.html", html), profile or Profile()).lines


# --- regression: hidden inside hidden used to raise TypeError -----------------

def test_nested_hidden_elements_do_not_crash(tmp_path):
    html = """<html><body>
      <div style="display:none"><p hidden>inner</p><span aria-hidden="true">x</span></div>
      <p>after</p></body></html>"""
    assert lines_of(tmp_path, html) == ["after"]


def test_deeply_nested_hidden_chain(tmp_path):
    html = "<html><body><div hidden><div hidden><div hidden><p>x</p></div></div></div><p>ok</p></body></html>"
    assert lines_of(tmp_path, html) == ["ok"]


# --- regression: inline tags used to fragment one sentence into five lines ----

def test_inline_spans_stay_on_one_line(tmp_path):
    html = "<html><body><p>Total is <span>72.4</span> of <span>100</span> points.</p></body></html>"
    assert lines_of(tmp_path, html) == ["Total is 72.4 of 100 points."]


def test_block_tags_still_split(tmp_path):
    html = "<html><body><table><tr><td>Metric</td><td>45.2%</td></tr></table></body></html>"
    assert lines_of(tmp_path, html) == ["Metric", "45.2%"]


# --- visibility --------------------------------------------------------------

@pytest.mark.parametrize("attr", [
    'style="display:none"', 'style="display: none"', 'style="visibility:hidden"',
    'hidden', 'aria-hidden="true"', 'class="d-none"', 'class="sr-only"',
])
def test_hidden_variants_are_dropped(tmp_path, attr):
    html = f"<html><body><div {attr}>gone</div><p>kept</p></body></html>"
    assert lines_of(tmp_path, html) == ["kept"]


def test_aria_hidden_false_is_kept(tmp_path):
    html = '<html><body><div aria-hidden="false">kept</div></body></html>'
    assert lines_of(tmp_path, html) == ["kept"]


# --- selectors ---------------------------------------------------------------

def test_include_and_exclude_selectors(tmp_path):
    html = """<html><body><nav>skip me</nav><main><p>data</p></main>
              <footer>boilerplate</footer></body></html>"""
    p = Profile(include_selectors=["main"], exclude_selectors=["nav"])
    assert lines_of(tmp_path, html, p) == ["data"]


# --- render guard ------------------------------------------------------------

def test_unrendered_page_is_rejected(tmp_path):
    html = "<html><body><p>Loading... please wait</p></body></html>"
    p = Profile(render_sentinels=["Loading... please wait"])
    path = write(tmp_path, "l.html", html)
    with pytest.raises(PageNotRenderedError) as exc:
        extract(path, p, side="left")
    assert "captured before rendering finished" in str(exc.value)
    assert extract(path, p, allow_unrendered=True).lines


# --- noise model -------------------------------------------------------------

def test_template_learns_volatile_spans_from_evidence():
    model = noise_baseline(
        Page("a", "left", ["Build 4f2a1b done in 1.20s"]),
        Page("b", "left", ["Build 90ffee done in 3.75s"]),
    )
    assert model.matches("Build zzzzzz done in 9.99s")
    assert not model.matches("Build 4f2a1b failed in 1.20s")


def test_stable_spans_are_widened_where_numeric():
    """Captures seconds apart share the date and hour, but the compared side
    will not, so those must not be baked into the template as literals."""
    model = noise_baseline(
        Page("a", "left", ["Generated on 2026-08-18 23:04:11"]),
        Page("b", "left", ["Generated on 2026-08-18 23:09:47"]),
    )
    assert model.matches("Generated on 2026-08-19 01:02:03")


def test_widening_does_not_swallow_different_prose():
    model = noise_baseline(
        Page("a", "left", ["Generated on 2026-08-18 23:04:11"]),
        Page("b", "left", ["Generated on 2026-08-18 23:09:47"]),
    )
    assert not model.matches("Published on 2026-08-19 01:02:03")


def test_weak_skeleton_is_not_generalised(tmp_path):
    """A bare value like 45.2% must never become a suppression pattern."""
    first = Page("a", "left", ["45.2%", "stable"])
    second = Page("b", "left", ["91.4%", "stable"])
    model = noise_baseline(first, second)
    assert not model.matches("72.9%")


def test_noise_suppressed_on_both_sides(tmp_path):
    """The compared side carries a third distinct value, unseen in the baseline."""
    model = noise_baseline(
        Page("a", "left", ["Generated on 2026-08-18 23:04:11 (request 8f3a-11c2)"]),
        Page("b", "left", ["Generated on 2026-08-18 23:09:47 (request b21d-90ff)"]),
    )
    assert model.matches("Generated on 2026-08-18 01:02:03 (request ffff-0000)")


def test_stable_capture_yields_empty_model():
    page = Page("a", "left", ["one", "two"])
    assert not noise_baseline(page, Page("b", "left", ["one", "two"]))


# --- engine ------------------------------------------------------------------

def test_identical_pages_are_clean(tmp_path):
    left = extract(FIXTURES / "legacy.html", Profile(), "left")
    right = extract(FIXTURES / "legacy.html", Profile(), "right")
    result = compare(left, right, Profile())
    assert result.status is Status.CLEAN
    assert result.changed == 0


def test_missing_value_is_reported(tmp_path):
    left = extract(FIXTURES / "legacy.html", Profile(), "left")
    right = extract(FIXTURES / "optimised.html", Profile(), "right")
    result = compare(left, right, Profile())
    assert result.status is Status.DRIFT
    removed = [l.text for h in result.hunks for l in h.lines if l.op.value == "remove"]
    assert "45.2%" in removed


def test_ignore_patterns_drop_lines():
    import re
    p = Profile(ignore_patterns=[re.compile(r"^Generated on")])
    left = Page("a", "left", ["Generated on X", "data"])
    right = Page("b", "right", ["Generated on Y", "data"])
    result = compare(left, right, p)
    assert result.status is Status.CLEAN
    assert result.ignored == 2


# --- errors ------------------------------------------------------------------

def test_missing_file_raises_typed_error():
    with pytest.raises(PageNotFoundError) as exc:
        extract("/nope/missing.html", Profile())
    assert "No such HTML file" in str(exc.value)


def test_unknown_profile_lists_search_paths():
    with pytest.raises(ProfileNotFoundError) as exc:
        load_profile("does-not-exist")
    assert "Looked in" in str(exc.value)
