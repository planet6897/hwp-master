"""문서 규약 검사 (spec_lock.convention).

- gongmun: 「행정업무의 운영 및 혁신에 관한 규정 시행규칙」 제2조의 항목 구분.
  상위부터 1. → 가. → 1) → 가) → (1) → (가) → ① → ㉮ 순서, 숫자는 오름차순·한글은 가나다순.
- gaejosik: 같은 조가 허용하는 특수 기호(□, ○, -, ㆍ)로 단계를 나누는 개조식 보고서 관행.
  단계 순서는 □ → ○ → - → ㆍ 이다. 명사형 종결은 관행이므로 경고로만 둔다.

검사 대상은 본문 문단(paragraph)이다. 제목(heading)은 개요 자동 번호이므로 제외하고,
제목을 만나면 항목 번호 상태를 처음부터 다시 센다. 표 셀은 검사하지 않는다.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

CONVENTIONS = ("gongmun", "gaejosik")

HANGUL_ORDER = "가나다라마바사아자차카타파하"
CIRCLED_NUM = "".join(chr(c) for c in range(0x2460, 0x2474))  # ①~⑳
CIRCLED_HANGUL = "".join(chr(c) for c in range(0x326E, 0x327C))  # ㉮~㉻ (가~하)

# (단계, 정규식, 값 → 순번 함수). 앞의 것부터 맞춰 본다: "(1)" 이 "1)" 로 잡히지 않도록 괄호형을 먼저 둔다.
_GONGMUN: List[Tuple[int, "re.Pattern[str]", callable]] = [
    (5, re.compile(r"^\((\d{1,2})\)(\s*)"), int),
    (6, re.compile(rf"^\(([{HANGUL_ORDER}])\)(\s*)"), lambda v: HANGUL_ORDER.index(v) + 1),
    (1, re.compile(r"^(\d{1,2})\.(\s*)(?!\d)"), int),
    (2, re.compile(rf"^([{HANGUL_ORDER}])\.(\s*)"), lambda v: HANGUL_ORDER.index(v) + 1),
    (3, re.compile(r"^(\d{1,2})\)(\s*)"), int),
    (4, re.compile(rf"^([{HANGUL_ORDER}])\)(\s*)"), lambda v: HANGUL_ORDER.index(v) + 1),
    (7, re.compile(rf"^([{CIRCLED_NUM}])(\s*)"), lambda v: CIRCLED_NUM.index(v) + 1),
    (8, re.compile(rf"^([{CIRCLED_HANGUL}])(\s*)"), lambda v: CIRCLED_HANGUL.index(v) + 1),
]
GONGMUN_LABEL = {1: "1.", 2: "가.", 3: "1)", 4: "가)", 5: "(1)", 6: "(가)", 7: "①", 8: "㉮"}

_GAEJOSIK = {"□": 1, "○": 2, "-": 3, "－": 3, "ㆍ": 4, "·": 4}
GAEJOSIK_LABEL = {1: "□", 2: "○", 3: "-", 4: "ㆍ"}

# 서술형 종결: "~니다.", "~한다.", "~이다." 등. 개조식은 "~함", "~임", "~ 추진" 같은 명사형으로 끝낸다.
_SENTENCE_END = re.compile(r"(니다|한다|된다|이다|있다|없다|했다|였다)\s*\.?\s*$")


def _marker_value(level: int, n: int) -> str:
    if level in (1, 3, 5):
        return {1: f"{n}.", 3: f"{n})", 5: f"({n})"}[level]
    if level in (2, 4, 6):
        if n > len(HANGUL_ORDER):
            return "?"
        ch = HANGUL_ORDER[n - 1]
        return {2: f"{ch}.", 4: f"{ch})", 6: f"({ch})"}[level]
    if level == 7:
        return CIRCLED_NUM[n - 1] if n <= len(CIRCLED_NUM) else "?"
    return CIRCLED_HANGUL[n - 1] if n <= len(CIRCLED_HANGUL) else "?"


def gongmun_marker(text: str) -> Optional[Tuple[int, int, str]]:
    """(단계, 순번, 기호 뒤 공백) 또는 None."""
    for level, pattern, ordinal in _GONGMUN:
        m = pattern.match(text)
        if m:
            return level, ordinal(m.group(1)), m.group(2)
    return None


def gongmun_marker_span(text: str) -> int:
    """앞머리 항목 기호와 뒤 공백의 글자 수 (기호가 없으면 0). gongmun_marker 와 같은 규칙."""
    for _level, pattern, _ordinal in _GONGMUN:
        m = pattern.match(text)
        if m:
            return m.end()
    return 0


def gaejosik_marker(text: str) -> Optional[Tuple[int, str]]:
    """(단계, 기호 뒤 공백) 또는 None."""
    if text and text[0] in _GAEJOSIK:
        rest = text[1:]
        if text[0] in "-－" and rest[:1].isdigit():
            return None  # "-5%" 같은 음수·범위는 항목 기호가 아니다
        return _GAEJOSIK[text[0]], rest[: len(rest) - len(rest.lstrip())]
    return None


def lint_convention(blocks, convention: Optional[str], where_prefix: str = "") -> list:
    """규약 위반 Finding 목록. convention 이 없거나 모르는 값이면 검사하지 않는다."""
    from hds import _err, _warn  # 순환 import 를 피해 호출 시점에 가져온다

    if convention not in CONVENTIONS or not isinstance(blocks, list):
        return []
    out = []
    counters = {}  # 단계 → 마지막 순번
    deepest = 0  # 마지막 항목의 단계 (0 = 아직 없음)
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        if b.get("type") == "heading":
            counters, deepest = {}, 0
            continue
        if b.get("type") != "paragraph" or not isinstance(b.get("text"), str):
            continue
        where = f"{where_prefix}blocks[{i}]"
        text = b["text"].lstrip()

        if convention == "gongmun":
            found = gongmun_marker(text)
            if not found:
                continue
            level, n, space = found
            label = GONGMUN_LABEL[level]
            if level > deepest + 1:
                need = GONGMUN_LABEL[deepest + 1]
                out.append(_err("C001", f"항목 단계를 건너뛰었습니다: {label} 앞에 {need} 단계가 필요합니다", where))
            else:
                expected = counters.get(level, 0) + 1
                if n != expected:
                    out.append(_err(
                        "C002",
                        f"항목 순서: {_marker_value(level, n)} 자리에 {_marker_value(level, expected)} 이 와야 합니다",
                        where))
            counters = {k: v for k, v in counters.items() if k < level}
            counters[level] = n
            deepest = level
        else:
            found = gaejosik_marker(text)
            if not found:
                continue
            level, space = found
            label = GAEJOSIK_LABEL[level]
            if level > deepest + 1:
                need = GAEJOSIK_LABEL[deepest + 1]
                out.append(_err("C011", f"개조식 단계를 건너뛰었습니다: {label} 앞에 {need} 단계가 필요합니다", where))
            deepest = level
            if _SENTENCE_END.search(text):
                out.append(_warn("C111", "개조식 항목은 명사형으로 끝냅니다 (예: '~함', '~ 추진')", where))

        if space != " ":
            out.append(_warn(
                "C101",
                "항목 기호 뒤에는 한 칸을 띄웁니다" if not space else "항목 기호 뒤 공백은 한 칸이어야 합니다",
                where))
    return out
