# Running across many entities

Comparing two pages is a spot check. A migration means comparing thousands, and that needs pairing, page types, and a way to record intentional differences.

[← back to the README](../README.md)

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

### Several pages per entity

An entity is rarely one page. A school has a report card, a demographics view,
an assessments view and more, and each needs comparing. Declare them and every
entity is compared on every page:

```toml
[pair]
left = "legacy"
right = "rewrite"

[[pair.page]]
name = "report-card"
left_path = "/Main/Dispatch"
right_path = "/school/{key}/report-card"
left_body = "FILTERS=1/{span}/1/{lea}/{key}/0&Call=GetRecordDetail"

[[pair.page]]
name = "demographics"
left_path = "/Main/Dispatch"
right_path = "/school/{key}/demographics"
left_body = "FILTERS=1/{span}/1/{lea}/{key}/0&Call=GetDemographics"
```

`left_body` and `right_body` exist because a dispatching backend often selects
the view from a payload parameter rather than from the path, so the URL alone
cannot distinguish one page from another.

Results are keyed by entity **and** page, and both the terminal and the report
break the totals down per page, since a flat number hides which view regressed:

```
entities  1,412 x 6 pages = 8,472 comparisons  in 214s
  report-card    1,410 clean   2 drift   0 failed
  demographics   1,412 clean   0 drift   0 failed
  mcap           1,398 clean  14 drift   0 failed
```

Comparing a single page per entity needs no page list; `left_path` and
`right_path` directly under `[pair]` still work.

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
