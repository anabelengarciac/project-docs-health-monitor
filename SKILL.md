---
name: project-docs-health-monitor
description: Audit and optionally standardize Confluence project documentation so teams can stay current, aligned, and accountable. Use when Codex needs to review pages against a required template, summarize documentation health by workstream, classify pages as OK, Incomplete, Empty, or Outdated, list missing sections, or preview template-alignment updates without deleting useful context.
---

# Project Docs Health Monitor

## Overview

Use this skill to audit project documentation in Confluence and produce a quick, practical health check by workstream. It focuses on template compliance, missing sections, thin pages, stale or obsolete pages, and optional template-alignment updates that preserve useful existing context.

## Defaults

- Target space: `Analytics Documentation Team`
- Target route: `Analytics Portal > Execution Phase - Project Details > Data Documentation > Data Documentation`
- Engine folders: `Paid Media`, `Owned Media`, `Earned Media`, `Retail Media`
- Secret env file: `~/.config/confluence/.env`
- Secret token file: `~/.config/confluence/api.token`
- Audit helper: `scripts/prueba_police.py`
- Detailed audit rules: `references/data-domains-audit-standard.md`

Read credentials from the local secret files only. Never echo the base URL, email, or token back to the user.

## Workflow

1. Start with an audit unless the user explicitly asks for live Confluence changes.
2. Load `references/data-domains-audit-standard.md` when you need the exact section list, status semantics, or update guardrails.
3. Run:
   ```bash
   python3 scripts/prueba_police.py audit
   ```
4. Return the audit grouped by engine with:
   - page title
   - status: `OK`, `Incomplete`, `Empty`, or `Outdated`
   - missing sections
   - weak sections
   - short notes for stale or obsolete pages
   - practical next action
5. Treat similar pages across different engine folders as informational only. Do not count them as an error unless the user explicitly wants cross-folder deduplication.
6. Use JSON output when another step needs to parse the result:
   ```bash
   python3 scripts/prueba_police.py --format json audit
   ```

## Audit Scope

- Validate each datasource page against this required template:
  - `Overview`
  - `Scope`
  - `Business Use`
  - `KPIs`
  - `Grain & Keys`
  - `Joins`
  - `Important Fields`
  - `Mapping`
  - `Data Logic (Raw + Silver + Gold)`
  - `Refresh`
  - `Notes/Limitations`
- Use `Mapping` immediately before `Data Logic`.
- Fill `Mapping` with:
  - key source fields mapped to their L1 target fields
  - only the most relevant metrics, keys, and important attributes
  - a short list of the main transformations applied, such as FX conversion, date alignment, and standardization
- Accept sensible heading variants such as `Data Grain & Keys` or `Notes / Limitations` as evidence that the section exists, but still suggest renaming headings to the standard template when appropriate.
- Flag pages as `Empty` when there is almost no usable content.
- Flag pages as `Incomplete` when sections are missing or obviously thin.
- Flag pages as `Outdated` when the page looks stale or contains obsolete or deprecated markers.
- Use `OK` only when the page is substantively complete and not stale.

## Filtering

- Limit the audit to specific engines:
  ```bash
  python3 scripts/prueba_police.py audit --engine "Paid Media" --engine "Owned Media"
  ```
- Audit a specific page title:
  ```bash
  python3 scripts/prueba_police.py audit --page "CDP"
  ```

## Optional Updates

Preview template-alignment updates first:

```bash
python3 scripts/prueba_police.py align
```

Apply updates only after the user explicitly asks:

```bash
python3 scripts/prueba_police.py align --apply
```

Notes:

- `align` targets `Incomplete` and `Empty` pages by default.
- `align` also generates the new `Mapping` section before `Data Logic`.
- Include `Outdated` only when the user explicitly wants a template rebuild for stale pages:
  ```bash
  python3 scripts/prueba_police.py align --status Incomplete,Empty,Outdated --apply
  ```
- The alignment flow preserves useful existing context by rebuilding the page with the standard sections and keeping source material instead of deleting it blindly.

## Reporting Style

- Prefer a short executive summary first.
- Then group results by engine.
- Call out only the pages that need attention in detail.
- Keep the output practical for a quick documentation audit.
- If nothing is wrong, state that clearly and mention any residual risk such as heading variants or recent pages that still deserve a manual spot check.

## Dependency Note

`scripts/prueba_police.py` reuses the local Confluence helper shipped with the installed `confluence-content-manager` skill. If that helper is missing, stop and report the dependency issue instead of trying to improvise Confluence write operations.
