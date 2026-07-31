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


def _entries(navigation):
    """Yield every (entry, where) across groups and hidden, for validation."""
    for group in navigation.get("groups", []):
        for entry in group.get("modules", []):
            yield entry, "group"
    for entry in navigation.get("hidden", []):
        yield entry, "hidden"


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

    # Per-group shape.
    for i, group in enumerate(groups):
        if not isinstance(group, dict):
            errors.append(f"navigation.json: group #{i} must be an object")
            continue
        if not group.get("title"):
            errors.append(f"navigation.json: group #{i} is missing a non-empty 'title'")
        if not isinstance(group.get("modules", []), list):
            errors.append(f"navigation.json: group '{group.get('title')}' "
                          "'modules' must be a list")

    # Per-entry shape, duplicate paths, and source linkage.
    source_ids = {s.get("id") for s in sources_config.get("sources", [])}
    referenced_sources = set()
    seen_paths = set()
    for entry, where in _entries(navigation):
        if not isinstance(entry, dict):
            errors.append(f"navigation.json: a {where} entry must be an object")
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
            if path in seen_paths:
                errors.append(
                    f"navigation.json: path '{path}' appears more than once"
                )
            seen_paths.add(path)

    # Completeness: every source must be represented (placed or hidden).
    for sid in sorted(s for s in source_ids if s):
        if sid not in referenced_sources:
            errors.append(
                f"navigation.json: source '{sid}' from sources.json is not "
                "represented (place it in a group or list it under 'hidden')"
            )

    return errors


def _group_paths(navigation):
    """Paths that must be rendered (and therefore must exist in the index)."""
    return {
        entry["path"]
        for group in navigation.get("groups", [])
        for entry in group.get("modules", [])
    }


def _manifest_paths(navigation):
    """All paths the manifest accounts for (grouped + hidden)."""
    return {entry["path"] for entry, _ in _entries(navigation)}


def _curate_children(children, navigation):
    """Return a rewritten children list per the manifest.

    Raises NavigationError on dangling grouped paths, on index modules the
    manifest neither groups nor hides, or on unexpected pathless nodes.
    """
    # Drop any existing group markers so re-running is idempotent, then index
    # the remaining nodes by path.
    real_nodes = [c for c in children if c.get("type") != "groupMarker"]
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
        new_children.append({"type": "groupMarker", "title": group["title"]})
        for entry in group.get("modules", []):
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
        ``modules`` order;
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
            ident = page_by_path.get(entry["path"].lower())
            if ident is not None:
                group_idents.append(ident)
        if group_idents:
            new_sections.append({
                "title": group["title"],
                "identifiers": group_idents,
                "anchor": _section_anchor(group["title"]),
            })
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
