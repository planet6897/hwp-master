"""Template Fill 라우트 종단 테스트: python3 -m unittest tests.test_template_fill (rhwp 필요)"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hwp-master/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhwp_locate  # noqa: E402
from form_fixture import make_form  # noqa: E402

FORM = [
    {"type": "paragraph", "text": "작성일: {{date}}  작성자: {{author}}"},
    {"type": "heading", "level": 1, "text": "추진 과제"},
    {"type": "paragraph", "text": "□ {{tasks.name}}"},
    {"type": "paragraph", "text": "  ○ 내용: {{tasks.detail}}"},
    {"type": "heading", "level": 1, "text": "예산"},
    {"type": "table", "rows": [["항목", "금액"], ["{{budget.item}}", "{{budget.amount}}"], ["합계", "{{total}}"]]},
    {"type": "paragraph", "text": "문의: {{author}}"},
]
DATA = {"title": "2027년 추진 계획", "date": "2027. 1. 5.", "author": "홍길동",
        "tasks": [{"name": "첫째 과제", "detail": "첫째 내용"}, {"name": "둘째 과제", "detail": "둘째 내용"},
                  {"name": "셋째 과제", "detail": "셋째 내용"}],
        "budget": [{"item": "인건비", "amount": "1,000"}, {"item": "운영비", "amount": "500"}],
        "total": "1,500"}


class TemplateFillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rhwp = rhwp_locate.locate(None)["path"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        (self.project / "sources").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def make_template(self, blocks=FORM, title="{{title}}", name="form.hwpx") -> Path:
        spec = Path(self.tmp.name) / "spec.json"
        spec.write_text(json.dumps({"version": "1", "title": title, "blocks": blocks}, ensure_ascii=False),
                        encoding="utf-8")
        out = self.project / "sources" / name
        subprocess.run([self.rhwp, "scaffold", str(spec), "-o", str(out)], check=True, capture_output=True)
        return out

    def write_data(self, data, name="data.json") -> str:
        (self.project / "sources" / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return f"sources/{name}"

    def run_script(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "template_fill.py"), *args, "--rhwp", self.rhwp],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        return proc, json.loads(proc.stdout.strip().splitlines()[-1])

    def fill(self, data, template="sources/form.hwpx", *extra):
        args = ["fill", str(self.project)] + ([template] if template else []) + ["--data", self.write_data(data), *extra]
        proc, summary = self.run_script(*args)
        report = json.loads((self.project / summary["report"]).read_text(encoding="utf-8"))
        return proc, summary, report

    def text(self, path: Path) -> str:
        out = Path(self.tmp.name) / f"txt_{path.stem}"
        subprocess.run([self.rhwp, "export-text", str(path), "-o", str(out)], check=True, capture_output=True)
        return "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("*.txt")))

    def codes(self, report):
        return {f["code"] for f in report["findings"]}

    # ------------------------------------------------------------ 정상 경로

    def test_scalars_paragraph_list_and_table_rows_are_filled_in_order(self):
        self.make_template()
        proc, summary, report = self.fill(DATA)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(report["ok"], report["findings"])
        out = self.project / summary["output"]
        lines = [l for l in self.text(out).splitlines() if l.strip()]
        self.assertEqual(lines[:2], ["2027년 추진 계획", "작성일: 2027. 1. 5.  작성자: 홍길동"])
        tasks = [l for l in lines if l.startswith("□") or l.strip().startswith("○")]
        self.assertEqual(tasks, ["□ 첫째 과제", "  ○ 내용: 첫째 내용", "□ 둘째 과제", "  ○ 내용: 둘째 내용",
                                 "□ 셋째 과제", "  ○ 내용: 셋째 내용"])
        self.assertEqual(lines[-1], "문의: 홍길동")  # 같은 키 두 곳
        tables = json.loads(subprocess.run([self.rhwp, "export-tables", str(out), "--json"], check=True,
                                           capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)["tables"]
        cells = sorted(((c["row"], c["col"]), c["text"]) for c in tables[0]["cells"])
        self.assertEqual([t for _, t in cells], ["항목", "금액", "인건비", "1,000", "운영비", "500", "합계", "1,500"])
        self.assertNotIn("{{", self.text(out))
        # 목록 둘 → 반복 단계 둘 + 채움 단계 하나, 캡슐 사슬이 끊기지 않는다
        self.assertEqual([s["label"] for s in report["stages"]], ["repeat_budget", "repeat_tasks", "fill"])
        head = self.project / report["stages"][-1]["capsule"]
        lin = json.loads(subprocess.run([self.rhwp, "lineage", str(head), "--json"], cwd=self.project,
                                        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)
        self.assertEqual((lin["depth"], lin["brokenAt"]), (3, None))
        self.assertEqual(report["layout"]["output"]["overlapCount"], 0)
        # 서식 원본은 그대로다
        self.assertEqual(report["templateSha256"],
                         hashlib.sha256((self.project / "sources/form.hwpx").read_bytes()).hexdigest())

    def test_single_record_list_needs_no_repeat_stage(self):
        self.make_template()
        data = dict(DATA, tasks=DATA["tasks"][:1], budget=DATA["budget"][:1])
        proc, summary, report = self.fill(data)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual([s["label"] for s in report["stages"]], ["fill"])
        self.assertIn("□ 첫째 과제", self.text(self.project / summary["output"]))

    def test_replaced_value_keeps_marker_character_format(self):
        src = self.make_template([{"type": "paragraph", "text": "기관: {{org}}"}], title="제목")
        bold = self.project / "sources/bold.hwpx"
        subprocess.run([self.rhwp, "edit", "apply-char-format", str(src), "--section", "0", "--para", "1",
                        "--offset", "4", "--count", "7", "--props", '{"bold":true,"textColor":"#C00000"}',
                        "-o", str(bold)], check=True, capture_output=True)
        proc, summary, report = self.fill({"org": "한국문서진흥원"}, "sources/bold.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stdout)

        def runs(path):
            xml = zipfile.ZipFile(path).read("Contents/section0.xml").decode("utf-8")
            para = next(p for p in re.findall(r"<hp:p .*?</hp:p>", xml, re.S) if "기관" in p)
            return re.findall(r'charPrIDRef="(\d+)"[^>]*>\s*<hp:t>([^<]*)', para)

        before, after = runs(bold), runs(self.project / summary["output"])
        self.assertEqual(before[1][1], "{{org}}")
        self.assertEqual(after, [before[0], (before[1][0], "한국문서진흥원")])

    def test_click_here_fields_and_markers_fill_together(self):
        make_form(self.rhwp, self.project / "sources/fields.hwpx", ["성명", "소속"])
        # 누름틀 서식의 제목을 자리표시로 바꿔 두 종류의 칸을 한 서식에 둔다
        plan = self.project / "mk.plan.json"
        plan.write_text(json.dumps({"planVersion": "1.0", "input": "sources/fields.hwpx",
                                    "output": "sources/mixed.hwpx",
                                    "steps": [{"action": "replace_text", "find": "참가 신청서",
                                               "replace": "{{행사}} 참가 신청서"}]}, ensure_ascii=False),
                        encoding="utf-8")
        subprocess.run([self.rhwp, "run", str(plan)], cwd=self.project, check=True, capture_output=True)
        proc, summary, report = self.fill({"행사": "2027 정책 포럼", "성명": "김철수", "소속": "기획팀"},
                                          "sources/mixed.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual((report["inventory"]["scalars"], report["inventory"]["fields"]), (["행사"], ["성명", "소속"]))
        lines = [l for l in self.text(self.project / summary["output"]).splitlines() if l.strip()]
        self.assertEqual(lines[0], "2027 정책 포럼 참가 신청서")
        self.assertIn("성명: 김철수", lines)
        self.assertIn("소속: 기획팀", lines)
        self.assertIn("T112", self.codes(self.fill({"행사": "포럼", "성명": "김철수"}, "sources/mixed.hwpx")[2]))

    def test_register_then_fill_by_house_name_and_detect_tampering(self):
        src = self.make_template(name="기관보고서.hwpx")
        house = Path(self.tmp.name) / "house"
        proc = subprocess.run([sys.executable, str(SCRIPTS / "template_fill.py"), "register", str(src),
                               "--name", "기관보고서", "--templates-dir", str(house), "--rhwp", self.rhwp],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lock = json.loads((house / "기관보고서/template.hwpx.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(l["name"] for l in lock["lists"]), ["budget", "tasks"])
        example = json.loads((house / "기관보고서/data.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example), {"author", "date", "title", "total", "tasks", "budget"})
        proc, summary, report = self.fill(DATA, None, "--house", "기관보고서", "--templates-dir", str(house))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(report["template"], "sources/기관보고서.hwpx")
        # 들여온 서식이 바뀌면 지문 잠금이 막는다
        shutil.copyfile(self.make_template(FORM[:1], name="other.hwpx"), self.project / "sources/기관보고서.hwpx")
        proc, summary, report = self.fill(DATA, "sources/기관보고서.hwpx")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("T003", self.codes(report))

    # ------------------------------------------------------------ 차단

    def test_data_problems_block_before_any_edit(self):
        self.make_template()
        cases = [
            ({k: v for k, v in DATA.items() if k != "total"}, "T101"),
            (dict(DATA, tasks=[]), "T105"),
            (dict(DATA, date="2027.\n1. 5."), "T103"),
            (dict(DATA, author="{{date}}"), "T104"),
            (dict(DATA, budget=[{"item": "인건비"}]), "T101"),
        ]
        for data, code in cases:
            with self.subTest(code=code):
                proc, summary, report = self.fill(data)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn(code, self.codes(report))
                self.assertIsNone(summary["output"])
                self.assertEqual(report["stages"], [])
        self.assertEqual(list((self.project / "exports").glob("*")), [])

    def test_extra_keys_only_warn(self):
        self.make_template()
        proc, summary, report = self.fill(dict(DATA, memo="쓰지 않음"))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("T111", self.codes(report))

    def test_template_structure_problems_are_reported_by_inspect(self):
        cases = [
            ([{"type": "paragraph", "text": "값 {{a b}}"}], "T001"),
            ([{"type": "paragraph", "text": "{{rows.x}}"},
              {"type": "table", "rows": [["{{rows.y}}"]]}], "T002"),
            ([{"type": "paragraph", "text": "{{rows.x}} {{rows.x}}"}], "T002"),
            ([{"type": "paragraph", "text": "{{a}}"}, {"type": "paragraph", "text": "{{a.b}}"}], "T002"),
        ]
        for blocks, code in cases:
            with self.subTest(code=code, blocks=blocks):
                self.make_template(blocks, title="제목", name="bad.hwpx")
                proc, summary = self.run_script("inspect", str(self.project), "sources/bad.hwpx")
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn(f"[오류 {code}]", proc.stdout)

    def test_inspect_writes_inventory_and_example_data(self):
        self.make_template()
        proc, summary = self.run_script("inspect", str(self.project), "sources/form.hwpx")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        inv = summary["inventory"]
        self.assertEqual(inv["scalars"], ["author", "date", "title", "total"])
        self.assertEqual({(l["name"], l["kind"]) for l in inv["lists"]},
                         {("tasks", "paragraphs"), ("budget", "table_rows")})
        example = json.loads((self.project / summary["example"]).read_text(encoding="utf-8"))
        self.assertEqual(example["tasks"], [{"detail": "<tasks.detail>", "name": "<tasks.name>"}])


if __name__ == "__main__":
    unittest.main()
