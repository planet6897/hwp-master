# 시험지 만들기 (Exam, 위임)

시험문제 자료(PDF·이미지·MD·DOCX)를 HWPX 시험지로 만드는 일은 rhwp 의 `rhwp-exam-ingest` 스킬이 맡는다.
문제 번호·지문·선택지·그림 bbox 를 Vision 으로 읽어 ingest JSON 을 쓰고 `rhwp build-from-ingest`로 조립하는 경로라,
hwp-master 의 HDS(제목·문단·표)로는 그림 배치와 선택지 구조를 옮길 수 없다.

## 절차

1. rhwp 저장소의 `.claude/skills/rhwp-exam-ingest/SKILL.md`를 연다. 스킬이 설치돼 있지 않으면
   rhwp 저장소를 받아 그 경로를 연다.
2. 그 스킬의 정지 규칙과 단계(입력 정규화 → 페이지별 분석 → ingest.json → 그림 자르기 → `build-from-ingest`)를 따른다.
3. 결과 HWPX 를 hwp-master 프로젝트에 두려면 `import-sources`로 가져오고, 문구 수정은 Edit Native
   라우트(`workflows/edit.md`)로 한다.

hwp-master 의 생성 검사기(HDS 검사·빌드 영수증)는 이 경로에 적용하지 않는다.
