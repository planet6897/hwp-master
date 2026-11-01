"""문서 규약 검사 단위 테스트: python3 -m unittest tests.test_conventions"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/hwp-master/scripts"))
import conventions  # noqa: E402
import hds  # noqa: E402


def p(text):
    return {"type": "paragraph", "text": text}


def h(text):
    return {"type": "heading", "level": 1, "text": text}


def codes(convention, *blocks):
    return [f.code for f in conventions.lint_convention(list(blocks), convention)]


class GongmunTest(unittest.TestCase):
    def test_full_ladder_is_clean(self):
        self.assertEqual(codes("gongmun",
                               p("1. 개요"), p("가. 목적"), p("1) 배경"), p("가) 현황"), p("(1) 세부"),
                               p("(가) 항목"), p("① 첫째"), p("㉮ 갑"), p("㉯ 을"), p("② 둘째"),
                               p("나. 범위"), p("2. 추진 계획")), [])

    def test_skipping_a_level_is_an_error(self):
        self.assertEqual(codes("gongmun", p("1. 개요"), p("1) 배경")), ["C001"])
        self.assertEqual(codes("gongmun", p("가. 목적")), ["C001"], "첫 항목은 1. 단계여야 한다")

    def test_out_of_order_items_are_errors(self):
        self.assertEqual(codes("gongmun", p("1. 개요"), p("3. 계획")), ["C002"])
        self.assertEqual(codes("gongmun", p("1. 개요"), p("나. 목적")), ["C002"])
        self.assertEqual(codes("gongmun", p("1. 개요"), p("가. 목적"), p("다. 범위")), ["C002"])

    def test_numbering_restarts_under_each_heading_and_after_a_parent(self):
        self.assertEqual(codes("gongmun", h("추진 배경"), p("1. 가"), h("추진 계획"), p("1. 나")), [])
        self.assertEqual(codes("gongmun", p("1. 갑"), p("가. 하위"), p("2. 을"), p("가. 하위")), [])

    def test_spacing_after_marker_is_a_warning(self):
        self.assertEqual(codes("gongmun", p("1.개요")), ["C101"])
        self.assertEqual(codes("gongmun", p("1.  개요")), ["C101"])

    def test_text_that_only_looks_numbered_is_not_an_item(self):
        self.assertEqual(codes("gongmun", p("2027. 3. 1.부터 시행한다."), p("3대 과제를 정한다.")), [])

    def test_headings_and_tables_are_not_checked(self):
        self.assertEqual(codes("gongmun", h("3. 계획"), {"type": "table", "rows": [["2. 항목"]]}), [])


class GaejosikTest(unittest.TestCase):
    def test_ladder_is_clean(self):
        self.assertEqual(codes("gaejosik", p("□ 추진 배경"), p("○ 현황 분석"), p("- 세부 내용"),
                               p("ㆍ 세목"), p("○ 다음 항목")), [])

    def test_skipping_a_level_is_an_error(self):
        self.assertEqual(codes("gaejosik", p("○ 현황")), ["C011"])
        self.assertEqual(codes("gaejosik", p("□ 추진"), p("- 세부")), ["C011"])

    def test_sentence_ending_is_a_warning(self):
        self.assertEqual(codes("gaejosik", p("□ 사업을 추진합니다.")), ["C111"])
        self.assertEqual(codes("gaejosik", p("□ 사업 추진")), [])

    def test_negative_number_is_not_a_marker(self):
        self.assertEqual(codes("gaejosik", p("-5% 감소가 예상된다")), [])

    def test_spacing_after_marker_is_a_warning(self):
        self.assertEqual(codes("gaejosik", p("□추진 배경")), ["C101"])


class ProjectWiringTest(unittest.TestCase):
    def project(self, tmp, lock, blocks):
        root = Path(tmp)
        (root / "units").mkdir()
        (root / "spec_lock.json").write_text(json.dumps(lock, ensure_ascii=False), encoding="utf-8")
        (root / "outline.json").write_text(json.dumps({"units": [{"id": "U01"}]}), encoding="utf-8")
        (root / "units/U01.json").write_text(
            json.dumps({"unit": "U01", "blocks": blocks}, ensure_ascii=False), encoding="utf-8")
        return root

    def test_spec_lock_convention_is_applied_to_units(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.project(t, {"convention": "gongmun"}, [h("개요"), p("1. 가"), p("3. 나")])
            found = [(f.code, f.where) for f in hds.lint_project(root)]
            self.assertIn(("C002", "units/U01.json:blocks[2]"), found)

    def test_no_convention_means_no_convention_check(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.project(t, {}, [h("개요"), p("1. 가"), p("3. 나")])
            self.assertEqual([f.code for f in hds.lint_project(root)], [])

    def test_unknown_convention_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.project(t, {"convention": "free"}, [h("개요")])
            self.assertIn("E009", [f.code for f in hds.lint_project(root)])


if __name__ == "__main__":
    unittest.main()
