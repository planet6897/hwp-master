"""쪽 나눔·각주·머리말/꼬리말 블록: python3 -m unittest tests.test_native

SplitTest·ValidateTest 는 rhwp 없이 돈다. ProduceTest 는 rhwp 바이너리(`RHWP_BIN` 또는 PATH)가 필요하다.
"""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/hwp-master/scripts"))
import hds  # noqa: E402
import native  # noqa: E402
import receipt  # noqa: E402
import rhwp_locate  # noqa: E402

DOC = {"version": "1", "title": "블록 확장 시험",
       "header": {"text": "블록 확장 시험 — 내부 검토용"},
       "footer": {"text": "- {page} -"},
       "blocks": [
           {"type": "heading", "level": 1, "text": "첫째 장"},
           {"type": "paragraph", "text": "□ 각주 두 개: 앞쪽 근거와 뒤쪽 근거", "footnotes": [
               {"text": "앞쪽 근거의 출처 & 날짜", "after": "앞쪽 근거"}, {"text": "문단 끝 각주"}],
            "emphasis": [{"text": "앞쪽 근거", "bold": True}, {"text": "뒤쪽 근거", "color": "#c00000", "underline": True}]},
           {"type": "table", "rows": [["항목", "값"], ["가", "1"]]},
           {"type": "page_break"},
           {"type": "heading", "level": 1, "text": "둘째 장"},
           {"type": "paragraph", "text": "□ 새 쪽에서 시작한다"},
       ]}


def codes(findings):
    return [f.code for f in findings if f.severity == "error"]


class SplitTest(unittest.TestCase):
    def test_split_strips_extensions(self):
        spec, plan = native.split(DOC)
        self.assertNotIn("header", spec)
        self.assertEqual([b["type"] for b in spec["blocks"]],
                         ["heading", "paragraph", "table", "heading", "paragraph"])
        self.assertFalse(any("footnotes" in b or "emphasis" in b for b in spec["blocks"]))
        self.assertEqual([(e["offset"], e["count"], e["props"]) for e in plan["emphasis"]],
                         [(len("□ 각주 두 개: "), 5, '{"bold":true}'),
                          (len("□ 각주 두 개: 앞쪽 근거와 "), 5, '{"underline":true,"textColor":"#C00000"}')])
        self.assertEqual(plan["page_breaks"], [3])  # 쪽 나눔 다음 블록(둘째 장)
        self.assertEqual([(f["block"], f["offset"]) for f in plan["footnotes"]],
                         [(1, len("□ 각주 두 개: 앞쪽 근거")), (1, len(DOC["blocks"][1]["text"]))])

    def test_plain_doc_is_scaffold_only(self):
        doc = {"version": "1", "blocks": [{"type": "paragraph", "text": "가"}]}
        spec, plan = native.split(doc)
        self.assertEqual(spec, doc)
        self.assertTrue(native.plan_empty(plan))

    def test_map_blocks_skips_title_and_table_tail(self):
        paras = [{"text": "제목", "table": False}, {"text": "가", "table": False},
                 {"text": "", "table": True}, {"text": "", "table": False}, {"text": "나", "table": False}]
        blocks = [{"type": "paragraph", "text": "가"}, {"type": "table"}, {"type": "paragraph", "text": "나"}]
        self.assertEqual(native.map_blocks(paras, blocks, True), [1, 2, 4])


class ValidateTest(unittest.TestCase):
    def test_extensions_valid(self):
        self.assertEqual(codes(hds.validate_document(DOC)), [])

    def test_page_break_edges(self):
        pb = {"type": "page_break"}
        para = {"type": "paragraph", "text": "가"}
        for blocks in ([pb, para], [para, pb], [para, pb, pb, para]):
            doc = {"version": "1", "blocks": blocks}
            self.assertIn("E105", codes(hds.validate_document(doc)), blocks)

    def test_footnote_errors(self):
        bad = [[], [{"text": ""}], [{"text": "가", "after": "없는 말"}], [{"text": "가", "x": 1}]]
        for notes in bad:
            doc = {"version": "1", "blocks": [{"type": "paragraph", "text": "본문", "footnotes": notes}]}
            self.assertTrue({"E106", "E107"} & set(codes(hds.validate_document(doc))), notes)

    def test_emphasis_errors(self):
        cases = {"E109": [[], [{"text": "본문"}], [{"text": "본문", "color": "red"}], [{"text": "본문", "bold": 1}],
                          [{"text": "본문", "size": 12}]],
                 "E110": [[{"text": "없는 말", "bold": True}]]}
        for code, items in cases.items():
            for marks in items:
                doc = {"version": "1", "blocks": [{"type": "paragraph", "text": "본문", "emphasis": marks}]}
                self.assertIn(code, codes(hds.validate_document(doc)), marks)

    def test_footnote_on_heading_rejected(self):
        doc = {"version": "1", "blocks": [{"type": "heading", "level": 1, "text": "가", "footnotes": []}]}
        self.assertIn("E003", codes(hds.validate_document(doc)))

    def test_header_footer_errors(self):
        for hf in ("문자열", {"text": ""}, {"text": "{page}/{page}"}, {"text": "가", "align": "center"}):
            doc = {"version": "1", "footer": hf, "blocks": [{"type": "paragraph", "text": "가"}]}
            self.assertIn("E108", codes(hds.validate_document(doc)), hf)


class ProjectLintTest(unittest.TestCase):
    """단위 끝의 쪽 나눔은 허용하지만, 문서 끝이나 단위 경계에서 겹치면 오류."""

    def lint(self, units):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "units").mkdir()
            (proj / "spec_lock.json").write_text(json.dumps({"footer": {"text": "{page}"}}), encoding="utf-8")
            (proj / "outline.json").write_text(json.dumps(
                {"units": [{"id": f"U0{i + 1}"} for i in range(len(units))]}), encoding="utf-8")
            for i, blocks in enumerate(units):
                (proj / "units" / f"U0{i + 1}.json").write_text(json.dumps({"blocks": blocks}), encoding="utf-8")
            doc = hds.assemble(proj)
            return codes(hds.lint_project(proj)), doc

    def test_unit_boundaries(self):
        p, pb = {"type": "paragraph", "text": "가"}, {"type": "page_break"}
        errs, doc = self.lint([[p, pb], [p]])
        self.assertEqual(errs, [])
        self.assertEqual(doc["footer"], {"text": "{page}"})
        self.assertIn("E105", self.lint([[p], [p, pb]])[0])
        self.assertIn("E105", self.lint([[p, pb], [pb, p]])[0])


class ProduceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def produce(self, name):
        out = self.dir / name / "out.hwpx"
        result = native.produce(DOC, out, self.rhwp, self.dir / name / "work")
        self.assertTrue(result["ok"], result)
        return out

    def test_all_extensions_land(self):
        out = self.produce("a")
        self.assertEqual(native.check(out, DOC), [])
        with zipfile.ZipFile(out) as z:
            xml = z.read("Contents/section0.xml").decode("utf-8")
        # 각주 번호는 앞쪽 근거 뒤 → 문단 끝 순서, 본문은 번호 뒤에 온다
        self.assertLess(xml.index("앞쪽 근거의 출처"), xml.index("문단 끝 각주"))
        self.assertRegex(xml, r'numType="FOOTNOTE".*?</hp:ctrl><hp:t> 앞쪽 근거의 출처')
        # 강조 글자는 뜻한 서식의 런으로 갈라진다
        self.assertRegex(xml, r'charPrIDRef="(\d+)"><hp:t>앞쪽 근거</hp:t>')
        self.assertIn("<hp:t>뒤쪽 근거</hp:t>", xml)
        # 쪽 나눔은 둘째 장 제목 문단에 걸린다
        self.assertRegex(xml, r'pageBreak="1"[^>]*><hp:run[^>]*><hp:t>둘째 장')

    def test_check_catches_missing_edits(self):
        out = self.produce("a")
        problems = native.check(self.dir / "a" / "work" / "scaffold.hwpx", DOC)
        self.assertTrue(any("강조 미반영" in p for p in problems), problems)
        self.assertTrue(any("각주" in p for p in problems), problems)
        self.assertEqual(native.check(out, DOC), [])

    def test_deterministic_and_receipt(self):
        a, b = self.produce("a"), self.produce("b")
        self.assertEqual(hashlib.sha256(a.read_bytes()).digest(), hashlib.sha256(b.read_bytes()).digest())
        spec = self.dir / "doc.json"
        spec.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
        rec = receipt.make(spec, a, self.rhwp)
        self.assertTrue(receipt.verify(rec, self.rhwp)["reproduced"])


if __name__ == "__main__":
    unittest.main()
