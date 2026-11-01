# 새 문서 만들기 (Default / Quick)

스크립트 경로는 `skills/hwp-master/scripts/`이다. rhwp 경로는 `RHWP_BIN` 환경변수나 `--rhwp`로 줄 수 있다.

## 0. 준비

```bash
python3 scripts/rhwp_locate.py          # 실패하면 중단하고 설치 안내
```

## Default

```bash
python3 scripts/project_manager.py init <이름>                     # projects/<이름>_<YYYYMMDD>/
python3 scripts/project_manager.py import-sources <프로젝트> <자료...>  # sources/ 사본 + analysis/ 추출본
```

1. `## [역할 전환: Strategist]` — `references/strategist.md`에 따라
   `doc_spec.md`·`spec_lock.json`·`outline.json`을 쓰고 **사용자 확인**을 받는다.
2. `## [역할 전환: Executor]` — `references/executor.md`·`references/hds_authoring.md`에 따라
   `units/U01.json`부터 쓴다. early 검사 → 나머지 → final 검사.
3. 빌드:

   ```bash
   python3 scripts/build.py <프로젝트> [--png] [--font-path <글꼴 폴더>]
   ```

4. `validation/report.json`의 `ok`가 `true`인지 확인한다. 오류가 있으면 해당 단위를 고쳐 다시 빌드한다.
5. `--png`로 만든 `build/png/*.png`를 열어 쪽마다 눈으로 확인한다(선택).
6. 사용자에게 `exports/*.hwpx` 경로, 쪽 수, 남은 경고, 미리보기 한계(M0 F2·F4)를 알린다.

## Quick

짧은 문서(2쪽 이하)이고 사용자가 빨리 원할 때. 기획 확인과 단위 분할을 생략한다. 재개할 수 없다.

```bash
python3 scripts/build.py --quick <문서.hds.json> -o <출력.hwpx>
```

HDS 문서는 `{"version": "1", "title": ..., "blocks": [...]}` 하나에 전부 쓴다.

## 알려진 한계 (rhwp v0.9.0 기준)

- 표 셀의 긴 텍스트는 한컴에서 줄바꿈되지만 rhwp 미리보기에서는 잘리고 쪽수가 적게 나올 수 있다 (W201).
- rhwp 미리보기에서는 개요 번호 뒤 간격이 한컴보다 좁다 ("1.추진" vs "1. 추진"). 한글 파일 자체는 정상이다.
- 개요 번호 형식은 한글 기본(`1.`, `1.1.`)이며 `가.` 형식은 아직 지정할 수 없다.
