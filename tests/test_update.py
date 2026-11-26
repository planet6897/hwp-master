"""업데이트 확인·받기 스크립트: python3 -m unittest tests.test_update

네트워크 없이 판정·알림 문구·확인 기록(하루 한 번)·받기 전 안전 확인을 본다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/hwp-master/scripts"))
import rhwp_locate  # noqa: E402
import update  # noqa: E402

REMOTE = {"version": "0.2.0", "tag": "rhwp-0.9.1-bbbbbbb", "rhwpVersion": "0.9.1"}


class CompareTest(unittest.TestCase):
    def test_same_is_not_an_update(self):
        st = update.compare("0.2.0", "rhwp-0.9.1-bbbbbbb", REMOTE, True)
        self.assertTrue(st["checked"])
        self.assertFalse(st["available"])
        self.assertIsNone(update.notice(st))

    def test_newer_project_and_binary(self):
        st = update.compare("0.1.17", "rhwp-0.9.1-aaaaaaa", REMOTE, True)
        self.assertEqual(st["project"], {"from": "0.1.17", "to": "0.2.0"})
        self.assertEqual(st["rhwp"], {"from": "rhwp-0.9.1-aaaaaaa", "to": "rhwp-0.9.1-bbbbbbb"})
        text = update.notice(st)
        self.assertIn("hwp-master 0.1.17 → 0.2.0", text)
        self.assertIn("rhwp-0.9.1-bbbbbbb", text)
        self.assertIn("update.py --apply", text)

    def test_binary_only(self):
        st = update.compare("0.2.0", "rhwp-0.9.1-aaaaaaa", REMOTE, True)
        self.assertIsNone(st["project"])
        self.assertTrue(st["available"])
        self.assertNotIn("hwp-master 0", update.notice(st))

    def test_local_ahead_of_main_is_not_an_update(self):
        st = update.compare("0.3.0", "rhwp-0.9.1-bbbbbbb", REMOTE, True)
        self.assertFalse(st["available"])

    def test_versions_compare_as_numbers(self):
        st = update.compare("0.1.9", "rhwp-0.9.1-bbbbbbb", {**REMOTE, "version": "0.1.10"}, True)
        self.assertEqual(st["project"]["to"], "0.1.10")

    def test_unreachable(self):
        st = update.compare("0.1.17", "rhwp-0.9.1-aaaaaaa", None, False)
        self.assertFalse(st["checked"])
        self.assertFalse(st["available"])
        self.assertTrue(st["binaryMissing"])
        self.assertIsNone(update.notice(st))

    def test_local_version_matches_plugin_and_skill(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(update.local_version(), plugin)
        with mock.patch.object(update, "PLUGIN", ROOT / "no-such-file.json"):
            self.assertEqual(update.local_version(), plugin)  # SKILL.md 의 metadata.version


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(update, "cache_path", return_value=Path(self.tmp.name) / "c" / "update_check.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_checks_once_within_ttl(self):
        with mock.patch.object(update, "fetch_remote", return_value=REMOTE) as fetch:
            self.assertEqual(update.remote_state("o/r", 100, now=1000), REMOTE)
            self.assertEqual(update.remote_state("o/r", 100, now=1050), REMOTE)
            self.assertEqual(fetch.call_count, 1)
            update.remote_state("o/r", 100, now=1200)   # 기한이 지남
            update.remote_state("o/r", None, now=1201)  # 직접 실행은 늘 새로 확인
            update.remote_state("other/r", 100, now=1202)  # 다른 저장소의 기록은 쓰지 않음
            self.assertEqual(fetch.call_count, 4)

    def test_network_failure_is_cached_and_silent(self):
        with mock.patch.object(update, "fetch_remote", side_effect=OSError("offline")) as fetch:
            self.assertIsNone(update.remote_state("o/r", 100, now=1000))
            self.assertIsNone(update.remote_state("o/r", 100, now=1010))
            self.assertEqual(fetch.call_count, 1)

    def test_broken_cache_file_is_ignored(self):
        path = update.cache_path()
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        with mock.patch.object(update, "fetch_remote", return_value=REMOTE):
            self.assertEqual(update.remote_state("o/r", 100, now=1000), REMOTE)


class QuietNoticeTest(unittest.TestCase):
    def _env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("CI", "HWP_MASTER_NO_UPDATE_CHECK")}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_disabled_by_env_without_network(self):
        for name in ("CI", "HWP_MASTER_NO_UPDATE_CHECK"):
            with self._env(**{name: "1"}), mock.patch.object(update, "check") as check:
                self.assertIsNone(update.quiet_notice())
                check.assert_not_called()

    def test_uses_daily_cache_and_never_raises(self):
        status = update.compare("0.1.17", "rhwp-0.9.1-aaaaaaa", REMOTE, True)
        with self._env(), mock.patch.object(update, "check", return_value=status) as check:
            self.assertIn("업데이트가 있습니다", update.quiet_notice())
            self.assertEqual(check.call_args.kwargs["max_age"], update.CACHE_TTL)
        with self._env(), mock.patch.object(update, "check", side_effect=RuntimeError("boom")):
            self.assertIsNone(update.quiet_notice())

    def test_rhwp_locate_passes_notice_through(self):
        with mock.patch.object(update, "quiet_notice", return_value="업데이트가 있습니다: x"):
            self.assertEqual(rhwp_locate.update_notice(), "업데이트가 있습니다: x")
        with mock.patch.object(update, "quiet_notice", side_effect=RuntimeError("boom")):
            self.assertIsNone(rhwp_locate.update_notice())


class ApplyTest(unittest.TestCase):
    """임시 git 저장소에서 받기 전 확인과 git pull 을 본다 (rhwp 설치는 이미 된 것으로 둔다)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.origin, self.clone = base / "origin", base / "clone"
        self._run(base, "init", "-q", "-b", "main", str(self.origin))
        self._commit(self.origin, "a.txt", "1")
        self._run(base, "clone", "-q", str(self.origin), str(self.clone))
        installer = mock.Mock()
        installer.installed_binary.return_value = Path("/installed/rhwp")
        for target, value in (("ROOT", self.clone), ("_install_rhwp", lambda: installer), ("_write_cache", lambda data: None),
                              ("load_lock", lambda: {"tag": "rhwp-0.9.1-aaaaaaa", "repo": "o/r"})):
            patcher = mock.patch.object(update, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        *args], cwd=cwd, check=True, capture_output=True)

    def _commit(self, repo, name, text):
        (repo / name).write_text(text, encoding="utf-8")
        self._run(repo, "add", name)
        self._run(repo, "commit", "-q", "-m", name)

    def test_pulls_new_commits(self):
        self._commit(self.origin, "b.txt", "2")
        result = update.apply()
        self.assertTrue(result["steps"][0]["changed"])
        self.assertTrue((self.clone / "b.txt").is_file())
        self.assertFalse(result["steps"][1]["changed"])
        self.assertFalse(update.apply()["steps"][0]["changed"])  # 다시 하면 이미 최신

    def test_refuses_with_uncommitted_changes(self):
        self._commit(self.origin, "b.txt", "2")
        (self.clone / "a.txt").write_text("edited", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "커밋하지 않은 변경"):
            update.apply()
        self.assertFalse((self.clone / "b.txt").exists())
        self.assertEqual((self.clone / "a.txt").read_text(encoding="utf-8"), "edited")

    def test_refuses_on_other_branch(self):
        self._run(self.clone, "switch", "-q", "-c", "work")
        with self.assertRaisesRegex(RuntimeError, "브랜치"):
            update.apply()

    def test_refuses_without_git(self):
        with tempfile.TemporaryDirectory() as plain, mock.patch.object(update, "ROOT", Path(plain)):
            with self.assertRaisesRegex(RuntimeError, "git 으로 받은 폴더가 아니"):
                update.apply()


if __name__ == "__main__":
    unittest.main()
