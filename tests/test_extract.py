"""Extract 라우트 테스트: python3 -m unittest tests.test_extract

rhwp 바이너리가 필요하다(`RHWP_BIN` 또는 PATH). 입력은 scaffold 와 누름틀 픽스처로 직접 만든다.
"""
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
import extract  # noqa: E402
import rhwp_locate  # noqa: E402
from form_fixture import make_form  # noqa: E402
from nested_fixture import make_layout, make_nested  # noqa: E402

SPEC = {"version": "1", "title": "추출 시험 문서", "blocks": [
    {"type": "heading", "level": 1, "text": "사업 개요"},
    {"type": "paragraph", "text": "계약일은 2026년 3월 15일이고 총사업비는 1,200,000원이다."},
    {"type": "table", "rows": [["구분", "금액"], ["인건비", "800,000원"], ["장비비", "400,000원"]]},
    {"type": "heading", "level": 1, "text": "추진 일정"},
    {"type": "paragraph", "text": "착수 보고는 2026년 4월 1일에 한다."},
]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


class GridTest(unittest.TestCase):
    def test_merged_cell_text_stays_top_left(self):
        table = {"rows": 2, "cols": 2, "cells": [
            {"row": 0, "col": 0, "rowSpan": 1, "colSpan": 2, "text": "머리"},
            {"row": 1, "col": 0, "rowSpan": 1, "colSpan": 1, "text": "가"},
            {"row": 1, "col": 1, "rowSpan": 1, "colSpan": 1, "text": "나"}]}
        self.assertEqual(extract.table_grid(table), [["머리", ""], ["가", "나"]])
        self.assertTrue(extract.table_area_ok(table))
        table["cells"].pop()
        self.assertFalse(extract.table_area_ok(table))


class AmountTest(unittest.TestCase):
    def test_finds_currency_amounts(self):
        text = ("총사업비는 1,200,000원이고 수출은 580억 달러, 수입은 $1.5 million, 단가는 USD 25.5, "
                "3억 5천만 원, 49억달러 흑자, ¥100 25 EUR, 2,000백만 원.")
        got = [(a["raw"], a["normalized"], a["currency"]) for a in extract.find_amounts(text)]
        self.assertEqual(got, [("1,200,000원", 1200000, "KRW"), ("580억 달러", 58000000000, "USD"),
                               ("$1.5 million", 1500000, "USD"), ("USD 25.5", 25.5, "USD"),
                               ("3억 5천만 원", 350000000, "KRW"), ("49억달러", 4900000000, "USD"),
                               ("¥100", 100, "JPY"), ("25 EUR", 25, "EUR"), ("2,000백만 원", 2000000000, "KRW")])
        for a in extract.find_amounts(text):
            self.assertEqual(text[a["charOffset"]:a["charOffset"] + a["length"]], a["raw"])

    def test_ignores_words_and_unit_captions(self):
        text = "3원칙과 위원 5명, 엔진 2대, 국립국어원, 2024년 5월 11.5% 증가 (단위: 억 달러, %)"
        self.assertEqual(extract.find_amounts(text), [])


class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        for sub in ("sources", "exports", "validation"):
            (self.project / sub).mkdir(parents=True)
        spec = Path(self.tmp.name) / "spec.json"
        spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
        self.doc = self.project / "sources/doc.hwpx"
        subprocess.run([self.rhwp, "scaffold", str(spec), "-o", str(self.doc)], check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_extract(self, *docs, extra=()):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.project), *docs,
                               "--rhwp", self.rhwp, *extra], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900)
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        return proc, report

    def test_supplements_dollar_amounts_in_body_and_cells(self):
        spec = Path(self.tmp.name) / "usd.json"
        spec.write_text(json.dumps({"version": "1", "title": "수출 동향", "blocks": [
            {"type": "paragraph", "text": "총사업비는 1,200,000원이고 5월 수출은 580억 달러로 11.5% 늘었다."},
            {"type": "table", "rows": [["구분", "금액"], ["무역수지", "49억 달러"]]},
        ]}, ensure_ascii=False), encoding="utf-8")
        doc = self.project / "sources/usd.hwpx"
        subprocess.run([self.rhwp, "scaffold", str(spec), "-o", str(doc)], check=True, capture_output=True)
        proc, report = self.run_extract("sources/usd.hwpx", extra=("--only", "data"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.project / "exports/extract/usd/data.json").read_text(encoding="utf-8"))
        amounts = [i for i in data["items"] if i["kind"] == "amount"]
        by_raw = {i["raw"]: i for i in amounts}
        self.assertEqual(sorted(by_raw), ["1,200,000원", "49억 달러", "580억 달러"])
        self.assertEqual(len(amounts), 3, "rhwp 가 잡은 원화 금액을 겹쳐 넣지 않는다")
        self.assertEqual(by_raw["580억 달러"]["normalized"], 58000000000)
        self.assertEqual(by_raw["580억 달러"]["currency"], "USD")
        self.assertEqual(by_raw["580억 달러"]["detector"], "hwp-master")
        self.assertIn("cell", by_raw["49억 달러"], "표 셀 안 금액은 셀 위치를 남긴다")
        self.assertEqual(data["counts"]["amount"], 3)
        rows = read_csv(self.project / "exports/extract/usd/data.csv")
        self.assertIn("currency", rows[0])
        self.assertTrue(any(r[1] == "49억 달러" and r[rows[0].index("cell")] for r in rows[1:]))

    def test_text_md_image_links_resolve(self):
        import re
        import struct
        import zlib

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        png = Path(self.tmp.name) / "dot.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 4) * 4))
                        + chunk(b"IEND", b""))
        subprocess.run([self.rhwp, "edit", "insert-image", str(self.doc), "--image", str(png), "-o", str(self.doc)],
                       check=True, capture_output=True)
        proc, report = self.run_extract("sources/doc.hwpx", extra=("--only", "text"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.project / "exports/extract/doc"
        links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", (out / "text.md").read_text(encoding="utf-8"))
        self.assertTrue(links, "그림이 들어간 문서인데 text.md 에 그림 링크가 없다")
        for link in links:
            self.assertTrue((out / link).is_file(), f"깨진 그림 링크: {link}")

    def test_extracts_text_tables_data_and_keeps_source(self):
        before = sha(self.doc)
        proc, report = self.run_extract("sources/doc.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(report["ok"])
        self.assertEqual(sha(self.doc), before, "원본은 고치지 않는다")
        out = self.project / "exports/extract/doc"
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["sha256"], before)
        self.assertTrue(summary["untrustedContent"])
        self.assertEqual(summary["counts"]["tables"], 1)

        self.assertIn("총사업비", (out / "text.md").read_text(encoding="utf-8"))
        self.assertTrue((out / "text.md").read_text(encoding="utf-8").startswith("<!-- page 1 -->"))
        self.assertEqual(read_csv(out / "tables/T01.csv"),
                         [["구분", "금액"], ["인건비", "800,000원"], ["장비비", "400,000원"]])

        data = json.loads((out / "data.json").read_text(encoding="utf-8"))
        kinds = {i["kind"] for i in data["items"]}
        self.assertIn("date", kinds)
        self.assertIn("amount", kinds)
        self.assertTrue(all("paragraph" in i and "page" in i for i in data["items"]), "값마다 위치를 남긴다")
        rows = read_csv(out / "data.csv")
        self.assertEqual(rows[0][:3], ["kind", "raw", "normalized"])
        self.assertTrue(any("1,200,000" in r[1] for r in rows[1:]))

        structure = json.loads((out / "structure.json").read_text(encoding="utf-8"))
        headings = [n["heading"] for n in structure["structure"]["roots"]]
        self.assertEqual(headings, ["사업 개요", "추진 일정"])

    def test_fields_and_only(self):
        form = make_form(self.rhwp, self.project / "sources/form.hwpx", ["회사명", "대표자"])
        proc, report = self.run_extract(f"sources/{form.name}", extra=("--only", "fields"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.project / "exports/extract/form"
        fields = json.loads((out / "fields.json").read_text(encoding="utf-8"))["fields"]
        self.assertEqual(sorted(f["name"] for f in fields), ["대표자", "회사명"])
        self.assertTrue((out / "fields.csv").is_file())
        self.assertFalse((out / "tables.json").exists(), "--only 로 고른 항목만 꺼낸다")

    def test_document_model_links_values_tables_and_fields(self):
        proc, report = self.run_extract("sources/doc.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse([f for f in report["findings"] if f["code"] == "W305"], "모든 항목이 블록에 이어진다")
        out = self.project / "exports/extract/doc"
        doc = json.loads((out / "document.json").read_text(encoding="utf-8"))
        self.assertTrue(doc["untrustedContent"])
        blocks = {b["id"]: b for b in doc["blocks"]}
        cells = {c["id"]: c for b in doc["blocks"] if b["type"] == "table" for c in b["cells"]}

        headings = [(b["level"], b["text"]) for b in doc["blocks"] if b["type"] == "heading"]
        self.assertEqual(headings, [(1, "사업 개요"), (1, "추진 일정")])
        body = next(b for b in doc["blocks"] if "총사업비" in b.get("text", ""))
        self.assertEqual(body["type"], "paragraph")
        self.assertEqual(body["headingPath"], ["사업 개요"])
        self.assertEqual(body["id"], f"s{body['section']}.p{body['paragraph']}")
        table = next(b for b in doc["blocks"] if b["type"] == "table")
        self.assertRegex(table["id"], r"^s0\.p\d+\.t0$")
        self.assertEqual((table["rows"], table["cols"]), (3, 2))
        self.assertEqual(cells[f"{table['id']}.r1c0"]["text"], "인건비")
        order = [b["id"] for b in doc["blocks"]]
        self.assertLess(order.index(body["id"]), order.index(table["id"]), "읽기 순서를 지킨다")

        tables = json.loads((out / "tables.json").read_text(encoding="utf-8"))["tables"]
        self.assertEqual(tables[0]["blockId"], table["id"])
        items = json.loads((out / "data.json").read_text(encoding="utf-8"))["items"]
        self.assertTrue(items)
        for item in items:
            target = blocks.get(item["blockId"]) or cells.get(item["blockId"])
            self.assertIsNotNone(target, item)
            self.assertIn(item["raw"].replace(" ", ""), target["text"].replace(" ", ""))
        in_cell = next(i for i in items if i["raw"] == "800,000원")
        self.assertEqual(in_cell["blockId"], f"{table['id']}.r1c1", "표 셀 안 값은 셀 ID 로 이어진다")

    def test_document_model_from_hwp5(self):
        hwp = self.project / "sources/doc5.hwp"
        subprocess.run([self.rhwp, "convert", str(self.doc), str(hwp)], check=True, capture_output=True)
        proc, report = self.run_extract("sources/doc5.hwp", extra=("--only", "document,data"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.project / "exports/extract/doc5"
        doc = json.loads((out / "document.json").read_text(encoding="utf-8"))
        self.assertEqual([b["text"] for b in doc["blocks"] if b["type"] == "heading"], ["사업 개요", "추진 일정"])
        items = json.loads((out / "data.json").read_text(encoding="utf-8"))["items"]
        self.assertTrue(items and all(i.get("blockId") for i in items))

    def test_nested_tables_are_kept(self):
        doc_path = make_nested(self.rhwp, self.project / "sources/nest.hwpx")
        proc, report = self.run_extract(f"sources/{doc_path.name}", extra=("--only", "document,tables,data"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse([f for f in report["findings"] if f["code"] == "W305"], report["findings"])
        out = self.project / "exports/extract/nest"
        doc = json.loads((out / "document.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["counts"]["nestedTable"], 1)
        outer = next(b for b in doc["blocks"] if b["type"] == "table")
        holder = next(c for c in outer["cells"] if c["hasNestedTable"])
        inner = holder["tables"][0]
        self.assertEqual(inner["id"], f"{holder['id']}.t0")
        self.assertEqual((inner["rows"], inner["cols"], inner["depth"]), (3, 2, 1))
        cells = {c["id"]: c["text"] for c in inner["cells"]}
        self.assertEqual(cells[f"{inner['id']}.r1c1"], "580억 달러")
        self.assertIn("세부 내역", holder["text"])
        self.assertIn("장비 | 580억 달러", holder["text"], "바깥 칸 글에 안쪽 표가 펼쳐진다")

        tables = json.loads((out / "tables.json").read_text(encoding="utf-8"))["tables"]
        self.assertEqual([t["label"] for t in tables], ["T01", "T01-1"])
        self.assertEqual(tables[1]["parentCellId"], holder["id"])
        self.assertEqual(read_csv(out / "tables/T01-1.csv"),
                         [["항목", "금액"], ["장비", "580억 달러"], ["인건비", "3억 5천만 원"]])
        self.assertEqual(read_csv(out / "tables/T01.csv")[1], ["예산", "세부 내역\n[표 T01-1]"])

        items = {i["raw"]: i for i in json.loads((out / "data.json").read_text(encoding="utf-8"))["items"]
                 if i["kind"] == "amount"}
        self.assertEqual(items["580억 달러"]["blockId"], f"{inner['id']}.r1c1", "가장 안쪽 칸으로 잇는다")
        self.assertEqual(items["3억 5천만 원"]["blockId"], f"{inner['id']}.r2c1")

    def test_layout_tables_are_unwrapped(self):
        doc_path = make_layout(self.rhwp, self.project / "sources/layout.hwpx")
        proc, report = self.run_extract(f"sources/{doc_path.name}", extra=("--only", "document,tables,data"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse([f for f in report["findings"] if f["code"] == "W305"], report["findings"])
        out = self.project / "exports/extract/layout"
        doc = json.loads((out / "document.json").read_text(encoding="utf-8"))
        tables = [b for b in doc["blocks"] if b["type"] == "table" and not b.get("fromLayoutTable")]
        self.assertEqual([(t["role"], t["roleReasons"]) for t in tables],
                         [("layout", ["single-cell"]), ("layout", ["text-column"]), ("data", []),
                          ("layout", ["borderless"])])
        self.assertEqual(doc["counts"]["layoutTable"], 3)
        unwrapped = [b for b in doc["blocks"] if b.get("fromLayoutTable")]
        texts = [b["text"] for b in unwrapped]
        self.assertIn("2026년 돌봄 확대 보도자료", texts)
        self.assertIn("사업 개요", texts)
        self.assertTrue(all(b["type"] == "paragraph" and b["id"].startswith(b["cellId"] + ".p") for b in unwrapped))
        order = [b["id"] for b in doc["blocks"]]
        first = tables[0]["id"]
        self.assertEqual(order[order.index(first) + 1], f"{first}.r0c0.p0", "풀린 문단은 표 바로 뒤에 온다")

        listed = json.loads((out / "tables.json").read_text(encoding="utf-8"))["tables"]
        self.assertEqual([t["role"] for t in listed], ["layout", "layout", "data", "layout"])
        self.assertEqual(sorted(p.name for p in (out / "tables").glob("*.csv")), ["T03.csv", "T03.records.csv"],
                         "CSV(와 숫자 표 레코드 CSV)는 데이터 표만")
        items = json.loads((out / "data.json").read_text(encoding="utf-8"))["items"]
        won = next(i for i in items if i["raw"] == "100원")
        self.assertEqual(won["blockId"], f"{tables[2]['id']}.r1c1")

    def test_fields_link_to_cells(self):
        form = make_form(self.rhwp, self.project / "sources/form.hwpx", ["회사명", "대표자"])
        proc, report = self.run_extract(f"sources/{form.name}", extra=("--only", "document,fields"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.project / "exports/extract/form"
        doc = json.loads((out / "document.json").read_text(encoding="utf-8"))
        ids = {b["id"] for b in doc["blocks"]} | {c["id"] for b in doc["blocks"] if b["type"] == "table"
                                                   for c in b["cells"]}
        fields = json.loads((out / "fields.json").read_text(encoding="utf-8"))["fields"]
        self.assertTrue(all(f.get("blockId") in ids for f in fields), fields)
        self.assertIn("blockId", read_csv(out / "fields.csv")[0])

    def test_rejects_outside_sources(self):
        outside = self.project / "exports/copy.hwpx"
        outside.write_bytes(self.doc.read_bytes())
        proc, report = self.run_extract("exports/copy.hwpx")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual([f["code"] for f in report["findings"]], ["X101"])

    def test_unknown_part(self):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.project), "sources/doc.hwpx",
                               "--only", "pictures", "--rhwp", self.rhwp], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()


class OutlineTest(unittest.TestCase):
    @staticmethod
    def blocks(texts):
        return [{"id": f"s0.p{i}", "type": "paragraph", "text": t, "page": 0} for i, t in enumerate(texts)]

    def test_gaejosik_levels_and_notes(self):
        blocks = self.blocks(["□ 추진 배경", " ○ 현황 분석", "   - 세부 내용", "     * 근거 자료", "  ○ 문제점",
                              "□ 추진 계획", "  - 바로 아래 하위 기호", "본문 이어짐"])
        extract.docmodel.assign_outline(blocks)
        got = [(b["outline"]["kind"], b["outline"]["level"], b["outline"]["parentId"]) if "outline" in b else None
               for b in blocks]
        self.assertEqual(got, [("□", 1, None), ("○", 2, "s0.p0"), ("-", 3, "s0.p1"), ("note", 4, "s0.p2"),
                               ("○", 2, "s0.p0"), ("□", 1, None), ("-", 2, "s0.p5"), None])
        self.assertEqual(blocks[1]["outline"]["body"], "현황 분석")

    def test_gongmun_numbering_and_rank(self):
        blocks = self.blocks(["1. 목적", "가. 배경", "1) 세부", "가) 더 세부", "(1) 항목", "나. 둘째", "2. 방법"])
        extract.docmodel.assign_outline(blocks)
        self.assertEqual([b["outline"]["level"] for b in blocks], [1, 2, 3, 4, 5, 2, 1])
        self.assertEqual(blocks[5]["outline"]["parentId"], "s0.p0")

    def test_dates_are_not_numbered_items(self):
        blocks = self.blocks(["4. 23.~6. 15. 접수 기간", "12. 31. 마감", "3. 10명 선발"])
        extract.docmodel.assign_outline(blocks)
        self.assertEqual(["outline" in b for b in blocks], [False, False, True])

    def test_lower_marker_first_then_higher(self):
        blocks = self.blocks(["○ 먼저 나온 하위", "□ 나중 나온 상위", "○ 그 아래"])
        extract.docmodel.assign_outline(blocks)
        self.assertEqual([b["outline"]["level"] for b in blocks], [1, 1, 2])

    def test_no_false_markers(self):
        blocks = self.blocks(["2024. 6. 17.(월) 배포", "-5% 감소했다", "1.5배 늘었다", "ㅇㅇ기관 보고", "□", "가나다 순"])
        extract.docmodel.assign_outline(blocks)
        self.assertFalse([b for b in blocks if "outline" in b], [b.get("outline") for b in blocks])

    def test_heading_resets_and_tree(self):
        blocks = self.blocks(["□ 첫 장 항목", "○ 하위"])
        blocks.insert(0, {"id": "s0.h", "type": "heading", "level": 1, "text": "1장", "page": 0,
                          "parentHeadingId": None})
        extract.docmodel.assign_outline(blocks)
        self.assertEqual(blocks[1]["outline"]["parentId"], "s0.h")
        tree = extract.docmodel.outline_tree(blocks)
        self.assertEqual(tree[0]["text"], "1장")
        self.assertEqual(tree[0]["children"][0]["children"][0]["text"], "하위")


class OutlineExtractTest(unittest.TestCase):
    def test_outline_json_from_document(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "exports", "validation"):
                (project / sub).mkdir(parents=True)
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"version": "1", "title": "개조식", "blocks": [
                {"type": "heading", "level": 1, "text": "추진 배경"},
                {"type": "paragraph", "text": "□ 현황"},
                {"type": "paragraph", "text": " ○ 수출 580억 달러로 증가"},
                {"type": "paragraph", "text": "   * 전년 대비 11.5%"},
                {"type": "paragraph", "text": "□ 과제"},
            ]}, ensure_ascii=False), encoding="utf-8")
            doc = project / "sources/g.hwpx"
            subprocess.run([rhwp, "scaffold", str(spec), "-o", str(doc)], check=True, capture_output=True)
            proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(project), "sources/g.hwpx",
                                   "--rhwp", rhwp, "--only", "document"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            tree = json.loads((project / "exports/extract/g/outline.json").read_text(encoding="utf-8"))["outline"]
            self.assertEqual(tree[0]["text"], "추진 배경")
            items = tree[0]["children"]
            self.assertEqual([(n["marker"], n["text"]) for n in items], [("□", "현황"), ("□", "과제")])
            self.assertEqual(items[0]["children"][0]["text"], "수출 580억 달러로 증가")
            self.assertEqual(items[0]["children"][0]["children"][0]["kind"], "note")
