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

Serve from `.build-output` (the parent), not `.build-output/latest`: the build
bakes a `/latest/` hosting base path into every asset URL, so the `/latest/`
prefix must map to the `latest/` directory. This example avoids port 8000,
which can be commonly used by other apps or examples. 

> Note: Ensure that you have a version of Swift available that will build the 
> content that you point at in sources.json. For latest development tree builds,
> that typically requires either the latest Xcode beta or a Swift nightly development
> build to support Swift, SwiftPM, swift-testing, and other core projects.

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

Every build writes a `build-manifest.json` into the output directory, alongside the
merged archive. It lets us check the state of a published site - how recent it is,
and what specific content it sourced from.

- `https://docs.swift.org/main/build-manifest.json` — tracks the `main` branch build
- `https://docs.swift.org/latest/build-manifest.json` — tracks the current release
   build (such as `release/6.4.x`)

### Schema

The rough schema for this file:

```jsonc
{
  "version": { "slug": "main" },            // from sources.json's top-level "version"
  "build_time": "2026-08-25T07:27:56Z",     // UTC, ISO 8601 — when build_docs.py ran
  "sources": [
    {
      "id": "swift-book",                   // matches an "id" in sources.json
      "type": "git",                        // "git" | "archive" | "local"
      "ref": "main",                        // configured branch/tag (git only)
      "commit": "3c9dcf6530bd3088cff..."    // resolved commit SHA (git only)
    },
    {
      "id": "swift-stdlib",
      "type": "archive",
      "ref": "",
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

**Known gap:** `type: "local"` entries (this repo's own packages) always report
`ref`/`commit` as `"unknown"` in the published manifest.

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
