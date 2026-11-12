#!/usr/bin/env python3
"""여러 문서 모아 보기: 문서별 추출 결과(exports/extract/<문서>/)를 exports/extract/_merged/ 로 합친다.

  documents.csv  문서마다 한 줄 — 출처·SHA-256·형식·쪽·항목별 개수·서식 묶음(formGroup)
  fields.csv     누름틀(이름·값·blockId) + source 열
  kv.csv         항목-값 + source 열
  data.csv       날짜·금액·기간… + source 열
  forms.json     서식 비교 — 누름틀 이름(없으면 항목-값 keyNorm) 집합이 같은 문서끼리 묶고, 다수 묶음과 다른 문서·빠진 이름

"신청서 30개에서 이름·연락처를 한 표로" 같은 요청은 fields.csv·kv.csv 를 열면 된다(행마다 source).
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

MERGED = "_merged"


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: List[Dict[str, str]], first: List[str]) -> None:
    keys = list(first)
    for row in rows:
        keys += [k for k in row if k not in keys]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def form_signature(doc_dir: Path) -> List[str]:
    """서식 비교 열쇠: 누름틀 이름 집합, 누름틀이 없으면 항목-값 keyNorm 집합."""
    names = sorted({r.get("name", "") for r in _read_csv(doc_dir / "fields.csv") if r.get("name")})
    if names:
        return names
    return sorted({r.get("keyNorm", "") for r in _read_csv(doc_dir / "kv.csv") if r.get("keyNorm")})


def merge(project: Path, summaries: List[dict]) -> dict:
    """summaries(extract_one 결과) → _merged/ 파일들과 서식 비교 결과."""
    out = project / "exports" / "extract" / MERGED
    out.mkdir(parents=True, exist_ok=True)
    docs, fields, kvs, data = [], [], [], []
    signatures: Dict[str, List[str]] = {}
    for s in summaries:
        doc_dir = project / s["dir"]
        source = s["source"]
        signatures[source] = form_signature(doc_dir)
        docs.append({"source": source, "dir": s["dir"], "sha256": s.get("sha256", ""),
                     "format": (s.get("info") or {}).get("format", ""),
                     "pages": (s.get("info") or {}).get("pageCount", ""),
                     **{k: v for k, v in s.get("counts", {}).items()}})
        fields += [{"source": source, **r} for r in _read_csv(doc_dir / "fields.csv")]
        kvs += [{"source": source, **r} for r in _read_csv(doc_dir / "kv.csv")]
        data += [{"source": source, **r} for r in _read_csv(doc_dir / "data.csv")]

    groups: Dict[str, List[str]] = {}
    for source, sig in signatures.items():
        groups.setdefault(json.dumps(sig, ensure_ascii=False), []).append(source)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    majority = json.loads(ranked[0][0]) if ranked else []
    group_of = {src: n for n, (_, members) in enumerate(ranked, 1) for src in members}
    differing = []
    # 같은 서식을 쓰는 문서가 둘 이상일 때만 "다른 서식"을 말한다(서로 다른 문서 두 건은 비교 기준이 없다).
    if majority and len(summaries) > 1 and len(ranked[0][1]) >= 2:
        for source, sig in signatures.items():
            if sig != majority:
                differing.append({"source": source, "missing": sorted(set(majority) - set(sig)),
                                  "extra": sorted(set(sig) - set(majority))})
    for row in docs:
        row["formGroup"] = group_of.get(row["source"], "")

    _write_csv(out / "documents.csv", docs, ["source", "dir", "formGroup", "format", "pages", "sha256"])
    _write_csv(out / "fields.csv", fields, ["source", "name", "value"])
    _write_csv(out / "kv.csv", kvs, ["source", "key", "value"])
    _write_csv(out / "data.csv", data, ["source", "kind", "raw", "normalized"])
    forms = {"documents": len(summaries), "majority": majority,
             "groups": [{"group": n, "signature": json.loads(sig), "documents": members}
                        for n, (sig, members) in enumerate(ranked, 1)],
             "differing": differing}
    (out / "forms.json").write_text(json.dumps(forms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"dir": out.relative_to(project).as_posix(), "documents": len(summaries), "fields": len(fields),
            "kv": len(kvs), "data": len(data), "differing": differing,
            "files": ["documents.csv", "fields.csv", "kv.csv", "data.csv", "forms.json"],
            "nameCounts": Counter(r.get("name", "") for r in fields)}
