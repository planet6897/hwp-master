"""hwp-master 문서 명세(HDS) 공용 라이브러리: 검증·규약 검사·조립.

HDS v1 은 `rhwp scaffold` 명세 v1 과 같은 형식이다.

    {"version": "1", "title": str?, "font": str?,
     "page_size": {"width_mm": num, "height_mm": num}?,
     "orientation": "portrait"|"landscape"?, "margins_mm": {left, right, top, bottom, header, footer}?,
     "header"?: {"text": str}, "footer"?: {"text": str},
     "style"?: {"body_pt"?, "heading_pt"?, "title_pt"?, "table_pt"?: pt, "line_spacing"?: %},
     "blocks": [heading | paragraph | table | page_break | image | list]}

    heading    {"type": "heading", "level": 1..7, "text": str}
    paragraph  {"type": "paragraph", "text": str,
                "footnotes"?: [{"text": str, "after"?: str}],
                "emphasis"?: [{"text": str, "bold"?, "italic"?, "underline"?, "strike"?: bool,
                               "color"?, "highlight"?: "#RRGGBB", "size"?: pt}]}
    table      {"type": "table", "rows": [[str]], "caption"?: str, "cell_align"?: str | [str],
                "header_row"?: bool, "header_fill"?: "#RRGGBB", "col_widths"?: [num],
                "merges"?: [{"row", "col", "rowspan"?, "colspan"?}],
                "cell_emphasis"?: [{"row", "col", "text"?, "bold"?, "italic"?, "underline"?, "color"?}]}
    page_break {"type": "page_break"}          # 다음 블록을 새 쪽에서 시작
    image      {"type": "image", "path": str, "width_mm"?: num, "caption"?: str,  # "{n}" = 순번
                "align"?: "left"|"center"|"right"}  # 글자처럼 취급한 그림 한 줄
    toc        {"type": "toc", "title"?: str, "levels"?: 1..3}   # 제목 목록과 쪽 번호
    list       {"type": "list", "items": [str | {"text": str, "level"?: 1..,
                "footnotes"?, "emphasis"?}], "markers"?: [str]}
                # 단계별 글머리(기본 □ ○ - ·)·왼쪽 여백·내어쓰기를 붙인 문단들

header/footer 의 text 안 "{page}" 는 쪽 번호가 된다. footnotes 는 after 문자열(문단에서
처음 나오는 곳) 바로 뒤, after 가 없으면 문단 끝에 각주 번호를 단다. emphasis 는 문단에서 text 가
처음 나오는 곳에 글자 서식을 준다.
제목·문단·표는 rhwp scaffold 명세 v1 그대로이고, 나머지는 native.py 가 rhwp edit 으로 붙인다.

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

import conventions

HDS_VERSION = "1"
TOP_KEYS = {"version", "title", "font", "page_size", "orientation", "margins_mm", "header", "footer", "style",
            "blocks"}
# 문서 전체 글자 크기(pt)·줄 간격(%). 없으면 scaffold 기본(본문·표 10, 개요 12, 제목 18, 줄 간격 160)
STYLE_PT_KEYS = ("body_pt", "heading_pt", "title_pt", "table_pt")
STYLE_KEYS = STYLE_PT_KEYS + ("line_spacing",)
STYLE_PT_RANGE = (6, 40)
STYLE_LS_RANGE = (80, 400)
PAGE_KEYS = ("page_size", "orientation", "margins_mm")
MARGIN_KEYS = ("left", "right", "top", "bottom", "header", "footer")
# scaffold 기본 여백(mm): 좌우 30, 위 20, 아래·머리말·꼬리말 15
DEFAULT_MARGINS_MM = {"left": 30.0, "right": 30.0, "top": 20.0, "bottom": 15.0, "header": 15.0, "footer": 15.0}
MIN_CONTENT_MM = 50.0
BLOCK_KEYS = {
    "heading": {"type", "level", "text"},
    "paragraph": {"type", "text", "footnotes", "emphasis"},
    "table": {"type", "rows", "caption", "cell_align", "header_row", "header_fill", "col_widths", "merges",
              "cell_emphasis"},
    "page_break": {"type"},
    "image": {"type", "path", "width_mm", "caption", "align"},
    "list": {"type", "items", "markers"},
    "toc": {"type", "title", "levels"},
}
LIST_ITEM_KEYS = {"text", "level", "footnotes", "emphasis"}
LIST_DEFAULT_LEVELS = 4
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
IMAGE_ALIGNS = ("left", "center", "right")
HF_KEYS = ("header", "footer")
PAGE_TOKEN = "{page}"
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
CELL_PAD_MM = 2 * 510 * 25.4 / 7200  # scaffold 셀 안쪽 여백 좌우 510 HU (약 1.8mm) 씩
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
        if btype == "paragraph" and "footnotes" in b:
            out.extend(_validate_footnotes(b, where))
        if btype == "paragraph" and "emphasis" in b:
            out.extend(_validate_emphasis(b, where))
        if btype == "table":
            out.extend(_validate_table(b, where))
        if btype == "image":
            out.extend(_validate_image(b, where))
        if btype == "list":
            out.extend(_validate_list(b, where))
        if btype == "toc":
            out.extend(_validate_toc(b, where))
    tocs = [i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "toc"]
    if len(tocs) > 1:
        out.append(_err("E122", "목차는 문서에 하나만 둡니다", f"{where_prefix}blocks[{tocs[1]}]"))
    return out


def _validate_toc(b: dict, where: str) -> List[Finding]:
    out: List[Finding] = []
    title = b.get("title")
    if title is not None and not isinstance(title, str):
        out.append(_err("E122", "toc.title 은 문자열이어야 합니다 (\"\" 이면 제목 없음)", where))
    levels = b.get("levels", 2)
    if not isinstance(levels, int) or isinstance(levels, bool) or not 1 <= levels <= 3:
        out.append(_err("E122", "toc.levels 는 1~3 정수여야 합니다", where))
    return out


def lint_toc(blocks, where_prefix: str = "") -> List[Finding]:
    """W205: 목차가 있는데 실을 제목이 없음."""
    if not isinstance(blocks, list):
        return []
    tocs = [i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "toc"]
    if tocs and not any(isinstance(b, dict) and b.get("type") == "heading" for b in blocks):
        return [_warn("W205", "목차가 있지만 제목(heading) 블록이 없습니다", f"{where_prefix}blocks[{tocs[0]}]")]
    return []


def _validate_list(b: dict, where: str) -> List[Finding]:
    out: List[Finding] = []
    markers = b.get("markers")
    levels = LIST_DEFAULT_LEVELS
    if markers is not None:
        if (not isinstance(markers, list) or not markers or not all(
                isinstance(m, str) and m and not any(c.isspace() for c in m) and len(m) <= 3
                for m in markers)):
            out.append(_err("E115", "list.markers 는 빈칸 없는 3글자 이하 문자열의 배열이어야 합니다", where))
        else:
            levels = len(markers)
    items = b.get("items")
    if not isinstance(items, list) or not items:
        return out + [_err("E114", "list.items 는 비어 있지 않은 배열이어야 합니다", where)]
    prev = 0
    for k, item in enumerate(items):
        w = f"{where}.items[{k}]"
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or set(item) - LIST_ITEM_KEYS:
            out.append(_err("E114", "목록 항목은 문자열이나 {text, level?, footnotes?, emphasis?} 객체여야 합니다", w))
            continue
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            out.append(_err("E114", "목록 항목 text 는 비어 있지 않은 문자열이어야 합니다", w))
            continue
        level = item.get("level", 1)
        if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= levels:
            out.append(_err("E114", f"목록 level 은 1~{levels} 정수여야 합니다", w))
            continue
        if level > prev + 1:
            out.append(_err("E114", f"목록 단계를 건너뛰었습니다: {prev} 단계 다음에 {level} 단계", w))
        prev = level
        if "footnotes" in item:
            out.extend(_validate_footnotes(item, w))
        if "emphasis" in item:
            out.extend(_validate_emphasis(item, w))
    return out


def _validate_footnotes(b: dict, where: str) -> List[Finding]:
    notes = b["footnotes"]
    if not isinstance(notes, list) or not notes:
        return [_err("E106", "footnotes 는 비어 있지 않은 배열이어야 합니다", where)]
    out: List[Finding] = []
    for k, n in enumerate(notes):
        w = f"{where}.footnotes[{k}]"
        if not isinstance(n, dict) or set(n) - {"text", "after"}:
            out.append(_err("E106", "각주는 {text, after?} 객체여야 합니다", w))
            continue
        if not isinstance(n.get("text"), str) or not n["text"].strip():
            out.append(_err("E106", "각주 text 는 비어 있지 않은 문자열이어야 합니다", w))
        after = n.get("after")
        if after is not None and (not isinstance(after, str) or not after or after not in b["text"]):
            out.append(_err("E107", f"각주 after {after!r} 가 문단 글자에 없습니다", w))
    return out


EMPHASIS_STYLES = ("bold", "italic", "underline", "strike", "color", "highlight", "size")
EMPHASIS_SIZE_PT = (6, 40)
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _validate_emphasis(b: dict, where: str) -> List[Finding]:
    marks = b["emphasis"]
    if not isinstance(marks, list) or not marks:
        return [_err("E109", "emphasis 는 비어 있지 않은 배열이어야 합니다", where)]
    out: List[Finding] = []
    for k, m in enumerate(marks):
        w = f"{where}.emphasis[{k}]"
        if not isinstance(m, dict) or set(m) - {"text", *EMPHASIS_STYLES}:
            out.append(_err("E109", "강조는 {text, bold?, italic?, underline?, strike?, color?, highlight?, size?} "
                                    "객체여야 합니다", w))
            continue
        text = m.get("text")
        if not isinstance(text, str) or not text or text not in b["text"]:
            out.append(_err("E110", f"강조 text {text!r} 가 문단 글자에 없습니다", w))
        for key in ("bold", "italic", "underline", "strike"):
            if key in m and not isinstance(m[key], bool):
                out.append(_err("E109", f"{key} 는 true/false 여야 합니다", w))
        for key in ("color", "highlight"):
            if key in m and not (isinstance(m[key], str) and HEX_COLOR.match(m[key])):
                out.append(_err("E109", f"{key} 는 #RRGGBB 형식이어야 합니다", w))
        size = m.get("size")
        lo, hi = EMPHASIS_SIZE_PT
        if "size" in m and (isinstance(size, bool) or not isinstance(size, (int, float)) or not lo <= size <= hi):
            out.append(_err("E109", f"size 는 {lo}~{hi} pt 숫자여야 합니다", w))
        if not any(m.get(k) for k in EMPHASIS_STYLES):
            out.append(_err("E109", "bold·italic·underline·strike·color·highlight·size 중 하나는 있어야 합니다", w))
    return out


def _validate_image(b: dict, where: str) -> List[Finding]:
    out: List[Finding] = []
    path = b.get("path")
    if not isinstance(path, str) or not path.strip():
        out.append(_err("E111", "image.path 는 비어 있지 않은 문자열이어야 합니다", where))
    elif not path.lower().endswith(IMAGE_EXTS):
        out.append(_err("E111", f"그림 형식은 {', '.join(IMAGE_EXTS)} 만 됩니다: {path!r}", where))
    w = b.get("width_mm")
    if w is not None and (isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0):
        out.append(_err("E111", "image.width_mm 는 양수여야 합니다", where))
    if "caption" in b:
        out.extend(_validate_caption(b["caption"], where))
    if b.get("align", "center") not in IMAGE_ALIGNS:
        out.append(_err("E111", f"image.align 은 {IMAGE_ALIGNS} 중 하나여야 합니다", where))
    return out


CAPTION_TOKEN = "{n}"
MANUAL_CAPTION_NUMBER = re.compile(r"^\s*[<〈［\[(]?\s*(그림|표|도표|사진)\s*\d+")


def _validate_caption(cap, where: str) -> List[Finding]:
    """E120 형식, W204 번호를 손으로 씀."""
    if not isinstance(cap, str) or not cap.strip():
        return [_err("E120", "caption 은 비어 있지 않은 문자열이어야 합니다", where)]
    if cap.count(CAPTION_TOKEN) > 1:
        return [_err("E120", "caption 의 {n} 은 한 번만 씁니다", where)]
    if CAPTION_TOKEN not in cap and MANUAL_CAPTION_NUMBER.match(cap):
        return [_warn("W204", f"캡션 번호를 직접 썼습니다: {cap!r} — \"<표 {{n}}> …\" 처럼 쓰면 순서대로 매겨집니다", where)]
    return []


def page_geometry(spec: dict) -> dict:
    """HDS(또는 spec_lock) → 실제 용지 가로·세로와 여백(mm). 값이 틀리면 기본값으로 본다."""
    ps = spec.get("page_size") if isinstance(spec.get("page_size"), dict) else {}
    try:
        w, h = float(ps.get("width_mm", A4_WIDTH_MM)), float(ps.get("height_mm", 297.0))
    except (TypeError, ValueError):
        w, h = A4_WIDTH_MM, 297.0
    if spec.get("orientation") == "landscape":
        w, h = max(w, h), min(w, h)
    margins = dict(DEFAULT_MARGINS_MM)
    if isinstance(spec.get("margins_mm"), dict):
        margins.update({k: float(v) for k, v in spec["margins_mm"].items()
                        if k in MARGIN_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool)})
    return {"width_mm": w, "height_mm": h, "margins": margins,
            "content_mm": w - margins["left"] - margins["right"]}


def validate_page(spec: dict, where: str = "") -> List[Finding]:
    """E121: orientation·margins_mm 형식, 본문 폭이 너무 좁음."""
    out: List[Finding] = []
    o = spec.get("orientation")
    if o is not None and o not in ("portrait", "landscape"):
        out.append(_err("E121", "orientation 은 portrait 또는 landscape 여야 합니다", where))
    m = spec.get("margins_mm")
    if m is not None:
        if not (isinstance(m, dict) and m and set(m) <= set(MARGIN_KEYS) and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100 for v in m.values())):
            out.append(_err("E121", f"margins_mm 는 {list(MARGIN_KEYS)} 중 일부를 0~100 mm 숫자로 줍니다", where))
            return out
    geo = page_geometry(spec)
    if geo["content_mm"] < MIN_CONTENT_MM:
        out.append(_err("E121", f"본문 폭이 {geo['content_mm']:.0f}mm 입니다 — 좌우 여백을 줄이세요 "
                                f"(최소 {MIN_CONTENT_MM:.0f}mm)", where))
    body_h = geo["height_mm"] - geo["margins"]["top"] - geo["margins"]["bottom"] \
        - geo["margins"]["header"] - geo["margins"]["footer"]
    if body_h < MIN_CONTENT_MM:
        out.append(_err("E121", f"본문 높이가 {body_h:.0f}mm 입니다 — 위아래·머리말·꼬리말 여백을 줄이세요", where))
    return out


def lint_image_widths(blocks, content_mm: float, where_prefix: str = "") -> List[Finding]:
    """E112: width_mm 가 본문 폭(content_mm)을 넘음."""
    content = content_mm
    out: List[Finding] = []
    for i, b in enumerate(blocks if isinstance(blocks, list) else []):
        if isinstance(b, dict) and b.get("type") == "image":
            w = b.get("width_mm")
            if isinstance(w, (int, float)) and not isinstance(w, bool) and w > content:
                out.append(_err("E112", f"그림 너비 {w}mm 가 본문 폭 {content:.0f}mm 를 넘습니다",
                                f"{where_prefix}blocks[{i}]"))
    return out


def lint_image_files(blocks, base: Path, where_prefix: str = "") -> List[Finding]:
    """E113: 그림 파일이 없음 (base 기준 상대 경로)."""
    out: List[Finding] = []
    for i, b in enumerate(blocks if isinstance(blocks, list) else []):
        if isinstance(b, dict) and b.get("type") == "image" and isinstance(b.get("path"), str):
            if not (base / b["path"]).is_file():
                out.append(_err("E113", f"그림 파일이 없습니다: {b['path']}", f"{where_prefix}blocks[{i}]"))
    return out


def validate_page_breaks(blocks, where_prefix: str = "", whole: bool = True) -> List[Finding]:
    """E105: 쪽 나눔은 블록 사이에만 한 번씩. whole=False(단위 하나)면 맨 앞·끝은 허용한다."""
    if not isinstance(blocks, list):
        return []
    out: List[Finding] = []
    kinds = [b.get("type") if isinstance(b, dict) else None for b in blocks]
    for i, k in enumerate(kinds):
        if k != "page_break":
            continue
        where = f"{where_prefix}blocks[{i}]"
        if i > 0 and kinds[i - 1] == "page_break":
            out.append(_err("E105", "쪽 나눔이 연달아 있습니다", where))
        elif whole and (i == 0 or i == len(kinds) - 1):
            out.append(_err("E105", "쪽 나눔은 문서 맨 앞이나 맨 끝에 둘 수 없습니다", where))
    return out


def validate_style(doc: dict, where: str = "") -> List[Finding]:
    """E123: style 은 {body_pt, heading_pt, title_pt, table_pt: 6~40 pt, line_spacing: 80~400 %} 중 일부."""
    st = doc.get("style")
    if st is None:
        return []
    msg = (f"style 은 {list(STYLE_KEYS)} 중 일부를 씁니다 — 글자 크기 {STYLE_PT_RANGE[0]}~{STYLE_PT_RANGE[1]}pt "
           f"(0.5 단위), 줄 간격 {STYLE_LS_RANGE[0]}~{STYLE_LS_RANGE[1]}% 정수")
    if not isinstance(st, dict) or not st or set(st) - set(STYLE_KEYS):
        return [_err("E123", msg, where)]
    for k, v in st.items():
        num = isinstance(v, (int, float)) and not isinstance(v, bool)
        if k == "line_spacing":
            ok = num and float(v).is_integer() and STYLE_LS_RANGE[0] <= v <= STYLE_LS_RANGE[1]
        else:
            ok = num and STYLE_PT_RANGE[0] <= v <= STYLE_PT_RANGE[1] and float(v * 2).is_integer()
        if not ok:
            return [_err("E123", f"{msg} ({k}={v!r})", where)]
    return []


def validate_header_footer(doc: dict, where: str = "") -> List[Finding]:
    out: List[Finding] = []
    for key in HF_KEYS:
        if key not in doc:
            continue
        hf = doc[key]
        w = f"{where}{key}"
        if not (isinstance(hf, dict) and set(hf) == {"text"} and isinstance(hf["text"], str)
                and hf["text"].strip()):
            out.append(_err("E108", f"{key} 는 {{text: 비어 있지 않은 문자열}} 이어야 합니다", w))
        elif hf["text"].count(PAGE_TOKEN) > 1:
            out.append(_err("E108", f"{key}.text 의 {PAGE_TOKEN} 는 한 번만 쓸 수 있습니다", w))
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
    if "header_row" in b and not isinstance(b["header_row"], bool):
        out.append(_err("E116", "header_row 는 true/false 여야 합니다", where))
    if "header_fill" in b:
        fill = b["header_fill"]
        if not (isinstance(fill, str) and HEX_COLOR.match(fill)):
            out.append(_err("E116", "header_fill 은 #RRGGBB 형식이어야 합니다", where))
        elif b.get("header_row") is not True:
            out.append(_err("E116", "header_fill 은 header_row: true 와 함께 씁니다", where))
    if "caption" in b:
        out.extend(_validate_caption(b["caption"], where))
    covered = set()
    if "merges" in b:
        found, covered = _validate_merges(b, rows, ncols, where)
        out.extend(found)
    if "cell_emphasis" in b:
        out.extend(_validate_cell_emphasis(b, rows, ncols, covered, where))
    widths = b.get("col_widths")
    if widths is not None and (not isinstance(widths, list) or len(widths) != ncols or not all(
            isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 for w in widths)):
        out.append(_err("E117", f"col_widths 는 열 수({ncols})만큼의 양수 배열이어야 합니다 (비율)", where))
    return out


def _cell_text(rows, r: int, c: int) -> str:
    return rows[r][c] if r < len(rows) and c < len(rows[r]) and isinstance(rows[r][c], str) else ""


def _validate_merges(b: dict, rows, ncols: int, where: str) -> tuple:
    """E118. 반환 (findings, 가려지는 셀 좌표 집합)."""
    merges = b["merges"]
    if not isinstance(merges, list) or not merges:
        return [_err("E118", "merges 는 비어 있지 않은 배열이어야 합니다", where)], set()
    out: List[Finding] = []
    taken, covered = set(), set()
    for k, m in enumerate(merges):
        w = f"{where}.merges[{k}]"
        if not isinstance(m, dict) or set(m) - {"row", "col", "rowspan", "colspan"} or not {"row", "col"} <= set(m):
            out.append(_err("E118", "병합은 {row, col, rowspan?, colspan?} 객체여야 합니다", w))
            continue
        vals = [m["row"], m["col"], m.get("rowspan", 1), m.get("colspan", 1)]
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in vals) or min(vals[:2]) < 0 \
                or min(vals[2:]) < 1:
            out.append(_err("E118", "row·col 은 0 이상, rowspan·colspan 은 1 이상 정수여야 합니다", w))
            continue
        r, c, rs, cs = vals
        if rs == cs == 1:
            out.append(_err("E118", "rowspan 이나 colspan 중 하나는 2 이상이어야 합니다", w))
            continue
        if r + rs > len(rows) or c + cs > ncols:
            out.append(_err("E118", f"병합 범위가 표({len(rows)}×{ncols}) 밖입니다", w))
            continue
        cells = {(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)}
        if cells & taken:
            out.append(_err("E118", "다른 병합과 겹칩니다", w))
            continue
        taken |= cells
        hidden = cells - {(r, c)}
        covered |= hidden
        full = sorted(x for x in hidden if _cell_text(rows, *x).strip())
        if full:
            out.append(_err("E118", f"병합으로 가려지는 셀 {full} 에 글이 있습니다 — 첫 셀에 모아 쓰고 나머지는 \"\" 로 둡니다", w))
    return out, covered


def _validate_cell_emphasis(b: dict, rows, ncols: int, covered: set, where: str) -> List[Finding]:
    marks = b["cell_emphasis"]
    if not isinstance(marks, list) or not marks:
        return [_err("E119", "cell_emphasis 는 비어 있지 않은 배열이어야 합니다", where)]
    out: List[Finding] = []
    for k, m in enumerate(marks):
        w = f"{where}.cell_emphasis[{k}]"
        if not isinstance(m, dict) or set(m) - {"row", "col", "text", *EMPHASIS_STYLES}:
            out.append(_err("E119", "셀 강조는 {row, col, text?, 강조 서식…} 객체여야 합니다", w))
            continue
        r, c = m.get("row"), m.get("col")
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (r, c)) \
                or r >= len(rows) or c >= ncols:
            out.append(_err("E119", f"셀 강조 좌표가 표({len(rows)}×{ncols}) 밖입니다", w))
            continue
        if (r, c) in covered:
            out.append(_err("E119", f"병합으로 가려지는 셀 ({r},{c}) 입니다 — 병합 첫 셀을 가리키세요", w))
            continue
        cell = _cell_text(rows, r, c)
        text = m.get("text")
        if text is not None and (not isinstance(text, str) or not text or text not in cell):
            out.append(_err("E110", f"강조 text {text!r} 가 셀 글자에 없습니다", w))
        if text is None and not cell:
            out.append(_err("E119", "빈 셀은 강조할 수 없습니다", w))
        out.extend(f for f in _validate_emphasis({"text": cell, "emphasis": [
            {**{k2: v for k2, v in m.items() if k2 in EMPHASIS_STYLES}, "text": text or cell or "x"}]}, w)
            if f.code == "E109")
    return out


def col_ratios(b: dict, ncols: int) -> List[float]:
    """열 폭 비율 (합 1). col_widths 가 없거나 틀리면 균등."""
    w = b.get("col_widths")
    if (isinstance(w, list) and len(w) == ncols and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in w)):
        total = float(sum(w))
        return [x / total for x in w]
    return [1.0 / ncols] * ncols


def table_pt(spec: dict) -> float:
    """표 글자 크기(pt): style.table_pt, 없으면 10."""
    st = spec.get("style") if isinstance(spec, dict) else None
    v = st.get("table_pt") if isinstance(st, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 10.0


def lint_table_widths(blocks, content_mm: float, where_prefix: str = "", pt: float = 10.0) -> List[Finding]:
    """W201: 셀 텍스트가 열 폭(col_widths 비율, 없으면 균등) 한 줄을 넘는지 추정한다 (M0 F2 임시 경고).

    content_mm 은 본문 폭(용지 폭 - 좌우 여백), pt 는 표 글자 크기다."""
    out: List[Finding] = []
    em_mm = BASE_EM_MM * pt / 10.0
    content_mm = content_mm - TABLE_OUTER_MM
    for i, b in enumerate(blocks if isinstance(blocks, list) else []):
        if not isinstance(b, dict) or b.get("type") != "table" or not isinstance(b.get("rows"), list):
            continue
        rows = [r for r in b["rows"] if isinstance(r, list)]
        ncols = max((len(r) for r in rows), default=0)
        if not ncols:
            continue
        ratios = col_ratios(b, ncols)
        spans = {(m.get("row"), m.get("col")): m.get("colspan", 1) for m in b.get("merges") or []
                 if isinstance(m, dict) and isinstance(m.get("colspan", 1), int)}
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                share = sum(ratios[c:c + max(1, spans.get((r, c), 1))])
                cap_em = (content_mm * share - CELL_PAD_MM) / em_mm
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
    if ps is not None:
        if not (isinstance(ps, dict) and set(ps) == {"width_mm", "height_mm"}
                and all(isinstance(ps[k], (int, float)) and ps[k] > 0 for k in ps)):
            out.append(_err("E007", "page_size 는 {width_mm, height_mm} 양수여야 합니다"))
    out.extend(validate_page(doc))
    width = page_geometry(doc)["content_mm"]
    for key in ("title", "font"):
        if key in doc and not isinstance(doc[key], str):
            out.append(_err("E004", f"{key} 는 문자열이어야 합니다"))
    out.extend(validate_header_footer(doc))
    out.extend(validate_style(doc))
    blocks = doc.get("blocks", [])
    out.extend(validate_blocks(blocks))
    out.extend(validate_page_breaks(blocks))
    out.extend(lint_table_widths(blocks, width, pt=table_pt(doc)))
    out.extend(lint_image_widths(blocks, width))
    out.extend(lint_toc(blocks))
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
    for key in ("title", "font", "style") + PAGE_KEYS + HF_KEYS:
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
    if "max_pages" in lock and not (
            isinstance(lock["max_pages"], int) and not isinstance(lock["max_pages"], bool)
            and lock["max_pages"] > 0):
        out.append(_err("E008", "spec_lock.max_pages 는 양의 정수여야 합니다", "spec_lock.json"))
    convention = lock.get("convention")
    if convention is not None and convention not in conventions.CONVENTIONS:
        out.append(_err("E009", f"spec_lock.convention 은 {list(conventions.CONVENTIONS)} 중 하나여야 합니다",
                        "spec_lock.json"))
    out.extend(validate_header_footer(lock, "spec_lock.json:"))
    out.extend(validate_style(lock, "spec_lock.json"))
    out.extend(validate_page(lock, "spec_lock.json"))
    width = page_geometry(lock)["content_mm"]
    all_blocks = []
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
        out.extend(lint_table_widths(blocks, width, prefix, pt=table_pt(lock)))
        out.extend(_lint_unit_budget(u, blocks, prefix))
        out.extend(conventions.lint_convention(blocks, convention, prefix))
        out.extend(validate_page_breaks(blocks, prefix, whole=False))
        out.extend(lint_image_widths(blocks, width, prefix))
        out.extend(lint_image_files(blocks, project, prefix))
        if isinstance(blocks, list):
            all_blocks.extend(blocks)
    if stage == "final" and not any(f.severity == "error" for f in out):
        kinds = [b.get("type") for b in all_blocks if isinstance(b, dict)]
        if kinds and "page_break" in (kinds[0], kinds[-1]):
            out.append(_err("E105", "쪽 나눔은 문서 맨 앞이나 맨 끝에 둘 수 없습니다"))
        elif any(a == b == "page_break" for a, b in zip(kinds, kinds[1:])):
            out.append(_err("E105", "단위 경계에서 쪽 나눔이 연달아 있습니다"))
        if kinds.count("toc") > 1:
            out.append(_err("E122", "목차는 문서에 하나만 둡니다 (여러 단위에 toc 가 있음)"))
        out.extend(lint_toc(all_blocks))
    return out


def _lint_unit_budget(u: dict, blocks, where: str) -> List[Finding]:
    target = u.get("target_chars")
    if not isinstance(target, int) or target <= 0 or not isinstance(blocks, list):
        return []
    chars = 0
    flat = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "list" and isinstance(b.get("items"), list):
            flat.extend({"text": it} if isinstance(it, str) else it for it in b["items"])
        else:
            flat.append(b)
    for b in flat:
        if not isinstance(b, dict):
            continue
        if isinstance(b.get("text"), str):
            chars += len(b["text"])
        if isinstance(b.get("caption"), str):
            chars += len(b["caption"])
        for n in b.get("footnotes", []) if isinstance(b.get("footnotes"), list) else []:
            if isinstance(n, dict) and isinstance(n.get("text"), str):
                chars += len(n["text"])
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
