"""추출 품질 평가(tools/eval_extract.py): 점수 계산과, 픽스처 정답 대비 기준 이하로 떨어지지 않는지(회귀 방지)."""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "skills" / "hwp-master" / "scripts"))

import eval_extract  # noqa: E402
import rhwp_locate  # noqa: E402


class ScoreTest(unittest.TestCase):
    def test_precision_recall(self):
        pred = {"values": Counter({"a": 2, "b": 1})}
        gold = {"values": Counter({"a": 1, "c": 1})}
        m = eval_extract.score(pred, gold, ["values"])["values"]
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (1, 2, 1))
        self.assertEqual((m["precision"], m["recall"]), (round(1 / 3, 4), 0.5))
        self.assertEqual(m["misses"], ["c"])

    def test_baseline_drop(self):
        result = {"totals": {"values": {"precision": 0.9, "recall": 1.0}}}
        self.assertEqual(eval_extract.compare_baseline(result, {"totals": {"values": {"precision": 0.95, "recall": 1.0}}}),
                         ["values precision 0.9 < 기준 0.95"])
        self.assertEqual(eval_extract.compare_baseline(result, {"totals": {"values": {"precision": 0.9, "recall": 1.0}}}), [])


class FixtureEvalTest(unittest.TestCase):
    def test_no_regression_against_baseline(self):
        result = eval_extract.run(rhwp_locate.locate(None)["path"])
        self.assertEqual({c["name"] for c in result["cases"]}, {"report", "nested", "layout"})
        self.assertFalse([e for c in result["cases"] for e in c["errors"]])
        baseline = json.loads((eval_extract.FIXTURES / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(eval_extract.compare_baseline(result, baseline), [],
                         json.dumps({c["name"]: {k: (v["misses"], v["falsePositives"]) for k, v in c["categories"].items()}
                                     for c in result["cases"]}, ensure_ascii=False))
        self.assertEqual(set(result["totals"]), set(eval_extract.CATEGORIES))


if __name__ == "__main__":
    unittest.main()
