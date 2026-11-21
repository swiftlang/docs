## Local validation

To build the combined documentation and view the result locally:

- from the docs repository root:
```bash
set -e
./scripts/build_docs.py
python3 -m http.server 8123 --directory .build-output
```

Then in another terminal:

```bash
open http://localhost:8123/main/documentation/
```

Serve from `.build-output` (the parent), not `.build-output/main`: the build
bakes a `/main/` hosting base path into every asset URL, so the `/main/`
prefix must map to the `main/` directory. The name comes from the `slug` in
`sources.json`. This example avoids port 8000, which can be commonly used by
other apps or examples.

To skip the build, download the output from a nightly or pull request build
and serve it with the same `http.server` command. See
[Viewing a built copy of the combined documentation](../CONTRIBUTING.md#viewing-a-built-copy-of-the-combined-documentation)
in `CONTRIBUTING.md`.

> Note: Ensure that you have a version of Swift available that will build the 
> content that you point at in sources.json. For latest development tree builds,
> that typically requires either the latest Xcode beta or a Swift nightly development
> build to support Swift, SwiftPM, swift-testing, and other core projects.

## Command-line options

| Option | Effect |
| --- | --- |
| `--output-dir DIR` | Where to write the combined archive and `build-manifest.json`. Defaults to `.build-output` in the repository root. |
| `--workspace DIR` | Where to clone repositories and stage downloads and intermediate archives. Defaults to `.workspace` in the repository root. |
| `--clean` | Delete the workspace and the `.build/` directory of every local Swift package before building. |
| `--only ID` | Build only that source and copy its archive to `--output-dir`. This skips the merge and overwrites `build-manifest.json` with a one-entry manifest. An unknown `ID` exits 1. |
| `--extra-hosting-prefix PREFIX` | Prepend a path segment to the hosting base path, as in `PREFIX/<slug>`. The output directory name does not change. |

The script exits 1 if any source or post-merge step fails.
Archive sources build first.
Any failure in an archive source stops the whole run.
A failure in any other source is recorded, and the remaining sources still build.

## Source fields

Every entry in `sources.json` needs a unique `id` and a `type`.
Unknown fields are errors, so a typo can't silently turn a feature off.

| Field | `local` | `git` | `archive` |
| --- | :---: | :---: | :---: |
| `path` | required | | |
| `repo`, `ref` | | required unless `reuse_checkout` | |
| `reuse_checkout` | | optional | |
| `add_docc_plugin` | | optional | |
| `targets` or `docc_catalog` (one of) | required | required | |
| `extra_flags`, `env`, `preflight` | optional | optional | |
| `additional_symbol_graph_dir` (with `docc_catalog`) | optional | optional | |
| `strip_linux_availability` | optional | optional | |
| `url`, `docc_archive_name` | | | required |
| `format` (`tar.gz` or `zip`), `strip_availability`, `version_label` | | | optional |

`validate_sources()` in `build_docs.py` is the source of truth for these rules.

## Building a source from generated symbol graphs

Most sources build with `swift package generate-documentation`: list the
package's `targets` and `build_docs.py` runs the DocC plugin.
Some packages can't use the plugin.
For those, `build_docs.py` can run `docc convert` directly, against symbol
graphs that the source produces itself.
Four `sources.json` fields make that work.

| Field | Applies to | Meaning |
| --- | --- | --- |
| `docc_catalog` | `git`, `local` | Path to a `.docc` catalog, relative to the checkout. Mutually exclusive with `targets`. |
| `preflight` | `git`, `local` | A shell command that runs in the checkout before the build. |
| `additional_symbol_graph_dir` | with `docc_catalog` | Directory of `.symbols.json` files, relative to the checkout. Passed to `docc convert --additional-symbol-graph-dir`. |
| `reuse_checkout` | `git` | The `id` of an earlier `git` source whose checkout this source shares. |

### `preflight`

`build_docs.py` runs the command with `bash -c`.
The working directory is the source's checkout.
The environment includes the source's `env` entries and `DOCS_ROOT`, the
absolute path of this repository's root.
Use `DOCS_ROOT` to run a helper script that lives in this repository, because
the checkout belongs to another project:

```jsonc
"preflight": "bash \"$DOCS_ROOT/scripts/extract-swift-foundation-symbol-graphs.sh\""
```

A non-zero exit fails the source.

### `additional_symbol_graph_dir`

The directory must exist and hold at least one entry when the build reaches
`docc convert`.
If it doesn't, the source fails with an error that names the directory.
This usually means the `preflight` didn't produce the symbol graphs.
Validation rejects the field on `archive` sources and on sources that use
`targets`.

### `reuse_checkout`

Several catalogs can live in one repository.
Give the first source a `repo` and a `ref`, and have later sources name it:

```jsonc
{
  "id": "swift-foundation-essentials",
  "type": "git",
  "repo": "https://github.com/swiftlang/swift-foundation.git",
  "ref": "main",
  "docc_catalog": "Sources/FoundationEssentials/FoundationEssentials.docc",
  "preflight": "bash \"$DOCS_ROOT/scripts/extract-swift-foundation-symbol-graphs.sh\"",
  "additional_symbol_graph_dir": ".build/symbols/FoundationEssentials"
},
{
  "id": "swift-foundation-internationalization",
  "type": "git",
  "reuse_checkout": "swift-foundation-essentials",
  "docc_catalog": "Sources/FoundationInternationalization/FoundationInternationalization.docc",
  "additional_symbol_graph_dir": ".build/symbols/FoundationInternationalization"
}
```

The second source clones nothing and builds nothing twice.
It uses the first source's checkout, including the files its `preflight` made.
Its `build-manifest.json` entry records the first source's `ref` and the checkout's commit.
A reusing source can have its own `preflight` and `env`.
Both run again in the shared checkout.

Updating a cached clone discards everything the last build left in it.
That includes `.build/` and the output of any `preflight`.

Rules for `reuse_checkout`:

- The value must name an earlier `git` source that has its own `repo`.
  Local and git sources build in `sources.json` order. Archive sources build first.
- The reusing source can't set `repo` or `ref`.
- Chains aren't allowed. Point every reusing source at the original.
- Building a reusing source alone with `--only` fails unless the first source's
  checkout already exists in the workspace.
  Build the first source before it.

## Navigation manifest (combined sidebar curation)

`navigation.json` controls the left-hand navigator of the **combined** archive
(`docc merge` output): it groups modules under labelled sections, hides
internal modules, and orders them. Hidden modules are also pruned from the
synthesized landing page body (`data/documentation.json`), so they disappear
from the main page's module list as well as the sidebar. Each entry names a
`source` (a `sources.json` id) and the module `path` it applies to. Every module
in the merged index must be either placed in a group or listed under `hidden` —
`build_docs.py` validates and applies this automatically (the
`navigator-curation` build step), and fails the build on any mismatch or
uncovered module. See `hacking-index-json.md` for the underlying mechanics.

A group's `title` is optional — omit it (or set it to `null`) for a headerless
group whose modules render with no section label in either the sidebar or the
landing page.

A group's `modules` list may also contain **external-link entries** — plain
links to content outside the archive, with no backing `sources.json` id. Give
these a `title` and an `https://` `url` instead of `source`/`path`:

```jsonc
{ "title": "Swift and C++", "url": "https://www.swift.org/documentation/cxx-interop/" }
```

External entries render in the sidebar only — they get no card on the
synthesized landing page — and aren't valid under `hidden` (there's no module
to hide). An optional `type` overrides the sidebar icon; it defaults to
`"resources"`.

### Checking the manifest while editing

Run the standalone checker — it does **not** modify any build output:

```bash
# From the repo root. Validates navigation.json against sources.json
# (no unknown sources, every source represented) and, if a combined archive
# exists at .build-output/<version>, dry-runs curation and previews the sidebar.
python3 scripts/validate_navigation.py
```

It exits non-zero when the manifest is invalid or a built archive contains a
module the manifest neither groups nor hides — so it doubles as a coverage
check. Point it at a specific archive (e.g. one you just built elsewhere) with:

```bash
python3 scripts/validate_navigation.py --archive path/to/combined.doccarchive
```

`--navigation` and `--sources` can override the input files for experimentation.

## Inspecting what's published (build-manifest.json)

Every build that builds at least one source writes a `build-manifest.json` into
the output directory, next to the `<slug>/` directory that holds the merged
archive. It lets us check the state of a published site - how recent it is,
and what specific content it sourced from.

The manifest lists only the sources that built. It is written even if a
post-merge step failed, so compare `sources` arrays with care after a failed
build. A build with `--only` overwrites it with a one-entry manifest.

- `https://docs.swift.org/main/build-manifest.json` — tracks the `main` branch build
- `https://docs.swift.org/latest/build-manifest.json` — tracks the current release
   build (such as `release/6.4.x`)

### Schema

The rough schema for this file:

```jsonc
{
  "version": { "slug": "main" },            // from sources.json's top-level "version"
  "build_time": "2026-08-25T07:27:56Z",     // UTC, ISO 8601 — when the manifest was written
  "sources": [
    {
      "id": "swift-book",                   // matches an "id" in sources.json
      "type": "git",                        // "git" | "archive" | "local"
      "ref": "main",                        // configured branch/tag (git only; a reuse_checkout
                                            // source records the ref of the source it reuses)
      "commit": "3c9dcf6530bd3088cff..."    // resolved commit SHA (git only)
    },
    {
      "id": "swift-stdlib",
      "type": "archive",
      "ref": "",                            // the source's "version_label", if set
      "commit": "",
      "url": "https://download.swift.org/docs/main/swift_docc.zip"  // archive only
    },
    {
      "id": "swift-linux",                  // a "local" source — one of this repo's
      "type": "local",                      // own packages (linux, windows, libraries,
      "ref": "unknown",                     // apple-platforms, prior)
      "commit": "unknown"
    }
  ]
}
```

**Known gap:** `type: "local"` entries (this repo's own packages) report
`ref`/`commit` as `"unknown"` in the published manifest.
A local build in a git checkout reports this repository's branch and commit
instead. The script falls back to `"unknown"` when `git` fails in the package
directory. Why it fails in CI is unverified. A detached checkout would report
`HEAD` as the ref.

### Using it to check recency against git/GitHub

1. Fetch the manifest for the version you care about (`main` or `latest`).
2. For each `"type": "git"` source, compare its `commit` against the HEAD of `ref` on
   the corresponding GitHub repo (repo URLs are in `sources.json`):
   ```bash
   gh api repos/swiftlang/<repo>/commits/<ref> --jq .sha
   # or
   git ls-remote https://github.com/swiftlang/<repo>.git <ref>
   ```
   Equal SHAs mean that source's content is current as of `build_time`. If they
   differ, `git log <manifest-commit>..<remote-HEAD>` on that repo shows exactly
   what's missing from the published docs.
3. For `"type": "archive"` sources (e.g. `swift-stdlib`), there's no commit to diff —
   the `url` points at a branch-keyed snapshot on `download.swift.org`. Compare
   `build_time` against that URL's `Last-Modified` header instead.
4. For `"type": "local"` sources, compare `build_time` against this repo's own commit
   history for that path, e.g. `git log -1 --format=%cI -- linux`, since the manifest
   doesn't carry a usable commit for them.
5. To see how far a release branch has drifted from `main` (or confirm a fix landed on
   both), fetch both manifests and diff their `sources` arrays.
