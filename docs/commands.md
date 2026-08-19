# Command reference

Every command and flag. `sitediff help` prints the same thing in your terminal, and each command has its own `--help`.

[← back to the README](../README.md)

## Commands

| Command | Aliases | Purpose |
|---|---|---|
| [`compare`](#sitediff-compare) | `c`, `diff` | diff two pages, from files, URLs or configured sources |
| [`extract`](#sitediff-extract) | `x` | show the visible lines pulled from one page |
| [`fetch`](#sitediff-fetch) | `f` | fetch one page and report how it was served |
| [`probe`](#sitediff-probe) | `p` | work out how a page delivers its values, and what it is built with |
| [`run`](#sitediff-run) | `r` | compare every paired entity on every page type, and aggregate |
| [`accept`](#sitediff-accept) | `a` | record the current differences as intentional |
| [`runs`](#sitediff-runs) | `rs` | list recent runs |
| [`har`](#sitediff-har) | `h` | read a browser HAR export and find the site's data endpoints |
| [`help`](#sitediff-help) | `?` | show every command and every option at once |
| [`profiles`](#sitediff-profiles) | `pf` | list profiles and the directories searched for them |
| [`sources`](#sitediff-sources) | `s` | list a profile's sources and its egress allowlist |


### `sitediff compare`

diff two pages, from files, URLs or configured sources

| Flag | Purpose |
|---|---|
| `<left>` | saved HTML file, URL, or source:/path (reference side) |
| `<right>` | saved HTML file, URL, or source:/path (candidate side) |
| `-p`, `--profile` | profile name or path to a .toml |
| `-s`, `--side-by-side` | two-column view |
| `-o`, `--html` | also write a standalone HTML report |
| `-b`, `--baseline` | two captures of the SAME side; anything differing between them is treated as inherent noise and suppressed from the main diff |
| `-C`, `--context` | context lines around each change |
| `--allow-unrendered` | compare even if a page still shows a loading placeholder |
| `--no-color` | disable colour output |
| `-q`, `--quiet` | summary only, no diff body |
| `-c`, `--capture` | override the capture strategy for both sides — one of `static`, `hydration`, `json`, `browser` |
| `--no-structure` | skip the element comparison, compare text only |
| `-A`, `--accept-file` | accepted-difference store (default .sitediff/accepted.toml) |

### `sitediff extract`

show the visible lines pulled from one page

| Flag | Purpose |
|---|---|
| `<page>` | saved HTML file, URL, or source:/path |
| `-p`, `--profile` | profile name or path to a .toml |
| `-n`, `--numbers` | prefix line numbers |
| `--allow-unrendered` | extract even if the page shows a loading placeholder |

### `sitediff fetch`

fetch one page and report how it was served

| Flag | Purpose |
|---|---|
| `<target>` | URL or source:/path |
| `-p`, `--profile` | profile name or path to a .toml |
| `-b`, `--body` | also print extracted lines |

### `sitediff probe`

work out how a page delivers its values, and what it is built with

| Flag | Purpose |
|---|---|
| `<target>` | URL, source:/path, or a saved HTML file |
| `-p`, `--profile` | profile name or path to a .toml |
| `-s`, `--suggest` | print a profile fragment you can paste and edit |

### `sitediff run`

compare every paired entity on every page type, and aggregate

| Flag | Purpose |
|---|---|
| `-p`, `--profile` | profile name or path to a .toml |
| `-l`, `--limit` | stop after N entities, for a smoke test |
| `-d`, `--db` | results database (default .sitediff/runs.db) |
| `-A`, `--accept-file` | accepted-difference store (default .sitediff/accepted.toml) |
| `-o`, `--html` | write a run report |
| `-w`, `--show` | rows of detail to print |
| `--no-color` | disable colour output |

### `sitediff accept`

record the current differences as intentional

| Flag | Purpose |
|---|---|
| `<left>` | reference side |
| `<right>` | candidate side |
| `-p`, `--profile` | profile name or path to a .toml |
| `-c`, `--capture` | override the capture strategy for both sides — one of `static`, `hydration`, `json`, `browser` |
| `-A`, `--accept-file` | accepted-difference store (default .sitediff/accepted.toml) |
| `-r`, `--reason` | why this difference is expected |
| `-n`, `--dry-run` | show what would be accepted, write nothing |

### `sitediff runs`

list recent runs

| Flag | Purpose |
|---|---|
| `-d`, `--db` | results database (default .sitediff/runs.db) |

### `sitediff har`

read a browser HAR export and find the site's data endpoints

| Flag | Purpose |
|---|---|
| `<file>` | path to a .har exported from devtools |
| `-a`, `--all` | list every endpoint, not only those carrying data |
| `-s`, `--suggest` | print a profile fragment wiring up the data endpoints |
| `-P`, `--placeholder` | replace a captured parameter value with {NAME} in the suggested body, e.g. --placeholder 1234=key. Repeatable |

### `sitediff help`

show every command and every option at once

Takes no options.

### `sitediff profiles`

list profiles and the directories searched for them

Takes no options.

### `sitediff sources`

list a profile's sources and its egress allowlist

| Flag | Purpose |
|---|---|
| `-p`, `--profile` | profile name or path to a .toml |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The two sides agree |
| `1` | Drift was found |
| `2` | The run failed |

## Environment variables

| Variable | Purpose |
|---|---|
| `SITEDIFF_PROFILES` | Extra profile directories, searched first |
| `NO_COLOR` | Disable colour output |
