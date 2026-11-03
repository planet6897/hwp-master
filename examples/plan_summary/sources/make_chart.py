"""2022년 국립국어원 업무계획 과제별 예산(억 원, 원문 표기) 가로 막대그래프."""
import sys
from PIL import Image, ImageDraw, ImageFont
font_path, out = sys.argv[1], sys.argv[2]
data = [("1. 국어 정보자원 확대 구축", 57), ("7. 수어·점자 문화 확산", 49), ("5. 한국어교육 내실화", 36.39),
        ("3. 국어정책 기초 연구·조사", 27.1), ("4. 국민의 국어능력 향상", 12.5), ("2. 국어사전 기능 강화", 12.03)]
W, H, S = 1200, 520, 2
img = Image.new("RGB", (W * S, H * S), "white")
d = ImageDraw.Draw(img)
f = ImageFont.truetype(font_path, 26 * S)
fs = ImageFont.truetype(font_path, 22 * S)
left, top, bar_h, gap, maxw = 420, 40, 48, 26, 680
for i, (label, v) in enumerate(data):
    y = top + i * (bar_h + gap)
    d.text(((left - 20) * S, (y + bar_h / 2) * S), label, font=f, fill="#222222", anchor="rm")
    w = maxw * v / 60
    d.rectangle([left * S, y * S, (left + w) * S, (y + bar_h) * S], fill="#1F4E79")
    d.text(((left + w + 12) * S, (y + bar_h / 2) * S), f"{v:g}", font=f, fill="#222222", anchor="lm")
d.line([left * S, (top - 10) * S, left * S, (top + 6 * (bar_h + gap)) * S], fill="#888888", width=2 * S)
d.text(((W - 20) * S, (H - 20) * S), "단위: 억 원 · 6번 과제는 원문에 합계 없음", font=fs, fill="#555555", anchor="rd")
img.resize((W, H), Image.LANCZOS).save(out, optimize=True)
