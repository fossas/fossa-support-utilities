#!/usr/bin/env python3
"""Walk every FOSSA project's latest revision, gather snippet matches, filter by lines-of-code,
and emit a full JSON dump + a flat TSV summary. Concurrent match-detail fetches.

Env vars:
  FOSSA_API_KEY  (required)
  LOC_LO         lower LoC bound for the TSV (default 10)
  LOC_HI         upper LoC bound for the TSV (default 20)
  WORKERS        concurrent API requests (default 16)
"""
import os, sys, json, urllib.parse, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor, as_completed

KEY = os.environ['FOSSA_API_KEY']
BASE = 'https://app.fossa.com/api'
LO, HI = int(os.environ.get('LOC_LO', 10)), int(os.environ.get('LOC_HI', 20))
WORKERS = int(os.environ.get('WORKERS', 16))

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

def main():
    projects = list_projects()
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
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
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
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
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
                    'detectedHighlighted': [{'n': c['lineNumber'], 'l': c['line']} for c in hi_d],
                    'referenceHighlighted': [{'n': c['lineNumber'], 'l': c['line']} for c in hi_r],
                })
        log(f"  match-details done in {time.time()-t0:.1f}s")

    in_range = [m for m in all_matches if LO <= m['loc_detected'] <= HI]
    log(f"\nTOTAL matches: {len(all_matches)}")
    log(f"IN RANGE (loc_detected in [{LO},{HI}]): {len(in_range)}")

    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, 'snippet_matches_all.json'), 'w') as f:
        json.dump({'loc_range': [LO, HI], 'total': len(all_matches), 'in_range': len(in_range),
                   'matches_in_range': in_range, 'all_matches': all_matches}, f, indent=2)
    with open(os.path.join(out_dir, 'snippet_matches_summary.tsv'), 'w') as f:
        f.write('loc_local\tloc_upstream\tspan_local\tmatch%\tpackage\tversion\tpath\tproject\tsnippetId\trejected\n')
        for m in sorted(in_range, key=lambda x: (-x['loc_detected'], x['project'])):
            f.write(f"{m['loc_detected']}\t{m['loc_reference']}\t{m['span_detected']}\t{m['matchPercentage']}\t{m['package']}\t{m['version']}\t{m['path']}\t{m['project']}\t{m['snippetId']}\t{m['rejected']}\n")
    log("wrote: snippet_matches_all.json, snippet_matches_summary.tsv")

if __name__ == '__main__':
    main()
