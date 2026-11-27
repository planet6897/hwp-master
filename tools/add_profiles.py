"""원본 코퍼스(hwpdocs)에서 아직 없는 묶음을 골라 기본 문서 유형 프로필을 새로 만든다.

    python3 tools/add_profiles.py --full <hwpdocs> [--files <목록.txt>] [--only 이름...] [--jobs 10] [--write]

그룹 규칙은 아래 groups() 에 있다. 그룹마다 경로(코퍼스 기준 상대 경로) SHA-1 순으로 최대 --sample 건을
결정적으로 뽑되 내용(SHA-256)이 같은 파일은 하나로 세고, style_profile.read_features 로 읽어 summarize 한다. 기존 기본 프로필과 같은 기준
(읽은 문서 30건 이상, 원래 서식 항목 중 일치율 0.6 이상 2개 이상)을 통과한 그룹만 --write 때
skills/hwp-master/profiles/<이름>.json 으로 쓴다. 이미 있는 프로필은 덮어쓰지 않는다.
집계값만 담고 개별 문서 목록·경로는 넣지 않는다. 읽은 특징은 --cache 에 남겨 다시 돌릴 때 재사용한다.
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
import rhwp_locate  # noqa: E402
import style_profile  # noqa: E402

PROFILES = ROOT / "skills/hwp-master/profiles"
MIN_DOCS = 30
PASS_AGREE = 0.6
PASS_ITEMS = 2
# 처음 기본 프로필을 고를 때 본 서식 항목 (역할 글꼴·문단 간격은 나중에 덧붙인 항목이라 판정에 쓰지 않는다)
CORE_KEYS = ("margins_mm", "font", "body_pt", "title_pt", "heading_pt", "table_pt", "line_spacing", "marker_system")

PRISM_KINDS = {
    "과업지시서": ["과업지시서"],
    "제안요청서": ["제안요청서"],
    "연구용역-평가결과서": ["평가결과서", "평가 결과서"],
    "연구용역-결과보고서": ["결과보고서", "최종보고서"],
    "연구용역-과제신청서": ["과제신청서", "과제 신청서"],
    "연구용역-활용결과": ["활용결과", "활용 결과"],
}

FORMS = ("신청서", "양식", "서식")
SCHOOLS = ("sch_daegue_downloads", "sch_daeguh_downloads")
EDU = ("jbe_downloads", "dge_downloads", "use_downloads", "gne_downloads", "goe_downloads", "sen_downloads")
# 2026-11 하순에 더해진 출처 폴더: (폴더들, 프로필 이름, 구분, 이름에 든 낱말 중 하나, 들면 빼는 낱말). 위에서부터 처음 맞는 규칙 하나만 쓴다.
SOURCE_RULES = [
    (("bizinfo_downloads", "msit_downloads", "motie_downloads", "mss_downloads"), "사업신청서", "type",
     FORMS + ("계획서",), ()),
    (("msit_downloads",), "공고-과학기술정보통신부", "agency", ("공고",), FORMS),
    (("motie_downloads",), "공고-산업통상자원부", "agency", ("공고",), FORMS),
    (("mss_downloads",), "공고-중소벤처기업부", "agency", ("공고",), FORMS),
    (("molit_downloads",), "고시-국토교통부", "agency", ("고시",), FORMS),
    (("molit_downloads",), "공고-국토교통부", "agency", ("공고",), FORMS),
    (("ftc_downloads",), "표준약관", "type", ("약관",), ()),
    (("ftc_downloads", "mss_downloads"), "표준계약서", "type", ("계약서", "약정서"), ()),
    (("ftc_downloads",), "심사지침-공정거래위원회", "agency", ("지침", "예규", "가이드라인"), ()),
    (("ftc_downloads",), "공고-공정거래위원회", "agency", ("공고",), FORMS),
    (("pps_downloads",), "계약기준-조달청", "agency", ("기준", "특수조건", "규정"), ()),
    (("pps_downloads",), "공고-조달청", "agency", ("공고",), FORMS),
    (("alio_downloads",), "규정-공공기관", "type", ("규정", "규칙", "지침"), ()),
    (("kostat_downloads",), "규정-통계청", "agency", ("규정", "훈령"), ()),
    (("moel_downloads",), "현황-고용노동부", "agency", ("현황",), ()),
    (EDU, "공문-교육청", "type", ("공문",), ()),
    (EDU, "계획-교육청", "type", ("계획",), FORMS),
    (EDU, "안내-교육청", "type", ("안내",), FORMS),
    # 2026-11 말에 더해진 출처 폴더
    (("nia_downloads",), "제안요청서-한국지능정보사회진흥원", "agency", ("제안요청서",), ()),
    (("kiat_downloads",), "제안요청서-한국산업기술진흥원", "agency", ("제안요청서",), ()),
    (("nipa_downloads",), "공고-정보통신산업진흥원", "agency", ("공고",), FORMS),
    (("nia_downloads",), "공고-한국지능정보사회진흥원", "agency", ("공고",), FORMS),
    (("kiat_downloads",), "입찰양식-한국산업기술진흥원", "agency", ("양식",), ()),
    (("index_downloads", "laborstat_downloads"), "보도자료-통계", "type", ("보도",), ()),
    (SCHOOLS, "가정통신문", "type", ("가정통신문", "통신문"), ()),
    (SCHOOLS, "안내-학교", "type", ("안내",), FORMS),
]


def listing(full: Path, files: Path | None):
    """코퍼스 기준 상대 경로 목록 ('폴더/…/이름.hwp')."""
    if files and files.exists():
        rel = [l.strip().lstrip("./") for l in files.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        rel = [p.relative_to(full).as_posix() for p in full.rglob("*")
               if p.is_file() and p.suffix.lower() in (".hwp", ".hwpx")]
    return [r for r in rel if r.lower().endswith((".hwp", ".hwpx"))]


def groups(rel):
    """프로필 이름 → (구분, 후보 상대 경로 목록)."""
    g: dict = {}

    def add(name, by, r):
        g.setdefault(name, (by, []))[1].append(r)

    for r in rel:
        parts = r.split("/")
        top, name = parts[0], parts[-1]
        # 법령·행정규칙·자치법규(law·admrul·ordin)의 별지 서식·별표는 넣지 않는다. 2026-11 집계에서 서식 종류별
        # (신청서·대장 등 34종)로 묶으면 지자체마다 글꼴·여백이 달라 기준 미달이었고, 법령만 모으면 통과하지만
        # 근거가 여백과 체크박스 □(개조식 항목 기호로 잡힘)뿐이라 새 문서에 □ 개조식을 권하는 잘못된 프로필이 된다.
        if top == "korea_downloads" and len(parts) > 2 and "보도" in name:
            add(f"보도자료-{parts[1]}", "agency", r)
        elif top == "prism_downloads":
            for pname, keys in PRISM_KINDS.items():
                if any(k in name for k in keys):
                    add(pname, "type", r)
                    break
        elif top == "nts_downloads" and "공고" in name:
            add("공고-국세청", "agency", r)
        else:
            for folders, pname, by, has, not_has in SOURCE_RULES:
                if top in folders and any(k in name for k in has) and not any(k in name for k in not_has):
                    add(pname, by, r)
                    break
    return g


def pick(rel, n: int, full: Path | None = None):
    """경로 SHA-1 순으로 n 건. full 을 주면 내용(SHA-256)이 같은 파일은 처음 하나만 센다
    (같은 양식을 여러 번 내려받은 묶음이 일치율을 부풀리지 않게)."""
    order = sorted(rel, key=lambda r: hashlib.sha1(r.encode("utf-8")).hexdigest())
    if full is None:
        return order[:n]
    seen, out = set(), []
    for r in order:
        h = hashlib.sha256((full / r).read_bytes()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(r)
            if len(out) == n:
                break
    return out


def features_for(full: Path, rel, rhwp: str, cache: dict, jobs: int):
    todo = [r for r in rel if r not in cache]

    def one(r: str):
        with tempfile.TemporaryDirectory(prefix="hwpm-addprof-") as tmp:
            try:
                return r, style_profile.read_features(style_profile.to_hwpx(full / r, rhwp, Path(tmp)))
            except Exception as exc:  # 손상·변환 실패 문서는 건너뛴다
                return r, {"error": f"{type(exc).__name__}: {exc}"[:200]}

    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for key, feat in ex.map(one, todo):
            cache[key] = feat
    return [cache[r] for r in rel if "error" not in cache[r]]


def judge(prof: dict):
    strong = [k for k in CORE_KEYS if prof["agreement"].get(k, 0) >= PASS_AGREE]
    ok = prof["documents"] >= MIN_DOCS and len(strong) >= PASS_ITEMS
    return ok, strong


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", type=Path, required=True, help="원본 코퍼스 폴더 (hwpdocs)")
    ap.add_argument("--files", type=Path, help="코퍼스 기준 상대 경로 목록 (없으면 폴더를 훑는다)")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--collected", default="2026-11")
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "hwpm_addprof_cache.json")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--rhwp")
    ap.add_argument("--write", action="store_true", help="통과한 그룹을 profiles/ 에 쓴다 (기본은 결과만 보인다)")
    a = ap.parse_args(argv)
    rhwp = rhwp_locate.locate(a.rhwp)["path"]
    version = rhwp_locate.locate(rhwp).get("version")
    cache = json.loads(a.cache.read_text(encoding="utf-8")) if a.cache.exists() else {}
    report = []
    for name, (by, cands) in sorted(groups(listing(a.full, a.files)).items()):
        if a.only and name not in a.only:
            continue
        path = PROFILES / f"{name}.json"
        if path.exists():
            report.append({"profile": name, "skipped": "이미 있음"})
            continue
        if len(cands) < MIN_DOCS:
            report.append({"profile": name, "skipped": f"후보 {len(cands)}건 < {MIN_DOCS}"})
            continue
        sample = pick(cands, a.sample, a.full)
        feats = features_for(a.full, sample, rhwp, cache, a.jobs)
        a.cache.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        if not feats:
            report.append({"profile": name, "skipped": "읽은 문서 없음", "of": len(sample)})
            continue
        prof = style_profile.summarize(feats, [])
        prof.pop("sources", None)
        ok, strong = judge(prof)
        row = {"profile": name, "candidates": len(cands), "read": len(feats), "of": len(sample),
               "pass": ok, "strong": strong, "font": prof["recommend"].get("font"),
               "body_pt": (prof["recommend"].get("style") or {}).get("body_pt")}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if ok and a.write:
            n = prof["documents"]
            prof["name"] = name
            prof["corpus"] = {"by": by, "documents": n, "candidates": len(cands), "collected": a.collected,
                              "rhwp": version,
                              "note": f"공공기관 공개 문서 {n}건의 관측 통계({a.collected} 수집) — 공식 서식 기준이 아님"}
            path.write_text(json.dumps(prof, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.report:
        a.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    passed = [r["profile"] for r in report if r.get("pass")]
    print(f"통과 {len(passed)}종: {', '.join(passed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
