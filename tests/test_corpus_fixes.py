"""#51 실제 문서 평가에서 드러난 추출 결함의 회귀 시험 — rhwp 없이 블록·칸·합성 OWPML 로 본다."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "hwp-master" / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docmodel  # noqa: E402
import eval_extract  # noqa: E402
import extract  # noqa: E402
import kv  # noqa: E402
import tablesem  # noqa: E402
import values  # noqa: E402
from test_docmodel_textbox import NS, make_hwpx, para  # noqa: E402


def cell(r, c, text, cid=None):
    return {"id": cid or f"t.r{r}c{c}", "row": r, "col": c, "rowSpan": 1, "colSpan": 1, "header": False,
            "text": text}


class OutlineTest(unittest.TestCase):
    def test_number_without_space_and_placeholders(self):
        self.assertIsNone(docmodel.outline_marker("1.다음을 듣고"), "빈칸 없는 손 번호는 그대로(자동 번호는 list 블록)")
        self.assertIsNone(docmodel.outline_marker("1.5배 늘었다"))
        self.assertIsNone(docmodel.outline_marker("4. 23.~6. 15. 접수"))
        self.assertIsNone(docmodel.outline_marker("○○ 신문사 탐사 보도"), "○○ 는 이름을 가린 자리 표시")
        self.assertEqual(docmodel.outline_marker("○ 항목")[0], "○")
        self.assertEqual(docmodel.outline_marker("․조세수입 : 포함")[0], "·")

    def test_auto_numbered_list_is_an_outline_item(self):
        blocks = [{"id": "p0", "type": "list", "listType": "number", "level": 1, "text": "중산층 이상 연금 줄어"},
                  {"id": "p1", "type": "paragraph", "text": "※ 연금 재정 전망"},
                  {"id": "p2", "type": "paragraph", "text": "○ 세부 항목"},
                  {"id": "p3", "type": "list", "listType": "number", "level": 1, "text": "재정 고갈 해결 안 돼"}]
        self.assertEqual(docmodel.assign_outline(blocks), 4)
        self.assertEqual([(b["outline"]["kind"], b["outline"]["level"]) for b in blocks],
                         [("auto", 1), ("note", 2), ("○", 2), ("auto", 1)])
        self.assertIsNone(blocks[3]["outline"]["parentId"], "같은 단계 자동 번호는 앞 번호 아래가 아니다")

    def test_cell_footnote_becomes_note_block(self):
        fn = ('<hp:ctrl><hp:footNote number="1"><hp:subList>'
              + para('<hp:run charPrIDRef="0"><hp:t>순 융 자 : 융자지출 - 융자회수</hp:t></hp:run>', 9)
              + '</hp:subList></hp:footNote></hp:ctrl>')
        tc = ('<hp:tc><hp:subList>' + para(f'<hp:run charPrIDRef="0"><hp:t>순융자</hp:t>{fn}</hp:run>', 8)
              + '</hp:subList><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
        tbl = f'<hp:tbl rowCnt="1" colCnt="1"><hp:tr>{tc}</hp:tr></hp:tbl>'
        section = (f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" {NS}>'
                   + para(f'<hp:run charPrIDRef="0">{tbl}</hp:run>') + "</hs:sec>")
        with tempfile.TemporaryDirectory() as tmp:
            doc = docmodel.build_from_hwpx(make_hwpx(Path(tmp) / "n.hwpx", section=section))
        notes = [b for b in doc["blocks"] if b["type"] == "note"]
        self.assertEqual([(n["text"], n["cellId"]) for n in notes], [("순 융 자 : 융자지출 - 융자회수", "s0.p0.t0.r0c0")])
        table = next(b for b in doc["blocks"] if b["type"] == "table")
        self.assertEqual(table["cells"][0]["text"], "순융자", "각주 글은 칸 글에 섞이지 않는다")
        items = kv.extract_kv(doc["blocks"])
        self.assertIn(("순융자", "융자지출 - 융자회수"), [(i["keyNorm"], i["value"]) for i in items])


class KvTest(unittest.TestCase):
    def test_label_value_pairs_from_real_forms(self):
        table = {"id": "t", "type": "table", "cells": [
            cell(0, 0, "등록번호"), cell(0, 1, "성명"),                       # 빈 칸 옆 라벨끼리 → 쌍 아님
            cell(1, 0, "담당"), cell(1, 1, "권x범"), cell(1, 2, "TEL: 010-1234-4567"),
            cell(2, 0, "발행일      2025년      06월      18일"),
            cell(3, 0, "요양기관 종류"), cell(3, 1, "[  ] 의원급    [  ] 병원급    [■] 종합병원"),
            cell(4, 0, "○○신문"), cell(4, 1, "2024년 □월 □일"),
            cell(5, 0, "http://korea.kore.or.kr")]}
        got = {(i["keyNorm"], i["value"]) for i in kv.extract_kv([table]) if i.get("duplicateOf") is None}
        self.assertIn(("TEL", "010-1234-4567"), got)
        self.assertIn(("발행일", "2025년 06월 18일"), got)
        self.assertIn(("요양기관종류", "종합병원"), got)
        self.assertNotIn(("등록번호", "성명"), got)
        self.assertFalse(any(k == "권x범" or v.startswith("TEL") for k, v in got), got)
        self.assertFalse(any("□" in v for _, v in got), "빈 날짜 틀은 값이 아니다")
        self.assertFalse(any(k == "http" for k, _ in got))

    def test_paragraph_markers_multi_pairs_and_repeated_keys(self):
        blocks = [{"id": "p0", "type": "paragraph",
                   "text": "   * 사회보장성기금 수입 : 43,539억원, 사회보장성기금 지출 : 18,862억원"},
                  {"id": "p1", "type": "paragraph", "text": "** 사회보장성기금(4개) : 국민연금기금, 고용보험기금"}]
        got = [(i["keyNorm"], i["value"]) for i in kv.extract_kv(blocks)]
        self.assertEqual(got, [("사회보장성기금수입", "43,539억원"), ("사회보장성기금지출", "18,862억원"),
                               ("사회보장성기금(4개)", "국민연금기금, 고용보험기금")])
        roster = {"id": "t", "type": "table", "cells": [cell(0, 0, "TEL: 010-1"), cell(1, 0, "TEL: 010-2")]}
        items = kv.extract_kv([roster])
        self.assertEqual([i.get("duplicateOf") for i in items], [None, None], "값이 다른 같은 라벨은 둘 다 값")

    def test_field_cells_are_not_paired_again(self):
        table = {"id": "t", "type": "table", "cells": [cell(0, 0, "책임자"), cell(0, 1, "부  장", "t.r0c1"),
                                                         cell(0, 2, "김도훈", "t.r0c2"), cell(0, 3, "(053-668-1310)", "t.r0c3")]}
        fields = [{"name": "책임자대외직명", "value": "부  장", "blockId": "t.r0c1"},
                  {"name": "책임자명", "value": "김도훈", "blockId": "t.r0c2"},
                  {"name": "책임자전화번호", "value": "(053-668-1310)", "blockId": "t.r0c3"}]
        got = [(i["keyNorm"], i["source"]) for i in kv.extract_kv([table], fields=fields)]
        self.assertEqual(got, [("책임자대외직명", "field"), ("책임자명", "field"), ("책임자전화번호", "field")])


class ValuesTest(unittest.TestCase):
    def test_same_law_and_negative_amounts(self):
        text = "조세특례제한법 제106조의 3 제11항 및 동법시행령 제106조의 8 제2항에 의한"
        laws = [r["normalized"]["law"] for r in values.find_references(text)]
        self.assertEqual(laws, ["조세특례제한법", "조세특례제한법 시행령"])
        amounts = extract.find_amounts("ㅇ △34,703십억원 = △10,027십억원 - 24,676십억원, 200만 원인 가입자")
        self.assertEqual([a["normalized"] for a in amounts], [-34703000000000, -10027000000000, 24676000000000, 2000000])

    def test_triangle_minus_is_not_always_a_rate(self):
        body = [c["normalized"] for c in values.find_changes("수출은 △6.7% 줄고 적자는 △34,703십억원이다")]
        self.assertEqual(body, [-6.7])
        cells = [c["normalized"] for c in values.find_changes("△2,006", cell=True)] + \
            [c["normalized"] for c in values.find_changes("△297", cell=True)] + \
            [c["normalized"] for c in values.find_changes("(△51.2)", cell=True)]
        self.assertEqual(cells, [-51.2], "표 칸의 % 없는 정수는 음수 수치, 소수는 증감률")


class TableTest(unittest.TestCase):
    def test_parenthesized_rate_row_and_empty_cells(self):
        table = {"rows": 3, "cols": 3, "cells": [
            cell(0, 0, "구분"), cell(0, 1, "1분기"), cell(0, 2, "2분기"),
            cell(1, 0, "총투자"), cell(1, 1, "282.3"), cell(1, 2, "-"),
            cell(2, 0, "증감률"), cell(2, 1, "(147.8)"), cell(2, 2, "(△51.2)")]}
        sem = tablesem.analyze(table)
        got = {(r["label"], k): v for r in sem["records"] for k, v in r["values"].items()}
        self.assertEqual(got, {("총투자", "1분기"): 282.3, ("증감률", "1분기"): 147.8, ("증감률", "2분기"): -51.2})
        accounting = {"rows": 2, "cols": 2, "cells": [cell(0, 0, "구분"), cell(0, 1, "손익"),
                                                       cell(1, 0, "영업"), cell(1, 1, "(1,234)")]}
        self.assertEqual(tablesem.analyze(accounting)["records"][0]["values"], {"손익": "(1,234)"},
                         "괄호 안에 △ 가 없는 표는 회계식 음수일 수 있어 숫자로 읽지 않는다")


class RestructureTest(unittest.TestCase):
    def test_page_long_footnote_goes_to_body_and_auto_items_stay_paragraphs(self):
        import ai_restructure
        long_note = "\n".join(f"풀이 {k}줄" for k in range(30))
        doc = {"blocks": [
            {"id": "p0", "type": "list", "listType": "number", "level": 1, "text": "다음 글을 읽고 답하시오.",
             "outline": {"marker": "", "kind": "auto", "level": 1, "parentId": None, "body": "다음 글을 읽고 답하시오."}},
            {"id": "p0.n0", "type": "note", "text": long_note},
            {"id": "p1", "type": "paragraph", "text": "둘째 문단"},
            {"id": "p1.n0", "type": "note", "text": "짧은 각주"}]}
        spec, notes = ai_restructure.to_hds(doc, "시험")
        self.assertEqual([b["type"] for b in spec["blocks"]], ["paragraph", "paragraph", "paragraph"])
        self.assertEqual(spec["blocks"][0]["text"], "다음 글을 읽고 답하시오.")
        self.assertTrue(spec["blocks"][1]["text"].startswith("※ 풀이 0줄"), "쪽을 넘는 긴 각주는 본문 주석으로")
        self.assertEqual(spec["blocks"][2]["footnotes"], [{"text": "짧은 각주"}])
        self.assertEqual(notes["longFootnotesInBody"], 1)

    def test_fragment_check_skips_auto_numbered_lists(self):
        import ai_readability
        blocks = [{"id": "q", "type": "list", "text": "다음을 듣고 고르시오",
                   "outline": {"marker": "", "kind": "auto", "level": 1, "parentId": None, "body": "다음을 듣고 고르시오"}}]
        blocks += [{"id": f"c{k}", "type": "list", "text": f"선택 {k}",
                    "outline": {"marker": "", "kind": "auto", "level": 2, "parentId": "q", "body": f"선택 {k}"}}
                   for k in range(4)]
        a08 = next(c for c in ai_readability.diagnose({"blocks": blocks})["checks"] if c["code"] == "A08")
        self.assertEqual(a08["count"], 0)


class CorpusJobsTest(unittest.TestCase):
    def test_gold_in_repo_matches_document_by_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, gold_dir = Path(tmp) / "docs" / "sub", Path(tmp) / "gold"
            corpus.mkdir(parents=True)
            gold_dir.mkdir()
            (corpus / "a.hwp").write_bytes(b"one")
            (corpus / "b.hwp").write_bytes(b"changed")
            for name, data in (("a", b"one"), ("b", b"two"), ("c", b"x")):
                (gold_dir / f"{name}.golden.json").write_text(json.dumps(
                    {"source": f"{name}.hwp", "sha256": hashlib.sha256(data).hexdigest(), "gold": {}}), encoding="utf-8")
            jobs, problems, missing = eval_extract.corpus_jobs(corpus.parent, gold_dir)
        self.assertEqual([j[0] for j in jobs], ["corpus:a"])
        self.assertEqual(len(problems), 1, "SHA-256 이 다른 판본은 평가하지 않고 오류")
        self.assertEqual(missing, ["c.hwp"])

    def test_ignore_entries_count_neither_way(self):
        gold = {"values": [{"kind": "amount", "normalized": 1}],
                "ignore": {"values": [{"kind": "amount", "normalized": 2}]}}
        pred = {c: eval_extract.Counter() for c in eval_extract.CATEGORIES}
        pred["values"].update([eval_extract._value_key("amount", 1), eval_extract._value_key("amount", 2)])
        for c, keys in eval_extract.ignore_keys(gold).items():
            pred[c] -= keys
        m = eval_extract.score(pred, eval_extract.gold_keys(gold), ["values"])["values"]
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (1, 0, 0))


if __name__ == "__main__":
    unittest.main()
