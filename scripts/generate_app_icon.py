#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = PROJECT_ROOT / "assets"
PNG_PATH = ASSET_DIR / "app_icon.png"
ICO_PATH = ASSET_DIR / "app_icon.ico"
ICNS_PATH = ASSET_DIR / "app_icon.icns"
ICONSET_DIR = ASSET_DIR / "app_icon.iconset"


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def layer_shadow(
    base: Image.Image,
    mask: Image.Image,
    offset: tuple[int, int],
    blur: int,
    color: tuple[int, int, int, int],
) -> None:
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_alpha = Image.new("L", base.size, 0)
    shadow_alpha.paste(mask, offset)
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(blur))
    shadow.putalpha(shadow_alpha.point(lambda value: int(value * (color[3] / 255))))
    tint = Image.new("RGBA", base.size, color[:3] + (0,))
    tint.putalpha(shadow.getchannel("A"))
    base.alpha_composite(tint)


def draw_icon(size: int = 1024, scale: int = 4) -> Image.Image:
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    def s(value: float) -> int:
        return int(round(value * scale))

    background_mask = rounded_mask((canvas_size, canvas_size), s(220))
    background = Image.new("RGBA", (canvas_size, canvas_size), "#111822")
    bg_draw = ImageDraw.Draw(background)
    for i in range(canvas_size):
        ratio = i / max(canvas_size - 1, 1)
        r = int(17 + ratio * 7)
        g = int(24 + ratio * 8)
        b = int(34 + ratio * 10)
        bg_draw.line((0, i, canvas_size, i), fill=(r, g, b, 255))
    image.paste(background, (0, 0), background_mask)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((s(66), s(66), s(958), s(958)), radius=s(186), outline=(255, 255, 255, 18), width=s(4))

    board_box = (s(238), s(194), s(786), s(784))
    board_mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(board_mask).rounded_rectangle(board_box, radius=s(56), fill=255)
    layer_shadow(image, board_mask, (s(0), s(22)), s(34), (0, 0, 0, 95))
    draw.rounded_rectangle(board_box, radius=s(56), fill="#F7F0E2")

    inner_box = (s(292), s(270), s(732), s(654))
    draw.rounded_rectangle(inner_box, radius=s(34), fill="#182531")

    teal = "#2DD4BF"
    chalk = "#F7F0E2"
    amber = "#F5B84B"
    muted = "#9AB5B4"

    draw.line((s(338), s(348), s(520), s(348)), fill=chalk, width=s(28))
    draw.line((s(338), s(428), s(566), s(428)), fill=(247, 240, 226, 210), width=s(22))
    draw.line((s(338), s(508), s(466), s(508)), fill=(247, 240, 226, 165), width=s(22))

    draw.rounded_rectangle((s(604), s(326), s(682), s(404)), radius=s(18), outline=teal, width=s(18))
    draw.rounded_rectangle((s(604), s(470), s(682), s(548)), radius=s(18), outline=amber, width=s(18))

    draw.line((s(326), s(704), s(682), s(704)), fill=muted, width=s(28))
    draw.line((s(654), s(646), s(720), s(704), s(654), s(762)), fill=teal, width=s(34), joint="curve")
    draw.ellipse((s(286), s(678), s(342), s(734)), fill=teal)

    shine = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shine_draw = ImageDraw.Draw(shine)
    shine_draw.polygon(
        [(s(176), s(84)), (s(546), s(84)), (s(232), s(454)), (s(84), s(454))],
        fill=(255, 255, 255, 18),
    )
    shine.putalpha(Image.composite(shine.getchannel("A"), Image.new("L", image.size, 0), background_mask))
    image.alpha_composite(shine)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_iconset(source: Image.Image) -> None:
    if ICONSET_DIR.exists():
        for child in ICONSET_DIR.iterdir():
            child.unlink()
    else:
        ICONSET_DIR.mkdir(parents=True)

    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for pixels, name in sizes:
        source.resize((pixels, pixels), Image.Resampling.LANCZOS).save(ICONSET_DIR / name)


def main() -> int:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon.save(PNG_PATH)
    icon.save(
        ICO_PATH,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    write_iconset(icon)
    if sys.platform == "darwin":
        subprocess.run(["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_PATH)], check=True)
        shutil.rmtree(ICONSET_DIR)
    else:
        print("Skipping .icns generation outside macOS.", file=sys.stderr)
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH}")
    print(f"Wrote {ICNS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
