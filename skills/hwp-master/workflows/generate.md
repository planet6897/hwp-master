# 새 문서 만들기 (Default / Quick)

스크립트 경로는 `skills/hwp-master/scripts/`이다. rhwp 경로는 `RHWP_BIN` 환경변수나 `--rhwp`로 줄 수 있다.

## 0. 준비

```bash
python3 scripts/rhwp_locate.py          # 실패하면 중단하고 설치 안내
```

## Default

```bash
python3 scripts/project_manager.py init <이름>                     # projects/<이름>_<YYYYMMDD>/
python3 scripts/project_manager.py import-sources <프로젝트> <자료...>  # sources/ 사본 + analysis/ 추출본
```

1. `## [역할 전환: Strategist]` — `references/strategist.md`에 따라
   `doc_spec.md`·`spec_lock.json`·`outline.json`을 쓰고 **사용자 확인**을 받는다.
2. `## [역할 전환: Executor]` — `references/executor.md`·`references/hds_authoring.md`에 따라
   `units/U01.json`부터 쓴다. early 검사 → 나머지 → final 검사.
3. 빌드:

   ```bash
   python3 scripts/build.py <프로젝트> [--png] [--font-path <글꼴 폴더>]
   ```

4. `validation/report.json`의 `ok`가 `true`인지 확인한다. 오류가 있으면 해당 단위를 고쳐 다시 빌드한다.
   산출물을 손으로 고치지 않는다. 고칠 곳은 항상 `units/*.json`이다.
5. 시각 자기 검토(아래 절)를 한다.
6. 사용자에게 `exports/*.hwpx` 경로, 쪽 수(한도), 남은 경고, 빌드 영수증 경로, 미리보기 한계를 알린다.

### 검사 코드

| 코드 | 단계 | 뜻 | 차단 |
|---|---|---|---|
| E0xx·E1xx | HDS 검사 | 명세 구조 오류, 제목의 수동 번호(E101), `max_pages` 형식(E008) | 예 |
| P001~P005 | HDS 검사 | 기획 산출물·단위 파일 누락/불일치 (final 에서 단위 누락은 P004) | 예 |
| R101 | rhwp verify | 제목 글이나 표 수가 산출물에 없음 | 예 |
| R102 | export-structure | 명세의 제목과 산출물 개요가 다름 | 예 |
| R103 | layout-anomaly | 넘침·겹침·화면 밖·빈 쪽 신호 | 예 |
| R104 | 쪽수 | `spec_lock.max_pages`(Quick 은 `--max-pages`) 초과 | 예 |
| R105 | 미리보기 | Linux 에서 한글 글꼴 파일을 찾지 못함 — HWPX 는 정상, 미리보기 PNG·PDF 의 한글이 빠짐 | 아니오 |
| C001·C002·C011 | 규약 검사 | `convention` 항목 단계 건너뜀·순서 어긋남 | 예 |
| C101·C111 | 규약 검사 | 항목 기호 뒤 띄어쓰기, 개조식 서술형 종결 | 아니오 |
| W2xx·W3xx | HDS 검사 | 셀 넘침 추정, 열 수 불일치, 빈 문단, 분량 계획 대비 ±30% | 아니오 |

### 빌드 영수증

빌드할 때마다 `capsules/<산출물 이름>.receipt.json`이 생긴다. 명세 사본과 명세·산출 SHA-256,
rhwp 버전을 담는다. 같은 rhwp 로 다시 만들어 같은 파일이 나오는지 확인하려면:

```bash
python3 scripts/receipt.py verify <프로젝트>/capsules/<이름>.receipt.json   # exit 0 재현, 3 불일치
```

rhwp `replay --capsule`은 기존 문서에 편집 계획을 다시 적용하는 영수증이라 무(無)에서 만드는
생성 단계를 담지 못한다. 생성 뒤에 rhwp 편집 명령을 이어 쓰면 그 편집은 `replay --capsule`로 남긴다.

### 시각 자기 검토

`--png`로 빌드하면 `build/png/`에 쪽 그림이 생긴다. 점수가 아니라 다음을 쪽마다 확인한다.

1. 쪽수가 기대와 맞는가. 장이 의도하지 않은 쪽에서 끊기지 않았는가.
2. 표가 본문 폭 안에 있고, 셀 글이 잘리지 않았는가. 행이 쪽 경계에서 어색하게 나뉘지 않았는가.
3. 제목 번호가 이중으로 붙지 않았는가("1. 1.").
4. 빈 쪽·쪽 끝의 외톨이 제목이 없는가.

문제가 보이면 해당 단위를 고쳐 다시 빌드한다. 미리보기는 rhwp 렌더이므로 한컴과 다를 수 있다
(아래 알려진 한계). 제출 전 최종 확인은 한컴에서 연 화면이나 한컴 PDF 로 한다.

## Quick

짧은 문서(2쪽 이하)이고 사용자가 빨리 원할 때. 기획 확인과 단위 분할을 생략한다. 재개할 수 없다.

```bash
python3 scripts/build.py --quick <문서.hds.json> -o <출력.hwpx> [--max-pages N] [--convention gongmun|gaejosik]
```

영수증과 보고서는 `<출력>.build/`에 생긴다.

HDS 문서는 `{"version": "1", "title": ..., "blocks": [...]}` 하나에 전부 쓴다.

## 알려진 한계 (rhwp v0.9.0 기준)

- 표 셀의 긴 텍스트는 한컴에서 줄바꿈되지만 rhwp 미리보기에서는 잘리고 쪽수가 적게 나올 수 있다 (W201).
- rhwp 미리보기에서는 개요 번호 뒤 간격이 한컴보다 좁다 ("1.추진" vs "1. 추진"). 한글 파일 자체는 정상이다.
- 개요 번호 형식은 한글 기본(`1.`, `1.1.`)이며 `가.` 형식은 아직 지정할 수 없다.
