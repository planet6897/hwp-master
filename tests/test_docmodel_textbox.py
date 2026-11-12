"""docmodel: 글상자(drawText)·control 번호·손상/암호 HWPX — rhwp 없이 손으로 만든 OWPML 로 시험한다."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import docmodel  # noqa: E402

NS = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"'
HEADER = f'<hh:head {NS}><hh:refList><hh:paraProperties><hh:paraPr id="0"><hh:heading type="NONE" idRef="0" level="0"/></hh:paraPr></hh:paraProperties></hh:refList></hh:head>'


def para(inner, pid=0):
    return f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0">{inner}</hp:p>'


def textbox(*texts):
    paras = "".join(para(f'<hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run>') for t in texts)
    return f'<hp:rect id="9"><hp:drawText><hp:subList>{paras}</hp:subList></hp:drawText></hp:rect>'


SECTION = (f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" {NS}>'
           + para('<hp:run charPrIDRef="0"><hp:secPr/><hp:ctrl><hp:colPr/></hp:ctrl></hp:run>'
                  f'<hp:run charPrIDRef="0">{textbox("목  차", "1장 개요")}{textbox("02-000-0000")}</hp:run>')
           + para('<hp:run charPrIDRef="0"><hp:t>본문 문단</hp:t></hp:run>', 1)
           + '</hs:sec>')


def make_hwpx(path: Path, header=HEADER, section=SECTION):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", header)
        z.writestr("Contents/section0.xml", section)
    return path


class TextBoxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = docmodel.build_from_hwpx(make_hwpx(Path(self.tmp.name) / "t.hwpx"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_textbox_blocks_and_control_numbers(self):
        boxes = [b for b in self.doc["blocks"] if b["type"] == "textBox"]
        self.assertEqual([(b["id"], b["control"]) for b in boxes], [("s0.p0.x0", 2), ("s0.p0.x1", 3)],
                         "control 번호는 run 자식 가운데 글(t)을 뺀 순번(secPr 0, ctrl 1, …)")
        inner = [(b["id"], b["text"]) for b in self.doc["blocks"] if b.get("fromTextBox")]
        self.assertEqual(inner, [("s0.p0.x0.p0", "목  차"), ("s0.p0.x0.p1", "1장 개요"), ("s0.p0.x1.p0", "02-000-0000")])
        self.assertEqual(self.doc["counts"]["textBox"], 2)
        self.assertNotIn("emptyParagraph", self.doc["counts"], "글상자만 있는 문단은 빈 문단이 아니다")

    def test_field_path_into_textbox(self):
        index = docmodel.BlockIndex(self.doc, [])
        self.assertEqual(index.locate(0, 0, path=[{"kind": "textBox", "control": 2, "paragraph": 1}]), "s0.p0.x0.p1")
        self.assertEqual(index.locate(0, 0, path=[{"kind": "textBox", "control": 3, "paragraph": 5}]), "s0.p0.x1")
        self.assertIsNone(index.locate(0, 0, path=[{"kind": "textBox", "control": 9, "paragraph": 0}]))
        self.assertEqual(index.locate(0, 1), "s0.p1")

    def test_unreadable_hwpx_is_an_error_not_a_crash(self):
        bad = make_hwpx(Path(self.tmp.name) / "enc.hwpx", header="\x00암호화된 바이트")
        result = docmodel.build_from_hwpx(bad)
        self.assertIn("_error", result)
        broken = Path(self.tmp.name) / "broken.hwpx"
        broken.write_bytes(b"not a zip")
        self.assertIn("_error", docmodel.build_from_hwpx(broken))


if __name__ == "__main__":
    unittest.main()
