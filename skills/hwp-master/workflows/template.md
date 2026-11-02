# 기관 서식에 내용 채우기 (Template Fill)

기관이 정한 서식(HWP/HWPX)의 자리표시 칸에 자료를 채워 문서 하나를 만든다. 용지·글꼴·머리말·
표 모양은 서식 그대로이고, 칸의 글자 서식(굵게·색 따위)도 그대로 이어받는다. 채움은 rhwp `run`
계획서(`repeat_and_fill_table_rows`·`repeat_and_fill_paragraph_block`·`replace_text`·`fill_fields`)가
하고, 스크립트는 칸 조사·자료 대조·단계 연결·판정을 맡는다. 서식 원본은 고치지 않는다.

명단 N행으로 문서 N개를 만들 때는 [Mail-merge](merge.md)를 쓴다. 이 라우트는 문서 하나에 목록을
반복해 넣는 경우다.

## 서식 준비 — 자리표시 문법

한컴에서 서식을 열고 채울 자리에 그대로 입력한다.

| 표기 | 뜻 |
|---|---|
| `{{키}}` | 값 하나. 같은 키를 여러 곳에 쓰면 모두 같은 값이 된다 (예: 작성자). |
| `{{목록.필드}}` | 반복 칸. 같은 `목록`의 칸이 모두 **표 셀**에 있으면 그 행(들), 모두 **본문 문단**에 있으면 그 문단 묶음이 원형이다. 자료 수만큼 원형을 반복한다. |
| 누름틀 | 서식의 누름틀 이름과 같은 키를 채운다. |

- 키·필드 이름에는 공백·`{`·`}`·`.`을 쓰지 않는다. 한글 이름도 된다.
- 한 목록의 칸은 한 표의 연속 행이거나, 한 구역의 연속 문단이어야 한다. 원형 안에 같은 필드를 두 번 쓰지 않는다.
- 자리표시는 본문과 본문 표(한 단계) 셀에서만 찾는다. 머리말·꼬리말·글상자 안의 칸은 누름틀로 만든다.
- 원형을 지우는 기능은 없다. 목록이 비면 오류이므로 "해당 없음" 같은 자료 1건을 넣는다.

## 절차

```bash
python3 scripts/project_manager.py init <이름>
python3 scripts/project_manager.py import-sources <프로젝트> <서식.hwp|.hwpx>
python3 scripts/template_fill.py inspect <프로젝트> sources/<서식>
#   → analysis/template_<서식>.json (칸 목록), analysis/template_<서식>.data.example.json (자료 틀)
#   자료 틀을 sources/data.json 으로 복사해 값을 채운다 (집필은 Executor 역할)
python3 scripts/template_fill.py fill <프로젝트> sources/<서식> --data sources/data.json
```

자주 쓰는 기관 서식은 등록해 두고 이름으로 부른다. 등록하면 서식 사본·SHA-256 잠금·자료 틀이
`~/.hwp-master/templates/house/<이름>/`(`HWP_MASTER_HOME`으로 바꿀 수 있다)에 생긴다. 플러그인 설치 폴더는
갱신 때 바뀌므로 거기에 두지 않는다. `--house`는 이 폴더를 먼저, 스킬에 동봉한 `templates/house/`를 다음에 찾는다.
들여온 서식이 등록 때와 다르면 채우지 않는다(T003).

```bash
python3 scripts/template_fill.py register <서식.hwpx> --name <이름>
python3 scripts/template_fill.py fill <프로젝트> --house <이름> --data sources/data.json
```

자료 JSON:

```json
{"title": "2027년 추진 계획", "author": "홍길동",
 "tasks": [{"name": "첫째 과제", "detail": "첫째 내용"}, {"name": "둘째 과제", "detail": "둘째 내용"}],
 "성명": "누름틀 값"}
```

값은 문자열이나 숫자다. 한 칸에 줄바꿈은 넣을 수 없다 — 여러 줄이면 목록으로 만든다.

## fill 이 하는 일

1. 칸 조사(`search "{{"`·`export-tables`·`fields`)와 자료 대조. 여기서 오류가 나면 아무것도 쓰지 않는다.
2. 목록마다(뒤쪽 원형부터) 원형 뒤에 2번째 자료부터 복제·채움. 단계마다 칸을 다시 조사해 주소를 정한다.
3. 남은 칸(값 칸, 원형 = 1번째 자료, 누름틀)을 한 계획에서 채운다.
4. 단계마다 `--dry-run` 선검증 → 실행(`assertions.verify` 저장 자기검증) → `replay --capsule` 작업 캡슐.
   캡슐은 `--parent`로 이어져 `rhwp lineage capsules/<마지막>`으로 전체 사슬을 검증할 수 있다.
5. 다시 읽은 산출물로 판정한다.

산출: `exports/<서식>_template_<시각>.<형식>`(입력 형식 유지), `history/template_<시각>/` 단계 계획서,
`capsules/template_<시각>_NN.capsule.json`, `validation/template_<시각>.json` 보고서.

| 코드 | 뜻 | 차단 |
|---|---|---|
| T000 | 서식·자료 파일 문제, 칸 검색 실패 | 예 |
| T001 | 자리표시 문법 오류·위치 불명(중첩 표 따위) | 예 |
| T002 | 목록 구성 오류 (셀·문단 혼재, 여러 표·구역, 병합 셀이 원형 밖으로, 같은 필드 두 번, 원형 겹침, 값 칸·목록·누름틀 이름 충돌) | 예 |
| T003 | 등록 이후 서식이 바뀜 | 예 |
| T004 | 자리표시도 누름틀도 없음 | 예 |
| T100 | 자료 JSON 형식 오류 | 예 |
| T101 | 빠진 값 칸·목록·필드 | 예 |
| T103 | 문자열·숫자가 아닌 값, 줄바꿈 | 예 |
| T104 | 값에 `{{`·`}}` | 예 |
| T105 | 빈 목록 | 예 |
| T211~T216 | 단계 dry-run·실행·재현 실패, 바뀐 곳 수 불일치, 원형 재조사 실패 | 예 |
| T201 | 채우지 못한 자리표시가 남음 | 예 |
| T202 | 넣은 값이 산출물 본문에 없음 | 예 |
| T203 | 서식보다 조판 이상(넘침·겹침·빈 쪽)이 늘어남 | 예 |
| T111 | 서식에 없는 키·필드 (쓰지 않음) | 아니오 |
| T112 | 자료가 채우지 않는 누름틀 (빈칸으로 남음) | 아니오 |
| T214 | 모호한 누름틀 이름 | 아니오 |
| T221 | 쪽수 변화 (목록이 길어진 결과인지 확인) | 아니오 |

오류가 1건이라도 있으면 산출물을 완료로 보고하지 않는다. 쪽수가 바뀌었거나 표가 길어졌으면
`rhwp export-png`로 바뀐 쪽을 직접 본다.

## 한컴 대조 (예시)

`tests/test_template_fill.py`의 서식(값 칸 4종, 문단 목록 3건, 표 행 목록 2건)을 채운 산출물을
한컴오피스 2024로 PDF 출력하면 1쪽이고 글자 순서가 같으며, rhwp 출력과의 실루엣 일치율은 99.90%다
(2026-11-02, `visual_sweep.py --silhouette-only --compat 2024 --dpi 96`).
