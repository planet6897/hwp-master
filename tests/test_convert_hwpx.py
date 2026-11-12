"""HWP → HWPX 일괄 변환·검증: 글·구조 비교, 폴더 변환, 건너뛰기·덮어쓰기, 차이 → 오류."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import convert_hwpx  # noqa: E402
import rhwp_locate  # noqa: E402

SPEC = {"version": "1", "title": "변환 시험", "blocks": [
    {"type": "heading", "level": 1, "text": "개요"},
    {"type": "paragraph", "text": "총사업비는 1,200,000원이다."},
    {"type": "table", "rows": [["구분", "금액"], ["인건비", "800,000원"]]},
]}


def model(texts, tables=0):
    blocks = [{"id": f"s0.p{i}", "type": "paragraph", "text": t} for i, t in enumerate(texts)]
    return {"blocks": blocks, "counts": {"paragraph": len(texts), "table": tables}}


class CompareTest(unittest.TestCase):
    def test_same_ignores_whitespace(self):
        self.assertTrue(convert_hwpx.compare(model(["가 나", "다"]), model(["가나", "다 "]))["textSame"])

    def test_text_and_count_differences(self):
        result = convert_hwpx.compare(model(["가", "나"]), model(["가", "바뀜"], tables=1))
        self.assertFalse(result["textSame"])
        self.assertEqual(result["firstDiff"]["index"], 1)
        self.assertFalse(result["countsSame"])
        self.assertEqual(result["counts"]["table"], [0, 1])


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.rhwp = rhwp_locate.locate(None)["path"]
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        for sub in ("sources/받은문서/2팀", "validation"):
            (self.project / sub).mkdir(parents=True)
        spec = Path(self.tmp.name) / "spec.json"
        spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
        hwpx = Path(self.tmp.name) / "base.hwpx"
        subprocess.run([self.rhwp, "scaffold", str(spec), "-o", str(hwpx)], check=True, capture_output=True)
        for rel in ("받은문서/a.hwp", "받은문서/b.hwp", "받은문서/2팀/c.hwp"):
            subprocess.run([self.rhwp, "convert", str(hwpx), str(self.project / "sources" / rel)],
                           check=True, capture_output=True)
        (self.project / "sources/받은문서/이미.hwpx").write_bytes(hwpx.read_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def run_convert(self, *extra):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "convert_hwpx.py"), str(self.project),
                               "sources/받은문서", "--rhwp", self.rhwp, *extra], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        return proc, summary, json.loads(Path(summary["report"]).read_text(encoding="utf-8"))

    def test_folder_convert_verify_and_skip(self):
        proc, summary, report = self.run_convert()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((summary["converted"], summary["skipped"], summary["failed"]), (3, 1, 0))
        for rel in ("a.hwpx", "b.hwpx", "2팀/c.hwpx"):
            self.assertTrue((self.project / "exports/hwpx/받은문서" / rel).is_file(), rel)
        ok = [d for d in report["documents"] if d["status"] == "ok"]
        self.assertTrue(all(d["textSame"] and d["countsSame"] and d["diffCount"] == 0 for d in ok))
        self.assertTrue(all(d["pages"][0] == d["pages"][1] for d in ok))
        self.assertTrue(Path(summary["report"]).with_suffix(".csv").is_file())

        proc, summary, _ = self.run_convert()
        self.assertEqual((summary["converted"], summary["skipped"]), (0, 4), "결과가 있으면 덮어쓰지 않는다")
        proc, summary, _ = self.run_convert("--force")
        self.assertEqual(summary["converted"], 3)

    def test_difference_is_error(self):
        real = convert_hwpx.docmodel.build_document

        def tampered(rhwp, path, dump=None):
            doc = real(rhwp, path, dump)
            if str(path).endswith(".hwpx"):
                doc["blocks"][1]["text"] = "누가 바꾼 글"
            return doc

        src = (self.project / "sources/받은문서/a.hwp").resolve()
        with mock.patch.object(convert_hwpx.docmodel, "build_document", side_effect=tampered):
            row = convert_hwpx.convert_one(self.rhwp, self.project.resolve(), src, force=True)
        self.assertEqual(row["status"], "failed")
        self.assertIn("C204", [f["code"] for f in row["findings"]])


if __name__ == "__main__":
    unittest.main()
