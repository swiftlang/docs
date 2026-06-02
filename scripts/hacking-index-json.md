# Hacking `index/index.json` — DocC Navigator Post-Processing

Swift DocC (as of version 6.3) doesn't yet provide curation and control for ordering
in the left navigation bar (presented in the web page by `index/index.json`). 
These notes provide details for future overrides (hacks) to curate the left-hand
navigation sidebar of a **combined** DocC archive (the one produced by
`docc merge` in `scripts/build_docs.py`). This leverages the upstream documentation for
[`RenderIndex.spec.json`](https://github.com/swiftlang/swift-docc/blob/main/Sources/SwiftDocC/SwiftDocC.docc/Resources/RenderIndex.spec.json)
(June 2026).

## What this file is

`<archive>/index/index.json` is the **render index** consumed by the DocC
JavaScript front-end to build the navigator (the collapsible tree in the left
sidebar). It is a *separate, derived* artifact from the page content under
`data/` — the renderer reads `index/index.json` to draw the tree, and only
fetches `data/.../*.json` when you click a node. This separation is what makes
post-processing safe-ish: editing the index changes the **navigation** without
touching page content.

Other files in `index/` (`data.mdb`, `navigator.index`, `availability.index`)
are LMDB/binary forms used by other tooling; the JS navigator uses the JSON.
We do **not** need to regenerate them for the web front-end to reflect changes,
but be aware they will be stale if something else reads them.

## Top-level shape

```jsonc
{
  "schemaVersion": { "major": 0, "minor": 1, "patch": 2 },
  "references": { ... },                              // present in merged output; leave intact
  "includedArchiveIdentifiers": ["Inner", "Outer"],   // optional; bundle IDs merged in
  "interfaceLanguages": {
    "swift": [ <Node> ],          // <-- length-1 array: a single synthesized ROOT node
    "occ":   [ ... ]              // other languages, if present, are independent trees
  }
}
```

**All curation happens inside `.interfaceLanguages.swift[0].children[]`.**

`swift[0]` is the synthesized root node — `{ "type": "module", "title":
"Swift - main", "path": "/documentation", "children": [...] }` (the
`--synthesized-landing-page-name`). Its **`children`** array holds **one
`module` node per merged module**, in merge order. That array is the curation
surface. (In the `main` build it has **36 entries** — see below.)

## The `Node` object

From the spec — a Node's **only required field is `title`**. Recognized fields:

| Field          | Type    | Notes |
|----------------|---------|-------|
| `title`        | string  | Required. Display text. |
| `type`         | string  | Enum (below). Absent on the synthesized root in some cases. |
| `path`         | string  | Link target, e.g. `/documentation/inner`. Omit for `groupMarker`. |
| `children`     | array   | Recursive array of `Node`. Builds nesting. |
| `deprecated`   | boolean | default false |
| `external`     | boolean | default false |
| `beta`         | boolean | default false |
| `icon`         | string  | reference to an `ImageRenderReference` |

### `type` enum (full set)

```
article, associatedtype, buildSetting, case, collection, class, container,
dictionarySymbol, enum, extension, func, groupMarker, httpRequest, init,
languageGroup, learn, macro, method, module, op, overview, project, property,
propertyListKey, propertyListKeyReference, protocol, resources, root,
sampleCode, section, struct, subscript, symbol, typealias, union, var
```

Two types matter most for curation:
- **`module`** — a top-level documentation source (one per merged archive).
- **`groupMarker`** — a **label-only** node: it has `title` + `type`, **no
  `path`, no `children`**. The renderer draws it as a non-clickable section
  heading among its siblings.

## The three curation operations

### 1. Inject a label (`groupMarker`)

Add a `groupMarker` node anywhere in a `children` array. It labels the siblings
that follow it, up to the next `groupMarker`. Works at **any depth** — before
the first module, between modules, or inside a module's own children.

```jsonc
{ "type": "groupMarker", "title": "Language & Standard Library" }
```

### 2. Hide a module (filter)

**Delete the module's Node object from `children[]`.** There is no `hidden`
flag in the spec — removal from the array is how you hide. The page content
under `data/` and `documentation/` still exists (deep links keep working); it
just no longer appears in the navigator. If you want it gone entirely, that is
a separate content-deletion step, not an index edit.

### 3. Group modules together

There are **two** ways, with different UX:

- **(a) Flat labelled sections** — keep modules as siblings, insert
  `groupMarker`s to head each run. This is what the example archive does. The
  tree stays one level deep; markers are visual separators.

  ```jsonc
  "children": [
    { "type": "groupMarker", "title": "Core" },
    { "type": "module", "title": "Swift", "path": "/documentation/swift" },
    { "type": "module", "title": "Standard Library", "path": "..." },
    { "type": "groupMarker", "title": "Server" },
    { "type": "module", "title": "ServerGuides", "path": "..." }
  ]
  ```

- **(b) True nesting** — wrap a set of module Nodes inside a synthetic parent
  Node that has its own `title` + `children`. The navigator renders this as a
  collapsible group. The parent can be a plain container; give it a `path` only
  if you want it clickable (it would need real content to land on). Nesting
  modules under a pathless parent is **off the beaten path** for DocC and should
  be validated in the actual renderer before relying on it.

  ```jsonc
  "children": [
    {
      "title": "Server-Side Swift",
      "type": "groupMarker",   // or a container; verify rendering
      "children": [ <module>, <module> ]
    }
  ]
  ```

  Note: a `groupMarker` with `children` is *not* in the example; the safe,
  proven pattern is **(a) flat markers**. Prefer (a) unless collapsibility is a
  hard requirement and you've confirmed (b) renders.

## Where this slots into the combined build

In `scripts/build_docs.py`:

1. Each source in `scripts/sources.json` builds to its own `.doccarchive`.
2. `merge_archives()` runs `docc merge ... --synthesized-landing-page-name ...
   --synthesized-landing-page-topics-style list` → produces the combined
   archive with a fresh `index/index.json`.
3. `transform_static_hosting()` bakes the hosting base path.

**The post-processing hook belongs between steps 2 and 3** — i.e. in
`_finalize_combined_archive`, after `merge_archives()` returns and before
`transform_static_hosting()`. The merged `index.json` exists at that point, and
curating before the static-hosting transform means the transform consumes the
already-curated tree. A `curate_navigator(archive, config)` function would: load
`index/index.json`, walk `interfaceLanguages.<lang>[0].children`, then
drop/reorder/relabel module nodes per config, and write the file back (preserve
`schemaVersion`, `references`, `includedArchiveIdentifiers`, and top-level key
order; write atomically).

**Real output, verified.** A current `main` merge produces an `index/`
directory containing **only `index.json`** (~4.9 MB) — no `data.mdb` /
`navigator.index` / `availability.index`. Its `swift[0].children` has **36
module nodes**, many of which are exactly the stdlib-internal noise to hide:

```
Cxx, CxxStdlib, Distributed, OSLogTestHelper, Observation, RegexBuilder,
Runtime, RuntimeUnittest, StdlibUnittest, StdlibUnittestFoundationExtras,
Swift, SwiftOnoneSupport, SwiftPrivate, SwiftPrivateLibcExtras,
SwiftPrivateThreadExtras, SwiftReflectionTest, Synchronization,
_Builtin_float, _Differentiation, _RegexParser, _Volatile,
The Swift Programming Language (6.3), Migrating to Swift 6,
Swift compiler diagnostics, Swift Package Manager, PackageDescription,
PackagePlugin, Testing, DocC, Embedded Swift, ...
```

Note `Swift Package Manager` / `PackageDescription` / `PackagePlugin` are three
separate module nodes from the single `swiftpm` source — multi-target sources
fan out, so curation must address modules by their rendered title/path, not by
`sources.json` id.

### Matching modules to sources

Module Nodes are keyed by `title` and `path` (`/documentation/<module>`,
lowercased). `sources.json` entries are keyed by `id` and produce modules named
by their `docc_catalog`/`targets`. The mapping from a `sources.json` `id` to the
resulting module `path`/`title` is **not 1:1 or guaranteed** — a single source
with multiple `targets` (e.g. `swiftpm` →
`PackageManagerDocs`/`PackageDescription`/`PackagePlugin`) yields multiple
module nodes. Any curation config must therefore address modules by their
**rendered title/path**, and ideally be validated against the actual merged
index rather than assumed from `sources.json` alone.

## Second surface: the synthesized landing page

`index/index.json` only drives the **sidebar**. The body of the synthesized
landing page (`/documentation/`) is rendered from **`data/documentation.json`**,
whose `topicSections[]` list every module as a card via `identifiers` like
`doc://com.apple.Swift/documentation/Cxx`. Hiding a module from the sidebar does
**not** remove it here — you must prune these too, or hidden modules still show
as cards on the main page.

Match an identifier to a manifest path by its **path component, lowercased**:
`urlparse("doc://com.apple.Swift/documentation/Cxx").path.lower()` →
`/documentation/cxx`, which equals the manifest `path` regardless of bundle host
or original casing. Drop hidden identifiers from each section's `identifiers`,
and drop a section that ends up empty. Leave `references` intact (unlinked
entries are harmless). Our `curate_navigator()` does both surfaces in one pass.

## Gotchas

- **Idempotency / order:** merge order determines initial child order; reordering
  must be explicit in the curation step, not assumed from `sources.json` order.
- **Stale binary indexes — not a concern for our pipeline.** The real `docc
  merge` output's `index/` holds only `index.json`, so a JSON edit is the whole
  job. (The `~` example archive's `data.mdb` / `navigator.index` /
  `availability.index` are an artifact of how that example was packaged.) If a
  future toolchain *does* emit those alongside the merged `index.json`, delete
  them after curating rather than leave them stale — a missing binary index
  degrades to the JSON navigator, a stale one could resurrect hidden modules.
- **`includedArchiveIdentifiers`** is informational; leave it intact.
- **Schema drift:** the spec lives upstream and evolves. Re-check the `type`
  enum and required fields against the pinned swift-docc version when builds
  start using a newer toolchain.
- **Validate in the renderer:** the spec permits many shapes the DocC JS
  navigator may render imperfectly (esp. nested modules / `groupMarker` with
  children). Always preview the combined archive after curating.

## Quick manual recipe

```bash
# Pretty-print the curation surface
python3 -c "import json; d=json.load(open('index/index.json')); \
print(json.dumps(d['interfaceLanguages']['swift'][0]['children'], indent=2))"
```

Edit `children[]` (drop nodes to hide; insert `{type:groupMarker,title:...}`
to label/group), write back, then re-preview the archive.
