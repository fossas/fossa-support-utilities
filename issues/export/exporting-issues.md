# fossa-issues.sh

A tiny CLI over the **FOSSA Issues V2 API** (`GET /api/v2/issues`). Pull issues -
vulnerabilities, licensing, or quality - scoped to a whole org, a project, a
specific revision, or a release group, without hand-building `curl` commands.

URL-encoding of locators (`+`, `/`), the `scope[...]` params, and `ids[]` is
handled for you.

---

## Requirements

- `curl`
- `jq` *(optional)* - used for pretty output and `--summary`; without it the script prints raw JSON.
- A **full-access** FOSSA API token. Push-only tokens will not return issue details.

## Setup

```bash
export FOSSA_API_TOKEN="<your full-access API token>"
# Optional - defaults to https://app.fossa.com:
export FOSSA_HOST="https://app.fossa.com"

chmod +x fossa-issues.sh
```

## Usage

```bash
./fossa-issues.sh [options]
```

### Options

| Option | Description | Default |
|---|---|---|
| `-c`, `--category <cat>` | `licensing` \| `vulnerability` \| `quality` | `vulnerability` |
| `-s`, `--status <status>` | `active` \| `ignored` \| `all` | `active` |
| **Scope (pick exactly one):** | | |
| `-p`, `--project <locator>` | Project locator, e.g. `custom+12345/github.com/ORG/repo` | |
| `-r`, `--revision <rev>` | Specific revision (use **with** `--project`); the part after `$` in the locator (git SHA / version) | |
| `--scan-id <n>` | Specific `revisionScanId` (optional, with `--project`/`--revision`) | |
| `-g`, `--release-group <id>` | Release group id (numeric) | |
| `--release <id>` | Release id (numeric) - **required** with `--release-group` | |
| `--release-scan-id <n>` | Specific `releaseScanId` (optional, with `--release-group`) | |
| `--global` | Whole-organization scope | |
| **Filters / paging:** | | |
| `--ids <id,id,...>` | Only these issue id(s), comma-separated | |
| `--page <n>` | Page number | `1` |
| `--count <n>` | Results per page | `100` |
| **Output:** | | |
| `--raw` | Print raw JSON (no `jq` formatting) | |
| `--summary` | Compact table (id, type, dependency) instead of full JSON | |
| `--host <url>` | Override FOSSA host | `$FOSSA_HOST` or `https://app.fossa.com` |
| `-h`, `--help` | Show help | |

### Quick reference

| I want… | Flags |
|---|---|
| Everything in the org | `--global` |
| A project (latest analyzed revision) | `-p <locator>` |
| A **specific revision** of a project | `-p <locator> -r <revision>` |
| A release group's release | `-g <rgId> --release <releaseId>` |
| Only vulnerabilities / licensing / quality | `-c vulnerability` / `-c licensing` / `-c quality` |
| Ignored or all issues (not just active) | `-s ignored` / `-s all` |
| Specific issue(s) by id | `--ids 123,456` |
| A compact table | `--summary` |

## Examples

```bash
# All active vulnerabilities in a project (latest analyzed revision)
./fossa-issues.sh -c vulnerability -p "custom+12345/github.com/ORG/repo"

# Licensing issues for a SPECIFIC revision (matches a deep link / `fossa test` URL)
./fossa-issues.sh -c licensing -p "custom+12345/github.com/ORG/repo" -r 462ad04b...

# A specific issue by id (e.g. verify a URL is valid), scoped to the right revision
./fossa-issues.sh -c vulnerability -p "custom+12345/github.com/ORG/repo" -r 462ad04b... --ids 73426009

# All quality issues for a release group's release
./fossa-issues.sh -c quality -g 1234 --release 5678

# Org-wide active vulnerabilities, compact table
./fossa-issues.sh -c vulnerability --global --summary
```

## How scope maps to the API

| Scope flag | `scope[type]` | Other params sent |
|---|---|---|
| `--project` | `project` | `scope[id]=<locator>` (+ `scope[revision]`, `scope[revisionScanId]` if given) |
| `--release-group` | `releaseGroup` | `scope[id]=<rgId>`, `scope[release]=<releaseId>` (+ `scope[releaseScanId]` if given) |
| `--global` | `global` | - |

## Important: project scope vs. a specific revision

When you use `--project` **without** `--revision`, the API resolves to the
project's **last-analyzed revision** and returns only issues that are `active`
and current (`latest`) on that revision.

If you're verifying a specific issue or URL that is pinned to a particular
revision (for example, a `fossa test` failure on a feature branch - those URLs
look like `…/refs/branch/<branch>/<revisionId>/issues/…`), pass `-r <revisionId>`
so the query targets the **same revision the link points to**. Otherwise the
call can come back empty even though the issue is real, because the project's
last-analyzed revision may be a different branch/commit.

## Output

- **Default:** pretty-printed JSON (via `jq`).
- **`--summary`:** a compact `id  type  dependency` table plus a total count.
- **`--raw`** (or when `jq` is not installed): the API's raw JSON response.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Error: set FOSSA_API_TOKEN` | The `FOSSA_API_TOKEN` env var isn't exported. |
| Empty `issues` for a project | No revision supplied and the issue lives on a non-latest revision - add `-r <revision>`. See the section above. |
| Issue details missing / empty | A push-only token was used. Use a full-access token. |
| `401 Unauthorized` | Token invalid/expired, or wrong `--host`. |
| Raw JSON instead of pretty/table | `jq` isn't installed (`brew install jq` / `apt-get install jq`). |
