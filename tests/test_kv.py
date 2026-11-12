"""항목-값(kv): 양식형 표 칸 쌍·"라벨: 값" 문단·누름틀 병합."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kv  # noqa: E402
import rhwp_locate  # noqa: E402
from form_fixture import make_form  # noqa: E402
from nested_fixture import make_layout  # noqa: E402


def cell(r, c, text):
    return {"id": f"t.r{r}c{c}", "row": r, "col": c, "rowSpan": 1, "colSpan": 1, "text": text}


class PairTest(unittest.TestCase):
    def test_labels(self):
        self.assertTrue(kv.is_label("성    명"))
        self.assertTrue(kv.is_label("보도시점"))
        self.assertFalse(kv.is_label("02-000-0000"))
        self.assertFalse(kv.is_label("이 문장은 라벨이 아니라 설명입니다."))
        self.assertEqual(kv.norm_key("성    명:"), "성명")

    def test_row_alternation_and_vertical(self):
        row = {"cells": [cell(0, 0, "보도시점"), cell(0, 1, "배포즉시"), cell(0, 2, "배포"),
                         cell(0, 3, "2024. 6. 17.(월) 09:00")]}
        self.assertEqual([(a["text"], b["text"]) for a, b in kv.table_pairs(row)],
                         [("보도시점", "배포즉시"), ("배포", "2024. 6. 17.(월) 09:00")])
        vertical = {"cells": [cell(0, 0, "성명"), cell(0, 1, "연락처"), cell(1, 0, "홍길동"), cell(1, 1, "010-0000-0000")]}
        self.assertEqual([(a["text"], b["text"]) for a, b in kv.table_pairs(vertical)],
                         [("성명", "홍길동"), ("연락처", "010-0000-0000")])

    def test_inline_paragraphs_and_skip_numeric_tables(self):
        blocks = [{"id": "s0.p0", "type": "paragraph", "text": "담당자: 홍길동\n09:00 회의 시작", "page": 0},
                  {"id": "s0.p1.t0", "type": "table", "page": 0, "cells": [cell(0, 0, "구분"), cell(0, 1, "금액")]}]
        items = kv.extract_kv(blocks, {"s0.p1.t0": {"kind": "numeric", "headerRows": 1}})
        self.assertEqual([(i["key"], i["value"], i["source"]) for i in items], [("담당자", "홍길동", "paragraph")])

    def test_field_first_and_duplicates(self):
        blocks = [{"id": "s0.p0", "type": "paragraph", "text": "회사명: 다른값", "page": 0}]
        items = kv.extract_kv(blocks, {}, [{"name": "회사명", "value": "한빛", "blockId": "s0.p3.t0.r0c1"}])
        self.assertEqual([(i["source"], i.get("duplicateOf")) for i in items], [("field", None), ("paragraph", 0)])


class ExtractKvTest(unittest.TestCase):
    def setUp(self):
        self.rhwp = rhwp_locate.locate(None)["path"]
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        for sub in ("sources", "exports", "validation"):
            (self.project / sub).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_extract(self, name, only):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.project), f"sources/{name}",
                               "--rhwp", self.rhwp, "--only", only], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads((self.project / f"exports/extract/{Path(name).stem}/kv.json").read_text(encoding="utf-8"))

    def test_layout_form_table(self):
        make_layout(self.rhwp, self.project / "sources/layout.hwpx")
        items = self.run_extract("layout.hwpx", "document,kv")["items"]
        pairs = {(i["key"], i["value"]) for i in items if i["source"] == "table"}
        self.assertIn(("담당", "홍길동"), pairs)
        self.assertIn(("연락처", "02-000-0000"), pairs)
        self.assertNotIn("항목", {i["key"] for i in items}, "숫자 데이터 표는 항목-값이 아니다")
        self.assertTrue((self.project / "exports/extract/layout/kv.csv").is_file())

    def test_fields_merge(self):
        make_form(self.rhwp, self.project / "sources/form.hwpx", ["회사명", "대표자"])
        items = self.run_extract("form.hwpx", "document,kv")["items"]
        self.assertTrue(all("keyNorm" in i for i in items))


if __name__ == "__main__":
    unittest.main()
