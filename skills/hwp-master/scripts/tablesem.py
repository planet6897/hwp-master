#!/usr/bin/env python3
"""표 의미화: 데이터 표(role=data)의 머리 행·행 이름 열·단위 캡션·칸 값 타입을 해석해 레코드로 만든다.

  semantics = {
    "headerRows": 2, "stubCols": 1,
    "unit": {"caption": "(단위 : 백만 달러, %)", "units": ["백만 달러", "%"], "source": "s0.p9"},
    "columns": [{"key": "2023년|5월", "col": 1, "unit": "백만 달러"}, ...],
    "records": [{"row": 2, "label": "수 출 (전년동기대비)", "values": {"2023년|5월": 52054},
                 "secondary": {"2023년|5월": -15.5}, "units": {...}, "cellIds": {...}}, ...],
  }

값 규칙: "△15.5"·"▽3"·"-2,197" 은 음수, "12.3%" 는 % 값, "52,054 (△15.5)" 는 주 값과 괄호 안 증감률(secondary),
"580억 달러"처럼 통화가 붙으면 금액(정규화 값과 통화). 머리 행·열 이름에 증감·률·비중·대비·% 가 있으면 그 칸 단위는 %.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

from kv import is_label

UNIT_CAPTION = re.compile(r"[(\[<〈]?\s*단\s*위\s*[:：]\s*([^)\]>〉]*)[)\]>〉]?")
_NUM = r"[\d,]+(?:\.\d+)?"
VALUE = re.compile(rf"^(?P<s1>[△▽-]?)\s*(?P<n1>{_NUM})\s*(?P<p1>%)?\s*"
                   rf"(?:\(\s*(?P<s2>[△▽-]?)\s*(?P<n2>{_NUM})\s*(?P<p2>%)?\s*\))?$")
PERCENT_WORDS = ("증감", "률", "율", "비중", "대비", "%", "증가율", "감소율")
UNIT_WORDS = re.compile(r"(원|달러|불|유로|엔|위안|톤|건|명|개|대|회|kg|㎏|㎡|m²|km|t|%|배|천|만|억|조|백만|시간|일|세대|가구|호)")


def _norm(text: str) -> str:
    return re.sub(r"[\s 　]+", " ", text or "").strip()


def parse_units(caption: str) -> List[str]:
    """"(단위 : 백만 달러, %)" → ["백만 달러", "%"]. "전년 동월 대비" 같은 설명은 단위가 아니다."""
    m = UNIT_CAPTION.search(caption or "")
    if not m:
        return []
    units = []
    for piece in re.split(r"[,，/]", m.group(1)):
        piece = _norm(piece)
        if not piece:
            continue
        if "%" in piece or "퍼센트" in piece:
            units.append("%")
        elif UNIT_WORDS.search(piece) and len(piece) <= 10 and "대비" not in piece:
            units.append(piece)
    return list(dict.fromkeys(units))


def parse_value(text: str, find_amounts: Optional[Callable[[str], List[dict]]] = None) -> dict:
    """칸 글 하나 → {raw, type(empty|number|percent|amount|text), value, secondary?}."""
    raw = _norm(text)
    if raw in ("", "-", "–", "—", "·", "…"):
        return {"raw": raw, "type": "empty", "value": None}
    m = VALUE.match(raw)
    if m:
        def num(sign, digits):
            v = float(digits.replace(",", ""))
            v = -v if sign in ("△", "▽", "-") else v
            return int(v) if v == int(v) and "." not in digits else v
        out = {"raw": raw, "type": "percent" if m.group("p1") else "number", "value": num(m.group("s1"), m.group("n1"))}
        if m.group("n2"):
            out["secondary"] = num(m.group("s2"), m.group("n2"))
        return out
    if find_amounts:
        amounts = find_amounts(raw)
        if len(amounts) == 1 and len(amounts[0]["raw"]) >= len(raw) - 2:
            return {"raw": raw, "type": "amount", "value": amounts[0]["normalized"],
                    "currency": amounts[0]["currency"]}
    return {"raw": raw, "type": "text", "value": raw}


def _grid(table: dict) -> Dict[Tuple[int, int], dict]:
    """(행, 열) → 그 자리를 덮는 칸 (병합 칸은 덮는 모든 자리에)."""
    grid = {}
    for cell in table["cells"]:
        for r in range(cell["row"], cell["row"] + max(1, cell["rowSpan"])):
            for c in range(cell["col"], cell["col"] + max(1, cell["colSpan"])):
                grid[(r, c)] = cell
    return grid


def find_caption(blocks: List[dict], position: int, table: dict) -> Tuple[str, Optional[str]]:
    """표 앞 두 블록·표 첫 행·표 뒤 한 블록에서 단위 캡션을 찾는다 → (캡션, 출처 ID)."""
    candidates = [blocks[i] for i in range(position - 1, max(-1, position - 3), -1)]
    candidates += [{"id": c["id"], "text": c["text"]} for c in table["cells"] if c["row"] == 0]
    if position + 1 < len(blocks):
        candidates.append(blocks[position + 1])
    for block in candidates:
        text = block.get("text") or ""
        if UNIT_CAPTION.search(text):
            return _norm(UNIT_CAPTION.search(text).group(0)), block.get("id")
    return "", None


def analyze(table: dict, caption: str = "", caption_id: Optional[str] = None,
            find_amounts: Optional[Callable[[str], List[dict]]] = None) -> dict:
    grid = _grid(table)
    rows, cols = table["rows"], table["cols"]
    corner = grid.get((0, 0))
    header_rows = max(1, corner["rowSpan"]) if corner else 1
    flagged = 0
    while flagged < rows and all(grid.get((flagged, c), {}).get("header") for c in range(cols)):
        flagged += 1
    header_rows = min(max(header_rows, flagged), max(rows - 1, 0))
    if rows <= 1:
        header_rows = 0

    def numeric(v):
        return v is not None and v["type"] in ("number", "percent", "amount")

    textual = not any(numeric(parse_value(cell["text"], find_amounts)) for cell in table["cells"])
    if textual:
        # 숫자가 하나도 없는 글 표: 머리 표시가 있거나, 3행 이상이고 첫 행이 모두 짧은 라벨일 때만 머리 행으로 본다.
        top = [grid[(0, c)] for c in range(cols) if (0, c) in grid]
        distinct = {id(c) for c in top}
        if flagged:
            header_rows = flagged
        elif rows >= 3 and cols >= 2 and len(distinct) >= 2 and all(is_label(c["text"]) for c in top):
            header_rows = max(1, corner["rowSpan"]) if corner else 1
        else:
            header_rows = 0
    values = {(r, c): parse_value(grid[(r, c)]["text"], find_amounts) if (r, c) in grid else None
              for r in range(header_rows, rows) for c in range(cols)}

    stub_cols = max(1, corner["colSpan"]) if corner else 1
    stub_cols = min(stub_cols, max(cols - 1, 0))
    if stub_cols and all(numeric(values.get((r, 0))) for r in range(header_rows, rows) if values.get((r, 0))):
        stub_cols = 0  # 첫 열도 숫자뿐이면 행 이름 열이 없다

    units = parse_units(caption)
    base_unit = next((u for u in units if u != "%"), None)
    has_percent = "%" in units
    if base_unit is None and has_percent:
        base_unit = "%"  # "(단위: 전년 동월 대비, %)" — 표 전체가 %

    columns = []
    for c in range(stub_cols, cols):
        parts = []
        for r in range(header_rows):
            cell = grid.get((r, c))
            text = _norm(cell["text"]) if cell else ""
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        key = "|".join(parts) or f"열{c + 1}"
        while key in (col["key"] for col in columns):
            key += "'"
        percent_col = any(w in key for w in PERCENT_WORDS)
        columns.append({"key": key, "col": c,
                        "unit": "%" if percent_col and (has_percent or not base_unit) else base_unit})

    records = []
    for r in range(header_rows, rows):
        label_parts = []
        for c in range(stub_cols):
            cell = grid.get((r, c))
            text = _norm(cell["text"]) if cell else ""
            if text and text not in label_parts:
                label_parts.append(text)
        label = " | ".join(label_parts)
        percent_row = any(w in label for w in PERCENT_WORDS)
        record = {"row": r, "label": label, "values": {}, "units": {}, "cellIds": {}}
        for column in columns:
            v = values.get((r, column["col"]))
            cell = grid.get((r, column["col"]))
            if v is None or cell is None or (cell["row"] != r and cell["col"] == column["col"]):
                continue  # 위 행에서 내려온 병합 칸은 한 번만
            record["values"][column["key"]] = v["value"]
            if v.get("currency"):
                unit = v["currency"]
            elif v["type"] == "percent":
                unit = "%"
            elif percent_row and "secondary" not in v and has_percent:
                unit = "%"  # "(증감률)" 행 — 괄호 증감률이 붙은 주 값 행은 열 단위를 따른다
            else:
                unit = column["unit"]
            if v["type"] in ("number", "percent", "amount") and unit:
                record["units"][column["key"]] = unit
            if "secondary" in v:
                record.setdefault("secondary", {})[column["key"]] = v["secondary"]
                if has_percent:
                    record.setdefault("secondaryUnits", {})[column["key"]] = "%"
            record["cellIds"][column["key"]] = cell["id"]
        if record["values"]:
            records.append(record)

    numeric_cells = sum(1 for v in values.values() if numeric(v))
    return {"headerRows": header_rows, "stubCols": stub_cols, "kind": "text" if textual else "numeric",
            "unit": {"caption": caption, "units": units, "source": caption_id} if caption else None,
            "columns": columns, "records": records,
            "numericCells": numeric_cells, "bodyCells": sum(1 for v in values.values() if v)}


def record_rows(semantics: dict) -> List[List]:
    """records 를 CSV 행으로 (첫 열 행 이름, 그다음 열 키). 단위는 머리에 [단위] 로 붙인다."""
    columns = semantics["columns"]
    head = ["행"] + [c["key"] + (f" [{c['unit']}]" if c.get("unit") else "") for c in columns]
    out = [head]
    for record in semantics["records"]:
        out.append([record["label"]] + [record["values"].get(c["key"], "") for c in columns])
    return out
