"""기본 프로필(skills/hwp-master/profiles)에 새 서식 항목을 원본 코퍼스에서 다시 집계해 덧붙인다.

    python3 tools/refresh_profiles.py --share <hwpdocs_10k_share> --full <hwpdocs> [--only 이름...] [--jobs 8]

기존 권장값(여백·크기·줄 간격·항목 체계 등)은 그대로 두고, style_profile.summarize 가 새로 내는
style 키(역할별 글꼴 *_font, 문단 간격 *_space_*)와 그 일치율만 더한다. 기존 프로필의 문서 수만큼
그룹에서 결정적으로(경로 SHA-1 순) 뽑아 읽고, 읽은 특징은 --cache 에 남겨 다시 돌릴 때 재사용한다.

그룹 규칙은 아래 GROUPS 에 있다. 원래 집계의 문서 목록은 남아 있지 않으므로 같은 출처 폴더·이름
규칙으로 다시 고른 표본이다. 정해 줄 수 없는 그룹(정책브리핑 보도자료/기타 구분 등)은 건너뛰고 알린다.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/hwp-master/scripts"))
import hds  # noqa: E402
import rhwp_locate  # noqa: E402
import style_profile  # noqa: E402

PROFILES = ROOT / "skills/hwp-master/profiles"
NEW_KEYS = set(hds.STYLE_FONT_KEYS) | set(hds.STYLE_SPACE_KEYS)


def _files(base: Path, sub: str, name_has=None, name_not=None):
    d = base / sub
    if not d.is_dir():
        return []
    out = []
    for f in d.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".hwp", ".hwpx"):
            n = f.name
            if name_has and not any(k in n for k in name_has):
                continue
            if name_not and any(k in n for k in name_not):
                continue
            out.append(f)
    return out


def groups(share: Path, full: Path) -> dict:
    """프로필 이름 → 후보 문서 목록 (None 이면 규칙을 정할 수 없어 건너뜀)."""
    g = {
        "공문": _files(share, "samples"),
        "보도자료": _files(share, "korea_downloads"),
        "연구용역": _files(share, "prism_downloads"),
        "속기자료": _files(share, "korea_policy_downloads", name_has=["속기"]),
        "속기자료-정책브리핑": _files(share, "korea_policy_downloads", name_has=["속기"]),
        "제개정이유서": _files(share, "opinion_downloads", name_has=["이유서"]),
        "법령안": _files(share, "opinion_downloads", name_has=["령안", "법률안", "규칙안", "조례안"],
                      name_not=["이유서", "규제영향"]),
        "의결서": _files(share, "acrc_downloads", name_has=["의결"]) or _files(share, "acrc_downloads"),
        "권고문": _files(full, "acrc_downloads", name_has=["권고"]),
        "규제영향분석서": _files(full, "opinion_downloads", name_has=["규제영향분석서"]),
        "공고": _files(full, "moe_downloads", name_has=["공고"]),
        "시행계획": _files(full, "prism_downloads", name_has=["시행계획"]),
        "보도자료-중앙선거관리위원회": _files(full, "nec_downloads", name_has=["보도"]),
        "보도자료-국회입법조사처": _files(full, "nars_downloads"),
        "보도자료-교육부": _files(full, "moe_downloads", name_has=["보도"]),
        "보도자료-정책브리핑": None,
        "기타-정책브리핑": None,
        "기타": None,
    }
    for p in sorted(PROFILES.glob("보도자료-*.json")):
        name = p.stem
        if name not in g:
            g[name] = _files(share, f"korea_downloads/{name.split('-', 1)[1]}")
    return g


def pick(files, n: int):
    return sorted(files, key=lambda f: hashlib.sha1(str(f).encode("utf-8")).hexdigest())[:n]


def features_for(paths, rhwp: str, cache: dict, jobs: int):
    todo = [p for p in paths if str(p) not in cache]

    def one(p: Path):
        with tempfile.TemporaryDirectory(prefix="hwpm-refresh-") as tmp:
            try:
                return str(p), style_profile.read_features(style_profile.to_hwpx(p, rhwp, Path(tmp)))
            except Exception as exc:  # 손상·변환 실패 문서는 건너뛴다
                return str(p), {"error": f"{type(exc).__name__}: {exc}"[:200]}

    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for key, feat in ex.map(one, todo):
            cache[key] = feat
    return [cache[str(p)] for p in paths if "error" not in cache[str(p)]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--share", type=Path, required=True)
    ap.add_argument("--full", type=Path, required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "hwpm_refresh_cache.json")
    ap.add_argument("--rhwp")
    ap.add_argument("--dry-run", action="store_true", help="프로필 파일을 고치지 않고 결과만 보인다")
    a = ap.parse_args(argv)
    rhwp = rhwp_locate.locate(a.rhwp)["path"]
    cache = json.loads(a.cache.read_text(encoding="utf-8")) if a.cache.exists() else {}
    report = []
    for name, files in groups(a.share, a.full).items():
        if a.only and name not in a.only:
            continue
        path = PROFILES / f"{name}.json"
        if not path.exists():
            continue
        prof = json.loads(path.read_text(encoding="utf-8"))
        if not files:
            report.append({"profile": name, "skipped": "그룹 규칙 없음" if files is None else "후보 문서 없음"})
            continue
        if len(files) < prof["documents"] / 2:
            # 같은 출처 규칙으로 원래 문서 수의 절반도 못 모으면 다른 표본이라 덧붙이지 않는다
            report.append({"profile": name, "skipped": f"후보 {len(files)}건 < 원래 {prof['documents']}건의 절반"})
            continue
        sample = pick(files, prof["documents"])
        feats = features_for(sample, rhwp, cache, a.jobs)
        a.cache.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        if not feats:
            report.append({"profile": name, "skipped": "읽은 문서 없음"})
            continue
        fresh = style_profile.summarize(feats, [])
        # 기존 프로필의 문서 전체 글꼴(font)을 기준으로 역할 글꼴을 다시 걸러 낸다
        base_font = prof["recommend"].get("font")
        add = {k: v for k, v in (fresh["recommend"].get("style") or {}).items() if k in NEW_KEYS
               and not (k.endswith("_font") and v == base_font)}
        agree = {k: v for k, v in fresh["agreement"].items() if k in NEW_KEYS}
        style = {k: v for k, v in (prof["recommend"].get("style") or {}).items() if k not in NEW_KEYS}
        style.update(add)
        if style:
            prof["recommend"]["style"] = style
        prof["agreement"] = {**{k: v for k, v in prof["agreement"].items() if k not in NEW_KEYS}, **agree}
        corpus = prof.setdefault("corpus", {})
        corpus["refreshed"] = {"keys": sorted(NEW_KEYS), "documents": len(feats), "candidates": len(files),
                               "rhwp": rhwp_locate.locate(rhwp).get("version")}
        report.append({"profile": name, "read": len(feats), "of": len(sample), "added": add, "agreement": agree})
        if not a.dry_run:
            path.write_text(json.dumps(prof, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
