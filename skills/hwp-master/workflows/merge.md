# 서식 하나로 여러 명분 만들기 (Mail-merge)

누름틀이 있는 서식 1개와 명단(CSV·JSONL) N행으로 문서 N개를 만든다. 채움은 rhwp `batch fill`이
하고, 스크립트는 사전 대조·선검증·행 수 보존 판정을 맡는다.

## 절차

```bash
python3 scripts/project_manager.py init <이름>
python3 scripts/project_manager.py import-sources <프로젝트> <서식.hwp|.hwpx> <명단.csv|.jsonl>
python3 scripts/merge.py <프로젝트> --form sources/<서식> --data sources/<명단> [--name-field 성명]
```

- 명단 CSV 의 첫 줄은 헤더이고, 헤더 이름은 서식의 누름틀 이름과 같아야 한다.
  누름틀 이름은 `rhwp fields <서식> --json`이나 `edit.py inspect`로 확인한다.
- 같은 이름의 누름틀이 여러 개면 열 이름을 `이름[순번]`으로 쓴다(0부터).
- `--name-field`를 주면 그 열 값이 파일 이름이 된다(없으면 `0001.hwpx`…). 값이 겹치면 `_2`가 붙는다.

산출: `exports/merge_<시각>/` 문서들, `validation/merge_<시각>.json` 요약, `validation/merge_<시각>.ndjson` 행별 원본 기록.

| 코드 | 뜻 | 차단 |
|---|---|---|
| M000 | 서식·명단 파일 문제, 빈 명단 | 예 |
| M001 | 서식에 없는 명단 열 | 예 |
| M002 | `--name-field`가 명단 열에 없음 | 예 |
| M003 | dry-run 에서 실패·미채움 행 (아무것도 쓰지 않음) | 예 |
| M004 | 입력 행 수 ≠ 산출 레코드 수, 실패 행, 저장 후 자기검증 불일치 | 예 |
| M101 | 명단이 채우지 않는 누름틀 (빈칸으로 남음) | 아니오 |
| M102 | 이름 열 값이 겹침 | 아니오 |

## 개인정보

명단에는 개인정보가 있을 수 있다. 요약 JSON 에는 개수·행 번호·파일 이름만 넣는다.
NDJSON 과 산출 문서에는 값이 들어 있으므로 대화에 붙여 넣거나 외부로 보내지 않는다.
제출 전 메타데이터 정리가 필요하면 rhwp `edit sanitize`를 쓴다.
