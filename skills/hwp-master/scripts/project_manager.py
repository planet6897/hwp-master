#!/usr/bin/env python3
"""hwp-master 프로젝트 폴더 관리.

    python3 project_manager.py init <이름> [--root projects]
    python3 project_manager.py import-sources <프로젝트> <파일...> [--rhwp PATH]
    python3 project_manager.py validate <프로젝트> [--stage early|final]
    python3 project_manager.py status <프로젝트>

원본 자료는 sources/ 에 복사만 하고 수정하지 않는다. 텍스트 추출본은 analysis/ 에 둔다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from itertools import count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hds  # noqa: E402
import rhwp_locate  # noqa: E402

SUBDIRS = ("sources", "analysis", "units", "build", "exports", "validation", "history", "capsules")
TEXT_SUFFIXES = {".md", ".txt", ".markdown"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_project(project: Path) -> dict:
    meta = project / "project.json"
    if not meta.exists():
        raise SystemExit(f"hwp-master 프로젝트가 아닙니다 (project.json 없음): {project}")
    return hds.load_json(meta)


def save_project(project: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=project, delete=False)
    pending = Path(temp.name)
    try:
        with temp:
            temp.write(payload)
        pending.replace(project / "project.json")
    finally:
        pending.unlink(missing_ok=True)


def cmd_init(args) -> int:
    stamp = dt.date.today().strftime("%Y%m%d")
    project = Path(args.root) / f"{args.name}_{stamp}"
    if project.exists():
        print(f"이미 있습니다: {project}", file=sys.stderr)
        return 2
    for sub in SUBDIRS:
        (project / sub).mkdir(parents=True)
    save_project(project, {
        "name": args.name,
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "hdsVersion": hds.HDS_VERSION,
        "sources": [],
    })
    print(project)
    return 0


def _run(cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)


def _reserve_analysis_dir(analysis: Path, src: Path) -> Path:
    for index in count(1):
        name = src.stem if index == 1 else f"{src.stem}-{index}"
        out_dir = analysis / name
        if (analysis / f"{name}.import.hwpx").exists():
            continue
        try:
            out_dir.mkdir(parents=True)
        except FileExistsError:
            continue
        return out_dir


def _extract(rhwp: str, src: Path, out_dir: Path, pandoc: str | None) -> dict:
    """원본 사본에서 Markdown 추출. 결과 dict 에 상태를 남긴다."""
    suffix = src.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        shutil.copy2(src, out_dir / (src.stem + ".md"))
        return {"extract": "copied"}
    if suffix in (".hwp", ".hwpx"):
        proc = _run([rhwp, "export-markdown", str(src), "-o", str(out_dir)])
        return {"extract": "rhwp export-markdown", "exit": proc.returncode,
                "stderr": proc.stderr.strip()[-500:] if proc.returncode else ""}
    if suffix == ".pdf":
        hwpx = out_dir.with_name(f"{out_dir.name}.import.hwpx")
        proc = _run([rhwp, "import-pdf", str(src), str(hwpx), "--layout", "editable", "--json"])
        if proc.returncode != 0:
            return {"extract": "rhwp import-pdf", "exit": proc.returncode, "stderr": proc.stderr.strip()[-500:]}
        proc = _run([rhwp, "export-markdown", str(hwpx), "-o", str(out_dir)])
        return {"extract": "rhwp import-pdf → export-markdown", "exit": proc.returncode,
                "stderr": proc.stderr.strip()[-500:] if proc.returncode else ""}
    if pandoc:
        proc = _run([pandoc, str(src), "-t", "gfm", "-o", str(out_dir / (src.stem + ".md"))])
        return {"extract": "pandoc", "exit": proc.returncode, "stderr": proc.stderr.strip()[-500:]}
    return {"extract": "unsupported", "note": "에이전트가 직접 읽어 analysis/ 에 요약을 남기세요"}


def cmd_import_sources(args) -> int:
    project = Path(args.project)
    data = load_project(project)
    rhwp = rhwp_locate.locate(args.rhwp)["path"]
    sources = data["sources"]
    source_indices = {os.path.normcase(entry["file"]): index for index, entry in enumerate(sources)}
    status = 0
    for name in args.files:
        src = Path(name)
        if not src.is_file():
            print(f"파일 없음: {src}", file=sys.stderr)
            status = 1
            continue
        dest = project / "sources" / src.name
        source_path = dest.relative_to(project).as_posix()
        key = os.path.normcase(source_path)
        index = source_indices.get(key)
        if dest.exists():
            if not dest.is_file() or sha256(src) != sha256(dest):
                print(f"같은 이름의 다른 자료가 이미 있습니다: {dest.name}", file=sys.stderr)
                status = 1
                continue
            if index is not None and sources[index].get("exit", 0) == 0:
                print(f"이미 가져온 파일: {dest.name}", file=sys.stderr)
                continue
        else:
            shutil.copy2(src, dest)
        entry = {"file": source_path, "sha256": sha256(dest), "bytes": dest.stat().st_size}
        suffix = dest.suffix.lower()
        pandoc = shutil.which("pandoc") if suffix in (".docx", ".odt", ".rtf", ".html", ".htm") else None
        out_dir = project / "analysis" / dest.stem
        if suffix in TEXT_SUFFIXES or suffix in (".hwp", ".hwpx", ".pdf") or pandoc:
            out_dir = _reserve_analysis_dir(project / "analysis", dest)
            entry["analysis"] = out_dir.relative_to(project).as_posix()
        try:
            entry.update(_extract(rhwp, dest, out_dir, pandoc))
        except (OSError, subprocess.TimeoutExpired) as exc:
            entry.update(extract="failed", exit=1, stderr=str(exc)[-500:])
        if entry.get("exit", 0) != 0:
            status = 1
        if index is None:
            source_indices[key] = len(sources)
            sources.append(entry)
        else:
            sources[index] = entry
        save_project(project, data)
        print(json.dumps(entry, ensure_ascii=False))
    return status


def cmd_validate(args) -> int:
    project = Path(args.project)
    load_project(project)
    result = hds.summarize(hds.lint_project(project, args.stage))
    out = project / "validation" / f"hds_lint_{args.stage}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("ok", "errorCount", "warningCount")}, ensure_ascii=False),
          f"→ {out}")
    return 0 if result["ok"] else 1


def cmd_status(args) -> int:
    project = Path(args.project)
    data = load_project(project)
    have = {p.stem for p in hds.unit_files(project)}
    outline = project / "outline.json"
    units = hds.load_json(outline).get("units", []) if outline.exists() else []
    print(f"프로젝트: {data['name']}  자료 {len(data['sources'])}건")
    for name in ("doc_spec.md", "spec_lock.json", "outline.json"):
        print(f"  {'✓' if (project / name).exists() else '·'} {name}")
    for u in units:
        print(f"  {'✓' if u.get('id') in have else '·'} {u.get('id')} {u.get('title', '')}")
    exports = sorted((project / "exports").glob("*"))
    for e in exports:
        print(f"  → {e.relative_to(project)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="hwp-master 프로젝트 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("name")
    p.add_argument("--root", default="projects")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("import-sources")
    p.add_argument("project")
    p.add_argument("files", nargs="+")
    p.add_argument("--rhwp")
    p.set_defaults(func=cmd_import_sources)
    p = sub.add_parser("validate")
    p.add_argument("project")
    p.add_argument("--stage", choices=("early", "final"), default="final")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("status")
    p.add_argument("project")
    p.set_defaults(func=cmd_status)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
