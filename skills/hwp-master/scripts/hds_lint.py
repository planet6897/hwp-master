#!/usr/bin/env python3
"""HDS 명세·규약 검사. 검사만 하고 고치지 않는다.

    python3 hds_lint.py <document.hds.json>              # 단일 HDS 문서 (Quick)
    python3 hds_lint.py <project_dir> --stage early|final # 프로젝트 단위들

오류가 1건이라도 있으면 exit 1, 경고만 있으면 exit 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hds  # noqa: E402


def run(target: Path, stage: str) -> dict:
    if target.is_dir():
        findings = hds.lint_project(target, stage)
    else:
        try:
            doc = hds.load_json(target)
        except (OSError, json.JSONDecodeError) as exc:
            findings = [hds.Finding("error", "E000", f"읽기 실패: {exc}", str(target))]
        else:
            findings = hds.validate_document(doc)
    result = hds.summarize(findings)
    result.update({"target": str(target), "stage": stage})
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="HDS 명세·규약 검사")
    ap.add_argument("target", type=Path)
    ap.add_argument("--stage", choices=("early", "final"), default="final")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.target, args.stage)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        for f in result["findings"]:
            mark = "오류" if f["severity"] == "error" else "경고"
            print(f"[{mark} {f['code']}] {f['where']} {f['message']}".rstrip())
        print(f"오류 {result['errorCount']}건, 경고 {result['warningCount']}건")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
