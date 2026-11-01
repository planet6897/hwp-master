"""Mail-merge 라우트 종단 테스트: python3 -m unittest tests.test_merge (rhwp 필요)"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhwp_locate  # noqa: E402
from form_fixture import make_form  # noqa: E402

FIELDS = ["성명", "소속", "연락처"]


class MergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        (self.project / "sources").mkdir(parents=True)
        make_form(self.rhwp, self.project / "sources/form.hwpx", FIELDS)

    def tearDown(self):
        self.tmp.cleanup()

    def merge(self, csv_text: str, *extra):
        (self.project / "sources/list.csv").write_text(csv_text, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(SCRIPTS / "merge.py"), str(self.project),
                               "--form", "sources/form.hwpx", "--data", "sources/list.csv", "--rhwp", self.rhwp,
                               *extra], capture_output=True, text=True, timeout=900)
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        return proc, report

    def outputs(self):
        return sorted(p.name for p in (self.project / "exports").rglob("*.hwpx"))

    def test_each_row_becomes_a_filled_document(self):
        proc, report = self.merge("성명,소속,연락처\n홍길동,기획팀,010-1111-2222\n김철수,총무팀,010-3333-4444\n",
                                  "--name-field", "성명")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(report["ok"])
        self.assertEqual((report["rows"], report["records"], report["succeeded"]), (2, 2, 2))
        self.assertEqual(self.outputs(), ["김철수.hwpx", "홍길동.hwpx"])
        out = next((self.project / "exports").rglob("홍길동.hwpx"))
        fields = json.loads(subprocess.run([self.rhwp, "fields", str(out), "--json"], capture_output=True,
                                           text=True).stdout)["fields"]
        self.assertEqual({f["name"]: f["value"] for f in fields},
                         {"성명": "홍길동", "소속": "기획팀", "연락처": "010-1111-2222"})
        self.assertNotIn("010-", json.dumps(report, ensure_ascii=False), "요약에는 명단 값을 넣지 않는다")

    def test_column_missing_from_form_stops_before_filling(self):
        proc, report = self.merge("성명,직급\n홍길동,과장\n")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual([f["code"] for f in report["findings"] if f["severity"] == "error"], ["M001"])
        self.assertEqual(self.outputs(), [])

    def test_bad_row_fails_the_dry_run_and_nothing_is_written(self):
        proc, report = self.merge("성명,소속,연락처\n홍길동,기획팀,010-1111-2222\n김철수\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("M003", [f["code"] for f in report["findings"]])
        self.assertEqual([e["row"] for e in report["dryRunErrors"]], [1])
        self.assertEqual(self.outputs(), [])

    def test_unfilled_form_field_is_only_a_warning(self):
        proc, report = self.merge("성명,소속\n홍길동,기획팀\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual([f["code"] for f in report["findings"]], ["M101"])
        self.assertEqual(self.outputs(), ["0001.hwpx"])


if __name__ == "__main__":
    unittest.main()
