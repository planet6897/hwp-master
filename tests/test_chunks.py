"""LLM 용 청크(chunks): 제목 경계·크기·블록 빠짐없음·표 나누기·값."""
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chunks  # noqa: E402
import rhwp_locate  # noqa: E402


def para(i, text, **kw):
    return {"id": f"s0.p{i}", "type": "paragraph", "text": text, "page": i // 10, "headingPath": [], **kw}


class BuildTest(unittest.TestCase):
    def test_heading_boundaries_and_coverage(self):
        blocks = [{"id": "s0.p0", "type": "heading", "level": 1, "text": "1장", "page": 0, "headingPath": []}]
        blocks += [para(i, "가" * 300, headingPath=["1장"]) for i in range(1, 8)]
        blocks += [{"id": "s0.p8", "type": "heading", "level": 1, "text": "2장", "page": 0, "headingPath": []},
                   para(9, "짧은 글", headingPath=["2장"])]
        out = chunks.build_chunks(blocks, chars=1000)
        counted = Counter(i for c in out for i in c["blockIds"])
        self.assertEqual(set(counted), {b["id"] for b in blocks})
        self.assertTrue(all(v == 1 for v in counted.values()), "모든 블록은 정확히 한 청크")
        self.assertTrue(all(c["chars"] <= 1300 for c in out))
        second = next(c for c in out if "s0.p8" in c["blockIds"])
        self.assertEqual(second["blockIds"][0], "s0.p8", "제목에서 새 청크")
        self.assertEqual(second["headingPath"], ["2장"])
        self.assertTrue(out[0]["text"].startswith("## 1장"))

    def test_big_table_split_repeats_header(self):
        cells = [{"id": f"t.r0c{c}", "row": 0, "col": c, "text": h} for c, h in enumerate(["구분", "값"])]
        cells += [{"id": f"t.r{r}c{c}", "row": r, "col": c, "text": f"항목{r}" if c == 0 else "x" * 40}
                  for r in range(1, 60) for c in range(2)]
        table = {"id": "s0.p1.t0", "type": "table", "role": "data", "rows": 60, "cols": 2, "cells": cells,
                 "page": 0, "headingPath": []}
        out = chunks.build_chunks([table], chars=600)
        self.assertGreater(len(out), 1)
        self.assertTrue(all(c["text"].lstrip().startswith("| 구분 | 값 |") for c in out), "부분마다 머리 행")
        self.assertEqual(out[0]["part"], f"1/{len(out)}")
        rows = sum(c["text"].count("| 항목") for c in out)
        self.assertEqual(rows, 59, "행이 빠지거나 겹치지 않는다")

    def test_values_and_outline_path(self):
        blocks = [para(0, "□ 수출 동향", outline={"kind": "□", "level": 1, "parentId": None, "body": "수출 동향"}),
                  para(1, "○ 580억 달러", outline={"kind": "○", "level": 2, "parentId": "s0.p0", "body": "580억 달러"})]
        items = [{"kind": "amount", "raw": "580억 달러", "normalized": 58000000000, "blockId": "s0.p1"}]
        out = chunks.build_chunks(blocks, items, chars=5)
        target = next(c for c in out if "s0.p1" in c["blockIds"])
        self.assertEqual(target["values"][0]["normalized"], 58000000000)
        self.assertEqual(target["outlinePath"], ["수출 동향"])


class ExtractChunksTest(unittest.TestCase):
    def test_chunks_jsonl(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "exports", "validation"):
                (project / sub).mkdir(parents=True)
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"version": "1", "title": "청크", "blocks": [
                {"type": "heading", "level": 1, "text": "추진 배경"},
                {"type": "paragraph", "text": "□ 수출은 580억 달러로 늘었다."},
                {"type": "table", "rows": [["구분", "금액"], ["장비", "100원"]]},
                {"type": "heading", "level": 1, "text": "추진 계획"},
                {"type": "paragraph", "text": "착수는 2026년 4월 1일이다."},
            ]}, ensure_ascii=False), encoding="utf-8")
            doc = project / "sources/c.hwpx"
            subprocess.run([rhwp, "scaffold", str(spec), "-o", str(doc)], check=True, capture_output=True)
            proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(project), "sources/c.hwpx",
                                   "--rhwp", rhwp, "--only", "document,tables,data,chunks"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = (project / "exports/extract/c/chunks.jsonl").read_text(encoding="utf-8").splitlines()
            out = [json.loads(line) for line in lines]
            self.assertEqual([c["headingPath"] for c in out if c["headingPath"]][:2], [["추진 배경"], ["추진 계획"]])
            first = next(c for c in out if c["headingPath"] == ["추진 배경"])
            self.assertIn("| 구분 | 금액 |", first["text"])
            self.assertIn("580억 달러", [v["raw"] for v in first["values"]])
            self.assertEqual(first["tables"][0]["label"], "T01")
            self.assertTrue(all(c["untrustedContent"] for c in out))


if __name__ == "__main__":
    unittest.main()
