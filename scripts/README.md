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
open http://localhost:8123/latest/documentation/
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

## Building multiple branches (main + release branches)

`build_documentation.yml`'s `build-docs` job builds two variants of
the documentation - using a matrix keyed by branch - for `main` and the latest
release branch (such as `release/6.4.x.

There isn't a shared multi-version file or `--sources` override — each release branch
owns and maintains its own manifest.

### Adding a new release branch

When a new release branch gets established, we start with branch creation, update its
`sources.json`, then add or update the entry to the `matrix.include` list in
`build_documentation.yml` on `main`.
