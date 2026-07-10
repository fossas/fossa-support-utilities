#!/usr/bin/env python3
"""Self-contained: paginate FOSSA snippet licensing issues, classify each issue's snippet
in-line (import-statement filter + contiguous-LoC), and emit a Jira-ready JSON payload of the
true-positive copied blocks. No dependency on walk_all.py or any pre-built snippet dump.

For every snippet licensing issue (the same request the FOSSA UI makes: category=licensing,
status=active, scope[type]=global, filter[issueSource][0]=snippet) the script:
  1. reads the issue's revisionId + analysisSnippetId,
  2. GET /revisions/{rev}/snippets/{id}      -> the matched file path(s) + package/purl/version,
  3. GET /revisions/{rev}/snippets/{id}/matches/{path} -> the highlighted matched lines,
  4. FINAL FILTER (walk_all's import logic, inlined below): drop the match if every non-blank
     highlighted line is an import/include/require statement, then keep the issue only if a
     surviving match's loc_contig (longest run of CONSECUTIVE matched lines, imports removed)
     falls in [--min-contig, --max-contig].

`--min-contig` is the refinement dial: raise it to surface longer, higher-confidence copied
blocks and leave boilerplate import duplication behind.

One JSON ticket == one snippet match to a package. Because FOSSA opens a separate issue per
flagged license, a package flagged under several licenses is collapsed into ONE ticket whose
`flaggedLicenses` lists them all (rather than emitting a near-duplicate ticket per license).
Each ticket carries the package name+version, package description, the flagged-license list,
snippet metadata, the FOSSA links, and timestamps for a downstream Jira creator.

Usage:
  export FOSSA_API_KEY=<token>
  python3 snippet_issues_to_jira.py                    # every snippet issue, default filter
  python3 snippet_issues_to_jira.py --min-contig 10    # tighter true-positive threshold
  python3 snippet_issues_to_jira.py --limit 50         # quick test on the first 50 issues
See README_jira.md for every configurable knob.
"""
import os, sys, json, re, argparse, urllib.parse, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE_DEFAULT = 'https://app.fossa.com/api'
APP_BASE = 'https://app.fossa.com'

# The live endpoint returns machine `type` values; map the common ones to the human labels the
# FOSSA UI/CSV use (unknown values pass through unchanged).
TYPE_LABEL = {'policy_flag': 'Flagged', 'policy_conflict': 'Flagged',
              'unlicensed': 'Unlicensed', 'unlicensed_dependency': 'Unlicensed'}


# --------------------------------------------------------------------------------------------
# Import-statement filter (ported verbatim from walk_all.py so classification is identical).
# A match is "import only" when every non-blank highlighted line is a pure import/include/
# require statement -- boilerplate duplication rather than copied logic. Import lines are also
# removed before measuring the contiguous block, so loc_contig reflects real copied code.
# --------------------------------------------------------------------------------------------
_IMPORT_PATTERNS = [
    r'import\b',                              # py/java/js/ts/go/kotlin/scala/swift: import ...
    r'from\s+[\w./\'"]+\s+import\b',          # python: from x import y
    r'#\s*include\b',                         # c/c++: #include <...>
    r'#\s*import\b',                          # objective-c: #import <...>
    r'require(_relative|_once)?\s*[\(\'"]',   # ruby/php: require(...) / require '...'
    r'(const|let|var)\s+.*=\s*require\s*\(',  # node: const x = require('...')
    r'include(_once)?\s*[\(\'"]',             # php: include '...'
    r'use\s+[\w:\\]',                         # rust/php: use a::b; / use A\B;
    r'using\s+[\w.]',                         # c#: using System.X;
    r'extern\s+crate\b',                      # rust: extern crate x;
    r'export\s+.*\bfrom\s+[\'"]',             # js/ts re-export: export { x } from '...'
    r'load\s*\(?\s*[\'"]',                    # starlark/bazel/ruby: load("...")
    r'(_|\.|\w+)?\s*"[^"]*/[^"]*"\s*$',       # go import-block member: _ "github.com/lib/pq"
]
_IMPORT_RE = re.compile(r'^\s*(?:' + '|'.join(_IMPORT_PATTERNS) + r')')


def is_import_line(line):
    return bool(_IMPORT_RE.match(line or ''))


def is_import_only(highlighted):
    non_blank = [h for h in highlighted if (h.get('l') or '').strip()]
    return bool(non_blank) and all(is_import_line(h['l']) for h in non_blank)


def contiguous_loc(highlighted, max_gap=1, skip_imports=True):
    """Longest run of consecutive highlighted line numbers, ignoring blank lines and (when
    skip_imports) import statements. One contiguous copied block rather than scattered hits."""
    nums = sorted({h['n'] for h in highlighted
                   if (h.get('l') or '').strip()
                   and not (skip_imports and is_import_line(h['l']))})
    if not nums:
        return 0
    best = run = 1
    for prev, cur in zip(nums, nums[1:]):
        run = run + 1 if (cur - prev) <= max_gap else 1
        best = max(best, run)
    return best


# --------------------------------------------------------------------------------------------
# FOSSA API
# --------------------------------------------------------------------------------------------
def log(msg):
    sys.stderr.write(msg + '\n'); sys.stderr.flush()


def get(path, key, base=BASE_DEFAULT, retries=4):
    url = base + path
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
    """Paginate the live snippet-issues endpoint and return the raw issue objects."""
    out, page = [], 1
    while True:
        params = {
            'category': cfg.category, 'status': cfg.status, 'scope[type]': cfg.scope,
            'filter[issueSource][0]': cfg.source, 'filter[search]': cfg.search,
            'sort': cfg.sort, 'page': page, 'count': cfg.count,
        }
        qp = '/v2/issues?' + urllib.parse.urlencode(params)
        # The org occasionally answers a burst of requests with an empty 200; retry page 1 a
        # few times before concluding there really are no issues.
        batch = get(qp, key, cfg.base_url).get('issues', [])
        if not batch and page == 1:
            for _ in range(4):
                time.sleep(3)
                batch = get(qp, key, cfg.base_url).get('issues', [])
                if batch:
                    break
        if not batch:
            break
        out.extend(batch)
        log(f"  fetched page {page}: {len(batch)} (running total {len(out)})")
        if len(batch) < cfg.count:
            break
        if cfg.limit and len(out) >= cfg.limit:
            break
        page += 1
    return out[:cfg.limit] if cfg.limit else out


def canon_issue(raw):
    """Flatten the nested live issue into the fields we need."""
    p0 = (raw.get('projects') or [{}])[0]
    src = raw.get('source') or {}
    typ = raw.get('type')
    return {
        'issueId': str(raw.get('id') or ''),
        'type': TYPE_LABEL.get(typ, typ),
        'license': raw.get('license'),
        'details': raw.get('details'),
        'issueUrl': raw.get('url'),
        'dependency': src.get('name'),
        'packageLocator': src.get('id'),
        'version': src.get('version'),
        'packageManager': src.get('packageManager'),
        'project': p0.get('id'),
        'projectUrl': p0.get('url'),
        'revisionId': p0.get('revisionId'),
        'depth': p0.get('depth'),
        'scannedAt': p0.get('scannedAt'),
        'analyzedAt': p0.get('analyzedAt'),
        'firstFoundAt': p0.get('firstFoundAt') or raw.get('createdAt'),
        'snippetId': str(raw['analysisSnippetId']) if raw.get('analysisSnippetId') else None,
    }


def _get_nonempty(path, key, cfg, is_empty, tries=5):
    """GET with retry when the org answers a burst with a (valid-JSON) empty body. Returns the
    payload, or None if it stayed empty/errored across all tries (i.e. throttled/unavailable)."""
    for attempt in range(tries):
        try:
            d = get(path, key, cfg.base_url)
            if not is_empty(d):
                return d
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def enrich(issue, cfg, key):
    """Fetch the issue's snippet + match detail, classify each matched path in-line, and return
    the per-path results (loc_contig, import_only, match%) plus authoritative package info.
    `reason` distinguishes a genuinely match-less snippet ('no_matches') from one we could not
    classify because the API kept returning empty/throttled responses ('unavailable')."""
    rev, sid = issue.get('revisionId'), issue.get('snippetId')
    if not rev or not sid:
        return {'reason': 'no_snippet_ref', 'matches': []}
    reve = urllib.parse.quote(rev, safe='')
    # A real snippet payload has an id. A 404 ("Snippet not found") is genuine and permanent
    # (~10% of analysisSnippetIds are stale) -> don't retry it. An empty/throttled body has no
    # id -> retry before giving up.
    snip = None
    for attempt in range(5):
        try:
            d = get(f'/revisions/{reve}/snippets/{sid}', key, cfg.base_url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {'reason': 'snippet_not_found', 'matches': []}
            d = None
        except Exception:
            d = None
        cand = ((d or {}).get('snippet') or {})
        if cand.get('id'):
            snip = cand; break
        time.sleep(1.5 * (attempt + 1))
    if snip is None:
        return {'reason': 'unavailable', 'matches': []}
    paths = [m['path'] for m in (snip.get('matches') or []) if m.get('path')][:cfg.max_paths]
    if not paths:
        return {'reason': 'no_matches', 'package': snip.get('package'), 'purl': snip.get('purl'),
                'version': snip.get('version'), 'matches': []}
    skip_imports = not cfg.keep_imports
    matches = []
    for path in paths:
        md = _get_nonempty(
            f'/revisions/{reve}/snippets/{sid}/matches/{urllib.parse.quote(path, safe="")}',
            key, cfg, lambda d: 'matchDetails' not in (d or {}))
        if md is None:
            continue
        det = md.get('matchDetails', {})
        hi_d = [c for c in (det.get('detectedCode') or []) if c.get('isHighlighted')]
        hi_r = [c for c in (det.get('referenceCode') or []) if c.get('isHighlighted')]
        det_hl = [{'n': c['lineNumber'], 'l': c['line']} for c in hi_d]
        ref_hl = [{'n': c['lineNumber'], 'l': c['line']} for c in hi_r]
        # FOSSA often returns an empty detectedCode while referenceCode is populated; classify
        # on whichever side actually has the matched lines.
        basis = det_hl if det_hl else ref_hl
        matches.append({
            'path': path,
            'match_pct': det.get('matchPercentage'),
            'loc_contig': contiguous_loc(basis, cfg.max_gap, skip_imports),
            'import_only': is_import_only(basis),
            'used_reference': (not det_hl) and bool(ref_hl),
        })
    return {
        'package': snip.get('package') or issue.get('dependency'),
        'purl': snip.get('purl'),
        'version': snip.get('version') or issue.get('version'),
        'matches': matches,
    }


# --------------------------------------------------------------------------------------------
# Payload assembly. One ticket == one snippet match to a package. A package is often flagged
# under several licenses (FOSSA opens one issue per license), so those are collapsed into a
# single ticket that lists every flagged license rather than emitting a near-duplicate per one.
# --------------------------------------------------------------------------------------------
def issue_url(issue):
    iid = issue.get('issueId')
    return issue.get('issueUrl') or (f"{APP_BASE}/issues/licensing/{iid}" if iid else None)


def package_description(rep, pkg_name, pkg_ver):
    eco = rep.get('packageManager') or 'unknown ecosystem'
    bits = [f"{pkg_name or 'unknown package'} ({eco}) at version {pkg_ver or 'unspecified version'}."]
    if rep.get('depth') is not None:
        bits.append(f"Dependency depth {rep['depth']}.")
    if rep.get('packageLocator'):
        bits.append(f"FOSSA locator: {rep['packageLocator']}.")
    return ' '.join(bits)


def snippet_metadata(considered):
    locs = [m['loc_contig'] for m in considered]
    pcts = [m['match_pct'] for m in considered if isinstance(m.get('match_pct'), (int, float))]
    paths = sorted({m['path'] for m in considered if m.get('path')})
    return {
        'n_matches': len(considered),
        'max_loc_contig': max(locs) if locs else 0,
        'sum_loc_contig': sum(locs) if locs else 0,
        'best_match_pct': max(pcts) if pcts else None,
        'sample_paths': paths[:10],
        'n_paths': len(paths),
        'used_reference_fallback': any(m.get('used_reference') for m in considered),
    }


def build_ticket(group_issues, enr_pkg, considered, generated_at):
    """group_issues: the issues (one per flagged license) for a single package match.
    enr_pkg: package info from a successful snippet enrichment. considered: the surviving
    (non-import, in-range) snippet matches for the package."""
    rep = group_issues[0]
    name = (enr_pkg or {}).get('package') or rep.get('dependency')
    ver = (enr_pkg or {}).get('version') or rep.get('version')

    # One entry per distinct flagged license, strongest concern first isn't meaningful here so
    # sort alphabetically for stable output.
    seen, licenses = set(), []
    for it in sorted(group_issues, key=lambda x: (x.get('license') or '')):
        lic = it.get('license')
        if lic in seen:
            continue
        seen.add(lic)
        licenses.append({
            'license': lic,
            'type': it.get('type'),
            'issueId': it.get('issueId'),
            'fossaIssueLink': issue_url(it),
            'details': (it.get('details') or '').strip() or None,
        })

    lic_names = [l['license'] or 'no license' for l in licenses]
    shown = ', '.join(lic_names[:3]) + (f" +{len(lic_names) - 3} more" if len(lic_names) > 3 else '')
    first_found = min([i.get('firstFoundAt') for i in group_issues if i.get('firstFoundAt')], default=None)
    return {
        'summary': (f"Snippet copy of {name or '?'}@{ver or '?'} flagged under "
                    f"{len(licenses)} license(s): {shown}"),
        'package': {
            'name': name,
            'version': ver,
            'purl': (enr_pkg or {}).get('purl'),
            'locator': rep.get('packageLocator'),
            'description': package_description(rep, name, ver),
        },
        'flaggedLicenses': licenses,
        'licenseCount': len(licenses),
        'snippet': snippet_metadata(considered),
        'fossaProjectUrl': rep.get('projectUrl'),
        'timestamps': {
            'scannedAt': rep.get('scannedAt'),
            'analyzedAt': rep.get('analyzedAt'),
            'firstFoundAt': first_found,
            'generatedAt': generated_at,
        },
    }


# --------------------------------------------------------------------------------------------
def parse_config(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--min-contig', type=int, default=1,
                   help='keep an issue only if a non-import match has loc_contig >= N '
                        '(default 1; raise to refine toward longer true-positive blocks)')
    p.add_argument('--max-contig', type=int, default=None,
                   help='keep an issue only if a non-import match has loc_contig <= N (default: no cap)')
    p.add_argument('--keep-imports', action='store_true',
                   help='disable the final import filter (import-only matches count too)')
    p.add_argument('--max-gap', type=int, default=1,
                   help='line-number gap still treated as contiguous (default 1 = strictly consecutive)')
    p.add_argument('--workers', type=int, default=16, help='concurrent API requests (default 16)')
    p.add_argument('--max-paths', type=int, default=25,
                   help='max matched paths to classify per snippet (default 25)')
    p.add_argument('--limit', type=int, default=None, help='only process the first N issues (for testing)')
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

    key = os.environ.get('FOSSA_API_KEY')
    if not key:
        log("ERROR: FOSSA_API_KEY not set"); sys.exit(1)

    log(f"fetching issues: category={cfg.category} status={cfg.status} "
        f"scope[type]={cfg.scope} filter[issueSource][0]={cfg.source}")
    raw_issues = fetch_issues(cfg, key)
    issues = [canon_issue(r) for r in raw_issues]
    log(f"issues fetched: {len(issues)}")
    if not issues:
        log("no issues returned (org may be throttling; re-run in a moment)")

    # A package flagged under several licenses yields several issues that share one snippet match
    # (same revision + analysisSnippetId). Enrich each DISTINCT snippet once, keyed by that pair.
    snip_keys = {}
    for iss in issues:
        k = (iss.get('revisionId'), iss.get('snippetId'))
        snip_keys.setdefault(k, iss)
    log(f"distinct snippets to classify: {len(snip_keys)} (from {len(issues)} issues)")

    enr_by_key = {}
    done = 0
    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futs = {ex.submit(enrich, rep, cfg, key): k for k, rep in snip_keys.items()}
        for fut in as_completed(futs):
            k = futs[fut]
            done += 1
            if done % 100 == 0 or done == len(snip_keys):
                log(f"  classified {done}/{len(snip_keys)}")
            try:
                enr_by_key[k] = fut.result()
            except Exception as e:
                enr_by_key[k] = {'reason': f'enrich_err:{e}', 'matches': []}

    # Cleanup pass: retry throttled ('unavailable') snippets SEQUENTIALLY, which sidesteps the
    # burst-based throttling. 'snippet_not_found' (a permanent 404) is NOT retried.
    for rnd in range(2):
        stragglers = [k for k, e in enr_by_key.items()
                      if not e.get('matches') and e.get('reason') == 'unavailable']
        if not stragglers:
            break
        log(f"  cleanup pass {rnd + 1}: retrying {len(stragglers)} throttled snippets sequentially")
        for k in stragglers:
            enr_by_key[k] = enrich(snip_keys[k], cfg, key)

    hi = cfg.max_contig if cfg.max_contig is not None else 10 ** 9
    lo = cfg.min_contig

    # Group issues into one ticket per package match (same package locator + project).
    groups = {}
    for iss in issues:
        gk = (iss.get('packageLocator'), iss.get('projectUrl'))
        g = groups.setdefault(gk, {'issues': [], 'keys': []})
        g['issues'].append(iss)
        mk = (iss.get('revisionId'), iss.get('snippetId'))
        if mk not in g['keys']:
            g['keys'].append(mk)

    tickets, unclassified = [], []
    n_no_match = n_all_import = n_below = n_unavailable = n_not_found = 0
    for gk, g in groups.items():
        matches, enr_pkg, reasons = [], None, set()
        for mk in g['keys']:
            enr = enr_by_key.get(mk, {'reason': 'unavailable', 'matches': []})
            reasons.add(enr.get('reason'))
            if enr.get('purl') and enr_pkg is None:
                enr_pkg = enr
            matches.extend(enr.get('matches') or [])

        if not matches:
            # No classifiable snippet for the whole package -> attribute to the clearest reason.
            if 'unavailable' in reasons:
                n_unavailable += 1
                unclassified.append({'packageLocator': gk[0], 'projectUrl': gk[1],
                                     'reason': 'unavailable',
                                     'issueIds': [i.get('issueId') for i in g['issues']]})
            elif 'snippet_not_found' in reasons:
                n_not_found += 1
            else:
                n_no_match += 1
            continue

        # FINAL FILTER (walk_all's import logic): drop import_only matches unless --keep-imports.
        pool = matches if cfg.keep_imports else [m for m in matches if not m['import_only']]
        if not pool:
            n_all_import += 1
            continue
        # LoC-contig refinement.
        considered = [m for m in pool if lo <= m['loc_contig'] <= hi]
        if not considered:
            n_below += 1
            continue
        tickets.append(build_ticket(g['issues'], enr_pkg, considered, generated_at))

    tickets.sort(key=lambda t: (-(t['snippet']['max_loc_contig'] or 0),
                                -(t['snippet']['best_match_pct'] or 0)))

    payload = {
        'generatedAt': generated_at,
        'source': cfg.base_url,
        'grouping': 'one ticket per package match; flaggedLicenses lists all flagged licenses',
        'filter': {'import_filter': 'off (--keep-imports)' if cfg.keep_imports else 'on',
                   'min_contig': lo, 'max_contig': cfg.max_contig, 'max_gap': cfg.max_gap},
        'counts': {'issues_in': len(issues), 'packages_in': len(groups),
                   'tickets_out': len(tickets),
                   'licenses_in_tickets': sum(t['licenseCount'] for t in tickets),
                   'dropped_no_snippet_match': n_no_match,
                   'dropped_snippet_not_found': n_not_found,
                   'dropped_all_imports': n_all_import,
                   'dropped_below_loc_contig': n_below,
                   'unclassified_unavailable': n_unavailable},
        'unclassified': unclassified,
        'tickets': tickets,
    }
    with open(cfg.out, 'w') as f:
        json.dump(payload, f, indent=2)

    log(f"\nissues in:                    {len(issues)}  (packages: {len(groups)})")
    log(f"dropped (no snippet match):   {n_no_match}")
    log(f"dropped (snippet not found):  {n_not_found}")
    log(f"dropped (all import-only):    {n_all_import}")
    log(f"dropped (below loc_contig):   {n_below}")
    if n_unavailable:
        log(f"UNCLASSIFIED (throttled):     {n_unavailable} packages  (listed under 'unclassified'; re-run to top up)")
    log(f"JIRA TICKETS (per package):   {len(tickets)}  covering {sum(t['licenseCount'] for t in tickets)} license flags")
    log(f"wrote: {cfg.out}")


if __name__ == '__main__':
    main()
