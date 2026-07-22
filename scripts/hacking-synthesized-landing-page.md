# Hacking the Synthesized Landing Page Body — DocC Merge Post-Processing

Companion to `hacking-index-json.md` (sidebar/navigator). This file covers the
**page body** of the combined archive's synthesized landing page
(`/documentation/`), rendered from `data/documentation.json`, and specifically
how to inject prose (e.g. a "Swift version: X" paragraph) into it.

Verified against the
[`swiftlang/swift-docc`](https://github.com/swiftlang/swift-docc) source
(specifically
[`MergeAction+SynthesizedLandingPage.swift`](https://github.com/swiftlang/swift-docc/blob/main/Sources/DocCCommandLine/Action/Actions/Merge/MergeAction%2BSynthesizedLandingPage.swift)
and
[`Sources/SwiftDocC/Model/Rendering/`](https://github.com/swiftlang/swift-docc/tree/main/Sources/SwiftDocC/Model/Rendering)),
not just observed output — the mechanics below are read from the code that
produces the page, not guessed from a JSON sample.

## What `docc merge` builds

`MergeAction.makeSynthesizedLandingPage(...)` constructs a `RenderNode` with:

- `identifier`, `kind: .article`
- `metadata.title` = `--synthesized-landing-page-name`
- `metadata.roleHeading` = `--synthesized-landing-page-kind` (e.g. `"Project"`)
- `metadata.role` = `"collection"`
- `topicSectionsStyle` from `--synthesized-landing-page-topics-style`
- `topicSections` = one or two sections (`"Modules"` / `"Tutorials"`, or a
  single unnamed section) built from every root-level render reference found
  under the merged archives' `data/documentation/` and `data/tutorials/`
- `sections = []` — **the synthesized page starts with zero primary content
  sections.** There is no auto-generated abstract or body prose.

`renderNode.sections` is the in-memory name for what encodes to the JSON key
**`primaryContentSections`** (see
[`RenderNode.swift`](https://github.com/swiftlang/swift-docc/blob/main/Sources/SwiftDocC/Model/Rendering/RenderNode.swift)
/
[`RenderNode+Codable.swift`](https://github.com/swiftlang/swift-docc/blob/main/Sources/SwiftDocC/Model/Rendering/RenderNode/RenderNode%2BCodable.swift):
`primaryContentSectionsVariants`, encoded via `encodeVariantCollectionArrayIfNotEmpty`).
Because it starts empty, **the key is absent from the emitted JSON entirely**
(empty variant collections are omitted, not written as `[]`). Don't assume the
key exists — check for it before indexing into it.

## Where prose paragraphs live: `primaryContentSections`

A content section on any `RenderNode` (landing page included) has exactly two
JSON keys — confirmed from
[`ContentRenderSection.swift`](https://github.com/swiftlang/swift-docc/blob/main/Sources/SwiftDocC/Model/Rendering/Symbol/ContentRenderSection.swift):

```jsonc
{
  "kind": "content",
  "content": [ /* RenderBlockContent[] */ ]
}
```

`heading` is a constructor convenience on the Swift side only — passing one
inserts a `heading` block at index 0 of `content`; it is **not** a separate
JSON key.

A plain paragraph block (confirmed against real fixtures under
[`Tests/SwiftDocCTests/Rendering/Rendering Fixtures/tables.json`](<https://github.com/swiftlang/swift-docc/blob/main/Tests/SwiftDocCTests/Rendering/Rendering%20Fixtures/tables.json>)
and
[`Tests/SwiftDocCTests/Test Resources/link-button-render-node.json`](<https://github.com/swiftlang/swift-docc/blob/main/Tests/SwiftDocCTests/Test%20Resources/link-button-render-node.json>)):

```jsonc
{
  "type": "paragraph",
  "inlineContent": [
    { "type": "text", "text": "Swift version: 6.3" }
  ]
}
```

So the minimal addition to make the landing page show one paragraph of body
text is:

```jsonc
"primaryContentSections": [
  {
    "kind": "content",
    "content": [
      {
        "type": "paragraph",
        "inlineContent": [
          { "type": "text", "text": "Swift version: 6.3" }
        ]
      }
    ]
  }
]
```

This renders as normal running prose **above the Topics section** and below
the title/role-heading — it is body content, not the short one-line
`abstract` teaser (a separate, also-currently-absent top-level key).

## Where this lives in our pipeline

`scripts/curate_navigator.py` → `set_version_paragraph(archive_path, text)`:
reads `data/documentation.json`, unconditionally replaces
`doc["primaryContentSections"]` with a single content section containing one
paragraph, writes atomically. No-op if the file is missing. Because it
replaces the array wholesale rather than appending, calling it repeatedly
(e.g. re-running the build) is idempotent — it does not accumulate
paragraphs.

Called from `scripts/build_docs.py` → `_finalize_combined_archive()`,
**unconditionally** (not gated on `navigation.json` existing, unlike
`curate_navigator()`/topic-section curation), right after `merge_archives()`
succeeds and before the static-hosting transform:

```python
descriptive_name = (version or {}).get("descriptive-name")
if descriptive_name:
    set_version_paragraph(combined_output, f"Swift version: {descriptive_name}")
```

`version` is the full `sources.json` `"version"` object
(`{"slug": ..., "descriptive-name": ...}`); the text is built here in
`build_docs.py`, not inside `curate_navigator.py`, so `set_version_paragraph`
stays a generic "set this text as the landing page body" primitive.

### Why it must run before the static-hosting transform

`transform_static_hosting()` runs `docc process-archive
transform-for-static-hosting`, which produces a **fresh archive** at a temp
path and then replaces the original wholesale (`shutil.rmtree` +
`shutil.move`). Whatever is in `data/documentation.json` at the time that
command runs is what gets baked into the final per-route HTML; editing the
JSON afterward doesn't help because the static-hosting archive may not carry
the same `data/*.json` structure forward. Same ordering constraint as
`curate_navigator()`'s landing-page section rewriting — see
`hacking-index-json.md`.

## Extending this further

To add more structure (multiple paragraphs, a heading, a link, an aside) to
the landing page body, grow the `content` array inside the single content
section using the same `RenderBlockContent` shapes DocC itself emits —
check
[`Sources/SwiftDocC/Model/Rendering/Content/RenderBlockContent.swift`](https://github.com/swiftlang/swift-docc/blob/main/Sources/SwiftDocC/Model/Rendering/Content/RenderBlockContent.swift)
for the `type` enum and each case's JSON shape, and cross-reference a fixture
under
[`Tests/SwiftDocCTests/Rendering/Rendering Fixtures/`](<https://github.com/swiftlang/swift-docc/tree/main/Tests/SwiftDocCTests/Rendering/Rendering%20Fixtures>)
before trusting a shape. Useful block `type`s seen in fixtures: `paragraph`,
`heading`, `aside`, `codeListing`, `orderedList`/`unorderedList`, `table`,
`links` (card grid).

If a change needs a different *inline* content shape (e.g. a link, code
voice, or emphasis inside the paragraph), the encoding for `RenderInlineContent`
follows the same "read the Swift source, confirm against a fixture" approach —
don't guess key names from memory, DocC's render JSON schema evolves.

For a broader, HTML-rendered walkthrough of the RenderNode/RenderIndex JSON
structures (a faster first stop than grepping Swift source for an unfamiliar
shape), see [`heckj/DocCArchive`](https://github.com/heckj/DocCArchive)'s
[`docs/`](https://github.com/heckj/DocCArchive/tree/main/docs) directory,
published at <https://heckj.github.io/DocCArchive/>.

## Gotchas

- **Don't assume `primaryContentSections` exists.** It's omitted, not `[]`,
  when empty — `doc.get("primaryContentSections")` before indexing.
- **`abstract` is a different, separate key** (the short one-line teaser under
  the title) — also omitted when empty on the synthesized landing page. Don't
  conflate it with `primaryContentSections`; this doc's approach targets body
  prose, not the teaser line.
- **Ordering vs. navigator curation:** `set_version_paragraph()` and
  `curate_navigator()` both rewrite `data/documentation.json` but touch
  disjoint keys (`primaryContentSections` vs. `topicSections`/`references`),
  so call order between them doesn't matter — verified by both being simple
  read-modify-write-atomic passes over the same file.
- **Local toolchain mismatches are real.** A quick empirical `docc convert`/
  `docc merge` smoke test against a much newer local Xcode beta toolchain
  produced *no* `data/` directory at all (content baked directly into HTML
  instead) — don't trust ad hoc local repro against a toolchain newer than the
  one this repo pins; prefer reading the pinned `swift-docc` source directly.
