#!/usr/bin/env python3
"""LLM 용 청크(chunks.jsonl): document.json 블록을 제목 경계에 맞춰 묶고 출처 ID·쪽·값을 함께 담는다.

한 줄에 청크 하나:
  {"id": "c0003", "headingPath": ["Ⅱ. 추진 계획"], "outlinePath": ["수출 동향"], "pages": [3, 4], "blockIds": ["s0.p40", "s0.p41.t0", ...],
   "chars": 1432, "text": "## Ⅱ. 추진 계획\n\n□ ...\n\n| 구분 | 금액 |\n|---|---|\n| 장비 | 580억 달러 |",
   "values": [{"kind": "amount", "raw": "580억 달러", "normalized": 58000000000, "blockId": "..."}],
   "tables": [{"blockId": "s0.p41.t0", "label": "T03", "records": [...]}], "untrustedContent": true}

규칙
- 개요 제목(heading)에서 언제나 새 청크를 시작한다. 같은 장 안에서는 목표 크기(chars, 기본 1500)를 넘기 전에
  자르고, 가능하면 1단계 개조식 항목(□·Ⅰ.·1.) 앞에서 자른다. 그래서 청크가 장 경계를 넘지 않는다.
- 데이터 표는 Markdown 표로 넣는다. 한 표는 쪼개지 않지만, 목표 크기의 두 배를 넘으면 행 단위로 나누고 머리 행을
  반복한다(part 1/3 …). 표의 semantics.records 는 tables 에 함께 담는다.
- 레이아웃 표는 글 없이 ID 만 넣는다(그 칸 글은 바로 뒤의 풀린 문단 블록으로 들어간다).
- 모든 블록은 정확히 한 청크에 들어간다(나눈 표만 여러 부분 청크에 나뉜다).
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional

DEFAULT_CHARS = 1500


def _cell_md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", "<br>").strip()


def table_rows(table: dict) -> List[List[str]]:
    rows, cols = table.get("rows", 0), table.get("cols", 0)
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.get("cells", []):
        r, c = cell["row"], cell["col"]
        if 0 <= r < rows and 0 <= c < cols:
            grid[r][c] = _cell_md(cell.get("text", ""))
    return grid


def table_markdown(rows: List[List[str]], header_rows: int = 1) -> str:
    if not rows:
        return ""
    header_rows = max(1, min(header_rows, len(rows)))
    head = [" / ".join(dict.fromkeys(x for x in col if x)) for col in zip(*rows[:header_rows])]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(row) + " |" for row in rows[header_rows:]]
    return "\n".join(lines)


def render(block: dict) -> str:
    kind = block["type"]
    text = (block.get("text") or "").strip()
    if kind == "heading":
        return "#" * min(6, max(1, block.get("level", 1)) + 1) + " " + text
    if kind in ("paragraph", "list"):
        return text
    if kind == "note":
        return f"[{'각주' if block.get('noteType') == 'footnote' else '미주'}] {text}"
    if kind == "headerFooter":
        return f"[{'머리말' if block.get('kind') == 'header' else '꼬리말'}] {text}" if text else ""
    if kind == "image":
        return "[그림]"
    return ""


def _is_top_item(block: dict) -> bool:
    o = block.get("outline")
    return bool(o) and o.get("level") == 1 and o.get("kind") != "note"


def build_chunks(blocks: List[dict], data_items: Iterable[dict] = (), tables_by_id: Optional[Dict[str, dict]] = None,
                 chars: int = DEFAULT_CHARS) -> List[dict]:
    tables_by_id = tables_by_id or {}
    values_by_block: Dict[str, List[dict]] = {}
    for item in data_items:
        if item.get("blockId"):
            values_by_block.setdefault(item["blockId"], []).append(
                {k: item.get(k) for k in ("kind", "raw", "normalized", "unit", "currency", "blockId") if item.get(k)
                 is not None})

    # 개조식 상위 항목 경로(□ … > ○ …): 개요 제목이 없는 보고서·보도자료에서도 청크의 맥락을 알 수 있게 한다.
    by_id = {b["id"]: b for b in blocks}

    def outline_path(block: dict) -> List[str]:
        path, seen = [], set()
        parent = (block.get("outline") or {}).get("parentId")
        while parent and parent in by_id and parent not in seen:
            seen.add(parent)
            node = by_id[parent]
            if node["type"] == "heading":
                break
            path.append((node.get("outline") or {}).get("body") or node.get("text", "").strip())
            parent = (node.get("outline") or {}).get("parentId")
        return list(reversed(path))

    chunks: List[dict] = []
    current: Optional[dict] = None

    def start(block: dict):
        nonlocal current
        current = {"headingPath": list(block.get("headingPath", [])) + ([block["text"].strip()]
                                                                        if block["type"] == "heading" else []),
                   "outlinePath": outline_path(block), "blocks": [], "parts": [], "tables": [], "pages": set(),
                   "chars": 0}
        chunks.append(current)

    def add(block: dict, text: str, owned_ids: Iterable[str], table_info: Optional[dict] = None):
        current["blocks"].append(block["id"])
        current["pages"].add(block.get("page", 0))
        if text:
            current["parts"].append(text)
            current["chars"] += len(text)
        current.setdefault("owned", []).extend(owned_ids)
        if table_info:
            current["tables"].append(table_info)

    for block in blocks:
        text = render(block)
        if block["type"] == "table" and block.get("role") != "layout":
            info = tables_by_id.get(block["id"], {})
            sem = info.get("semantics") or {}
            rows = table_rows(block)
            header_rows = sem.get("headerRows") or 1
            caption = (sem.get("unit") or {}).get("caption", "")
            md = table_markdown(rows, header_rows)
            owned = [block["id"]] + [c["id"] for c in block.get("cells", [])]
            for cell in block.get("cells", []):
                for child in cell.get("tables", []):
                    owned += [child["id"]] + [c["id"] for c in child.get("cells", [])]
            table_info = {"blockId": block["id"], "label": info.get("label"), "records": sem.get("records", [])}
            if len(md) > chars * 2 and len(rows) > header_rows + 1:
                body = rows[header_rows:]
                per = max(1, int(len(body) * chars / max(len(md), 1)))
                parts = [body[k:k + per] for k in range(0, len(body), per)]
                for n, part in enumerate(parts, 1):
                    if current is None or current["chars"] > 0:
                        start(block)
                    piece = (caption + "\n" if caption else "") + table_markdown(rows[:header_rows] + part, header_rows)
                    add(block, piece, owned, {**table_info, "part": f"{n}/{len(parts)}"})
                    current["part"] = f"{n}/{len(parts)}"
                current = None  # 나눈 표 뒤에는 새 청크
                continue
            text = (caption + "\n" if caption else "") + md
            if current is None or (current["chars"] + len(text) > chars and current["chars"] > 0):
                start(block)
            add(block, text, owned, table_info)
            continue
        if block["type"] == "table":  # 레이아웃 표: 글 없이 ID 만
            if current is None:
                start(block)
            add(block, "", [block["id"]])  # 칸 값은 풀린 문단(cellId)이 가져간다
            continue
        if current is None or block["type"] == "heading" or \
                (current["chars"] > 0 and current["chars"] + len(text) > chars) or \
                (_is_top_item(block) and current["chars"] > chars * 0.6):
            start(block)
        add(block, text, [block["id"]] + ([block["cellId"]] if block.get("cellId") else []))

    out = []
    for k, chunk in enumerate(chunks, 1):
        values, seen = [], set()
        for block_id in chunk.get("owned", []):
            for v in values_by_block.get(block_id, []):
                key = json.dumps(v, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    values.append(v)
        text = "\n\n".join(chunk["parts"])
        entry = {"schemaVersion": "1", "id": f"c{k:04d}", "headingPath": chunk["headingPath"],
                 "outlinePath": chunk["outlinePath"],
                 "pages": sorted(chunk["pages"]),
                 "blockIds": chunk["blocks"], "chars": len(text), "text": text, "values": values,
                 "tables": chunk["tables"], "untrustedContent": True}
        if chunk.get("part"):
            entry["part"] = chunk["part"]
        out.append(entry)
    return out
