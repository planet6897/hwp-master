"""AI 가독성 진단: 항목별 양성·음성, 점수, CLI 보고서."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_readability  # noqa: E402
import rhwp_locate  # noqa: E402
from nested_fixture import make_layout, make_nested  # noqa: E402


def para(i, text, page=0, outline=None, kind="paragraph"):
    b = {"id": f"s0.p{i}", "type": kind, "text": text, "page": page}
    if outline:
        b["outline"] = outline
    return b


def counts(document):
    return {c["code"]: c["count"] for c in ai_readability.diagnose(document)["checks"]}


class DiagnoseTest(unittest.TestCase):
    def test_clean_document_scores_100(self):
        doc = {"blocks": [para(0, "사업을 추진한다."), para(1, "예산은 1억 원이다.")], "emptyRuns": []}
        result = ai_readability.diagnose(doc)
        self.assertEqual((result["score"], result["grade"]), (100.0, "A"))
        self.assertTrue(all(c["fix"] == "" for c in result["checks"]))

    def test_alignment_hand_numbers_and_empty_runs(self):
        doc = {"blocks": [para(0, "성명      홍길동"), para(1, "   - 들여쓴 개조식은 괜찮다"),
                          para(2, "1. 목적", outline={"kind": "1.", "level": 1, "parentId": None, "body": "목적"}),
                          para(3, "1. 자동", outline={"kind": "1.", "level": 1, "parentId": None, "body": "자동"},
                               kind="list")],
               "emptyRuns": [{"start": "s0.p9", "length": 3, "page": 0}]}
        c = counts(doc)
        self.assertEqual((c["A04"], c["A05"], c["A06"]), (1, 1, 1))

    def test_toc_lines_are_not_hand_numbers(self):
        toc = para(0, "1. 추진 배경	3", outline={"kind": "1.", "level": 1, "parentId": None, "body": "추진 배경	3"})
        dots = para(1, "2. 추진 계획	…… 12", outline={"kind": "1.", "level": 1, "parentId": None, "body": "추진 계획"})
        hand = para(2, "3. 손으로 친 번호", outline={"kind": "1.", "level": 1, "parentId": None, "body": "손으로 친 번호"})
        self.assertEqual(counts({"blocks": [toc, dots, hand]})["A06"], 1, "목차 줄은 빼고 진짜 손 번호만 센다")

    def test_fragment_only_section(self):
        top = para(0, "□ 현황", outline={"kind": "□", "level": 1, "parentId": None, "body": "현황"})
        items = [para(i, f"○ 항목 {i}", outline={"kind": "○", "level": 2, "parentId": "s0.p0", "body": f"항목 {i}"})
                 for i in range(1, 4)]
        self.assertEqual(counts({"blocks": [top] + items})["A08"], 1)
        items[0]["outline"]["body"] = "수출이 늘었다."
        self.assertEqual(counts({"blocks": [top] + items})["A08"], 0, "서술 문장이 하나라도 있으면 괜찮다")

    def test_image_only_page(self):
        doc = {"blocks": [para(0, "본문"), {"id": "s0.p1.g0", "type": "image", "page": 1}]}
        self.assertEqual(counts(doc)["A07"], 1)

    def test_empty_document_scores_zero(self):
        result = ai_readability.diagnose({"blocks": [{"id": "s0.p0.t0", "type": "table", "page": 0, "role": "layout",
                                                     "rows": 1, "cols": 1, "cells": [{"id": "c", "text": " "}]}]})
        self.assertEqual((result["score"], result["grade"]), (0.0, "D"))
        self.assertEqual(next(c for c in result["checks"] if c["code"] == "A09")["count"], 1)

    def test_penalty_caps(self):
        doc = {"blocks": [para(i, f"가    나 {i}") for i in range(30)]}
        a04 = next(c for c in ai_readability.diagnose(doc)["checks"] if c["code"] == "A04")
        self.assertEqual((a04["count"], a04["penalty"]), (30, 10.0))


class CliTest(unittest.TestCase):
    def test_report_for_layout_and_nested(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "validation"):
                (project / sub).mkdir(parents=True)
            make_layout(rhwp, project / "sources/layout.hwpx")
            make_nested(rhwp, project / "sources/nest.hwpx")
            proc = subprocess.run([sys.executable, str(SCRIPTS / "ai_readability.py"), str(project),
                                   "sources/layout.hwpx", "sources/nest.hwpx", "--rhwp", rhwp, "--min-score", "99"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(proc.returncode, 1, "기준보다 낮은 문서가 있으면 exit 1")
            report = json.loads(Path(json.loads(proc.stdout.strip().splitlines()[-1])["report"]).read_text("utf-8"))
            by = {d["source"]: {c["code"]: c["count"] for c in d["checks"]} for d in report["documents"]}
            self.assertEqual(by["sources/layout.hwpx"]["A03"], 3)
            self.assertEqual(by["sources/nest.hwpx"]["A01"], 1)
            proc = subprocess.run([sys.executable, str(SCRIPTS / "ai_readability.py"), str(project), "../x.hwp",
                                   "--rhwp", rhwp], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
