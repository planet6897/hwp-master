"""HDS → 네이티브 HWPX 생산: rhwp scaffold 와 편집 후처리.

scaffold 명세 v1 은 제목·문단·표만 받는다. HDS 의 나머지 요소는 scaffold 결과에
`rhwp edit` 을 순서대로 적용해 붙인다.

    page_break 블록          → edit insert-page-break (다음 블록 문단 앞에서 쪽 나눔)
    paragraph.footnotes      → edit insert-footnote + insert-footnote-text
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
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
PAGE_TOKEN = "{page}"


def split(doc: dict) -> tuple:
    """HDS → (scaffold 명세, 편집 계획). 편집 계획이 비면 scaffold 만으로 끝난다."""
    spec = {k: copy.deepcopy(v) for k, v in doc.items() if k not in ("header", "footer", "blocks")}
    blocks: List[dict] = []
    plan: Dict[str, object] = {"page_breaks": [], "footnotes": [], "header": doc.get("header"),
                               "footer": doc.get("footer")}
    pending_break = False
    for b in doc.get("blocks", []):
        if b.get("type") == "page_break":
            pending_break = True
            continue
        b = copy.deepcopy(b)
        notes = b.pop("footnotes", None) or []
        if pending_break:
            plan["page_breaks"].append(len(blocks))
            pending_break = False
        for n in notes:
            text = b.get("text", "")
            after = n.get("after")
            offset = len(text) if after is None else text.index(after) + len(after)
            plan["footnotes"].append({"block": len(blocks), "offset": offset, "text": n["text"]})
        blocks.append(b)
    spec["blocks"] = blocks
    return spec, plan


def plan_empty(plan: dict) -> bool:
    return not (plan["page_breaks"] or plan["footnotes"] or plan["header"] or plan["footer"])


def top_paragraphs(hwpx: Path) -> List[dict]:
    """구역 0 의 최상위 문단 목록: 글자(본문 run 의 hp:t 만)와 표 포함 여부."""
    with zipfile.ZipFile(hwpx) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    out = []
    for p in root.findall(f"{{{HP}}}p"):
        text = "".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
        out.append({"text": text, "table": p.find(f".//{{{HP}}}tbl") is not None})
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
    need = set(plan["page_breaks"]) | {f["block"] for f in plan["footnotes"]}
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
    for kind in ("header", "footer"):
        hf = plan[kind]
        has = f"<hp:{kind} " in xml
        if bool(hf) != has:
            problems.append(f"{kind} {'없음' if hf else '계획에 없는데 있음'}")
        if hf and PAGE_TOKEN in hf["text"] and 'numType="PAGE"' not in xml:
            problems.append(f"{kind} 쪽 번호 없음")
    return problems
