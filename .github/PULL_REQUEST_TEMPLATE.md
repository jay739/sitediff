## What this changes

<!-- One or two sentences. What was wrong or missing, and what now happens. -->

## Why

<!-- The reasoning, not a restatement of the diff. If it fixes a bug, what was
     the failure mode, and how would someone have noticed it? -->

## Checklist

- [ ] `ruff check sitediff tests` passes
- [ ] `pytest` passes
- [ ] `python -m build` still produces a working wheel
- [ ] No site-specific values added to the package (hosts, paths, selectors, ids)
- [ ] New profile keys or CLI flags are documented in `docs/`
- [ ] New behaviour has a test that would fail without the change

## Anything reviewers should look at closely

<!-- Trade-offs you made, things you were unsure about, or a heuristic whose
     threshold is a judgement call. -->
