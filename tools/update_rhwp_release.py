#!/usr/bin/env python3
"""rhwp 바이너리 Release 를 hwp-master 의 고정 기록에 반영한다 (관리자용).

    gh workflow run rhwp-binaries.yml            # devel 최신 커밋 빌드·Release (약 1시간)
    python3 tools/update_rhwp_release.py         # 가장 최근 rhwp-* Release 를 반영
    python3 tools/update_rhwp_release.py --tag rhwp-0.9.0-eb84afc --dry-run

하는 일
1. Release 의 SHA256SUMS.txt 와 본문의 원본 커밋을 읽는다 (gh CLI 필요).
2. 다섯 플랫폼(SUFFIXES) 파일이 모두 있는지 확인한다.
3. skills/hwp-master/rhwp_release.json 을 새 태그·커밋·SHA-256 으로 바꾼다.
4. README.md 의 이전 태그를 새 태그로 바꾸고 바이너리 표의 날짜를 오늘로 바꾼다.
   (표의 행은 플랫폼이 바뀔 때 손으로 맞춘다.)

반영한 뒤 install_rhwp.py 로 설치하고 전체 테스트를 돌려 확인한 다음 PR 로 올린다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "skills/hwp-master/rhwp_release.json"
README = ROOT / "README.md"
SUFFIXES = ("macos-aarch64", "macos-x86_64", "linux-x86_64-glibc2.28", "linux-x86_64-glibc2.35",
            "windows-x86_64")
TAG_RE = re.compile(r"^rhwp-(?P<version>\d+\.\d+\.\d+)-(?P<short>[0-9a-f]{7})$")


def gh(*args: str) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} 실패: {proc.stderr.strip()}")
    return proc.stdout


def latest_tag(repo: str) -> str:
    rows = json.loads(gh("release", "list", "--repo", repo, "--limit", "50",
                         "--json", "tagName,createdAt"))
    tags = sorted((r for r in rows if TAG_RE.match(r["tagName"])), key=lambda r: r["createdAt"])
    if not tags:
        raise SystemExit("rhwp-* Release 가 없습니다.")
    return tags[-1]["tagName"]


def parse_sums(text: str) -> dict[str, str]:
    sums = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-f]{64})\s+\*?(\S+)$", line.strip())
        if m:
            sums[m.group(2)] = m.group(1)
    return sums


def build_lock(old: dict, tag: str, commit: str, sums: dict[str, str]) -> dict:
    m = TAG_RE.match(tag)
    if not m:
        raise SystemExit(f"태그 형식이 아닙니다: {tag}")
    if not commit.startswith(m["short"]):
        raise SystemExit(f"커밋 {commit} 이 태그 {tag} 와 맞지 않습니다.")
    assets = {}
    for suffix in SUFFIXES:
        names = [n for n in sums if n.startswith(f"{tag}-{suffix}.")]
        if len(names) != 1:
            raise SystemExit(f"{suffix} 파일을 SHA256SUMS.txt 에서 찾지 못했습니다: {sorted(sums)}")
        assets[names[0]] = sums[names[0]]
    return {**old, "tag": tag, "rhwp_version": m["version"], "rhwp_commit": commit,
            "assets": dict(sorted(assets.items()))}


def update_readme(text: str, old_tag: str, new_tag: str, today: str) -> str:
    text = text.replace(old_tag, new_tag)
    # 바이너리 표의 확인 날짜: | ... | `rhwp-...` | 설명 (YYYY-MM-DD) |
    return re.sub(rf"(\| `{re.escape(new_tag)}-[^`]+` \|[^|\n]*?)\d{{4}}-\d{{2}}-\d{{2}}(\) \|)",
                  rf"\g<1>{today}\g<2>", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", help="반영할 Release 태그 (기본: 가장 최근 rhwp-* Release)")
    ap.add_argument("--dry-run", action="store_true", help="바꿀 내용만 출력")
    args = ap.parse_args(argv)

    old = json.loads(LOCK.read_text(encoding="utf-8"))
    repo = old["repo"]
    tag = args.tag or latest_tag(repo)
    body = gh("release", "view", tag, "--repo", repo, "--json", "body", "--jq", ".body")
    m = re.search(r"edwardkim/rhwp/commit/([0-9a-f]{40})", body)
    if not m:
        raise SystemExit(f"{tag} 본문에서 원본 커밋을 찾지 못했습니다.")
    sums = parse_sums(gh("release", "download", tag, "--repo", repo, "--pattern", "SHA256SUMS.txt",
                         "--output", "-"))
    new = build_lock(old, tag, m.group(1), sums)
    today = dt.date.today().isoformat()
    readme = update_readme(README.read_text(encoding="utf-8"), old["tag"], tag, today)

    print(json.dumps(new, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    LOCK.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    README.write_text(readme, encoding="utf-8")
    print(f"반영: {old['tag']} -> {tag}. 이제 install_rhwp.py 와 전체 테스트로 확인합니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
