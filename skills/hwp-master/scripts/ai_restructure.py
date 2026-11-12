#!/usr/bin/env python3
"""AI 가독성 개선본: 기존 HWP/HWPX 를 AI 가 읽기 쉬운 구조로 다시 짠 새 HWPX 를 만든다 (원본 무수정).

    python3 ai_restructure.py <프로젝트> <sources/문서> [...] [--rhwp PATH] [--preview]

문서 모델(docmodel)을 HDS 명세로 옮겨 Quick 빌드(build.py)로 만든다. 고치는 것(ai_readability 항목 기준):
  A03 레이아웃 표   제목 상자·투명 표·한 행 표를 풀어 일반 문단으로 (글상자 안 글도)
  A01 표 안의 표    안쪽 표를 바깥 표 바로 뒤로 꺼내고, 바깥 칸에는 "[아래 표 n]" 표시를 남긴다
  A04 공백 정렬     글 가운데 공백·탭 여러 개를 하나로
  A05 빈 문단       빈 문단을 옮기지 않는다(간격은 문단 서식이 맡는다)
  A06 손 번호       짧은 "1. 가." 항목은 개요 제목(자동 번호)으로, 숫자형 항목은 단계 목록(list)으로.
                   개조식(□ ○ - ·)은 기호를 글에 둔 문단으로(들여쓰기 꾸밈 생략 — 긴 문서 빌드 시간)
  ※·* 주석         바로 앞 문단·항목의 각주로
그대로 두는 것: 그림(옮기지 않고 보고), 머리말·꼬리말, 글자 모양. 개선본은 AI·검색·요약용 정리본이다.
제출용 원본을 대신하지 않는다.

결과: exports/ai/<문서>.hds.json·.hwpx, validation/ai_restructure_<시각>.json
  - before/after: ai_readability 점수·등급·항목
  - coverage: 원본의 글 단위(문단·칸)가 개선본에 모두 들어갔는지(공백·항목 기호를 빼고 비교), 빠진 글 목록
글이 빠지거나(coverage < 1) 빌드가 실패하면 그 문서는 오류다(exit 1).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_readability  # noqa: E402
import docmodel  # noqa: E402
import rhwp_locate  # noqa: E402

READABLE = {".hwp", ".hwpx", ".hml"}
HEADING_NUMBERS = {"Ⅰ.", "1.", "가."}
BULLET_KINDS = {"□", "○", "-", "·", "①"}
_GAPS = re.compile(r"[ \t 　]{2,}")
_SPACE = re.compile(r"[\s 　]+")


def clean(text: str) -> str:
    """줄 안의 공백·탭 정렬을 한 칸으로. 줄 바꿈은 둔다."""
    return "\n".join(_GAPS.sub(" ", line).strip() for line in (text or "").split("\n")).strip()


def _short_title(body: str) -> bool:
    return len(body) <= 30 and not re.search(r"(다|요|함|음|임)[.。]?$", body)


def table_spec(table: dict, nested_labels: Dict[str, str]) -> dict:
    rows = [["" for _ in range(table["cols"])] for _ in range(table["rows"])]
    merges = []
    for cell in table["cells"]:
        r, c = cell["row"], cell["col"]
        if not (0 <= r < table["rows"] and 0 <= c < table["cols"]):
            continue
        own = "\n".join(clean(p["text"]) for p in cell.get("paragraphs", []) if p["text"].strip()) \
            if cell.get("paragraphs") else clean(cell["text"])
        marks = [f"[아래 표 {nested_labels[t['id']]}]" for t in cell.get("tables", [])]
        text = "\n".join(x for x in [own] + marks if x)
        # 병합을 풀고 덮던 자리마다 같은 글을 둔다 — "칸마다 값 하나"(A02). 행 이름·머리 경로가 칸마다 남아 AI 가 읽기 쉽다.
        for rr in range(r, min(table["rows"], r + max(1, cell["rowSpan"]))):
            for cc in range(c, min(table["cols"], c + max(1, cell["colSpan"]))):
                rows[rr][cc] = text
    # 머리 행 꾸밈(바탕·굵게·가운데)은 넣지 않는다. 사람 보기용 꾸밈이라 AI 읽기에는 쓸모가 없고, 칸마다 rhwp 편집을
    # 세 번씩 돌려 긴 문서(표 86개·머리 칸 384개)에서는 빌드가 한 시간을 넘긴다.
    return {"type": "table", "rows": rows}


def to_hds(document: dict, title: str) -> dict:
    """문서 모델 → HDS 명세. 보고용 메모(옮기지 않은 것)를 함께 돌려준다."""
    blocks: List[dict] = []
    notes = {"images": 0, "headerFooters": 0, "headingsFromNumbers": 0, "listItems": 0, "footnotes": 0,
             "nestedTablesLifted": 0, "layoutTablesUnwrapped": 0, "textBoxes": 0}
    current_list: Optional[dict] = None
    levels: Dict[int, str] = {}
    list_base = [1]
    last_target: Optional[dict] = None  # 각주를 달 문단·항목

    def flush():
        nonlocal current_list
        if current_list:
            depth = max(i.get("level", 1) for i in current_list["items"])
            current_list["markers"] = [levels.get(k, "-") for k in range(1, depth + 1)]
            blocks.append(current_list)
        current_list = None

    def footnote(text: str):
        nonlocal last_target
        if last_target is not None:
            last_target.setdefault("footnotes", []).append({"text": text})
            notes["footnotes"] += 1
        else:
            flush()
            blocks.append({"type": "paragraph", "text": f"※ {text}"})

    def emit_table(table: dict):
        nonlocal last_target
        flush()
        lifted = [t for t in docmodel.iter_tables(table) if t is not table]
        labels = {t["id"]: str(n) for n, t in enumerate(lifted, 1)}
        blocks.append(table_spec(table, labels))
        for t in lifted:
            if t.get("role") == "layout":
                # 칸 안에 있던 상자·투명 표는 표로 다시 만들지 않고 글을 문단으로 꺼낸다(A03)
                for cell in sorted(t["cells"], key=lambda x: (x["row"], x["col"])):
                    for para in cell.get("paragraphs", []) or [{"text": cell["text"]}]:
                        if clean(para["text"]):
                            blocks.append({"type": "paragraph", "text": clean(para["text"])})
                notes["layoutTablesUnwrapped"] += 1
                continue
            spec = table_spec(t, labels)
            spec["caption"] = f"아래 표 {labels[t['id']]} (앞 표의 칸 안에 있던 표)"
            blocks.append(spec)
            notes["nestedTablesLifted"] += 1
        last_target = None

    for block in document["blocks"]:
        kind = block["type"]
        if kind == "image":
            notes["images"] += 1
            continue
        if kind == "headerFooter":
            notes["headerFooters"] += 1
            continue
        if kind == "textBox":
            notes["textBoxes"] += 1
            continue
        if kind == "table":
            if block.get("role") == "layout":
                notes["layoutTablesUnwrapped"] += 1  # 칸 글은 뒤따르는 풀린 문단 블록으로 온다
                continue
            emit_table(block)
            continue
        if kind == "note":
            footnote(clean(block.get("text", "")))
            continue
        if kind == "heading":
            flush()
            blocks.append({"type": "heading", "level": min(7, block.get("level", 1)), "text": clean(block["text"])})
            last_target = None
            continue
        text = clean(block.get("text", ""))
        if not text:
            continue
        o = block.get("outline")
        if o and o["kind"] == "note":
            footnote(clean(o["body"]))
            continue
        if o and o["kind"] in HEADING_NUMBERS and o["level"] <= 3 and _short_title(o["body"]):
            flush()  # 손으로 친 "1. 추진 배경" → 개요 제목(한글 자동 번호)
            blocks.append({"type": "heading", "level": o["level"], "text": clean(o["body"])})
            notes["headingsFromNumbers"] += 1
            last_target = None
            continue
        if o and o["kind"] in BULLET_KINDS:
            # 개조식 항목은 기호를 글에 남긴 문단으로 넣는다. 들여쓰기 꾸밈(rhwp edit 한 번씩)은 AI 읽기에 쓸모가 없고
            # 긴 문서에서 빌드 시간을 크게 늘린다. 기호가 글에 있으니 다시 추출해도 계층이 그대로 나온다.
            flush()
            para = {"type": "paragraph", "text": f"{o['marker']} {clean(o['body'])}"}
            blocks.append(para)
            notes["listItems"] += 1
            last_target = para
            continue
        if o:
            # 숫자형 항목(1) 가) (1) …)은 단계 목록으로. HDS 목록은 1단계에서 시작하고 단계를 건너뛸 수 없다
            # → 목록 첫 항목을 1단계로 보고 상대 단계로, 한 칸씩만 깊게
            if current_list is None:
                current_list = {"type": "list", "items": []}
                levels.clear()
                list_base[0] = o["level"]
            prev = current_list["items"][-1]["level"] if current_list["items"] else 0
            rel = max(1, min(o["level"] - list_base[0] + 1, prev + 1, 4))
            levels.setdefault(rel, o["marker"] if o["kind"] in ("□", "○", "-", "·", "①") else "-")
            item = {"text": clean(o["body"]), "level": rel}
            current_list["items"].append(item)
            notes["listItems"] += 1
            last_target = item
            continue
        flush()
        para = {"type": "paragraph", "text": text}
        blocks.append(para)
        last_target = para
    flush()
    if not blocks:
        blocks.append({"type": "paragraph", "text": "(본문 글자가 없는 문서입니다)"})
    return {"version": "1", "title": title, "blocks": blocks}, notes


def text_units(document: dict) -> List[str]:
    """보존 확인용 원본 글 단위(기호·공백 제외)."""
    units = []
    for block in document["blocks"]:
        if block["type"] in ("textBox", "image"):
            continue
        if block["type"] == "table" and block.get("role") == "layout":
            continue  # 칸 글은 뒤따르는 풀린 문단 블록(fromLayoutTable)으로 따로 센다
        if block["type"] == "table":
            for cell in docmodel.iter_cells(block):
                own = "".join(p["text"] for p in cell.get("paragraphs", [])) if cell.get("paragraphs") else cell["text"]
                units.append(own)
        elif block.get("text"):
            o = block.get("outline")
            units.append(o["body"] if o else block["text"])
    out = []
    for unit in units:
        parts = []
        for line in unit.splitlines():
            found = docmodel.outline_marker(line)  # 항목 기호는 개선본에서 목록·제목 번호가 되므로 빼고 비교
            parts.append(found[2] if found else line)
        out.append(_SPACE.sub("", "".join(parts)))
    return [u for u in out if u]


def coverage(before: dict, after: dict) -> dict:
    target = _SPACE.sub("", "".join(
        (b.get("text") or "") + "".join(c["text"] for c in (docmodel.iter_cells(b) if b["type"] == "table" else []))
        for b in after["blocks"]))
    units = text_units(before)
    missing = [u for u in units if u not in target]
    return {"units": len(units), "kept": len(units) - len(missing),
            "ratio": round((len(units) - len(missing)) / len(units), 4) if units else 1.0,
            "missing": [m[:60] for m in missing[:20]]}


def restructure_one(rhwp: str, project: Path, src: Path, preview: bool, timeout: int = 3600) -> dict:
    rel = src.relative_to(project).as_posix()
    out_dir = project / "exports" / "ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    row = {"source": rel, "findings": []}
    before = docmodel.build_document(rhwp, src)
    if "_error" in before:
        row["findings"].append({"severity": "error", "code": "I101", "message": before["_error"]})
        return row
    title = next((clean(b["text"]) for b in before["blocks"] if b["type"] == "heading"), src.stem)
    spec, notes = to_hds(before, title)
    hds_path = out_dir / f"{src.stem}.hds.json"
    hds_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = out_dir / f"{src.stem}.hwpx"
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "build.py"), "--quick", str(hds_path), "-o", str(out),
           "--rhwp", rhwp] + ([] if preview else ["--no-preview"])
    row.update({"hds": hds_path.relative_to(project).as_posix(), "output": out.relative_to(project).as_posix(),
                "changes": notes})
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        row["findings"].append({"severity": "error", "code": "I102",
                                "message": f"개선본 빌드가 {timeout}초 안에 끝나지 않았습니다(--timeout 으로 늘림). "
                                           "목록 항목이 아주 많은 긴 문서는 오래 걸린다"})
        return row
    if proc.returncode != 0 or not out.exists():
        row["findings"].append({"severity": "error", "code": "I102",
                                "message": f"개선본 빌드 실패(exit {proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}"})
        return row
    after = docmodel.build_document(rhwp, out)
    if "_error" in after:
        row["findings"].append({"severity": "error", "code": "I101", "message": after["_error"]})
        return row
    b, a = ai_readability.diagnose(before), ai_readability.diagnose(after)
    row["before"] = {"score": b["score"], "grade": b["grade"],
                     "checks": {c["code"]: c["count"] for c in b["checks"] if c["count"]}}
    row["after"] = {"score": a["score"], "grade": a["grade"],
                    "checks": {c["code"]: c["count"] for c in a["checks"] if c["count"]}}
    row["coverage"] = coverage(before, after)
    if row["coverage"]["ratio"] < 1.0:
        row["findings"].append({"severity": "error", "code": "I103",
                                "message": f"원본 글 {row['coverage']['units'] - row['coverage']['kept']}건이 개선본에 없음"})
    if notes["images"]:
        row["findings"].append({"severity": "warning", "code": "I201",
                                "message": f"그림 {notes['images']}개는 옮기지 않았습니다(그림 속 글은 원본에서 확인)"})
    if a["score"] < b["score"]:
        row["findings"].append({"severity": "warning", "code": "I202", "message": "개선본 점수가 원본보다 낮습니다"})
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project")
    ap.add_argument("documents", nargs="+")
    ap.add_argument("--rhwp")
    ap.add_argument("--preview", action="store_true", help="개선본 미리보기 PDF 도 만든다")
    ap.add_argument("--timeout", type=int, default=3600, help="문서 하나의 개선본 빌드 제한 시간(초, 기본 3600)")
    args = ap.parse_args(argv)
    project = Path(args.project).resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    rows = []
    for doc in args.documents:
        path = (Path(doc) if Path(doc).is_absolute() else project / doc).resolve()
        try:
            path.relative_to((project / "sources").resolve())
        except ValueError:
            rows.append({"source": doc, "findings": [{"severity": "error", "code": "X101",
                                                      "message": "sources/ 안의 문서가 아닙니다"}]})
            continue
        if not path.is_file() or path.suffix.lower() not in READABLE:
            rows.append({"source": doc, "findings": [{"severity": "error", "code": "X101",
                                                      "message": "HWP·HWPX·HML 문서가 아닙니다"}]})
            continue
        rows.append(restructure_one(rhwp, project, path, args.preview, args.timeout))
    errors = sum(1 for r in rows for f in r["findings"] if f["severity"] == "error")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = project / "validation" / f"ai_restructure_{stamp}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"route": "ai-restructure", "ok": errors == 0, "errors": errors, "documents": rows},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for r in rows:
        if "after" in r:
            print(f"{r['source']}: {r['before']['score']}({r['before']['grade']}) → {r['after']['score']}"
                  f"({r['after']['grade']}), 글 보존 {r['coverage']['ratio']:.0%} → {r['output']}", file=sys.stderr)
        for f in r["findings"]:
            print(f"  [{f['severity']}] {f['code']} {f['message'][:160]}", file=sys.stderr)
    print(json.dumps({"ok": errors == 0, "report": str(report),
                      "scores": {r["source"]: [r.get("before", {}).get("score"), r.get("after", {}).get("score")]
                                 for r in rows}}, ensure_ascii=False))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
