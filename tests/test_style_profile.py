"""문서 서식(style)과 문서 유형 프로필: python3 -m unittest tests.test_style_profile

StyleValidateTest·SummarizeTest 는 rhwp 없이 돈다. 나머지는 rhwp 바이너리가 필요하다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
import hds  # noqa: E402
import native  # noqa: E402
import rhwp_locate  # noqa: E402
import style_profile  # noqa: E402

STYLE = {"body_pt": 15, "heading_pt": 16, "title_pt": 22, "table_pt": 12, "line_spacing": 180}
DOC = {"version": "1", "title": "서식 시험", "font": "휴먼명조", "style": STYLE,
       "blocks": [
           {"type": "heading", "level": 1, "text": "추진 배경"},
           {"type": "list", "items": ["첫째 항목", {"text": "둘째 단계 항목", "level": 2,
                                                  "emphasis": [{"text": "둘째", "bold": True, "size": 18}]}]},
           {"type": "table", "header_row": True, "rows": [["구분", "값"], ["가", "1"]],
            "cell_emphasis": [{"row": 1, "col": 1, "size": 14}]},
           {"type": "paragraph", "text": "끝 문단"},
       ]}


def codes(findings):
    return [f.code for f in findings if f.severity == "error"]


class StyleValidateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(codes(hds.validate_document(DOC)), [])

    def test_invalid(self):
        for bad in ({}, {"body_pt": 5}, {"body_pt": 10.3}, {"line_spacing": 150.5}, {"line_spacing": 500},
                    {"color": "#000000"}, {"body_pt": True}, "15pt"):
            with self.subTest(bad=bad):
                self.assertIn("E123", codes(hds.validate_document(dict(DOC, style=bad))))

    def test_list_indent_scales_with_body(self):
        _, plan = native.split(DOC)
        left, hang = native.list_indent(2, "○", 1.5)
        self.assertEqual((plan["indents"][1]["left"], -plan["indents"][1]["indent"]), (left, hang))
        self.assertEqual(left, round(native.LIST_STEP_HU * 1.5))
        # style 이 없으면 예전 값 그대로 (영수증 재현)
        self.assertEqual(native.list_indent(2, "○"), (native.LIST_STEP_HU, native.marker_hu("○") + native.SPACE_HU))

    def test_table_width_lint_uses_table_pt(self):
        blocks = [{"type": "table", "rows": [["가" * 15, "나"]]}]
        self.assertEqual(hds.lint_table_widths(blocks, 150), [])
        self.assertEqual([f.code for f in hds.lint_table_widths(blocks, 150, pt=20)], ["W201"])

    def test_assemble_passes_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "units").mkdir()
            (p / "spec_lock.json").write_text(json.dumps({"title": "가", "style": {"body_pt": 12}}), encoding="utf-8")
            (p / "outline.json").write_text(json.dumps({"units": []}), encoding="utf-8")
            self.assertEqual(hds.assemble(p)["style"], {"body_pt": 12})


def feature(body_pt=15.0, font="휴먼명조", order=("□", "○", "-"), pagenum=True, ls=("PERCENT", 160)):
    return {"page": {"width_mm": 210, "height_mm": 297, "landscape": False,
                     "margins_mm": {"left": 20, "right": 20, "top": 15, "bottom": 15, "header": 10, "footer": 10}},
            "tables": 1, "header": False, "footer": pagenum, "pagenum": pagenum,
            "body_chars": 900, "table_chars": 100, "body": {"font": font, "pt": body_pt},
            "table_text": {"font": font, "pt": 12.0}, "line_spacing": ls, "body_align": "JUSTIFY",
            "title": {"pt": 20.0, "bold": True, "align": "CENTER"}, "top_marker_pt": 16.0,
            "markers": {m: 3 for m in order}, "marker_order": list(order), "endings": {"nominal": 8, "narrative": 1}}


class SummarizeTest(unittest.TestCase):
    def test_majority(self):
        feats = [feature(), feature(), feature(body_pt=14.5), feature(font="바탕", order=("1.", "가."), pagenum=False)]
        prof = style_profile.summarize(feats, [])
        r = prof["recommend"]
        self.assertEqual(r["font"], "휴먼명조")
        self.assertEqual(r["style"], {"body_pt": 15.0, "title_pt": 20.0, "heading_pt": 16.0, "table_pt": 12.0,
                                      "line_spacing": 160})
        self.assertEqual(r["convention"], "gaejosik")
        self.assertEqual(r["list_markers"], ["□", "○", "-"])
        self.assertEqual(r["footer"], {"text": "- {page} -"})
        self.assertEqual(r["ending"], "nominal")
        self.assertEqual(prof["agreement"]["font"], 0.75)
        self.assertEqual(codes(hds.validate_page(r) + hds.validate_style(r)), [])

    def test_low_agreement_is_left_out(self):
        feats = [feature(body_pt=10), feature(body_pt=13, order=("□", "-")), feature(body_pt=16, order=("1.",)),
                 feature(body_pt=20, order=("Ⅰ.", "□"))]
        prof = style_profile.summarize(feats, [])
        self.assertNotIn("body_pt", prof["recommend"].get("style", {}))
        self.assertNotIn("convention", prof["recommend"])
        self.assertNotIn("list_markers", prof["recommend"])

    def test_unsupported_numbering(self):
        prof = style_profile.summarize([feature(order=("Ⅰ.", "□", "○"))] * 3, [])
        self.assertTrue(any("Ⅰ." in u for u in prof["unsupported"]))
        self.assertEqual(prof["recommend"]["convention"], "gaejosik")

    def test_non_percent_line_spacing_unsupported(self):
        prof = style_profile.summarize([feature(ls=("FIXED", 2000))] * 2, [])
        self.assertNotIn("line_spacing", prof["recommend"]["style"])
        self.assertTrue(any("FIXED" in u for u in prof["unsupported"]))


class StyleBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def test_style_applied_and_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.hwpx"
            res = native.produce(DOC, out, self.rhwp, Path(tmp) / "w")
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(native.check(out, DOC, self.rhwp), [])
            shapes, styles = native.char_shapes(out), native.char_styles(out)
            heights = [{shapes[c]["height"] for c in ids} for ids in styles if ids]
            self.assertEqual(heights[0], {2200})  # 제목
            self.assertEqual(heights[1], {1600})  # 개요
            self.assertEqual(heights[2], {1500})  # 목록 1단계
            self.assertEqual(heights[3], {1500, 1800})  # 강조 18pt 가 이긴다
            cells = native.table_cells(out)[0]
            self.assertEqual({shapes[c]["height"] for cell in cells for c in cell["chars"]}, {1200, 1400})
            self.assertEqual(native.para_line_spacings(out)[0], ("PERCENT", 180))

    def test_check_catches_missing_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.hwpx"
            plain = {k: v for k, v in DOC.items() if k != "style"}
            self.assertTrue(native.produce(plain, out, self.rhwp, Path(tmp) / "w")["ok"])
            problems = native.check(out, DOC, self.rhwp)
            self.assertTrue(any("문서 글자 크기 미반영" in p for p in problems))
            self.assertTrue(any("문서 줄 간격 미반영" in p for p in problems))
            self.assertTrue(any("표 글자 크기 미반영" in p for p in problems))


class ProfileRoundTripTest(unittest.TestCase):
    """style 로 만든 문서들을 분석하면 같은 서식이 프로필로 나오고, 등록·lock 이 spec_lock 에 옮긴다."""

    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.environ.get("HWP_MASTER_HOME")
        os.environ["HWP_MASTER_HOME"] = str(Path(self.tmp.name) / "home")

    def tearDown(self):
        if self.home is None:
            os.environ.pop("HWP_MASTER_HOME", None)
        else:
            os.environ["HWP_MASTER_HOME"] = self.home
        self.tmp.cleanup()

    def make_docs(self, n=3) -> Path:
        d = Path(self.tmp.name) / "docs"
        d.mkdir()
        for i in range(n):
            doc = dict(DOC, margins_mm={"left": 20, "right": 20, "top": 15, "bottom": 15},
                       footer={"text": "- {page} -"},
                       blocks=[{"type": "list", "items": [
                           f"{i}번째 문서의 첫째 항목으로 길게 쓴 개조식 문장이며 명사형으로 끝맺음",
                           {"text": "둘째 단계 항목도 명사형으로 끝나는 충분히 긴 문장을 씀", "level": 2},
                           {"text": "셋째 단계 항목 역시 명사형 종결로 끝나는 긴 문장을 씀", "level": 3}]},
                           {"type": "table", "rows": [["구분", "값"], ["가", "1"]]}])
            self.assertTrue(native.produce(doc, d / f"d{i}.hwpx", self.rhwp, Path(self.tmp.name) / f"w{i}")["ok"])
        return d

    def test_round_trip(self):
        docs = self.make_docs()
        prof = style_profile.analyze([docs], self.rhwp)
        r = prof["recommend"]
        self.assertEqual(prof["documents"], 3)
        self.assertEqual(r["font"], "휴먼명조")
        self.assertEqual(r["style"]["body_pt"], 15.0)
        self.assertEqual(r["style"]["title_pt"], 22.0)
        self.assertEqual(r["style"]["table_pt"], 12.0)
        self.assertEqual(r["style"]["line_spacing"], 180)
        self.assertEqual(r["margins_mm"]["left"], 20)
        self.assertEqual(r["marker_system"], ["□", "○", "-"])
        self.assertEqual(r["convention"], "gaejosik")
        self.assertEqual(r["footer"], {"text": "- {page} -"})
        self.assertEqual(r["ending"], "nominal")
        self.assertEqual(len(prof["sources"]), 3)

        path = style_profile.register(prof, "시험_보고서", force=False)
        self.assertTrue(path.exists())
        with self.assertRaises(SystemExit):
            style_profile.register(prof, "시험_보고서", force=False)
        with self.assertRaises(SystemExit):
            style_profile.register(prof, "../밖", force=False)

        project = Path(self.tmp.name) / "proj"
        project.mkdir()
        (project / "spec_lock.json").write_text(json.dumps({"title": "새 문서", "font": "바탕"}, ensure_ascii=False),
                                                encoding="utf-8")
        res = style_profile.lock("시험_보고서", project, force=False)
        self.assertIn("font", res["kept"])
        self.assertIn("style", res["set"])
        spec = json.loads((project / "spec_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["font"], "바탕")
        self.assertEqual(spec["style"]["body_pt"], 15.0)
        self.assertEqual(spec["profile"]["name"], "시험_보고서")
        style_profile.lock("시험_보고서", project, force=True)
        spec = json.loads((project / "spec_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["font"], "휴먼명조")

    def test_hwp5_input_and_cli(self):
        docs = self.make_docs(1)
        hwp = docs / "d0.hwp"
        subprocess.run([self.rhwp, "convert", str(docs / "d0.hwpx"), str(hwp)],
                       check=True, capture_output=True)
        (docs / "d0.hwpx").unlink()
        proc = subprocess.run([sys.executable, str(SCRIPTS / "style_profile.py"), "analyze", str(docs)],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("문서 1건", proc.stdout)
        self.assertIn("본문: 15.0pt", proc.stdout)


if __name__ == "__main__":
    unittest.main()
