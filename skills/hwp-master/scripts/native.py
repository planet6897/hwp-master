"""HDS → 네이티브 HWPX 생산: rhwp scaffold 와 편집 후처리.

scaffold 명세 v1 은 제목·문단·표만 받는다. HDS 의 나머지 요소는 scaffold 결과에
`rhwp edit` 을 순서대로 적용해 붙인다.

    page_break 블록          → edit insert-page-break (다음 블록 문단 앞에서 쪽 나눔)
    image 블록               → 표시 글자 문단 → edit delete-text + insert-picture
                               + set-picture {"treatAsChar":true} + apply-para-format 정렬,
                               caption 은 가운데 정렬 문단
    paragraph.emphasis       → edit apply-char-format (굵게·기울임·밑줄·글자색)
    paragraph.footnotes      → edit insert-footnote + insert-footnote-text
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

HU_PER_MM = 7200 / 25.4
HU_PER_PX = 75  # 96dpi 픽셀 1개 (rhwp insert-picture 의 원본 크기 환산과 같다)
A4_WIDTH_MM = 210.0
SIDE_MARGINS_MM = 60.0  # scaffold 기본 좌우 여백 30mm × 2
IMAGE_MARK = "[[hwp-master:image:{n}]]"
HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
PAGE_TOKEN = "{page}"
LIST_MARKERS = ("□", "○", "-", "·")
LIST_STEP_HU = 1000  # 단계마다 왼쪽 여백 10pt
# 글머리 + 빈칸 한 개의 폭(본문 10pt 기준 HWPUNIT). 한컴 2024 가 함초롬바탕 10pt 를 그린 PDF 의
# 글자 상자 간격에서 쟀다: "□ " 14.75pt, "○ " 14.75pt, "- " 10.55pt, "· " 8.16pt, "가. " 약 18pt.
SPACE_HU = 475
MARKER_HU = {"-": 580, "·": 341, "ㆍ": 341, "*": 500, ".": 300, ")": 330, "(": 330}
BODY_HEIGHT_HU = 1000  # scaffold 본문 글자 크기 10pt



def split(doc: dict) -> tuple:
    """HDS → (scaffold 명세, 편집 계획). 편집 계획이 비면 scaffold 만으로 끝난다."""
    spec = {k: copy.deepcopy(v) for k, v in doc.items() if k not in ("header", "footer", "blocks")}
    blocks: List[dict] = []
    plan: Dict[str, object] = {"page_breaks": [], "footnotes": [], "emphasis": [], "images": [],
                               "aligns": [], "indents": [], "header": doc.get("header"),
                               "footer": doc.get("footer")}
    pending_break = False

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
            plan["aligns"].append({"block": len(blocks), "align": b.get("align", "center")})
            blocks.append({"type": "paragraph", "text": mark})
            if b.get("caption"):
                plan["aligns"].append({"block": len(blocks), "align": "center"})
                blocks.append({"type": "paragraph", "text": b["caption"]})
            continue
        add_para(b)
    spec["blocks"] = blocks
    return spec, plan


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
    if mark.get("color"):
        props["textColor"] = mark["color"].upper()
    return json.dumps(props, separators=(",", ":"))


def plan_empty(plan: dict) -> bool:
    return not (plan["page_breaks"] or plan["footnotes"] or plan["emphasis"] or plan["images"]
                or plan["indents"] or plan["header"] or plan["footer"])


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
    content_mm = float((doc.get("page_size") or {}).get("width_mm", A4_WIDTH_MM)) - SIDE_MARGINS_MM
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


def top_paragraphs(hwpx: Path) -> List[dict]:
    """구역 0 의 최상위 문단 목록: 글자(본문 run 의 hp:t 만)와 표 포함 여부."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = []
    for p in root.findall(f"{{{HP}}}p"):
        text = "".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
        out.append({"text": text, "table": p.find(f".//{{{HP}}}tbl") is not None})
    return out


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
    """header.xml charPr id → {bold, italic, underline, color}."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/header.xml"))
    out = {}
    for cp in root.iter(f"{{{HH}}}charPr"):
        ul = cp.find(f"{{{HH}}}underline")
        out[cp.get("id")] = {"bold": cp.find(f"{{{HH}}}bold") is not None,
                             "italic": cp.find(f"{{{HH}}}italic") is not None,
                             "underline": ul is not None and ul.get("type", "NONE") != "NONE",
                             "color": (cp.get("textColor") or "").upper()}
    return out


def map_blocks(paras: List[dict], blocks: List[dict], has_title: bool) -> List[Optional[int]]:
    """scaffold 블록 순서대로 대응하는 최상위 문단 번호. 못 찾으면 None."""
    out: List[Optional[int]] = []
    i = 1 if has_title else 0
    for b in blocks:
        found = None
        for j in range(i, len(paras)):
            p = paras[j]
            if (b.get("type") == "table" and p["table"]) or (
                    b.get("type") != "table" and not p["table"] and p["text"] == b.get("text")):
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
    """HDS → out. 반환: {"ok", "scaffold": {...}, "edits": [...], "error"?}."""
    work.mkdir(parents=True, exist_ok=True)
    spec, plan = split(doc)
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
        if not step(["apply-para-format", "--para", str(paras[al["block"]]),
                     "--props", json.dumps({"alignment": al["align"]}, separators=(",", ":"))]):
            result["error"] = "문단 정렬 실패"
            return result
    # apply-para-format 의 여백 값은 HWPUNIT 의 2배로 받는다(4000 → 저장 2000 = 20pt).
    for ind in plan["indents"]:
        props = {"marginLeft": ind["left"] * 2, "indent": ind["indent"] * 2}
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


def check(out: Path, doc: dict) -> List[str]:
    """산출물에 계획한 요소가 실제로 들어갔는지 센다. 어긋난 항목의 설명 목록."""
    _, plan = split(doc)
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
        spec, _ = split(doc)
        paras = map_blocks(top_paragraphs(out), spec["blocks"], bool(spec.get("title")))
        styles, shapes = char_styles(out), char_shapes(out)
        for em in plan["emphasis"]:
            want = json.loads(em["props"])
            pi = paras[em["block"]]
            ids = styles[pi][em["offset"]:em["offset"] + em["count"]] if pi is not None else []
            bad = len(ids) != em["count"] or any(
                not shapes.get(i, {}).get(k) if k != "textColor" else shapes.get(i, {}).get("color") != v
                for i in ids for k, v in want.items())
            if bad:
                problems.append(f"강조 미반영: blocks[{em['block']}] {em['offset']}+{em['count']} {em['props']}")
    if plan["indents"]:
        spec, _ = split(doc)
        paras = map_blocks(top_paragraphs(out), spec["blocks"], bool(spec.get("title")))
        margins = para_margins(out)
        for ind in plan["indents"]:
            pi = paras[ind["block"]]
            got = margins[pi] if pi is not None else None
            if got != (ind["left"], ind["indent"]):
                problems.append(f"목록 들여쓰기 미반영: blocks[{ind['block']}] {got} "
                                f"(계획 {(ind['left'], ind['indent'])})")
    for kind in ("header", "footer"):
        hf = plan[kind]
        has = f"<hp:{kind} " in xml
        if bool(hf) != has:
            problems.append(f"{kind} {'없음' if hf else '계획에 없는데 있음'}")
        if hf and PAGE_TOKEN in hf["text"] and 'numType="PAGE"' not in xml:
            problems.append(f"{kind} 쪽 번호 없음")
    return problems
