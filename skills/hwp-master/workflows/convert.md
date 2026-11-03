# 다른 형식 자료를 한글 문서로 (Convert-in)

MD·DOCX·PDF 자료를 HDS 단위로 옮긴 뒤 Default 빌드로 네이티브 HWPX 를 만든다.
변환기는 결정적으로 옮기고 정규화만 한다. 내용을 고쳐 쓰지 않으며, 담지 못한 것은 손실 목록에 남긴다.

## 1. 변환

```bash
python3 scripts/project_manager.py init <이름>
python3 scripts/project_manager.py import-sources <프로젝트> <자료.md|.docx|.pdf>
python3 scripts/convert.py <프로젝트> sources/<자료> [--convention gaejosik|gongmun] [--title 제목] [--max-pages N]
```

산출: `spec_lock.json`(없으면 새로, 있으면 title 이 빈 경우만 채움), `outline.json`, `units/U01.json`…
(수준 1 제목마다 한 단위), `analysis/<자료>.convert.json`(정규화·손실 보고).
이미 단위가 있으면 덮어쓰지 않는다(`--force`로만).

| 입력 | 경로 | 구조 인식 |
|---|---|---|
| `.md` | 직접 해석 | 제목(`#`)·문단·목록·파이프 표(열 정렬 포함)·인용·코드 블록 |
| `.docx` | `word/document.xml` 직접 해석(외부 도구 없음) | Title·heading N 스타일, 목록 번호(numPr), 표 |
| `.pdf` | `rhwp import-pdf` → `export-markdown` → 줄 단위 문단 | 표만. 제목 수준·목록은 알 수 없다 |

정규화 규칙:

- 제목에 직접 쓴 번호("1. 개요")는 떼고 `strippedHeadingNumbers`에 기록한다 (개요 자동 번호와 이중 번호 방지).
- 가장 높은 제목 수준이 1이 되게 당긴다. 다른 제목 출처가 없으면 맨 앞의 유일한 최상위 제목을 문서 제목으로 쓴다.
- 목록: gaejosik 은 `list` 블록(기본 글머리 `□ ○ - ·`, 단계별 여백·내어쓰기)으로, 규약 없는 기호 목록은
  `list` 블록(`- ·`)으로 옮긴다. 단계를 건너뛴 항목은 한 단계씩 당기고 `listLevelAdjusted`에 남긴다.
  번호가 매겨져야 하는 gongmun `1. 가. 1) 가) …`(제목마다 다시 셈)과 규약 없는 순서 목록(`1.`)은 문단으로 둔다.
- Markdown 표는 첫 행이 머리행이므로 `header_row: true`를 붙인다. DOCX 표는 알 수 없어 붙이지 않는다.

손실(`losses`) 종류: `image`(그림 — 본문에 넣지 않음), `char_format`(굵게·기울임 등), `link`(주소),
`footnote`, `blockquote`, `code_block`, `cell_merge`(DOCX 병합 셀), `pdf_layout`, `pdf_structure`.

## 2. 검토 (Executor)

`## [역할 전환: Executor]` — 변환 결과는 초안이다. 다음을 확인하고 `units/*.json`을 고친다.

1. `analysis/<자료>.convert.json`의 손실 목록. 그림·각주가 꼭 필요하면 사용자에게 알린다(그림으로 대체하지 않는다).
2. 단위 나눔과 제목. **PDF 는 제목 수준을 알 수 없어 한 단위의 문단 나열이 된다** — 제목 문단을 heading
   블록으로 바꾸고 단위를 다시 나눈 뒤 `outline.json`도 맞춘다. 번호가 박힌 제목("1. 1. 추진 배경")은 번호를 뗀다.
3. `project_manager.py validate <프로젝트>`로 규약·구조 검사.

## 3. 빌드

```bash
python3 scripts/build.py <프로젝트> [--png]
```

이후는 `workflows/generate.md`의 4~6단계와 같다.

## 한계

- HDS v1 은 제목·문단·표뿐이라 글자 서식·그림·각주·셀 병합은 옮기지 못한다(손실로 보고).
- PDF 는 글 흐름만 옮긴다. 원본 PDF 의 쪽 모양을 그대로 HWP 로 옮기려면 `rhwp import-pdf --layout fidelity`(기본값, 위치 고정 글상자)를
  직접 쓰고, 그 결과는 Edit Native 라우트로 고친다.
