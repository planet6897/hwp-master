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
    paragraph.footnotes      → edit insert-footnote + insert-footnote-text
    table.header_row         → 첫 행 셀마다 edit set-cell-props {fill, isHeader} +
                               apply-char-format-in-cell 굵게 + apply-para-format-in-cell 가운데
    table.col_widths         → edit set-column-widths (scaffold 표 폭을 비율로 나눔)
    table.merges             → edit merge-cells (머리 행 꾸밈보다 먼저, 가려진 셀은 건너뜀)
    table.cell_emphasis      → edit apply-char-format-in-cell --start/--end
    list 블록                → 항목마다 "글머리 + 빈칸 + 글" 문단 + apply-para-format
                               왼쪽 여백(단계)·내어쓰기(글머리 폭)
    header / footer          → edit insert-header-footer + insert-header-footer-text
                               (+ "{page}" 자리에 insert-field-in-hf 쪽 번호)

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
HEADER_FILL = "#D9D9D9"  # 머리 행 기본 배경 (회색 15%)
TABLE_STYLE_KEYS = ("header_row", "header_fill", "col_widths", "merges", "cell_emphasis")



def split(doc: dict, toc_pages: Optional[Dict[int, int]] = None) -> tuple:
    """HDS → (scaffold 명세, 편집 계획). 편집 계획이 비면 scaffold 만으로 끝난다.

    toc_pages 는 목차 항목(제목 블록 순번 → 쪽)이다. 없으면 쪽 번호 자리에 TOC_PLACEHOLDER 를 둔다."""
    spec = {k: copy.deepcopy(v) for k, v in doc.items()
            if k not in ("header", "footer", "blocks", "orientation", "margins_mm")}
    page = page_props(doc)
    if page and doc.get("orientation") == "landscape" and "page_size" in spec:
        # scaffold 는 세로 치수로 만들고, 가로는 한컴처럼 landscape 표시로 돌린다
        w, h = spec["page_size"]["width_mm"], spec["page_size"]["height_mm"]
        spec["page_size"] = {"width_mm": min(w, h), "height_mm": max(w, h)}
    blocks: List[dict] = []
    plan: Dict[str, object] = {"page_breaks": [], "footnotes": [], "emphasis": [], "images": [],
                               "aligns": [], "indents": [], "tables": [], "page": page, "toc": None, "header": doc.get("header"),
                               "footer": doc.get("footer")}
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
                plan["indents"].append({"block": len(blocks), "left": (e["level"] - 1) * LIST_STEP_HU,
                                        "indent": 0, "tab_right": True})
                add_para({"type": "paragraph", "text": f"{e['number']} {e['text']}\t{page}"})
            continue
        if b.get("type") == "list":
            markers = b.get("markers") or LIST_MARKERS
            for item in b["items"]:
                item = {"text": item} if isinstance(item, str) else dict(item)
                level = item.pop("level", 1)
                marker = markers[level - 1]
                left, hang = list_indent(level, marker)
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
                "table": ntab, "cols": max(len(r) for r in b["rows"]),
                "header_fill": (style.get("header_fill") or HEADER_FILL).upper()
                if style.get("header_row") else None,
                "col_widths": style.get("col_widths"), "merges": merges, "cell_emphasis": cell_marks})
        add_para(b)
    spec["blocks"] = blocks
    return spec, plan


def toc_entries(doc: dict, levels: int) -> List[dict]:
    """목차 항목: 제목 블록 순번(heading), 단계, 개요 번호("1.1."), 글.

    번호는 scaffold 개요 번호(한글 기본 1. / 1.1. / 1.1.1. …)와 같은 셈이다."""
    out, counts, nh = [], [0] * 8, 0
    for b in doc.get("blocks", []):
        if b.get("type") != "heading":
            continue
        level = b["level"]
        counts[level] += 1
        for k in range(level + 1, 8):
            counts[k] = 0
        if level <= levels:
            number = ".".join(str(counts[k]) for k in range(1, level + 1)) + "."
            out.append({"heading": nh, "level": level, "number": number, "text": b["text"]})
        nh += 1
    return out


def heading_pages(hwpx: Path, rhwp: str, doc: dict) -> Dict[int, int]:
    """rhwp 쪽 나눔 결과에서 제목 블록 순번 → 쪽 번호(1부터)."""
    spec, _ = split(doc)
    paras = map_blocks(top_paragraphs(hwpx), spec["blocks"], bool(spec.get("title")))
    proc = subprocess.run([rhwp, "dump-pages", str(hwpx), "--json"], capture_output=True, text=True, timeout=600)
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


def list_indent(level: int, marker: str) -> tuple:
    """(왼쪽 여백, 내어쓰기) HWPUNIT. 둘째 줄부터 글머리 뒤 글자에 맞춘다."""
    return (level - 1) * LIST_STEP_HU, marker_hu(marker) + SPACE_HU


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
    return not (plan["page_breaks"] or plan["footnotes"] or plan["emphasis"] or plan["images"]
                or plan["indents"] or any(table_styled(t) for t in plan["tables"]) or plan["page"]
                or plan["header"] or plan["footer"])


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


def covered_cells(merges: List[dict]) -> set:
    """병합으로 가려지는 셀 (row, col) — 병합 첫 셀은 빠진다."""
    return {(r, c) for m in merges for r in range(m["row"], m["row"] + m["rowspan"])
            for c in range(m["col"], m["col"] + m["colspan"])} - {(m["row"], m["col"]) for m in merges}


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
    """header.xml charPr id → {bold, italic, underline, strike, color, shade, height}."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/header.xml"))
    out = {}
    for cp in root.iter(f"{{{HH}}}charPr"):
        ul = cp.find(f"{{{HH}}}underline")
        so = cp.find(f"{{{HH}}}strikeout")
        out[cp.get("id")] = {"bold": cp.find(f"{{{HH}}}bold") is not None,
                             "italic": cp.find(f"{{{HH}}}italic") is not None,
                             "underline": ul is not None and ul.get("type", "NONE") != "NONE",
                             "strike": so is not None and so.get("shape", "NONE") != "NONE",
                             "color": (cp.get("textColor") or "").upper(),
                             "shade": (cp.get("shadeColor") or "").upper(),
                             "height": int(cp.get("height") or 0)}
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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
                          capture_output=True, text=True, timeout=900)
    result: dict = {"ok": False, "scaffold": {"exit": proc.returncode}, "edits": []}
    if proc.returncode != 0:
        result["scaffold"]["stderr"] = proc.stderr.strip()[-2000:]
        result["error"] = "scaffold 실패"
        return result
    if plan_empty(plan):
        result["ok"] = True
        return result

    paras = map_blocks(top_paragraphs(first), spec["blocks"], bool(spec.get("title")))
    need = set(plan["page_breaks"]) | {f["block"] for f in plan["footnotes"] + plan["emphasis"]
                                       + plan["images"] + plan["aligns"]
                                       + plan["indents"]}
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
    for al in plan["aligns"]:
        props = {"alignment": al["align"]}
        if al.get("keep"):
            props["keepWithNext"] = True
        if not step(["apply-para-format", "--para", str(paras[al["block"]]),
                     "--props", json.dumps(props, separators=(",", ":"))]):
            result["error"] = "문단 정렬 실패"
            return result
    if plan["page"]:
        if not step(["set-page-def", "--props", json.dumps(plan["page"], separators=(",", ":"))]):
            result["error"] = "용지 설정 실패"
            return result
    widths = table_widths(cur) if plan["tables"] else []
    new_total = table_total_hu(doc) if plan["page"] else None
    for tb in plan["tables"]:
        t = str(tb["table"])
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
        if tb["header_fill"]:
            props = json.dumps({"fillType": "solid", "fillColor": tb["header_fill"], "isHeader": True},
                               separators=(",", ":"))
            for c in range(tb["cols"]):
                if (0, c) in hidden:
                    continue
                cell = ["--table", t, "--row", "0", "--col", str(c)]
                if not (step(["set-cell-props"] + cell + ["--props", props])
                        and step(["apply-char-format-in-cell"] + cell + ["--props", '{"bold":true}'])
                        and step(["apply-para-format-in-cell"] + cell + ["--props", '{"alignment":"center"}'])):
                    result["error"] = "표 머리 행 꾸밈 실패"
                    return result
        for em in tb["cell_emphasis"]:
            if not step(["apply-char-format-in-cell", "--table", t, "--row", str(em["row"]),
                         "--col", str(em["col"]), "--start", str(em["start"]),
                         "--end", str(em["start"] + em["count"]), "--props", em["props"]]):
                result["error"] = "표 셀 강조 실패"
                return result
    # apply-para-format 의 여백 값은 HWPUNIT 의 2배로 받는다(4000 → 저장 2000 = 20pt).
    for ind in plan["indents"]:
        props = {"marginLeft": ind["left"] * 2, "indent": ind["indent"] * 2}
        if ind.get("tab_right"):
            props["tabAutoRight"] = True  # 목차 쪽 번호: 문단 오른쪽 끝 자동 탭
        if not step(["apply-para-format", "--para", str(paras[ind["block"]]),
                     "--props", json.dumps(props, separators=(",", ":"))]):
            result["error"] = "목록 들여쓰기 실패"
            return result
    # 강조는 각주보다 먼저: 각주 컨트롤이 들어가기 전이라 오프셋이 HDS 글자 위치 그대로다.
    for em in plan["emphasis"]:
        if not step(["apply-char-format", "--para", str(paras[em["block"]]), "--offset", str(em["offset"]),
                     "--count", str(em["count"]), "--props", em["props"]]):
            result["error"] = "글자 강조 적용 실패"
            return result
    # 같은 문단의 각주는 뒤에서부터 넣는다: 앞 오프셋이 그대로이고 새 각주는 늘 그 문단의 첫 컨트롤이다.
    for fn in sorted(plan["footnotes"], key=lambda f: (f["block"], -f["offset"])):
        para = str(paras[fn["block"]])
        if not (step(["insert-footnote", "--para", para, "--offset", str(fn["offset"])])
                and step(["insert-footnote-text", "--para", para, "--ctrl", "0", "--fn-para", "0",
                          "--offset", "2", "--text", fn["text"]])):
            result["error"] = "각주 삽입 실패"
            return result
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
    for f in plan["footnotes"]:
        if escape(f["text"]) not in xml:
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
    return problems + problems_toc


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
