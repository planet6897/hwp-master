<p align="center"><img src="assets/icon-256.png" width="128" alt="hwp-master 아이콘"></p>

# hwp-master — AI 가 자료로 한컴에서 바로 편집할 수 있는 한글 문서를 만듭니다

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![rhwp](https://img.shields.io/badge/engine-rhwp%200.9.0-blue)](https://github.com/edwardkim/rhwp)

자료(PDF·DOCX·MD·기존 HWP) 또는 주제를 받아 **한컴오피스에서 바로 편집할 수 있는 네이티브 HWPX** 를 만드는
에이전트 스킬입니다. 제목은 한글 개요 번호, 표는 한글 표 개체로 들어가므로 그림으로 붙인 문서가 아니라
그대로 고쳐 쓸 수 있는 문서가 나옵니다. 기관 서식 채우기, 명단으로 여러 부 만들기, 기존 문서 부분 수정,
손 번호를 개요 번호로 정리하기도 합니다.

- 문서 엔진: [rhwp](https://github.com/edwardkim/rhwp) — HWP/HWPX 를 읽고 쓰는 오픈소스 엔진의 CLI 를 호출합니다.
- hwp-master 는 기획·집필 절차와 결과 검사를 맡습니다. 검사에서 오류가 하나라도 나오면 완료로 보고하지 않습니다.

> 상태: 0.1.16 — 새 문서(Default·Quick)·형식 변환·서식 채우기·명단 문서·부분 수정·번호 정리, 시험지 위임.
> 새 문서의 블록은 제목·문단·표·쪽 나눔·그림(캡션)·목록(□ ○ - · 단계, 내어쓰기)·목차(쪽 번호)·표 캡션·제목 행·열 폭 비율·셀 병합·셀 강조와 글자 강조(굵게·기울임·밑줄·취소선·색·형광펜·크기)·캡션 자동 번호·각주·머리말·꼬리말(쪽 번호)·용지 방향과 여백입니다. 예제 결과물은 [examples/](examples/) 에 있습니다.

## 빠른 시작

### 1. 사전 준비

**필요한 것은 [Python](https://www.python.org/downloads/) 3.9 이상과 rhwp 0.9.0 이상 두 가지입니다.**
hwp-master 스크립트는 Python 표준 라이브러리만 쓰므로 `pip install` 할 것이 없습니다.

**rhwp 는 이 저장소의 Release 에 올린 바이너리로 설치합니다(빌드 불필요).** 공개 upstream
[edwardkim/rhwp](https://github.com/edwardkim/rhwp) 의 고정 커밋을 GitHub Actions 에서 그대로 빌드한 것이고(MIT),
어느 Release·커밋·SHA-256 을 쓸지는 `skills/hwp-master/rhwp_release.json` 에 고정돼 있습니다.

```bash
python3 skills/hwp-master/scripts/install_rhwp.py
# rhwp v0.9.0 설치 — ~/.local/share/hwp-master/rhwp/rhwp-0.9.0-4e2cc6b/rhwp/rhwp
python3 skills/hwp-master/scripts/rhwp_locate.py    # 설치한 바이너리를 자동으로 찾는다
```

Windows 에서는 `python3` 이 Microsoft Store 바로가기라 실행되지 않을 수 있습니다. 그때는 이 문서의
`python3` 을 `python`(또는 `py -3`)으로 바꿔 실행합니다.

| OS | 파일 | 확인 |
|---|---|---|
| macOS (Apple Silicon) | `rhwp-0.9.0-4e2cc6b-macos-aarch64.tar.gz` | 설치·hwp-master 전체 테스트·예제 재현 (2026-11-03) |
| Linux x86_64 (glibc 2.35 이상) | `rhwp-0.9.0-4e2cc6b-linux-x86_64.tar.gz` | 빌드 runner 에서 PNG 내보내기 검증 |
| Windows x86_64 | `rhwp-0.9.0-4e2cc6b-windows-x86_64.zip` | 설치·hwp-master 전체 테스트 (Windows 11, 2026-11-03) |

- 받은 압축 파일의 SHA-256 이 `rhwp_release.json` 과 다르면 설치하지 않습니다.
- 받기는 `gh release download` 를 먼저 씁니다. 저장소가 비공개인 동안에는 [GitHub CLI](https://cli.github.com/) 로
  `gh auth login` 한 계정에 저장소 권한이 있어야 합니다.
- Linux 미리보기(PNG·PDF)에 한글이 나오려면 한글 글꼴이 있어야 합니다(`sudo apt install fonts-noto-cjk`). 없으면
  HWPX 는 정상이고 미리보기 한글만 빠지며, hwp-master 가 경고(R105)를 냅니다.
- Intel Mac·Linux ARM 은 바이너리가 없어 아래처럼 소스에서 빌드합니다.

<details>
<summary><strong>소스에서 빌드</strong> — Intel Mac·Linux ARM, 또는 다른 rhwp 커밋을 쓸 때</summary>

```bash
# Rust 툴체인 (이미 있으면 생략)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# Linux 는 먼저: sudo apt install build-essential libfontconfig1-dev libfreetype6-dev fonts-noto-cjk

# native-skia 기능을 켜야 PNG·PDF 미리보기가 동작합니다
cargo install --locked --git https://github.com/edwardkim/rhwp --branch devel \
  --features native-skia --bin rhwp rhwp
rhwp --version    # rhwp v0.9.0
```

macOS 약 11분, Linux 24코어 11~20분 걸렸습니다(2026-11-02~03). Linux 에서 `libfontconfig1-dev`·`libfreetype6-dev` 가
없으면 빌드 마지막에 `unable to find library -lfreetype` 로 실패합니다. 이 저장소 관리자는
`gh workflow run rhwp-binaries.yml -f rhwp_ref=<커밋 SHA>` 로 새 Release 를 만들 수 있습니다.
</details>

찾는 순서는 `--rhwp` 인자 → `RHWP_BIN` 환경변수 → 설치 스크립트 바이너리 → PATH 의 `rhwp` 입니다. 직접 빌드한 rhwp 를
쓰려면 `RHWP_BIN=/경로/rhwp` 로 지정합니다.

### 2. 에이전트 고르기

hwp-master 는 **파일을 읽고 쓰고 명령을 실행할 수 있는 AI 에이전트** 안에서 동작합니다. 처음 써 보는 분도
걱정하지 않아도 됩니다. 여기서 에이전트는 "파일을 다룰 수 있는 AI 채팅 창" 역할만 합니다. 코딩은 필요 없습니다.

> **권장: [Claude Code](https://claude.ai/code)** — 이 프로젝트를 만들고 시험한 환경입니다. 터미널 CLI 나
> VS Code / JetBrains 확장으로 씁니다.

| 종류 | 예 | 비고 |
|---|---|---|
| **CLI 에이전트** | [Claude Code](https://claude.ai/code) CLI | 확인한 환경 |
| **IDE 확장** | Claude Code (VS Code / JetBrains 확장) | CLI 와 같은 엔진 |
| **그 밖의 에이전트** | Codex CLI, Cursor, Gemini CLI 등 | 저장소 루트의 `AGENTS.md` 를 읽는 에이전트라면 동작할 수 있으나 확인하지 않았습니다 |

### 3. 설치

**방법 A — Git clone** (권장, [Git](https://git-scm.com/downloads) 필요): 언제든 최신 버전을 받을 수 있습니다.

```bash
git clone https://github.com/planet6897/hwp-master.git
cd hwp-master
python3 skills/hwp-master/scripts/rhwp_locate.py    # rhwp 경로·버전 확인 (Linux 는 한글 글꼴도)
python3 -m unittest discover -s tests               # 선택: 자동 테스트 (rhwp 0.9.0 에서 모두 OK)
```

> 저장소가 비공개인 동안에는 이 저장소에 접근 권한이 있는 GitHub 계정이 필요합니다.

**방법 B — ZIP 내려받기** (Git 없이 빠르게 써 볼 때): [GitHub 페이지](https://github.com/planet6897/hwp-master)에서
**Code → Download ZIP** 을 눌러 받아 압축을 풉니다. ZIP 에는 Git 기록이 없어 `git pull` 로 업데이트할 수 없습니다.

#### 나중에 업데이트

**Git clone 설치:**

```bash
git pull
```

**ZIP 설치:** 최신 ZIP 을 새 폴더에 풀고, 이전 폴더의 `projects/` 를 새 폴더로 옮깁니다.

> **방법 C — Claude Code 플러그인 마켓플레이스**: 저장소에 `.claude-plugin/marketplace.json` 이 있어 Claude Code 안에서
> 바로 설치할 수 있습니다.
>
> ```bash
> /plugin marketplace add planet6897/hwp-master
> /plugin install hwp-master@hwp-master
> ```
>
> 업데이트는 `/plugin marketplace update hwp-master` 뒤 `/plugin update hwp-master@hwp-master` 입니다.
> 플러그인으로 설치하면 스킬 파일만 받습니다(설치 폴더는 갱신 때 바뀌는 캐시입니다). 작업 폴더 `projects/` 는
> Claude Code 를 실행한 폴더 아래에 생기고, 등록한 기관 서식은 `~/.hwp-master/templates/house/` 에 보관됩니다.
> 플러그인 없이 스킬만 쓰려면 `skills/hwp-master` 폴더를 `~/.claude/skills/hwp-master` 로 복사해도 됩니다.

### 4. 만들기

**먼저 에이전트에서 프로젝트 폴더를 엽니다.** 방법 A·B 라면 `cd hwp-master` 후 `claude` 를 실행하거나, IDE 에서
**File → Open Folder** 로 `hwp-master` 폴더를 엽니다. 이후는 모두 채팅으로 합니다.

**자료를 주고 요청합니다(권장).** 파일 경로를 채팅에 붙여 넣으면 됩니다. 파일 관리자나 IDE 사이드바에서
파일을 오른쪽 클릭 → **경로 복사** 로 얻을 수 있습니다.

```
나: ~/받은자료/계획안.docx 로 3쪽 이내 개조식 보고서를 한글 파일로 만들어 줘
```

**내용을 직접 붙여 넣어도 됩니다.**

```
나: 다음 내용으로 공문서 항목 기호를 쓴 기안문을 만들어 줘: [내용 붙여 넣기...]
```

기본 흐름에서는 AI 가 먼저 **문서 기획을 보여 주고 확인을 받습니다**. 확인해야 집필로 넘어갑니다.

```
AI: 문서 기획을 확인해 주세요.
    [유형] 개조식 보고서 (□ ○ - 기호, 명사형 종결)
    [분량] 3쪽 이내, A4 함초롬바탕
    [장]   ① 검토 개요 ② 현황 ③ 추진 계획 ④ 기대 효과
    [근거] 계획안.docx — 자료에 없는 수치는 쓰지 않음
```

AI 가 장별 집필, 한글 문서 조립, 규약·조판 검사, 미리보기까지 진행하고 결과를 알려 줍니다.

이 흐름으로 만든 2쪽 보고서(기획·원고·결과 HWPX·한컴 PDF 대조)는 [examples/report/](examples/report/) 에 있습니다.

**빠른 생성 (확인 단계 생략)**: 2쪽 이하의 짧은 문서는 그렇게 말하면 확인 없이 바로 만듭니다.

```
나: 회의 결과를 한 쪽짜리 한글 문서로 빨리 만들어 줘, 확인은 필요 없어
```

> **결과물:** `projects/<이름>_<날짜>/exports/` 에 HWPX 와 미리보기 PDF 가 생기고, `validation/report.json` 에 검사 결과,
> `capsules/` 에 같은 결과를 다시 만들 수 있는지 확인하는 빌드 영수증이 남습니다. 미리보기는 rhwp 렌더라 한컴과 다를 수
> 있으니 제출 전에는 한컴에서 열어 확인하세요.

> **이미 있는 한글 문서를 쓰고 싶다면?** 서식과 자료를 주고 "이 서식에 채워 줘"(서식 채우기), "이 신청서 서식에 명단.csv 로
> 한 부씩"(명단 문서), "이 문서의 2026년을 2027년으로"(부분 수정), "1. 가. 1) 번호를 자동 번호로"(번호 정리)처럼 요청합니다.
> 원본은 고치지 않고 결과를 새 파일로 만듭니다. 라우트별 사용법은 [사용 설명서](docs/usage.md)에 있습니다.

> **잘 안 될 때:** AI 가 맥락을 잃으면 `skills/hwp-master/SKILL.md` 를 읽으라고 하세요. 오류 코드와 대처는
> [사용 설명서의 막혔을 때](docs/usage.md#6-막혔을-때)에 있습니다.

## 문서

| 문서 | 내용 |
|---|---|
| [사용 설명서](docs/usage.md) | 라우트 고르기, Claude 에게 요청하는 예, 라우트별 명령, 결과 확인, 문제 해결 |
| [SKILL.md](skills/hwp-master/SKILL.md) | 에이전트가 따르는 라우팅·원칙·정지 규칙 |
| [workflows/](skills/hwp-master/workflows/) | 라우트별 절차와 검사 코드 |
| [examples/](examples/) | 예제 결과물 (HWPX·한컴 2024 PDF·실루엣 대조 TSV) |
| [docs/plan.md](docs/plan.md) | 전체 계획과 단계별 진행 |

## 작업 흐름

```
자료 → Strategist(문서 기획·확인) → Executor(장별 집필, HDS JSON)
     → rhwp scaffold(한글 문서 조립) + rhwp edit(쪽 나눔·그림·목록·표 꾸밈·강조·각주·머리말) → 검사(규약·개요·조판·쪽수) → exports/*.hwpx
```

## 디렉터리

```
skills/hwp-master/   SKILL.md, references/, workflows/, scripts/, templates/
docs/                사용 설명서·계획 문서
examples/            예제 결과물
projects/            작업 폴더 (Git 제외)
```

## 참고한 프로젝트

설계 개념만 참고했으며 코드는 포함하지 않습니다.

- [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) — 작업 흐름, 역할 전환, 검사기 원칙, 설치 안내 구성
- [hawa130/docx-master](https://github.com/hawa130/docx-master) — 스타일 바인딩·자동 번호 규약과 감사
- [dashbear-ai/grant-master](https://github.com/dashbear-ai/grant-master) — 장문 단위 집필

## 라이선스

MIT
