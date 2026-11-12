"""여러 문서 모아 보기: 폴더 입력·_merged 합친 표·서식 비교(W307)·같은 이름 문서."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rhwp_locate  # noqa: E402
from form_fixture import make_form  # noqa: E402


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.rhwp = rhwp_locate.locate(None)["path"]
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        for sub in ("sources/신청서/2반", "exports", "validation"):
            (self.project / sub).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_extract(self, *docs):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.project), *docs, "--rhwp",
                               self.rhwp, "--only", "document,fields,kv"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        return proc, summary, json.loads(Path(summary["report"]).read_text(encoding="utf-8"))

    def test_folder_merge_and_form_check(self):
        base = self.project / "sources/신청서"
        for name in ("a", "b", "c"):
            make_form(self.rhwp, base / f"{name}.hwpx", ["성명", "연락처"])
        make_form(self.rhwp, base / "2반/a.hwpx", ["성명", "연락처"])  # 같은 이름, 다른 폴더
        make_form(self.rhwp, base / "odd.hwpx", ["회사명"])           # 다른 서식
        proc, summary, report = self.run_extract("sources/신청서")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(summary["dirs"]), 5)
        self.assertEqual(len(set(summary["dirs"])), 5, "같은 이름 문서도 결과 폴더가 겹치지 않는다")
        self.assertIn("exports/extract/a-2", summary["dirs"])
        merged = self.project / summary["merged"]
        docs = read_csv(merged / "documents.csv")
        self.assertEqual(len(docs), 5)
        fields = read_csv(merged / "fields.csv")
        self.assertEqual(list(fields[0])[:3], ["source", "name", "value"])
        self.assertEqual({r["source"] for r in fields if r["name"] == "성명"},
                         {"sources/신청서/a.hwpx", "sources/신청서/b.hwpx", "sources/신청서/c.hwpx",
                          "sources/신청서/2반/a.hwpx"})
        forms = json.loads((merged / "forms.json").read_text(encoding="utf-8"))
        self.assertEqual(forms["majority"], ["성명", "연락처"])
        self.assertEqual([d["source"] for d in forms["differing"]], ["sources/신청서/odd.hwpx"])
        w307 = [f for f in report["findings"] if f["code"] == "W307"]
        self.assertEqual([f["where"] for f in w307], ["sources/신청서/odd.hwpx"])

    def test_glob_and_no_comparison_for_two_unrelated(self):
        make_form(self.rhwp, self.project / "sources/x.hwpx", ["성명"])
        make_form(self.rhwp, self.project / "sources/y.hwpx", ["회사명"])
        proc, summary, report = self.run_extract("sources/*.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(summary["dirs"]), 2)
        self.assertFalse([f for f in report["findings"] if f["code"] == "W307"], "기준 서식이 없으면 비교하지 않는다")

    def test_no_match_is_error(self):
        proc, summary, report = self.run_extract("sources/없는폴더/*.hwp")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report["findings"][0]["code"], "X101")


if __name__ == "__main__":
    unittest.main()
