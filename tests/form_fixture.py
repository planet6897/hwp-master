"""테스트용 누름틀 서식: scaffold 문서의 "이름: " 문단마다 누름틀(CLICK_HERE)을 넣는다.

누름틀 마크업은 rhwp 가 HWP 의 누름틀을 HWPX 로 내보낼 때 쓰는 형태와 같다.
"""
import json
import subprocess
import zipfile
from pathlib import Path

_CTRL = ('<hp:ctrl><hp:fieldBegin id="{fid}" type="CLICK_HERE" name="{name}" editable="1" dirty="0">'
         '<hp:parameters cnt="1" name=""><hp:stringParam name="Command">'
         'Clickhere:set:48:Direction:wstring:6:여기에 입력 HelpState:wstring:0:  '
         '</hp:stringParam></hp:parameters></hp:fieldBegin></hp:ctrl>'
         '<hp:ctrl><hp:fieldEnd beginIDRef="{fid}"/></hp:ctrl>')


def make_form(rhwp: str, out: Path, fields) -> Path:
    spec = out.with_name(out.stem + ".spec.json")
    blocks = [{"type": "paragraph", "text": f"{name}: "} for name in fields]
    spec.write_text(json.dumps({"version": "1", "title": "참가 신청서", "blocks": blocks}, ensure_ascii=False),
                    encoding="utf-8")
    plain = out.with_name(out.stem + ".plain.hwpx")
    subprocess.run([rhwp, "scaffold", str(spec), "-o", str(plain)], check=True, capture_output=True)
    with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "Contents/section0.xml":
                xml = data.decode("utf-8")
                for k, name in enumerate(fields):
                    label = f"<hp:t>{name}: </hp:t>"
                    if xml.count(label) != 1:
                        raise AssertionError(f"scaffold 문단 '{name}: ' 를 하나만 찾아야 한다 ({xml.count(label)})")
                    xml = xml.replace(label, label + _CTRL.format(fid=1000 + k, name=name))
                data = xml.encode("utf-8")
            zout.writestr(item, data, compress_type=zipfile.ZIP_STORED if item.filename == "mimetype"
                          else zipfile.ZIP_DEFLATED)
    plain.unlink()
    spec.unlink()
    return out
