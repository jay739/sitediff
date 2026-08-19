"""Reading a browser HAR export to find a site's data endpoints."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.har import InvalidHarError, load, suggest_profile


def entry(url, method="GET", body="", response="", mime="text/html",
          post_mime="application/x-www-form-urlencoded"):
    return {
        "request": {"method": method, "url": url,
                    "postData": {"mimeType": post_mime, "text": body} if body else {}},
        "response": {"content": {"mimeType": mime, "text": response}},
    }


def write(tmp_path, entries):
    path = tmp_path / "capture.har"
    path.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return path


NUMBERS = " ".join(f"{n}.{n}" for n in range(1, 30))


def test_endpoints_carrying_numbers_are_identified(tmp_path):
    har = write(tmp_path, [
        entry("https://a.example.com/chrome", response="<html>nav footer</html>"),
        entry("https://a.example.com/data", method="POST", body="id=1", response=NUMBERS),
    ])
    report = load(har)
    assert [e.path for e in report.data_endpoints] == ["/data"]


def test_third_party_and_assets_are_skipped(tmp_path):
    har = write(tmp_path, [
        entry("https://www.googletagmanager.com/gtag/js", response="x"),
        entry("https://a.example.com/app.min.js", response="x"),
        entry("https://a.example.com/page", response=NUMBERS),
    ])
    report = load(har)
    assert report.skipped_third_party == 1
    assert report.skipped_assets == 1
    assert [e.path for e in report.endpoints] == ["/page"]


def test_one_endpoint_called_twice_is_listed_once(tmp_path):
    """The same view fetched with different parameters is one endpoint; listing
    it twice buries the others."""
    har = write(tmp_path, [
        entry("https://a.example.com/v", method="POST", body="id=1", response="short"),
        entry("https://a.example.com/v", method="POST", body="id=2", response=NUMBERS),
    ])
    report = load(har)
    assert len(report.endpoints) == 1
    assert report.endpoints[0].calls == 2
    # the richest response is kept, since it shows what the endpoint returns
    assert report.endpoints[0].decimals > 10


def test_form_and_json_payloads_are_both_parsed(tmp_path):
    har = write(tmp_path, [
        entry("https://a.example.com/f", method="POST", body="RECORD_ID=1234&LEA=02",
              response=NUMBERS),
        entry("https://a.example.com/j", method="POST", body='{"RECORD_ID":"1234"}',
              response=NUMBERS, post_mime="application/json"),
    ])
    by_path = {e.path: e for e in load(har).endpoints}
    assert by_path["/f"].params == {"RECORD_ID": "1234", "LEA": "02"}
    assert by_path["/j"].params == {"RECORD_ID": "1234"}


def test_responses_without_bodies_are_counted(tmp_path):
    """A HAR exported without content is the common mistake, so say so."""
    har = write(tmp_path, [entry("https://a.example.com/x", response="")])
    assert load(har).missing_bodies == 1


def test_suggested_fragment_names_the_data_endpoints(tmp_path):
    har = write(tmp_path, [
        entry("https://a.example.com/reports/view", method="POST",
              body="RECORD_ID=1234", response=NUMBERS),
    ])
    fragment = suggest_profile(load(har), {"1234": "{key}"})
    assert "[[pair.page]]" in fragment
    assert "/reports/view" in fragment
    assert "RECORD_ID={key}" in fragment
    assert 'allow_hosts = ["a.example.com"]' in fragment


def test_suggestion_is_honest_when_nothing_carries_data(tmp_path):
    har = write(tmp_path, [entry("https://a.example.com/x", response="<html/>")])
    assert "No response in this capture" in suggest_profile(load(har))


@pytest.mark.parametrize("content,problem", [
    ("not json at all", "not valid JSON"),
    ('{"log": {}}', "no log.entries"),
    ('{"nope": 1}', "no log.entries"),
])
def test_a_bad_har_explains_how_to_export_one(tmp_path, content, problem):
    path = tmp_path / "bad.har"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(InvalidHarError) as exc:
        load(path)
    assert problem in str(exc.value)
    assert "Save all as HAR with content" in str(exc.value)


def test_reading_a_har_touches_no_network(tmp_path, monkeypatch):
    """A HAR is a local file; parsing one must never make a request."""
    import sitediff.source as source_mod

    def explode(*args, **kwargs):
        raise AssertionError("har parsing must not fetch anything")

    monkeypatch.setattr(source_mod, "fetch", explode)
    har = write(tmp_path, [entry("https://a.example.com/d", response=NUMBERS)])
    assert load(har).data_endpoints
