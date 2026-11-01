"""Convert-in 테스트: python3 -m unittest tests.test_convert

MD·DOCX 해석과 정규화는 rhwp 없이 확인하고, 변환 결과의 빌드는 rhwp 로 확인한다.
"""
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
import convert  # noqa: E402
import hds  # noqa: E402
import rhwp_locate  # noqa: E402

MD = """# 2027년 문서 전자화 추진 계획

## 1. 추진 배경

종이 문서 중심으로 **검색**이 어렵다.

- 연간 출력량 약 120만 쪽
  - 보관 공간 3개 층
- 취합에 평균 2일

## 2. 추진 계획

| 단계 | 기간 | 예산 |
|:---:|:---|---:|
| 1단계 | 1~3월 | 12,000 |

1. 양식 통폐합
2. 전자결재 의무화

![조직도](org.png)
"""


def blocks_of(md, convention=None):
    losses = convert.Losses()
    report = {"strippedHeadingNumbers": []}
    blocks, title = convert.ir_to_blocks(convert.md_to_ir(md, losses), convention, report)
    return blocks, title, losses, report


class MarkdownTest(unittest.TestCase):
    def test_title_headings_and_stripped_numbers(self):
        blocks, title, _, report = blocks_of(MD)
        self.assertEqual(title, "2027년 문서 전자화 추진 계획")
        self.assertEqual([(b["level"], b["text"]) for b in blocks if b["type"] == "heading"],
                         [(1, "추진 배경"), (1, "추진 계획")])
        self.assertEqual(len(report["strippedHeadingNumbers"]), 2)

    def test_lists_follow_the_convention(self):
        texts = [b["text"] for b in blocks_of(MD, "gaejosik")[0] if b["type"] == "paragraph"]
        self.assertIn("□ 연간 출력량 약 120만 쪽", texts)
        self.assertIn("  ○ 보관 공간 3개 층", texts)
        texts = [b["text"] for b in blocks_of(MD, "gongmun")[0] if b["type"] == "paragraph"]
        self.assertIn("1. 연간 출력량 약 120만 쪽", texts)
        self.assertIn("  가. 보관 공간 3개 층", texts)
        self.assertIn("2. 취합에 평균 2일", texts)

    def test_converted_items_pass_the_convention_check(self):
        for conv in ("gaejosik", "gongmun"):
            with self.subTest(convention=conv):
                blocks = blocks_of(MD, conv)[0]
                errors = [f.code for f in convert.conventions.lint_convention(blocks, conv)
                          if f.severity == "error"]
                self.assertEqual(errors, [])

    def test_table_alignment_and_losses(self):
        blocks, _, losses, _ = blocks_of(MD)
        table = next(b for b in blocks if b["type"] == "table")
        self.assertEqual(table["cell_align"], ["center", "left", "right"])
        kinds = {i["kind"] for i in losses.items}
        self.assertEqual(kinds, {"char_format", "image"})
        self.assertNotIn("조직도", json.dumps(blocks, ensure_ascii=False), "그림 대체 문구를 본문으로 만들지 않는다")
        self.assertEqual(hds.validate_document({"version": "1", "blocks": blocks}), [])

    def test_line_mode_keeps_each_line(self):
        ir = convert.md_to_ir("첫 줄\n둘째 줄\n1. 1. 추진 배경\n", convert.Losses(), line_mode=True)
        self.assertEqual(ir, [("para", "첫 줄"), ("para", "둘째 줄"), ("para", "1. 1. 추진 배경")])


DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>회의 결과 보고</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>1. 회의 개요</w:t></w:r></w:p>
<w:p><w:r><w:t>일시: 2027. 3. 2.</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>참석: 6명</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>외부 2명</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>세부 안건</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>안건</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>결과</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>예산</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>원안 의결</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:sectPr/></w:body></w:document>"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
</w:styles>"""

NUMBERING_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>
<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>"""


class DocxTest(unittest.TestCase):
    def test_docx_styles_lists_and_tables(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "회의.docx"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", DOC_XML)
                z.writestr("word/styles.xml", STYLES_XML)
                z.writestr("word/numbering.xml", NUMBERING_XML)
            losses = convert.Losses()
            ir, title = convert.docx_to_ir(path, losses)
            report = {"strippedHeadingNumbers": []}
            blocks, _ = convert.ir_to_blocks(ir, "gaejosik", report, heading_as_title=title is None)
        self.assertEqual(title, "회의 결과 보고")
        self.assertEqual([(b["level"], b["text"]) for b in blocks if b["type"] == "heading"],
                         [(1, "회의 개요"), (2, "세부 안건")])
        texts = [b["text"] for b in blocks if b["type"] == "paragraph"]
        self.assertEqual(texts, ["일시: 2027. 3. 2.", "□ 참석: 6명", "  ○ 외부 2명"])
        self.assertEqual(next(b for b in blocks if b["type"] == "table")["rows"],
                         [["안건", "결과"], ["예산", "원안 의결"]])
        self.assertIn("char_format", {i["kind"] for i in losses.items})


class ConvertBuildTest(unittest.TestCase):
    def test_markdown_project_converts_and_builds(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as t:
            run = lambda *a: subprocess.run([sys.executable, *map(str, a)], capture_output=True, text=True,
                                            timeout=900)
            md = Path(t) / "plan.md"
            md.write_text(MD, encoding="utf-8")
            proc = run(SCRIPTS / "project_manager.py", "init", "변환", "--root", Path(t) / "projects")
            project = Path(proc.stdout.strip())
            run(SCRIPTS / "project_manager.py", "import-sources", project, md, "--rhwp", rhwp)
            proc = run(SCRIPTS / "convert.py", project, "sources/plan.md", "--convention", "gaejosik",
                       "--max-pages", "2", "--rhwp", rhwp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outline = json.loads((project / "outline.json").read_text(encoding="utf-8"))
            self.assertEqual([u["title"] for u in outline["units"]], ["추진 배경", "추진 계획"])

            proc = run(SCRIPTS / "convert.py", project, "sources/plan.md")
            self.assertEqual(proc.returncode, 2, "기존 단위는 --force 없이 덮어쓰지 않는다")

            proc = run(SCRIPTS / "build.py", project, "--no-preview", "--rhwp", rhwp)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads((project / "validation/report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["pageCount"], 1)


if __name__ == "__main__":
    unittest.main()
