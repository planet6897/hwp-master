"""Beautify 시험 입력: scaffold 문서에 한컴 기본 개요 번호(1. 가. 1) 가) (1) (가) ①)를 넣는다.

scaffold 산출에는 번호 정의가 없어 개요 번호가 1.1. 꼴로 나온다. 한컴 새 문서의 header.xml 이
싣는 `<hh:numbering>`(수준별 paraHead)을 refList 에 더하고 구역의 outlineShapeIDRef 를 그 번호로
가리키게 한다. 번호 정의 외의 내용은 바꾸지 않는다.
"""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

# (수준, numFormat, 형식 문자열) — 한컴 2020/2024 새 문서 기본값
HANCOM_OUTLINE = [
    (1, "DIGIT", "^1."), (2, "HANGUL_SYLLABLE", "^2."), (3, "DIGIT", "^3)"),
    (4, "HANGUL_SYLLABLE", "^4)"), (5, "DIGIT", "(^5)"), (6, "HANGUL_SYLLABLE", "(^6)"),
    (7, "CIRCLED_DIGIT", "^7"),
]


def numbering_xml(formats=HANCOM_OUTLINE) -> str:
    heads = "".join(
        f'<hh:paraHead start="1" level="{lv}" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" '
        f'textOffsetType="PERCENT" textOffset="50" numFormat="{fmt}" charPrIDRef="4294967295" '
        f'checkable="0">{text}</hh:paraHead>'
        for lv, fmt, text in formats)
    return f'<hh:numberings itemCnt="1"><hh:numbering id="1" start="0">{heads}</hh:numbering></hh:numberings>'


def make_outline_doc(rhwp: str, out: Path, texts, formats=HANCOM_OUTLINE) -> Path:
    """본문 문단 texts 로 scaffold 한 뒤 번호 정의를 넣은 HWPX 를 out 에 쓴다."""
    spec = {"version": "1", "blocks": [{"type": "paragraph", "text": t} for t in texts]}
    raw = out.with_suffix(".raw.hwpx")
    spec_path = out.with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    subprocess.run([rhwp, "scaffold", str(spec_path), "-o", str(raw)], check=True, capture_output=True)
    with zipfile.ZipFile(raw) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "Contents/header.xml":
                text = data.decode("utf-8")
                assert "<hh:numberings" not in text
                text = text.replace("</hh:refList>", numbering_xml(formats) + "</hh:refList>", 1)
                data = text.encode("utf-8")
            elif item.filename.startswith("Contents/section"):
                data = data.decode("utf-8").replace('outlineShapeIDRef="0"', 'outlineShapeIDRef="1"', 1).encode("utf-8")
            zout.writestr(item, data)
    raw.unlink()
    spec_path.unlink()
    return out
