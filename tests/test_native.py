"""쪽 나눔·각주·머리말/꼬리말 블록: python3 -m unittest tests.test_native

SplitTest·ValidateTest 는 rhwp 없이 돈다. ProduceTest 는 rhwp 바이너리(`RHWP_BIN` 또는 PATH)가 필요하다.
"""
import hashlib
import json
import sys
import tempfile
import unittest
import struct
import zipfile
import zlib
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


def png_bytes(w: int, h: int) -> bytes:
    """흰 RGB PNG (stdlib 만으로)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\xff" * (3 * w) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


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
                          [{"text": "본문", "font": "굴림"}]],
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


class ImageTest(unittest.TestCase):
    def test_split_image_and_caption(self):
        doc = {"version": "1", "blocks": [
            {"type": "table", "rows": [["가"]]},
            {"type": "image", "path": "/x/a.png", "caption": "<그림 1> 가"},
            {"type": "paragraph", "text": "끝"}]}
        spec, plan = native.split(doc)
        self.assertEqual([b.get("text") for b in spec["blocks"]],
                         [None, native.IMAGE_MARK.format(n=0), "<그림 1> 가", "끝"])
        self.assertEqual(plan["images"][0]["block"], 1)
        self.assertEqual(plan["aligns"], [{"block": 1, "align": "center", "keep": True},
                                          {"block": 2, "align": "center", "keep": False}])

    def test_image_size_and_width(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "a.png"
            png.write_bytes(png_bytes(40, 20))
            self.assertEqual(native.image_size_px(png), (40, 20))
            jpg = Path(tmp) / "a.jpg"  # SOI + APP0 + SOF0(높이 30, 너비 50)
            jpg.write_bytes(b"\xff\xd8" + b"\xff\xe0\x00\x04\x00\x00"
                            + b"\xff\xc0\x00\x11\x08\x00\x1e\x00\x32" + b"\x00" * 12)
            self.assertEqual(native.image_size_px(jpg), (50, 30))
            doc = {"version": "1"}
            self.assertEqual(native.image_width_hu(doc, {"path": str(png)}), 40 * 75)
            big = Path(tmp) / "b.png"
            big.write_bytes(png_bytes(2000, 10))
            self.assertEqual(native.image_width_hu(doc, {"path": str(big)}), round(150 * native.HU_PER_MM))
            self.assertEqual(native.image_width_hu(doc, {"path": str(big), "width_mm": 50}),
                             round(50 * native.HU_PER_MM))

    def test_validate_image(self):
        bad = [{"path": ""}, {"path": "a.gif"}, {"path": "a.png", "width_mm": 0},
               {"path": "a.png", "align": "justify"}]
        for b in bad:
            doc = {"version": "1", "blocks": [dict(b, type="image")]}
            self.assertIn("E111", codes(hds.validate_document(doc)), b)
        doc = {"version": "1", "blocks": [{"type": "image", "path": "a.png", "caption": ""}]}
        self.assertIn("E120", codes(hds.validate_document(doc)))
        doc = {"version": "1", "blocks": [{"type": "image", "path": "a.png", "width_mm": 200}]}
        self.assertIn("E112", codes(hds.validate_document(doc)))
        self.assertIn("E113", codes(hds.lint_image_files(doc["blocks"], Path("/nonexistent"))))


LIST_DOC = {"version": "1", "title": "목록", "blocks": [
    {"type": "list", "items": [
        {"text": "첫째 항목", "emphasis": [{"text": "첫째", "bold": True}]},
        {"text": "하위 - 항목", "level": 2, "footnotes": [{"text": "근거", "after": "하위"}]},
        "둘째 항목"]},
    {"type": "paragraph", "text": "목록 뒤 문단"},
    {"type": "list", "markers": ["1.", "가."], "items": ["번호", {"text": "하위", "level": 2}]}]}


class ListTest(unittest.TestCase):
    def test_split_list(self):
        spec, plan = native.split(LIST_DOC)
        self.assertEqual([b["text"] for b in spec["blocks"]],
                         ["□ 첫째 항목", "○ 하위 - 항목", "□ 둘째 항목", "목록 뒤 문단", "1. 번호", "가. 하위"])
        self.assertTrue(all(b["type"] == "paragraph" for b in spec["blocks"]))
        # 단계마다 10pt, 내어쓰기는 글머리 + 빈칸 폭 (한컴 2024 실측)
        self.assertEqual(plan["indents"], [
            {"block": 0, "left": 0, "indent": -1475}, {"block": 1, "left": 1000, "indent": -1475},
            {"block": 2, "left": 0, "indent": -1475}, {"block": 4, "left": 0, "indent": -1325},
            {"block": 5, "left": 1000, "indent": -1775}])
        # 강조·각주 위치는 글머리만큼 밀리고, 글머리 안의 "-" 와 헷갈리지 않는다
        self.assertEqual(plan["emphasis"][0]["offset"], 2)
        self.assertEqual(plan["footnotes"], [{"block": 1, "offset": 4, "text": "근거"}])
        self.assertFalse(native.plan_empty(plan))

    def test_validate_list(self):
        self.assertEqual(codes(hds.validate_blocks(LIST_DOC["blocks"])), [])
        bad = [{"type": "list", "items": []},
               {"type": "list", "items": [{"text": "a", "level": 2}]},
               {"type": "list", "items": ["a", {"text": "b", "level": 5}]},
               {"type": "list", "items": [{"text": " "}]},
               {"type": "list", "items": [{"text": "a", "bullet": "x"}]},
               {"type": "list", "markers": ["1.", "가."], "items": ["a", {"text": "b", "level": 2},
                                                             {"text": "c", "level": 3}]},
               {"type": "list", "items": [{"text": "a", "emphasis": [{"text": "z", "bold": True}]}]}]
        self.assertEqual(codes(hds.validate_blocks(bad)), ["E114"] * 6 + ["E110"])
        self.assertEqual(codes(hds.validate_blocks([{"type": "list", "markers": ["a b"], "items": ["x"]}])),
                         ["E115"])

    def test_convention_sees_items(self):
        import conventions
        blocks = [{"type": "list", "items": ["수출 증가", {"text": "반도체가 늘었습니다.", "level": 3}]}]
        found = [(f.code, f.where) for f in conventions.lint_convention(blocks, "gaejosik")]
        self.assertEqual(found, [("C011", "blocks[0].items[1]"), ("C111", "blocks[0].items[1]")])

    def test_budget_counts_items(self):
        unit = {"target_chars": 10}
        blocks = [{"type": "list", "items": ["가나다라마", {"text": "바사아자차", "footnotes": [{"text": "x"}]}]}]
        self.assertEqual(hds._lint_unit_budget(unit, blocks, "u"), [])


TABLE_DOC = {"version": "1", "title": "표", "blocks": [
    {"type": "paragraph", "text": "앞"},
    {"type": "table", "header_row": True, "col_widths": [2, 3, 2], "cell_align": ["center", "left", "right"],
     "rows": [["구분", "값", ""], ["가", "1", "2"]]},
    {"type": "table", "header_row": True, "header_fill": "#dce6f2", "rows": [["a", "b"], ["c", "d"]]},
    {"type": "table", "col_widths": [1, 4], "rows": [["x", "y"]]},
    {"type": "table", "rows": [["plain"]]}]}


class TableStyleTest(unittest.TestCase):
    def test_split_strips_style(self):
        spec, plan = native.split(TABLE_DOC)
        self.assertTrue(all(set(b) <= {"type", "rows", "cell_align", "text"} for b in spec["blocks"]))
        self.assertEqual(plan["tables"], [
            {"table": 0, "cols": 3, "header_fill": "#D9D9D9", "col_widths": [2, 3, 2], "merges": [],
             "cell_emphasis": []},
            {"table": 1, "cols": 2, "header_fill": "#DCE6F2", "col_widths": None, "merges": [],
             "cell_emphasis": []},
            {"table": 2, "cols": 2, "header_fill": None, "col_widths": [1, 4], "merges": [],
             "cell_emphasis": []},
            {"table": 3, "cols": 1, "header_fill": None, "col_widths": None, "merges": [],
             "cell_emphasis": []}])
        self.assertEqual([native.table_styled(t) for t in plan["tables"]], [True, True, True, False])

    def test_split_widths(self):
        self.assertEqual(native.split_widths(41952, [2, 3, 2]), [11986, 17979, 11987])
        self.assertEqual(sum(native.split_widths(41953, [1, 1, 1])), 41953)

    def test_validate(self):
        self.assertEqual(codes(hds.validate_blocks(TABLE_DOC["blocks"])), [])
        bad = [{"type": "table", "rows": [["a"]], "header_row": "yes"},
               {"type": "table", "rows": [["a"]], "header_row": True, "header_fill": "gray"},
               {"type": "table", "rows": [["a"]], "header_fill": "#FFFFFF"},
               {"type": "table", "rows": [["a", "b"]], "col_widths": [1]},
               {"type": "table", "rows": [["a", "b"]], "col_widths": [1, 0]}]
        self.assertEqual(codes(hds.validate_blocks(bad)), ["E116"] * 3 + ["E117"] * 2)

    def test_w201_uses_ratios(self):
        cell = "가" * 14
        even = [{"type": "table", "rows": [[cell, "b", "c"]]}]
        wide = [{"type": "table", "rows": [[cell, "b", "c"]], "col_widths": [4, 1, 1]}]
        self.assertEqual([f.code for f in hds.lint_table_widths(even, 150)], ["W201"])
        self.assertEqual([f.code for f in hds.lint_table_widths(wide, 150)], [])


MERGE_DOC = {"version": "1", "title": "병합", "blocks": [
    {"type": "table", "header_row": True, "col_widths": [1, 2, 1],
     "merges": [{"row": 0, "col": 1, "colspan": 2}, {"row": 1, "col": 0, "rowspan": 2}],
     "cell_emphasis": [{"row": 1, "col": 2, "text": "54", "bold": True, "color": "#C00000"},
                       {"row": 1, "col": 0, "underline": True}],
     "rows": [["구분", "실적", ""], ["주력", "반도체", "54.5%"], ["", "자동차", "-4%"]]}]}


class MergeTest(unittest.TestCase):
    def test_split(self):
        _, plan = native.split(MERGE_DOC)
        tb = plan["tables"][0]
        self.assertEqual(tb["merges"], [{"row": 0, "col": 1, "rowspan": 1, "colspan": 2},
                                        {"row": 1, "col": 0, "rowspan": 2, "colspan": 1}])
        self.assertEqual([(e["start"], e["count"]) for e in tb["cell_emphasis"]], [(0, 2), (0, 2)])
        self.assertEqual(native.covered_cells(tb["merges"]), {(0, 2), (2, 0)})

    def test_validate(self):
        self.assertEqual(codes(hds.validate_blocks(MERGE_DOC["blocks"])), [])
        rows = [["a", "b"], ["c", "d"]]
        bad = [{"type": "table", "rows": rows, "merges": []},
               {"type": "table", "rows": rows, "merges": [{"row": 0, "col": 0}]},
               {"type": "table", "rows": rows, "merges": [{"row": 1, "col": 0, "rowspan": 2}]},
               {"type": "table", "rows": rows, "merges": [{"row": 0, "col": 0, "colspan": 2}]},
               {"type": "table", "rows": [["a", ""], ["", ""]], "merges": [
                   {"row": 0, "col": 0, "colspan": 2}, {"row": 0, "col": 1, "rowspan": 2}]},
               {"type": "table", "rows": [["a", ""]], "merges": [{"row": 0, "col": 0, "colspan": 2}],
                "cell_emphasis": [{"row": 0, "col": 1, "bold": True}]},
               {"type": "table", "rows": rows, "cell_emphasis": [{"row": 0, "col": 0, "text": "z", "bold": True}]},
               {"type": "table", "rows": rows, "cell_emphasis": [{"row": 0, "col": 0}]}]
        self.assertEqual(codes(hds.validate_blocks(bad)),
                         ["E118"] * 5 + ["E119", "E110", "E109"])

    def test_w201_merged_width(self):
        cell = "가" * 30
        rows = [[cell, ""], ["a", "b"]]
        self.assertEqual([f.code for f in hds.lint_table_widths([{"type": "table", "rows": rows}], 150)],
                         ["W201"])
        merged = {"type": "table", "rows": rows, "merges": [{"row": 0, "col": 0, "colspan": 2}]}
        self.assertEqual(hds.lint_table_widths([merged], 150), [])


class CaptionEmphasisTest(unittest.TestCase):
    def test_char_props_extended(self):
        self.assertEqual(native.char_props({"text": "x", "size": 14, "highlight": "#ffff00", "strike": True}),
                         '{"strikethrough":true,"shadeColor":"#FFFF00","fontSize":1400}')
        shape = {"bold": False, "strike": True, "shade": "#FFFF00", "height": 1400, "color": "#000000"}
        self.assertTrue(native.props_match(shape, {"strikethrough": True, "shadeColor": "#FFFF00", "fontSize": 1400}))
        self.assertFalse(native.props_match(shape, {"fontSize": 1200}))
        self.assertFalse(native.props_match(shape, {"bold": True}))

    def test_validate_extended_emphasis(self):
        ok = {"type": "paragraph", "text": "가나다", "emphasis": [{"text": "가", "size": 12.5, "highlight": "#FFFF00"}]}
        self.assertEqual(codes(hds.validate_blocks([ok])), [])
        bad = {"type": "paragraph", "text": "가나다", "emphasis": [
            {"text": "가", "size": 100}, {"text": "나", "highlight": "yellow"}, {"text": "다", "strike": 1}]}
        self.assertEqual(codes(hds.validate_blocks([bad])), ["E109"] * 3)

    def test_caption_numbering(self):
        doc = {"version": "1", "blocks": [
            {"type": "paragraph", "text": "a"}, {"type": "page_break"},
            {"type": "table", "caption": "<표 {n}> 첫", "rows": [["a"]]},
            {"type": "image", "path": "x.png", "caption": "<그림 {n}> 첫"},
            {"type": "table", "rows": [["b"]]},
            {"type": "table", "caption": "<표 {n}> 둘", "rows": [["c"]]}]}
        spec, plan = native.split(doc)
        texts = [b.get("text", b["type"]) for b in spec["blocks"]]
        self.assertEqual(texts, ["a", "<표 1> 첫", "table", "[[hwp-master:image:0]]", "<그림 1> 첫",
                                 "table", "<표 2> 둘", "table"])
        # 쪽 나눔은 표가 아니라 그 위 캡션에 걸리고, 캡션·그림 문단은 다음 문단과 묶인다
        self.assertEqual(plan["page_breaks"], [1])
        self.assertEqual([(a["block"], a["keep"]) for a in plan["aligns"]],
                         [(1, True), (3, True), (4, False), (6, True)])

    def test_validate_caption(self):
        blocks = [{"type": "table", "caption": "<표 3> 손번호", "rows": [["a"]]},
                  {"type": "table", "caption": "{n}{n}", "rows": [["a"]]},
                  {"type": "table", "caption": " ", "rows": [["a"]]},
                  {"type": "image", "path": "a.png", "caption": "<그림 {n}> 자동"}]
        found = hds.validate_blocks(blocks)
        self.assertEqual([(f.code, f.where) for f in found],
                         [("W204", "blocks[0]"), ("E120", "blocks[1]"), ("E120", "blocks[2]")])


class PageTest(unittest.TestCase):
    def test_geometry(self):
        geo = hds.page_geometry({"orientation": "landscape", "margins_mm": {"left": 20, "right": 20}})
        self.assertEqual((geo["width_mm"], geo["height_mm"], geo["content_mm"]), (297.0, 210.0, 257.0))
        self.assertEqual(hds.page_geometry({})["content_mm"], 150.0)

    def test_page_props_and_split(self):
        doc = {"version": "1", "orientation": "landscape", "page_size": {"width_mm": 297, "height_mm": 210},
               "margins_mm": {"left": 20, "top": 15}, "blocks": [{"type": "table", "rows": [["a"]]}]}
        spec, plan = native.split(doc)
        self.assertNotIn("orientation", spec)
        self.assertNotIn("margins_mm", spec)
        self.assertEqual(spec["page_size"], {"width_mm": 210, "height_mm": 297})  # scaffold 는 세로 치수
        self.assertEqual(plan["page"], {"landscape": True, "marginLeft": round(20 * native.HU_PER_MM),
                                        "marginTop": round(15 * native.HU_PER_MM)})
        self.assertFalse(native.plan_empty(plan))
        self.assertIsNone(native.page_props({"version": "1", "orientation": "portrait"}))
        # 본문 폭 297-20-30 = 247mm 에서 표 바깥 여백 2mm 를 뺀다
        self.assertEqual(native.table_total_hu(doc), round(247 * native.HU_PER_MM) - 566)

    def test_validate(self):
        base = {"version": "1", "blocks": [{"type": "paragraph", "text": "a"}]}
        self.assertEqual(codes(hds.validate_document(dict(base, orientation="landscape",
                                                          margins_mm={"left": 15, "right": 15}))), [])
        for bad in ({"orientation": "wide"}, {"margins_mm": {"inner": 10}}, {"margins_mm": {"left": -1}},
                    {"margins_mm": {"left": 90, "right": 90}}, {"orientation": "landscape", "margins_mm": {"top": 90, "bottom": 90}}):
            self.assertEqual(codes(hds.validate_document(dict(base, **bad))), ["E121"], bad)

    def test_image_width_uses_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "wide.png"
            png.write_bytes(png_bytes(3000, 100))  # 본문 폭보다 넓은 그림은 본문 폭으로 줄어든다
            img = {"path": str(png)}
            self.assertEqual(native.image_width_hu({"orientation": "landscape"}, img), round(237 * native.HU_PER_MM))
            self.assertEqual(native.image_width_hu({}, img), round(150 * native.HU_PER_MM))


def toc_doc(title=None):
    filler = "쪽을 채우는 본문 문단입니다. " * 14
    blocks = [{"type": "toc"} if title is None else {"type": "toc", "title": title}, {"type": "page_break"}]
    for ch in (1, 2):
        blocks.append({"type": "heading", "level": 1, "text": f"{ch}장"})
        blocks.append({"type": "heading", "level": 2, "text": f"{ch}장 1절"})
        blocks += [{"type": "paragraph", "text": filler}] * 12
        blocks.append({"type": "heading", "level": 3, "text": "목차 밖"})
    return {"version": "1", "title": "목차", "blocks": blocks}


class TocTest(unittest.TestCase):
    def test_entries_and_split(self):
        doc = toc_doc()
        self.assertEqual([(e["heading"], e["number"], e["text"]) for e in native.toc_entries(doc, 2)],
                         [(0, "1.", "1장"), (1, "1.1.", "1장 1절"), (3, "2.", "2장"), (4, "2.1.", "2장 1절")])
        self.assertEqual([e["number"] for e in native.toc_entries(doc, 3)][:3], ["1.", "1.1.", "1.1.1."])
        spec, plan = native.split(doc, {0: 2, 1: 2, 3: 3, 4: 3})
        self.assertEqual([b["text"] for b in spec["blocks"][:5]],
                         ["목차", "1. 1장\t2", "1.1. 1장 1절\t2", "2. 2장\t3", "2.1. 2장 1절\t3"])
        self.assertEqual([(i["left"], i.get("tab_right")) for i in plan["indents"]],
                         [(0, True), (1000, True), (0, True), (1000, True)])
        self.assertEqual(plan["page_breaks"], [5])  # 목차 뒤 첫 제목에서 쪽 나눔
        self.assertEqual(native.split(toc_doc(""))[0]["blocks"][0]["text"], "1. 1장\t0")

    def test_validate(self):
        self.assertEqual(codes(hds.validate_document(toc_doc())), [])
        bad = {"version": "1", "blocks": [{"type": "toc", "levels": 4}, {"type": "toc", "title": 3}]}
        self.assertEqual(codes(hds.validate_document(bad)), ["E122"] * 3)
        lonely = {"version": "1", "blocks": [{"type": "toc"}, {"type": "paragraph", "text": "a"}]}
        self.assertEqual([f.code for f in hds.validate_document(lonely)], ["W205"])


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


class BudgetTest(unittest.TestCase):
    def test_footnote_and_caption_count(self):
        blocks = [{"type": "paragraph", "text": "가" * 50, "footnotes": [{"text": "나" * 30}]},
                  {"type": "image", "path": "a.png", "caption": "다" * 20}]
        self.assertEqual(hds._lint_unit_budget({"target_chars": 100}, blocks, ""), [])
        self.assertEqual(hds._lint_unit_budget({"target_chars": 200}, blocks, "")[0].code, "W301")


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

    def test_image_block(self):
        png = self.dir / "fig.png"
        png.write_bytes(png_bytes(400, 200))
        doc = {"version": "1", "title": "그림", "blocks": [
            {"type": "paragraph", "text": "앞 문단"},
            {"type": "table", "rows": [["가", "나"]]},
            {"type": "image", "path": str(png), "caption": "<그림 1> 시험"},
            {"type": "paragraph", "text": "뒤 문단"}]}
        out = self.dir / "img" / "out.hwpx"
        result = native.produce(doc, out, self.rhwp, self.dir / "img" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, doc), [])
        paras = native.top_paragraphs(out)
        texts = [p["text"] for p in paras]
        cap = texts.index("<그림 1> 시험")
        # 그림 문단은 표 뒤, 캡션 바로 앞이고 표시 글자는 지워졌다
        self.assertEqual(texts[cap - 1], "")
        self.assertGreater(cap - 1, next(i for i, p in enumerate(paras) if p["table"]))
        self.assertEqual(texts[cap + 1], "뒤 문단")
        self.assertEqual(native.check(self.dir / "img" / "work" / "scaffold.hwpx", doc)[0][:4], "글자처럼")
        spec = self.dir / "img.json"
        spec.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        rec = receipt.make(spec, out, self.rhwp)
        self.assertIn(str(png), rec["images"])
        self.assertTrue(receipt.verify(rec, self.rhwp)["reproduced"])
        png.write_bytes(png_bytes(400, 201))
        self.assertFalse(receipt.verify(rec, self.rhwp)["reproduced"])

    def test_list_block(self):
        out = self.dir / "list" / "out.hwpx"
        result = native.produce(LIST_DOC, out, self.rhwp, self.dir / "list" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, LIST_DOC), [])
        margins = native.para_margins(out)
        texts = [p["text"] for p in native.top_paragraphs(out)]
        # 목록 문단만 여백이 바뀌고 뒤 일반 문단은 그대로다
        self.assertEqual(margins[texts.index("○ 하위 - 항목")], (1000, -1475))
        self.assertEqual(margins[texts.index("목록 뒤 문단")][0], 0)
        self.assertEqual(margins[texts.index("목록 뒤 문단")][1], 0)
        problems = native.check(self.dir / "list" / "work" / "scaffold.hwpx", LIST_DOC)
        self.assertTrue(any("목록 들여쓰기 미반영" in p for p in problems), problems)

    def test_table_style(self):
        out = self.dir / "tbl" / "out.hwpx"
        result = native.produce(TABLE_DOC, out, self.rhwp, self.dir / "tbl" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, TABLE_DOC), [])
        tables, fills = native.table_cells(out), native.fill_colors(out)
        widths = native.table_widths(out)
        # 머리 행만 배경·제목 셀이고, 표 전체 폭은 그대로 열 폭만 바뀐다
        self.assertEqual([fills[c["border_fill"]] for c in tables[0] if c["row"] == 0], ["#D9D9D9"] * 3)
        self.assertTrue(all(fills[c["border_fill"]] is None and not c["header"]
                            for c in tables[0] if c["row"] == 1))
        self.assertEqual([c["width"] for c in tables[2]], native.split_widths(widths[2], [1, 4]))
        self.assertEqual(widths, native.table_widths(self.dir / "tbl" / "work" / "scaffold.hwpx"))
        aligns = native.para_aligns(out)
        self.assertEqual(aligns[tables[0][5]["para_pr"]], "RIGHT")  # 데이터 행 cell_align 유지
        problems = native.check(self.dir / "tbl" / "work" / "scaffold.hwpx", TABLE_DOC)
        self.assertTrue(any("머리 행" in p for p in problems) and any("열 폭" in p for p in problems))

    def test_merge_and_cell_emphasis(self):
        out = self.dir / "merge" / "out.hwpx"
        result = native.produce(MERGE_DOC, out, self.rhwp, self.dir / "merge" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, MERGE_DOC), [])
        cells = native.table_cells(out)[0]
        # 가려진 셀은 사라지고, 병합 첫 셀이 범위를 갖는다. 머리 행은 남은 두 셀만 꾸민다.
        self.assertEqual(sorted((c["row"], c["col"]) for c in cells), [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual(sum(c["header"] for c in cells), 2)
        problems = native.check(self.dir / "merge" / "work" / "scaffold.hwpx", MERGE_DOC)
        self.assertTrue(any("병합" in p for p in problems) and any("셀 강조" in p for p in problems), problems)

    def test_caption_and_extended_emphasis(self):
        png = self.dir / "fig.png"
        png.write_bytes(png_bytes(300, 120))
        doc = {"version": "1", "title": "캡션", "blocks": [
            {"type": "paragraph", "text": "크게 형광 취소", "emphasis": [
                {"text": "크게", "size": 14}, {"text": "형광", "highlight": "#FFFF00"}, {"text": "취소", "strike": True}]},
            {"type": "table", "caption": "<표 {n}> 시험", "rows": [["a", "b"]],
             "cell_emphasis": [{"row": 0, "col": 1, "highlight": "#FFF2CC"}]},
            {"type": "image", "path": str(png), "caption": "<그림 {n}> 시험"},
            {"type": "paragraph", "text": "끝"}]}
        out = self.dir / "cap" / "out.hwpx"
        result = native.produce(doc, out, self.rhwp, self.dir / "cap" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, doc), [])
        texts = [p["text"] for p in native.top_paragraphs(out)]
        layout = native.para_layout(out)
        cap = texts.index("<표 1> 시험")
        self.assertEqual(layout[cap], ("CENTER", True))
        self.assertTrue(native.top_paragraphs(out)[cap + 1]["table"])  # 캡션 바로 뒤가 표
        self.assertIn("<그림 1> 시험", texts)
        problems = native.check(self.dir / "cap" / "work" / "scaffold.hwpx", doc)
        self.assertTrue(any("정렬·묶음" in p for p in problems), problems)

    def test_landscape_and_margins(self):
        doc = {"version": "1", "title": "가로", "orientation": "landscape",
               "margins_mm": {"left": 20, "right": 20, "top": 15},
               "blocks": [{"type": "table", "header_row": True, "rows": [["a", "b", "c"], ["1", "2", "3"]]},
                          {"type": "table", "col_widths": [1, 3], "rows": [["x", "y"]]},
                          {"type": "table", "rows": [["plain"]]}]}
        out = self.dir / "page" / "out.hwpx"
        result = native.produce(doc, out, self.rhwp, self.dir / "page" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, doc), [])
        page = native.page_def(out)
        self.assertEqual((page["width"], page["height"], page["landscape"]), (59528, 84188, True))
        # 꾸밈 없는 표까지 모두 새 본문 폭으로 넓어진다
        self.assertEqual(native.table_widths(out), [native.table_total_hu(doc)] * 3)
        self.assertGreater(native.table_total_hu(doc), max(native.table_widths(self.dir / "page" / "work" / "scaffold.hwpx")))
        problems = native.check(self.dir / "page" / "work" / "scaffold.hwpx", doc)
        self.assertTrue(any("용지 설정" in p for p in problems) and any("폭" in p for p in problems), problems)

    def test_toc_pages(self):
        doc = toc_doc()
        out = self.dir / "toc" / "out.hwpx"
        result = native.produce(doc, out, self.rhwp, self.dir / "toc" / "work")
        self.assertTrue(result["ok"], result)
        self.assertEqual(native.check(out, doc, self.rhwp), [])
        pages = native.heading_pages(out, self.rhwp, doc)
        self.assertEqual(result["toc_pages"], pages)  # 쪽 번호를 넣은 뒤에도 쪽 나눔이 같다
        self.assertEqual(pages[0], 2)  # 목차 뒤 쪽 나눔
        self.assertGreater(pages[3], pages[0])
        texts = [p["text"] for p in native.top_paragraphs(out)]
        self.assertIn(f"2. 2장\t{pages[3]}", texts)
        # 첫 패스(자리표시 0)는 실제 쪽과 다르다고 잡힌다
        problems = native.check(self.dir / "toc" / "work" / "toc-pass1.hwpx", doc, self.rhwp)
        self.assertTrue(any("목차 쪽 번호 0" in p for p in problems), problems)

    def test_deterministic_and_receipt(self):
        a, b = self.produce("a"), self.produce("b")
        self.assertEqual(hashlib.sha256(a.read_bytes()).digest(), hashlib.sha256(b.read_bytes()).digest())
        spec = self.dir / "doc.json"
        spec.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
        rec = receipt.make(spec, a, self.rhwp)
        self.assertTrue(receipt.verify(rec, self.rhwp)["reproduced"])


if __name__ == "__main__":
    unittest.main()
