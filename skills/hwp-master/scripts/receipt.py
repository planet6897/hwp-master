#!/usr/bin/env python3
"""빌드 영수증: HDS 명세 → HWPX 생성의 재현 증명.

    python3 receipt.py verify <영수증.json> [--rhwp PATH]

rhwp `replay --capsule` 은 기존 문서에 편집 계획을 다시 적용하는 영수증이라
무(無)에서 만드는 `scaffold` 를 담지 못한다. 그래서 생성 단계는 이 영수증으로 남긴다.

영수증 = 명세 사본(그대로) + 명세·산출 SHA-256 + rhwp 버전.
verify 는 영수증 안의 명세로 임시 파일에 다시 생성해 산출 해시가 같은지 판정한다.
rhwp 버전이 다르면 해시가 달라질 수 있으므로 판정과 함께 버전 일치 여부를 보고한다.
exit 0 = 재현, 3 = 불일치, 2 = 실행 오류.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhwp_locate  # noqa: E402

RECEIPT_KIND = "hwp-master/build-receipt"
RECEIPT_VERSION = "1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rhwp_version(rhwp: str) -> str:
    proc = subprocess.run([rhwp, "--version"], capture_output=True, text=True, timeout=60)
    return proc.stdout.strip()


def make(spec_path: Path, output: Path, rhwp: str) -> dict:
    spec_bytes = spec_path.read_bytes()
    return {
        "kind": RECEIPT_KIND,
        "receiptVersion": RECEIPT_VERSION,
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rhwpVersion": rhwp_version(rhwp),
        "command": ["rhwp", "scaffold", "<spec>", "-o", "<output>"],
        "specSha256": sha256_bytes(spec_bytes),
        "spec": spec_bytes.decode("utf-8"),
        "output": output.name,
        "outputSha256": sha256_bytes(output.read_bytes()),
    }


def save(rec: dict, path: Path) -> None:
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def verify(rec: dict, rhwp: str) -> dict:
    if rec.get("kind") != RECEIPT_KIND:
        raise ValueError(f"빌드 영수증이 아닙니다 (kind={rec.get('kind')!r})")
    spec_bytes = rec["spec"].encode("utf-8")
    result = {
        "specIntact": sha256_bytes(spec_bytes) == rec["specSha256"],
        "rhwpVersion": rhwp_version(rhwp),
        "rhwpVersionSame": None,
        "expectedOutputSha256": rec["outputSha256"],
    }
    result["rhwpVersionSame"] = result["rhwpVersion"] == rec.get("rhwpVersion")
    with tempfile.TemporaryDirectory(prefix="hwpm-receipt-") as tmp:
        spec = Path(tmp) / "spec.json"
        out = Path(tmp) / "out.hwpx"
        spec.write_bytes(spec_bytes)
        proc = subprocess.run([rhwp, "scaffold", str(spec), "-o", str(out)],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"scaffold 실패 (exit {proc.returncode}): {proc.stderr.strip()[-500:]}")
        result["actualOutputSha256"] = sha256_bytes(out.read_bytes())
    result["reproduced"] = result["specIntact"] and (
        result["actualOutputSha256"] == result["expectedOutputSha256"])
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="hwp-master 빌드 영수증")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("verify")
    p.add_argument("receipt", type=Path)
    p.add_argument("--rhwp")
    args = ap.parse_args()
    try:
        rec = json.loads(args.receipt.read_text(encoding="utf-8"))
        rhwp = rhwp_locate.locate(args.rhwp)["path"]
        result = verify(rec, rhwp)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["reproduced"] else 3


if __name__ == "__main__":
    sys.exit(main())
