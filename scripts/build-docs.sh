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
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCES_FILE="$SCRIPT_DIR/sources.json"

# NOTE: The order of the archives in sources.json matters for merging into a combined archive,
# as they're listed in the order they are merged, which is driven (in this script)
# by the ordering in the JSON. All sources are included in the combined archive.

# Ensure consistent, pretty-printed DocC JSON output
export DOCC_JSON_PRETTYPRINT=YES

# Common DocC build flags applied to all documentation builds
DOCC_BUILD_FLAGS=(
    --experimental-enable-custom-templates
    --enable-mentioned-in
    --enable-experimental-external-link-support
)

# Common template files to copy into each .docc catalog before building
COMMON_DIR="$ROOT_DIR/common"
TEMPLATE_FILES=(header.html footer.html)

# Defaults
OUTPUT_DIR="$ROOT_DIR/.build-output"
WORKSPACE="$ROOT_DIR/.workspace"
CLEAN=false
ONLY=""
BRANCH=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Build and export DocC documentation archives from multiple sources.

Options:
  --output-dir <path>        Where to export built archives (default: .build-output/)
  --workspace <path>         Where to clone external repos (default: .workspace/)
  --clean                    Remove workspace and re-clone everything
  --only <id>                Build only a specific source by id
  --branch <branch>           Use this branch for git sources that have it (e.g. release/6.3).
                             Falls back to 'main' unless source has 'fallback_branch' set.
  -h, --help                 Show this help message
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --workspace)
            WORKSPACE="$2"
            shift 2
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --only)
            ONLY="$2"
            shift 2
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Check prerequisites
for cmd in jq swift git; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: '$cmd' is required but not found in PATH."
        exit 1
    fi
done

if [[ ! -f "$SOURCES_FILE" ]]; then
    echo "Error: sources.json not found at $SOURCES_FILE"
    exit 1
fi

if ! jq empty "$SOURCES_FILE" 2>/dev/null; then
    echo "Error: sources.json is not valid JSON"
    exit 1
fi

# Validate common template files exist
for tmpl in "${TEMPLATE_FILES[@]}"; do
    if [[ ! -f "$COMMON_DIR/$tmpl" ]]; then
        echo "Error: common template '$tmpl' not found at $COMMON_DIR/$tmpl"
        exit 1
    fi
done

# Check whether a remote repository has a given branch (refs only, no data transfer).
remote_has_branch() {
    local repo_url="$1"
    local branch_name="$2"
    git ls-remote --heads "$repo_url" "$branch_name" 2>/dev/null | grep -q .
}

# Find the latest release/* branch in a remote repository (version-sorted).
# Returns the full branch name (e.g. "release/1.1") or empty string if none.
latest_release_branch() {
    local repo_url="$1"
    git ls-remote --heads "$repo_url" "release/*" 2>/dev/null \
        | sed 's|.*refs/heads/||' \
        | sort -t/ -k2 -V \
        | tail -n 1
}

# Validate all source entries before doing any work
validate_sources() {
    local validation_failed=false

    for i in $(seq 0 $((source_count - 1))); do
        local entry entry_id entry_type entry_path entry_repo has_targets has_docc_catalog label
        entry=$(jq ".[$i]" "$SOURCES_FILE")
        entry_id=$(echo "$entry" | jq -r '.id // empty')
        entry_type=$(echo "$entry" | jq -r '.type // empty')
        entry_path=$(echo "$entry" | jq -r '.path // empty')
        entry_repo=$(echo "$entry" | jq -r '.repo // empty')
        has_targets=$(echo "$entry" | jq 'has("targets")')
        has_docc_catalog=$(echo "$entry" | jq 'has("docc_catalog")')

        label="Entry $i"
        if [[ -n "$entry_id" ]]; then
            label="'$entry_id' (index $i)"
        fi

        if [[ -z "$entry_id" ]]; then
            echo "Validation error: $label is missing 'id'"
            validation_failed=true
        fi

        if [[ -z "$entry_type" ]]; then
            echo "Validation error: $label is missing 'type'"
            validation_failed=true
        elif [[ "$entry_type" != "local" && "$entry_type" != "git" ]]; then
            echo "Validation error: $label has unknown type '$entry_type' (expected 'local' or 'git')"
            validation_failed=true
        fi

        if [[ "$entry_type" == "local" && -z "$entry_path" ]]; then
            echo "Validation error: $label is type 'local' but missing 'path'"
            validation_failed=true
        fi

        if [[ "$entry_type" == "git" && -z "$entry_repo" ]]; then
            echo "Validation error: $label is type 'git' but missing 'repo'"
            validation_failed=true
        fi

        if [[ "$has_targets" == "false" && "$has_docc_catalog" == "false" ]]; then
            echo "Validation error: $label must have either 'targets' or 'docc_catalog'"
            validation_failed=true
        fi

        if [[ "$has_targets" == "true" && "$has_docc_catalog" == "true" ]]; then
            echo "Validation error: $label has both 'targets' and 'docc_catalog' (they are mutually exclusive)"
            validation_failed=true
        fi

        local add_docc_plugin
        add_docc_plugin=$(echo "$entry" | jq -r '.add_docc_plugin // false')
        if [[ "$add_docc_plugin" == "true" && "$entry_type" != "git" ]]; then
            echo "Validation error: $label has 'add_docc_plugin' but is not type 'git'"
            validation_failed=true
        fi

        local fallback_branch
        fallback_branch=$(echo "$entry" | jq -r '.fallback_branch // empty')
        if [[ -n "$fallback_branch" && "$entry_type" != "git" ]]; then
            echo "Validation error: $label has 'fallback_branch' but is not type 'git'"
            validation_failed=true
        fi

        local preflight
        preflight=$(echo "$entry" | jq -r '.preflight // empty')
        if [[ "$(echo "$entry" | jq 'has("preflight")')" == "true" && -z "$preflight" ]]; then
            echo "Validation error: $label has 'preflight' but it is empty"
            validation_failed=true
        fi
    done

    if [[ "$validation_failed" == true ]]; then
        echo ""
        echo "Fix the errors in $SOURCES_FILE before continuing."
        return 1
    fi

    echo "Validated $source_count source entries."
}

source_count=$(jq length "$SOURCES_FILE")
validate_sources

# Fresh start if requested
if [[ "$CLEAN" == true ]]; then
    echo "Removing workspace: $WORKSPACE"
    rm -rf "$WORKSPACE"
fi

mkdir -p "$OUTPUT_DIR" "$WORKSPACE"

# Track results
declare -a SUCCEEDED=()
declare -a FAILED=()
declare -a COMBINED_ARCHIVES=()
declare -a MANIFEST_ENTRIES=()

# Find the docc tool — prefer xcrun on macOS, fall back to PATH.
# Resolved once as an array and reused throughout.
DOCC_CMD=()
if command -v xcrun &>/dev/null && xcrun --find docc &>/dev/null 2>&1; then
    DOCC_CMD=(xcrun docc)
elif command -v docc &>/dev/null; then
    DOCC_CMD=(docc)
fi

# Find .doccarchive files produced by swift package generate-documentation.
# They land in the .build directory tree; the exact path varies by toolchain.
find_doccarchive() {
    local search_dir="$1"
    local target="$2"
    # Look for <Target>.doccarchive anywhere under .build
    find "$search_dir/.build" -type d -name "${target}.doccarchive" 2>/dev/null | head -n 1
}

# Discover the .docc catalog directory for a Swift package target using
# `swift package describe`. Returns the path relative to the package root.
find_docc_catalog_for_target() {
    local source_dir="$1"
    local target="$2"

    local target_path
    target_path=$(cd "$source_dir" && swift package describe --type json 2>/dev/null \
        | jq -r --arg t "$target" '.targets[] | select(.name == $t) | .path')

    if [[ -z "$target_path" ]]; then
        echo ""
        return
    fi

    # Look for a .docc directory inside the target's source path
    find "$source_dir/$target_path" -maxdepth 1 -name "*.docc" -type d 2>/dev/null | head -n 1
}

# Copy common template files (header.html, footer.html) into a .docc catalog.
# Warns if the catalog already contains a file that will be overwritten.
install_templates() {
    local catalog_dir="$1"
    local source_id="$2"

    for tmpl in "${TEMPLATE_FILES[@]}"; do
        if [[ -f "$catalog_dir/$tmpl" ]]; then
            echo "  WARNING: overwriting existing $tmpl in $source_id catalog"
        fi
        cp "$COMMON_DIR/$tmpl" "$catalog_dir/$tmpl"
        echo "  Installed $tmpl -> $catalog_dir/"
    done
}

build_source() {
    local id type path repo docc_catalog source_dir
    local actual_branch="main"
    local fallback_used=false
    local entry="$1"

    id=$(echo "$entry" | jq -r '.id')
    type=$(echo "$entry" | jq -r '.type')
    path=$(echo "$entry" | jq -r '.path // empty')
    repo=$(echo "$entry" | jq -r '.repo // empty')
    docc_catalog=$(echo "$entry" | jq -r '.docc_catalog // empty')

    # Determine which branch to use for git sources
    if [[ "$type" == "git" && -n "$BRANCH" ]]; then
        if remote_has_branch "$repo" "$BRANCH"; then
            actual_branch="$BRANCH"
        else
            local fallback_branch
            fallback_branch=$(echo "$entry" | jq -r '.fallback_branch // empty')

            if [[ "$fallback_branch" == "latest-release" ]]; then
                local detected
                detected=$(latest_release_branch "$repo")
                if [[ -n "$detected" ]]; then
                    echo "  Note: $repo does not have branch '$BRANCH', using latest release branch '$detected'"
                    actual_branch="$detected"
                else
                    echo "  Note: $repo does not have branch '$BRANCH' and no release branches found, falling back to 'main'"
                    actual_branch="main"
                fi
            elif [[ -n "$fallback_branch" ]]; then
                echo "  Note: $repo does not have branch '$BRANCH', using fallback branch '$fallback_branch'"
                actual_branch="$fallback_branch"
            else
                echo "  Note: $repo does not have branch '$BRANCH', falling back to 'main'"
                actual_branch="main"
            fi
            fallback_used=true
        fi
    fi
    local add_docc_plugin
    add_docc_plugin=$(echo "$entry" | jq -r '.add_docc_plugin // false')

    # Read per-source extra flags as a bash array (may be empty)
    local extra_flags=()
    local extra_flags_json
    extra_flags_json=$(echo "$entry" | jq -r '.extra_flags // empty')
    if [[ -n "$extra_flags_json" ]]; then
        while IFS= read -r f; do
            extra_flags+=("$f")
        done < <(echo "$entry" | jq -r '.extra_flags[]')
    fi

    # Read targets as a bash array (may be empty)
    local targets=()
    local targets_json
    targets_json=$(echo "$entry" | jq -r '.targets // empty')
    if [[ -n "$targets_json" ]]; then
        while IFS= read -r t; do
            targets+=("$t")
        done < <(echo "$entry" | jq -r '.targets[]')
    fi

    echo ""
    echo "========================================"
    echo "Building: $id"
    echo "========================================"

    # Resolve source directory
    if [[ "$type" == "local" ]]; then
        source_dir="$ROOT_DIR/$path"
        if [[ ! -d "$source_dir" ]]; then
            echo "Error: local path '$source_dir' does not exist"
            return 1
        fi
    elif [[ "$type" == "git" ]]; then
        source_dir="$WORKSPACE/$id"
        if [[ -d "$source_dir/.git" ]]; then
            echo "Updating existing clone (branch: $actual_branch)..."
            git -C "$source_dir" fetch --quiet origin "$actual_branch"
            git -C "$source_dir" checkout --quiet "$actual_branch" 2>/dev/null || git -C "$source_dir" checkout --quiet -b "$actual_branch" "origin/$actual_branch"
            git -C "$source_dir" reset --quiet --hard "origin/$actual_branch"
        else
            echo "Cloning $repo (branch: $actual_branch)..."
            git clone --quiet --branch "$actual_branch" "$repo" "$source_dir"
        fi
    else
        echo "Error: unknown type '$type'"
        return 1
    fi

    # Run preflight command if configured
    local preflight
    preflight=$(echo "$entry" | jq -r '.preflight // empty')
    if [[ -n "$preflight" ]]; then
        echo "Running preflight: $preflight"
        (cd "$source_dir" && bash -c "$preflight")
    fi

    # Inject swift-docc-plugin dependency if requested and not already present
    if [[ "$add_docc_plugin" == "true" ]]; then
        if ! grep -q 'swift-docc-plugin' "$source_dir/Package.swift"; then
            echo "Adding swift-docc-plugin dependency..."
            (cd "$source_dir" && swift package add-dependency \
                https://github.com/swiftlang/swift-docc-plugin --from 1.1.0)
        else
            echo "swift-docc-plugin dependency already present, skipping."
        fi
    fi

    # Build based on JSON configuration:
    #   targets     -> swift package generate-documentation --target <T>
    #   docc_catalog -> docc convert
    if [[ ${#targets[@]} -gt 0 ]]; then
        # Install templates into each target's .docc catalog
        for target in "${targets[@]}"; do
            local catalog_dir
            catalog_dir=$(find_docc_catalog_for_target "$source_dir" "$target")
            if [[ -n "$catalog_dir" ]]; then
                install_templates "$catalog_dir" "$id/$target"
            else
                echo "  Note: could not locate .docc catalog for target '$target', skipping template install"
            fi
        done

        # Build each target via swift package
        for target in "${targets[@]}"; do
            echo "Building target: $target"
            (cd "$source_dir" && swift package generate-documentation \
                --target "$target" \
                "${DOCC_BUILD_FLAGS[@]}" \
                ${extra_flags[@]+"${extra_flags[@]}"})

            local archive
            archive=$(find_doccarchive "$source_dir" "$target")
            if [[ -z "$archive" ]]; then
                echo "Error: could not find .doccarchive for target '$target'"
                return 1
            fi

            local output_name="$id"
            if [[ ${#targets[@]} -gt 1 ]]; then
                output_name="${id}-${target}"
            fi
            echo "Exporting $archive -> $OUTPUT_DIR/${output_name}.doccarchive"
            rm -rf "${OUTPUT_DIR:?}/${output_name}.doccarchive"
            cp -R "$archive" "$OUTPUT_DIR/${output_name}.doccarchive"

            COMBINED_ARCHIVES+=("$OUTPUT_DIR/${output_name}.doccarchive")
        done

    else
        # Use docc convert directly
        local catalog_path="$source_dir/$docc_catalog"
        if [[ ! -d "$catalog_path" ]]; then
            echo "Error: docc catalog not found at '$catalog_path'"
            return 1
        fi

        if [[ ${#DOCC_CMD[@]} -eq 0 ]]; then
            echo "Error: 'docc' tool not found (tried xcrun and PATH)"
            return 1
        fi

        # Install templates into the catalog
        install_templates "$catalog_path" "$id"

        local output_path="$OUTPUT_DIR/${id}.doccarchive"
        echo "Converting catalog with docc convert..."
        rm -rf "$output_path"

        "${DOCC_CMD[@]}" convert "$catalog_path" --output-path "$output_path" \
            "${DOCC_BUILD_FLAGS[@]}" \
            ${extra_flags[@]+"${extra_flags[@]}"}

        COMBINED_ARCHIVES+=("$output_path")
    fi

    # Record manifest entry
    local commit_sha=""
    if [[ "$type" == "git" ]]; then
        commit_sha=$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || echo "unknown")
    elif [[ "$type" == "local" ]]; then
        actual_branch=$(git -C "$source_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        commit_sha=$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || echo "unknown")
    fi

    MANIFEST_ENTRIES+=("$(jq -n \
        --arg id "$id" \
        --arg type "$type" \
        --arg actual "$actual_branch" \
        --argjson fallback "$fallback_used" \
        --arg commit "$commit_sha" \
        '{id: $id, type: $type, actual_branch: $actual, fallback_used: $fallback, commit: $commit}')")

    echo "Done: $id"
}

# Read sources and iterate
for i in $(seq 0 $((source_count - 1))); do
    entry=$(jq ".[$i]" "$SOURCES_FILE")
    entry_id=$(echo "$entry" | jq -r '.id')

    # Filter if --only is set
    if [[ -n "$ONLY" ]] && [[ "$entry_id" != "$ONLY" ]]; then
        continue
    fi

    if build_source "$entry"; then
        SUCCEEDED+=("$entry_id")
    else
        FAILED+=("$entry_id")
    fi
done

# Merge all archives — only when building everything (not --only)
if [[ ${#COMBINED_ARCHIVES[@]} -gt 0 && -z "$ONLY" ]]; then
    echo ""
    echo "========================================"
    echo "Merging combined documentation archive"
    echo "========================================"

    if [[ ${#FAILED[@]} -gt 0 ]]; then
        echo "Error: cannot merge — the following sources failed to build:"
        for fid in "${FAILED[@]}"; do
            echo "  - $fid"
        done
        echo "Skipping merge step."
        FAILED+=("combined-merge")

    elif [[ ${#DOCC_CMD[@]} -eq 0 ]]; then
        echo "Error: 'docc' tool not found, cannot merge archives"
        FAILED+=("combined-merge")

    else
        # Verify every expected archive exists on disk
        missing_archives=()
        for a in "${COMBINED_ARCHIVES[@]}"; do
            if [[ ! -d "$a" ]]; then
                missing_archives+=("$a")
            fi
        done

        if [[ ${#missing_archives[@]} -gt 0 ]]; then
            echo "Error: the following expected archives are missing:"
            for ma in "${missing_archives[@]}"; do
                echo "  - $ma"
            done
            FAILED+=("combined-merge")
        else
            COMBINED_OUTPUT="$OUTPUT_DIR/Combined.doccarchive"
            rm -rf "$COMBINED_OUTPUT"

            echo "Merging ${#COMBINED_ARCHIVES[@]} archives..."
            for a in "${COMBINED_ARCHIVES[@]}"; do
                echo "  - $(basename "$a")"
            done

            if "${DOCC_CMD[@]}" merge "${COMBINED_ARCHIVES[@]}" \
                --output-path "$COMBINED_OUTPUT"; then
                echo "Combined archive: $COMBINED_OUTPUT"
                SUCCEEDED+=("combined-merge")
            else
                echo "Error: docc merge failed"
                FAILED+=("combined-merge")
            fi
        fi
    fi
fi

# Generate build manifest
if [[ ${#MANIFEST_ENTRIES[@]} -gt 0 ]]; then
    MANIFEST_FILE="$OUTPUT_DIR/build-manifest.json"
    sources_json="["
    for idx in "${!MANIFEST_ENTRIES[@]}"; do
        if [[ $idx -gt 0 ]]; then
            sources_json+=","
        fi
        sources_json+="${MANIFEST_ENTRIES[$idx]}"
    done
    sources_json+="]"

    jq -n \
        --arg build_time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg branch "$BRANCH" \
        --argjson sources "$sources_json" \
        '{build_time: $build_time, branch: $branch, sources: $sources}' \
        > "$MANIFEST_FILE"
    echo "Build manifest: $MANIFEST_FILE"
fi

# Summary
echo ""
echo "========================================"
echo "Build Summary"
echo "========================================"
echo "Succeeded (${#SUCCEEDED[@]}): ${SUCCEEDED[*]:-none}"
echo "Failed    (${#FAILED[@]}): ${FAILED[*]:-none}"
echo "Output:   $OUTPUT_DIR"
if [[ -n "$BRANCH" ]]; then
    echo "Branch: $BRANCH"
fi
if [[ -f "$OUTPUT_DIR/build-manifest.json" ]]; then
    echo "Manifest: $OUTPUT_DIR/build-manifest.json"
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
