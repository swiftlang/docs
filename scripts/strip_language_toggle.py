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
strip_language_toggle.py — Remove the interface-language variant data that
drives swift-docc-render's "Language: Swift" nav pill from a DocC archive.

DocC only attaches a top-level `variants` array (traits like
`{"interfaceLanguage": "swift"}`) to render nodes produced from a compiled
symbol graph — every page built via `swift package generate-documentation`,
whether or not the target has any real public symbols — and to prebuilt
archives fetched already containing one (e.g. the downloaded stdlib
archive). Pages converted straight from a bare `.docc` catalog via `docc
convert`, with no compiled target behind them, never get one.
swift-docc-render's `LanguageToggle` nav item keys off exactly this array
(`DocumentationTopic.vue`'s `swiftPath`/`objcPath` computed properties read
`variant.traits[].interfaceLanguage`), so the pill ends up showing on some
pages and not others — a build-path artifact, not a per-page choice.

Walks every JSON file under <archive>/data/ and deletes the render node's
own top-level "variants" key wherever present. Does NOT recurse: "variants"
is also a legitimate key elsewhere in the same document — e.g.
`references.<id>.variants` on an image/video asset reference, holding its
light/dark or resolution variants — and deleting those would break asset
rendering.

Usage:
    ./strip_language_toggle.py path/to/archive.doccarchive
"""

import json
import os
import sys
import tempfile

TARGET_KEY = "variants"


def process_file(path):
    with open(path, "rb") as f:
        data = json.load(f)

    if not isinstance(data, dict) or TARGET_KEY not in data:
        return False
    del data[TARGET_KEY]

    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".strip-language-", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return True


def strip_archive(archive_path):
    """Delete the top-level 'variants' key from every render node JSON file
    under <archive>/data/.

    Returns (files_scanned, files_modified). Raises ValueError if
    archive_path doesn't look like a .doccarchive (missing data/ directory).
    """
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
            try:
                modified = process_file(path)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"skip (invalid JSON): {path}: {e}\n")
                continue
            if modified:
                files_modified += 1

    return files_scanned, files_modified


def main():
    if len(sys.argv) != 2:
        sys.stderr.write(f"usage: {sys.argv[0]} <path-to-doccarchive>\n")
        sys.exit(2)

    archive = os.path.abspath(sys.argv[1])
    try:
        files_scanned, files_modified = strip_archive(archive)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        sys.exit(1)

    print(f"scanned {files_scanned} files; modified {files_modified}")


if __name__ == "__main__":
    main()
