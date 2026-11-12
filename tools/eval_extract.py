#!/usr/bin/env python3
"""추출 품질 평가: 정답(gold) 대비 정밀도·재현율.

    python3 tools/eval_extract.py [--baseline tests/fixtures/extract/baseline.json] [--update-baseline] [--rhwp PATH]
    EXTRACT_CORPUS=/로컬/실문서 python3 tools/eval_extract.py [--corpus-only] [--update-baseline]

평가 범주와 맞춤 열쇠
  values   의미 값 (kind, 정규화 값)          — gold 의 kinds 에 든 종류만 센다(수량 number 는 빼기 쉬움)
  outline  개조식 항목·제목 (본문 앞 12자, 단계)
  records  데이터 표 레코드 (행 이름, 열 키, 값)
  kv       항목-값 (keyNorm, 값)
  roles    표 역할 (표 이름, layout|data)

정답 파일
  tests/fixtures/extract/*.json  — {"name", "build": {"spec": HDS 명세} | {"fixture": "nested"|"layout"}, "gold": {...}}
  tests/fixtures/extract/corpus/<문서>.golden.json — 실제 문서 정답 {"source": 파일 이름, "sha256", "genre", "gold"}.
      실제 문서는 저장소에 넣지 않는다(라이선스). EXTRACT_CORPUS 폴더(하위 폴더 포함)에서 source 이름으로 찾고,
      SHA-256 이 다르면 다른 판본이라 평가하지 않고 실패로 본다. 문서가 없으면 건너뛴다고 알린다.
  EXTRACT_CORPUS 폴더의 <문서>.golden.json ({"gold": {...}}) — 문서 옆에 둔 정답(예전 방식, 그대로 읽는다).

기준은 둘이다. 픽스처는 baseline.json, 실제 문서는 corpus_baseline.json(EXTRACT_CORPUS 가 있을 때만 비교).
범주별 precision·recall 이 기준보다 낮으면 exit 1 (회귀). --update-baseline 은 평가한 쪽의 기준을 지금 값으로 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
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
CORPUS_GOLD = FIXTURES / "corpus"
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
    _add_keys(keys, gold)
    return keys


def ignore_keys(gold: dict) -> Dict[str, Counter]:
    """정답의 ignore: 사람이 봐도 정답이 갈리는 항목. 예측에 있어도 맞음·틀림 어느 쪽에도 세지 않는다."""
    keys = {c: Counter() for c in CATEGORIES}
    _add_keys(keys, gold.get("ignore") or {})
    return keys


def _add_keys(keys: Dict[str, Counter], gold: dict) -> None:
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
    pred = predictions(out, gold.get("kinds", []))
    for c, keys in ignore_keys(gold).items():
        pred[c] -= keys
    result = score(pred, gold_keys(gold), categories)
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_jobs(corpus: Path, gold_dir: Path = CORPUS_GOLD) -> tuple:
    """([(이름, 문서, 정답)], [문제]) — 저장소 정답은 source 이름으로 corpus 에서 문서를 찾고 SHA-256 을 맞춘다."""
    jobs, problems, missing = [], [], []
    found: Dict[str, List[Path]] = {}
    for path in corpus.rglob("*"):
        if path.suffix.lower() in (".hwp", ".hwpx", ".hml") and path.is_file():
            found.setdefault(path.name, []).append(path)
    for golden in sorted(gold_dir.glob("*.golden.json")):
        meta = json.loads(golden.read_text(encoding="utf-8"))
        name = golden.name[: -len(".golden.json")]
        hits = [p for p in found.get(meta["source"], []) if sha256(p) == meta["sha256"]]
        if hits:
            jobs.append((f"corpus:{name}", hits[0], meta["gold"]))
        elif found.get(meta["source"]):
            problems.append(f"{name}: {meta['source']} 가 있지만 SHA-256 이 정답과 다름(다른 판본)")
        else:
            missing.append(meta["source"])
    for golden in sorted(corpus.glob("*.golden.json")):
        stem = golden.name[: -len(".golden.json")]
        doc = next((corpus / f"{stem}{ext}" for ext in (".hwp", ".hwpx", ".hml")
                    if (corpus / f"{stem}{ext}").exists()), None)
        if doc is not None:
            jobs.append((f"corpus:{stem}", doc, json.loads(golden.read_text(encoding="utf-8"))["gold"]))
    return jobs, problems, missing


def run(rhwp: str, fixtures: bool = True, corpus: Optional[Path] = None) -> dict:
    cases, corpus_cases, problems, missing = [], [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        for sub in ("sources", "exports", "validation"):
            (project / sub).mkdir(parents=True)
        if fixtures:
            for path in sorted(FIXTURES.glob("*.json")):
                if path.name.endswith("baseline.json"):
                    continue
                case = json.loads(path.read_text(encoding="utf-8"))
                src = build_fixture(case, rhwp, project / "sources")
                cases.append(evaluate_case(case["name"], src, project, case["gold"], rhwp))
        if corpus:
            jobs, problems, missing = corpus_jobs(corpus)
            for k, (name, doc, gold) in enumerate(jobs):
                copy = project / "sources" / f"corpus{k:02d}_{doc.name}"
                copy.write_bytes(doc.read_bytes())
                corpus_cases.append(evaluate_case(name, copy, project, gold, rhwp))
    return {"cases": cases, "totals": totals(cases), "corpusCases": corpus_cases,
            "corpusTotals": totals(corpus_cases), "corpusProblems": problems, "corpusMissing": missing}


def compare_baseline(result: dict, baseline: dict, key: str = "totals") -> List[str]:
    drops = []
    for c, base in baseline.get("totals", {}).items():
        now = result[key].get(c)
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
    ap.add_argument("--corpus-baseline", type=Path, default=FIXTURES / "corpus_baseline.json")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--corpus-only", action="store_true", help="픽스처 없이 EXTRACT_CORPUS 만 평가")
    ap.add_argument("--rhwp")
    args = ap.parse_args(argv)
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    corpus = Path(os.environ["EXTRACT_CORPUS"]) if os.environ.get("EXTRACT_CORPUS") else None
    result = run(rhwp, fixtures=not args.corpus_only, corpus=corpus)
    def show(label, tot):
        for c, m in sorted(tot.items()):
            print(f"{label} {c:8s} P {m['precision']:.3f}  R {m['recall']:.3f}  (tp {m['tp']}, fp {m['fp']}, fn {m['fn']})",
                  file=sys.stderr)

    show("픽스처", result["totals"])
    if corpus:
        show("실문서", result["corpusTotals"])
        if result["corpusMissing"]:
            print(f"[건너뜀] EXTRACT_CORPUS 에 없는 문서 {len(result['corpusMissing'])}건: "
                  + ", ".join(result["corpusMissing"]), file=sys.stderr)
    all_cases = result["cases"] + result["corpusCases"]
    errors = [e for case in all_cases for e in case["errors"]] + result["corpusProblems"]
    drops = []

    def write(path, tot):
        path.write_text(json.dumps({"totals": {c: {"precision": m["precision"], "recall": m["recall"]}
                                               for c, m in sorted(tot.items())}},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.corpus_only:
        if args.update_baseline:
            write(args.baseline, result["totals"])
        elif args.baseline.exists():
            drops += compare_baseline(result, json.loads(args.baseline.read_text(encoding="utf-8")))
    if corpus:
        if args.update_baseline:
            if result["corpusMissing"] or result["corpusProblems"]:
                print("[기준] 빠진 문서가 있어 실문서 기준을 쓰지 않습니다", file=sys.stderr)
            else:
                write(args.corpus_baseline, result["corpusTotals"])
        elif args.corpus_baseline.exists() and not result["corpusMissing"]:
            drops += [f"실문서 {d}" for d in compare_baseline(
                result, json.loads(args.corpus_baseline.read_text(encoding="utf-8")), "corpusTotals")]
    for d in drops:
        print(f"[회귀] {d}", file=sys.stderr)
    for e in result["corpusProblems"]:
        print(f"[오류] {e}", file=sys.stderr)
    print(json.dumps({"ok": not drops and not errors, "totals": result["totals"],
                      "corpusTotals": result["corpusTotals"], "drops": drops,
                      "corpusMissing": result["corpusMissing"], "corpusProblems": result["corpusProblems"],
                      "cases": [{"name": c["name"], **{k: {m: v[m] for m in ("precision", "recall", "misses",
                                                                                 "falsePositives")}
                                                      for k, v in c["categories"].items()}} for c in all_cases]},
                     ensure_ascii=False))
    return 0 if not drops and not errors else 1


if __name__ == "__main__":
    sys.path.insert(0, str(SCRIPTS))
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
