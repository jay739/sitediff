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

Work out how a site delivers its values before writing any profile:

```
sitediff probe https://app.example.com --suggest
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

## Probing an unfamiliar site

`sitediff probe` answers, from a single plain GET with no JavaScript executed,
the question you would otherwise answer by opening devtools and reading the
Network tab:

- **Which capture strategy this site needs**, with the viable alternatives
  ranked rather than a single answer forced. A server-rendered single page app
  genuinely supports more than one.
- **What it is built with.** Next.js, Nuxt, SvelteKit, Remix, Gatsby, Astro,
  Angular, AngularJS, Knockout, Ember, Backbone, Vue, React, HTMX, Alpine,
  jQuery. Meta-frameworks are named ahead of the view layer beneath them.
- **Embedded state**, and how large it is: `__NEXT_DATA__`, `self.__next_f`,
  `__NUXT__`, `__remixContext`, `__INITIAL_STATE__`, `__PRELOADED_STATE__`,
  `__APOLLO_STATE__`.
- **Candidate data endpoints** pulled out of inline script and markup, which is
  the Network-tab substitute.
- **Loading placeholders**, ready to paste as `render_sentinels`.
- **How much of the payload is readable text**, the clearest no-JS signal that
  a page is client-rendered.
- **Response metadata**: status, resolved address, TLS issuer, headers.

`--suggest` prints a profile fragment you can paste and edit. It is a loadable
profile, not pseudocode.

### Capture strategies

| Strategy | Where values come from | Browser |
|---|---|---|
| `static` | Already in the served HTML | No |
| `hydration` | Embedded state in a script tag | No |
| `json` | A data endpoint called directly | No |
| `browser` | The settled DOM after JS runs | Yes |

Preference runs hydration, then json, then static, then browser. Structured
data beats text for a verification tool, because a field diff names the field
that moved instead of handing back a hunk to interpret. Browser support is an
optional extra, never a base dependency.

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

## Configuration reference

Every key the tool accepts is listed below. Anything else is rejected with an
error naming the closest valid key, so a typo cannot look configured while
doing nothing.

Only `[[source]].name` is ever required. A profile with no sources compares
saved HTML files and never touches the network.

### `[profile]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `name` | string | filename stem | Name shown in listings |
| `description` | string | `""` | One line shown by `sitediff profiles` |

### `[[source]]` (repeatable)

One named endpoint. Reference it on the command line as `name:/path`.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `name` | string | **required** | Reference used as `name:/path` |
| `capture` | `static`, `hydration`, `json`, `browser` | `static` | How values are obtained. Run `sitediff probe` to find out which this site needs |
| `hydration_marker` | string | `""` | Name a specific embedded payload (`__NEXT_DATA__`, `__NUXT__`) when a page carries several |
| `base_url` | string | `""` | Scheme, host and optional port |
| `connect_to` | string | `""` | Address to actually connect to, keeping the original `Host` header and TLS SNI. Reaches one region behind a geo-routed name, an origin behind a CDN, or a service by tunnel address |
| `headers` | table | `{}` | Extra request headers, for example `Authorization` |
| `timeout` | number | `30.0` | Seconds before a request is abandoned |
| `verify_tls` | bool | `true` | Set false only for an endpoint expected to present an untrusted certificate |
| `follow_redirects` | bool | `true` | Follow 3xx responses |
| `max_redirects` | int | `5` | Hops before giving up |

### `[extract]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `include_selectors` | list | `[]` | CSS selectors to compare. Empty means the whole `<body>` |
| `exclude_selectors` | list | `[]` | CSS selectors removed before comparison, such as `nav` or `footer` |
| `render_sentinels` | list | `[]` | Strings meaning the page was captured before rendering finished. A match aborts the run, since diffing a half-rendered page reports every value as missing |
| `hidden_classes` | list | `hidden`, `hide`, `d-none`, `sr-only`, `visually-hidden`, `screen-reader-only` | Class names that hide content via CSS |
| `hidden_styles` | list | `display:none`, `visibility:hidden` | Inline style fragments that hide content |
| `respect_aria_hidden` | bool | `true` | Treat `aria-hidden="true"` as hidden |
| `noise_tags` | list | `script`, `style`, `noscript`, `template`, `svg`, `path`, `iframe`, `canvas`, `object`, `embed` | Tags removed entirely |
| `block_tags` | list | the block-level HTML tags | Tags that start a new comparison line. Inline tags such as `span` and `a` are deliberately excluded: treating them as blocks splits one sentence across several lines and separates every value from its label |
| `parser` | string | `lxml` | BeautifulSoup parser. Falls back to `html.parser` if unavailable |

### `[compare]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `mode` | `values` or `shape` | `values` | `values` when both sides read the same data and any disagreement is a defect. `shape` when they hold different data by design, which masks values and compares labels and structure |
| `context_lines` | int | `3` | Unchanged lines shown around each change |
| `ignore_patterns` | list of regex | `[]` | Lines never reported. Use for intentional differences, otherwise one deliberate change is reported on every page and buries the real findings |
| `headers` | list | `[]` | Response headers compared alongside content, for example `server` |

### `[security]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `allow_hosts` | list | `[]` | Hosts this profile may contact. Hosts named by `[[source]]` are added automatically. A leading dot matches subdomains only, so `.example.com` permits `a.example.com` but not `example.com.attacker.net`. Empty everywhere means no network access at all |

### `[[normalize]]` and `[[redact]]` (repeatable)

`normalize` rewrites lines before comparison, for differences that are real but
uninteresting, such as absolute URLs that differ by environment. `redact`
removes sensitive text and runs last, after every other rule, on both page
content and response metadata.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `pattern` | regex | **required** | What to match |
| `replacement` | string | required for `normalize`, `[REDACTED]` for `redact` | What to substitute |
| `note` | string | `""` | Why this rule exists |

### Environment variables

| Variable | Purpose |
|---|---|
| `SITEDIFF_PROFILES` | Extra profile directories, searched first. Separated by `:` on Linux and macOS, `;` on Windows |
| `NO_COLOR` | Disable colour output, equivalent to `--no-color` |

### Worked example

```toml
[profile]
name = "proxy-parity"
description = "Does the public hostname serve the same thing as the direct address?"

[[source]]
name = "direct"
base_url = "http://10.0.0.50:8096"
timeout = 10

[[source]]
name = "public"
base_url = "https://app.example.com"
connect_to = "203.0.113.10"

[extract]
include_selectors = ["main"]
exclude_selectors = ["nav", "footer"]

[compare]
mode = "values"
headers = ["server", "content-type"]
ignore_patterns = ["All rights reserved"]

[[normalize]]
pattern = 'https?://[A-Za-z0-9.-]+(:\d+)?'
replacement = '{origin}'
note = "absolute URLs differ by design between the two paths"

[security]
allow_hosts = [".example.com", "10.0.0.50", "203.0.113.10"]

[[redact]]
pattern = '(?i)(api[_-]?key|token|password|secret)["\s:=]+\S+'
note = "diff reports become CI artifacts"
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

**A URL you type is its own authorisation.** Passing a URL directly on the
command line permits that one host for that run, since typing it is the
consent. Everything else still comes from the profile, and redirects from that
URL remain constrained, so an ad-hoc target cannot bounce the tool onward.

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
