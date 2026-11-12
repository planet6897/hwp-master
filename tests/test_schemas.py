"""Extract 결과 스키마: 검사기 동작과, 픽스처 추출 결과가 모두 스키마를 지키는지."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rhwp_locate  # noqa: E402
import schema_check  # noqa: E402
from form_fixture import make_form  # noqa: E402
from nested_fixture import make_layout, make_nested  # noqa: E402


class ValidatorTest(unittest.TestCase):
    SCHEMA = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "integer", "minimum": 0}, "b": {"enum": ["x", "y"]}, "c": {"type": "string", "pattern": "^s\d"},
        "d": {"type": "array", "items": {"$ref": "#/$defs/n"}, "minItems": 1}},
        "$defs": {"n": {"type": "object", "required": ["k"], "properties": {"k": {"type": ["string", "null"]}}}}}

    def test_valid(self):
        self.assertEqual(schema_check.validate({"a": 1, "b": "x", "c": "s0", "d": [{"k": None}]}, self.SCHEMA), [])

    def test_errors(self):
        errors = schema_check.validate({"a": -1, "b": "z", "c": "p0", "d": [{}]}, self.SCHEMA)
        self.assertEqual(len(errors), 4, errors)
        self.assertTrue(any("필수 키 'a'" in e for e in schema_check.validate({}, self.SCHEMA)))
        self.assertTrue(schema_check.validate({"a": True}, self.SCHEMA), "bool 은 integer 가 아니다")
        self.assertTrue(schema_check.validate({"a": 1, "d": []}, self.SCHEMA), "minItems")

    def test_every_schema_file_loads(self):
        names = sorted(p.name.split(".")[0] for p in schema_check.SCHEMAS.glob("*.schema.json"))
        self.assertEqual(set(names), set(schema_check.FILES.values()) | {"chunk"})


class OutputsTest(unittest.TestCase):
    def test_fixture_outputs_match_schemas(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "exports", "validation"):
                (project / sub).mkdir(parents=True)
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"version": "1", "title": "스키마", "blocks": [
                {"type": "heading", "level": 1, "text": "개요"},
                {"type": "paragraph", "text": "□ 기간 2026. 3. 1.~12. 31., 580억 달러, 담당자: 홍길동"},
                {"type": "paragraph", "text": "(단위: 백만 원, %)"},
                {"type": "table", "rows": [["구분", "2025년", "증감률"], ["매출", "1,200", "△3.5"]]},
            ]}, ensure_ascii=False), encoding="utf-8")
            subprocess.run([rhwp, "scaffold", str(spec), "-o", str(project / "sources/doc.hwpx")], check=True,
                           capture_output=True)
            make_nested(rhwp, project / "sources/nest.hwpx")
            make_layout(rhwp, project / "sources/layout.hwpx")
            make_form(rhwp, project / "sources/form.hwpx", ["성명", "연락처"])
            proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(project), "sources", "--rhwp", rhwp],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for name in ("doc", "nest", "layout", "form"):
                out = project / "exports/extract" / name
                self.assertEqual(schema_check.check_dir(out), [], name)
                for path in out.glob("*.json"):
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schemaVersion"], "1", path.name)
                self.assertTrue((out / "chunks.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
