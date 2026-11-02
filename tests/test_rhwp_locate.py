"""rhwp_locate 의 한글 글꼴 확인 (rhwp 바이너리 불필요)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"))
import rhwp_locate  # noqa: E402


class KoreanFontStatusTest(unittest.TestCase):
    def _status(self, files, platform="linux"):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as extra:
            for name in files:
                p = Path(extra) / "sub" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"")
            env = {k: v for k, v in os.environ.items() if k != "RHWP_FONT_PATH"}
            with mock.patch.object(sys, "platform", platform), \
                    mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(Path, "home", return_value=Path(home)), \
                    mock.patch.object(rhwp_locate, "_SYSTEM_FONT_DIRS", ()):
                return rhwp_locate.korean_font_status([extra])

    def test_noto_cjk_found(self):
        st = self._status(["NotoSansCJK-Regular.ttc", "DejaVuSans.ttf"])
        self.assertTrue(st["ok"])
        self.assertTrue(st["example"].endswith("NotoSansCJK-Regular.ttc"))

    def test_nanum_and_hamchorom_found(self):
        self.assertTrue(self._status(["NanumGothic.ttf"])["ok"])
        self.assertTrue(self._status(["HANBatang.ttf", "HCRBatang.ttf"])["ok"])

    def test_latin_only_warns(self):
        st = self._status(["DejaVuSans.ttf", "LiberationSerif-Regular.ttf", "readme-kr.txt"])
        self.assertFalse(st["ok"])
        self.assertIn("fonts-noto-cjk", st["hint"])

    def test_non_linux_skips_check(self):
        st = self._status([], platform="darwin")
        self.assertTrue(st["ok"])
        self.assertFalse(st["checked"])


if __name__ == "__main__":
    unittest.main()
