# walk_all.py — FOSSA Snippet-Match Exporter

## Overview

This tool systematically examines all projects within a FOSSA organization, extracts snippet matches, and produces both a comprehensive JSON dump and a filterable TSV report. The TSV is sorted and (optionally) filtered on `loc_contig` — the longest run of *consecutive* matched lines with import statements removed — which surfaces genuine copied blocks rather than scattered or boilerplate matches. See [The `loc_contig` metric](#the-loc_contig-metric).

By default every match is reported, except matches whose flagged lines are *entirely* import / include / require statements. Those are excluded from the triage TSV because they represent boilerplate import duplication rather than meaningful copied logic (they remain in the full JSON). See [Excluding Import-Statement Matches](#excluding-import-statement-matches).

## Requirements

- Python 3.8 or later (relies solely on built-in modules)
- FOSSA API token with organizational read permissions

## Execution

```sh
export FOSSA_API_KEY=<your token>
python3 walk_all.py
```

With no flags, the script processes every project associated with your token and reports **all** matches (import-only rows excluded). Use the flags below to narrow to a `loc_contig` range, keep import-only rows, or change other behavior.

## Command-Line Flags

| Flag | Default | Purpose |
|---|---|---|
| `--all` | on when no range given | Report all matches, with no `loc_contig` range filter. Import-only rows are still dropped unless `--keep-imports`. |
| `--min-contig N` | unset (`LOC_LO`) | Keep only matches whose `loc_contig >= N`. |
| `--max-contig N` | unset (`LOC_HI`) | Keep only matches whose `loc_contig <= N`. |
| `--keep-imports` | off (`EXCLUDE_IMPORTS=1`) | Keep import-only matches in the TSV. |
| `--raw-loc` | off | Filter and sort on the raw flagged-line count (`loc_match`) instead of `loc_contig`. |
| `--max-gap N` | `1` (`MAX_GAP`) | Line-number gap still treated as contiguous. `1` means strictly consecutive; raise to bridge small gaps within a block. |
| `--workers N` | `16` (`WORKERS`) | Concurrent API requests. Adjust based on rate limits. |
| `--projects L [L ...]` | all visible projects | Limit to these FOSSA project locators. |
| `--out-prefix P` | `snippet_matches` | Output file prefix. |

A `loc_contig` range applies only when `--min-contig` or `--max-contig` is given and `--all` is not set; otherwise every match is reported.

Usage examples:

```sh
python3 walk_all.py                              # every match, import-only rows dropped
python3 walk_all.py --min-contig 10 --max-contig 20   # 10–20 contiguous LoC
python3 walk_all.py --keep-imports               # keep import-only rows
python3 walk_all.py --raw-loc --min-contig 10    # filter on raw flagged lines instead
python3 walk_all.py --max-gap 2                  # bridge single-line gaps in a block
python3 walk_all.py --workers 32
python3 walk_all.py --projects <locator1> <locator2>
python3 walk_all.py --out-prefix run1
python3 walk_all.py 2>&1 | tee walk_all.log
```

### Environment Variables

The following env vars set back-compat defaults for the corresponding flags. Flags take precedence.

| Environment Variable | Flag it defaults | Default |
|---|---|---|
| `LOC_LO` | `--min-contig` | unset |
| `LOC_HI` | `--max-contig` | unset |
| `WORKERS` | `--workers` | `16` |
| `MAX_GAP` | `--max-gap` | `1` |
| `EXCLUDE_IMPORTS` | `--keep-imports` | `1` (set `0` to keep import-only rows) |

## Output Files

With the default `--out-prefix`:

| File | Contents |
|---|---|
| `snippet_matches_all.json` | Comprehensive dataset with all matches, highlighted code lines, line counts (`loc_contig`, `loc_match`, `loc_detected`, `loc_reference`), spans, confidence, review status, and an `import_only` flag on every match |
| `snippet_matches_summary.tsv` | The selected rows (all by default) for spreadsheet triage, sorted by `loc_contig` descending, with import-only rows excluded by default |

> **Note on FOSSA's `detectedCode`:** the API sometimes returns an empty `detectedCode`
> array (your-code side) while `referenceCode` (the matched upstream lines) is populated —
> this happens for matches whose revision is still being analyzed. When that happens
> `loc_detected` is `0`, so the script falls back to `referenceCode` for the LoC metrics
> and the import classification. See
> [LoC fallback](#loc-fallback-when-fossa-omits-detectedcode).

### TSV Column Reference

Columns in order as written by the script:

| Column | Definition |
|---|---|
| `loc_contig` | Longest run of *consecutive* flagged lines (by line number) with imports removed. The default filter/sort metric; one contiguous block of copied code. |
| `loc_match` | Raw flagged-line count: `loc_local` when present, otherwise `loc_upstream`. May include scattered lines and imports. Used as the filter metric only under `--raw-loc`. |
| `import_only` | `True` when every non-blank flagged line is an import statement. Excluded from the TSV by default. |
| `loc_local` | Your codebase lines flagged in the match (highlighted count from `matchDetails.detectedCode`). Often `0` because FOSSA omits this side; see the note above. |
| `loc_upstream` | Third-party package lines flagged (highlighted count from `matchDetails.referenceCode`). Alignment with `loc_local` indicates quality. |
| `span_local` | Vertical extent: `max(lineNumber) − min(lineNumber) + 1`. Exceeds `loc_local` when gaps exist between matched segments. |
| `match%` | FOSSA confidence (0–100 scale). 100 indicates exact matching; lower values suggest pattern similarity alone. |
| `package` | Attributed upstream package name. Attribution accuracy depends on indexed package content. |
| `version` | Release identifier or tag. |
| `path` | Location within your repository. |
| `project` | FOSSA project identifier. |
| `purl` | Package URL of the attributed upstream package, when available. |
| `snippetId` | Unique match identifier. Reusable across multiple files. |
| `rejected` | Review status in FOSSA (`True` when the match has rejection details). |

## The `loc_contig` metric

`loc_contig` is the length of the longest run of *consecutive* flagged lines in a match,
counted by line number, with blank lines and (by default) import statements removed. A run
continues while consecutive flagged line numbers differ by no more than `--max-gap`
(default `1`, i.e. strictly consecutive).

This measures a single contiguous block of copied code rather than scattered clusters spread
across a file, which makes it a better triage signal than the raw flagged-line count:

- `loc_contig` is the **first TSV column** and the **default filter and sort key**.
- `loc_match` (raw flagged-line count) is retained for reference and becomes the filter/sort
  key when you pass `--raw-loc`.
- Raise `--max-gap` to bridge small gaps (e.g. a stray comment line) and treat a lightly
  interrupted block as one contiguous run.

## Excluding Import-Statement Matches

Snippet detection frequently flags blocks of `import` / `#include` / `require` /
`use` / `using` statements. These are near-identical across many codebases and are
rarely actionable, so they inflate the triage report.

A match is treated as **import-only** when every non-blank flagged (highlighted) line
is an import statement. Blank lines are ignored. A match that mixes imports with real
logic is kept. Classification uses your-code highlights (`detectedCode`) when present,
and falls back to the matched upstream highlights (`referenceCode`) when FOSSA returns
an empty detected side (see [LoC fallback](#loc-fallback-when-fossa-omits-detectedcode)).
Import lines are also removed before computing `loc_contig` unless `--keep-imports` is set.

Behavior:

- Every match in `snippet_matches_all.json` carries an `import_only` boolean, so
  nothing is lost — the full dataset remains complete.
- When import exclusion is on (the default; `EXCLUDE_IMPORTS=1` and no `--keep-imports`),
  import-only matches are removed from `matches_in_range` in the JSON and from
  `snippet_matches_summary.tsv`. The JSON records how many were excluded under
  `import_only_excluded`, and the count is also printed to stderr.
- Pass `--keep-imports` (or set `EXCLUDE_IMPORTS=0`) to keep them in the triage outputs.

Recognized import forms include Python (`import x`, `from x import y`), JS/TS
(`import ... from`, `const x = require(...)`, `export ... from`), C/C++ (`#include`),
Objective-C (`#import`), Ruby/PHP (`require`, `include`, `use`), Rust (`use`,
`extern crate`), C# (`using`), Go import-block members with a path separator
(`_ "github.com/lib/pq"`), and Bazel/Starlark (`load(...)`). Single-word Go stdlib
imports (`"fmt"`) are intentionally not matched, to avoid misreading plain quoted
strings as imports. Detection is line-pattern based; review the
`detectedHighlighted` lines in the JSON if you need to audit a specific exclusion.

You can also recover excluded matches directly from the complete JSON:

```sh
# list import-only matches within a loc_contig range
jq '.all_matches | map(select(.import_only and (.loc_contig >= 10 and .loc_contig <= 20)))' snippet_matches_all.json
```

## LoC fallback when FOSSA omits `detectedCode`

FOSSA's match-detail endpoint sometimes returns an **empty `detectedCode`** (your-code
side) while `referenceCode` (the matched upstream lines) is populated. This shows up for
matches whose revision is still being analyzed — in testing it affected 100% of matches
immediately after a scan, then dropped to ~11% once analysis settled. Deriving the LoC
metrics solely from `detectedCode` would score any such match at `0` lines and drop it
from the TSV regardless of the range.

To keep the report usable, each match carries `loc_match = loc_detected or loc_reference`,
and both `loc_contig` and the import classification are computed from the detected-side
highlights when present, otherwise from the reference-side highlights. When the fallback is
in effect the script logs a line such as:

```
NOTE: 2445 matches had empty detectedCode; used loc_reference highlights
```

and records the count under `loc_reference_fallback` in the JSON. `loc_detected` /
`loc_local` and `loc_reference` / `loc_upstream` remain in the output unchanged for
transparency — if `loc_local` is `0` everywhere, FOSSA did not return the detected side for
that revision. If your token / org *does* populate `detectedCode`, the metrics simply come
from the detected side and behavior is unchanged.

## Extracting Alternative Ranges

The JSON preserves complete results independent of filtering parameters. Requery without API calls:

```sh
jq '.all_matches | map(select(.loc_contig >= 5 and .loc_contig <= 9))' snippet_matches_all.json
jq '.all_matches | map(select(.matchPercentage == 100))' snippet_matches_all.json
```

## Performance Estimates

The tool requires approximately 2 seconds per match. Timing projections:

| Estimated Match Volume | Time Required |
|---|---|
| 500 | ~2 minutes |
| 3,000 | ~10 minutes |
| 10,000 | ~30 minutes |

Stderr reports progress per project with periodic status updates.

## Triage Guidelines

- **High `loc_contig`** → A long unbroken block of copied logic. The strongest signal; review first.
- **Import-only matches** → Boilerplate import blocks. Excluded by default; re-enable with `--keep-imports` if you want to review them.
- **Low confidence + equal line counts** → Typically boilerplate patterns. Safe rejection.
- **100% confidence + equal line counts** → Verbatim duplication. Verify upstream attribution.
- **Unequal line counts** → Warrants individual examination before action.
