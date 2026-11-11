<p align="center"><img src="assets/icon-256.png" width="128" alt="hwp-master 아이콘"></p>

# hwp-master — AI 가 rhwp 로 한글(HWP/HWPX) 문서를 읽고 쓰고 고칩니다

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![rhwp](https://img.shields.io/badge/engine-rhwp%200.9.0-blue)](https://github.com/edwardkim/rhwp)

**이 프로젝트는 [rhwp](https://github.com/edwardkim/rhwp) 를 사용해 hwp/hwpx 를 읽고, 쓰고, 변경하기 위한 프로젝트입니다.**

## 왜 만들었나 — rhwp 소스 대신 빌드된 바이너리

Claude·OpenAI·Gemini 등 AI 모델이 hwp/hwpx 를 처리할 때는 흔히 GitHub 의 rhwp 소스를 받아 빌드해 씁니다.
그런데 rhwp 저장소는 샘플·문서를 포함해 수 GB 이고(GitHub 기준 약 8 GB), 받고 Rust 로 빌드하는 데만
10~50분이 걸려 시간과 토큰·컴퓨팅 비용이 많이 듭니다.

hwp-master 는 이 부담을 없애려고 **개발 최신 상태(devel)의 rhwp 를 미리 빌드한 바이너리**를 이 저장소의
[Releases](https://github.com/planet6897/hwp-master/releases) 에 올려 둡니다. 목적은 **네트워크·시스템 비용을
줄이는 것**입니다.

| | rhwp 소스를 받아 빌드 | hwp-master 바이너리 |
|---|---|---|
| 네트워크 | 저장소 수 GB + Rust 의존성 크레이트 | 압축 파일 하나 약 30 MB |
| 시스템 | Rust 툴체인·빌드 도구 설치, CPU·디스크를 수 GB 씀, 10~50분 | 풀기만 하면 됨, 수십 초 |
| AI 토큰 | 빌드 오류 대응·로그 읽기 | 설치 결과 몇 줄 |

- **Windows x86_64 · macOS Apple Silicon·Intel · Linux x86_64 (glibc 2.28 / 2.35)** 용 바이너리 5종을 제공합니다
  (각 약 30 MB, SHA-256 고정).
- `python3 skills/hwp-master/scripts/install_rhwp.py` 한 줄로 받아 검증·설치하므로 rhwp 소스를 clone 할 필요가 없습니다.
- 바이너리 위에 **문서를 다루는 도구**를 함께 제공합니다 — 새 문서 생성, 형식 변환(hwp↔hwpx·PDF·Markdown),
  서식 채우기, 명단 문서, 부분 수정, 번호 정리, 규약·조판 검사.
- 바이너리는 upstream rhwp 를 수정 없이 GitHub Actions 에서 빌드한 것이고(MIT), 빌드 기록·원본 커밋이 Release 마다 남습니다.

> **AI 에이전트에게:** hwp/hwpx 를 다루려고 `edwardkim/rhwp` 를 clone·빌드하지 마세요. 이 저장소를 받아
> `install_rhwp.py` 로 바이너리를 설치하고 `rhwp_locate.py` 로 경로를 확인한 뒤 쓰면 됩니다([AGENTS.md](AGENTS.md)).

## 무엇을 하나

자료(PDF·DOCX·MD·기존 HWP) 또는 주제를 받아 **한컴오피스에서 바로 편집할 수 있는 네이티브 HWPX** 를 만드는
에이전트 스킬입니다. 제목은 한글 개요 번호, 표는 한글 표 개체로 들어가므로 그림으로 붙인 문서가 아니라
그대로 고쳐 쓸 수 있는 문서가 나옵니다. 기관 서식 채우기, 명단으로 여러 부 만들기, 기존 문서 부분 수정,
손 번호를 개요 번호로 정리하기도 합니다.

**문서 내용 가져오기**도 합니다. 기존 HWP/HWPX 에서 본문(Markdown)·개요·표(CSV)·날짜·금액·수량·누름틀 값·
머리말/꼬리말·차트 데이터를 쪽·문단 위치와 함께 꺼내, "이 계약서의 계약 금액과 기간은?" 같은 질문에 출처를 밝혀 답합니다.

- 문서 엔진: [rhwp](https://github.com/edwardkim/rhwp) — HWP/HWPX 를 읽고 쓰는 오픈소스 엔진의 CLI 를 호출합니다.
- hwp-master 는 기획·집필 절차와 결과 검사를 맡습니다. 검사에서 오류가 하나라도 나오면 완료로 보고하지 않습니다.

> 상태: 0.1.17 — 새 문서(Default·Quick)·형식 변환·서식 채우기·명단 문서·부분 수정·번호 정리, 시험지 위임.
> 새 문서의 블록은 제목·문단·표·쪽 나눔·그림(캡션)·목록(□ ○ - · 단계, 내어쓰기)·목차(쪽 번호)·표 캡션·제목 행·열 폭 비율·셀 병합·셀 강조와 글자 강조(굵게·기울임·밑줄·취소선·색·형광펜·크기)·캡션 자동 번호·각주·머리말·꼬리말(쪽 번호)·용지 방향과 여백입니다. 예제 결과물은 [examples/](examples/) 에 있습니다.

## 빠른 시작

### 1. 설치

**필요한 것:** [Git](https://git-scm.com/downloads), [Python](https://www.python.org/downloads/) 3.9 이상,
[Claude Code](https://claude.ai/code). 그 밖의 것은 AI 가 설치합니다.

Claude Code 를 실행하고 GitHub 주소와 함께 "설치해줘"라고 입력합니다.

```
나: https://github.com/planet6897/hwp-master 설치해줘
```

AI 가 저장소를 홈 폴더의 `~/hwp-master` 에 받고, 문서 엔진 rhwp 를 설치·확인하고, 자동 테스트까지 돌린 뒤
hwp-master 스킬을 Claude Code 사용자 스킬 폴더(`~/.claude/skills/hwp-master`)에 연결합니다.
다른 위치를 원하면 함께 말합니다("~/tools/hwp-master 에 설치해줘").

**설치가 끝나면 그 창에서 바로 문서를 요청하면 됩니다**(아래 [2. 만들기](#2-만들기)). 설치한 AI 가 지침을 읽어 두고,
다음부터는 어느 폴더에서 `claude` 를 실행해도 스킬이 자동으로 로딩됩니다. 연결은 복사가 아니라 링크라서
`git pull` 하면 스킬도 함께 갱신됩니다. 연결을 풀려면 `python3 skills/hwp-master/scripts/install_skill.py --remove` 합니다.

<details>
<summary><strong>AI 에이전트용 설치 절차</strong> — GitHub 주소와 함께 설치를 요청받았을 때</summary>

1. 사용자가 위치를 말하지 않았으면 `git clone https://github.com/planet6897/hwp-master.git ~/hwp-master` 한다
   (Windows 는 `%USERPROFILE%\hwp-master`). 그 폴더가 이미 있으면 덮어쓰지 말고, 이 저장소의 clone 이면
   `git pull` 할지 사용자에게 묻는다.
2. 받은 폴더(저장소 루트)에서 차례로 실행한다. Windows 에서 `python3` 이 안 되면 `python`(또는 `py -3`)을 쓴다.
   1. `python3 skills/hwp-master/scripts/install_rhwp.py` — 지원하지 않는 플랫폼이면 아래 "소스에서 빌드"를 안내하고 멈춘다.
   2. `python3 skills/hwp-master/scripts/rhwp_locate.py` — 경로·버전 확인 (Linux 는 한글 글꼴 경고도 전한다).
   3. `python3 -m unittest discover -s tests` — 실패가 있으면 출력과 함께 알리고 설치 완료로 보고하지 않는다.
   4. `python3 skills/hwp-master/scripts/install_skill.py` — `~/.claude/skills/hwp-master` 에 스킬 링크를 만든다.
      이미 다른 hwp-master(플러그인·복사본)가 있어 실패하면 그 내용을 알리고, 지우지 말고 사용자에게 묻는다.
3. 설치한 이 세션에서 바로 문서를 만들 수 있게 받은 폴더의 `AGENTS.md` 와 `skills/hwp-master/SKILL.md` 를 읽는다.
   이후 작업은 저장소 루트를 작업 폴더로 삼는다.
4. 받은 폴더의 절대 경로와, 이 창에서 바로 문서를 요청해도 되고 다음부터는 어느 폴더에서 `claude` 를 실행해도
   hwp-master 스킬이 자동으로 로딩된다는 안내로 마친다.
</details>

> 공개 저장소라 GitHub 계정이나 로그인 없이 설치할 수 있습니다. clone 과 rhwp 받기 모두 공개 주소를 씁니다.

> Claude Code 는 터미널 CLI 대신 VS Code / JetBrains 확장으로 써도 됩니다. 설치 요청은 같고, 끝난 뒤
> **File → Open Folder** 로 `~/hwp-master` 폴더를 엽니다. 저장소 루트의 `AGENTS.md` 를 읽는 다른
> 에이전트(Codex CLI, Cursor, Gemini CLI 등)도 동작할 수 있으나 확인하지 않았습니다.

**나중에 업데이트:** `~/hwp-master` 에서 연 Claude Code 에 "업데이트해줘"라고 하거나 직접 `git pull` 합니다.
rhwp 버전이 바뀌었으면 다시 "설치해줘"라고 하면 됩니다.

<details>
<summary><strong>직접 설치하기</strong> — AI 없이 터미널에서, 또는 설치가 막혔을 때</summary>

AI 가 "설치해줘"에서 하는 일은 아래 명령과 같습니다.

```bash
git clone https://github.com/planet6897/hwp-master.git ~/hwp-master
cd ~/hwp-master
python3 skills/hwp-master/scripts/install_rhwp.py
# rhwp v0.9.0 설치 — ~/.local/share/hwp-master/rhwp/rhwp-0.9.0-e118de8/rhwp/rhwp
python3 skills/hwp-master/scripts/rhwp_locate.py    # 설치한 바이너리를 자동으로 찾는다 (Linux 는 한글 글꼴도)
python3 -m unittest discover -s tests               # 자동 테스트 (rhwp 0.9.0 에서 모두 OK)
python3 skills/hwp-master/scripts/install_skill.py  # ~/.claude/skills/hwp-master 에 스킬 연결 (어느 폴더에서나 자동 로딩)
```

Windows 에서는 `python3` 이 Microsoft Store 바로가기라 실행되지 않을 수 있습니다. 그때는 `python3` 을
`python`(또는 `py -3`)으로 바꿔 실행합니다.

rhwp 바이너리는 공개 upstream [edwardkim/rhwp](https://github.com/edwardkim/rhwp) 의 고정 커밋을 GitHub Actions 에서
그대로 빌드한 것이고(MIT), 어느 Release·커밋·SHA-256 을 쓸지는 `skills/hwp-master/rhwp_release.json` 에 고정돼 있습니다.

| OS | 파일 | 확인 |
|---|---|---|
| macOS Apple Silicon (arm64) | `rhwp-0.9.0-e118de8-macos-aarch64.tar.gz` | CI 빌드·PNG 내보내기 검증 (2026-11-11) |
| macOS Intel (x86_64) | `rhwp-0.9.0-e118de8-macos-x86_64.tar.gz` | CI 빌드·PNG 내보내기 검증 (2026-11-11) |
| Linux x86_64, glibc 2.35 이상 (Ubuntu 22.04+, Debian 12+, RHEL 10) | `rhwp-0.9.0-e118de8-linux-x86_64-glibc2.35.tar.gz` | CI 빌드·PNG 내보내기 검증 (2026-11-11) |
| Linux x86_64, glibc 2.28 이상 (RHEL/Rocky 8·9, Debian 10·11, Ubuntu 20.04) | `rhwp-0.9.0-e118de8-linux-x86_64-glibc2.28.tar.gz` | CI 빌드·glibc 2.28 컨테이너 PNG 내보내기 검증 (2026-11-11) |
| Windows x86_64 | `rhwp-0.9.0-e118de8-windows-x86_64.zip` | 설치·hwp-master 전체 테스트 (Windows 11, 2026-11-11) |

- `install_rhwp.py` 가 CPU 와 Linux 의 glibc 버전(`ldd --version`)을 보고 맞는 파일을 고릅니다. glibc 2.35 이상이면
  2.35 빌드를, 2.28~2.34 면 2.28 빌드를 씁니다. glibc 2.28 미만이나 musl(Alpine)은 소스에서 빌드합니다.

- 받은 압축 파일의 SHA-256 이 `rhwp_release.json` 과 다르면 설치하지 않습니다.
- 받기는 [GitHub CLI](https://cli.github.com/) 가 있으면 `gh release download` 를, 없으면 공개 Release 주소를 씁니다(로그인 불필요).
- Linux 미리보기(PNG·PDF)에 한글이 나오려면 한글 글꼴이 있어야 합니다(`sudo apt install fonts-noto-cjk`). 없으면
  HWPX 는 정상이고 미리보기 한글만 빠지며, hwp-master 가 경고(R105)를 냅니다.
- Linux ARM·Windows ARM·musl·glibc 2.28 미만은 바이너리가 없어 아래처럼 소스에서 빌드합니다.

찾는 순서는 `--rhwp` 인자 → `RHWP_BIN` 환경변수 → 설치 스크립트 바이너리 → PATH 의 `rhwp` 입니다. 직접 빌드한 rhwp 를
쓰려면 `RHWP_BIN=/경로/rhwp` 로 지정합니다.
</details>

<details>
<summary><strong>소스에서 빌드</strong> — Linux ARM·musl·오래된 glibc, 또는 다른 rhwp 커밋을 쓸 때</summary>

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
없으면 빌드 마지막에 `unable to find library -lfreetype` 로 실패합니다.
</details>

<details>
<summary><strong>최신 devel rhwp 배포하기</strong> — 저장소 관리자용</summary>

```bash
gh workflow run rhwp-binaries.yml                     # edwardkim/rhwp devel 최신 커밋을 5개 플랫폼으로 빌드·Release (약 1시간)
gh workflow run rhwp-binaries.yml -f rhwp_ref=<커밋>   # 특정 브랜치·커밋
python3 tools/update_rhwp_release.py                  # 최근 Release 를 rhwp_release.json·README 에 반영
python3 skills/hwp-master/scripts/install_rhwp.py     # 새 바이너리 설치·SHA-256 대조
python3 -m unittest discover -s tests                 # 모두 통과해야 PR 로 올린다
```

워크플로는 브랜치를 먼저 커밋 SHA 하나로 고정해 모든 플랫폼이 같은 커밋을 빌드하고, 그 커밋의 Release 가 이미 있으면
멈춥니다. 각 압축 파일은 패키지 뒤 PNG 내보내기로 검증하고, Release 에 `SHA256SUMS.txt` 와 빌드 기록을 붙입니다.
</details>

<details>
<summary><strong>다른 설치 방법</strong> — ZIP 내려받기, Claude Code 플러그인</summary>

**ZIP 내려받기** (Git 없이): [GitHub 페이지](https://github.com/planet6897/hwp-master)에서 **Code → Download ZIP** 을
눌러 받아 압축을 풀고, 그 폴더에서 `claude` 를 실행해 "설치해줘"라고 합니다(받기 단계 없이 나머지를 진행합니다). ZIP 에는 Git 기록이 없어 `git pull` 로
업데이트할 수 없으니, 최신 ZIP 을 새 폴더에 풀고 이전 폴더의 `projects/` 를 옮깁니다.

**Claude Code 플러그인 마켓플레이스**: 저장소에 `.claude-plugin/marketplace.json` 이 있어 Claude Code 안에서
바로 설치할 수 있습니다.

```bash
/plugin marketplace add planet6897/hwp-master
/plugin install hwp-master@hwp-master
```

업데이트는 `/plugin marketplace update hwp-master` 뒤 `/plugin update hwp-master@hwp-master` 입니다.
플러그인으로 설치하면 스킬 파일만 받습니다(설치 폴더는 갱신 때 바뀌는 캐시입니다). 작업 폴더 `projects/` 는
Claude Code 를 실행한 폴더 아래에 생기고, 등록한 기관 서식은 `~/.hwp-master/templates/house/` 에 보관됩니다.
플러그인 없이 스킬만 쓰려면 `skills/hwp-master` 폴더를 `~/.claude/skills/hwp-master` 로 복사해도 됩니다.
</details>

### 2. 만들기

설치한 그 창에서, 또는 아무 폴더에서 새로 실행한 `claude` 에서 채팅으로 요청합니다. 자료가 있는 폴더에서
실행하면 결과가 그 아래 `projects/` 에 생겨 찾기 쉽습니다.

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

**기관·유형 서식에 맞추기**: 기본 프로필 37종(보도자료·공고·권고문·규제영향분석서·시행계획 등, 부처·국회·선관위 보도자료)
중 하나를 말하면 글꼴·크기·여백·항목 기호를 그 문서들의 관측값에 맞춥니다. 내 기관 문서 5건 이상으로 새 프로필을 등록할 수도 있습니다
([사용 설명서 4.4](docs/usage.md#44-기관-서식-채우기-template-fill)).

```
나: 보도자료-교육부 프로필로 새 학기 돌봄 확대 보도자료를 만들어 줘
```

> **결과물:** `projects/<이름>_<날짜>/exports/` 에 HWPX 와 미리보기 PDF 가 생기고, `validation/report.json` 에 검사 결과,
> `capsules/` 에 같은 결과를 다시 만들 수 있는지 확인하는 빌드 영수증이 남습니다. 미리보기는 rhwp 렌더라 한컴과 다를 수
> 있으니 제출 전에는 한컴에서 열어 확인하세요.

> **이미 있는 한글 문서를 쓰고 싶다면?** 서식과 자료를 주고 "이 서식에 채워 줘"(서식 채우기), "이 신청서 서식에 명단.csv 로
> 한 부씩"(명단 문서), "이 문서의 2026년을 2027년으로"(부분 수정), "1. 가. 1) 번호를 자동 번호로"(번호 정리)처럼 요청합니다.
> 원본은 고치지 않고 결과를 새 파일로 만듭니다. 라우트별 사용법은 [사용 설명서](docs/usage.md)에 있습니다.

> **문서에서 정보를 꺼내려면?** "이 계약서에서 계약 금액과 기간을 뽑아 줘", "이 hwp 의 표를 CSV 로", "신청서에 채워진 값을 표로"처럼
> 요청합니다. 결과는 `exports/extract/<문서>/` 에 Markdown·JSON·CSV 로 남고, 답에는 쪽·표·문단 위치가 붙습니다.

> **잘 안 될 때:** AI 가 맥락을 잃으면 `skills/hwp-master/SKILL.md` 를 읽으라고 하세요. 오류 코드와 대처는
> [사용 설명서의 막혔을 때](docs/usage.md#6-막혔을-때)에 있습니다.

### 3. 사용 예

채팅에 이렇게 말하면 됩니다. 파일 경로는 그대로 붙여 넣습니다. 괄호 안은 AI 가 고르는 라우트입니다.

**새 문서 만들기**

```
나: ~/자료/시장조사.pdf 와 ~/자료/내부메모.docx 로 5쪽 이내 사업계획서를 hwpx 로 만들어 줘          (Default)
나: 다음 주 화요일 부서 회의 결과를 한 쪽짜리 회의록으로 빨리 만들어 줘, 확인은 필요 없어          (Quick)
나: "청년 창업 지원 확대" 기안문을 공문서 항목 기호(1. 가. 1))로 써 줘. 붙임은 예산표 하나         (Default)
나: 보도자료-교육부 프로필로 새 학기 돌봄 확대 보도자료를 만들어 줘                               (Default + 프로필)
나: 가로 방향 A4 에 분기별 실적 표가 들어간 2쪽 보고서로, 표 머리 행은 회색 바탕으로              (Default)
나: 머리말에 "대외비", 꼬리말에 쪽 번호를 넣고 목차가 있는 제안서로 만들어 줘                       (Default)
```

**다른 형식 자료를 한글 문서로 옮기기**

```
나: 이 워드 파일(~/보고서.docx)을 한글 문서로 바꿔 줘. 제목은 개요 번호로                        (Convert-in)
나: README.md 를 개조식(□ ○ -) 한글 보고서로 옮겨 줘                                            (Convert-in)
나: 이 PDF 를 편집할 수 있는 hwpx 로 만들어 줘                                                  (Convert-in)
```

**기존 한글 문서에서 내용·세부 정보 가져오기**

```
나: ~/계약서.hwp 에서 계약 금액, 계약 기간, 지체상금률을 뽑아 줘                                 (Extract)
나: 이 공고문(.hwpx)의 신청 마감일과 제출 서류 목록을 알려 줘                                     (Extract)
나: 이 보고서의 표를 전부 엑셀에서 열 수 있게 CSV 로 줘                                           (Extract)
나: 받은신청서/ 폴더의 hwp 30개에서 신청자 이름·연락처(누름틀 값)를 하나의 표로 모아 줘             (Extract)
나: 이 규정(.hwp)의 장·조 목차만 정리하고, 제12조 내용을 그대로 보여 줘                            (Extract)
나: 이 hwp 를 세 줄로 요약하고, 나온 금액은 모두 쪽 번호와 함께 표로 정리해 줘                      (Extract)
나: 예산서.hwpx 안의 차트 숫자를 CSV 로 꺼내 줘                                                  (Extract)
```

답에는 "1,200,000원 (2쪽, 표 1 3행)"처럼 출처가 붙고, 문서에 없는 값은 추측하지 않고 "찾지 못함"이라고 합니다.

**기존 문서·서식 활용**

```
나: 이 사업계획서 서식(.hwp)의 빈칸에 첨부 자료 내용을 채워 줘. 참여 인력 표는 사람 수만큼 행을 늘려서   (Template Fill)
나: 수료증 서식.hwpx 에 명단.csv 의 사람마다 한 부씩 만들어 줘                                     (Mail-merge)
나: 이 문서의 "2026년"을 전부 "2027년"으로, 예산 표 2행 3열을 "1,500"으로 바꿔 줘                    (Edit Native)
나: 신청서의 "개인정보 수집 동의" 체크박스에 표시하고 신청인 칸에 홍길동을 넣어 줘                    (Edit Native)
나: 손으로 친 1. 가. 1) 번호를 한글 자동 개요 번호로 정리해 줘                                     (Beautify)
나: 우리 기관 보도자료 5건(~/보도자료/*.hwp)으로 서식 프로필을 등록해 줘                             (프로필 등록)
```

**rhwp 바이너리를 바로 쓰기** — 스킬 없이 스크립트나 다른 AI 에이전트에서 (`rhwp_locate.py` 가 경로를 알려 줌)

```bash
RHWP=$(python3 skills/hwp-master/scripts/rhwp_locate.py --json | python3 -c "import sys,json;print(json.load(sys.stdin)['path'])")
$RHWP export-text 문서.hwp --json                 # 쪽별 평문
$RHWP export-markdown 문서.hwpx -o out/           # 쪽별 Markdown
$RHWP export-tables 문서.hwp --json               # 표 격자 (병합 보존)
$RHWP extract-data 문서.hwp --kind amount --json  # 금액과 위치
$RHWP fields 신청서.hwpx --json                   # 누름틀 이름·값
$RHWP export-pdf 문서.hwp -o 문서.pdf             # PDF 미리보기
$RHWP export-hwpx 옛문서.hwp 새문서.hwpx --verify  # HWP → HWPX 변환 (차이 검출)
$RHWP armor 문서.hwp --json                       # LLM 에 넣기 전 프롬프트 주입 신호 검사
```

추출을 한 번에 하려면 `python3 skills/hwp-master/scripts/extract.py <프로젝트> sources/<문서>` 가 위 명령을 묶어
`exports/extract/<문서>/` 에 `summary.json`·`text.md`·`tables/*.csv`·`data.csv`·`fields.csv` 로 남깁니다.

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

## 기여

이슈와 Pull Request 를 환영합니다. `main` 브랜치는 보호되어 있어 직접 push 할 수 없고, 모든 변경은 Pull Request 로
반영합니다(관리자 포함, 강제 push·브랜치 삭제 금지).

1. 저장소를 fork 하거나 브랜치를 만들어 고칩니다.
2. `python3 -m unittest discover -s tests` 가 모두 통과하는지 확인합니다.
3. Pull Request 에 바꾼 내용과 확인 결과를 적습니다. 한글 문서 결과가 바뀌는 변경이면 재현에 쓴 hwp/hwpx 와 미리보기를 함께 올립니다.

## 라이선스

MIT
