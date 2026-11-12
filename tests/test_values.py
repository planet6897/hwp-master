"""의미 값 확장(values): 기간·증감률·연락처·조문 참조."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rhwp_locate  # noqa: E402
import values  # noqa: E402


def found(finder, text):
    return [(i["raw"], i["normalized"]) for i in finder(text)]


class PeriodTest(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(found(values.find_periods, "기간은 2026. 3. 1.~12. 31.이다"),
                         [("2026. 3. 1.~12. 31.", {"start": "2026-03-01", "end": "2026-12-31"})])
        self.assertEqual(found(values.find_periods, "2026년 3월~12월 운영"),
                         [("2026년 3월~12월", {"start": "2026-03", "end": "2026-12"})])
        self.assertEqual(found(values.find_periods, "’22~’26년 개편"), [("’22~’26년", {"start": "2022", "end": "2026"})])
        self.assertEqual(found(values.find_periods, "‘22. 11.~’23. 1."),
                         [("‘22. 11.~’23. 1.", {"start": "2022-11", "end": "2023-01"})])

    def test_not_periods(self):
        for text in ["2024. 6. 17.(월) 09:00 배포", "3. 12. 회의", "1~5월 누계", "12. 31.~3. 1. 역순"]:
            self.assertEqual(found(values.find_periods, text), [], text)


class ChangeContactReferenceTest(unittest.TestCase):
    def test_changes(self):
        self.assertEqual(found(values.find_changes, "부품(△6.7%) 감소"), [("△6.7%", -6.7)])
        self.assertEqual(found(values.find_changes, "11.5% 증가, 2.0% 감소, 0.3%p 하락"),
                         [("11.5% 증가", 11.5), ("2.0% 감소", -2.0), ("0.3%p 하락", -0.3)])

    def test_contacts(self):
        got = found(values.find_contacts, "(042-481-7760) 문의 1588-0000, Help@Korea.go.kr")
        self.assertEqual([g[1] for g in got], [{"type": "phone", "value": "042-481-7760"},
                                               {"type": "phone", "value": "1588-0000"},
                                               {"type": "email", "value": "help@korea.go.kr"}])
        self.assertEqual(found(values.find_contacts, "2024-06-17 와 52,054"), [])

    def test_references(self):
        refs = [g[1] for g in found(values.find_references, "관세법 제222조 제1항 제7호, 관세법 시행령 제231조 제1항")]
        self.assertEqual(refs, [{"law": "관세법", "article": "제222조", "paragraph": "제1항", "item": "제7호"},
                                {"law": "관세법 시행령", "article": "제231조", "paragraph": "제1항", "item": None}])
        self.assertEqual(found(values.find_references, "「국어기본법」 시행령 제5조의2")[0][1]["law"], "국어기본법 시행령")
        self.assertEqual(found(values.find_references, "제14조의2")[0][1]["article"], "제14조의2")
        self.assertEqual(found(values.find_references, "관세법 부칙(’19.12.31) 제10조(경과조치)")[0][1]["law"], "관세법 부칙")
        self.assertEqual(found(values.find_references, "「국어기본법」 부칙 제2조")[0][1]["law"], "국어기본법 부칙")


class ExtractValuesTest(unittest.TestCase):
    def test_values_in_data_json(self):
        rhwp = rhwp_locate.locate(None)["path"]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            for sub in ("sources", "exports", "validation"):
                (project / sub).mkdir(parents=True)
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"version": "1", "title": "공고", "blocks": [
                {"type": "paragraph", "text": "접수 기간: 2026. 3. 1.~3. 31. (관세법 제222조 제1항)"},
                {"type": "table", "rows": [["항목", "증감"], ["수출", "△6.7%"]]},
                {"type": "paragraph", "text": "문의: 042-481-7760"},
            ]}, ensure_ascii=False), encoding="utf-8")
            doc = project / "sources/v.hwpx"
            subprocess.run([rhwp, "scaffold", str(spec), "-o", str(doc)], check=True, capture_output=True)
            proc = subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(project), "sources/v.hwpx",
                                   "--rhwp", rhwp, "--only", "document,data"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((project / "exports/extract/v/data.json").read_text(encoding="utf-8"))
            kinds = {i["kind"]: i for i in data["items"] if i.get("detector") == "hwp-master"}
            self.assertEqual(kinds["period"]["normalized"], {"start": "2026-03-01", "end": "2026-03-31"})
            self.assertEqual(kinds["reference"]["normalized"]["article"], "제222조")
            self.assertEqual(kinds["change"]["normalized"], -6.7)
            self.assertRegex(kinds["change"]["blockId"], r"\.t0\.r1c1$", "표 칸 값은 칸 ID")
            self.assertEqual(kinds["contact"]["normalized"]["value"], "042-481-7760")
            report = json.loads(Path(json.loads(proc.stdout.strip().splitlines()[-1])["report"]).read_text("utf-8"))
            self.assertIn("W306", [f["code"] for f in report["findings"]])
            csv_text = (project / "exports/extract/v/data.csv").read_text(encoding="utf-8-sig")
            self.assertIn('{""start"": ""2026-03-01""', csv_text, "기간은 CSV 에 JSON 으로")


if __name__ == "__main__":
    unittest.main()
