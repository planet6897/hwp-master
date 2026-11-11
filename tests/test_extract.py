"""Extract 라우트 테스트: python3 -m unittest tests.test_extract

rhwp 바이너리가 필요하다(`RHWP_BIN` 또는 PATH). 입력은 scaffold 와 누름틀 픽스처로 직접 만든다.
"""
import csv
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
sys.path.insert(0, str(ROOT / "tests"))
import extract  # noqa: E402
import rhwp_locate  # noqa: E402
from form_fixture import make_form  # noqa: E402

SPEC = {"version": "1", "title": "추출 시험 문서", "blocks": [
    {"type": "heading", "level": 1, "text": "사업 개요"},
    {"type": "paragraph", "text": "계약일은 2026년 3월 15일이고 총사업비는 1,200,000원이다."},
    {"type": "table", "rows": [["구분", "금액"], ["인건비", "800,000원"], ["장비비", "400,000원"]]},
    {"type": "heading", "level": 1, "text": "추진 일정"},
    {"type": "paragraph", "text": "착수 보고는 2026년 4월 1일에 한다."},
]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


class GridTest(unittest.TestCase):
    def test_merged_cell_text_stays_top_left(self):
        table = {"rows": 2, "cols": 2, "cells": [
            {"row": 0, "col": 0, "rowSpan": 1, "colSpan": 2, "text": "머리"},
            {"row": 1, "col": 0, "rowSpan": 1, "colSpan": 1, "text": "가"},
            {"row": 1, "col": 1, "rowSpan": 1, "colSpan": 1, "text": "나"}]}
        self.assertEqual(extract.table_grid(table), [["머리", ""], ["가", "나"]])
        self.assertTrue(extract.table_area_ok(table))
        table["cells"].pop()
        self.assertFalse(extract.table_area_ok(table))


class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        for sub in ("sources", "exports", "validation"):
            (self.project / sub).mkdir(parents=True)
        spec = Path(self.tmp.name) / "spec.json"
        spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
        self.doc = self.project / "sources/doc.hwpx"
        subprocess.run([self.rhwp, "scaffold", str(spec), "-o", str(self.doc)], check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_extract(self, *docs, extra=()):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.project), *docs,
                               "--rhwp", self.rhwp, *extra], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900)
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        return proc, report

    def test_extracts_text_tables_data_and_keeps_source(self):
        before = sha(self.doc)
        proc, report = self.run_extract("sources/doc.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(report["ok"])
        self.assertEqual(sha(self.doc), before, "원본은 고치지 않는다")
        out = self.project / "exports/extract/doc"
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["sha256"], before)
        self.assertTrue(summary["untrustedContent"])
        self.assertEqual(summary["counts"]["tables"], 1)

        self.assertIn("총사업비", (out / "text.md").read_text(encoding="utf-8"))
        self.assertTrue((out / "text.md").read_text(encoding="utf-8").startswith("<!-- page 1 -->"))
        self.assertEqual(read_csv(out / "tables/T01.csv"),
                         [["구분", "금액"], ["인건비", "800,000원"], ["장비비", "400,000원"]])

        data = json.loads((out / "data.json").read_text(encoding="utf-8"))
        kinds = {i["kind"] for i in data["items"]}
        self.assertIn("date", kinds)
        self.assertIn("amount", kinds)
        self.assertTrue(all("paragraph" in i and "page" in i for i in data["items"]), "값마다 위치를 남긴다")
        rows = read_csv(out / "data.csv")
        self.assertEqual(rows[0][:3], ["kind", "raw", "normalized"])
        self.assertTrue(any("1,200,000" in r[1] for r in rows[1:]))

        structure = json.loads((out / "structure.json").read_text(encoding="utf-8"))
        headings = [n["heading"] for n in structure["structure"]["roots"]]
        self.assertEqual(headings, ["사업 개요", "추진 일정"])

    def test_fields_and_only(self):
        form = make_form(self.rhwp, self.project / "sources/form.hwpx", ["회사명", "대표자"])
        proc, report = self.run_extract(f"sources/{form.name}", extra=("--only", "fields"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.project / "exports/extract/form"
        fields = json.loads((out / "fields.json").read_text(encoding="utf-8"))["fields"]
        self.assertEqual(sorted(f["name"] for f in fields), ["대표자", "회사명"])
        self.assertTrue((out / "fields.csv").is_file())
        self.assertFalse((out / "tables.json").exists(), "--only 로 고른 항목만 꺼낸다")

    def test_rejects_outside_sources(self):
        outside = self.project / "exports/copy.hwpx"
        outside.write_bytes(self.doc.read_bytes())
        proc, report = self.run_extract("exports/copy.hwpx")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual([f["code"] for f in report["findings"]], ["X101"])

    def test_unknown_part(self):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.project), "sources/doc.hwpx",
                               "--only", "pictures", "--rhwp", self.rhwp], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
