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
"""Build and merge DocC documentation archives from multiple sources."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Common DocC build flags applied to all documentation builds
DOCC_BUILD_FLAGS = [
    "--experimental-enable-custom-templates",
    "--enable-mentioned-in",
    "--enable-experimental-external-link-support",
]

# Common template files to copy into each .docc catalog before building
TEMPLATE_FILES = ["header.html", "footer.html"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build and export DocC documentation archives from multiple sources."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Where to export built archives (default: .build-output/)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Where to clone external repos (default: .workspace/)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove workspace and re-clone everything",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Build only a specific source by id",
    )
    return parser.parse_args()


def check_prerequisites():
    """Verify that required tools are available in PATH."""
    for cmd in ("swift", "git"):
        if shutil.which(cmd) is None:
            print(f"Error: '{cmd}' is required but not found in PATH.")
            sys.exit(1)


def find_docc_command():
    """Find the docc tool — prefer xcrun on macOS, fall back to PATH.

    Returns a list of command components (e.g. ["xcrun", "docc"] or ["docc"]),
    or an empty list if docc is not found.
    """
    if shutil.which("xcrun"):
        try:
            subprocess.run(
                ["xcrun", "--find", "docc"],
                check=True,
                capture_output=True,
            )
            return ["xcrun", "docc"]
        except subprocess.CalledProcessError:
            pass
    if shutil.which("docc"):
        return ["docc"]
    return []


def validate_sources(config):
    """Validate the loaded JSON structure."""
    errors = []

    if "version" not in config:
        errors.append("Top-level 'version' field is missing")

    if "sources" not in config:
        errors.append("Top-level 'sources' field is missing")
        # Can't validate further without sources
        if errors:
            for e in errors:
                print(f"Validation error: {e}")
            sys.exit(1)

    sources = config["sources"]
    if not isinstance(sources, list):
        errors.append("'sources' must be an array")
        for e in errors:
            print(f"Validation error: {e}")
        sys.exit(1)

    for i, entry in enumerate(sources):
        entry_id = entry.get("id", "")
        label = f"'{entry_id}' (index {i})" if entry_id else f"Entry {i}"

        if not entry_id:
            errors.append(f"{label} is missing 'id'")

        entry_type = entry.get("type", "")
        if not entry_type:
            errors.append(f"{label} is missing 'type'")
        elif entry_type not in ("local", "git"):
            errors.append(
                f"{label} has unknown type '{entry_type}' (expected 'local' or 'git')"
            )

        if entry_type == "local" and not entry.get("path"):
            errors.append(f"{label} is type 'local' but missing 'path'")

        if entry_type == "git" and not entry.get("repo"):
            errors.append(f"{label} is type 'git' but missing 'repo'")

        if entry_type == "git" and not entry.get("ref"):
            errors.append(f"{label} is type 'git' but missing 'ref'")

        has_targets = "targets" in entry
        has_docc_catalog = "docc_catalog" in entry

        if not has_targets and not has_docc_catalog:
            errors.append(f"{label} must have either 'targets' or 'docc_catalog'")

        if has_targets and has_docc_catalog:
            errors.append(
                f"{label} has both 'targets' and 'docc_catalog' (they are mutually exclusive)"
            )

        if entry.get("add_docc_plugin") and entry_type != "git":
            errors.append(f"{label} has 'add_docc_plugin' but is not type 'git'")

        if "preflight" in entry and not entry["preflight"]:
            errors.append(f"{label} has 'preflight' but it is empty")

    if errors:
        for e in errors:
            print(f"Validation error: {e}")
        print(f"\nFix the errors in sources.json before continuing.")
        sys.exit(1)

    print(f"Validated {len(sources)} source entries.")


def clone_or_update(source, workspace, ref):
    """Git clone or fetch+checkout for a given ref. Returns the source directory."""
    source_id = source["id"]
    repo = source["repo"]
    source_dir = workspace / source_id

    if (source_dir / ".git").is_dir():
        print(f"Updating existing clone (ref: {ref})...")
        subprocess.run(
            ["git", "-C", str(source_dir), "fetch", "--quiet", "origin", ref],
            check=True,
        )
        # Try checking out the ref; if it's a new branch, create a tracking branch
        result = subprocess.run(
            ["git", "-C", str(source_dir), "checkout", "--quiet", ref],
            capture_output=True,
        )
        if result.returncode != 0:
            subprocess.run(
                [
                    "git", "-C", str(source_dir), "checkout", "--quiet",
                    "-b", ref, f"origin/{ref}",
                ],
                check=True,
            )
        subprocess.run(
            [
                "git", "-C", str(source_dir), "reset", "--quiet", "--hard",
                f"origin/{ref}",
            ],
            check=True,
        )
        # Remove untracked files left by previous builds (e.g. swift-book's
        # generated SummaryOfTheGrammar.md) so preflight scripts don't fail
        # when they detect stale output from a cached workspace.
        subprocess.run(
            ["git", "-C", str(source_dir), "clean", "--quiet", "-fdx"],
            check=True,
        )
    else:
        print(f"Cloning {repo} (ref: {ref})...")
        subprocess.run(
            ["git", "clone", "--quiet", "--branch", ref, repo, str(source_dir)],
            check=True,
        )

    return source_dir


def find_docc_catalog_for_target(source_dir, target):
    """Discover the .docc catalog directory for a Swift package target.

    Uses `swift package describe --type json` to find the target's source path,
    then looks for a .docc directory inside it.
    """
    try:
        result = subprocess.run(
            ["swift", "package", "describe", "--type", "json"],
            cwd=str(source_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        pkg_info = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None

    for t in pkg_info.get("targets", []):
        if t.get("name") == target:
            target_path = t.get("path", "")
            if not target_path:
                return None
            target_dir = source_dir / target_path
            for child in target_dir.iterdir():
                if child.is_dir() and child.suffix == ".docc":
                    return child
            return None
    return None


def install_templates(catalog_dir, common_dir, source_id):
    """Copy common template files (header.html, footer.html) into a .docc catalog."""
    for tmpl in TEMPLATE_FILES:
        src = common_dir / tmpl
        dst = catalog_dir / tmpl
        if dst.exists():
            print(f"  WARNING: overwriting existing {tmpl} in {source_id} catalog")
        shutil.copy2(str(src), str(dst))
        print(f"  Installed {tmpl} -> {catalog_dir}/")


def find_doccarchive(search_dir, target):
    """Find a .doccarchive directory produced by swift package generate-documentation."""
    build_dir = search_dir / ".build"
    if not build_dir.exists():
        return None
    archive_name = f"{target}.doccarchive"
    for root, dirs, _files in os.walk(str(build_dir)):
        if archive_name in dirs:
            return Path(root) / archive_name
    return None


def add_docc_plugin(source_dir):
    """Inject swift-docc-plugin dependency if not already present."""
    package_swift = source_dir / "Package.swift"
    if "swift-docc-plugin" in package_swift.read_text():
        print("swift-docc-plugin dependency already present, skipping.")
        return
    print("Adding swift-docc-plugin dependency...")
    subprocess.run(
        [
            "swift", "package", "add-dependency",
            "https://github.com/swiftlang/swift-docc-plugin",
            "--from", "1.1.0",
        ],
        cwd=str(source_dir),
        check=True,
    )


def build_source(source, root_dir, workspace, common_dir, temp_archive_dir, docc_cmd, env):
    """Build a single source entry.

    Returns a tuple of (list_of_archive_paths, manifest_entry) on success,
    or raises an exception on failure.
    """
    source_id = source["id"]
    source_type = source["type"]
    docc_catalog = source.get("docc_catalog", "")
    targets = source.get("targets", [])
    extra_flags = source.get("extra_flags", [])

    print()
    print("=" * 40)
    print(f"Building: {source_id}")
    print("=" * 40)

    # Resolve source directory
    if source_type == "local":
        source_dir = root_dir / source["path"]
        if not source_dir.is_dir():
            raise RuntimeError(f"local path '{source_dir}' does not exist")
    elif source_type == "git":
        ref = source["ref"]
        source_dir = clone_or_update(source, workspace, ref)
    else:
        raise RuntimeError(f"unknown type '{source_type}'")

    # Run preflight command if configured
    preflight = source.get("preflight", "")
    if preflight:
        print(f"Running preflight: {preflight}")
        subprocess.run(
            ["bash", "-c", preflight],
            cwd=str(source_dir),
            check=True,
            env=env,
        )

    # Inject swift-docc-plugin if requested
    if source.get("add_docc_plugin"):
        add_docc_plugin(source_dir)

    archives = []

    if targets:
        # Install templates into each target's .docc catalog
        for target in targets:
            catalog_dir = find_docc_catalog_for_target(source_dir, target)
            if catalog_dir:
                install_templates(catalog_dir, common_dir, f"{source_id}/{target}")
            else:
                print(
                    f"  Note: could not locate .docc catalog for target '{target}', "
                    "skipping template install"
                )

        # Build each target via swift package
        for target in targets:
            print(f"Building target: {target}")
            cmd = [
                "swift", "package", "generate-documentation",
                "--target", target,
            ] + DOCC_BUILD_FLAGS + extra_flags
            subprocess.run(cmd, cwd=str(source_dir), check=True, env=env)

            archive = find_doccarchive(source_dir, target)
            if not archive:
                raise RuntimeError(
                    f"could not find .doccarchive for target '{target}'"
                )

            output_name = source_id if len(targets) == 1 else f"{source_id}-{target}"
            dest = temp_archive_dir / f"{output_name}.doccarchive"
            if dest.exists():
                shutil.rmtree(str(dest))
            shutil.copytree(str(archive), str(dest))
            print(f"Exported {archive} -> {dest}")
            archives.append(dest)
    else:
        # Use docc convert directly
        catalog_path = source_dir / docc_catalog
        if not catalog_path.is_dir():
            raise RuntimeError(f"docc catalog not found at '{catalog_path}'")

        if not docc_cmd:
            raise RuntimeError("'docc' tool not found (tried xcrun and PATH)")

        install_templates(catalog_path, common_dir, source_id)

        dest = temp_archive_dir / f"{source_id}.doccarchive"
        if dest.exists():
            shutil.rmtree(str(dest))

        print("Converting catalog with docc convert...")
        cmd = docc_cmd + [
            "convert", str(catalog_path),
            "--output-path", str(dest),
        ] + DOCC_BUILD_FLAGS + extra_flags
        subprocess.run(cmd, check=True, env=env)
        archives.append(dest)

    # Build manifest entry
    commit_sha = ""
    actual_ref = ""
    if source_type == "git":
        actual_ref = source["ref"]
        try:
            result = subprocess.run(
                ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            commit_sha = result.stdout.strip()
        except subprocess.CalledProcessError:
            commit_sha = "unknown"
    elif source_type == "local":
        try:
            result = subprocess.run(
                ["git", "-C", str(source_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            actual_ref = result.stdout.strip()
            result = subprocess.run(
                ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            commit_sha = result.stdout.strip()
        except subprocess.CalledProcessError:
            actual_ref = "unknown"
            commit_sha = "unknown"

    manifest_entry = {
        "id": source_id,
        "type": source_type,
        "ref": actual_ref,
        "commit": commit_sha,
    }

    print(f"Done: {source_id}")
    return archives, manifest_entry


def merge_archives(archives, output_path, docc_cmd):
    """Merge multiple .doccarchive directories into one using docc merge."""
    if output_path.exists():
        shutil.rmtree(str(output_path))

    print(f"Merging {len(archives)} archives...")
    for a in archives:
        print(f"  - {a.name}")

    cmd = docc_cmd + ["merge"] + [str(a) for a in archives] + [
        "--output-path", str(output_path),
    ]
    subprocess.run(cmd, check=True)
    print(f"Combined archive: {output_path}")


def write_manifest(output_dir, version, entries):
    """Write build-manifest.json to the output directory."""
    manifest = {
        "version": version,
        "build_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": entries,
    }
    manifest_path = output_dir / "build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Build manifest: {manifest_path}")


def main():
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    root_dir = script_dir.parent
    sources_file = script_dir / "sources.json"
    common_dir = root_dir / "common"

    output_dir = Path(args.output_dir) if args.output_dir else root_dir / ".build-output"
    workspace = Path(args.workspace) if args.workspace else root_dir / ".workspace"

    check_prerequisites()
    docc_cmd = find_docc_command()

    # Validate common template files exist
    for tmpl in TEMPLATE_FILES:
        tmpl_path = common_dir / tmpl
        if not tmpl_path.is_file():
            print(f"Error: common template '{tmpl}' not found at {tmpl_path}")
            sys.exit(1)

    # Load and validate sources
    if not sources_file.is_file():
        print(f"Error: sources.json not found at {sources_file}")
        sys.exit(1)

    try:
        config = json.loads(sources_file.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: sources.json is not valid JSON: {e}")
        sys.exit(1)

    validate_sources(config)

    version = config["version"]
    sources = config["sources"]

    # Ensure consistent, pretty-printed DocC JSON output
    env = os.environ.copy()
    env["DOCC_JSON_PRETTYPRINT"] = "YES"

    # Fresh start if requested
    if args.clean:
        print(f"Removing workspace: {workspace}")
        if workspace.exists():
            shutil.rmtree(str(workspace))

    output_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    # Temp directory for intermediate archives (under workspace)
    temp_archive_dir = workspace / "_archives"
    temp_archive_dir.mkdir(parents=True, exist_ok=True)

    # Track results
    succeeded = []
    failed = []
    all_archives = []
    manifest_entries = []

    for source in sources:
        source_id = source["id"]

        # Filter if --only is set
        if args.only and source_id != args.only:
            continue

        try:
            archives, manifest_entry = build_source(
                source, root_dir, workspace, common_dir,
                temp_archive_dir, docc_cmd, env,
            )
            succeeded.append(source_id)
            all_archives.extend(archives)
            manifest_entries.append(manifest_entry)
        except Exception as e:
            print(f"Error building {source_id}: {e}")
            failed.append(source_id)

    # When --only is used, copy the single source's archive to output directly
    if args.only and all_archives:
        for archive in all_archives:
            dest = output_dir / archive.name
            if dest.exists():
                shutil.rmtree(str(dest))
            shutil.copytree(str(archive), str(dest))
            print(f"Output: {dest}")

    # Merge all archives — only when building everything (not --only)
    if all_archives and not args.only:
        print()
        print("=" * 40)
        print("Merging combined documentation archive")
        print("=" * 40)

        if failed:
            print("Error: cannot merge — the following sources failed to build:")
            for fid in failed:
                print(f"  - {fid}")
            print("Skipping merge step.")
            failed.append("combined-merge")
        elif not docc_cmd:
            print("Error: 'docc' tool not found, cannot merge archives")
            failed.append("combined-merge")
        else:
            # Verify every expected archive exists on disk
            missing = [a for a in all_archives if not a.is_dir()]
            if missing:
                print("Error: the following expected archives are missing:")
                for ma in missing:
                    print(f"  - {ma}")
                failed.append("combined-merge")
            else:
                combined_output = output_dir / f"{version}"
                try:
                    merge_archives(all_archives, combined_output, docc_cmd)
                    succeeded.append("combined-merge")
                except subprocess.CalledProcessError:
                    print("Error: docc merge failed")
                    failed.append("combined-merge")

    # Clean up intermediate archives
    if not args.only and temp_archive_dir.exists():
        shutil.rmtree(str(temp_archive_dir))

    # Generate build manifest
    if manifest_entries:
        write_manifest(output_dir, version, manifest_entries)

    # Summary
    print()
    print("=" * 40)
    print("Build Summary")
    print("=" * 40)
    print(f"Succeeded ({len(succeeded)}): {' '.join(succeeded) if succeeded else 'none'}")
    print(f"Failed    ({len(failed)}): {' '.join(failed) if failed else 'none'}")
    print(f"Output:   {output_dir}")
    if (output_dir / "build-manifest.json").is_file():
        print(f"Manifest: {output_dir / 'build-manifest.json'}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
