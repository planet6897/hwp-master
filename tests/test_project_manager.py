import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import project_manager


class ImportSourcesTests(unittest.TestCase):
    def test_timeout_keeps_source_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = root / "plan.hwp"
            document.write_bytes(b"original")
            notes = root / "notes.txt"
            notes.write_text("notes", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(project_manager.cmd_init(Namespace(root=root, name="test")), 0)
                project = Path(stdout.getvalue().strip())
                args = Namespace(project=project, files=[document, notes], rhwp=None)
                with patch.object(project_manager.rhwp_locate, "locate", return_value={"path": "rhwp"}):
                    with patch.object(project_manager, "_run", side_effect=subprocess.TimeoutExpired("rhwp", 600)):
                        self.assertEqual(project_manager.cmd_import_sources(args), 1)
                    failed, imported_notes = project_manager.load_project(project)["sources"]
                    self.assertEqual(failed["exit"], 1)
                    self.assertIn("timed out", failed["stderr"])
                    self.assertEqual(imported_notes["extract"], "copied")

                    args.files = [document]
                    result = subprocess.CompletedProcess([], 0, stderr="")
                    with patch.object(project_manager, "_run", return_value=result) as run:
                        self.assertEqual(project_manager.cmd_import_sources(args), 0)
                    self.assertEqual(Path(run.call_args.args[0][2]), project / "sources" / document.name)

            recovered, unchanged_notes = project_manager.load_project(project)["sources"]
            self.assertEqual(recovered["exit"], 0)
            self.assertEqual(recovered["sha256"], failed["sha256"])
            self.assertEqual(recovered["analysis"], "analysis/plan-2")
            self.assertEqual(unchanged_notes, imported_notes)

    def test_same_stem_sources_keep_separate_markdown_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_source = root / "plan.txt"
            markdown_source = root / "plan.md"
            text_source.write_text("text version", encoding="utf-8")
            markdown_source.write_text("markdown version", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(project_manager.cmd_init(Namespace(root=root, name="test")), 0)
                project = Path(stdout.getvalue().strip())
                args = Namespace(project=project, files=[text_source, markdown_source], rhwp=None)
                with patch.object(project_manager.rhwp_locate, "locate", return_value={"path": "rhwp"}):
                    self.assertEqual(project_manager.cmd_import_sources(args), 0)

            entries = project_manager.load_project(project)["sources"]
            self.assertEqual([entry["analysis"] for entry in entries], ["analysis/plan", "analysis/plan-2"])
            for entry, expected in zip(entries, ["text version", "markdown version"]):
                output = project / entry["analysis"] / "plan.md"
                self.assertEqual(output.read_text(encoding="utf-8"), expected)
