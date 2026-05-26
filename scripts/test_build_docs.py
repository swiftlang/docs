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

Run from the scripts/ directory (or repository root) with:
    python3 -m unittest scripts/test_build_docs.py
"""

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
    """Run validate_sources and return joined error text, or None if valid.

    Joining preserves the assertion shape used by existing tests
    (assertIn("url", output)) without needing per-error matching.
    """
    errors = build_docs.validate_sources(config)
    return "\n".join(errors) if errors else None


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


class DiscoverTools(unittest.TestCase):
    def test_swiftly_present_routes_everything_through_swiftly(self):
        with mock.patch.object(build_docs.shutil, "which") as which:
            which.side_effect = lambda name: f"/fake/{name}" if name == "swiftly" else None
            tools = build_docs.discover_tools()
        self.assertEqual(tools.swiftly, ["swiftly"])
        self.assertEqual(tools.swift, ["swiftly", "run", "swift"])
        self.assertEqual(tools.docc, ["swiftly", "run", "docc"])

    def test_no_swiftly_uses_direct_swift(self):
        present = {"swift": "/usr/bin/swift"}
        with mock.patch.object(build_docs.shutil, "which", side_effect=present.get):
            tools = build_docs.discover_tools()
        self.assertEqual(tools.swiftly, [])
        self.assertEqual(tools.swift, ["swift"])
        self.assertEqual(tools.docc, [])

    def test_xcrun_finds_docc_when_no_swiftly(self):
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
        self.assertEqual(tools.swiftly, [])
        self.assertEqual(tools.swift, [])
        self.assertEqual(tools.docc, [])


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


class GetActiveSwiftVersion(unittest.TestCase):
    def test_parses_apple_swift_format(self):
        stdout = (
            "Apple Swift version 6.1.2 (swiftlang-6.1.2.0.55 "
            "clang-1700.0.13.5)\n"
            "Target: arm64-apple-macosx15.0\n"
        )
        result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with mock.patch.object(build_docs.shutil, "which", return_value="/usr/bin/swift"):
            with mock.patch.object(build_docs.subprocess, "run", return_value=result):
                self.assertEqual(build_docs.get_active_swift_version(), (6, 1))

    def test_parses_oss_swift_format(self):
        stdout = (
            "Swift version 5.10 (swift-5.10-RELEASE)\n"
            "Target: x86_64-unknown-linux-gnu\n"
        )
        result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with mock.patch.object(build_docs.shutil, "which", return_value="/usr/bin/swift"):
            with mock.patch.object(build_docs.subprocess, "run", return_value=result):
                self.assertEqual(build_docs.get_active_swift_version(), (5, 10))

    def test_returns_none_when_swift_missing(self):
        with mock.patch.object(build_docs.shutil, "which", return_value=None):
            self.assertIsNone(build_docs.get_active_swift_version())

    def test_returns_none_when_output_unparseable(self):
        result = subprocess.CompletedProcess([], 0, stdout="garbage\n", stderr="")
        with mock.patch.object(build_docs.shutil, "which", return_value="/usr/bin/swift"):
            with mock.patch.object(build_docs.subprocess, "run", return_value=result):
                self.assertIsNone(build_docs.get_active_swift_version())

    def test_returns_none_when_swift_invocation_fails(self):
        def fake_run(*a, **kw):
            raise subprocess.CalledProcessError(1, a[0])

        with mock.patch.object(build_docs.shutil, "which", return_value="/usr/bin/swift"):
            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                self.assertIsNone(build_docs.get_active_swift_version())


class SelectSwiftToolchain(unittest.TestCase):
    def _write_package(self, dir_, tools_version):
        (Path(dir_) / "Package.swift").write_text(
            f"// swift-tools-version: {tools_version}\nimport PackageDescription\n"
        )

    def test_no_swiftly_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "6.3")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swift"], Path(tmp), [], active_swift_version=(5, 0)
                )
            self.assertEqual(result, ["swift"])
            self.assertEqual(calls, [])

    def test_swift_version_file_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "6.3")
            (Path(tmp) / ".swift-version").write_text("6.3\n")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=(5, 0),
                )
            self.assertEqual(result, ["swiftly", "run", "swift"])
            self.assertEqual(calls, [])

    def test_no_tools_version_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=(6, 1),
                )
            self.assertEqual(result, ["swiftly", "run", "swift"])
            self.assertEqual(calls, [])

    def test_active_equal_to_required_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "6.1")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=(6, 1),
                )
            self.assertEqual(result, ["swiftly", "run", "swift"])
            self.assertEqual(calls, [])

    def test_active_newer_than_required_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "5.10")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=(6, 1),
                )
            self.assertEqual(result, ["swiftly", "run", "swift"])
            self.assertEqual(calls, [])

    def test_active_older_installs_and_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "6.3")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=(6, 1),
                )
            self.assertEqual(result, ["swiftly", "run", "swift", "+6.3"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][:3], ["swiftly", "install", "6.3"])
            self.assertIn("--assume-yes", calls[0])

    def test_active_unknown_installs_and_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "6.3")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=None,
                )
            self.assertEqual(result, ["swiftly", "run", "swift", "+6.3"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][:3], ["swiftly", "install", "6.3"])

    def test_install_failure_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_package(tmp, "6.3")

            def fake_run(cmd, **kw):
                raise subprocess.CalledProcessError(1, cmd)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                result = build_docs.select_swift_toolchain(
                    ["swiftly", "run", "swift"],
                    Path(tmp),
                    ["swiftly"],
                    active_swift_version=(6, 1),
                )
            self.assertEqual(result, ["swiftly", "run", "swift"])


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

    def test_merge_failure_records_combined_merge_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "a.doccarchive"
            archive.mkdir()

            def fake_run(cmd, **kw):
                raise subprocess.CalledProcessError(1, cmd)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                succeeded, failed = build_docs._finalize_combined_archive(
                    [archive], tmp_path, "main", ["docc"], prior_failed=[]
                )
        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["combined-merge"])

    def test_transform_failure_records_only_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "a.doccarchive"
            archive.mkdir()
            (archive / "index.html").write_text("ok")

            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                # First call is `docc merge`; succeed by creating output dir.
                if "merge" in cmd:
                    out_idx = cmd.index("--output-path") + 1
                    Path(cmd[out_idx]).mkdir(parents=True, exist_ok=True)
                    return subprocess.CompletedProcess(cmd, 0)
                # Second call is the transform; fail.
                raise subprocess.CalledProcessError(1, cmd)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                succeeded, failed = build_docs._finalize_combined_archive(
                    [archive], tmp_path, "main", ["docc"], prior_failed=[]
                )
        self.assertEqual(succeeded, ["combined-merge"])
        self.assertEqual(failed, ["static-hosting-transform"])

    def test_full_success_records_both_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "a.doccarchive"
            archive.mkdir()
            (archive / "index.html").write_text("ok")

            def fake_run(cmd, **kw):
                if "merge" in cmd:
                    out_idx = cmd.index("--output-path") + 1
                    out = Path(cmd[out_idx])
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "index.html").write_text("merged")
                    return subprocess.CompletedProcess(cmd, 0)
                # Transform step: simulate docc producing the transformed
                # archive at --output-path.
                out_idx = cmd.index("--output-path") + 1
                out = Path(cmd[out_idx])
                out.mkdir(parents=True, exist_ok=True)
                (out / "index.html").write_text("transformed")
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(build_docs.subprocess, "run", side_effect=fake_run):
                succeeded, failed = build_docs._finalize_combined_archive(
                    [archive], tmp_path, "main", ["docc"], prior_failed=[]
                )
        self.assertEqual(succeeded, ["combined-merge", "static-hosting-transform"])
        self.assertEqual(failed, [])


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


if __name__ == "__main__":
    unittest.main()