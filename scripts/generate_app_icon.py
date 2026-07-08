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

    draw = ImageDraw.Draw(base)

    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    ]
    font = None
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, s(430))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "EDB", font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text(
        (s(495) - text_width // 2, s(516) - text_height // 2 - bbox[1] + s(12)),
        "EDB",
        fill="#101923",
        font=font,
    )

    def round_line(
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        fill: str,
        width: int,
    ) -> None:
        draw.line((*start, *end), fill=fill, width=width)
        radius = width // 2
        for x, y in (start, end):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

    wand_shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(wand_shadow)
    shadow_draw.line((s(660), s(780), s(892), s(548)), fill=(0, 0, 0, 42), width=s(54))
    wand_shadow = wand_shadow.filter(ImageFilter.GaussianBlur(s(14)))
    base.alpha_composite(wand_shadow)

    round_line((s(660), s(780)), (s(858), s(582)), fill="#101923", width=s(46))
    round_line((s(848), s(592)), (s(892), s(548)), fill="#22C9BD", width=s(46))

    for points in [
        [(914, 428), (925, 456), (953, 467), (925, 478), (914, 506), (903, 478), (875, 467), (903, 456)],
        [(959, 506), (967, 526), (987, 534), (967, 542), (959, 562), (951, 542), (931, 534), (951, 526)],
        [(922, 604), (931, 625), (952, 634), (931, 642), (922, 663), (914, 642), (893, 634), (914, 625)],
    ]:
        draw.polygon([(s(x), s(y)) for x, y in points], fill="#22C9BD")


def draw_icon(size: int = 1024, scale: int = 4) -> Image.Image:
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    def s(value: float) -> int:
        return int(round(value * scale))

    background_mask = rounded_mask((canvas_size, canvas_size), s(232))
    background = Image.new("RGBA", (canvas_size, canvas_size), "#FFFDF8")
    bg_draw = ImageDraw.Draw(background)
    for i in range(canvas_size):
        ratio = i / max(canvas_size - 1, 1)
        r, g, b = mix("#FFFDF8", "#F8F0E5", ratio)
        bg_draw.line((0, i, canvas_size, i), fill=(r, g, b, 255))
    image.paste(background, (0, 0), background_mask)

    draw_symbol(image, scale)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_svg_files() -> None:
    app_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="ClassIn EDB app icon">
  <defs>
    <linearGradient id="bg" x1="132" y1="76" x2="884" y2="948" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fffdf8"/>
      <stop offset="1" stop-color="#f8f0e5"/>
    </linearGradient>
    <filter id="wandShadow" x="-12%" y="-12%" width="124%" height="124%">
      <feDropShadow dx="0" dy="12" stdDeviation="10" flood-color="#000000" flood-opacity=".18"/>
    </filter>
  </defs>
  <rect width="1024" height="1024" rx="232" fill="url(#bg)"/>
  <text x="492" y="674" text-anchor="middle" font-family="Arial Black, Arial, Helvetica, sans-serif" font-size="428" font-weight="900" letter-spacing="12" fill="#101923">EDB</text>
  <g fill="none" stroke-linecap="round" filter="url(#wandShadow)">
    <path d="M660 780 858 582" stroke="#101923" stroke-width="46"/>
    <path d="M848 592 892 548" stroke="#22c9bd" stroke-width="46"/>
  </g>
  <g fill="#22c9bd">
    <path d="M914 428 925 456 953 467 925 478 914 506 903 478 875 467 903 456z"/>
    <path d="M959 506 967 526 987 534 967 542 959 562 951 542 931 534 951 526z"/>
    <path d="M922 604 931 625 952 634 931 642 922 663 914 642 893 634 914 625z"/>
  </g>
</svg>
"""
    mark_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 128" role="img" aria-label="ClassIn EDB brand mark">
  <text x="78" y="83" text-anchor="middle" font-family="Arial Black, Arial, Helvetica, sans-serif" font-size="63" font-weight="900" letter-spacing="1.5" fill="#101923">EDB</text>
  <g fill="none" stroke-linecap="round">
    <path d="M124 95 164 55" stroke="#101923" stroke-width="9"/>
    <path d="M160 59 171 48" stroke="#22c9bd" stroke-width="9"/>
  </g>
  <g fill="#22c9bd">
    <path d="M174 34 177 42 185 45 177 48 174 56 171 48 163 45 171 42z"/>
    <path d="M184 58 186 63 191 65 186 67 184 72 182 67 177 65 182 63z"/>
    <path d="M177 77 180 84 187 87 180 90 177 97 174 90 167 87 174 84z"/>
  </g>
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
