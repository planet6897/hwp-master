# Executor: 단위별 집필

목표: `outline.json`의 단위를 순서대로 하나씩 `units/<id>.json`으로 쓴다.

## 규칙

- **직접 쓴다.** 블록을 찍어 내는 생성 스크립트를 만들지 않는다. 앞뒤 단위의 흐름을 보며 한 단위씩 쓴다.
- 기획(`doc_spec.md`·`spec_lock.json`·`outline.json`)을 바꾸지 않는다. 바꿔야 하면 Strategist로 돌아가 사용자 확인을 받는다.
- 자료에 없는 사실·수치를 만들지 않는다.
- 단위를 다시 쓸 때는 이전 파일을 `history/<id>_<시각>.json`으로 옮긴 뒤 쓴다.
- 작성법은 `hds_authoring.md`를 따른다.

## 순서

1. U01~U03을 쓴다 (단위가 3개 이하면 전부).
2. early 검사: `python3 scripts/project_manager.py validate <프로젝트> --stage early`
   오류가 있으면 고친 뒤 계속한다. 같은 실수를 뒤 단위에서 반복하지 않는다.
3. 나머지 단위를 중간 검사 없이 이어 쓴다.
4. final 검사: `python3 scripts/project_manager.py validate <프로젝트> --stage final`
   오류 0건이어야 한다. 경고는 판단해서 남기거나 고친다.
5. 빌드: `python3 scripts/build.py <프로젝트>` → `exports/*.hwpx`, `validation/report.json`

## 단위 파일 형식

```json
{"unit": "U01", "blocks": [
  {"type": "heading", "level": 1, "text": "추진 배경"},
  {"type": "list", "items": [
    "종이 문서 중심의 결재·보관 체계로 검색과 재활용이 어려움",
    {"text": "연간 출력량 약 120만 쪽", "level": 2, "emphasis": [{"text": "120만 쪽", "bold": true}]}]}
]}
```

- 개조식 항목은 기호를 문단에 직접 쓰지 말고 `list` 블록으로 쓴다(둘째 줄 내어쓰기가 맞는다).
- 수치 비교는 표로 쓴다. 표마다 `caption`(`"<표 {n}> …"`)과 `header_row`를 붙이고, 셀 글이 한 줄을 넘는다는
  W201 경고가 나오면 `col_widths`로 긴 열을 넓힌다. 같은 값이 세로로 반복되는 칸은 `merges`로 합친다.
- 차트·그림은 자료의 수치로 그린 파일만 `image` 블록으로 넣고 `caption`을 붙인다.
- 목차(`toc`)는 Strategist 가 계획했을 때만 U01 맨 앞에 `toc` + `page_break`로 둔다.
