"""Claude MCP 연결 설치: Desktop 설정 추가·중복·충돌·제거·dry-run, Claude Code CLI 호출(가짜), mcp-serve 확인.

실제 사용자 설정은 건드리지 않는다 — Desktop 은 임시 파일, Claude Code 는 가짜 runner 로 시험한다.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "hwp-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install_mcp  # noqa: E402
import rhwp_locate  # noqa: E402

RHWP = r"C:\tools\rhwp\rhwp.exe" if sys.platform == "win32" else "/opt/rhwp/rhwp"


class FakeClaude:
    """claude mcp get/add/remove 를 흉내 낸다."""

    def __init__(self, registered=None):
        self.registered = dict(registered or {})
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        sub, name = cmd[2], cmd[3] if cmd[2] == "get" else None
        if sub == "get":
            if name in self.registered:
                out = f"{name}:\n  Scope: User config\n  Type: stdio\n  Command: {self.registered[name]}\n  Args: mcp-serve\n"
                return subprocess.CompletedProcess(cmd, 0, out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "not found")
        if sub == "add":
            self.registered[cmd[5]] = cmd[7]
        if sub == "remove":
            self.registered.pop(cmd[3], None)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")


class ClaudeCodeTest(unittest.TestCase):
    def test_add_already_conflict_remove(self):
        fake = FakeClaude()
        r = install_mcp.claude_code("add", "rhwp", RHWP, False, "claude", fake)
        self.assertEqual(r["status"], "added")
        self.assertEqual(fake.calls[-1], ["claude", "mcp", "add", "--scope", "user", "rhwp", "--", RHWP, "mcp-serve"])
        self.assertEqual(install_mcp.claude_code("add", "rhwp", RHWP, False, "claude", fake)["status"], "already")
        other = FakeClaude({"rhwp": r"D:\rhwp\target\release\rhwp.exe"})
        r = install_mcp.claude_code("add", "rhwp", RHWP, False, "claude", other)
        self.assertEqual(r["status"], "conflict")
        self.assertFalse([c for c in other.calls if c[2] == "add"], "다른 서버는 덮어쓰지 않는다")
        self.assertEqual(install_mcp.claude_code("remove", "rhwp", RHWP, False, "claude", other)["status"], "conflict")
        self.assertEqual(install_mcp.claude_code("remove", "rhwp", RHWP, False, "claude", fake)["status"], "removed")
        self.assertNotIn("rhwp", fake.registered)

    def test_dry_run_changes_nothing(self):
        fake = FakeClaude()
        r = install_mcp.claude_code("add", "rhwp", RHWP, True, "claude", fake)
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual([c[2] for c in fake.calls], ["get"])


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "Claude" / "claude_desktop_config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_merges_and_backs_up(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}), "utf-8")
        r = install_mcp.claude_desktop("add", "rhwp", RHWP, False, self.config)
        self.assertEqual(r["status"], "added")
        data = json.loads(self.config.read_text("utf-8"))
        self.assertEqual(data["mcpServers"]["rhwp"], {"command": RHWP, "args": ["mcp-serve"]})
        self.assertEqual(data["mcpServers"]["other"], {"command": "x"}, "기존 설정은 그대로")
        self.assertEqual(data["theme"], "dark")
        self.assertTrue(Path(r["backup"]).is_file())
        self.assertEqual(install_mcp.claude_desktop("add", "rhwp", RHWP, False, self.config)["status"], "already")

    def test_conflict_dry_run_remove_and_new_file(self):
        r = install_mcp.claude_desktop("add", "rhwp", RHWP, True, self.config)
        self.assertEqual(r["status"], "dry-run")
        self.assertFalse(self.config.exists(), "dry-run 은 파일을 만들지 않는다")
        self.assertEqual(install_mcp.claude_desktop("add", "rhwp", RHWP, False, self.config)["status"], "added")
        conflict = install_mcp.claude_desktop("add", "rhwp", "/other/rhwp", False, self.config)
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(install_mcp.claude_desktop("remove", "rhwp", "/other/rhwp", False, self.config)["status"],
                         "conflict")
        self.assertEqual(install_mcp.claude_desktop("remove", "rhwp", RHWP, False, self.config)["status"], "removed")
        self.assertNotIn("rhwp", json.loads(self.config.read_text("utf-8"))["mcpServers"])

    def test_bad_json_is_reported(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text("{not json", "utf-8")
        self.assertEqual(install_mcp.claude_desktop("add", "rhwp", RHWP, False, self.config)["status"], "failed")
        self.assertEqual(self.config.read_text("utf-8"), "{not json", "깨진 설정은 고치지 않는다")


class ServerCheckTest(unittest.TestCase):
    def test_mcp_serve_handshake(self):
        result = install_mcp.check_server(rhwp_locate.locate(None)["path"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["server"]["name"], "rhwp")
        self.assertGreater(result["tools"], 50)


if __name__ == "__main__":
    unittest.main()
