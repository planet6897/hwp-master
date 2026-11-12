#!/usr/bin/env python3
"""의미 값 확장: rhwp extract-data 가 잡지 않는 기간·증감률·연락처·조문 참조를 document.json 글에서 찾는다.

  period     "2026. 3. 1.~12. 31."      → {"start": "2026-03-01", "end": "2026-12-31"}
             "2026년 3월~12월", "’22~’26년" → {"start": "2026-03", "end": "2026-12"}, {"start": "2022", "end": "2026"}
  change     "△6.7%", "6.7% 감소"        → -6.7 (unit %)   ("증가·상승"이면 +)
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
CHANGE_SIGN = re.compile(r"(?P<s>[△▽▲])\s?(?P<n>\d+(?:\.\d+)?)\s?(?P<p>%|%p|퍼센트)?")
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


def find_changes(text: str) -> Iterator[dict]:
    taken = []
    for m in CHANGE_SIGN.finditer(text):
        value = float(m.group("n"))
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


def find_references(text: str) -> Iterator[dict]:
    for m in ARTICLE.finditer(text):
        law = m.group("q") or m.group("w")
        if m.group("q"):
            tail = re.findall(r"(?<=[」\s])(시행령|시행규칙|부칙)", m.group(0).split("제")[0])
            law = " ".join([law] + tail) if tail else law
        law = re.sub(r"\s+", " ", law).strip() if law else None
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


def extract_values(blocks: List[dict], iter_cells) -> List[dict]:
    items = []
    for block, block_id, text in text_units(blocks, iter_cells):
        for finder in FINDERS:
            for item in finder(text):
                item.update({"page": block.get("page", 0), "section": block.get("section"),
                             "paragraph": block.get("paragraph"), "blockId": block_id, "detector": "hwp-master"})
                items.append(item)
    return items
