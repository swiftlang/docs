#!/usr/bin/env python3
##===----------------------------------------------------------------------===##
##
## This source file is part of the Swift.org open source project
##
## Copyright (c) 2026 Apple Inc. and the Swift.org project authors
## Licensed under Apache License v2.0
##
## See LICENSE.txt for license information
## See CONTRIBUTORS.txt for the list of Swift.org project authors
##
## SPDX-License-Identifier: Apache-2.0
##
##===----------------------------------------------------------------------===##
"""Curate the combined DocC archive's navigator sidebar.

After ``docc merge`` produces the combined archive, its left-hand navigator is
driven by ``<archive>/index/index.json`` →
``interfaceLanguages.<lang>[0].children[]`` — a flat list of one node per merged
module. This module rewrites that list per ``navigation.json``: hiding modules,
grouping the rest under ``groupMarker`` labels, and ordering them explicitly.

See ``hacking-index-json.md`` for the verified mechanics.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlparse


class NavigationError(Exception):
    """Raised when curation cannot be applied (dangling/unlisted modules, etc.)."""


# `RenderIndex.Node.type` enum, from `hacking-index-json.md` (mirrors
# `RenderIndex.spec.json`), minus `groupMarker`: that type forces
# docc-render's `NavigatorCardItem.vue` to null out the node's `path`
# (`:url="isGroupMarker ? null : (item.path || '')"`), which would silently
# turn an external-link entry into a dead, non-clickable label.
VALID_EXTERNAL_LINK_TYPES = frozenset({
    "article", "associatedtype", "buildSetting", "case", "collection", "class",
    "container", "dictionarySymbol", "enum", "extension", "func", "httpRequest",
    "init", "languageGroup", "learn", "macro", "method", "module", "op",
    "overview", "project", "property", "propertyListKey",
    "propertyListKeyReference", "protocol", "resources", "root", "sampleCode",
    "section", "struct", "subscript", "symbol", "typealias", "union", "var",
})


def _entries(navigation):
    """Yield every (entry, where) across groups and hidden, for validation."""
    for group in navigation.get("groups", []):
        for entry in group.get("modules", []):
            yield entry, "group"
    for entry in navigation.get("hidden", []):
        yield entry, "hidden"


def _is_external_entry(entry):
    """An external-link entry has a `url` instead of a `source`/`path`.

    It has no backing module in the merged index — the sidebar node is
    synthesized directly from the manifest entry.
    """
    return "url" in entry


def _is_synthesized_external_node(node):
    """A previously-synthesized external-link node from an earlier curation
    pass, so re-curating an already-curated archive doesn't mistake it for
    a real merged module. Identified by the same `external` marker
    `_curate_children` stamps on it when synthesizing it — real `docc
    merge` output never sets this for nodes produced by this
    single-archive merge pipeline.
    """
    return node.get("external") is True


def _is_hybrid_entry(entry):
    """True if an external-link entry (has `url`) also carries `source`/
    `path` — invalid, since an entry can't be both a module pointer and an
    external link.
    """
    return "source" in entry or "path" in entry


def _entry_label(entry):
    """Human-readable name for an entry in error messages: its `title` if
    it's a non-empty string, else its raw `url`."""
    title = entry.get("title")
    return title if isinstance(title, str) and title else entry.get("url")


def _external_entry_shape_errors(entry, where):
    """Structural validation of an external-link entry, independent of
    sources.json.

    Shared by ``validate_navigation()`` and, defensively, by curation
    itself: ``curate_navigator()``/``dry_run()`` take no sources.json and
    can't call ``validate_navigation()`` on their own, so they call this
    directly before trusting an entry's `title`/`url`/`type`.

    Returns ``(errors, is_hybrid)`` — callers branch on ``is_hybrid``
    without re-deriving it themselves.
    """
    if _is_hybrid_entry(entry):
        return [
            f"navigation.json: a {where} entry has both 'url' and "
            "'source'/'path' — an entry must be either a module "
            "pointer or an external link, not both"
        ], True

    errors = []
    title = entry.get("title")
    url = entry.get("url")
    label = _entry_label(entry)

    if not isinstance(title, str) or not title:
        errors.append(f"navigation.json: an external {where} entry is missing a non-empty 'title'")

    if not isinstance(url, str) or not url:
        errors.append(f"navigation.json: an external {where} entry is missing a non-empty 'url'")
    elif url != url.strip():
        errors.append(
            f"navigation.json: external entry '{label}' has a 'url' "
            f"with leading/trailing whitespace ({url!r})"
        )
    elif not url.startswith("https://"):
        errors.append(
            f"navigation.json: external entry '{label}' has a 'url' "
            f"that is not https:// ({url!r})"
        )
    elif not urlparse(url).netloc:
        errors.append(
            f"navigation.json: external entry '{label}' has a 'url' "
            f"with no host ({url!r})"
        )

    entry_type = entry.get("type")
    if entry_type is not None and (
        not isinstance(entry_type, str) or entry_type not in VALID_EXTERNAL_LINK_TYPES
    ):
        errors.append(
            f"navigation.json: external entry '{label}' has an "
            f"invalid 'type' ({entry_type!r})"
        )

    return errors, False


def validate_navigation(navigation, sources_config):
    """Validate navigation.json against itself and sources.json (offline).

    Returns a list of human-readable error strings; empty means valid.
    """
    errors = []

    if "version" not in navigation:
        errors.append("navigation.json: missing required 'version'")

    groups = navigation.get("groups", [])
    if not isinstance(groups, list):
        errors.append("navigation.json: 'groups' must be a list")
        groups = []
    hidden = navigation.get("hidden", [])
    if not isinstance(hidden, list):
        errors.append("navigation.json: 'hidden' must be a list")

    # Per-group shape. `title` is optional: omit it (or set it to null) for a
    # headerless group, whose modules render with no groupMarker in the
    # sidebar and no titled section on the landing page. When present it
    # must be a non-empty string.
    for i, group in enumerate(groups):
        if not isinstance(group, dict):
            errors.append(f"navigation.json: group #{i} must be an object")
            continue
        title = group.get("title")
        if title is not None and not (isinstance(title, str) and title):
            errors.append(f"navigation.json: group #{i} has an invalid 'title' "
                          "(must be a non-empty string, or omitted/null for no header)")
        if not isinstance(group.get("modules", []), list):
            errors.append(f"navigation.json: group #{i} 'modules' must be a list")

    # Per-entry shape, duplicate paths/urls, and source linkage.
    source_ids = {s.get("id") for s in sources_config.get("sources", [])}
    referenced_sources = set()
    seen_identifiers = set()
    for entry, where in _entries(navigation):
        if not isinstance(entry, dict):
            errors.append(f"navigation.json: a {where} entry must be an object")
            continue

        if _is_external_entry(entry):
            shape_errors, is_hybrid = _external_entry_shape_errors(entry, where)
            errors.extend(shape_errors)
            if is_hybrid:
                continue

            url = entry.get("url")

            if where == "hidden":
                errors.append(
                    f"navigation.json: external entry '{_entry_label(entry)}' is "
                    "not valid under 'hidden' (there is no module to hide)"
                )
                continue

            if isinstance(url, str) and url:
                if ("url", url) in seen_identifiers:
                    errors.append(
                        f"navigation.json: url '{url}' appears more than once"
                    )
                seen_identifiers.add(("url", url))
            continue

        src = entry.get("source")
        path = entry.get("path")
        if not src:
            errors.append(f"navigation.json: a {where} entry is missing 'source'")
        if not path:
            errors.append(f"navigation.json: a {where} entry is missing 'path'")
        if src:
            referenced_sources.add(src)
            if src not in source_ids:
                errors.append(
                    f"navigation.json: entry references source '{src}' "
                    "not present in sources.json"
                )
        if path:
            if ("path", path) in seen_identifiers:
                errors.append(
                    f"navigation.json: path '{path}' appears more than once"
                )
            seen_identifiers.add(("path", path))

    # Completeness: every source must be represented (placed or hidden).
    for sid in sorted(s for s in source_ids if s):
        if sid not in referenced_sources:
            errors.append(
                f"navigation.json: source '{sid}' from sources.json is not "
                "represented (place it in a group or list it under 'hidden')"
            )

    return errors


def _group_paths(navigation):
    """Paths that must be rendered (and therefore must exist in the index).

    Excludes external-link entries — they have no backing index module.
    """
    return {
        entry["path"]
        for group in navigation.get("groups", [])
        for entry in group.get("modules", [])
        if not _is_external_entry(entry)
    }


def _manifest_paths(navigation):
    """All paths the manifest accounts for (grouped + hidden).

    Excludes external-link entries — they have no backing index module.
    """
    return {
        entry["path"]
        for entry, _ in _entries(navigation)
        if not _is_external_entry(entry)
    }


def _curate_children(children, navigation):
    """Return a rewritten children list per the manifest.

    Raises NavigationError on dangling grouped paths, on index modules the
    manifest neither groups nor hides, or on unexpected pathless nodes.
    """
    # Drop any existing group markers and previously-synthesized external
    # links so re-running is idempotent, then index the remaining real
    # modules by path.
    real_nodes = [
        c for c in children
        if c.get("type") != "groupMarker" and not _is_synthesized_external_node(c)
    ]
    path_map = {}
    for node in real_nodes:
        path = node.get("path")
        if not path:
            raise NavigationError(
                f"navigator node without a path cannot be curated: {node!r}"
            )
        path_map[path] = node

    index_paths = set(path_map)

    # Grouped modules must exist; hidden ones may already be absent (e.g. on a
    # second curation pass), so they are not required to be present.
    dangling = _group_paths(navigation) - index_paths
    if dangling:
        raise NavigationError(
            "navigation.json groups modules absent from the merged index: "
            + ", ".join(sorted(dangling))
        )

    # Strict total coverage: every index module must be grouped or hidden.
    unlisted = index_paths - _manifest_paths(navigation)
    if unlisted:
        raise NavigationError(
            "modules present in the merged index are neither grouped nor hidden "
            "in navigation.json: " + ", ".join(sorted(unlisted))
        )

    new_children = []
    for group in navigation.get("groups", []):
        if group.get("title"):
            new_children.append({"type": "groupMarker", "title": group["title"]})
        for entry in group.get("modules", []):
            if _is_external_entry(entry):
                shape_errors, _ = _external_entry_shape_errors(entry, "group")
                if shape_errors:
                    raise NavigationError(
                        "navigation.json has an invalid external-link entry: "
                        + "; ".join(shape_errors)
                    )
                new_children.append({
                    "type": entry.get("type", "resources"),
                    "title": entry["title"],
                    "path": entry["url"],
                    "external": True,
                })
                continue
            node = path_map[entry["path"]]
            if entry.get("title"):
                node["title"] = entry["title"]
            new_children.append(node)
    # Hidden entries are simply not re-appended.
    return new_children


def _load_index(archive_path):
    """Load <archive_path>/index/index.json; raise NavigationError if absent."""
    index_path = Path(archive_path) / "index" / "index.json"
    if not index_path.is_file():
        raise NavigationError(f"index.json not found at {index_path}")
    return index_path, json.loads(index_path.read_text())


def _curate_doc(doc, navigation):
    """Curate every interface-language tree of a loaded index doc, in place."""
    for lang, roots in doc.get("interfaceLanguages", {}).items():
        if not roots:
            continue
        root = roots[0]
        children = root.get("children")
        if children is None:
            continue
        root["children"] = _curate_children(children, navigation)


def curate_navigator(archive_path, navigation):
    """Rewrite <archive_path>/index/index.json per the navigation manifest.

    Also prunes hidden modules from the synthesized landing page
    (data/documentation.json) so they disappear from the page body, not just
    the sidebar. Raises NavigationError / OSError / json.JSONDecodeError on
    failure.
    """
    index_path, doc = _load_index(archive_path)
    _curate_doc(doc, navigation)

    tmp = index_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, index_path)

    _curate_landing_page(archive_path, navigation)


def _identifier_path(identifier):
    """Path component of a topic reference, lowercased.

    ``doc://com.apple.Swift/documentation/Cxx`` → ``/documentation/cxx`` — the
    same normalized key the manifest uses, so a manifest entry matches its
    landing-page topic regardless of bundle host or original casing.
    """
    return urlparse(identifier).path.lower()


def _curate_landing_page(archive_path, navigation):
    """Rewrite the synthesized landing page (data/documentation.json).

    The merged archive's landing page ships with two flat sections — "Modules"
    and "Tutorials" — driven by docc-merge defaults. This rewrites them in
    place to mirror the curated sidebar:

      * one ``topicSection`` per ``navigation.groups`` entry, in declared order,
        titled by the group's ``title``, with identifiers in the group's
        ``modules`` order; a group with no ``title`` produces a section with
        no ``title``/``anchor`` key, so its modules appear with no header;
      * groups whose modules don't match any identifier on the page are
        dropped (e.g. a nav module the merge step never surfaced as a card);
      * each kept identifier's reference is forced to ``kind:"symbol"``,
        ``role:"collection"`` so all cards render with the same collection icon
        regardless of how upstream framed the source's root page.

    No-op when ``data/documentation.json`` is absent or has no topic sections.
    """
    page = Path(archive_path) / "data" / "documentation.json"
    if not page.is_file():
        return
    doc = json.loads(page.read_text())
    sections = doc.get("topicSections")
    if not isinstance(sections, list):
        return

    # Index every identifier on the page by its lowercased path component, so
    # nav-manifest paths (which match the navigator) line up with landing-page
    # references regardless of bundle host or original casing.
    page_by_path = {}
    for section in sections:
        for ident in section.get("identifiers", []):
            page_by_path.setdefault(_identifier_path(ident), ident)

    new_sections = []
    placed_idents = []
    for group in navigation.get("groups", []):
        group_idents = []
        for entry in group.get("modules", []):
            if _is_external_entry(entry):
                continue
            ident = page_by_path.get(entry["path"].lower())
            if ident is not None:
                group_idents.append(ident)
        if group_idents:
            title = group.get("title")
            section = {}
            if title:
                section["title"] = title
            section["identifiers"] = group_idents
            if title:
                section["anchor"] = _section_anchor(title)
            new_sections.append(section)
            placed_idents.extend(group_idents)
    doc["topicSections"] = new_sections

    refs = doc.get("references")
    if isinstance(refs, dict):
        for ident in placed_idents:
            ref = refs.get(ident)
            if isinstance(ref, dict):
                ref["kind"] = "symbol"
                ref["role"] = "collection"

    tmp = page.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, page)


def _section_anchor(title):
    """Slug DocC uses for a topicSection's anchor — title with spaces dashed.

    Verified against existing converted pages: ``"Creating a Package"`` →
    ``"Creating-a-Package"``. Case is preserved; only ASCII whitespace is
    replaced with a dash. Existing dashes in the title pass through unchanged.
    """
    return "-".join(title.split())


def dry_run(archive_path, navigation):
    """Compute the curated navigator WITHOUT modifying the archive.

    Returns a dict mapping each interface language to the list of nodes its
    sidebar would contain after curation. Raises the same errors as
    ``curate_navigator`` (dangling/unlisted modules, missing/malformed index)
    so it doubles as a coverage check while editing ``navigation.json``.
    """
    _, doc = _load_index(archive_path)
    _curate_doc(doc, navigation)
    return {
        lang: roots[0].get("children", [])
        for lang, roots in doc.get("interfaceLanguages", {}).items()
        if roots
    }
