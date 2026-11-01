"""Edit Native 라우트 종단 테스트: python3 -m unittest tests.test_edit_native

rhwp 바이너리가 필요하다(`RHWP_BIN` 또는 PATH). 입력은 scaffold 로 직접 만든다.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
import edit  # noqa: E402
import rhwp_locate  # noqa: E402

SPEC = {"version": "1", "title": "편집 시험 문서", "blocks": [
    {"type": "heading", "level": 1, "text": "추진 배경"},
    {"type": "paragraph", "text": "2026년 계획을 정리한다."},
    {"type": "table", "rows": [["구분", "금액"], ["인건비", "1,000"]]},
    {"type": "heading", "level": 1, "text": "추진 계획"},
    {"type": "paragraph", "text": "세부 일정은 별도로 정한다."},
]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EditNativeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        for sub in ("sources", "analysis", "exports", "validation", "history", "capsules"):
            (self.project / sub).mkdir(parents=True)
        spec = Path(self.tmp.name) / "spec.json"
        spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
        self.source = self.project / "sources/doc.hwpx"
        subprocess.run([self.rhwp, "scaffold", str(spec), "-o", str(self.source)], check=True,
                       capture_output=True)
        self.source_sha = sha(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, plan: dict):
        path = Path(self.tmp.name) / "plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(SCRIPTS / "edit.py"), "apply", str(self.project), str(path),
                               "--rhwp", self.rhwp], capture_output=True, text=True, timeout=900)
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        return proc, report

    def test_inspect_lists_tables_and_outline(self):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "edit.py"), "inspect", str(self.project),
                               "sources/doc.hwpx", "--rhwp", self.rhwp], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.project / "analysis/doc.inspect.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["tables"]), 1)
        self.assertEqual([o["heading"] for o in data["outline"]], ["추진 배경", "추진 계획"])
        self.assertEqual(data["sha256"], self.source_sha)

    def test_chained_edits_keep_source_and_link_capsules(self):
        proc, first = self.apply({"planVersion": "1.0", "input": "sources/doc.hwpx", "steps": [
            {"action": "replace_text", "find": "2026년", "replace": "2027년"}]})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(first["ok"], first["findings"])
        self.assertEqual(first["inputSha256"], self.source_sha)

        proc, second = self.apply({"planVersion": "1.0", "input": first["output"], "steps": [
            {"action": "set_cell", "table": 0, "row": 1, "col": 1, "text": "2,000"}]})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(second["ok"], second["findings"])
        self.assertEqual(second["parentCapsule"], first["capsule"])
        self.assertEqual(Path(second["output"]).name.count("_edit_"), 1, "편집을 이어도 이름이 길어지지 않는다")
        self.assertNotEqual(second["output"], first["output"])
        self.assertNotEqual(second["capsule"], first["capsule"])
        self.assertEqual(sha(self.source), self.source_sha, "원본 사본은 바뀌지 않는다")

        txt_dir = Path(self.tmp.name) / "txt"
        subprocess.run([self.rhwp, "export-text", str(self.project / second["output"]), "-o", str(txt_dir)],
                       check=True, capture_output=True)
        text = "".join(p.read_text(encoding="utf-8") for p in txt_dir.glob("*.txt"))
        self.assertIn("2027년", text)
        self.assertIn("2,000", text)

        lineage = subprocess.run([self.rhwp, "lineage", second["capsule"], "--deep", "--json"],
                                 cwd=self.project, capture_output=True, text=True)
        self.assertEqual(lineage.returncode, 0, lineage.stdout + lineage.stderr)
        env = json.loads(lineage.stdout)
        self.assertEqual(env["depth"], 2)
        self.assertIsNone(env["brokenAt"])

    def test_missing_target_is_not_saved(self):
        proc, report = self.apply({"planVersion": "1.0", "input": "sources/doc.hwpx", "steps": [
            {"action": "set_cell", "table": 5, "row": 0, "col": 0, "text": "x"}]})
        self.assertEqual(proc.returncode, 1)
        self.assertIn(report["findings"][0]["code"], ("R211", "R212"))
        self.assertEqual(list((self.project / "exports").iterdir()), [], "실패한 편집은 저장하지 않는다")

    def test_output_outside_exports_is_refused(self):
        proc, report = self.apply({"planVersion": "1.0", "input": "sources/doc.hwpx",
                                   "output": "sources/doc.hwpx", "steps": [
                                       {"action": "replace_text", "find": "2026년", "replace": "2027년"}]})
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report["findings"][0]["code"], "E203")
        self.assertEqual(sha(self.source), self.source_sha)


class RenderDiffJudgeTest(unittest.TestCase):
    def test_unedited_page_movement_is_an_error(self):
        env = {"pageCountA": 2, "pageCountB": 2, "pages": [
            {"page": 0, "maxDisp": 40.0, "structureMismatch": True},
            {"page": 1, "maxDisp": 12.5, "structureMismatch": False}]}
        found = edit.judge_render_diff(env, [0], 1.0)
        self.assertEqual([f["code"] for f in found], ["R202"])
        self.assertIn("2쪽", found[0]["message"])

    def test_edited_page_change_and_page_count_change(self):
        env = {"pageCountA": 2, "pageCountB": 3, "pageCountMismatch": True, "pages": [
            {"page": 0, "maxDisp": 40.0, "structureMismatch": True}]}
        self.assertEqual([f["code"] for f in edit.judge_render_diff(env, [0], 1.0)], ["W211"])


if __name__ == "__main__":
    unittest.main()
