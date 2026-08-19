# Configuration reference

Everything a profile can contain. Any key not listed here is rejected with the closest valid one suggested.

[← back to the README](../README.md)

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
| `left_path` / `right_path` | string | required unless `[[pair.page]]` is used | URL templates, each containing `{key}` |
| `left_body` / `right_body` | string | `""` | Request bodies for the single-page shorthand |
| `keys` | list | `[]` | Entity keys, when you already know them |
| `workers` | int | `4` | Parallel comparisons |
| `delay_ms` | int | `0` | Pause before each comparison |

### `[[pair.page]]` (repeatable)

One comparable page type, applied to every entity. Mutually exclusive with
`left_path`/`right_path` directly under `[pair]`.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `name` | string | **required** | Page identifier, shown in results. Must be unique |
| `left_path` / `right_path` | string | **required** | URL templates, each containing `{key}` |
| `left_body` / `right_body` | string | `""` | Per-page request body, overriding the source's |

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
