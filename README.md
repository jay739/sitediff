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

## Running across many entities

Comparing two pages is a spot check. A migration means comparing thousands, and
that needs two things existing tools do not give you: a way to pair pages
across sites that disagree about URLs, and a way to stop one intentional change
from being reported once per entity.

### Pairing

Every other tool keys its comparison on URL, so two sites with different URL
schemes produce two unrelated lists. sitediff pairs on an **entity key**
instead.

```toml
[pair]
left = "legacy"
right = "rewrite"
left_path = "/api/GetRecord?id={key}"
right_path = "/records/{key}"
workers = 4
delay_ms = 200          # be polite to the far end

[pair.discover]         # or list them outright with keys = ["1234", ...]
source = "legacy"
path = "/index?page={param}"
params = ["01", "02", "03"]
pattern = 'Record/(\d{4})/'
```

Keys come from one of three places, in order of preference:

**`keys`** when you already know them.

**`[pair.discover]`** scrapes them from a listing endpoint. This matters when a
site cannot be crawled at all (hash routing, JavaScript navigation) while still
exposing a perfectly ordinary index.

### Entities identified by more than one value

An entity is not always addressed by a single id. A Example State school needs both a
county and a school id, and both have to reach the URL and the request body. Use
named groups, one of which must be `key`:

```toml
[pair]
left_path = "/api?lea={lea}&id={key}"
right_path = "/school/{key}"

[pair.discover]
source = "legacy"
path = "/Directory/Index?l={param}"
params = ["01", "02", "30"]
pattern = 'RecordDetail/1/\w+/1/(?P<lea>\d+)/(?P<key>\d{4})/0'
```

`key` is the identity used for reporting and for the accepted-difference store.
Every other named group is available as a placeholder in `left_path`,
`right_path` and a source's `body`.

**`[pair.crawl]`** points at one apex domain and follows links from there:

```toml
[pair.crawl]
source = "rewrite"                  # crawl whichever side has real URLs
seeds = ["/"]
key_pattern = '/school/(\d{4})'      # matched against the URL, one capture group
follow = '^/(school|district)'      # optional: restrict which links to follow
max_pages = 500
delay_ms = 200
respect_robots = true
```

Crawl the side that is crawlable and derive the other side's URLs from the key.
That asymmetry is the normal case: a rewrite is usually server-rendered with
real paths while the legacy app is hash-routed and exposes no crawlable URLs at
all. The crawl stays on the seed's origin regardless of the allowlist, and
honours `robots.txt` unless told otherwise.

```
sitediff run -p my-profile --limit 10     # smoke test first
sitediff run -p my-profile --html run.html
sitediff runs                             # history
```

Results go to SQLite at `.sitediff/runs.db`. A few thousand rows needs no
server, and local storage preserves the property the security posture rests on.

### Accepted differences

Some differences are intentional. Without a way to record that, a single
reworded footer is reported on every entity and buries everything that matters:

```
clean  0   drift  12
  4002  drift  ~1  alter footer: Example State Department of Education -> ESDE
  4004  drift  ~1  alter footer: Example State Department of Education -> ESDE
  ... twelve identical findings, three real bugs among them
```

Record the decision once:

```
sitediff accept legacy:/api/GetRecord?id=4002 rewrite:/records/4002 \
  -p my-profile --reason "rewrite shortened the department name by design"
```

```
clean  9   drift  3
12 finding(s) suppressed as accepted
  4003  drift  ~1  alter metrics.pctProficient: 42.1 -> (absent)
  4007  drift  ~1  alter metrics.pctProficient: 44.9 -> (absent)
  4009  drift  ~1  alter metrics.attendance: 91.8 -> 96.8
```

Fingerprints deliberately exclude the entity a finding came from, so accepting
once suppresses it everywhere. The store lives at `.sitediff/accepted.toml`
**in your own repository**: accepting a difference should be a reviewed change
with an author and a date, not a click that disappears. `--dry-run` shows what
would be recorded without writing.

## Comparing elements, not only text

Text extraction flattens a page into lines and discards the markup, so a search
box that vanished, a table that lost a column, or an image that lost its alt
text are all invisible to a content diff. Those are exactly the regressions a
rewrite introduces, and they are what a person notices first.

Every comparison of two HTML pages therefore also compares what the pages are
**made of**, and reports it in plain language:

```
47 element(s) on legacy are absent from the rewrite.
59 element(s) appear on the rewrite that are not on legacy.
19 element type(s) appear a different number of times.

Missing from the rewrite (47)
  Button 'Download CSV' is on legacy but not on the rewrite.
  Button 'Download PDF' is on legacy but not on the rewrite.
  Radio button 'Trend' is on legacy but not on the rewrite.

Different shape (3)
  Table 'Indicators' is 12 rows x 6 columns on legacy but 12 rows x 5 columns
  on the rewrite.
```

Inventoried: headings (with level), tables (with row and column counts), form
controls named by their label, buttons, images (flagging missing alt text),
landmarks, and lists. Anonymous containers are ignored, since a `<div>` nobody
can name is not a finding.

Structural findings count toward the verdict, so a page whose text matches
exactly still reports drift if a control disappeared. They can be accepted like
any other intentional difference. Pass `--no-structure` to compare text only.

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

### `[pair]`

Required only for `sitediff run`. `sitediff compare` needs none of it.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `left` / `right` | string | **required** | Source names for the two sides |
| `left_path` / `right_path` | string | **required** | URL templates, each containing `{key}` |
| `keys` | list | `[]` | Entity keys, when you already know them |
| `workers` | int | `4` | Parallel comparisons |
| `delay_ms` | int | `0` | Pause before each comparison |

### `[pair.crawl]`

Alternative to `[pair.discover]`: find keys by crawling one side from a seed.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `source` | string | **required** | Which side to crawl. Pick the one with real URLs |
| `key_pattern` | regex | **required** | Matched against each URL. One capture group, or named groups including `(?P<key>...)` |
| `seeds` | list | `["/"]` | Paths to start from |
| `follow` | regex | `""` | Only follow links whose path matches |
| `max_pages` | int | `500` | Ceiling on pages fetched |
| `delay_ms` | int | `200` | Pause between fetches |
| `respect_robots` | bool | `true` | Honour the origin's `robots.txt` |

### `[pair.discover]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `source` | string | **required** | Source serving the listing |
| `path` | string | **required** | Listing path, optionally containing `{param}` |
| `pattern` | regex | **required** | One capture group (the entity key), or named groups including `(?P<key>...)` |
| `params` | list | `[]` | Values substituted into `{param}`, one request each |
| `base_url` | string | `""` | Scheme, host and optional port |
| `connect_to` | string | `""` | Address to actually connect to, keeping the original `Host` header and TLS SNI. Reaches one region behind a geo-routed name, an origin behind a CDN, or a service by tunnel address |
| `headers` | table | `{}` | Extra request headers, for example `Authorization` |
| `method` | string | `GET` | HTTP method. Data endpoints on server-rendered stacks are often POST actions |
| `body` | string | `""` | Request body, sent as JSON unless `headers` says otherwise. `{key}` is substituted with the entity key during a run |
| `timeout` | number | `30.0` | Seconds before a request is abandoned |
| `verify_tls` | bool | `true` | Set false only for an endpoint expected to present an untrusted certificate |
| `follow_redirects` | bool | `true` | Follow 3xx responses |
| `max_redirects` | int | `5` | Hops before giving up |

### `[extract]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `include_selectors` | list | `[]` | CSS selectors to compare. Empty means the whole `<body>`. Nested matches are de-duplicated, so only the outermost is walked |
| `exclude_selectors` | list | `[]` | CSS selectors removed before comparison, such as `nav` or `footer` |
| `render_sentinels` | list | `[]` | Strings meaning the page was captured before rendering finished. A match aborts the run, since diffing a half-rendered page reports every value as missing |
| `hidden_classes` | list | `hidden`, `hide`, `d-none`, `sr-only`, `visually-hidden`, `screen-reader-only` | Class names that hide content via CSS |
| `hidden_styles` | list | `display:none`, `visibility:hidden` | Inline style fragments that hide content |
| `respect_aria_hidden` | bool | `true` | Treat `aria-hidden="true"` as hidden |
| `noise_tags` | list | `script`, `style`, `noscript`, `template`, `svg`, `path`, `iframe`, `canvas`, `object`, `embed` | Tags removed entirely |
| `block_tags` | list | the block-level HTML tags | Tags that start a new comparison line. Inline tags such as `span` and `a` are deliberately excluded: treating them as blocks splits one sentence across several lines and separates every value from its label |
| `parser` | string | `lxml` | BeautifulSoup parser. Falls back to `html.parser` if unavailable |

### `[extract.left]` and `[extract.right]`

The two sides are different codebases, so one set of selectors rarely fits both.
These sub-tables override the shared `[extract]` block for one side only, and
accept `include_selectors`, `exclude_selectors`, `render_sentinels`,
`hidden_classes`, `hidden_styles` and `parser`.

```toml
[extract.left]
include_selectors = ["#GraphContent"]

[extract.right]
include_selectors = ["#main"]
```

### `[compare]`

| Key | Type | Default | Purpose |
|---|---|---|---|
| `mode` | `values` or `shape` | `values` | `values` when both sides read the same data and any disagreement is a defect. `shape` when they hold different data by design, which masks values and compares labels and structure |
| `context_lines` | int | `3` | Unchanged lines shown around each change |
| `ignore_case` | bool | `false` | Fold letter case before comparing. Two codebases routinely differ in case where CSS `text-transform` renders them identically |
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

## Reports

`--html` writes a self-contained page. No external stylesheet, script, font or
image, so it opens on a machine with no network.

**Two layouts, toggled in the toolbar and remembered between reports:**

*Side by side* is the default. Each row shows what both versions say, with only
the words that actually moved highlighted. That answers "what does each side
say", which is the question a parity check is asking. A line present on one side
only gets a hatched cell opposite it, so a gap reads as a gap rather than as
blank space.

*Unified* is the familiar `git diff` layout, better when reviewing an edit
rather than comparing two versions.

Also: filter chips, text search (`/` to focus, `Esc` to clear), collapsible
sections, sortable run tables, a persisted light/dark toggle, and animations
that disable themselves under `prefers-reduced-motion`.

Worked examples, committed so you can see the output without running anything:

| Example | Shows |
|---|---|
| [`docs/report.example.html`](docs/report.example.html) | Text diff, hunks and context |
| [`docs/report-fields.example.html`](docs/report-fields.example.html) | Structured diff, field by field |
| [`docs/report-run.example.html`](docs/report-run.example.html) | A run across many entities |

The look lives in `sitediff/templates/report.css` and `report.js`, which are
ordinary files you can edit. They are inlined at build time. **Generated
reports are build output and are gitignored; the templates that produce them
are tracked.**

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
