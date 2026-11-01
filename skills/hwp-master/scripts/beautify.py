#!/usr/bin/env python3
"""Beautify 라우트: 기존 문서의 글자를 그대로 두고, 손으로 친 항목 번호를 개요 번호로 바꾼다.

    python3 beautify.py propose <프로젝트> <sources/문서.hwpx> [--bind-styles] [--rhwp PATH]
    python3 beautify.py apply   <프로젝트> <history/beautify_<시각>.plan.json> [--rhwp PATH]

손으로 친 "1. ", "가. ", "1) " … 는 화면에서는 번호처럼 보여도 문서 구조에서는 그냥 글자다.
개요 번호로 바꾸면 한글의 개요 보기·차례 만들기·번호 자동 갱신이 동작한다.

propose (읽기 전용):
  - HWPX 의 구역 본문 문단(표 안 제외)에서 공문서 항목 기호로 시작하고 개요 번호가 없는 문단을 찾는다.
  - 문서의 개요 번호 정의가 공문서 순서(1. 가. 1) 가) (1) (가) ①)인지 확인한다. 아니면 B001 오류.
  - 기존 개요 문단과 함께 번호를 앞에서부터 세어, 자동 번호가 손으로 친 번호와 같아지는 문단만 고른다.
    다르면(건너뛴 번호·단계 건너뜀) 그 문단은 빼고 B101 경고를 남긴다.
  - history/beautify_<시각>.plan.json 에 계획을 쓴다. 에이전트·사용자가 items 를 지워 범위를 줄일 수 있다.

apply:
  - 입력 SHA-256 이 계획과 같아야 한다(B002).
  - 문단마다 rhwp `edit delete-text`(기호 지우기) → (--bind-styles 면 `edit apply-style "개요 N"`) →
    `edit apply-para-format {"headType":"Outline"}` 을 잇고, 결과는 exports/<이름>_beautify_<시각>.hwpx.
  - 내용 보존 판정(B201): 전후 `export-text` 의 공백 제외 문자열이 같아야 한다 — 지운 기호를 자동 번호가
    같은 자리에서 다시 내므로, 한 글자라도 다르면 실패이고 산출을 남기지 않는다.
  - 조판 판정: 원본 대비 layout-anomaly 신호(겹침·넘침 등)가 늘거나 쪽수가 바뀌면 안 된다.
  - 한꺼번에 적용해 어느 판정이든 어긋나면 한 문단씩 다시 적용하며, 조판을 흐트러뜨리는 문단(B105)과
    그 때문에 번호가 당겨지는 문단(B106)을 빼고 나머지만 적용한다. 그래도 어긋나면 오류(R103·B201).
  - validation/beautify_<시각>.json 보고서. rhwp `run` 계획서가 이 편집을 담지 못하므로 작업 캡슐 대신
    보고서에 입력·계획·산출 SHA-256 과 실행한 명령 목록을 남긴다.

gaejosik(□ ○ - ㆍ) 문서는 대상이 아니다. 이 기호들은 개요 번호가 아니라 글머리표라서
번호 정의 대조로 보존을 보장할 수 없다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conventions  # noqa: E402
import rhwp_locate  # noqa: E402
from edit import finding, inside, run_json, sha256  # noqa: E402

NS = {
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}
HP = "{%s}" % NS["hp"]
MAX_LEVEL = 7  # 개요 1~7 (㉮ 는 개요 번호 정의에 없다)
# 문단에 실린 개체. 이런 문단의 글자를 고치면 개체 배치가 다시 계산되므로 손대지 않는다.
OBJECT_TAGS = {f"{HP}{t}" for t in ("tbl", "pic", "rect", "ellipse", "line", "arc", "polygon", "curve",
                                    "connectLine", "container", "equation", "ole", "textart", "chart")}
TOC_LINE = re.compile(r"\t\s*\d+\s*$")  # 차례 항목: 탭 뒤 쪽 번호로 끝난다

# 공문서 단계 → 한컴 개요 번호 정의(numFormat, 형식 문자열)
GONGMUN_OUTLINE = {
    1: ("DIGIT", "^1."), 2: ("HANGUL_SYLLABLE", "^2."), 3: ("DIGIT", "^3)"),
    4: ("HANGUL_SYLLABLE", "^4)"), 5: ("DIGIT", "(^5)"), 6: ("HANGUL_SYLLABLE", "(^6)"),
    7: ("CIRCLED_DIGIT", "^7"),
}


# ---------------------------------------------------------------- HWPX 읽기

def _para_text(p: ET.Element) -> str:
    """구역 문단의 본문 글자(표·글상자 안 문단 제외). 탭은 \\t."""
    out = []
    for run in p.findall(f"{HP}run"):
        for t in run.findall(f"{HP}t"):
            out.append(t.text or "")
            for child in t:
                if child.tag == f"{HP}tab":
                    out.append("\t")
                out.append(child.tail or "")
    return "".join(out)


def read_hwpx(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        header = ET.fromstring(z.read("Contents/header.xml"))
        sec_names = sorted((n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)),
                           key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
        sections = [ET.fromstring(z.read(n)) for n in sec_names]

    numberings: Dict[str, Dict[int, tuple]] = {}
    for num in header.iter(f"{{{NS['hh']}}}numbering"):
        heads = {}
        for ph in num.findall("hh:paraHead", NS):
            heads[int(ph.get("level", "0"))] = (ph.get("numFormat"), (ph.text or "").strip(),
                                                ph.get("start", "1"))
        numberings[num.get("id")] = heads
    para_heads = {}
    for pp in header.iter(f"{{{NS['hh']}}}paraPr"):
        h = pp.find("hh:heading", NS)
        para_heads[pp.get("id")] = (h.get("type", "NONE"), int(h.get("level", "0"))) if h is not None else ("NONE", 0)
    styles = {}
    for st in header.iter(f"{{{NS['hh']}}}style"):
        if st.get("type", "PARA") == "PARA":
            styles[st.get("name")] = {"id": int(st.get("id")), "paraPr": st.get("paraPrIDRef")}

    out_sections = []
    for si, sec in enumerate(sections):
        sec_pr = sec.find(".//hp:secPr", NS)
        outline_ref = sec_pr.get("outlineShapeIDRef") if sec_pr is not None else None
        paras = []
        for pi, p in enumerate(sec.findall(f"{HP}p")):
            head = para_heads.get(p.get("paraPrIDRef"), ("NONE", 0))
            has_object = any(el.tag in OBJECT_TAGS for el in p.iter())
            paras.append({"section": si, "para": pi, "text": _para_text(p), "head": head, "object": has_object})
        out_sections.append({"outlineRef": outline_ref, "paras": paras})
    return {"numberings": numberings, "styles": styles, "sections": out_sections}


def outline_mismatch(numbering: Optional[Dict[int, tuple]], levels) -> List[str]:
    """필요한 단계의 개요 번호 정의가 공문서 순서와 다른 항목 목록."""
    bad = []
    for lv in sorted(levels):
        want = GONGMUN_OUTLINE[lv]
        got = (numbering or {}).get(lv)
        if not got or (got[0], got[1]) != want or got[2] != "1":
            bad.append(f"{lv}수준 {want[1]}({want[0]}) ← 문서 {got[1] if got else '없음'}"
                       f"({got[0] if got else '-'}, 시작 {got[2] if got else '-'})")
    return bad


# ---------------------------------------------------------------- propose

def unique_stamp(project: Path) -> str:
    base = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stamp, n = base, 1
    while (any((project / "history").glob(f"beautify_{stamp}.*"))
           or any((project / "validation").glob(f"beautify_{stamp}.*"))
           or any((project / "exports").glob(f"*_beautify_{stamp}.*"))):
        n += 1
        stamp = f"{base}-{n}"
    return stamp


def plan_items(doc: dict, findings: List[dict]) -> List[dict]:
    """자동 번호가 손으로 친 번호와 같아지는 문단만 고른다. 개요 번호는 문서 전체에서 이어 센다."""
    counters = [0] * (MAX_LEVEL + 1)
    items = []
    for sec in doc["sections"]:
        for p in sec["paras"]:
            htype, hlevel = p["head"]
            where = f"구역 {p['section']} 문단 {p['para']}"
            if htype == "OUTLINE":  # 이미 개요 문단 — 번호 세기에만 참여
                lv = hlevel + 1
                if 1 <= lv <= MAX_LEVEL:
                    counters[lv] += 1
                    counters[lv + 1:] = [0] * (MAX_LEVEL - lv)
                continue
            if htype != "NONE":
                continue
            stripped = p["text"].lstrip(" 　")
            found = conventions.gongmun_marker(stripped)
            if not found:
                continue
            lv, n, space = found
            if p.get("object"):
                findings.append(finding("warning", "B104", "표·그림 같은 개체를 품은 문단이라 그대로 둡니다", where))
                continue
            if TOC_LINE.search(p["text"]):
                findings.append(finding("warning", "B103", "차례 항목(탭 뒤 쪽 번호)이라 그대로 둡니다", where))
                continue
            if lv > MAX_LEVEL:
                findings.append(finding("warning", "B102", f"{conventions.GONGMUN_LABEL[lv]} 단계는 개요 번호에 없어 그대로 둡니다", where))
                continue
            if lv > 1 and counters[lv - 1] == 0:
                findings.append(finding("warning", "B101",
                                        f"상위 단계 없이 {conventions.GONGMUN_LABEL[lv]} 이 나와 제외합니다", where))
                continue
            if n != counters[lv] + 1:
                findings.append(finding("warning", "B101",
                                        f"손 번호 {conventions._marker_value(lv, n)} 이 자동 순서 "
                                        f"{conventions._marker_value(lv, counters[lv] + 1)} 와 달라 제외합니다", where))
                continue
            lead = len(p["text"]) - len(stripped)
            marker_len = conventions.gongmun_marker_span(stripped)
            counters[lv] = n
            counters[lv + 1:] = [0] * (MAX_LEVEL - lv)
            items.append({"section": p["section"], "para": p["para"], "level": lv,
                          "delete": {"offset": 0, "count": lead + marker_len},
                          "marker": stripped[:marker_len].strip(), "preview": stripped[:40]})
    return items


def cmd_propose(args) -> int:
    project = Path(args.project).resolve()
    src = inside(project, args.document)
    findings: List[dict] = []
    if src is None or not src.is_file():
        print(f"[오류 B000] 프로젝트 안의 문서가 아닙니다: {args.document}")
        return 1
    if src.suffix.lower() != ".hwpx":
        print(f"[오류 B000] HWPX 만 받습니다. 먼저 `rhwp export-hwpx {args.document} sources/<이름>.hwpx` 로 바꾸세요")
        return 1
    rel = str(src.relative_to(project))
    doc = read_hwpx(src)
    items = plan_items(doc, findings)

    refs = {s["outlineRef"] for s in doc["sections"]}
    levels = {it["level"] for it in items}
    for ref in sorted(r or "" for r in refs):
        bad = outline_mismatch(doc["numberings"].get(ref), levels) if levels else []
        if bad:
            findings.append(finding("error", "B001",
                                    "개요 번호 정의가 공문서 순서와 달라 자동 번호가 손 번호를 재현하지 못합니다: "
                                    + "; ".join(bad), f"outlineShapeIDRef={ref or '-'}"))
    if args.bind_styles:
        for lv in sorted(levels):
            st = doc["styles"].get(f"개요 {lv}")
            if not st:
                findings.append(finding("error", "B003", f"'개요 {lv}' 스타일이 문서에 없습니다 (--bind-styles)"))
            else:
                for it in items:
                    if it["level"] == lv:
                        it["style"] = st["id"]

    stamp = unique_stamp(project)
    plan = {"kind": "hwp-master/beautify-plan", "input": rel, "inputSha256": sha256(src),
            "bindStyles": bool(args.bind_styles), "items": items, "findings": findings}
    plan_path = project / "history" / f"beautify_{stamp}.plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for f in findings:
        print(f"[{'오류' if f['severity'] == 'error' else '경고'} {f['code']}] {f['where']} {f['message']}".replace("  ", " "))
    ok = not any(f["severity"] == "error" for f in findings)
    print(json.dumps({"ok": ok, "items": len(items), "plan": str(plan_path.relative_to(project))}, ensure_ascii=False))
    return 0 if ok else 1


# ---------------------------------------------------------------- apply

def export_text(rhwp: str, path: Path, workdir: Path) -> Optional[str]:
    out = Path(tempfile.mkdtemp(dir=workdir))
    proc = subprocess.run([rhwp, "export-text", str(path), "-o", str(out)], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    text = "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("*.txt")))
    shutil.rmtree(out, ignore_errors=True)
    return text


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def cmd_apply(args) -> int:
    project = Path(args.project).resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    findings: List[dict] = []
    plan_path = inside(project, args.plan)
    report = {"started": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "rhwp": rhwp,
              "plan": args.plan, "commands": [], "findings": findings}
    stamp = re.sub(r"^beautify_|\.plan$", "", Path(args.plan).stem) if plan_path else unique_stamp(project)
    report_path = project / "validation" / f"beautify_{stamp}.json"
    out_path: Optional[Path] = None

    def finish() -> int:
        errors = [f for f in findings if f["severity"] == "error"]
        report["ok"] = not errors
        if errors and out_path and out_path.exists():
            out_path.unlink()
            report["output"] = None
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for f in findings:
            print(f"[{'오류' if f['severity'] == 'error' else '경고'} {f['code']}] {f.get('where', '')} {f['message']}".strip())
        print(json.dumps({"ok": report["ok"], "output": report.get("output"), "changed": report.get("changed"),
                          "report": str(report_path.relative_to(project))}, ensure_ascii=False))
        return 0 if report["ok"] else 1

    if plan_path is None or not plan_path.is_file():
        findings.append(finding("error", "B000", f"계획 파일 없음: {args.plan}"))
        return finish()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    report["planSha256"] = sha256(plan_path)
    src = inside(project, plan.get("input", ""))
    if src is None or not src.is_file():
        findings.append(finding("error", "B000", f"입력 문서 없음: {plan.get('input')}"))
        return finish()
    report["input"], report["inputSha256"] = plan["input"], sha256(src)
    if report["inputSha256"] != plan.get("inputSha256"):
        findings.append(finding("error", "B002", "입력 문서가 계획을 세운 뒤 바뀌었습니다 — propose 를 다시 실행하세요"))
        return finish()
    if any(f.get("severity") == "error" for f in plan.get("findings", [])):
        findings.append(finding("error", "B001", "계획에 오류가 있어 실행하지 않습니다 (propose 결과 확인)"))
        return finish()
    items = plan.get("items") or []
    if not items:
        findings.append(finding("error", "B000", "바꿀 문단이 없습니다"))
        return finish()
    if plan.get("bindStyles") and any("style" not in it for it in items):
        findings.append(finding("error", "B003", "--bind-styles 계획인데 스타일 번호가 없는 항목이 있습니다"))
        return finish()

    base = re.sub(r"(_beautify_\d{8}-\d{6}(-\d+)?)+$", "", src.stem)
    out_path = project / "exports" / f"{base}_beautify_{stamp}.hwpx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keys = ("overflowCount", "overlapCount", "textOverlapCount", "offCanvasCount", "emptyPageCount")
    work = Path(tempfile.mkdtemp(prefix="beautify_"))
    counter = [0]

    def edit_item(cur: Path, it: dict) -> Optional[Path]:
        """한 문단: 기호 지우기 → 개요 번호(또는 개요 스타일). 실패하면 B200 을 남기고 None."""
        sec, para = str(it["section"]), str(it["para"])
        subs = [["delete-text", "--offset", str(it["delete"]["offset"]), "--count", str(it["delete"]["count"])]]
        if it.get("style") is not None:
            # apply-style 은 직접 서식이 있는 문단의 문단 모양을 유지하므로 개요 번호는 아래에서 따로 켠다
            subs.append(["apply-style", "--style", str(it["style"])])
        subs.append(["apply-para-format", "--props",
                     # rhwp --props 는 공백 없는 "키":값 꼴만 읽는다 — 공백이 있으면 조용히 무시된다
                     json.dumps({"headType": "Outline", "paraLevel": it["level"] - 1}, separators=(",", ":"))])
        for sub in subs:
            counter[0] += 1
            nxt = work / f"step{counter[0]}.hwpx"
            cmd = [rhwp, "edit", sub[0], str(cur)] + sub[1:] + ["--section", sec, "--para", para,
                                                               "-o", str(nxt), "--verify", "--json"]
            res = run_json(cmd, work)
            report["commands"].append({"edit": sub[0], "section": it["section"], "para": it["para"],
                                       "exit": res["exit"]})
            if res["exit"] != 0 or not nxt.is_file():
                venv = (res.get("envelope") or {}).get("verify") or {}
                why = (f"저장 후 다시 연 문서가 편집 결과와 다릅니다 (차이 {venv.get('diffCount')}건) — "
                       "rhwp 가 이 문서를 그대로 저장하지 못합니다" if venv.get("identical") is False
                       else res.get("stderr", "").strip()[-300:])
                findings.append(finding("error", "B200", f"rhwp edit {sub[0]} 실패: {why}", f"구역 {sec} 문단 {para}"))
                return None
            cur = nxt
        return cur

    def anomaly(path: Path) -> dict:
        env = run_json([rhwp, "layout-anomaly", str(path), "--json"], work, ok_codes=(0, 3)).get("envelope") or {}
        return {k: env.get(k) or 0 for k in keys + ("pageCount",)}

    def worse(base_a: dict, got: dict) -> List[str]:
        out = [f"{k} {base_a[k]}→{got[k]}" for k in keys if got[k] > base_a[k]]
        if got["pageCount"] != base_a["pageCount"]:
            out.append(f"쪽수 {base_a['pageCount']}→{got['pageCount']}")
        return out

    def text_of(path: Path) -> Optional[str]:
        t = export_text(rhwp, path, work)
        return None if t is None else squash(t)

    try:
        base_a = anomaly(src)
        base_text = text_of(src)
        if base_text is None:
            findings.append(finding("error", "B201", "export-text 로 원본 내용을 읽지 못했습니다"))
            return finish()
        cur = work / "step0.hwpx"
        shutil.copyfile(src, cur)
        applied = []
        for it in items:  # 1차: 전부 잇달아 적용
            cur = edit_item(cur, it)
            if cur is None:
                return finish()
            applied.append(it)
        got, got_text = anomaly(cur), text_of(cur)
        if worse(base_a, got) or got_text != base_text:
            # 2차: 한 문단씩 적용하며 조판을 흐트러뜨리거나(rhwp 재조판 결함) 글자를 바꾸는(번호 순서 어긋남)
            # 문단을 뺀다. 앞 문단을 빼서 뒤 번호가 당겨지면 그 문단도 내용 검사에서 빠진다.
            report["isolation"] = {"firstPass": worse(base_a, got) + ([] if got_text == base_text else ["내용 차이"])}
            cur = work / "iso0.hwpx"
            shutil.copyfile(src, cur)
            applied = []
            for it in items:
                nxt = edit_item(cur, it)
                if nxt is None:
                    return finish()
                where = f"구역 {it['section']} 문단 {it['para']}"
                bad = worse(base_a, anomaly(nxt))
                if bad:
                    findings.append(finding("warning", "B105",
                                            "이 문단을 바꾸면 조판이 흐트러져 그대로 둡니다 (" + ", ".join(bad) + ")", where))
                    continue
                if text_of(nxt) != base_text:
                    findings.append(finding("warning", "B106",
                                            "자동 번호가 손 번호와 달라져 그대로 둡니다 (앞 문단 제외로 순서가 어긋남)", where))
                    continue
                cur = nxt
                applied.append(it)
            if not applied:
                findings.append(finding("error", "B000", "내용·조판을 지키며 바꿀 수 있는 문단이 없습니다"))
                return finish()
            got, got_text = anomaly(cur), text_of(cur)
        report["layoutAnomaly"] = {k: [base_a[k], got[k]] for k in keys + ("pageCount",)}
        grown = worse(base_a, got)
        if grown:
            findings.append(finding("error", "R103", "조판 이상 신호·쪽수가 바뀌었습니다: " + ", ".join(grown)))
            return finish()
        if got_text != base_text:
            a, b = base_text, got_text or ""
            i = next((j for j in range(min(len(a), len(b))) if a[j] != b[j]), min(len(a), len(b)))
            findings.append(finding("error", "B201",
                                    f"내용이 달라졌습니다 (공백 제외 {i}번째 글자부터: '{a[max(0, i - 8):i + 12]}' → "
                                    f"'{b[max(0, i - 8):i + 12]}')"))
            return finish()
        shutil.copyfile(cur, out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 한글은 개요 번호 뒤를 번호 정의의 간격(글자 크기 비율)으로 띄우고 넘친 줄을 본문 시작에 맞춰 들여 쓴다.
    # 손으로 친 공백과 폭이 달라 꽉 찬 줄은 한글에서 다시 줄바꿈될 수 있다 (rhwp 판정으로는 예측 불가).
    findings.append(finding("warning", "B150",
                            "개요 번호는 번호 뒤 간격·넘친 줄 들여쓰기가 손 번호와 달라 한글에서 줄바꿈·쪽 나눔이 "
                            "바뀔 수 있습니다 — 한글에서 열어 꽉 찬 제목 줄과 쪽 끝을 확인하세요"))
    report.update({"output": str(out_path.relative_to(project)), "outputSha256": sha256(out_path),
                   "changed": len(applied), "applied": [{"section": i["section"], "para": i["para"]} for i in applied]})
    return finish()


def main() -> int:
    ap = argparse.ArgumentParser(description="hwp-master Beautify (손 번호 → 개요 번호)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("project")
    p.add_argument("document")
    p.add_argument("--bind-styles", action="store_true", help="개요 번호 대신 문서의 '개요 N' 스타일을 적용")
    p.add_argument("--rhwp")
    p.set_defaults(func=cmd_propose)
    p = sub.add_parser("apply")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("--rhwp")
    p.set_defaults(func=cmd_apply)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
