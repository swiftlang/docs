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
"""Tests for build_docs.py.

Most tests call functions directly. The "end-to-end" classes at the end of
the file run the real build functions against temporary directories. They
replace `swift` and `docc` with small shell scripts that write the files the
real tools would write, and they do not patch `build_docs` internals.

Run from the repository root with:
    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import gzip
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_docs  # noqa: E402
import curate_navigator  # noqa: E402
import inject_canonical_link  # noqa: E402
import strip_availability  # noqa: E402
import strip_language_toggle  # noqa: E402
import suppress_eyebrows  # noqa: E402
import validate_navigation as validate_navigation_cli  # noqa: E402


def _validate(config):
    """Run validate_sources and return joined error text, or None if valid.

    Joining preserves the assertion shape used by existing tests
    (assertIn("url", output)) without needing per-error matching.
    """
    errors = build_docs.validate_sources(config)
    return "\n".join(errors) if errors else None


def _wrap(entry):
    return {
        "version": {"slug": "main"},
        "sources": [entry],
    }


class ValidateVersionField(unittest.TestCase):
    def _config(self, version):
        return {
            "version": version,
            "sources": [{
                "id": "stdlib",
                "type": "archive",
                "url": "https://example.com/Swift.doccarchive.tar.gz",
                "docc_archive_name": "Swift.doccarchive",
            }],
        }

    def test_valid_version_object(self):
        output = _validate(self._config({"slug": "main"}))
        self.assertIsNone(output)

    def test_version_missing_is_rejected(self):
        config = self._config({"slug": "main"})
        del config["version"]
        output = _validate(config)
        self.assertIsNotNone(output)
        self.assertIn("version", output)

    def test_version_as_plain_string_is_rejected(self):
        output = _validate(self._config("main"))
        self.assertIsNotNone(output)
        self.assertIn("version", output)

    def test_version_missing_slug_is_rejected(self):
        output = _validate(self._config({}))
        self.assertIsNotNone(output)
        self.assertIn("slug", output)


class ValidateCanonicalBaseUrlField(unittest.TestCase):
    """canonical_base_url is deliberately NOT validated by validate_sources():
    a malformed value should warn and disable canonical-link injection (see
    ResolveCanonicalBaseUrl below), not fail the whole documentation build.
    """

    def _config(self, canonical_base_url=None):
        config = {
            "version": {"slug": "main"},
            "sources": [{
                "id": "stdlib",
                "type": "archive",
                "url": "https://example.com/Swift.doccarchive.tar.gz",
                "docc_archive_name": "Swift.doccarchive",
            }],
        }
        if canonical_base_url is not None:
            config["canonical_base_url"] = canonical_base_url
        return config

    def test_omitted_is_valid(self):
        output = _validate(self._config())
        self.assertIsNone(output)

    def test_valid_https_url_is_accepted(self):
        output = _validate(self._config("https://docs.swift.org/latest"))
        self.assertIsNone(output)

    def test_malformed_value_does_not_fail_validation(self):
        for bad_value in ({"not": "a string"}, "", "docs.swift.org/latest"):
            with self.subTest(bad_value=bad_value):
                output = _validate(self._config(bad_value))
                self.assertIsNone(output)


class ResolveCanonicalBaseUrl(unittest.TestCase):
    """build_docs._resolve_canonical_base_url() is the single point that
    decides whether canonical-link injection runs. A missing field is silent;
    a malformed field prints an explicit warning (so it shows up in the build
    script's own output, and therefore in CI logs) and disables injection for
    this build rather than failing it outright.
    """

    def test_absent_returns_none_silently(self):
        with mock.patch("builtins.print") as mock_print:
            result = build_docs._resolve_canonical_base_url({})
        self.assertIsNone(result)
        mock_print.assert_not_called()

    def test_valid_https_url_is_returned(self):
        config = {"canonical_base_url": "https://docs.swift.org/latest"}
        with mock.patch("builtins.print") as mock_print:
            result = build_docs._resolve_canonical_base_url(config)
        self.assertEqual(result, "https://docs.swift.org/latest")
        mock_print.assert_not_called()

    def test_valid_http_url_is_returned(self):
        config = {"canonical_base_url": "http://example.com/latest"}
        result = build_docs._resolve_canonical_base_url(config)
        self.assertEqual(result, "http://example.com/latest")

    def test_malformed_value_warns_and_disables_injection(self):
        bad_values = [
            {"not": "a string"},
            "",
            "docs.swift.org/latest",
            "https://",
            "ftp://docs.swift.org/latest",
        ]
        for bad_value in bad_values:
            with self.subTest(canonical_base_url=bad_value):
                config = {"canonical_base_url": bad_value}
                with mock.patch("builtins.print") as mock_print:
                    result = build_docs._resolve_canonical_base_url(config)
                self.assertIsNone(result)
                mock_print.assert_called_once()
                warning = mock_print.call_args[0][0]
                self.assertIn("Warning", warning)
                self.assertIn("canonical_base_url", warning)


class ValidateArchiveType(unittest.TestCase):
    def test_minimal_valid(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_full_valid(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
            "format": "tar.gz",
            "version_label": "main",
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_missing_url(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "docc_archive_name": "Swift.doccarchive",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("url", output)

    def test_missing_docc_archive_name(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("docc_archive_name", output)

    def test_bad_docc_archive_name_suffix(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/foo.tar.gz",
            "docc_archive_name": "Swift",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn(".doccarchive", output)

    def test_unsupported_format(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.7z",
            "docc_archive_name": "Swift.doccarchive",
            "format": "7z",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("format", output)

    def test_rejects_mutually_exclusive_fields(self):
        base = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
        }
        disallowed = {
            "targets": ["Foo"],
            "docc_catalog": "Foo.docc",
            "path": "some/path",
            "repo": "https://example.com/repo.git",
            "ref": "main",
            "preflight": "echo hi",
            "add_docc_plugin": True,
            "extra_flags": ["--foo"],
            "additional_symbol_graph_dir": ".build/symbols/Foo",
        }
        for field, value in disallowed.items():
            with self.subTest(field=field):
                entry = dict(base)
                entry[field] = value
                output = _validate(_wrap(entry))
                self.assertIsNotNone(
                    output, f"expected rejection when '{field}' is set"
                )
                self.assertIn(field, output)

    def test_unknown_type_still_rejected(self):
        entry = {
            "id": "stdlib",
            "type": "remote",
            "url": "https://example.com/foo.tar.gz",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("unknown type", output)

    def test_strip_availability_bool_is_valid(self):
        for value in (True, False):
            with self.subTest(strip_availability=value):
                entry = {
                    "id": "stdlib",
                    "type": "archive",
                    "url": "https://example.com/Swift.doccarchive.zip",
                    "docc_archive_name": "Swift.doccarchive",
                    "format": "zip",
                    "strip_availability": value,
                }
                self.assertIsNone(_validate(_wrap(entry)))

    def test_strip_availability_non_bool_rejected(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
            "strip_availability": "yes",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("strip_availability", output)


class ValidateStripAvailabilityOnNonArchive(unittest.TestCase):
    def test_rejected_on_git_source(self):
        entry = {
            "id": "swift-book",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-book.git",
            "ref": "main",
            "docc_catalog": "TSPL.docc",
            "strip_availability": True,
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("strip_availability", output)

    def test_rejected_on_local_source(self):
        entry = {
            "id": "api-guidelines",
            "type": "local",
            "path": "api-guidelines",
            "targets": ["APIGuidelines"],
            "strip_availability": True,
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("strip_availability", output)


class ValidateStripLinuxAvailability(unittest.TestCase):
    def test_bool_is_valid_on_git(self):
        for value in (True, False):
            with self.subTest(strip_linux_availability=value):
                entry = {
                    "id": "swift-testing",
                    "type": "git",
                    "repo": "https://github.com/swiftlang/swift-testing.git",
                    "ref": "main",
                    "targets": ["Testing"],
                    "strip_linux_availability": value,
                }
                self.assertIsNone(_validate(_wrap(entry)))

    def test_bool_is_valid_on_local(self):
        entry = {
            "id": "api-guidelines",
            "type": "local",
            "path": "api-guidelines",
            "targets": ["APIGuidelines"],
            "strip_linux_availability": True,
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_non_bool_rejected(self):
        entry = {
            "id": "swift-testing",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-testing.git",
            "ref": "main",
            "targets": ["Testing"],
            "strip_linux_availability": "yes",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("strip_linux_availability", output)

    def test_rejected_on_archive_source(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
            "strip_linux_availability": True,
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("strip_linux_availability", output)


class ValidateAdditionalSymbolGraphDir(unittest.TestCase):
    def test_valid_with_docc_catalog_on_local(self):
        entry = {
            "id": "swift-foundation-essentials",
            "type": "local",
            "path": ".workspace/swift-foundation",
            "docc_catalog": "Sources/FoundationEssentials/FoundationEssentials.docc",
            "additional_symbol_graph_dir": ".build/symbols/FoundationEssentials",
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_valid_with_docc_catalog_on_git(self):
        entry = {
            "id": "swift-book",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-book.git",
            "ref": "main",
            "docc_catalog": "TSPL.docc",
            "additional_symbol_graph_dir": ".build/symbols/Foo",
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_rejected_on_archive_source(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
            "additional_symbol_graph_dir": ".build/symbols/Foo",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("additional_symbol_graph_dir", output)

    def test_rejected_with_targets(self):
        entry = {
            "id": "swift-testing",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-testing.git",
            "ref": "main",
            "targets": ["Testing"],
            "additional_symbol_graph_dir": ".build/symbols/Testing",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("additional_symbol_graph_dir", output)

    def test_non_string_rejected(self):
        entry = {
            "id": "swift-book",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-book.git",
            "ref": "main",
            "docc_catalog": "TSPL.docc",
            "additional_symbol_graph_dir": 123,
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("additional_symbol_graph_dir", output)

    def test_empty_string_rejected(self):
        entry = {
            "id": "swift-book",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-book.git",
            "ref": "main",
            "docc_catalog": "TSPL.docc",
            "additional_symbol_graph_dir": "",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("additional_symbol_graph_dir", output)


class ValidateExistingTypesStillWork(unittest.TestCase):
    """Regression: make sure local and git validation is unchanged."""

    def test_local_valid(self):
        entry = {
            "id": "api-guidelines",
            "type": "local",
            "path": "api-guidelines",
            "targets": ["APIGuidelines"],
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_git_valid_with_targets(self):
        entry = {
            "id": "swift-testing",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-testing.git",
            "ref": "main",
            "targets": ["Testing"],
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_git_valid_with_catalog(self):
        entry = {
            "id": "swift-book",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-book.git",
            "ref": "main",
            "docc_catalog": "TSPL.docc",
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_git_missing_ref_rejected(self):
        entry = {
            "id": "swift-book",
            "type": "git",
            "repo": "https://github.com/swiftlang/swift-book.git",
            "docc_catalog": "TSPL.docc",
        }
        output = _validate(_wrap(entry))
        self.assertIsNotNone(output)
        self.assertIn("ref", output)


def _make_tarball(tmp_path, archive_name, wrap_top_level=True, with_platforms=False):
    """Build a tar.gz containing a minimal .doccarchive directory.

    When wrap_top_level is True, the tarball's root entry is the .doccarchive
    directory itself; when False, its contents are at the tarball root.
    When with_platforms is True, seed a data/ directory with a JSON file that
    contains a metadata.platforms key (so strip_archive() has something to find).
    Returns the path to the tarball.
    """
    archive_dir = tmp_path / archive_name
    archive_dir.mkdir()
    (archive_dir / "metadata.json").write_text('{"bundleDisplayName":"Test"}')
    (archive_dir / "index").mkdir()
    (archive_dir / "index" / "index.json").write_text("{}")

    if with_platforms:
        data_dir = archive_dir / "data" / "documentation"
        data_dir.mkdir(parents=True)
        (data_dir / "x.json").write_text(
            json.dumps(
                {
                    "metadata": {
                        "title": "X",
                        "platforms": [{"name": "iOS"}],
                    },
                    "kind": "symbol",
                }
            )
        )

    tarball = tmp_path / f"{archive_name}.tar.gz"
    with tarfile.open(str(tarball), "w:gz") as tar:
        if wrap_top_level:
            tar.add(str(archive_dir), arcname=archive_name)
        else:
            for entry in archive_dir.rglob("*"):
                rel = entry.relative_to(archive_dir)
                tar.add(str(entry), arcname=str(rel))
    return tarball


class FetchArchive(unittest.TestCase):
    def test_successful_fetch_with_wrapped_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(tmp_path, "Swift.doccarchive")
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            source = {
                "id": "stdlib",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
            }
            result = build_docs.fetch_archive(source, workspace)
            self.assertTrue(result.is_dir())
            self.assertEqual(result.name, "Swift.doccarchive")
            self.assertTrue((result / "metadata.json").is_file())

    def test_successful_fetch_with_unwrapped_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(
                tmp_path, "Swift.doccarchive", wrap_top_level=False
            )
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            source = {
                "id": "stdlib",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
            }
            with self.assertRaises(build_docs.ArchiveFetchError):
                build_docs.fetch_archive(source, workspace)

    def test_download_failure_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            source = {
                "id": "stdlib",
                "url": "file:///no/such/path/archive.tar.gz",
                "docc_archive_name": "Swift.doccarchive",
            }
            with self.assertRaises(build_docs.ArchiveFetchError):
                build_docs.fetch_archive(source, workspace)

    def test_missing_docc_archive_name_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(tmp_path, "Other.doccarchive")
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            source = {
                "id": "stdlib",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
            }
            with self.assertRaises(build_docs.ArchiveFetchError):
                build_docs.fetch_archive(source, workspace)

    def test_rerun_wipes_previous_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(tmp_path, "Swift.doccarchive")
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            source = {
                "id": "stdlib",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
            }
            first = build_docs.fetch_archive(source, workspace)
            # Seed a stale file so we can verify re-entry wiped the directory.
            stale = first.parent.parent / "stale.txt"
            stale.write_text("stale")
            second = build_docs.fetch_archive(source, workspace)
            self.assertTrue(second.is_dir())
            self.assertFalse(stale.exists())


class BuildSourceArchiveBranch(unittest.TestCase):
    def test_archive_source_copies_to_temp_and_returns_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(tmp_path, "Swift.doccarchive")
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            temp_archive_dir = workspace / "_archives"
            temp_archive_dir.mkdir()
            source = {
                "id": "stdlib",
                "type": "archive",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
                "version_label": "main",
            }
            archives, manifest_entry = build_docs.build_source(
                source,
                root_dir=tmp_path,
                workspace=workspace,
                common_dir=tmp_path,
                temp_archive_dir=temp_archive_dir,
                docc_cmd=[],
                env={},
            )
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].name, "stdlib.doccarchive")
            self.assertTrue(archives[0].is_dir())
            self.assertTrue((archives[0] / "metadata.json").is_file())
            self.assertEqual(manifest_entry["id"], "stdlib")
            self.assertEqual(manifest_entry["type"], "archive")
            self.assertEqual(manifest_entry["ref"], "main")
            self.assertEqual(manifest_entry["commit"], "")
            self.assertEqual(manifest_entry["url"], tarball.as_uri())

    def test_archive_source_without_version_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(tmp_path, "Swift.doccarchive")
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            temp_archive_dir = workspace / "_archives"
            temp_archive_dir.mkdir()
            source = {
                "id": "stdlib",
                "type": "archive",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
            }
            _, manifest_entry = build_docs.build_source(
                source,
                root_dir=tmp_path,
                workspace=workspace,
                common_dir=tmp_path,
                temp_archive_dir=temp_archive_dir,
                docc_cmd=[],
                env={},
            )
            self.assertEqual(manifest_entry["ref"], "")

    def test_strip_availability_removes_platforms_from_copied_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(
                tmp_path, "Swift.doccarchive", with_platforms=True
            )
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            temp_archive_dir = workspace / "_archives"
            temp_archive_dir.mkdir()
            source = {
                "id": "stdlib",
                "type": "archive",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
                "strip_availability": True,
            }
            archives, _ = build_docs.build_source(
                source,
                root_dir=tmp_path,
                workspace=workspace,
                common_dir=tmp_path,
                temp_archive_dir=temp_archive_dir,
                docc_cmd=[],
                env={},
            )
            data_file = archives[0] / "data" / "documentation" / "x.json"
            payload = json.loads(data_file.read_text())
            self.assertNotIn("platforms", payload["metadata"])
            self.assertEqual(payload["metadata"]["title"], "X")

    def test_strip_availability_absent_leaves_platforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball = _make_tarball(
                tmp_path, "Swift.doccarchive", with_platforms=True
            )
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            temp_archive_dir = workspace / "_archives"
            temp_archive_dir.mkdir()
            source = {
                "id": "stdlib",
                "type": "archive",
                "url": tarball.as_uri(),
                "docc_archive_name": "Swift.doccarchive",
            }
            archives, _ = build_docs.build_source(
                source,
                root_dir=tmp_path,
                workspace=workspace,
                common_dir=tmp_path,
                temp_archive_dir=temp_archive_dir,
                docc_cmd=[],
                env={},
            )
            data_file = archives[0] / "data" / "documentation" / "x.json"
            payload = json.loads(data_file.read_text())
            self.assertIn("platforms", payload["metadata"])


def _make_archive_with_platforms(root, archive_name="Swift.doccarchive"):
    """Build a minimal .doccarchive tree on disk with platforms data sprinkled in.

    Returns the archive path. The tree contains data/ JSON files with both
    metadata.platforms and primaryContentSections[*].declarations[*].platforms,
    so strip_archive() should remove them and not touch unrelated keys.
    """
    archive = root / archive_name
    data_dir = archive / "data" / "documentation"
    data_dir.mkdir(parents=True)

    symbol = {
        "metadata": {
            "title": "FooSymbol",
            "platforms": [{"name": "iOS", "introducedAt": "13.0"}],
        },
        "primaryContentSections": [
            {
                "kind": "declarations",
                "declarations": [
                    {
                        "tokens": [{"text": "func foo()"}],
                        "platforms": ["macOS"],
                    }
                ],
            }
        ],
        "kind": "symbol",
    }
    (data_dir / "foosymbol.json").write_text(json.dumps(symbol))

    article = {
        "metadata": {"title": "Article"},
        "kind": "article",
    }
    (data_dir / "article.json").write_text(json.dumps(article))

    return archive


class StripArchive(unittest.TestCase):
    def test_removes_platforms_keys_and_returns_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_platforms(Path(tmp))
            scanned, modified, removed = strip_availability.strip_archive(archive)

            symbol = json.loads(
                (archive / "data" / "documentation" / "foosymbol.json").read_text()
            )
            self.assertNotIn("platforms", symbol["metadata"])
            self.assertNotIn(
                "platforms", symbol["primaryContentSections"][0]["declarations"][0]
            )
            # Unrelated keys preserved.
            self.assertEqual(symbol["metadata"]["title"], "FooSymbol")
            self.assertEqual(
                symbol["primaryContentSections"][0]["declarations"][0]["tokens"],
                [{"text": "func foo()"}],
            )

            self.assertEqual(scanned, 2)
            self.assertEqual(modified, 1)
            self.assertEqual(removed, 2)

    def test_archive_without_platforms_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Swift.doccarchive"
            data_dir = archive / "data"
            data_dir.mkdir(parents=True)
            payload = {"metadata": {"title": "X"}, "kind": "article"}
            (data_dir / "x.json").write_text(json.dumps(payload))

            scanned, modified, removed = strip_availability.strip_archive(archive)
            self.assertEqual(scanned, 1)
            self.assertEqual(modified, 0)
            self.assertEqual(removed, 0)
            self.assertEqual(
                json.loads((data_dir / "x.json").read_text()), payload
            )

    def test_missing_data_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_an_archive = Path(tmp) / "NotAnArchive"
            not_an_archive.mkdir()
            with self.assertRaises(ValueError):
                strip_availability.strip_archive(not_an_archive)


def _make_archive_with_linux_platform(root, archive_name="Testing.doccarchive"):
    """Build a minimal .doccarchive tree with a synthesized Linux platform entry.

    Mirrors what DocC produces for a package target whose symbol graph was
    extracted on a Linux toolchain: a "Linux" entry sits alongside the real
    "Swift"/"Xcode" version markers in metadata.platforms, and a plain-string
    "Linux" entry in a declaration's platforms list. strip_linux_availability()
    should remove only those Linux entries and leave the rest untouched.
    """
    archive = root / archive_name
    data_dir = archive / "data" / "documentation"
    data_dir.mkdir(parents=True)

    symbol = {
        "metadata": {
            "title": "FooSymbol",
            "platforms": [
                {"name": "Linux", "introducedAt": "6.2"},
                {"name": "Swift", "introducedAt": "6.2"},
                {"name": "Xcode", "introducedAt": "26.0"},
            ],
        },
        "primaryContentSections": [
            {
                "kind": "declarations",
                "declarations": [
                    {
                        "tokens": [{"text": "func foo()"}],
                        "platforms": ["Linux"],
                    }
                ],
            }
        ],
        "kind": "symbol",
    }
    (data_dir / "foosymbol.json").write_text(json.dumps(symbol))

    article = {
        "metadata": {"title": "Article"},
        "kind": "article",
    }
    (data_dir / "article.json").write_text(json.dumps(article))

    return archive


class StripLinuxAvailability(unittest.TestCase):
    def test_removes_only_linux_entries_and_returns_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_linux_platform(Path(tmp))
            scanned, modified, removed = strip_availability.strip_linux_availability(
                archive
            )

            symbol = json.loads(
                (archive / "data" / "documentation" / "foosymbol.json").read_text()
            )
            platform_names = {p["name"] for p in symbol["metadata"]["platforms"]}
            self.assertNotIn("Linux", platform_names)
            self.assertEqual(platform_names, {"Swift", "Xcode"})
            self.assertNotIn(
                "platforms", symbol["primaryContentSections"][0]["declarations"][0]
            )
            # Unrelated keys preserved.
            self.assertEqual(symbol["metadata"]["title"], "FooSymbol")
            self.assertEqual(
                symbol["primaryContentSections"][0]["declarations"][0]["tokens"],
                [{"text": "func foo()"}],
            )

            self.assertEqual(scanned, 2)
            self.assertEqual(modified, 1)
            self.assertEqual(removed, 2)

    def test_leaves_non_linux_declaration_platforms_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_platforms(Path(tmp))
            scanned, modified, removed = strip_availability.strip_linux_availability(
                archive
            )

            symbol = json.loads(
                (archive / "data" / "documentation" / "foosymbol.json").read_text()
            )
            self.assertEqual(
                symbol["metadata"]["platforms"], [{"name": "iOS", "introducedAt": "13.0"}]
            )
            self.assertEqual(
                symbol["primaryContentSections"][0]["declarations"][0]["platforms"],
                ["macOS"],
            )
            self.assertEqual(modified, 0)
            self.assertEqual(removed, 0)

    def test_archive_without_platforms_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Testing.doccarchive"
            data_dir = archive / "data"
            data_dir.mkdir(parents=True)
            payload = {"metadata": {"title": "X"}, "kind": "article"}
            (data_dir / "x.json").write_text(json.dumps(payload))

            scanned, modified, removed = strip_availability.strip_linux_availability(
                archive
            )
            self.assertEqual(scanned, 1)
            self.assertEqual(modified, 0)
            self.assertEqual(removed, 0)
            self.assertEqual(
                json.loads((data_dir / "x.json").read_text()), payload
            )


def _make_archive_with_language_variants(root, archive_name="Swift.doccarchive"):
    """Build a minimal .doccarchive tree with a module page carrying the
    top-level `variants` array that drives the "Language: Swift" nav pill,
    a docc-convert-style page with none, and an image reference whose own
    `variants` key (light/dark asset variants) must survive untouched.

    Returns the archive path.
    """
    archive = root / archive_name
    doc_dir = archive / "data" / "documentation"
    doc_dir.mkdir(parents=True)

    (doc_dir / "testing.json").write_text(json.dumps({
        "metadata": {"title": "Testing", "role": "collection"},
        "variants": [
            {"paths": ["/documentation/testing"],
             "traits": [{"interfaceLanguage": "swift"}]},
        ],
        "references": {
            "hero.png": {
                "type": "image",
                "variants": [{"traits": ["light"], "url": "/images/hero.png"}],
            },
        },
    }))
    (doc_dir / "userdocs.json").write_text(json.dumps({
        "metadata": {"title": "VS Code", "role": "collection"},
    }))

    return archive


class StripLanguageToggleArchive(unittest.TestCase):
    def test_removes_top_level_variants_and_returns_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_language_variants(Path(tmp))
            scanned, modified = strip_language_toggle.strip_archive(archive)

            testing = json.loads(
                (archive / "data" / "documentation" / "testing.json").read_text()
            )
            self.assertNotIn("variants", testing)
            # Unrelated keys preserved.
            self.assertEqual(testing["metadata"]["title"], "Testing")

            self.assertEqual(scanned, 2)
            self.assertEqual(modified, 1)

    def test_nested_asset_variants_are_not_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_language_variants(Path(tmp))
            strip_language_toggle.strip_archive(archive)

            testing = json.loads(
                (archive / "data" / "documentation" / "testing.json").read_text()
            )
            self.assertIn("variants", testing["references"]["hero.png"])
            self.assertEqual(
                testing["references"]["hero.png"]["variants"],
                [{"traits": ["light"], "url": "/images/hero.png"}],
            )

    def test_archive_without_variants_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Swift.doccarchive"
            data_dir = archive / "data"
            data_dir.mkdir(parents=True)
            payload = {"metadata": {"title": "X"}, "kind": "article"}
            (data_dir / "x.json").write_text(json.dumps(payload))

            scanned, modified = strip_language_toggle.strip_archive(archive)
            self.assertEqual(scanned, 1)
            self.assertEqual(modified, 0)
            self.assertEqual(
                json.loads((data_dir / "x.json").read_text()), payload
            )

    def test_missing_data_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_an_archive = Path(tmp) / "NotAnArchive"
            not_an_archive.mkdir()
            with self.assertRaises(ValueError):
                strip_language_toggle.strip_archive(not_an_archive)


def _make_transformed_archive(root, archive_name="Combined.doccarchive"):
    """Build a minimal post-transform-for-static-hosting archive tree: a root
    index.html stub and two nested per-route stubs, mirroring the shape
    `docc process-archive transform-for-static-hosting` produces (one
    index.html per route, sharing the same generic <head>).

    Returns the archive path.
    """
    archive = root / archive_name
    stub = "<!doctype html><html><head><title>Documentation</title></head><body></body></html>"

    (archive).mkdir(parents=True)
    (archive / "index.html").write_text(stub)

    doc_dir = archive / "documentation"
    doc_dir.mkdir()
    (doc_dir / "index.html").write_text(stub)

    nested_dir = doc_dir / "wasmguide"
    nested_dir.mkdir()
    (nested_dir / "index.html").write_text(stub)

    return archive


class CanonicalizeArchive(unittest.TestCase):
    def test_injects_canonical_link_for_root_and_nested_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_transformed_archive(Path(tmp))

            scanned, modified = inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            root_html = (archive / "index.html").read_text()
            doc_html = (archive / "documentation" / "index.html").read_text()
            nested_html = (
                archive / "documentation" / "wasmguide" / "index.html"
            ).read_text()

            self.assertIn(
                '<link rel="canonical" href="https://docs.swift.org/latest">',
                root_html,
            )
            self.assertIn(
                '<link rel="canonical" '
                'href="https://docs.swift.org/latest/documentation">',
                doc_html,
            )
            self.assertIn(
                '<link rel="canonical" href="https://docs.swift.org/latest'
                '/documentation/wasmguide">',
                nested_html,
            )
            self.assertEqual(scanned, 3)
            self.assertEqual(modified, 3)

    def test_trailing_slash_on_base_url_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_transformed_archive(Path(tmp))

            inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest/"
            )

            root_html = (archive / "index.html").read_text()
            self.assertIn(
                '<link rel="canonical" href="https://docs.swift.org/latest">',
                root_html,
            )

    def test_replaces_existing_canonical_link_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_transformed_archive(Path(tmp))
            stale = (
                "<!doctype html><html><head><title>Documentation</title>"
                '<link rel="canonical" href="https://docs.swift.org/main">'
                "</head><body></body></html>"
            )
            (archive / "index.html").write_text(stale)

            scanned, modified = inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            root_html = (archive / "index.html").read_text()
            self.assertEqual(
                root_html.count('rel="canonical"'), 1,
                "must replace the stale tag, not add a second one",
            )
            self.assertIn(
                '<link rel="canonical" href="https://docs.swift.org/latest">',
                root_html,
            )
            self.assertNotIn("docs.swift.org/main", root_html)
            self.assertEqual(scanned, 3)
            self.assertEqual(modified, 3)

    def test_rerun_with_unchanged_url_reports_no_further_modification(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_transformed_archive(Path(tmp))
            inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            scanned, modified = inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            self.assertEqual(scanned, 3)
            self.assertEqual(modified, 0)

    def test_route_segment_is_html_escaped_in_href(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "Combined.doccarchive"
            archive.mkdir()
            stub = (
                "<!doctype html><html><head><title>Documentation</title>"
                "</head><body></body></html>"
            )
            (archive / "index.html").write_text(stub)
            odd_dir = archive / 'a&b'
            odd_dir.mkdir()
            (odd_dir / "index.html").write_text(stub)

            inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            odd_html = (odd_dir / "index.html").read_text()
            self.assertIn(
                '<link rel="canonical" '
                'href="https://docs.swift.org/latest/a&amp;b">',
                odd_html,
            )

    def test_file_without_head_close_tag_is_scanned_but_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_transformed_archive(Path(tmp))
            (archive / "index.html").write_text("<html><body>no head here</body></html>")

            scanned, modified = inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            self.assertEqual(scanned, 3)
            self.assertEqual(modified, 2)
            self.assertEqual(
                (archive / "index.html").read_text(),
                "<html><body>no head here</body></html>",
            )

    def test_missing_root_index_html_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_an_archive = Path(tmp) / "NotAnArchive"
            not_an_archive.mkdir()
            with self.assertRaises(ValueError):
                inject_canonical_link.canonicalize_archive(
                    not_an_archive, "https://docs.swift.org/latest"
                )

    def test_non_ascii_content_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Combined.doccarchive"
            archive.mkdir()
            stub = (
                "<!doctype html><html><head>"
                "<title>Café Documentation — wasm→native</title>"
                "</head><body></body></html>"
            )
            (archive / "index.html").write_text(stub, encoding="utf-8")

            scanned, modified = inject_canonical_link.canonicalize_archive(
                archive, "https://docs.swift.org/latest"
            )

            root_html = (archive / "index.html").read_text(encoding="utf-8")
            self.assertEqual(scanned, 1)
            self.assertEqual(modified, 1)
            self.assertIn("Café Documentation — wasm→native", root_html)
            self.assertIn(
                '<link rel="canonical" href="https://docs.swift.org/latest">',
                root_html,
            )

    def test_write_failure_leaves_original_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_transformed_archive(Path(tmp))
            original = (archive / "index.html").read_text()

            with mock.patch.object(
                inject_canonical_link.os, "replace", side_effect=OSError("boom")
            ):
                with self.assertRaises(OSError):
                    inject_canonical_link.canonicalize_archive(
                        archive, "https://docs.swift.org/latest"
                    )

            self.assertEqual((archive / "index.html").read_text(), original)
            leftover_temp_files = [
                p for p in archive.iterdir() if p.name.startswith(".")
            ]
            self.assertEqual(
                leftover_temp_files, [],
                "a failed write must not leave a stray temp file behind",
            )


class InjectCanonicalLinkCLI(unittest.TestCase):
    def test_os_error_from_canonicalize_archive_exits_cleanly(self):
        argv = ["inject_canonical_link.py", "/some/archive", "https://docs.swift.org/latest"]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(
                inject_canonical_link, "canonicalize_archive",
                side_effect=OSError("boom"),
            ):
                with mock.patch.object(sys, "stderr") as mock_stderr:
                    with self.assertRaises(SystemExit) as cm:
                        inject_canonical_link.main()
        self.assertEqual(cm.exception.code, 1)
        written = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
        self.assertIn("error:", written)
        self.assertIn("boom", written)


def _make_archive_with_collections(root, archive_name="Combined.doccarchive"):
    """Build a minimal merged .doccarchive tree with two module landing pages,
    the synthesized combined landing page, and one non-collection page.

    Returns the archive path.
    """
    archive = root / archive_name
    doc_dir = archive / "data" / "documentation"
    doc_dir.mkdir(parents=True)

    (archive / "data" / "documentation.json").write_text(json.dumps({
        "metadata": {"title": "Swift Documentation", "role": "collection",
                     "roleHeading": "Project"},
    }))
    (doc_dir / "swift.json").write_text(json.dumps({
        "metadata": {"title": "Swift", "role": "collection",
                     "roleHeading": "Framework"},
    }))
    (doc_dir / "userdocs.json").write_text(json.dumps({
        "metadata": {"title": "VS Code", "role": "collection", "roleHeading": ""},
    }))
    (doc_dir / "closedrange.json").write_text(json.dumps({
        "metadata": {"title": "ClosedRange", "role": "symbol",
                     "roleHeading": "Structure"},
    }))

    return archive


class SuppressEyebrowsArchive(unittest.TestCase):
    def test_no_config_is_a_true_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_collections(Path(tmp))
            before = (archive / "data" / "documentation" / "swift.json").read_text()

            scanned, modified = suppress_eyebrows.suppress_archive(archive)

            self.assertEqual((scanned, modified), (0, 0))
            after = (archive / "data" / "documentation" / "swift.json").read_text()
            self.assertEqual(before, after)

    def test_suppress_true_blanks_only_collection_roleheadings(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_collections(Path(tmp))

            scanned, modified = suppress_eyebrows.suppress_archive(
                archive, {"suppress": True}
            )

            landing = json.loads(
                (archive / "data" / "documentation.json").read_text()
            )
            swift = json.loads(
                (archive / "data" / "documentation" / "swift.json").read_text()
            )
            symbol = json.loads(
                (archive / "data" / "documentation" / "closedrange.json").read_text()
            )
            self.assertEqual(landing["metadata"]["roleHeading"], "")
            self.assertEqual(swift["metadata"]["roleHeading"], "")
            # Non-collection pages are left untouched.
            self.assertEqual(symbol["metadata"]["roleHeading"], "Structure")
            self.assertEqual(scanned, 4)
            # userdocs.json was already "", landing + swift actually changed.
            self.assertEqual(modified, 2)

    def test_override_wins_over_suppress(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_collections(Path(tmp))

            suppress_eyebrows.suppress_archive(archive, {
                "suppress": True,
                "overrides": {"/documentation/swift": "Standard Library"},
            })

            swift = json.loads(
                (archive / "data" / "documentation" / "swift.json").read_text()
            )
            landing = json.loads(
                (archive / "data" / "documentation.json").read_text()
            )
            self.assertEqual(swift["metadata"]["roleHeading"], "Standard Library")
            self.assertEqual(landing["metadata"]["roleHeading"], "")

    def test_override_applies_without_global_suppress(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive_with_collections(Path(tmp))

            suppress_eyebrows.suppress_archive(archive, {
                "overrides": {"/documentation": ""},
            })

            landing = json.loads(
                (archive / "data" / "documentation.json").read_text()
            )
            swift = json.loads(
                (archive / "data" / "documentation" / "swift.json").read_text()
            )
            self.assertEqual(landing["metadata"]["roleHeading"], "")
            # Untouched: not overridden and global suppress is off.
            self.assertEqual(swift["metadata"]["roleHeading"], "Framework")

    def test_missing_data_dir_raises_when_config_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_an_archive = Path(tmp) / "NotAnArchive"
            not_an_archive.mkdir()
            with self.assertRaises(ValueError):
                suppress_eyebrows.suppress_archive(
                    not_an_archive, {"suppress": True}
                )

    def test_missing_data_dir_is_ignored_when_config_is_inactive(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_an_archive = Path(tmp) / "NotAnArchive"
            not_an_archive.mkdir()
            scanned, modified = suppress_eyebrows.suppress_archive(not_an_archive)
            self.assertEqual((scanned, modified), (0, 0))


class TransformStaticHosting(unittest.TestCase):
    def test_raises_when_docc_cmd_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "main.doccarchive"
            archive.mkdir()
            with self.assertRaises(RuntimeError):
                build_docs.transform_static_hosting(archive, "main", [])


class DiscoverTools(unittest.TestCase):
    def test_swift_in_path_sets_swift_cmd(self):
        present = {"swift": "/usr/bin/swift"}
        with mock.patch.object(build_docs.shutil, "which", side_effect=present.get):
            tools = build_docs.discover_tools()
        self.assertEqual(tools.swift, ["swift"])
        self.assertEqual(tools.docc, [])

    def test_xcrun_finds_docc(self):
        present = {"swift": "/usr/bin/swift", "xcrun": "/usr/bin/xcrun"}

        def fake_run(cmd, **kw):
            self.assertEqual(cmd[:2], ["xcrun", "--find"])
            return subprocess.CompletedProcess(cmd, 0, stdout="/path/docc\n", stderr="")

        with mock.patch.object(build_docs.shutil, "which", side_effect=present.get):
            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                tools = build_docs.discover_tools()
        self.assertEqual(tools.docc, ["xcrun", "docc"])

    def test_falls_back_to_path_docc_when_xcrun_fails(self):
        present = {
            "swift": "/usr/bin/swift",
            "xcrun": "/usr/bin/xcrun",
            "docc": "/usr/local/bin/docc",
        }

        def fake_run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(build_docs.shutil, "which", side_effect=present.get):
            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                tools = build_docs.discover_tools()
        self.assertEqual(tools.docc, ["docc"])

    def test_nothing_found_returns_empty_lists(self):
        with mock.patch.object(build_docs.shutil, "which", return_value=None):
            tools = build_docs.discover_tools()
        self.assertEqual(tools.swift, [])
        self.assertEqual(tools.docc, [])


class CollectGitMetadata(unittest.TestCase):
    def test_configured_ref_returned_verbatim_with_commit(self):
        def fake_run(cmd, **kw):
            self.assertIn("rev-parse", cmd)
            self.assertIn("HEAD", cmd)
            self.assertNotIn("--abbrev-ref", cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            ref, commit = build_docs._collect_git_metadata(
                Path("/tmp/x"), configured_ref="release/6.1"
            )
        self.assertEqual(ref, "release/6.1")
        self.assertEqual(commit, "abc123")

    def test_configured_ref_kept_when_git_fails(self):
        def fake_run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            ref, commit = build_docs._collect_git_metadata(
                Path("/tmp/x"), configured_ref="main"
            )
        self.assertEqual(ref, "main")
        self.assertEqual(commit, "unknown")

    def test_no_configured_ref_reads_both_from_git(self):
        outputs = iter([
            subprocess.CompletedProcess([], 0, stdout="feature-x\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="def456\n", stderr=""),
        ])

        def fake_run(cmd, **kw):
            return next(outputs)

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            ref, commit = build_docs._collect_git_metadata(Path("/tmp/x"))
        self.assertEqual(ref, "feature-x")
        self.assertEqual(commit, "def456")

    def test_no_configured_ref_returns_unknown_on_git_failure(self):
        def fake_run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            ref, commit = build_docs._collect_git_metadata(Path("/tmp/x"))
        self.assertEqual(ref, "unknown")
        self.assertEqual(commit, "unknown")


class DoccPluginResolves(unittest.TestCase):
    def test_true_when_dependency_present(self):
        def fake_run(cmd, **kw):
            payload = json.dumps({
                "dependencies": [
                    {"url": "https://github.com/swiftlang/swift-docc-plugin"},
                ]
            })
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            self.assertTrue(
                build_docs._docc_plugin_resolves(Path("/tmp/x"), ["swift"])
            )

    def test_false_when_dependency_absent(self):
        def fake_run(cmd, **kw):
            payload = json.dumps({
                "dependencies": [
                    {"url": "https://github.com/swiftlang/swift-syntax.git"},
                ]
            })
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            self.assertFalse(
                build_docs._docc_plugin_resolves(Path("/tmp/x"), ["swift"])
            )

    def test_false_when_describe_fails(self):
        def fake_run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            self.assertFalse(
                build_docs._docc_plugin_resolves(Path("/tmp/x"), ["swift"])
            )

    def test_false_when_describe_output_is_not_json(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

        with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
            self.assertFalse(
                build_docs._docc_plugin_resolves(Path("/tmp/x"), ["swift"])
            )


class AddDoccPlugin(unittest.TestCase):
    def _describe_result(self, cmd, has_plugin):
        deps = [{"url": "https://github.com/swiftlang/swift-syntax.git"}]
        if has_plugin:
            deps.append({"url": "https://github.com/swiftlang/swift-docc-plugin"})
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"dependencies": deps}), stderr=""
        )

    def test_skips_when_already_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / "Package.swift").write_text(
                'dependencies: [\n    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0"),\n],\n'
            )
            with mock.patch.object(build_docs.subprocess, "run") as run:
                build_docs.add_docc_plugin(source_dir, ["swift"])
            run.assert_not_called()

    def test_no_relocation_needed_when_plugin_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            package_swift = source_dir / "Package.swift"
            package_swift.write_text(
                'dependencies: [\n'
                '    .package(url: "https://example.com/a.git", from: "1.0.0"),\n'
                '],\n'
            )

            def fake_run(cmd, **kw):
                if "add-dependency" in cmd:
                    text = package_swift.read_text()
                    text = text.replace(
                        '.package(url: "https://example.com/a.git", from: "1.0.0"),',
                        '.package(url: "https://example.com/a.git", from: "1.0.0"),\n'
                        '    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0"),',
                    )
                    package_swift.write_text(text)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return self._describe_result(cmd, has_plugin=True)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                build_docs.add_docc_plugin(source_dir, ["swift"])

            final = package_swift.read_text()
            self.assertEqual(final.count("swift-docc-plugin"), 1)

    def test_relocates_when_added_into_inactive_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            package_swift = source_dir / "Package.swift"
            package_swift.write_text(
                'dependencies: cond ? [\n'
                '    .package(url: "https://example.com/active.git", from: "1.0.0"),\n'
                '] : [\n'
                '    .package(path: "../active"),\n'
                '],\n'
            )
            describe_results = iter([False, True])

            def fake_run(cmd, **kw):
                if "add-dependency" in cmd:
                    text = package_swift.read_text()
                    text = text.replace(
                        '.package(path: "../active"),',
                        '.package(path: "../active"),\n'
                        '    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0"),',
                    )
                    package_swift.write_text(text)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return self._describe_result(cmd, has_plugin=next(describe_results))

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                build_docs.add_docc_plugin(source_dir, ["swift"])

            final = package_swift.read_text()
            self.assertEqual(final.count("swift-docc-plugin"), 1)
            self.assertLess(
                final.index("swift-docc-plugin"), final.index("../active"),
                "plugin dependency should have been relocated into the first array",
            )

    def test_raises_when_still_unresolved_after_relocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            package_swift = source_dir / "Package.swift"
            package_swift.write_text(
                'dependencies: cond ? [\n'
                '    .package(url: "https://example.com/active.git", from: "1.0.0"),\n'
                '] : [\n'
                '    .package(path: "../active"),\n'
                '],\n'
            )

            def fake_run(cmd, **kw):
                if "add-dependency" in cmd:
                    text = package_swift.read_text()
                    text = text.replace(
                        '.package(path: "../active"),',
                        '.package(path: "../active"),\n'
                        '    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0"),',
                    )
                    package_swift.write_text(text)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return self._describe_result(cmd, has_plugin=False)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    build_docs.add_docc_plugin(source_dir, ["swift"])

    def test_raises_when_added_line_not_isolatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            package_swift = source_dir / "Package.swift"
            package_swift.write_text('dependencies: [\n],\n')

            def fake_run(cmd, **kw):
                if "add-dependency" in cmd:
                    package_swift.write_text(
                        'dependencies: [\n'
                        '    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0"),\n'
                        '    // swift-docc-plugin duplicate mention\n'
                        '],\n'
                    )
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return self._describe_result(cmd, has_plugin=False)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    build_docs.add_docc_plugin(source_dir, ["swift"])


class FinalizeCombinedArchive(unittest.TestCase):
    def test_prior_failures_skip_merge(self):
        succeeded, failed = build_docs._finalize_combined_archive(
            [], Path("/tmp"), "main", ["docc"], prior_failed=["foo", "bar"]
        )
        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["combined-merge"])

    def test_missing_docc_skips_merge(self):
        succeeded, failed = build_docs._finalize_combined_archive(
            [], Path("/tmp"), "main", [], prior_failed=[]
        )
        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["combined-merge"])

    def test_missing_archive_dir_skips_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ghost = tmp_path / "doesnotexist.doccarchive"
            succeeded, failed = build_docs._finalize_combined_archive(
                [ghost], tmp_path, "main", ["docc"], prior_failed=[]
            )
        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["combined-merge"])

class RenderCommonTemplate(unittest.TestCase):
    def test_substitutes_copyright_year_placeholder(self):
        rendered = build_docs.render_common_template(
            "Copyright (c) {{COPYRIGHT_YEAR}} Apple Inc.", year=2031
        )
        self.assertEqual(rendered, "Copyright (c) 2031 Apple Inc.")

    def test_defaults_to_current_utc_year_when_not_given(self):
        from datetime import datetime, timezone

        expected_year = datetime.now(timezone.utc).year
        rendered = build_docs.render_common_template("{{COPYRIGHT_YEAR}}")
        self.assertEqual(rendered, str(expected_year))

    def test_no_op_when_placeholder_absent(self):
        rendered = build_docs.render_common_template("no placeholder here", year=2031)
        self.assertEqual(rendered, "no placeholder here")

    def test_substitutes_multiple_occurrences(self):
        rendered = build_docs.render_common_template(
            "{{COPYRIGHT_YEAR}} and {{COPYRIGHT_YEAR}}", year=2031
        )
        self.assertEqual(rendered, "2031 and 2031")


class InstallTemplates(unittest.TestCase):
    def test_substitutes_copyright_year_in_installed_footer(self):
        from datetime import datetime, timezone

        expected_year = datetime.now(timezone.utc).year
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            (common / "header.html").write_text("HDR")
            (common / "footer.html").write_text("Copyright {{COPYRIGHT_YEAR}}")
            (common / "favicon.ico").write_bytes(b"ICO")
            catalog = root / "Foo.docc"
            catalog.mkdir()
            build_docs.install_templates(catalog, common, "foo")
            self.assertEqual(
                (catalog / "footer.html").read_text(), f"Copyright {expected_year}"
            )
            self.assertEqual((catalog / "header.html").read_text(), "HDR")

    def test_warns_and_overwrites_existing_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            (common / "header.html").write_text("HDR")
            (common / "footer.html").write_text("FTR")
            (common / "favicon.ico").write_bytes(b"ICO")
            catalog = root / "Foo.docc"
            catalog.mkdir()
            (catalog / "footer.html").write_text("stale")
            build_docs.install_templates(catalog, common, "foo")
            self.assertEqual((catalog / "footer.html").read_text(), "FTR")

    def _write_common_templates(self, common):
        (common / "header.html").write_text("HDR")
        (common / "footer.html").write_text("FTR")

    def test_copies_favicon_byte_for_byte(self):
        favicon_bytes = bytes(range(256))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            self._write_common_templates(common)
            (common / "favicon.ico").write_bytes(favicon_bytes)
            catalog = root / "Foo.docc"
            catalog.mkdir()
            build_docs.install_templates(catalog, common, "foo")
            self.assertEqual((catalog / "favicon.ico").read_bytes(), favicon_bytes)

    def test_warns_and_overwrites_existing_favicon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            self._write_common_templates(common)
            (common / "favicon.ico").write_bytes(b"NEW")
            catalog = root / "Foo.docc"
            catalog.mkdir()
            (catalog / "favicon.ico").write_bytes(b"stale")
            build_docs.install_templates(catalog, common, "foo")
            self.assertEqual((catalog / "favicon.ico").read_bytes(), b"NEW")


class InjectCustomTemplatesIntoStubs(unittest.TestCase):
    """Workaround for swiftlang/swift-docc#1532: see build_docs function docstring."""

    ANCHOR = '<body data-color-scheme="auto">'

    def _make_archive(self, root, header_text, footer_text):
        """Build a fake archive tree with a templated root and stripped subroute stubs."""
        archive = root / "main"
        archive.mkdir()
        # Common templates the function reads from.
        common = root / "common"
        common.mkdir()
        (common / "header.html").write_text(header_text)
        (common / "footer.html").write_text(footer_text)
        # Root index.html already contains the templates (from docc convert/merge).
        root_index = archive / "index.html"
        root_index.write_text(
            f'<!doctype html><html><body data-color-scheme="auto">'
            f'<template id="custom-footer">{footer_text}</template>'
            f'<template id="custom-header">{header_text}</template>'
            f'<div id="app"></div></body></html>'
        )
        # Stripped subroute stubs from transform-for-static-hosting.
        for sub in (
            "documentation/index.html",
            "documentation/foo/index.html",
            "documentation/foo/bar/index.html",
            "tutorials/baz/index.html",
        ):
            sub_path = archive / sub
            sub_path.parent.mkdir(parents=True, exist_ok=True)
            sub_path.write_text(
                '<!doctype html><html><body data-color-scheme="auto">'
                '<div id="app"></div></body></html>'
            )
        return archive, common

    def test_patches_subroute_stubs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, common = self._make_archive(Path(tmp), "HDR", "FTR")
            patched = build_docs.inject_custom_templates_into_stubs(archive, common)
            self.assertEqual(patched, 4)
            for sub in (
                "documentation/index.html",
                "documentation/foo/index.html",
                "documentation/foo/bar/index.html",
                "tutorials/baz/index.html",
            ):
                text = (archive / sub).read_text()
                self.assertIn('<template id="custom-header">HDR</template>', text)
                self.assertIn('<template id="custom-footer">FTR</template>', text)

    def test_root_index_is_left_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, common = self._make_archive(Path(tmp), "HDR", "FTR")
            before = (archive / "index.html").read_text()
            build_docs.inject_custom_templates_into_stubs(archive, common)
            self.assertEqual((archive / "index.html").read_text(), before)

    def test_idempotent_when_run_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, common = self._make_archive(Path(tmp), "HDR", "FTR")
            first = build_docs.inject_custom_templates_into_stubs(archive, common)
            after_first = {
                sub: (archive / sub).read_text()
                for sub in ("documentation/index.html", "tutorials/baz/index.html")
            }
            second = build_docs.inject_custom_templates_into_stubs(archive, common)
            self.assertEqual(first, 4)
            self.assertEqual(second, 0)
            for sub, text in after_first.items():
                self.assertEqual((archive / sub).read_text(), text)

    def test_skips_files_without_anchor(self):
        """If DocC ever fixes #1532 and emits a different shell shape, leave it alone."""
        with tempfile.TemporaryDirectory() as tmp:
            archive, common = self._make_archive(Path(tmp), "HDR", "FTR")
            no_anchor = archive / "documentation" / "no-anchor" / "index.html"
            no_anchor.parent.mkdir(parents=True, exist_ok=True)
            original = "<!doctype html><html><body><div id=\"app\"></div></body></html>"
            no_anchor.write_text(original)
            build_docs.inject_custom_templates_into_stubs(archive, common)
            self.assertEqual(no_anchor.read_text(), original)

    def test_no_subroute_stubs_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "main"
            archive.mkdir()
            (archive / "index.html").write_text(
                '<body data-color-scheme="auto">'
                '<template id="custom-header">HDR</template>'
                '<template id="custom-footer">FTR</template></body>'
            )
            common = Path(tmp) / "common"
            common.mkdir()
            (common / "header.html").write_text("HDR")
            (common / "footer.html").write_text("FTR")
            patched = build_docs.inject_custom_templates_into_stubs(archive, common)
            self.assertEqual(patched, 0)

    def test_substitutes_copyright_year_placeholder_in_stubs(self):
        from datetime import datetime, timezone

        expected_year = datetime.now(timezone.utc).year
        with tempfile.TemporaryDirectory() as tmp:
            archive, common = self._make_archive(
                Path(tmp), "HDR", "Copyright {{COPYRIGHT_YEAR}}"
            )
            build_docs.inject_custom_templates_into_stubs(archive, common)
            text = (archive / "documentation" / "index.html").read_text()
            self.assertIn(
                f'<template id="custom-footer">Copyright {expected_year}</template>',
                text,
            )

    def test_template_ordering_matches_docc_convert(self):
        """custom-footer comes before custom-header — same order docc convert emits."""
        with tempfile.TemporaryDirectory() as tmp:
            archive, common = self._make_archive(Path(tmp), "HDR", "FTR")
            build_docs.inject_custom_templates_into_stubs(archive, common)
            text = (archive / "documentation" / "index.html").read_text()
            footer_pos = text.index('<template id="custom-footer">')
            header_pos = text.index('<template id="custom-header">')
            self.assertLess(footer_pos, header_pos)


class RestoreCustomFavicon(unittest.TestCase):
    """Workaround for `docc process-archive transform-for-static-hosting`
    unconditionally overwriting favicon.ico with its own bundled default
    (swift-docc's TransformForStaticHostingAction copies every file from its
    HTML template directory over the output, with no exclusion for
    favicon.ico)."""

    def test_overwrites_transformed_favicon_with_shared_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            (common / "favicon.ico").write_bytes(b"SWIFT-FAVICON")
            archive = root / "main"
            archive.mkdir()
            (archive / "favicon.ico").write_bytes(b"DOCC-DEFAULT-GLOBE")
            build_docs.restore_custom_favicon(archive, common)
            self.assertEqual(
                (archive / "favicon.ico").read_bytes(), b"SWIFT-FAVICON"
            )

    def test_restores_favicon_svg_when_filename_given(self):
        """favicon.svg has no DocC catalog-level override at all (unlike
        favicon.ico), so this post-transform restore is the only place it
        can ever be fixed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            (common / "favicon.svg").write_bytes(b"SWIFT-FAVICON-SVG")
            archive = root / "main"
            archive.mkdir()
            (archive / "favicon.svg").write_bytes(b"DOCC-DEFAULT-GLOBE-SVG")
            build_docs.restore_custom_favicon(
                archive, common, filename=build_docs.FAVICON_SVG_FILE
            )
            self.assertEqual(
                (archive / "favicon.svg").read_bytes(), b"SWIFT-FAVICON-SVG"
            )


class CleanPackageBuildDirs(unittest.TestCase):
    def test_removes_build_dir_for_local_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "server-guides"
            pkg.mkdir()
            (pkg / "Package.swift").write_text("// swift-tools-version:6.0\n")
            (pkg / ".build").mkdir()
            (pkg / ".build" / "stale.txt").write_text("x")

            sources = [
                {"id": "server-guides", "type": "local", "path": "server-guides"}
            ]
            removed = build_docs.clean_package_build_dirs(root, sources)

            self.assertFalse((pkg / ".build").exists())
            self.assertEqual(len(removed), 1)
            self.assertEqual(removed[0], (pkg / ".build").resolve())

    def test_removes_build_dirs_for_repo_root_packages_not_in_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["api-guidelines", "language-guides"]:
                pkg = root / name
                pkg.mkdir()
                (pkg / "Package.swift").write_text("// swift-tools-version:6.0\n")
                (pkg / ".build").mkdir()
            removed = build_docs.clean_package_build_dirs(root, sources=[])
            self.assertEqual(len(removed), 2)
            for name in ["api-guidelines", "language-guides"]:
                self.assertFalse((root / name / ".build").exists())

    def test_dedupes_when_local_source_is_also_repo_root_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "server-guides"
            pkg.mkdir()
            (pkg / "Package.swift").write_text("// swift-tools-version:6.0\n")
            (pkg / ".build").mkdir()

            sources = [
                {"id": "server-guides", "type": "local", "path": "server-guides"}
            ]
            removed = build_docs.clean_package_build_dirs(root, sources)
            self.assertEqual(len(removed), 1)

    def test_skips_directories_without_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "api-guidelines"
            pkg.mkdir()
            (pkg / "Package.swift").write_text("// swift-tools-version:6.0\n")
            removed = build_docs.clean_package_build_dirs(root, sources=[])
            self.assertEqual(removed, [])

    def test_ignores_non_package_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "common"
            common.mkdir()
            (common / ".build").mkdir()  # no Package.swift — must not be removed
            removed = build_docs.clean_package_build_dirs(root, sources=[])
            self.assertEqual(removed, [])
            self.assertTrue((common / ".build").exists())

    def test_ignores_non_local_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = [
                {
                    "id": "x",
                    "type": "git",
                    "repo": "https://example.com/x.git",
                    "ref": "main",
                },
                {
                    "id": "y",
                    "type": "archive",
                    "url": "https://example.com/y.tar.gz",
                },
            ]
            removed = build_docs.clean_package_build_dirs(root, sources)
            self.assertEqual(removed, [])


class AssembleInSourceOrder(unittest.TestCase):
    """assemble_in_source_order flattens build results into sources.json order."""

    def test_orders_by_source_index_not_build_order(self):
        # Built in build order (archive index 1 first), but expect source order.
        results_by_index = {
            1: (["archive-1.doccarchive"], {"id": "stdlib"}),
            0: (["archive-0.doccarchive"], {"id": "book"}),
        }
        archives, entries = build_docs.assemble_in_source_order(
            results_by_index, num_sources=2
        )
        self.assertEqual(archives, ["archive-0.doccarchive", "archive-1.doccarchive"])
        self.assertEqual(entries, [{"id": "book"}, {"id": "stdlib"}])

    def test_multi_target_archives_stay_grouped_and_ordered(self):
        results_by_index = {
            0: (["a.doccarchive"], {"id": "book"}),
            1: (
                ["spm-A.doccarchive", "spm-B.doccarchive", "spm-C.doccarchive"],
                {"id": "swiftpm"},
            ),
        }
        archives, entries = build_docs.assemble_in_source_order(
            results_by_index, num_sources=2
        )
        self.assertEqual(
            archives,
            [
                "a.doccarchive",
                "spm-A.doccarchive",
                "spm-B.doccarchive",
                "spm-C.doccarchive",
            ],
        )
        self.assertEqual(entries, [{"id": "book"}, {"id": "swiftpm"}])

    def test_skips_missing_indices(self):
        # Source at index 1 failed to build and has no result entry.
        results_by_index = {
            0: (["a.doccarchive"], {"id": "book"}),
            2: (["c.doccarchive"], {"id": "testing"}),
        }
        archives, entries = build_docs.assemble_in_source_order(
            results_by_index, num_sources=3
        )
        self.assertEqual(archives, ["a.doccarchive", "c.doccarchive"])
        self.assertEqual(entries, [{"id": "book"}, {"id": "testing"}])

    def test_empty_results(self):
        archives, entries = build_docs.assemble_in_source_order({}, num_sources=3)
        self.assertEqual(archives, [])
        self.assertEqual(entries, [])


# ---------------------------------------------------------------------------
# Navigator curation (curate_navigator.py)
# ---------------------------------------------------------------------------

def _module(title, path):
    """Build a module navigator node (key order mirrors real docc output)."""
    return {"type": "module", "title": title, "path": path}


def _make_index(tmp_path, children, langs=("swift",)):
    """Write a minimal merged index.json into a fresh archive; return its Path.

    Mirrors the real shape: top-level schemaVersion / references /
    includedArchiveIdentifiers / interfaceLanguages, with a single synthesized
    root module per language whose `children` is the curation surface.
    """
    archive = tmp_path / "combined.doccarchive"
    (archive / "index").mkdir(parents=True)
    doc = {
        "schemaVersion": {"major": 0, "minor": 1, "patch": 2},
        "references": {"keep": "me"},
        "includedArchiveIdentifiers": ["A", "B"],
        "interfaceLanguages": {
            lang: [
                {
                    "title": "Swift Documentation",
                    "children": [dict(c) for c in children],
                    "path": "/documentation",
                    "type": "module",
                }
            ]
            for lang in langs
        },
    }
    (archive / "index" / "index.json").write_text(json.dumps(doc, indent=2) + "\n")
    return archive


def _children_of(archive, lang="swift"):
    doc = json.loads((archive / "index" / "index.json").read_text())
    return doc["interfaceLanguages"][lang][0]["children"]


class ValidateNavigation(unittest.TestCase):
    SOURCES = {
        "version": {"slug": "main"},
        "sources": [{"id": "a"}, {"id": "b"}],
    }

    def _nav(self, **over):
        nav = {
            "version": 1,
            "groups": [
                {"title": "Lang", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                ]},
            ],
            "hidden": [
                {"source": "b", "path": "/documentation/internal"},
            ],
        }
        nav.update(over)
        return nav

    def test_valid_navigation_has_no_errors(self):
        self.assertEqual(
            curate_navigator.validate_navigation(self._nav(), self.SOURCES), []
        )

    def test_eyebrows_valid_shape_has_no_errors(self):
        nav = self._nav(eyebrows={"suppress": True, "landing": "Project"})
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_eyebrows_non_object_is_reported(self):
        nav = self._nav(eyebrows=True)
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("'eyebrows' must be an object" in e for e in errors), errors)

    def test_eyebrows_suppress_non_bool_is_reported(self):
        nav = self._nav(eyebrows={"suppress": "yes"})
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(
            any("'eyebrows.suppress' must be a boolean" in e for e in errors), errors
        )

    def test_eyebrows_landing_non_string_is_reported(self):
        nav = self._nav(eyebrows={"landing": 42})
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(
            any("'eyebrows.landing' must be a string" in e for e in errors), errors
        )

    def test_eyebrows_landing_null_is_valid(self):
        nav = self._nav(eyebrows={"suppress": False, "landing": None})
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_module_eyebrow_field_is_valid(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift", "eyebrow": "Standard Library"},
            ]},
        ])
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_module_eyebrow_non_string_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift", "eyebrow": 7},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(
            any("'eyebrow' that must be a string" in e for e in errors), errors
        )

    def test_hidden_module_eyebrow_field_is_valid(self):
        nav = self._nav(hidden=[
            {"source": "b", "path": "/documentation/internal", "eyebrow": "Framework"},
        ])
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_external_entry_eyebrow_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
                {"title": "C++ Interop", "url": "https://www.swift.org/x/",
                 "eyebrow": "Nope"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("has 'eyebrow'" in e for e in errors), errors)

    def test_unknown_source_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "ghost", "path": "/documentation/swift"},
            ]},
        ], hidden=[{"source": "b", "path": "/documentation/internal"}])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("ghost" in e for e in errors), errors)

    def test_source_missing_from_navigation_is_reported(self):
        # 'b' is in sources.json but referenced nowhere in navigation.
        nav = self._nav(hidden=[])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("b" in e for e in errors), errors)

    def test_duplicate_path_is_reported(self):
        nav = self._nav(
            groups=[{"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
            ]}],
            hidden=[
                {"source": "b", "path": "/documentation/swift"},
            ],
        )
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("/documentation/swift" in e for e in errors), errors)

    def test_group_without_title_is_reported(self):
        nav = self._nav(groups=[{"title": "", "modules": [
            {"source": "a", "path": "/documentation/swift"},
        ]}])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(errors)

    def test_group_with_omitted_title_is_valid(self):
        # Omitting `title` entirely means "no visible header" for this
        # group's modules — distinct from an explicit empty string, which is
        # still rejected above.
        nav = self._nav(groups=[{"modules": [
            {"source": "a", "path": "/documentation/swift"},
        ]}])
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_group_with_null_title_is_valid(self):
        nav = self._nav(groups=[{"title": None, "modules": [
            {"source": "a", "path": "/documentation/swift"},
        ]}])
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_malformed_entry_missing_path_is_reported(self):
        nav = self._nav(groups=[{"title": "Lang", "modules": [
            {"source": "a"},
        ]}])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(errors)

    def test_valid_external_entry_has_no_errors(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/"},
            ]},
        ])
        self.assertEqual(curate_navigator.validate_navigation(nav, self.SOURCES), [])

    def test_external_entry_missing_url_is_reported(self):
        # `url` key present but empty — still classified as external (has a
        # `url` key at all), unlike an entry that omits `url` entirely (which
        # would misclassify as a plain module pointer missing source/path).
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [{"title": "C++ Interop", "url": ""}]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("non-empty 'url'" in e for e in errors), errors)

    def test_external_entry_missing_title_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"url": "https://www.swift.org/documentation/cxx-interop/"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("non-empty 'title'" in e for e in errors), errors)

    def test_external_entry_non_https_url_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "http://www.swift.org/documentation/cxx-interop/"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("https://" in e for e in errors), errors)

    def test_external_entry_under_hidden_is_reported(self):
        nav = self._nav(hidden=[
            {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/"},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("hidden" in e for e in errors), errors)

    def test_duplicate_external_urls_are_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/"},
                {"title": "C++ Interop Again", "url": "https://www.swift.org/documentation/cxx-interop/"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("cxx-interop" in e for e in errors), errors)

    def test_non_string_url_is_reported_not_crashed(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [{"title": "C++ Interop", "url": 12345}]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("non-empty 'url'" in e for e in errors), errors)

    def test_non_string_title_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": 12345, "url": "https://www.swift.org/documentation/cxx-interop/"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("non-empty 'title'" in e for e in errors), errors)

    def test_hybrid_entry_with_source_and_url_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift", "title": "X",
                 "url": "https://www.swift.org/documentation/cxx-interop/"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("both" in e for e in errors), errors)

    def test_hybrid_entry_does_not_validate_its_bogus_source(self):
        # The hybrid-entry error alone should surface; a typo'd source on the
        # same entry must not silently slip past unreported OR get evaluated
        # as if it were a normal module pointer.
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"source": "TYPO_BOGUS_SOURCE", "path": "/documentation/never-in-index",
                 "title": "X", "url": "https://www.swift.org/documentation/cxx-interop/"},
                {"source": "a", "path": "/documentation/swift"},
                {"source": "b", "path": "/documentation/internal"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("both" in e for e in errors), errors)

    def test_invalid_type_value_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/",
                 "type": "totally-bogus-type"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("type" in e for e in errors), errors)

    def test_non_string_type_value_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/",
                 "type": 12345},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("type" in e for e in errors), errors)

    def test_groupmarker_type_is_rejected_for_external_entry(self):
        # type "groupMarker" forces the frontend to null out the node's path
        # (docc-render's NavigatorCardItem.vue), which would silently break
        # the link — reject it explicitly rather than let it through.
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/",
                 "type": "groupMarker"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("type" in e for e in errors), errors)

    def test_url_with_leading_or_trailing_whitespace_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/ "},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("whitespace" in e for e in errors), errors)

    def test_scheme_only_url_is_reported(self):
        nav = self._nav(groups=[
            {"title": "Lang", "modules": [
                {"title": "C++ Interop", "url": "https://"},
            ]},
        ])
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        self.assertTrue(any("host" in e for e in errors), errors)

    def test_hidden_entry_duplicating_group_url_reports_once(self):
        url = "https://www.swift.org/documentation/cxx-interop/"
        nav = self._nav(
            groups=[{"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
                {"title": "C++ Interop", "url": url},
            ]}],
            hidden=[{"title": "C++ Interop (dup)", "url": url}],
        )
        errors = curate_navigator.validate_navigation(nav, self.SOURCES)
        hidden_errors = [e for e in errors if "not valid under 'hidden'" in e]
        self.assertEqual(len(hidden_errors), 1, errors)
        self.assertFalse(any("appears more than once" in e for e in errors), errors)


class CollectEyebrowOverrides(unittest.TestCase):
    def test_gathers_group_and_hidden_module_eyebrows(self):
        nav = {
            "groups": [
                {"title": "Lang", "modules": [
                    {"source": "a", "path": "/documentation/swift",
                     "eyebrow": "Standard Library"},
                    {"source": "b", "path": "/documentation/testing"},
                ]},
            ],
            "hidden": [
                {"source": "a", "path": "/documentation/cxx", "eyebrow": "Framework"},
            ],
        }
        overrides = curate_navigator.collect_eyebrow_overrides(nav)
        self.assertEqual(overrides, {
            "/documentation/swift": "Standard Library",
            "/documentation/cxx": "Framework",
        })

    def test_ignores_external_entries_even_if_they_carry_eyebrow(self):
        nav = {
            "groups": [
                {"title": "Lang", "modules": [
                    {"title": "Foundation", "url": "https://developer.apple.com/x",
                     "eyebrow": "Nope"},
                ]},
            ],
            "hidden": [],
        }
        self.assertEqual(curate_navigator.collect_eyebrow_overrides(nav), {})

    def test_landing_override_maps_to_documentation_root(self):
        nav = {"groups": [], "hidden": [], "eyebrows": {"landing": "Project"}}
        self.assertEqual(
            curate_navigator.collect_eyebrow_overrides(nav),
            {"/documentation": "Project"},
        )

    def test_no_eyebrows_block_and_no_module_eyebrows_returns_empty(self):
        nav = {
            "groups": [{"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
            ]}],
            "hidden": [],
        }
        self.assertEqual(curate_navigator.collect_eyebrow_overrides(nav), {})

    def test_landing_null_is_omitted(self):
        nav = {"groups": [], "hidden": [], "eyebrows": {"landing": None}}
        self.assertEqual(curate_navigator.collect_eyebrow_overrides(nav), {})


class CurateNavigator(unittest.TestCase):
    def _nav(self):
        return {
            "version": 1,
            "groups": [
                {"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift",
                     "title": "The Swift Language"},
                    {"source": "b", "path": "/documentation/testing"},
                ]},
            ],
            "hidden": [
                {"source": "a", "path": "/documentation/internal"},
            ],
        }

    def _happy_children(self):
        return [
            _module("Swift", "/documentation/swift"),
            _module("Internal", "/documentation/internal"),
            _module("Testing", "/documentation/testing"),
        ]

    def test_groups_hides_and_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._happy_children())
            curate_navigator.curate_navigator(archive, self._nav())
            kids = _children_of(archive)

        self.assertEqual(len(kids), 3)
        self.assertEqual(kids[0], {"type": "groupMarker", "title": "Language"})
        self.assertNotIn("path", kids[0])
        # Manifest order: swift (renamed) then testing; internal hidden.
        self.assertEqual(kids[1]["path"], "/documentation/swift")
        self.assertEqual(kids[1]["title"], "The Swift Language")
        self.assertEqual(kids[2]["path"], "/documentation/testing")
        self.assertFalse(any(k.get("path") == "/documentation/internal" for k in kids))

    def test_headerless_group_has_no_group_marker(self):
        # A group with no `title` renders its modules with no preceding
        # groupMarker, while other groups still get theirs.
        nav = {
            "version": 1,
            "groups": [
                {"modules": [{"source": "a", "path": "/documentation/swift"}]},
                {"title": "Language", "modules": [
                    {"source": "b", "path": "/documentation/testing"},
                ]},
            ],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [
                _module("Swift", "/documentation/swift"),
                _module("Testing", "/documentation/testing"),
            ])
            curate_navigator.curate_navigator(archive, nav)
            kids = _children_of(archive)

        self.assertEqual(len(kids), 3)
        self.assertEqual(kids[0]["path"], "/documentation/swift")
        self.assertNotEqual(kids[0].get("type"), "groupMarker")
        self.assertEqual(kids[1], {"type": "groupMarker", "title": "Language"})
        self.assertEqual(kids[2]["path"], "/documentation/testing")

    def test_dangling_manifest_path_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [_module("Swift", "/documentation/swift")])
            nav = {
                "version": 1,
                "groups": [{"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                    {"source": "a", "path": "/documentation/missing"},
                ]}],
                "hidden": [],
            }
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.curate_navigator(archive, nav)

    def test_unlisted_index_module_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [
                _module("Swift", "/documentation/swift"),
                _module("Extra", "/documentation/extra"),
            ])
            nav = {
                "version": 1,
                "groups": [{"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                ]}],
                "hidden": [],
            }
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.curate_navigator(archive, nav)

    def test_preserves_top_level_keys_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._happy_children())
            curate_navigator.curate_navigator(archive, self._nav())
            doc = json.loads((archive / "index" / "index.json").read_text())

        self.assertEqual(
            list(doc.keys()),
            ["schemaVersion", "references", "includedArchiveIdentifiers",
             "interfaceLanguages"],
        )
        self.assertEqual(doc["references"], {"keep": "me"})
        self.assertEqual(doc["includedArchiveIdentifiers"], ["A", "B"])
        self.assertEqual(doc["schemaVersion"], {"major": 0, "minor": 1, "patch": 2})

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._happy_children())
            index = archive / "index" / "index.json"
            curate_navigator.curate_navigator(archive, self._nav())
            once = index.read_bytes()
            curate_navigator.curate_navigator(archive, self._nav())
            twice = index.read_bytes()
        self.assertEqual(once, twice)

    def test_curates_each_interface_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._happy_children(),
                                  langs=("swift", "occ"))
            curate_navigator.curate_navigator(archive, self._nav())
            for lang in ("swift", "occ"):
                kids = _children_of(archive, lang)
                self.assertEqual(kids[0]["type"], "groupMarker")
                self.assertFalse(
                    any(k.get("path") == "/documentation/internal" for k in kids)
                )

    def test_missing_index_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "empty.doccarchive"
            archive.mkdir()
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.curate_navigator(archive, self._nav())

    def test_external_entry_is_synthesized(self):
        nav = {
            "version": 1,
            "groups": [{"title": "Interoperability", "modules": [
                {"source": "a", "path": "/documentation/swift"},
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/"},
            ]}],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [_module("Swift", "/documentation/swift")])
            curate_navigator.curate_navigator(archive, nav)
            kids = _children_of(archive)

        self.assertEqual(len(kids), 3)
        external = kids[2]
        self.assertEqual(external["title"], "C++ Interop")
        self.assertEqual(external["path"], "https://www.swift.org/documentation/cxx-interop/")
        self.assertEqual(external["type"], "resources")
        self.assertTrue(external["external"])
        self.assertNotIn("children", external)

    def test_external_entry_survives_repeated_curation(self):
        # Re-curating an already-curated archive (e.g. validate_navigation.py
        # dry-running against a prior build) must not treat a previously
        # synthesized external node as a real merged module.
        nav = {
            "version": 1,
            "groups": [{"title": "Interoperability", "modules": [
                {"source": "a", "path": "/documentation/swift"},
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/"},
            ]}],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [_module("Swift", "/documentation/swift")])
            curate_navigator.curate_navigator(archive, nav)
            once = (archive / "index" / "index.json").read_bytes()
            curate_navigator.curate_navigator(archive, nav)
            twice = (archive / "index" / "index.json").read_bytes()

        self.assertEqual(once, twice)

    def test_external_entry_honors_explicit_type(self):
        nav = {
            "version": 1,
            "groups": [{"title": "Interoperability", "modules": [
                {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/",
                 "type": "article"},
            ]}],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [])
            curate_navigator.curate_navigator(archive, nav)
            kids = _children_of(archive)

        self.assertEqual(kids[1]["type"], "article")

    def test_external_entry_does_not_affect_dangling_or_unlisted_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [_module("Swift", "/documentation/swift")])
            nav = {
                "version": 1,
                "groups": [{"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                    {"title": "C++ Interop", "url": "https://www.swift.org/documentation/cxx-interop/"},
                ]}],
                "hidden": [],
            }
            # Should not raise: the external entry has no path to be dangling
            # or unlisted against the merged index.
            curate_navigator.curate_navigator(archive, nav)

    def test_hybrid_entry_raises_even_without_prior_validation(self):
        # curate_navigator()/dry_run() take no sources.json and can't call
        # validate_navigation() themselves — they must defend against a
        # hybrid entry on their own, not rely on a caller having validated
        # first (every test in this class calls curate_navigator directly,
        # same as a hypothetical future caller that skips validation).
        # No other module in the index, so the only way this can raise is
        # the explicit hybrid check — not a coincidental dangling/unlisted
        # collision on '/documentation/nonexistent'.
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [])
            nav = {
                "version": 1,
                "groups": [{"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/nonexistent",
                     "title": "Hijacked!", "url": "https://evil.example.com/"},
                ]}],
                "hidden": [],
            }
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.curate_navigator(archive, nav)

    def test_groupmarker_type_raises_even_without_prior_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [])
            nav = {
                "version": 1,
                "groups": [{"title": "Language", "modules": [
                    {"title": "Dead Link", "url": "https://example.com/",
                     "type": "groupMarker"},
                ]}],
                "hidden": [],
            }
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.curate_navigator(archive, nav)

    def test_missing_title_on_external_entry_raises_even_without_prior_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [])
            nav = {
                "version": 1,
                "groups": [{"title": "Language", "modules": [
                    {"url": "https://example.com/"},
                ]}],
                "hidden": [],
            }
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.curate_navigator(archive, nav)

    def test_malformed_index_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.doccarchive"
            (archive / "index").mkdir(parents=True)
            (archive / "index" / "index.json").write_text("{not json")
            with self.assertRaises(json.JSONDecodeError):
                curate_navigator.curate_navigator(archive, self._nav())


class CurateNavigatorDryRun(unittest.TestCase):

    def _nav(self):
        return {
            "version": 1,
            "groups": [{"title": "Language", "modules": [
                {"source": "a", "path": "/documentation/swift"},
            ]}],
            "hidden": [{"source": "a", "path": "/documentation/internal"}],
        }

    def test_returns_curated_children_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [
                _module("Swift", "/documentation/swift"),
                _module("Internal", "/documentation/internal"),
            ])
            index = archive / "index" / "index.json"
            before = index.read_bytes()
            result = curate_navigator.dry_run(archive, self._nav())
            after = index.read_bytes()

        self.assertEqual(before, after)  # archive untouched
        kids = result["swift"]
        self.assertEqual(kids[0], {"type": "groupMarker", "title": "Language"})
        self.assertEqual([k.get("path") for k in kids[1:]], ["/documentation/swift"])

    def test_unlisted_module_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [
                _module("Swift", "/documentation/swift"),
                _module("Extra", "/documentation/extra"),
            ])
            with self.assertRaises(curate_navigator.NavigationError):
                curate_navigator.dry_run(archive, self._nav())


class AutoArchiveVersionSlug(unittest.TestCase):
    """_auto_archive must resolve the {slug} version object."""

    def _sources_path(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        return scripts_dir / "sources.json"

    def test_uses_slug_from_version_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources_path = self._sources_path(tmp_path)
            sources = {
                "version": {"slug": "main"},
                "sources": [],
            }
            index_dir = tmp_path / ".build-output" / "main" / "index"
            index_dir.mkdir(parents=True)
            (index_dir / "index.json").write_text("{}")

            found = validate_navigation_cli._auto_archive(sources, sources_path)
            self.assertEqual(found, tmp_path.resolve() / ".build-output" / "main")

    def test_missing_slug_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources_path = self._sources_path(tmp_path)
            sources = {
                "version": {},
                "sources": [],
            }
            self.assertIsNone(validate_navigation_cli._auto_archive(sources, sources_path))

    def test_version_missing_entirely_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources_path = self._sources_path(tmp_path)
            self.assertIsNone(validate_navigation_cli._auto_archive({"sources": []}, sources_path))


class ValidateNavigationCLI(unittest.TestCase):
    def _setup(self, tmp, nav, sources, archive_modules=None):
        """Write nav + sources files (and optionally an archive); return argv."""
        tmp = Path(tmp)
        nav_path = tmp / "navigation.json"
        src_path = tmp / "sources.json"
        nav_path.write_text(json.dumps(nav))
        src_path.write_text(json.dumps(sources))
        argv = ["--navigation", str(nav_path), "--sources", str(src_path)]
        if archive_modules is not None:
            archive = _make_index(tmp, [_module(t, p) for t, p in archive_modules])
            argv += ["--archive", str(archive)]
        return argv

    SOURCES = {
        "version": {"slug": "main"},
        "sources": [{"id": "a"}, {"id": "b"}],
    }

    def _nav(self):
        return {
            "version": 1,
            "groups": [{"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
            ]}],
            "hidden": [{"source": "b", "path": "/documentation/internal"}],
        }

    def test_valid_without_archive_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._setup(tmp, self._nav(), self.SOURCES)
            self.assertEqual(validate_navigation_cli.main(argv), 0)

    def test_unknown_source_returns_1(self):
        nav = self._nav()
        nav["groups"][0]["modules"][0]["source"] = "ghost"
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._setup(tmp, nav, self.SOURCES)
            self.assertEqual(validate_navigation_cli.main(argv), 1)

    def test_archive_fully_covered_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._setup(
                tmp, self._nav(), self.SOURCES,
                archive_modules=[
                    ("Swift", "/documentation/swift"),
                    ("Internal", "/documentation/internal"),
                ],
            )
            self.assertEqual(validate_navigation_cli.main(argv), 0)

    def test_archive_with_unlisted_module_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._setup(
                tmp, self._nav(), self.SOURCES,
                archive_modules=[
                    ("Swift", "/documentation/swift"),
                    ("Internal", "/documentation/internal"),
                    ("Extra", "/documentation/extra"),
                ],
            )
            self.assertEqual(validate_navigation_cli.main(argv), 1)


def _write_landing_page(archive, sections, references=None):
    """Write a minimal synthesized landing page (data/documentation.json).

    `sections` is a list of (title, [identifiers]). When `references` is
    omitted, every identifier appearing in any section is given a stock
    article-shaped reference, mirroring how docc emits article-kind cards by
    default for standalone .docc catalogs.
    """
    data = archive / "data"
    data.mkdir(parents=True, exist_ok=True)
    if references is None:
        references = {}
        for _, ids in sections:
            for ident in ids:
                references.setdefault(ident, {
                    "identifier": ident,
                    "kind": "article",
                    "title": ident.rsplit("/", 1)[-1],
                    "type": "topic",
                    "url": "/documentation/x",
                })
    doc = {
        "identifier": {"url": "doc://X/documentation", "interfaceLanguage": "swift"},
        "kind": "symbol",
        "topicSections": [
            {"title": t, "identifiers": list(ids)} for t, ids in sections
        ],
        "references": references,
    }
    (data / "documentation.json").write_text(json.dumps(doc, indent=2) + "\n")


def _landing_sections(archive):
    doc = json.loads((archive / "data" / "documentation.json").read_text())
    return [(s.get("title"), s.get("identifiers")) for s in doc.get("topicSections", [])]


class CurateLandingPage(unittest.TestCase):
    def _nav(self):
        return {
            "version": 1,
            "groups": [
                {"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                    {"source": "a", "path": "/documentation/testing"},
                ]},
                {"title": "Server-Side Swift", "modules": [
                    {"source": "a", "path": "/documentation/serverguides"},
                    {"source": "a", "path": "/tutorials/getting-started-swift-server"},
                ]},
            ],
            "hidden": [{"source": "a", "path": "/documentation/internal"}],
        }

    def _index_children(self):
        return [
            _module("Swift", "/documentation/swift"),
            _module("Internal", "/documentation/internal"),
            _module("Testing", "/documentation/testing"),
            _module("Server Guides", "/documentation/serverguides"),
            _module("Getting Started", "/tutorials/getting-started-swift-server"),
        ]

    def _docc_merge_landing(self):
        """Approximate what docc merge produces: one Modules + one Tutorials."""
        return [
            ("Modules", [
                "doc://Test/documentation/Swift",
                "doc://Test/documentation/Internal",
                "doc://Test/documentation/Testing",
                "doc://Test/documentation/ServerGuides",
            ]),
            ("Tutorials", [
                "doc://Test/tutorials/getting-started-swift-server",
            ]),
        ]

    def test_landing_page_sections_match_nav_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._index_children())
            _write_landing_page(archive, self._docc_merge_landing())
            curate_navigator.curate_navigator(archive, self._nav())
            sections = _landing_sections(archive)

        # One section per nav group, in nav order.
        self.assertEqual([t for t, _ in sections],
                         ["Language", "Server-Side Swift"])
        # Identifiers in declared nav order; hidden module dropped from Language.
        self.assertEqual(sections[0][1], [
            "doc://Test/documentation/Swift",
            "doc://Test/documentation/Testing",
        ])
        # Tutorials get folded into Server-Side Swift via nav-group placement.
        self.assertEqual(sections[1][1], [
            "doc://Test/documentation/ServerGuides",
            "doc://Test/tutorials/getting-started-swift-server",
        ])

    def test_hidden_modules_dropped_from_landing_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._index_children())
            _write_landing_page(archive, self._docc_merge_landing())
            curate_navigator.curate_navigator(archive, self._nav())
            sections = _landing_sections(archive)

        all_ids = [i for _, ids in sections for i in ids]
        self.assertNotIn("doc://Test/documentation/Internal", all_ids)

    def test_group_with_no_matches_is_dropped(self):
        nav = {
            "version": 1,
            "groups": [
                {"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                ]},
                # Group whose modules don't appear in the landing page (e.g. the
                # navigator references a path the merge step didn't surface).
                {"title": "Empty Group", "modules": [
                    {"source": "a", "path": "/documentation/never-on-page"},
                ]},
            ],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [
                _module("Swift", "/documentation/swift"),
                _module("NeverOnPage", "/documentation/never-on-page"),
            ])
            _write_landing_page(archive, [
                ("Modules", ["doc://Test/documentation/Swift"]),
            ])
            curate_navigator.curate_navigator(archive, nav)
            titles = [t for t, _ in _landing_sections(archive)]

        self.assertEqual(titles, ["Language"])

    def test_external_entry_produces_no_landing_page_card(self):
        nav = {
            "version": 1,
            "groups": [
                {"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/swift"},
                    {"title": "C++ Interop",
                     "url": "https://www.swift.org/documentation/cxx-interop/"},
                ]},
            ],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [_module("Swift", "/documentation/swift")])
            _write_landing_page(archive, [
                ("Modules", ["doc://Test/documentation/Swift"]),
            ])
            curate_navigator.curate_navigator(archive, nav)
            sections = _landing_sections(archive)

        self.assertEqual(sections, [("Language", ["doc://Test/documentation/Swift"])])

    def test_headerless_group_produces_titleless_section(self):
        nav = {
            "version": 1,
            "groups": [
                {"modules": [{"source": "a", "path": "/documentation/swift"}]},
                {"title": "Language", "modules": [
                    {"source": "a", "path": "/documentation/testing"},
                ]},
            ],
            "hidden": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), [
                _module("Swift", "/documentation/swift"),
                _module("Testing", "/documentation/testing"),
            ])
            _write_landing_page(archive, [
                ("Modules", [
                    "doc://Test/documentation/Swift",
                    "doc://Test/documentation/Testing",
                ]),
            ])
            curate_navigator.curate_navigator(archive, nav)
            doc = json.loads((archive / "data" / "documentation.json").read_text())
            sections = doc["topicSections"]

        self.assertNotIn("title", sections[0])
        self.assertNotIn("anchor", sections[0])
        self.assertEqual(sections[0]["identifiers"], ["doc://Test/documentation/Swift"])
        self.assertEqual(sections[1]["title"], "Language")
        self.assertEqual(sections[1]["anchor"], "Language")

    def test_overwrites_kept_reference_kind_and_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._index_children())
            _write_landing_page(archive, self._docc_merge_landing())
            curate_navigator.curate_navigator(archive, self._nav())
            doc = json.loads((archive / "data" / "documentation.json").read_text())
            refs = doc["references"]

        for ident in (
            "doc://Test/documentation/Swift",
            "doc://Test/documentation/Testing",
            "doc://Test/documentation/ServerGuides",
            "doc://Test/tutorials/getting-started-swift-server",
        ):
            self.assertEqual(refs[ident]["kind"], "symbol", ident)
            self.assertEqual(refs[ident]["role"], "collection", ident)

    def test_hidden_reference_kind_and_role_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._index_children())
            _write_landing_page(archive, [
                ("Modules", [
                    "doc://Test/documentation/Swift",
                    "doc://Test/documentation/Internal",
                ]),
            ])
            curate_navigator.curate_navigator(archive, self._nav())
            doc = json.loads((archive / "data" / "documentation.json").read_text())
            refs = doc["references"]

        # Hidden reference's fields are not touched (kind stays "article").
        self.assertEqual(refs["doc://Test/documentation/Internal"]["kind"], "article")
        self.assertNotIn("role", refs["doc://Test/documentation/Internal"])

    def test_missing_landing_page_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._index_children())
            # No data/documentation.json written.
            curate_navigator.curate_navigator(archive, self._nav())
            # Index still curated; no error raised.
            self.assertEqual(_children_of(archive)[0]["type"], "groupMarker")

    def test_landing_page_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_index(Path(tmp), self._index_children())
            _write_landing_page(archive, self._docc_merge_landing())
            page = archive / "data" / "documentation.json"
            curate_navigator.curate_navigator(archive, self._nav())
            once = page.read_bytes()
            curate_navigator.curate_navigator(archive, self._nav())
            twice = page.read_bytes()
        self.assertEqual(once, twice)


def _git_source(source_id="swift-foundation-essentials", ref="main", **extra):
    entry = {
        "id": source_id,
        "type": "git",
        "repo": "https://github.com/swiftlang/swift-foundation.git",
        "ref": ref,
        "docc_catalog": "Sources/A/A.docc",
    }
    entry.update(extra)
    return entry


def _reusing_source(source_id="swift-foundation-internationalization", **extra):
    entry = {
        "id": source_id,
        "type": "git",
        "reuse_checkout": "swift-foundation-essentials",
        "docc_catalog": "Sources/B/B.docc",
    }
    entry.update(extra)
    return entry


def _wrap_many(*entries):
    return {"version": {"slug": "main"}, "sources": list(entries)}


class ValidateReuseCheckout(unittest.TestCase):
    def test_valid_when_it_names_an_earlier_git_source(self):
        config = _wrap_many(_git_source(), _reusing_source())
        self.assertIsNone(_validate(config))

    def test_repo_and_ref_not_required_when_reusing(self):
        output = _validate(_wrap_many(_git_source(), _reusing_source()))
        self.assertIsNone(output)

    def test_repo_rejected_when_reusing(self):
        entry = _reusing_source(repo="https://example.com/x.git")
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)
        self.assertIn("repo", output)

    def test_ref_rejected_when_reusing(self):
        entry = _reusing_source(ref="main")
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)

    def test_unknown_id_rejected(self):
        entry = _reusing_source(reuse_checkout="nope")
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIsNotNone(output)
        self.assertIn("nope", output)

    def test_forward_reference_rejected(self):
        output = _validate(_wrap_many(_reusing_source(), _git_source()))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)

    def test_self_reference_rejected(self):
        entry = _reusing_source(
            source_id="a", reuse_checkout="a"
        )
        output = _validate(_wrap_many(entry))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)

    def test_target_must_be_git_source(self):
        local = {
            "id": "swift-foundation-essentials",
            "type": "local",
            "path": "x",
            "docc_catalog": "A.docc",
        }
        output = _validate(_wrap_many(local, _reusing_source()))
        self.assertIsNotNone(output)
        self.assertIn("git", output)

    def test_non_string_rejected(self):
        entry = _reusing_source(reuse_checkout=5)
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)

    def test_rejected_on_local_source(self):
        entry = {
            "id": "b",
            "type": "local",
            "path": "b",
            "docc_catalog": "B.docc",
            "reuse_checkout": "swift-foundation-essentials",
        }
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)

    def test_rejected_on_archive_source(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
            "reuse_checkout": "swift-foundation-essentials",
        }
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIsNotNone(output)
        self.assertIn("reuse_checkout", output)

    def test_git_without_repo_or_ref_still_rejected_without_reuse(self):
        entry = _reusing_source()
        del entry["reuse_checkout"]
        output = _validate(_wrap_many(_git_source(), entry))
        self.assertIn("repo", output)
        self.assertIn("ref", output)


def _git_env():
    """The environment for git commands that create commits in test repos.

    Commits use the invoking user's git identity. A CI runner has none, so
    fall back to a placeholder author. Signing is always off for these
    throwaway repos (see _git), so the placeholder is never used to sign.
    """
    env = dict(os.environ)
    configured = subprocess.run(
        ["git", "config", "user.name"], capture_output=True, text=True
    ).stdout.strip()
    if not configured:
        env.update({
            "GIT_AUTHOR_NAME": "docs tests",
            "GIT_AUTHOR_EMAIL": "docs-tests@invalid",
            "GIT_COMMITTER_NAME": "docs tests",
            "GIT_COMMITTER_EMAIL": "docs-tests@invalid",
        })
    return env


def _git(path, *args):
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
         "-C", str(path), *args],
        capture_output=True, text=True, check=True, env=_git_env(),
    )
    return result.stdout.strip()


def _commit_file(repo, name, text, message):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "--quiet", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class CloneOrUpdate(unittest.TestCase):
    """clone_or_update against real local repos, including dirty caches."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.remote = tmp / "remote"
        self.remote.mkdir()
        _git(self.remote, "init", "--quiet", "-b", "main")
        self.main_commit = _commit_file(self.remote, "f.txt", "main\n", "main")
        _git(self.remote, "tag", "v1")
        _git(self.remote, "checkout", "--quiet", "-b", "release")
        self.release_commit = _commit_file(
            self.remote, "f.txt", "release\n", "release"
        )
        _git(self.remote, "checkout", "--quiet", "main")
        self.workspace = tmp / "workspace"
        self.workspace.mkdir()
        self.source = {"id": "src", "repo": str(self.remote)}

    def _sync(self, ref):
        return build_docs.clone_or_update(self.source, self.workspace, ref)

    def _head(self, source_dir):
        return _git(source_dir, "rev-parse", "HEAD")

    def test_fresh_clone_checks_out_the_branch(self):
        source_dir = self._sync("release")
        self.assertEqual(self._head(source_dir), self.release_commit)

    def test_update_picks_up_new_upstream_commits(self):
        source_dir = self._sync("main")
        new_commit = _commit_file(self.remote, "f.txt", "newer\n", "newer")
        self._sync("main")
        self.assertEqual(self._head(source_dir), new_commit)

    def test_update_switches_ref_despite_dirty_tracked_file(self):
        # Mirrors add_docc_plugin editing Package.swift in a cached clone.
        source_dir = self._sync("main")
        (source_dir / "f.txt").write_text("edited by a previous build\n")
        self._sync("release")
        self.assertEqual(self._head(source_dir), self.release_commit)
        self.assertEqual((source_dir / "f.txt").read_text(), "release\n")

    def test_update_removes_untracked_files(self):
        source_dir = self._sync("main")
        (source_dir / "generated.md").write_text("stale")
        self._sync("main")
        self.assertFalse((source_dir / "generated.md").exists())

    def test_update_follows_a_tag(self):
        source_dir = self._sync("release")
        self._sync("v1")
        self.assertEqual(self._head(source_dir), self.main_commit)

    def test_update_survives_an_upstream_force_push(self):
        source_dir = self._sync("release")
        _git(self.remote, "checkout", "--quiet", "release")
        _git(self.remote, "reset", "--quiet", "--hard", "main")
        rewritten = _commit_file(self.remote, "f.txt", "rewritten\n", "rewrite")
        _git(self.remote, "checkout", "--quiet", "main")
        self._sync("release")
        self.assertEqual(self._head(source_dir), rewritten)

# ---------------------------------------------------------------------------
# End-to-end tests: real build functions, temporary directories, and shell
# scripts standing in for `swift` and `docc`.
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).resolve().parent / "build_docs.py"

# A stand-in for `docc`. Every subcommand that takes `--output-path` creates
# that directory with an empty `data/` folder and a stub `index.html`.
# Behavior switches, set through the environment:
#   FAKE_DOCC_LOG=<file>              append each invocation's arguments
#   FAKE_DOCC_FAIL=1                  exit 1 on every call
#   FAKE_DOCC_FAIL_ON=<subcommand>    exit 1 for that subcommand only
#   FAKE_DOCC_NO_OUTPUT=1             exit 0 without writing anything
#   FAKE_DOCC_MERGE_INDEX=<file>      `merge` writes this as index/index.json
#   FAKE_DOCC_CLOBBER_FAVICONS=1      `process-archive` writes DocC's default
#                                     favicons, as the real tool does
#   FAKE_DOCC_COPY=<dir>              copy this directory into the output
FAKE_DOCC = """#!/bin/bash
sub="$1"
[ -n "$FAKE_DOCC_LOG" ] && echo "$@" >> "$FAKE_DOCC_LOG"
[ -n "$FAKE_DOCC_FAIL" ] && exit 1
[ -n "$FAKE_DOCC_FAIL_ON" ] && [ "$FAKE_DOCC_FAIL_ON" = "$sub" ] && exit 1
[ -n "$FAKE_DOCC_NO_OUTPUT" ] && exit 0
out=""
while [ $# -gt 0 ]; do
    if [ "$1" = "--output-path" ]; then out="$2"; shift; fi
    shift
done
mkdir -p "$out/data/documentation"
[ -n "$FAKE_DOCC_COPY" ] && cp -R "$FAKE_DOCC_COPY/." "$out/"
if [ "$sub" = "merge" ] && [ -n "$FAKE_DOCC_MERGE_INDEX" ]; then
    mkdir -p "$out/index"
    cp "$FAKE_DOCC_MERGE_INDEX" "$out/index/index.json"
fi
echo '<html><head><title>Documentation</title></head><body></body></html>' > "$out/index.html"
if [ "$sub" = "process-archive" ] && [ -n "$FAKE_DOCC_CLOBBER_FAVICONS" ]; then
    printf DOCC-DEFAULT > "$out/favicon.ico"
    printf DOCC-DEFAULT-SVG > "$out/favicon.svg"
fi
"""

# A stand-in for `swift`. `package describe` reports one target, `Lib`, whose
# sources hold a `.docc` catalog. `package generate-documentation` writes a
# `.doccarchive` for the requested target under `.build/`.
FAKE_SWIFT = """#!/bin/bash
if [ "$1" = "package" ] && [ "$2" = "describe" ]; then
    echo '{"targets": [{"name": "Lib", "path": "Sources/Lib"}], "dependencies": []}'
    exit 0
fi
if [ "$1" = "package" ] && [ "$2" = "generate-documentation" ]; then
    target=""
    while [ $# -gt 0 ]; do
        if [ "$1" = "--target" ]; then target="$2"; shift; fi
        shift
    done
    mkdir -p ".build/plugins/$target.doccarchive/data"
    exit 0
fi
exit 1
"""


def _write_tool(directory, name, body):
    path = Path(directory) / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _make_common_dir(root):
    common = Path(root) / "common"
    common.mkdir()
    (common / "header.html").write_text("<header>{{COPYRIGHT_YEAR}}</header>")
    (common / "footer.html").write_text("<footer></footer>")
    (common / "favicon.ico").write_bytes(b"ico")
    (common / "favicon.svg").write_text("<svg/>")
    return common


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Silence the build's progress output.
        patcher = mock.patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_docc(self):
        return [str(_write_tool(self.tmp, "fake-docc", FAKE_DOCC))]

    def env_with(self, **extra):
        return mock.patch.dict(os.environ, extra)

    def docc_calls(self, log):
        """Return each logged docc invocation as a list of arguments."""
        if not Path(log).exists():
            return []
        return [line.split() for line in Path(log).read_text().splitlines()]


class ResolveCanonicalBaseUrlMalformed(unittest.TestCase):
    def test_unparseable_url_warns_instead_of_raising(self):
        with mock.patch("builtins.print") as printed:
            result = build_docs._resolve_canonical_base_url(
                {"canonical_base_url": "http://[bad"}
            )
        self.assertIsNone(result)
        self.assertTrue(printed.called)


class FetchArchiveTruncatedDownload(TempDirTestCase):
    def test_truncated_gzip_raises_archive_fetch_error(self):
        full = self.tmp / "full.tar.gz"
        with gzip.open(full, "wb") as f:
            f.write(b"x" * 100_000)
        data = full.read_bytes()
        (self.tmp / "bad.tar.gz").write_bytes(data[: len(data) // 2])
        source = {
            "id": "a",
            "type": "archive",
            "url": (self.tmp / "bad.tar.gz").as_uri(),
            "docc_archive_name": "A.doccarchive",
        }
        with self.assertRaises(build_docs.ArchiveFetchError):
            build_docs.fetch_archive(source, self.tmp / "ws")

    def test_zip_archive_extracts(self):
        zip_path = self.tmp / "a.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("top/A.doccarchive/index.html", "x")
        source = {
            "id": "a",
            "type": "archive",
            "url": zip_path.as_uri(),
            "docc_archive_name": "A.doccarchive",
            "format": "zip",
        }
        found = build_docs.fetch_archive(source, self.tmp / "ws")
        self.assertEqual(found.name, "A.doccarchive")
        self.assertTrue((found / "index.html").is_file())


class TransformStaticHostingSafety(TempDirTestCase):
    def _archive(self):
        archive = self.tmp / "Combined.doccarchive"
        (archive / "data").mkdir(parents=True)
        (archive / "marker.txt").write_text("original")
        return archive

    def test_success_replaces_archive_in_place(self):
        archive = self._archive()
        build_docs.transform_static_hosting(archive, "main", self.fake_docc())
        self.assertTrue(archive.is_dir())
        self.assertFalse((archive / "marker.txt").exists())

    def test_docc_exiting_zero_without_output_keeps_original(self):
        archive = self._archive()
        with self.env_with(FAKE_DOCC_NO_OUTPUT="1"):
            with self.assertRaises(RuntimeError):
                build_docs.transform_static_hosting(
                    archive, "main", self.fake_docc()
                )
        self.assertEqual((archive / "marker.txt").read_text(), "original")

    def test_docc_failure_keeps_original(self):
        archive = self._archive()
        with self.env_with(FAKE_DOCC_FAIL="1"):
            with self.assertRaises(subprocess.CalledProcessError):
                build_docs.transform_static_hosting(
                    archive, "main", self.fake_docc()
                )
        self.assertEqual((archive / "marker.txt").read_text(), "original")


class FinalizeCombinedArchiveRealSteps(TempDirTestCase):
    """Drive _finalize_combined_archive with a fake docc and real post-steps."""

    def _run(self, common_dir=None, **kwargs):
        src = self.tmp / "src.doccarchive"
        (src / "data").mkdir(parents=True)
        out = self.tmp / "out"
        out.mkdir()
        return build_docs._finalize_combined_archive(
            [src], out, "main", self.fake_docc(), [],
            common_dir=common_dir, **kwargs,
        )

    def test_full_success_records_every_step(self):
        common = _make_common_dir(self.tmp)
        succeeded, failed = self._run(common_dir=common)
        self.assertEqual(failed, [])
        self.assertEqual(
            succeeded,
            [
                "combined-merge",
                "eyebrow-suppression",
                "language-toggle-suppression",
                "static-hosting-transform",
            ],
        )

    def test_step_raising_value_error_is_recorded_not_raised(self):
        # A merged archive without data/ makes the language-toggle step raise
        # ValueError. A failing step must be recorded, not crash the build.
        src = self.tmp / "src.doccarchive"
        src.mkdir()
        out = self.tmp / "out"
        out.mkdir()
        fake = _write_tool(
            self.tmp, "docc-no-data",
            '#!/bin/bash\nwhile [ $# -gt 0 ]; do '
            '[ "$1" = "--output-path" ] && mkdir -p "$2"; shift; done\n',
        )
        succeeded, failed = build_docs._finalize_combined_archive(
            [src], out, "main", [str(fake)], [],
        )
        self.assertEqual(succeeded, ["combined-merge", "eyebrow-suppression"])
        self.assertEqual(failed, ["language-toggle-suppression"])

    def test_missing_favicon_is_recorded_not_raised(self):
        common = _make_common_dir(self.tmp)
        (common / "favicon.svg").unlink()
        succeeded, failed = self._run(common_dir=common)
        self.assertEqual(failed, ["static-hosting-transform"])
        self.assertNotIn("static-hosting-transform", succeeded)

    def test_failed_step_stops_later_steps(self):
        common = _make_common_dir(self.tmp)
        (common / "favicon.svg").unlink()
        succeeded, failed = self._run(
            common_dir=common, canonical_base_url="https://example.com/x"
        )
        self.assertNotIn("canonical-link-injection", succeeded + failed)


class ValidateSourcesStrictness(unittest.TestCase):
    def _errors(self, *entries, **top):
        config = {"version": {"slug": "main"}, "sources": list(entries)}
        config.update(top)
        return "\n".join(build_docs.validate_sources(config))

    def local(self, **extra):
        return {"id": "p", "type": "local", "path": "p", "targets": ["T"], **extra}

    def git(self, **extra):
        entry = {
            "id": "g", "type": "git", "repo": "https://x/y.git", "ref": "main",
            "docc_catalog": "C.docc",
        }
        entry.update(extra)
        return entry

    def test_empty_targets_rejected(self):
        self.assertIn("targets", self._errors(self.local(targets=[])))

    def test_targets_must_be_list_of_strings(self):
        self.assertIn("targets", self._errors(self.local(targets="Foo")))
        self.assertIn("targets", self._errors(self.local(targets=[1])))

    def test_non_object_entry_is_an_error_not_a_crash(self):
        self.assertIn("object", self._errors("not-an-object"))
        self.assertIn("object", self._errors(None))

    def test_non_string_archive_name_is_an_error_not_a_crash(self):
        entry = {
            "id": "a", "type": "archive", "url": "https://x/a.tar.gz",
            "docc_archive_name": 5,
        }
        self.assertIn("docc_archive_name", self._errors(entry))

    def test_duplicate_ids_rejected(self):
        self.assertIn("duplicate", self._errors(self.local(), self.local()))

    def test_unknown_field_rejected(self):
        # A typo must not silently disable a feature.
        out = self._errors(self.git(strip_linux_availabilty=True))
        self.assertIn("strip_linux_availabilty", out)

    def test_wrong_field_types_rejected(self):
        for field, value in [
            ("docc_catalog", 5),
            ("extra_flags", "--x"),
            ("preflight", 5),
            ("ref", 5),
            ("repo", 5),
            ("add_docc_plugin", "yes"),
        ]:
            with self.subTest(field=field):
                self.assertIn(field, self._errors(self.git(**{field: value})))

    def test_non_string_id_rejected(self):
        self.assertIn("id", self._errors(self.local(id=5)))

    def test_local_source_rejects_git_only_fields(self):
        self.assertIn("repo", self._errors(self.local(repo="https://x/y.git")))

    def test_valid_configs_still_pass(self):
        self.assertEqual(self._errors(self.local(), self.git()), "")

    def test_real_sources_json_is_valid(self):
        path = Path(__file__).resolve().parent / "sources.json"
        config = json.loads(path.read_text())
        self.assertEqual(build_docs.validate_sources(config), [])


class BuildAllSources(unittest.TestCase):
    """build_all_sources sorts failures by severity and keeps source order."""

    def _sources(self):
        return [
            {"id": "git1", "type": "git"},
            {"id": "arch", "type": "archive"},
            {"id": "local1", "type": "local"},
        ]

    def test_archives_build_first_and_results_are_keyed_by_index(self):
        order = []

        def build(source):
            order.append(source["id"])
            return [Path(source["id"])], {"id": source["id"]}

        succeeded, failed, results = build_docs.build_all_sources(
            self._sources(), build
        )
        self.assertEqual(order[0], "arch")
        self.assertEqual(sorted(results), [0, 1, 2])
        self.assertEqual(failed, [])
        self.assertCountEqual(succeeded, ["git1", "arch", "local1"])

    def test_non_archive_failure_is_recorded_and_build_continues(self):
        def build(source):
            if source["id"] == "git1":
                raise RuntimeError("boom")
            return [Path(source["id"])], {"id": source["id"]}

        succeeded, failed, results = build_docs.build_all_sources(
            self._sources(), build
        )
        self.assertEqual(failed, ["git1"])
        self.assertNotIn(0, results)
        self.assertIn(2, results)

    def test_archive_fetch_error_is_fatal(self):
        built = []

        def build(source):
            built.append(source["id"])
            if source["type"] == "archive":
                raise build_docs.ArchiveFetchError("no network")
            return [], {}

        with mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as ctx:
                build_docs.build_all_sources(self._sources(), build)
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(built, ["arch"])

    def test_other_archive_errors_are_fatal_too(self):
        # An archive source that breaks for any reason must not be left to
        # surface as a raw traceback.
        def build(source):
            if source["type"] == "archive":
                raise OSError("disk full")
            return [], {}

        with mock.patch("builtins.print"):
            with self.assertRaises(SystemExit):
                build_docs.build_all_sources(self._sources(), build)

    def test_only_builds_just_that_source(self):
        built = []

        def build(source):
            built.append(source["id"])
            return [], {"id": source["id"]}

        build_docs.build_all_sources(self._sources(), build, only="local1")
        self.assertEqual(built, ["local1"])


@unittest.skipUnless(
    shutil.which("swift") and shutil.which("git"),
    "main() requires swift and git on PATH",
)
class MainCommandLine(unittest.TestCase):
    def _run(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--output-dir", str(Path(tmp) / "out"),
                 "--workspace", str(Path(tmp) / "ws"), *args],
                capture_output=True, text=True, timeout=120,
            )

    def test_unknown_only_id_exits_nonzero(self):
        result = self._run("--only", "no-such-source")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no-such-source", result.stdout + result.stderr)


class BuildPackageTargetsEndToEnd(TempDirTestCase):
    def _package(self):
        pkg = self.tmp / "pkg"
        catalog = pkg / "Sources" / "Lib" / "Lib.docc"
        catalog.mkdir(parents=True)
        return pkg, catalog

    def test_builds_target_and_installs_templates(self):
        pkg, catalog = self._package()
        common = _make_common_dir(self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        _write_tool(bin_dir, "swift", FAKE_SWIFT)
        staging = self.tmp / "staging"
        staging.mkdir()
        source = {"id": "pkg", "type": "local", "path": "pkg", "targets": ["Lib"]}
        with self.env_with(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}"):
            archives = build_docs._build_package_targets(
                source, pkg, common, staging, ["swift"], os.environ.copy()
            )
        self.assertEqual(archives, [staging / "pkg.doccarchive"])
        self.assertTrue((staging / "pkg.doccarchive" / "data").is_dir())
        self.assertTrue((catalog / "header.html").is_file())
        self.assertTrue((catalog / "favicon.ico").is_file())

    def test_multiple_targets_get_suffixed_names(self):
        pkg, _ = self._package()
        common = _make_common_dir(self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        _write_tool(bin_dir, "swift", FAKE_SWIFT)
        staging = self.tmp / "staging"
        staging.mkdir()
        source = {
            "id": "pkg", "type": "local", "path": "pkg",
            "targets": ["Lib", "Other"],
        }
        with self.env_with(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}"):
            archives = build_docs._build_package_targets(
                source, pkg, common, staging, ["swift"], os.environ.copy()
            )
        self.assertEqual(
            [a.name for a in archives],
            ["pkg-Lib.doccarchive", "pkg-Other.doccarchive"],
        )


class BuildSourceLocalEndToEnd(TempDirTestCase):
    def _setup(self, **source_extra):
        root = self.tmp / "root"
        catalog = root / "pkg" / "Docs.docc"
        catalog.mkdir(parents=True)
        common = _make_common_dir(self.tmp)
        staging = self.tmp / "staging"
        staging.mkdir()
        source = {
            "id": "pkg", "type": "local", "path": "pkg",
            "docc_catalog": "Docs.docc", **source_extra,
        }
        return root, common, staging, source

    def _build(self, root, common, staging, source):
        return build_docs.build_source(
            source, root, self.tmp / "ws", common, staging,
            self.fake_docc(), os.environ.copy(),
        )

    def test_local_source_builds_catalog(self):
        root, common, staging, source = self._setup()
        archives, entry = self._build(root, common, staging, source)
        self.assertEqual(archives, [staging / "pkg.doccarchive"])
        self.assertTrue(archives[0].is_dir())
        self.assertEqual(entry["id"], "pkg")
        self.assertEqual(entry["type"], "local")

    def test_missing_local_path_raises(self):
        root, common, staging, source = self._setup(path="nope")
        with self.assertRaises(RuntimeError):
            self._build(root, common, staging, source)

    def test_unknown_type_raises(self):
        root, common, staging, source = self._setup(type="bogus")
        with self.assertRaises(RuntimeError):
            self._build(root, common, staging, source)

    def test_source_env_and_docs_root_reach_preflight(self):
        root, common, staging, source = self._setup(
            env={"MY_FLAG": "1"},
            preflight='echo "$MY_FLAG:$DOCS_ROOT" > "$PWD/preflight.out"',
        )
        self._build(root, common, staging, source)
        written = (root / "pkg" / "preflight.out").read_text().strip()
        self.assertEqual(written, f"1:{root}")

    def test_failing_preflight_raises(self):
        root, common, staging, source = self._setup(preflight="exit 3")
        with self.assertRaises(subprocess.CalledProcessError):
            self._build(root, common, staging, source)


def _option(call, flag):
    """Return the argument that follows `flag` in a logged docc call."""
    return call[call.index(flag) + 1]


class MergeArchivesEndToEnd(TempDirTestCase):
    def _merge(self, out_name):
        log = self.tmp / "docc.log"
        archive = self.tmp / "a.doccarchive"
        archive.mkdir()
        with self.env_with(FAKE_DOCC_LOG=str(log)):
            build_docs.merge_archives(
                [archive], self.tmp / out_name, self.fake_docc(),
                landing_page_name="Swift Documentation",
            )
        return self.docc_calls(log)[0]

    def test_passes_landing_page_name_kind_and_style(self):
        call = self._merge("6.2")
        self.assertEqual(call[0], "merge")
        self.assertEqual(_option(call, "--synthesized-landing-page-name"), "Swift")
        self.assertEqual(_option(call, "--synthesized-landing-page-kind"), "Project")
        self.assertEqual(_option(call, "--synthesized-landing-page-topics-style"), "list")

    def test_replaces_existing_output(self):
        out = self.tmp / "out"
        (out / "stale").mkdir(parents=True)
        self._merge("out")
        self.assertFalse((out / "stale").exists())


class TransformStaticHostingArguments(TempDirTestCase):
    def test_passes_hosting_base_path_and_replaces_in_place(self):
        log = self.tmp / "docc.log"
        archive = self.tmp / "main.doccarchive"
        archive.mkdir()
        (archive / "index.html").write_text("ORIGINAL")
        with self.env_with(FAKE_DOCC_LOG=str(log)):
            build_docs.transform_static_hosting(archive, "docs/main", self.fake_docc())
        call = self.docc_calls(log)[0]
        self.assertEqual(call[:2], ["process-archive", "transform-for-static-hosting"])
        self.assertEqual(_option(call, "--hosting-base-path"), "docs/main")
        self.assertNotEqual((archive / "index.html").read_text(), "ORIGINAL")


class FinalizeCombinedArchiveFailures(TempDirTestCase):
    def _finalize(self, slug="main", **kwargs):
        src = self.tmp / "src.doccarchive"
        (src / "data").mkdir(parents=True, exist_ok=True)
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        return build_docs._finalize_combined_archive(
            [src], out, slug, self.fake_docc(), [], **kwargs
        )

    def test_merge_failure_records_only_the_merge(self):
        with self.env_with(FAKE_DOCC_FAIL_ON="merge"):
            succeeded, failed = self._finalize()
        self.assertEqual((succeeded, failed), ([], ["combined-merge"]))

    def test_transform_failure_records_only_the_transform(self):
        with self.env_with(FAKE_DOCC_FAIL_ON="process-archive"):
            succeeded, failed = self._finalize()
        self.assertEqual(
            succeeded,
            ["combined-merge", "eyebrow-suppression", "language-toggle-suppression"],
        )
        self.assertEqual(failed, ["static-hosting-transform"])

    def test_landing_page_name_does_not_depend_on_the_version(self):
        for slug in ("main", "6.2"):
            with self.subTest(slug=slug):
                log = self.tmp / f"docc-{slug}.log"
                with self.env_with(FAKE_DOCC_LOG=str(log)):
                    self._finalize(slug=slug)
                merge = self.docc_calls(log)[0]
                self.assertEqual(
                    _option(merge, "--synthesized-landing-page-name"), "Swift"
                )


class FinalizeCombinedArchiveFeatures(TempDirTestCase):
    def _finalize(self, **kwargs):
        src = self.tmp / "src.doccarchive"
        (src / "data").mkdir(parents=True, exist_ok=True)
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        return build_docs._finalize_combined_archive(
            [src], out, "main", self.fake_docc(), [], **kwargs
        ), out / "main"

    def test_canonical_link_injection_runs_when_configured(self):
        (succeeded, failed), combined = self._finalize(
            canonical_base_url="https://docs.swift.org/latest"
        )
        self.assertEqual(failed, [])
        self.assertEqual(succeeded[-1], "canonical-link-injection")
        self.assertIn("canonical", (combined / "index.html").read_text())

    def test_canonical_link_injection_is_skipped_when_not_configured(self):
        (succeeded, failed), combined = self._finalize()
        self.assertEqual(failed, [])
        self.assertNotIn("canonical-link-injection", succeeded)
        self.assertNotIn("canonical", (combined / "index.html").read_text())

    def _navigation(self, indexed_modules):
        index = {
            "schemaVersion": {"major": 0, "minor": 1, "patch": 2},
            "interfaceLanguages": {"swift": [{
                "title": "Swift Documentation",
                "path": "/documentation",
                "type": "module",
                "children": [
                    {"type": "module", "title": t, "path": p}
                    for t, p in indexed_modules
                ],
            }]},
        }
        index_file = self.tmp / "merged-index.json"
        index_file.write_text(json.dumps(index))
        navigation = {
            "version": 1,
            "groups": [{"title": "Lang", "modules": [
                {"source": "a", "path": "/documentation/swift"},
            ]}],
            "hidden": [],
        }
        return index_file, navigation

    def test_navigator_curation_succeeds_when_every_module_is_placed(self):
        index_file, navigation = self._navigation([("Swift", "/documentation/swift")])
        with self.env_with(FAKE_DOCC_MERGE_INDEX=str(index_file)):
            (succeeded, failed), _ = self._finalize(navigation=navigation)
        self.assertEqual(failed, [])
        self.assertEqual(succeeded[:2], ["combined-merge", "navigator-curation"])

    def test_navigator_curation_fails_on_an_unplaced_module(self):
        index_file, navigation = self._navigation([
            ("Swift", "/documentation/swift"),
            ("Extra", "/documentation/extra"),
        ])
        with self.env_with(FAKE_DOCC_MERGE_INDEX=str(index_file)):
            (succeeded, failed), _ = self._finalize(navigation=navigation)
        self.assertEqual(succeeded, ["combined-merge"])
        self.assertEqual(failed, ["navigator-curation"])

    def test_shared_favicons_survive_the_static_hosting_transform(self):
        common = _make_common_dir(self.tmp)
        with self.env_with(FAKE_DOCC_CLOBBER_FAVICONS="1"):
            (succeeded, failed), combined = self._finalize(common_dir=common)
        self.assertEqual(failed, [])
        self.assertEqual((combined / "favicon.ico").read_bytes(), b"ico")
        self.assertEqual((combined / "favicon.svg").read_text(), "<svg/>")


class BuildDoccCatalogEndToEnd(TempDirTestCase):
    def _convert(self, graph_dir_name=None, populate=True, extra_flags=None):
        common = _make_common_dir(self.tmp)
        source_dir = self.tmp / "checkout"
        (source_dir / "Foo.docc").mkdir(parents=True)
        source = {"id": "foo", "docc_catalog": "Foo.docc"}
        if extra_flags:
            source["extra_flags"] = extra_flags
        if graph_dir_name:
            source["additional_symbol_graph_dir"] = graph_dir_name
            graph_dir = source_dir / graph_dir_name
            graph_dir.mkdir(parents=True)
            if populate:
                (graph_dir / "Foo.symbols.json").write_text("{}")
        staging = self.tmp / "staging"
        staging.mkdir(exist_ok=True)
        log = self.tmp / "docc.log"
        with self.env_with(FAKE_DOCC_LOG=str(log)):
            archives = build_docs._build_docc_catalog(
                source, source_dir, common, staging, self.fake_docc(), os.environ.copy()
            )
        return archives, self.docc_calls(log), source_dir

    def test_passes_the_symbol_graph_dir_before_extra_flags(self):
        archives, calls, source_dir = self._convert(
            graph_dir_name=".build/symbols/Foo", extra_flags=["--final-flag"]
        )
        call = calls[0]
        self.assertEqual(
            _option(call, "--additional-symbol-graph-dir"),
            str(source_dir / ".build/symbols/Foo"),
        )
        self.assertLess(
            call.index("--additional-symbol-graph-dir"), call.index("--final-flag")
        )
        self.assertTrue(archives[0].is_dir())

    def test_omits_the_flag_when_not_configured(self):
        _, calls, _ = self._convert()
        self.assertNotIn("--additional-symbol-graph-dir", calls[0])

    def test_missing_symbol_graph_dir_fails_before_running_docc(self):
        common = _make_common_dir(self.tmp)
        source_dir = self.tmp / "checkout"
        (source_dir / "Foo.docc").mkdir(parents=True)
        source = {
            "id": "foo", "docc_catalog": "Foo.docc",
            "additional_symbol_graph_dir": "graphs",
        }
        log = self.tmp / "docc.log"
        with self.env_with(FAKE_DOCC_LOG=str(log)):
            with self.assertRaises(RuntimeError) as ctx:
                build_docs._build_docc_catalog(
                    source, source_dir, common, self.tmp, self.fake_docc(),
                    os.environ.copy(),
                )
        self.assertIn("graphs", str(ctx.exception))
        self.assertEqual(self.docc_calls(log), [])

    def test_empty_symbol_graph_dir_fails_before_running_docc(self):
        with self.assertRaises(RuntimeError):
            self._convert(graph_dir_name="graphs", populate=False)


@unittest.skipUnless(shutil.which("git"), "requires git")
class BuildSourceReuseCheckoutEndToEnd(TempDirTestCase):
    def _sources(self):
        return [
            {
                "id": "first", "type": "git", "repo": "https://x/y.git",
                "ref": "release/6.4.x", "docc_catalog": "A.docc",
            },
            {
                "id": "second", "type": "git", "reuse_checkout": "first",
                "docc_catalog": "B.docc",
            },
        ]

    def _build(self, workspace, sources, source):
        common = _make_common_dir(self.tmp)
        staging = self.tmp / "staging"
        staging.mkdir(exist_ok=True)
        return build_docs.build_source(
            source, self.tmp, workspace, common, staging, self.fake_docc(),
            os.environ.copy(), sources=sources,
        )

    def test_builds_from_the_named_checkout_and_records_its_ref_and_commit(self):
        workspace = self.tmp / "ws"
        checkout = workspace / "first"
        (checkout / "B.docc").mkdir(parents=True)
        _git(checkout, "init", "--quiet", "-b", "main")
        _git(checkout, "commit", "--quiet", "--allow-empty", "-m", "init")
        commit = _git(checkout, "rev-parse", "HEAD")
        sources = self._sources()
        log = self.tmp / "docc.log"
        with self.env_with(FAKE_DOCC_LOG=str(log)):
            archives, entry = self._build(workspace, sources, sources[1])
        convert = self.docc_calls(log)[0]
        self.assertEqual(convert[1], str(checkout / "B.docc"))
        self.assertEqual(entry["id"], "second")
        self.assertEqual(entry["ref"], "release/6.4.x")
        self.assertEqual(entry["commit"], commit)
        self.assertTrue(archives[0].is_dir())

    def test_missing_checkout_names_the_source_to_build_first(self):
        sources = self._sources()
        with self.assertRaises(RuntimeError) as ctx:
            self._build(self.tmp / "ws", sources, sources[1])
        self.assertIn("first", str(ctx.exception))


class BuildSourceStripLinuxAvailabilityEndToEnd(TempDirTestCase):
    def _build(self, **source_extra):
        root = self.tmp / "root"
        (root / "pkg" / "Docs.docc").mkdir(parents=True)
        common = _make_common_dir(self.tmp)
        staging = self.tmp / "staging"
        staging.mkdir()
        fixture = _make_archive_with_linux_platform(self.tmp, "Fixture.doccarchive")
        source = {
            "id": "pkg", "type": "local", "path": "pkg",
            "docc_catalog": "Docs.docc", **source_extra,
        }
        with self.env_with(FAKE_DOCC_COPY=str(fixture)):
            archives, _ = build_docs.build_source(
                source, root, self.tmp / "ws", common, staging,
                self.fake_docc(), os.environ.copy(),
            )
        payload = json.loads(
            (archives[0] / "data" / "documentation" / "foosymbol.json").read_text()
        )
        return {p["name"] for p in payload["metadata"]["platforms"]}

    def test_strips_only_linux_when_enabled(self):
        self.assertEqual(
            self._build(strip_linux_availability=True), {"Swift", "Xcode"}
        )

    def test_keeps_linux_when_not_enabled(self):
        self.assertIn("Linux", self._build())


if __name__ == "__main__":
    unittest.main()
