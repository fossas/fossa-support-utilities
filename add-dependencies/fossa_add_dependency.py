#!/usr/bin/env python3
"""Validate and/or add a manual dependency to a FOSSA project's Dependencies tab.

Two FOSSA endpoints are used:

  * VALIDATE (public, documented):
      GET /api/v2/dependencies/{locator}
    Returns the global dependency object if FOSSA has the package/revision indexed.
    A 404 means FOSSA has not resolved it (yet).

  * ADD (internal, what the web "Add Dependency" modal calls -- NOT in the public
    OpenAPI spec, so treat it as unsupported / subject to change):
      POST /api/revisions/{parentLocator}/dependencies
      body: {"dependencyData": {"kind": "new", "newDependency": {...}}}

Locator format is  fetcher+packageName$revision , e.g.  npm+lodash$4.17.21 .

Modes:
  validate-then-add  (default) validate first; only POST the add if it resolves
                     (or if --force is set)
  add-and-report     skip validation, POST the add, report FOSSA's result/error
                     (handles not-yet-indexed packages, since the add itself is
                     the resolution step)
  validate-only      just run the lookup and report, never add

Examples:
  export FOSSA_API_KEY=<token>
  # validate then add lodash to a project
  python3 fossa_add_dependency.py \
      --project 'custom+123/my-project$1.0.0' \
      --fetcher npm --name lodash --revision 4.17.21

  # just check whether FOSSA knows a package
  python3 fossa_add_dependency.py --validate-only \
      --fetcher pip --name requests --revision 2.31.0

  # add without pre-checking (let the add resolve it)
  python3 fossa_add_dependency.py --mode add-and-report \
      --project 'custom+123/my-project$1.0.0' \
      --fetcher gem --name rails --revision 7.1.0
"""
import os
import sys
import json
import argparse
import urllib.parse
import urllib.request
import urllib.error


def log(msg):
    sys.stderr.write(msg + '\n')
    sys.stderr.flush()


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Validate and/or add a manual dependency to a FOSSA project.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--project', '--parent', dest='project',
                   help="parent project revision locator the dependency is added to, "
                        "e.g. 'custom+123/my-project$1.0.0'. Required unless --validate-only.")
    p.add_argument('--fetcher', help="package manager / fetcher, e.g. npm, pip, gem, mvn")
    p.add_argument('--name', help="package name, e.g. lodash")
    p.add_argument('--revision', help="package version/revision, e.g. 4.17.21")
    p.add_argument('--locator',
                   help="full dependency locator (fetcher+name$revision); overrides "
                        "--fetcher/--name/--revision if given")
    # Linux fetchers need distro/arch on the locator payload
    p.add_argument('--distro', help="linux distro (for apk/deb/rpm fetchers)")
    p.add_argument('--distro-release', dest='distro_release', help="linux distro release")
    p.add_argument('--arch', help="cpu architecture (for linux fetchers)")
    p.add_argument('--mode', choices=('validate-then-add', 'add-and-report', 'validate-only'),
                   default='validate-then-add', help="workflow to run (default: validate-then-add)")
    p.add_argument('--validate-only', action='store_true',
                   help="shortcut for --mode validate-only")
    p.add_argument('--force', action='store_true',
                   help="in validate-then-add, add even if validation says not resolved")
    p.add_argument('--replace', metavar='LOCATOR',
                   help="replace this existing (unresolved) dependency locator instead of "
                        "adding a new one")
    p.add_argument('--endpoint', default=os.environ.get('FOSSA_ENDPOINT', 'https://app.fossa.com'),
                   help="FOSSA base URL (default: https://app.fossa.com or $FOSSA_ENDPOINT)")
    args = p.parse_args(argv)
    if args.validate_only:
        args.mode = 'validate-only'
    return args


def build_locator(args):
    """Return fetcher+name$revision, from --locator or the discrete flags."""
    if args.locator:
        return args.locator
    if not (args.fetcher and args.name and args.revision):
        raise SystemExit("error: provide --locator, or all of --fetcher --name --revision")
    return f"{args.fetcher}+{args.name}${args.revision}"


def split_locator(locator):
    """fetcher+name$revision -> (fetcher, name, revision). Tolerant of missing revision."""
    fetcher, _, rest = locator.partition('+')
    name, _, revision = rest.partition('$')
    return fetcher, name, revision


def request(method, url, key, body=None):
    """Return (status, parsed_json_or_text). Never raises on HTTP error status."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Authorization': f'Bearer {key}'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, _maybe_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, _maybe_json(raw)
    except urllib.error.URLError as e:
        raise SystemExit(f"error: could not reach {url}: {e}")


def _maybe_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def validate(endpoint, key, locator):
    """GET /api/v2/dependencies/{locator}. Returns (resolved: bool, status, payload)."""
    enc = urllib.parse.quote(locator, safe='')
    url = f"{endpoint}/api/v2/dependencies/{enc}?includeResolutionNotes=true"
    status, payload = request('GET', url, key)
    resolved = status == 200 and isinstance(payload, dict)
    return resolved, status, payload


def build_new_dependency(args):
    """The `newDependency` object for a locator-kind manual dependency."""
    fetcher, name, revision = (args.fetcher, args.name, args.revision)
    if args.locator and not (fetcher and name and revision):
        fetcher, name, revision = split_locator(args.locator)
    dep = {
        'kind': 'locator',
        'fetcher': fetcher,
        'packageName': name,
        'revision': revision,
    }
    if args.distro:
        dep['distro'] = args.distro
    if args.distro_release:
        dep['distroRelease'] = args.distro_release
    if args.arch:
        dep['arch'] = args.arch
    return dep


def add(endpoint, key, project_locator, new_dependency, replace_locator=None):
    """POST /api/revisions/{parentLocator}/dependencies. Returns (ok, status, payload)."""
    enc = urllib.parse.quote(project_locator, safe='')
    url = f"{endpoint}/api/revisions/{enc}/dependencies"
    if replace_locator:
        dep_data = {'kind': 'replace', 'newDependency': new_dependency,
                    'overwrittenLocator': replace_locator}
    else:
        dep_data = {'kind': 'new', 'newDependency': new_dependency}
    status, payload = request('POST', url, key, {'dependencyData': dep_data})
    ok = 200 <= status < 300
    return ok, status, payload


def main(argv=None):
    args = parse_args(argv)
    key = os.environ.get('FOSSA_API_KEY')
    if not key:
        raise SystemExit("error: set FOSSA_API_KEY")

    locator = build_locator(args)
    result = {'locator': locator, 'mode': args.mode}

    if args.mode in ('validate-then-add', 'validate-only'):
        log(f"validating {locator} ...")
        resolved, status, payload = validate(args.endpoint, key, locator)
        result['validate'] = {'resolved': resolved, 'status': status}
        if resolved:
            log(f"  RESOLVED (FOSSA knows this package)  [{status}]")
        else:
            log(f"  NOT resolved  [{status}]")
        if args.mode == 'validate-only':
            result['validate']['response'] = payload
            print(json.dumps(result, indent=2))
            return 0 if resolved else 2
        if not resolved and not args.force:
            log("  skipping add (use --force to add anyway, or --mode add-and-report)")
            print(json.dumps(result, indent=2))
            return 2

    if not args.project:
        raise SystemExit("error: --project (parent locator) is required to add a dependency")

    new_dep = build_new_dependency(args)
    verb = 'replacing' if args.replace else 'adding'
    log(f"{verb} dependency on project {args.project} ...")
    ok, status, payload = add(args.endpoint, key, args.project, new_dep, args.replace)
    result['add'] = {'ok': ok, 'status': status, 'response': payload}
    if ok:
        log(f"  ADDED  [{status}]")
    else:
        log(f"  FAILED  [{status}] -- see response in output")
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
