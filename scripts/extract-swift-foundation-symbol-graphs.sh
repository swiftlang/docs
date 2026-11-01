#!/bin/bash
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
#
# Builds swift-foundation and extracts symbol graphs for FoundationEssentials
# and FoundationInternationalization into .build/symbols/<Module>.
#
# Run this from the root of a swift-foundation checkout. build_docs.py does
# that for you: sources.json's swift-foundation-essentials entry names this
# script as its `preflight`, and the swift-foundation-internationalization
# entry reuses that checkout with `reuse_checkout`.
#
# `swift package generate-documentation` currently crashes on swift-foundation,
# so it can't use the plugin path every other package in sources.json uses. Instead, `docc_catalog` + `additional_symbol_graph_dir`
# make build_docs.py run `docc convert` directly against the symbol graphs
# this script produces, and fold the result into the combined archive like
# any other source. Drop this script once the plugin works on swift-foundation.

set -e

# `swift-symbolgraph-extract` is on PATH directly on Linux, but only
# reachable via `xcrun` on macOS (mirrors build_docs.py's discover_tools()).
# On macOS it also needs an explicit -sdk — unlike `swift build`, it doesn't
# auto-infer one, and fails with "missing required modules: 'Swift', ..." /
# "did you forget to set an SDK" without it. Linux doesn't need neither xcrun or `-sdk`.
#
# The vendored ICU headers also need a platform-specific -I:
# macOS's toolchain has no bundled copy of _FoundationICU, so
# without also pointing -I directly at .../icuSources/include/_foundation_unicode
# (where its module.modulemap actually lives), Clang can't find the module at
# all ("missing required module '_FoundationICU'").
# Linux's toolchain image *does* ship its own copy at
# /usr/lib/swift/_foundation_unicode/, found automatically regardless of our
# -I flags; adding that same extra -I on Linux makes ICU's own vendored
# _foundation_unicode/uchar.h reachable via the bare name `<uchar.h>`.
# This shadows the real system C11 header _foundation_unicode/ptypes.h expects
# for char16_t, producing "unknown type name 'U_CAPI'".
if command -v xcrun >/dev/null 2>&1 && xcrun --find swift-symbolgraph-extract >/dev/null 2>&1; then
    SYMBOLGRAPH_EXTRACT="xcrun swift-symbolgraph-extract"
    SDK_FLAGS="-sdk $(xcrun --sdk macosx --show-sdk-path)"
    ICU_EXTRA_INCLUDE_FLAGS="-I .build/checkouts/swift-foundation-icu/icuSources/include/_foundation_unicode"
else
    SYMBOLGRAPH_EXTRACT="swift-symbolgraph-extract"
    SDK_FLAGS=""
    ICU_EXTRA_INCLUDE_FLAGS=""
fi

echo "Building swift-foundation..."
# `swift build` is currently known to exit non-zero on Linux nightly
# toolchains, failing at/near the very last build step with a bare
# "error: Build failed" and no underlying compiler diagnostic anywhere in the
# log — every other line is a warning. Despite that, both
# FoundationEssentials and FoundationInternationalization still get their
# .swiftmodule produced successfully, so we don't let this abort the script —
# the symbol-graph extraction steps below are the real verification that the
# two modules we need built correctly.
swift build || echo "swift build reported a non-zero exit; continuing to see whether the modules we need still built."

echo "Setting up .build/symbols directories..."
rm -rf .build/symbols
mkdir -p .build/symbols/FoundationEssentials .build/symbols/FoundationInternationalization

echo "Extracting FoundationEssentials symbol graph..."
# shellcheck disable=SC2086 # the flag variables are intentionally word-split
$SYMBOLGRAPH_EXTRACT -pretty-print -module-name FoundationEssentials \
    -output-dir .build/symbols/FoundationEssentials \
    -I .build/debug \
    -I Sources/_FoundationCShims/include \
    $SDK_FLAGS \
    -emit-extension-block-symbols

# See the platform-specific ICU_EXTRA_INCLUDE_FLAGS comment above for why
# that flag is conditional rather than always present or always absent.
echo "Extracting FoundationInternationalization symbol graph..."
# shellcheck disable=SC2086 # the flag variables are intentionally word-split
$SYMBOLGRAPH_EXTRACT -pretty-print -module-name FoundationInternationalization \
    -output-dir .build/symbols/FoundationInternationalization \
    -I .build/debug \
    -I Sources/_FoundationCShims/include \
    -I .build/checkouts/swift-foundation-icu/icuSources/include \
    $ICU_EXTRA_INCLUDE_FLAGS \
    $SDK_FLAGS \
    -emit-extension-block-symbols

echo "Done. Symbol graphs ready under $(pwd)/.build/symbols/"
