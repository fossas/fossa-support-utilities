#!/usr/bin/env python3
"""Walk every FOSSA project's latest revision, gather snippet matches, and emit a full JSON
dump + a flat TSV that reports ALL matches with a `loc_contig` column (longest run of
CONSECUTIVE matched lines with import statements removed).

By default every match is reported (import-only rows excluded). Use the flags below to
narrow to a loc_contig range or to change behavior.

Flags:
  --all                 report ALL matches, no loc_contig range filter (this is also the
                        default when no --min-contig/--max-contig is given). Import-only
                        rows are still dropped unless --keep-imports.
  --min-contig N        keep only matches whose loc_contig >= N
  --max-contig N        keep only matches whose loc_contig <= N
  --keep-imports        keep import-only matches (default: drop them)
  --raw-loc             filter/sort on the raw flagged-line count (loc_match) instead of
                        loc_contig (contiguous, imports removed)
  --max-gap N           line-number gap still treated as contiguous (default 1 = strictly
                        consecutive; raise to bridge small gaps within a block)
  --workers N           concurrent API requests (default 16)
  --projects L [L ...]  limit to these FOSSA project locators (default: every project the
                        token can see)
  --out-prefix P        output file prefix (default: snippet_matches)

Env vars:
  FOSSA_API_KEY   (required)
  LOC_LO / LOC_HI back-compat defaults for --min-contig / --max-contig
  WORKERS         back-compat default for --workers
  MAX_GAP         back-compat default for --max-gap
  EXCLUDE_IMPORTS back-compat: set 0 to default --keep-imports on

Outputs (<prefix> = --out-prefix):
  <prefix>_all.json      every match with loc_contig, loc_match, import_only, highlights, ...
  <prefix>_summary.tsv   the selected rows (all by default) with loc_contig as the first column
"""
import os, sys, json, re, urllib.parse, urllib.request, urllib.error, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

KEY = os.environ['FOSSA_API_KEY']
BASE = 'https://app.fossa.com/api'


def _envbool(name, default='1'):
    return os.environ.get(name, default) not in ('0', 'false', 'False', '')


def parse_config(argv=None):
    p = argparse.ArgumentParser(
        description='Walk FOSSA projects, gather snippet matches, emit JSON + a TSV that '
                    'reports all matches with a loc_contig (contiguous, imports removed) column.')
    p.add_argument('--all', action='store_true',
                   help='report ALL matches (no loc_contig range filter). Import-only rows are '
                        'still dropped unless --keep-imports.')
    p.add_argument('--min-contig', type=int,
                   default=(int(os.environ['LOC_LO']) if os.environ.get('LOC_LO') else None),
                   help='minimum loc_contig to include (longest run of consecutive matched '
                        'lines, imports excluded)')
    p.add_argument('--max-contig', type=int,
                   default=(int(os.environ['LOC_HI']) if os.environ.get('LOC_HI') else None),
                   help='maximum loc_contig to include')
    p.add_argument('--keep-imports', action='store_true', default=not _envbool('EXCLUDE_IMPORTS'),
                   help='keep import-only matches (default: drop them)')
    p.add_argument('--raw-loc', action='store_true',
                   help='filter/sort on raw flagged-line count (loc_match) instead of loc_contig')
    p.add_argument('--max-gap', type=int, default=int(os.environ.get('MAX_GAP', 1)),
                   help='line-number gap still treated as contiguous (default 1)')
    p.add_argument('--workers', type=int, default=int(os.environ.get('WORKERS', 16)),
                   help='concurrent API requests (default 16)')
    p.add_argument('--projects', nargs='*', default=None,
                   help='limit to these FOSSA project locators (default: all visible projects)')
    p.add_argument('--out-prefix', default='snippet_matches', help='output file prefix')
    return p.parse_args(argv)


# Lines that are nothing but a module/package import across common languages. A match is
# considered "import only" when every non-blank flagged line matches one of these, so it
# represents boilerplate import duplication rather than meaningful copied logic.
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
    # Go import-block members with a path separator, e.g. _ "github.com/lib/pq" or alias "x/y".
    # Requires a "/" so plain quoted strings (versions, log text) are not misread as imports.
    r'(_|\.|\w+)?\s*"[^"]*/[^"]*"\s*$',
]
_IMPORT_RE = re.compile(r'^\s*(?:' + '|'.join(_IMPORT_PATTERNS) + r')')


def is_import_line(line):
    """True if a single source line is purely an import/include/require statement."""
    return bool(_IMPORT_RE.match(line or ''))


def is_import_only(highlighted):
    """True if a match's flagged lines exist and are all imports (blank lines ignored)."""
    non_blank = [h for h in highlighted if (h.get('l') or '').strip()]
    return bool(non_blank) and all(is_import_line(h['l']) for h in non_blank)


def contiguous_loc(highlighted, max_gap=1, skip_imports=True):
    """Length of the longest run of *consecutive* flagged lines (by line number), ignoring
    blank lines and—when skip_imports—import statements. A run continues while consecutive
    flagged line numbers differ by <= max_gap. This measures one contiguous block of matched
    code rather than scattered clusters spread across the file."""
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


def log(msg):
    sys.stderr.write(msg + '\n'); sys.stderr.flush()

def get(path, params=None, retries=4):
    qs = ('?' + urllib.parse.urlencode(params, doseq=True)) if params else ''
    url = BASE + path + qs
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {KEY}'})
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

def list_projects():
    out = []; page = 1
    while True:
        d = get('/v2/projects', {'count': 100, 'page': page})
        out.extend(d.get('projects', []))
        if len(out) >= d.get('total', 0) or not d.get('projects'):
            break
        page += 1
    return out

def walk_paths(rev_enc, p, files):
    data = get(f'/revisions/{rev_enc}/snippets/paths', {'path': p})
    for entry in data.get('paths', []):
        if entry['type'] == 'directory':
            walk_paths(rev_enc, entry['path'], files)
        else:
            files.append(entry)

def fetch_match_detail(rev_enc, snippet_id, path):
    return get(f'/revisions/{rev_enc}/snippets/{urllib.parse.quote(snippet_id, safe="")}/matches/{urllib.parse.quote(path, safe="")}')

def gather_matches(cfg):
    projects = list_projects()
    if cfg.projects:
        wanted = set(cfg.projects)
        projects = [p for p in projects if p['id'] in wanted]
        log(f"projects (filtered to --projects): {len(projects)}")
    else:
        log(f"projects: {len(projects)}")

    all_matches = []
    for pi, proj in enumerate(projects, 1):
        rev = proj['latestRevision']['locator']
        rev_enc = urllib.parse.quote(rev, safe='')
        log(f"\n[{pi}/{len(projects)}] {proj['id']}")
        try:
            cnt = get(f'/revisions/{rev_enc}/snippets/count', {'path': '/'}).get('count', 0)
        except Exception as e:
            log(f"  count err: {e}"); cnt = 0
        log(f"  total snippets: {cnt}")
        if cnt == 0:
            continue

        t0 = time.time()
        files = []
        try:
            walk_paths(rev_enc, '/', files)
        except Exception as e:
            log(f"  walk err: {e}"); continue
        log(f"  files w/ snippets: {len(files)}  (walk {time.time()-t0:.1f}s)")

        # Parallel: list snippets per file
        t0 = time.time()
        snip_pairs = []  # (file_path, snippet_dict)
        with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futs = {ex.submit(get, f'/revisions/{rev_enc}/snippets', {'path': f['path'], 'pageSize': 200, 'page': 1}): f['path'] for f in files}
            for fut in as_completed(futs):
                fp = futs[fut]
                try:
                    snips = fut.result().get('results', [])
                    for s in snips:
                        snip_pairs.append((fp, s))
                except Exception as e:
                    log(f"  list err @ {fp}: {e}")
        log(f"  snippet entries: {len(snip_pairs)}  (list {time.time()-t0:.1f}s)")

        # Parallel: fetch match detail per (snippet, path)
        t0 = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futs = {ex.submit(fetch_match_detail, rev_enc, s['id'], fp): (fp, s) for (fp, s) in snip_pairs}
            for fut in as_completed(futs):
                fp, s = futs[fut]
                done += 1
                if done % 50 == 0 or done == len(snip_pairs):
                    log(f"    match-details {done}/{len(snip_pairs)}")
                try:
                    md = fut.result()
                except Exception as e:
                    log(f"    err {s['id']} @ {fp}: {e}"); continue
                det = md.get('matchDetails', {})
                detected = det.get('detectedCode') or []
                ref = det.get('referenceCode') or []
                hi_d = [c for c in detected if c.get('isHighlighted')]
                hi_r = [c for c in ref if c.get('isHighlighted')]
                loc_d = len(hi_d)
                span_d = (max(c['lineNumber'] for c in hi_d) - min(c['lineNumber'] for c in hi_d) + 1) if hi_d else 0
                loc_r = len(hi_r)
                span_r = (max(c['lineNumber'] for c in hi_r) - min(c['lineNumber'] for c in hi_r) + 1) if hi_r else 0
                detected_hl = [{'n': c['lineNumber'], 'l': c['line']} for c in hi_d]
                reference_hl = [{'n': c['lineNumber'], 'l': c['line']} for c in hi_r]
                # FOSSA frequently returns an empty detectedCode while referenceCode is
                # populated; classify imports on whichever side actually has the matched lines.
                import_basis = detected_hl if detected_hl else reference_hl
                all_matches.append({
                    'project': proj['id'],
                    'projectTitle': proj.get('title'),
                    'revision': rev,
                    'path': fp,
                    'snippetId': s['id'],
                    'package': s.get('package'),
                    'version': s.get('version'),
                    'purl': s.get('purl'),
                    'kind': s.get('kind'),
                    'matchPercentage': det.get('matchPercentage'),
                    'rejected': bool(det.get('rejectionDetails')),
                    'loc_detected': loc_d,
                    'span_detected': span_d,
                    'loc_reference': loc_r,
                    'span_reference': span_r,
                    'import_only': is_import_only(import_basis),
                    'detectedHighlighted': detected_hl,
                    'referenceHighlighted': reference_hl,
                })
        log(f"  match-details done in {time.time()-t0:.1f}s")
    return all_matches


def main(argv=None):
    cfg = parse_config(argv)
    all_matches = gather_matches(cfg)

    # FOSSA often returns an empty detectedCode (loc_detected == 0) while referenceCode is
    # populated. Use detectedCode highlights when present, otherwise fall back to referenceCode.
    #   loc_match  = raw count of flagged lines (may be scattered, includes imports).
    #   loc_contig = longest run of CONSECUTIVE flagged lines with imports removed (one block).
    skip_imports = not cfg.keep_imports
    for m in all_matches:
        m['loc_match'] = m['loc_detected'] if m['loc_detected'] else m['loc_reference']
        basis = m['detectedHighlighted'] if m['detectedHighlighted'] else m['referenceHighlighted']
        m['loc_contig'] = contiguous_loc(basis, cfg.max_gap, skip_imports)
    fallback_n = sum(1 for m in all_matches if not m['loc_detected'] and m['loc_reference'])

    metric = 'loc_match' if cfg.raw_loc else 'loc_contig'

    # Selection. Default (and --all) reports every match. A loc_contig range applies only when
    # --min-contig / --max-contig is given and --all is not set.
    use_range = (not cfg.all) and (cfg.min_contig is not None or cfg.max_contig is not None)
    if use_range:
        lo = cfg.min_contig if cfg.min_contig is not None else 0
        hi = cfg.max_contig if cfg.max_contig is not None else 10 ** 9
        selected = [m for m in all_matches if lo <= m[metric] <= hi]
        sel_desc = f'{metric} in [{lo},{hi}]'
    else:
        selected = list(all_matches)
        sel_desc = 'all'

    # Import-only rows are dropped from the spreadsheet unless --keep-imports (they always
    # remain in the full JSON with their import_only flag).
    import_only_n = sum(1 for m in selected if m['import_only'])
    if not cfg.keep_imports:
        selected = [m for m in selected if not m['import_only']]

    log(f"\nTOTAL matches: {len(all_matches)}")
    if fallback_n:
        log(f"NOTE: {fallback_n} matches had empty detectedCode; used loc_reference highlights")
    log(f"selection: {sel_desc}  (metric={metric}, max_gap={cfg.max_gap}, "
        f"imports {'kept' if cfg.keep_imports else 'excluded'})")
    log(f"import-only matches in selection: {import_only_n} "
        f"({'kept' if cfg.keep_imports else 'excluded'})")
    log(f"ROWS IN SPREADSHEET: {len(selected)}")

    out_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(out_dir, f'{cfg.out_prefix}_all.json')
    tsv_path = os.path.join(out_dir, f'{cfg.out_prefix}_summary.tsv')
    with open(json_path, 'w') as f:
        json.dump({'selection': sel_desc, 'filter_metric': metric, 'max_gap': cfg.max_gap,
                   'total': len(all_matches), 'in_range': len(selected),
                   'keep_imports': cfg.keep_imports,
                   'exclude_imports': not cfg.keep_imports,
                   'import_only_excluded': import_only_n if not cfg.keep_imports else 0,
                   'loc_reference_fallback': fallback_n,
                   'matches_in_range': selected, 'all_matches': all_matches}, f, indent=2)
    with open(tsv_path, 'w') as f:
        f.write('loc_contig\tloc_match\timport_only\tloc_local\tloc_upstream\tspan_local\t'
                'match%\tpackage\tversion\tpath\tproject\tpurl\tsnippetId\trejected\n')
        for m in sorted(selected, key=lambda x: (-x['loc_contig'], -x['loc_match'], x['project'])):
            f.write(f"{m['loc_contig']}\t{m['loc_match']}\t{m['import_only']}\t{m['loc_detected']}\t"
                    f"{m['loc_reference']}\t{m['span_detected']}\t{m['matchPercentage']}\t"
                    f"{m['package']}\t{m['version']}\t{m['path']}\t{m['project']}\t{m.get('purl')}\t"
                    f"{m['snippetId']}\t{m['rejected']}\n")
    log(f"wrote: {os.path.basename(json_path)}, {os.path.basename(tsv_path)}")


if __name__ == '__main__':
    main()
