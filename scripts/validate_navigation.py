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
"""Standalone checker for navigation.json — run while editing the manifest.

Validates navigation.json against sources.json (mismatches + completeness) and,
when a merged combined archive is available, dry-runs the curation to report
dangling/unlisted modules and preview the resulting sidebar — all without
modifying any build output.

    python3 scripts/validate_navigation.py                 # static checks only
    python3 scripts/validate_navigation.py --archive PATH  # also check coverage

See ``hacking-index-json.md`` for the underlying mechanics.
"""

import argparse
import json
import sys
from pathlib import Path

import curate_navigator

SCRIPT_DIR = Path(__file__).resolve().parent


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--navigation", default=str(SCRIPT_DIR / "navigation.json"),
        help="path to navigation.json (default: alongside this script)",
    )
    parser.add_argument(
        "--sources", default=str(SCRIPT_DIR / "sources.json"),
        help="path to sources.json (default: alongside this script)",
    )
    parser.add_argument(
        "--archive", default=None,
        help="merged .doccarchive to check coverage against and preview "
             "(default: auto-detect .build-output/<version> if present)",
    )
    return parser.parse_args(argv)


def _load_json(path):
    return json.loads(Path(path).read_text())


def _auto_archive(sources, sources_path):
    """Return the conventional combined archive path if it exists, else None.

    Resolved relative to sources.json (…/<dir>/../.build-output/<slug>), so
    pointing --sources at a throwaway file does not pick up the real build.
    """
    version = sources.get("version")
    if not isinstance(version, dict):
        return None
    slug = version.get("slug")
    if not slug:
        return None
    repo_root = Path(sources_path).resolve().parent.parent
    candidate = repo_root / ".build-output" / slug
    return candidate if (candidate / "index" / "index.json").is_file() else None


def main(argv=None):
    """Return 0 when navigation.json is valid (and covers the archive), else 1."""
    args = _parse_args(argv)

    try:
        navigation = _load_json(args.navigation)
        sources = _load_json(args.sources)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: could not read inputs: {e}")
        return 1

    # 1. Static checks: mismatches + completeness against sources.json.
    errors = curate_navigator.validate_navigation(navigation, sources)
    if errors:
        print("navigation.json is NOT valid:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"navigation.json is valid against {args.sources} (no mismatches, "
          "all sources represented).")

    # 2. Optional coverage check + preview against a merged archive.
    archive = Path(args.archive) if args.archive else _auto_archive(sources, args.sources)
    if archive is None:
        print("No merged archive given or found — skipped coverage check. "
              "Pass --archive to verify against a built combined archive.")
        return 0

    print(f"\nChecking coverage against {archive} ...")
    try:
        preview = curate_navigator.dry_run(archive, navigation)
    except curate_navigator.NavigationError as e:
        print(f"  COVERAGE ERROR: {e}")
        return 1
    except (OSError, json.JSONDecodeError) as e:
        print(f"  Error reading archive index: {e}")
        return 1

    for lang, children in preview.items():
        print(f"\nSidebar preview [{lang}]:")
        for node in children:
            kind = node.get("type", "?")
            marker = "  " if kind == "groupMarker" else "    - "
            print(f"{marker}{node.get('title')}")
    print("\nCoverage OK — every module is grouped or hidden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
