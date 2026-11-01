#!/usr/bin/env python3
"""HDS → 네이티브 HWPX 빌드와 산출물 검사.

    python3 build.py <프로젝트>                       # Default: units 조립 → 빌드
    python3 build.py --quick <doc.hds.json> -o <out.hwpx>  # Quick: 단일 HDS
    공통 옵션: [--rhwp PATH] [--no-preview] [--png] [--font-path DIR ...]

단계: (조립) → HDS 검사(오류면 중단) → rhwp scaffold → rhwp verify(표 수·제목 포함)
      → export-structure 개요 대조 → layout-anomaly → 미리보기 PDF/PNG → report.json
검사는 판정만 하고 산출물을 고치지 않는다. 오류가 있으면 exit 1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hds  # noqa: E402
import rhwp_locate  # noqa: E402


def run_json(cmd: List[str], ok_codes=(0,)) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    result = {"cmd": [Path(cmd[0]).name] + cmd[1:], "exit": proc.returncode}
    try:
        result["envelope"] = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        result["stdout"] = proc.stdout[-2000:]
    if proc.returncode not in ok_codes:
        result["stderr"] = proc.stderr[-2000:]
    return result


def outline_headings(node_list) -> List[tuple]:
    out = []
    for node in node_list or []:
        out.append((node.get("level"), node.get("heading")))
        out.extend(outline_headings(node.get("children")))
    return out


def build(doc: dict, out_hwpx: Path, workdir: Path, rhwp: str, preview: bool, png: bool,
          font_paths: List[str], prelinted: Optional[dict] = None) -> dict:
    report = {"started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "rhwp": rhwp, "output": str(out_hwpx), "steps": {}, "findings": []}
    steps = report["steps"]
    workdir.mkdir(parents=True, exist_ok=True)

    lint = prelinted if prelinted is not None else hds.summarize(hds.validate_document(doc))
    steps["hds_lint"] = {k: lint[k] for k in ("ok", "errorCount", "warningCount")}
    report["findings"].extend(lint["findings"])
    if not lint["ok"]:
        report["ok"] = False
        report["stoppedAt"] = "hds_lint"
        return report

    spec_path = workdir / "document.hds.json"
    spec_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_hwpx.parent.mkdir(parents=True, exist_ok=True)
    steps["scaffold"] = run_json([rhwp, "scaffold", str(spec_path), "-o", str(out_hwpx), "--json"])
    if steps["scaffold"]["exit"] != 0:
        report["ok"] = False
        report["stoppedAt"] = "scaffold"
        return report

    blocks = doc.get("blocks", [])
    headings = [(b["level"], b["text"]) for b in blocks if b.get("type") == "heading"]
    tables = sum(1 for b in blocks if b.get("type") == "table")
    verify_cmd = [rhwp, "verify", str(out_hwpx), "--expect-table-count", str(tables), "--json"]
    for _, text in headings:
        verify_cmd += ["--expect-contains", text]
    steps["verify"] = run_json(verify_cmd, ok_codes=(0, 3))
    env = steps["verify"].get("envelope") or {}
    if env.get("verdict") != "pass":
        report["findings"].append(hds.Finding(
            "error", "R101", f"rhwp verify 실패 ({env.get('failCount')}건)").to_dict())

    steps["structure"] = run_json([rhwp, "export-structure", str(out_hwpx), "--mode", "outline", "--json"])
    struct = ((steps["structure"].get("envelope") or {}).get("structure") or {})
    got = outline_headings(struct.get("roots"))
    if got != headings:
        report["findings"].append(hds.Finding(
            "error", "R102", f"개요 불일치: 명세 {len(headings)}개 / 산출물 {len(got)}개",
        ).to_dict())
        steps["structure"]["mismatch"] = {"expected": headings, "actual": got}
    steps["structure"].pop("envelope", None)  # 본문 전체가 들어 있어 보고서에서 생략

    steps["layout_anomaly"] = run_json([rhwp, "layout-anomaly", str(out_hwpx), "--json"], ok_codes=(0, 3))
    la = steps["layout_anomaly"].get("envelope") or {}
    pages = la.get("pageCount")
    report["pageCount"] = pages
    if la.get("hasSignal"):
        report["findings"].append(hds.Finding(
            "error", "R103",
            "조판 이상 신호: " + ", ".join(f"{k}={la.get(k)}" for k in (
                "overflowCount", "overlapCount", "textOverlapCount", "offCanvasCount", "emptyPageCount")
                if la.get(k)),
        ).to_dict())
    steps["layout_anomaly"]["envelope"] = {k: v for k, v in la.items() if k != "pages"}

    font_args = []
    for fp in font_paths:
        font_args += ["--font-path", fp]
    if preview:
        pdf = out_hwpx.with_suffix(".preview.pdf")
        steps["preview_pdf"] = run_json([rhwp, "export-pdf", str(out_hwpx), "-o", str(pdf)] + font_args)
        steps["preview_pdf"]["file"] = str(pdf)
    if png:
        png_dir = workdir / "png"
        steps["png"] = run_json([rhwp, "export-png", str(out_hwpx), "-o", str(png_dir), "--json"] + font_args)

    errors = [f for f in report["findings"] if f["severity"] == "error"]
    report["ok"] = not errors
    report["errorCount"] = len(errors)
    report["warningCount"] = len(report["findings"]) - len(errors)
    report["notes"] = [
        "미리보기 PDF/PNG 는 rhwp 렌더다. 표 셀 줄바꿈(M0 F2)이 고쳐지기 전까지 쪽수·표 모양은 한컴과 다를 수 있다.",
    ]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="HDS → HWPX 빌드")
    ap.add_argument("project", nargs="?", type=Path)
    ap.add_argument("--quick", type=Path, help="단일 HDS 문서 경로 (Quick 라우트)")
    ap.add_argument("-o", "--output", type=Path, help="Quick 출력 HWPX")
    ap.add_argument("--rhwp")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--font-path", action="append", default=[])
    args = ap.parse_args()

    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    if args.quick:
        if not args.output:
            ap.error("--quick 에는 -o <out.hwpx> 가 필요합니다")
        doc = hds.load_json(args.quick)
        workdir = args.output.parent / (args.output.stem + ".build")
        report = build(doc, args.output, workdir, rhwp, not args.no_preview, args.png, args.font_path)
        report_path = workdir / "report.json"
    elif args.project:
        project = args.project
        lint = hds.summarize(hds.lint_project(project, "final"))
        if not lint["ok"]:
            print(json.dumps(lint, ensure_ascii=False, indent=1))
            return 1
        doc = hds.assemble(project)
        name = hds.load_json(project / "project.json")["name"]
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = project / "exports" / f"{name}_{stamp}.hwpx"
        report = build(doc, out, project / "build", rhwp, not args.no_preview, args.png, args.font_path,
                       prelinted=lint)
        report_path = project / "validation" / "report.json"
    else:
        ap.error("프로젝트 경로 또는 --quick 이 필요합니다")
        return 2

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for f in report["findings"]:
        mark = "오류" if f["severity"] == "error" else "경고"
        print(f"[{mark} {f['code']}] {f.get('where', '')} {f['message']}")
    print(json.dumps({"ok": report["ok"], "output": report["output"], "pageCount": report.get("pageCount"),
                      "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
