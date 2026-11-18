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
"""Build the combined Swift documentation site.

Reads `sources.json` (and `navigation.json`, if present) from this directory.
Builds each source into a `.doccarchive`. A source is a local package, a git
checkout, or a prebuilt archive. Merges the results with `docc merge` in
`sources.json` order, then curates the navigator and post-processes the
combined archive.

Writes `<output-dir>/<slug>/` and `<output-dir>/build-manifest.json`. Exits 1
if any source or step failed. See `README.md` in this directory for the
pipeline and the `sources.json` format.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from inject_canonical_link import canonicalize_archive as canonicalize_link_archive
from strip_availability import strip_archive, strip_linux_availability
from strip_language_toggle import strip_archive as strip_language_toggle_archive
from suppress_eyebrows import suppress_archive as suppress_eyebrow_archive
from curate_navigator import (
    curate_navigator,
    validate_navigation,
    collect_eyebrow_overrides,
    NavigationError,
)


class ArchiveFetchError(Exception):
    """Fatal failure fetching or extracting an `archive` source.

    Raised by `fetch_archive()` when a download or extraction fails, or when
    the expected `.doccarchive` is not in the download. Archive sources build
    first. `build_all_sources()` treats any exception from them as fatal: it
    prints the error and exits 1 before any git or swift build starts.
    """


# DocC flags passed to every `docc convert` and package-plugin build.
# Prebuilt `archive` sources do not use them.
DOCC_BUILD_FLAGS = [
    "--experimental-enable-custom-templates",
    "--enable-mentioned-in",
    # "--enable-experimental-external-link-support",
    # Emit a markdown copy of every rendered document (plus a manifest) under
    # data/documentation/. These sidecar files survive docc merge and
    # process-archive transform-for-static-hosting, so they reach the published
    # combined archive for consumption by crawlers and other tooling.
    "--enable-experimental-markdown-output",
    "--enable-experimental-markdown-output-manifest",
]

# Common template files installed into each .docc catalog before building.
# `render_common_template` fills in their placeholders.
TEMPLATE_FILES = ["header.html", "footer.html"]

# Shared favicon copied verbatim (no placeholder substitution) into each .docc
# catalog root before building. DocC recognizes a favicon.ico at the catalog
# root as a custom favicon and copies it into the output, overriding its own
# default icon (see swift-docc's DocumentationBundleFileTypes.isCustomFavicon).
FAVICON_FILE = "favicon.ico"

# Shared mask-icon SVG (Safari pinned-tab icon). Unlike favicon.ico, DocC has
# no catalog-level override for this file — a loose favicon.svg dropped into
# a .docc catalog is silently discarded by `docc convert`. The only place
# this can be fixed is the post-transform restore in
# _finalize_combined_archive (see restore_custom_favicon).
FAVICON_SVG_FILE = "favicon.svg"


def parse_args(argv=None):
    """Parse command-line arguments; `argv` defaults to `sys.argv[1:]`."""
    parser = argparse.ArgumentParser(
        description="Build and export DocC documentation archives from multiple sources."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Where to write the combined archive and build-manifest.json "
             "(default: <repo root>/.build-output)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Where to clone repos and stage downloads and intermediate "
             "archives (default: <repo root>/.workspace)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the workspace and the .build/ directory of every local "
             "Swift package before building",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Build only the source with this id and copy its archive to "
             "--output-dir. Skips the merge and overwrites build-manifest.json "
             "with a one-entry manifest. Exits 1 if the id is unknown. A "
             "'reuse_checkout' source needs its target checkout already in "
             "the workspace.",
    )
    parser.add_argument(
        "--extra-hosting-prefix",
        default=None,
        metavar="PREFIX",
        help="Prepend a path segment to the hosting base path (for example, "
             "'docs' gives 'docs/<slug>'). Does not affect the output "
             "directory name or landing page title.",
    )
    return parser.parse_args(argv)


def check_prerequisites():
    """Exit with status 1 if `swift` or `git` is not on PATH.

    Prints an error for the first missing tool. Does not check for `docc`,
    which `discover_tools()` locates. A missing `docc` fails later, when a
    build needs it.
    """
    for cmd in ("swift", "git"):
        if shutil.which(cmd) is None:
            print(f"Error: '{cmd}' is required but not found in PATH.")
            sys.exit(1)


Tools = namedtuple("Tools", ["swift", "docc"])


def discover_tools():
    """Discover the swift toolchain commands available in PATH.

    Returns a `Tools` pair of command prefixes (each a list; empty when
    unavailable). `swift` is `["swift"]` when it is on PATH. `docc` is
    `["xcrun", "docc"]` when `xcrun --find docc` succeeds, else `["docc"]`
    when it is on PATH. Prints nothing and does not raise for missing tools.
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


SOURCE_TYPES = ("local", "git", "archive")

# The fields each source type accepts in sources.json. This table is the one
# place that records which fields belong to which type. Any other key is an
# error, so a typo cannot silently disable a feature.
_PACKAGE_FIELDS = {
    "id", "type", "targets", "docc_catalog", "extra_flags", "env",
    "preflight", "strip_linux_availability", "additional_symbol_graph_dir",
}
SOURCE_FIELDS = {
    "local": _PACKAGE_FIELDS | {"path"},
    "git": _PACKAGE_FIELDS | {"repo", "ref", "reuse_checkout", "add_docc_plugin"},
    "archive": {
        "id", "type", "url", "docc_archive_name", "format",
        "strip_availability", "version_label",
    },
}
_ALL_SOURCE_FIELDS = set().union(*SOURCE_FIELDS.values())

# Fields whose value must be a string.
_STRING_FIELDS = (
    "path", "repo", "ref", "docc_catalog", "preflight", "url",
    "docc_archive_name", "version_label",
)


def validate_sources(config):
    """Return a list of validation error strings; empty list means valid.

    Does not print or exit. The caller decides how to surface errors. The
    function collects every problem it finds in one pass, and does not raise
    on malformed input.

    Top level: `version` must be an object with a `slug`, and `sources` must be
    an array. If `sources` is missing or not an array, the function returns
    at once, because there are no entries to check.

    Each entry must be an object with a unique string `id` and a `type` of
    `local`, `git`, or `archive`. `SOURCE_FIELDS` lists the fields each type
    accepts. Beyond that table, the function checks:

    - Value types: strings, booleans, string lists, and the `env` mapping.
    - Required fields per type (`path`, `repo` and `ref`, `url`, and so on).
    - `local` and `git` sources need exactly one of `targets` (a non-empty
      list) or `docc_catalog`.
    - `additional_symbol_graph_dir` works only with `docc_catalog`.
    - `reuse_checkout` is `git`-only, excludes `repo` and `ref`, and must name
      an earlier `git` source that clones its own repo. Chains and forward
      references are errors.
    - `archive` sources accept only the archive fields.
    """
    errors = []

    if "version" not in config:
        errors.append("Top-level 'version' field is missing")
    elif not isinstance(config["version"], dict):
        errors.append("Top-level 'version' field must be an object with 'slug'")
    else:
        if not config["version"].get("slug"):
            errors.append("Top-level 'version' object is missing 'slug'")

    if "sources" not in config:
        errors.append("Top-level 'sources' field is missing")
        return errors

    sources = config["sources"]
    if not isinstance(sources, list):
        errors.append("'sources' must be an array")
        return errors

    # Earlier `git` sources that clone their own repo.
    # Only these can be a `reuse_checkout` target.
    earlier_clones = set()
    seen_ids = set()

    for i, entry in enumerate(sources):
        if not isinstance(entry, dict):
            errors.append(f"Entry {i} must be an object (got {type(entry).__name__})")
            continue

        entry_id = entry.get("id", "")
        if not isinstance(entry_id, str):
            errors.append(f"Entry {i} 'id' must be a string")
            entry_id = ""
        label = f"'{entry_id}' (index {i})" if entry_id else f"Entry {i}"

        if not entry_id:
            errors.append(f"{label} is missing 'id'")
        elif entry_id in seen_ids:
            errors.append(f"{label} has a duplicate id (an earlier source uses it)")
        seen_ids.add(entry_id)

        entry_type = entry.get("type", "")
        if not isinstance(entry_type, str):
            errors.append(f"{label} 'type' must be a string")
            entry_type = ""
        if not entry_type:
            errors.append(f"{label} is missing 'type'")
        elif entry_type not in SOURCE_TYPES:
            errors.append(
                f"{label} has unknown type '{entry_type}' "
                "(expected 'local', 'git', or 'archive')"
            )

        for field in entry:
            if field not in _ALL_SOURCE_FIELDS:
                errors.append(f"{label} has unknown field '{field}'")

        for field in _STRING_FIELDS:
            if field in entry and not isinstance(entry[field], str):
                errors.append(
                    f"{label} '{field}' must be a string "
                    f"(got {type(entry[field]).__name__})"
                )
        if "extra_flags" in entry and not _is_string_list(entry["extra_flags"]):
            errors.append(f"{label} 'extra_flags' must be a list of strings")
        if "add_docc_plugin" in entry and not isinstance(
            entry["add_docc_plugin"], bool
        ):
            errors.append(
                f"{label} 'add_docc_plugin' must be a boolean "
                f"(got {type(entry['add_docc_plugin']).__name__})"
            )

        if entry_type == "local" and not entry.get("path"):
            errors.append(f"{label} is type 'local' but missing 'path'")

        reuses_checkout = "reuse_checkout" in entry

        if entry_type == "git" and not reuses_checkout:
            if not entry.get("repo"):
                errors.append(f"{label} is type 'git' but missing 'repo'")
            if not entry.get("ref"):
                errors.append(f"{label} is type 'git' but missing 'ref'")

        if reuses_checkout:
            reuse = entry["reuse_checkout"]
            if entry_type != "git":
                errors.append(
                    f"{label} has 'reuse_checkout' but is not type 'git'"
                )
            elif not isinstance(reuse, str) or not reuse:
                errors.append(
                    f"{label} 'reuse_checkout' must be a non-empty string"
                )
            else:
                for field in ("repo", "ref"):
                    if field in entry:
                        errors.append(
                            f"{label} has 'reuse_checkout' and '{field}' "
                            "(the checkout is shared, so it comes from the "
                            "named source)"
                        )
                if reuse not in earlier_clones:
                    errors.append(
                        f"{label} 'reuse_checkout' names '{reuse}', which is "
                        "not an earlier 'git' source that clones its own repo"
                    )

        if entry_type == "archive":
            if not entry.get("url"):
                errors.append(f"{label} is type 'archive' but missing 'url'")
            docc_archive_name = entry.get("docc_archive_name", "")
            if not docc_archive_name:
                errors.append(
                    f"{label} is type 'archive' but missing 'docc_archive_name'"
                )
            elif isinstance(docc_archive_name, str) and not docc_archive_name.endswith(
                ".doccarchive"
            ):
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
            for field in sorted(_ALL_SOURCE_FIELDS - SOURCE_FIELDS["archive"]):
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

            if has_targets and not (
                _is_string_list(entry["targets"]) and entry["targets"]
            ):
                errors.append(f"{label} 'targets' must be a non-empty list of strings")

            if "strip_availability" in entry:
                errors.append(
                    f"{label} has 'strip_availability' but is not type "
                    "'archive' (only allowed on archive sources)"
                )

            # Fields that belong to the other package-source type. The
            # `reuse_checkout`, `add_docc_plugin` and `strip_availability`
            # cases have their own messages above and below.
            other_type_fields = (
                _ALL_SOURCE_FIELDS
                - SOURCE_FIELDS[entry_type]
                - SOURCE_FIELDS["archive"]
                - {"reuse_checkout", "add_docc_plugin"}
            )
            for field in sorted(other_type_fields):
                if field in entry:
                    errors.append(
                        f"{label} is type '{entry_type}' but has '{field}' "
                        "(not allowed for this source type)"
                    )

            if "strip_linux_availability" in entry and not isinstance(
                entry["strip_linux_availability"], bool
            ):
                errors.append(
                    f"{label} 'strip_linux_availability' must be a boolean "
                    f"(got {type(entry['strip_linux_availability']).__name__})"
                )

            if "additional_symbol_graph_dir" in entry:
                value = entry["additional_symbol_graph_dir"]
                if not isinstance(value, str) or not value:
                    errors.append(
                        f"{label} 'additional_symbol_graph_dir' must be a "
                        "non-empty string"
                    )
                if has_targets:
                    errors.append(
                        f"{label} has 'additional_symbol_graph_dir' but also "
                        "'targets' (only allowed alongside 'docc_catalog')"
                    )

        if entry.get("add_docc_plugin") and entry_type != "git":
            errors.append(f"{label} has 'add_docc_plugin' but is not type 'git'")

        if "preflight" in entry and not entry["preflight"]:
            errors.append(f"{label} has 'preflight' but it is empty")

        if entry_type == "git" and entry_id and not reuses_checkout:
            earlier_clones.add(entry_id)

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


def _is_string_list(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _resolve_canonical_base_url(config):
    """Return config's `canonical_base_url` if it is a usable URL, else None.

    A usable URL is a string with an `http` or `https` scheme and a host. The
    function returns it unchanged. A missing or null field is silent, and
    canonical-link injection does not run.

    Any other value prints a warning to stdout and returns None. That covers a
    non-string, an empty string, another scheme, no host, or an unparseable
    URL such as `http://[bad`. The warning shows up in CI logs. The build
    continues without canonical links, because they are only SEO metadata.
    """
    canonical_base_url = config.get("canonical_base_url")
    if canonical_base_url is None:
        return None

    parsed = None
    if isinstance(canonical_base_url, str):
        try:
            parsed = urllib.parse.urlparse(canonical_base_url)
        except ValueError:
            pass

    if parsed and parsed.scheme in ("http", "https") and parsed.netloc:
        return canonical_base_url

    print(
        f"Warning: 'canonical_base_url' ({canonical_base_url!r}) is not a valid "
        "absolute http(s) URL (expected e.g. 'https://docs.swift.org/latest'); "
        "skipping canonical link injection."
    )
    return None


def clean_package_build_dirs(root_dir, sources):
    """Delete `.build/` for local Swift packages.

    Targets the union of two sets:

    - Each `local` source's `path`, resolved under `root_dir`. The function
      does not check these for a `Package.swift`.
    - Each immediate subdirectory of `root_dir` that holds a `Package.swift`.
      This check keeps unrelated directories such as `common/` untouched.

    Returns the removed `.build/` paths, sorted by package directory. Prints
    nothing. The caller reports each removal.
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
    """Sync `<workspace>/<id>` to `ref` and return that directory.

    A fresh clone uses `git clone --branch ref`, so `ref` must be a branch or
    tag. The clone is left on that branch.

    An existing clone is fetched, then reset. This discards all local state:
    edits, untracked files, and ignored files such as `.build/` and preflight
    output. The clone is then checked out detached at the fetched commit. Here
    `ref` may also be a commit SHA. The clone's `origin` URL is not compared
    with `source["repo"]`.

    Prints progress. Raises `subprocess.CalledProcessError` on any git failure.
    """
    source_id = source["id"]
    repo = source["repo"]
    source_dir = workspace / source_id

    if (source_dir / ".git").is_dir():
        print(f"Updating existing clone (ref: {ref})...")
        git = ["git", "-C", str(source_dir)]
        subprocess.run([*git, "fetch", "--quiet", "origin", ref], check=True)
        # Discard leftovers from earlier builds (the docc plugin's edit to
        # Package.swift, swift-book's generated SummaryOfTheGrammar.md) before
        # switching refs. Stale files can break the preflight scripts.
        subprocess.run([*git, "reset", "--quiet", "--hard"], check=True)
        subprocess.run([*git, "clean", "--quiet", "-fdx"], check=True)
        # Detach at what was fetched, so branches and tags take one path.
        subprocess.run(
            [*git, "checkout", "--quiet", "--force", "--detach", "FETCH_HEAD"],
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

    Always re-downloads. It first wipes `<workspace>/_downloads/<id>`. It
    extracts a `zip` archive when `format` is `zip`, and a `tar.gz` otherwise.
    Returns the Path to the shallowest directory named `docc_archive_name`
    inside the extracted files.

    Raises `ArchiveFetchError` if the download or extraction fails, or if no
    directory has that name. Errors from clearing or creating the download
    directory are not wrapped.
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
    except (OSError, ValueError) as e:  # OSError covers URLError and HTTPError
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
    except (
        tarfile.TarError, zipfile.BadZipFile, zlib.error, EOFError,
        OSError, ValueError, RuntimeError, NotImplementedError,
    ) as e:
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
    """Return the `.docc` directory directly inside a package target, or None.

    Runs `swift package describe --type json` in `source_dir` to find the
    target's source path. Uses the process environment, not the source's `env`
    overrides. Returns the first `.docc` child it finds.

    None also means that `describe` failed or printed invalid JSON, or that
    the target is unknown or has no path.
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


def render_common_template(text, year=None):
    """Substitute the `{{COPYRIGHT_YEAR}}` placeholder used in common/*.html.

    `year` defaults to the current UTC year. Other `{{...}}` text is left as is.
    """
    if year is None:
        year = datetime.now(timezone.utc).year
    return text.replace("{{COPYRIGHT_YEAR}}", str(year))


def install_templates(catalog_dir, common_dir, source_id):
    """Install the shared header, footer and favicon into a `.docc` catalog.

    The files come from `TEMPLATE_FILES` and `FAVICON_FILE` in `common_dir`.
    The header and footer pass through `render_common_template`, which fills
    in placeholders. The favicon is copied byte for byte.

    Overwrites any file of the same name already in `catalog_dir`, and prints a
    warning that names `source_id`. Raises `FileNotFoundError` if a file is
    missing from `common_dir`. Files written before the error stay in place.
    """
    for tmpl in TEMPLATE_FILES:
        src = common_dir / tmpl
        dst = catalog_dir / tmpl
        if dst.exists():
            print(f"  WARNING: overwriting existing {tmpl} in {source_id} catalog")
        dst.write_text(render_common_template(src.read_text()))
        print(f"  Installed {tmpl} -> {catalog_dir}/")

    favicon_src = common_dir / FAVICON_FILE
    favicon_dst = catalog_dir / FAVICON_FILE
    if favicon_dst.exists():
        print(f"  WARNING: overwriting existing {FAVICON_FILE} in {source_id} catalog")
    shutil.copyfile(str(favicon_src), str(favicon_dst))
    print(f"  Installed {FAVICON_FILE} -> {catalog_dir}/")


def find_doccarchive(search_dir, target):
    """Return the first `<target>.doccarchive` under `search_dir/.build`, or None.

    `search_dir` is the package root. The function adds `.build` itself. The
    search order depends on the filesystem, so if several matches exist (stale
    output in a reused `.build/`), any one may be returned.
    """
    build_dir = search_dir / ".build"
    if not build_dir.exists():
        return None
    archive_name = f"{target}.doccarchive"
    for root, dirs, _files in os.walk(str(build_dir)):
        if archive_name in dirs:
            return Path(root) / archive_name
    return None


def _docc_plugin_resolves(source_dir, swift_cmd):
    """Return True if the manifest lists swift-docc-plugin as a dependency.

    Runs `swift package describe --type json` in `source_dir`. That evaluates
    `Package.swift`, so unlike a text search it ignores a dependency in an
    inactive branch of a conditional expression. An example is a manifest with
    two `dependencies:` arrays gated by an environment variable.

    Checks the manifest's direct dependencies for a URL that contains
    `swift-docc-plugin`. It does not check resolution. It uses the process
    environment, not the source's `env` overrides. Returns False if `describe`
    fails or prints invalid JSON.
    """
    try:
        result = subprocess.run(
            swift_cmd + ["package", "describe", "--type", "json"],
            cwd=str(source_dir), capture_output=True, text=True, check=True,
        )
        pkg_info = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return False
    return any(
        "swift-docc-plugin" in dep.get("url", "")
        for dep in pkg_info.get("dependencies", [])
    )


def add_docc_plugin(source_dir, swift_cmd):
    """Make `Package.swift` depend on swift-docc-plugin (from 1.1.0).

    Edits the manifest in place. Skips the edit when the text
    `swift-docc-plugin` appears anywhere in `Package.swift`, even in a comment
    or an inactive branch.

    Otherwise runs `swift package add-dependency`, then checks the manifest
    with `_docc_plugin_resolves`. Some manifests have more than one
    `dependencies:` array, such as a ternary gated on a flag. In that case
    `add-dependency` can add the entry to a branch that is not active. The
    function then moves the line into the first `dependencies:` array and
    checks again.

    Raises `CalledProcessError` if `add-dependency` fails. Raises
    `RuntimeError` if it cannot isolate the added line, or the plugin still
    does not resolve. The edited manifest stays on disk after a
    `RuntimeError`. Prints progress.
    """
    package_swift = source_dir / "Package.swift"
    before = package_swift.read_text()
    if "swift-docc-plugin" in before:
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

    if _docc_plugin_resolves(source_dir, swift_cmd):
        return

    print(
        "  swift-docc-plugin did not resolve after being added — the "
        "manifest likely has more than one 'dependencies:' array; "
        "relocating it into the first one..."
    )
    after = package_swift.read_text()
    added_lines = [
        line for line in after.splitlines(keepends=True)
        if "swift-docc-plugin" in line
    ]
    if len(added_lines) != 1:
        raise RuntimeError(
            "could not isolate the swift-docc-plugin line added by "
            "'swift package add-dependency' in Package.swift"
        )
    plugin_line = added_lines[0]

    without_line = after.replace(plugin_line, "", 1)
    marker = without_line.index("dependencies:")
    bracket = without_line.index("[", marker) + 1
    fixed = without_line[:bracket] + "\n" + plugin_line.strip() + without_line[bracket:]
    package_swift.write_text(fixed)

    if not _docc_plugin_resolves(source_dir, swift_cmd):
        raise RuntimeError(
            "swift-docc-plugin still does not resolve after relocating it "
            "into the first 'dependencies:' array in Package.swift"
        )
    print("  Relocated swift-docc-plugin into the active dependencies array.")


def _build_archive_source(source, workspace, temp_archive_dir):
    """Fetch an archive source and stage it in `temp_archive_dir`.

    Copies the fetched archive to `<temp_archive_dir>/<id>.doccarchive`. When
    `strip_availability` is true, removes the `platforms` keys from it.

    Returns `([dest], entry)`. `entry` is `{id, type, ref, commit, url}`.
    `ref` is the source's `version_label` (or `""`) and `commit` is `""`.
    Raises `ArchiveFetchError` if the fetch fails. Errors while copying or
    stripping are not wrapped.
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
    """Build each package target with `swift package generate-documentation`.

    First installs the shared templates into each target's `.docc` catalog. If
    no catalog is found, it prints a note and builds that target without the
    shared header and footer. Then, for each target, runs the plugin in
    `source_dir` with `env`, `DOCC_BUILD_FLAGS` and the source's `extra_flags`.

    Stages each archive as `<temp_archive_dir>/<id>.doccarchive` for a single
    target, or `<id>-<target>.doccarchive` for several. Returns the staged
    paths. Raises `CalledProcessError` if a build fails, and `RuntimeError` if
    its archive is not found.
    """
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
    """Convert a standalone `.docc` catalog with `docc convert`.

    Installs the shared templates into the catalog inside `source_dir`,
    overwriting existing copies. Then replaces any earlier
    `<temp_archive_dir>/<id>.doccarchive`.

    With `additional_symbol_graph_dir`, resolves it against `source_dir`. The
    directory must exist and hold at least one entry. It is passed as
    `--additional-symbol-graph-dir`, before the source's `extra_flags`.

    Returns `[<temp_archive_dir>/<id>.doccarchive]`. Raises `RuntimeError` if
    the catalog, `docc`, or the symbol-graph directory is missing or empty.
    Raises `CalledProcessError` if `docc convert` fails. The templates are
    installed, and the old archive removed, before the symbol-graph check.
    """
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

    symbol_graph_flags = []
    additional_symbol_graph_dir = source.get("additional_symbol_graph_dir")
    if additional_symbol_graph_dir:
        graph_dir = source_dir / additional_symbol_graph_dir
        if not graph_dir.is_dir() or not any(graph_dir.iterdir()):
            raise RuntimeError(
                f"'additional_symbol_graph_dir' '{graph_dir}' is missing or "
                "empty (did the source's preflight generate the symbol graphs?)"
            )
        symbol_graph_flags = [
            "--additional-symbol-graph-dir",
            str(graph_dir),
        ]

    print("Converting catalog with docc convert...")
    cmd = docc_cmd + [
        "convert", str(catalog_path),
        "--output-path", str(dest),
    ] + DOCC_BUILD_FLAGS + symbol_graph_flags + extra_flags
    subprocess.run(cmd, check=True, env=env)
    return [dest]


def _configured_ref(source, sources):
    """Return the ref that sources.json configures for a git source.

    For a `reuse_checkout` source, returns the ref of the source it names.
    Returns None for non-git sources. Also returns None for a reusing source
    when `sources` is None or has no entry with that id. The caller then reads
    the checkout's own HEAD name, which is `HEAD` for a detached checkout.
    """
    if source["type"] != "git":
        return None
    reused_id = source.get("reuse_checkout")
    if not reused_id:
        return source["ref"]
    for candidate in sources or []:
        if candidate["id"] == reused_id:
            return candidate["ref"]
    return None


def _collect_git_metadata(source_dir, configured_ref=None):
    """Return `(ref, commit)` for the git working tree at `source_dir`.

    When `configured_ref` is not None, it is returned as the ref. Only the
    commit comes from `git rev-parse HEAD`. Otherwise both come from git. The
    ref is `git rev-parse --abbrev-ref HEAD`, which is `HEAD` for a detached
    checkout. A local package inside this repo reports this repo's branch and
    commit.

    A failing git command (not a checkout, for example) gives `"unknown"` for
    the failed part, with no warning. A missing `git` binary raises.
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


def build_source(source, root_dir, workspace, common_dir, temp_archive_dir, docc_cmd, env, swift_cmd=None, sources=None):
    """Build one `sources.json` entry and return `(archives, manifest_entry)`.

    `archives` lists `.doccarchive` paths in `temp_archive_dir`.
    `manifest_entry` is `{id, type, ref, commit}`, plus `url` for archives.

    An `archive` source is fetched and staged, and the function returns before
    any toolchain or preflight step. For `local` and `git` sources, the
    function takes these steps in order:

    1. Find the checkout. A `local` source uses `root_dir/<path>`. A `git`
       source is cloned or updated in `<workspace>/<id>`. With
       `reuse_checkout`, the source uses the checkout of the source it names,
       which must already exist.
    2. Add the source's `env` (values converted to strings) to `env`.
    3. Run `preflight` with `bash -c` in the checkout. Its environment holds
       `DOCS_ROOT`, the path to `root_dir`.
    4. Run `add_docc_plugin`, if set.
    5. Build. With `targets`, build each package target. Otherwise convert
       `docc_catalog` with `docc convert`, using the source's
       `additional_symbol_graph_dir` and `extra_flags`.
    6. Strip Linux availability from each archive, if
       `strip_linux_availability` is set.

    `sources` is the full list from `sources.json`. Only the `ref` lookup for
    a `reuse_checkout` source uses it. Without it, that source records the
    checkout's own HEAD name.

    Raises `RuntimeError` for an unknown type, a missing local path or reused
    checkout, or a build problem. Raises `CalledProcessError` if a command
    fails, and `ArchiveFetchError` for an archive fetch. Never exits.
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
    elif source_type == "git" and source.get("reuse_checkout"):
        reused_id = source["reuse_checkout"]
        source_dir = workspace / reused_id
        if not (source_dir / ".git").is_dir():
            raise RuntimeError(
                f"'reuse_checkout' names '{reused_id}', but its checkout "
                f"'{source_dir}' does not exist (build '{reused_id}' first)"
            )
        print(f"Reusing checkout of '{reused_id}'")
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
        # The preflight runs in the source's checkout. DOCS_ROOT points back
        # to this repo's helper scripts.
        subprocess.run(
            ["bash", "-c", preflight],
            cwd=str(source_dir), check=True,
            env={**env, "DOCS_ROOT": str(root_dir)},
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

    if source.get("strip_linux_availability"):
        for archive in archives:
            print(f"Stripping Linux availability from {archive}...")
            scanned, modified, removed = strip_linux_availability(archive)
            print(
                f"  scanned {scanned} files; modified {modified}; "
                f"removed {removed} Linux platform entries"
            )

    configured_ref = _configured_ref(source, sources)
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
    """Merge `.doccarchive` directories into `output_path` with `docc merge`.

    Deletes `output_path` first. The order of `archives` is the merge order.
    Names the synthesized landing page `landing_page_name`, with kind
    `Project` and a `list` topics style. Runs with the process environment.
    Raises `CalledProcessError` if `docc` fails. `docc_cmd` must not be empty.
    """
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
    so its router and links resolve under `/<hosting_base_path>/`. It writes
    to a temporary sibling, then swaps that into the original location.

    The original archive stays intact if `docc` fails or writes no output.
    The function raises `CalledProcessError` in the first case, and
    `RuntimeError` in the second. It also raises `RuntimeError` if `docc_cmd`
    is empty. Returns None.
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

    if not transformed.is_dir():
        raise RuntimeError(
            f"docc exited successfully but wrote no output to '{transformed}'"
        )

    # Swap through a backup so a failed move cannot lose the original.
    backup = archive_path.parent / f".{archive_path.name}.original"
    if backup.exists():
        shutil.rmtree(str(backup))
    shutil.move(str(archive_path), str(backup))
    try:
        shutil.move(str(transformed), str(archive_path))
    except BaseException:
        shutil.move(str(backup), str(archive_path))
        raise
    shutil.rmtree(str(backup))
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

    Walks every index.html under `archive_path` except the root. It skips a
    file whose text contains the string `custom-header` anywhere, or that lacks
    the exact anchor `<body data-color-scheme="auto">`. This makes the function
    idempotent, and it turns itself off once DocC fixes the upstream bug.

    For every other file, it inserts the footer and header blocks right after
    the first anchor. This mirrors the placement `docc convert` produces in the
    root. The templates pass through `render_common_template`. Returns the
    number of stubs patched. Raises `FileNotFoundError` if `header.html` or
    `footer.html` is missing from `common_dir`.
    """
    header = render_common_template((common_dir / "header.html").read_text())
    footer = render_common_template((common_dir / "footer.html").read_text())
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


def restore_custom_favicon(archive_path, common_dir, filename=FAVICON_FILE):
    """Copy `common_dir/<filename>` over `archive_path/<filename>`.

    Workaround for `docc process-archive transform-for-static-hosting`
    unconditionally overwriting favicon.ico and favicon.svg with its own
    bundled defaults. Call it once per file, right after
    `transform_static_hosting`. Drop it when swift-docc stops overwriting them.

    Creates the destination if the transform did not write one. Raises
    `FileNotFoundError` if the source file is missing.
    """
    favicon_src = Path(common_dir) / filename
    favicon_dst = Path(archive_path) / filename
    shutil.copyfile(str(favicon_src), str(favicon_dst))


def _finalize_combined_archive(all_archives, output_dir, version_slug, docc_cmd, prior_failed, common_dir=None, navigation=None, hosting_base_path=None, canonical_base_url=None):
    """Merge per-source archives, then post-process the combined archive.

    Returns `(succeeded_steps, failed_steps)`: names to add to the build
    summary's success and failure lists.

    Refuses to merge, and returns `([], ["combined-merge"])`, if any source
    already failed (`prior_failed`), `docc_cmd` is empty, or any archive in
    `all_archives` is missing. `all_archives` must not be empty.

    Otherwise it runs these steps in order, into `<output_dir>/<version_slug>`:

    1. `combined-merge`
    2. `navigator-curation`, only if `navigation` is not None
    3. `eyebrow-suppression`
    4. `language-toggle-suppression`
    5. `static-hosting-transform`, which bakes in `hosting_base_path`
       (default `version_slug`). If `common_dir` is not None, this step also
       patches the custom header and footer into the per-route stubs and
       restores the shared favicons.
    6. `canonical-link-injection`, only if `canonical_base_url` is set

    The first step that raises any exception stops the run. It is returned as
    the only failed step. Steps after it do not run and appear in neither
    list. The output directory then holds a half-processed archive that must
    not be published. The caller exits non-zero when the failed list is not
    empty.

    Replaces `<output_dir>/<version_slug>` if it exists. Prints progress.
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

    combined_output = output_dir / version_slug

    def merge():
        merge_archives(
            all_archives, combined_output, docc_cmd,
            landing_page_name="Swift Documentation",
        )

    def curate():
        curate_navigator(combined_output, navigation)
        print("Curated combined navigator per navigation.json.")

    def suppress_eyebrows():
        config = {
            "suppress": ((navigation or {}).get("eyebrows") or {}).get("suppress", False),
            "overrides": collect_eyebrow_overrides(navigation or {}),
        }
        scanned, modified = suppress_eyebrow_archive(combined_output, config)
        print(f"Eyebrow suppression: scanned {scanned} file(s), modified {modified}.")

    def strip_language_toggle():
        scanned, modified = strip_language_toggle_archive(combined_output)
        print(
            f"Language toggle suppression: scanned {scanned} file(s), "
            f"modified {modified}."
        )

    def transform_and_patch():
        transform_static_hosting(
            combined_output, hosting_base_path or version_slug, docc_cmd
        )
        if common_dir is None:
            return
        # Workaround for swiftlang/swift-docc#1532 — drop this when fixed.
        patched = inject_custom_templates_into_stubs(combined_output, common_dir)
        print(f"Patched custom-header/footer into {patched} per-route stub(s).")
        # Workaround: transform-for-static-hosting overwrites the favicons
        # with DocC's own defaults (see restore_custom_favicon docstring).
        restore_custom_favicon(combined_output, common_dir)
        restore_custom_favicon(combined_output, common_dir, FAVICON_SVG_FILE)
        print("Restored shared favicon.ico/favicon.svg after static-hosting transform.")

    def inject_canonical():
        scanned, modified = canonicalize_link_archive(combined_output, canonical_base_url)
        print(f"Canonical link injection: scanned {scanned} file(s), modified {modified}.")

    steps = [("combined-merge", merge)]
    if navigation is not None:
        steps.append(("navigator-curation", curate))
    steps += [
        ("eyebrow-suppression", suppress_eyebrows),
        ("language-toggle-suppression", strip_language_toggle),
        ("static-hosting-transform", transform_and_patch),
    ]
    if canonical_base_url:
        steps.append(("canonical-link-injection", inject_canonical))

    succeeded = []
    for name, run in steps:
        try:
            run()
        except Exception as e:
            print(f"Error: {name} failed: {e}")
            return succeeded, [name]
        succeeded.append(name)
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

    Keys outside `range(num_sources)` are ignored. Returns
    `(all_archives, manifest_entries)`.
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
    """Write `<output_dir>/build-manifest.json` and print its path.

    The file holds `{"version": version, "build_time": ..., "sources": entries}`.
    `version` is the `version` object from `sources.json`, and `entries` are
    the manifest entries from `build_source`. `build_time` is the UTC time of
    writing. Overwrites any existing file. `output_dir` must exist.
    """
    manifest = {
        "version": version,
        "build_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": entries,
    }
    manifest_path = output_dir / "build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Build manifest: {manifest_path}")


def build_all_sources(sources, build, only=None):
    """Build every source, sorting failures by severity.

    `build(source)` returns `(archives, manifest_entry)` or raises. Returns
    `(succeeded, failed, results_by_index)`. `succeeded` and `failed` list
    source ids in build order. `results_by_index` maps a source's position in
    `sources` to its `build` result, and holds only sources that built.

    Archive sources build first, so a network or extraction failure aborts the
    run before any slow git clone or swift build. Any exception from an
    archive source is fatal: it prints the error and calls `sys.exit(1)`. An
    exception from any other source is printed and recorded in `failed`, and
    the remaining sources still build. With `only`, every source whose id
    differs is skipped.
    """
    succeeded = []
    failed = []
    results_by_index = {}

    def attempt(index, source, fatal):
        sid = source["id"]
        if only and sid != only:
            return
        try:
            results_by_index[index] = build(source)
        except Exception as e:
            if fatal:
                print(f"Fatal: {e}")
                sys.exit(1)
            print(f"Error building {sid}: {e}")
            failed.append(sid)
            return
        succeeded.append(sid)

    for i, source in enumerate(sources):
        if source["type"] == "archive":
            attempt(i, source, fatal=True)
    for i, source in enumerate(sources):
        if source["type"] != "archive":
            attempt(i, source, fatal=False)
    return succeeded, failed, results_by_index


def _load_json(path, label):
    """Return the parsed JSON in `path`, or None after printing the problem."""
    if not path.is_file():
        print(f"Error: {label} not found at {path}")
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: {label} is not valid JSON: {e}")
        return None


def main(argv=None):
    """Build the combined documentation site and return the exit code.

    Pipeline: check tools and the `common/` templates, load and validate
    `sources.json` (and `navigation.json` if present), build every source
    with `build_all_sources`, then merge and post-process the results with
    `_finalize_combined_archive`. The merged archive goes to
    `<output-dir>/<slug>`, and `build-manifest.json` to `<output-dir>`.

    Returns 1 if a tool, template or config file is missing or invalid, if
    `--only` names no source in `sources.json`, or if any source or
    post-merge step failed. Returns 0 otherwise. `check_prerequisites` and a
    fatal archive failure call `sys.exit(1)` directly.

    With `--only <id>`, the function builds just that source, copies its
    archive to `<output-dir>`, skips the merge, and overwrites
    `build-manifest.json` with a one-entry manifest.
    """
    args = parse_args(argv)

    script_dir = Path(__file__).resolve().parent
    root_dir = script_dir.parent
    sources_file = script_dir / "sources.json"
    common_dir = root_dir / "common"

    output_dir = Path(args.output_dir) if args.output_dir else root_dir / ".build-output"
    workspace = Path(args.workspace) if args.workspace else root_dir / ".workspace"

    check_prerequisites()
    tools = discover_tools()

    # Validate common template files and the shared favicons exist
    for tmpl in TEMPLATE_FILES + [FAVICON_FILE, FAVICON_SVG_FILE]:
        tmpl_path = common_dir / tmpl
        if not tmpl_path.is_file():
            print(f"Error: common template '{tmpl}' not found at {tmpl_path}")
            return 1

    config = _load_json(sources_file, "sources.json")
    if config is None:
        return 1

    validate_errors = validate_sources(config)
    if validate_errors:
        for e in validate_errors:
            print(f"Validation error: {e}")
        print("\nFix the errors in sources.json before continuing.")
        return 1
    print(f"Validated {len(config['sources'])} source entries.")

    sources = config["sources"]
    if args.only and args.only not in {s["id"] for s in sources}:
        print(f"Error: --only '{args.only}' does not match any source id in sources.json")
        print(f"Known ids: {', '.join(s['id'] for s in sources)}")
        return 1

    # Load and validate the navigation manifest (drives combined-archive
    # sidebar curation). Validated up front so a mismatch fails fast.
    navigation_file = script_dir / "navigation.json"
    navigation = None
    if navigation_file.is_file():
        navigation = _load_json(navigation_file, "navigation.json")
        if navigation is None:
            return 1
        nav_errors = validate_navigation(navigation, config)
        if nav_errors:
            for e in nav_errors:
                print(f"Validation error: {e}")
            print("\nFix the errors in navigation.json before continuing.")
            return 1
        print("Validated navigation.json against sources.json.")
    else:
        print("No navigation.json found — combined navigator will not be curated.")

    version = config["version"]
    version_slug = version["slug"]
    hosting_base_path = f"{args.extra_hosting_prefix}/{version_slug}" if args.extra_hosting_prefix else version_slug
    canonical_base_url = _resolve_canonical_base_url(config)

    # Ensure consistent, pretty-printed DocC JSON output
    env = os.environ.copy()
    env["DOCC_JSON_PRETTYPRINT"] = "YES"

    if args.clean:
        print(f"Removing workspace: {workspace}")
        if workspace.exists():
            shutil.rmtree(str(workspace))
        for build_dir in clean_package_build_dirs(root_dir, sources):
            print(f"Removed package build dir: {build_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    # Intermediate archives live under the workspace until the merge is done.
    temp_archive_dir = workspace / "_archives"
    temp_archive_dir.mkdir(parents=True, exist_ok=True)

    succeeded, failed, results_by_index = build_all_sources(
        sources,
        lambda source: build_source(
            source, root_dir, workspace, common_dir,
            temp_archive_dir, tools.docc, env,
            swift_cmd=tools.swift,
            sources=sources,
        ),
        only=args.only,
    )

    # Build order differs from merge order (archive sources build first), so
    # reassemble by position to make the merge and manifest follow sources.json.
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
            all_archives, output_dir, version_slug, tools.docc, failed,
            common_dir=common_dir, navigation=navigation,
            hosting_base_path=hosting_base_path,
            canonical_base_url=canonical_base_url,
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

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
