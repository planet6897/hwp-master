#!/usr/bin/env python3
"""HWP → HWPX 일괄 변환과 검증 보고서 (원본 무수정).

    python3 convert_hwpx.py <프로젝트> <sources/문서|폴더|glob> [...] [--force] [--rhwp PATH]

공직자통합메일이 HWPX 외 첨부를 막아(2026-10~) HWP 를 HWPX 로 바꿔야 하지만, 바꾼 뒤 같은지 확인할 길이 없었다.
문서마다 세 겹으로 확인한다.
  1. rhwp export-hwpx --verify --verify-pages — 다시 읽은 IR 차이(diffCount)와 렌더 쪽 수
  2. 글 동일성 — 원본과 결과의 document.json(문단·표 칸·각주) 글을 공백을 빼고 차례대로 비교
  3. 구조 개수 — 문단·표·칸·그림 수

결과는 exports/hwpx/<sources 아래 경로>.hwpx (하위 폴더 유지). 같은 이름이 있으면 --force 없이는 덮어쓰지 않는다.
보고서 validation/convert_hwpx_<시각>.json·.csv.

  C201 오류  rhwp 변환 실패
  C202 오류  IR 차이(diffCount > 0)
  C203 오류  렌더 쪽 수가 다름
  C204 오류  글이 다름(첫 차이 위치를 보고)
  C205 오류  문단·표·칸·그림 수가 다름
  C101 경고  이미 HWPX 라 건너뜀 / C102 경고 결과 파일이 있어 건너뜀(--force)
오류가 하나라도 있으면 exit 1 이고, 그 문서는 완료로 보고하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docmodel  # noqa: E402
import extract  # noqa: E402
import rhwp_locate  # noqa: E402

CONVERTIBLE = {".hwp", ".hml"}
COUNT_KEYS = ("paragraph", "heading", "list", "table", "nestedTable", "cell", "image", "note")


def text_units(document: dict) -> List[str]:
    """비교용 글 목록: 블록·칸(안쪽 칸 포함) 글을 읽기 순서대로, 공백 없이."""
    units = []
    for block in document.get("blocks", []):
        if block.get("fromLayoutTable"):
            continue
        if block["type"] == "table":
            for cell in docmodel.iter_cells(block):
                own = "".join(p["text"] for p in cell.get("paragraphs", []))
                units.append(re.sub(r"\s+", "", own))
        elif block.get("text") is not None:
            units.append(re.sub(r"\s+", "", block["text"]))
    return units


def compare(before: dict, after: dict) -> dict:
    """두 문서 모델 비교 → {textSame, firstDiff, counts{key: [전, 후]}, countsSame}."""
    a, b = text_units(before), text_units(after)
    first = None
    for k in range(max(len(a), len(b))):
        x = a[k] if k < len(a) else None
        y = b[k] if k < len(b) else None
        if x != y:
            first = {"index": k, "before": (x or "")[:80], "after": (y or "")[:80]}
            break
    counts = {key: [before["counts"].get(key, 0), after["counts"].get(key, 0)] for key in COUNT_KEYS}
    return {"textSame": first is None, "firstDiff": first, "units": [len(a), len(b)], "counts": counts,
            "countsSame": all(x == y for x, y in counts.values())}


def convert_one(rhwp: str, project: Path, src: Path, force: bool) -> dict:
    rel = src.relative_to((project / "sources").resolve())
    out = project / "exports" / "hwpx" / rel.with_suffix(".hwpx")
    row = {"source": src.relative_to(project.resolve()).as_posix(), "output": out.relative_to(project).as_posix(),
           "status": "", "findings": []}

    def find(severity, code, message):
        row["findings"].append({"severity": severity, "code": code, "message": message})

    if src.suffix.lower() == ".hwpx":
        row["status"] = "skipped"
        find("warning", "C101", "이미 HWPX 입니다")
        return row
    if out.exists() and not force:
        row["status"] = "skipped"
        find("warning", "C102", "결과 파일이 이미 있습니다(--force 로 덮어씀)")
        return row
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([rhwp, "export-hwpx", str(src), str(out), "--verify", "--verify-pages", "--json"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    try:
        env = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        env = {}
    row["rhwpExit"] = proc.returncode
    row["bytes"] = env.get("bytes")
    verify = env.get("verify") or {}
    row["diffCount"] = verify.get("diffCount")
    pages = env.get("verifyPages") or {}
    row["pages"] = [pages.get("before", pages.get("source")), pages.get("after", pages.get("output"))] if pages else None
    if proc.returncode not in (0, 3, 4) or not out.exists():
        row["status"] = "failed"
        find("error", "C201", f"rhwp export-hwpx exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}")
        return row
    if proc.returncode == 3 or (row["diffCount"] or 0) > 0:
        find("error", "C202", f"다시 읽은 IR 차이 {row['diffCount']}건")
    if proc.returncode == 4:
        find("error", "C203", f"렌더 쪽 수가 다름: {row['pages']}")

    before = docmodel.build_document(rhwp, src)
    after = docmodel.build_document(rhwp, out)
    if "_error" in before or "_error" in after:
        find("error", "C201", f"문서 모델을 만들지 못함: {before.get('_error') or after.get('_error')}")
    else:
        result = compare(before, after)
        row.update({"textSame": result["textSame"], "countsSame": result["countsSame"], "counts": result["counts"]})
        if not result["textSame"]:
            d = result["firstDiff"]
            find("error", "C204", f"글이 다름(글 단위 {d['index']}): 전 {d['before']!r} / 후 {d['after']!r}")
        if not result["countsSame"]:
            diff = {k: v for k, v in result["counts"].items() if v[0] != v[1]}
            find("error", "C205", f"구조 개수가 다름: {diff}")
    row["status"] = "failed" if any(f["severity"] == "error" for f in row["findings"]) else "ok"
    return row


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project")
    ap.add_argument("documents", nargs="+", help="sources/ 아래 HWP·HML, 폴더(하위 포함), 또는 glob")
    ap.add_argument("--force", action="store_true", help="exports/hwpx/ 에 같은 이름이 있으면 덮어쓴다")
    ap.add_argument("--rhwp")
    args = ap.parse_args(argv)
    project = Path(args.project).resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    findings: List[dict] = []
    rows = []
    for doc, src in extract.expand_documents(project, args.documents, findings):
        rows.append(convert_one(rhwp, project, src, args.force))
    for f in findings:  # 인자 오류(X101)
        rows.append({"source": f["where"], "output": "", "status": "failed",
                     "findings": [{"severity": "error", "code": f["code"], "message": f["message"]}]})
    errors = sum(1 for r in rows for f in r["findings"] if f["severity"] == "error")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = project / "validation" / f"convert_hwpx_{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {"ok": errors == 0, "converted": sum(1 for r in rows if r["status"] == "ok"),
               "failed": sum(1 for r in rows if r["status"] == "failed"),
               "skipped": sum(1 for r in rows if r["status"] == "skipped")}
    report_path.write_text(json.dumps({"route": "convert-hwpx", **summary, "errors": errors, "documents": rows},
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with open(report_path.with_suffix(".csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "output", "status", "diffCount", "pages", "textSame", "countsSame", "findings"])
        for r in rows:
            writer.writerow([r["source"], r["output"], r["status"], r.get("diffCount", ""),
                             json.dumps(r.get("pages")) if r.get("pages") else "", r.get("textSame", ""),
                             r.get("countsSame", ""), "; ".join(f"{x['code']} {x['message']}" for x in r["findings"])])
    for r in rows:
        notes = "; ".join(f"{x['code']} {x['message']}" for x in r["findings"])
        print(f"[{r['status']}] {r['source']} → {r['output']}" + (f" — {notes}" if notes else ""), file=sys.stderr)
    print(json.dumps({**summary, "report": str(report_path)}, ensure_ascii=False))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
