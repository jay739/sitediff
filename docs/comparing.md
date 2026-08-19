# What gets compared, and how it is reported

Content, structure, and the shape of the reports.

[← back to the README](../README.md)

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

### `[structure]`

The defaults are ordinary HTML semantics, which are not a per-site decision: an
`<h1>` is a heading everywhere. A profile narrows the set, silences labels that
are pure chrome, or registers components this tool has never heard of.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `enabled` | bool | `true` | Compare elements at all |
| `kinds` | list | `[]` | Element types to inventory. Empty means all. An unknown kind is rejected with the full list |
| `landmarks` | list | `nav`, `main`, `header`, `footer`, `aside`, `form` | Landmark elements to record |
| `ignore_labels` | list of regex | `[]` | Labels to skip, for chrome like "Skip to main content" |

```toml
[structure]
kinds = ["Heading", "Table", "Button", "Dropdown", "Text input"]
ignore_labels = ['^Skip to main content$']

# Your own components, inventoried alongside the standard ones.
[[structure.custom]]
selector = "[role=tab]"
kind = "Tab"

[[structure.custom]]
selector = "x-chart"
kind = "Chart"
label_attr = "data-title"
```

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
