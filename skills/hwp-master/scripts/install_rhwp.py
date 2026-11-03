#!/usr/bin/env python3
"""hwp-master Release 에 올린 rhwp 바이너리를 받아 설치한다.

어느 Release·파일·SHA-256 을 쓸지는 skills/hwp-master/rhwp_release.json 에 고정돼 있다.
받은 압축 파일의 SHA-256 이 그 값과 다르면 설치하지 않는다.

    python3 install_rhwp.py [--dir 설치폴더] [--json]

받기는 `gh release download`(비공개 저장소도 됨)를 먼저 쓰고, gh 가 없으면 공개 URL 로 받는다.
설치 위치 기본값: ~/.local/share/hwp-master/rhwp/<태그>/ (Windows: %LOCALAPPDATA%\\hwp-master\\rhwp\\<태그>\\).
rhwp_locate.py 는 RHWP_BIN·--rhwp 다음으로 이 위치를 찾는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

LOCK = Path(__file__).resolve().parents[1] / "rhwp_release.json"


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def platform_suffix() -> Optional[str]:
    """이 컴퓨터에 맞는 압축 파일 접미사. 지원하지 않으면 None."""
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-aarch64"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "windows-x86_64"
    return None


def default_dir(tag: str) -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "hwp-master" / "rhwp" / tag


def binary_path(install_dir: Path) -> Path:
    return install_dir / "rhwp" / ("rhwp.exe" if sys.platform == "win32" else "rhwp")


def installed_binary(lock: Optional[dict] = None) -> Optional[Path]:
    """고정된 Release 의 바이너리가 기본 위치에 설치돼 있으면 그 경로."""
    try:
        lock = lock or load_lock()
    except (OSError, ValueError):
        return None
    path = binary_path(default_dir(lock["tag"]))
    return path if path.is_file() else None


def _download(lock: dict, asset: str, dest: Path) -> str:
    if shutil.which("gh"):
        proc = subprocess.run(["gh", "release", "download", lock["tag"], "--repo", lock["repo"],
                               "--pattern", asset, "--dir", str(dest.parent), "--clobber"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0 and dest.is_file():
            return "gh"
    url = f"https://github.com/{lock['repo']}/releases/download/{lock['tag']}/{asset}"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except OSError as exc:
        raise RuntimeError(f"받지 못했습니다: {url} ({exc}). 비공개 저장소면 `gh auth login` 뒤 다시 실행하세요.")
    return "https"


def _safe_extract(archive: Path, target: Path) -> None:
    """압축 안의 경로가 target 밖을 가리키면 거부한다."""
    root = target.resolve()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not (target / name).resolve().is_relative_to(root):
                    raise RuntimeError(f"압축 안 경로가 설치 폴더 밖을 가리킵니다: {name}")
            z.extractall(target)
    else:
        with tarfile.open(archive, "r:gz") as t:
            for m in t.getmembers():
                if not (target / m.name).resolve().is_relative_to(root) or m.issym() or m.islnk():
                    raise RuntimeError(f"압축 안 항목을 받아들일 수 없습니다: {m.name}")
            if hasattr(tarfile, "data_filter"):
                t.extractall(target, filter="data")
            else:
                t.extractall(target)


def install(dest_dir: Optional[Path] = None) -> dict:
    lock = load_lock()
    suffix = platform_suffix()
    if suffix is None:
        raise RuntimeError(f"이 플랫폼({sys.platform} {platform.machine()})용 바이너리는 없습니다. "
                           "README 의 cargo install 방법으로 빌드하세요.")
    asset = next((a for a in lock["assets"] if suffix in a), None)
    if asset is None:
        raise RuntimeError(f"{lock['tag']} 에 {suffix} 파일이 없습니다")
    target = dest_dir or default_dir(lock["tag"])
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset
        via = _download(lock, asset, archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != lock["assets"][asset]:
            raise RuntimeError(f"SHA-256 이 다릅니다: {asset} {digest} (기대 {lock['assets'][asset]})")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        _safe_extract(archive, target)
    exe = binary_path(target)
    if not exe.is_file():
        raise RuntimeError(f"압축을 풀었지만 바이너리가 없습니다: {exe}")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return {"ok": proc.returncode == 0, "path": str(exe), "version": (proc.stdout or proc.stderr).strip(),
            "tag": lock["tag"], "rhwpCommit": lock["rhwp_commit"], "asset": asset, "sha256": digest, "via": via}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, help="설치 폴더 (기본: 사용자 데이터 폴더)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = install(args.dir)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) if args.json else f"오류: {exc}",
              file=sys.stdout if args.json else sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['version']} 설치 — {result['path']}")
        print(f"(Release {result['tag']}, rhwp {result['rhwpCommit'][:7]}, SHA-256 확인)")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
