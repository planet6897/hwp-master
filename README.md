# hwp-master

자료(PDF·DOCX·MD·웹·기존 HWP) 또는 주제를 받아 **한컴에서 바로 편집할 수 있는 네이티브 HWPX/HWP**를
만드는 에이전트 스킬입니다. 스타일·개요 번호·표·그림·각주·머리말/꼬리말이 모두 실제 한글 개체로 들어갑니다.

- 문서 생성 엔진: [rhwp](https://github.com/edwardkim/rhwp) (`rhwp scaffold` 등 CLI 바이너리를 호출)
- 스킬 런타임: Claude Code 등 에이전트 호스트

> 상태: 0.1.0 — Default·Quick·Convert-in·Template Fill·Edit Native·Mail-merge·Beautify 라우트와 Exam 위임.
> HDS v1 블록은 제목·문단·표. 전체 계획은 [docs/plan.md](docs/plan.md), M0 대조 결과는 [docs/m0_findings.md](docs/m0_findings.md),
> 예제 결과물은 [examples/](examples/).

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

## 설치

### 1. rhwp 바이너리 (0.9.0 이상)

hwp-master 는 rhwp CLI 를 호출한다. **0.9.0 이 필요하다.** 최신 릴리스 v0.8.7 에서는 누름틀 채우기·표 행 반복의
저장 자기검증이 일치하지 않고(exit 3) 조판 넘침이 검출되어, 이 저장소 테스트 57건 중 8건이 실패한다.

0.9.0 릴리스 전에는 rhwp `devel` 에서 직접 빌드한다(Rust 툴체인 필요, 2026-11-02 `0c1e5a6f` 에서 확인 — 빌드 약 11분). 릴리스 바이너리와 같은
`native-skia` 기능을 켜야 PNG·PDF 미리보기가 동작한다.

```bash
cargo install --locked --git https://github.com/edwardkim/rhwp --branch devel \
  --features native-skia --bin rhwp rhwp
rhwp --version   # rhwp v0.9.0
```

0.9.0 이 릴리스되면 [GitHub Releases](https://github.com/edwardkim/rhwp/releases)의 OS별 압축 파일을 받아
`SHA256SUMS.txt` 로 확인한 뒤 압축을 풀고 `rhwp` 를 PATH 에 둔다(crates.io·npm 에는 CLI 가 없다).

PATH 대신 `RHWP_BIN=/경로/rhwp` 로 지정할 수도 있다. 확인: `python3 skills/hwp-master/scripts/rhwp_locate.py`.

### 2. 스킬

Claude Code 플러그인으로 설치한다 (저장소가 비공개인 동안은 이 저장소에 git 접근 권한이 있어야 한다).

```
/plugin marketplace add planet6897/hwp-master
/plugin install hwp-master@hwp-master
```

플러그인 없이 쓰려면 `skills/hwp-master` 폴더를 `~/.claude/skills/hwp-master` 로 복사한다.

### 3. 그 밖의 요구 사항

- Python 3.9+ (표준 라이브러리만 사용)
- 등록한 기관 서식은 `~/.hwp-master/templates/house/` 에 저장된다(`HWP_MASTER_HOME` 으로 바꿀 수 있다).
  플러그인 설치 폴더는 갱신 때 바뀌므로 거기에 두지 않는다.
- 생성 작업 폴더(`projects/<이름>`)는 명령을 실행한 폴더 아래에 생긴다.

## 참고한 프로젝트

설계 개념만 참고했으며 코드는 포함하지 않습니다.

- [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) — 작업 흐름, 역할 전환, 검사기 원칙
- [hawa130/docx-master](https://github.com/hawa130/docx-master) — 스타일 바인딩·자동 번호 규약과 감사
- [dashbear-ai/grant-master](https://github.com/dashbear-ai/grant-master) — 장문 단위 집필

## 라이선스

MIT
