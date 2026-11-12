"""AI 가독성 개선본(ai_restructure): 문서 모델 → HDS 변환 규칙, 글 보존 확인, 픽스처로 끝까지(빌드·점수)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_restructure  # noqa: E402
import rhwp_locate  # noqa: E402
from nested_fixture import make_layout, make_nested  # noqa: E402


def para(i, text, outline=None, **kw):
    b = {"id": f"s0.p{i}", "type": "paragraph", "text": text, "page": 0, **kw}
    if outline:
        b["outline"] = outline
    return b


def item(kind, level, body, parent=None, marker=None):
    return {"marker": marker or kind, "kind": kind, "level": level, "parentId": parent, "body": body}


class ToHdsTest(unittest.TestCase):
    def test_numbers_to_headings_lists_and_footnotes(self):
        doc = {"blocks": [
            para(0, "1. 추진 배경", item("1.", 1, "추진 배경")),
            para(1, "  □ 현황    분석", item("□", 2, "현황 분석", "s0.p0")),
            para(2, "    ○ 세부 내용", item("○", 3, "세부 내용", "s0.p1")),
            para(3, "    * 근거 자료", item("note", 4, "근거 자료", "s0.p2", "*")),
            para(4, "성명      홍길동"),
        ]}
        spec, notes = ai_restructure.to_hds(doc, "제목")
        self.assertEqual([b["type"] for b in spec["blocks"]], ["heading", "paragraph", "paragraph", "paragraph"])
        self.assertEqual(spec["blocks"][0], {"type": "heading", "level": 1, "text": "추진 배경"})
        self.assertEqual([b["text"] for b in spec["blocks"][1:]], ["□ 현황 분석", "○ 세부 내용", "성명 홍길동"],
                         "개조식은 기호를 글에 둔 문단(들여쓰기 꾸밈 없음), 공백 정렬을 푼다")
        self.assertEqual(spec["blocks"][2]["footnotes"], [{"text": "근거 자료"}], "※·* 주석은 앞 항목의 각주")
        self.assertEqual((notes["headingsFromNumbers"], notes["listItems"], notes["footnotes"]), (1, 2, 1))

    def test_numbered_items_become_lists_starting_at_level_one(self):
        doc = {"blocks": [para(0, "1) 가장 긴 세부 사항을 설명하는 숫자 항목이다", item("1)", 3, "가장 긴 세부 사항을 설명하는 숫자 항목이다")),
                          para(1, "가) 그 아래 항목", item("가)", 4, "그 아래 항목", "s0.p0"))]}
        spec, _ = ai_restructure.to_hds(doc, "t")
        self.assertEqual(spec["blocks"][0]["type"], "list")
        self.assertEqual([i["level"] for i in spec["blocks"][0]["items"]], [1, 2])

    def test_merges_are_filled_and_nested_tables_lifted(self):
        inner = {"id": "s0.p0.t0.r1c1.t0", "rows": 1, "cols": 2, "role": "data", "cells": [
            {"id": "a", "row": 0, "col": 0, "rowSpan": 1, "colSpan": 1, "text": "x", "paragraphs": [{"text": "x", "tables": []}]},
            {"id": "b", "row": 0, "col": 1, "rowSpan": 1, "colSpan": 1, "text": "y", "paragraphs": [{"text": "y", "tables": []}]}]}
        outer = {"id": "s0.p0.t0", "type": "table", "role": "data", "rows": 2, "cols": 2, "page": 0, "cells": [
            {"id": "c0", "row": 0, "col": 0, "rowSpan": 1, "colSpan": 2, "text": "머리", "paragraphs": [{"text": "머리", "tables": []}]},
            {"id": "c1", "row": 1, "col": 0, "rowSpan": 1, "colSpan": 1, "text": "가", "paragraphs": [{"text": "가", "tables": []}]},
            {"id": "c2", "row": 1, "col": 1, "rowSpan": 1, "colSpan": 1, "text": "x | y",
             "paragraphs": [{"text": "", "tables": [inner["id"]]}], "tables": [inner]}]}
        spec, notes = ai_restructure.to_hds({"blocks": [outer]}, "t")
        first, second = spec["blocks"]
        self.assertEqual(first["rows"], [["머리", "머리"], ["가", "[아래 표 1]"]], "병합은 풀고 덮던 칸마다 같은 글")
        self.assertNotIn("merges", first)
        self.assertEqual(second["rows"], [["x", "y"]])
        self.assertIn("아래 표 1", second["caption"])
        self.assertEqual(notes["nestedTablesLifted"], 1)

    def test_coverage_ignores_markers(self):
        before = {"blocks": [para(0, "□ 직접비용", item("□", 1, "직접비용")), para(1, "남은 글")]}
        after = {"blocks": [{"type": "list", "text": "직접비용"}, {"type": "paragraph", "text": "남은 글"}]}
        self.assertEqual(ai_restructure.coverage(before, after)["ratio"], 1.0)
        lost = ai_restructure.coverage(before, {"blocks": [{"type": "paragraph", "text": "남은 글"}]})
        self.assertEqual((lost["ratio"], lost["missing"]), (0.5, ["직접비용"]))


class EndToEndTest(unittest.TestCase):
    def test_layout_and_nested_fixtures_improve(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "exports", "validation"):
                (project / sub).mkdir(parents=True)
            make_layout(rhwp, project / "sources/layout.hwpx")
            make_nested(rhwp, project / "sources/nest.hwpx")
            proc = subprocess.run([sys.executable, str(SCRIPTS / "ai_restructure.py"), str(project),
                                   "sources/layout.hwpx", "sources/nest.hwpx", "--rhwp", rhwp],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            report = json.loads(Path(json.loads(proc.stdout.strip().splitlines()[-1])["report"]).read_text("utf-8"))
            for row in report["documents"]:
                self.assertEqual(row["coverage"]["ratio"], 1.0, row["coverage"])
                self.assertGreater(row["after"]["score"], row["before"]["score"], row)
                self.assertTrue((project / row["output"]).is_file())
            by = {r["source"]: r for r in report["documents"]}
            self.assertNotIn("A03", by["sources/layout.hwpx"]["after"]["checks"], "레이아웃 표가 사라진다")
            self.assertNotIn("A01", by["sources/nest.hwpx"]["after"]["checks"], "표 안의 표가 사라진다")


if __name__ == "__main__":
    unittest.main()
