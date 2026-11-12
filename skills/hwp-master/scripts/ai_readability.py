#!/usr/bin/env python3
"""AI 가독성 진단: 행정문서가 AI 가 읽기 쉬운 구조인지 점검하고 고치는 법을 제안한다 (원본 무수정).

    python3 ai_readability.py <프로젝트> <sources/문서> [...] [--rhwp PATH] [--min-score 80]

근거: 행안부 'AI 시대 행정문서 작성 가이드라인'(서술형 문장, 표 안의 표·복잡한 병합 지양)과 지디넷 2026-11-10
「HWP 탓하는 정부, 이미 다 읽는 AI」(투명 표·표 안의 표·의전용 편집이 데이터와 틀의 구분을 막는다).

점검 항목 (100점에서 항목별 상한까지 깎는다)
  A01 표 안의 표            -5/개, 최대 -20   안쪽 표를 바깥 표 밖으로 꺼내 따로 둔다
  A02 복잡한 병합           -3/표, 최대 -15   병합 칸 면적 30% 이상이거나 머리 행 3단 이상. 머리 행을 1~2단으로 줄인다
  A03 레이아웃 표           -1/개, 최대 -15   제목 상자·투명 표 대신 문단 스타일(개요·글상자 없는 본문)을 쓴다
  A04 공백·탭 정렬          -1/개, 최대 -10   글 가운데 공백 4칸 이상·탭 2개 이상으로 맞춘 문단. 표나 탭 설정을 쓴다
  A05 빈 문단 간격          -1/곳, 최대 -10   빈 문단 2개 이상 연속. 문단 간격(위·아래 여백)으로 띄운다
  A06 손 번호               -0.2/개, 최대 -10 "1." "가." 를 글자로 친 문단. 한글 자동 번호(개요·문단 번호)를 쓴다
  A07 그림만 있는 쪽        -5/쪽, 최대 -10   글 없이 그림만 있는 쪽. 그림 속 글을 본문으로 옮기거나 대체 글을 단다
  A08 단편만 있는 장        -2/장, 최대 -10   개조식 항목 3개 이상이 모두 서술어 없는 단편. 핵심 문장을 서술형으로 쓴다

결과: validation/ai_readability_<시각>.json (문서마다 score·grade·checks[code, count, penalty, blockIds, fix]).
--min-score 를 주면 그보다 낮은 문서가 있을 때 exit 1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docmodel  # noqa: E402
import rhwp_locate  # noqa: E402

READABLE = {".hwp", ".hwpx", ".hml"}
CHECKS = {
    "A01": ("표 안의 표", 5.0, 20.0, "안쪽 표를 바깥 표 밖으로 꺼내 따로 두세요. AI 는 칸 안의 표를 바깥 칸 글로 읽습니다."),
    "A02": ("복잡한 병합", 3.0, 15.0, "머리 행을 1~2단으로 줄이고 병합을 풀어 칸마다 값 하나를 두세요."),
    "A03": ("레이아웃 표", 1.0, 15.0, "제목 상자·투명 표 대신 문단 스타일(개요 제목·본문)을 쓰세요. 표는 데이터에만 씁니다."),
    "A04": ("공백·탭 정렬", 1.0, 10.0, "공백·탭을 여러 개 넣어 줄을 맞추지 말고 표나 탭 설정을 쓰세요."),
    "A05": ("빈 문단 간격", 1.0, 10.0, "빈 문단을 여러 개 넣지 말고 문단 위·아래 간격으로 띄우세요."),
    "A06": ("손 번호", 0.2, 10.0, "\"1.\" \"가.\" 를 글자로 치지 말고 한글 자동 번호(개요·문단 번호)를 쓰세요."),
    "A07": ("그림만 있는 쪽", 5.0, 10.0, "그림 속 글을 본문으로 옮기거나 그림 아래에 설명 문장을 다세요."),
    "A08": ("단편만 있는 장", 2.0, 10.0, "개조식 단편만 늘어놓지 말고 장마다 핵심을 서술형 문장으로 한 번 쓰세요."),
}
HAND_NUMBER_KINDS = {"1.", "가.", "1)", "가)", "(1)", "(가)"}
ALIGN = re.compile(r"\S(?: {4,}|\t{2,}|[ 　]{2,})\S")
SENTENCE_END = re.compile(r"(다|요|음|함|임|됨|니다|습니다)[.。]?\s*$")


def _merged_ratio(table: dict) -> float:
    area = table["rows"] * table["cols"] or 1
    merged = sum(c["rowSpan"] * c["colSpan"] for c in table["cells"] if c["rowSpan"] * c["colSpan"] > 1)
    return merged / area


def _header_depth(table: dict) -> int:
    corner = next((c for c in table["cells"] if c["row"] == 0 and c["col"] == 0), None)
    return corner["rowSpan"] if corner else 1


def diagnose(document: dict) -> dict:
    blocks = document["blocks"]
    found: Dict[str, List[str]] = {code: [] for code in CHECKS}
    for block in blocks:
        if block["type"] == "table" and not block.get("fromLayoutTable"):
            for table in docmodel.iter_tables(block):
                if table.get("depth"):
                    found["A01"].append(table["id"])
                if table.get("role") == "layout":
                    found["A03"].append(table["id"])
                elif _merged_ratio(table) >= 0.3 or _header_depth(table) >= 3:
                    found["A02"].append(table["id"])
        elif block.get("text"):
            if ALIGN.search(block["text"]):
                found["A04"].append(block["id"])
            o = block.get("outline")
            if o and o["kind"] in HAND_NUMBER_KINDS and block["type"] == "paragraph":
                found["A06"].append(block["id"])
    found["A05"] = [r["start"] for r in document.get("emptyRuns", [])]

    pages: Dict[int, Dict[str, int]] = {}
    for block in blocks:
        info = pages.setdefault(block.get("page", 0), {"text": 0, "image": 0})
        if block["type"] == "image":
            info["image"] += 1
        elif block.get("text", "").strip() or block["type"] == "table":
            info["text"] += 1
    image_pages = [p for p, info in sorted(pages.items()) if info["image"] and not info["text"]]
    found["A07"] = [next(b["id"] for b in blocks if b.get("page", 0) == p and b["type"] == "image")
                    for p in image_pages]

    # 1단계 항목(또는 개요 제목) 아래 항목들이 모두 서술어 없는 단편인 장
    sections: Dict[str, List[dict]] = {}
    for block in blocks:
        o = block.get("outline")
        if o and o["kind"] != "note" and o["level"] >= 2 and o.get("parentId"):
            root = o["parentId"]
            while True:
                parent = next((b for b in blocks if b["id"] == root), None)
                po = (parent or {}).get("outline")
                if not po or po["level"] <= 1 or not po.get("parentId"):
                    break
                root = po["parentId"]
            sections.setdefault(root, []).append(block)
    for root, items in sections.items():
        if len(items) >= 3 and not any(SENTENCE_END.search(i["outline"]["body"]) for i in items):
            found["A08"].append(root)

    checks, score = [], 100.0
    for code, (title, per, cap, fix) in CHECKS.items():
        ids = found[code]
        penalty = round(min(cap, per * len(ids)), 1)
        score -= penalty
        checks.append({"code": code, "title": title, "count": len(ids), "penalty": penalty,
                       "blockIds": ids[:50], "fix": fix if ids else ""})
    score = round(max(0.0, score), 1)
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 65 else "D"
    return {"score": score, "grade": grade, "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project")
    ap.add_argument("documents", nargs="+")
    ap.add_argument("--rhwp")
    ap.add_argument("--min-score", type=float)
    args = ap.parse_args(argv)
    project = Path(args.project).resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    results, errors = [], []
    for doc in args.documents:
        path = Path(doc) if Path(doc).is_absolute() else project / doc
        try:
            path.resolve().relative_to((project / "sources").resolve())
        except ValueError:
            errors.append({"code": "X101", "where": doc, "message": "sources/ 안의 문서가 아닙니다"})
            continue
        if not path.is_file() or path.suffix.lower() not in READABLE:
            errors.append({"code": "X101", "where": doc, "message": "HWP·HWPX·HML 문서가 아닙니다"})
            continue
        import subprocess
        dump = subprocess.run([rhwp, "dump-pages", str(path), "--json"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        dump_json = json.loads(dump.stdout) if dump.returncode == 0 and dump.stdout.strip() else {}
        document = docmodel.build_document(rhwp, path, dump_json)
        if "_error" in document:
            errors.append({"code": "X102", "where": doc, "message": document["_error"]})
            continue
        result = {"source": path.resolve().relative_to(project).as_posix(), **diagnose(document)}
        results.append(result)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = project / "validation" / f"ai_readability_{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    low = [r for r in results if args.min_score is not None and r["score"] < args.min_score]
    report = {"route": "ai-readability", "ok": not errors and not low, "errors": errors, "documents": results,
              "minScore": args.min_score, "untrustedContent": True}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for r in results:
        worst = ", ".join(f"{c['code']} {c['title']} {c['count']}" for c in r["checks"] if c["count"])
        print(f"{r['source']}: {r['score']}점 ({r['grade']}) — {worst or '지적 없음'}", file=sys.stderr)
    for e in errors:
        print(f"[error] {e['code']} {e['message']} {e['where']}", file=sys.stderr)
    print(json.dumps({"ok": report["ok"], "report": str(report_path),
                      "scores": {r["source"]: r["score"] for r in results}}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
