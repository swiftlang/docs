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
"""
suppress-eyebrows.py — Blank or override the DocC "eyebrow" label on
collection pages in a DocC archive.

Every module/framework landing page in a Swift.doccarchive — and the `docc
merge`-synthesized combined landing page — has `metadata.role == "collection"`
plus a `metadata.roleHeading` string that swift-docc-render shows as a small
label above the page title ("Framework", "Platforms", "Project", etc). Walks
every JSON file under <archive>/data/ and, for each render node with
role == "collection", rewrites roleHeading per an `eyebrows` config (the shape
stored under navigation.json's top-level "eyebrows" key):

  - `overrides[slug]` wins outright, where `slug` is the file's path relative
    to data/ with no extension and a leading "/" — e.g. "/documentation/swift"
    for a module page, or "/documentation" for the synthesized landing page.
  - Otherwise `suppress: true` blanks roleHeading to "".
  - Otherwise the page is left untouched.

This isn't baked into any prerendered HTML — swift-docc-render fetches this
JSON at runtime — but it must still run before `docc process-archive
transform-for-static-hosting`, which replaces the archive wholesale; see
hacking-synthesized-landing-page.md for why edits to data/*.json must precede
that step.

Usage:
    ./suppress_eyebrows.py path/to/archive.doccarchive [--suppress] \
        [--override /documentation/swift=Framework ...]
"""

import argparse
import json
import os
import sys
import tempfile


def _slug_for(data_dir, path):
    """The file's path relative to data/, minus '.json', with a leading '/'."""
    rel = os.path.relpath(path, data_dir)
    if rel.endswith(".json"):
        rel = rel[: -len(".json")]
    return "/" + rel.replace(os.sep, "/")


def process_file(path, slug, suppress, overrides):
    with open(path, "rb") as f:
        doc = json.load(f)

    metadata = doc.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("role") != "collection":
        return False
    if "roleHeading" not in metadata:
        return False

    if slug in overrides:
        new_value = overrides[slug]
    elif suppress:
        new_value = ""
    else:
        return False

    if metadata["roleHeading"] == new_value:
        return False
    metadata["roleHeading"] = new_value

    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".eyebrows-", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return True


def suppress_archive(archive_path, eyebrows_config=None):
    """Apply `eyebrows_config` (navigation.json's "eyebrows" object) to a
    merged .doccarchive.

    Returns (files_scanned, files_modified). A falsy/absent config (suppress
    not set, no overrides) is a true no-op — the archive isn't even checked
    for a data/ directory — since that's the default, off state and this
    runs unconditionally on every build. Raises ValueError if archive_path
    doesn't look like a .doccarchive (missing data/ directory) whenever
    there's actually something to apply.
    """
    eyebrows_config = eyebrows_config or {}
    suppress = bool(eyebrows_config.get("suppress", False))
    overrides = eyebrows_config.get("overrides") or {}

    if not suppress and not overrides:
        return 0, 0

    archive = os.fspath(archive_path)
    data_dir = os.path.join(archive, "data")
    if not os.path.isdir(data_dir):
        raise ValueError(
            f"{archive!r} does not look like a .doccarchive "
            f"(missing data/ directory)"
        )

    files_scanned = 0
    files_modified = 0

    for root, _, names in os.walk(data_dir):
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            files_scanned += 1
            slug = _slug_for(data_dir, path)
            try:
                modified = process_file(path, slug, suppress, overrides)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"skip (invalid JSON): {path}: {e}\n")
                continue
            if modified:
                files_modified += 1

    return files_scanned, files_modified


def main():
    parser = argparse.ArgumentParser(
        description="Blank or override the DocC eyebrow label on collection pages."
    )
    parser.add_argument("archive", help="Path to a .doccarchive")
    parser.add_argument(
        "--suppress", action="store_true",
        help="Blank roleHeading on every collection page (default: off)",
    )
    parser.add_argument(
        "--override", action="append", default=[], metavar="SLUG=TEXT",
        help="Force a specific page's roleHeading, e.g. "
             "/documentation/swift=Framework. Repeatable.",
    )
    args = parser.parse_args()

    overrides = {}
    for item in args.override:
        slug, _, text = item.partition("=")
        overrides[slug] = text

    try:
        scanned, modified = suppress_archive(
            args.archive, {"suppress": args.suppress, "overrides": overrides}
        )
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        sys.exit(1)

    print(f"scanned {scanned} files; modified {modified}")


if __name__ == "__main__":
    main()
