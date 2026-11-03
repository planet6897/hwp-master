"""Beautify 라우트 종단 테스트: python3 -m unittest tests.test_beautify (rhwp 필요)"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import beautify  # noqa: E402
import rhwp_locate  # noqa: E402
from outline_fixture import make_outline_doc  # noqa: E402

TEXTS = ["1. 추진 배경", "가. 현황", "본문 문장입니다.", "나. 문제점", "1) 세부 사항",
         "3. 건너뛴 번호", "2. 추진 계획", "1. 차례 항목\t3"]


class BeautifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        (self.project / "sources").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "beautify.py"), *args, "--rhwp", self.rhwp],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        return proc, json.loads(proc.stdout.strip().splitlines()[-1])

    def propose(self, name="doc.hwpx", *extra):
        return self.run_script("propose", str(self.project), f"sources/{name}", *extra)

    def apply(self, plan):
        proc, summary = self.run_script("apply", str(self.project), plan)
        report = json.loads((self.project / summary["report"]).read_text(encoding="utf-8"))
        return proc, summary, report

    def text(self, path: Path) -> str:
        out = Path(self.tmp.name) / f"txt_{path.stem}"
        subprocess.run([self.rhwp, "export-text", str(path), "-o", str(out)], check=True, capture_output=True)
        return "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("*.txt")))

    def test_typed_markers_become_outline_numbers_with_same_text(self):
        src = make_outline_doc(self.rhwp, self.project / "sources/doc.hwpx", TEXTS)
        proc, summary = self.propose()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        plan = json.loads((self.project / summary["plan"]).read_text(encoding="utf-8"))
        self.assertEqual([(i["para"], i["level"], i["marker"]) for i in plan["items"]],
                         [(0, 1, "1."), (1, 2, "가."), (3, 2, "나."), (4, 3, "1)"), (6, 1, "2.")])
        codes = {f["code"] for f in plan["findings"]}
        self.assertIn("B101", codes, "자동 순서와 다른 '3.' 은 빼야 한다")
        self.assertIn("B103", codes, "차례 항목은 빼야 한다")

        proc, summary, report = self.apply(summary["plan"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.project / summary["output"]
        self.assertEqual(report["changed"], 5)
        self.assertEqual(re.sub(r"\s+", "", self.text(src)), re.sub(r"\s+", "", self.text(out)))
        heads = {p["para"]: p["head"] for p in beautify.read_hwpx(out)["sections"][0]["paras"]}
        self.assertEqual([heads[i] for i in (0, 1, 3, 4, 6)],
                         [("OUTLINE", 0), ("OUTLINE", 1), ("OUTLINE", 1), ("OUTLINE", 2), ("OUTLINE", 0)])
        self.assertEqual(heads[5][0], "NONE")
        paras = beautify.read_hwpx(out)["sections"][0]["paras"]
        self.assertEqual(paras[0]["text"], "추진 배경", "손으로 친 기호는 지운다")
        self.assertEqual(report["inputSha256"], plan["inputSha256"])
        self.assertIn("B150", {f["code"] for f in report["findings"]}, "한글 줄바꿈 변화 가능성은 항상 알린다")

    def test_document_without_gongmun_outline_numbering_is_refused(self):
        make_outline_doc(self.rhwp, self.project / "sources/doc.hwpx", TEXTS[:3],
                         formats=[(lv, "DIGIT", "^N") for lv in range(1, 8)])
        proc, summary = self.propose()
        self.assertEqual(proc.returncode, 1)
        plan = json.loads((self.project / summary["plan"]).read_text(encoding="utf-8"))
        self.assertIn("B001", {f["code"] for f in plan["findings"]})
        proc, summary, report = self.apply(summary["plan"])
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(any((self.project / "exports").glob("*.hwpx")))

    def test_item_that_would_change_text_is_left_alone(self):
        make_outline_doc(self.rhwp, self.project / "sources/doc.hwpx", TEXTS[:3])
        _, summary = self.propose()
        plan_path = self.project / summary["plan"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["items"][1]["level"] = 3  # '가.' 를 3수준으로 잘못 지정 → 자동 번호는 '1)' 이 된다
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        proc, summary, report = self.apply(summary["plan"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(report["changed"], 1)
        self.assertIn("B106", {f["code"] for f in report["findings"]})
        paras = beautify.read_hwpx(self.project / summary["output"])["sections"][0]["paras"]
        self.assertEqual((paras[1]["text"], paras[1]["head"][0]), ("가. 현황", "NONE"))

    def test_changed_input_is_refused(self):
        src = make_outline_doc(self.rhwp, self.project / "sources/doc.hwpx", TEXTS[:3])
        _, summary = self.propose()
        make_outline_doc(self.rhwp, src, TEXTS[:2])
        proc, summary, report = self.apply(summary["plan"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("B002", {f["code"] for f in report["findings"]})

    def test_bind_styles_needs_outline_styles(self):
        make_outline_doc(self.rhwp, self.project / "sources/doc.hwpx", TEXTS[:3])
        proc, summary = self.propose("doc.hwpx", "--bind-styles")
        self.assertEqual(proc.returncode, 1)
        plan = json.loads((self.project / summary["plan"]).read_text(encoding="utf-8"))
        self.assertIn("B003", {f["code"] for f in plan["findings"]})

    def test_hwp_input_is_redirected_to_hwpx(self):
        (self.project / "sources/doc.hwp").write_bytes(b"x")
        proc = subprocess.run([sys.executable, str(SCRIPTS / "beautify.py"), "propose", str(self.project),
                               "sources/doc.hwp"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("export-hwpx", proc.stdout)


if __name__ == "__main__":
    unittest.main()
