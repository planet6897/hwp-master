#!/usr/bin/env python3
"""항목-값(key-value) 추출: 양식형 표의 "라벨 | 값" 칸 쌍, "담당자: 홍길동" 꼴 문단, 누름틀을 하나의 목록으로.

  {"key": "보도시점", "keyNorm": "보도시점", "value": "배포즉시", "source": "table",
   "blockId": "s0.p1.t0.r0c1", "keyBlockId": "s0.p1.t0.r0c0", "page": 0}

- 표: 숫자 레코드 표(semantics.kind == numeric)와 머리 행이 있는 글 표는 항목-값이 아니라 레코드라 뺀다. 그 밖의 표(글 표·레이아웃 표)에서
  한 행의 칸을 왼쪽부터 (라벨, 값) 으로 짝짓는다. 라벨은 짧고 숫자가 거의 없는 글이다(성    명 → keyNorm 성명).
  첫 행이 모두 라벨이고 행이 둘이면 세로 양식(라벨 행 아래 값 행)으로 본다.
- 문단: "라벨: 값" (라벨 12자 이하, 숫자 없음). "09:00" 같은 시각은 라벨이 아니다. 앞의 개조식·주석 기호(※ · 1) ․ 등)는
  떼고 보고, "가 : 1, 나 : 2" 처럼 한 줄에 여럿이면 나눈다.
- 칸 하나에 라벨과 값이 함께 든 것("TEL: 010-…", "발행일  2025년 06월 18일")은 그 칸만으로 한 쌍이고, 옆 칸과 짝짓지 않는다.
- 값이 라벨처럼 보이는 칸(양식 라벨 낱말이 든 짧은 글)은 값이 아니다(빈 칸 옆 라벨끼리 짝짓지 않는다).
  체크 상자 값("[ ] 병원급 [■] 종합병원")은 표시된 항목만, 빈 날짜 틀("2024년 □월 □일", "년 월 일")은 값이 아니다.
- 누름틀: 이름과 값이 있으면 source=field 로 넣는다. 같은 keyNorm 이 표·문단에도 있으면 누름틀을 앞에 두고
  나머지에 duplicateOf 를 붙인다.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional

LABEL_MAX = 14
_SPACES = re.compile(r"[\s 　]+")
INLINE = re.compile(r"^\s*([가-힣A-Za-z][가-힣A-Za-z()·\d\s 　]{0,11}?)\s*[:：]\s*(?!//)(\S.*)$")
# 칸 안의 "라벨 + 숫자 값"(발행일  2025년 06월 18일 · 대표전화000-000-0000). 값은 숫자와 날짜·전화 꾸밈만.
CELL_INLINE = re.compile(r"^\s*([가-힣]{2,8})\s*(\d[\d\s년월일.\-~()]*\d[년월일.]?)\s*$")
# 문단 앞 개조식·주석 기호 — "※ 라벨 : 값", "1) 라벨 : 값", "․라벨 : 값"
LEAD_MARKER = re.compile(r"^\s*(?:※|\*{1,3}|\(?\d{1,2}\)|\d{1,2}\.(?!\d)|[①-⑳]|[․‧·∙•ㆍ○◦●◎□■◆◇▣▶►-])\s*")
# 한 줄에 쌍이 여럿: ", 라벨 : " 앞에서 나눈다
MULTI_SPLIT = re.compile(r",\s*(?=[가-힣A-Za-z][가-힣A-Za-z()·\s 　]{0,11}?\s*[:：]\s*\S)")
CHECKED = re.compile(r"\[\s*[■√✓✔Vv●▣]\s*\]\s*([^\[\]]+?)\s*(?=\[|$)")
BLANK_DATE = re.compile(r"[□○]\s*[월일]|^[\s년월일()~～.\-:]*$")


def norm_key(text: str) -> str:
    return _SPACES.sub("", text or "").rstrip(":：")


def clean(text: str) -> str:
    return _SPACES.sub(" ", text or "").strip()


def is_label(text: str) -> bool:
    """라벨처럼 보이는 칸: 짧고, 숫자가 거의 없고, 문장 끝맺음이 없다."""
    t = clean(text).rstrip(":：")
    if not t or len(norm_key(t)) > LABEL_MAX or "\n" in (text or "").strip():
        return False
    digits = sum(ch.isdigit() for ch in t)
    if digits > 1 or re.search(r"[.。!?]$", t):
        return False
    return bool(re.search(r"[가-힣A-Za-z]", t))


# 양식 라벨에 흔한 낱말 — 2행 표가 가로(왼쪽 열이 라벨)인지 세로(위 행이 라벨)인지 가를 때만 쓴다.
LABEL_WORDS = ("성명", "이름", "연락처", "전화", "휴대", "주소", "담당", "부서", "직위", "직급", "소속", "기관", "일시",
               "기간", "날짜", "일자", "금액", "비고", "구분", "회사", "대표", "사업자", "생년월일", "이메일", "메일",
               "제목", "번호", "시점", "장소", "목적", "내용", "과장", "책임자")


def _label_hits(cells: List[dict]) -> int:
    return sum(1 for c in cells if any(w in norm_key(c["text"]) for w in LABEL_WORDS))


def _row_cells(table: dict) -> Dict[int, List[dict]]:
    rows: Dict[int, List[dict]] = {}
    for cell in table["cells"]:
        rows.setdefault(cell["row"], []).append(cell)
    return {r: sorted(cs, key=lambda c: c["col"]) for r, cs in sorted(rows.items())}


def _looks_label(cell: dict) -> bool:
    """값 자리에 온 칸이 사실은 라벨인가: 라벨 꼴이고 양식 라벨 낱말이 든다(성명·구분·담당 …)."""
    return is_label(cell["text"]) and _label_hits([cell]) > 0


def label_digits(label: str) -> bool:
    """라벨에 숫자가 있나(괄호 안 "(4개)" 는 빼고 본다). 숫자가 든 글은 라벨이 아니다."""
    return bool(re.search(r"\d", re.sub(r"\([^)]*\)", "", label)))


def cell_pair(text: str) -> Optional[tuple]:
    """칸 하나에 든 (라벨, 값) — "TEL: 010-…", "발행일  2025년 06월 18일". 없으면 None."""
    m = INLINE.match(text or "")
    if m and not label_digits(m.group(1)) and clean(m.group(2)):
        return clean(m.group(1)), clean(m.group(2))
    m = CELL_INLINE.match(text or "")
    if m and is_label(m.group(1)):
        return m.group(1), clean(m.group(2))
    return None


def clean_value(value: str) -> str:
    """체크 상자 값은 표시된 항목만 남긴다. 빈 날짜 틀은 빈 값."""
    value = clean(value)
    if re.search(r"\[\s*\]", value) and CHECKED.search(value):
        value = ", ".join(clean(x) for x in CHECKED.findall(value))
    if BLANK_DATE.search(value) and not re.search(r"\d\s*[월일]", value):
        return ""
    return value


def table_pairs(table: dict) -> Iterator[tuple]:
    """(라벨 칸, 값 칸) 쌍. 칸 하나로 쌍이 되는 칸(cell_pair)은 옆 칸과 짝짓지 않는다."""
    rows = _row_cells(table)
    if len(rows) == 2:
        top, bottom = rows[min(rows)], rows[max(rows)]
        left = [cs[0] for cs in rows.values() if cs]
        if len(top) == len(bottom) >= 2 and all(is_label(c["text"]) for c in top) \
                and not all(is_label(c["text"]) for c in bottom) and _label_hits(top) > _label_hits(left):
            yield from zip(top, bottom)  # 세로 양식: 위 행이 라벨, 아래 행이 값
            return
    for cells in rows.values():
        k = 0
        while k + 1 < len(cells):
            label, value = cells[k], cells[k + 1]
            if cell_pair(label["text"]) or cell_pair(value["text"]):
                k += 1
            elif (is_label(label["text"]) and clean_value(value["text"]) and not _looks_label(value)):
                yield label, value
                k += 2
            else:
                k += 1


def extract_kv(blocks: List[dict], tables_semantics: Optional[Dict[str, dict]] = None,
               fields: Optional[List[dict]] = None, iter_tables=None) -> List[dict]:
    tables_semantics = tables_semantics or {}
    items: List[dict] = []
    field_cells = {f.get("blockId") for f in fields or [] if f.get("blockId") and clean(str(f.get("value", "")))}
    for f in fields or []:
        name, value = clean(f.get("name", "")), clean(str(f.get("value", "")))
        if name and value:
            items.append({"key": name, "keyNorm": norm_key(name), "value": value, "source": "field",
                          "blockId": f.get("blockId"), "fieldType": f.get("fieldType")})
    for block in blocks:
        if block["type"] == "table" and not block.get("fromLayoutTable"):
            for table in (iter_tables(block) if iter_tables else [block]):
                sem = tables_semantics.get(table["id"])
                if sem and (sem.get("kind") == "numeric" or sem.get("headerRows")):
                    continue  # 숫자 레코드 표·머리 행이 있는 글 표는 항목-값이 아니라 레코드다
                for label, value in table_pairs(table):
                    if value["id"] in field_cells or label["id"] in field_cells:
                        continue
                    items.append({"key": clean(label["text"]).rstrip(":： "), "keyNorm": norm_key(label["text"]),
                                  "value": clean_value(value["text"]), "source": "table", "blockId": value["id"],
                                  "keyBlockId": label["id"], "page": block.get("page")})
                for cell in table["cells"]:
                    pair = cell_pair(cell["text"]) if "\n" not in cell["text"].strip() else None
                    if pair and clean_value(pair[1]):
                        items.append({"key": pair[0], "keyNorm": norm_key(pair[0]), "value": clean_value(pair[1]),
                                      "source": "table", "blockId": cell["id"], "keyBlockId": cell["id"],
                                      "page": block.get("page")})
        elif block["type"] in ("paragraph", "list", "note") and block.get("text"):  # 각주 풀이 "순 융 자 : …" 도
            for line in block["text"].split("\n"):
                line = LEAD_MARKER.sub("", line, count=1)
                for piece in MULTI_SPLIT.split(line):
                    m = INLINE.match(piece)
                    if m and not label_digits(m.group(1)) and len(norm_key(m.group(1))) <= LABEL_MAX \
                            and clean_value(m.group(2)):
                        items.append({"key": clean(m.group(1)), "keyNorm": norm_key(m.group(1)),
                                      "value": clean_value(m.group(2)), "source": "paragraph",
                                      "blockId": block["id"], "page": block.get("page")})
    first: Dict[str, int] = {}
    for k, item in enumerate(items):
        prev = first.get(item["keyNorm"])
        if prev is not None and (items[prev]["source"] == "field" or norm_key(items[prev]["value"]) == norm_key(item["value"])):
            item["duplicateOf"] = prev
        elif prev is None:
            first[item["keyNorm"]] = k
        item["index"] = k
    return items
