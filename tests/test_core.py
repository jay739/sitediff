"""Regression tests. The first two cover bugs that made the original
prototype crash or silently mangle output on real pages."""

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.config import Profile, load_profile
from sitediff.engine import Status, compare, noise_baseline
from sitediff.errors import (
    InvalidProfileError,
    PageNotFoundError,
    PageNotRenderedError,
    ProfileNotFoundError,
)
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
    html = (
        "<html><body><div hidden><div hidden><div hidden><p>x</p></div></div></div>"
        "<p>ok</p></body></html>"
    )
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
    removed = [ln.text for h in result.hunks for ln in h.lines if ln.op.value == "remove"]
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


# --- profile discovery -------------------------------------------------------

def test_local_profile_dir_shadows_bundled(tmp_path, monkeypatch):
    """A team's own profile wins over a bundled template of the same name."""
    local = tmp_path / ".sitediff"
    local.mkdir()
    (local / "default.toml").write_text(
        '[profile]\nname = "default"\ndescription = "local override"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert load_profile("default").description == "local override"


def test_env_var_dir_takes_precedence(tmp_path, monkeypatch):
    elsewhere = tmp_path / "shared"
    elsewhere.mkdir()
    (elsewhere / "team.toml").write_text(
        '[profile]\nname = "team"\n[security]\nallow_hosts = ["a.example.com"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SITEDIFF_PROFILES", str(elsewhere))
    monkeypatch.chdir(tmp_path)
    assert load_profile("team").egress_policy().allowed == ["a.example.com"]


def test_missing_profile_reports_every_directory_searched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ProfileNotFoundError) as exc:
        load_profile("nope")
    assert ".sitediff" in str(exc.value)


def test_no_host_literals_are_baked_into_the_engine():
    """The egress allowlist must come from the profile, never from the tool."""
    import sitediff
    engine_dir = pathlib.Path(sitediff.__file__).parent
    pattern = re.compile(r"https?://(?!reference\.example|candidate\.example)[a-z0-9.-]+\.[a-z]{2,}")
    for module in engine_dir.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert not pattern.search(text), f"{module.name} contains a hardcoded host"


def test_bundled_profiles_name_only_reserved_example_domains():
    """A shipped template must never carry a usable allowlist.

    RFC 2606 reserves .example / .example.com for documentation, so a template
    referencing those cannot reach anything real even if run unedited.
    """
    import sitediff
    bundled = pathlib.Path(sitediff.__file__).parent / "profiles"
    for p in bundled.glob("*.toml"):
        for host in load_profile(str(p)).egress_policy().allowed:
            assert host.endswith((".example.com", ".example")), f"{p.name}: {host}"


def test_unknown_key_is_rejected_with_a_suggestion(tmp_path):
    """A typo in a security-relevant key must fail loudly, not silently."""
    profile = tmp_path / "p.toml"
    profile.write_text('[security]\nallow_host = ["a.example.com"]\n', encoding="utf-8")
    with pytest.raises(InvalidProfileError) as exc:
        load_profile(str(profile))
    assert "allow_host" in str(exc.value)
    assert "did you mean 'allow_hosts'" in str(exc.value)


def test_unknown_source_key_is_rejected(tmp_path):
    profile = tmp_path / "p.toml"
    profile.write_text(
        '[[source]]\nname = "a"\nbase_url = "https://a.example.com"\ntimout = 5\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidProfileError) as exc:
        load_profile(str(profile))
    assert "timeout" in str(exc.value)


def test_every_documented_key_is_accepted():
    """The configuration reference and the parser must not drift apart."""
    import re as _re

    from sitediff.config import (
        COMPARE_KEYS,
        EXTRACT_KEYS,
        PROFILE_KEYS,
        RULE_KEYS,
        SECURITY_KEYS,
        SOURCE_KEYS,
    )
    # The configuration reference lives in docs/, so that is what must not drift.
    docs = (pathlib.Path(__file__).resolve().parent.parent
            / "docs" / "configuration.md").read_text(encoding="utf-8")
    documented = set(_re.findall(r"^\| `(\w+)` \|", docs, _re.M))
    known = PROFILE_KEYS | EXTRACT_KEYS | COMPARE_KEYS | SECURITY_KEYS | SOURCE_KEYS | RULE_KEYS
    missing = known - documented
    assert not missing, f"undocumented config keys: {sorted(missing)}"


def test_nested_include_selectors_do_not_duplicate_content(tmp_path):
    """Selectors for two codebases are given as one union, so several can match
    nested elements on one page. The inner match must not be walked twice."""
    html = """<html><body><main id="main"><p>outer</p>
              <div id="inner"><p>value</p></div></main></body></html>"""
    profile = Profile(include_selectors=["#main", "#inner"])
    assert lines_of(tmp_path, html, profile) == ["outer", "value"]


def test_sibling_include_selectors_are_both_kept(tmp_path):
    html = """<html><body><div id="a"><p>one</p></div>
              <div id="b"><p>two</p></div></body></html>"""
    profile = Profile(include_selectors=["#a", "#b"])
    assert lines_of(tmp_path, html, profile) == ["one", "two"]


def test_ignore_case_folds_markup_only_differences():
    """CSS text-transform makes these render identically, so a case-only
    difference is in the markup but not in anything a reader can see."""
    import re as _re
    left = Page("l", "left", ["INDICATOR", "Earned Points"])
    right = Page("r", "right", ["Indicator", "Earned Points"])
    assert compare(left, right, Profile()).status is Status.DRIFT
    assert compare(left, right, Profile(ignore_case=True)).status is Status.CLEAN
    assert _re  # keep the import used


def test_ignore_case_does_not_hide_real_text_changes():
    left = Page("l", "left", ["Earned Points"])
    right = Page("r", "right", ["Possible Points"])
    assert compare(left, right, Profile(ignore_case=True)).status is Status.DRIFT
