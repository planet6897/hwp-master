# hwp-master 사용 설명서

hwp-master 는 자료나 주제를 받아 **한컴오피스에서 바로 편집할 수 있는 한글 문서(HWPX)** 를 만들고,
기존 한글 문서를 안전하게 고치는 Claude 스킬입니다. 문서를 만들고 고치는 일은 rhwp 가 하고,
hwp-master 는 기획·집필 절차와 결과 검사를 맡습니다.

쓰는 방법은 두 가지입니다.

- **Claude 에게 말로 요청** — 대부분은 이것으로 충분합니다. Claude 가 알맞은 라우트를 골라 아래 명령을 대신 실행하고 결과를 알려 줍니다.
- **스크립트를 직접 실행** — 같은 일을 사람이 터미널에서 하거나, 다른 자동화에 넣을 때.

## 1. 처음 한 번 — 설치와 확인

설치는 [README](../README.md#1-설치)를 따릅니다. Claude Code 에 "https://github.com/planet6897/hwp-master 설치해줘"라고
하면 AI 가 `~/hwp-master` 에 저장소 받기·rhwp 0.9.0 설치·확인·자동 테스트를 하고, 스킬을
`~/.claude/skills/hwp-master` 에 링크로 연결합니다.

- **설치한 그 창에서 바로** 문서를 요청해도 됩니다. 설치한 AI 가 지침(`AGENTS.md`·`SKILL.md`)을 읽어 둡니다.
- **다음부터는 어느 폴더에서 `claude` 를 실행해도** hwp-master 스킬이 자동으로 로딩됩니다. 자료가 있는 폴더에서
  실행하면 작업 폴더 `projects/` 도 그 아래에 생겨 찾기 쉽습니다.
- 연결은 복사가 아니라 링크라서 `~/hwp-master` 에서 `git pull` 하면 스킬도 함께 갱신됩니다.
  연결을 풀려면 `python3 ~/hwp-master/skills/hwp-master/scripts/install_skill.py --remove` 합니다.

직접 확인하려면:

```bash
rhwp --version        # rhwp v0.9.0 이상
python3 --version     # 3.9 이상
```

Windows 에서 `python3` 이 Microsoft Store 를 열면 이 문서의 `python3` 을 `python`(또는 `py -3`)으로 바꿔 씁니다.

새 세션에서 "hwp-master 로 뭘 할 수 있어?"라고 물어 스킬이 보이는지 확인합니다(플러그인으로 설치했을 때도 같습니다). rhwp 를 찾지 못하면 Claude 가 멈추고 설치 안내를 보여 줍니다.

스크립트를 직접 실행할 때는 스킬 폴더를 변수로 잡아 두면 편합니다.

```bash
# "설치해줘"로 설치(스킬 연결)·수동 복사
HM=~/.claude/skills/hwp-master
# 플러그인 설치
HM=$(ls -d ~/.claude/plugins/cache/hwp-master/hwp-master/*/skills/hwp-master | tail -1)   # 설치된 버전 폴더
# 저장소 clone 만 했을 때: HM=<clone 위치>/skills/hwp-master
python3 $HM/scripts/rhwp_locate.py      # rhwp 경로와 버전 확인
```

rhwp 가 PATH 에 없으면 `export RHWP_BIN=/경로/rhwp` 로 지정합니다(모든 스크립트는 `--rhwp 경로`도 받습니다).

## 2. 무엇을 할 수 있나 — 라우트 고르기

| 하고 싶은 일 | 라우트 | Claude 에게 이렇게 |
|---|---|---|
| 주제나 자료로 보고서·사업계획서·기안문 새로 만들기 | Default | "이 자료로 개조식 보고서를 한글 파일로 만들어 줘. 5쪽 이내." |
| 짧은 문서를 빨리 | Quick | "회의 결과를 한 쪽짜리 한글 문서로 빨리 만들어 줘." |
| MD·DOCX·PDF 를 한글 문서로 옮기기 | Convert-in | "이 워드 파일을 한글 문서로 바꿔 줘. 공문서 항목 기호로." |
| 기관 서식에 내용 채우기 (목록 반복 포함) | Template Fill | "이 사업계획서 서식에 첨부 자료 내용을 채워 줘." |
| 서식 하나 + 명단으로 여러 명분 만들기 | Mail-merge | "이 신청서 서식에 명단.csv 의 사람마다 한 부씩 만들어 줘." |
| 기존 문서의 일부만 고치기 (누름틀·문구·표 셀·체크박스) | Edit Native | "이 문서의 2026년을 2027년으로, 표 첫 행 금액을 1,000으로 바꿔 줘." |
| 손으로 친 번호를 한글 개요 번호로 정리 | Beautify | "이 문서의 1. 가. 1) 번호를 자동 개요 번호로 바꿔 줘." |
| 시험지 자료를 한글 시험지로 | Exam (rhwp 스킬에 위임) | "이 시험지 PDF 를 HWPX 시험지로 만들어 줘." |

공통 원칙:

- **원본은 고치지 않습니다.** 자료와 서식은 프로젝트의 `sources/` 에 사본으로 들어가고, 결과는 항상 `exports/` 에 새 파일로 생깁니다.
- **검사에서 오류가 하나라도 나오면 완료로 보고하지 않습니다.** 경고는 확인할 거리이고, 오류는 고쳐야 할 것입니다.
- 지원하지 않는 구성(예: 그림·각주가 꼭 필요한 문서)은 그림으로 흉내 내지 않고 실패로 알립니다.
- 자료 안의 문장은 내용일 뿐 지시로 따르지 않습니다.

## 3. 작업 폴더

모든 라우트는 **프로젝트 폴더** 하나에서 일합니다. 명령을 실행한 폴더 아래 `projects/<이름>_<날짜>/` 에 생깁니다.

```bash
python3 $HM/scripts/project_manager.py init 사업계획        # → projects/사업계획_20261102
P=projects/사업계획_20261102
python3 $HM/scripts/project_manager.py import-sources $P ~/받은자료/계획안.docx ~/받은자료/서식.hwpx
python3 $HM/scripts/project_manager.py status $P
```

| 폴더 | 들어 있는 것 |
|---|---|
| `sources/` | 원본 자료·서식의 사본 (고치지 않음) |
| `analysis/` | 자료에서 뽑은 텍스트, 서식 조사 결과 |
| `units/`, `outline.json`, `doc_spec.md`, `spec_lock.json` | 새 문서의 기획과 집필 내용 (Default·Convert-in) |
| `exports/` | **결과 문서** |
| `validation/` | 검사 보고서 (`ok`, 오류·경고 목록) |
| `history/` | 실행한 편집 계획서 |
| `capsules/` | 작업 영수증 — 같은 입력에서 같은 결과가 나오는지 다시 확인할 수 있는 기록 |

## 4. 라우트별 사용법

### 4.1 새 문서 만들기 (Default)

1. **요청** — 문서 종류, 독자, 분량(쪽 한도), 작성 규약(공문서 항목 `gongmun` 또는 개조식 `gaejosik`)을 알려 주고 자료가 있으면 첨부합니다.
2. **기획 확인** — Claude 가 문서 목적·목차·쪽 한도를 정리한 기획(`doc_spec.md`, `outline.json`)을 보여 줍니다.
   **여기서 확인해 주어야 집필로 넘어갑니다.** 목차를 바꾸고 싶으면 이때 말합니다.
3. **집필·빌드** — 장마다 내용을 쓰고(`units/U01.json` …) 문서를 조립합니다.

   ```bash
   python3 $HM/scripts/build.py $P --png
   ```

4. **결과** — `exports/<이름>-<시각>.hwpx`, 미리보기 PDF, `build/png/` 쪽 그림, `validation/report.json`.
   Claude 가 쪽수, 남은 경고, 영수증 경로를 알려 줍니다.

고치고 싶을 때는 결과 HWPX 를 손대지 말고 "2장의 예산 표에 운영비 행을 추가해 줘"처럼 요청합니다.
Claude 는 해당 장(`units/*.json`)을 고쳐 다시 빌드합니다. 한컴에서 직접 고친 파일은 다음 빌드에 반영되지 않습니다.

### 4.2 짧은 문서 빨리 (Quick)

2쪽 이하이고 기획 확인이 필요 없을 때. 문서 하나의 내용(HDS JSON)을 바로 빌드합니다. 이어서 고쳐 가는 작업에는 Default 를 씁니다.

```bash
python3 $HM/scripts/build.py --quick 회의결과.hds.json -o 회의결과.hwpx --max-pages 1 --convention gaejosik
```

HDS 는 제목(`heading`)·문단(`paragraph`)·표(`table`)·쪽 나눔(`page_break`)·그림(`image`)·목록(`list`)·목차(`toc`) 블록으로 쓰고(표는 제목 행 `header_row`·열 폭 비율 `col_widths`·병합 `merges`·셀 강조 `cell_emphasis`), 문단에 글자 강조(`emphasis`)·각주(`footnotes`), 문서에 머리말(`header`)·꼬리말(`footer`, `{page}` 쪽 번호)·용지 방향(`orientation`)·여백(`margins_mm`)을 붙일 수 있습니다. 작성법은
`$HM/references/hds_authoring.md` 에 있습니다. 제목에 번호("1. 추진 배경")를 직접 쓰지 않습니다 — 한글이 자동으로 붙입니다.

### 4.3 다른 형식 자료를 옮기기 (Convert-in)

```bash
python3 $HM/scripts/project_manager.py import-sources $P 계획안.docx
python3 $HM/scripts/convert.py $P sources/계획안.docx --convention gongmun
python3 $HM/scripts/build.py $P
```

- 옮길 수 있는 것: 제목·문단·목록·표. 그림·글자 서식·각주·병합 셀은 옮기지 못하고 `analysis/<자료>.convert.json` 의 손실 목록에 남습니다.
- PDF 는 제목 단계를 알 수 없어 문단 나열이 됩니다. Claude 가 제목을 다시 잡고 장을 나눈 뒤 빌드합니다.

### 4.4 기관 서식 채우기 (Template Fill)

**서식 준비** — 한컴에서 서식을 열고 채울 자리에 다음처럼 입력해 저장합니다.

| 입력 | 뜻 |
|---|---|
| `{{기관명}}` | 값 하나. 같은 이름을 여러 곳에 쓰면 모두 같은 값이 됩니다. |
| `{{과제.이름}}`, `{{과제.내용}}` | 반복 칸. 표의 한 행에 넣으면 그 행이, 본문 문단에 넣으면 그 문단들이 자료 수만큼 반복됩니다. |
| 누름틀 | 이미 누름틀이 있는 서식은 그 이름으로 채웁니다. |

예: 표의 둘째 행 셀에 `{{예산.항목}}` | `{{예산.금액}}` 을 쓰면, 예산 자료 3건이 3개 행이 됩니다.

**채우기**

```bash
python3 $HM/scripts/project_manager.py import-sources $P 서식.hwpx
python3 $HM/scripts/template_fill.py inspect $P sources/서식.hwpx
#   → analysis/template_서식.data.example.json : 채울 칸 목록이 들어간 자료 틀
cp $P/analysis/template_서식.data.example.json $P/sources/data.json    # 값을 채워 넣는다
python3 $HM/scripts/template_fill.py fill $P sources/서식.hwpx --data sources/data.json
```

자료 JSON 모양:

```json
{"기관명": "한국문서진흥원",
 "과제": [{"이름": "첫째 과제", "내용": "첫째 내용"}, {"이름": "둘째 과제", "내용": "둘째 내용"}],
 "성명": "누름틀에 들어갈 값"}
```

Claude 에게 맡기면 서식 조사 → 자료에서 값 뽑기 → `data.json` 작성 → 채우기 → 검사까지 합니다.
값을 정하기 어려운 칸(예: 자료에 없는 날짜)은 물어봅니다.

**자주 쓰는 서식 등록**

```bash
python3 $HM/scripts/template_fill.py register 우리기관_보고서.hwpx --name 기관보고서
python3 $HM/scripts/template_fill.py fill $P --house 기관보고서 --data sources/data.json
```

등록한 서식은 `~/.hwp-master/templates/house/` 에 보관됩니다. 서식 파일이 등록 때와 달라지면 채우지 않고 다시 등록하라고 알립니다.

**주의** — 한 칸에 줄바꿈은 넣을 수 없습니다(여러 줄이면 반복 칸으로 만듭니다). 반복 칸 자료가 0건이면
원형을 지우지 못하므로 "해당 없음" 같은 1건을 넣습니다. 머리말·꼬리말·글상자 안의 칸은 `{{ }}` 대신 누름틀로 만듭니다.

**문서 유형 프로필** — 서식 파일 대신 *실제로 작성된 문서들*이 있으면, 그 문서들의 서식을 뽑아 등록하고
새 문서를 같은 서식으로 만들 수 있습니다.

기본 프로필 37종이 함께 들어 있습니다 — 문서 유형별 12종(공문·보도자료·법령안·연구용역·공고·권고문·규제영향분석서·시행계획 등)과
부처·국회입법조사처·선관위 보도자료 등 기관별 25종입니다. 공공기관 공개 문서 약 1만 2천 건(2026-07~11 수집)의
관측 통계로 만든 *예시*이며 공식 서식 기준은 아닙니다. 목록과 핵심 서식은 `list` 로 봅니다.

```bash
python3 $HM/scripts/style_profile.py list                      # 전체
python3 $HM/scripts/style_profile.py list --search 보도자료     # 이름에 낱말이 든 것만
```

```
이름                         구분  문서    글꼴      본문  제목  줄간격  항목  쪽번호
보도자료                     기본  1722건  -         14pt  15pt  -       -     넣음
보도자료-국회입법조사처      기본  270건   바탕      10pt  -     160%    -     -
보도자료-중앙선거관리위원회  기본  300건   휴먼명조  14pt  -     -       -     넣음
보도자료-행정안전부          기본  60건    바탕      14pt  15pt  -       □ ○   넣음
...
```

`-` 는 문서마다 달라서(일치율 50% 미만) 정하지 않은 항목입니다. 그 항목은 hwp-master 기본값을 씁니다.

Claude 에게는 이름을 말하면 됩니다. 이름을 모르면 유형·기관만 말해도 Claude 가 후보 표를 보여 주고 고르게 합니다.

```
나: 보도자료-교육부 프로필로 새 학기 돌봄 확대 보도자료를 만들어 줘
나: 규제영향분석서 형식으로 이 개정안 검토 문서를 만들어 줘
나: 권익위 권고문처럼 만들 수 있는 프로필 있어?
```

| 기본 프로필 | 이름 |
|---|---|
| 문서 유형별 12종 | 공문 · 공고 · 권고문 · 규제영향분석서 · 기타 · 법령안 · 보도자료 · 속기자료 · 시행계획 · 연구용역 · 의결서 · 제개정이유서 |
| 기관별 25종 | 보도자료-(고용노동부 · 공정거래위원회 · 과학기술정보통신부 · 교육부 · 국무조정실 · 국토교통부 · 국회입법조사처 · 기획재정부 · 농림축산식품부 · 농촌진흥청 · 문화체육관광부 · 보건복지부 · 산림청 · 산업통상자원부 · 식품의약품안전처 · 외교부 · 정책브리핑 · 중소벤처기업부 · 중앙선거관리위원회 · 질병관리청 · 해양수산부 · 행정안전부 · 환경부), 기타-정책브리핑, 속기자료-정책브리핑 |

`--json` 은 기계용 출력입니다.
내 문서로 만든 프로필이 같은 이름이면 그것이 우선합니다.

```bash
python3 $HM/scripts/style_profile.py register ~/받은문서/의결서/ --name 권익위_의결서
python3 $HM/scripts/style_profile.py show 권익위_의결서
```

```
프로필 권익위_의결서 — 문서 24건
  여백(mm): 좌20 우20 위15 아래15 (42%)   ← 괄호는 그 값을 쓴 문서 비율
  글꼴: 휴먼명조 (79%)
  본문: 15.0pt (46%)
  ...
```

Claude 에게 "권익위_의결서 프로필로 만들어 줘"라고 하면 기획 단계에서 프로필을 적용합니다(`lock`).
같은 유형 문서를 5건 이상 주세요. 일치율이 50% 아래인 값은 적용하지 않습니다. 등록 위치는 `~/.hwp-master/profiles/` 이고,
기본 프로필은 `skills/hwp-master/profiles/` 에 있습니다(읽기 전용).

### 4.5 여러 명분 만들기 (Mail-merge)

서식의 누름틀 이름과 명단(CSV 첫 줄 또는 JSONL 키)을 맞춥니다.

```bash
python3 $HM/scripts/project_manager.py import-sources $P 신청서.hwpx 명단.csv
python3 $HM/scripts/merge.py $P --form sources/신청서.hwpx --data sources/명단.csv --name-field 성명
```

결과는 `exports/merge_<시각>/` 에 사람마다 한 파일(`홍길동.hwpx` …)입니다. 명단에는 개인정보가 있을 수 있으니
결과 파일과 `validation/*.ndjson` 을 대화나 외부로 붙여 넣지 않습니다.

### 4.6 기존 문서 고치기 (Edit Native)

```bash
python3 $HM/scripts/project_manager.py import-sources $P 신청서.hwp
python3 $HM/scripts/edit.py inspect $P sources/신청서.hwp        # 누름틀·표·제목 조사
python3 $HM/scripts/edit.py apply $P 고칠내용.plan.json
```

고칠 내용(`고칠내용.plan.json`)은 다음 모양입니다. Claude 에게 말로 요청하면 이 계획서를 대신 씁니다.

```json
{"planVersion": "1.0", "input": "sources/신청서.hwp", "steps": [
  {"action": "fill_fields", "data": {"회사명": "예시소프트"}},
  {"action": "replace_text", "find": "2026년", "replace": "2027년"},
  {"action": "set_cell", "table": 0, "row": 1, "col": 2, "text": "1,000"}
]}
```

- 계획 전체가 한 번에 저장됩니다. 하나라도 실패하면 아무것도 바뀌지 않습니다.
- 고치지 않은 쪽의 배치가 움직이면 오류로 알립니다.
- 이어서 고칠 때는 앞 결과(`exports/…`)를 다음 계획의 `input` 으로 씁니다. 작업 영수증이 사슬로 이어집니다.

### 4.7 번호 정리 (Beautify)

```bash
python3 $HM/scripts/beautify.py propose $P sources/보도자료.hwpx      # 바꿀 문단 목록 → history/beautify_<시각>.plan.json
python3 $HM/scripts/beautify.py apply $P history/beautify_<시각>.plan.json
```

- `propose` 결과에서 바꾸지 않을 문단을 지운 뒤 `apply` 합니다. 글자는 그대로이고 번호만 자동 개요 번호가 됩니다.
- HWP 는 먼저 `rhwp export-hwpx 문서.hwp 문서.hwpx` 로 바꿉니다.
- **바꾼 뒤 한글에서 꼭 열어 보세요.** 번호 뒤 간격이 달라져 줄바꿈·쪽 나눔이 바뀔 수 있습니다(B150 경고).

### 4.8 시험지 (Exam)

hwp-master 가 아니라 rhwp 의 `rhwp-exam-ingest` 스킬이 만듭니다. Claude 에게 요청하면 그 스킬로 넘깁니다.

## 5. 결과 확인하기

1. **검사 보고서** — `validation/` 의 최신 JSON 에서 `"ok": true` 인지 봅니다. 오류·경고는 `findings` 에
   코드와 함께 있습니다. 코드의 뜻은 각 라우트 문서(`$HM/workflows/*.md`)의 표에 있습니다.
2. **눈으로** — 결과 HWPX 를 한컴오피스에서 엽니다. rhwp 미리보기(PNG·PDF)는 한컴과 다를 수 있으므로
   제출 전 최종 확인은 한컴에서 합니다. 특히 표가 길어졌거나 쪽수가 바뀌었다는 경고가 있으면 그 쪽을 봅니다.
3. **재현 확인 (필요할 때)** — 같은 입력에서 같은 결과가 나오는지:

   ```bash
   python3 $HM/scripts/receipt.py verify $P/capsules/<이름>.receipt.json       # 새 문서 (exit 0 이면 재현)
   (cd $P && rhwp lineage capsules/<마지막>.capsule.json --json)              # 편집·서식 채우기 (brokenAt 이 null)
   ```

## 6. 막혔을 때

| 증상 | 원인과 대처 |
|---|---|
| "rhwp 바이너리를 찾지 못했습니다" | rhwp 를 설치하고 PATH 에 두거나 `RHWP_BIN` 을 지정합니다. |
| 다른 폴더에서 연 Claude 가 hwp-master 를 모름 | 스킬이 연결되지 않았습니다. `python3 ~/hwp-master/skills/hwp-master/scripts/install_skill.py` 를 실행하고 Claude Code 를 다시 엽니다. |
| install_skill.py "이미 있고 이 저장소를 가리키지 않는다" | `~/.claude/skills/hwp-master` 에 다른 복사본이나 이전 설치가 있습니다. 필요 없으면 그 폴더를 직접 지운 뒤 다시 실행합니다. 플러그인 설치와 함께 쓰면 스킬이 둘 보이니 하나만 남깁니다. |
| "최소 버전 0.9.0 미만" | 릴리스 v0.8.7 은 지원하지 않습니다. `python3 skills/hwp-master/scripts/install_rhwp.py` 로 이 저장소 Release 의 0.9.0 을 설치합니다. |
| 제목이 "1. 1. 추진 배경" 처럼 이중 번호 | 제목에 번호를 직접 썼습니다(E101). 번호를 지웁니다. |
| 표 셀 글이 미리보기에서 잘림 (W201) | 한컴에서는 줄바꿈됩니다. 셀 문장을 줄이거나 열 수를 줄입니다. |
| 쪽수 초과 (R104) | 분량을 줄이거나 쪽 한도를 늘립니다. |
| 미리보기에 한글이 안 보임 (R105, Linux) | 한글 글꼴이 없습니다. `sudo apt install fonts-noto-cjk` 하거나 `build.py --font-path <글꼴 폴더>` 를 줍니다. HWPX 자체는 정상입니다. |
| Template Fill T101 | 자료에 빠진 칸이 있습니다. 메시지의 키를 `data.json` 에 넣습니다. |
| Template Fill T105 | 반복 칸 자료가 비었습니다. "해당 없음" 1건을 넣습니다. |
| Template Fill T003 | 등록 이후 서식이 바뀌었습니다. `register --force` 로 다시 등록합니다. |
| Beautify B001 | 문서의 개요 번호 정의가 공문서 순서(1. 가. 1) …)가 아닙니다. 한컴 새 문서에서 만든 문서인지 확인합니다. |
| Edit R202 | 고치지 않은 쪽이 움직였습니다. 고친 문구가 길어져 뒤가 밀렸는지 한컴에서 확인합니다. |

## 7. 지금 할 수 없는 것

- 새 문서에서 수식은 아직 만들 수 없습니다. 각주 구분선은 없고, 머리말·꼬리말은 모든 쪽에 같은 한 줄입니다.
- 기관 서식 파일의 스타일을 그대로 이어받아 새 문서를 만들지는 못합니다. 서식은 Template Fill 로 채우거나, 실제 문서들로 문서 유형 프로필을 만들어 글꼴·크기·여백·항목 기호를 맞춥니다.
- 개요 번호 모양은 한글 기본(`1.`, `1.1.`)입니다.
