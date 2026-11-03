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


    def test_non_percent_line_spacing_unsupported(self):
        prof = style_profile.summarize([feature(ls=("FIXED", 2000))] * 2, [])
        self.assertNotIn("line_spacing", prof["recommend"]["style"])
        self.assertTrue(any("FIXED" in u for u in prof["unsupported"]))


OUTLINE = [{"text": "^1.", "num_format": "roman_capital"}, {"text": "^2."},
           {"text": "^3.", "num_format": "hangul_syllable"}]


class OutlineNumberingTest(unittest.TestCase):
    """제목 번호 모양(outline_numbering): 검사·목차 번호·프로필 매핑 (rhwp 없이)."""

    def doc(self, outline=OUTLINE):
        return {"version": "1", "outline_numbering": outline, "blocks": [
            {"type": "heading", "level": 1, "text": "가"}, {"type": "heading", "level": 2, "text": "나"},
            {"type": "heading", "level": 3, "text": "다"}, {"type": "heading", "level": 3, "text": "라"},
            {"type": "heading", "level": 1, "text": "마"}, {"type": "heading", "level": 4, "text": "바"}]}

    def test_valid_and_invalid(self):
        self.assertEqual(codes(hds.validate_document(self.doc())), [])
        for bad in ([], [{"text": "^1"}] * 8, [{"text": " "}], [{"text": "^2."}], [{"text": "^x"}],
                    [{"text": "^1.", "num_format": "roman"}], [{"text": "^1.", "num_format": "hangul_number"}],
                    [{"text": "^1.", "align": "left"}], "^1."):
            with self.subTest(bad=bad):
                self.assertIn("E124", codes(hds.validate_document(self.doc(bad))))

    def test_toc_numbers_follow_outline(self):
        nums = [e["number"] for e in native.toc_entries(self.doc(), 7)]
        self.assertEqual(nums, ["I.", "1.", "가.", "나.", "II.", "가)"])  # 4수준은 한글 기본 ^4)
        plain = {k: v for k, v in self.doc().items() if k != "outline_numbering"}
        self.assertEqual([e["number"] for e in native.toc_entries(plain, 7)][:3], ["1.", "1.1.", "1.1.1."])

    def test_format_number_matches_rhwp(self):
        self.assertEqual([native.format_number(n, "roman_capital") for n in (1, 4, 9, 14)], ["I", "IV", "IX", "XIV"])
        self.assertEqual(native.format_number(27, "latin_capital"), "AA")
        self.assertEqual(native.format_number(15, "hangul_syllable"), "15")
        self.assertEqual(native.format_number(3, "circled_digit"), "③")

    def test_profile_maps_roman_headings(self):
        prof = style_profile.summarize([feature(order=("Ⅰ.", "1.", "□", "○"))] * 3, [])
        r = prof["recommend"]
        self.assertEqual(r["outline_numbering"], [{"text": "^1.", "num_format": "roman_capital"},
                                                  {"text": "^2.", "num_format": "digit"}])
        self.assertEqual(r["list_markers"], ["□"])  # 항목 체계는 앞 3단계까지 본다
        self.assertEqual(r["convention"], "gaejosik")
        r = style_profile.summarize([feature(order=("Ⅰ.", "□", "○"))] * 3, [])["recommend"]
        self.assertEqual(r["outline_numbering"], [{"text": "^1.", "num_format": "roman_capital"}])
        self.assertEqual(r["list_markers"], ["□", "○"])
        # 1. 가. 로 시작하면 공문서 본문 항목으로 보고 제목 번호로 옮기지 않는다
        self.assertNotIn("outline_numbering",
                         style_profile.summarize([feature(order=("1.", "가."))] * 3, [])["recommend"])

    def test_lock_skips_outline_when_rhwp_lacks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.environ.get("HWP_MASTER_HOME")
            os.environ["HWP_MASTER_HOME"] = tmp
            try:
                prof = style_profile.summarize([feature(order=("Ⅰ.", "□"))] * 3, [])
                style_profile.register(prof, "로마", force=False)
                proj = Path(tmp) / "p"
                proj.mkdir()
                old = native.supports_outline_numbering
                native.supports_outline_numbering = lambda rhwp: False
                try:
                    res = style_profile.lock("로마", proj, force=False, rhwp="rhwp")
                finally:
                    native.supports_outline_numbering = old
                spec = json.loads((proj / "spec_lock.json").read_text(encoding="utf-8"))
                self.assertNotIn("outline_numbering", spec)
                self.assertTrue(any("outline_numbering" in u for u in res["unsupported"]))
            finally:
                if home is None:
                    os.environ.pop("HWP_MASTER_HOME", None)
                else:
                    os.environ["HWP_MASTER_HOME"] = home


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


class BuiltinProfilesTest(unittest.TestCase):
    """스킬에 함께 배포하는 기본 프로필 — 형식·출처·lock 가능 여부와 사용자 프로필 우선순위."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.environ.get("HWP_MASTER_HOME")
        os.environ["HWP_MASTER_HOME"] = str(Path(self.tmp.name) / "home")
        self.files = sorted(style_profile.BUILTIN_DIR.glob("*.json"))

    def tearDown(self):
        if self.home is None:
            os.environ.pop("HWP_MASTER_HOME", None)
        else:
            os.environ["HWP_MASTER_HOME"] = self.home
        self.tmp.cleanup()

    def test_files_are_well_formed_and_carry_provenance(self):
        self.assertGreaterEqual(len(self.files), 8)
        for f in self.files:
            with self.subTest(profile=f.stem):
                text = f.read_text(encoding="utf-8")
                prof = json.loads(text)
                style_profile.check_name(f.stem)
                self.assertEqual(prof["name"], f.stem)
                self.assertEqual(prof["version"], style_profile.PROFILE_VERSION)
                self.assertNotIn("sources", prof, "개별 문서 목록을 배포하지 않는다")
                self.assertRegex(text, r"\A[^\\]*\Z", "로컬 경로(역슬래시)가 없어야 한다")
                self.assertNotRegex(text, r"[A-Za-z]:/")
                self.assertIn("공식 서식 기준이 아님", prof["corpus"]["note"])
                self.assertEqual(prof["corpus"]["documents"], prof["documents"])

    def test_every_builtin_locks_into_a_valid_spec(self):
        for f in self.files:
            with self.subTest(profile=f.stem):
                project = Path(self.tmp.name) / f"p_{f.stem}"
                project.mkdir()
                res = style_profile.lock(f.stem, project, force=False)  # 내부에서 E1xx 검사
                spec = json.loads((project / "spec_lock.json").read_text(encoding="utf-8"))
                self.assertEqual(spec["profile"]["origin"], "builtin")
                self.assertTrue(res["set"])

    def test_user_profile_shadows_builtin_and_list_marks_origin(self):
        name = self.files[0].stem
        self.assertEqual(style_profile.find(name)[1], "builtin")
        prof = json.loads(self.files[0].read_text(encoding="utf-8"))
        style_profile.register(dict(prof, documents=1), name, force=False)
        self.assertEqual(style_profile.find(name)[1], "user")
        rows = {r["name"]: r for r in style_profile.list_profiles()}
        self.assertEqual(rows[name]["origin"], "user")
        self.assertEqual(rows[name]["documents"], 1)
        self.assertEqual(len(rows), len(self.files))

    def test_list_search_and_cli(self):
        rows = style_profile.list_profiles("보도자료")
        self.assertTrue(rows)
        self.assertTrue(all("보도자료" in r["name"] for r in rows))
        proc = subprocess.run([sys.executable, str(SCRIPTS / "style_profile.py"), "list", "--search", "보도자료"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertTrue(lines[0].startswith("이름"))
        self.assertEqual(len(lines), len(rows) + 1)
        self.assertIn("기본", lines[1])
        proc = subprocess.run([sys.executable, str(SCRIPTS / "style_profile.py"), "list", "--json",
                               "--search", "없는이름xyz"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(proc.stdout), [])


ROLE_STYLE = {"body_pt": 14, "title_pt": 18, "heading_pt": 16,
              "title_space_after": 12, "heading_space_before": 10, "heading_space_after": 4,
              "body_space_before": 3, "body_space_after": 0}
ROLE_DOC = {"version": "1", "title": "역할 서식 시험", "style": ROLE_STYLE,
            "blocks": [{"type": "heading", "level": 1, "text": "추진 배경"},
                       {"type": "list", "items": ["첫째 항목", "둘째 항목"]},
                       {"type": "table", "rows": [["구분", "값"], ["가", "1"]]},
                       {"type": "paragraph", "text": "끝 문단"}]}
FONTS = {"title_font": "HY헤드라인M", "heading_font": "HY견고딕", "body_font": "휴먼명조", "table_font": "맑은 고딕"}


class RoleStyleValidateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(codes(hds.validate_document(dict(ROLE_DOC, style={**ROLE_STYLE, **FONTS}))), [])

    def test_invalid(self):
        for bad in ({"title_font": ""}, {"body_font": " 바탕"}, {"body_font": "가" * 65}, {"table_font": 3},
                    {"body_font": 'a"b'}, {"body_space_before": -1}, {"body_space_before": 101},
                    {"heading_space_after": 2.3}, {"title_space_before": True}, {"list_space_before": 3}):
            with self.subTest(bad=bad):
                self.assertIn("E123", codes(hds.validate_document(dict(ROLE_DOC, style=bad))))

    def test_props(self):
        st = {**ROLE_STYLE, **FONTS, "line_spacing": 170}
        self.assertEqual(json.loads(native.role_char_props(st, "title")),
                         {"fontSize": 1800, "fontName": "HY헤드라인M"})
        self.assertEqual(json.loads(native.role_para_props(st, "heading")),
                         {"lineSpacing": 170, "lineSpacingType": "Percent",
                          "spacingBefore": 10 * native.SPACE_PROP_PER_PT, "spacingAfter": 4 * native.SPACE_PROP_PER_PT})
        self.assertIsNone(native.role_char_props({"line_spacing": 160}, "body"))
        self.assertEqual(native.style_fonts({"style": FONTS}), list(FONTS.values()))


class RoleStyleBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def test_spacing_applied_and_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.hwpx"
            res = native.produce(ROLE_DOC, out, self.rhwp, Path(tmp) / "w")
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(native.check(out, ROLE_DOC, self.rhwp), [])
            gaps = native.para_spacings(out)
            self.assertEqual(gaps[0][1], 1200)            # 제목 아래 12pt
            self.assertEqual(gaps[1], (1000, 400))        # 개요 위 10pt·아래 4pt
            self.assertEqual(gaps[2], (300, 0))           # 목록 항목 = 본문

    def test_check_catches_missing_spacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.hwpx"
            plain = dict(ROLE_DOC, style={"body_pt": 14})
            self.assertTrue(native.produce(plain, out, self.rhwp, Path(tmp) / "w")["ok"])
            problems = native.check(out, ROLE_DOC, self.rhwp)
            self.assertTrue(any("문단 위 간격 미반영" in p for p in problems), problems)

    def test_fonts_applied_or_refused(self):
        doc = dict(ROLE_DOC, style={**ROLE_STYLE, **FONTS})
        with tempfile.TemporaryDirectory() as tmp:
            import build
            out = Path(tmp) / "o.hwpx"
            if not native.supports_font_name(self.rhwp):
                # fontName 을 모르는 rhwp: 조용히 무시하지 않고 E125 로 멈춘다
                rep = build.build(doc, out, Path(tmp) / "b", self.rhwp, False, False, [])
                self.assertFalse(rep["ok"])
                self.assertIn("E125", [f["code"] for f in rep["findings"]])
                self.skipTest("설치된 rhwp 가 fontName 을 지원하지 않음 — E125 확인만 함")
            res = native.produce(doc, out, self.rhwp, Path(tmp) / "w")
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(native.check(out, doc, self.rhwp), [])
            shapes, styles = native.char_shapes(out), native.char_styles(out)
            fonts = [{shapes[c]["font"] for c in ids} for ids in styles if ids]
            self.assertEqual(fonts[0], {"HY헤드라인M"})
            self.assertEqual(fonts[1], {"HY견고딕"})
            self.assertEqual(fonts[2], {"휴먼명조"})
            cells = native.table_cells(out)[0]
            self.assertEqual({shapes[c]["font"] for cell in cells for c in cell["chars"]}, {"맑은 고딕"})

    def test_lock_drops_role_fonts_without_support(self):
        home = os.environ.get("HWP_MASTER_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HWP_MASTER_HOME"] = str(Path(tmp) / "home")
            try:
                prof = style_profile.summarize([feature()] * 2, [])
                prof["recommend"]["style"].update({"title_font": "HY헤드라인M", "body_space_before": 3})
                style_profile.register(prof, "역할시험", force=False)
                old = native.supports_font_name
                native.supports_font_name = lambda rhwp: False
                try:
                    proj = Path(tmp) / "p"
                    proj.mkdir()
                    res = style_profile.lock("역할시험", proj, force=False, rhwp=self.rhwp)
                finally:
                    native.supports_font_name = old
                spec = json.loads((proj / "spec_lock.json").read_text(encoding="utf-8"))
                self.assertNotIn("title_font", spec["style"])
                self.assertEqual(spec["style"]["body_space_before"], 3)
                self.assertTrue(any("fontName" in u for u in res["unsupported"]))
            finally:
                if home is None:
                    os.environ.pop("HWP_MASTER_HOME", None)
                else:
                    os.environ["HWP_MASTER_HOME"] = home


class RoleSummarizeTest(unittest.TestCase):
    def test_role_fonts_and_spacing(self):
        def f(**kw):
            base = feature()
            base["title"] = dict(base["title"], font="HY헤드라인M", before=0.0, after=12.0)
            base.update(top_marker_font="HY견고딕", heading_space=(10.0, 4.0), body_space=(0.0, 0.0))
            base.update(kw)
            return base
        prof = style_profile.summarize([f(), f(), f(), f(top_marker_font="휴먼명조")], [])
        st = prof["recommend"]["style"]
        self.assertEqual(st["title_font"], "HY헤드라인M")
        self.assertEqual(st["heading_font"], "HY견고딕")
        self.assertNotIn("table_font", st)  # 문서 전체 글꼴(font)과 같으면 따로 두지 않는다
        self.assertEqual((st["title_space_before"], st["title_space_after"]), (0.0, 12.0))
        self.assertEqual((st["heading_space_before"], st["heading_space_after"]), (10.0, 4.0))
        self.assertEqual(st["body_space_before"], 0.0)  # 0pt 도 다수결 값으로 남긴다
        self.assertEqual(prof["agreement"]["heading_font"], 0.75)
        self.assertEqual(codes(hds.validate_style(prof["recommend"])), [])


if __name__ == "__main__":
    unittest.main()
