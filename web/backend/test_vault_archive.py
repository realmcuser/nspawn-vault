"""Regression test for vault_archive's path-safety logic - the one place
in this app that resolves an arbitrary, user-supplied relative path against
a directory tree it doesn't otherwise control (a container's own backed-up
filesystem, which can contain symlinks pointing anywhere). A regression
here (e.g. someone "simplifying" resolve_safe_path in a future change)
would silently reopen a path-traversal hole in the file browser and
single-file download endpoints - this only guards against exactly that,
it is not a general test suite for the module.

Run with: python3 -m unittest test_vault_archive -v
(stdlib unittest only - deliberately no pytest dependency, since
requirements.txt here also builds the RPM's shipped runtime venv, see
CLAUDE.md gotcha #4/#5 - a test-only dependency has no business in there.)
"""
import os
import tempfile
import unittest

import vault_archive


class ResolveSafePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "snap-root")
        os.makedirs(os.path.join(self.root, "subdir"))
        with open(os.path.join(self.root, "normal.txt"), "w") as f:
            f.write("normal file")
        with open(os.path.join(self.root, "subdir", "nested.txt"), "w") as f:
            f.write("nested file")
        with open(os.path.join(self.tmp.name, "secret-outside.txt"), "w") as f:
            f.write("should never be reachable")
        os.symlink("/etc/passwd", os.path.join(self.root, "evil-abs-symlink"))
        os.symlink("../secret-outside.txt", os.path.join(self.root, "evil-rel-symlink"))
        os.symlink("subdir", os.path.join(self.root, "good-symlink-dir"))

    def tearDown(self):
        self.tmp.cleanup()

    def assert_allowed(self, rel_path, expected_real_suffix=None):
        resolved = vault_archive.resolve_safe_path(self.root, rel_path)
        root_real = os.path.realpath(self.root)
        self.assertTrue(
            resolved == root_real or resolved.startswith(root_real + os.sep),
            f"{rel_path!r} resolved outside the root: {resolved}",
        )
        if expected_real_suffix is not None:
            self.assertTrue(resolved.endswith(expected_real_suffix))

    def assert_blocked(self, rel_path):
        with self.assertRaises(vault_archive.PathEscapeError):
            vault_archive.resolve_safe_path(self.root, rel_path)

    def test_normal_file_allowed(self):
        self.assert_allowed("normal.txt")

    def test_nested_file_allowed(self):
        self.assert_allowed("subdir/nested.txt")

    def test_root_itself_allowed(self):
        self.assert_allowed("")

    def test_dotdot_traversal_blocked(self):
        self.assert_blocked("../secret-outside.txt")
        self.assert_blocked("../../etc/passwd")
        self.assert_blocked("subdir/../../secret-outside.txt")

    def test_absolute_symlink_escape_blocked(self):
        self.assert_blocked("evil-abs-symlink")

    def test_relative_symlink_escape_blocked(self):
        self.assert_blocked("evil-rel-symlink")

    def test_legitimate_symlink_into_root_allowed(self):
        # good-symlink-dir -> subdir, still inside the root - browsing
        # *into* it must work, and must resolve to the real subdir target.
        self.assert_allowed("good-symlink-dir/nested.txt", expected_real_suffix="subdir/nested.txt")

    def test_escape_through_legitimate_symlink_blocked(self):
        self.assert_blocked("good-symlink-dir/../../secret-outside.txt")


class ListSnapshotDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "snap-root")
        os.makedirs(os.path.join(self.root, "adir"))
        for i in range(5):
            with open(os.path.join(self.root, f"file{i}.txt"), "w") as f:
                f.write("x" * i)
        os.symlink("/etc/passwd", os.path.join(self.root, "evil-symlink"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_all_entries_with_correct_types(self):
        result = vault_archive.list_snapshot_dir(self.root, "")
        self.assertEqual(result["total"], 7)  # adir + 5 files + evil-symlink
        by_name = {e["name"]: e for e in result["entries"]}
        self.assertTrue(by_name["adir"]["is_dir"])
        self.assertFalse(by_name["file0.txt"]["is_dir"])
        self.assertTrue(by_name["evil-symlink"]["is_symlink"])
        # listing a symlink must never follow it - just report what it is
        self.assertEqual(by_name["evil-symlink"]["symlink_target"], "/etc/passwd")

    def test_pagination(self):
        page1 = vault_archive.list_snapshot_dir(self.root, "", offset=0, limit=3)
        page2 = vault_archive.list_snapshot_dir(self.root, "", offset=3, limit=3)
        self.assertEqual(len(page1["entries"]), 3)
        self.assertEqual(page1["total"], 7)
        self.assertEqual(page2["total"], 7)
        names1 = {e["name"] for e in page1["entries"]}
        names2 = {e["name"] for e in page2["entries"]}
        self.assertEqual(names1 & names2, set())  # no overlap between pages

    def test_listing_a_file_raises(self):
        with self.assertRaises(NotADirectoryError):
            vault_archive.list_snapshot_dir(self.root, "file0.txt")

    def test_listing_respects_path_escape_protection(self):
        with self.assertRaises(vault_archive.PathEscapeError):
            vault_archive.list_snapshot_dir(self.root, "../../../")


if __name__ == "__main__":
    unittest.main()
