#!/usr/bin/env python3
"""
strip-availability.py — Remove platform availability data from a DocC archive.

Walks every JSON file under <archive>/data/ and deletes any "platforms" key it
finds. In a Swift.doccarchive these only appear in two places:

  - metadata.platforms                          (the iOS/macOS/... badge table
                                                 on each symbol page)
  - primaryContentSections[*].declarations[*].platforms
                                                (per-declaration variant tag,
                                                 e.g. ["macOS"])

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


def process_file(path):
    with open(path, "rb") as f:
        data = json.load(f)

    removed = strip(data)
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
    data_dir = os.path.join(archive, "data")
    if not os.path.isdir(data_dir):
        sys.stderr.write(
            f"error: {archive!r} does not look like a .doccarchive "
            f"(missing data/ directory)\n"
        )
        sys.exit(1)

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
                removed = process_file(path)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"skip (invalid JSON): {path}: {e}\n")
                continue
            if removed:
                files_modified += 1
                keys_removed += removed

    print(
        f"scanned {files_scanned} files; "
        f"modified {files_modified}; "
        f"removed {keys_removed} 'platforms' keys"
    )


if __name__ == "__main__":
    main()