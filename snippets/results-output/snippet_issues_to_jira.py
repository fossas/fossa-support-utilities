#!/usr/bin/env python3
"""Turn FOSSA snippet licensing issues into a Jira-ready JSON payload, applying walk_all.py's
import-statement filter as the FINAL filter so only true-positive copied blocks survive.

Pipeline:
  1. Collect snippet licensing issues -- either by paginating the live FOSSA issues endpoint
     (the same request the FOSSA UI makes: category=licensing, status=active,
     scope[type]=global, filter[issueSource][0]=snippet) or by reading a CSV export.
  2. Join each issue to its snippet matches (from walk_all.py's snippet_matches_all.json) by
     package identity, exactly like correlate_issues.py.
  3. FINAL FILTER (walk_all's logic): drop every `import_only` match, then keep the issue only
     if a surviving match's `loc_contig` (longest run of consecutive matched lines, imports
     removed) falls in [--min-contig, --max-contig]. This is the LoC-contig refinement that
     surfaces genuine copied logic rather than boilerplate import duplication.
  4. Emit a JSON payload -- one entry per kept issue -- for a downstream Jira ticket creator:
     package name+version, a package description, the licensing-issue description, snippet
     metadata (loc_contig, match%, sample paths, snippetIds), the FOSSA link, and timestamps.

The import classification and loc_contig live in snippet_matches_all.json already (walk_all.py
computes `import_only` for every match regardless of flags, and `loc_contig` with imports
removed by default), so this script reuses that logic directly rather than re-deriving it.

Usage:
  export FOSSA_API_KEY=<token>
  python3 snippet_issues_to_jira.py                       # live fetch, default filter
  python3 snippet_issues_to_jira.py --min-contig 10       # tighter true-positive threshold
  python3 snippet_issues_to_jira.py --issues-csv CSV_Licensing_ISSUES_*.csv   # offline
See README_jira.md for every configurable knob.
"""
import os, sys, csv, json, argparse, urllib.parse, urllib.request, urllib.error, time
from collections import defaultdict
from datetime import datetime, timezone

# Reuse the package-identity normalizers from the existing correlation tool (no import-time
# side effects), so issue<->snippet joins behave identically here and in correlate_issues.py.
from correlate_issues import norm_locator, norm_purl

BASE_DEFAULT = 'https://app.fossa.com/api'
APP_BASE = 'https://app.fossa.com'


# --------------------------------------------------------------------------------------------
# Issue collection
# --------------------------------------------------------------------------------------------
def _get(url, key, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {key}',
                                                       'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504):
                time.sleep(1.5 * (i + 1)); continue
            raise
        except Exception as e:
            last = e
            time.sleep(1.0); continue
    raise last


def fetch_issues(cfg, key):
    """Paginate the live FOSSA issues endpoint and return the raw issue objects."""
    out, page = [], 1
    while True:
        params = {
            'category': cfg.category,
            'status': cfg.status,
            'scope[type]': cfg.scope,
            'filter[issueSource][0]': cfg.source,
            'filter[search]': cfg.search,
            'sort': cfg.sort,
            'page': page,
            'count': cfg.count,
        }
        url = cfg.base_url + '/v2/issues?' + urllib.parse.urlencode(params)
        d = _get(url, key)
        # The endpoint returns either {"issues": [...], "total": N} or a bare list.
        batch = d.get('issues', d) if isinstance(d, dict) else d
        if not batch:
            break
        out.extend(batch)
        total = d.get('total') if isinstance(d, dict) else None
        log(f"  fetched page {page}: {len(batch)} issues (running total {len(out)}"
            + (f"/{total}" if total is not None else "") + ")")
        if len(batch) < cfg.count or (total is not None and len(out) >= total):
            break
        page += 1
    return out


def read_issues_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


# The live endpoint returns machine `type` values; map the common ones to the human labels the
# CSV export uses (unknown values pass through unchanged).
TYPE_LABEL = {'policy_flag': 'Flagged', 'policy_conflict': 'Flagged',
              'unlicensed': 'Unlicensed', 'unlicensed_dependency': 'Unlicensed'}


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ''):
            return d[k]
    return None


def canon_issue(raw):
    """Normalize a raw issue to a flat dict of canonical fields, handling BOTH the nested live
    endpoint shape ({source:{...}, projects:[{...}], analysisSnippetId, ...}) and the flat CSV
    export columns. `snippetId` (from the live `analysisSnippetId`) is the precise join key to
    walk_all's matches; the CSV has no such id and joins by package locator instead."""
    src = raw.get('source') if isinstance(raw, dict) else None
    projs = raw.get('projects') if isinstance(raw, dict) else None
    if src is not None or (isinstance(projs, list) and projs):
        # --- live endpoint (nested) ---
        p0 = projs[0] if isinstance(projs, list) and projs else {}
        src = src or {}
        typ = raw.get('type')
        return {
            'issueId': str(_first(raw, 'id', 'issueId') or ''),
            'type': TYPE_LABEL.get(typ, typ),
            'license': raw.get('license'),
            'dependency': _first(src, 'name'),
            'packageLocator': _first(src, 'id'),
            'version': _first(src, 'version'),
            'packageManager': _first(src, 'packageManager'),
            'snippetId': str(raw['analysisSnippetId']) if raw.get('analysisSnippetId') else None,
            'project': _first(p0, 'id'),
            'projectUrl': _first(p0, 'url'),
            'issueUrl': _first(raw, 'url'),
            'depth': p0.get('depth'),
            'usage': None,
            'details': raw.get('details'),
            'status': 'active',
            'policyName': None,
            'scannedAt': _first(p0, 'scannedAt'),
            'analyzedAt': _first(p0, 'analyzedAt'),
            'firstFoundAt': _first(p0, 'firstFoundAt') or _first(raw, 'createdAt'),
        }
    # --- CSV export (flat) ---
    return {
        'issueId': _first(raw, 'issueId', 'id'),
        'type': raw.get('type'),
        'license': raw.get('license'),
        'dependency': raw.get('dependency'),
        'packageLocator': raw.get('packageLocator'),
        'version': raw.get('version'),
        'packageManager': None,
        'snippetId': None,
        'project': raw.get('project'),
        'projectUrl': raw.get('projectUrl'),
        'issueUrl': None,
        'depth': raw.get('depth'),
        'usage': raw.get('usage'),
        'details': raw.get('details'),
        'status': raw.get('status'),
        'policyName': raw.get('policyName'),
        'scannedAt': raw.get('scannedAt'),
        'analyzedAt': raw.get('analyzedAt'),
        'firstFoundAt': _first(raw, 'firstFoundAt', 'createdAt'),
    }


# --------------------------------------------------------------------------------------------
# Snippet join (walk_all.py output)
# --------------------------------------------------------------------------------------------
def load_snippet_index(path):
    """Index walk_all's matches for joining: by snippetId (the precise key) and by package
    identity (the CSV fallback). Every match already carries import_only + loc_contig."""
    d = json.load(open(path))
    rows = d.get('all_matches') or d.get('matches_in_range') or []
    by_snip, by_ver, by_name = defaultdict(list), defaultdict(list), defaultdict(list)
    for m in rows:
        if m.get('snippetId') is not None:
            by_snip[str(m['snippetId'])].append(m)
        k = norm_purl(m.get('purl'))
        if k:
            by_ver[k].append(m)
            by_name[(k[0], k[1])].append(m)
    return (by_snip, by_ver, by_name), len(rows)


def matches_for_issue(issue, idx):
    """The snippet matches behind this issue. Prefer the exact join on snippetId (live issues
    carry analysisSnippetId); fall back to package identity for CSV issues that have no id.
    snippetId is reused across files, so narrow to the issue's own project when possible."""
    by_snip, by_ver, by_name = idx
    sid = issue.get('snippetId')
    if sid and sid in by_snip:
        cand = by_snip[sid]
        same_proj = [m for m in cand if m.get('project') == issue.get('project')]
        return (same_proj or cand), 'snippetId'
    k = norm_locator(issue.get('packageLocator'))
    if k:
        if k in by_ver:
            return by_ver[k], 'version'
        if (k[0], k[1]) in by_name:
            return by_name[(k[0], k[1])], 'name'
    return [], 'none'


# --------------------------------------------------------------------------------------------
# Payload assembly
# --------------------------------------------------------------------------------------------
def package_description(issue):
    k = norm_locator(issue.get('packageLocator'))
    eco = issue.get('packageManager') or (k[0] if k else 'unknown ecosystem')
    name = issue.get('dependency') or (k[1] if k else '') or issue.get('packageLocator') or 'unknown package'
    ver = issue.get('version') or 'unspecified version'
    bits = [f"{name} ({eco}) at version {ver}."]
    if issue.get('depth') is not None:
        bits.append(f"Dependency depth {issue['depth']}, {issue.get('usage') or 'unknown'} usage.")
    if issue.get('packageLocator'):
        bits.append(f"FOSSA locator: {issue['packageLocator']}.")
    return ' '.join(bits)


def issue_description(issue):
    typ = issue.get('type') or 'Licensing issue'
    lic = issue.get('license')
    head = f"{typ}" + (f" under license {lic}" if lic else "") + "."
    details = (issue.get('details') or '').strip()
    parts = [head]
    if details:
        parts.append(details)
    if issue.get('policyName'):
        parts.append(f"Policy: {issue['policyName']}.")
    return ' '.join(parts)


def fossa_link(issue):
    # The live endpoint gives a real issue-detail URL; otherwise build one from the id. The
    # project URL is the fallback link (all the CSV export carries).
    iid = issue.get('issueId')
    issue_url = issue.get('issueUrl') or (f"{APP_BASE}/issues/licensing/{iid}" if iid else None)
    return issue_url or issue.get('projectUrl'), issue_url


def snippet_metadata(considered):
    """Summarize the surviving (non-import, in-range) snippet matches for the ticket."""
    locs = [m['loc_contig'] for m in considered if isinstance(m.get('loc_contig'), int)]
    pcts = [m['matchPercentage'] for m in considered if isinstance(m.get('matchPercentage'), (int, float))]
    paths = sorted({m['path'] for m in considered if m.get('path')})
    projects = sorted({m['project'] for m in considered if m.get('project')})
    snip_ids = sorted({str(m['snippetId']) for m in considered if m.get('snippetId')})
    sample = considered[0] if considered else {}
    return {
        'n_matches': len(considered),
        'max_loc_contig': max(locs) if locs else 0,
        'sum_loc_contig': sum(locs) if locs else 0,
        'best_match_pct': max(pcts) if pcts else None,
        'attributed_package': sample.get('package'),
        'attributed_purl': sample.get('purl'),
        'sample_paths': paths[:10],
        'n_paths': len(paths),
        'projects': projects,
        'snippetIds': snip_ids[:25],
    }


def build_ticket(issue, considered, strength, generated_at):
    link, issue_url = fossa_link(issue)
    return {
        'summary': (f"Snippet license issue: {issue.get('type') or 'Licensing'}"
                    f" ({issue.get('license') or 'no license'}) in "
                    f"{issue.get('dependency') or '?'}@{issue.get('version') or '?'}"),
        'package': {
            'name': issue.get('dependency'),
            'version': issue.get('version'),
            'locator': issue.get('packageLocator'),
            'description': package_description(issue),
        },
        'licenseIssue': {
            'issueId': issue.get('issueId'),
            'type': issue.get('type'),
            'license': issue.get('license'),
            'status': issue.get('status'),
            'policyName': issue.get('policyName'),
            'description': issue_description(issue),
        },
        'snippet': {**snippet_metadata(considered), 'match_strength': strength},
        'fossaIssueLink': link,
        'fossaIssueUrl': issue_url,
        'fossaProjectUrl': issue.get('projectUrl'),
        'timestamps': {
            'scannedAt': issue.get('scannedAt'),
            'analyzedAt': issue.get('analyzedAt'),
            'firstFoundAt': issue.get('firstFoundAt'),
            'generatedAt': generated_at,
        },
    }


# --------------------------------------------------------------------------------------------
def log(msg):
    sys.stderr.write(msg + '\n'); sys.stderr.flush()


def parse_config(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--snippets', default='snippet_matches_all.json',
                   help='walk_all.py JSON dump used for import_only + loc_contig (default: '
                        'snippet_matches_all.json)')
    p.add_argument('--issues-csv', default=None,
                   help='read issues from this FOSSA CSV export instead of the live endpoint')
    p.add_argument('--min-contig', type=int, default=1,
                   help='keep an issue only if a non-import match has loc_contig >= N '
                        '(default 1; raise to refine toward longer true-positive blocks)')
    p.add_argument('--max-contig', type=int, default=None,
                   help='keep an issue only if a non-import match has loc_contig <= N (default: no cap)')
    p.add_argument('--keep-imports', action='store_true',
                   help='disable the final import filter (consider import_only matches too)')
    p.add_argument('--keep-unmatched', action='store_true',
                   help='also keep issues with no snippet match in the JSON (null snippet metadata)')
    # live endpoint query params (mirror the FOSSA UI request)
    p.add_argument('--category', default='licensing')
    p.add_argument('--status', default='active')
    p.add_argument('--scope', default='global', help='scope[type] query value')
    p.add_argument('--source', default='snippet', help='filter[issueSource][0] query value')
    p.add_argument('--search', default='')
    p.add_argument('--sort', default='created_at_desc')
    p.add_argument('--count', type=int, default=50, help='page size (default 50)')
    p.add_argument('--base-url', default=BASE_DEFAULT, help=f'API base (default {BASE_DEFAULT})')
    p.add_argument('--out', default='jira_snippet_issues.json', help='output JSON payload path')
    return p.parse_args(argv)


def main(argv=None):
    cfg = parse_config(argv)
    generated_at = datetime.now(timezone.utc).isoformat()

    if not os.path.exists(cfg.snippets):
        log(f"ERROR: snippet dump not found: {cfg.snippets} (run walk_all.py first)"); sys.exit(1)
    idx, n_snip = load_snippet_index(cfg.snippets)
    log(f"snippet matches indexed: {n_snip}")

    # Source the issues.
    if cfg.issues_csv:
        raw_issues = read_issues_csv(cfg.issues_csv)
        log(f"issues from CSV {cfg.issues_csv}: {len(raw_issues)}")
    else:
        key = os.environ.get('FOSSA_API_KEY')
        if not key:
            log("ERROR: FOSSA_API_KEY not set (needed for live fetch; or pass --issues-csv)"); sys.exit(1)
        log(f"fetching issues: category={cfg.category} status={cfg.status} "
            f"scope[type]={cfg.scope} filter[issueSource][0]={cfg.source}")
        raw_issues = fetch_issues(cfg, key)
        log(f"issues fetched: {len(raw_issues)}")

    hi = cfg.max_contig if cfg.max_contig is not None else 10 ** 9
    lo = cfg.min_contig

    tickets = []
    n_no_match = n_all_import = n_below = 0
    for raw in raw_issues:
        issue = canon_issue(raw)
        matches, strength = matches_for_issue(issue, idx)

        if not matches:
            n_no_match += 1
            if cfg.keep_unmatched:
                tickets.append(build_ticket(issue, [], 'none', generated_at))
            continue

        # FINAL FILTER (walk_all's import logic): drop import_only matches unless --keep-imports.
        pool = matches if cfg.keep_imports else [m for m in matches if not m.get('import_only')]
        if not pool:
            n_all_import += 1
            continue

        # LoC-contig refinement: keep only matches whose contiguous block is in range.
        considered = [m for m in pool if lo <= (m.get('loc_contig') or 0) <= hi]
        if not considered:
            n_below += 1
            continue

        tickets.append(build_ticket(issue, considered, strength, generated_at))

    # Strongest signal first: longest contiguous block, then best match%.
    tickets.sort(key=lambda t: (-(t['snippet'].get('max_loc_contig') or 0),
                                -(t['snippet'].get('best_match_pct') or 0)))

    payload = {
        'generatedAt': generated_at,
        'source': 'csv:' + cfg.issues_csv if cfg.issues_csv else 'live:' + cfg.base_url,
        'filter': {
            'import_filter': 'off (--keep-imports)' if cfg.keep_imports else 'on',
            'min_contig': lo, 'max_contig': cfg.max_contig,
            'keep_unmatched': cfg.keep_unmatched,
        },
        'counts': {
            'issues_in': len(raw_issues), 'tickets_out': len(tickets),
            'dropped_no_snippet_match': n_no_match,
            'dropped_all_imports': n_all_import,
            'dropped_below_loc_contig': n_below,
        },
        'tickets': tickets,
    }
    with open(cfg.out, 'w') as f:
        json.dump(payload, f, indent=2)

    log(f"\nissues in:                    {len(raw_issues)}")
    log(f"dropped (no snippet match):   {n_no_match}"
        + (" [kept as unmatched]" if cfg.keep_unmatched else ""))
    log(f"dropped (all import-only):    {n_all_import}")
    log(f"dropped (below loc_contig):   {n_below}")
    log(f"JIRA TICKETS:                 {len(tickets)}")
    log(f"wrote: {cfg.out}")


if __name__ == '__main__':
    main()
