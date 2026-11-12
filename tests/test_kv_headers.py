"""#55 실제 문서 평가에서 남은 결함의 회귀 시험 — 양식 머리 칸·명단 표 kv 오탐, 증감률 레코드 change 값. rhwp 없이 칸으로 본다."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "hwp-master" / "scripts"))

import kv  # noqa: E402
import tablesem  # noqa: E402
import values  # noqa: E402


def cell(r, c, text, rs=1, cs=1):
    return {"id": f"t.r{r}c{c}", "row": r, "col": c, "rowSpan": rs, "colSpan": cs, "header": False, "text": text}


def pairs(*cells, fields=None):
    table = {"id": "t", "type": "table", "cells": list(cells)}
    return {(i["keyNorm"], i["value"]) for i in kv.extract_kv([table], fields=fields) if i["source"] == "table"}


class HeaderGridTest(unittest.TestCase):
    def test_column_headers_over_value_slots_are_not_pairs(self):
        # 진료비 영수증: 머리 행 아래가 빈 값 자리·값이다 → 머리 칸끼리 짝짓지 않는다
        got = pairs(cell(0, 0, "진료과목"), cell(0, 1, "질병군(DRG)번호"), cell(0, 2, "병실"),
                    cell(1, 0, ""), cell(1, 1, ""), cell(1, 2, ""),
                    cell(2, 0, "진료기간", cs=5), cell(2, 5, "야간(공휴일)진료", cs=3),
                    cell(3, 0, "진료 시작일부터 진료 종료일까지", cs=5), cell(3, 5, "야간 공휴일", cs=3),
                    fields=[{"name": "night+yn", "value": "야간 공휴일", "blockId": "t.r3c5"}])
        self.assertEqual(got, set(), got)

    def test_header_over_a_filled_field_is_a_header(self):
        got = pairs(cell(0, 0, "환자성명"), cell(0, 1, "진료구분"), cell(1, 0, "가나다"), cell(1, 1, "야간"),
                    fields=[{"name": "night", "value": "야간", "blockId": "t.r1c1"}])
        self.assertNotIn(("환자성명", "진료구분"), got)

    def test_group_header_and_sub_headers(self):
        # 높이가 다른 칸: 묶음 머리(입원료, 세 줄)와 갈래 머리(1인실) · 줄을 나눈 머리 칸("전액\n본인부담")
        got = pairs(cell(0, 0, "입원료", rs=3), cell(0, 1, "1인실"), cell(1, 1, "2·3인실"), cell(2, 1, "4인실 이상"),
                    cell(3, 0, "일부본인부담"), cell(3, 1, "전액\n본인부담", rs=2), cell(4, 0, "본인부담금"))
        self.assertEqual(got, set(), got)

    def test_numbered_form_items_are_labels(self):
        got = pairs(cell(0, 0, "② 공단부담금 합계"), cell(0, 1, "③ 전액 본인부담 합계"))
        self.assertEqual(got, set())

    def test_ordinary_forms_still_pair(self):
        # 아래 행도 라벨|값 인 양식, 아래 값이 빈 양식, 너비가 다른 아래 칸 — 그대로 짝짓는다
        got = pairs(cell(0, 0, "성명"), cell(0, 1, "홍길동"), cell(0, 2, "상호"), cell(0, 3, "대한병원"),
                    cell(1, 0, "직위"), cell(1, 1, ""), cell(1, 2, "사업장 소재지", cs=2))
        self.assertEqual(got, {("성명", "홍길동"), ("상호", "대한병원")})
        got = pairs(cell(0, 0, "사업자등록번호"), cell(0, 1, "000-00-00000"), cell(1, 0, ""), cell(1, 1, ""))
        self.assertEqual(got, {("사업자등록번호", "000-00-00000")}, "값이 라벨 꼴이 아니면 머리 칸 규칙을 보지 않는다")


    def test_header_row_does_not_shift_onto_the_next_header(self):
        months = [cell(0, k, f"{k + 6}월") for k in range(5)] + [cell(1, k, "") for k in range(5)]
        self.assertEqual(pairs(*months), set(), "6월 | 7월 을 버린 뒤 7월 → 8월 · 9월 → 10월 로 밀리지 않는다")

    def test_one_label_over_two_value_rows(self):
        # 보도자료 머리: "담당 부서" 하나가 두 줄 부서를 덮는다(아래 칸은 비어 있고, 둘째 부서는 누름틀)
        got = pairs(cell(0, 0, "담당 부서"), cell(0, 1, "낙동강사업처"), cell(1, 0, ""), cell(1, 1, "건설관리부"),
                    fields=[{"name": "담당부서", "value": "건설관리부", "blockId": "t.r1c1"}])
        self.assertEqual(got, {("담당부서", "낙동강사업처")})

    def test_symbol_and_unit_only_values_are_blank(self):
        got = pairs(cell(0, 0, "(A)"), cell(0, 1, "－"), cell(1, 0, "구매지원금"), cell(1, 1, "원"),
                    cell(2, 0, "진행"), cell(2, 1, "▶"), cell(3, 0, "비음"), cell(3, 1, "ㅁ"))
        self.assertEqual(got, {("비음", "ㅁ")}, "자모 값은 글이다")


class RosterTest(unittest.TestCase):
    def test_roster_rows_keep_only_in_cell_pairs(self):
        got = pairs(cell(0, 0, "담당", rs=2), cell(0, 1, "사양 및 과업"), cell(0, 2, "aaa"), cell(0, 3, "권x범"),
                    cell(0, 4, "TEL: 010-1234-4567"),
                    cell(1, 1, "입찰 및 계약"), cell(1, 2, "bbb"), cell(1, 3, "임x연"), cell(1, 4, "TEL: 010-0000-1121"))
        self.assertEqual(got, {("TEL", "010-1234-4567"), ("TEL", "010-0000-1121")})

    def test_repeated_titles_keep_other_pairs_in_the_row(self):
        # 자료문의 표: 직위(부 장·차 장)가 열마다 되풀이된다 → 직위|이름 은 레코드, 같은 행의 "배포부서 | 홍보부" 는 쌍
        got = pairs(cell(0, 0, "자료문의", rs=2), cell(0, 1, "약제관리실", rs=2), cell(0, 2, "부 장"), cell(0, 3, "박 영 미"),
                    cell(0, 4, "02-2182-8525"), cell(1, 2, "차 장"), cell(1, 3, "전 미 정"), cell(1, 4, "02-2182-8526"),
                    cell(2, 0, "배포부서", rs=2), cell(2, 1, "고객홍보실 홍보부", rs=2), cell(2, 2, "부 장"),
                    cell(2, 3, "배 영 덕"), cell(2, 4, "033-739-1432"), cell(3, 2, "차 장"), cell(3, 3, "이 성 규"),
                    cell(3, 4, "02-705-6922"))
        self.assertEqual(got, {("자료문의", "약제관리실"), ("배포부서", "고객홍보실 홍보부")}, got)

    def test_interview_table_and_sentences(self):
        got = pairs(cell(0, 0, "기자"), cell(0, 1, "세 분이 어떻게 함께하게 되었나요?"),
                    cell(1, 0, "학생"), cell(1, 1, "같은 반 친구라서요"),
                    cell(2, 0, "기자"), cell(2, 1, "느낀 점을 말씀해 주세요."))
        self.assertFalse(any(k == "기자" for k, _ in got), got)
        self.assertEqual(pairs(cell(0, 0, "원 외 처 방 전"), cell(0, 1, "외부약국에서 약을 구입하시기 바랍니다.")), set())

    def test_placeholders_and_blank_templates(self):
        got = pairs(cell(0, 0, "수납자: 수납자명"), cell(0, 1, "수납일시 : 수납 일시"), cell(0, 2, "위 금액을 정히 영수함."),
                    cell(1, 0, "③금지금 기준가격"), cell(1, 1, "원/g ( 년 월 일 기준)"))
        self.assertEqual(got, set(), got)
        self.assertEqual(kv.clean_value("2024년 3월 1일 기준"), "2024년 3월 1일 기준")


class RecordChangeTest(unittest.TestCase):
    def test_unsigned_parenthesized_rates_from_records(self):
        table = {"id": "t", "rows": 3, "cols": 3, "cells": [
            cell(0, 0, "구분"), cell(0, 1, "1분기"), cell(0, 2, "2분기"),
            cell(1, 0, "총투자"), cell(1, 1, "282.3"), cell(1, 2, "198.5"),
            cell(2, 0, "증감률"), cell(2, 1, "(147.8)"), cell(2, 2, "(△51.2)")]}
        block = {"id": "t", "type": "table", "page": 0, "section": 0, "paragraph": 3}
        sem = {"t": tablesem.analyze(table)}
        cells = {c["id"]: c for c in table["cells"]}
        existing = [{"kind": "change", "normalized": -51.2, "blockId": "t.r2c2"}]  # 글 패턴(△)이 이미 잡은 칸
        got = values.record_changes(sem, {"t": (block, cells)}, existing)
        self.assertEqual([(i["blockId"], i["normalized"], i["raw"]) for i in got], [("t.r2c1", 147.8, "(147.8)")])
        self.assertEqual((got[0]["section"], got[0]["paragraph"], got[0]["detector"]), (0, 3, "hwp-master"))

    def test_amount_rows_and_rate_columns(self):
        table = {"id": "t", "rows": 3, "cols": 3, "cells": [
            cell(0, 0, "구분"), cell(0, 1, "2025년"), cell(0, 2, "증감률"),
            cell(1, 0, "매출"), cell(1, 1, "1,200"), cell(1, 2, "2.0"),
            cell(2, 0, "수 출 (전년동기대비)"), cell(2, 1, "52,054"), cell(2, 2, "3.1")]}
        block = {"id": "t", "type": "table", "page": 0, "section": 0, "paragraph": 0}
        got = values.record_changes({"t": tablesem.analyze(table)}, {"t": (block, {c["id"]: c for c in table["cells"]})}, [])
        self.assertEqual(sorted((i["blockId"], i["normalized"]) for i in got), [("t.r1c2", 2.0), ("t.r2c2", 3.1)],
                         "증감률 열만 change — 행 이름의 '대비' 만으로 주 값을 증감률로 보지 않는다")
        diff = {"id": "t", "rows": 2, "cols": 3, "cells": [
            cell(0, 0, "구분"), cell(0, 1, "2025"), cell(0, 2, "증 감 (B-A)"),
            cell(1, 0, "총지출"), cell(1, 1, "656,600"), cell(1, 2, "23,309")]}
        got = values.record_changes({"t": tablesem.analyze(diff)}, {"t": (block, {c["id"]: c for c in diff["cells"]})}, [])
        self.assertEqual(got, [], "'증감'·'증감액' 은 차이 금액이지 증감률이 아니다")


if __name__ == "__main__":
    unittest.main()
