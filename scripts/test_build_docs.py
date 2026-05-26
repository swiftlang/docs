"""Tests for build_docs.py.

Run from the scripts/ directory (or repo root) with:
    python3 -m unittest scripts/test_build_docs.py
"""

import contextlib
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_docs  # noqa: E402


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
            "url": "https://example.com/Swift.doccarchive.zip",
            "docc_archive_name": "Swift.doccarchive",
            "format": "zip",
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


def _make_tarball(tmp_path, archive_name, wrap_top_level=True):
    """Build a tar.gz containing a minimal .doccarchive directory.

    When wrap_top_level is True, the tarball's root entry is the .doccarchive
    directory itself; when False, its contents are at the tarball root.
    Returns the path to the tarball.
    """
    archive_dir = tmp_path / archive_name
    archive_dir.mkdir()
    (archive_dir / "metadata.json").write_text('{"bundleDisplayName":"Test"}')
    (archive_dir / "index").mkdir()
    (archive_dir / "index" / "index.json").write_text("{}")

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


if __name__ == "__main__":
    unittest.main()
