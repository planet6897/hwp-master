# HWP → HWPX 일괄 변환·검증

HWP(및 HML) 문서를 HWPX 로 바꾸고, 바꾼 뒤에도 내용이 같은지 검증한다. 원본은 고치지 않는다.
공직자통합메일은 2026년 10월부터 HWPX 외 첨부를 막는다.

이런 요청이 이 라우트다.

- "이 폴더 hwp 를 전부 hwpx 로 바꿔 줘", "메일로 보내게 hwpx 로 변환해 줘"
- "변환해도 내용이 그대로인지 확인해 줘"

## 실행

```bash
python3 scripts/project_manager.py init <이름>
python3 scripts/project_manager.py import-sources <프로젝트> <문서 또는 폴더> [...]
python3 scripts/convert_hwpx.py <프로젝트> sources/<폴더|문서|glob> [--force]
```

결과는 `exports/hwpx/<sources 아래 경로>.hwpx` 다. 하위 폴더 구조는 그대로 유지한다.
같은 이름의 결과가 있으면 `--force` 없이는 덮어쓰지 않는다(C102). 이미 HWPX 인 문서는 건너뛴다(C101).

문서마다 세 겹으로 검증하고, 보고서를 `validation/convert_hwpx_<시각>.json`·`.csv` 로 남긴다.

1. rhwp `export-hwpx --verify --verify-pages`: 다시 읽은 IR 차이(`diffCount`)와 렌더 쪽 수(`pages` 전/후)
2. 글 동일성: 원본과 결과의 문단·표 칸·각주 글을 공백을 빼고 차례대로 비교(`textSame`)
3. 구조 개수: 문단·제목·목록·표·중첩 표·칸·그림·각주 수(`countsSame`)

| 코드 | 수준 | 뜻 |
|---|---|---|
| C201 | 오류 | rhwp 변환 실패 |
| C202 | 오류 | IR 차이(diffCount > 0) |
| C203 | 오류 | 렌더 쪽 수가 다름 |
| C204 | 오류 | 글이 다름 — 첫 차이 위치와 전/후 글 |
| C205 | 오류 | 문단·표·칸·그림 수가 다름 |
| C101 | 경고 | 이미 HWPX 라 건너뜀 |
| C102 | 경고 | 결과 파일이 있어 건너뜀(`--force` 로 덮어씀) |

오류가 있으면 exit 1 이다. 그 문서는 변환 완료로 보고하지 않고, 차이 내용을 사용자에게 보여 준다.
변환한 HWPX 는 한컴에서 한 번 열어 보라고 안내한다. 미리보기는 rhwp 조판이라 한컴과 다를 수 있다.
