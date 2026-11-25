"""Build test geometry and its independent answer key in a temporary directory."""
import json
from PIL import Image, ImageDraw


def build(root):
    boxes = [
        (50, 28, 415, 82), (49, 174, 435, 383), (49, 431, 439, 685),
        (505, 166, 905, 391), (510, 439, 901, 699),
        (51, 771, 904, 998), (52, 1127, 437, 1183),
    ]
    truth = [[x, y, r - x + 1, b - y + 1] for x, y, r, b in boxes]
    for tag in ("lined", "plain"):
        folder = root / tag
        folder.mkdir()
        page = Image.new("RGB", (960, 1200), "white")
        draw = ImageDraw.Draw(page)
        if tag == "lined":
            for y in (121, 1080):
                draw.rectangle((30, y, 930, y + 2), fill="#566960")
        for i, (x, y, r, b) in enumerate(boxes, 1):
            draw.rectangle((x, y, r, b), outline="#617e70", width=3)
            draw.text((x + 12, y + 12), str(i), fill="#253b42")
            if b - y > 100:
                draw.rectangle((x + 24, y + 76, x + 24 + 13*i, y + 80 + 6*i),
                               fill=(42 + 12*i, 100 + i, 143 - 5*i))
        page.save(folder / "분수 연습.v2.png")
        (folder / "truth.json").write_text(json.dumps(truth), encoding="utf8")

    for name, background, ink in [("four", "white", "#555555"), ("alpha", (0, 0, 0, 0), "white")]:
        image = Image.new("RGBA", (120, 100), background)
        draw = ImageDraw.Draw(image)
        for x, y in [(12, 14), (72, 9), (12, 49), (72, 45)]:
            draw.rectangle((x, y, x + 33, y + 17), fill=ink)
        image.save(root / (name + ".png"))
    Image.new("1", (4096, 4096), 1).save(root / "oversize.png")
