"""표 안의 표 픽스처: scaffold 로 만든 HWPX 의 두 번째 표를 첫 번째 표의 칸 안으로 옮긴다.

scaffold 는 중첩 표를 만들지 못하므로 OWPML 을 문자열로 고친다. rhwp 가 그대로 읽는지는 테스트가 확인한다.
"""
from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path

SPEC = {"version": "1", "title": "중첩 표 시험", "blocks": [
    {"type": "paragraph", "text": "아래 표의 예산 칸 안에 세부 표가 있다."},
    {"type": "table", "rows": [["구분", "내용"], ["예산", "세부 내역"]]},
    {"type": "table", "rows": [["항목", "금액"], ["장비", "580억 달러"], ["인건비", "3억 5천만 원"]]},
]}


def make_nested(rhwp: str, out: Path, anchor: str = "세부 내역") -> Path:
    """두 번째 표를 첫 번째 표의 `anchor` 칸 문단 뒤(같은 칸의 다음 run)로 옮긴 HWPX 를 만든다."""
    spec = out.with_suffix(".json")
    spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    plain = out.with_name(out.stem + "_plain.hwpx")
    subprocess.run([rhwp, "scaffold", str(spec), "-o", str(plain)], check=True, capture_output=True)
    with zipfile.ZipFile(plain) as z:
        entries = [(info, z.read(info.filename)) for info in z.infolist()]
    sec = next(data for info, data in entries if info.filename == "Contents/section0.xml").decode("utf-8")

    first = sec.index("<hp:tbl")
    second = sec.index("<hp:tbl", first + 1)
    tbl_end = sec.index("</hp:tbl>", second) + len("</hp:tbl>")
    inner = sec[second:tbl_end]
    p_start = sec.rindex("<hp:p ", 0, second)
    p_end = sec.index("</hp:p>", tbl_end) + len("</hp:p>")
    sec = sec[:p_start] + sec[p_end:]  # 두 번째 표의 문단을 지운다

    cell_run = f"<hp:t>{anchor}</hp:t></hp:run>"
    assert sec.count(cell_run) == 1, "anchor 칸을 찾지 못함"
    sec = sec.replace(cell_run, cell_run + f'<hp:run charPrIDRef="0">{inner}<hp:t/></hp:run>')

    with zipfile.ZipFile(out, "w") as z:
        for info, data in entries:
            if info.filename == "Contents/section0.xml":
                data = sec.encode("utf-8")
            z.writestr(info, data, compress_type=info.compress_type)
    plain.unlink()
    spec.unlink()
    return out


LAYOUT_SPEC = {"version": "1", "title": "레이아웃 표 시험", "blocks": [
    {"type": "table", "rows": [["2026년 돌봄 확대 보도자료"]]},
    {"type": "table", "rows": [["사업 개요"], ["이 칸은 상자 안에 든 본문으로, 서른 글자가 넘는 긴 설명 문장을 담고 있다."]]},
    {"type": "table", "rows": [["항목", "금액"], ["장비", "100원"]]},
    {"type": "table", "rows": [["담당", "홍길동"], ["연락처", "02-000-0000"]]},
]}


def make_layout(rhwp: str, out: Path, borderless_table: int = 4) -> Path:
    """1×1 상자·제목+본문 상자·데이터 표·투명 표(n번째 표의 칸 테두리를 NONE borderFill 로)를 담은 HWPX."""
    spec = out.with_suffix(".json")
    spec.write_text(json.dumps(LAYOUT_SPEC, ensure_ascii=False), encoding="utf-8")
    plain = out.with_name(out.stem + "_plain.hwpx")
    subprocess.run([rhwp, "scaffold", str(spec), "-o", str(plain)], check=True, capture_output=True)
    with zipfile.ZipFile(plain) as z:
        entries = [(info, z.read(info.filename)) for info in z.infolist()]
    data = dict((info.filename, d) for info, d in entries)
    header = data["Contents/header.xml"].decode("utf-8")
    none_id = re.search(r'<hh:borderFill id="(\d+)"[^>]*>(?:(?!</hh:borderFill>).)*?<hh:leftBorder type="NONE"'
                        r'(?:(?!</hh:borderFill>).)*?<hh:rightBorder type="NONE"(?:(?!</hh:borderFill>).)*?'
                        r'<hh:topBorder type="NONE"(?:(?!</hh:borderFill>).)*?<hh:bottomBorder type="NONE"',
                        header, re.S)
    assert none_id, "테두리 없는 borderFill 이 없다"
    sec = data["Contents/section0.xml"].decode("utf-8")
    starts = [m.start() for m in re.finditer(r"<hp:tbl ", sec)]
    start = starts[borderless_table - 1]
    end = sec.index("</hp:tbl>", start)
    part = re.sub(r'(<hp:tc [^>]*borderFillIDRef=")\d+"', rf'\g<1>{none_id.group(1)}"', sec[start:end])
    sec = sec[:start] + part + sec[end:]
    with zipfile.ZipFile(out, "w") as z:
        for info, d in entries:
            if info.filename == "Contents/section0.xml":
                d = sec.encode("utf-8")
            z.writestr(info, d, compress_type=info.compress_type)
    plain.unlink()
    spec.unlink()
    return out
