"""rhwp Release 반영 도구: python3 -m unittest tests.test_update_rhwp_release

네트워크 없이 SHA256SUMS 해석·고정 기록 생성·README 치환을 본다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import update_rhwp_release as upd  # noqa: E402

TAG = "rhwp-0.9.1-abcdef0"
COMMIT = "abcdef0" + "1" * 33
SUMS = "\n".join(f"{c * 64}  {TAG}-{s}.{'zip' if s.startswith('windows') else 'tar.gz'}"
                 for c, s in zip("abcde", upd.SUFFIXES))
OLD = {"repo": "o/r", "tag": "rhwp-0.9.0-1234567", "rhwp_version": "0.9.0", "rhwp_commit": "1234567" + "0" * 33,
       "features": ["native-skia"], "assets": {"old.zip": "0" * 64}}


class UpdateReleaseTest(unittest.TestCase):
    def test_build_lock(self):
        new = upd.build_lock(OLD, TAG, COMMIT, upd.parse_sums(SUMS))
        self.assertEqual(new["rhwp_version"], "0.9.1")
        self.assertEqual(new["features"], ["native-skia"])
        self.assertEqual(new["assets"][f"{TAG}-windows-x86_64.zip"], "e" * 64)
        self.assertEqual(len(new["assets"]), 5)

    def test_rejects_commit_mismatch_and_missing_platform(self):
        with self.assertRaises(SystemExit):
            upd.build_lock(OLD, TAG, "f" * 40, upd.parse_sums(SUMS))
        with self.assertRaises(SystemExit):
            upd.build_lock(OLD, TAG, COMMIT, upd.parse_sums("\n".join(SUMS.splitlines()[1:])))

    def test_readme_tag_and_date(self):
        text = ("rhwp/rhwp-0.9.0-1234567/rhwp\n"
                "| macOS | `rhwp-0.9.0-1234567-macos-aarch64.tar.gz` | CI 빌드 (2026-01-01) |\n")
        out = upd.update_readme(text, OLD["tag"], TAG, "2026-11-12")
        self.assertNotIn("1234567", out)
        self.assertIn(f"`{TAG}-macos-aarch64.tar.gz` | CI 빌드 (2026-11-12) |", out)


if __name__ == "__main__":
    unittest.main()
