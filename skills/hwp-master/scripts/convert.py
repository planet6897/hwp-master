#!/usr/bin/env python3
"""Convert-in 라우트: MD·DOCX·PDF 를 HDS 단위로 옮긴다.

    python3 convert.py <프로젝트> <sources/자료> [--convention gongmun|gaejosik] [--title 제목]
                       [--max-pages N] [--force] [--rhwp PATH]

- MD: 직접 해석 (ATX 제목, 문단, 목록, 파이프 표, 인용, 코드 블록)
- DOCX: word/document.xml 을 직접 읽는다 (제목 스타일, 목록 번호, 표). 외부 도구 불필요.
- PDF: rhwp import-pdf → export-markdown → MD 경로.

정규화:
- 제목의 수동 번호("1. 개요")는 개요 자동 번호와 겹치므로 떼고 기록한다.
- 제목 수준은 가장 높은 수준이 1 이 되게 당긴다. 문서 맨 앞의 유일한 최상위 제목은 문서 제목으로 쓴다.
- 목록은 규약의 항목 기호와 두 칸 들여쓰기로 바꾼다
  (gaejosik: □ ○ - ㆍ, gongmun: 1. 가. 1) 가) …, 규약 없음: 원래 기호 유지).
- 표 열 정렬(:--, :-:, --:)은 cell_align 으로 옮긴다.
- HDS v1 이 담지 못하는 것(그림, 굵게·기울임 등 글자 서식, 링크 주소, 각주)은 버리지 않고 손실 목록에 남긴다.

산출: spec_lock.json(없을 때만), outline.json, units/U01.json… (수준 1 제목마다 한 단위),
analysis/<자료>.convert.json(정규화·손실 보고). 기존 단위가 있으면 --force 없이는 덮어쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conventions  # noqa: E402
import hds  # noqa: E402

# ------------------------------------------------------------------ 중간 표현
# ("heading", level, text) / ("para", text) / ("item", depth, ordered, text)
# ("table", rows, aligns[, header]) — header 는 첫 행이 머리행으로 표시된 표(Markdown 표)


class Losses:
    def __init__(self):
        self.items: List[dict] = []

    def add(self, kind: str, detail: str):
        self.items.append({"kind": kind, "detail": detail[:200]})


_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")
_EMPH = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_CODE = re.compile(r"`([^`]+)`")
_FOOTREF = re.compile(r"\[\^[^\]]+\]")


def inline(text: str, losses: Losses) -> str:
    """MD 인라인을 평문으로. 잃는 서식은 기록한다."""
    for m in _IMG.finditer(text):
        losses.add("image", f"{m.group(1)} ({m.group(2)})")
    text = _IMG.sub("", text)  # 그림은 본문에 넣지 않는다. 대체 문구를 문단으로 만들면 없는 내용이 된다.
    for m in _LINK.finditer(text):
        losses.add("link", f"{m.group(1)} → {m.group(2)}")
    text = _LINK.sub(lambda m: m.group(1), text)
    if _FOOTREF.search(text):
        losses.add("footnote", text)
        text = _FOOTREF.sub("", text)
    if _EMPH.search(text):
        losses.add("char_format", text)
        while _EMPH.search(text):
            text = _EMPH.sub(lambda m: m.group(2), text)
    text = _CODE.sub(lambda m: m.group(1), text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ Markdown

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def md_to_ir(text: str, losses: Losses, line_mode: bool = False) -> list:
    """line_mode: 줄마다 문단으로 둔다(PDF 추출본은 문단 사이 빈 줄이 없다)."""
    lines = text.replace("\r\n", "\n").split("\n")
    ir: list = []
    para: List[str] = []

    def flush():
        if para:
            t = inline(" ".join(para), losses)
            if t:
                ir.append(("para", t))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            flush()
            fence = line.lstrip()[:3]
            i += 1
            code = []
            while i < len(lines) and not lines[i].lstrip().startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1
            losses.add("code_block", "고정폭 서식 없이 문단으로 옮김")
            for c in code:
                if c.strip():
                    ir.append(("para", c.rstrip()))
            continue
        m = _ATX.match(line)
        if m:
            flush()
            ir.append(("heading", len(m.group(1)), inline(m.group(2), losses)))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush()
            header = _split_row(line)
            aligns = []
            for spec in _split_row(lines[i + 1]):
                left, right = spec.startswith(":"), spec.endswith(":")
                aligns.append("center" if left and right else "right" if right else "left" if left else None)
            rows = [[inline(c, losses) for c in header]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([inline(c, losses) for c in _split_row(lines[i])])
                i += 1
            ir.append(("table", rows, aligns, True))
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush()
            i += 1
            continue
        # PDF 추출본(line_mode)의 "1. 1. 추진 배경" 같은 줄은 목록이 아니라 번호가 박힌 제목일 수 있어
        # 목록으로 해석하지 않고 원문 그대로 둔다.
        mb, mo = (None, None) if line_mode else (_BULLET.match(line), _ORDERED.match(line))
        if mb or mo:
            flush()
            m = mb or mo
            depth = len(m.group(1).replace("\t", "    ")) // 2
            body = m.group(3)
            i += 1
            while i < len(lines) and lines[i].strip() and not _BULLET.match(lines[i]) \
                    and not _ORDERED.match(lines[i]) and lines[i].startswith(" "):
                body += " " + lines[i].strip()
                i += 1
            ir.append(("item", depth, bool(mo), inline(body, losses)))
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            losses.add("blockquote", "인용 서식 없이 문단으로 옮김")
            t = inline(" ".join(quote), losses)
            if t:
                ir.append(("para", t))
            continue
        para.append(line.strip())
        if line_mode:
            flush()
        i += 1
    flush()
    return ir


# ------------------------------------------------------------------ DOCX

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_styles(z: zipfile.ZipFile) -> dict:
    """styleId → (heading level 또는 0, title 여부)."""
    out = {}
    try:
        root = ET.fromstring(z.read("word/styles.xml"))
    except KeyError:
        return out
    for st in root.iter(W + "style"):
        sid = st.get(W + "styleId")
        name_el = st.find(W + "name")
        name = (name_el.get(W + "val") if name_el is not None else sid or "").lower()
        m = re.match(r"^(heading|제목)\s*(\d)$", name)
        if m:
            out[sid] = (int(m.group(2)), False)
        elif name in ("title", "제목"):
            out[sid] = (0, True)
    return out


def _docx_numbering(z: zipfile.ZipFile) -> dict:
    """numId → {ilvl: 번호 형식(bullet|decimal|…)}."""
    try:
        root = ET.fromstring(z.read("word/numbering.xml"))
    except KeyError:
        return {}
    abstract = {}
    for an in root.iter(W + "abstractNum"):
        lv = {}
        for l in an.iter(W + "lvl"):
            fmt = l.find(W + "numFmt")
            lv[int(l.get(W + "ilvl", "0"))] = fmt.get(W + "val") if fmt is not None else "bullet"
        abstract[an.get(W + "abstractNumId")] = lv
    out = {}
    for num in root.iter(W + "num"):
        ref = num.find(W + "abstractNumId")
        if ref is not None:
            out[num.get(W + "numId")] = abstract.get(ref.get(W + "val"), {})
    return out


def _para_text(p, losses: Losses) -> str:
    parts = []
    for node in p.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append(" ")
        elif node.tag in (W + "br", W + "cr"):
            parts.append(" ")
        elif node.tag == W + "drawing" or node.tag.endswith("}pict"):
            losses.add("image", "DOCX 그림")
        elif node.tag == W + "footnoteReference":
            losses.add("footnote", "DOCX 각주")
    for tag in ("b", "i", "u", "color", "highlight"):
        if p.find(f".//{W}rPr/{W}{tag}") is not None:
            losses.add("char_format", f"DOCX 글자 서식 {tag}")
            break
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def docx_to_ir(path: Path, losses: Losses) -> Tuple[list, Optional[str]]:
    with zipfile.ZipFile(path) as z:
        styles, numbering = _docx_styles(z), _docx_numbering(z)
        body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    ir, title = [], None
    for el in body:
        if el.tag == W + "p":
            text = _para_text(el, losses)
            ppr = el.find(W + "pPr")
            sid = None
            if ppr is not None and ppr.find(W + "pStyle") is not None:
                sid = ppr.find(W + "pStyle").get(W + "val")
            level, is_title = styles.get(sid, (0, False))
            num = ppr.find(W + "numPr") if ppr is not None else None
            if not text:
                continue
            if is_title and title is None:
                title = text
            elif level:
                ir.append(("heading", level, text))
            elif num is not None:
                ilvl_el, nid_el = num.find(W + "ilvl"), num.find(W + "numId")
                ilvl = int(ilvl_el.get(W + "val")) if ilvl_el is not None else 0
                fmt = numbering.get(nid_el.get(W + "val") if nid_el is not None else "", {}).get(ilvl, "bullet")
                ir.append(("item", ilvl, fmt != "bullet", text))
            else:
                ir.append(("para", text))
        elif el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    if tc.find(f"{W}tcPr/{W}vMerge") is not None or tc.find(f"{W}tcPr/{W}gridSpan") is not None:
                        losses.add("cell_merge", "DOCX 셀 병합은 병합 없이 옮김")
                    cells.append(" ".join(t for t in (_para_text(p, losses) for p in tc.iter(W + "p")) if t))
                rows.append(cells)
            if rows:
                ir.append(("table", rows, []))
        elif el.tag == W + "sectPr":
            continue
    return ir, title


# ------------------------------------------------------------------ PDF

def pdf_to_markdown(path: Path, rhwp: str, work: Path) -> str:
    hwpx = work / f"{path.stem}.import.hwpx"
    proc = subprocess.run([rhwp, "import-pdf", str(path), str(hwpx), "--layout", "editable", "--json"],
                          capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"import-pdf 실패 (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    md_dir = work / "md"
    proc = subprocess.run([rhwp, "export-markdown", str(hwpx), "-o", str(md_dir)],
                          capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"export-markdown 실패 (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    return "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(md_dir.rglob("*.md")))


# ------------------------------------------------------------------ IR → HDS

GAEJOSIK = ["□", "○", "-", "ㆍ"]
PLAIN_BULLETS = ["-", "·", "-", "·"]  # 규약이 없을 때 기호 목록


def _gongmun_marker(depth: int, n: int) -> str:
    level = min(depth + 1, 8)
    return conventions._marker_value(level, n)


def ir_to_blocks(ir: list, convention: Optional[str], report: dict,
                 heading_as_title: bool = True) -> Tuple[list, Optional[str]]:
    """heading_as_title: 다른 제목 출처(DOCX Title 스타일·--title)가 없을 때만 맨 앞 유일 최상위 제목을 문서 제목으로."""
    title = None
    levels = sorted({b[1] for b in ir if b[0] == "heading"})
    top = levels[0] if levels else 1
    tops = [b for b in ir if b[0] == "heading" and b[1] == top]
    if heading_as_title and tops and ir and ir[0] == tops[0] and len(tops) == 1 and len(levels) > 1:
        title = tops[0][2]
        ir = ir[1:]
        levels = sorted({b[1] for b in ir if b[0] == "heading"})
        top = levels[0] if levels else 1
        report["titleFromHeading"] = title
    blocks = []
    counters: dict = {}
    current_list: Optional[dict] = None  # 이어 붙이는 중인 list 블록
    for b in ir:
        kind = b[0]
        if kind != "item":
            current_list = None
        if kind == "heading":
            level = max(1, min(7, b[1] - top + 1))
            text = b[2]
            m = hds.MANUAL_NUMBER.match(text)
            if m and text[m.end():].strip():
                report["strippedHeadingNumbers"].append({"from": text, "to": text[m.end():].strip()})
                text = text[m.end():].strip()
            if text:
                blocks.append({"type": "heading", "level": level, "text": text})
            counters = {}
        elif kind == "para":
            # 항목 번호는 제목에서만 다시 센다 (conventions.lint_convention 과 같은 규칙).
            blocks.append({"type": "paragraph", "text": b[1]})
        elif kind == "item":
            depth, ordered, text = b[1], b[2], b[3]
            counters = {k: v for k, v in counters.items() if k <= depth}
            counters[depth] = counters.get(depth, 0) + 1
            # 개조식과 규약 없는 기호 목록은 list 블록(단계별 여백·내어쓰기)으로 옮긴다.
            # 번호 항목(gongmun, 규약 없는 순서 목록)은 번호가 매겨져야 하므로 문단으로 둔다.
            if convention == "gaejosik" or (convention is None and not ordered):
                markers = GAEJOSIK if convention == "gaejosik" else PLAIN_BULLETS
                if current_list is None or current_list["markers"] != markers:
                    current_list = {"type": "list", "markers": markers, "items": []}
                    blocks.append(current_list)
                prev = current_list["items"][-1]["level"] if current_list["items"] else 0
                level = min(depth + 1, prev + 1, len(markers))
                if level != depth + 1:
                    report.setdefault("listLevelAdjusted", []).append(
                        {"text": text, "from": depth + 1, "to": level})
                current_list["items"].append({"text": text, "level": level})
                continue
            current_list = None
            indent = "  " * depth
            if convention == "gaejosik":
                marker = GAEJOSIK[min(depth, 3)]
            elif convention == "gongmun":
                marker = _gongmun_marker(depth, counters[depth])
            else:
                marker = f"{counters[depth]}." if ordered else "-"
            blocks.append({"type": "paragraph", "text": f"{indent}{marker} {text}"})
        elif kind == "table":
            rows, aligns = b[1], b[2]
            ncols = max(len(r) for r in rows)
            rows = [r + [""] * (ncols - len(r)) for r in rows]
            block = {"type": "table", "rows": rows}
            if len(b) > 3 and b[3] and len(rows) > 1:
                block["header_row"] = True
            if any(aligns):
                block["cell_align"] = [a or "justify" for a in (aligns + [None] * ncols)[:ncols]]
            blocks.append(block)
    for blk in blocks:
        if blk["type"] == "list":
            blk["items"] = [it["text"] if it["level"] == 1 else it for it in blk["items"]]
            if blk["markers"] == GAEJOSIK:
                blk.pop("markers")  # list 기본 글머리(□ ○ - ·)를 쓴다
    return blocks, title


def split_units(blocks: list) -> List[Tuple[str, list]]:
    units, current = [], []
    for b in blocks:
        if b["type"] == "heading" and b["level"] == 1 and current:
            units.append(current)
            current = []
        current.append(b)
    if current:
        units.append(current)
    out = []
    for i, blks in enumerate(units, 1):
        name = next((b["text"] for b in blks if b["type"] == "heading"), "머리말")
        out.append((f"U{i:02d}", name, blks))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="hwp-master Convert-in")
    ap.add_argument("project", type=Path)
    ap.add_argument("source")
    ap.add_argument("--convention", choices=conventions.CONVENTIONS)
    ap.add_argument("--title")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--force", action="store_true", help="기존 outline.json·units 를 덮어쓴다")
    ap.add_argument("--rhwp")
    args = ap.parse_args()

    project = args.project.resolve()
    if not (project / "project.json").exists():
        print(f"hwp-master 프로젝트가 아닙니다 (project.json 없음): {args.project}", file=sys.stderr)
        return 2
    src = (project / args.source).resolve()
    try:
        src.relative_to(project)
    except ValueError:
        print(f"프로젝트 안의 자료가 아닙니다: {args.source}", file=sys.stderr)
        return 2
    if not src.is_file():
        print(f"파일 없음: {args.source}", file=sys.stderr)
        return 2
    units_dir = project / "units"
    if (any(units_dir.glob("U*.json")) or (project / "outline.json").exists()) and not args.force:
        print("이미 outline.json 이나 units/ 가 있습니다. 덮어쓰려면 --force", file=sys.stderr)
        return 2

    losses = Losses()
    report = {"source": str(src.relative_to(project)), "strippedHeadingNumbers": []}
    suffix = src.suffix.lower()
    docx_title = None
    try:
        if suffix in (".md", ".markdown", ".txt"):
            ir = md_to_ir(src.read_text(encoding="utf-8"), losses)
            report["path"] = "markdown"
        elif suffix == ".docx":
            ir, docx_title = docx_to_ir(src, losses)
            report["path"] = "docx (word/document.xml)"
        elif suffix == ".pdf":
            import rhwp_locate
            rhwp = rhwp_locate.locate(args.rhwp)["path"]
            with tempfile.TemporaryDirectory(prefix="hwpm-convert-") as tmp:
                ir = md_to_ir(pdf_to_markdown(src, rhwp, Path(tmp)), losses, line_mode=True)
            report["path"] = "pdf (rhwp import-pdf → export-markdown → 줄 단위 문단)"
            losses.add("pdf_layout", "PDF 의 쪽 배치·글자 모양은 옮기지 않고 글 흐름만 옮김")
            if not any(b[0] == "heading" for b in ir):
                losses.add("pdf_structure", "제목 수준을 알 수 없어 모든 줄을 문단으로 옮김 — 단위·제목을 손으로 다시 나눌 것")
        else:
            print(f"지원하지 않는 형식: {suffix} (지원: .md .docx .pdf)", file=sys.stderr)
            return 2
    except (RuntimeError, zipfile.BadZipFile, ET.ParseError, KeyError, UnicodeDecodeError) as exc:
        print(f"변환 실패: {exc}", file=sys.stderr)
        return 1

    blocks, heading_title = ir_to_blocks(ir, args.convention, report,
                                         heading_as_title=not (args.title or docx_title))
    title = args.title or docx_title or heading_title
    units = split_units(blocks)
    if not units:
        print("옮길 내용이 없습니다", file=sys.stderr)
        return 1

    lock_path = project / "spec_lock.json"
    lock = hds.load_json(lock_path) if lock_path.exists() else {}
    if title and not lock.get("title"):
        lock["title"] = title
    if args.convention:
        lock["convention"] = args.convention
    if args.max_pages:
        lock["max_pages"] = args.max_pages
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    units_dir.mkdir(exist_ok=True)
    for old in units_dir.glob("U*.json"):
        old.unlink()
    outline = {"source": report["source"], "units": []}
    for uid, name, blks in units:
        chars = sum(len(b.get("text", "")) + sum(len(c) for r in b.get("rows", []) for c in r) for b in blks)
        outline["units"].append({"id": uid, "title": name, "target_chars": chars})
        (units_dir / f"{uid}.json").write_text(
            json.dumps({"unit": uid, "blocks": blks}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (project / "outline.json").write_text(json.dumps(outline, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8")

    kinds: dict = {}
    for item in losses.items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    report.update({"title": title, "unitCount": len(units), "blockCount": len(blocks),
                   "losses": kinds, "lossDetails": losses.items})
    out = project / "analysis" / f"{src.stem}.convert.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"title": title, "units": len(units), "blocks": len(blocks), "losses": kinds,
                      "strippedHeadingNumbers": len(report["strippedHeadingNumbers"]), "report": str(out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
