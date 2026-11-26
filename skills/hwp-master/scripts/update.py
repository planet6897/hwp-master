#!/usr/bin/env python3
"""hwp-master 와 rhwp 바이너리의 업데이트 확인·받기.

    python3 update.py            # 지금 확인해서 알려 준다 (아무것도 바꾸지 않음)
    python3 update.py --json
    python3 update.py --apply    # git pull --ff-only 로 hwp-master 를 받고, 고정된 rhwp 바이너리를 설치

확인하는 것
- hwp-master: 저장소 main 의 버전(.claude-plugin/plugin.json)이 지금 쓰는 버전보다 높은가
- rhwp 바이너리: main 이 고정한 Release(rhwp_release.json 의 tag)가 지금 것과 다른가,
  지금 고정된 Release 의 바이너리가 설치돼 있는가

rhwp_locate.py 가 하루에 한 번까지 이 확인을 하고 업데이트가 있으면 한 줄로 알린다. 네트워크가 안 되면 조용히 넘어간다.
확인을 끄려면 환경변수 HWP_MASTER_NO_UPDATE_CHECK=1 을 준다(CI 환경변수가 있어도 꺼진다).

--apply 는 git 으로 받은 폴더에서만 동작한다. 커밋하지 않은 변경이 있거나 main 브랜치가 아니면 건드리지 않고 멈춘다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT = SKILL_DIR.parents[1]
LOCK = SKILL_DIR / "rhwp_release.json"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
SKILL_MD = SKILL_DIR / "SKILL.md"
BRANCH = "main"
CACHE_TTL = 24 * 3600
APPLY_HINT = "python3 skills/hwp-master/scripts/update.py --apply"


def _version_tuple(text: Optional[str]) -> Optional[tuple]:
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def local_version() -> Optional[str]:
    """지금 쓰는 hwp-master 버전. plugin.json 이 없으면(스킬 폴더만 복사) SKILL.md 의 metadata.version."""
    try:
        return json.loads(PLUGIN.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        m = re.search(r"^\s*version:\s*([0-9][^\s]*)", SKILL_MD.read_text(encoding="utf-8"), re.MULTILINE)
        return m.group(1) if m else None
    except OSError:
        return None


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _install_rhwp():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import install_rhwp
    return install_rhwp


def cache_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "hwp-master" / "update_check.json"


def _fetch_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "hwp-master-update-check"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_remote(repo: str, timeout: float = 5.0) -> dict:
    """main 의 버전과 고정 Release. 네트워크 오류는 OSError·ValueError 로 올라간다."""
    base = f"https://raw.githubusercontent.com/{repo}/{BRANCH}"
    plugin = _fetch_json(f"{base}/.claude-plugin/plugin.json", timeout)
    lock = _fetch_json(f"{base}/skills/hwp-master/rhwp_release.json", timeout)
    return {"version": plugin.get("version"), "tag": lock.get("tag"), "rhwpVersion": lock.get("rhwp_version")}


def _read_cache() -> Optional[dict]:
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(data: dict) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def remote_state(repo: str, max_age: Optional[float], timeout: float = 5.0, now: Optional[float] = None) -> Optional[dict]:
    """max_age 초 안에 확인한 기록이 있으면 그것을, 아니면 새로 받아 온다. 받지 못하면 None.

    받지 못한 것도 기록해 두어, 오프라인일 때 실행마다 기다리지 않게 한다.
    """
    now = time.time() if now is None else now
    cached = _read_cache()
    if max_age is not None and cached and cached.get("repo") == repo and 0 <= now - cached.get("checkedAt", 0) < max_age:
        return cached.get("remote")
    try:
        remote = fetch_remote(repo, timeout)
    except (OSError, ValueError):
        remote = None
    _write_cache({"repo": repo, "checkedAt": now, "remote": remote})
    return remote


def compare(local_ver: Optional[str], local_tag: Optional[str], remote: Optional[dict], binary_installed: bool) -> dict:
    """업데이트가 있는지 판정한다 (네트워크·파일 접근 없음)."""
    result = {"checked": remote is not None, "available": False, "project": None, "rhwp": None,
              "binaryMissing": not binary_installed, "localVersion": local_ver, "localTag": local_tag}
    if remote is None:
        return result
    lv, rv = _version_tuple(local_ver), _version_tuple(remote.get("version"))
    if lv and rv and rv > lv:
        result["project"] = {"from": local_ver, "to": remote["version"]}
    if remote.get("tag") and local_tag and remote["tag"] != local_tag:
        result["rhwp"] = {"from": local_tag, "to": remote["tag"]}
    result["available"] = bool(result["project"] or result["rhwp"])
    return result


def check(max_age: Optional[float] = None, timeout: float = 5.0) -> dict:
    """지금 상태와 main 을 견준다. max_age 를 주면 그 시간 안의 확인 기록을 다시 쓴다."""
    lock = load_lock()
    installed = _install_rhwp().installed_binary(lock) is not None
    remote = remote_state(lock["repo"], max_age, timeout)
    return compare(local_version(), lock.get("tag"), remote, installed)


def notice(status: dict) -> Optional[str]:
    """사용자에게 보일 한 줄. 알릴 것이 없으면 None."""
    parts = []
    if status.get("project"):
        parts.append(f"hwp-master {status['project']['from']} → {status['project']['to']}")
    if status.get("rhwp"):
        parts.append(f"rhwp 바이너리 {status['rhwp']['from']} → {status['rhwp']['to']}")
    if not parts:
        return None
    return f"업데이트가 있습니다: {', '.join(parts)}. 받으려면 `{APPLY_HINT}`"


def quiet_notice() -> Optional[str]:
    """rhwp_locate.py 용: 하루 한 번까지만 확인하고, 어떤 오류도 올리지 않는다."""
    if os.environ.get("HWP_MASTER_NO_UPDATE_CHECK") or os.environ.get("CI"):
        return None
    try:
        return notice(check(max_age=CACHE_TTL, timeout=3.0))
    except Exception:  # 알림 때문에 본 작업이 죽으면 안 된다
        return None


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def apply() -> dict:
    """hwp-master 를 main 최신으로 받고 고정된 rhwp 바이너리를 설치한다. 실패하면 RuntimeError."""
    steps = []
    if not (ROOT / ".git").exists():
        raise RuntimeError(f"{ROOT} 는 git 으로 받은 폴더가 아니라 자동으로 받을 수 없습니다. "
                           "플러그인으로 설치했으면 플러그인을 업데이트하고, 압축 파일로 받았으면 새로 받으세요.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != BRANCH:
        raise RuntimeError(f"지금 브랜치가 {branch} 입니다. {BRANCH} 브랜치에서만 자동으로 받습니다 "
                           f"(`git switch {BRANCH}` 뒤 다시 실행).")
    dirty = _git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        raise RuntimeError("커밋하지 않은 변경이 있어 받지 않았습니다:\n" + dirty)
    before = _git("rev-parse", "HEAD").stdout.strip()
    pull = _git("pull", "--ff-only", "origin", BRANCH)
    if pull.returncode != 0:
        raise RuntimeError("git pull --ff-only 실패: " + (pull.stderr or pull.stdout).strip())
    after = _git("rev-parse", "HEAD").stdout.strip()
    steps.append({"step": "git pull", "from": before[:7], "to": after[:7], "changed": before != after})

    # 받은 뒤의 코드·고정 기록으로 설치해야 하므로 새 프로세스로 돌린다.
    lock = load_lock()
    if _install_rhwp().installed_binary(lock) is None:
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("install_rhwp.py")), "--json"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            result = {"ok": False, "error": (proc.stderr or proc.stdout).strip()}
        if not result.get("ok"):
            raise RuntimeError("rhwp 바이너리 설치 실패: " + str(result.get("error", result)))
        steps.append({"step": "install rhwp", "tag": result["tag"], "path": result["path"], "changed": True})
    else:
        steps.append({"step": "install rhwp", "tag": lock["tag"], "changed": False})
    _write_cache({})  # 다음 확인은 새로 한다
    return {"ok": True, "version": local_version(), "tag": lock["tag"], "steps": steps}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="업데이트를 받는다 (git pull --ff-only + rhwp 바이너리 설치)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.apply:
        try:
            result = apply()
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) if args.json else f"오류: {exc}",
                  file=sys.stdout if args.json else sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for s in result["steps"]:
                if s["step"] == "git pull":
                    print(f"hwp-master: {s['from']} → {s['to']}" if s["changed"] else "hwp-master: 이미 최신")
                else:
                    print(f"rhwp 바이너리: {s['tag']} 설치 — {s['path']}" if s["changed"]
                          else f"rhwp 바이너리: {s['tag']} 이미 설치됨")
            print(f"지금: hwp-master {result['version']}, {result['tag']}")
        return 0

    status = check()
    if args.json:
        print(json.dumps(status, ensure_ascii=False))
        return 0
    print(f"지금: hwp-master {status['localVersion']}, {status['localTag']}")
    if not status["checked"]:
        print("업데이트를 확인하지 못했습니다 (네트워크).", file=sys.stderr)
    else:
        print(notice(status) or "최신입니다.")
    if status["binaryMissing"]:
        print(f"고정된 rhwp 바이너리({status['localTag']})가 설치돼 있지 않습니다. "
              "`python3 skills/hwp-master/scripts/install_rhwp.py` 로 설치하세요.")
    return 0


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
