# M0 결과: 현행 `rhwp scaffold` 산출물의 한컴 대조

- 실행: 2026-11-01 (KST)
- 목적: 현재 scaffold v1(heading·paragraph·table)만으로 만든 문서가 한컴에서 열리는지,
  rhwp 미리보기와 한컴 Print 출력이 얼마나 일치하는지 확인하고 HDS 설계 결정을 내린다.

## 1. 실행 환경

| 항목 | 값 |
|---|---|
| rhwp | `v0.9.0`, 소스 `85b831028` (planet6897/rhwp 로컬 통합 브랜치), `target/pr-review/release/rhwp` |
| 한컴 | 한컴오피스 2024 `13.0.0.3901`, 원격 변환 MCP `engine: 2024` |
| 한컴 PDF 출력 | `pdf_print_method: 0`, `pdf_output_mode: frame_print_to_pdf_ex_one_up` (Print 경로 1-up), 한컴 글꼴 범위 `verified` |
| rhwp 글꼴 | 로컬에 함초롬바탕 미설치 → `RHWP_FONT_PATH=rhwp/ttfs/opensource` (RHWPPublicBatang·Noto Sans KR)로 대체 |
| 점수 | rhwp `scripts/visual_sweep.py --silhouette-only --dpi 96` (Native만, WASM 미측정) |

글꼴이 대체됐으므로 아래 점수는 **설계 판단용 참고값**이며 rhwp PR 게이트 증거가 아니다.

## 2. 입력과 결과

| 문서 | 명세 | 생성 HWPX SHA-256 | rhwp 쪽수 | 한컴 쪽수 | raw 실루엣 |
|---|---|---|---|---|---|
| 개조식 보고서 | `examples/m0/specs/report_gaejosik.json` | `96e5feee…6d88` | 1 | 1 | 93.36% |
| 기안문 | `examples/m0/specs/gian.json` | `f72e2a43…734d` | 1 | 1 | 99.57% |
| 사업계획서 | `examples/m0/specs/business_plan.json` | `76fde135…6e1d` | **2** | **3** | 측정 불가 (쪽수 불일치) |

- 한컴 PDF: `examples/m0/hancom/*-2024.pdf`, TSV: `examples/m0/tsv/`, 비교 이미지: `examples/m0/png/`
- `rhwp layout-anomaly`: 3종 모두 overflow·겹침·빈 쪽 0건 (아래 F2의 결함을 잡지 못함)

## 3. 발견

### F1. 한컴 열림: 3종 모두 정상

한컴 2024가 경고 없이 열고 Print PDF를 출력했다. scaffold 산출물의 파일 유효성은 MVP에 충분하다.

### F2. 표 셀 긴 텍스트가 rhwp에서 줄바꿈되지 않고 잘림 (쪽수 불일치의 원인)

- 관측: 사업계획서 Ⅲ절 표의 "세부 과업 1: 요구사항 정의, 설계, 구현 및 시험"이 rhwp에서는
  "세부 과업 1: 요구사항"까지만 한 줄로 보이고 나머지가 잘린다. 한컴은 셀 안에서 3줄로 줄바꿈하고
  행 높이를 늘려 문서가 3쪽이 된다. (`png/zoom_bp_rhwp.png`, `png/cmp_bp.png`)
- 원인(코드): `rhwp/src/scaffold/builder.rs`의 `make_cell_para`가 셀 문단마다 저장 LineSeg를
  **1줄짜리로 고정** 생성하고, 셀 높이도 `패딩 + 1000`(1줄)로 고정한다. rhwp는 이 저장 줄 정보를
  신뢰해 1줄로 배치하고, 한컴은 다시 조판해 줄을 나눈다. 즉 scaffold가 **실제와 맞지 않는 저장 조판 정보**를 쓴다.
- 영향: 긴 셀 텍스트가 있는 모든 표에서 rhwp 미리보기·쪽수가 한컴과 달라진다. `layout-anomaly`도 검출하지 못한다.
- 조치: rhwp 코어 이슈 후보 (§5 초안 A). M2의 첫 작업으로 둔다.

### F3. 개요 제목은 자동 번호다: 명세에 번호를 쓰면 이중 번호

- scaffold `heading`은 개요 문단(자동 번호)으로 만들어진다. 명세에 "1. 추진 배경"처럼 번호를 쓰면
  한컴에서 "1. 1. 추진 배경", 수준 2는 "3.1. 가. 표준 양식 정비"가 된다.
- 한컴 기본 개요 번호 형식은 `1.` / `1.1.`이다. 개조식 보고서·공문의 `1.` / `가.` / `1)` 형식은 지정할 수단이 없다.
- 결정: HDS 규약 "제목 텍스트에 번호를 쓰지 않는다"를 `hds_lint` 오류 규칙으로 둔다(docx-master의 자동 번호 규약과 같음).
  번호 형식은 scaffold v2의 `numbering` 정의(수준별 형식)로 지정한다.

### F4. 개요 번호와 제목 사이 간격: rhwp는 붙고 한컴은 띄움

- 같은 문서에서 rhwp는 "1.1. 추진 배경", 한컴은 "1. 1. 추진 배경"으로 번호 뒤 간격이 다르다.
  (`png/zoom_rg.png`, 위가 rhwp·아래가 한컴) 보고서 점수 93.36%의 주요 감점 요인으로 보인다.
- F3 규약을 적용하면 이중 번호는 사라지지만, 번호 뒤 간격 차이는 남을 수 있다. rhwp 코어 이슈 후보 (§5 초안 B).
  제출 전 번호 텍스트를 뺀 명세로 재측정해 차이가 남는지 확인한다.

### F5. 기호 문단(□, ○)은 양쪽 모두 정상

현재 scaffold로는 글머리표를 문자로 직접 쓴다. 자동 글머리표·내어쓰기는 scaffold v2 `list` 블록에서 다룬다.

### F6. HTML 경로는 CLI에 노출돼 있지 않다

`paste_html_native`는 코어 API에만 있고 CLI·`run` 계획서에서 쓸 수 없다. 반면 scaffold는 JSON 명세,
`deny_unknown_fields`, 봉투 출력을 이미 갖췄다.

### F7. `rhwp verify`는 기대 조건을 요구한다

조건 없이 호출하면 판정하지 않는다. 검사기는 `outline.json`에서 `--contains`(장 제목)·`--min-pages`
같은 기대 조건을 만들어 넘긴다.

### F8. 한컴 변환 MCP는 HWPX → HWPX 재저장을 지원하지 않는다

`--target hwpx`는 `.hwpx` 입력에서 거부된다. 한컴 정규화 저장본이 필요하면 `--target hwp`를 쓴다.

## 4. 결정

| 번호 | 결정 | 근거 |
|---|---|---|
| D1 | 중간 표현은 **HDS(JSON, scaffold 명세 확장)** 로 확정한다 | F6. HTML 경로는 CLI가 없고 판정이 데이터로 남지 않는다 |
| D2 | 제목 텍스트에 번호 금지, 번호 형식은 명세의 `numbering`으로 지정 | F3 |
| D3 | M2의 첫 작업은 새 블록이 아니라 **표 셀 저장 줄 정보 결함 수정**(F2) | 표가 들어간 모든 문서의 미리보기·쪽수가 틀림 |
| D4 | F2 수정 전까지 hwp-master 최종 검사의 쪽수·미리보기 기준은 한컴 PDF(사용 가능할 때)로 하고, rhwp 미리보기는 참고로만 쓴다 | F2 |
| D5 | 검사기에 "셀 텍스트 길이 대비 열 폭" 경고를 임시로 둔다 | F2를 `layout-anomaly`가 못 잡음 |

## 5. rhwp 이슈 초안 (게시 전, 사용자 승인 필요)

### A. scaffold: 표 셀 문단의 저장 LineSeg를 1줄로 고정해 긴 셀 텍스트가 줄바꿈되지 않고 잘린다

- 재현: `examples/m0/specs/business_plan.json` → `rhwp scaffold` → rhwp 2쪽 / 한컴 2024 Print 3쪽
- 원인: `src/scaffold/builder.rs` `make_cell_para`의 단일 LineSeg와 `build_table_paragraph`의 1줄 고정 셀 높이
- 기대: 저장 조판 정보가 없거나 다시 조판해야 하는 문단으로 생성해, rhwp와 한컴이 같은 줄 나눔·쪽수를 낸다
- 관련: #7216, #7232, #7234 (scaffold 표 후속)

### B. 개요 자동 번호 뒤 간격이 한컴과 다르다 (rhwp "1.1." / 한컴 "1. 1.")

- 재현: `examples/m0/specs/report_gaejosik.json`의 수준 1 제목
- 먼저 번호 텍스트를 뺀 명세로 다시 측정해 문제 범위를 좁힌 뒤 게시한다.

## 6. 다음 단계 (M1)

1. `scripts/rhwp_locate.py`: rhwp 바이너리·버전 확인
2. `scripts/project_manager.py init|import-sources|validate`
3. `scripts/hds_lint.py`: 스키마 검사 + D2·D5 규칙
4. `scripts/build.py`: scaffold → PDF/PNG 미리보기, 봉투 수집
5. `references/strategist.md`·`executor.md`·`hds_authoring.md` 초안과 Quick 라우트로 개조식 보고서 끝까지 생성
