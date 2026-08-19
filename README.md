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

## What you must provide

This is a **diff engine**. It ships no knowledge of any site, and it will not
work until you tell it about yours. Concretely, you supply:

| You provide | Why | How to get it |
|---|---|---|
| **Two things to compare** | The tool never guesses what to fetch | Two saved HTML files, two URLs, or two sources in a profile |
| **A profile** for anything beyond a one-off file diff | All site knowledge lives here, none in the package | Copy the bundled `example` profile into `./.sitediff/` |
| **Hosts in `[security] allow_hosts`** | Nothing is contacted unless you name it. The default allowlist is empty and blocks everything | The hostnames of the two environments |
| **How each side delivers values** | HTML, embedded state, or a data endpoint: this cannot be inferred reliably | `sitediff probe <url>` tells you; `sitediff har <file>` finds endpoints |
| **URL templates containing `{key}`** | Two codebases disagree about URLs; pairing is on an entity key, not a path | Read them off the two sites |
| **How to enumerate entities**, for a `run` | The tool will not crawl anything you have not pointed it at | A key list, a listing endpoint (`[pair.discover]`), or a seed to crawl (`[pair.crawl]`) |
| **Which region of each page holds the data** | Two codebases have different DOMs, so one selector set rarely fits both | `[extract.left]` and `[extract.right]` |
| **Network access to both sides** | It runs where you run it and has no other route in | Run it from a machine that can reach both |

It does **not** provide: a crawler that works without configuration, credentials
or session handling, knowledge of your URL scheme, or any judgement about which
differences are acceptable. That last one is `sitediff accept`, and it is
deliberately a reviewed decision rather than a heuristic.

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

## Documentation

| Guide | Covers |
|---|---|
| [Commands](docs/commands.md) | Every command and flag, with short forms and aliases |
| [Configuration](docs/configuration.md) | Every key a profile can contain |
| [Discovery](docs/discovery.md) | Working out how a site delivers its values, with `probe` and `har` |
| [Running at scale](docs/scale.md) | Pairing entities, several pages each, accepted differences, noise |
| [What gets compared](docs/comparing.md) | Content, page structure, and the reports |
| [Security posture](docs/security.md) | What it reads, writes and contacts, and what it will not do |

Worked report examples, viewable without running anything:
[text diff](docs/report.example.html) ·
[field diff](docs/report-fields.example.html) ·
[run summary](docs/report-run.example.html)

## Development

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT
