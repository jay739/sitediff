# Working out how a site delivers its values

Before comparing anything you need to know where a page's values come from. These two commands answer that without guesswork.

[← back to the README](../README.md)

## Finding a site's data endpoints

Working out how an application fetches its values is the slowest part of
onboarding a site, and it is normally done by hand: open devtools, click every
view, read each request, guess which response carries the data. A HAR export
already contains all of it.

```
sitediff har capture.har --suggest
```

```
  13 endpoint(s) across 1 host(s)
  skipped 2 third-party and 0 asset request(s)

  Endpoints carrying data

  data POST /Records/GetRecordView/
           206,996b   202 numbers  text/html  x2
         payload (application/x-www-form-urlencoded): FIELD_A, FIELD_B, REGION, RECORD_ID
  data POST /Assessments/GetElaPerformanceBarChart
            16,252b    20 numbers  application/json  x1
```

Endpoints are ranked by how many numbers their response contains, which is
evidence rather than a verdict: a response full of figures is carrying data, one
full of markup is carrying layout. Third-party beacons and static assets are
dropped, and an endpoint called several times is listed once, keeping its
richest response.

`--suggest` prints a profile fragment with a `[[pair.page]]` block per data
endpoint and the captured payload as `left_body`. Export with **Save all as HAR
with content**, and turn on **Preserve log** before clicking through the views,
or the capture will hold only the last one.

Reading a HAR makes no network requests, and a test enforces that.

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
