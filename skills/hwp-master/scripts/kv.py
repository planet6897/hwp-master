#!/usr/bin/env python3
"""항목-값(key-value) 추출: 양식형 표의 "라벨 | 값" 칸 쌍, "담당자: 홍길동" 꼴 문단, 누름틀을 하나의 목록으로.

  {"key": "보도시점", "keyNorm": "보도시점", "value": "배포즉시", "source": "table",
   "blockId": "s0.p1.t0.r0c1", "keyBlockId": "s0.p1.t0.r0c0", "page": 0}

- 표: 숫자 레코드 표(semantics.kind == numeric)와 머리 행이 있는 글 표는 항목-값이 아니라 레코드라 뺀다. 그 밖의 표(글 표·레이아웃 표)에서
  한 행의 칸을 왼쪽부터 (라벨, 값) 으로 짝짓는다. 라벨은 짧고 숫자가 거의 없는 글이다(성    명 → keyNorm 성명).
  첫 행이 모두 라벨이고 행이 둘이면 세로 양식(라벨 행 아래 값 행)으로 본다.
- 문단: "라벨: 값" (라벨 12자 이하, 숫자 없음). "09:00" 같은 시각은 라벨이 아니다.
- 누름틀: 이름과 값이 있으면 source=field 로 넣는다. 같은 keyNorm 이 표·문단에도 있으면 누름틀을 앞에 두고
  나머지에 duplicateOf 를 붙인다.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional

LABEL_MAX = 14
_SPACES = re.compile(r"[\s 　]+")
INLINE = re.compile(r"^\s*([가-힣A-Za-z][가-힣A-Za-z()·\s 　]{0,11}?)\s*[:：]\s*(\S.*)$")


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


def table_pairs(table: dict) -> Iterator[tuple]:
    """(라벨 칸, 값 칸) 쌍."""
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
            if is_label(label["text"]) and clean(value["text"]):
                yield label, value
                k += 2
            else:
                k += 1


def extract_kv(blocks: List[dict], tables_semantics: Optional[Dict[str, dict]] = None,
               fields: Optional[List[dict]] = None, iter_tables=None) -> List[dict]:
    tables_semantics = tables_semantics or {}
    items: List[dict] = []
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
                    items.append({"key": clean(label["text"]).rstrip(":： "), "keyNorm": norm_key(label["text"]),
                                  "value": clean(value["text"]), "source": "table", "blockId": value["id"],
                                  "keyBlockId": label["id"], "page": block.get("page")})
        elif block["type"] in ("paragraph", "list") and block.get("text"):
            for line in block["text"].split("\n"):
                m = INLINE.match(line)
                if m and not re.search(r"\d", m.group(1)) and len(norm_key(m.group(1))) <= LABEL_MAX:
                    items.append({"key": clean(m.group(1)), "keyNorm": norm_key(m.group(1)),
                                  "value": clean(m.group(2)), "source": "paragraph", "blockId": block["id"],
                                  "page": block.get("page")})
    first: Dict[str, int] = {}
    for k, item in enumerate(items):
        if item["keyNorm"] in first:
            item["duplicateOf"] = first[item["keyNorm"]]
        else:
            first[item["keyNorm"]] = k
        item["index"] = k
    return items
