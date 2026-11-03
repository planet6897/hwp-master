"""rhwp 바이너리 설치 스크립트: python3 -m unittest tests.test_install_rhwp

네트워크 없이 고정 기록(rhwp_release.json)·플랫폼 판정·압축 풀기 안전성·SHA-256 대조를 본다.
"""
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/hwp-master/scripts"))
import install_rhwp  # noqa: E402


def tar_with(name: str, data: bytes = b"x") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class LockTest(unittest.TestCase):
    def test_lock_pins_commit_and_three_platforms(self):
        lock = install_rhwp.load_lock()
        self.assertRegex(lock["rhwp_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(lock["tag"].startswith(f"rhwp-{lock['rhwp_version']}-{lock['rhwp_commit'][:7]}"))
        for suffix in ("macos-aarch64", "linux-x86_64", "windows-x86_64"):
            asset = next(a for a in lock["assets"] if suffix in a)
            self.assertRegex(lock["assets"][asset], r"^[0-9a-f]{64}$")

    def test_platform_suffix(self):
        cases = [("darwin", "arm64", "macos-aarch64"), ("linux", "x86_64", "linux-x86_64"),
                 ("win32", "AMD64", "windows-x86_64"), ("darwin", "x86_64", None), ("linux", "aarch64", None)]
        for plat, machine, want in cases:
            with self.subTest(plat=plat, machine=machine), mock.patch.object(install_rhwp.sys, "platform", plat), \
                    mock.patch.object(install_rhwp.platform, "machine", return_value=machine):
                self.assertEqual(install_rhwp.platform_suffix(), want)


class ExtractTest(unittest.TestCase):
    def test_rejects_paths_outside_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar.gz"
            archive.write_bytes(tar_with("../escape"))
            with self.assertRaises(RuntimeError):
                install_rhwp._safe_extract(archive, Path(tmp) / "out")
            self.assertFalse((Path(tmp) / "escape").exists())

    def test_checksum_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = {"repo": "o/r", "tag": "rhwp-0.0.0-abcdef0", "rhwp_commit": "a" * 40,
                    "assets": {"rhwp-x-macos-aarch64.tar.gz": "0" * 64}}

            def fake_download(_lock, _asset, dest):
                dest.write_bytes(tar_with("rhwp/rhwp"))
                return "test"

            with mock.patch.object(install_rhwp, "load_lock", return_value=lock), \
                    mock.patch.object(install_rhwp, "platform_suffix", return_value="macos-aarch64"), \
                    mock.patch.object(install_rhwp, "_download", side_effect=fake_download):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    install_rhwp.install(Path(tmp) / "dest")
            self.assertFalse((Path(tmp) / "dest").exists(), "검증 전에는 설치 폴더를 만들지 않는다")


if __name__ == "__main__":
    unittest.main()
