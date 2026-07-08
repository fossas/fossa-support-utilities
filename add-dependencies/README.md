# fossa_add_dependency.py

Validate and/or add a **manual dependency** to a FOSSA project's Dependencies tab from the command line. This is the scriptable equivalent of the web UI's **Add Dependency > "Search for your packages across the web"** flow.

## What it does

The tool wraps two FOSSA endpoints:

| Step | Endpoint | Status |
|---|---|---|
| **Validate** ("can FOSSA find this package?") | `GET /api/v2/dependencies/{locator}` | Public, documented |
| **Add** (attach the dependency to a project) | `POST /api/revisions/{parentLocator}/dependencies` | Internal (what the web modal calls) |

> ⚠️ The **add** endpoint is the internal endpoint the FOSSA web app uses. It is **not** part of the public OpenAPI spec, so treat it as unsupported and subject to change. The **validate** endpoint is public and stable.

## Requirements

- Python 3.8+ (standard library only)
- A FOSSA API token

```sh
export FOSSA_API_KEY=<your token>
```

## Locator format

FOSSA package locators are:

```
fetcher+packageName$revision
```

For example `npm+lodash$4.17.21`, `pip+requests$2.31.0`, `mvn+com.google.guava:guava$33.0.0-jre`.

You can pass the locator either as three flags (`--fetcher npm --name lodash --revision 4.17.21`) or as one string (`--locator 'npm+lodash$4.17.21'`).

## Modes

| Mode | Behavior |
|---|---|
| `validate-then-add` (default) | Look up the package first; only add it if FOSSA resolves it. Use `--force` to add anyway. |
| `add-and-report` | Skip the pre-check and POST the add directly, reporting FOSSA's result. Use this for packages FOSSA has not indexed yet, since the add itself is the resolution step. |
| `validate-only` | Just run the lookup and report. Never adds. |

Exit codes: `0` success (resolved / added), `1` add failed, `2` not resolved (and not added).

## Usage

```sh
export FOSSA_API_KEY=<your token>

# validate then add lodash to a project
python3 fossa_add_dependency.py \
  --project 'custom+12948/test-vendetta$<revision>' \
  --fetcher npm --name lodash --revision 4.17.21

# just check whether FOSSA knows a package
python3 fossa_add_dependency.py --validate-only \
  --fetcher pip --name requests --revision 2.31.0

# add without pre-checking (let the add resolve it)
python3 fossa_add_dependency.py --mode add-and-report \
  --project 'custom+12948/test-vendetta$<revision>' \
  --fetcher gem --name rails --revision 7.1.0

# replace an existing unresolved dependency instead of adding new
python3 fossa_add_dependency.py \
  --project 'custom+12948/test-vendetta$<revision>' \
  --locator 'npm+left-pad$1.3.0' \
  --replace 'npm+left-pad$0.0.0'
```

> **Note on `--project`:** the parent must be a **revision** locator (it includes `$revision`), not just a project locator. The Dependencies tab always shows a specific revision, and the add attaches to that revision. A bare `custom+12948/test-vendetta` will not work; append the revision you are targeting.

## Flags

| Flag | Description |
|---|---|
| `--project`, `--parent` | Parent project **revision** locator to add the dependency to. Required unless `--validate-only`. |
| `--fetcher` | Package manager / ecosystem (see table below). |
| `--name` | Package name. |
| `--revision` | Package version/revision. |
| `--locator` | Full `fetcher+name$revision` string; overrides the three flags above. |
| `--distro` | Linux distro (for `apk` / `deb` / `rpm-generic`). |
| `--distro-release` | Linux distro release, e.g. `23.10`. |
| `--arch` | CPU architecture (for Linux fetchers). |
| `--mode` | `validate-then-add` (default), `add-and-report`, or `validate-only`. |
| `--validate-only` | Shortcut for `--mode validate-only`. |
| `--force` | In `validate-then-add`, add even if validation says not resolved. |
| `--replace LOCATOR` | Replace this existing (unresolved) dependency instead of adding a new one. |
| `--endpoint` | FOSSA base URL. Defaults to `https://app.fossa.com` or `$FOSSA_ENDPOINT`. |

## Fetcher options

These are the registry / ecosystem fetchers accepted for a registry-locator dependency (the "Search across the web" tab). Values come straight from the app's fetcher map.

| Fetcher value | Ecosystem |
|---|---|
| `apk` | APK (Alpine) |
| `bower` | Bower |
| `cargo` | Cargo (Rust) |
| `cart` | Carthage |
| `comp` | Composer (PHP) |
| `conda` | Conda |
| `cpan` | CPAN (Perl) |
| `cran` | CRAN (R) |
| `deb` | Debian |
| `gem` | RubyGems |
| `git` | Git |
| `go` | Go modules |
| `hackage` | Hackage (Haskell) |
| `hex` | Hex (Erlang/Elixir) |
| `mvn` | Maven (Java) |
| `npm` | npm (JavaScript) |
| `nuget` | NuGet (.NET) |
| `pip` | pip (Python) |
| `pod` | CocoaPods |
| `pub` | Pub (Dart) |
| `rpm-generic` | RPM |
| `swift` | Swift |

### Linux fetchers

`apk`, `deb`, and `rpm-generic` also need `--distro`, `--distro-release`, and `--arch`.

| Fetcher | Distros (`--distro`) |
|---|---|
| `apk` | `alpine` |
| `deb` | `ubuntu`, `debian` |
| `rpm-generic` | `redhat`, `centos`, `oraclelinux`, `rhel`, `fedora`, `sles` |

Common `--arch` values: `all`, `noarch`, `amd64`, `arm64`, `x86_64`, `aarch64`.

Example:

```sh
python3 fossa_add_dependency.py \
  --project 'custom+12948/test-vendetta$<revision>' \
  --fetcher deb --name curl --revision 7.88.1-10 \
  --distro ubuntu --distro-release 23.10 --arch amd64
```

### FOSSA-specific fetchers (not for this tool's locator lookup)

The UI's other Add Dependency tabs use FOSSA-internal fetchers that do **not** resolve against a public registry, so they are outside this tool's registry-locator flow: `archive`, `url`, `url-private`, `user` (user-defined with explicit licenses), `path`, `sbom`, `csbinary`, `binary`, `custom`. If you need `user`-defined (license-only) or `archive`/`url` dependencies, use the web modal or ask for those modes to be added here.

## Output

- Progress goes to **stderr** (`validating ...`, `RESOLVED`, `ADDED`, etc.).
- A JSON result summary goes to **stdout**, so you can pipe it into `jq`:

```sh
python3 fossa_add_dependency.py --validate-only --locator 'npm+lodash$4.17.21' \
  | jq '.validate.response.dependency.locator'
```
