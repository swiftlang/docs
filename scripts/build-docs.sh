#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCES_FILE="$SCRIPT_DIR/sources.json"

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

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Build and export DocC documentation archives from multiple sources.

Options:
  --output-dir <path>   Where to export built archives (default: .build-output/)
  --workspace <path>    Where to clone external repos (default: .workspace/)
  --clean               Remove workspace and re-clone everything
  --only <id>           Build only a specific source by id
  -h, --help            Show this help message
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
declare -a COMBINED_EXPECTED_IDS=()

# Find the docc tool — prefer xcrun on macOS, fall back to PATH.
# Resolved once and reused throughout.
resolve_docc_cmd() {
    if command -v xcrun &>/dev/null && xcrun --find docc &>/dev/null 2>&1; then
        echo "xcrun docc"
    elif command -v docc &>/dev/null; then
        echo "docc"
    else
        echo ""
    fi
}
DOCC_CMD=$(resolve_docc_cmd)

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
    local entry="$1"

    id=$(echo "$entry" | jq -r '.id')
    type=$(echo "$entry" | jq -r '.type')
    path=$(echo "$entry" | jq -r '.path // empty')
    repo=$(echo "$entry" | jq -r '.repo // empty')
    docc_catalog=$(echo "$entry" | jq -r '.docc_catalog // empty')
    local is_combined
    is_combined=$(echo "$entry" | jq -r '.combined // false')

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
            echo "Updating existing clone..."
            git -C "$source_dir" fetch --quiet origin
            git -C "$source_dir" reset --quiet --hard origin/main
        else
            echo "Cloning $repo..."
            git clone --quiet --depth 1 "$repo" "$source_dir"
        fi
    else
        echo "Error: unknown type '$type'"
        return 1
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

            if [[ "$is_combined" == "true" ]]; then
                COMBINED_ARCHIVES+=("$OUTPUT_DIR/${output_name}.doccarchive")
            fi
        done

    else
        # Use docc convert directly
        local catalog_path="$source_dir/$docc_catalog"
        if [[ ! -d "$catalog_path" ]]; then
            echo "Error: docc catalog not found at '$catalog_path'"
            return 1
        fi

        if [[ -z "$DOCC_CMD" ]]; then
            echo "Error: 'docc' tool not found (tried xcrun and PATH)"
            return 1
        fi

        # Install templates into the catalog
        install_templates "$catalog_path" "$id"

        local output_path="$OUTPUT_DIR/${id}.doccarchive"
        echo "Converting catalog with docc convert..."
        rm -rf "$output_path"

        $DOCC_CMD convert "$catalog_path" --output-path "$output_path" \
            "${DOCC_BUILD_FLAGS[@]}" \
            ${extra_flags[@]+"${extra_flags[@]}"}

        if [[ "$is_combined" == "true" ]]; then
            COMBINED_ARCHIVES+=("$output_path")
        fi
    fi

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

    is_combined=$(echo "$entry" | jq -r '.combined // false')

    if [[ "$is_combined" == "true" ]]; then
        COMBINED_EXPECTED_IDS+=("$entry_id")
    fi

    if build_source "$entry"; then
        SUCCEEDED+=("$entry_id")
    else
        FAILED+=("$entry_id")
    fi
done

# Merge combined archives — only if all combined sources built successfully
if [[ ${#COMBINED_EXPECTED_IDS[@]} -gt 0 && -z "$ONLY" ]]; then
    echo ""
    echo "========================================"
    echo "Merging combined documentation archive"
    echo "========================================"

    # Check that no combined source appears in the FAILED list
    combined_failures=()
    for cid in "${COMBINED_EXPECTED_IDS[@]}"; do
        for fid in ${FAILED[@]+"${FAILED[@]}"}; do
            if [[ "$cid" == "$fid" ]]; then
                combined_failures+=("$cid")
            fi
        done
    done

    if [[ ${#combined_failures[@]} -gt 0 ]]; then
        echo "Error: cannot merge — the following combined sources failed to build:"
        for cf in "${combined_failures[@]}"; do
            echo "  - $cf"
        done
        echo "Skipping merge step."
        FAILED+=("combined-merge")

    elif [[ ${#COMBINED_ARCHIVES[@]} -eq 0 ]]; then
        echo "Error: no combined archives were produced despite all sources succeeding"
        FAILED+=("combined-merge")

    elif [[ -z "$DOCC_CMD" ]]; then
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

            if $DOCC_CMD merge "${COMBINED_ARCHIVES[@]}" \
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

# Summary
echo ""
echo "========================================"
echo "Build Summary"
echo "========================================"
echo "Succeeded (${#SUCCEEDED[@]}): ${SUCCEEDED[*]:-none}"
echo "Failed    (${#FAILED[@]}): ${FAILED[*]:-none}"
echo "Output:   $OUTPUT_DIR"

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
