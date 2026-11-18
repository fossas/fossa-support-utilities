# FOSSA Support Utilities

Scripts the FOSSA Customer Success and Support team uses to do things the FOSSA UI doesn't do in bulk: exporting issues and ignore rules, adding dependencies from the command line, triaging snippet matches, and managing teams in CI.

Each tool lives in its own folder with its own README. This page shows what's here and how to run any of them.

> These are support utilities, not a supported FOSSA product. Some of them call **internal** FOSSA endpoints (marked below) that can change without notice. For the supported API, see the [FOSSA API reference](https://docs.fossa.com/reference).

## Tools

| Tool | What it does | Language | Endpoints |
|---|---|---|---|
| [`issues/export/fossa-issues.sh`](issues/export/exporting-issues.md) | Pull licensing, vulnerability or quality issues for an org, project, revision or release group, without hand-building `curl` commands. Includes a `--summary` mode. | bash, `curl` (+ `jq` optional) | Public (Issues V2) |
| [`exceptions/export/export-ignore-rules.py`](exceptions/export/README.md) | Export an org's ignore rules (issue exceptions) as JSON or CSV. | Python + `requests` | Public |
| [`add-dependencies/fossa_add_dependency.py`](add-dependencies/README.md) | Check that FOSSA can find a package, and add it to a project as a manual dependency (the UI's **Add Dependency → Search for your packages** flow). | Python (stdlib) | Validate: public. **Add: internal** |
| [`snippets/results-analysis/walk_all.py`](snippets/results-analysis/README.md) | Walk every project in an org, collect snippet matches, and write a JSON dump plus a TSV ranked by `loc_contig` (longest run of consecutive matched lines, imports removed). | Python (stdlib) | Public + snippet endpoints |
| [`snippets/results-output/snippet_issues_to_jira.py`](snippets/results-output/README.md) | Turn snippet licensing issues into a Jira-ready JSON payload: one ticket per matched package, with `.properties` and import-only matches filtered out. | Python (stdlib) | Public + snippet endpoints |
| [`fossa-deps/samples/add-teams/`](fossa-deps/samples/add-teams/TEAM-WORKFLOW-README.md) | GitHub Actions workflow and shell script that create a FOSSA team if it's missing, then run `fossa analyze` with the project assigned to it. | GitHub Actions, bash | Public |

### Which tool do I need?

- **"Give me all the issues for this project or release"** → `fossa-issues.sh`
- **"What has this org ignored, and why?"** → `export-ignore-rules.py`
- **"FOSSA missed a dependency; add it"** → `fossa_add_dependency.py`
- **"Which snippet matches are real copied code?"** → `walk_all.py` (whole org, TSV for triage)
- **"Open Jira tickets for snippet matches"** → `snippet_issues_to_jira.py`
- **"New repos should land in the right team automatically"** → `fossa-deps/samples/add-teams/`

## Getting started

```bash
git clone git@github.com:fossas/fossa-support-utilities.git
cd fossa-support-utilities
```

Requirements, depending on the tool:

- **Python 3.8+.** Everything except `export-ignore-rules.py` uses only the standard library; that one also needs `pip install requests`.
- **bash, `curl`**, and optionally **`jq`** for the shell tools.
- The [FOSSA CLI](https://github.com/fossas/fossa-cli) for the team workflow.

## Authentication

Every tool needs a FOSSA API token. Use a **full-access** token: push-only tokens can upload, but can't read issues, snippets or ignore rules. Results only include what the token's user can see. For org-wide exports, use a token from a user who can view every project.

Pass the token in an environment variable. Don't put it on the command line, where it's saved in your shell history and visible to other users through `ps`:

```bash
read -rs FOSSA_API_KEY && export FOSSA_API_KEY   # paste the token; nothing is echoed
```

The tools don't all use the same variable names yet:

| Tool | Token | FOSSA server (default `https://app.fossa.com`) |
|---|---|---|
| `fossa_add_dependency.py` | `FOSSA_API_KEY` | `FOSSA_ENDPOINT` or `--endpoint` |
| `walk_all.py` | `FOSSA_API_KEY` | app.fossa.com only (hardcoded) |
| `snippet_issues_to_jira.py` | `FOSSA_API_KEY` | `--base-url`, the API base including `/api` (e.g. `https://app.fossa.com/api`). Links in the output still point to app.fossa.com. |
| `manage-team-and-analyze.sh` | `FOSSA_API_KEY` | `FOSSA_ENDPOINT` |
| `fossa-issues.sh` | `FOSSA_API_TOKEN` | `FOSSA_HOST` or `--host` |
| `export-ignore-rules.py` | Passed as the first argument (`python export-ignore-rules.py <token>`). Clear it from your shell history afterwards. | Always app.fossa.com; can't be changed |

For a dedicated or self-hosted FOSSA instance (for example `https://app.<customer>.fossa.com`), set the server variable or flag, and use a token from that instance. Tokens only work on the instance that issued them.

## Safety

- **Read-only by default:** the export and analysis tools (`fossa-issues.sh`, `export-ignore-rules.py`, `walk_all.py`, `snippet_issues_to_jira.py`) only read from FOSSA.
- **These tools write:**
  - `fossa_add_dependency.py` changes a project's dependencies. Run it with `--validate-only` first.
  - The team workflow and script create teams and upload analyses.
- **Customer data:** exports and JSON dumps contain the data of whichever org the token belongs to. Keep them out of this repository, and delete them when you're done.

## Contributing

New tools are welcome. To keep the repo usable:

1. Put the tool in a folder named after what it does (`<area>/<action>/`), with a `README.md` covering purpose, requirements, setup, flags and examples.
2. Read the token from `FOSSA_API_KEY`, and the server from `FOSSA_ENDPOINT` (default `https://app.fossa.com`). Never accept a token as an argument.
3. Default to a dry run for anything that writes, and require an explicit flag (for example `--apply`) to make changes.
4. Say which endpoints are public and which are internal. Prefer the [documented API](https://docs.fossa.com/reference).
5. Don't commit tokens, customer names, org IDs or exported data.
6. Add a row to the **Tools** table above.

## Support

Questions or problems: contact your FOSSA Customer Success team, or support@fossa.com, with the tool name, the command you ran (token removed) and the output.
