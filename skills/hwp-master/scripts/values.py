#!/usr/bin/env python3
"""의미 값 확장: rhwp extract-data 가 잡지 않는 기간·증감률·연락처·조문 참조를 document.json 글에서 찾는다.

  period     "2026. 3. 1.~12. 31."      → {"start": "2026-03-01", "end": "2026-12-31"}
             "2026년 3월~12월", "’22~’26년" → {"start": "2026-03", "end": "2026-12"}, {"start": "2022", "end": "2026"}
  change     "△6.7%", "6.7% 감소"        → -6.7 (unit %)   ("증가·상승"이면 +)
             숫자 표의 증감·증가율·감소율 행·열 값("(147.8)")도 표 레코드에서 보탠다(from=tableRecord)
  contact    "042-481-7760", "1588-0000", "a@b.go.kr" → {"type": "phone"|"email", "value": ...}
  reference  "「관세법」 제222조제1항제7호", "같은 법 시행령 제231조" → {"law", "article", "paragraph", "item"}

모든 값에 blockId(문단·셀 ID)와 그 글 안의 charOffset·length, detector=hwp-master 를 붙인다.
끝 날짜에서 생략한 연·월은 시작 날짜를 따른다. 연도를 알 수 없는 기간은 넣지 않는다.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Tuple

_Y4 = r"(?:19|20)\d{2}"
_YQ = r"[’‘'`]\s?\d{2}"  # ’24
_WD = r"(?:\s*\([월화수목금토일]\))?"
# 날짜: 2026. 3. 1. / 2026년 3월 1일 / ’24.5.1 / 3. 1. / 3월 1일 / 12. 31.
def _date_pattern(p: str) -> str:
    return (r"(?:(?P<" + p + r"y>" + _Y4 + "|" + _YQ + r")\s*(?:\.|년)\s*)?"
            r"(?P<" + p + r"m>\d{1,2})\s*(?:\.|월)\s*(?:(?P<" + p + r"d>\d{1,2})\s*(?:\.|일)?)?" + _WD)


_RANGE_SEP = r"\s*[~∼～〜]\s*"
PERIOD = re.compile(_date_pattern("a") + _RANGE_SEP + _date_pattern("b"))
YEAR_RANGE = re.compile(rf"(?P<a>{_Y4}|{_YQ})\s*년?{_RANGE_SEP}(?P<b>{_Y4}|{_YQ})\s*년")
CHANGE_SIGN = re.compile(r"(?P<s>[△▽▲])\s?(?P<n>\d[\d,]*(?:\.\d+)?)\s?(?P<p>%p|%|퍼센트)?")
# △ 뒤 숫자에 금액 단위가 붙으면 증감률이 아니라 음수 금액이다("△34,703십억원")
_AMOUNT_TAIL = re.compile(r"\s?(?:조|천억|백억|십억|억|천만|백만|십만|만|천)?\s?원")
CHANGE_WORD = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s?(?P<p>%p?|퍼센트)\s?(?:포인트\s?)?(?P<w>증가|감소|상승|하락|늘|줄|확대|축소)")
PHONE = re.compile(r"(?<![\d-])(?:0\d{1,2}[-)\s.]\s?\d{3,4}[-.\s]\d{4}|1[5-9]\d{2}-\d{4})(?![\d-])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 법령 이름 뒤의 시행령·시행규칙·부칙도 이름에 넣는다. 부칙 뒤 "(’19.12.31)" 같은 개정일 괄호는 건너뛴다.
_LAW_TAIL = r"(?:\s?(?:시행령|시행규칙))?(?:\s?부칙)?"
LAW_NAME = (r"(?:「(?P<q>[^」]{1,40})」" + _LAW_TAIL + "|"
            r"(?P<w>(?:같은\s?법|[가-힣·]{1,24}(?:법률|법))" + _LAW_TAIL + "|"
            r"[가-힣·]{1,24}(?:고시|규정|조례|규칙|훈령|예규|지침)))"
            r"(?:\s?\([^)]{1,20}\))?")
ARTICLE = re.compile(rf"(?:{LAW_NAME}\s?)?"
                     rf"제\s?(?P<a>\d+)\s?조(?:의\s?(?P<a2>\d+))?(?:\s?제\s?(?P<pa>\d+)\s?항)?(?:\s?제\s?(?P<it>\d+)\s?호)?")
NEGATIVE_WORDS = ("감소", "하락", "줄", "축소")


def _year(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if len(digits) == 4 else 2000 + int(digits)


def _date(y, m, d) -> str:
    out = f"{y:04d}-{m:02d}"
    return out + (f"-{d:02d}" if d else "")


def find_periods(text: str) -> Iterator[dict]:
    taken: List[Tuple[int, int]] = []
    for m in PERIOD.finditer(text):
        ay, am, ad = _year(m.group("ay")), int(m.group("am")), m.group("ad")
        by, bm, bd = _year(m.group("by")), int(m.group("bm")), m.group("bd")
        if ay is None or not (1 <= am <= 12 and 1 <= bm <= 12):
            continue
        ad, bd = (int(ad) if ad else None), (int(bd) if bd else None)
        if bool(ad) != bool(bd) and not (ad and bd is None and m.group("bd") is None and bm):
            pass
        if ad is None and bd is None and not m.group("by") and "월" not in m.group(0):
            continue  # "3. 12." 같은 숫자 나열은 기간으로 보지 않는다
        by = by or ay
        start, end = _date(ay, am, ad), _date(by, bm, bd)
        if end < start:
            continue
        taken.append(m.span())
        yield {"kind": "period", "raw": m.group(0).strip(), "normalized": {"start": start, "end": end},
               "charOffset": m.start(), "length": len(m.group(0).rstrip())}
    for m in YEAR_RANGE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        a, b = _year(m.group("a")), _year(m.group("b"))
        if a and b and a <= b:
            yield {"kind": "period", "raw": m.group(0), "normalized": {"start": f"{a}", "end": f"{b}"},
                   "charOffset": m.start(), "length": len(m.group(0))}


def find_changes(text: str, cell: bool = False) -> Iterator[dict]:
    """cell=True 는 표 칸 글: 칸의 "△2,006"·"△297" 처럼 % 도 소수점도 없는 값은 음수 수치이지 증감률이 아니다."""
    taken = []
    for m in CHANGE_SIGN.finditer(text):
        number = m.group("n")
        if not m.group("p") and ("," in number or _AMOUNT_TAIL.match(text, m.end())
                                 or (cell and "." not in number)):
            taken.append(m.span())
            continue
        value = float(number.replace(",", ""))
        value = value if m.group("s") == "▲" else -value
        unit = "%p" if (m.group("p") or "").startswith("%p") else ("%" if m.group("p") else None)
        taken.append(m.span())
        yield {"kind": "change", "raw": m.group(0), "normalized": value, "unit": unit,
               "charOffset": m.start(), "length": len(m.group(0))}
    for m in CHANGE_WORD.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        value = float(m.group("n"))
        if any(m.group("w").startswith(w) for w in NEGATIVE_WORDS):
            value = -value
        yield {"kind": "change", "raw": m.group(0), "normalized": value,
               "unit": "%p" if m.group("p").startswith("%p") else "%",
               "charOffset": m.start(), "length": len(m.group(0))}


def find_contacts(text: str) -> Iterator[dict]:
    for m in PHONE.finditer(text):
        yield {"kind": "contact", "raw": m.group(0), "normalized": {"type": "phone",
               "value": re.sub(r"[\s.)]+", "-", m.group(0)).strip("-")}, "charOffset": m.start(), "length": len(m.group(0))}
    for m in EMAIL.finditer(text):
        yield {"kind": "contact", "raw": m.group(0), "normalized": {"type": "email", "value": m.group(0).lower()},
               "charOffset": m.start(), "length": len(m.group(0))}


_SAME_LAW = re.compile(r"^(?:동법|같은\s?법)\s?(시행령|시행규칙|부칙)?$")


def find_references(text: str) -> Iterator[dict]:
    last = None  # 이 글에서 마지막으로 이름이 나온 법 — "동법 시행령"·"같은 법" 은 그 법을 가리킨다
    for m in ARTICLE.finditer(text):
        law = m.group("q") or m.group("w")
        if m.group("q"):
            tail = re.findall(r"(?<=[」\s])(시행령|시행규칙|부칙)", m.group(0).split("제")[0])
            law = " ".join([law] + tail) if tail else law
        law = re.sub(r"\s+", " ", law).strip() if law else None
        same = _SAME_LAW.match(law or "")
        if same and last:
            law = " ".join(x for x in (last, same.group(1)) if x)
        elif law and not same:
            last = re.sub(r"\s?(?:시행령|시행규칙|부칙)$", "", law)
        article = f"제{m.group('a')}조" + (f"의{m.group('a2')}" if m.group("a2") else "")
        yield {"kind": "reference", "raw": m.group(0).strip(),
               "normalized": {"law": law, "article": article,
                              "paragraph": f"제{m.group('pa')}항" if m.group("pa") else None,
                              "item": f"제{m.group('it')}호" if m.group("it") else None},
               "charOffset": m.start(), "length": len(m.group(0).strip())}


FINDERS = (find_periods, find_changes, find_contacts, find_references)


def text_units(blocks: List[dict], iter_cells) -> Iterator[Tuple[dict, str, str]]:
    """(블록 또는 칸, 그 ID, 글). 레이아웃 표에서 풀린 문단은 칸으로 이미 다루므로 건너뛴다.
    칸 글은 칸 자기 문단만 (안쪽 표 글은 안쪽 칸이 따로 나온다)."""
    for block in blocks:
        if block.get("fromLayoutTable"):
            continue
        if block["type"] == "table":
            for cell in iter_cells(block):
                own = "\n".join(p["text"] for p in cell.get("paragraphs", [])) if cell.get("paragraphs") \
                    else cell.get("text", "")
                if own.strip():
                    yield block, cell["id"], own
        elif block.get("text"):
            yield block, block["id"], block["text"]


# 숫자 표 레코드에서 증감률로 볼 행·열 이름 — 비율 낱말만. "증감 (B-A)"·"증감액" 은 차이 금액이라 뺀다.
# "대비" 는 "수 출 (전년동기대비)" 처럼 주 값 행 이름에도 붙어 뺀다.
CHANGE_RECORD_WORDS = ("증감률", "증감율", "증감비율", "증가율", "감소율", "변동률", "등락률", "상승률", "하락률", "성장률")


def record_changes(semantics: Dict[str, dict], tables: Dict[str, dict], existing: List[dict]) -> List[dict]:
    """숫자 표 레코드에서 행·열 이름이 증감률인 값 → change 값(#55).

    통계표의 증감률 행은 "(147.8)"·"(△51.2)" 처럼 괄호만 쓴다. △ 가 붙은 값은 글 패턴(find_changes)이
    잡지만 부호 없는 값은 놓친다. 표 의미화(tablesem)가 읽은 값을 칸 ID 와 함께 보탠다. 이미 같은 칸에서
    change 를 찾았으면 다시 넣지 않는다. tables: {표 ID: (표 블록, 칸 ID → 칸)}.
    """
    have = {i.get("blockId") for i in existing if i.get("kind") == "change"}
    items = []
    for table_id, sem in semantics.items():
        if not sem or sem.get("kind") != "numeric" or table_id not in tables:
            continue
        block, cells = tables[table_id]
        for record in sem.get("records", []):
            if record.get("secondary"):
                continue  # 주 값 행(괄호 증감률은 secondary)
            row_hit = any(w in re.sub(r"\s+", "", record.get("label") or "") for w in CHANGE_RECORD_WORDS)
            for key, value in record.get("values", {}).items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if not (row_hit or any(w in re.sub(r"\s+", "", key) for w in CHANGE_RECORD_WORDS)):
                    continue
                cell_id = record.get("cellIds", {}).get(key)
                if not cell_id or cell_id in have:
                    continue
                raw = (cells.get(cell_id) or {}).get("text", "").strip() or str(value)
                unit = record.get("units", {}).get(key)
                items.append({"kind": "change", "raw": raw, "normalized": float(value),
                              "unit": unit if unit in ("%", "%p") else None, "charOffset": 0, "length": len(raw),
                              "page": block.get("page", 0), "section": block.get("section"),
                              "paragraph": block.get("paragraph"), "blockId": cell_id, "detector": "hwp-master",
                              "from": "tableRecord"})
                have.add(cell_id)
    return items


def extract_values(blocks: List[dict], iter_cells) -> List[dict]:
    items = []
    for block, block_id, text in text_units(blocks, iter_cells):
        for finder in FINDERS:
            found = finder(text, cell=True) if finder is find_changes and block["type"] == "table" else finder(text)
            for item in found:
                item.update({"page": block.get("page", 0), "section": block.get("section"),
                             "paragraph": block.get("paragraph"), "blockId": block_id, "detector": "hwp-master"})
                items.append(item)
    return items
