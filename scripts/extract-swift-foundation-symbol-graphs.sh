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
# This script is meant to be run from the root of a swift-foundation checkout,
# and replaces what we would normally do with `swift package generate-documentation`.
# As of Swift 6.4.x, the orchestrated path with the docc-plugin crashes.
# As a workaround, the build_docs.py added structure to sources.json to support creating
# them with a pre-flight script and then reading them in through a defined
# `additional_symbol_graph_dir`.

set -e

# Use the extractor from the same toolchain as the `swift build` below.
# The one on PATH matches `swift`. On macOS it is reachable only through `xcrun`.
if command -v swift-symbolgraph-extract >/dev/null 2>&1; then
    SYMBOLGRAPH_EXTRACT="swift-symbolgraph-extract"
elif command -v xcrun >/dev/null 2>&1 && xcrun --find swift-symbolgraph-extract >/dev/null 2>&1; then
    SYMBOLGRAPH_EXTRACT="xcrun swift-symbolgraph-extract"
else
    echo "error: swift-symbolgraph-extract not found" >&2
    exit 1
fi

# building swift-foundation on macOS needs two flags that Linux does not.
#
# - An explicit `-sdk`. Unlike `swift build`, the extractor does not infer
# one. Without it, the build fails, reporting: "did you forget to set an SDK".
#
# - An extra `-I` for the vendored ICU headers. The macOS toolchain doesn't
# bundle _FoundationICU. Clang cannot find the module unless `-I` points at
# .../icuSources/include/_foundation_unicode, for its module.modulemap.
#
# Swift on Linux ships its own copy in /usr/lib/swift/_foundation_unicode/. There, the
# extra `-I` makes ICU's vendored uchar.h shadow the system C11 `<uchar.h>`.
# That breaks char16_t and causes a failure: "unknown type name 'U_CAPI'".
if command -v xcrun >/dev/null 2>&1 && xcrun --sdk macosx --show-sdk-path >/dev/null 2>&1; then
    SDK_FLAGS="-sdk $(xcrun --sdk macosx --show-sdk-path)"
    ICU_EXTRA_INCLUDE_FLAGS="-I .build/checkouts/swift-foundation-icu/icuSources/include/_foundation_unicode"
else
    SDK_FLAGS=""
    ICU_EXTRA_INCLUDE_FLAGS=""
fi

echo "Building swift-foundation..."
# On Linux nightly toolchains (6.4.x), `swift build` exits non-zero at the last
# step with "error: Build failed" and no compiler diagnostic.
# Both modules still produce their .swiftmodule, so we can extract the symbolgraphs from that.
# The extraction steps below are the real check.
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

# The extractor also emits `<Module>@_StringProcessing` graphs, which adds
# re-exported symbols from `_stringprocessing`, so this drops them.
rm -f .build/symbols/*/*@_StringProcessing.symbols.json

echo "Done. Symbol graphs ready under $(pwd)/.build/symbols/"
