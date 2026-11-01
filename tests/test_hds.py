"""hds 검사 규칙 단위 테스트: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/hwp-master/scripts"))
import hds  # noqa: E402


def codes(doc):
    return sorted(f.code for f in hds.validate_document(doc))


class LintTest(unittest.TestCase):
    def doc(self, *blocks):
        return {"version": "1", "blocks": list(blocks)}

    def test_clean(self):
        self.assertEqual(codes(self.doc({"type": "heading", "level": 1, "text": "추진 배경"},
                                        {"type": "paragraph", "text": "□ 내용"})), [])

    def test_manual_number_in_heading(self):
        for text in ("1. 추진 배경", "2.1. 현황", "가. 개요", "Ⅲ. 일정", "(1) 세부", "① 항목"):
            with self.subTest(text=text):
                self.assertEqual(codes(self.doc({"type": "heading", "level": 1, "text": text})), ["E101"])

    def test_number_like_but_not_numbering(self):
        for text in ("2027년 추진 계획", "3대 과제", "HWPX 전환"):
            with self.subTest(text=text):
                self.assertEqual(codes(self.doc({"type": "heading", "level": 1, "text": text})), [])

    def test_unknown_block_and_field(self):
        self.assertEqual(codes(self.doc({"type": "image", "path": "a.png"})), ["E002"])
        self.assertEqual(codes(self.doc({"type": "paragraph", "text": "a", "bold": True})), ["E003"])
        self.assertEqual(codes({"version": "1", "blocks": [], "theme": "x"}), ["E003"])

    def test_level_range(self):
        self.assertEqual(codes(self.doc({"type": "heading", "level": 8, "text": "a"})), ["E102"])

    def test_table_overflow_warning(self):
        long = "세부 과업 1: 요구사항 정의, 설계, 구현 및 시험"
        rows = [["단계", "내용", "기간", "산출물"], ["1단계", long, "1~2월", "보고서"]]
        self.assertEqual(codes(self.doc({"type": "table", "rows": rows})), ["W201"])
        self.assertEqual(codes(self.doc({"type": "table", "rows": [["a", "b"], ["c"]]})), ["W202"])
        self.assertEqual(codes(self.doc({"type": "table", "rows": [["a"]], "cell_align": "middle"})), ["E005"])


if __name__ == "__main__":
    unittest.main()
