# Contributing

Thanks for looking. This is a small, deliberately boring codebase: standard
library where possible, three runtime dependencies, and every site-specific
decision pushed into a profile rather than into the code.

## Getting set up

```bash
git clone https://github.com/jay739/sitediff
cd sitediff
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Requires Python 3.11 or newer (`tomllib` is used for profiles).

## Before you open a pull request

```bash
.venv/bin/ruff check sitediff tests    # must pass
.venv/bin/pytest                       # must pass
.venv/bin/python -m build              # packaging must not break
```

CI runs the tests on Linux, macOS and Windows against 3.11 and 3.12, then
installs the built wheel into a clean environment and runs the console script.
A wheel that builds is not a wheel that works, so that last step matters.

## The rules that are not negotiable

**No site-specific values in the package.** No hostnames, no paths, no
selectors, no entity ids. Everything a site needs lives in a profile. Two tests
enforce this, and they will fail your PR:

- no engine module may contain a host literal
- bundled profiles may only name RFC 2606 documentation domains

**The tool contacts nothing it was not told to.** The egress allowlist is
checked before every connection, including each redirect hop, and it is empty
by default. If you add a code path that opens a socket, it goes through
`sitediff/source.py` and the same policy.

**Documentation is generated where it can be.** The command reference in
`docs/commands.md` and the configuration key tables are checked against the
parser by tests. If you add a flag or a profile key, document it or CI will
tell you.

## Adding things

**A profile key** — add it to the parser in `sitediff/config.py`, to the
relevant `*_KEYS` set (unknown keys are rejected, deliberately), and to
`docs/configuration.md`.

**A CLI flag** — give it a `help=` string and a short form if a sensible letter
is free within that subcommand. Regenerate `docs/commands.md`.

**A capture strategy** — `sitediff/capture.py`. It must fail with an actionable
message when it cannot produce a payload, naming what to try instead.

**A framework signature for `probe`** — `sitediff/probe.py`. Meta-frameworks are
listed before the view layer beneath them, and patterns must tolerate versioned
filenames (`jquery-3.6.0.min.js`, not just `jquery.min.js`).

## Style

Errors carry a typed class from `sitediff/errors.py` and a message that names
the failure *and* the fix. `"Could not fetch X"` is not enough; say what to
change. There are no bare `except Exception` handlers except where one entity
failing must not discard a run of thousands, and those record the exception
type so real defects stay visible.

Comments explain *why*, not *what*. If a line needs a comment to say what it
does, rewrite the line.

## Reporting a bug

A profile that reproduces it is worth more than a description. Redact hostnames
if you need to; the shape is what matters. Please include the `sitediff
--version`, your Python version, and the OS.
