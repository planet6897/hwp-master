"""#42 빌드 속도: 문단 서식 묶음 적용·표 속성 건너뛰기가 한 단계씩 돈 결과와 XML 까지 같은지, rhwp 호출이 줄었는지."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import native  # noqa: E402
import rhwp_locate  # noqa: E402

DOC = {"version": "1", "title": "묶음 시험", "blocks": [
    {"type": "heading", "level": 1, "text": "추진 배경"},
    {"type": "list", "items": [{"text": f"항목 {k}", "level": 1 + k % 3} for k in range(12)]},
    {"type": "table", "rows": [["구분", "금액"], ["장비", "100원"]], "header_row": True},
    {"type": "paragraph", "text": "본문 문단이다."},
    {"type": "list", "items": ["다시 첫 단계", {"text": "둘째 단계", "level": 2}]},
    {"type": "table", "rows": [["a", "b"], ["c", "d"]]},
    {"type": "image", "path": "", "caption": None} if False else {"type": "paragraph", "text": "끝."},
]}


def xml_parts(path):
    """비교용 정규형: 서식 목록(paraPr·charPr·borderFill)은 id 를 뺀 내용으로 바꾸고, 본문의 서식 번호는 그 내용으로
    바꾼다. rhwp 는 문서 style 처럼 같은 서식을 문단마다 새 번호로 덧붙이기도 해(중복), 묶음 적용은 중복 없이 하나를
    나눠 쓴다. 문단·run·칸이 가리키는 서식 내용이 모두 같으면 같은 문서다. 나머지 XML 은 바이트까지 같아야 한다."""
    import re
    with zipfile.ZipFile(path) as z:
        header = z.read("Contents/header.xml").decode("utf-8")
        section = z.read("Contents/section0.xml").decode("utf-8")
    table = {}
    for kind, ref in (("paraPr", "paraPrIDRef"), ("charPr", "charPrIDRef"), ("borderFill", "borderFillIDRef")):
        for m in re.finditer(rf'<hh:{kind} id="(\d+)"(.*?)</hh:{kind}>', header, re.S):
            table[(ref, m.group(1))] = m.group(2)
    def canon(m):
        return f'{m.group(1)}="{table.get((m.group(1), m.group(2)), m.group(2))}"'
    section = re.sub(r'(paraPrIDRef|charPrIDRef|borderFillIDRef)="(\d+)"', canon, section)
    header = re.sub(r"<hh:(paraProperties|charProperties|borderFills)\b.*?</hh:\1>", "", header, flags=re.S)
    header = re.sub(r'(paraPrIDRef|charPrIDRef|borderFillIDRef)="(\d+)"', canon, header)
    return {"header(서식 목록 제외)": header, "section0(서식 내용으로)": section}


class BatchTest(unittest.TestCase):
    def test_batched_build_equals_step_by_step(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            outs, edits = {}, {}
            for mode in (True, False):
                native.BATCH_EDITS = mode
                try:
                    out = Path(tmp) / f"out_{mode}.hwpx"
                    result = native._produce_once(json.loads(json.dumps(DOC)), out, rhwp, Path(tmp) / f"w_{mode}", None)
                finally:
                    native.BATCH_EDITS = True
                self.assertTrue(result["ok"], result.get("error"))
                final = sorted((Path(tmp) / f"w_{mode}").glob("e*.hwpx"), key=lambda p: p.stat().st_mtime)[-1]
                outs[mode] = xml_parts(final)
                edits[mode] = sum(1 for e in result["edits"] if e["cmd"][0] != "batch-para-format")
            self.assertEqual(outs[True], outs[False], "묶음 적용 결과가 한 단계씩 돈 결과와 XML 까지 같다")
            self.assertLess(edits[True], edits[False] / 2, f"rhwp 호출 수: 묶음 {edits[True]} / 한 단계씩 {edits[False]}")

    def compare_modes(self, doc):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            outs, calls = {}, {}
            for mode in (True, False):
                native.BATCH_EDITS = mode
                try:
                    work = Path(tmp) / f"w_{mode}"
                    result = native._produce_once(json.loads(json.dumps(doc)), Path(tmp) / f"o_{mode}.hwpx", rhwp,
                                                  work, None)
                finally:
                    native.BATCH_EDITS = True
                self.assertTrue(result["ok"], result.get("error"))
                final = sorted(work.glob("e*.hwpx"), key=lambda p: p.stat().st_mtime)[-1]
                outs[mode] = xml_parts(final)
                calls[mode] = sum(1 for e in result["edits"] if not e["cmd"][0].startswith("batch-"))
            self.assertEqual(outs[True], outs[False], "묶음 적용 결과가 한 단계씩 돈 결과와 XML 까지 같다")
            return calls

    def test_header_rows_and_table_font_across_tables(self):
        blocks = []
        for k in range(4):
            blocks.append({"type": "heading", "level": 1, "text": f"{k + 1}장"})
            blocks.append({"type": "table", "header_row": True,
                           "rows": [["구분", "2025년", "2026년", "비고"]] + [[f"항목{r}", "1", "2", "-"] for r in range(3)]})
        calls = self.compare_modes({"version": "1", "title": "표 꾸밈", "style": {"table_pt": 9}, "blocks": blocks})
        self.assertLess(calls[True], calls[False] / 4, calls)

    def test_cell_emphasis_keeps_per_table_order(self):
        doc = {"version": "1", "title": "강조", "blocks": [
            {"type": "table", "header_row": True, "rows": [["구분", "금액"], ["장비", "100원"]],
             "cell_emphasis": [{"row": 1, "col": 1, "text": "100", "bold": True, "color": "#FF0000"}]},
            {"type": "table", "header_row": True, "rows": [["항목", "값", "비고"], ["a", "b", "c"]]},
        ]}
        self.compare_modes(doc)

    def test_document_style_with_emphasis_and_footnotes(self):
        blocks = []
        for k in range(5):
            blocks.append({"type": "heading", "level": 1 + k % 2, "text": f"{k + 1}. 추진 과제"})
            blocks.append({"type": "paragraph", "text": f"과제 {k} 의 본문이다. 예산은 {k + 1}억 원이다."})
            blocks.append({"type": "paragraph", "text": "두 번째 본문 문단이다."})
        blocks[1]["emphasis"] = [{"text": "예산", "bold": True}]
        blocks[2]["footnotes"] = [{"text": "근거 자료"}]
        doc = {"version": "1", "title": "문서 서식", "blocks": blocks,
               "style": {"body_pt": 11, "heading_pt": 14, "title_pt": 18, "line_spacing": 170}}
        calls = self.compare_modes(doc)
        self.assertLess(calls[True], calls[False] / 3, calls)

    def test_footnote_with_tab_passes_self_check(self):
        """각주 글의 탭은 rhwp 가 <hp:tab/> 로 쓴다 — 자체 확인(check)이 누락으로 보지 않는다(재시험에서 R106)."""
        rhwp = rhwp_locate.locate(None)["path"]
        doc = {"version": "1", "title": "탭 각주", "blocks": [
            {"type": "paragraph", "text": "본문", "footnotes": [{"text": "호랑이	인간의 사나움"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fn.hwpx"
            result = native.produce(json.loads(json.dumps(doc)), out, rhwp, Path(tmp) / "w")
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(native.check(out, doc, rhwp), [])

    def test_footnote_with_quotes_and_line_break_paragraph(self):
        """따옴표 든 각주(&quot;)·줄 바꿈 든 문단 뒤의 블록도 자체 확인·블록 대응을 통과한다(재시험 R106·블록 누락)."""
        rhwp = rhwp_locate.locate(None)["path"]
        doc = {"version": "1", "title": "따옴표", "blocks": [
            {"type": "paragraph", "text": "\n".join(["첫 줄", "둘째 줄"])},
            {"type": "paragraph", "text": "본문", "footnotes": [{"text": '정의는 "전송상태값" 을 따른다'}]},
            {"type": "list", "items": ["항목"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "q.hwpx"
            result = native.produce(json.loads(json.dumps(doc)), out, rhwp, Path(tmp) / "w")
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(native.check(out, doc, rhwp), [])

    def test_para_pr_ref_rewrite_touches_only_top_level(self):
        xml = ('<hs:sec xmlns:hs="s" xmlns:hp="p"><hp:p id="0" paraPrIDRef="1"><hp:run><hp:tbl><hp:tr><hp:tc>'
               '<hp:subList><hp:p id="9" paraPrIDRef="1"/></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>'
               '<hp:p id="1" paraPrIDRef="1"></hp:p></hs:sec>')
        spans = native._top_p_spans(xml)
        self.assertEqual(len(spans), 2, "표 안 문단은 최상위가 아니다")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "a.hwpx", Path(tmp) / "b.hwpx"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("Contents/section0.xml", xml)
            native.set_para_pr_refs(src, dst, {1: "7"})
            out = zipfile.ZipFile(dst).read("Contents/section0.xml").decode()
        self.assertEqual(out, xml.replace('<hp:p id="1" paraPrIDRef="1">', '<hp:p id="1" paraPrIDRef="7">'))


if __name__ == "__main__":
    unittest.main()
