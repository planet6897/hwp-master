# hwp-master

자료(PDF·DOCX·MD·웹·기존 HWP) 또는 주제를 받아 **한컴에서 바로 편집할 수 있는 네이티브 HWPX/HWP**를
만드는 에이전트 스킬입니다. 스타일·개요 번호·표·그림·각주·머리말/꼬리말이 모두 실제 한글 개체로 들어갑니다.

- 문서 생성 엔진: [rhwp](https://github.com/edwardkim/rhwp) (`rhwp scaffold` 등 CLI 바이너리를 호출)
- 스킬 런타임: Claude Code 등 에이전트 호스트

> 상태: M1 — 주제/자료 → 개조식 보고서 HWPX 생성 경로 동작. 전체 계획은 [docs/plan.md](docs/plan.md), M0 대조 결과는 [docs/m0_findings.md](docs/m0_findings.md).

## 작업 흐름 (요약)

```
intake → Strategist(문서 기획·확인) → Executor(단위별 집필, HDS JSON)
      → Assembler(rhwp scaffold) → Checker(규약·조판 검사) → Export(HWPX/HWP/PDF)
```

## 디렉터리

```
skills/hwp-master/   SKILL.md, references/, workflows/, schemas/, scripts/, templates/
docs/                계획·설계 문서
projects/            생성 작업 폴더 (Git 제외)
```

## 요구 사항

- `rhwp` 바이너리 0.9.0 이상 (`RHWP_BIN` 환경변수 또는 PATH, `scripts/rhwp_locate.py`로 확인)
- Python 3.9+ (표준 라이브러리만 사용)

## 참고한 프로젝트

설계 개념만 참고했으며 코드는 포함하지 않습니다.

- [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) — 작업 흐름, 역할 전환, 검사기 원칙
- [hawa130/docx-master](https://github.com/hawa130/docx-master) — 스타일 바인딩·자동 번호 규약과 감사
- [dashbear-ai/grant-master](https://github.com/dashbear-ai/grant-master) — 장문 단위 집필

## 라이선스

MIT
