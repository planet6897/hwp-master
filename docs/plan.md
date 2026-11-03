# hwp-master 계획: rhwp 기반 "자료 → 편집 가능한 한글 문서" 에이전트 워크플로

- 작성: 2026-11-01 (KST)
- 상태: 초안 (구현 전 계획)
- 기준 바이너리: `rhwp v0.9.0` (`target/pr-review/release/rhwp`)

## 0. 한 줄 요약

자료(PDF·DOCX·MD·웹·기존 HWP) 또는 주제를 받아 **한컴에서 바로 편집할 수 있는 네이티브 HWPX/HWP**
(스타일·개요 번호·표·그림·각주·머리말/꼬리말이 모두 실제 한글 개체)를 만드는 Claude Code 스킬
`hwp-master`를 만든다. 문서를 만드는 결정적 엔진은 rhwp이고, 스킬은 판단(기획·집필)과
규약 검사·시각 검토 루프를 담당한다.

## 1. 참고 프로젝트와 가져올 것

| 프로젝트 | 성격 | hwp-master가 가져올 것 | 가져오지 않을 것 |
|---|---|---|---|
| [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) (MIT) | 자료 → 네이티브 PPTX. Strategist → Executor(쪽마다 SVG 직접 작성) → 품질 검사기 → `svg_to_pptx.py` | 프로젝트 폴더 규약, 역할 전환(Strategist/Executor), `design_spec.md` + `spec_lock.md` 계약, "오류는 차단·경고는 비차단" 검사기, 래스터 대체 금지(미지원이면 실패), 단일 에이전트 직렬 작성, Quick 모드 | 쪽 단위 고정 좌표 SVG 중간 표현(HWP는 흐름 조판이므로 부적합) |
| [hawa130/docx-master](https://github.com/hawa130/docx-master) (Apache-2.0) | Word OOXML 직접 수정 스킬. 스타일 바인딩 규약, 자동 번호, 캡션/상호참조, `audit`, `apply --dry-run` | **문서 작성 규약 계층**: 모든 문단은 이름 있는 스타일에 바인딩, 번호는 자동 번호, 규약 위반 `audit`, `overview`→`inspect`→`apply` 도구 흐름, 원본 무수정·새 파일 산출 | Word 전용 OOXML 세부 구현 |
| [dashbear-ai/grant-master](https://github.com/dashbear-ai/grant-master) (MIT) | 중국어 과제 신청서 집필 파이프라인. 과제 준비 → 문헌 조사 → 방안 → 개요 → 단위(unit) 집필 → Word 출력 | **장문 집필 방식**: 개요의 "단위 계획"(분량·논증 과제·근거·경계), 단위별 집필·반복 수정, `history/` 버전 보존 | Codex 전용 웹 워크벤치(초기 범위 밖) |

판단: HWP 문서는 슬라이드가 아니라 **흐름 조판 문서**이므로, 뼈대는 ppt-master의 워크플로를 따르되
중간 표현과 품질 기준은 docx-master(규약·감사)와 grant-master(장문 단위 집필) 쪽이 더 가깝다.
세 프로젝트의 코드는 복사하지 않고 설계 개념만 참고해 독립 구현한다.

## 2. 목표와 비목표

### 목표

1. 자료/주제 → 편집 가능한 HWPX(필요하면 HWP5 변환·PDF 미리보기)를 한 번의 스킬 호출로 만든다.
2. 한국 공공·기업 문서 유형을 1급 시민으로 다룬다: 개조식 보고서, 사업계획서, 제안서, 공문(기안문),
   회의록, 결과보고서.
3. 결과물은 "그림으로 박은 문서"가 아니라 한컴에서 스타일·번호·표를 그대로 고칠 수 있는 문서다.
4. 모든 단계의 판정은 rhwp `--json` 봉투 데이터로 남기고, 작업 영수증(`replay --capsule`)으로 재현할 수 있게 한다.

### 비목표 (초기)

- 한컴 수준의 자유 디자인(복잡한 도형 배치·잡지형 레이아웃).
- 웹 워크벤치 UI. (M6 이후 rhwp-studio 연동으로 검토)
- rhwp 코어의 대규모 재작성. 필요한 코어 확장은 개별 이슈·PR로 rhwp 정본 게이트를 통과시킨다.

## 3. rhwp 현황: 이미 있는 자산과 빈 곳

### 이미 있는 것

| 필요 기능 | rhwp 자산 |
|---|---|
| 새 문서 생성 | `rhwp scaffold <spec.json> -o out.hwpx` — `src/scaffold/schema.rs`의 `ScaffoldSpec` (title·font·page_size·blocks) |
| 생성 블록 | 현재 `heading`(개요 1~7)·`paragraph`(평문)·`table`(평문 셀, `cell_align`) 3종 |
| 전용 빌더 선례 | `rhwp build-from-ingest` + `src/document_core/builders/exam_paper.rs` (시험지: 지문·선택지·그림 배치) |
| 코어 편집 API | 그림 `insert_picture_native`, 수식 `insert_equation_native`, 도형 `create_shape_control_native`, 각주 `footnote_ops`, 머리말/꼬리말 `header_footer_ops`, 표 `table_ops`, HTML 붙여넣기 `paste_html_native` |
| 서식 기반 생성 | `rhwp run` 계획서: `fill_template`, `import_paragraph_block`, `repeat_and_fill_paragraph_block`, `repeat_and_fill_table_rows`, `fill_fields`, `set_cell` |
| 서식 수정 | `edit apply-style`, `apply-char-format`, `apply-para-format`, `insert-table`, `insert-image`, `insert-footnote`, `insert-header-footer` 등 |
| 입력 정규화 | `import-pdf`, `export-markdown`, `export-structure`, `export-tables`, `digest` |
| 출력 | HWPX(기본), `convert`(→HWP5), `export-pdf`, `export-png`, `export-svg` |
| 검사 | `verify`, `layout-anomaly`, `render-diff`, `export-structure`, `word-count`, `inspect`(보안) |
| 증명 | `replay --capsule`, `lineage`, `audit` |

### 빈 곳 (hwp-master를 위해 채워야 할 것)

1. **scaffold 표현력**: 글자 서식 run(굵게·기울임·색·크기), 스타일 참조, 글머리표/번호 목록,
   그림·캡션, 각주, 수식, 쪽 나눔, 머리말/꼬리말·쪽 번호, 표 병합·열 폭·테두리·음영, 표지, 차례.
   코어 API는 대부분 있으므로 **새 조판 로직이 아니라 scaffold 빌더가 기존 API를 호출하도록 연결**하는 작업이 중심이다.
2. **서식(템플릿) 기반 scaffold**: 빈 문서 대신 기관 서식 HWPX의 스타일·용지·머리말을 상속해 블록을 채우는 모드.
3. **규약 감사기**: "개요 번호를 손으로 쳤다", "본문이 스타일 없이 직접 서식", "공문 항목 기호 순서 위반" 같은 규약 위반 검출.

## 4. 아키텍처

### 4.1 파이프라인

```
[입력: 주제 / PDF·DOCX·MD·웹 / 기존 HWP·HWPX / 기관 서식 HWPX]
   │ 1. intake      : source_to_md (rhwp import-pdf · export-markdown · digest 재사용)
   │ 2. project init: projects/<이름>_<YYYYMMDD>/ 생성, sources/ 보관
   ▼
[Strategist] ── 사용자 확인 게이트(BLOCKING)
   │  doc_spec.md   : 문서 유형·독자·목적·분량·톤·장 구성
   │  spec_lock.json: 용지·여백·글꼴·스타일 표·번호 체계·서식 HWPX SHA-256 (기계용 계약)
   │  outline.json  : 장/절 + 단위(unit) 계획(분량·논증 과제·근거 출처·경계)
   ▼
[Executor] ── 단위별 직렬 집필 (생성 스크립트로 일괄 생성 금지)
   │  units/U01.json … : hwp-master 문서 명세(HDS, §4.2) 블록
   │  U01~U03 후 early 검사 → 나머지 집필 → final 검사
   ▼
[Assembler] ── 결정적 조립
   │  hds_assemble.py → build/document.hds.json
   │  rhwp scaffold (v2) --template <서식.hwpx> -o exports/<이름>.hwpx --json
   ▼
[Checker] ── 오류 차단 / 경고 비차단
   │  hds_lint (명세·규약) → rhwp verify · export-structure · layout-anomaly
   │  → export-png 쪽 썸네일로 시각 자기 검토(옵션)
   ▼
[Export]  exports/<이름>.hwpx  (+ convert → .hwp, export-pdf → 미리보기 PDF)
          validation/report.json, capsules/*.capsule.json
```

역할은 ppt-master처럼 **별도 에이전트가 아니라 한 에이전트가 필요할 때 읽는 지시 범위**다.
역할 전환 시 `## [역할 전환: Strategist]`와 읽는 참조 파일을 명시한다.

### 4.2 중간 표현: hwp-master 문서 명세(HDS) = scaffold v2 JSON

ppt-master가 "SVG는 PPT Master에 맞춘다(전체 SVG 표준을 따르지 않는다)"고 한 것처럼,
HDS는 **rhwp가 결정적으로 네이티브 개체로 바꿀 수 있는 블록만** 허용한다.

- 형식: JSON, `deny_unknown_fields`, JSON Schema 공개(`rhwp export-scaffold-schema` 신설 또는 기존 schema registry 확장).
- 단위 파일 하나 = 블록 배열. 조립 시 순서대로 이어 붙인다.
- 미지원 구성은 **실패**한다. 그림으로 대체하지 않는다.

블록 후보(우선순위 순):

| 우선 | 블록 | 주요 필드 | 매핑할 rhwp 자산 |
|---|---|---|---|
| P0 | `heading` | level, text(runs) | 기존 |
| P0 | `paragraph` | runs[{text,bold,italic,underline,color,size_pt}], style | 기존 + `apply-char-format` 경로 |
| P0 | `table` | rows, col_widths_mm, merges, header_rows, cell_align, shading | 기존 + `table_ops` (병합·열 폭) |
| P0 | `list` | kind(bullet/number/공문항목), items[{level,runs}] | 문단 번호/글머리표 |
| P0 | `page_break` | — | `insert-page-break` |
| P1 | `image` | path, width_mm, caption, align | `insert_picture_native` |
| P1 | `footnote` | (paragraph run 안의 inline) | `footnote_ops` |
| P1 | `header_footer` | header/footer text, page_number 형식 | `header_footer_ops` |
| P1 | `caption` + 자동 번호 | 표·그림 번호 | 캡션 컨트롤 |
| P2 | `equation` | 한글 수식 스크립트 | `insert_equation_native` |
| P2 | `cover` | 제목·부제·기관·일자 | 서식의 표지 블록 + `fill_template` |
| P2 | `toc` | 깊이 | 차례 컨트롤(지원 범위 확인 필요) |
| P3 | `chart` | type, categories, series | `csv-to-chart`(기존 차트 서식 복제 방식) |
| P3 | `textbox`/`shape` | 간단한 강조 상자 | `create_shape_control_native` |

대안 검토: HTML 부분 집합 + `paste_html_native`. LLM 친화성은 높지만 판정이 데이터로 남지 않고
속성 해석이 휴리스틱이 되기 쉽다. **M0에서 두 방식을 같은 샘플로 비교해 확정**하되, 기본안은 HDS(JSON)다.

### 4.3 서식(템플릿) 체계

ppt-master의 brand/style/layout/deck 4분류를 HWP에 맞춰 다음처럼 바꾼다.

| 종류 | 내용 | 예 |
|---|---|---|
| `house` (기관 서식) | 실제 HWPX 파일: 용지·여백·글꼴·스타일 정의·머리말/꼬리말·표지 | 기관 보고서 양식, 사업계획서 표준 서식 |
| `convention` (작성 규약) | 번호 체계·항목 기호·문장 형식 규칙(JSON) | 공문서 항목 기호 `1. → 가. → 1) → 가) → (1) → (가) → ① → ㉮`, 개조식 문장 끝맺음 |
| `doctype` (문서 유형) | 장 구성 골격·필수 절·분량 기준 | 개조식 보고서(추진 배경/현황/추진 계획/기대 효과), 사업계획서, 기안문 |

- 서식은 사용자가 명시할 때만 적용한다(주제 유사도로 추정하지 않는다).
- 서식 등록 시 `rhwp export-structure`·스타일 목록으로 **스타일 표(style map)** 를 추출해
  `templates/house/<이름>/style_map.json`(HDS 역할 → 서식 스타일 번호)을 만든다.
- 원본 서식 HWPX는 SHA-256을 `spec_lock.json`에 고정한다(`run`의 `preconditions.inputSha256`과 같은 철학).

### 4.4 검사기 (hds_lint + rhwp 판정)

| 단계 | 검사 | 차단 여부 |
|---|---|---|
| 명세 | HDS 스키마, 미지원 블록, 이미지 경로·크기 | 오류 |
| 규약 | 수동 번호("1.1" 직접 입력), 스타일 미바인딩 문단, 항목 기호 순서, 표 머리행 누락 | 오류/경고 (규약별 지정) |
| 내용 | 분량 대비 ±20%, 근거 출처 누락, 미해결 `TODO` | 경고 |
| 산출물 | `rhwp verify --min-pages/--contains/--format`, `export-structure` 개요가 outline과 일치 | 오류 |
| 조판 | `rhwp layout-anomaly --strict` (overflow·겹침·빈 쪽) | 오류 |
| 시각 | `export-png` 썸네일을 에이전트가 직접 판독(옵션 `visual-review`) | 사용자 판단 |
| 보안 | 외부 자료 기반이면 `inspect injection`·`armor`로 입력 격리 | 경고 |

검사기는 **검사만 하고 자동 수정하지 않는다**(ppt-master·docx-master 공통 원칙).
자기 해석을 자기 기준으로 채점하는 동어반복을 피하기 위해, 산출물 검사는 **다시 파싱한 rhwp 결과**로 한다.

### 4.5 라우트

| 라우트 | 입력 | 경로 |
|---|---|---|
| Default | 주제/자료 | 전체 파이프라인 |
| Quick | 짧은 문서 | Strategist·확인 게이트 생략, 단일 HDS → scaffold → final 검사 (재개 불가) |
| Template Fill | 기관 서식 + 자료 | 구현: 자리표시(`{{키}}`·`{{목록.필드}}`)·누름틀 → `run` 반복·채움 계획 사슬(`template_fill.py`, `workflows/template.md`). 서식 상속 scaffold(`--template`)는 rhwp 코어 확장 대기 |
| Edit Native | 기존 HWP/HWPX | `rhwp-safe-edit` 경로(`run` 계획서·`--dry-run`·`--verify`), 원본 무수정 |
| Beautify | 기존 문서 | 내용·쪽 구성 유지, 스타일 바인딩·번호 자동화만 적용(`apply-style`·`apply-para-format`) |
| Convert-in | DOCX/PDF/MD | intake → HDS → scaffold (서식 정규화 포함) |
| Mail-merge | 서식 + 명단 | 기존 `rhwp batch fill` 위임 |
| Exam | 시험지 자료 | 기존 `rhwp-exam-ingest` 위임 |

## 5. 디렉터리 구조 (제안)

```
hwp-master/                       # github.com/planet6897/hwp-master (rhwp는 의존 바이너리)
├── .claude-plugin/marketplace.json
├── skills/hwp-master/
│   ├── SKILL.md                  # 인덱스·라우팅 표·정지 규칙만
│   ├── references/
│   │   ├── strategist.md         # 문서 유형 판단·확인 게이트
│   │   ├── executor.md           # HDS 작성 규칙·단위 집필
│   │   ├── hds_authoring.md      # 블록별 작성법·금지 사항
│   │   ├── conventions_kr.md     # 공문서·개조식 작성 규약
│   │   └── visual_review.md
│   ├── workflows/{generate,template-fill,edit-native,beautify}.md
│   ├── schemas/hds_v2.schema.json # rhwp 정본 스키마의 사본(버전 고정·드리프트 검사)
│   ├── scripts/
│   │   ├── project_manager.py    # init / import-sources / validate / unit-context
│   │   ├── source_to_md.py       # rhwp import-pdf·export-markdown 래퍼 (+ 선택 pandoc)
│   │   ├── hds_assemble.py       # units → document.hds.json
│   │   ├── hds_lint.py           # 명세·규약 검사 (--stage early|final --json)
│   │   ├── build.py              # rhwp scaffold/convert/export-pdf 호출, 봉투 수집
│   │   ├── template_register.py  # 서식 HWPX → style_map.json
│   │   └── rhwp_locate.py        # rhwp 바이너리·버전 확인 (rhwp-onboarding의 doctor 재사용)
│   └── templates/
│       ├── house/default/        # 저작권 문제 없는 자체 제작 기본 서식
│       ├── convention/{gongmun,gaejosik}.json
│       └── doctype/{report,business_plan,proposal,gian,minutes}.json
└── projects/<이름>_<YYYYMMDD>/
    ├── sources/  analysis/
    ├── doc_spec.md  spec_lock.json  outline.json
    ├── units/U01.json …
    ├── build/document.hds.json
    ├── exports/<이름>_<시각>.hwpx (.hwp, .pdf)
    ├── validation/{hds_lint.json, rhwp_*.json, report.json, workflow.log}
    ├── capsules/            # replay --capsule 영수증
    └── history/             # 교체된 단위의 이전 버전
```

저장소 결정(2026-11-01): **`planet6897` GitHub 계정에 `hwp-master` 저장소를 별도로 생성**한다.
rhwp 저장소의 PR 게이트(전쪽 Visual Sweep 등)는 코어 조판 변경을 위한 것이므로,
스킬·스크립트는 `planet6897/hwp-master`에서 빠르게 반복하고, **코어 확장(scaffold v2 블록)만
rhwp에 PR**한다(origin `planet6897/rhwp`에 push → `edwardkim/rhwp` devel로 PR).
hwp-master는 rhwp 소스를 포함하지 않고 설치된 `rhwp` 바이너리를 호출하며, 지원하는 최소 rhwp 버전을
`scripts/rhwp_locate.py`에서 확인한다.

## 6. 단계별 로드맵

### M0. 탐색·결정 (1주)

- `src/scaffold/builder.rs`, `exam_paper.rs`, `html_import`의 생성 경로와 한컴 열림 상태를 확인한다.
- 기준 문서 3종(개조식 보고서 5쪽, 사업계획서 15쪽, 기안문 1쪽)의 **목표 HWPX를 한컴에서 직접 작성**하고
  한컴 Print PDF를 확보한다. 이것이 이후 모든 시각 비교의 독립 기준이다.
- HDS(JSON) vs HTML 부분 집합을 같은 기준 문서로 시험해 중간 표현을 확정한다.
- 완료 기준: 결정 기록, 기준 문서·PDF 3종, scaffold v2 블록 목록 확정.

### M1. MVP — 현재 scaffold만으로 끝까지 (1~2주)

- 스킬 뼈대, `project_manager.py`, `source_to_md.py`, `build.py`, `hds_lint.py`(스키마 수준).
- Strategist/Executor 참조 문서, Quick 라우트.
- heading·paragraph·table만으로 개조식 보고서를 생성 → HWPX → 한컴에서 열림 확인 → PDF 미리보기.
- 완료 기준: 주제 하나로 5쪽 보고서가 오류 없이 생성되고 한컴에서 열리며 `export-structure` 개요가 outline과 일치.

### M2. scaffold v2 코어 확장 (rhwp PR, 3~5주, 블록별 이슈)

- P0 블록(runs·style·list·table 병합/열 폭·page_break) → P1(image·footnote·header_footer·caption).
- 2026-11-03: `page_break`·`footnote`·`header_footer`(쪽 번호 포함)는 코어 확장 없이 scaffold 결과에
  `rhwp edit` 을 적용하는 방식(`scripts/native.py`)으로 먼저 제공했다. 한컴 2024 대조 실루엣 98.58~100%
  (남은 차이는 제목 번호 간격, rhwp #8433). 본문 흐름 안 그림(`image`)은 `edit insert-image` 가 쪽 좌표 고정
  개체라 코어 확장이 필요하다.
- 2026-11-03: 문단 글자 강조(`emphasis`: 굵게·기울임·밑줄·색)를 같은 방식(`edit apply-char-format`)으로 추가.
  한컴 2024 대조 97.77%, 굵게·색·기울임·밑줄 모두 한컴과 같게 표시.
- 2026-11-03: 본문 그림(`image`)도 코어 확장 없이 제공 — 기존 `edit insert-picture`(문단 좌표) 뒤
  `edit set-picture {"treatAsChar":true}` 로 글자처럼 취급하고 문단 정렬을 준다. 앞서 "코어 확장 필요"로
  본 판단은 `insert-image`(도장용)만 보고 내린 것이었다. 한컴 2024 대조 99.53~99.78%.
- 2026-11-03: 목록(`list`: □ ○ - · 단계, `markers` 로 바꿈)도 코어 확장 없이 제공 — 항목 문단에
  `edit apply-para-format {"marginLeft","indent"}`(값은 HWPUNIT 의 2배)로 단계별 왼쪽 여백 10pt·글머리 폭 내어쓰기.
  글머리 폭은 한컴 2024 PDF 실측(함초롬바탕 10pt). 둘째 줄 시작이 한컴에서 글머리 뒤 글자와 1pt 안쪽으로 맞고,
  rhwp 미리보기 실루엣 95.26%(남은 차이는 제목 번호 간격 #8433·대체 글꼴 줄바꿈).
- 2026-11-03: 표 꾸밈(`header_row`·`header_fill`·`col_widths`)도 코어 확장 없이 제공 — `edit set-column-widths`
  (scaffold 표 폭을 비율로 나눔), 첫 행 셀마다 `set-cell-props {fillType, fillColor, isHeader}`(기존 테두리 유지)
  + `apply-char-format-in-cell` 굵게 + `apply-para-format-in-cell` 가운데. 한컴 2024 대조 실루엣 100%.
- 2026-11-03: 표 셀 병합(`merges` → `edit merge-cells`)·셀 강조(`cell_emphasis` → `apply-char-format-in-cell
  --start/--end`, end 는 미포함). 한컴 2024 대조 실루엣 100%. 표 셀 각주는 `insert-footnote` 가 본문 문단 좌표만
  받아 보류(rhwp 코어 확장 필요).
- 2026-11-03: 강조에 `size`(fontSize, pt×100)·`highlight`(shadeColor)·`strike`(strikethrough) 추가, 캡션 자동 번호
  (`{n}`, 그림·표 따로)와 표 캡션(표 위, keepWithNext). 그림 문단도 캡션과 묶는다. 한컴 2024 대조 실루엣 99.85%.
- 2026-11-03: 용지 방향(`orientation`)·여백(`margins_mm`) — `edit set-page-def`. 가로는 scaffold 세로 치수에
  `landscape:true`(한컴 저장 방식 NARROWLY, A4 상수 59528×84188 유지). scaffold 가 표 폭을 만들 때 용지를 쓰므로
  용지를 바꾼 뒤 모든 표를 `set-column-widths` 로 새 본문 폭(본문 - 표 바깥 여백 2mm)에 맞춘다. 한컴 2024 대조 99.98~99.99%.
- 2026-11-03: 목차(`toc`) — 제목마다 "번호 제목<탭>쪽" 문단 + `tabAutoRight`(오른쪽 끝 자동 탭). 쪽 번호는 한 번
  만든 결과의 `rhwp dump-pages --json`(paraIndex → 쪽)에서 읽어 다시 만든다. 7쪽 시험 문서에서 9개 항목 쪽 번호가
  한컴 2024 PDF 의 실제 제목 쪽과 모두 같고, 실루엣 98.69~100%. 점선 채움(tab leader)은 편집 명령이 탭 정의를 받지 않아 없음.
- 2026-11-03: 새 블록 전체를 쓴 실사용 시험(공공 업무계획 35쪽 → 4쪽 요약, `examples/plan_summary/`). 찾은 것:
  (1) rhwp scaffold 표가 `table.attr` bit13 은 켜고 `common.flow_with_text` 는 false 라 HWPX 에 flowWithText="0" 으로
  저장 → 한컴이 쪽 끝 표를 각주 영역에 겹쳐 그림. 빌드가 모든 표에 `set-table-props {"restrictInPage":true}` 로 우회
  (rhwp 쪽 수정은 별도 이슈 후보). (2) W201 셀 안쪽 여백을 1mm 로 잡아 넘침을 놓침 → scaffold 값 1.8mm×2.
  (3) 작성 안내에 목록·표 캡션·목차 사용 시점이 없었음 → executor·strategist·generate 보강, Convert-in 도 list·header_row 로.
- 각 블록은 rhwp 이슈 `task/<이슈번호>-scaffold-<블록>` 브랜치로 진행한다.
- 완료 기준: 블록별로 rhwp 정본 게이트 통과(§7).

### M3. 서식·규약 (2~3주)

- `template_register.py`, 서식 상속 scaffold(`--template`), `convention`/`doctype` JSON.
- 공문서 항목 기호·개조식 규약 검사를 `hds_lint`에 추가.
- 완료 기준: 기관 서식 1종을 등록해 사업계획서를 생성, 스타일이 서식 스타일에 바인딩되고 규약 위반 0건.

### M4. 품질 루프 (1~2주)

- `layout-anomaly`·`verify` 통합, early/final 단계 검사, `export-png` 시각 검토 절차.
- `replay --capsule`로 빌드 영수증 남기기, `validation/report.json` 집계.
- 완료 기준: 의도적으로 넣은 결함(넘침·수동 번호·누락 절)을 검사기가 모두 검출.

### M5. 추가 라우트 (2주)

- Edit Native·Beautify·Template Fill·Convert-in, Mail-merge/Exam 위임.
- 완료 기준: 라우트별 예제 1건씩 끝까지 실행.

### M6. 배포 (1주)

- `.claude-plugin`, `npx skills add planet6897/hwp-master` 설치 경로, rhwp 바이너리 설치 안내(`npm`/crates/릴리스).
- 예제 결과물(HWPX·PDF) 공개 저장소.
- 진행(2026-11-02): `.claude-plugin/plugin.json`·`marketplace.json`(`claude plugin validate` 통과, 격리 설정에서
  marketplace add → install → 스킬 인식 확인), README 설치 안내, `examples/template_fill/`. rhwp CLI 는 crates.io·npm 에
  없고 릴리스 v0.8.7 은 테스트 8건 실패라 0.9.0 릴리스 전에는 `cargo install --git … --branch devel --features native-skia`.
  `npx skills add` 경로는 미검증. 저장소 공개 여부는 사용자 결정.

## 7. rhwp 코어 PR 시 지켜야 할 정본 게이트

scaffold v2는 조판 결과를 바꾸는 생성 경로이므로 rhwp `CLAUDE.md`/`AGENTS.md`의 다음 조건을 그대로 적용한다.

- 블록 하나 = 이슈 하나, 브랜치 `task/<이슈번호>-<영문 설명>`, 최신 `upstream/devel` 기준.
- 생성 결과는 한컴 사양(HWPX 단위·속성)을 따르고 휴리스틱으로 맞추지 않는다. 생성한 HWPX를
  **한컴에서 열어 저장·Print PDF로 출력한 것**을 독립 기준으로 쓴다.
- 영향 문서 전쪽 Native/fresh WASM TSV, `silhouette_raw_match_percent` 쪽별 90% 이상, 전체 쪽수 일치, 확대 판독.
- Rust lint 묶음(fmt·clippy native/wasm/workspace)·manifest 검사, 새 회귀 테스트 첫머리 측정 근거 주석.
- 새 회귀는 `tests/cases/`에 관계형(소속·순서·누락·중복) 검사로 작성한다.
- GitHub 게시·push·PR 생성은 사용자 승인 후, origin(planet6897/rhwp)에 push하고 upstream devel로 PR.

## 8. 위험과 대응

| 위험 | 대응 |
|---|---|
| 생성 HWPX가 rhwp에서는 정상인데 한컴에서 다르게 보임 | M0 기준 문서를 한컴에서 작성·출력해 독립 기준 확보, 블록 추가마다 한컴 열기·Print PDF 대조 |
| 장문 집필 중 컨텍스트 소진 | 단위 집필 + `project_manager.py unit-context`로 필요한 맥락만 로드, `rhwp-handoff`로 세션 인계 |
| 외부 자료의 프롬프트 주입 | intake 단계에서 `inspect injection`·`armor`, 자료는 데이터로만 취급 |
| 글꼴(함초롬·바탕 등) 라이선스·환경 차이 | 기본 서식은 배포 가능한 글꼴만 지정, 미리보기 글꼴 환경을 report에 기록 |
| scaffold 확장이 코어 PR 게이트에 막혀 지연 | 스킬은 M1(현행 scaffold)로 먼저 쓸 수 있게 하고, 미지원 블록은 HDS에서 실패로 명확히 알림 |
| 공문서 규정 해석 오류 | 「행정업무운영 편람」 등 원문 근거를 `conventions_kr.md`에 출처와 함께 기록 |
| 참고 프로젝트 라이선스 | 코드 미복사, 개념만 참고하고 README에 참고 출처 명시 |

## 9. 결정이 필요한 사항

1. ~~저장소 위치~~ → **결정됨**: `planet6897/hwp-master` 별도 저장소. (공개 범위 public/private와 라이선스는 생성 시 확정)
2. 중간 표현: HDS(JSON, 권장) vs HTML 부분 집합 — M0 실험 후 확정.
3. 1차 대상 문서 유형: 개조식 보고서 + 사업계획서(권장) 외 우선순위.
4. 기본 산출 형식: HWPX(권장, scaffold 기본) vs HWP5 동시 산출.
5. 이미지 생성·웹 검색(ppt-master의 `image_gen`/`image_search`)을 초기 범위에 넣을지 여부.
