"""Tests for the command line surface itself.

This layer had no coverage, which is how three separate rich-markup bugs
survived: every other test calls the library directly and never renders a
single line of output.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sitediff.cli import EXIT_CLEAN, EXIT_DRIFT, EXIT_ERROR, main

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
LEGACY = str(FIXTURES / "legacy.html")
OPTIMISED = str(FIXTURES / "optimised.html")


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- exit codes --------------------------------------------------------------

def test_identical_pages_exit_clean(capsys):
    code, out, _ = run(capsys, "compare", LEGACY, LEGACY)
    assert code == EXIT_CLEAN
    assert "CLEAN" in out


def test_differing_pages_exit_drift(capsys):
    code, out, _ = run(capsys, "compare", LEGACY, OPTIMISED)
    assert code == EXIT_DRIFT
    assert "DRIFT" in out


def test_failure_exits_error(capsys):
    code, _, err = run(capsys, "compare", "/nope/missing.html", LEGACY)
    assert code == EXIT_ERROR
    assert "No such HTML file" in err


# --- error output ------------------------------------------------------------

def test_errors_go_to_stderr_leaving_stdout_clean(capsys):
    _, out, err = run(capsys, "compare", "/nope/missing.html", LEGACY)
    assert err.strip()
    assert "error" not in out.lower()


def test_bracketed_profile_keys_survive_the_markup_parser(capsys):
    """Regression: rich consumed [security] from the message telling you to
    edit [security], so the guidance named no key at all."""
    profile = FIXTURES / "bad_key.toml"
    profile.write_text('[security]\nallow_host = ["a.example.com"]\n', encoding="utf-8")
    try:
        code, _, err = run(capsys, "compare", LEGACY, OPTIMISED, "-p", str(profile))
        assert code == EXIT_ERROR
        assert "[security]" in err
        assert "did you mean 'allow_hosts'" in err
    finally:
        profile.unlink()


def test_a_typed_url_authorises_its_own_host(capsys):
    """Deliberate loosening: typing a URL is the consent for that one host.

    It must therefore reach the network and fail there, not be refused by an
    allowlist it was never going to be in. Enforcement for configured sources
    and for redirect hops is covered in test_source.py, where it still applies.
    """
    profile = FIXTURES / "empty_egress.toml"
    profile.write_text('[profile]\nname = "t"\n', encoding="utf-8")
    try:
        code, _, err = run(capsys, "compare", LEGACY,
                           "https://blocked.invalid./", "-p", str(profile))
        assert code == EXIT_ERROR
        assert "egress allowlist" not in err.lower()
        assert "Could not fetch" in err
    finally:
        profile.unlink()


def test_a_configured_source_cannot_reach_an_unlisted_host(capsys, tmp_path):
    """connect_to points somewhere the profile never named, so it is refused."""
    profile = tmp_path / "p.toml"
    profile.write_text(
        '[[source]]\nname = "s"\nbase_url = "https://a.example.com"\n'
        '[security]\nallow_hosts = ["a.example.com"]\n',
        encoding="utf-8",
    )
    code, _, _err = run(capsys, "compare", LEGACY, "s:/", "-p", str(profile))
    assert code == EXIT_ERROR


# --- rendering ---------------------------------------------------------------

def test_unified_view_marks_added_and_removed_lines(capsys):
    _, out, _ = run(capsys, "compare", LEGACY, OPTIMISED)
    assert "45.2%" in out
    assert "@@" in out


def test_side_by_side_renders(capsys):
    code, out, _ = run(capsys, "compare", LEGACY, OPTIMISED, "-s")
    assert code == EXIT_DRIFT
    assert "left" in out and "right" in out


def test_quiet_suppresses_the_body_but_keeps_the_verdict(capsys):
    _, out, _ = run(capsys, "compare", LEGACY, OPTIMISED, "-q")
    assert "DRIFT" in out
    assert "@@" not in out


def test_no_color_emits_no_escape_sequences(capsys):
    _, out, _ = run(capsys, "compare", LEGACY, OPTIMISED, "--no-color")
    assert "\x1b[" not in out


# --- structured path ---------------------------------------------------------

def test_json_capture_reports_fields_not_lines(capsys, tmp_path):
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text(json.dumps({"id": "1234", "pct": 45.2}), encoding="utf-8")
    right.write_text(json.dumps({"id": "1234", "pct": None}), encoding="utf-8")
    code, out, _ = run(capsys, "compare", str(left), str(right), "--capture", "json")
    assert code == EXIT_DRIFT
    assert "pct" in out
    assert "@@" not in out


def test_json_captured_as_text_produces_a_note(capsys, tmp_path):
    data = tmp_path / "a.json"
    data.write_text('{"a": 1}', encoding="utf-8")
    _, out, _ = run(capsys, "compare", str(data), str(data))
    assert "note:" in out


# --- reports -----------------------------------------------------------------

@pytest.mark.parametrize("extra", [[], ["--capture", "json"]])
def test_html_report_is_written(capsys, tmp_path, extra):
    """--html was advertised in --help and crashed, because nothing ran it."""
    if extra:
        left = tmp_path / "a.json"
        right = tmp_path / "b.json"
        left.write_text('{"a": 1}', encoding="utf-8")
        right.write_text('{"a": 2}', encoding="utf-8")
        args = [str(left), str(right)]
    else:
        args = [LEGACY, OPTIMISED]
    out_file = tmp_path / "nested" / "report.html"
    code, _, _ = run(capsys, "compare", *args, *extra, "--html", str(out_file), "-q")
    assert code == EXIT_DRIFT
    assert out_file.is_file()
    assert "<!doctype html>" in out_file.read_text(encoding="utf-8")


# --- other subcommands -------------------------------------------------------

def test_extract_lists_visible_lines(capsys):
    code, out, _ = run(capsys, "extract", LEGACY)
    assert code == EXIT_CLEAN
    assert "Percent Proficient" in out


def test_profiles_lists_bundled_and_shows_the_search_path(capsys):
    code, out, _ = run(capsys, "profiles")
    assert code == EXIT_CLEAN
    assert "example" in out and "Searched" in out


def test_sources_reports_the_egress_allowlist(capsys):
    code, out, _ = run(capsys, "sources", "-p", "example")
    assert code == EXIT_CLEAN
    assert "Egress allowlist" in out


def test_probe_reports_a_strategy_and_frameworks(capsys):
    code, out, _ = run(capsys, "probe", LEGACY)
    assert code == EXIT_CLEAN
    assert "capture" in out


def test_probe_suggest_emits_literal_toml_sections(capsys):
    """Regression: the markup parser rendered [[source]] as an empty list."""
    code, out, _ = run(capsys, "probe", LEGACY, "--suggest")
    assert code == EXIT_CLEAN
    assert "[[source]]" in out
    assert "[security]" in out


def test_baseline_reports_a_stable_capture(capsys):
    code, out, _ = run(capsys, "compare", LEGACY, OPTIMISED,
                       "--baseline", LEGACY, LEGACY, "-q")
    assert code == EXIT_DRIFT
    assert "stable" in out.lower()


# --- run, accept, runs -------------------------------------------------------

def _pair_profile(tmp_path, left_json, right_json):
    """Two file-backed sides is not enough for `run`, which needs sources, so
    these exercise the argument handling and error paths around it."""
    path = tmp_path / "p.toml"
    path.write_text('[profile]\nname = "t"\n', encoding="utf-8")
    return path


def test_run_without_a_pair_section_explains_what_to_add(capsys, tmp_path):
    profile = _pair_profile(tmp_path, None, None)
    code, _, err = run(capsys, "run", "-p", str(profile))
    assert code == EXIT_ERROR
    assert "[pair]" in err
    assert "sitediff compare" in err


def test_runs_reports_nothing_recorded(capsys, tmp_path):
    code, out, _ = run(capsys, "runs", "--db", str(tmp_path / "none.db"))
    assert code == EXIT_CLEAN
    assert "No runs recorded" in out


def test_accept_dry_run_writes_nothing(capsys, tmp_path):
    store = tmp_path / "acc.toml"
    code, out, _ = run(capsys, "accept", LEGACY, OPTIMISED,
                       "--accept-file", str(store), "--dry-run")
    assert code == EXIT_CLEAN
    assert "Dry run" in out
    assert not store.exists()


def test_accept_writes_the_store_then_suppresses_the_findings(capsys, tmp_path):
    store = tmp_path / "acc.toml"
    code, out, _ = run(capsys, "accept", LEGACY, OPTIMISED,
                       "--accept-file", str(store), "--reason", "expected")
    assert code == EXIT_CLEAN
    assert store.is_file()
    assert "expected" in store.read_text(encoding="utf-8")

    code, out, _ = run(capsys, "compare", LEGACY, OPTIMISED,
                       "--accept-file", str(store))
    assert code == EXIT_CLEAN
    assert "suppressed as accepted" in out


def test_accept_is_idempotent(capsys, tmp_path):
    store = tmp_path / "acc.toml"
    run(capsys, "accept", LEGACY, OPTIMISED, "--accept-file", str(store))
    code, out, _ = run(capsys, "accept", LEGACY, OPTIMISED, "--accept-file", str(store))
    assert code == EXIT_CLEAN
    assert "Nothing new to accept" in out
