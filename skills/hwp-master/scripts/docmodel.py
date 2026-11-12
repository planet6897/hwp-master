#!/usr/bin/env python3
"""통합 문서 모델(document.json): HWP/HWPX 를 읽기 순서의 블록 목록으로 만들고 고정 ID 를 붙인다.

블록 ID (rhwp 의 구역·문단 번호와 같다)
  s0.p12            구역 0 의 최상위 문단 12
  s0.p5.t0          문단 5 안의 첫 번째 표
  s0.p5.t0.r2c1     그 표의 2행 1열 셀 (병합 셀은 왼쪽 위 칸 주소)
  s0.p7.g0          문단 7 안의 첫 번째 그림
  s0.p7.n0          문단 7 의 첫 번째 각주(미주)
  s0.p0.h0          문단 0 에 붙은 첫 번째 머리말/꼬리말 (그 안의 표는 이 블록에 이어진다)

블록 종류: heading(개요 문단, level 1~) · paragraph · list(번호·글머리표 문단) · table · image · note ·
headerFooter(머리말/꼬리말) · textBox(글상자 — 안의 문단·표는 fromTextBox 블록 …x0.p1).

표에는 role 이 붙는다. layout(1×1 상자·투명 표·본문만 담은 한 열 표)이면 칸 문단을 바로 뒤에 일반 블록으로 풀어
놓는다(fromLayoutTable·cellId, ID 는 s0.p3.t0.r0c0.p1). 표 블록과 칸 ID 는 그대로 남는다.
글이 없는 빈 문단은 블록으로 만들지 않는다(ID 는 문단 번호라 그대로 유지된다).
쪽(page)은 rhwp JSON 관례대로 0부터 센다.

HWP·HML 은 rhwp export-hwpx 로 임시 HWPX 를 만든 뒤 같은 방법으로 읽는다(원본 무수정).
문단 번호는 HWPX 최상위 <hp:p> 순서이고, 표 셀 번호는 <hp:tc> 순서라 rhwp search·fields·extract-data 의
위치(section·paragraph·cell)와 그대로 맞는다.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

SCHEMA_VERSION = "1"


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _text_of(p) -> str:
    """문단의 글. 안에 든 표·글상자(subList)·각주의 글은 빼고, 줄 바꿈·탭은 살린다."""
    out: List[str] = []

    def walk(el):
        for child in el:
            name = _tag(child)
            if name in ("tbl", "subList", "footNote", "endNote", "pic", "drawText"):
                continue
            if name == "t":
                if child.text:
                    out.append(child.text)
                for sub in child:
                    sub_name = _tag(sub)
                    if sub_name == "lineBreak":
                        out.append("\n")
                    elif sub_name == "tab":
                        out.append("\t")
                    if sub.tail:
                        out.append(sub.tail)
            elif name == "lineBreak":
                out.append("\n")
            elif name == "tab":
                out.append("\t")
            else:
                walk(child)

    walk(p)
    return "".join(out)


def _controls(p) -> Iterator:
    """문단에 직접 든 표·그림·각주/미주를 나오는 차례대로 (다른 표·글상자 안의 것은 빼고)."""
    for child in p:
        name = _tag(child)
        if name in ("tbl", "pic", "footNote", "endNote", "header", "footer"):
            yield child
            continue
        if name in ("subList", "drawText"):
            continue
        yield from _controls(child)


def _sub_paragraphs(el) -> List:
    """셀·각주의 subList 바로 아래 문단들."""
    paras = []
    for child in el:
        if _tag(child) == "subList":
            paras.extend(c for c in child if _tag(c) == "p")
    return paras


def _child(el, name: str):
    for c in el:
        if _tag(c) == name:
            return c
    return None


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def paragraph_kinds(header_xml: bytes) -> Dict[str, Tuple[str, int]]:
    """paraPr id → (heading 종류 OUTLINE/NUMBER/BULLET/NONE, level 0~)."""
    kinds = {}
    root = ET.fromstring(header_xml)
    for el in root.iter():
        if _tag(el) == "paraPr":
            heading = _child(el, "heading")
            if heading is None:
                for sub in el.iter():
                    if _tag(sub) == "heading":
                        heading = sub
                        break
            if heading is not None:
                kinds[el.get("id", "")] = (heading.get("type", "NONE"), _int(heading.get("level")))
    return kinds


def border_fills(header_xml: bytes) -> Dict[str, bool]:
    """borderFill id → 테두리가 하나라도 보이는가 (네 변이 모두 NONE 이면 False = 투명)."""
    visible = {}
    root = ET.fromstring(header_xml)
    for el in root.iter():
        if _tag(el) == "borderFill":
            sides = [c.get("type", "NONE") for c in el if _tag(c) in ("leftBorder", "rightBorder", "topBorder",
                                                                      "bottomBorder")]
            visible[el.get("id", "")] = any(s.upper() != "NONE" for s in sides)
    return visible


# 레이아웃 표 판정. 한글 문서는 제목 상자(1×1)·투명 표(테두리 없음)·본문만 담은 한 열 표로 모양을 잡는 일이 많다.
# 이런 표는 데이터가 아니라 틀이라, 표로 다루면 AI 가 본문을 표 데이터로 오해한다(지디넷 2026-11-10, 행안부 가이드라인).
LAYOUT_TEXT_MIN = 30  # 한 열 표의 칸이 이만큼 긴 글이면 본문으로 본다


def classify_table(table: dict) -> Tuple[str, List[str]]:
    """('layout' | 'data', 근거 목록)."""
    reasons = []
    cells = table["cells"]
    if table["rows"] * table["cols"] <= 1 or len(cells) <= 1:
        reasons.append("single-cell")
    elif table["rows"] == 1:
        # 한 행 표는 머리 행과 값 행이 없어 열 데이터가 아니다(번호 상자·로고 줄·"보도시점 | 배포즉시" 같은 항목-값 줄).
        # 항목-값은 #13 이 역할과 상관없이 칸에서 꺼낸다.
        reasons.append("single-row")
    known = [c["border"] for c in cells if c.get("border") is not None]
    if known and not any(known):
        reasons.append("borderless")
    texts = [c["text"] for c in cells if c["text"].strip()]
    # 한 열·4행 이하에 긴 글(본문)이 든 칸이 있으면 제목+요약 상자로 본다. 짧은 항목만 있는 한 열 표는 데이터다.
    if (table["cols"] == 1 and len(cells) > 1 and texts and table["rows"] <= 4
            and max(len(t) for t in texts) >= LAYOUT_TEXT_MIN):
        reasons.append("text-column")
    return ("layout" if reasons else "data"), reasons


def page_map(dump_pages: dict) -> Dict[Tuple[int, int], int]:
    """(구역, 문단) → 처음 나오는 쪽 (0부터)."""
    pages: Dict[Tuple[int, int], int] = {}
    for page in dump_pages.get("pages", []):
        section = _int(page.get("section"))
        index = _int(page.get("pageIndex"))
        for column in page.get("columns", []):
            for item in column.get("items", []):
                if "paraIndex" in item:
                    pages.setdefault((section, _int(item["paraIndex"])), index)
    return pages


def _section_names(names: List[str]) -> List[str]:
    found = [n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)]
    return sorted(found, key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))


def control_indices(p) -> Dict[int, int]:
    """문단 안 컨트롤의 rhwp control 번호: run 자식 가운데 글(t)을 뺀 순번. id(요소) → 번호."""
    index, k = {}, 0
    for run_el in (c for c in p if _tag(c) == "run"):
        for child in run_el:
            if _tag(child) == "t":
                continue
            index[id(child)] = k
            for inner in child:
                index.setdefault(id(inner), k)
            k += 1
    return index


def _direct_tables(p) -> List:
    """칸 문단에 직접 든 표(그 안의 더 깊은 표는 빼고)."""
    return [c for c in _controls(p) if _tag(c) == "tbl"]


def table_lines(table: dict) -> List[str]:
    """표를 행마다 ' | ' 로 이은 줄들 (칸 글 안에 안쪽 표를 펼쳐 넣을 때)."""
    rows: Dict[int, List[Tuple[int, str]]] = {}
    for cell in table["cells"]:
        rows.setdefault(cell["row"], []).append((cell["col"], cell["text"].replace("\n", " ")))
    return [" | ".join(text for _, text in sorted(cols)) for _, cols in sorted(rows.items())]


def parse_table(tbl, tid: str, counts: Dict[str, int], depth: int = 0,
                borders: Optional[Dict[str, bool]] = None) -> dict:
    """표 하나. 칸 안의 표(표 안의 표)는 칸의 tables 로 재귀해 넣고 ID 를 이어 붙인다(…r1c2.t0.r0c0).

    rhwp export-tables 는 중첩 표를 내보내지 않는다(edwardkim/rhwp#8988). 여기서는 HWPX 를 직접 읽어 잃지 않는다.
    표마다 role(layout|data)·roleReasons 를 붙인다(classify_table).
    """
    borders = borders or {}
    cells = []
    for tr in (c for c in tbl if _tag(c) == "tr"):
        for tc in (c for c in tr if _tag(c) == "tc"):
            addr, span = _child(tc, "cellAddr"), _child(tc, "cellSpan")
            row = _int(addr.get("rowAddr")) if addr is not None else 0
            col = _int(addr.get("colAddr")) if addr is not None else 0
            cid = f"{tid}.r{row}c{col}"
            paragraphs, nested, lines = [], [], []
            for k, sp in enumerate(_sub_paragraphs(tc)):
                own = _text_of(sp)
                inner = []
                controls = control_indices(sp)
                for tbl_el in _direct_tables(sp):
                    child = parse_table(tbl_el, f"{cid}.t{len(nested)}", counts, depth + 1, borders)
                    child["cellParagraph"] = k
                    child["control"] = controls.get(id(tbl_el))
                    nested.append(child)
                    inner.append(child["id"])
                paragraphs.append({"text": own, "tables": inner})
                if own.strip():
                    lines.append(own)
                for child_id in inner:
                    lines.extend(table_lines(next(t for t in nested if t["id"] == child_id)))
            cell = {
                "id": cid, "index": len(cells), "row": row, "col": col,
                "rowSpan": _int(span.get("rowSpan"), 1) if span is not None else 1,
                "colSpan": _int(span.get("colSpan"), 1) if span is not None else 1,
                "header": tc.get("header") == "1",
                "border": borders.get(tc.get("borderFillIDRef", "")),
                "text": "\n".join(lines).strip(),
                "hasNestedTable": bool(nested),
                "paragraphs": paragraphs,
            }
            if nested:
                cell["tables"] = nested
            cells.append(cell)
    counts["cell"] += len(cells)
    if depth:
        counts["nestedTable"] += 1
    table = {"id": tid, "rows": _int(tbl.get("rowCnt")), "cols": _int(tbl.get("colCnt")), "depth": depth,
             "cells": cells}
    table["role"], table["roleReasons"] = classify_table(table)
    counts["layoutTable" if table["role"] == "layout" else "dataTable"] += 1
    return table


def unwrap_layout(table: dict, base: dict, path: dict) -> List[dict]:
    """레이아웃 표의 칸 문단을 일반 블록으로 푼다(읽기 순서: 행 → 열 → 칸 문단).

    풀린 문단 ID 는 칸 ID 뒤에 칸 문단 번호를 붙인다(s0.p3.t0.r0c0.p1). 칸 안의 표는 표 블록으로 내놓고,
    그 표도 레이아웃이면 다시 푼다. 칸 ID 는 표 블록 안에 그대로 남아 값·누름틀의 blockId 가 계속 맞는다.
    """
    out = []
    for cell in sorted(table["cells"], key=lambda c: (c["row"], c["col"])):
        for k, para in enumerate(cell.get("paragraphs", [])):
            if para["text"].strip():
                out.append({"id": f"{cell['id']}.p{k}", "type": "paragraph", "text": para["text"],
                            "fromLayoutTable": table["id"], "cellId": cell["id"], **base, **path})
            for child_id in para["tables"]:
                child = next(t for t in cell.get("tables", []) if t["id"] == child_id)
                out.append({"type": "table", **base, **child, "fromLayoutTable": table["id"], **path})
                if child["role"] == "layout":
                    out.extend(unwrap_layout(child, base, path))
    return out


def iter_cells(table: dict) -> Iterator[dict]:
    """표의 모든 칸(안쪽 표의 칸까지)."""
    for cell in table.get("cells", []):
        yield cell
        for child in cell.get("tables", []):
            yield from iter_cells(child)


def iter_tables(table: dict) -> Iterator[dict]:
    """표 자신과 그 안의 모든 중첩 표."""
    yield table
    for cell in table.get("cells", []):
        for child in cell.get("tables", []):
            yield from iter_tables(child)


# 개조식·공문 항목 기호. (종류, 정규식, 관례 순위) — 순위가 작을수록 상위다.
# 공문서 항목은 1. → 가. → 1) → 가) → (1) → (가) → ① 순이고, 개조식 보고서는 □ → ○ → - → · 순이다.
_SP = r"[\s 　]"
OUTLINE_MARKERS = [
    ("Ⅰ.", re.compile(rf"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?){_SP}"), 0),
    ("1.", re.compile(rf"^(\d{{1,2}}\.)(?!\d){_SP}"), 1),
    ("□", re.compile(rf"^([□■◆◇▣▶►]){_SP}?"), 2),
    ("가.", re.compile(rf"^([가나다라마바사아자차카타파하]\.){_SP}"), 3),
    ("○", re.compile(rf"^([○◦●◎]|ㅇ(?={_SP}|\()){_SP}?"), 4),
    ("1)", re.compile(rf"^(\d{{1,2}}\)){_SP}"), 5),
    ("-", re.compile(rf"^([-‐–—―]){_SP}"), 6),
    ("가)", re.compile(rf"^([가나다라마바사아자차카타파하]\)){_SP}"), 7),
    ("·", re.compile(rf"^([·∙•ㆍ]){_SP}?"), 8),
    ("(1)", re.compile(rf"^(\(\d{{1,2}}\)){_SP}"), 9),
    ("(가)", re.compile(rf"^(\([가나다라마바사아자차카타파하]\)){_SP}"), 10),
    ("①", re.compile(rf"^([①-⑳]){_SP}?"), 11),
]
NOTE_MARKER = re.compile(rf"^(※|\*{{1,3}}){_SP}?")
_RANK = {name: rank for name, _, rank in OUTLINE_MARKERS}


def outline_marker(text: str) -> Optional[Tuple[str, str, str]]:
    """문단 앞 항목 기호 → (종류, 기호 원문, 기호 뒤 글). 주석(※·*)은 종류 'note'."""
    stripped = text.lstrip(" \t 　")
    m = NOTE_MARKER.match(stripped)
    if m:
        return "note", m.group(1), stripped[m.end():].strip()
    for name, pattern, _ in OUTLINE_MARKERS:
        m = pattern.match(stripped)
        if m:
            body = stripped[m.end():].strip()
            return (name, m.group(1), body) if body else None
    return None


def assign_outline(blocks: List[dict]) -> int:
    """개조식·공문 기호로 문단 계층을 매긴다. 블록에 outline{marker, kind, level, parentId, body} 를 붙인다.

    새 기호 종류가 나오면 관례 순위로 자리를 찾는다(이미 쌓인 더 하위 종류는 닫는다). 개요 제목(heading)이 나오면
    계층을 새로 시작한다. ※·* 주석은 바로 앞 항목의 한 단계 아래에 붙고 계층을 쌓지 않는다.
    """
    stack: List[Tuple[str, str]] = []  # (종류, 블록 ID)
    count = 0
    heading_id: Optional[str] = None
    for block in blocks:
        if block["type"] == "heading":
            stack, heading_id = [], block["id"]
            continue
        if block["type"] not in ("paragraph", "list") or not block.get("text"):
            continue
        found = outline_marker(block["text"])
        if not found:
            continue
        kind, marker, body = found
        if kind == "note":
            parent = stack[-1][1] if stack else heading_id
            block["outline"] = {"marker": marker, "kind": "note", "level": len(stack) + 1, "parentId": parent,
                                "body": body}
            count += 1
            continue
        names = [k for k, _ in stack]
        if kind in names:
            del stack[names.index(kind):]
        else:
            while stack and _RANK[stack[-1][0]] > _RANK[kind]:
                stack.pop()
        parent = stack[-1][1] if stack else heading_id
        stack.append((kind, block["id"]))
        block["outline"] = {"marker": marker, "kind": kind, "level": len(stack), "parentId": parent, "body": body}
        count += 1
    return count


def outline_tree(blocks: List[dict]) -> List[dict]:
    """개요 제목과 개조식 항목을 부모-자식 트리로 (outline.json)."""
    nodes: Dict[str, dict] = {}
    roots: List[dict] = []
    for block in blocks:
        if block["type"] == "heading":
            node = {"id": block["id"], "kind": "heading", "level": block["level"], "text": block["text"].strip(),
                    "page": block["page"], "children": []}
            parent = nodes.get(block.get("parentHeadingId") or "")
        elif block.get("outline"):
            o = block["outline"]
            node = {"id": block["id"], "kind": o["kind"], "marker": o["marker"], "level": o["level"],
                    "text": o["body"], "page": block["page"], "children": []}
            parent = nodes.get(o.get("parentId") or "")
        else:
            continue
        nodes[node["id"]] = node
        (parent["children"] if parent else roots).append(node)
    return roots


def build_from_hwpx(hwpx: Path, pages: Optional[Dict[Tuple[int, int], int]] = None) -> dict:
    pages = pages or {}
    try:
        return _build_from_hwpx(hwpx, pages)
    except (ET.ParseError, zipfile.BadZipFile, KeyError) as exc:
        return {"_error": f"HWPX 를 읽지 못했습니다(암호 문서이거나 손상): {exc}"}


def _build_from_hwpx(hwpx: Path, pages: Dict[Tuple[int, int], int]) -> dict:
    with zipfile.ZipFile(hwpx) as z:
        names = z.namelist()
        header = z.read("Contents/header.xml") if "Contents/header.xml" in names else b""
        kinds = paragraph_kinds(header) if header else {}
        borders = border_fills(header) if header else {}
        sections = [(i, ET.fromstring(z.read(n))) for i, n in enumerate(_section_names(names))]

    blocks: List[dict] = []
    heading_stack: List[Tuple[int, str, str]] = []  # (level, id, text)
    counts = {"heading": 0, "paragraph": 0, "list": 0, "table": 0, "nestedTable": 0, "layoutTable": 0,
              "dataTable": 0, "image": 0, "note": 0, "headerFooter": 0, "cell": 0}

    def path_for() -> dict:
        return {"headingPath": [t for _, _, t in heading_stack],
                "parentHeadingId": heading_stack[-1][1] if heading_stack else None}

    last_page = 0
    empty_runs: List[dict] = []  # 빈 문단 2개 이상 연속 — 빈 줄로 띄운 간격(AI 가독성 진단)
    run: Optional[dict] = None
    for section, root in sections:
        top = [c for c in root if _tag(c) == "p"]
        for index, p in enumerate(top):
            pid = f"s{section}.p{index}"
            page = pages.get((section, index), last_page)
            last_page = page
            if not _text_of(p).strip() and not any(True for _ in _controls(p)) \
                    and not any(_tag(x) == "drawText" for x in p.iter()):
                counts["emptyParagraph"] = counts.get("emptyParagraph", 0) + 1
                if run and run["section"] == section and run["end"] == index - 1:
                    run["end"], run["length"] = index, run["length"] + 1
                else:
                    run = {"start": pid, "section": section, "end": index, "length": 1, "page": page}
                    empty_runs.append(run)
                continue
            text = _text_of(p)
            kind, level = kinds.get(p.get("paraPrIDRef", ""), ("NONE", 0))
            base = {"section": section, "paragraph": index, "page": page}
            if text.strip():
                if kind == "OUTLINE":
                    lvl = level + 1
                    while heading_stack and heading_stack[-1][0] >= lvl:
                        heading_stack.pop()
                    blocks.append({"id": pid, "type": "heading", "level": lvl, "text": text, **base, **path_for()})
                    heading_stack.append((lvl, pid, text.strip()))
                    counts["heading"] += 1
                elif kind in ("NUMBER", "BULLET"):
                    blocks.append({"id": pid, "type": "list", "listType": kind.lower(), "level": level + 1,
                                   "text": text, **base, **path_for()})
                    counts["list"] += 1
                else:
                    blocks.append({"id": pid, "type": "paragraph", "text": text, **base, **path_for()})
                    counts["paragraph"] += 1

            # rhwp 의 control 번호 = 문단 run 자식 가운데 글(t)을 뺀 순번(secPr·ctrl·tbl·rect…). 블록에 그대로 남긴다.
            index_of, shapes, k = {}, [], 0
            for run_el in (c for c in p if _tag(c) == "run"):
                for child in run_el:
                    if _tag(child) == "t":
                        continue
                    index_of[id(child)] = k
                    for inner in child:
                        index_of.setdefault(id(inner), k)  # ctrl > header·footNote …
                    if _tag(child) not in ("tbl", "pic", "ctrl", "secPr") and \
                            any(_tag(x) == "drawText" for x in child.iter()):
                        shapes.append((k, child))
                    k += 1

            ordinal = {"tbl": 0, "pic": 0, "note": 0, "hf": 0}
            for ctrl in _controls(p):
                name = _tag(ctrl)
                cbase = {**base, "control": index_of.get(id(ctrl))}
                if name == "tbl":
                    table = parse_table(ctrl, f"{pid}.t{ordinal['tbl']}", counts, borders=borders)
                    ordinal["tbl"] += 1
                    counts["table"] += 1
                    blocks.append({"type": "table", **cbase, **table, **path_for()})
                    if table["role"] == "layout":
                        blocks.extend(unwrap_layout(table, base, path_for()))
                elif name == "pic":
                    blocks.append({"id": f"{pid}.g{ordinal['pic']}", "type": "image", **cbase, **path_for()})
                    ordinal["pic"] += 1
                    counts["image"] += 1
                elif name in ("header", "footer"):
                    lines = []
                    for sp in _sub_paragraphs(ctrl):
                        lines.append(_text_of(sp))
                        for e in sp.iter():  # 머리말 안 표의 글도 함께
                            if _tag(e) == "tc":
                                lines.extend(_text_of(x) for x in _sub_paragraphs(e))
                    blocks.append({"id": f"{pid}.h{ordinal['hf']}", "type": "headerFooter", "kind": name,
                                   "text": "\n".join(x for x in lines if x.strip()).strip(), **cbase})
                    ordinal["hf"] += 1
                    counts["headerFooter"] += 1
                else:
                    note_text = "\n".join(_text_of(sp) for sp in _sub_paragraphs(ctrl)).strip()
                    blocks.append({"id": f"{pid}.n{ordinal['note']}", "type": "note",
                                   "noteType": "footnote" if name == "footNote" else "endnote",
                                   "text": note_text, **cbase, **path_for()})
                    ordinal["note"] += 1
                    counts["note"] += 1

            # 글상자(그리기 개체의 drawText): 상자 블록 하나 + 안의 문단·표를 풀어 놓은 블록(fromTextBox).
            # 보도자료 머리 상자·목차 상자 같은 글이 여기에 들어 있다.
            for n, (cidx, shape) in enumerate(shapes):
                tbid = f"{pid}.x{n}"
                blocks.append({"id": tbid, "type": "textBox", "shape": _tag(shape), **base, "control": cidx,
                               **path_for()})
                counts["textBox"] = counts.get("textBox", 0) + 1
                j = 0
                for draw in (x for x in shape.iter() if _tag(x) == "drawText"):
                    for sp in _sub_paragraphs(draw):
                        text = _text_of(sp)
                        if text.strip():
                            blocks.append({"id": f"{tbid}.p{j}", "type": "paragraph", "text": text,
                                           "fromTextBox": tbid, **base, **path_for()})
                        controls = control_indices(sp)
                        for m, tbl_el in enumerate(_direct_tables(sp)):
                            table = parse_table(tbl_el, f"{tbid}.p{j}.t{m}", counts, borders=borders)
                            table["control"] = controls.get(id(tbl_el))
                            counts["table"] += 1
                            blocks.append({"type": "table", **base, **table, "fromTextBox": tbid, **path_for()})
                        j += 1
    counts["outlineItem"] = assign_outline(blocks)
    runs = [{k: r[k] for k in ("start", "length", "page")} for r in empty_runs if r["length"] >= 2]
    return {"schemaVersion": SCHEMA_VERSION, "untrustedContent": True, "counts": counts, "blocks": blocks,
            "emptyRuns": runs}


def build_document(rhwp: str, src: Path, dump_pages: Optional[dict] = None) -> dict:
    """src(HWP/HWPX/HML)의 문서 모델. 실패하면 {'_error': ...}."""
    pages = page_map(dump_pages or {})
    if src.suffix.lower() == ".hwpx":
        return build_from_hwpx(src, pages)
    with tempfile.TemporaryDirectory() as tmp:
        hwpx = Path(tmp) / (src.stem + ".hwpx")
        proc = subprocess.run([rhwp, "export-hwpx", str(src), str(hwpx)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        if proc.returncode != 0 or not hwpx.exists():
            return {"_error": f"export-hwpx exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}"}
        return build_from_hwpx(hwpx, pages)


class BlockIndex:
    """rhwp 위치(section·paragraph·control·cell)를 document.json 블록 ID 로 바꾼다."""

    def __init__(self, document: dict, tables: Optional[List[dict]] = None):
        self.paragraphs = set()
        self.tables: Dict[Tuple[int, int], List[dict]] = {}
        self.header_footers: Dict[Tuple[int, int], List[dict]] = {}
        self.textboxes: Dict[Tuple[int, int, Optional[int]], dict] = {}
        self.textbox_tables: Dict[str, List[dict]] = {}
        self.ids = set()
        for block in document.get("blocks", []):
            key = (block["section"], block["paragraph"])
            self.ids.add(block["id"])
            if block["type"] == "textBox":
                self.textboxes[(block["section"], block["paragraph"], block.get("control"))] = block
                continue
            if block["type"] == "table" and block.get("fromTextBox"):
                self.textbox_tables.setdefault(block["fromTextBox"], []).append(block)
                continue
            if block.get("fromLayoutTable") or block.get("fromTextBox"):
                continue  # 레이아웃 표·글상자에서 풀린 블록 — 위치는 바깥 칸·글상자로 찾는다
            if block["type"] == "table":
                self.tables.setdefault(key, []).append(block)
            elif block["type"] in ("heading", "paragraph", "list"):
                self.paragraphs.add(key)
            elif block["type"] == "headerFooter":
                self.header_footers.setdefault(key, []).append(block)
        # rhwp 의 control 번호(문단 안 모든 컨트롤 중 순번) → 그 문단 안 표 순번
        self.control_to_ordinal: Dict[Tuple[int, int, int], int] = {}
        by_para: Dict[Tuple[int, int], List[int]] = {}
        for t in tables or []:
            by_para.setdefault((_int(t.get("section")), _int(t.get("paragraph"))), []).append(_int(t.get("control")))
        for key, controls in by_para.items():
            for k, control in enumerate(sorted(controls)):
                self.control_to_ordinal[(*key, control)] = k

    def table(self, section, paragraph, control) -> Optional[dict]:
        """표 블록. 머리말/꼬리말 안의 표면 그 headerFooter 블록."""
        key = (_int(section), _int(paragraph))
        found = self.tables.get(key, [])
        exact = [t for t in found if t.get("control") is not None and t.get("control") == _int(control, -1)]
        if len(exact) == 1:
            return exact[0]  # docmodel 이 센 control 번호가 rhwp 와 같다
        k = self.control_to_ordinal.get((*key, _int(control)))
        if k is None:
            k = 0 if len(found) == 1 else None
        if k is not None and k < len(found):
            return found[k]
        box = self.textboxes.get((key[0], key[1], _int(control, -1)))
        if box and len(self.textbox_tables.get(box["id"], [])) == 1:
            return self.textbox_tables[box["id"]][0]  # 글상자 안의 표 — rhwp 는 글상자의 control 번호를 준다
        hf = self.header_footers.get(key, [])
        return hf[0] if len(hf) == 1 else None

    def cell(self, section, paragraph, control, cell_index) -> Optional[dict]:
        table = self.table(section, paragraph, control)
        if table is None or table["type"] != "table":
            return None
        idx = _int(cell_index, -1)
        return table["cells"][idx] if 0 <= idx < len(table["cells"]) else None

    @staticmethod
    def _innermost(cell: dict, cell_paragraph, text: str) -> dict:
        """바깥 칸 주소만 있을 때(rhwp search 는 중첩 칸도 최외곽 칸으로 준다, edwardkim/rhwp#8989)
        그 칸 문단에 든 안쪽 표 가운데 글이 맞는 칸이 하나뿐이면 그 칸으로 내려간다."""
        norm = re.sub(r"\s+", "", text or "")
        if not norm or not cell.get("tables"):
            return cell
        own = cell.get("paragraphs", [])
        k = _int(cell_paragraph, -1)
        if 0 <= k < len(own) and norm in re.sub(r"\s+", "", own[k]["text"]):
            return cell  # 바깥 칸 자기 글이다
        candidates = [t for t in cell["tables"] if k < 0 or t.get("cellParagraph") == k] or cell["tables"]
        hits = [c for t in candidates for c in iter_cells(t)
                if not c.get("tables") and norm in re.sub(r"\s+", "", c["text"])]
        if len(hits) == 1:
            return hits[0]
        deeper = [c for t in candidates for c in iter_cells(t) if c.get("tables")
                  and norm in re.sub(r"\s+", "", c["text"])]
        return BlockIndex._innermost(deeper[0], -1, text) if len(deeper) == 1 and not hits else cell

    @staticmethod
    def _pick(tables: List[dict], control) -> Optional[dict]:
        """같은 문단의 표 후보에서 control 번호로 고르고, 하나뿐이면 그것."""
        exact = [t for t in tables if t.get("control") is not None and t.get("control") == _int(control, -1)]
        if len(exact) == 1:
            return exact[0]
        return tables[0] if len(tables) == 1 else None

    @staticmethod
    def _cell_at(table: Optional[dict], index) -> Optional[dict]:
        idx = _int(index, -1)
        return table["cells"][idx] if table and 0 <= idx < len(table["cells"]) else None

    def _follow(self, cell: dict, prev: dict, step: dict) -> Optional[dict]:
        """한 단계 안쪽 칸으로. 경로 단계의 paragraph 는 '그 단계 칸 안의 문단'이라(fields location.nested·
        rhwp#8991 cellPath), 안쪽 표는 앞 단계(prev) 칸의 그 문단에서 찾고 이번 단계 control 로 고른다."""
        tables = [t for t in cell.get("tables", []) if t.get("cellParagraph") == _int(prev.get("paragraph"))]
        return self._cell_at(self._pick(tables or cell.get("tables", []), step.get("control")), step.get("cell"))

    def locate(self, section, paragraph, cell: Optional[dict] = None, text: str = "",
               path: Optional[List[dict]] = None) -> Optional[str]:
        """본문 문단이면 문단 ID, 셀 주소({control, cell, paragraph})가 있으면 셀 ID.

        path(바깥 → 안쪽 칸 주소 목록: fields location.nested, rhwp#8991 의 cellPath)를 주면 그 경로를 따라간다.
        중간에 끊기면 거기까지 찾은 가장 안쪽 칸을 준다. path 가 없고 text 가 있으면 글로 가장 안쪽 칸을 찾는다.
        """
        if path:
            first, rest = path[0], list(path[1:])
            if first.get("kind") == "textBox":
                box = self.textboxes.get((_int(section), _int(paragraph), _int(first.get("control"), -1)))
                if box is None:
                    return None
                if not rest:
                    inner = f"{box['id']}.p{_int(first.get('paragraph'))}"
                    return inner if inner in self.ids else box["id"]
                prefix = f"{box['id']}.p{_int(first.get('paragraph'))}.t"
                candidates = [t for t in self.textbox_tables.get(box["id"], []) if t["id"].startswith(prefix)]
                found = self._cell_at(self._pick(candidates, rest[0].get("control")), rest[0].get("cell"))
                if found is None:
                    return box["id"]
                prev, rest = rest[0], rest[1:]
            else:
                found = self.cell(section, paragraph, first.get("control"), first.get("cell"))
                prev = first
            for step in rest:
                if found is None:
                    break
                deeper = self._follow(found, prev, step)
                if deeper is None:
                    break  # 경로가 끊기면 지금까지의 가장 안쪽 칸
                found, prev = deeper, step
            return found["id"] if found else None
        if cell:
            found = self.cell(section, paragraph, cell.get("control"), cell.get("cell"))
            if found and text:
                found = self._innermost(found, cell.get("paragraph"), text)
            return found["id"] if found else None
        key = (_int(section), _int(paragraph))
        return f"s{key[0]}.p{key[1]}" if key in self.paragraphs else None


if __name__ == "__main__":
    import sys

    print(json.dumps(build_from_hwpx(Path(sys.argv[1])), ensure_ascii=False, indent=2))
