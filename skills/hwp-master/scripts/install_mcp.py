#!/usr/bin/env python3
"""Claude MCP 연결 설치: 배포 rhwp 바이너리의 mcp-serve(hwp_* 도구)를 Claude Code·Claude Desktop 에 등록한다.

    python3 install_mcp.py [--desktop] [--no-code] [--name rhwp] [--remove] [--dry-run] [--rhwp PATH] [--config PATH]

Claude 는 HWP 공식 업로드를 지원하지 않지만 MCP 서버를 연결하면 HWP 를 읽고 고칠 수 있다(지디넷 2026-11-10).
배포 rhwp 바이너리에 mcp-serve 가 들어 있어, rhwp 소스를 받거나 빌드하지 않고 바로 연결된다.

- Claude Code: `claude mcp add --scope user <name> -- <rhwp> mcp-serve`. 같은 이름이 이미 있으면
  (다른 rhwp 를 가리키면) 덮어쓰지 않고 알린다 — 다른 이름(--name hwp-master-rhwp)을 쓰거나 기존 것을 직접 지운다.
- Claude Desktop(--desktop): claude_desktop_config.json 의 mcpServers 에 같은 항목을 넣는다. 고치기 전에
  <파일>.bak-<시각> 으로 백업하고, 기존 설정은 그대로 두고 합친다.
- --remove: 이 스크립트가 넣은 것(같은 rhwp 경로)만 지운다. --dry-run: 바꾸지 않고 할 일만 보여 준다.
- 끝에 mcp-serve 와 initialize·tools/list 를 주고받아 도구 수를 확인한다.

사용자가 원할 때만 실행한다(설치 절차의 선택 단계). 결과는 JSON 한 줄로 stdout 에 낸다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhwp_locate  # noqa: E402

Runner = Callable[..., subprocess.CompletedProcess]


def desktop_config_path() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def _same_path(a: str, b: str) -> bool:
    norm = lambda p: os.path.normcase(os.path.normpath(p.strip().strip('"')))
    return norm(a) == norm(b)


def check_server(rhwp: str, timeout: int = 60) -> dict:
    """mcp-serve 와 initialize·tools/list 를 주고받아 서버 이름·도구 수를 확인한다."""
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "hwp-master-install", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        proc = subprocess.run([rhwp, "mcp-serve"], input="".join(json.dumps(m) + "\n" for m in messages),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)[:200]}
    server, tools = None, None
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 1:
            server = (msg.get("result") or {}).get("serverInfo")
        if msg.get("id") == 2:
            tools = len((msg.get("result") or {}).get("tools", []))
    return {"ok": bool(server) and bool(tools), "server": server, "tools": tools}


def claude_code(action: str, name: str, rhwp: str, dry_run: bool, claude: Optional[str],
                run: Runner = subprocess.run) -> dict:
    claude = claude or shutil.which("claude")
    if not claude:
        return {"target": "claude-code", "status": "unavailable", "message": "claude CLI 를 찾지 못했습니다"}
    got = run([claude, "mcp", "get", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
    exists = got.returncode == 0
    # `claude mcp get` 은 "Command: <경로>" 줄을 준다. 그 경로가 이 rhwp 일 때만 우리 등록으로 본다.
    command = next((line.split(":", 1)[1].strip() for line in got.stdout.splitlines()
                    if line.strip().lower().startswith("command:")), "")
    ours = exists and bool(command) and _same_path(command, rhwp)
    if action == "add":
        if exists and ours:
            return {"target": "claude-code", "status": "already", "message": f"'{name}' 이 이미 이 rhwp 를 가리킵니다"}
        if exists:
            return {"target": "claude-code", "status": "conflict",
                    "message": f"'{name}' 이 다른 서버로 이미 등록돼 있어 덮어쓰지 않았습니다. "
                               f"--name hwp-master-rhwp 처럼 다른 이름을 쓰거나 `claude mcp remove {name}` 후 다시 실행하세요",
                    "existing": got.stdout.strip()[:300]}
        cmd = [claude, "mcp", "add", "--scope", "user", name, "--", rhwp, "mcp-serve"]
    else:
        if not exists:
            return {"target": "claude-code", "status": "absent", "message": f"'{name}' 이 등록돼 있지 않습니다"}
        if not ours:
            return {"target": "claude-code", "status": "conflict",
                    "message": f"'{name}' 은 이 rhwp 가 아니라 지우지 않았습니다", "existing": got.stdout.strip()[:300]}
        cmd = [claude, "mcp", "remove", name, "--scope", "user"]
    if dry_run:
        return {"target": "claude-code", "status": "dry-run", "command": cmd}
    done = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        return {"target": "claude-code", "status": "failed", "command": cmd,
                "message": (done.stderr or done.stdout).strip()[-300:]}
    return {"target": "claude-code", "status": "added" if action == "add" else "removed", "command": cmd}


def claude_desktop(action: str, name: str, rhwp: str, dry_run: bool, config: Path) -> dict:
    data = {}
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            return {"target": "claude-desktop", "status": "failed", "config": str(config),
                    "message": f"설정 파일이 JSON 이 아닙니다: {exc}"}
    servers = data.setdefault("mcpServers", {})
    entry = {"command": rhwp, "args": ["mcp-serve"]}
    current = servers.get(name)
    ours = bool(current) and _same_path(str(current.get("command", "")), rhwp) and current.get("args") == ["mcp-serve"]
    if action == "add":
        if ours:
            return {"target": "claude-desktop", "status": "already", "config": str(config)}
        if current:
            return {"target": "claude-desktop", "status": "conflict", "config": str(config), "existing": current,
                    "message": f"'{name}' 이 다른 서버로 이미 있어 덮어쓰지 않았습니다(--name 으로 다른 이름)"}
        servers[name] = entry
    else:
        if not current:
            return {"target": "claude-desktop", "status": "absent", "config": str(config)}
        if not ours:
            return {"target": "claude-desktop", "status": "conflict", "config": str(config), "existing": current,
                    "message": f"'{name}' 은 이 rhwp 가 아니라 지우지 않았습니다"}
        del servers[name]
    if dry_run:
        return {"target": "claude-desktop", "status": "dry-run", "config": str(config), "entry": servers.get(name)}
    backup = None
    if config.exists():
        backup = config.with_name(config.name + ".bak-" + dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(config, backup)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"target": "claude-desktop", "status": "added" if action == "add" else "removed", "config": str(config),
            "backup": str(backup) if backup else None, "message": "Claude Desktop 을 다시 시작하면 연결됩니다"}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", default="rhwp", help="MCP 서버 이름 (기본 rhwp)")
    ap.add_argument("--desktop", action="store_true", help="Claude Desktop 설정에도 넣는다")
    ap.add_argument("--no-code", action="store_true", help="Claude Code 에는 넣지 않는다")
    ap.add_argument("--remove", action="store_true", help="이 rhwp 를 가리키는 등록만 지운다")
    ap.add_argument("--dry-run", action="store_true", help="바꾸지 않고 할 일만 보여 준다")
    ap.add_argument("--rhwp", help="rhwp 경로 (기본: rhwp_locate 가 찾은 배포 바이너리)")
    ap.add_argument("--config", type=Path, help="Claude Desktop 설정 파일 경로 (기본: OS 별 위치)")
    ap.add_argument("--claude", help="claude CLI 경로 (기본: PATH)")
    args = ap.parse_args(argv)
    rhwp = str(Path(rhwp_locate.locate(args.rhwp)["path"]).resolve())
    action = "remove" if args.remove else "add"
    results = []
    if not args.no_code:
        results.append(claude_code(action, args.name, rhwp, args.dry_run, args.claude))
    if args.desktop:
        results.append(claude_desktop(action, args.name, rhwp, args.dry_run, args.config or desktop_config_path()))
    check = check_server(rhwp) if action == "add" else None
    for r in results:
        print(f"[{r['status']}] {r['target']}: {r.get('message') or r.get('config') or ''}".rstrip(), file=sys.stderr)
    if check:
        print(f"mcp-serve 확인: {'성공' if check['ok'] else '실패'} — {check.get('server')} 도구 {check.get('tools')}개",
              file=sys.stderr)
    bad = [r for r in results if r["status"] in ("failed", "conflict")]
    ok = not bad and (check is None or check["ok"])
    print(json.dumps({"ok": ok, "rhwp": rhwp, "name": args.name, "results": results, "check": check},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
