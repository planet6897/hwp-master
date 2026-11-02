#!/usr/bin/env python3
"""Template Fill 라우트: 기관 서식(HWP/HWPX)의 자리표시 칸을 자료로 채운다. 서식 원본은 고치지 않는다.

    python3 template_fill.py inspect <프로젝트> <sources/서식>                 # 칸 조사 → analysis/
    python3 template_fill.py fill <프로젝트> <sources/서식> --data <sources/자료.json> [--rhwp PATH]
    python3 template_fill.py fill <프로젝트> --house <이름> --data <sources/자료.json>  # 등록 서식 사용
    python3 template_fill.py register <서식> --name <이름> [--templates-dir DIR]  # 기관 서식 등록

자리표시 문법 (서식을 한컴에서 만들 때 본문·표 셀에 그대로 입력한다):
  {{키}}         값 하나. 같은 키가 여러 곳에 있으면 모두 같은 값으로 바뀐다.
  {{목록.필드}}  반복 칸. 같은 `목록` 의 칸이 모두 표 셀에 있으면 그 행(들)이, 모두 본문 문단에
                 있으면 그 문단 묶음이 원형이다. 원형을 자료 수만큼 반복하고 각 칸을 채운다.
  누름틀         서식의 누름틀 이름과 같은 키도 채운다(`fill_fields`).
자료 JSON: {"키": "값", "목록": [{"필드": "값", ...}, ...], "누름틀이름": "값"}.

fill 단계 (rhwp `run` 계획서만 쓴다 — 조판 로직을 새로 만들지 않는다):
  1. 칸 조사(`search "{{"`·`export-tables`·`fields`)와 자료 대조 — 빠진 키·필드는 오류, 남는 키는 경고.
  2. 목록마다 `repeat_and_fill_table_rows` / `repeat_and_fill_paragraph_block` 로 원형 뒤에
     2번째 자료부터 복제·채움(원형은 채우지 않은 채 남긴다). 단계마다 칸을 다시 조사해 주소를 정한다.
  3. 남은 칸 = 값 칸 + 원형(1번째 자료)을 `replace_text`(서식 그대로 글자만 교체)와 `fill_fields` 로
     한 계획에서 채운다. `assertions.verify` 로 저장 자기검증.
  4. 단계마다 `--dry-run` 선검증 → 실행 → `replay --capsule` 작업 캡슐(앞 단계 캡슐을 --parent 로 잇는다).
  5. 판정: 남은 `{{` 0개(T201), 값마다 본문 존재(T202), 서식보다 늘어난 조판 이상 없음(T203).
     validation/template_<시각>.json. 오류가 있으면 exit 1.

자리표시는 본문과 본문 표(한 단계) 셀만 찾는다. 머리말·꼬리말·글상자 안의 칸은 누름틀로 만든다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhwp_locate  # noqa: E402
from edit import finding, inside, run_json, sha256  # noqa: E402

EDITABLE = {".hwp", ".hwpx"}
KEY = r"[^\s{}.]+"
MARKER = re.compile(r"\{\{(" + KEY + r")(?:\.(" + KEY + r"))?\}\}")
ANOMALY_KEYS = ("overflowCount", "overlapCount", "textOverlapCount", "offCanvasCount", "emptyPageCount")
SKILL_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 칸 조사

def scan(rhwp: str, cwd: Path, doc: str) -> dict:
    """문서의 자리표시·목록·누름틀. 반환: {"markers", "scalars", "lists", "fields", "findings"}."""
    found: List[dict] = []
    res = run_json([rhwp, "search", doc, "{{", "--json"], cwd)
    if res["exit"] != 0 or not res.get("envelope"):
        return {"findings": [finding("error", "T000", f"자리표시 검색 실패: {res.get('stderr', '')[-300:]}")]}
    env = res["envelope"]
    if env.get("truncated"):
        found.append(finding("error", "T000", "자리표시 검색 결과가 잘렸습니다"))
    markers: List[dict] = []
    for m in env.get("matches", []):
        text, off = m.get("text") or "", m.get("charOffset", -1)
        where = f"구역 {m.get('section')} 문단 {m.get('paragraph')}" + (
            f" 표 셀 {m['cell'].get('cell')}" if m.get("cell") else "")
        if text[off:off + 2] != "{{":
            found.append(finding("error", "T001", f"자리표시 위치를 문단 글자와 맞추지 못했습니다", where))
            continue
        mt = MARKER.match(text, off)
        if not mt:
            found.append(finding("error", "T001", f"자리표시 문법 오류: {text[off:off + 30]!r}", where))
            continue
        cell = m.get("cell")
        if cell is not None and not all(isinstance(cell.get(k), int) for k in ("control", "cell", "paragraph")):
            found.append(finding("error", "T001", "지원하지 않는 위치의 자리표시(중첩 표 따위)", where))
            continue
        markers.append({"literal": mt.group(0), "key": mt.group(1), "field": mt.group(2),
                        "section": m["section"], "paragraph": m["paragraph"], "cell": cell,
                        "start": off, "end": mt.end(), "where": where})

    tables: Dict[Tuple[int, int, int], dict] = {}
    if any(mk["cell"] for mk in markers):
        tres = run_json([rhwp, "export-tables", doc, "--json"], cwd)
        for t in (tres.get("envelope") or {}).get("tables", []):
            tables[(t["section"], t["paragraph"], t["control"])] = t

    scalars: Dict[str, int] = {}
    groups: Dict[str, List[dict]] = {}
    for mk in markers:
        if mk["field"] is None:
            scalars[mk["key"]] = scalars.get(mk["key"], 0) + 1
        else:
            groups.setdefault(mk["key"], []).append(mk)
    for name in scalars:
        if name in groups:
            found.append(finding("error", "T002", f"'{name}' 이 값 칸과 목록 이름으로 함께 쓰였습니다"))

    lists = []
    for name, mks in groups.items():
        lst = classify_list(name, mks, tables, found)
        if lst:
            lists.append(lst)
    for a in lists:
        for b in lists:
            if a is not b and a["kind"] == b["kind"] == "paragraphs" and a["section"] == b["section"] \
                    and a["start"] < b["end"] and b["start"] < a["end"]:
                found.append(finding("error", "T002", f"목록 '{a['name']}' 과 '{b['name']}' 의 원형 문단이 겹칩니다"))
    fields: List[str] = []
    fres = run_json([rhwp, "fields", doc, "--json"], cwd)
    for fld in (fres.get("envelope") or {}).get("fields", []):
        if fld.get("name") and fld["name"] not in fields:
            fields.append(fld["name"])
    return {"markers": markers, "scalars": scalars, "lists": lists, "fields": fields, "findings": found}


def classify_list(name: str, mks: List[dict], tables: dict, found: List[dict]) -> Optional[dict]:
    fields = sorted({m["field"] for m in mks})
    dup = sorted({m["field"] for m in mks if sum(1 for o in mks if o["field"] == m["field"]) > 1})
    if dup:
        found.append(finding("error", "T002", f"목록 '{name}' 원형에 같은 필드가 두 번 있습니다: {dup}"))
        return None
    in_cells = [m for m in mks if m["cell"]]
    if in_cells and len(in_cells) != len(mks):
        found.append(finding("error", "T002", f"목록 '{name}' 의 칸이 표 셀과 본문 문단에 섞여 있습니다"))
        return None
    sections = {m["section"] for m in mks}
    if len(sections) != 1:
        found.append(finding("error", "T002", f"목록 '{name}' 의 칸이 여러 구역에 있습니다"))
        return None
    section = sections.pop()
    if in_cells:
        owners = {(m["section"], m["paragraph"], m["cell"]["control"]) for m in mks}
        if len(owners) != 1:
            found.append(finding("error", "T002", f"목록 '{name}' 의 칸이 여러 표에 있습니다"))
            return None
        owner = owners.pop()
        table = tables.get(owner)
        if not table:
            found.append(finding("error", "T002", f"목록 '{name}' 의 표를 찾지 못했습니다(본문 최상위 표만 지원)"))
            return None
        cells = table["cells"]
        rows = [cells[m["cell"]["cell"]]["row"] for m in mks]
        start, end = min(rows), max(rows) + 1
        selected = sorted((i for i, c in enumerate(cells) if start <= c["row"] < end),
                          key=lambda i: (cells[i]["row"], cells[i]["col"]))
        for i in selected:
            c = cells[i]
            if c["row"] + c.get("rowSpan", 1) > end:
                found.append(finding("error", "T002", f"목록 '{name}' 원형 행 {start}~{end - 1} 을 넘는 병합 셀이 있습니다"))
                return None
        bindings = []
        for m in mks:
            bindings.append({"key": m["field"], "target": {"kind": "textRange", "path": [
                {"kind": "paragraph", "index": 0}, {"kind": "control", "index": 0},
                {"kind": "cell", "index": selected.index(m["cell"]["cell"])},
                {"kind": "paragraph", "index": m["cell"]["paragraph"]}], "start": m["start"], "end": m["end"]}})
        return {"name": name, "kind": "table_rows", "fields": fields, "section": section,
                "paragraph": owner[1], "control": owner[2], "start": start, "end": end, "bindings": bindings}
    paras = [m["paragraph"] for m in mks]
    start, end = min(paras), max(paras) + 1
    bindings = [{"key": m["field"], "target": {"kind": "textRange",
                                               "path": [{"kind": "paragraph", "index": m["paragraph"] - start}],
                                               "start": m["start"], "end": m["end"]}} for m in mks]
    return {"name": name, "kind": "paragraphs", "fields": fields, "section": section,
            "start": start, "end": end, "bindings": bindings}


def inventory(sc: dict) -> dict:
    """사람·에이전트용 요약 (주소 없음)."""
    return {"scalars": sorted(sc.get("scalars", {})),
            "lists": [{"name": l["name"], "kind": l["kind"], "fields": l["fields"],
                       "prototype": (f"표(문단 {l['paragraph']}) 행 {l['start']}~{l['end'] - 1}"
                                     if l["kind"] == "table_rows" else f"문단 {l['start']}~{l['end'] - 1}")}
                      for l in sc.get("lists", [])],
            "fields": sc.get("fields", [])}


def example_data(inv: dict) -> dict:
    data = {k: f"<{k}>" for k in inv["scalars"]}
    for l in inv["lists"]:
        data[l["name"]] = [{f: f"<{l['name']}.{f}>" for f in l["fields"]}]
    for f in inv["fields"]:
        data.setdefault(f, f"<누름틀 {f}>")
    return data


# ---------------------------------------------------------------- 자료 대조

def value_text(v) -> Optional[str]:
    if isinstance(v, bool) or v is None or isinstance(v, (list, dict)):
        return None
    return str(v)


def check_data(sc: dict, data: dict, findings: List[dict]) -> dict:
    """자료를 칸에 맞춰 문자열로 정규화한다. 반환 {"scalars", "lists", "fields"}."""
    norm = {"scalars": {}, "lists": {}, "fields": {}}
    if not isinstance(data, dict):
        findings.append(finding("error", "T100", "자료는 JSON 객체여야 합니다"))
        return norm

    def text_ok(label: str, v) -> Optional[str]:
        s = value_text(v)
        if s is None:
            findings.append(finding("error", "T103", f"{label}: 문자열·숫자만 넣을 수 있습니다 ({type(v).__name__})"))
        elif "\n" in s or "\r" in s:
            findings.append(finding("error", "T103", f"{label}: 줄바꿈은 넣을 수 없습니다 — 칸을 나누거나 목록으로 만드세요"))
            return None
        elif "{{" in s or "}}" in s:
            findings.append(finding("error", "T104", f"{label}: 값에 자리표시 기호 {{{{ }}}} 를 넣을 수 없습니다"))
            return None
        return s

    fields = set(sc["fields"])
    for k in sc["scalars"]:
        if k in fields:
            findings.append(finding("error", "T002", f"'{k}' 이 자리표시와 누름틀 이름으로 함께 쓰였습니다"))
        if k not in data:
            findings.append(finding("error", "T101", f"자료에 값 칸 '{k}' 이 없습니다"))
        else:
            s = text_ok(k, data[k])
            if s is not None:
                norm["scalars"][k] = s
    for l in sc["lists"]:
        rows = data.get(l["name"])
        if not isinstance(rows, list):
            findings.append(finding("error", "T101", f"자료에 목록 '{l['name']}' (객체 배열)이 없습니다"))
            continue
        if not rows:
            findings.append(finding("error", "T105", f"목록 '{l['name']}' 이 비었습니다 — 원형을 지우지 않으므로 "
                                                     "'해당 없음' 같은 자료 1건을 넣으세요"))
            continue
        out = []
        for i, rec in enumerate(rows):
            if not isinstance(rec, dict):
                findings.append(finding("error", "T100", f"목록 '{l['name']}'[{i}] 은 객체여야 합니다"))
                continue
            missing = [f for f in l["fields"] if f not in rec]
            if missing:
                findings.append(finding("error", "T101", f"목록 '{l['name']}'[{i}] 에 필드 {missing} 가 없습니다"))
            extra = [f for f in rec if f not in l["fields"]]
            if extra:
                findings.append(finding("warning", "T111", f"목록 '{l['name']}'[{i}] 의 서식에 없는 필드 {extra} 는 쓰지 않습니다"))
            out.append({f: text_ok(f"{l['name']}[{i}].{f}", rec[f]) for f in l["fields"] if f in rec})
        norm["lists"][l["name"]] = out
    known = set(sc["scalars"]) | {l["name"] for l in sc["lists"]}
    for k, v in data.items():
        if k in known:
            continue
        if k.split("[", 1)[0] in fields:
            s = text_ok(f"누름틀 {k}", v)
            if s is not None:
                norm["fields"][k] = s
        else:
            findings.append(finding("warning", "T111", f"서식에 없는 키 '{k}' 는 쓰지 않습니다"))
    unfilled = [f for f in sc["fields"] if f not in {k.split("[", 1)[0] for k in norm["fields"]}]
    if unfilled:
        findings.append(finding("warning", "T112", f"자료가 채우지 않는 누름틀(빈칸으로 남음): {unfilled}"))
    return norm


# ---------------------------------------------------------------- 실행

def unique_stamp(project: Path) -> str:
    base = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stamp, n = base, 1
    while any(any((project / d).glob(f"*template_{stamp}*")) for d in ("history", "capsules", "validation", "exports")):
        n += 1
        stamp = f"{base}-{n}"
    return stamp


def repeat_step(lst: dict, records: List[dict]) -> dict:
    recs = [{f: r[f] for f in lst["fields"]} for r in records]
    if lst["kind"] == "table_rows":
        return {"action": "repeat_and_fill_table_rows", "request": {
            "sectionIndex": lst["section"], "paragraphIndex": lst["paragraph"], "controlIndex": lst["control"],
            "startRow": lst["start"], "endRow": lst["end"], "insertBefore": lst["end"],
            "bindings": lst["bindings"], "records": recs}}
    return {"action": "repeat_and_fill_paragraph_block", "request": {
        "block": {"sectionIndex": lst["section"], "sourceStart": lst["start"], "sourceEnd": lst["end"],
                  "insertBefore": lst["end"], "count": len(recs)},
        "bindings": lst["bindings"], "records": recs}}


def anomaly_counts(rhwp: str, cwd: Path, doc: str) -> Optional[dict]:
    env = run_json([rhwp, "layout-anomaly", doc, "--json"], cwd, ok_codes=(0, 3)).get("envelope")
    if not env:
        return None
    return {k: env.get(k, 0) for k in ANOMALY_KEYS + ("pageCount",)}


def cmd_fill(args) -> int:
    project = Path(args.project).resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    stamp = unique_stamp(project)
    findings: List[dict] = []
    report = {"started": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "rhwp": rhwp,
              "stages": [], "findings": findings}
    report_path = project / "validation" / f"template_{stamp}.json"

    def finish() -> int:
        errors = [f for f in findings if f["severity"] == "error"]
        report["ok"] = not errors
        report["errorCount"] = len(errors)
        report["warningCount"] = len(findings) - len(errors)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for f in findings:
            print(f"[{'오류' if f['severity'] == 'error' else '경고'} {f['code']}] {f['message']}"
                  + (f" ({f['where']})" if f.get("where") else ""))
        print(json.dumps({"ok": report["ok"], "output": report.get("output"),
                          "report": str(report_path.relative_to(project))}, ensure_ascii=False))
        return 0 if report["ok"] else 1

    if args.house:  # 등록한 기관 서식을 프로젝트 sources/ 로 들여온다 (지문 잠금 파일과 함께)
        house = Path(args.templates_dir or SKILL_DIR / "templates" / "house") / args.house
        tpl = next((p for p in sorted(house.glob("template.*")) if p.suffix.lower() in EDITABLE), None)
        if tpl is None:
            findings.append(finding("error", "T000", f"등록된 기관 서식이 없습니다: {args.house}"))
            return finish()
        dest = project / "sources" / f"{args.house}{tpl.suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(tpl, dest)
        # 잠금은 늘 함께 들여온다 — 같은 이름의 다른 파일이 이미 있으면 아래 지문 대조가 막는다.
        lock_src = tpl.with_name(tpl.name + ".lock.json")
        if lock_src.is_file():
            shutil.copyfile(lock_src, dest.with_name(dest.name + ".lock.json"))
        args.template = str(dest.relative_to(project))
    if not args.template:
        findings.append(finding("error", "T000", "서식(sources/…) 또는 --house 이름이 필요합니다"))
        return finish()
    src = inside(project, args.template)
    data_path = inside(project, args.data)
    if src is None or not src.is_file() or src.suffix.lower() not in EDITABLE:
        findings.append(finding("error", "T000", f"서식은 프로젝트 안의 HWP/HWPX 파일이어야 합니다: {args.template}"))
        return finish()
    if data_path is None or not data_path.is_file():
        findings.append(finding("error", "T000", f"자료는 프로젝트 안의 JSON 파일이어야 합니다: {args.data}"))
        return finish()
    rel_src = str(src.relative_to(project))
    report.update({"template": rel_src, "templateSha256": sha256(src), "data": str(data_path.relative_to(project))})
    lock = src.with_name(src.name + ".lock.json")
    if lock.is_file():  # register 로 등록한 서식을 들여온 경우 지문을 대조한다
        want = json.loads(lock.read_text(encoding="utf-8")).get("sha256")
        if want and want != report["templateSha256"]:
            findings.append(finding("error", "T003", "등록 이후 서식 파일이 바뀌었습니다 — 다시 등록하세요"))
            return finish()
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        findings.append(finding("error", "T100", f"자료를 읽지 못했습니다: {exc}"))
        return finish()

    sc = scan(rhwp, project, rel_src)
    findings.extend(sc["findings"])
    if any(f["severity"] == "error" for f in findings):
        return finish()
    report["inventory"] = inventory(sc)
    if not sc["markers"] and not sc["fields"]:
        findings.append(finding("error", "T004", "서식에 자리표시({{키}})도 누름틀도 없습니다"))
        return finish()
    norm = check_data(sc, data, findings)
    if any(f["severity"] == "error" for f in findings):
        return finish()
    baseline = anomaly_counts(rhwp, project, rel_src)

    work = project / "history" / f"template_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    (project / "capsules").mkdir(exist_ok=True)
    out = project / "exports" / f"{src.stem}_template_{stamp}{src.suffix.lower()}"
    out.parent.mkdir(parents=True, exist_ok=True)
    current, parent_capsule = rel_src, None

    def run_stage(label: str, steps: List[dict], output: str) -> bool:
        nonlocal current, parent_capsule
        k = len(report["stages"])
        plan = {"planVersion": "1.0", "input": current, "output": output,
                "preconditions": {"inputSha256": sha256(project / current)},
                "assertions": {"verify": True, "notFoundEmpty": True}, "steps": steps}
        plan_path = work / f"{k:02d}_{label}.plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        rel_plan = str(plan_path.relative_to(project))
        stage = {"label": label, "plan": rel_plan, "input": current, "output": output}
        report["stages"].append(stage)
        dry = run_json([rhwp, "run", rel_plan, "--dry-run", "--json"], project, ok_codes=(0, 3))
        denv = dry.get("envelope") or {}
        if dry["exit"] != 0 or denv.get("invalid"):
            stage["dryRun"] = {"exit": dry["exit"], "invalid": denv.get("invalid"), "stderr": dry.get("stderr")}
            findings.append(finding("error", "T211", f"{label}: dry-run 실패 (exit {dry['exit']}): "
                                    f"{denv.get('invalid') or dry.get('stderr', '')}"))
            return False
        real = run_json([rhwp, "run", rel_plan, "--json"], project, ok_codes=(0, 3))
        renv = real.get("envelope") or {}
        stage["run"] = {"exit": real["exit"], "outputSha256": renv.get("outputSha256"),
                        "steps": renv.get("steps")}
        if real["exit"] != 0 or not (project / output).is_file():
            findings.append(finding("error", "T212", f"{label}: 실행 실패 (exit {real['exit']}) — 저장하지 않았습니다"))
            return False
        for st in renv.get("steps", []):
            if st.get("notFound"):
                findings.append(finding("error", "T213", f"{label} step {st.get('step')}: 찾지 못한 대상 {st['notFound']}"))
            if st.get("ambiguous") or st.get("confusable"):
                findings.append(finding("warning", "T214", f"{label} step {st.get('step')}: 모호한 누름틀 "
                                        f"{st.get('ambiguous') or st.get('confusable')}"))
        capsule = project / "capsules" / f"template_{stamp}_{k:02d}.capsule.json"
        cmd = [rhwp, "replay", rel_plan, "--expect-output-sha256", renv.get("outputSha256") or sha256(project / output),
               "--capsule", str(capsule.relative_to(project)), "--json"]
        if parent_capsule:
            cmd += ["--parent", parent_capsule]
        rep = run_json(cmd, project, ok_codes=(0, 3))
        if rep["exit"] != 0:
            findings.append(finding("error", "T215", f"{label}: 재현되지 않습니다 (replay 산출 해시 불일치)"))
            return False
        stage["capsule"] = parent_capsule = str(capsule.relative_to(project))
        current = output
        return True

    # 2. 목록 반복 — 뒤쪽 원형부터, 단계마다 다시 조사한 주소로.
    order = sorted(sc["lists"], key=lambda l: (l["section"], l.get("paragraph", l["start"])), reverse=True)
    for lst in order:
        records = norm["lists"][lst["name"]]
        if len(records) < 2:
            continue
        now = scan(rhwp, project, current)
        target = next((l for l in now["lists"] if l["name"] == lst["name"]), None)
        if now["findings"] or target is None:
            findings.extend(now["findings"])
            findings.append(finding("error", "T216", f"목록 '{lst['name']}' 원형을 다시 찾지 못했습니다"))
            return finish()
        mid = str((work / f"stage_{len(report['stages']):02d}{src.suffix.lower()}").relative_to(project))
        if not run_stage(f"repeat_{lst['name']}", [repeat_step(target, records[1:])], mid):
            return finish()

    # 3. 남은 칸 — 값 칸, 원형(1번째 자료), 누름틀.
    after = scan(rhwp, project, current)
    findings.extend(after["findings"])
    if any(f["severity"] == "error" for f in findings):
        return finish()
    expected: Dict[str, Tuple[str, int]] = {}
    for mk in after["markers"]:
        if mk["field"] is None:
            value = norm["scalars"][mk["key"]]
        else:
            value = norm["lists"][mk["key"]][0][mk["field"]]
        prev = expected.get(mk["literal"], (value, 0))
        expected[mk["literal"]] = (value, prev[1] + 1)
    steps = [{"action": "replace_text", "find": lit, "replace": val} for lit, (val, _) in expected.items()]
    if norm["fields"]:
        steps.append({"action": "fill_fields", "data": norm["fields"]})
    if not run_stage("fill", steps, str(out.relative_to(project))):
        return finish()
    report["output"] = str(out.relative_to(project))
    report["outputSha256"] = sha256(out)
    replaced = {st.get("find"): st.get("replacedCount")
                for st in report["stages"][-1]["run"].get("steps") or [] if st.get("action") == "replace_text"}
    for lit, (_, count) in expected.items():
        if replaced.get(lit) != count:
            findings.append(finding("error", "T213", f"{lit}: {count}곳 중 {replaced.get(lit)}곳만 바뀌었습니다"))

    # 5. 판정 — 다시 읽은 산출물로.
    final = scan(rhwp, project, report["output"])
    if final["markers"] or any(f["code"] == "T001" for f in final["findings"]):
        left = sorted({m["literal"] for m in final["markers"]})
        findings.append(finding("error", "T201", f"채우지 못한 자리표시가 남았습니다: {left or '형식 오류 칸'}"))
    text_dir = work / "final_text"
    proc = subprocess.run([rhwp, "export-text", report["output"], "-o", str(text_dir.relative_to(project))],
                          cwd=project, capture_output=True, text=True)
    body = "".join(p.read_text(encoding="utf-8") for p in sorted(text_dir.glob("*.txt"))) if proc.returncode == 0 else ""
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    values = list(norm["scalars"].values()) + [v for rows in norm["lists"].values() for r in rows for v in r.values()]
    missing = sorted({v for v in values if v.strip() and squash(v) not in squash(body)})
    if missing:
        findings.append(finding("error", "T202", f"산출물 본문에서 찾지 못한 값 {len(missing)}개: {missing[:5]}"))
    counts = anomaly_counts(rhwp, project, report["output"])
    report["layout"] = {"template": baseline, "output": counts}
    if counts is None or baseline is None:
        findings.append(finding("error", "T203", "조판 이상 검사를 실행하지 못했습니다"))
    else:
        grown = [f"{k} {baseline[k]}→{counts[k]}" for k in ANOMALY_KEYS if counts[k] > baseline[k]]
        if grown:
            findings.append(finding("error", "T203", "서식보다 조판 이상이 늘었습니다: " + ", ".join(grown)))
        if counts["pageCount"] != baseline["pageCount"]:
            findings.append(finding("warning", "T221", f"쪽수 변화: {baseline['pageCount']}쪽 → {counts['pageCount']}쪽 "
                                    "— 목록이 길어진 결과인지 확인하세요"))
    return finish()


# ---------------------------------------------------------------- inspect · register

def cmd_inspect(args) -> int:
    project = Path(args.project).resolve()
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    src = inside(project, args.template)
    if src is None or not src.is_file() or src.suffix.lower() not in EDITABLE:
        print(f"프로젝트 안의 HWP/HWPX 서식이 아닙니다: {args.template}", file=sys.stderr)
        return 2
    sc = scan(rhwp, project, str(src.relative_to(project)))
    inv = inventory(sc) if "markers" in sc else {}
    out = project / "analysis" / f"template_{src.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    rec = {"template": str(src.relative_to(project)), "sha256": sha256(src), **inv, "findings": sc["findings"]}
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    example = out.with_name(f"template_{src.stem}.data.example.json")
    if inv:
        example.write_text(json.dumps(example_data(inv), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for f in sc["findings"]:
        print(f"[{'오류' if f['severity'] == 'error' else '경고'} {f['code']}] {f['message']}")
    ok = not any(f["severity"] == "error" for f in sc["findings"])
    print(json.dumps({"ok": ok, "inventory": inv, "analysis": str(out.relative_to(project)),
                      "example": str(example.relative_to(project)) if inv else None}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_register(args) -> int:
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    src = Path(args.template).resolve()
    if not src.is_file() or src.suffix.lower() not in EDITABLE:
        print(f"HWP/HWPX 서식이 아닙니다: {args.template}", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[\w가-힣-]+", args.name):
        print(f"이름은 글자·숫자·_·- 만 씁니다: {args.name}", file=sys.stderr)
        return 2
    dest = Path(args.templates_dir or SKILL_DIR / "templates" / "house") / args.name
    if dest.exists() and not args.force:
        print(f"이미 등록된 이름입니다(덮어쓰려면 --force): {dest}", file=sys.stderr)
        return 2
    sc = scan(rhwp, src.parent, src.name)
    for f in sc["findings"]:
        print(f"[{'오류' if f['severity'] == 'error' else '경고'} {f['code']}] {f['message']}")
    if any(f["severity"] == "error" for f in sc["findings"]):
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"template{src.suffix.lower()}"
    shutil.copyfile(src, target)
    inv = inventory(sc)
    lock = {"name": args.name, "source": src.name, "sha256": sha256(target),
            "registered": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **inv}
    (dest / f"{target.name}.lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=1) + "\n",
                                                  encoding="utf-8")
    (dest / "data.example.json").write_text(json.dumps(example_data(inv), ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
    print(json.dumps({"ok": True, "registered": str(dest), **inv}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="hwp-master Template Fill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("project")
    p.add_argument("template")
    p.add_argument("--rhwp")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("fill")
    p.add_argument("project")
    p.add_argument("template", nargs="?")
    p.add_argument("--house", help="register 로 등록한 기관 서식 이름")
    p.add_argument("--templates-dir")
    p.add_argument("--data", required=True)
    p.add_argument("--rhwp")
    p.set_defaults(func=cmd_fill)
    p = sub.add_parser("register")
    p.add_argument("template")
    p.add_argument("--name", required=True)
    p.add_argument("--templates-dir")
    p.add_argument("--force", action="store_true")
    p.add_argument("--rhwp")
    p.set_defaults(func=cmd_register)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
