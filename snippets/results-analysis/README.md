# walk_all.py — FOSSA snippet-match exporter

Walks every project in your FOSSA org, pulls all snippet matches, and emits a JSON dump plus a flat TSV for triage. Designed for filtering matches by lines-of-code (e.g. the 10–20 LoC band that's typically dominated by boilerplate / package-manager imports).

## Prerequisites

- Python 3.8+ (uses only the standard library — no `pip install` needed)
- A FOSSA API token with read access to your org

## Run it

```sh
export FOSSA_API_KEY=<your token>
python3 walk_all.py
```

That's the whole thing. It walks every project in the org tied to that token.

## What you get

Two files written next to the script:

| File | Purpose |
|---|---|
| `snippet_matches_all.json` | Full data — every match, both sides' highlighted code, LoC counts, match%, rejection state |
| `snippet_matches_summary.tsv` | One row per match in the LoC band. Open in Excel / Google Sheets for triage |

### TSV columns

| Column | Meaning |
|---|---|
| `loc_local` | Lines of *your* file flagged as part of the match (count of highlighted lines in `matchDetails.detectedCode`). This is the column the LoC band filters on. |
| `loc_upstream` | Lines of the upstream third-party package flagged as the match (count of highlighted lines in `matchDetails.referenceCode`). Usually equals `loc_local`; if it differs significantly, the match is suspicious. |
| `span_local` | The vertical line range on your side: `max(lineNumber) − min(lineNumber) + 1`. Equals `loc_local` when matched lines are contiguous; larger if there are gaps. |
| `match%` | FOSSA's confidence score for this specific match (0–100). 100 = exact. Low values typically mean structural similarity only (e.g. Java getter/setter boilerplate). |
| `package` | Name of the upstream package FOSSA attributes the snippet to. Note: attribution can be wrong — it's whichever indexed package contains the same lines. |
| `version` | Package version / tag. |
| `path` | File path inside your project where the match was detected. |
| `project` | FOSSA project locator. |
| `snippetId` | FOSSA's unique ID for the snippet. The same ID can match many of your files. |
| `rejected` | `True` / `False` — whether someone has already rejected this match in FOSSA. |

## Tuning (optional env vars)

| Var | Default | What it does |
|---|---|---|
| `LOC_LO` | `10` | Lower bound of the LoC band in the TSV |
| `LOC_HI` | `20` | Upper bound of the LoC band in the TSV |
| `WORKERS` | `16` | Concurrent API requests. Bump to 32 if FOSSA tolerates it; drop to 4 if you hit rate limits |

Examples:

```sh
# Wider band
LOC_LO=5 LOC_HI=50 python3 walk_all.py

# More parallelism
WORKERS=32 python3 walk_all.py

# Save progress to a log file
python3 walk_all.py 2>&1 | tee walker.log
```

## Re-filtering without re-running

The JSON contains **all** matches regardless of `LOC_LO`/`LOC_HI` — only the TSV is filtered. So you can pull a different band from the existing JSON without hitting the API again:

```sh
# Match all snippets in 5–9 LoC
jq '.all_matches | map(select(.loc_detected >= 5 and .loc_detected <= 9))' snippet_matches_all.json

# Only 100% matches in the existing band
jq '.matches_in_range | map(select(.matchPercentage == 100))' snippet_matches_all.json
```

## Runtime

Roughly **2 seconds per match** across all projects, dominated by the per-match `/matches/{path}` API calls.

| Total snippet matches in org | Expected runtime |
|---|---|
| ~500 | ~2 min |
| ~3,000 | ~10 min |
| ~10,000 | ~30 min |

Progress prints to stderr per project, including a `match-details N/total` counter every 50 calls so you can tell it's making progress.

## Triage rule of thumb

- `loc_local == loc_upstream` + low `match%` → boilerplate (getter/setters, common patterns) — usually safe to reject.
- `loc_local == loc_upstream` + 100% `match%` → exact copy. Decide based on whether the attributed package is the *real* upstream.
- `loc_local != loc_upstream` → suspicious — review individually before rejecting.
