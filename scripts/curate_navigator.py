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


class NavigationError(Exception):
    """Raised when curation cannot be applied (dangling/unlisted modules, etc.)."""


def validate_navigation(navigation, sources_config):
    """Validate navigation.json against itself and sources.json (offline).

    Returns a list of human-readable error strings; empty means valid.
    """
    raise NotImplementedError


def curate_navigator(archive_path, navigation):
    """Rewrite <archive_path>/index/index.json per the navigation manifest.

    Raises NavigationError / OSError / json.JSONDecodeError on failure.
    """
    raise NotImplementedError
