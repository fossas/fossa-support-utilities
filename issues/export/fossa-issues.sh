#!/usr/bin/env bash
#
# fossa-issues.sh — a tiny CLI over the FOSSA Issues V2 API (GET /api/v2/issues)
#
# Grab issues (vulnerabilities, licensing, or quality) scoped to a whole org,
# a project, a specific revision, or a release group.
#
# ─── Setup ────────────────────────────────────────────────────────────────
#   export FOSSA_API_TOKEN="<your full-access API token>"
#   # optional (defaults to https://app.fossa.com):
#   export FOSSA_HOST="https://app.fossa.com"
#   chmod +x fossa-issues.sh
#
# Requires: curl, and jq (optional — used for pretty output; falls back to raw).
#
# ─── Usage ────────────────────────────────────────────────────────────────
#   ./fossa-issues.sh [options]
#
#   -c, --category <cat>     licensing | vulnerability | quality   (default: vulnerability)
#   -s, --status <status>    active | ignored | all                (default: active)
#
#   Pick exactly ONE scope:
#   -p, --project <locator>      project locator, e.g. custom+30344/github.com/ORG/repo
#   -r, --revision <rev>         specific revision (use WITH --project).
#                                  This is just the part after the `$` in the locator,
#                                  e.g. a git SHA / version. See note at bottom.
#       --scan-id <n>            specific revisionScanId (optional, with --project/--revision)
#   -g, --release-group <id>     release group id (numeric)
#       --release <id>           release id (numeric, REQUIRED with --release-group)
#       --global                 whole-organization scope
#
#   Filters / paging:
#       --ids <id,id,...>        only these issue id(s) (comma-separated)
#       --page <n>               page number       (default: 1)
#       --count <n>              results per page   (default: 100)
#
#   Output:
#       --raw                    print raw JSON (no jq formatting)
#       --summary                print a compact table (id, type, dependency) instead of full JSON
#       --host <url>             override FOSSA host
#   -h, --help                   show this help
#
# ─── Examples ─────────────────────────────────────────────────────────────
#   # All active vulnerabilities in a project (latest analyzed revision):
#   ./fossa-issues.sh -c vulnerability -p "custom+30344/github.com/ORG/repo"
#
#   # Licensing issues for a SPECIFIC revision (matches what a deep link / fossa test URL points to):
#   ./fossa-issues.sh -c licensing -p "custom+30344/github.com/ORG/repo" -r 462ad04b...
#
#   # A specific issue by id (verify a URL is valid), scoped to the right revision:
#   ./fossa-issues.sh -c vulnerability -p "custom+30344/github.com/ORG/repo" -r 462ad04b... --ids 73426009
#
#   # All quality issues across a release group's release:
#   ./fossa-issues.sh -c quality -g 1234 --release 5678
#
#   # Org-wide active vulnerabilities, compact table:
#   ./fossa-issues.sh -c vulnerability --global --summary
#
set -euo pipefail

HOST="${FOSSA_HOST:-https://app.fossa.com}"
TOKEN="${FOSSA_API_TOKEN:-}"

CATEGORY="vulnerability"
STATUS="active"
PAGE="1"
COUNT="100"
RAW="false"
SUMMARY="false"

PROJECT="" ; REVISION="" ; SCAN_ID=""
RG="" ; RELEASE="" ; RELEASE_SCAN_ID=""
GLOBAL="false"
IDS=""

die() { echo "Error: $*" >&2; exit 1; }
usage() { sed -n '2,70p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--category)      CATEGORY="$2"; shift 2 ;;
    -s|--status)        STATUS="$2"; shift 2 ;;
    -p|--project)       PROJECT="$2"; shift 2 ;;
    -r|--revision)      REVISION="$2"; shift 2 ;;
    --scan-id)          SCAN_ID="$2"; shift 2 ;;
    -g|--release-group) RG="$2"; shift 2 ;;
    --release)          RELEASE="$2"; shift 2 ;;
    --release-scan-id)  RELEASE_SCAN_ID="$2"; shift 2 ;;
    --global)           GLOBAL="true"; shift ;;
    --ids)              IDS="$2"; shift 2 ;;
    --page)             PAGE="$2"; shift 2 ;;
    --count)            COUNT="$2"; shift 2 ;;
    --raw)              RAW="true"; shift ;;
    --summary)          SUMMARY="true"; shift ;;
    --host)             HOST="$2"; shift 2 ;;
    -h|--help)          usage 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

# ─── Validate ───────────────────────────────────────────────────────────────
[[ -n "$TOKEN" ]] || die "set FOSSA_API_TOKEN (a full-access token; push-only tokens won't return issue details)."
case "$CATEGORY" in licensing|vulnerability|quality) ;; *) die "category must be licensing|vulnerability|quality";; esac
case "$STATUS"   in active|ignored|all) ;;             *) die "status must be active|ignored|all";; esac

# exactly one scope
scope_count=0
[[ -n "$PROJECT" ]] && scope_count=$((scope_count+1))
[[ -n "$RG"      ]] && scope_count=$((scope_count+1))
[[ "$GLOBAL" == "true" ]] && scope_count=$((scope_count+1))
[[ "$scope_count" -eq 1 ]] || die "pick exactly one scope: --project, --release-group, or --global"
[[ -n "$RG" && -z "$RELEASE" ]] && die "--release <id> is required with --release-group"
[[ -n "$REVISION" && -z "$PROJECT" ]] && die "--revision must be used with --project"

# ─── Build query (curl --data-urlencode handles encoding of values) ──────────
args=( -sS -G "${HOST}/api/v2/issues"
       -H "accept: application/json"
       -H "authorization: Bearer ${TOKEN}"
       --data-urlencode "category=${CATEGORY}"
       --data-urlencode "status=${STATUS}"
       --data-urlencode "page=${PAGE}"
       --data-urlencode "count=${COUNT}" )

if [[ -n "$PROJECT" ]]; then
  args+=( --data-urlencode "scope[type]=project"
          --data-urlencode "scope[id]=${PROJECT}" )
  [[ -n "$REVISION" ]] && args+=( --data-urlencode "scope[revision]=${REVISION}" )
  [[ -n "$SCAN_ID"  ]] && args+=( --data-urlencode "scope[revisionScanId]=${SCAN_ID}" )
elif [[ -n "$RG" ]]; then
  args+=( --data-urlencode "scope[type]=releaseGroup"
          --data-urlencode "scope[id]=${RG}"
          --data-urlencode "scope[release]=${RELEASE}" )
  [[ -n "$RELEASE_SCAN_ID" ]] && args+=( --data-urlencode "scope[releaseScanId]=${RELEASE_SCAN_ID}" )
else
  args+=( --data-urlencode "scope[type]=global" )
fi

# ids[]=a&ids[]=b
if [[ -n "$IDS" ]]; then
  IFS=',' read -ra _ids <<< "$IDS"
  for id in "${_ids[@]}"; do args+=( --data-urlencode "ids[]=${id}" ); done
fi

# ─── Call + render ───────────────────────────────────────────────────────────
resp="$(curl "${args[@]}")"

if [[ "$RAW" == "true" ]] || ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' "$resp"
elif [[ "$SUMMARY" == "true" ]]; then
  # Compact table: issue id, type, and the dependency it was found on.
  printf '%s\n' "$resp" | jq -r '
    (.issues // .results // []) as $list
    | "Total: \(($list | length))",
      ($list[] | "\(.id // "?")\t\(.type // .category // "?")\t\(.revisionId // .dependency // "")")
  ' | column -t -s $'\t'
else
  printf '%s\n' "$resp" | jq .
fi
