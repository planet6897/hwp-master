"""HDS → 네이티브 HWPX 생산: rhwp scaffold 와 편집 후처리.

scaffold 명세 v1 은 제목·문단·표만 받는다. HDS 의 나머지 요소는 scaffold 결과에
`rhwp edit` 을 순서대로 적용해 붙인다.

    page_break 블록          → edit insert-page-break (다음 블록 문단 앞에서 쪽 나눔)
    image 블록               → 표시 글자 문단 → edit delete-text + insert-picture
                               + set-picture {"treatAsChar":true} + apply-para-format 정렬,
                               caption 은 가운데 정렬 문단("{n}" = 그림 순번), 그림 문단은 다음 문단과 같이
    toc 블록                 → 제목마다 "번호 글<탭>쪽" 문단 + 오른쪽 끝 자동 탭(tabAutoRight). 쪽 번호는
                               한 번 만든 결과의 rhwp dump-pages 에서 읽어 다시 만든다(produce 두 번)
    table.caption            → 표 위 가운데 문단("{n}" = 표 순번), 표와 같은 쪽에 둠(keepWithNext)
    paragraph.emphasis       → edit apply-char-format (굵게·기울임·밑줄·취소선·글자색·형광펜·크기)
    paragraph.footnotes      → edit insert-footnote + insert-footnote-text. 각주가 여럿이면 하나만 rhwp 로
                               넣고 그 컨트롤을 본으로 나머지를 구역 XML 에 바로 쓴다(글만 든 문단, #50)
    모든 표                  → edit set-table-props {"restrictInPage":true} (쪽 영역 안으로 제한)
    table.header_row         → 첫 행 셀마다 edit set-cell-props {fill, isHeader} +
                               apply-char-format-in-cell 굵게 + apply-para-format-in-cell 가운데
    table.col_widths         → edit set-column-widths (scaffold 표 폭을 비율로 나눔)
    table.merges             → edit merge-cells (머리 행 꾸밈보다 먼저, 가려진 셀은 건너뜀)
    table.cell_emphasis      → edit apply-char-format-in-cell --start/--end
    list 블록                → 항목마다 "글머리 + 빈칸 + 글" 문단 + apply-para-format
                               왼쪽 여백(단계)·내어쓰기(글머리 폭)
    header / footer          → edit insert-header-footer + insert-header-footer-text
                               (+ "{page}" 자리에 insert-field-in-hf 쪽 번호)
    style                    → 문단마다 edit apply-char-format fontSize·fontName(제목·개요·본문) +
                               apply-para-format lineSpacing·spacingBefore·spacingAfter, 표 셀마다
                               apply-char-format-in-cell fontSize(table_pt)·fontName(table_font).
                               강조보다 먼저 적용해 강조 크기가 이긴다.
                               목록 들여쓰기·글머리 폭은 본문 크기에 비례해 키운다.

블록 → 문단 위치는 산술로 셈하지 않고 scaffold 산출물의 최상위 문단을 읽어 찾는다
(제목 문단, 표 뒤 빈 문단 등 scaffold 가 덧붙이는 문단이 있기 때문).
같은 HDS 와 같은 rhwp 이면 같은 바이트가 나온다. 빌드 영수증 재현도 이 함수를 쓴다.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import unicodedata
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

import hds

HU_PER_MM = 7200 / 25.4
HU_PER_PX = 75  # 96dpi 픽셀 1개 (rhwp insert-picture 의 원본 크기 환산과 같다)
TABLE_OUTER_HU = 283 * 2  # scaffold 표 바깥 여백 좌우 (약 1mm 씩)
IMAGE_MARK = "[[hwp-master:image:{n}]]"
IMAGE_MARK_RE = re.compile(r"\[\[hwp-master:image:\d+\]\]")
HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
PAGE_TOKEN = "{page}"
# 가운데 정렬하는 머리말/꼬리말. 쪽 번호 꼬리말은 가운데가 관행이고, 머리말은 rhwp 기본(왼쪽)을 둔다.
HF_CENTER = ("footer",)
CAPTION_TOKEN = "{n}"
TOC_TITLE = "목차"
TOC_TITLE_PT = 14
TOC_LEVELS = 2
TOC_PLACEHOLDER = 0
LIST_MARKERS = ("□", "○", "-", "·")
LIST_STEP_HU = 1000  # 단계마다 왼쪽 여백 10pt
# 글머리 + 빈칸 한 개의 폭(본문 10pt 기준 HWPUNIT). 한컴 2024 가 함초롬바탕 10pt 를 그린 PDF 의
# 글자 상자 간격에서 쟀다: "□ " 14.75pt, "○ " 14.75pt, "- " 10.55pt, "· " 8.16pt, "가. " 약 18pt.
SPACE_HU = 475
MARKER_HU = {"-": 580, "·": 341, "ㆍ": 341, "*": 500, ".": 300, ")": 330, "(": 330}
BODY_HEIGHT_HU = 1000  # scaffold 본문 글자 크기 10pt
BODY_PT = 10
HEADER_FILL = "#D9D9D9"  # 머리 행 기본 배경 (회색 15%)
TABLE_STYLE_KEYS = ("header_row", "header_fill", "col_widths", "merges", "cell_emphasis")



def split(doc: dict, toc_pages: Optional[Dict[int, int]] = None) -> tuple:
    """HDS → (scaffold 명세, 편집 계획). 편집 계획이 비면 scaffold 만으로 끝난다.

    toc_pages 는 목차 항목(제목 블록 순번 → 쪽)이다. 없으면 쪽 번호 자리에 TOC_PLACEHOLDER 를 둔다."""
    spec = {k: copy.deepcopy(v) for k, v in doc.items()
            if k not in ("header", "footer", "blocks", "orientation", "margins_mm", "style")}
    page = page_props(doc)
    if page and doc.get("orientation") == "landscape" and "page_size" in spec:
        # scaffold 는 세로 치수로 만들고, 가로는 한컴처럼 landscape 표시로 돌린다
        w, h = spec["page_size"]["width_mm"], spec["page_size"]["height_mm"]
        spec["page_size"] = {"width_mm": min(w, h), "height_mm": max(w, h)}
    blocks: List[dict] = []
    plan: Dict[str, object] = {"page_breaks": [], "footnotes": [], "emphasis": [], "images": [],
                               "aligns": [], "indents": [], "tables": [], "page": page, "toc": None, "header": doc.get("header"),
                               "footer": doc.get("footer"), "style": doc.get("style") or None}
    scale = ((doc.get("style") or {}).get("body_pt") or BODY_PT) / BODY_PT
    pending_break = False
    counters = {"image": 0, "table": 0}

    def add_para(b: dict, prefix: str = "") -> None:
        nonlocal pending_break
        b = copy.deepcopy(b)
        notes = b.pop("footnotes", None) or []
        marks = b.pop("emphasis", None) or []
        if pending_break:
            plan["page_breaks"].append(len(blocks))
            pending_break = False
        text = prefix + b.get("text", "")
        if "text" in b or prefix:
            b["text"] = text
        start = len(prefix)
        for n in notes:
            after = n.get("after")
            offset = len(text) if after is None else text.index(after, start) + len(after)
            plan["footnotes"].append({"block": len(blocks), "offset": offset, "text": n["text"]})
        for m in marks:
            plan["emphasis"].append({"block": len(blocks), "offset": text.index(m["text"], start),
                                     "count": len(m["text"]), "props": char_props(m)})
        blocks.append(b)

    for b in doc.get("blocks", []):
        if b.get("type") == "page_break":
            pending_break = True
            continue
        if b.get("type") == "toc":
            entries = toc_entries(doc, b.get("levels", TOC_LEVELS))
            plan["toc"] = {"entries": []}
            title = b.get("title", TOC_TITLE)
            if title:
                plan["aligns"].append({"block": len(blocks), "align": "center", "keep": True})
                plan["emphasis"].append({"block": len(blocks), "offset": 0, "count": len(title),
                                         "props": char_props({"bold": True, "size": TOC_TITLE_PT})})
                add_para({"type": "paragraph", "text": title})
            for e in entries:
                page = (toc_pages or {}).get(e["heading"], TOC_PLACEHOLDER)
                plan["toc"]["entries"].append({**e, "block": len(blocks), "page": page})
                plan["indents"].append({"block": len(blocks), "left": round((e["level"] - 1) * LIST_STEP_HU * scale),
                                        "indent": 0, "tab_right": True})
                add_para({"type": "paragraph", "text": f"{e['number']} {e['text']}\t{page}"})
            continue
        if b.get("type") == "list":
            markers = b.get("markers") or LIST_MARKERS
            for item in b["items"]:
                item = {"text": item} if isinstance(item, str) else dict(item)
                level = item.pop("level", 1)
                marker = markers[level - 1]
                left, hang = list_indent(level, marker, scale)
                plan["indents"].append({"block": len(blocks), "left": left, "indent": -hang})
                add_para({"type": "paragraph", **item}, marker + " ")
            continue
        if b.get("type") == "image":
            if pending_break:
                plan["page_breaks"].append(len(blocks))
                pending_break = False
            mark = IMAGE_MARK.format(n=len(plan["images"]))
            plan["images"].append({"block": len(blocks), "mark": mark, "path": b["path"],
                                   "width_mm": b.get("width_mm")})
            # 캡션이 있으면 그림 문단을 캡션과 같은 쪽에 묶는다
            plan["aligns"].append({"block": len(blocks), "align": b.get("align", "center"),
                                   "keep": bool(b.get("caption"))})
            blocks.append({"type": "paragraph", "text": mark})
            if b.get("caption"):
                counters["image"] += 1
                plan["aligns"].append({"block": len(blocks), "align": "center", "keep": False})
                blocks.append({"type": "paragraph", "text": number_caption(b["caption"], counters["image"])})
            continue
        if b.get("type") == "table" and b.get("caption"):
            # 표 캡션은 표 위 가운데 문단, 표와 같은 쪽에 묶는다
            counters["table"] += 1
            plan["aligns"].append({"block": len(blocks), "align": "center", "keep": True})
            add_para({"type": "paragraph", "text": number_caption(b["caption"], counters["table"])})
            b = {k: v for k, v in b.items() if k != "caption"}
        if b.get("type") == "table":
            ntab = sum(1 for x in blocks if x.get("type") == "table")
            style = {k: b[k] for k in TABLE_STYLE_KEYS if k in b}
            b = {k: v for k, v in b.items() if k not in TABLE_STYLE_KEYS}
            # 용지가 바뀌면 꾸밈 없는 표도 새 본문 폭으로 맞추므로 모든 표를 적는다
            merges = [{"row": m["row"], "col": m["col"], "rowspan": m.get("rowspan", 1),
                       "colspan": m.get("colspan", 1)} for m in style.get("merges") or []]
            cell_marks = []
            for m in style.get("cell_emphasis") or []:
                cell = b["rows"][m["row"]][m["col"]] if m["col"] < len(b["rows"][m["row"]]) else ""
                start = cell.index(m["text"]) if m.get("text") else 0
                count = len(m["text"]) if m.get("text") else len(cell)
                cell_marks.append({"row": m["row"], "col": m["col"], "start": start, "count": count,
                                   "props": char_props(m)})
            plan["tables"].append({
                "table": ntab, "cols": max(len(r) for r in b["rows"]), "rows": len(b["rows"]),
                "pt": (doc.get("style") or {}).get("table_pt"),
                "font": (doc.get("style") or {}).get("table_font"),
                "header_fill": (style.get("header_fill") or HEADER_FILL).upper()
                if style.get("header_row") else None,
                "col_widths": style.get("col_widths"), "merges": merges, "cell_emphasis": cell_marks})
        add_para(b)
    spec["blocks"] = blocks
    return spec, plan


_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
          (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
_GANADA = "가나다라마바사아자차카타파하"


def format_number(n: int, fmt: str) -> str:
    """번호 글자 — rhwp 조판(renderer format_number)과 같은 규칙. 범위 밖은 아라비아 숫자."""
    if fmt in ("roman_capital", "roman_small") and 0 < n < 4000:
        out, k = "", n
        for v, sym in _ROMAN:
            while k >= v:
                out, k = out + sym, k - v
        return out if fmt == "roman_capital" else out.lower()
    if fmt in ("latin_capital", "latin_small") and n > 0:
        out, k = "", n
        while k > 0:
            k -= 1
            out = chr(ord("A") + k % 26) + out
            k //= 26
        return out if fmt == "latin_capital" else out.lower()
    if fmt == "hangul_syllable" and 1 <= n <= len(_GANADA):
        return _GANADA[n - 1]
    if fmt == "circled_digit" and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    return str(n)


def outline_number(doc: dict, counts: List[int], level: int) -> str:
    """제목 번호 글자. outline_numbering 이 없으면 scaffold 기본(1. / 1.1. / 1.1.1.)."""
    levels = doc.get("outline_numbering")
    if not levels:
        return ".".join(str(counts[k]) for k in range(1, level + 1)) + "."
    text, fmt = hds.OUTLINE_DEFAULTS[level - 1]
    if level <= len(levels):
        text, fmt = levels[level - 1]["text"], levels[level - 1].get("num_format", "digit")
    fmts = [(levels[k - 1].get("num_format", "digit") if k <= len(levels) else hds.OUTLINE_DEFAULTS[k - 1][1])
            for k in range(1, 8)]
    return re.sub(r"\^([1-7])", lambda m: format_number(counts[int(m.group(1))], fmts[int(m.group(1)) - 1]), text)


def toc_entries(doc: dict, levels: int) -> List[dict]:
    """목차 항목: 제목 블록 순번(heading), 단계, 개요 번호, 글.

    번호는 산출물 개요 번호와 같은 셈이다 — outline_numbering 이 있으면 그 모양(Ⅰ. 가. …),
    없으면 scaffold 기본(1. / 1.1. / 1.1.1. …)."""
    out, counts, nh = [], [0] * 8, 0
    for b in doc.get("blocks", []):
        if b.get("type") != "heading":
            continue
        level = b["level"]
        counts[level] += 1
        for k in range(level + 1, 8):
            counts[k] = 0
        if level <= levels:
            number = outline_number(doc, counts, level)
            out.append({"heading": nh, "level": level, "number": number, "text": b["text"]})
        nh += 1
    return out


_SUPPORT_CACHE: Dict[tuple, bool] = {}


def supports_outline_numbering(rhwp: str) -> bool:
    """이 rhwp scaffold 가 outline_numbering 을 받는가. 작은 명세로 한 번 만들어 보고 기억한다."""
    import tempfile
    key = ("outline_numbering", str(Path(rhwp).resolve()))
    if key not in _SUPPORT_CACHE:
        with tempfile.TemporaryDirectory(prefix="hwpm-probe-") as tmp:
            spec = Path(tmp) / "probe.json"
            spec.write_text(json.dumps({"version": "1", "outline_numbering": [{"text": "^1."}],
                                        "blocks": [{"type": "heading", "level": 1, "text": "probe"}]}),
                            encoding="utf-8")
            proc = subprocess.run([rhwp, "scaffold", str(spec), "-o", str(Path(tmp) / "probe.hwpx")],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            _SUPPORT_CACHE[key] = proc.returncode == 0
    return _SUPPORT_CACHE[key]


def supports_font_name(rhwp: str) -> bool:
    """이 rhwp 의 apply-char-format 이 fontName(글꼴 이름)을 받는가.

    모르는 props 키는 rhwp 가 조용히 무시하므로 종료 코드가 아니라 저장본의 글꼴로 판정한다."""
    import tempfile
    key = ("font_name", str(Path(rhwp).resolve()))
    if key not in _SUPPORT_CACHE:
        face = "hwpm-probe-font"
        with tempfile.TemporaryDirectory(prefix="hwpm-probe-") as tmp:
            spec, base, out = Path(tmp) / "probe.json", Path(tmp) / "probe.hwpx", Path(tmp) / "out.hwpx"
            spec.write_text(json.dumps({"version": "1", "blocks": [{"type": "paragraph", "text": "probe"}]}),
                            encoding="utf-8")
            ok = False
            run = dict(capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if subprocess.run([rhwp, "scaffold", str(spec), "-o", str(base)], **run).returncode == 0:
                proc = subprocess.run([rhwp, "edit", "apply-char-format", str(base), "--para", "0", "--props",
                                       json.dumps({"fontName": face}, separators=(",", ":")), "-o", str(out)], **run)
                if proc.returncode == 0 and out.exists():
                    shapes = char_shapes(out)
                    ok = any(shapes.get(cid, {}).get("font") == face for cid in char_styles(out)[0])
            _SUPPORT_CACHE[key] = ok
    return _SUPPORT_CACHE[key]


def heading_pages(hwpx: Path, rhwp: str, doc: dict) -> Dict[int, int]:
    """rhwp 쪽 나눔 결과에서 제목 블록 순번 → 쪽 번호(1부터)."""
    spec, _ = split(doc)
    paras = map_blocks(top_paragraphs(hwpx), spec["blocks"], bool(spec.get("title")))
    proc = subprocess.run([rhwp, "dump-pages", str(hwpx), "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"dump-pages 실패: {proc.stderr.strip()[-400:]}")
    first_page: Dict[int, int] = {}
    for i, page in enumerate(json.loads(proc.stdout)["pages"]):
        for col in page.get("columns", []):
            for item in col.get("items", []):
                if isinstance(item.get("paraIndex"), int):
                    first_page.setdefault(item["paraIndex"], i + 1)
    out, nh = {}, 0
    for bi, b in enumerate(spec["blocks"]):
        if b.get("type") == "heading":
            if paras[bi] is not None and paras[bi] in first_page:
                out[nh] = first_page[paras[bi]]
            nh += 1
    return out


def number_caption(caption: str, n: int) -> str:
    """캡션의 "{n}" 을 그 종류(그림·표)의 순번으로 바꾼다."""
    return caption.replace(CAPTION_TOKEN, str(n))


def marker_hu(marker: str) -> int:
    """글머리 글자들의 폭(HWPUNIT, 본문 10pt). 표에 없는 글자는 전각 1em, 반각 0.55em 으로 본다."""
    total = 0
    for ch in marker:
        if ch in MARKER_HU:
            total += MARKER_HU[ch]
        else:
            total += BODY_HEIGHT_HU if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 550
    return total


def list_indent(level: int, marker: str, scale: float = 1.0) -> tuple:
    """(왼쪽 여백, 내어쓰기) HWPUNIT. 둘째 줄부터 글머리 뒤 글자에 맞춘다.

    scale 은 본문 글자 크기 / 10pt 다(style.body_pt). 폭은 10pt 실측값을 그 비율로 키운다."""
    if scale == 1.0:
        return (level - 1) * LIST_STEP_HU, marker_hu(marker) + SPACE_HU
    return round((level - 1) * LIST_STEP_HU * scale), round((marker_hu(marker) + SPACE_HU) * scale)


def style_targets(spec: dict, plan: dict) -> List[tuple]:
    """style 을 받을 scaffold 문단: (블록 순번 또는 "title", 역할 "title"·"heading"·"body").

    제목은 title_*, 개요는 heading_*, 본문 문단(목록 항목·캡션 포함)은 body_* 를 받는다.
    그림 문단은 글자가 없어 뺀다. 줄 간격은 이 문단 모두에 준다."""
    st = plan.get("style") or {}
    if not st:
        return []
    images = {img["block"] for img in plan["images"]}
    out: List[tuple] = []
    if spec.get("title"):
        out.append(("title", "title"))
    for bi, b in enumerate(spec["blocks"]):
        if b.get("type") == "heading":
            out.append((bi, "heading"))
        elif b.get("type") == "paragraph" and bi not in images:
            out.append((bi, "body"))
    return out


# 문단 간격: apply-para-format 은 HWPUNIT 의 2배로 받는다(1pt = 100 HWPUNIT → 200, 여백과 같음)
SPACE_PROP_PER_PT = 200
SPACE_HU_PER_PT = 100


def role_char_props(st: dict, role: str) -> Optional[str]:
    """역할의 글자 크기·글꼴 → apply-char-format props (없으면 None)."""
    props: Dict[str, object] = {}
    if st.get(f"{role}_pt") is not None:
        props["fontSize"] = int(round(st[f"{role}_pt"] * 100))
    if st.get(f"{role}_font"):
        props["fontName"] = st[f"{role}_font"]
    return json.dumps(props, ensure_ascii=False, separators=(",", ":")) if props else None


def role_para_props(st: dict, role: str) -> Optional[str]:
    """줄 간격·역할의 문단 위·아래 간격 → apply-para-format props (없으면 None).

    개요(heading)는 다음 문단과 함께 둔다(keepWithNext) — 쪽 끝에 제목만 남는 외톨이 제목을 막는다."""
    props: Dict[str, object] = {}
    if role == "heading":
        props["keepWithNext"] = True
    if st.get("line_spacing") is not None:
        props.update({"lineSpacing": int(st["line_spacing"]), "lineSpacingType": "Percent"})
    for side, key in (("before", "spacingBefore"), ("after", "spacingAfter")):
        v = st.get(f"{role}_space_{side}")
        if v is not None:
            props[key] = int(round(v * SPACE_PROP_PER_PT))
    return json.dumps(props, separators=(",", ":")) if props else None


def style_fonts(doc: dict) -> List[str]:
    """style 에 쓴 역할별 글꼴 이름들."""
    st = doc.get("style") or {}
    return [st[k] for k in ("title_font", "heading_font", "body_font", "table_font") if st.get(k)]


def para_line_spacings(hwpx: Path) -> List[Optional[tuple]]:
    """최상위 문단마다 (줄 간격 종류, 값). paraPr 의 HwpUnitChar 갈래(첫 lineSpacing)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
        head = ET.fromstring(z.read("Contents/header.xml"))
    shapes = {}
    for pp in head.iter(f"{{{HH}}}paraPr"):
        ls = pp.find(f".//{{{HH}}}lineSpacing")
        if ls is not None:
            shapes[pp.get("id")] = (ls.get("type"), int(ls.get("value")))
    return [shapes.get(p.get("paraPrIDRef")) for p in root.findall(f"{{{HP}}}p")]


def para_spacings(hwpx: Path) -> List[Optional[tuple]]:
    """최상위 문단마다 (위 간격, 아래 간격) HWPUNIT. paraPr 의 첫 margin 갈래(HwpUnitChar)."""
    hc = "http://www.hancom.co.kr/hwpml/2011/core"
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
        head = ET.fromstring(z.read("Contents/header.xml"))
    shapes = {}
    for pp in head.iter(f"{{{HH}}}paraPr"):
        margin = pp.find(f".//{{{HH}}}margin")
        if margin is None:
            continue
        prev, nxt = margin.find(f"{{{hc}}}prev"), margin.find(f"{{{hc}}}next")
        shapes[pp.get("id")] = (int(prev.get("value")) if prev is not None else 0,
                                int(nxt.get("value")) if nxt is not None else 0)
    return [shapes.get(p.get("paraPrIDRef")) for p in root.findall(f"{{{HP}}}p")]


def char_props(mark: dict) -> str:
    """강조 → apply-char-format --props. 공백 없는 JSON 으로 넘긴다(rhwp #8439: "key": 공백이면 무시됨)."""
    props = {k: True for k in ("bold", "italic", "underline") if mark.get(k)}
    if mark.get("strike"):
        props["strikethrough"] = True
    if mark.get("color"):
        props["textColor"] = mark["color"].upper()
    if mark.get("highlight"):
        props["shadeColor"] = mark["highlight"].upper()
    if mark.get("size"):
        props["fontSize"] = int(round(mark["size"] * 100))
    return json.dumps(props, separators=(",", ":"))


def props_match(shape: dict, want: dict) -> bool:
    """char_shapes 한 항목이 apply-char-format props 를 모두 만족하는가."""
    for k, v in want.items():
        got = {"textColor": shape.get("color"), "shadeColor": shape.get("shade"),
               "fontSize": shape.get("height"), "strikethrough": shape.get("strike")}.get(k, shape.get(k))
        if (got != v) if k in ("textColor", "shadeColor", "fontSize") else not got:
            return False
    return True


def table_styled(tb: dict) -> bool:
    return bool(tb["header_fill"] or tb["col_widths"] or tb["merges"] or tb["cell_emphasis"])


def plan_empty(plan: dict) -> bool:
    # 표가 하나라도 있으면 "쪽 영역 안으로 제한"을 켜야 하므로 편집이 필요하다
    return not (plan["page_breaks"] or plan["footnotes"] or plan["emphasis"] or plan["images"]
                or plan["indents"] or plan["tables"] or plan["page"]
                or plan["header"] or plan["footer"] or plan.get("style"))


def page_props(doc: dict) -> Optional[dict]:
    """orientation·margins_mm → edit set-page-def props (HWPUNIT). 기본 용지면 None."""
    if doc.get("orientation") != "landscape" and not doc.get("margins_mm"):
        return None
    props: Dict[str, object] = {}
    if doc.get("orientation") == "landscape":
        # 너비·높이는 scaffold 가 만든 세로 치수(A4 는 한컴 상수 59528×84188)를 그대로 두고 돌리기만 한다
        props["landscape"] = True
    for key, value in (doc.get("margins_mm") or {}).items():
        props["margin" + key.capitalize()] = round(value * HU_PER_MM)
    return props


def table_total_hu(doc: dict) -> int:
    """scaffold 와 같은 셈의 표 폭: 본문 폭 - 표 바깥 여백 1mm × 2 (rhwp scaffold builder)."""
    return max(round(hds.page_geometry(doc)["content_mm"] * HU_PER_MM) - TABLE_OUTER_HU, 7200)


def image_size_px(path: Path) -> Optional[tuple]:
    """PNG·JPEG 헤더만 읽어 (가로, 세로) 픽셀. 모르면 None."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + seg
    return None


def image_width_hu(doc: dict, img: dict) -> int:
    """그림 너비(HWPUNIT). width_mm 가 없으면 원본 픽셀 크기, 본문 폭을 넘으면 본문 폭."""
    content_mm = hds.page_geometry(doc)["content_mm"]
    if img.get("width_mm"):
        return round(img["width_mm"] * HU_PER_MM)
    size = image_size_px(Path(img["path"]))
    natural = size[0] * HU_PER_PX if size else round(content_mm * HU_PER_MM)
    return min(natural, round(content_mm * HU_PER_MM))


def resolve_images(doc: dict, base: Path) -> dict:
    """image.path 를 base 기준 절대 경로로 바꾼 사본. 빌드가 명세를 저장하기 전에 부른다."""
    out = copy.deepcopy(doc)
    for b in out.get("blocks", []):
        if isinstance(b, dict) and b.get("type") == "image" and isinstance(b.get("path"), str):
            b["path"] = str((base / b["path"]).resolve())
    return out


def _t_text(t) -> str:
    """hp:t 의 글자. 안에 든 hp:tab 은 "\t" 로, 다른 인라인 요소는 건너뛰고 꼬리 글자는 잇는다."""
    out = [t.text or ""]
    for child in t:
        if child.tag == f"{{{HP}}}tab":
            out.append("\t")
        elif child.tag == f"{{{HP}}}lineBreak":
            out.append("\n")  # HDS 글의 "\n" 은 scaffold 에서 줄 바꿈 요소가 된다 — 같은 글로 맞춘다
        out.append(child.tail or "")
    return "".join(out)


def top_paragraphs(hwpx: Path) -> List[dict]:
    """구역 0 의 최상위 문단 목록: 글자(본문 run 의 hp:t 만)와 표 포함 여부."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = []
    for p in root.findall(f"{{{HP}}}p"):
        text = "".join(_t_text(t) for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
        out.append({"text": text, "table": p.find(f".//{{{HP}}}tbl") is not None,
                    "pic": p.find(f".//{{{HP}}}pic") is not None})
    return out


def _top_tables(root) -> list:
    """구역 최상위 문단에 직접 든 표(hp:tbl) — rhwp edit 의 --table 번호 순서."""
    return [t for p in root.findall(f"{{{HP}}}p") for run in p.findall(f"{{{HP}}}run")
            for t in run.findall(f"{{{HP}}}tbl")]


def table_widths(hwpx: Path) -> List[int]:
    """최상위 표마다 전체 폭(HWPUNIT)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    return [int(t.find(f"{{{HP}}}sz").get("width")) for t in _top_tables(root)]


# #42: 같은 문단 서식을 묶음마다 rhwp 한 번으로(batched_para_format), 이미 맞는 표 속성은 건너뛰기.
# False 면 예전처럼 한 단계씩 돈다(같은 결과인지 대조할 때 쓴다).
BATCH_EDITS = True


def tables_in_page(hwpx: Path) -> List[bool]:
    """최상위 표마다 이미 쪽 영역 안으로 제한돼 있는가(hp:pos flowWithText="1"). 그렇다면 set-table-props
    restrictInPage 는 산출 XML 을 바꾸지 않으므로 건너뛴다(rhwp 0.9 scaffold 는 처음부터 1 로 쓴다)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = []
    for t in _top_tables(root):
        pos = t.find(f"{{{HP}}}pos")
        out.append(pos is not None and pos.get("flowWithText") == "1")
    return out


_P_TAG = re.compile(r"<hp:p\b[^>]*?/?>|</hp:p>")


def _top_p_spans(xml: str) -> List[tuple]:
    """구역 XML 에서 최상위 <hp:p ...> 시작 태그의 (시작, 끝) 위치 (표·글상자 안 문단은 빼고)."""
    spans, depth = [], 0
    for m in _P_TAG.finditer(xml):
        tag = m.group(0)
        if tag.startswith("</"):
            depth -= 1
        else:
            if depth == 0:
                spans.append(m.span())
            if not tag.endswith("/>"):
                depth += 1
    return spans


def para_pr_refs(hwpx: Path) -> List[Optional[str]]:
    """구역 0 최상위 문단마다 paraPrIDRef."""
    with zipfile.ZipFile(hwpx) as z:
        xml = z.read("Contents/section0.xml").decode("utf-8")
    out = []
    for s, e in _top_p_spans(xml):
        m = re.search(r'\sparaPrIDRef="(\d+)"', xml[s:e])
        out.append(m.group(1) if m else None)
    return out


def set_para_pr_refs(src: Path, dst: Path, refs: Dict[int, str]) -> None:
    """최상위 문단 paraPrIDRef 만 바꿔 dst 에 쓴다(다른 바이트는 그대로)."""
    with zipfile.ZipFile(src) as z:
        entries = [(info, z.read(info.filename)) for info in z.infolist()]
    out = []
    for info, data in entries:
        if info.filename == "Contents/section0.xml":
            xml = data.decode("utf-8")
            pieces, last = [], 0
            for k, (s, e) in enumerate(_top_p_spans(xml)):
                if k in refs:
                    tag = re.sub(r'(\sparaPrIDRef=")\d+(")', rf'\g<1>{refs[k]}\g<2>', xml[s:e], count=1)
                    pieces += [xml[last:s], tag]
                    last = e
            data = ("".join(pieces) + xml[last:]).encode("utf-8")
        out.append((info, data))
    with zipfile.ZipFile(dst, "w") as z:
        for info, data in out:
            z.writestr(info, data, compress_type=info.compress_type)


def _balanced(xml: str, start: int, tag: str) -> int:
    """xml[start] 의 <hp:tag ...> 와 짝이 맞는 </hp:tag> 끝 위치(같은 태그 중첩을 센다)."""
    pat = re.compile(rf"<hp:{tag}\b[^>]*?/?>|</hp:{tag}>")
    depth = 0
    for m in pat.finditer(xml, start):
        t = m.group(0)
        if t.startswith("</"):
            depth -= 1
            if depth == 0:
                return m.end()
        elif not t.endswith("/>"):
            depth += 1
    return len(xml)


def _top_table_cells(xml: str) -> List[Dict[tuple, tuple]]:
    """최상위 표마다 {(행, 열): (칸 시작, 칸 끝)} — rhwp --table 번호 순서. 안쪽 표의 칸은 빼고."""
    tables = []
    for ps, pe in _top_p_spans(xml):
        p_end = _balanced(xml, ps, "p")
        pos = pe
        while True:
            m = re.compile(r"<hp:tbl\b").search(xml, pos, p_end)
            if not m:
                break
            t_end = _balanced(xml, m.start(), "tbl")
            cells, k = {}, xml.index(">", m.start()) + 1
            while True:
                c = re.compile(r"<hp:tc\b|<hp:tbl\b").search(xml, k, t_end)
                if not c:
                    break
                if c.group(0) == "<hp:tbl":  # 칸 안의 표는 건너뛴다
                    k = _balanced(xml, c.start(), "tbl")
                    continue
                c_end = _balanced(xml, c.start(), "tc")
                addr = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', xml[c.start():c_end])
                if addr:
                    cells[(int(addr.group(2)), int(addr.group(1)))] = (c.start(), c_end)
                k = c_end
            tables.append(cells)
            pos = t_end
    return tables


def _inner_regions(body: str, tags: tuple) -> List[tuple]:
    """body 안에서 tags(예: tbl·subList)로 감싼 구역들 — 그 안의 문단·run 은 바깥 것이 아니다."""
    regions = []
    for tag in tags:
        for m in re.finditer(rf"<hp:{tag}\b", body):
            regions.append((m.start(), _balanced(body, m.start(), tag)))
    return regions


def top_para_runs(xml: str) -> List[tuple]:
    """최상위 문단마다 자기 run 의 charPrIDRef 들(표·글상자·각주 안 run 은 빼고)."""
    out = []
    for s, _ in _top_p_spans(xml):
        e = _balanced(xml, s, "p")
        body = xml[s:e]
        inner = _inner_regions(body, ("tbl", "subList"))
        out.append(tuple(m.group(1) for m in re.finditer(r'<hp:run\b[^>]*?\scharPrIDRef="(\d+)"', body)
                         if not any(a <= m.start() < b for a, b in inner)))
    return out


def set_top_para_runs(xml: str, runs: Dict[int, tuple]) -> str:
    """최상위 문단 k 의 자기 run charPrIDRef 를 runs[k] 차례대로 바꾼다."""
    spans = _top_p_spans(xml)
    for k in sorted(runs, reverse=True):
        s = spans[k][0]
        e = _balanced(xml, s, "p")
        body = xml[s:e]
        inner = _inner_regions(body, ("tbl", "subList"))
        want, pieces, last, i = runs[k], [], 0, 0
        for m in re.finditer(r'(<hp:run\b[^>]*?\scharPrIDRef=")(\d+)(")', body):
            if any(a <= m.start() < b for a, b in inner):
                continue
            pieces += [body[last:m.start()], f"{m.group(1)}{want[i] if i < len(want) else m.group(2)}{m.group(3)}"]
            last, i = m.end(), i + 1
        xml = xml[:s] + "".join(pieces) + body[last:] + xml[e:]
    return xml


def _cell_state(xml: str, span: tuple) -> tuple:
    """칸 하나의 서식 상태: (tc 시작 태그의 header·borderFillIDRef, 칸 문단 paraPrIDRef 들, run charPrIDRef 들).
    안쪽 표 안의 것은 빼고 본다."""
    s, e = span
    body = xml[s:e]
    inner = [m.span() for m in re.finditer(r"<hp:tbl\b", body)]
    for k, (a, _) in enumerate(inner):
        inner[k] = (a, _balanced(body, a, "tbl"))
    def outside(i):
        return not any(a <= i < b for a, b in inner)
    head = re.match(r"<hp:tc\b[^>]*>", body).group(0)
    tc = (re.search(r'\sheader="(\d)"', head).group(1) if 'header="' in head else None,
          re.search(r'\sborderFillIDRef="(\d+)"', head).group(1) if "borderFillIDRef" in head else None)
    paras = tuple(m.group(1) for m in re.finditer(r'<hp:p\b[^>]*?\sparaPrIDRef="(\d+)"', body) if outside(m.start()))
    runs = tuple(m.group(1) for m in re.finditer(r'<hp:run\b[^>]*?\scharPrIDRef="(\d+)"', body) if outside(m.start()))
    return tc, paras, runs


def _rewrite_cell(body: str, want: tuple) -> str:
    """칸 XML 을 want(= _cell_state 꼴) 대로 고친다. 안쪽 표 안은 건드리지 않는다."""
    (header, fill), paras, runs = want
    inner = []
    for m in re.finditer(r"<hp:tbl\b", body):
        inner.append((m.start(), _balanced(body, m.start(), "tbl")))
    head_end = body.index(">") + 1
    head = body[:head_end]
    if header is not None:
        head = re.sub(r'(\sheader=")\d(")', rf"\g<1>{header}\g<2>", head, count=1)
    if fill is not None:
        head = re.sub(r'(\sborderFillIDRef=")\d+(")', rf"\g<1>{fill}\g<2>", head, count=1)
    out, last, pi, ri = [head], head_end, 0, 0
    pat = re.compile(r'(<hp:p\b[^>]*?\sparaPrIDRef=")(\d+)(")|(<hp:run\b[^>]*?\scharPrIDRef=")(\d+)(")')
    for m in pat.finditer(body, head_end):
        if any(a <= m.start() < b for a, b in inner):
            continue
        out.append(body[last:m.start()])
        if m.group(1):
            out.append(f"{m.group(1)}{paras[pi] if pi < len(paras) else m.group(2)}{m.group(3)}")
            pi += 1
        else:
            out.append(f"{m.group(4)}{runs[ri] if ri < len(runs) else m.group(5)}{m.group(6)}")
            ri += 1
        last = m.end()
    out.append(body[last:])
    return "".join(out)


# 각주 묶음 삽입(#50): rhwp 로 넣은 각주 하나를 본으로 삼아 나머지는 구역 XML 에 바로 쓴다.
_FN_TAB = '<hp:tab width="0" leader="0" type="1"/>'  # rhwp 가 글 속 탭을 쓰는 꼴
_FN_RUN = re.compile(r'<hp:run charPrIDRef="\d+">((?:<hp:t>(?:[^<]|<hp:tab\b[^>]*/>)*</hp:t>)*)</hp:run>')
_FN_TOKEN = re.compile(r"<hp:tab\b[^>]*/>|&[a-z]+;|&#x?[0-9a-fA-F]+;|[^<&]")
_FN_TEXT_OK = re.compile(r"[^\x00-\x08\x0a-\x1f\U00010000-\U0010ffff]*")


def _fn_escape(text: str) -> str:
    return "".join(_FN_TAB if ch == "\t" else escape(ch, {'"': "&quot;", "'": "&apos;"}) for ch in text)


def footnote_para_simple(xml: str, k: int) -> bool:
    """최상위 문단 k 가 글(hp:t·탭)만 든 run 들로만 되어 있나 — 각주를 XML 로 바로 넣을 수 있는 꼴."""
    spans = _top_p_spans(xml)
    s, e = spans[k]
    end = _balanced(xml, s, "p")
    if xml[s:e].endswith("/>"):
        return False
    body = xml[e:end - len("</hp:p>")]
    pos = 0
    for m in _FN_RUN.finditer(body):
        if m.start() != pos:
            return False
        pos = m.end()
    if pos != len(body):
        return False
    return all(len(tok) == 1 or tok.startswith(("<", "&")) for tok in _FN_TOKEN.findall(body)) and \
        not re.search(r"[\U00010000-\U0010ffff]", body)


def footnote_template(xml: str, k: int) -> str:
    """최상위 문단 k 의 첫 각주 컨트롤 XML(줄 배치 정보는 뺀다 — 한컴·rhwp 가 다시 계산한다)."""
    s = _top_p_spans(xml)[k][0]
    body = xml[s:_balanced(xml, s, "p")]
    a = body.index("<hp:ctrl><hp:footNote ")
    b = _balanced(body, a, "ctrl")
    return re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", body[a:b], flags=re.S)


def _fn_from_template(template: str, text: str) -> str:
    head, sep, rest = template.partition("</hp:autoNum></hp:ctrl><hp:t>")
    if not sep:
        raise ValueError("각주 본에 번호 뒤 글이 없음")
    return f"{head}{sep} {_fn_escape(text)}</hp:t>{rest[rest.index('</hp:t>') + len('</hp:t>'):]}"


def insert_footnotes_xml(xml: str, notes: Dict[int, List[tuple]], template: str) -> str:
    """notes = {최상위 문단: [(글자 오프셋, 각주 글)]} 를 rhwp insert-footnote 처럼 넣는다.

    각주 컨트롤은 그 자리 글이 든 run 안에 넣고 hp:t 를 둘로 가른다. run 경계면 앞 run 의 끝에 붙는다.
    번호(footNote number·autoNum num)와 문단 id 는 rhwp 처럼 문서 차례로 다시 매긴다."""
    spans = _top_p_spans(xml)
    for k in sorted(notes, reverse=True):
        s, e = spans[k]
        end = _balanced(xml, s, "p") - len("</hp:p>")
        body = xml[e:end]
        for offset, text in sorted(notes[k], reverse=True):
            ctrl = _fn_from_template(template, text)
            pos, done = 0, False
            ts = [m for m in re.finditer(r"<hp:t>((?:[^<]|<hp:tab\b[^>]*/>)*)</hp:t>", body)]
            for m in ts:
                toks = _FN_TOKEN.findall(m.group(1))
                if pos + len(toks) >= offset:
                    cut = offset - pos
                    left, right = "".join(toks[:cut]), "".join(toks[cut:])
                    new = (f"<hp:t>{left}</hp:t>" if left else "") + ctrl + (f"<hp:t>{right}</hp:t>" if right else "")
                    body = body[:m.start()] + new + body[m.end():]
                    done = True
                    break
                pos += len(toks)
            if not done:
                raise ValueError(f"각주 자리 {offset} 가 문단 {k} 의 글보다 뒤")
        xml = xml[:e] + body + xml[end:]
    num = iter(range(1, 1 << 30))
    xml = re.sub(r'(<hp:footNote number=")\d+(")', lambda m: f"{m.group(1)}{next(num)}{m.group(2)}", xml)
    num = iter(range(1, 1 << 30))
    xml = re.sub(r'(<hp:autoNum num=")\d+(" numType="FOOTNOTE")', lambda m: f"{m.group(1)}{next(num)}{m.group(2)}", xml)
    return renumber_para_ids(xml)


def renumber_para_ids(xml: str) -> str:
    """문단 id 를 rhwp 저장 차례(문단이 닫히는 차례, 0 부터)로 다시 매긴다."""
    stack, ids, nxt = [], {}, 0
    for m in _P_TAG.finditer(xml):
        tag = m.group(0)
        if tag.startswith("</"):
            ids[stack.pop()] = nxt
            nxt += 1
        elif tag.endswith("/>"):
            ids[m.start()] = nxt
            nxt += 1
        else:
            stack.append(m.start())
    out, last = [], 0
    for start in sorted(ids):
        tag_end = xml.index(">", start)
        tag = re.sub(r'^(<hp:p id=")\d+(")', rf"\g<1>{ids[start]}\g<2>", xml[start:tag_end])
        out += [xml[last:start], tag]
        last = tag_end
    out.append(xml[last:])
    return "".join(out)


def read_section(hwpx: Path) -> str:
    with zipfile.ZipFile(hwpx) as z:
        return z.read("Contents/section0.xml").decode("utf-8")


def write_section(src: Path, dst: Path, xml: str) -> None:
    with zipfile.ZipFile(src) as z:
        entries = [(info, z.read(info.filename)) for info in z.infolist()]
    with zipfile.ZipFile(dst, "w") as z:
        for info, data in entries:
            if info.filename == "Contents/section0.xml":
                data = xml.encode("utf-8")
            z.writestr(info, data, compress_type=info.compress_type)


def covered_cells(merges: List[dict]) -> set:
    """병합으로 가려지는 셀 (row, col) — 병합 첫 셀은 빠진다."""
    return {(r, c) for m in merges for r in range(m["row"], m["row"] + m["rowspan"])
            for c in range(m["col"], m["col"] + m["colspan"])} - {(m["row"], m["col"]) for m in merges}


def table_flow(hwpx: Path) -> List[bool]:
    """최상위 표마다 쪽 영역 안으로 제한(flowWithText="1")인가."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    return [t.find(f"{{{HP}}}pos").get("flowWithText") == "1" for t in _top_tables(root)]


def split_widths(total: int, ratios: List[float]) -> List[int]:
    """total 을 비율대로 나눈 정수 폭. 반올림 나머지는 마지막 열에 준다."""
    s = float(sum(ratios))
    out = [round(total * r / s) for r in ratios[:-1]]
    return out + [total - sum(out)]


def table_cells(hwpx: Path) -> List[List[dict]]:
    """최상위 표마다 셀 목록: row·col·header·borderFill·폭·첫 글자 charPr."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = []
    for t in _top_tables(root):
        cells = []
        for tc in t.iter(f"{{{HP}}}tc"):
            addr, size = tc.find(f"{{{HP}}}cellAddr"), tc.find(f"{{{HP}}}cellSz")
            runs = [r for r in tc.iter(f"{{{HP}}}run") if r.find(f"{{{HP}}}t") is not None]
            span = tc.find(f"{{{HP}}}cellSpan")
            first = tc.find(f".//{{{HP}}}p")
            chars = [] if first is None else [
                r.get("charPrIDRef") for r in first.findall(f"{{{HP}}}run")
                for t in r.findall(f"{{{HP}}}t") for _ in (t.text or "")]
            cells.append({"row": int(addr.get("rowAddr")), "col": int(addr.get("colAddr")),
                          "rowspan": int(span.get("rowSpan")), "colspan": int(span.get("colSpan")),
                          "chars": chars,
                          "header": tc.get("header") == "1", "border_fill": tc.get("borderFillIDRef"),
                          "width": int(size.get("width")),
                          "char_prs": [r.get("charPrIDRef") for r in runs],
                          "para_pr": (tc.find(f".//{{{HP}}}p").get("paraPrIDRef")
                                      if tc.find(f".//{{{HP}}}p") is not None else None)})
        out.append(cells)
    return out


def fill_colors(hwpx: Path) -> Dict[str, Optional[str]]:
    """header.xml borderFill id → 면 색(#RRGGBB) 또는 None."""
    hc = "http://www.hancom.co.kr/hwpml/2011/core"
    with zipfile.ZipFile(hwpx) as z:
        head = ET.fromstring(z.read("Contents/header.xml"))
    out = {}
    for bf in head.iter(f"{{{HH}}}borderFill"):
        brush = bf.find(f".//{{{hc}}}winBrush")
        out[bf.get("id")] = brush.get("faceColor").upper() if brush is not None else None
    return out


def para_aligns(hwpx: Path) -> Dict[str, str]:
    """header.xml paraPr id → 가로 정렬 (JUSTIFY·CENTER …)."""
    with zipfile.ZipFile(hwpx) as z:
        head = ET.fromstring(z.read("Contents/header.xml"))
    out = {}
    for pp in head.iter(f"{{{HH}}}paraPr"):
        al = pp.find(f"{{{HH}}}align")
        out[pp.get("id")] = al.get("horizontal") if al is not None else None
    return out


def hf_aligns(hwpx: Path) -> Dict[str, Optional[str]]:
    """구역 0 머리말/꼬리말 첫 문단의 가로 정렬 ({"header": "CENTER", ...})."""
    aligns = para_aligns(hwpx)
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = {}
    for kind in ("header", "footer"):
        el = root.find(f".//{{{HP}}}{kind}")
        p = el.find(f".//{{{HP}}}p") if el is not None else None
        if p is not None:
            out[kind] = aligns.get(p.get("paraPrIDRef"))
    return out


def page_def(hwpx: Path) -> dict:
    """구역 0 용지: set-page-def props 와 같은 키 (HWPUNIT)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    pp = root.find(f".//{{{HP}}}pagePr")
    mg = pp.find(f"{{{HP}}}margin")
    out = {"width": int(pp.get("width")), "height": int(pp.get("height")),
           "landscape": pp.get("landscape") == "NARROWLY"}
    for key in ("left", "right", "top", "bottom", "header", "footer"):
        out["margin" + key.capitalize()] = int(mg.get(key))
    return out


def para_layout(hwpx: Path) -> List[Optional[tuple]]:
    """최상위 문단마다 (가로 정렬, 다음 문단과 함께)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
        head = ET.fromstring(z.read("Contents/header.xml"))
    shapes = {}
    for pp in head.iter(f"{{{HH}}}paraPr"):
        al, br = pp.find(f"{{{HH}}}align"), pp.find(f"{{{HH}}}breakSetting")
        shapes[pp.get("id")] = (al.get("horizontal") if al is not None else None,
                                br is not None and br.get("keepWithNext") == "1")
    return [shapes.get(p.get("paraPrIDRef")) for p in root.findall(f"{{{HP}}}p")]


def para_margins(hwpx: Path) -> List[Optional[tuple]]:
    """최상위 문단마다 (왼쪽 여백, 들여쓰기) HWPUNIT. paraPr 의 HwpUnitChar 갈래(한컴 2024 가 쓰는 값)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
        head = ET.fromstring(z.read("Contents/header.xml"))
    hc = "http://www.hancom.co.kr/hwpml/2011/core"
    shapes = {}
    for pp in head.iter(f"{{{HH}}}paraPr"):
        margin = pp.find(f".//{{{HH}}}margin")
        if margin is None:
            continue
        left, intent = margin.find(f"{{{hc}}}left"), margin.find(f"{{{hc}}}intent")
        if left is not None and intent is not None:
            shapes[pp.get("id")] = (int(left.get("value")), int(intent.get("value")))
    return [shapes.get(p.get("paraPrIDRef")) for p in root.findall(f"{{{HP}}}p")]


def char_styles(hwpx: Path) -> List[List[str]]:
    """최상위 문단마다 본문 글자별 charPrIDRef 목록 (top_paragraphs 의 text 와 같은 길이)."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = []
    for p in root.findall(f"{{{HP}}}p"):
        ids: List[str] = []
        for run in p.findall(f"{{{HP}}}run"):
            for t in run.findall(f"{{{HP}}}t"):
                ids.extend([run.get("charPrIDRef")] * len(t.text or ""))
        out.append(ids)
    return out


def char_shapes(hwpx: Path) -> Dict[str, dict]:
    """header.xml charPr id → {bold, italic, underline, strike, color, shade, height, font(한글 글꼴)}."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/header.xml"))
    hangul = {}
    for ff in root.iter(f"{{{HH}}}fontface"):
        if ff.get("lang") == "HANGUL":
            hangul = {f.get("id"): f.get("face") for f in ff.findall(f"{{{HH}}}font")}
    out = {}
    for cp in root.iter(f"{{{HH}}}charPr"):
        ref = cp.find(f"{{{HH}}}fontRef")
        ul = cp.find(f"{{{HH}}}underline")
        so = cp.find(f"{{{HH}}}strikeout")
        out[cp.get("id")] = {"bold": cp.find(f"{{{HH}}}bold") is not None,
                             "italic": cp.find(f"{{{HH}}}italic") is not None,
                             "underline": ul is not None and ul.get("type", "NONE") != "NONE",
                             "strike": so is not None and so.get("shape", "NONE") != "NONE",
                             "color": (cp.get("textColor") or "").upper(),
                             "shade": (cp.get("shadeColor") or "").upper(),
                             "height": int(cp.get("height") or 0),
                             "font": hangul.get(ref.get("hangul")) if ref is not None else None}
    return out


def map_blocks(paras: List[dict], blocks: List[dict], has_title: bool) -> List[Optional[int]]:
    """scaffold 블록 순서대로 대응하는 최상위 문단 번호. 못 찾으면 None."""
    out: List[Optional[int]] = []
    i = 1 if has_title else 0
    for b in blocks:
        found = None
        for j in range(i, len(paras)):
            p = paras[j]
            text = b.get("text") or ""
            # 그림 표시 글자는 산출물에서 지워지고 그 문단에 그림이 들어간다
            image = IMAGE_MARK_RE.fullmatch(text) is not None
            if (b.get("type") == "table" and p["table"]) or (
                    b.get("type") != "table" and not p["table"]
                    and (p["text"] == text or (image and p.get("pic") and not p["text"]))):
                found = j
                break
        out.append(found)
        if found is not None:
            i = found + 1
    return out


def _edit(rhwp: str, args: List[str], log: List[dict]) -> bool:
    cmd = [rhwp, "edit"] + args + ["--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    entry = {"cmd": ["edit"] + args, "exit": proc.returncode}
    if proc.returncode != 0:
        entry["stderr"] = proc.stderr.strip()[-800:]
    log.append(entry)
    return proc.returncode == 0


def _hf(rhwp: str, kind: str, text: str, src: Path, work: Path, n: int, log: List[dict]) -> Optional[Path]:
    flag = f"--{kind}"
    before, sep, after = text.partition(PAGE_TOKEN)
    cur = src
    nxt = work / f"e{n:02d}a.hwpx"
    if not _edit(rhwp, ["insert-header-footer", str(cur), flag, "-o", str(nxt)], log):
        return None
    cur = nxt
    if before + after:
        nxt = work / f"e{n:02d}b.hwpx"
        if not _edit(rhwp, ["insert-header-footer-text", str(cur), flag, "--text", before + after,
                            "-o", str(nxt)], log):
            return None
        cur = nxt
    if sep:
        nxt = work / f"e{n:02d}c.hwpx"
        if not _edit(rhwp, ["insert-field-in-hf", str(cur), flag, "--field-type", "1",
                            "--offset", str(len(before)), "-o", str(nxt)], log):
            return None
        cur = nxt
    if kind in HF_CENTER:
        nxt = work / f"e{n:02d}d.hwpx"
        if not _edit(rhwp, ["apply-para-format-in-hf", str(cur), flag,
                            "--props", '{"alignment":"center"}', "-o", str(nxt)], log):
            return None
        cur = nxt
    return cur


def produce(doc: dict, out: Path, rhwp: str, work: Path) -> dict:
    """HDS → out. 반환: {"ok", "scaffold": {...}, "edits": [...], "error"?}.

    목차가 있으면 두 번 만든다: 쪽 번호 자리표시로 한 번 만들어 rhwp 쪽 나눔에서 제목 쪽을 읽고,
    그 쪽 번호를 넣어 다시 만든다(목차 줄 수는 같으므로 쪽 나눔이 바뀌지 않는다)."""
    if not any(b.get("type") == "toc" for b in doc.get("blocks", [])):
        return _produce_once(doc, out, rhwp, work, None)
    first = _produce_once(doc, work / "toc-pass1.hwpx", rhwp, work / "pass1", None)
    if not first["ok"]:
        return first
    try:
        pages = heading_pages(work / "toc-pass1.hwpx", rhwp, doc)
    except RuntimeError as exc:
        return {"ok": False, "scaffold": first["scaffold"], "edits": first["edits"], "error": str(exc)}
    result = _produce_once(doc, out, rhwp, work / "pass2", pages)
    result["toc_pages"] = pages
    return result


def _produce_once(doc: dict, out: Path, rhwp: str, work: Path, toc_pages: Optional[Dict[int, int]]) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    spec, plan = split(doc, toc_pages)
    spec_path = work / "scaffold.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    first = out if plan_empty(plan) else work / "scaffold.hwpx"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([rhwp, "scaffold", str(spec_path), "-o", str(first), "--json"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    result: dict = {"ok": False, "scaffold": {"exit": proc.returncode}, "edits": []}
    if proc.returncode != 0:
        result["scaffold"]["stderr"] = proc.stderr.strip()[-2000:]
        result["error"] = "scaffold 실패"
        return result
    if plan_empty(plan):
        result["ok"] = True
        return result

    paras = map_blocks(top_paragraphs(first), spec["blocks"], bool(spec.get("title")))
    targets = style_targets(spec, plan)
    need = set(plan["page_breaks"]) | {f["block"] for f in plan["footnotes"] + plan["emphasis"]
                                       + plan["images"] + plan["aligns"]
                                       + plan["indents"]} | {t for t, _ in targets if t != "title"}
    missing = sorted(i for i in need if paras[i] is None)
    if missing:
        result["error"] = f"scaffold 산출물에서 블록 문단을 찾지 못함: blocks {missing}"
        return result

    log = result["edits"]
    cur, n = first, 0

    def step(args: List[str]) -> bool:
        nonlocal cur, n
        n += 1
        nxt = work / f"e{n:02d}.hwpx"
        ok = _edit(rhwp, [args[0], str(cur)] + args[1:] + ["-o", str(nxt)], log)
        if ok:
            cur = nxt
        return ok

    def batched_cell_edit(entries: List[tuple], label: str) -> bool:
        """[(명령, 표, 행, 열, props JSON)] 칸 편집을 (명령, props, 칸 바탕 상태) 묶음마다 rhwp 한 번으로(#42).

        대표 칸에만 rhwp 를 돌리고, 바로 전 상태가 대표 칸과 똑같은 칸에 대표 칸의 결과 상태(tc 의 header·
        borderFillIDRef, 칸 문단 paraPrIDRef, run charPrIDRef)를 옮긴다. rhwp 가 대표 칸 말고 다른 칸을 바꾸거나,
        문단·run 수가 달라지거나, 상태가 다른 칸은 한 단계씩 돈다.
        """
        nonlocal cur, n

        def one(e):
            cmd, t, r, c, pj = e
            return step([cmd, "--table", str(t), "--row", str(r), "--col", str(c), "--props", pj])

        if not BATCH_EDITS:
            return all(one(e) for e in entries)

        def states(xml):
            return {(t, k): _cell_state(xml, sp) for t, cells in enumerate(_top_table_cells(xml)) for k, sp in cells.items()}

        first = states(read_section(cur))
        groups: Dict[tuple, List[tuple]] = {}
        for k, e in enumerate(entries):
            st = first.get((e[1], (e[2], e[3])))
            groups.setdefault((e[0], e[4], st) if st else ("one", k), []).append(e)
        for members in groups.values():
            head, rest = members[0], members[1:]
            before_xml = read_section(cur)
            before = states(before_xml)
            target = (head[1], (head[2], head[3]))
            if not one(head):
                return False
            if not rest:
                continue
            after_xml = read_section(cur)
            after = states(after_xml)
            new = after.get(target)
            safe = (new is not None and set(before) == set(after)
                    and all(before[k] == after[k] for k in before if k != target)
                    and len(new[1]) == len(before[target][1]) and len(new[2]) == len(before[target][2]))
            copy_to = [e for e in rest if safe and before.get((e[1], (e[2], e[3]))) == before[target]]
            for e in rest:
                if e not in copy_to and not one(e):
                    return False
            if not copy_to:
                continue
            xml = read_section(cur)
            spans = _top_table_cells(xml)
            for s, e2 in sorted((spans[e[1]][(e[2], e[3])] for e in copy_to), reverse=True):
                xml = xml[:s] + _rewrite_cell(xml[s:e2], new) + xml[e2:]
            n += 1
            nxt = work / f"e{n:02d}.hwpx"
            write_section(cur, nxt, xml)
            log.append({"cmd": ["batch-cell-edit", label, head[0], f"{len(copy_to)} 칸"], "exit": 0})
            cur = nxt
        return True

    def batched_char_format(entries: List[tuple], label: str) -> bool:
        """[(최상위 문단 번호, props JSON)] 문단 전체 글자 서식(apply-char-format, 범위 없음)을 묶음마다 rhwp 한 번으로(#44).

        묶음 열쇠는 (문단 자기 run 들의 바탕 charPrIDRef, props). 대표 문단에만 rhwp 를 돌리고, rhwp 가 그 문단 말고는
        아무 run 도 바꾸지 않았고 run 수도 그대로면 결과 charPrIDRef 들을 바로 전 상태가 같은 문단에 옮긴다.
        """
        nonlocal cur, n

        def one(p, pj):
            return step(["apply-char-format", "--para", str(p), "--props", pj])

        if not BATCH_EDITS:
            return all(one(p, pj) for p, pj in entries)
        counts: Dict[int, int] = {}
        for p, _ in entries:
            counts[p] = counts.get(p, 0) + 1
        first = top_para_runs(read_section(cur))
        groups: Dict[tuple, List[tuple]] = {}
        for k, (p, pj) in enumerate(entries):
            key = ("one", k) if counts[p] > 1 or p >= len(first) else (first[p], pj)
            groups.setdefault(key, []).append((p, pj))
        for members in groups.values():
            (p0, pj), rest = members[0], members[1:]
            before = top_para_runs(read_section(cur))
            if not one(p0, pj):
                return False
            if not rest:
                continue
            xml = read_section(cur)
            after = top_para_runs(xml)
            safe = (len(before) == len(after) and len(after[p0]) == len(before[p0])
                    and all(a == b for k, (a, b) in enumerate(zip(before, after)) if k != p0))
            copy_to = [p for p, _ in rest if safe and before[p] == before[p0]]
            for p, pj2 in rest:
                if p not in copy_to and not one(p, pj2):
                    return False
            if not copy_to:
                continue
            n += 1
            nxt = work / f"e{n:02d}.hwpx"
            write_section(cur, nxt, set_top_para_runs(xml, {p: after[p0] for p in copy_to}))
            log.append({"cmd": ["batch-char-format", label, f"{len(copy_to)} 문단"], "exit": 0})
            cur = nxt
        return True

    def batched_para_format(entries: List[tuple], label: str) -> bool:
        """[(최상위 문단 번호, props JSON)] 를 (바탕 paraPr, props) 묶음마다 rhwp 한 번으로 적용한다(#42).

        rhwp edit 는 부를 때마다 문서 전체를 읽고 저장해 긴 문서에서 한 번에 수 초가 든다. apply-para-format 은
        바탕 paraPr 을 복제·수정해 새 번호로 붙이거나 같은 것을 재사용하므로, 같은 바탕·같은 props 의 문단은 결과
        paraPr 이 같다. 묶음의 첫 문단에만 rhwp 를 돌리고 그 번호를 나머지 문단에 옮긴다. rhwp 가 그 문단 말고
        다른 문단 번호까지 바꾸면 그 묶음은 안전하게 한 단계씩 돈다. 한 문단에 서식이 둘 이상이면 차례대로 돈다.
        """
        nonlocal cur, n
        if not BATCH_EDITS:
            return all(step(["apply-para-format", "--para", str(p), "--props", pj]) for p, pj in entries)
        counts: Dict[int, int] = {}
        for p, _ in entries:
            counts[p] = counts.get(p, 0) + 1
        refs = para_pr_refs(cur)
        groups: Dict[tuple, List[tuple]] = {}
        for k, (p, pj) in enumerate(entries):
            key = ("one", k) if counts[p] > 1 else (refs[p] if p < len(refs) else None, pj)
            groups.setdefault(key, []).append((p, pj))
        for members in groups.values():
            p0, pj = members[0]
            before = para_pr_refs(cur)
            if not step(["apply-para-format", "--para", str(p0), "--props", pj]):
                return False
            rest = [p for p, _ in members[1:]]
            if not rest:
                continue
            after = para_pr_refs(cur)
            if len(before) != len(after) or any(a != b for k, (a, b) in enumerate(zip(before, after)) if k != p0):
                for p in rest:
                    if not step(["apply-para-format", "--para", str(p), "--props", pj]):
                        return False
                continue
            n += 1
            nxt = work / f"e{n:02d}.hwpx"
            set_para_pr_refs(cur, nxt, {p: after[p0] for p in rest})
            log.append({"cmd": ["batch-para-format", label, f"paraPrIDRef={after[p0]}", f"{len(rest)} 문단"],
                        "exit": 0})
            cur = nxt
        return True

    for bi in plan["page_breaks"]:
        if not step(["insert-page-break", "--para", str(paras[bi]), "--offset", "0"]):
            result["error"] = "쪽 나눔 삽입 실패"
            return result
    for img in plan["images"]:
        para = str(paras[img["block"]])
        if not Path(img["path"]).is_file():
            result["error"] = f"그림 파일이 없습니다: {img['path']}"
            return result
        if not (step(["delete-text", "--para", para, "--offset", "0", "--count", str(len(img["mark"]))])
                and step(["insert-picture", "--image", img["path"], "--para", para, "--offset", "0",
                          "--width", str(image_width_hu(doc, img))])
                and step(["set-picture", "--section", "0", "--para", para, "--ctrl", "0",
                          "--props", '{"treatAsChar":true}'])):
            result["error"] = "그림 삽입 실패"
            return result
    aligns = []
    for al in plan["aligns"]:
        props = {"alignment": al["align"]}
        if al.get("keep"):
            props["keepWithNext"] = True
        aligns.append((paras[al["block"]], json.dumps(props, separators=(",", ":"))))
    if not batched_para_format(aligns, "정렬"):
        result["error"] = "문단 정렬 실패"
        return result
    if plan["page"]:
        if not step(["set-page-def", "--props", json.dumps(plan["page"], separators=(",", ":"))]):
            result["error"] = "용지 설정 실패"
            return result
    widths = table_widths(cur) if plan["tables"] else []
    new_total = table_total_hu(doc) if plan["page"] else None
    in_page = tables_in_page(cur) if plan["tables"] else []
    any_emphasis = any(tb["cell_emphasis"] for tb in plan["tables"])
    deferred: List[tuple] = []
    for tb in plan["tables"]:
        t = str(tb["table"])
        entries: List[tuple] = []
        # scaffold 표가 flowWithText="0"(쪽 영역 밖으로 나갈 수 있음)이면 한컴이 쪽 끝 표를 각주 영역 위에
        # 겹쳐 그린다. 한글에서 만든 표처럼 쪽 영역 안으로 제한한다. 이미 "1" 이면 rhwp 를 부르지 않는다(#42).
        already = BATCH_EDITS and tb["table"] < len(in_page) and in_page[tb["table"]]
        if not already and not step(["set-table-props", "--table", t, "--props", '{"restrictInPage":true}']):
            result["error"] = "표 배치 속성 설정 실패"
            return result
        if tb["col_widths"] or new_total:
            cols = split_widths(new_total or widths[tb["table"]], tb["col_widths"] or [1] * tb["cols"])
            if not step(["set-column-widths", "--table", t, "--widths", ",".join(map(str, cols))]):
                result["error"] = "표 열 폭 설정 실패"
                return result
        for m in tb["merges"]:
            if not step(["merge-cells", "--table", t, "--row", str(m["row"]), "--col", str(m["col"]),
                         "--end-row", str(m["row"] + m["rowspan"] - 1),
                         "--end-col", str(m["col"] + m["colspan"] - 1)]):
                result["error"] = "표 셀 병합 실패"
                return result
        hidden = covered_cells(tb["merges"])
        if tb.get("pt") is not None or tb.get("font"):
            props = json.dumps({**({"fontSize": int(round(tb["pt"] * 100))} if tb.get("pt") is not None else {}),
                                **({"fontName": tb["font"]} if tb.get("font") else {})},
                               ensure_ascii=False, separators=(",", ":"))
            entries += [("apply-char-format-in-cell", tb["table"], r, c, props)
                        for r in range(tb["rows"]) for c in range(tb["cols"]) if (r, c) not in hidden]
        if tb["header_fill"]:
            props = json.dumps({"fillType": "solid", "fillColor": tb["header_fill"], "isHeader": True},
                               separators=(",", ":"))
            for c in range(tb["cols"]):
                if (0, c) in hidden:
                    continue
                entries += [("set-cell-props", tb["table"], 0, c, props),
                            ("apply-char-format-in-cell", tb["table"], 0, c, '{"bold":true}'),
                            ("apply-para-format-in-cell", tb["table"], 0, c, '{"alignment":"center"}')]
        # 칸 강조가 있으면 그 표의 꾸밈을 강조보다 먼저 끝내야 해서 표마다, 없으면 모든 표를 한 번에 묶는다(#42).
        if any_emphasis:
            if not batched_cell_edit(entries, f"표 {t}"):
                result["error"] = "표 글자 크기·머리 행 꾸밈 실패"
                return result
        else:
            deferred += entries
        for em in tb["cell_emphasis"]:
            if not step(["apply-char-format-in-cell", "--table", t, "--row", str(em["row"]),
                         "--col", str(em["col"]), "--start", str(em["start"]),
                         "--end", str(em["start"] + em["count"]), "--props", em["props"]]):
                result["error"] = "표 셀 강조 실패"
                return result
    if deferred and not batched_cell_edit(deferred, "표 꾸밈"):
        result["error"] = "표 글자 크기·머리 행 꾸밈 실패"
        return result
    # apply-para-format 의 여백 값은 HWPUNIT 의 2배로 받는다(4000 → 저장 2000 = 20pt).
    indents = []
    for ind in plan["indents"]:
        props = {"marginLeft": ind["left"] * 2, "indent": ind["indent"] * 2}
        if ind.get("tab_right"):
            props["tabAutoRight"] = True  # 목차 쪽 번호: 문단 오른쪽 끝 자동 탭
        indents.append((paras[ind["block"]], json.dumps(props, separators=(",", ":"))))
    if not batched_para_format(indents, "목록 들여쓰기"):
        result["error"] = "목록 들여쓰기 실패"
        return result
    # 문서 글자 크기·줄 간격은 강조보다 먼저: 강조의 size 가 문서 크기를 덮어쓴다.
    st = plan.get("style") or {}
    # 글자 서식과 문단 서식은 서로 다른 목록(charPr·paraPr)이라 문단마다 번갈아 하지 않고 묶음으로 나눠도 같다(#44).
    char_entries, para_entries = [], []
    for target, role in targets:
        para = 0 if target == "title" else paras[target]
        cprops, pprops = role_char_props(st, role), role_para_props(st, role)
        if cprops:
            char_entries.append((para, cprops))
        if pprops:
            para_entries.append((para, pprops))
    if not batched_char_format(char_entries, "문서 글자 크기·글꼴"):
        result["error"] = "문서 글자 크기·글꼴 적용 실패"
        return result
    if not batched_para_format(para_entries, "문서 줄 간격·문단 간격"):
        result["error"] = "문서 줄 간격·문단 간격 적용 실패"
        return result
    # 강조는 각주보다 먼저: 각주 컨트롤이 들어가기 전이라 오프셋이 HDS 글자 위치 그대로다.
    for em in plan["emphasis"]:
        if not step(["apply-char-format", "--para", str(paras[em["block"]]), "--offset", str(em["offset"]),
                     "--count", str(em["count"]), "--props", em["props"]]):
            result["error"] = "글자 강조 적용 실패"
            return result
    # 같은 문단의 각주는 뒤에서부터 넣는다: 앞 오프셋이 그대로이고 새 각주는 늘 그 문단의 첫 컨트롤이다.
    def one_footnote(fn) -> bool:
        para = str(paras[fn["block"]])
        return (step(["insert-footnote", "--para", para, "--offset", str(fn["offset"])])
                and step(["insert-footnote-text", "--para", para, "--ctrl", "0", "--fn-para", "0",
                          "--offset", "2", "--text", fn["text"]]))

    ordered = sorted(plan["footnotes"], key=lambda f: (f["block"], -f["offset"]))
    direct: Dict[int, List[dict]] = {}
    if BATCH_EDITS and len(ordered) > 1:
        # 각주가 많으면 rhwp 를 각주마다 두 번 부르는 데 시간이 오래 걸린다(#50, 88개면 약 9분).
        # 글만 든 문단(같은 자리 각주가 없고 각주 글에 줄 바꿈이 없음)은 XML 로 바로 넣는다.
        xml = read_section(cur)
        groups: Dict[int, List[dict]] = {}
        for fn in ordered:
            groups.setdefault(fn["block"], []).append(fn)
        for b, fns in groups.items():
            offsets = [f["offset"] for f in fns]
            if (len(set(offsets)) == len(offsets) and footnote_para_simple(xml, paras[b])
                    and all(_FN_TEXT_OK.fullmatch(f["text"]) for f in fns)):
                direct[b] = fns
    if sum(map(len, direct.values())) < 2:
        direct = {}
    for fn in ordered:
        if fn["block"] not in direct and not one_footnote(fn):
            result["error"] = "각주 삽입 실패"
            return result
    if direct:
        # 본: 첫 묶음 문단의 맨 뒤 각주를 rhwp 로 넣고 그 컨트롤 XML 을 나머지에 쓴다
        b0 = next(iter(direct))
        head = direct[b0][0]
        if not one_footnote(head):
            result["error"] = "각주 삽입 실패"
            return result
        xml = read_section(cur)
        rest = {paras[b]: [(f["offset"], f["text"]) for f in fns if f is not head] for b, fns in direct.items()}
        try:
            xml = insert_footnotes_xml(xml, {k: v for k, v in rest.items() if v}, footnote_template(xml, paras[b0]))
        except ValueError as exc:
            result["error"] = f"각주 삽입 실패: {exc}"
            return result
        n += 1
        nxt = work / f"e{n:02d}.hwpx"
        write_section(cur, nxt, xml)
        log.append({"cmd": ["batch-footnote", f"{sum(map(len, rest.values()))} 개"], "exit": 0})
        cur = nxt
    for kind in ("header", "footer"):
        hf = plan[kind]
        if hf:
            n += 1
            nxt = _hf(rhwp, kind, hf["text"], cur, work, n, log)
            if nxt is None:
                result["error"] = f"{kind} 삽입 실패"
                return result
            cur = nxt
    out.write_bytes(cur.read_bytes())
    result["ok"] = True
    return result


def check(out: Path, doc: dict, rhwp: Optional[str] = None) -> List[str]:
    """산출물에 계획한 요소가 실제로 들어갔는지 센다. 어긋난 항목의 설명 목록.

    rhwp 를 주면 목차 쪽 번호가 산출물의 실제 제목 쪽과 같은지도 본다."""
    shown = toc_pages_shown(out, doc)
    _, plan = split(doc, shown)
    if plan["toc"] is not None:
        problems_toc = _check_toc(out, doc, rhwp)
    else:
        problems_toc = []
    with zipfile.ZipFile(out) as z:
        xml = z.read("Contents/section0.xml").decode("utf-8")
    problems = []
    got = len(re.findall(r'<hp:p [^>]*pageBreak="1"', xml))
    if got != len(plan["page_breaks"]):
        problems.append(f"쪽 나눔 {got}개 (계획 {len(plan['page_breaks'])}개)")
    got = xml.count("<hp:footNote ")
    if got != len(plan["footnotes"]):
        problems.append(f"각주 {got}개 (계획 {len(plan['footnotes'])}개)")
    # rhwp 는 글 속 탭·줄 바꿈을 <hp:tab/>·<hp:lineBreak/> 요소로 쓴다. 글자로 바꿔 놓고 찾는다.
    xml_text = re.sub(r"<hp:lineBreak\b[^>]*/>", "\n", re.sub(r"<hp:tab\b[^>]*/>", "\t", xml))
    # rhwp 는 본문 글의 따옴표도 &quot;·&apos; 로 쓴다 — 풀어서 원래 글과 비교한다
    xml_text = xml_text.replace("&quot;", '"').replace("&apos;", "'")
    for f in plan["footnotes"]:
        if escape(f["text"]) not in xml_text:
            problems.append(f"각주 본문 누락: {f['text'][:30]!r}")
    got = sum('treatAsChar="1"' in seg.split("</hp:pic>")[0] for seg in xml.split("<hp:pic ")[1:])
    if got != len(plan["images"]):
        problems.append(f"글자처럼 취급한 그림 {got}개 (계획 {len(plan['images'])}개)")
    for img in plan["images"]:
        if img["mark"] in xml:
            problems.append(f"그림 표시 글자가 남음: {img['mark']}")
    if plan["emphasis"]:
        spec, _ = split(doc, shown)
        paras = map_blocks(top_paragraphs(out), spec["blocks"], bool(spec.get("title")))
        styles, shapes = char_styles(out), char_shapes(out)
        for em in plan["emphasis"]:
            want = json.loads(em["props"])
            pi = paras[em["block"]]
            ids = styles[pi][em["offset"]:em["offset"] + em["count"]] if pi is not None else []
            bad = len(ids) != em["count"] or any(not props_match(shapes.get(i, {}), want) for i in ids)
            if bad:
                problems.append(f"강조 미반영: blocks[{em['block']}] {em['offset']}+{em['count']} {em['props']}")
    if plan["aligns"]:
        spec, _ = split(doc, shown)
        paras = map_blocks(top_paragraphs(out), spec["blocks"], bool(spec.get("title")))
        layout = para_layout(out)
        for al in plan["aligns"]:
            pi = paras[al["block"]]
            want = (al["align"].upper(), bool(al.get("keep")))
            got = layout[pi] if pi is not None else None
            if got is None or got[0] != want[0] or (want[1] and not got[1]):
                problems.append(f"문단 정렬·묶음 미반영: blocks[{al['block']}] {got} (계획 {want})")
    if plan["indents"]:
        spec, _ = split(doc, shown)
        paras = map_blocks(top_paragraphs(out), spec["blocks"], bool(spec.get("title")))
        margins = para_margins(out)
        for ind in plan["indents"]:
            pi = paras[ind["block"]]
            got = margins[pi] if pi is not None else None
            if got != (ind["left"], ind["indent"]):
                problems.append(f"목록 들여쓰기 미반영: blocks[{ind['block']}] {got} "
                                f"(계획 {(ind['left'], ind['indent'])})")
    if plan.get("style"):
        problems.extend(_check_style(out, doc, shown, plan))
    if plan["page"]:
        got = page_def(out)
        for k, v in plan["page"].items():
            if got.get(k) != v:
                problems.append(f"용지 설정 {k}={got.get(k)} (계획 {v})")
    if plan["page"] and plan["tables"]:
        want_total = table_total_hu(doc)
        for i, w in enumerate(table_widths(out)):
            if abs(w - want_total) > len(plan["tables"][i]["col_widths"] or [0] * plan["tables"][i]["cols"]):
                problems.append(f"표 {i} 폭 {w} (본문 폭 기준 {want_total})")
    if plan["tables"]:
        flows = table_flow(out)
        loose = [i for i, f in enumerate(flows) if not f]
        if loose:
            problems.append(f"쪽 영역 안으로 제한되지 않은 표: {loose}")
        tables, fills, aligns = table_cells(out), fill_colors(out), para_aligns(out)
        shapes = char_shapes(out)
        for tb in plan["tables"]:
            cells = tables[tb["table"]] if tb["table"] < len(tables) else []
            where = f"표 {tb['table']}"
            if tb["col_widths"]:
                want = split_widths(table_widths(out)[tb["table"]], tb["col_widths"])
                # 열마다 병합되지 않은 셀 하나의 폭 (그런 셀이 없는 열은 계획값과 같다고 본다)
                got = [next((c["width"] for c in cells if c["col"] == k and c["colspan"] == 1), want[k])
                       for k in range(len(want))]
                if got != want:
                    problems.append(f"{where} 열 폭 {got} (계획 {want})")
            got_spans = sorted((c["row"], c["col"], c["rowspan"], c["colspan"]) for c in cells
                               if c["rowspan"] > 1 or c["colspan"] > 1)
            want_spans = sorted((m["row"], m["col"], m["rowspan"], m["colspan"]) for m in tb["merges"])
            if got_spans != want_spans:
                problems.append(f"{where} 병합 {got_spans} (계획 {want_spans})")
            by_addr = {(c["row"], c["col"]): c for c in cells}
            if tb.get("pt") is not None:
                want = int(round(tb["pt"] * 100))
                sized = {}
                for em in tb["cell_emphasis"]:
                    if "fontSize" in json.loads(em["props"]):
                        sized.setdefault((em["row"], em["col"]), set()).update(
                            range(em["start"], em["start"] + em["count"]))
                for c in cells:
                    skip = sized.get((c["row"], c["col"]), set())
                    if any(shapes.get(cid, {}).get("height") != want
                           for k, cid in enumerate(c["chars"]) if k not in skip):
                        problems.append(f"{where} 표 글자 크기 미반영: 셀 ({c['row']},{c['col']}) (계획 {want})")
            if tb.get("font"):
                for c in cells:
                    if any(shapes.get(cid, {}).get("font") != tb["font"] for cid in c["chars"]):
                        problems.append(f"{where} 표 글꼴 미반영: 셀 ({c['row']},{c['col']}) (계획 {tb['font']})")
            for em in tb["cell_emphasis"]:
                want = json.loads(em["props"])
                ids = by_addr.get((em["row"], em["col"]), {}).get("chars", [])[em["start"]:em["start"] + em["count"]]
                bad = len(ids) != em["count"] or any(not props_match(shapes.get(i, {}), want) for i in ids)
                if bad:
                    problems.append(f"{where} 셀 강조 미반영: ({em['row']},{em['col']}) {em['props']}")
            if tb["header_fill"]:
                for c in cells:
                    head = c["row"] == 0
                    ok = (c["header"] == head
                          and (fills.get(c["border_fill"]) == tb["header_fill"]) == head)
                    if head:
                        ok = ok and aligns.get(c["para_pr"]) == "CENTER" and all(
                            shapes.get(i, {}).get("bold") for i in c["char_prs"])
                    if not ok:
                        problems.append(f"{where} 머리 행 꾸밈 미반영: 셀 ({c['row']},{c['col']})")
    for kind in ("header", "footer"):
        hf = plan[kind]
        has = f"<hp:{kind} " in xml
        if bool(hf) != has:
            problems.append(f"{kind} {'없음' if hf else '계획에 없는데 있음'}")
        if hf and PAGE_TOKEN in hf["text"] and 'numType="PAGE"' not in xml:
            problems.append(f"{kind} 쪽 번호 없음")
        if hf and kind in HF_CENTER and hf_aligns(out).get(kind) != "CENTER":
            problems.append(f"{kind} 가운데 정렬 미반영")
    return problems + problems_toc


def _check_style(out: Path, doc: dict, shown, plan: dict) -> List[str]:
    """style 의 글자 크기·글꼴·줄 간격·문단 간격이 대상 문단에 들어갔는가. 크기를 준 강조 범위는 강조 크기를 따른다."""
    spec, _ = split(doc, shown)
    paras = map_blocks(top_paragraphs(out), spec["blocks"], bool(spec.get("title")))
    styles, shapes, spacings = char_styles(out), char_shapes(out), para_line_spacings(out)
    gaps, layouts = para_spacings(out), para_layout(out)
    sized = {}
    for em in plan["emphasis"]:
        if "fontSize" in json.loads(em["props"]):
            sized.setdefault(em["block"], set()).update(range(em["offset"], em["offset"] + em["count"]))
    st = plan["style"]
    ls = st.get("line_spacing")
    problems = []
    for target, role in style_targets(spec, plan):
        pi = 0 if target == "title" else paras[target]
        where = "제목" if target == "title" else f"blocks[{target}]"
        if pi is None:
            problems.append(f"문서 서식 대상 문단 없음: {where}")
            continue
        pt = st.get(f"{role}_pt")
        if pt is not None:
            want = int(round(pt * 100))
            skip = sized.get(target, set())
            bad = [shapes.get(cid, {}).get("height") for k, cid in enumerate(styles[pi])
                   if k not in skip and shapes.get(cid, {}).get("height") != want]
            if bad:
                problems.append(f"문서 글자 크기 미반영: {where} {sorted(set(bad))} (계획 {want})")
        font = st.get(f"{role}_font")
        if font:
            bad = sorted({str(shapes.get(cid, {}).get("font")) for cid in styles[pi]} - {font})
            if bad:
                problems.append(f"문서 글꼴 미반영: {where} {bad} (계획 {font})")
        if ls is not None and spacings[pi] != ("PERCENT", int(ls)):
            problems.append(f"문서 줄 간격 미반영: {where} {spacings[pi]} (계획 {int(ls)}%)")
        if role == "heading" and not (layouts[pi] and layouts[pi][1]):
            problems.append(f"개요 다음 문단과 함께 미반영: {where}")
        for k, side in enumerate(("before", "after")):
            v = st.get(f"{role}_space_{side}")
            if v is not None:
                want = int(round(v * SPACE_HU_PER_PT))
                got = gaps[pi][k] if gaps[pi] else None
                if got != want:
                    problems.append(f"문단 {'위' if side == 'before' else '아래'} 간격 미반영: {where} {got} "
                                    f"(계획 {want})")
    return problems


def toc_pages_shown(out: Path, doc: dict) -> Optional[Dict[int, int]]:
    """산출물 목차 줄에 적힌 쪽 번호(제목 블록 순번 → 쪽). 목차가 없으면 None."""
    if not any(b.get("type") == "toc" for b in doc.get("blocks", [])):
        return None
    texts = [p["text"] for p in top_paragraphs(out)]
    shown = {}
    for e in toc_entries(doc, _toc_levels(doc)):
        prefix = f"{e['number']} {e['text']}\t"
        line = next((t for t in texts if t.startswith(prefix)), None)
        if line is not None and line[len(prefix):].isdigit():
            shown[e["heading"]] = int(line[len(prefix):])
    return shown


def _check_toc(out: Path, doc: dict, rhwp: Optional[str]) -> List[str]:
    """목차 줄이 있고 오른쪽 탭이 걸렸는지, rhwp 가 있으면 쪽 번호가 실제 제목 쪽과 같은지."""
    paras = top_paragraphs(out)
    texts = [p["text"] for p in paras]
    with zipfile.ZipFile(out) as z:
        head = ET.fromstring(z.read("Contents/header.xml"))
        root = ET.fromstring(z.read("Contents/section0.xml"))
    tab_right = {pp.get("id") for pp in head.iter(f"{{{HH}}}paraPr")
                 if any(t.get("autoTabRight") == "1" for t in head.iter(f"{{{HH}}}tabPr")
                        if t.get("id") == pp.get("tabPrIDRef"))}
    pp_ids = [p.get("paraPrIDRef") for p in root.findall(f"{{{HP}}}p")]
    actual = heading_pages(out, rhwp, doc) if rhwp else None
    problems = []
    for e in toc_entries(doc, _toc_levels(doc)):
        prefix = f"{e['number']} {e['text']}"
        idx = next((i for i, t in enumerate(texts) if t.startswith(prefix)), None)
        if idx is None:
            problems.append(f"목차 줄 없음: {prefix}")
            continue
        if pp_ids[idx] not in tab_right:
            problems.append(f"목차 줄 오른쪽 탭 없음: {prefix}")
        shown = texts[idx][len(prefix):].strip()
        if actual is not None and shown != str(actual.get(e["heading"])):
            problems.append(f"목차 쪽 번호 {shown} ≠ 실제 {actual.get(e['heading'])}: {prefix}")
    return problems


def _toc_levels(doc: dict) -> int:
    return next((b.get("levels", TOC_LEVELS) for b in doc.get("blocks", []) if b.get("type") == "toc"), TOC_LEVELS)
