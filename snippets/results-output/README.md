# snippet_issues_to_jira.py — Snippet issues → Jira-ready JSON

## Overview

Takes FOSSA **snippet licensing issues** and applies `walk_all.py`'s import-statement filter as
the **final filter**, so the output contains only true-positive copied blocks — not boilerplate
`import` / `#include` / `require` duplication. Each surviving issue is emitted as a JSON entry a
downstream job can turn into a Jira ticket.

The refinement dial is **`loc_contig`** (the longest run of *consecutive* matched lines with
imports removed, computed by `walk_all.py`). Raise `--min-contig` to look only at longer, more
confident blocks of copied logic.

## How it works

1. **Collect issues** — either paginate the live FOSSA issues endpoint (the same request the UI
   makes: `category=licensing`, `status=active`, `scope[type]=global`,
   `filter[issueSource][0]=snippet`) or read a CSV export with `--issues-csv`.
2. **Join to snippets** — each issue is matched to its snippet matches in
   `snippet_matches_all.json` (from `walk_all.py`). Live issues carry an `analysisSnippetId`, so
   they join **directly on `snippetId`** (`match_strength: "snippetId"`) — the exact match behind
   the issue. CSV issues have no such id and fall back to package identity (`version` strength =
   ecosystem+name+version agree; `name` = ecosystem+name agree), the same join
   `correlate_issues.py` uses.
3. **Final filter (walk_all's logic)** — drop every `import_only` match, then keep the issue only
   if a surviving match's `loc_contig` is within `[--min-contig, --max-contig]`.
4. **Emit payload** — `jira_snippet_issues.json`, sorted strongest-signal first (longest
   contiguous block, then best match %).

> `import_only` and `loc_contig` are already precomputed for every match in
> `snippet_matches_all.json`, so this script reuses `walk_all.py`'s classification directly.
> Regenerate that file with `python3 walk_all.py` if it is stale.

## Requirements

- Python 3.8+ (standard library only)
- `snippet_matches_all.json` produced by `walk_all.py`
- A FOSSA API token (`FOSSA_API_KEY`) for live fetch — **not** needed with `--issues-csv`

## Usage

```sh
export FOSSA_API_KEY=<token>
python3 snippet_issues_to_jira.py                     # live fetch, default filter
python3 snippet_issues_to_jira.py --min-contig 10     # tighter true-positive threshold
python3 snippet_issues_to_jira.py --min-contig 20 --max-contig 200
python3 snippet_issues_to_jira.py \
    --issues-csv CSV_Licensing_ISSUES_2026-07-02_155335638Z.csv   # offline, no token
```

## What's configurable

| Flag | Default | Purpose |
|---|---|---|
| `--min-contig N` | `1` | **Main refinement dial.** Keep an issue only if a non-import match has `loc_contig >= N`. Raise it to focus on longer copied blocks (`1 → 204`, `10 → 81`, `50 → 14` tickets on the sample data). |
| `--max-contig N` | none | Cap `loc_contig` (e.g. exclude giant vendored files). |
| `--keep-imports` | off | Disable the final import filter (import-only matches count too). |
| `--keep-unmatched` | off | Also emit issues with **no** snippet match in the JSON (snippet metadata is null). Off by default, since those can't be confirmed as true positives. |
| `--snippets PATH` | `snippet_matches_all.json` | The `walk_all.py` dump used for `import_only` + `loc_contig`. |
| `--issues-csv PATH` | none | Read issues from a FOSSA CSV export instead of the live endpoint (offline; no token needed). |
| `--out PATH` | `jira_snippet_issues.json` | Output payload path. |
| `--count N` | `50` | Page size for live pagination. |
| `--category` / `--status` / `--scope` / `--source` / `--search` / `--sort` | `licensing` / `active` / `global` / `snippet` / `""` / `created_at_desc` | Live endpoint query params — mirror the FOSSA UI request; override to widen or narrow. |
| `--base-url` | `https://app.fossa.com/api` | API base (for on-prem / staging). |

Environment: `FOSSA_API_KEY` — required for live fetch only.

## Output payload

`jira_snippet_issues.json`:

```jsonc
{
  "generatedAt": "2026-07-10T15:30:04Z",     // when this payload was built (UTC)
  "source": "live:https://app.fossa.com/api",// or "csv:<path>"
  "filter":  { "import_filter": "on", "min_contig": 1, "max_contig": null, "keep_unmatched": false },
  "counts":  { "issues_in": 1634, "tickets_out": 204,
               "dropped_no_snippet_match": 1424, "dropped_all_imports": 2,
               "dropped_below_loc_contig": 4 },
  "tickets": [ /* one entry per kept issue, strongest signal first */ ]
}
```

Each ticket:

| Field | Contents |
|---|---|
| `summary` | Ready-to-use Jira title, e.g. `Snippet license issue: Flagged (EPL-2.0) in afterglow@0.1.0-SNAPSHOT`. |
| `package.name` / `package.version` | Package name and version. |
| `package.description` | Human sentence: name, ecosystem, version, dependency depth/usage, FOSSA locator. |
| `licenseIssue.description` | The licensing-issue write-up: type, license, FOSSA `details`, policy. |
| `licenseIssue.{issueId,type,license,status,policyName}` | Structured issue fields. |
| `snippet` | Snippet metadata: `max_loc_contig`, `sum_loc_contig`, `best_match_pct`, `n_matches`, `sample_paths`, `n_paths`, `snippetIds`, `projects`, `match_strength`. |
| `fossaIssueLink` | Best link to the issue (project URL, falling back to an issue-scoped URL). |
| `fossaIssueUrl` / `fossaProjectUrl` | Issue-scoped and project links separately. |
| `timestamps` | `scannedAt`, `analyzedAt`, `firstFoundAt` (from FOSSA) plus `generatedAt` (this run, UTC). |

## Notes

- **Live auth uses the Bearer token** (`Authorization: Bearer $FOSSA_API_KEY`), the same as
  `walk_all.py` — not the browser cookie from the devtools curl. If your token lacks issue-read
  scope, export the issues from the UI and use `--issues-csv`.
- **Unknown/renamed JSON fields**: the endpoint's JSON keys are read defensively (they mirror the
  CSV column names). If the live response nests fields differently, prefer `--issues-csv`, which
  reads the stable export columns.
```
