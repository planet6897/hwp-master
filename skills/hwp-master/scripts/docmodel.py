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
headerFooter(머리말/꼬리말).
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


def _direct_tables(p) -> List:
    """칸 문단에 직접 든 표(그 안의 더 깊은 표는 빼고)."""
    return [c for c in _controls(p) if _tag(c) == "tbl"]


def table_lines(table: dict) -> List[str]:
    """표를 행마다 ' | ' 로 이은 줄들 (칸 글 안에 안쪽 표를 펼쳐 넣을 때)."""
    rows: Dict[int, List[Tuple[int, str]]] = {}
    for cell in table["cells"]:
        rows.setdefault(cell["row"], []).append((cell["col"], cell["text"].replace("\n", " ")))
    return [" | ".join(text for _, text in sorted(cols)) for _, cols in sorted(rows.items())]


def parse_table(tbl, tid: str, counts: Dict[str, int], depth: int = 0) -> dict:
    """표 하나. 칸 안의 표(표 안의 표)는 칸의 tables 로 재귀해 넣고 ID 를 이어 붙인다(…r1c2.t0.r0c0).

    rhwp export-tables 는 중첩 표를 내보내지 않는다(edwardkim/rhwp#8988). 여기서는 HWPX 를 직접 읽어 잃지 않는다.
    """
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
                for tbl_el in _direct_tables(sp):
                    child = parse_table(tbl_el, f"{cid}.t{len(nested)}", counts, depth + 1)
                    child["cellParagraph"] = k
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
                "text": "\n".join(lines).strip(),
                "hasNestedTable": bool(nested),
            }
            if nested:
                cell["paragraphs"] = paragraphs
                cell["tables"] = nested
            cells.append(cell)
    counts["cell"] += len(cells)
    if depth:
        counts["nestedTable"] += 1
    return {"id": tid, "rows": _int(tbl.get("rowCnt")), "cols": _int(tbl.get("colCnt")), "depth": depth,
            "cells": cells}


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


def build_from_hwpx(hwpx: Path, pages: Optional[Dict[Tuple[int, int], int]] = None) -> dict:
    pages = pages or {}
    with zipfile.ZipFile(hwpx) as z:
        names = z.namelist()
        kinds = paragraph_kinds(z.read("Contents/header.xml")) if "Contents/header.xml" in names else {}
        sections = [(i, ET.fromstring(z.read(n))) for i, n in enumerate(_section_names(names))]

    blocks: List[dict] = []
    heading_stack: List[Tuple[int, str, str]] = []  # (level, id, text)
    counts = {"heading": 0, "paragraph": 0, "list": 0, "table": 0, "nestedTable": 0, "image": 0, "note": 0,
              "headerFooter": 0, "cell": 0}

    def path_for() -> dict:
        return {"headingPath": [t for _, _, t in heading_stack],
                "parentHeadingId": heading_stack[-1][1] if heading_stack else None}

    last_page = 0
    for section, root in sections:
        top = [c for c in root if _tag(c) == "p"]
        for index, p in enumerate(top):
            pid = f"s{section}.p{index}"
            page = pages.get((section, index), last_page)
            last_page = page
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

            ordinal = {"tbl": 0, "pic": 0, "note": 0, "hf": 0}
            for ctrl in _controls(p):
                name = _tag(ctrl)
                if name == "tbl":
                    table = parse_table(ctrl, f"{pid}.t{ordinal['tbl']}", counts)
                    ordinal["tbl"] += 1
                    counts["table"] += 1
                    blocks.append({"id": table.pop("id"), "type": "table", **base, **table, **path_for()})
                elif name == "pic":
                    blocks.append({"id": f"{pid}.g{ordinal['pic']}", "type": "image", **base, **path_for()})
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
                                   "text": "\n".join(x for x in lines if x.strip()).strip(), **base})
                    ordinal["hf"] += 1
                    counts["headerFooter"] += 1
                else:
                    note_text = "\n".join(_text_of(sp) for sp in _sub_paragraphs(ctrl)).strip()
                    blocks.append({"id": f"{pid}.n{ordinal['note']}", "type": "note",
                                   "noteType": "footnote" if name == "footNote" else "endnote",
                                   "text": note_text, **base, **path_for()})
                    ordinal["note"] += 1
                    counts["note"] += 1
    return {"schemaVersion": SCHEMA_VERSION, "untrustedContent": True, "counts": counts, "blocks": blocks}


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
        for block in document.get("blocks", []):
            key = (block["section"], block["paragraph"])
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
        k = self.control_to_ordinal.get((*key, _int(control)))
        found = self.tables.get(key, [])
        if k is None:
            k = 0 if len(found) == 1 else None
        if k is not None and k < len(found):
            return found[k]
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

    def _follow(self, cell: dict, step: dict) -> Optional[dict]:
        """fields location.nested 처럼 경로가 있으면 한 단계 안쪽 칸으로."""
        tables = [t for t in cell.get("tables", []) if t.get("cellParagraph") == _int(step.get("paragraph"))]
        tables = tables or cell.get("tables", [])
        if len(tables) != 1:
            return None
        idx = _int(step.get("cell"), -1)
        return tables[0]["cells"][idx] if 0 <= idx < len(tables[0]["cells"]) else None

    def locate(self, section, paragraph, cell: Optional[dict] = None, text: str = "",
               path: Optional[List[dict]] = None) -> Optional[str]:
        """본문 문단이면 문단 ID, 셀 주소({control, cell, paragraph})가 있으면 셀 ID.

        path(바깥 → 안쪽 칸 주소 목록)를 주면 그 경로를 따라가고, 없고 text 가 있으면 글로 가장 안쪽 칸을 찾는다.
        """
        if path:
            found = self.cell(section, paragraph, path[0].get("control"), path[0].get("cell"))
            for step in path[1:]:
                if found is None:
                    break
                found = self._follow(found, step)
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
