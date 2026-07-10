# snippet_issues_to_jira.py — Snippet issues → Jira-ready JSON

## Overview

**Self-contained, single command.** Paginates FOSSA **snippet licensing issues**, classifies each
issue's snippet in-line (walk_all's import-statement filter + contiguous-LoC), and writes a JSON
payload of the true-positive copied blocks for a downstream Jira-ticket creator. No `walk_all.py`
run and no pre-built `snippet_matches_all.json` are needed — everything comes from the live API.

**One ticket = one snippet match to a package.** FOSSA opens a separate issue per flagged
license, so a package flagged under several licenses would otherwise become many near-duplicate
tickets. This script collapses them into a single ticket whose `flaggedLicenses` lists every
license (with its own issueId, link, and guidance). In testing, 1561 issues over 310 packages
collapsed to ~310 tickets — e.g. `GovPay@3.8.0` (10 flagged licenses) is now one ticket, not ten.

The refinement dial is **`--min-contig`** (the longest run of *consecutive* matched lines with
imports removed). Raise it to look only at longer, higher-confidence copied blocks and leave
boilerplate `import` / `#include` / `require` duplication behind.

## How it works

For every snippet licensing issue (the same request the FOSSA UI makes — `category=licensing`,
`status=active`, `scope[type]=global`, `filter[issueSource][0]=snippet`) the script:

1. reads the issue's `revisionId` + `analysisSnippetId`;
2. `GET /revisions/{rev}/snippets/{id}` → the matched file path(s) + package / purl / version;
3. `GET /revisions/{rev}/snippets/{id}/matches/{path}` → the highlighted matched lines;
4. **final filter (walk_all's import logic, ported into this script):** drop the match if every
   non-blank highlighted line is an import statement, then keep the issue only if a surviving
   match's `loc_contig` is within `[--min-contig, --max-contig]`.

When FOSSA returns an empty `detectedCode` (your-code side) it falls back to `referenceCode` (the
matched upstream lines) for classification, exactly like `walk_all.py`.

## Requirements

- Python 3.8+ (standard library only)
- `FOSSA_API_KEY` — a token with org read access to issues + revisions

## Usage

```sh
export FOSSA_API_KEY=<token>
python3 snippet_issues_to_jira.py                    # every snippet issue, default filter
python3 snippet_issues_to_jira.py --min-contig 10    # tighter true-positive threshold
python3 snippet_issues_to_jira.py --limit 50         # quick test on the first 50 issues
python3 snippet_issues_to_jira.py --keep-imports     # audit: include import-only matches
```

A full org run makes ~2 API calls per issue; expect a few minutes for a couple thousand issues.

## What's configurable

| Flag | Default | Purpose |
|---|---|---|
| `--min-contig N` | `1` | **Main refinement dial.** Keep an issue only if a non-import match has `loc_contig >= N`. Raise it to focus on longer copied blocks. |
| `--max-contig N` | none | Cap `loc_contig` (e.g. exclude giant vendored files). |
| `--keep-imports` | off | Disable the final import filter (import-only matches count too). |
| `--max-gap N` | `1` | Line-number gap still treated as contiguous. `1` = strictly consecutive; raise to bridge stray comment/blank lines inside a block. |
| `--workers N` | `16` | Concurrent API requests. **Lower this (e.g. `--workers 6`) if you see many `unclassified` results** — that means the org is rate-limiting the burst. |
| `--max-paths N` | `25` | Max matched paths to classify per snippet (snippets almost always have one). |
| `--limit N` | none | Only process the first N issues — for a quick test run. |
| `--out PATH` | `jira_snippet_issues.json` | Output payload path. |
| `--count N` | `50` | Page size for issue pagination. |
| `--category` / `--status` / `--scope` / `--source` / `--search` / `--sort` | `licensing` / `active` / `global` / `snippet` / `""` / `created_at_desc` | Live endpoint query params — mirror the FOSSA UI request; override to widen or narrow. |
| `--base-url` | `https://app.fossa.com/api` | API base (for on-prem / staging). |

Environment: `FOSSA_API_KEY` (required).

## Output payload

`jira_snippet_issues.json`:

```jsonc
{
  "generatedAt": "2026-07-10T17:01:07Z",
  "source": "https://app.fossa.com/api",
  "grouping": "one ticket per package match; flaggedLicenses lists all flagged licenses",
  "filter": { "import_filter": "on", "min_contig": 1, "max_contig": null, "max_gap": 1 },
  "counts": {
    "issues_in": 1561,                     // raw licensing issues (one per package per license)
    "packages_in": 310,                    // distinct package matches (the ticket grain)
    "tickets_out": 260,
    "licenses_in_tickets": 700,            // total license flags carried across tickets
    "dropped_no_snippet_match": 5,         // snippet genuinely has no matched path
    "dropped_snippet_not_found": 40,       // analysisSnippetId 404s (stale snippet, ~10%)
    "dropped_all_imports": 20,             // every matched line was an import statement
    "dropped_below_loc_contig": 25,        // no matched block met --min-contig
    "unclassified_unavailable": 0          // API kept returning empty/throttled — see below
  },
  "unclassified": [ { "packageLocator": "...", "reason": "unavailable", "issueIds": ["..."] } ],
  "tickets": [ /* one entry per package match, strongest signal first */ ]
}
```

Each ticket (one per package match):

| Field | Contents |
|---|---|
| `summary` | Ready-to-use Jira title, e.g. `Snippet copy of GovPay@3.8.0 flagged under 10 license(s): CDDL-1.1, EPL-2.0, GPL-2.0-only +7 more`. |
| `package.name` / `package.version` / `package.purl` / `package.locator` | Package identity (from the snippet detail). |
| `package.description` | Human sentence: name, ecosystem, version, dependency depth, FOSSA locator. |
| `flaggedLicenses` | **The N flagged licenses for this package**, each `{license, type, issueId, fossaIssueLink, details}`. `details` is FOSSA's obligation note for that license. |
| `licenseCount` | Number of flagged licenses (length of `flaggedLicenses`). |
| `snippet` | `max_loc_contig`, `sum_loc_contig`, `best_match_pct`, `n_matches`, `sample_paths`, `n_paths`, `used_reference_fallback`. |
| `fossaProjectUrl` | The project link (per-license issue links live in `flaggedLicenses`). |
| `timestamps` | `scannedAt`, `analyzedAt`, `firstFoundAt` (earliest across the grouped issues) + `generatedAt` (this run, UTC). |

`type` is mapped to the human label (e.g. `policy_flag` → `Flagged`, `unlicensed_dependency` →
`Unlicensed`).

## Rate limiting & the `unclassified` list

The org can answer a burst of requests with **empty `200`** responses instead of errors. The
script retries such empty responses, runs a sequential cleanup pass over any stragglers, and —
crucially — a package it still can't classify is put in the `unclassified` list with
`reason: "unavailable"` rather than being silently dropped. So `tickets_out` is never inflated
*or* quietly deflated by throttling.

If `unclassified_unavailable` is non-zero:

- re-run the script (already-classifiable snippets resolve quickly), and/or
- lower `--workers` (e.g. `--workers 6`) to reduce the burst rate.

The other drop buckets are **not** throttling artifacts:

- `dropped_snippet_not_found` — the issue's `analysisSnippetId` returns `404 Snippet not found`
  (a stale/removed snippet; ~10% of issues). Permanent, so it is not retried.
- `dropped_no_snippet_match` — the snippet resolved but has no matched path in the revision.
- `dropped_all_imports` / `dropped_below_loc_contig` — the filter did its job.
```
