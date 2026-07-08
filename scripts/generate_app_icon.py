#!/usr/bin/env python3
from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = PROJECT_ROOT / "assets"
PNG_PATH = ASSET_DIR / "app_icon.png"
ICO_PATH = ASSET_DIR / "app_icon.ico"
ICNS_PATH = ASSET_DIR / "app_icon.icns"
SVG_PATH = ASSET_DIR / "app_icon.svg"
MARK_SVG_PATH = ASSET_DIR / "brand_mark.svg"
ICONSET_DIR = ASSET_DIR / "app_icon.iconset"


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, ratio: float) -> tuple[int, int, int]:
    ar, ag, ab = hex_to_rgb(a)
    br, bg, bb = hex_to_rgb(b)
    ratio = max(0.0, min(1.0, ratio))
    return (
        int(round(ar + (br - ar) * ratio)),
        int(round(ag + (bg - ag) * ratio)),
        int(round(ab + (bb - ab) * ratio)),
    )


def gradient_color(stops: list[tuple[float, str]], ratio: float) -> tuple[int, int, int, int]:
    ratio = max(0.0, min(1.0, ratio))
    for index, (stop, color) in enumerate(stops):
        if ratio <= stop:
            if index == 0:
                return hex_to_rgb(color) + (255,)
            prev_stop, prev_color = stops[index - 1]
            local = (ratio - prev_stop) / max(stop - prev_stop, 0.001)
            return mix(prev_color, color, local) + (255,)
    return hex_to_rgb(stops[-1][1]) + (255,)


def cubic_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    u = 1 - t
    x = (u**3 * p0[0]) + (3 * u * u * t * p1[0]) + (3 * u * t * t * p2[0]) + (t**3 * p3[0])
    y = (u**3 * p0[1]) + (3 * u * u * t * p1[1]) + (3 * u * t * t * p2[1]) + (t**3 * p3[1])
    return x, y


def sample_bezier_path(
    segments: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]],
    samples_per_segment: int = 48,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for segment_index, segment in enumerate(segments):
        start = 1 if segment_index else 0
        for i in range(start, samples_per_segment + 1):
            points.append(cubic_point(*segment, i / samples_per_segment))
    return points


def path_length(points: list[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def draw_gradient_path(
    layer: Image.Image,
    points: list[tuple[int, int]],
    *,
    width: int,
    stops: list[tuple[float, str]],
) -> None:
    draw = ImageDraw.Draw(layer)
    total = path_length([(float(x), float(y)) for x, y in points])
    travelled = 0.0
    radius = width // 2
    for index, (a, b) in enumerate(zip(points, points[1:])):
        if index:
            travelled += math.hypot(a[0] - points[index - 1][0], a[1] - points[index - 1][1])
        color = gradient_color(stops, travelled / max(total, 1.0))
        draw.line((a[0], a[1], b[0], b[1]), fill=color, width=width)
        draw.ellipse((a[0] - radius, a[1] - radius, a[0] + radius, a[1] + radius), fill=color)
    if points:
        color = gradient_color(stops, 1.0)
        x, y = points[-1]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def draw_shadowed_gradient_path(
    base: Image.Image,
    points: list[tuple[int, int]],
    *,
    width: int,
    shadow_offset: tuple[int, int],
    shadow_blur: int,
    shadow_alpha: int,
    stops: list[tuple[float, str]],
) -> None:
    shadow_mask = Image.new("L", base.size, 0)
    shifted = [(x + shadow_offset[0], y + shadow_offset[1]) for x, y in points]
    shadow_draw = ImageDraw.Draw(shadow_mask)
    if len(shifted) > 1:
        shadow_draw.line(shifted, fill=255, width=width, joint="curve")
        radius = width // 2
        for x, y in shifted:
            shadow_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))
    shadow = Image.new("RGBA", base.size, (17, 78, 62, 0))
    shadow.putalpha(shadow_mask.point(lambda value: int(value * (shadow_alpha / 255))))
    base.alpha_composite(shadow)
    draw_gradient_path(base, points, width=width, stops=stops)


def scaled_points(raw_points: list[tuple[float, float]], scale: int) -> list[tuple[int, int]]:
    return [(int(round(x * scale)), int(round(y * scale))) for x, y in raw_points]


def draw_symbol(base: Image.Image, scale: int) -> None:
    def s(value: float) -> int:
        return int(round(value * scale))

    arc_stops = [(0.0, "#A8F05B"), (0.48, "#42D9C6"), (1.0, "#6FB6FA")]
    top_arc = sample_bezier_path(
        [
            ((322, 516), (282, 318), (438, 214), (570, 250)),
            ((570, 250), (748, 300), (812, 410), (768, 580)),
        ],
        60,
    )
    lower_arc = sample_bezier_path(
        [
            ((760, 586), (672, 782), (450, 802), (323, 707)),
        ],
        74,
    )
    draw_shadowed_gradient_path(
        base,
        scaled_points(top_arc, scale),
        width=s(88),
        shadow_offset=(0, s(24)),
        shadow_blur=s(22),
        shadow_alpha=34,
        stops=arc_stops,
    )
    draw_shadowed_gradient_path(
        base,
        scaled_points(lower_arc, scale),
        width=s(88),
        shadow_offset=(0, s(24)),
        shadow_blur=s(22),
        shadow_alpha=34,
        stops=arc_stops,
    )

    draw = ImageDraw.Draw(base)

    wand_shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(wand_shadow)
    shadow_draw.line((s(664), s(498), s(775), s(609)), fill=(21, 85, 87, 55), width=s(78))
    shadow_draw.line((s(773), s(589), s(678), s(684)), fill=(21, 85, 87, 48), width=s(78))
    wand_shadow = wand_shadow.filter(ImageFilter.GaussianBlur(s(14)))
    base.alpha_composite(wand_shadow)

    draw.line((s(672), s(502), s(779), s(609)), fill="#62DDCC", width=s(78))
    draw.line((s(774), s(584), s(678), s(680)), fill="#68C7E8", width=s(78))
    radius = s(39)
    draw.ellipse((s(676) - radius, s(669) - radius, s(676) + radius, s(669) + radius), fill="#70B4F9")

    card_shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(card_shadow)
    shadow_draw.rounded_rectangle((s(230), s(365), s(794), s(650)), radius=s(70), fill=(20, 64, 67, 38))
    card_shadow = card_shadow.filter(ImageFilter.GaussianBlur(s(18)))
    base.alpha_composite(card_shadow)

    draw.rounded_rectangle(
        (s(228), s(356), s(796), s(640)),
        radius=s(70),
        fill=(251, 255, 248, 214),
        outline="#D7F4E7",
        width=s(5),
    )

    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    ]
    font = None
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, s(214))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "EDB", font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text(
        (s(512) - text_width // 2, s(512) - text_height // 2 - bbox[1] + s(10)),
        "EDB",
        fill="#143A43",
        font=font,
    )


def draw_icon(size: int = 1024, scale: int = 4) -> Image.Image:
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    def s(value: float) -> int:
        return int(round(value * scale))

    background_mask = rounded_mask((canvas_size, canvas_size), s(232))
    background = Image.new("RGBA", (canvas_size, canvas_size), "#FBFFF8")
    bg_draw = ImageDraw.Draw(background)
    for i in range(canvas_size):
        ratio = i / max(canvas_size - 1, 1)
        r, g, b = mix("#FBFFF8", "#EEF8FF", ratio)
        bg_draw.line((0, i, canvas_size, i), fill=(r, g, b, 255))
    image.paste(background, (0, 0), background_mask)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((s(40), s(40), s(984), s(984)), radius=s(205), outline="#D7F4E7", width=s(8))
    draw_symbol(image, scale)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_svg_files() -> None:
    app_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="ClassIn EDB app icon">
  <defs>
    <linearGradient id="bg" x1="120" y1="60" x2="900" y2="960" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fbfff8"/>
      <stop offset="1" stop-color="#eef8ff"/>
    </linearGradient>
    <linearGradient id="flow" x1="280" y1="210" x2="812" y2="800" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#a8f05b"/>
      <stop offset=".48" stop-color="#42d9c6"/>
      <stop offset="1" stop-color="#6fb6fa"/>
    </linearGradient>
    <filter id="softShadow" x="-12%" y="-12%" width="124%" height="130%">
      <feDropShadow dx="0" dy="24" stdDeviation="18" flood-color="#114e3e" flood-opacity=".16"/>
    </filter>
  </defs>
  <rect width="1024" height="1024" rx="232" fill="url(#bg)"/>
  <rect x="40" y="40" width="944" height="944" rx="205" fill="none" stroke="#d7f4e7" stroke-width="8"/>
  <g fill="none" stroke="url(#flow)" stroke-width="88" stroke-linecap="round" filter="url(#softShadow)">
    <path d="M322 516C282 318 438 214 570 250C748 300 812 410 768 580"/>
    <path d="M760 586C672 782 450 802 323 707"/>
  </g>
  <g fill="none" stroke-linecap="round" stroke-width="78" filter="url(#softShadow)">
    <path d="M672 502 779 609" stroke="#62ddcc"/>
    <path d="M774 584 678 680" stroke="#68c7e8"/>
  </g>
  <circle cx="676" cy="669" r="39" fill="#70b4f9"/>
  <rect x="228" y="356" width="568" height="284" rx="70" fill="#fbfff8" fill-opacity=".84" stroke="#d7f4e7" stroke-width="5" filter="url(#softShadow)"/>
  <text x="512" y="586" text-anchor="middle" font-family="Inter, Arial, Helvetica, sans-serif" font-size="220" font-weight="800" letter-spacing="8" fill="#143a43">EDB</text>
</svg>
"""
    mark_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 128" role="img" aria-label="ClassIn EDB brand mark">
  <defs>
    <linearGradient id="flow" x1="46" y1="16" x2="154" y2="112" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#a8f05b"/>
      <stop offset=".48" stop-color="#42d9c6"/>
      <stop offset="1" stop-color="#6fb6fa"/>
    </linearGradient>
  </defs>
  <g fill="none" stroke="url(#flow)" stroke-width="14" stroke-linecap="round">
    <path d="M59 69C52 41 77 24 102 31C128 38 138 57 130 80"/>
    <path d="M129 79C113 108 79 109 59 91"/>
  </g>
  <g fill="none" stroke-linecap="round" stroke-width="13">
    <path d="M125 61 143 79" stroke="#62ddcc"/>
    <path d="M143 75 127 91" stroke="#68c7e8"/>
  </g>
  <circle cx="126" cy="90" r="7" fill="#70b4f9"/>
  <rect x="39" y="41" width="112" height="55" rx="15" fill="#fbfff8" fill-opacity=".86" stroke="#d7f4e7" stroke-width="1.5"/>
  <text x="96" y="78" text-anchor="middle" font-family="Inter, Arial, Helvetica, sans-serif" font-size="39" font-weight="800" letter-spacing="1.5" fill="#143a43">EDB</text>
</svg>
"""
    SVG_PATH.write_text(app_svg, encoding="utf-8")
    MARK_SVG_PATH.write_text(mark_svg, encoding="utf-8")


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
    write_svg_files()
    if PNG_PATH.exists():
        icon = Image.open(PNG_PATH).convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
    else:
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
    print(f"Wrote {SVG_PATH}")
    print(f"Wrote {MARK_SVG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
