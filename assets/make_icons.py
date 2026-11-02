"""아이콘·소셜 미리보기 생성: python3 assets/make_icons.py assets <NotoSansKR-Regular.ttf>

Pillow 가 필요하다. 글자 모양은 Noto Sans KR(SIL OFL 1.1)로 그린 래스터 이미지이며 글꼴 파일은 포함하지 않는다.
"""
import sys
from PIL import Image, ImageDraw, ImageFont
F = sys.argv[2] if len(sys.argv) > 2 else "NotoSansKR-Regular.ttf"
OUT = sys.argv[1]
BLUE, DARK, GREEN, WHITE = (31, 95, 191), (20, 52, 110), (34, 160, 90), (255, 255, 255)

def icon(S=1024):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * .22), fill=BLUE)
    # 문서 한 장 (접힌 모서리)
    x0, y0, x1, y1, f = S * .24, S * .16, S * .76, S * .84, S * .14
    d.polygon([(x0, y0), (x1 - f, y0), (x1, y0 + f), (x1, y1), (x0, y1)], fill=WHITE)
    d.polygon([(x1 - f, y0), (x1 - f, y0 + f), (x1, y0 + f)], fill=(200, 215, 240))
    font = ImageFont.truetype(F, int(S * .34))
    d.text((S * .5, S * .44), "한", font=font, fill=DARK, anchor="mm", stroke_width=int(S * .012), stroke_fill=DARK)
    for i, w in enumerate((.36, .36, .24)):
        y = S * (.62 + i * .06)
        d.rounded_rectangle((S * .32, y, S * (.32 + w), y + S * .022), radius=S * .011, fill=(180, 195, 220))
    # 검사 통과 배지
    cx, cy, r = S * .74, S * .76, S * .14
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GREEN, outline=WHITE, width=int(S * .025))
    d.line([(cx - r * .45, cy + r * .02), (cx - r * .1, cy + r * .38), (cx + r * .5, cy - r * .35)],
           fill=WHITE, width=int(S * .035), joint="curve")
    return im

big = icon()
for n in (512, 256):
    big.resize((n, n), Image.LANCZOS).save(f"{OUT}/icon-{n}.png", optimize=True)

# 소셜 미리보기 1280x640
W, H = 1280, 640
sp = Image.new("RGB", (W, H), (245, 248, 253))
sp.paste(big.resize((360, 360), Image.LANCZOS), (110, 140), big.resize((360, 360), Image.LANCZOS))
d = ImageDraw.Draw(sp)
t = ImageFont.truetype(F, 92)
s = ImageFont.truetype(F, 38)
d.text((540, 230), "hwp-master", font=t, fill=DARK, anchor="ls", stroke_width=2, stroke_fill=DARK)
d.text((540, 310), "자료나 주제로 한컴에서 바로 편집하는", font=s, fill=(60, 70, 90), anchor="ls")
d.text((540, 365), "네이티브 HWPX 문서를 만드는 에이전트 스킬", font=s, fill=(60, 70, 90), anchor="ls")
d.text((540, 440), "rhwp 기반 · 검사기로 판정 · 같은 입력이면 같은 파일", font=ImageFont.truetype(F, 28), fill=(110, 120, 140), anchor="ls")
sp.save(f"{OUT}/social-preview.png", optimize=True)
