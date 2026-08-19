# sitediff

Compare what two renderings of the same page actually say, when the two sides
are produced by different codebases and cannot be compared byte for byte.

Built for cross-environment parity checks:

| Check | Mode | What it catches |
|---|---|---|
| Legacy against rewrite | `values` | Missing database values, dropped fields, reworded copy |
| Region against region | `values` | Replication lag, a region serving stale data |
| Cache or CDN against origin | `values` | Stale edge content |
| Reverse proxy against direct address | `values` | The proxy answering 200 with the wrong service's content |
| Staging against production | `shape` | Missing fields and structural drift, values ignored |

It is a local command line tool. There is no server, no agent, and no cloud
component.

## Why not an existing tool

Screenshot differs (Percy, BackstopJS, Playwright's `toHaveScreenshot`) compare
pixels, so a blank cell where a number should be is a tiny delta while every
legitimately different value is a huge one. Crawl-and-compare tools key their
comparison on URL, which fails the moment the two sides have different URL
schemes. `difflib` on saved HTML gives you a wall of noise from timestamps and
boilerplate.

sitediff pairs pages across sites that disagree about URLs, subtracts the noise
each side generates on its own, and reports what is left.

## Install

```
pip install sitediff
```

Requires Python 3.11 or newer. Runs on Linux, macOS and Windows. Colour, box
drawing and terminal width are handled per platform, including VT mode on
Windows consoles, with ASCII fallback where the code page cannot render
Unicode.

## Quick start

Compare two saved HTML files:

```
sitediff compare legacy.html rewrite.html
```

Compare two live endpoints defined in a profile:

```
sitediff compare direct:/ public:/ -p homelab-proxy
```

See how a page was served, without diffing:

```
sitediff fetch public:/status -p homelab-proxy
```

Show what a profile is allowed to contact:

```
sitediff sources -p homelab-proxy
```

Exit codes follow the diff convention, so this composes in CI:

| Code | Meaning |
|---|---|
| `0` | The two sides agree |
| `1` | Drift was found |
| `2` | The run failed |

## Noise cancellation

Two captures of the *same* side will disagree wherever a page is
non-deterministic: timestamps, request ids, unstable collection ordering, or a
client-rendered page captured before it settled. Pass both and anything that
varies is excluded from the real comparison.

```
sitediff compare legacy.html rewrite.html --baseline legacy.html legacy-again.html
```

Exact strings are not enough on their own, because the side under comparison
carries a third distinct value that was never in the baseline. So each pair of
corresponding unstable lines also yields a template, with the volatile spans
learned by aligning the two captures rather than guessed from what an
identifier is assumed to look like.

This doubles as a validation gate. If the baseline is not clean, your capture
is not reproducible, and every result built on it is unreliable. Finding that
out first is the point.

## Profiles

Everything site-specific lives in a TOML profile, so the tool itself stays
generic. Nothing about any particular site is compiled into the engine.

### Where profiles live

Profiles that describe a real site name real hosts, addresses and sometimes
clients. That is infrastructure detail, and it does not belong in this
repository. It belongs in yours, where it stays under your own review.

Named profiles are looked up in this order, most specific first:

| Location | Use |
|---|---|
| `$SITEDIFF_PROFILES` | Explicit directories, colon separated (semicolon on Windows) |
| `./.sitediff/` | Checked into the consuming team's own repo, alongside its CI |
| Per-user config dir | `~/.config/sitediff`, `~/Library/Application Support/sitediff`, or `%APPDATA%\sitediff` |
| Bundled with the package | Generic templates only |

An earlier entry shadows a later one, so a local profile overrides a bundled
template of the same name. `sitediff profiles` prints what it found and where,
along with every directory it searched.

A path is always accepted directly: `sitediff compare a.html b.html -p ./my.toml`.

Only `default` and `example` ship with the package, and both are templates. Copy
`example` into `./.sitediff/` and fill in your own hosts. Note that the
allowlist being versioned in your repo is a feature, since it is a security
control and benefits from review.

```toml
[profile]
name = "homelab-proxy"
description = "Does the public hostname serve the same thing as the direct address?"

[[source]]
name = "direct"
base_url = "http://203.0.113.10:8096"

[[source]]
name = "public"
base_url = "https://app.example.com"
# Connect to a chosen address while keeping the original Host header and TLS
# SNI. This is how you reach one region behind a geo-routed hostname, the
# origin behind a CDN, or a service by tunnel address rather than public name.
connect_to = "203.0.113.10"

[extract]
include_selectors = ["main"]
exclude_selectors = ["nav", "footer"]
render_sentinels = ["Loading... please wait"]

[compare]
mode = "values"          # or "shape" when the sides hold different data
context_lines = 3
headers = ["server", "content-type"]
ignore_patterns = ["All rights reserved"]

[[normalize]]
pattern = 'https?://[A-Za-z0-9.-]+(:\d+)?'
replacement = '{origin}'

[security]
allow_hosts = [".jay739.dev", "203.0.113.10"]

[[redact]]
pattern = '(?i)(api[_-]?key|token|password|secret)["\s:=]+\S+'
```

### Response metadata

For proxy and region checks the defect is frequently not in the body. sitediff
records the resolved address, HTTP status, redirect chain, TLS issuer and
expiry, and any headers you list, then diffs those alongside the content.
Identical content served from a different upstream is reported as drift, which
is the reverse proxy failure that every status check reports as green.

## Security posture

Teams adopting this tool ask what it does with their internal pages. The short
answer is that it is a local CLI with no egress it was not told to make.

**No network access by default.** A profile with no sources and no
`allow_hosts` has an empty allowlist, and an empty allowlist blocks everything.

**Nothing is hardcoded.** There is not a single hostname or address literal in
any engine module, and a test enforces that. Every host the tool may contact
comes from the profile you write. Bundled templates may reference only RFC 2606
documentation domains, which cannot reach anything real, and that is enforced
too.

**Egress allowlist.** The tool contacts only hosts the profile names. This is
checked before every connection, including each redirect hop, so an allowed
host cannot bounce it to somewhere else. A `connect_to` override is a second
destination and requires its own entry. Suffix entries (`.example.com`) match
subdomains only, not lookalikes such as `example.com.attacker.net`.

**No phoning home.** No telemetry, no analytics, no auto-update, no license
check, no crash reporting. The only outbound requests are the ones your profile
configures. Verify with a packet capture, or by reading `sitediff/source.py`,
which is the single file that opens a socket.

**Local only.** It reads the files and URLs you name and writes the report path
you name. Nothing else is written and nothing is retained between runs.

**Redaction.** `[[redact]]` rules are applied last, after every other
transformation, to both page content and response metadata. Diff reports become
CI artifacts and get passed around, so anything sensitive should be redacted
before it can reach one.

**Small dependency tree.** All network I/O uses the Python standard library, so
reviewing what goes on the wire does not mean auditing an HTTP stack. The only
runtime dependencies are `beautifulsoup4`, `lxml` and `rich`.

### What it deliberately does not do

It does not detect whether you are on a particular network, and it will not
refuse to run based on your location. That check would be bypassable in a
one-line patch, redundant with targets that are already unreachable from
outside, and it would require the tool to inspect your network interfaces,
which is more invasive than the problem it claims to solve. If a target should
only be reachable from inside a perimeter, enforce that at the network, where
enforcement is real.

## Development

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT
