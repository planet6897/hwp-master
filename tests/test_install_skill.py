"""스킬 연결 스크립트: python3 -m unittest tests.test_install_skill

임시 스킬 폴더에 링크를 만들고·다시 실행해도 그대로이고·남의 폴더는 건드리지 않고·지울 수 있는지 본다.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/hwp-master/scripts"))
import install_skill  # noqa: E402


class InstallSkillTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.skills = Path(self._tmp.name) / "skills"

    def tearDown(self):
        link = self.skills / "hwp-master"
        if install_skill._is_link(link):
            install_skill._remove_link(link)
        self._tmp.cleanup()

    def test_link_points_to_repo_skill(self):
        r = install_skill.install(self.skills)
        self.assertEqual(r["action"], "created")
        link = self.skills / "hwp-master"
        self.assertTrue((link / "SKILL.md").is_file())
        self.assertTrue(install_skill._points_here(link))

    def test_rerun_is_unchanged(self):
        install_skill.install(self.skills)
        self.assertEqual(install_skill.install(self.skills)["action"], "unchanged")

    def test_foreign_folder_is_left_alone(self):
        foreign = self.skills / "hwp-master"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("other", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            install_skill.install(self.skills)
        with self.assertRaises(RuntimeError):
            install_skill.remove(self.skills)
        self.assertEqual((foreign / "SKILL.md").read_text(encoding="utf-8"), "other")

    def test_remove_keeps_repo(self):
        install_skill.install(self.skills)
        self.assertEqual(install_skill.remove(self.skills)["action"], "removed")
        self.assertFalse((self.skills / "hwp-master").exists())
        self.assertTrue((install_skill.SKILL_DIR / "SKILL.md").is_file())
        self.assertEqual(install_skill.remove(self.skills)["action"], "absent")


if __name__ == "__main__":
    unittest.main()
