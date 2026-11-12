"""표 의미화(tablesem): 단위 캡션·칸 값·머리 행·레코드."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract  # noqa: E402
import rhwp_locate  # noqa: E402
import tablesem  # noqa: E402


def cell(r, c, text, rs=1, cs=1, header=False):
    return {"id": f"t.r{r}c{c}", "row": r, "col": c, "rowSpan": rs, "colSpan": cs, "header": header, "text": text}


class UnitTest(unittest.TestCase):
    def test_parse_units(self):
        self.assertEqual(tablesem.parse_units("(단위 : 백만 달러, %)"), ["백만 달러", "%"])
        self.assertEqual(tablesem.parse_units("(단위:만 톤, 전년 동월 대비%)"), ["만 톤", "%"])
        self.assertEqual(tablesem.parse_units("(단위:전년 동월 대비, %)"), ["%"])
        self.assertEqual(tablesem.parse_units("<단위: 억원>"), ["억원"])
        self.assertEqual(tablesem.parse_units("단위 없는 글"), [])

    def test_parse_value(self):
        pv = lambda t: tablesem.parse_value(t, extract.find_amounts)
        self.assertEqual(pv("△15.5")["value"], -15.5)
        self.assertEqual(pv("-2,197")["value"], -2197)
        self.assertEqual((pv("12.3%")["type"], pv("12.3%")["value"]), ("percent", 12.3))
        v = pv("52,054 (△15.5)")
        self.assertEqual((v["value"], v["secondary"]), (52054, -15.5))
        a = pv("580억 달러")
        self.assertEqual((a["type"], a["value"], a["currency"]), ("amount", 58000000000, "USD"))
        self.assertEqual(pv("-")["type"], "empty")
        self.assertEqual(pv("반도체")["type"], "text")
        self.assertEqual(pv(",")["type"], "text", "쉼표만 있는 칸은 숫자가 아니다(재시험 exam_science 의 멈춤)")
        self.assertEqual(pv("△,")["type"], "text")


class AnalyzeTest(unittest.TestCase):
    TABLE = {"rows": 4, "cols": 3, "cells": [
        cell(0, 0, "구분", rs=2), cell(0, 1, "2024년", cs=2),
        cell(1, 1, "금액"), cell(1, 2, "증감률"),
        cell(2, 0, "수출"), cell(2, 1, "52,054"), cell(2, 2, "△15.5"),
        cell(3, 0, "수입"), cell(3, 1, "54,251 (3.1)"), cell(3, 2, "2.0"),
    ]}

    def test_two_level_header_and_units(self):
        s = tablesem.analyze(self.TABLE, "(단위: 백만 달러, %)", "s0.p1")
        self.assertEqual((s["headerRows"], s["stubCols"], s["kind"]), (2, 1, "numeric"))
        self.assertEqual([(c["key"], c["unit"]) for c in s["columns"]],
                         [("2024년|금액", "백만 달러"), ("2024년|증감률", "%")])
        export, imp = s["records"]
        self.assertEqual((export["label"], export["values"]), ("수출", {"2024년|금액": 52054, "2024년|증감률": -15.5}))
        self.assertEqual(export["units"], {"2024년|금액": "백만 달러", "2024년|증감률": "%"})
        self.assertEqual(export["cellIds"]["2024년|금액"], "t.r2c1")
        self.assertEqual(imp["secondary"], {"2024년|금액": 3.1})
        self.assertEqual(tablesem.record_rows(s)[0], ["행", "2024년|금액 [백만 달러]", "2024년|증감률 [%]"])

    def test_text_table_has_no_guessed_header(self):
        table = {"rows": 2, "cols": 2, "cells": [cell(0, 0, "담당"), cell(0, 1, "홍길동"),
                                                 cell(1, 0, "부서"), cell(1, 1, "정책과")]}
        s = tablesem.analyze(table)
        self.assertEqual((s["kind"], s["headerRows"]), ("text", 0))
        self.assertEqual([r["label"] for r in s["records"]], ["담당", "부서"])

    def test_find_caption_before_table(self):
        blocks = [{"id": "s0.p1", "type": "paragraph", "text": "   (단위 : 억원)"},
                  {"id": "s0.p2.t0", "type": "table", **self.TABLE}]
        self.assertEqual(tablesem.find_caption(blocks, 1, blocks[1]), ("(단위 : 억원)", "s0.p1"))


class ExtractSemanticsTest(unittest.TestCase):
    def test_records_csv_and_semantics(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "exports", "validation"):
                (project / sub).mkdir(parents=True)
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"version": "1", "title": "실적", "blocks": [
                {"type": "paragraph", "text": "(단위: 백만 원, %)"},
                {"type": "table", "rows": [["구분", "2025년", "증감률"], ["매출", "1,200", "△3.5"],
                                           ["비용", "800", "2.0"]]},
            ]}, ensure_ascii=False), encoding="utf-8")
            doc = project / "sources/r.hwpx"
            subprocess.run([rhwp, "scaffold", str(spec), "-o", str(doc)], check=True, capture_output=True)
            proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(project), "sources/r.hwpx",
                                   "--rhwp", rhwp, "--only", "document,tables"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = project / "exports/extract/r"
            table = json.loads((out / "tables.json").read_text(encoding="utf-8"))["tables"][0]
            sem = table["semantics"]
            self.assertEqual(sem["unit"]["units"], ["백만 원", "%"])
            self.assertEqual(sem["records"][0]["values"], {"2025년": 1200, "증감률": -3.5})
            self.assertEqual(sem["records"][0]["units"], {"2025년": "백만 원", "증감률": "%"})
            rows = (out / "tables/T01.records.csv").read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(rows[0], "행,2025년 [백만 원],증감률 [%]")
            self.assertEqual(rows[1], "매출,1200,-3.5")


if __name__ == "__main__":
    unittest.main()
