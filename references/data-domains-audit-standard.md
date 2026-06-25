# Data Documentation Audit Standard

Use this reference when auditing or aligning pages under:

`Analytics Documentation Team > Analytics Portal > Execution Phase - Project Details > Data Documentation > Data Documentation`

## Engine Scope

- `Paid Media`
- `Owned Media`
- `Earned Media`
- `Retail Media`

## Required Template

Each datasource page should cover these sections:

1. `Overview`
2. `Scope`
3. `Business Use`
4. `KPIs`
5. `Grain & Keys`
6. `Joins`
7. `Important Fields`
8. `Mapping`
9. `Data Logic (Raw + Silver + Gold)`
10. `Refresh`
11. `Notes/Limitations`

Accept close heading variants as evidence of documentation, but recommend the standard label when the wording drifts.

## Mapping Section

Place `Mapping` immediately before `Data Logic`.

Include:

- key source fields mapped to L1 fields
- only the most relevant metrics, keys, and important attributes
- a short list of the main transformations applied

Example patterns:

- `source.clicks -> L1_..._CLICKS`
- `source.cost -> L1_..._COST`
- `Currency conversion (FX)`
- `Date alignment`
- `Standardization (UPPER, null handling, etc.)`

## Status Rules

- `OK`: all required sections are substantively present and the page does not look stale
- `Incomplete`: one or more sections are missing or clearly too thin
- `Empty`: almost no usable content exists on the page
- `Outdated`: the page looks stale because of age or obsolete or deprecated wording

## Duplicate Rule

Do not automatically mark similar pages across different engine folders as an error. Some mirrored or overlapping pages are intentional to preserve local context inside each domain.

If similar pages are detected, report them as informational only.

## Update Guardrails

- Start with an audit.
- Preview changes before applying them.
- Do not remove useful tables, examples, notes, or local context just to force a perfect template.
- Preserve meaningful existing content when aligning a page to the template.
- Do not apply live updates unless the user explicitly asks for them.
