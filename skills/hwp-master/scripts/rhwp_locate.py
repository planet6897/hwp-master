#!/usr/bin/env python3
"""rhwp 바이너리 위치·버전 확인.

탐색 순서: --rhwp 인자 → 환경변수 RHWP_BIN → PATH 의 `rhwp`.
최소 버전 미만이거나 찾지 못하면 exit 2 와 설치 안내를 낸다.

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
from typing import Optional

MIN_VERSION = (0, 9, 0)
INSTALL_HINT = (
    "rhwp 바이너리를 찾지 못했습니다. https://github.com/edwardkim/rhwp 에서 빌드하거나 "
    "설치한 뒤 PATH 에 두거나 RHWP_BIN 환경변수로 경로를 지정하세요."
)


class RhwpNotFound(RuntimeError):
    pass


def _parse_version(text: str) -> Optional[tuple]:
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def locate(explicit: Optional[str] = None) -> dict:
    """rhwp 경로와 버전을 dict 로 돌려준다. 실패하면 RhwpNotFound."""
    candidate = explicit or os.environ.get("RHWP_BIN") or shutil.which("rhwp")
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
    if args.json:
        print(json.dumps({"ok": True, **info}, ensure_ascii=False))
    else:
        print(f"rhwp {info['version']} — {info['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
