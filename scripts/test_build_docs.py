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

Run from the scripts/ directory (or repo root) with:
    python3 -m unittest scripts/test_build_docs.py
"""

import contextlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_docs  # noqa: E402
import strip_availability  # noqa: E402


def _validate(config):
    """Call validate_sources with stdout suppressed. Returns None on success,
    or the captured stdout text on SystemExit."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            build_docs.validate_sources(config)
        except SystemExit:
            return buf.getvalue()
    return None


def _wrap(entry):
    return {"version": "main", "sources": [entry]}


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

    def test_strip_availability_true_is_valid(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.zip",
            "docc_archive_name": "Swift.doccarchive",
            "format": "zip",
            "strip_availability": True,
        }
        self.assertIsNone(_validate(_wrap(entry)))

    def test_strip_availability_false_is_valid(self):
        entry = {
            "id": "stdlib",
            "type": "archive",
            "url": "https://example.com/Swift.doccarchive.tar.gz",
            "docc_archive_name": "Swift.doccarchive",
            "strip_availability": False,
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


class TransformStaticHosting(unittest.TestCase):
    def test_invokes_docc_with_correct_args_and_replaces_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "main.doccarchive"
            archive.mkdir()
            (archive / "index.html").write_text("ORIGINAL")

            captured = {}

            def fake_run(cmd, check, **kw):
                captured["cmd"] = cmd
                # Simulate docc producing the transformed archive at --output-path.
                idx = cmd.index("--output-path") + 1
                out_path = Path(cmd[idx])
                out_path.mkdir(parents=True, exist_ok=True)
                (out_path / "index.html").write_text("TRANSFORMED")
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                build_docs.transform_static_hosting(archive, "main", ["docc"])

            self.assertEqual(
                captured["cmd"][:3],
                ["docc", "process-archive", "transform-for-static-hosting"],
            )
            self.assertIn("--hosting-base-path", captured["cmd"])
            hbp_idx = captured["cmd"].index("--hosting-base-path") + 1
            self.assertEqual(captured["cmd"][hbp_idx], "main")
            # Archive replaced in place.
            self.assertTrue(archive.is_dir())
            self.assertEqual((archive / "index.html").read_text(), "TRANSFORMED")

    def test_failure_leaves_original_archive_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "main.doccarchive"
            archive.mkdir()
            (archive / "index.html").write_text("ORIGINAL")

            def fake_run(cmd, check, **kw):
                raise subprocess.CalledProcessError(1, cmd)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(subprocess.CalledProcessError):
                    build_docs.transform_static_hosting(archive, "main", ["docc"])

            self.assertTrue(archive.is_dir())
            self.assertEqual((archive / "index.html").read_text(), "ORIGINAL")

    def test_raises_when_docc_cmd_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "main.doccarchive"
            archive.mkdir()
            with self.assertRaises(RuntimeError):
                build_docs.transform_static_hosting(archive, "main", [])


class FindSwiftly(unittest.TestCase):
    def test_returns_command_when_on_path(self):
        with mock.patch.object(build_docs.shutil, "which", return_value="/usr/local/bin/swiftly"):
            self.assertEqual(build_docs.find_swiftly(), ["swiftly"])

    def test_returns_empty_when_absent(self):
        with mock.patch.object(build_docs.shutil, "which", return_value=None):
            self.assertEqual(build_docs.find_swiftly(), [])


class FindSwiftCommand(unittest.TestCase):
    def test_prefers_swiftly_when_available(self):
        def fake_which(name):
            return f"/fake/{name}" if name == "swiftly" else None
        with mock.patch.object(build_docs.shutil, "which", side_effect=fake_which):
            self.assertEqual(build_docs.find_swift_command(), ["swiftly", "run", "swift"])

    def test_falls_back_to_direct_swift(self):
        def fake_which(name):
            return "/usr/bin/swift" if name == "swift" else None
        with mock.patch.object(build_docs.shutil, "which", side_effect=fake_which):
            self.assertEqual(build_docs.find_swift_command(), ["swift"])

    def test_returns_empty_when_neither_present(self):
        with mock.patch.object(build_docs.shutil, "which", return_value=None):
            self.assertEqual(build_docs.find_swift_command(), [])


class FindDoccCommandPrefersSwiftly(unittest.TestCase):
    def test_prefers_swiftly_when_available(self):
        def fake_which(name):
            return f"/fake/{name}" if name == "swiftly" else None
        with mock.patch.object(build_docs.shutil, "which", side_effect=fake_which):
            self.assertEqual(
                build_docs.find_docc_command(),
                ["swiftly", "run", "docc"],
            )


class EnsureToolchainInstalled(unittest.TestCase):
    def test_no_swift_version_file_skips_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            calls = []

            def fake_run(cmd, check, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                build_docs.ensure_toolchain_installed(source_dir, ["swiftly"])

            self.assertEqual(calls, [])

    def test_swift_version_file_triggers_install_in_source_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / ".swift-version").write_text("6.0.1\n")

            captured = {}

            def fake_run(cmd, check, **kw):
                captured["cmd"] = cmd
                captured["cwd"] = kw.get("cwd")
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                build_docs.ensure_toolchain_installed(source_dir, ["swiftly"])

            self.assertEqual(captured["cmd"][:2], ["swiftly", "install"])
            self.assertIn("--assume-yes", captured["cmd"])
            self.assertEqual(captured["cwd"], str(source_dir))

    def test_no_swiftly_skips_even_with_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / ".swift-version").write_text("6.0.1\n")
            calls = []

            def fake_run(cmd, check, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                build_docs.ensure_toolchain_installed(source_dir, [])

            self.assertEqual(calls, [])


class ReadSwiftToolsVersion(unittest.TestCase):
    def _write_package(self, dir_, first_line):
        pkg = Path(dir_) / "Package.swift"
        pkg.write_text(first_line + "\nimport PackageDescription\n")
        return pkg

    def test_parses_simple_major_minor(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "// swift-tools-version: 6.3")
            self.assertEqual(build_docs.read_swift_tools_version(Path(tmp)), "6.3")

    def test_parses_no_space_after_colon(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "// swift-tools-version:5.10")
            self.assertEqual(build_docs.read_swift_tools_version(Path(tmp)), "5.10")

    def test_strips_patch_to_major_minor(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "// swift-tools-version:5.5.2")
            self.assertEqual(build_docs.read_swift_tools_version(Path(tmp)), "5.5")

    def test_handles_extended_form_with_semicolon(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "// swift-tools-version:5.5;something")
            self.assertEqual(build_docs.read_swift_tools_version(Path(tmp)), "5.5")

    def test_returns_none_when_no_package_swift(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(build_docs.read_swift_tools_version(Path(tmp)))

    def test_returns_none_when_first_line_unrelated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "// some other comment")
            self.assertIsNone(build_docs.read_swift_tools_version(Path(tmp)))

    def test_returns_none_when_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Package.swift").write_text("")
            self.assertIsNone(build_docs.read_swift_tools_version(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
