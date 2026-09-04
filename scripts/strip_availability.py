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
strip-availability.py — Remove platform availability data from a DocC archive.

Walks every JSON file under <archive>/data/ and deletes or modifies any "platforms"
key it finds. In a Swift.doccarchive these only appear in two places:

  - metadata.platforms
    (the iOS/macOS/... badge table on each symbol page)
  - primaryContentSections[*].declarations[*].platforms
    (per-declaration variant tag, e.g. ["macOS"])

Both are populated by DocC at convert-time from the bundle's
CDAppleDefaultAvailability Info.plist key plus any compiler-provided
@available data. Deleting them yields a platform-neutral doc set.

Usage:
    ./strip-availability.py path/to/Swift.doccarchive
"""

import json
import os
import sys
import tempfile

TARGET_KEY = "platforms"
LINUX_PLATFORM_NAME = "Linux"


def strip(node):
    """Recursively delete every key named TARGET_KEY. Returns count removed."""
    removed = 0
    if isinstance(node, dict):
        if TARGET_KEY in node:
            del node[TARGET_KEY]
            removed += 1
        for v in node.values():
            removed += strip(v)
    elif isinstance(node, list):
        for v in node:
            removed += strip(v)
    return removed


def _is_linux_entry(entry):
    if isinstance(entry, str):
        return entry == LINUX_PLATFORM_NAME
    if isinstance(entry, dict):
        return entry.get("name") == LINUX_PLATFORM_NAME
    return False


def strip_linux(node):
    """Recursively remove only the Linux entry from every TARGET_KEY list.

    Unlike strip(), this leaves "platforms" and its other entries (Swift,
    Xcode) intact -- it only removes the Linux entry DocC synthesizes from
    the build host's target triple.
    """
    removed = 0
    if isinstance(node, dict):
        if TARGET_KEY in node and isinstance(node[TARGET_KEY], list):
            before = node[TARGET_KEY]
            after = [entry for entry in before if not _is_linux_entry(entry)]
            removed += len(before) - len(after)
            if len(after) != len(before):
                if after:
                    node[TARGET_KEY] = after
                else:
                    del node[TARGET_KEY]
        for v in node.values():
            removed += strip_linux(v)
    elif isinstance(node, list):
        for v in node:
            removed += strip_linux(v)
    return removed


def process_file(path, strip_fn=strip):
    with open(path, "rb") as f:
        data = json.load(f)

    removed = strip_fn(data)
    if removed == 0:
        return 0

    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".strip-", dir=dir_)
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
    return removed


def main():
    if len(sys.argv) != 2:
        sys.stderr.write(f"usage: {sys.argv[0]} <path-to-doccarchive>\n")
        sys.exit(2)

    archive = os.path.abspath(sys.argv[1])
    try:
        files_scanned, files_modified, keys_removed = strip_archive(archive)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        sys.exit(1)

    print(
        f"scanned {files_scanned} files; "
        f"modified {files_modified}; "
        f"removed {keys_removed} 'platforms' keys"
    )


def strip_archive(archive_path, strip_fn=strip):
    """Strip 'platforms' data from JSON files under <archive>/data/.

    By default deletes every 'platforms' key outright (strip_fn=strip).

    Returns (files_scanned, files_modified, keys_removed).
    Raises ValueError if archive_path doesn't look like a .doccarchive
    (i.e. has no data/ subdirectory).
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
    keys_removed = 0

    for root, _, names in os.walk(data_dir):
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            files_scanned += 1
            try:
                removed = process_file(path, strip_fn)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"skip (invalid JSON): {path}: {e}\n")
                continue
            if removed:
                files_modified += 1
                keys_removed += removed

    return files_scanned, files_modified, keys_removed


def strip_linux_availability(archive_path):
    """Remove only the Linux entry from every 'platforms' list in an archive.

    Thin wrapper around strip_archive() using strip_linux() -- see its
    docstring for why this is narrower than the default full wipe.
    """
    return strip_archive(archive_path, strip_fn=strip_linux)


if __name__ == "__main__":
    main()