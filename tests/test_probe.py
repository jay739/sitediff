"""Probe tests: does it reach the same conclusion a person would reach by
opening devtools and reading the Network tab?"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.config import Profile, load_profile
from sitediff.probe import Strategy, probe, suggested_profile

KNOCKOUT = """<html><head>
<script src="/Scripts/jquery-3.6.0.min.js"></script>
<script src="/Scripts/knockout-3.5.1.js"></script>
<script>$.getJSON("/api/Record/GetRecordData?id=1234",
  function(d){ ko.applyBindings(d); });</script>
</head><body><div id="main">
<h1 data-bind="text: schoolName">Loading... please wait</h1>
<table><tr><td>Percent Proficient</td><td data-bind="text: pct">Loading...</td></tr></table>
</div></body></html>"""

NEXT = """<html><head><link rel="preload" href="/_next/static/chunks/main.js"></head><body>
<main><h1>Example Elementary</h1><table><tr><td>Percent Proficient</td><td>45.2%</td></tr></table>
<p>Total score is 72.4 out of 100 points across the full reporting year here.</p></main>
<script>self.__next_f.push([1,"{\\"recordId\\":\\"1234\\",\\"pct\\":45.2,\\"earned\\":18.5,
\\"name\\":\\"Example Elementary\\",\\"county\\":\\"01\\",\\"attendance\\":91.4}"])</script>
</body></html>"""

STATIC = """<html><body><main><h1>Annual Report</h1>
<p>This page is rendered on the server and holds every value directly in markup.</p>
<table><tr><td>Revenue</td><td>$1,204,500</td></tr><tr><td>Headcount</td><td>318</td></tr></table>
</main></body></html>"""

OPAQUE = """<html><head><script src="/static/app.bundle.js"></script></head>
<body><div id="root"></div><script>window.boot({mode:"live"});</script></body></html>"""


def run(html, url="https://a.example.com/p"):
    return probe(html, url, Profile())


@pytest.mark.parametrize("html,expected", [
    (KNOCKOUT, Strategy.JSON),
    (NEXT, Strategy.HYDRATION),
    (STATIC, Strategy.STATIC),
    (OPAQUE, Strategy.BROWSER),
])
def test_strategy_recommendation(html, expected):
    assert run(html).strategy is expected


def test_frameworks_are_named_most_specific_first():
    """Naming the meta-framework is more useful than the view layer under it."""
    assert next(n for n, _ in run(NEXT).frameworks) == "Next.js"
    names = [n for n, _ in run(KNOCKOUT).frameworks]
    assert "Knockout" in names and "jQuery" in names


def test_endpoints_are_recovered_from_inline_script():
    """This is the Network-tab substitute, so it must find the data call."""
    assert any("GetRecordData" in e for e in run(KNOCKOUT).endpoints)


def test_assets_are_not_reported_as_data_endpoints():
    endpoints = run(NEXT).endpoints
    assert not any(e.endswith((".js", ".css", ".png", ".woff2")) for e in endpoints)


def test_loading_placeholders_become_sentinel_candidates():
    markers = run(KNOCKOUT).loading_markers
    assert any("loading" in m.lower() for m in markers)


def test_placeholder_text_does_not_make_static_viable():
    """Knockout serves plenty of text, but it is placeholders, not values."""
    report = run(KNOCKOUT)
    assert report.visible_lines > 0
    assert Strategy.STATIC.value not in [name for name, _ in report.alternatives]


def test_ssr_spa_reports_both_viable_strategies():
    """Values are readable as text and present as state; say so, don't hide one."""
    report = run(NEXT)
    assert report.strategy is Strategy.HYDRATION
    assert Strategy.STATIC.value in [name for name, _ in report.alternatives]


def test_ld_json_alone_does_not_imply_hydration():
    """Structured metadata is not page data."""
    html = ('<html><body><script type="application/ld+json">{"@type":"Thing"}</script>'
            '<div id="root"></div></body></html>')
    assert run(html).strategy is Strategy.BROWSER


@pytest.mark.parametrize("html", [KNOCKOUT, NEXT, STATIC, OPAQUE])
def test_suggested_fragment_is_a_loadable_profile(html, tmp_path):
    """The fragment is meant to be pasted, so it must actually parse."""
    out = tmp_path / "frag.toml"
    out.write_text(suggested_profile(run(html)), encoding="utf-8")
    assert load_profile(str(out)).egress_policy().allowed == ["a.example.com"]


def test_probing_a_saved_file_still_yields_a_valid_base_url(tmp_path):
    """A file path has no scheme or host, which must not produce '://host'."""
    fragment = suggested_profile(probe(STATIC, "/tmp/saved.html", Profile()))
    assert "://" in fragment
    assert '"://' not in fragment
