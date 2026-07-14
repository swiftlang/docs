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
import tarfile
import urllib.error
import urllib.request
import zipfile
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from strip_availability import strip_archive
from curate_navigator import curate_navigator, validate_navigation, NavigationError


class ArchiveFetchError(Exception):
    """Fatal failure fetching or extracting an archive source.

    Raised by fetch_archive() when a download, extraction, or archive
    resolution step fails. Propagated by main() to abort the entire build,
    rather than being collected into the per-source failure list.
    """


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
    parser.add_argument(
        "--extra-hosting-prefix",
        default=None,
        metavar="PREFIX",
        help="Prepend a path segment to the hosting base path (e.g. 'docs' → 'docs/main'). "
             "Does not affect the output directory name or landing page title.",
    )
    return parser.parse_args()


def check_prerequisites():
    """Verify that required tools are available in PATH."""
    for cmd in ("swift", "git"):
        if shutil.which(cmd) is None:
            print(f"Error: '{cmd}' is required but not found in PATH.")
            sys.exit(1)


Tools = namedtuple("Tools", ["swift", "docc"])


def discover_tools():
    """Discover the swift toolchain commands available in PATH.

    Returns a `Tools` pair of command prefixes (each a list; empty when
    unavailable). swift comes directly from PATH, and docc is located via
    `xcrun --find` on macOS with a fallback to PATH.
    """
    swift = ["swift"] if shutil.which("swift") else []

    docc = []
    if shutil.which("xcrun"):
        try:
            subprocess.run(
                ["xcrun", "--find", "docc"],
                check=True, capture_output=True,
            )
            docc = ["xcrun", "docc"]
        except subprocess.CalledProcessError:
            pass
    if not docc and shutil.which("docc"):
        docc = ["docc"]

    return Tools(swift=swift, docc=docc)


def validate_sources(config):
    """Return a list of validation error strings; empty list means valid.

    Pure check — does not print or exit. The caller decides how to surface
    errors. Returns early after structural problems (missing top-level keys,
    `sources` not a list) so per-entry validation only runs against a validated
    shape.
    """
    errors = []

    if "version" not in config:
        errors.append("Top-level 'version' field is missing")

    if "sources" not in config:
        errors.append("Top-level 'sources' field is missing")
        return errors

    sources = config["sources"]
    if not isinstance(sources, list):
        errors.append("'sources' must be an array")
        return errors

    for i, entry in enumerate(sources):
        entry_id = entry.get("id", "")
        label = f"'{entry_id}' (index {i})" if entry_id else f"Entry {i}"

        if not entry_id:
            errors.append(f"{label} is missing 'id'")

        entry_type = entry.get("type", "")
        if not entry_type:
            errors.append(f"{label} is missing 'type'")
        elif entry_type not in ("local", "git", "archive"):
            errors.append(
                f"{label} has unknown type '{entry_type}' "
                "(expected 'local', 'git', or 'archive')"
            )

        if entry_type == "local" and not entry.get("path"):
            errors.append(f"{label} is type 'local' but missing 'path'")

        if entry_type == "git" and not entry.get("repo"):
            errors.append(f"{label} is type 'git' but missing 'repo'")

        if entry_type == "git" and not entry.get("ref"):
            errors.append(f"{label} is type 'git' but missing 'ref'")

        if entry_type == "archive":
            if not entry.get("url"):
                errors.append(f"{label} is type 'archive' but missing 'url'")
            docc_archive_name = entry.get("docc_archive_name", "")
            if not docc_archive_name:
                errors.append(
                    f"{label} is type 'archive' but missing 'docc_archive_name'"
                )
            elif not docc_archive_name.endswith(".doccarchive"):
                errors.append(
                    f"{label} 'docc_archive_name' must end with '.doccarchive' "
                    f"(got '{docc_archive_name}')"
                )
            archive_format = entry.get("format", "tar.gz")
            if archive_format not in ("tar.gz", "zip"):
                errors.append(
                    f"{label} has unsupported 'format' '{archive_format}' "
                    "(supported: 'tar.gz', 'zip')"
                )
            if "strip_availability" in entry and not isinstance(
                entry["strip_availability"], bool
            ):
                errors.append(
                    f"{label} 'strip_availability' must be a boolean "
                    f"(got {type(entry['strip_availability']).__name__})"
                )
            disallowed_for_archive = (
                "targets", "docc_catalog", "path", "repo", "ref",
                "preflight", "add_docc_plugin", "extra_flags", "env",
            )
            for field in disallowed_for_archive:
                if field in entry:
                    errors.append(
                        f"{label} is type 'archive' but has '{field}' "
                        "(not allowed for archive sources)"
                    )

        if entry_type in ("local", "git"):
            has_targets = "targets" in entry
            has_docc_catalog = "docc_catalog" in entry

            if not has_targets and not has_docc_catalog:
                errors.append(
                    f"{label} must have either 'targets' or 'docc_catalog'"
                )

            if has_targets and has_docc_catalog:
                errors.append(
                    f"{label} has both 'targets' and 'docc_catalog' "
                    "(they are mutually exclusive)"
                )

            if "strip_availability" in entry:
                errors.append(
                    f"{label} has 'strip_availability' but is not type "
                    "'archive' (only allowed on archive sources)"
                )

        if entry.get("add_docc_plugin") and entry_type != "git":
            errors.append(f"{label} has 'add_docc_plugin' but is not type 'git'")

        if "preflight" in entry and not entry["preflight"]:
            errors.append(f"{label} has 'preflight' but it is empty")

        if "env" in entry:
            env_val = entry["env"]
            if not isinstance(env_val, dict):
                errors.append(
                    f"{label} 'env' must be an object mapping names to values "
                    f"(got {type(env_val).__name__})"
                )
            else:
                for key, value in env_val.items():
                    if not isinstance(key, str) or not key:
                        errors.append(f"{label} 'env' has non-string or empty key")
                    if not isinstance(value, (str, int, float, bool)):
                        errors.append(
                            f"{label} 'env[{key}]' must be a string, number, or bool "
                            f"(got {type(value).__name__})"
                        )

    return errors


def clean_package_build_dirs(root_dir, sources):
    """Remove `.build/` dirs for every local Swift package this script touches.

    Targets the union of: `local`-typed sources in `sources.json` (their
    `path` resolved under `root_dir`) and any sibling of `root_dir` that
    contains a `Package.swift`. Existence of `Package.swift` gates the
    repo-root sweep so unrelated subdirectories like `common/` are left alone.
    Returns the list of removed `.build/` paths in deterministic order.
    """
    targets = set()
    for s in sources:
        if s.get("type") == "local" and s.get("path"):
            targets.add((root_dir / s["path"]).resolve())
    for pkg_manifest in root_dir.glob("*/Package.swift"):
        targets.add(pkg_manifest.parent.resolve())

    removed = []
    for pkg_dir in sorted(targets):
        build_dir = pkg_dir / ".build"
        if build_dir.exists():
            shutil.rmtree(str(build_dir))
            removed.append(build_dir)
    return removed


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


def fetch_archive(source, workspace):
    """Download and extract a pre-built .doccarchive from a URL.

    Always re-downloads (no caching). Returns the Path to the extracted
    .doccarchive directory. Raises ArchiveFetchError on any failure so the
    caller can abort the whole build.
    """
    source_id = source["id"]
    url = source["url"]
    docc_archive_name = source["docc_archive_name"]
    archive_format = source.get("format", "tar.gz")

    download_dir = workspace / "_downloads" / source_id
    if download_dir.exists():
        shutil.rmtree(str(download_dir))
    download_dir.mkdir(parents=True)

    filename = Path(url).name or f"{source_id}.{archive_format}"
    download_path = download_dir / filename
    extract_dir = download_dir / "extracted"
    extract_dir.mkdir()

    print(f"Downloading {url}...")
    try:
        urllib.request.urlretrieve(url, str(download_path))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        raise ArchiveFetchError(
            f"failed to download archive for '{source_id}' from {url}: {e}"
        ) from e

    print(f"Extracting to {extract_dir}...")
    try:
        if archive_format == "zip":
            with zipfile.ZipFile(str(download_path)) as zf:
                zf.extractall(path=str(extract_dir))
        else:
            with tarfile.open(str(download_path), "r:gz") as tar:
                tar.extractall(path=str(extract_dir), filter="data")
    except (tarfile.TarError, zipfile.BadZipFile, OSError, ValueError) as e:
        raise ArchiveFetchError(
            f"failed to extract archive for '{source_id}': {e}"
        ) from e

    matches = [p for p in extract_dir.rglob(docc_archive_name) if p.is_dir()]
    if not matches:
        raise ArchiveFetchError(
            f"'{docc_archive_name}' not found in archive for '{source_id}'"
        )
    archive_path = min(matches, key=lambda p: len(p.parts))
    print(f"Found {docc_archive_name} at {archive_path}")
    return archive_path

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


def add_docc_plugin(source_dir, swift_cmd):
    """Inject swift-docc-plugin dependency if not already present."""
    package_swift = source_dir / "Package.swift"
    if "swift-docc-plugin" in package_swift.read_text():
        print("swift-docc-plugin dependency already present, skipping.")
        return
    print("Adding swift-docc-plugin dependency...")
    subprocess.run(
        swift_cmd + [
            "package", "add-dependency",
            "https://github.com/swiftlang/swift-docc-plugin",
            "--from", "1.1.0",
        ],
        cwd=str(source_dir),
        check=True,
    )


def _build_archive_source(source, workspace, temp_archive_dir):
    """Fetch an archive source, copy it into the staging dir, optionally strip.

    Returns (archives, manifest_entry). Raises ArchiveFetchError on download
    or extraction failure (caller treats this as fatal).
    """
    source_id = source["id"]
    archive_path = fetch_archive(source, workspace)
    dest = temp_archive_dir / f"{source_id}.doccarchive"
    if dest.exists():
        shutil.rmtree(str(dest))
    shutil.copytree(str(archive_path), str(dest))
    print(f"Copied {archive_path} -> {dest}")

    if source.get("strip_availability"):
        print(f"Stripping availability from {dest}...")
        scanned, modified, removed = strip_archive(dest)
        print(
            f"  scanned {scanned} files; modified {modified}; "
            f"removed {removed} 'platforms' keys"
        )

    manifest_entry = {
        "id": source_id,
        "type": "archive",
        "ref": source.get("version_label", ""),
        "commit": "",
        "url": source["url"],
    }
    return [dest], manifest_entry


def _build_package_targets(source, source_dir, common_dir, temp_archive_dir, swift_cmd, env):
    """Build each Swift package target with `swift package generate-documentation`."""
    source_id = source["id"]
    targets = source["targets"]
    extra_flags = source.get("extra_flags", [])

    for target in targets:
        catalog_dir = find_docc_catalog_for_target(source_dir, target)
        if catalog_dir:
            install_templates(catalog_dir, common_dir, f"{source_id}/{target}")
        else:
            print(
                f"  Note: could not locate .docc catalog for target '{target}', "
                "skipping template install"
            )

    archives = []
    for target in targets:
        print(f"Building target: {target}")
        cmd = swift_cmd + [
            "package", "generate-documentation",
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
    return archives


def _build_docc_catalog(source, source_dir, common_dir, temp_archive_dir, docc_cmd, env):
    """Convert a standalone `.docc` catalog with `docc convert`."""
    source_id = source["id"]
    docc_catalog = source["docc_catalog"]
    extra_flags = source.get("extra_flags", [])

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
    return [dest]


def _collect_git_metadata(source_dir, configured_ref=None):
    """Read (ref, commit) from a git working tree.

    For git-type sources, configured_ref is the ref from sources.json and is
    returned verbatim — only the commit comes from `git rev-parse HEAD`. For
    local sources (configured_ref is None), both come from git, defaulting to
    'unknown' when the working tree isn't a git checkout.
    """
    if configured_ref is not None:
        try:
            result = subprocess.run(
                ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            return configured_ref, result.stdout.strip()
        except subprocess.CalledProcessError:
            return configured_ref, "unknown"

    try:
        ref_result = subprocess.run(
            ["git", "-C", str(source_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        commit_result = subprocess.run(
            ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return ref_result.stdout.strip(), commit_result.stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown", "unknown"


def build_source(source, root_dir, workspace, common_dir, temp_archive_dir, docc_cmd, env, swift_cmd=None):
    """Build a single source entry.

    Returns (archives, manifest_entry) on success. Dispatches to a helper for
    each source type; archive sources short-circuit before any toolchain or
    preflight setup.
    """
    if swift_cmd is None:
        swift_cmd = ["swift"]

    source_id = source["id"]
    source_type = source["type"]

    print()
    print("=" * 40)
    print(f"Building: {source_id}")
    print("=" * 40)

    if source_type == "archive":
        archives, manifest_entry = _build_archive_source(
            source, workspace, temp_archive_dir
        )
        print(f"Done: {source_id}")
        return archives, manifest_entry

    if source_type == "local":
        source_dir = root_dir / source["path"]
        if not source_dir.is_dir():
            raise RuntimeError(f"local path '{source_dir}' does not exist")
    elif source_type == "git":
        source_dir = clone_or_update(source, workspace, source["ref"])
    else:
        raise RuntimeError(f"unknown type '{source_type}'")

    source_env = source.get("env")
    if source_env:
        env = env.copy()
        for key, value in source_env.items():
            env[key] = str(value)
        print(f"Source env overrides: {', '.join(sorted(source_env))}")

    preflight = source.get("preflight", "")
    if preflight:
        print(f"Running preflight: {preflight}")
        subprocess.run(
            ["bash", "-c", preflight],
            cwd=str(source_dir), check=True, env=env,
        )

    if source.get("add_docc_plugin"):
        add_docc_plugin(source_dir, swift_cmd)

    if source.get("targets"):
        archives = _build_package_targets(
            source, source_dir, common_dir, temp_archive_dir, swift_cmd, env
        )
    else:
        archives = _build_docc_catalog(
            source, source_dir, common_dir, temp_archive_dir, docc_cmd, env
        )

    configured_ref = source["ref"] if source_type == "git" else None
    actual_ref, commit_sha = _collect_git_metadata(source_dir, configured_ref)

    manifest_entry = {
        "id": source_id,
        "type": source_type,
        "ref": actual_ref,
        "commit": commit_sha,
    }
    print(f"Done: {source_id}")
    return archives, manifest_entry


def merge_archives(archives, output_path, docc_cmd, landing_page_name):
    """Merge multiple .doccarchive directories into one using docc merge."""
    if output_path.exists():
        shutil.rmtree(str(output_path))

    print(f"Merging {len(archives)} archives...")
    for a in archives:
        print(f"  - {a.name}")

    cmd = docc_cmd + ["merge"] + [str(a) for a in archives] + [
        "--output-path", str(output_path),
        "--synthesized-landing-page-name", landing_page_name,
        "--synthesized-landing-page-kind", "Project",
        "--synthesized-landing-page-topics-style", "list",
    ]
    subprocess.run(cmd, check=True)
    print(f"Combined archive: {output_path}")


def transform_static_hosting(archive_path, hosting_base_path, docc_cmd):
    """Bake a hosting base path into a finished .doccarchive in place.

    Runs `docc process-archive transform-for-static-hosting` on the archive
    so its router and links resolve under /<hosting_base_path>/, then swaps
    the transformed archive into the original location. On failure, leaves
    the original archive untouched.
    """
    if not docc_cmd:
        raise RuntimeError("'docc' tool not found (tried xcrun and PATH)")

    archive_path = Path(archive_path)
    transformed = archive_path.parent / f".{archive_path.name}.transforming"
    if transformed.exists():
        shutil.rmtree(str(transformed))

    print(f"Applying hosting base path '{hosting_base_path}' to {archive_path.name}...")
    cmd = docc_cmd + [
        "process-archive", "transform-for-static-hosting",
        str(archive_path),
        "--hosting-base-path", hosting_base_path,
        "--output-path", str(transformed),
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        if transformed.exists():
            shutil.rmtree(str(transformed))
        raise

    shutil.rmtree(str(archive_path))
    shutil.move(str(transformed), str(archive_path))
    print(f"Transformed archive: {archive_path}")


def inject_custom_templates_into_stubs(archive_path, common_dir):
    """Inject custom-header/custom-footer templates into per-route index.html stubs.

    Workaround for swiftlang/swift-docc#1532. `docc process-archive
    transform-for-static-hosting` regenerates a stripped index.html for every
    route under the archive, and those stubs do not include the
    `<template id="custom-header">` / `<template id="custom-footer">` blocks
    that `docc convert`/`docc merge` baked into the archive root. Without
    this fix, only the archive-root index.html displays the custom header and
    footer; every other route is served by a stub that lacks them.

    Walks every index.html under archive_path except the root, skips any file
    that already contains custom-header (idempotent — and self-disabling once
    DocC fixes the upstream bug), and inserts both template blocks immediately
    after `<body data-color-scheme="auto">` to mirror the placement
    docc convert produces in the root. Returns the number of stubs patched.
    """
    header = (common_dir / "header.html").read_text()
    footer = (common_dir / "footer.html").read_text()
    anchor = '<body data-color-scheme="auto">'
    # Mirror docc convert ordering: footer template first, then header.
    injection = (
        anchor
        + f'<template id="custom-footer">{footer}</template>'
        + f'<template id="custom-header">{header}</template>'
    )

    archive_path = Path(archive_path)
    root_index = (archive_path / "index.html").resolve()
    patched = 0
    for index_path in archive_path.rglob("index.html"):
        if index_path.resolve() == root_index:
            continue
        text = index_path.read_text()
        if "custom-header" in text or anchor not in text:
            continue
        index_path.write_text(text.replace(anchor, injection, 1))
        patched += 1
    return patched


def _finalize_combined_archive(all_archives, output_dir, version, docc_cmd, prior_failed, common_dir=None, navigation=None, hosting_base_path=None):
    """Merge per-source archives and apply the static-hosting transform.

    Returns (succeeded_steps, failed_steps): names that should be added to
    the build summary's success and failure lists, respectively. Bails early
    on missing prerequisites; isolates the merge step from the curation and
    transform steps so a later failure still records the earlier steps as
    succeeded.
    """
    print()
    print("=" * 40)
    print("Merging combined documentation archive")
    print("=" * 40)

    if prior_failed:
        print("Error: cannot merge — the following sources failed to build:")
        for fid in prior_failed:
            print(f"  - {fid}")
        print("Skipping merge step.")
        return [], ["combined-merge"]

    if not docc_cmd:
        print("Error: 'docc' tool not found, cannot merge archives")
        return [], ["combined-merge"]

    missing = [a for a in all_archives if not a.is_dir()]
    if missing:
        print("Error: the following expected archives are missing:")
        for ma in missing:
            print(f"  - {ma}")
        return [], ["combined-merge"]

    combined_output = output_dir / f"{version}"
    try:
        merge_archives(
            all_archives, combined_output, docc_cmd,
            landing_page_name=f"Swift - {version}",
        )
    except subprocess.CalledProcessError:
        print("Error: docc merge failed")
        return [], ["combined-merge"]

    if navigation is not None:
        try:
            curate_navigator(combined_output, navigation)
            print("Curated combined navigator per navigation.json.")
        except (NavigationError, OSError, json.JSONDecodeError) as e:
            print(f"Error: navigator curation failed: {e}")
            return ["combined-merge"], ["navigator-curation"]

    try:
        transform_static_hosting(combined_output, hosting_base_path or version, docc_cmd)
    except subprocess.CalledProcessError:
        print("Error: docc process-archive transform-for-static-hosting failed")
        curated = ["combined-merge"]
        if navigation is not None:
            curated.append("navigator-curation")
        return curated, ["static-hosting-transform"]

    # Workaround for swiftlang/swift-docc#1532 — drop this when fixed.
    if common_dir is not None:
        patched = inject_custom_templates_into_stubs(combined_output, common_dir)
        print(f"Patched custom-header/footer into {patched} per-route stub(s).")

    succeeded = ["combined-merge", "static-hosting-transform"]
    if navigation is not None:
        succeeded.insert(1, "navigator-curation")
    return succeeded, []


def assemble_in_source_order(results_by_index, num_sources):
    """Flatten per-source build results into strict sources.json order.

    `results_by_index` maps a source's position in sources.json to its
    (archives, manifest_entry) result. Build order may differ from source
    order (archive-type sources are built first for fail-fast), so this
    reassembles by ascending index to guarantee the merge — and the manifest
    — follow sources.json exactly. Indices with no result (skipped or failed
    sources) are omitted. A source's archives stay grouped and in the order
    build_source returned them (e.g. multi-target packages).

    Returns (all_archives, manifest_entries).
    """
    all_archives = []
    manifest_entries = []
    for i in range(num_sources):
        result = results_by_index.get(i)
        if result is None:
            continue
        archives, entry = result
        all_archives.extend(archives)
        manifest_entries.append(entry)
    return all_archives, manifest_entries


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
    tools = discover_tools()

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

    validate_errors = validate_sources(config)
    if validate_errors:
        for e in validate_errors:
            print(f"Validation error: {e}")
        print("\nFix the errors in sources.json before continuing.")
        sys.exit(1)
    print(f"Validated {len(config['sources'])} source entries.")

    # Load and validate the navigation manifest (drives combined-archive
    # sidebar curation). Validated up front so a mismatch fails fast.
    navigation_file = script_dir / "navigation.json"
    navigation = None
    if navigation_file.is_file():
        try:
            navigation = json.loads(navigation_file.read_text())
        except json.JSONDecodeError as e:
            print(f"Error: navigation.json is not valid JSON: {e}")
            sys.exit(1)
        nav_errors = validate_navigation(navigation, config)
        if nav_errors:
            for e in nav_errors:
                print(f"Validation error: {e}")
            print("\nFix the errors in navigation.json before continuing.")
            sys.exit(1)
        print("Validated navigation.json against sources.json.")
    else:
        print("No navigation.json found — combined navigator will not be curated.")

    version = config["version"]
    hosting_base_path = f"{args.extra_hosting_prefix}/{version}" if args.extra_hosting_prefix else version
    sources = config["sources"]

    # Ensure consistent, pretty-printed DocC JSON output
    env = os.environ.copy()
    env["DOCC_JSON_PRETTYPRINT"] = "YES"

    # Fresh start if requested
    if args.clean:
        print(f"Removing workspace: {workspace}")
        if workspace.exists():
            shutil.rmtree(str(workspace))
        for build_dir in clean_package_build_dirs(root_dir, sources):
            print(f"Removed package build dir: {build_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    # Temp directory for intermediate archives (under workspace)
    temp_archive_dir = workspace / "_archives"
    temp_archive_dir.mkdir(parents=True, exist_ok=True)

    # Track results
    succeeded = []
    failed = []
    results_by_index = {}

    # Build archive-type sources first so network or extraction failures abort
    # the run before any slow git clones or swift builds. Build order is
    # decoupled from merge order: each result is keyed by the source's position
    # in sources.json, then reassembled in that order below so the merge — and
    # the manifest — strictly follow sources.json regardless of build order.
    def attempt_build(index, source, fatal=(), recoverable=()):
        """Run build_source for one entry, sorting exceptions by severity.

        `fatal` exceptions print and sys.exit(1). `recoverable` exceptions are
        logged and recorded in `failed`. Anything else propagates uncaught.
        """
        sid = source["id"]
        if args.only and sid != args.only:
            return
        try:
            archives, entry = build_source(
                source, root_dir, workspace, common_dir,
                temp_archive_dir, tools.docc, env,
                swift_cmd=tools.swift,
            )
        except fatal as e:
            print(f"Fatal: {e}")
            sys.exit(1)
        except recoverable as e:
            print(f"Error building {sid}: {e}")
            failed.append(sid)
            return
        succeeded.append(sid)
        results_by_index[index] = (archives, entry)

    for i, source in enumerate(sources):
        if source["type"] == "archive":
            attempt_build(i, source, fatal=(ArchiveFetchError,))
    for i, source in enumerate(sources):
        if source["type"] != "archive":
            attempt_build(i, source, recoverable=(Exception,))

    all_archives, manifest_entries = assemble_in_source_order(
        results_by_index, len(sources)
    )

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
        s_steps, f_steps = _finalize_combined_archive(
            all_archives, output_dir, version, tools.docc, failed,
            common_dir=common_dir, navigation=navigation,
            hosting_base_path=hosting_base_path,
        )
        succeeded.extend(s_steps)
        failed.extend(f_steps)

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
