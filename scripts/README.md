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
bakes a `/main/` hosting base path into every asset URL, so the `/main/` prefix
must map to the `main/` directory. This example avoids port 8000, which can be 
commonly used by other apps or examples. 

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
uncovered module. See `../hacking-index-json.md` for the underlying mechanics.

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

