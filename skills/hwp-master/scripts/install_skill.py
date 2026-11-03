#!/usr/bin/env python3
"""hwp-master 스킬을 Claude Code 사용자 스킬 폴더에 연결한다.

    python3 install_skill.py [--skills-dir 폴더] [--remove] [--json]

~/.claude/skills/hwp-master (CLAUDE_CONFIG_DIR 가 있으면 그 아래 skills/) 에 이 저장소의
skills/hwp-master 를 가리키는 링크를 만든다. 복사가 아니라 링크라서 저장소를 git pull 하면 스킬도 함께 갱신된다.
그러면 어느 폴더에서 claude 를 실행해도 hwp-master 스킬이 자동으로 로딩된다.

- macOS·Linux: 심볼릭 링크. Windows: 디렉터리 정션(관리자 권한·개발자 모드 불필요).
- 같은 이름의 폴더·링크가 이미 있고 이 저장소를 가리키지 않으면 건드리지 않고 실패한다.
- --remove 는 이 저장소를 가리키는 링크만 지운다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parents[1]
NAME = "hwp-master"


def default_skills_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "skills"


def _is_link(p: Path) -> bool:
    """심볼릭 링크 또는 Windows 정션."""
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)  # Python 3.12+
    if is_junction is not None:
        return is_junction()
    try:
        return os.path.realpath(p) != os.path.abspath(p) and p.is_dir()
    except OSError:
        return False


def _points_here(p: Path) -> bool:
    try:
        return Path(os.path.realpath(p)) == Path(os.path.realpath(SKILL_DIR))
    except OSError:
        return False


def _make_link(link: Path, target: Path) -> str:
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError):
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"정션을 만들지 못했다: {(r.stderr or r.stdout).strip()}")
        return "junction"
    os.symlink(target, link, target_is_directory=True)
    return "symlink"


def _remove_link(link: Path) -> None:
    if link.is_symlink() and os.name != "nt":
        link.unlink()
    else:
        os.rmdir(link)  # 정션·디렉터리 심볼릭 링크는 rmdir 로 링크만 지운다 (대상 폴더는 그대로)


def install(skills_dir: Optional[Path] = None) -> dict:
    skills_dir = skills_dir or default_skills_dir()
    link = skills_dir / NAME
    if link.exists() or link.is_symlink():
        if _is_link(link) and _points_here(link):
            return {"ok": True, "action": "unchanged", "path": str(link), "target": str(SKILL_DIR)}
        raise RuntimeError(
            f"{link} 가 이미 있고 이 저장소({SKILL_DIR})를 가리키지 않는다. "
            "다른 hwp-master(플러그인·이전 복사본)라면 직접 정리한 뒤 다시 실행한다.")
    skills_dir.mkdir(parents=True, exist_ok=True)
    kind = _make_link(link, SKILL_DIR)
    if not (link / "SKILL.md").is_file():
        raise RuntimeError(f"링크를 만들었으나 {link / 'SKILL.md'} 를 읽을 수 없다.")
    return {"ok": True, "action": "created", "kind": kind, "path": str(link), "target": str(SKILL_DIR)}


def remove(skills_dir: Optional[Path] = None) -> dict:
    link = (skills_dir or default_skills_dir()) / NAME
    if not (link.exists() or link.is_symlink()):
        return {"ok": True, "action": "absent", "path": str(link)}
    if not (_is_link(link) and _points_here(link)):
        raise RuntimeError(f"{link} 는 이 저장소를 가리키는 링크가 아니라서 지우지 않는다.")
    _remove_link(link)
    return {"ok": True, "action": "removed", "path": str(link)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skills-dir", type=Path, help="사용자 스킬 폴더 (기본: ~/.claude/skills)")
    ap.add_argument("--remove", action="store_true", help="이 저장소를 가리키는 링크를 지운다")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = (remove if args.remove else install)(args.skills_dir)
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) if args.json else f"오류: {exc}",
              file=sys.stdout if args.json else sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        msg = {"created": "스킬 연결", "unchanged": "스킬 연결 확인(이미 있음)",
               "removed": "스킬 연결 해제", "absent": "연결 없음"}[result["action"]]
        print(f"{msg} — {result['path']}" + (f" → {result['target']}" if "target" in result else ""))
    return 0


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
