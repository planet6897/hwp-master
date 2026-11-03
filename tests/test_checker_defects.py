"""M4 검사기 종단 테스트: 일부러 넣은 결함을 검사기가 모두 잡는지 확인한다.

    python3 -m unittest tests.test_checker_defects

rhwp 바이너리가 필요하다(`RHWP_BIN` 또는 PATH). 없으면 건너뛰지 않고 실패한다.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
import rhwp_locate  # noqa: E402

PARA = ("공공기관 보고서는 대부분 한글 문서로 작성되며 동일한 양식을 반복해서 채우는 작업에 많은 시간이 든다. "
        "개요 번호와 표 서식을 손으로 맞추는 과정에서 오류가 자주 생기고 제출 직전 검토 부담이 크다.")


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def unit(uid, heading, paras=1):
    blocks = [{"type": "heading", "level": 1, "text": heading}]
    blocks += [{"type": "paragraph", "text": PARA} for _ in range(paras)]
    return {"unit": uid, "blocks": blocks}


class CheckerDefectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def make_project(self, tmp: Path, units: dict, max_pages=None) -> Path:
        project = tmp / "proj"
        for sub in ("units", "build", "exports", "validation", "capsules"):
            (project / sub).mkdir(parents=True, exist_ok=True)
        write(project / "project.json", {"name": "검사", "hdsVersion": "1", "sources": []})
        lock = {"title": "검사기 시험 문서"}
        if max_pages is not None:
            lock["max_pages"] = max_pages
        write(project / "spec_lock.json", lock)
        write(project / "outline.json", {"units": [{"id": u} for u in ("U01", "U02", "U03")]})
        for uid, data in units.items():
            write(project / "units" / f"{uid}.json", data)
        return project

    def build(self, project: Path):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "build.py"), str(project), "--no-preview",
                               "--rhwp", self.rhwp], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        report_path = project / "validation" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        return proc, report

    def lint(self, project: Path):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "project_manager.py"), "validate", str(project)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        result = json.loads((project / "validation" / "hds_lint_final.json").read_text(encoding="utf-8"))
        return proc, result

    def test_clean_project_builds_with_reproducible_receipt(self):
        with tempfile.TemporaryDirectory() as t:
            project = self.make_project(Path(t), {
                "U01": unit("U01", "추진 배경"), "U02": unit("U02", "현황"), "U03": unit("U03", "추진 계획")},
                max_pages=1)
            proc, report = self.build(project)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(report["ok"])
            self.assertEqual(report["errorCount"], 0)
            self.assertEqual(report["pageCount"], 1)

            receipts = sorted((project / "capsules").glob("*.receipt.json"))
            self.assertEqual(len(receipts), 1)
            verify = subprocess.run([sys.executable, str(SCRIPTS / "receipt.py"), "verify", str(receipts[0]),
                                     "--rhwp", self.rhwp], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            self.assertTrue(json.loads(verify.stdout)["reproduced"])

            # 영수증 안의 명세를 바꾸면 재현으로 판정하지 않는다.
            rec = json.loads(receipts[0].read_text(encoding="utf-8"))
            rec["spec"] = rec["spec"].replace("추진 배경", "추진 경과")
            tampered = Path(t) / "tampered.receipt.json"
            write(tampered, rec)
            verify = subprocess.run([sys.executable, str(SCRIPTS / "receipt.py"), "verify", str(tampered),
                                     "--rhwp", self.rhwp], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(verify.returncode, 3, verify.stdout + verify.stderr)
            out = json.loads(verify.stdout)
            self.assertFalse(out["specIntact"])
            self.assertFalse(out["reproduced"])

    def test_manual_number_and_missing_section_are_blocking(self):
        with tempfile.TemporaryDirectory() as t:
            project = self.make_project(Path(t), {
                "U01": unit("U01", "추진 배경"), "U02": unit("U02", "2. 현황")})  # U03 누락
            proc, lint = self.lint(project)
            self.assertEqual(proc.returncode, 1)
            found = {(f["code"], f.get("where", "")) for f in lint["findings"]}
            self.assertIn(("E101", "units/U02.json:blocks[0]"), found)
            self.assertIn(("P004", "units/U03.json"), found)

            proc, report = self.build(project)
            self.assertEqual(proc.returncode, 1)
            self.assertFalse(list((project / "exports").glob("*.hwpx")), "검사 오류가 있으면 산출물을 만들지 않는다")

    def test_page_limit_overflow_is_an_error(self):
        with tempfile.TemporaryDirectory() as t:
            project = self.make_project(Path(t), {
                "U01": unit("U01", "추진 배경", 12), "U02": unit("U02", "현황", 12),
                "U03": unit("U03", "추진 계획", 12)}, max_pages=1)
            proc, report = self.build(project)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            codes = [f["code"] for f in report["findings"] if f["severity"] == "error"]
            self.assertEqual(codes, ["R104"], report["findings"])
            self.assertGreater(report["pageCount"], 1)

    def test_invalid_page_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            project = self.make_project(Path(t), {
                "U01": unit("U01", "추진 배경"), "U02": unit("U02", "현황"), "U03": unit("U03", "추진 계획")},
                max_pages=0)
            proc, lint = self.lint(project)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("E008", [f["code"] for f in lint["findings"]])


if __name__ == "__main__":
    unittest.main()
