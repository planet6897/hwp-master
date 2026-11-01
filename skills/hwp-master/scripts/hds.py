"""hwp-master 문서 명세(HDS) 공용 라이브러리: 검증·규약 검사·조립.

HDS v1 은 `rhwp scaffold` 명세 v1 과 같은 형식이다.

    {"version": "1", "title": str?, "font": str?,
     "page_size": {"width_mm": num, "height_mm": num}?,
     "blocks": [heading | paragraph | table]}

    heading   {"type": "heading", "level": 1..7, "text": str}
    paragraph {"type": "paragraph", "text": str}
    table     {"type": "table", "rows": [[str]], "cell_align"?: str | [str]}

검사 결과는 Finding 목록이다. severity 가 "error" 이면 차단, "warning" 이면 비차단.
검사기는 검사만 하고 명세를 고치지 않는다.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional

HDS_VERSION = "1"
TOP_KEYS = {"version", "title", "font", "page_size", "blocks"}
BLOCK_KEYS = {
    "heading": {"type", "level", "text"},
    "paragraph": {"type", "text"},
    "table": {"type", "rows", "cell_align"},
}
CELL_ALIGNS = {"justify", "left", "center", "right"}

# 제목 앞의 수동 번호: "1.", "1.2.", "가.", "Ⅲ.", "1)", "(가)", "①" 등
MANUAL_NUMBER = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+)*\.(?!\d)"          # 1.  1.2.
    r"|[가-하]\."                     # 가.
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\."             # Ⅲ.
    r"|[IVX]+\.(?=\s)"                # IV.
    r"|\(?\d+\)"                      # 1)  (1)
    r"|\(?[가-하]\)"                  # 가)  (가)
    r"|[①-⑳㉮-㉻]"                    # ① ㉮
    r")"
)

# scaffold 기본 쪽(A4, 좌우 여백 30mm, 표 바깥 여백 1mm×2)과 셀 좌우 안쪽 여백 0.5mm×2
A4_WIDTH_MM = 210.0
SIDE_MARGINS_MM = 60.0
TABLE_OUTER_MM = 2.0
CELL_PAD_MM = 1.0
BASE_EM_MM = 10.0 * 25.4 / 72.0  # 10pt


@dataclass
class Finding:
    severity: str  # "error" | "warning"
    code: str
    message: str
    where: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _err(code, msg, where=""):
    return Finding("error", code, msg, where)


def _warn(code, msg, where=""):
    return Finding("warning", code, msg, where)


def text_em(text: str) -> float:
    """대략적인 글자 폭(em). 전각(한글·한자·전각 기호)=1, 그 밖=0.5."""
    total = 0.0
    for ch in text:
        total += 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.5
    return total


def validate_blocks(blocks, where_prefix: str = "") -> List[Finding]:
    out: List[Finding] = []
    if not isinstance(blocks, list):
        return [_err("E001", "blocks 는 배열이어야 합니다", where_prefix or "blocks")]
    for i, b in enumerate(blocks):
        where = f"{where_prefix}blocks[{i}]"
        if not isinstance(b, dict):
            out.append(_err("E001", "블록은 객체여야 합니다", where))
            continue
        btype = b.get("type")
        if btype not in BLOCK_KEYS:
            out.append(_err("E002", f"지원하지 않는 블록 type {btype!r} (지원: {sorted(BLOCK_KEYS)})", where))
            continue
        unknown = set(b) - BLOCK_KEYS[btype]
        if unknown:
            out.append(_err("E003", f"{btype} 블록의 알 수 없는 필드 {sorted(unknown)}", where))
        if btype in ("heading", "paragraph"):
            text = b.get("text")
            if not isinstance(text, str):
                out.append(_err("E004", f"{btype}.text 는 문자열이어야 합니다", where))
                continue
        if btype == "heading":
            level = b.get("level")
            if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 7:
                out.append(_err("E102", "heading.level 은 1~7 정수여야 합니다", where))
            if not b["text"].strip():
                out.append(_err("E103", "빈 제목", where))
            elif MANUAL_NUMBER.match(b["text"]):
                out.append(_err(
                    "E101",
                    f"제목에 번호를 직접 쓰지 마세요 (개요 자동 번호와 이중 번호가 됨): {b['text']!r}",
                    where,
                ))
        if btype == "paragraph" and not b["text"].strip():
            out.append(_warn("W203", "빈 문단 — 간격 조정용이면 문단 간격 규약을 쓰세요", where))
        if btype == "table":
            out.extend(_validate_table(b, where))
    return out


def _validate_table(b: dict, where: str) -> List[Finding]:
    out: List[Finding] = []
    rows = b.get("rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
        return [_err("E104", "table.rows 는 비어 있지 않은 2차원 배열이어야 합니다", where)]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if not isinstance(cell, str):
                out.append(_err("E004", "표 셀은 문자열이어야 합니다", f"{where}.rows[{r}][{c}]"))
    ncols = max(len(r) for r in rows)
    if ncols == 0:
        return out + [_err("E104", "열이 없는 표", where)]
    if any(len(r) != ncols for r in rows):
        out.append(_warn("W202", f"행마다 열 수가 다릅니다 — 최대 {ncols}열로 빈 셀이 채워집니다", where))
    align = b.get("cell_align")
    if align is not None:
        items = align if isinstance(align, list) else [align]
        if not all(isinstance(a, str) and a in CELL_ALIGNS for a in items):
            out.append(_err("E005", f"cell_align 값은 {sorted(CELL_ALIGNS)} 중 하나(또는 그 목록)", where))
    return out


def lint_table_widths(blocks, page_width_mm: float, where_prefix: str = "") -> List[Finding]:
    """W201: 셀 텍스트가 균등 열 폭 한 줄을 넘는지 추정한다 (M0 F2 임시 경고)."""
    out: List[Finding] = []
    content_mm = page_width_mm - SIDE_MARGINS_MM - TABLE_OUTER_MM
    for i, b in enumerate(blocks if isinstance(blocks, list) else []):
        if not isinstance(b, dict) or b.get("type") != "table" or not isinstance(b.get("rows"), list):
            continue
        rows = [r for r in b["rows"] if isinstance(r, list)]
        ncols = max((len(r) for r in rows), default=0)
        if not ncols:
            continue
        cap_em = (content_mm / ncols - CELL_PAD_MM) / BASE_EM_MM
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if isinstance(cell, str) and text_em(cell) > cap_em:
                    out.append(_warn(
                        "W201",
                        f"셀 텍스트(약 {text_em(cell):.1f}em)가 열 폭 한 줄(약 {cap_em:.1f}em)을 넘습니다 — "
                        "한컴은 줄바꿈하지만 현재 rhwp 미리보기에서는 잘립니다 (M0 F2)",
                        f"{where_prefix}blocks[{i}].rows[{r}][{c}]",
                    ))
    return out


def validate_document(doc) -> List[Finding]:
    if not isinstance(doc, dict):
        return [_err("E001", "HDS 문서는 객체여야 합니다")]
    out: List[Finding] = []
    unknown = set(doc) - TOP_KEYS
    if unknown:
        out.append(_err("E003", f"알 수 없는 최상위 필드 {sorted(unknown)}"))
    if doc.get("version") != HDS_VERSION:
        out.append(_err("E006", f"version 은 {HDS_VERSION!r} 이어야 합니다"))
    ps = doc.get("page_size")
    width = A4_WIDTH_MM
    if ps is not None:
        if not (isinstance(ps, dict) and set(ps) == {"width_mm", "height_mm"}
                and all(isinstance(ps[k], (int, float)) and ps[k] > 0 for k in ps)):
            out.append(_err("E007", "page_size 는 {width_mm, height_mm} 양수여야 합니다"))
        else:
            width = float(ps["width_mm"])
    for key in ("title", "font"):
        if key in doc and not isinstance(doc[key], str):
            out.append(_err("E004", f"{key} 는 문자열이어야 합니다"))
    blocks = doc.get("blocks", [])
    out.extend(validate_blocks(blocks))
    out.extend(lint_table_widths(blocks, width))
    return out


# ---------------------------------------------------------------- 프로젝트 조립

def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def unit_files(project: Path) -> List[Path]:
    return sorted((project / "units").glob("U*.json"))


def assemble(project: Path) -> dict:
    """spec_lock.json + outline.json 순서의 units/*.json → HDS 문서."""
    lock = load_json(project / "spec_lock.json")
    doc = {"version": HDS_VERSION}
    for key in ("title", "font", "page_size"):
        if lock.get(key) is not None:
            doc[key] = lock[key]
    order = _unit_order(project)
    blocks = []
    for uid in order:
        unit = load_json(project / "units" / f"{uid}.json")
        blocks.extend(unit.get("blocks", []))
    doc["blocks"] = blocks
    return doc


def _unit_order(project: Path) -> List[str]:
    outline_path = project / "outline.json"
    if outline_path.exists():
        outline = load_json(outline_path)
        return [u["id"] for u in outline.get("units", [])]
    return [p.stem for p in unit_files(project)]


def lint_project(project: Path, stage: str = "final") -> List[Finding]:
    """프로젝트 단위 검사. stage=early 는 존재하는 단위만, final 은 개요 전체를 요구한다."""
    out: List[Finding] = []
    for name in ("spec_lock.json", "outline.json"):
        if not (project / name).exists():
            out.append(_err("P001", f"{name} 이 없습니다 (Strategist 단계 산출물)"))
    if out:
        return out
    outline = load_json(project / "outline.json")
    units = outline.get("units", [])
    ids = [u.get("id") for u in units]
    if len(set(ids)) != len(ids):
        out.append(_err("P002", "outline.json 의 단위 id 가 중복됩니다"))
    present = {p.stem for p in unit_files(project)}
    for extra in sorted(present - set(ids)):
        out.append(_err("P003", f"개요에 없는 단위 파일 units/{extra}.json"))
    lock = load_json(project / "spec_lock.json")
    width = float((lock.get("page_size") or {}).get("width_mm", A4_WIDTH_MM))
    for u in units:
        uid = u.get("id")
        path = project / "units" / f"{uid}.json"
        if not path.exists():
            if stage == "final":
                out.append(_err("P004", f"단위 {uid} 가 아직 작성되지 않았습니다", f"units/{uid}.json"))
            continue
        try:
            unit = load_json(path)
        except json.JSONDecodeError as exc:
            out.append(_err("E000", f"JSON 해석 실패: {exc}", f"units/{uid}.json"))
            continue
        prefix = f"units/{uid}.json:"
        if set(unit) - {"unit", "blocks"}:
            out.append(_err("E003", f"단위 파일의 알 수 없는 필드 {sorted(set(unit) - {'unit', 'blocks'})}", prefix))
        if unit.get("unit") not in (None, uid):
            out.append(_err("P005", f"unit 필드 {unit.get('unit')!r} 가 파일 이름 {uid} 와 다릅니다", prefix))
        blocks = unit.get("blocks", [])
        out.extend(validate_blocks(blocks, prefix))
        out.extend(lint_table_widths(blocks, width, prefix))
        out.extend(_lint_unit_budget(u, blocks, prefix))
    return out


def _lint_unit_budget(u: dict, blocks, where: str) -> List[Finding]:
    target = u.get("target_chars")
    if not isinstance(target, int) or target <= 0 or not isinstance(blocks, list):
        return []
    chars = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if isinstance(b.get("text"), str):
            chars += len(b["text"])
        for row in b.get("rows", []) if isinstance(b.get("rows"), list) else []:
            chars += sum(len(c) for c in row if isinstance(c, str))
    ratio = chars / target
    if ratio < 0.7 or ratio > 1.3:
        return [_warn("W301", f"분량 {chars}자 — 계획 {target}자의 {ratio:.0%}", where)]
    return []


def summarize(findings: Iterable[Finding]) -> dict:
    findings = list(findings)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    return {
        "ok": not errors,
        "errorCount": len(errors),
        "warningCount": len(warnings),
        "findings": [f.to_dict() for f in findings],
    }
