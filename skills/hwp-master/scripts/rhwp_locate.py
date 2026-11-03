#!/usr/bin/env python3
"""rhwp 바이너리 위치·버전 확인.

탐색 순서: --rhwp 인자 → 환경변수 RHWP_BIN → install_rhwp.py 가 설치한 고정 Release 바이너리 → PATH 의 `rhwp`.
최소 버전 미만이거나 찾지 못하면 exit 2 와 설치 안내를 낸다.
Linux 에서 한글 글꼴 파일이 보이지 않으면 경고한다(미리보기 PNG·PDF 의 한글이 빠짐). 경고는 exit 0.

    python3 rhwp_locate.py [--rhwp PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

MIN_VERSION = (0, 9, 0)
INSTALL_HINT = (
    "rhwp 바이너리를 찾지 못했습니다. `python3 skills/hwp-master/scripts/install_rhwp.py` 로 "
    "hwp-master Release 의 바이너리를 설치하거나, https://github.com/edwardkim/rhwp 에서 빌드해 "
    "PATH 에 두거나 RHWP_BIN 환경변수로 경로를 지정하세요."
)


FONT_HINT = (
    "한글 글꼴을 찾지 못했습니다. HWPX 는 정상으로 만들어지지만 rhwp 미리보기(PNG·PDF)에서 한글이 빠집니다. "
    "Ubuntu/Debian 은 `sudo apt install fonts-noto-cjk`, 그 밖에는 한글 TTF/OTF 를 ~/.local/share/fonts 에 두거나 "
    "build.py --font-path <폴더> 로 지정하세요."
)
# 파일 이름으로 한글 글꼴을 가린다. fc-list 가 없는 최소 설치 환경에서도 동작하게 하려는 것이다.
_KOREAN_FONT_NAME = re.compile(
    r"(cjk|[-_ ]kr[-_. ]|kr\.|korean|nanum|hamchorom|hcr|hancom|batang|gulim|dotum|gungsuh|malgun|"
    r"baekmuk|unbatang|undotum|myeongjo|pretendard|spoqa|kopub|ibmplexsanskr|gowun|seoul)",
    re.IGNORECASE,
)
_FONT_EXT = {".ttf", ".otf", ".ttc", ".otc"}
_SYSTEM_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts")


def korean_font_status(extra_dirs: Iterable[str] = ()) -> dict:
    """미리보기에 쓸 한글 글꼴이 있는지 본다. macOS·Windows 는 시스템 한글 글꼴이 있어 확인을 생략한다."""
    dirs: List[str] = list(extra_dirs)
    env = os.environ.get("RHWP_FONT_PATH")
    if env:
        dirs += [d for d in env.split(os.pathsep) if d]
    if not sys.platform.startswith("linux"):
        return {"ok": True, "checked": False, "platform": sys.platform}
    dirs += list(_SYSTEM_FONT_DIRS) + [str(Path.home() / ".local/share/fonts"), str(Path.home() / ".fonts")]
    for d in dirs:
        root = Path(d)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in _FONT_EXT and _KOREAN_FONT_NAME.search(path.name):
                return {"ok": True, "checked": True, "platform": sys.platform, "example": str(path)}
    return {"ok": False, "checked": True, "platform": sys.platform, "searched": dirs, "hint": FONT_HINT}


class RhwpNotFound(RuntimeError):
    pass


def _parse_version(text: str) -> Optional[tuple]:
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def _installed() -> Optional[str]:
    """install_rhwp.py 가 기본 위치에 설치한 고정 Release 바이너리."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("install_rhwp", Path(__file__).with_name("install_rhwp.py"))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = module.installed_binary()
    return str(path) if path else None


def locate(explicit: Optional[str] = None) -> dict:
    """rhwp 경로와 버전을 dict 로 돌려준다. 실패하면 RhwpNotFound."""
    candidate = explicit or os.environ.get("RHWP_BIN") or _installed() or shutil.which("rhwp")
    if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise RhwpNotFound(INSTALL_HINT + (f" (확인한 경로: {candidate})" if candidate else ""))
    proc = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=30)
    raw = (proc.stdout or proc.stderr).strip()
    version = _parse_version(raw)
    if version is None:
        raise RhwpNotFound(f"rhwp 버전을 해석하지 못했습니다: {raw!r}")
    if version < MIN_VERSION:
        need = ".".join(map(str, MIN_VERSION))
        raise RhwpNotFound(f"rhwp {raw} 는 최소 버전 {need} 미만입니다.")
    return {"path": os.path.abspath(candidate), "version": ".".join(map(str, version)), "raw": raw}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rhwp", help="rhwp 바이너리 경로")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        info = locate(args.rhwp)
    except RhwpNotFound as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(str(exc), file=sys.stderr)
        return 2
    font = korean_font_status()
    if args.json:
        print(json.dumps({"ok": True, **info, "koreanFont": font}, ensure_ascii=False))
    else:
        print(f"rhwp {info['version']} — {info['path']}")
        if not font["ok"]:
            print(f"경고: {FONT_HINT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
