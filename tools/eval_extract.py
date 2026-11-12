#!/usr/bin/env python3
"""추출 품질 평가: 정답(gold) 대비 정밀도·재현율.

    python3 tools/eval_extract.py [--baseline tests/fixtures/extract/baseline.json] [--update-baseline] [--rhwp PATH]
    EXTRACT_CORPUS=/로컬/실문서 python3 tools/eval_extract.py --corpus-only

평가 범주와 맞춤 열쇠
  values   의미 값 (kind, 정규화 값)          — gold 의 kinds 에 든 종류만 센다(수량 number 는 빼기 쉬움)
  outline  개조식 항목·제목 (본문 앞 12자, 단계)
  records  데이터 표 레코드 (행 이름, 열 키, 값)
  kv       항목-값 (keyNorm, 값)
  roles    표 역할 (표 이름, layout|data)

정답 파일
  tests/fixtures/extract/*.json  — {"name", "build": {"spec": HDS 명세} | {"fixture": "nested"|"layout"}, "gold": {...}}
  EXTRACT_CORPUS 폴더 — <문서>.hwp|.hwpx 옆의 <문서>.golden.json ({"gold": {...}}). 실제 문서는 저장소에 넣지 않는다.

기준(baseline.json)의 범주별 precision·recall 보다 낮으면 exit 1 (회귀). --update-baseline 은 지금 값을 기준으로 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "hwp-master" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "extract"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import extract  # noqa: E402
import rhwp_locate  # noqa: E402

CATEGORIES = ("values", "outline", "records", "kv", "roles")


def _norm(text: str) -> str:
    return re.sub(r"[\s 　]+", "", str(text or ""))


def _value_key(kind, normalized) -> str:
    return f"{kind}|{json.dumps(normalized, ensure_ascii=False, sort_keys=True)}"


def predictions(out: Path, kinds: List[str]) -> Dict[str, Counter]:
    """추출 결과 폴더 → 범주별 예측 열쇠 Counter."""
    load = lambda name, key: json.loads((out / name).read_text(encoding="utf-8")).get(key, []) \
        if (out / name).exists() else []
    pred = {c: Counter() for c in CATEGORIES}
    for item in load("data.json", "items"):
        if item["kind"] in kinds:
            pred["values"][_value_key(item["kind"], item.get("normalized"))] += 1
    for block in load("document.json", "blocks"):
        if block["type"] == "heading":
            pred["outline"][f"{_norm(block['text'])[:12]}|{block['level']}|heading"] += 1
        elif block.get("outline"):
            o = block["outline"]
            pred["outline"][f"{_norm(o['body'])[:12]}|{o['level']}|{'note' if o['kind'] == 'note' else 'item'}"] += 1
    for table in load("tables.json", "tables"):
        if table.get("role"):
            pred["roles"][f"{table['label']}|{table['role']}"] += 1
        for record in (table.get("semantics") or {}).get("records", []):
            if (table.get("semantics") or {}).get("kind") != "numeric":
                continue
            for key, value in record["values"].items():
                pred["records"][f"{_norm(record['label'])}|{_norm(key)}|{value}"] += 1
    for item in load("kv.json", "items"):
        if item.get("duplicateOf") is None:
            pred["kv"][f"{item['keyNorm']}|{_norm(item['value'])}"] += 1
    return pred


def gold_keys(gold: dict) -> Dict[str, Counter]:
    keys = {c: Counter() for c in CATEGORIES}
    for v in gold.get("values", []):
        keys["values"][_value_key(v["kind"], v["normalized"])] += 1
    for o in gold.get("outline", []):
        keys["outline"][f"{_norm(o['text'])[:12]}|{o['level']}|{o.get('type', 'item')}"] += 1
    for r in gold.get("records", []):
        keys["records"][f"{_norm(r['label'])}|{_norm(r['key'])}|{r['value']}"] += 1
    for k in gold.get("kv", []):
        keys["kv"][f"{_norm(k['key'])}|{_norm(k['value'])}"] += 1
    for r in gold.get("roles", []):
        keys["roles"][f"{r['label']}|{r['role']}"] += 1
    return keys


def score(pred: Dict[str, Counter], gold: Dict[str, Counter], categories) -> Dict[str, dict]:
    out = {}
    for c in categories:
        tp = sum((pred[c] & gold[c]).values())
        fp = sum((pred[c] - gold[c]).values())
        fn = sum((gold[c] - pred[c]).values())
        out[c] = {"tp": tp, "fp": fp, "fn": fn,
                  "precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
                  "recall": round(tp / (tp + fn), 4) if tp + fn else 1.0,
                  "falsePositives": sorted((pred[c] - gold[c]).elements())[:20],
                  "misses": sorted((gold[c] - pred[c]).elements())[:20]}
    return out


def build_fixture(case: dict, rhwp: str, sources: Path) -> Path:
    import nested_fixture
    build = case["build"]
    target = sources / f"{case['name']}.hwpx"
    if "spec" in build:
        spec = sources.parent / f"{case['name']}.spec.json"
        spec.write_text(json.dumps(build["spec"], ensure_ascii=False), encoding="utf-8")
        subprocess.run([rhwp, "scaffold", str(spec), "-o", str(target)], check=True, capture_output=True)
    elif build.get("fixture") == "nested":
        nested_fixture.make_nested(rhwp, target)
    elif build.get("fixture") == "layout":
        nested_fixture.make_layout(rhwp, target)
    else:
        raise ValueError(f"알 수 없는 build: {build}")
    return target


def evaluate_case(name: str, src: Path, project: Path, gold: dict, rhwp: str) -> dict:
    findings: List[dict] = []
    summary = extract.extract_one(rhwp, project, src.resolve(), extract.PARTS, 2000, findings)
    out = project / summary["dir"]
    categories = [c for c in CATEGORIES if c in gold]
    result = score(predictions(out, gold.get("kinds", [])), gold_keys(gold), categories)
    return {"name": name, "categories": result,
            "errors": [f for f in findings if f["severity"] == "error"]}


def totals(cases: List[dict]) -> Dict[str, dict]:
    agg: Dict[str, Dict[str, int]] = {}
    for case in cases:
        for c, m in case["categories"].items():
            a = agg.setdefault(c, {"tp": 0, "fp": 0, "fn": 0})
            for k in ("tp", "fp", "fn"):
                a[k] += m[k]
    for c, a in agg.items():
        a["precision"] = round(a["tp"] / (a["tp"] + a["fp"]), 4) if a["tp"] + a["fp"] else 1.0
        a["recall"] = round(a["tp"] / (a["tp"] + a["fn"]), 4) if a["tp"] + a["fn"] else 1.0
    return agg


def run(rhwp: str, fixtures: bool = True, corpus: Optional[Path] = None) -> dict:
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        for sub in ("sources", "exports", "validation"):
            (project / sub).mkdir(parents=True)
        if fixtures:
            for path in sorted(FIXTURES.glob("*.json")):
                if path.name == "baseline.json":
                    continue
                case = json.loads(path.read_text(encoding="utf-8"))
                src = build_fixture(case, rhwp, project / "sources")
                cases.append(evaluate_case(case["name"], src, project, case["gold"], rhwp))
        if corpus:
            for golden in sorted(corpus.glob("*.golden.json")):
                stem = golden.name[: -len(".golden.json")]
                doc = next((corpus / f"{stem}{ext}" for ext in (".hwp", ".hwpx", ".hml")
                            if (corpus / f"{stem}{ext}").exists()), None)
                if doc is None:
                    continue
                copy = project / "sources" / f"corpus_{doc.name}"
                copy.write_bytes(doc.read_bytes())
                gold = json.loads(golden.read_text(encoding="utf-8"))["gold"]
                cases.append(evaluate_case(f"corpus:{stem}", copy, project, gold, rhwp))
    return {"cases": cases, "totals": totals(cases)}


def compare_baseline(result: dict, baseline: dict) -> List[str]:
    drops = []
    for c, base in baseline.get("totals", {}).items():
        now = result["totals"].get(c)
        if not now:
            drops.append(f"{c}: 평가되지 않음")
            continue
        for metric in ("precision", "recall"):
            if now[metric] + 1e-9 < base[metric]:
                drops.append(f"{c} {metric} {now[metric]} < 기준 {base[metric]}")
    return drops


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, default=FIXTURES / "baseline.json")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--corpus-only", action="store_true", help="픽스처 없이 EXTRACT_CORPUS 만 평가")
    ap.add_argument("--rhwp")
    args = ap.parse_args(argv)
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    corpus = Path(os.environ["EXTRACT_CORPUS"]) if os.environ.get("EXTRACT_CORPUS") else None
    result = run(rhwp, fixtures=not args.corpus_only, corpus=corpus)
    for c, m in sorted(result["totals"].items()):
        print(f"{c:8s} P {m['precision']:.3f}  R {m['recall']:.3f}  (tp {m['tp']}, fp {m['fp']}, fn {m['fn']})",
              file=sys.stderr)
    errors = [e for case in result["cases"] for e in case["errors"]]
    drops = []
    if args.update_baseline and not args.corpus_only:
        args.baseline.write_text(json.dumps({"totals": {c: {"precision": m["precision"], "recall": m["recall"]}
                                                        for c, m in result["totals"].items()}},
                                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.baseline.exists() and not args.corpus_only:
        drops = compare_baseline(result, json.loads(args.baseline.read_text(encoding="utf-8")))
    for d in drops:
        print(f"[회귀] {d}", file=sys.stderr)
    print(json.dumps({"ok": not drops and not errors, "totals": result["totals"], "drops": drops,
                      "cases": [{"name": c["name"], **{k: {m: v[m] for m in ("precision", "recall", "misses",
                                                                                 "falsePositives")}
                                                      for k, v in c["categories"].items()}} for c in result["cases"]]},
                     ensure_ascii=False))
    return 0 if not drops and not errors else 1


if __name__ == "__main__":
    sys.path.insert(0, str(SCRIPTS))
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
