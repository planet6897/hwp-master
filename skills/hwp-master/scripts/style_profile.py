#!/usr/bin/env python3
"""문서 유형 프로필: 실제로 작성된 문서들에서 서식을 뽑아 등록하고 새 문서 기획에 적용한다.

    python3 style_profile.py analyze <문서|폴더 ...> [-o profile.json]       # 분석만 (등록 안 함)
    python3 style_profile.py register <문서|폴더 ...> --name <이름> [--force]  # 분석 + 등록
    python3 style_profile.py list                                           # 등록된 프로필
    python3 style_profile.py show <이름>                                     # 프로필 요약
    python3 style_profile.py lock <이름> <프로젝트> [--force]                # spec_lock.json 에 적용
    공통 옵션: [--rhwp PATH]

분석은 문서마다 용지·여백·글꼴·글자 크기·줄 간격·제목·항목 기호 체계·문장 종결·표·쪽 번호를 읽고,
문서 다수결로 권장값을 정한다. 값마다 일치율(그 값을 쓴 문서 비율)을 남긴다.
HWP 는 rhwp export-hwpx 로 임시 HWPX 를 만들어 읽는다. 원본 문서는 읽기만 한다.

등록 위치: `$HWP_MASTER_HOME/profiles/<이름>/profile.json` (기본 `~/.hwp-master/profiles`).
lock 은 권장값을 spec_lock.json 의 font·margins_mm·style·convention·footer 로 옮기고
"profile" 에 이름과 SHA-256 을 남긴다. 이미 있는 값은 --force 없이 덮어쓰지 않는다.
hwp-master 가 아직 만들 수 없는 서식(제목 번호 Ⅰ. 형식, 표 글자 크기 등)은 unsupported 에 적는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hds  # noqa: E402
import rhwp_locate  # noqa: E402

PROFILE_VERSION = "1"
NS = {"hh": "http://www.hancom.co.kr/hwpml/2011/head",
      "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
      "hc": "http://www.hancom.co.kr/hwpml/2011/core"}
HP = "{%s}" % NS["hp"]
HU_TO_MM = 25.4 / 7200
DOC_EXTS = (".hwp", ".hwpx")
NAME_RE = re.compile(r"^[^\s/\\:*?\"<>|.][^/\\:*?\"<>|]{0,63}$")
PAGE_FOOTER = {"text": "- {page} -"}
MIN_AGREE = 0.5  # 권장값으로 쓰려면 문서 절반 이상이 같은 값을 써야 한다(글자 크기는 ±0.5pt 묶음)

# 문단 맨 앞 기호 → 이름. 위에서부터 먼저 맞는 것.
MARKERS = [
    ("제N조", r"제\s*\d+\s*조"), ("제N장", r"제\s*\d+\s*[장절편관]"),
    ("Ⅰ.", r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.\s]"), ("I.", r"(?:I|II|III|IV|V|VI|VII|VIII|IX|X)\.\s"),
    ("1.", r"\d{1,2}\.\s"), ("가.", r"[가-하]\.\s"), ("1)", r"\d{1,2}\)"), ("가)", r"[가-하]\)"),
    ("(1)", r"\(\d{1,2}\)"), ("(가)", r"\([가-하]\)"), ("①", r"[①-⑳]"), ("㉮", r"[㉮-㉻㉠-㉭]"),
    ("□", r"[□■]"), ("○", r"[○◦ㅇ❍●]"), ("-", r"[-‐–―−]\s"), ("ㆍ", r"[ㆍ·∙•]"),
    ("▶", r"[▶▷►▸]"), ("◇", r"[◇◆]"), ("※", r"※"), ("*", r"\*"),
]
MARKER_RX = [(n, re.compile(r"^\s*" + p)) for n, p in MARKERS]
NOTE_MARKERS = {"※", "*"}  # 주석 기호는 항목 체계에서 뺀다
GAEJOSIK = {"□", "○", "-", "ㆍ"}
GONGMUN = ["1.", "가.", "1)", "가)", "(1)", "(가)", "①", "㉮"]
LIST_MARKER_CHAR = {"□": "□", "○": "○", "-": "-", "ㆍ": "ㆍ", "▶": "▶", "◇": "◇"}
# 제목 번호로 옮길 수 있는 기호 → (형식 문자열 틀, num_format). "{L}" 은 수준 번호.
HEADING_NUMBER = {"Ⅰ.": ("^{L}.", "roman_capital"), "I.": ("^{L}.", "roman_capital"),
                  "1.": ("^{L}.", "digit"), "가.": ("^{L}.", "hangul_syllable"),
                  "1)": ("^{L})", "digit"), "가)": ("^{L})", "hangul_syllable"),
                  "(1)": ("(^{L})", "digit"), "(가)": ("(^{L})", "hangul_syllable"), "①": ("^{L}", "circled_digit")}


def outline_of(system: List[str]) -> Optional[List[dict]]:
    """항목 체계가 로마 숫자 제목(Ⅰ.)으로 시작하면, 앞쪽 번호 기호들을 제목 번호(outline_numbering)로.

    1. 가. 로 시작하는 체계는 공문서 항목(본문 기호)으로 보고 제목 번호로 옮기지 않는다."""
    if not system or system[0] not in ("Ⅰ.", "I."):
        return None
    out = []
    for m in system:
        if m not in HEADING_NUMBER:
            break
        text, fmt = HEADING_NUMBER[m]
        out.append({"text": text.replace("{L}", str(len(out) + 1)), "num_format": fmt})
    return out


# ---------------------------------------------------------------- 문서 한 건 읽기

def marker_of(text: str) -> Optional[str]:
    for name, rx in MARKER_RX:
        if rx.match(text):
            return name
    return None


def ending_of(text: str) -> Optional[str]:
    """문장 종결: narrative(~다)·nominal(~함·~임)·noun(명사로 끝남)."""
    t = re.sub(r"[\s.。]+$", "", text)
    if not t:
        return None
    if re.search(r"(다|요|까)$", t):
        return "narrative"
    if re.search(r"(함|임|음|됨|짐|봄)$", t):
        return "nominal"
    if re.search(r"[가-힣]$", t):
        return "noun"
    return None


def _head(z: zipfile.ZipFile):
    h = ET.fromstring(z.read("Contents/header.xml"))
    fonts = {}
    for ff in h.iter("{%s}fontface" % NS["hh"]):
        if ff.get("lang") == "HANGUL":
            for f in ff.findall("hh:font", NS):
                fonts[f.get("id")] = f.get("face")
    chars = {}
    for c in h.iter("{%s}charPr" % NS["hh"]):
        fr = c.find("hh:fontRef", NS)
        chars[c.get("id")] = {"pt": int(c.get("height", "1000")) / 100,
                              "font": fonts.get(fr.get("hangul")) if fr is not None else None,
                              "bold": c.find("hh:bold", NS) is not None}
    paras = {}
    for p in h.iter("{%s}paraPr" % NS["hh"]):
        al = p.find("hh:align", NS)
        ls = p.find(".//hh:lineSpacing", NS)
        paras[p.get("id")] = {"align": al.get("horizontal") if al is not None else None,
                              "ls": (ls.get("type"), int(ls.get("value"))) if ls is not None else None}
    return chars, paras


def _walk(el, in_table: bool, out: dict) -> None:
    for ch in el:
        tag = ch.tag
        if tag == HP + "tbl":
            out["tables"] += 1
            _walk(ch, True, out)
            continue
        if tag in (HP + "header", HP + "footer", HP + "footNote", HP + "endNote"):
            # 머리말·꼬리말·각주 글은 본문 서식·항목 기호로 세지 않는다 (꼬리말 "- 1 -" 의 "-" 따위)
            if tag in (HP + "header", HP + "footer"):
                out[tag[len(HP):]] = True
            if any(x.tag == HP + "pageNum" or (x.tag == HP + "autoNum" and x.get("numType") == "PAGE")
                   for x in ch.iter()):
                out["pagenum"] = True
            continue
        if tag == HP + "pageNum" or (tag == HP + "autoNum" and ch.get("numType") == "PAGE"):
            out["pagenum"] = True
        if tag == HP + "p":
            runs = [(r.get("charPrIDRef"), "".join(t.text or "" for t in r.iter(HP + "t")))
                    for r in ch.findall("hp:run", NS)]
            out["paras"].append({"pp": ch.get("paraPrIDRef"), "table": in_table, "runs": runs})
            for r in ch.findall("hp:run", NS):
                _walk(r, in_table, out)
            continue
        _walk(ch, in_table, out)


def read_features(hwpx: Path) -> dict:
    """HWPX 한 건의 서식 특징."""
    with zipfile.ZipFile(hwpx) as z:
        chars, paras = _head(z)
        sections = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
        out = {"tables": 0, "header": False, "footer": False, "pagenum": False, "paras": []}
        page = None
        for name in sections:
            root = ET.fromstring(z.read(name))
            if page is None:
                pp = root.find(".//hp:pagePr", NS)
                if pp is not None:
                    m = pp.find("hp:margin", NS)
                    w, h = int(pp.get("width")), int(pp.get("height"))
                    page = {"width_mm": round(w * HU_TO_MM), "height_mm": round(h * HU_TO_MM),
                            "landscape": w > h,
                            "margins_mm": {k: round(int(m.get(k)) * HU_TO_MM) for k in hds.MARGIN_KEYS}
                            if m is not None else None}
            _walk(root, False, out)
    body_chars, table_chars, body_pp = Counter(), Counter(), Counter()
    markers, order, ends, top_pt = Counter(), [], Counter(), []
    texts = []
    for p in out["paras"]:
        text = "".join(t for _, t in p["runs"])
        n = len(text.strip())
        if not n:
            continue
        for cid, t in p["runs"]:
            (table_chars if p["table"] else body_chars)[cid] += len(t.strip())
        if p["table"]:
            continue
        body_pp[p["pp"]] += n
        texts.append((p, text))
        m = marker_of(text)
        if m and m not in NOTE_MARKERS:
            markers[m] += 1
            if m not in order:
                order.append(m)
        e = ending_of(text)
        if e and n > 15:
            ends[e] += 1
    # 최상위 항목(첫 기호) 문단의 글자 크기 = 개요 글자 크기 후보
    if order:
        for p, text in texts:
            if marker_of(text) == order[0]:
                cid = next((c for c, t in p["runs"] if t.strip()), None)
                if cid in chars:
                    top_pt.append(chars[cid]["pt"])

    def main_char(cnt: Counter) -> Optional[dict]:
        if not cnt:
            return None
        c = chars.get(cnt.most_common(1)[0][0], {})
        return {"font": c.get("font"), "pt": c.get("pt")}

    title = None
    for p, text in texts[:8]:
        for cid, t in p["runs"]:
            c = chars.get(cid)
            if c and t.strip() and (title is None or c["pt"] > title["pt"]):
                title = {"pt": c["pt"], "bold": c["bold"], "align": paras.get(p["pp"], {}).get("align")}
    body_pp_main = paras.get(body_pp.most_common(1)[0][0], {}) if body_pp else {}
    return {
        "page": page, "tables": out["tables"],
        "header": out["header"], "footer": out["footer"], "pagenum": out["pagenum"],
        "body_chars": sum(body_chars.values()), "table_chars": sum(table_chars.values()),
        "body": main_char(body_chars), "table_text": main_char(table_chars),
        "line_spacing": body_pp_main.get("ls"), "body_align": body_pp_main.get("align"),
        "title": title, "top_marker_pt": statistics.median(top_pt) if top_pt else None,
        "markers": dict(markers), "marker_order": order[:6], "endings": dict(ends),
    }


def to_hwpx(src: Path, rhwp: str, work: Path) -> Path:
    if src.suffix.lower() == ".hwpx":
        return src
    dst = work / (hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:16] + ".hwpx")
    proc = subprocess.run([rhwp, "export-hwpx", str(src), str(dst)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"export-hwpx 실패 (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    return dst


def collect(paths: Iterable[Path]) -> List[Path]:
    """문서 경로 목록. 폴더는 아래의 .hwp/.hwpx 를 모두 넣는다(이름순)."""
    out: List[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in DOC_EXTS))
        elif p.is_file() and p.suffix.lower() in DOC_EXTS:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 다수결

def _freeze(v):
    """JSON 에서 다시 읽은 특징(리스트)도 튜플처럼 셀 수 있게 한다."""
    return tuple(_freeze(x) for x in v) if isinstance(v, (list, tuple)) else v


def _vote(values: List, n: int, key=lambda v: v):
    """(최빈값, 일치율). 값이 없으면 (None, 0)."""
    vals = [_freeze(v) for v in values if v is not None]
    if not vals:
        return None, 0.0
    cnt = Counter(key(v) for v in vals)
    k, c = cnt.most_common(1)[0]
    rep = next(v for v in vals if key(v) == k)
    return rep, round(c / n, 2)


def _pt_vote(values: List[Optional[float]], n: int):
    """글자 크기: ±0.5pt 안의 값을 한 묶음으로 세고, 묶음 중앙값을 0.5 단위로 낸다."""
    vals = sorted(v for v in values if v)
    if not vals:
        return None, 0.0
    best, best_n = None, 0
    for v in vals:
        group = [x for x in vals if abs(x - v) <= 0.5]
        if len(group) > best_n:
            best, best_n = group, len(group)
    return round(statistics.median(best) * 2) / 2, round(best_n / n, 2)


def marker_system(order: List[str]) -> List[str]:
    """문서 기호 순서 → 항목 체계(앞 3단계)."""
    return order[:3]


def convention_of(system: List[str]) -> Optional[str]:
    body = [m for m in system if m not in ("Ⅰ.", "I.", "제N조", "제N장")]
    if body and body[0] in GAEJOSIK:
        return "gaejosik"
    if body and body[0] in GONGMUN:
        return "gongmun"
    return None


def summarize(features: List[dict], sources: List[dict]) -> dict:
    """문서 특징 목록 → 프로필 (recommend·agreement·unsupported·stats)."""
    n = len(features)
    rec: Dict[str, object] = {}
    agree: Dict[str, float] = {}
    unsupported: List[str] = []

    page, a = _vote([f["page"] and (f["page"]["width_mm"], f["page"]["height_mm"], f["page"]["landscape"])
                     for f in features], n)
    if page:
        agree["page"] = a
        if a >= MIN_AGREE:
            w, h, land = page
            rec["page_size"] = {"width_mm": min(w, h), "height_mm": max(w, h)}
            if land:
                rec["orientation"] = "landscape"
    margins, a = _vote([f["page"] and f["page"].get("margins_mm") and
                        tuple(sorted(f["page"]["margins_mm"].items())) for f in features], n)
    if margins:
        agree["margins_mm"] = a
        m = dict(margins)
        geo_ok = not hds.validate_page({"margins_mm": m, **({"page_size": rec["page_size"]} if "page_size" in rec else {})})
        if a >= MIN_AGREE and geo_ok:
            rec["margins_mm"] = m
    font, a = _vote([(f.get("body") or f.get("table_text") or {}).get("font") for f in features], n)
    if font:
        agree["font"] = a
        if a >= MIN_AGREE:
            rec["font"] = font
    style: Dict[str, object] = {}
    body_pt, a = _pt_vote([(f.get("body") or {}).get("pt") for f in features], n)
    if body_pt:
        agree["body_pt"] = a
        if a >= MIN_AGREE:
            style["body_pt"] = body_pt
    title_pt, a = _pt_vote([(f.get("title") or {}).get("pt") for f in features], n)
    if title_pt:
        agree["title_pt"] = a
        if a >= MIN_AGREE and (not body_pt or title_pt > body_pt):
            style["title_pt"] = title_pt
    head_pt, a = _pt_vote([f.get("top_marker_pt") for f in features], n)
    if head_pt:
        agree["heading_pt"] = a
        if a >= MIN_AGREE and (not body_pt or head_pt >= body_pt):
            style["heading_pt"] = head_pt
    tpt, a = _pt_vote([(f.get("table_text") or {}).get("pt") for f in features if f.get("table_chars")],
                      max(1, sum(1 for f in features if f.get("table_chars"))))
    if tpt:
        agree["table_pt"] = a
        if a >= MIN_AGREE:
            style["table_pt"] = tpt
    ls, a = _vote([f.get("line_spacing") for f in features], n)
    if ls:
        agree["line_spacing"] = a
        kind, value = ls
        if a >= MIN_AGREE:
            if kind == "PERCENT" and hds.STYLE_LS_RANGE[0] <= value <= hds.STYLE_LS_RANGE[1]:
                style["line_spacing"] = value
            else:
                unsupported.append(f"줄 간격 {kind} {value} — hwp-master style.line_spacing 은 퍼센트만 씁니다")
    for k in list(style):
        bad = hds.validate_style({"style": {k: style[k]}})
        if bad:
            unsupported.append(f"style.{k}={style.pop(k)} — 허용 범위 밖")
    if style:
        rec["style"] = style
    system, a = _vote([tuple(marker_system(f["marker_order"])) or None for f in features], n)
    if system:
        agree["marker_system"] = a
        system = list(system)
        rec["marker_system"] = system
        conv = convention_of(system)
        markers = [LIST_MARKER_CHAR[m] for m in system if m in LIST_MARKER_CHAR]
        if a >= MIN_AGREE:
            if conv:
                rec["convention"] = conv
            if markers and system[0] in LIST_MARKER_CHAR:
                rec["list_markers"] = markers
        outline = outline_of(system) if a >= MIN_AGREE else None
        if outline:
            rec["outline_numbering"] = outline
            if system[len(outline):] and system[len(outline)] in LIST_MARKER_CHAR:
                rec["list_markers"] = [LIST_MARKER_CHAR[m] for m in system[len(outline):] if m in LIST_MARKER_CHAR]
                rec["convention"] = convention_of(system[len(outline):]) or rec.get("convention")
    pagenum = sum(1 for f in features if f["pagenum"]) / n
    agree["pagenum"] = round(max(pagenum, 1 - pagenum), 2)  # 넣는 쪽이든 안 넣는 쪽이든 다수결 비율
    if pagenum >= MIN_AGREE:
        rec["footer"] = dict(PAGE_FOOTER)
    ends = Counter()
    for f in features:
        ends.update(f["endings"])
    total = sum(ends.values())
    if total:
        nominal = (ends["nominal"] + ends["noun"]) / total
        rec["ending"] = "nominal" if nominal >= 0.5 else "narrative"
        agree["ending"] = round(max(nominal, 1 - nominal), 2)
    title_align, a = _vote([(f.get("title") or {}).get("align") for f in features], n)
    stats = {
        "tables_median": statistics.median([f["tables"] for f in features]),
        "table_text_share_median": round(statistics.median(
            [f["table_chars"] / max(f["table_chars"] + f["body_chars"], 1) for f in features]), 2),
        "header_pct": round(sum(f["header"] for f in features) / n, 2),
        "title_align": title_align, "title_bold_pct": round(
            sum(1 for f in features if (f.get("title") or {}).get("bold")) / n, 2),
        "endings": dict(ends),
        "marker_docs": dict(Counter(m for f in features for m in f["markers"])),
    }
    return {"version": PROFILE_VERSION, "documents": n, "recommend": rec, "agreement": agree,
            "unsupported": unsupported, "stats": stats, "sources": sources}


def analyze(paths: List[Path], rhwp: str) -> dict:
    docs = collect(paths)
    if not docs:
        raise SystemExit("분석할 .hwp/.hwpx 문서가 없습니다")
    features, sources, failed = [], [], []
    with tempfile.TemporaryDirectory(prefix="hwpm-profile-") as tmp:
        for d in docs:
            try:
                features.append(read_features(to_hwpx(d, rhwp, Path(tmp))))
                sources.append({"file": str(d), "sha256": sha256(d)})
            except Exception as exc:  # 손상 문서 등은 건너뛰고 보고한다
                failed.append({"file": str(d), "error": f"{type(exc).__name__}: {exc}"[:300]})
    if not features:
        raise SystemExit(f"읽을 수 있는 문서가 없습니다 ({len(failed)}건 실패)")
    prof = summarize(features, sources)
    prof["failed"] = failed
    prof["rhwp"] = rhwp_locate.locate(rhwp).get("version") if rhwp else None
    prof["created"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return prof


# ---------------------------------------------------------------- 등록·적용

def profiles_dir() -> Path:
    home = Path(os.environ.get("HWP_MASTER_HOME") or Path.home() / ".hwp-master")
    return home / "profiles"


def profile_path(name: str) -> Path:
    return profiles_dir() / name / "profile.json"


def check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise SystemExit(f"프로필 이름 {name!r} 을 쓸 수 없습니다 (경로 문자·공백 시작 불가, 64자 이하)")


def register(prof: dict, name: str, force: bool) -> Path:
    check_name(name)
    path = profile_path(name)
    if path.exists() and not force:
        raise SystemExit(f"프로필 {name!r} 이 이미 있습니다 — 바꾸려면 --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    prof = dict(prof, name=name)
    path.write_text(json.dumps(prof, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load(name: str) -> dict:
    check_name(name)
    path = profile_path(name)
    if not path.exists():
        raise SystemExit(f"프로필 {name!r} 이 없습니다 ({path})")
    return json.loads(path.read_text(encoding="utf-8"))


LOCK_KEYS = ("font", "page_size", "orientation", "margins_mm", "style", "convention", "footer", "outline_numbering")


def lock(name: str, project: Path, force: bool, rhwp: Optional[str] = None) -> dict:
    """프로필 권장값 → spec_lock.json. 반환: {"set": [...], "kept": [...]}.

    rhwp 가 제목 번호 모양(outline_numbering)을 지원하지 않으면 그 값은 옮기지 않고 unsupported 에 적는다."""
    prof = load(name)
    unsupported = list(prof.get("unsupported", []))
    skip = set()
    if prof["recommend"].get("outline_numbering"):
        import native
        if not rhwp or not native.supports_outline_numbering(rhwp):
            skip.add("outline_numbering")
            unsupported.append("제목 번호 모양(outline_numbering) — 설치된 rhwp 가 지원하지 않아 적용하지 않음")
    lock_path = project / "spec_lock.json"
    spec = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    changed, kept = [], []
    for k in LOCK_KEYS:
        if k not in prof["recommend"] or k in skip:
            continue
        if k in spec and spec[k] != prof["recommend"][k] and not force:
            kept.append(k)
            continue
        spec[k] = prof["recommend"][k]
        changed.append(k)
    raw = profile_path(name).read_bytes()
    spec["profile"] = {"name": name, "sha256": hashlib.sha256(raw).hexdigest()}
    problems = (hds.validate_page(spec, "spec_lock.json") + hds.validate_style(spec, "spec_lock.json")
                + hds.validate_outline_numbering(spec, "spec_lock.json:"))
    if problems:
        raise SystemExit("; ".join(f"[{f.code}] {f.message}" for f in problems))
    lock_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"set": changed, "kept": kept, "spec_lock": str(lock_path),
            "list_markers": prof["recommend"].get("list_markers"), "ending": prof["recommend"].get("ending"),
            "unsupported": unsupported}


def describe(prof: dict) -> str:
    r, a = prof["recommend"], prof["agreement"]

    def pct(k):
        return f" ({round(100 * a[k])}%)" if k in a else ""
    st = r.get("style", {})
    lines = [f"프로필 {prof.get('name', '(미등록)')} — 문서 {prof['documents']}건"
             + (f", 읽기 실패 {len(prof.get('failed', []))}건" if prof.get("failed") else "")]
    if "margins_mm" in r:
        m = r["margins_mm"]
        lines.append(f"  여백(mm): 좌{m['left']} 우{m['right']} 위{m['top']} 아래{m['bottom']}{pct('margins_mm')}")
    for label, val, k in (("글꼴", r.get("font"), "font"), ("본문", st.get("body_pt"), "body_pt"),
                          ("개요", st.get("heading_pt"), "heading_pt"), ("제목", st.get("title_pt"), "title_pt"),
                          ("표 글자", st.get("table_pt"), "table_pt"),
                          ("줄 간격", st.get("line_spacing"), "line_spacing")):
        if val is not None:
            unit = "%" if k == "line_spacing" else ("pt" if k.endswith("_pt") else "")
            lines.append(f"  {label}: {val}{unit}{pct(k)}")
    if r.get("marker_system") and a.get("marker_system", 0) >= MIN_AGREE:
        lines.append(f"  항목 체계: {' → '.join(r['marker_system'])}{pct('marker_system')}"
                     + (f" → 규약 {r['convention']}" if r.get("convention") else ""))
    if r.get("outline_numbering"):
        lines.append("  제목 번호: " + " → ".join(
            native_number(lv) for lv in r["outline_numbering"]))
    if r.get("ending"):
        lines.append(f"  문장 종결: {'명사형(~함·~임)' if r['ending'] == 'nominal' else '서술형(~다)'}{pct('ending')}")
    lines.append(f"  쪽 번호: {'넣음' if r.get('footer') else '넣지 않음'}{pct('pagenum')}")
    low = [k for k, v in a.items() if v < MIN_AGREE]
    if r.get("marker_system") and a.get("marker_system", 0) < MIN_AGREE:
        lines.append(f"  가장 많은 항목 체계(참고): {' → '.join(r['marker_system'])}{pct('marker_system')}")
    if low:
        lines.append(f"  일치율이 낮아 권장값에서 뺀 항목: {', '.join(low)}")
    for u in prof.get("unsupported", []):
        lines.append(f"  적용 불가: {u}")
    return "\n".join(lines)


def native_number(lv: dict) -> str:
    """제목 번호 한 수준의 첫 번호 모양 (예: {"text": "^1.", "num_format": "roman_capital"} → "I.")."""
    import native
    return re.sub(r"\^([1-7])", lambda m: native.format_number(1, lv.get("num_format", "digit")), lv["text"])


def main() -> int:
    ap = argparse.ArgumentParser(description="문서 유형 프로필 분석·등록·적용")
    ap.add_argument("--rhwp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("-o", "--output", type=Path)
    p = sub.add_parser("register")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--name", required=True)
    p.add_argument("--force", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("name")
    p = sub.add_parser("lock")
    p.add_argument("name")
    p.add_argument("project", type=Path)
    p.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.cmd in ("analyze", "register"):
        rhwp = rhwp_locate.locate(args.rhwp)["path"]
        prof = analyze(args.paths, rhwp)
        if args.cmd == "analyze":
            if args.output:
                args.output.write_text(json.dumps(prof, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(describe(prof))
        else:
            path = register(prof, args.name, args.force)
            print(describe(dict(prof, name=args.name)))
            print(json.dumps({"ok": True, "profile": str(path)}, ensure_ascii=False))
    elif args.cmd == "list":
        root = profiles_dir()
        for d in sorted(root.glob("*/profile.json")) if root.exists() else []:
            prof = json.loads(d.read_text(encoding="utf-8"))
            print(f"{d.parent.name}\t문서 {prof['documents']}건\t{prof.get('created', '')}")
    elif args.cmd == "show":
        print(describe(load(args.name)))
    elif args.cmd == "lock":
        rhwp = rhwp_locate.locate(args.rhwp)["path"]
        print(json.dumps(lock(args.name, args.project, args.force, rhwp), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    from console import utf8_stdio
    utf8_stdio()
    sys.exit(main())
