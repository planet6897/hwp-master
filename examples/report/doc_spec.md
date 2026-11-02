# 문서 기획: rhwp 기반 한글 문서 자동화 도입 검토

- 유형: 개조식 보고서 (`convention: gaejosik`, 명사형 종결)
- 독자와 목적: 부서장 — 반복 한글 문서 작업에 rhwp·hwp-master 를 시범 적용할지 판단
- 분량: 3쪽 이내 (`max_pages: 3`), 본문 약 2,150자
- 서식: 없음 (A4, 함초롬바탕)

## 장 구성

| 장 | 핵심 메시지 |
|---|---|
| 검토 개요 | 한글 문서 생성·편집 자동화 도구 2종을 공개 자료와 실측으로 검토함 |
| 도구 현황 | rhwp 는 MIT 오픈소스 HWP/HWPX 엔진, hwp-master 는 그 위의 문서 작성 스킬 |
| 업무별 적용 가능성 | 보고서 작성·서식 채우기·명단 문서·부분 수정·번호 정리에 대응 기능이 있음 |
| 제약 사항 | rhwp 0.9.0 미릴리스, 새 문서는 제목·문단·표만, 미리보기는 한컴과 다를 수 있음 |
| 검토 의견 | 서식 채우기부터 시범 적용하고 한컴 화면 확인을 절차에 넣음 |

## 근거 자료

- `sources/rhwp_README.md` — rhwp 저장소 README (기능, 배포 채널, 라이선스, 기여자 규모)
- `sources/rhwp_v0.8.7_release.md` — rhwp v0.8.7 릴리스 노트
- `sources/hwp-master_README.md`, `sources/hwp-master_usage.md` — hwp-master 설명·사용법
- `sources/template_fill_example.md` — 서식 채우기 예제의 한컴 대조 실측

## 확인 필요

- 우리 부서의 대상 문서 종류와 월간 건수 (자료 없음 — 본문에 수치를 쓰지 않음)
- 시범 적용 담당자와 기간 (자료 없음)
