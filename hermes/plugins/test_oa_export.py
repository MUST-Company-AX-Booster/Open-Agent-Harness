import importlib.util
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("oa_export", Path(__file__).parent / "oa_export" / "__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PmExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "exports"
        mod._export_root = lambda: self.root

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_saved_and_tagged(self):
        out = json.loads(mod.save_markdown({"filename": "sprint-summary-2026-10-03", "content": "# Hi\n"}))
        path = Path(out["path"])
        self.assertEqual(path.name, "sprint-summary-2026-10-03.md")
        self.assertEqual(path.read_text(encoding="utf-8"), "# Hi\n")
        self.assertEqual(out["media_tag"], f"MEDIA:{path}")
        self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))

    def test_names_cannot_escape_or_forge_tags(self):
        for raw in ["../../etc/passwd", "/opt/data/.env", "a\\..\\b", "MEDIA:/etc/x", "", None, "...", "주간 보고"]:
            path = Path(json.loads(mod.save_markdown({"filename": raw, "content": "x"}))["path"])
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()), raw)
            self.assertTrue(path.name.endswith(".md"))
            self.assertNotIn(":", path.name)
        self.assertEqual(mod._safe_name("주간 보고", ".md"), "주간-보고.md")

    def test_zip_has_files_and_readme(self):
        out = json.loads(mod.save_zip({"filename": "report.zip", "files": [
            {"filename": "summary", "content": "# S"}, {"filename": "../people.md", "content": "# P"}]}))
        with zipfile.ZipFile(out["path"]) as z:
            self.assertEqual(sorted(z.namelist()), ["README.md", "people.md", "summary.md"])
            self.assertEqual(z.read("summary.md").decode(), "# S")
            self.assertIn("- people.md", z.read("README.md").decode())

    def test_rejects_bad_input_without_writing(self):
        bad = [
            mod.save_markdown({"filename": "a", "content": "  "}),
            mod.save_markdown({"filename": "a", "content": "x" * (mod.MAX_BYTES + 1)}),
            mod.save_zip({"filename": "z", "files": []}),
            mod.save_zip({"filename": "z", "files": [{"filename": "a", "content": "1"}, {"filename": "a.md", "content": "2"}]}),
            mod.save_zip({"filename": "z", "files": [{"filename": "a", "content": "1"}] * (mod.MAX_FILES + 1)}),
        ]
        for result in bad:
            self.assertIn("error", json.loads(result))
        self.assertFalse(self.root.exists() and any(self.root.iterdir()))

    def test_old_exports_are_cleaned(self):
        old = Path(json.loads(mod.save_markdown({"filename": "old", "content": "x"}))["path"]).parent
        stale = time.time() - mod.KEEP_SECONDS - 60
        os.utime(old, (stale, stale))
        mod.save_markdown({"filename": "new", "content": "y"})
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
