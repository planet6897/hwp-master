"""콘솔 입출력 인코딩 맞추기.

Windows 한국어 콘솔은 기본 인코딩이 cp949 라 `—` 같은 문자를 print 하면 UnicodeEncodeError 로 죽는다.
스크립트 진입점에서 utf8_stdio() 를 불러 stdout·stderr 를 UTF-8 로 바꾼다.
"""
from __future__ import annotations

import sys


def utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and (stream.encoding or "").lower().replace("-", "") != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
