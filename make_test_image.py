#!/usr/bin/env python3
"""
실제 수능/모의고사 스타일의 물리 문제 이미지를 생성합니다.
사용자가 보여준 스크린샷의 문제를 A4 스캔본처럼 재현합니다.
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

FONT_PATH = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"
IMG_W, IMG_H = 900, 1280  # A4 비율, 200 DPI 기준
BG = (255, 255, 255)
FG = (15, 15, 15)

def font(size):
    return ImageFont.truetype(FONT_PATH, size)

def wrap_text(draw, text, x, y, max_w, fnt, fill=FG, line_h=None):
    """단어 단위 줄바꿈."""
    words = text.split()
    line = ""
    line_height = line_h or (fnt.size + 6)
    for word in words:
        test = line + word + " "
        w = draw.textlength(test, font=fnt)
        if w > max_w and line:
            draw.text((x, y), line.rstrip(), font=fnt, fill=fill)
            y += line_height
            line = word + " "
        else:
            line = test
    if line.strip():
        draw.text((x, y), line.rstrip(), font=fnt, fill=fill)
        y += line_height
    return y


img = Image.new("RGB", (IMG_W, IMG_H), BG)
draw = ImageDraw.Draw(img)

# ── 문제 번호 + 별점 ──────────────────────────────────
draw.rounded_rectangle((40, 36, IMG_W - 40, 90), radius=10, fill=(240, 240, 240))
draw.text((60, 44), "1.", font=font(28), fill=FG)
draw.text((100, 46), "★★★☆☆", font=font(22), fill=(180, 140, 0))
draw.text((220, 46), "물리학Ⅱ", font=font(20), fill=(60, 100, 200))
draw.text((340, 46), "특수 상대성 이론", font=font(20), fill=(180, 60, 60))

# ── 지문 본문 ─────────────────────────────────────────
y = 110
body_x, body_w = 60, IMG_W - 100

body = (
    "그림과 같이 관찰자 A에 대해 관찰자 B, C가 탄 우주선이 각각 속력"
    " 0.7c, 0.9c 로 우주 정거장 P, Q 를 잇는 직선과 나란하게 등속도 운동을 한다."
    " A 의 관성계에서, 정지해 있는 P 와 Q 사이의 거리는 d 이고 B, C 가 탄"
    " 우주선의 길이는 같다. C 의 관성계에서, C 가 탄 우주선의 길이는 L 이다."
)
y = wrap_text(draw, body, body_x, y, body_w, font(21), line_h=34)
y += 10

question = (
    "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?"
    " (단, c 는 빛의 속력이다.)"
)
y = wrap_text(draw, question, body_x, y, body_w, font(21), line_h=34)
y += 24

# ── <보기> 박스 ──────────────────────────────────────
draw.rectangle((60, y, IMG_W - 60, y + 4), fill=(100, 100, 100))
y += 10
draw.text((body_x, y), "< 보 기 >", font=font(20), fill=FG)
y += 32

choices = [
    "ㄱ. B 의 관성계에서, B 가 탄 우주선의 길이는 L 보다 크다.",
    "ㄴ. C 의 관성계에서, Q 가 C 를 지나는 순간부터 P 가 C 를 지나는 순간까지",
    "    걸리는 시간은 d / 0.9c 보다 작다.",
    "ㄷ. P 와 Q 사이의 거리는 B 의 관성계에서가 C 의 관성계에서보다 크다.",
]
for line in choices:
    draw.text((body_x + 10, y), line, font=font(21), fill=FG)
    y += 34

draw.rectangle((60, y + 6, IMG_W - 60, y + 10), fill=(100, 100, 100))
y += 28

# ── 선지 ① ~ ⑤ ──────────────────────────────────────
y += 10
options = [
    "① ㄱ",
    "② ㄴ",
    "③ ㄷ",
    "④ ㄱ, ㄴ",
    "⑤ ㄴ, ㄷ",
]
ox = body_x
col_w = (IMG_W - 120) // 5
for i, opt in enumerate(options):
    draw.text((ox + i * col_w, y), opt, font=font(21), fill=FG)
y += 50

# ── 물리 다이어그램 ────────────────────────────────────
diag_top = y + 20
diag_h = 200
diag_left, diag_right = 80, IMG_W - 80

# 배경
draw.rectangle((diag_left, diag_top, diag_right, diag_top + diag_h), outline=FG, width=2)

mid_y = diag_top + diag_h // 2

# 관찰자 A (위)
a_x = (diag_left + diag_right) // 2
draw.text((a_x - 10, diag_top + 8), "A", font=font(18), fill=FG)
draw.line([(a_x, diag_top + 28), (a_x, mid_y - 20)], fill=FG, width=2)

# P, Q 정거장 마커
p_x, q_x = diag_left + 60, diag_right - 60
for lx, label in [(p_x, "P"), (q_x, "Q")]:
    draw.rectangle((lx - 8, mid_y - 8, lx + 8, mid_y + 8), outline=FG, width=2)
    draw.text((lx - 5, mid_y - 6), label, font=font(14), fill=FG)
draw.line([(p_x + 8, mid_y), (q_x - 8, mid_y)], fill=(180, 180, 180), width=1)
draw.text(((p_x + q_x) // 2 - 8, mid_y + 12), "d", font=font(16), fill=FG)

# 우주선 B (0.7c)
bship_y = mid_y - 45
draw.rounded_rectangle((p_x + 20, bship_y - 12, p_x + 90, bship_y + 12), radius=6, fill=(80, 120, 200), outline=FG)
draw.text((p_x + 93, bship_y - 8), "0.7c →", font=font(14), fill=FG)
draw.text((p_x + 30, bship_y - 8), "B", font=font(14), fill=(255, 255, 255))

# 우주선 C (0.9c)
cship_y = mid_y + 30
draw.rounded_rectangle((p_x + 20, cship_y - 12, p_x + 90, cship_y + 12), radius=6, fill=(200, 80, 80), outline=FG)
draw.text((p_x + 93, cship_y - 8), "0.9c →", font=font(14), fill=FG)
draw.text((p_x + 30, cship_y - 8), "C", font=font(14), fill=(255, 255, 255))

# 답 선지 번호 열
y_after = diag_top + diag_h + 40
answer_opts = ["① ㄱ", "② ㄴ", "③ ㄱ, ㄷ", "④ ㄴ, ㄷ", "⑤ ㄱ, ㄴ, ㄷ"]
for i, opt in enumerate(answer_opts):
    draw.text((body_x + i * col_w, y_after), opt, font=font(21), fill=FG)

out = Path("/home/user/edb_make/test_physics_problem.png")
img.save(out, dpi=(200, 200))
print(f"Saved: {out}  ({IMG_W}x{IMG_H}px)")
