#!/usr/bin/env python3
"""Mail-merge 라우트: 서식 1개 + 명단 N행 → 문서 N개. 실제 채움은 rhwp `batch fill`이 한다.

    python3 merge.py <프로젝트> --form sources/<서식.hwp|hwpx> --data sources/<명단.csv|jsonl>
                     [--name-field <열 이름>] [--rhwp PATH]

단계:
  1. 사전 검사 — 서식 누름틀(`rhwp fields`)과 명단 열 대조.
     명단에만 있는 열(M001)·없는 --name-field(M002)는 오류, 명단이 채우지 않는 누름틀(M101)·
     겹치는 이름 값(M102, rhwp 가 _2 를 붙임)은 경고.
  2. `batch fill --dry-run` — 모든 행이 채워져야 한다(실패 행·찾지 못한 누름틀은 M003 오류).
  3. `batch fill --verify` → exports/merge_<시각>/ — 입력 행 수 = 산출 레코드 수, 실패 0, 행마다 자기검증 일치(M004).
  4. validation/merge_<시각>.json 요약(개수·행 번호·산출 파일 이름만) + 원본 NDJSON.

명단은 개인정보일 수 있다. 요약에는 값을 넣지 않는다. NDJSON 과 산출 문서에는 값이 있으므로
대화나 외부로 붙여 넣지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhwp_locate  # noqa: E402


def finding(severity: str, code: str, message: str) -> dict:
    return {"severity": severity, "code": code, "message": message}


def read_columns(path: Path) -> Tuple[List[str], List[dict]]:
    """(열 이름 목록, 행 목록). CSV 는 첫 줄이 헤더, JSONL 은 객체 키의 합집합."""
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            rows = [dict(zip(header, r)) for r in reader if any(c.strip() for c in r)]
        return [h.strip() for h in header], rows
    if path.suffix.lower() == ".jsonl":
        cols: List[str] = []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            rows.append(obj)
            for k in obj:
                if k not in cols:
                    cols.append(k)
        return cols, rows
    raise ValueError(f"명단은 .csv 또는 .jsonl 이어야 합니다: {path.name}")


def batch_fill(rhwp: str, project: Path, form: str, data: str, out_dir: str, name_field, dry: bool):
    cmd = [rhwp, "batch", "fill", "--form", form, "--data", data, "--out-dir", out_dir, "--json"]
    if name_field:
        cmd += ["--name-field", name_field]
    cmd += ["--dry-run"] if dry else ["--verify"]
    proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=3600)
    records = []
    for line in proc.stdout.splitlines():
        if line.strip():
            records.append(json.loads(line))
    return proc, records


def main() -> int:
    ap = argparse.ArgumentParser(description="hwp-master Mail-merge (rhwp batch fill)")
    ap.add_argument("project", type=Path)
    ap.add_argument("--form", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--name-field")
    ap.add_argument("--rhwp")
    args = ap.parse_args()

    project = args.project.resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    findings: List[dict] = []
    summary = {"form": args.form, "data": args.data, "nameField": args.name_field, "findings": findings}
    out_json = project / "validation" / f"merge_{stamp}.json"

    def finish() -> int:
        errors = [f for f in findings if f["severity"] == "error"]
        summary["ok"] = not errors
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for f in findings:
            print(f"[{'오류' if f['severity'] == 'error' else '경고'} {f['code']}] {f['message']}")
        print(json.dumps({"ok": summary["ok"], "rows": summary.get("rows"), "outDir": summary.get("outDir"),
                          "report": str(out_json)}, ensure_ascii=False))
        return 0 if summary["ok"] else 1

    form, data = (project / args.form), (project / args.data)
    for label, path in (("서식", form), ("명단", data)):
        try:
            path.resolve().relative_to(project)
        except ValueError:
            findings.append(finding("error", "M000", f"{label}은 프로젝트 안의 파일이어야 합니다: {path}"))
            return finish()
        if not path.is_file():
            findings.append(finding("error", "M000", f"{label} 파일 없음: {path.relative_to(project)}"))
            return finish()

    proc = subprocess.run([rhwp, "fields", args.form, "--json"], cwd=project, capture_output=True, text=True)
    if proc.returncode != 0:
        findings.append(finding("error", "M000", f"서식 누름틀 조사 실패: {proc.stderr.strip()[-300:]}"))
        return finish()
    form_fields = []
    for fld in json.loads(proc.stdout).get("fields", []):
        if fld.get("name") and fld["name"] not in form_fields:
            form_fields.append(fld["name"])
    try:
        columns, rows = read_columns(data)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        findings.append(finding("error", "M000", f"명단을 읽지 못했습니다: {exc}"))
        return finish()
    summary.update({"formFields": form_fields, "columns": columns, "rows": len(rows)})

    def base(name: str) -> str:  # "목차[1]" 같은 순번 지목은 이름만 대조한다
        return name.split("[", 1)[0]

    extra = [c for c in columns if base(c) not in form_fields]
    if extra:
        findings.append(finding("error", "M001", f"서식에 없는 명단 열: {extra} (서식 누름틀: {form_fields})"))
    if args.name_field and args.name_field not in columns:
        findings.append(finding("error", "M002", f"--name-field '{args.name_field}' 가 명단 열에 없습니다"))
    unfilled = [f for f in form_fields if f not in {base(c) for c in columns}]
    if unfilled:
        findings.append(finding("warning", "M101", f"명단이 채우지 않는 누름틀(빈칸으로 남음): {unfilled}"))
    if args.name_field and args.name_field in columns:
        names = [str(r.get(args.name_field, "")) for r in rows]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            findings.append(finding("warning", "M102", f"이름 열 값이 겹치는 행 {len(dup)}종 — 파일 이름에 _2 등이 붙습니다"))
    if not rows:
        findings.append(finding("error", "M000", "명단에 행이 없습니다"))
    if any(f["severity"] == "error" for f in findings):
        return finish()

    out_rel = f"exports/merge_{stamp}"
    proc, records = batch_fill(rhwp, project, args.form, args.data, out_rel, args.name_field, dry=True)
    bad = [r.get("row") for r in records if r.get("error") or r.get("notFound")]
    if bad or len(records) != len(rows):
        findings.append(finding("error", "M003",
                                f"dry-run: {len(rows)}행 중 실패·미채움 행 {bad} (레코드 {len(records)}개) — 저장하지 않았습니다"))
        summary["dryRunErrors"] = [{"row": r.get("row"), "error": r.get("error"), "notFound": r.get("notFound")}
                                   for r in records if r.get("error") or r.get("notFound")]
        return finish()

    (project / out_rel).mkdir(parents=True, exist_ok=True)
    proc, records = batch_fill(rhwp, project, args.form, args.data, out_rel, args.name_field, dry=False)
    raw = project / "validation" / f"merge_{stamp}.ndjson"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(proc.stdout, encoding="utf-8")
    failed = [r.get("row") for r in records if r.get("error")]
    unverified = [r.get("row") for r in records if not r.get("error") and not (r.get("verify") or {}).get("identical")]
    outputs = [Path(r["output"]).name for r in records if r.get("output") and not r.get("error")]
    summary.update({"outDir": out_rel, "records": len(records), "succeeded": len(outputs),
                    "failedRows": failed, "unverifiedRows": unverified, "outputs": outputs, "ndjson": raw.name})
    if len(records) != len(rows) or failed or unverified or proc.returncode != 0:
        findings.append(finding("error", "M004",
                                f"입력 {len(rows)}행 / 레코드 {len(records)} / 실패 {failed} / 자기검증 불일치 {unverified} "
                                f"(exit {proc.returncode})"))
    return finish()


if __name__ == "__main__":
    sys.exit(main())
