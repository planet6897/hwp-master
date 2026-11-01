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
  {"type": "paragraph", "text": "□ 종이 문서 중심의 결재·보관 체계로 검색과 재활용이 어려움"}
]}
```
