# Template Fill 예제

| 파일 | 내용 | SHA-256 |
|---|---|---|
| `form.hwpx` | 자리표시 서식 (`form.spec.json` 을 `rhwp scaffold` 로 만든 것) — 값 칸 4종, 문단 목록 `tasks`, 표 행 목록 `budget` | `55fe8033a953e58c58f8b9da884a5da4b536b895f0424e59f216d3c02868562c` |
| `data.json` | 채울 자료 (과제 3건, 예산 2행) | |
| `filled.hwpx` | 채운 결과 | `95f3c32d532f9ca223f51f52e827d5718812aa86a16ee61d36d71bd0153eb709` |
| `hancom/filled-2024.pdf` | `filled.hwpx` 를 한컴오피스 2024 로 PDF 출력 (1쪽) | `8696ed76e2539e615ae1178750e656b1ae0066c9f2d51342060e192708e2d043` |
| `tsv/filled_native_silhouette.tsv` | rhwp Native 출력과 한컴 PDF 의 쪽별 실루엣 일치율 (1쪽 99.90%) | |

재현 (rhwp 0.9.0, 같은 입력이면 같은 SHA-256):

```bash
P=$(python3 skills/hwp-master/scripts/project_manager.py init tf-demo)   # projects/tf-demo_<날짜>
cp examples/template_fill/form.hwpx examples/template_fill/data.json $P/sources/
python3 skills/hwp-master/scripts/template_fill.py fill $P sources/form.hwpx --data sources/data.json
```

측정: 2026-11-02, `scripts/visual_sweep.py --silhouette-only --compat 2024 --dpi 96`(rhwp 저장소),
rhwp 글꼴 `ttfs/opensource`. 한컴 PDF 텍스트(`pdftotext -layout`)의 줄 순서가 rhwp `export-text` 와 같다.
