#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import shutil
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


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
    samples_per_segment: int = 38,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for segment_index, segment in enumerate(segments):
        start = 1 if segment_index else 0
        for i in range(start, samples_per_segment + 1):
            points.append(cubic_point(*segment, i / samples_per_segment))
    return points


def path_length(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def draw_soft_path(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    *,
    width: int,
    stops: list[tuple[float, str]],
) -> None:
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


def draw_shadowed_path(
    base: Image.Image,
    points: list[tuple[int, int]],
    *,
    width: int,
    shadow_offset: tuple[int, int],
    shadow_blur: int,
    shadow_alpha: int,
    stops: list[tuple[float, str]],
) -> None:
    mask = Image.new("L", base.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    shifted = [(x + shadow_offset[0], y + shadow_offset[1]) for x, y in points]
    if len(shifted) > 1:
        mask_draw.line(shifted, fill=255, width=width, joint="curve")
        radius = width // 2
        for x, y in shifted:
            mask_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(shadow_blur))
    shadow = Image.new("RGBA", base.size, (16, 78, 61, 0))
    shadow.putalpha(mask.point(lambda value: int(value * (shadow_alpha / 255))))
    base.alpha_composite(shadow)

    draw_soft_path(ImageDraw.Draw(base), points, width=width, stops=stops)


def scaled_points(
    raw_points: list[tuple[float, float]],
    scale: int,
) -> list[tuple[int, int]]:
    return [(int(round(x * scale)), int(round(y * scale))) for x, y in raw_points]


def write_svg_files() -> None:
    mark_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 128" role="img" aria-label="ClassIn EDB brand mark">
  <defs>
    <linearGradient id="bg" x1="12" y1="10" x2="180" y2="118" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fbfff8"/>
      <stop offset=".58" stop-color="#eafff3"/>
      <stop offset="1" stop-color="#edf8ff"/>
    </linearGradient>
    <linearGradient id="flow" x1="24" y1="18" x2="168" y2="112" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#b8f76b"/>
      <stop offset=".52" stop-color="#44dcc7"/>
      <stop offset="1" stop-color="#78b8ff"/>
    </linearGradient>
  </defs>
  <rect x="8" y="18" width="176" height="92" rx="28" fill="url(#bg)" stroke="#d7f4e7" stroke-width="4"/>
  <g fill="#fbfff8" opacity=".82">
    <rect x="26" y="35" width="20" height="58" rx="8"/>
    <rect x="26" y="35" width="40" height="20" rx="8"/>
    <rect x="26" y="55" width="34" height="18" rx="7"/>
    <rect x="26" y="73" width="42" height="20" rx="8"/>
  </g>
  <g fill="none" stroke="#fbfff8" stroke-width="17" stroke-linecap="round" stroke-linejoin="round" opacity=".82">
    <path d="M76 43V85M76 43C111 43 111 85 76 85"/>
    <path d="M122 43V85M122 43C153 43 153 64 122 64M122 64C158 64 158 85 122 85"/>
  </g>
  <g fill="url(#flow)">
    <rect x="31" y="40" width="10" height="48" rx="5"/>
    <rect x="31" y="40" width="31" height="10" rx="5"/>
    <rect x="31" y="59" width="25" height="10" rx="5"/>
    <rect x="31" y="78" width="33" height="10" rx="5"/>
  </g>
  <g fill="none" stroke="url(#flow)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round">
    <path d="M76 43V85M76 43C111 43 111 85 76 85"/>
    <path d="M122 43V85M122 43C153 43 153 64 122 64M122 64C158 64 158 85 122 85"/>
  </g>
</svg>
"""
    app_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="ClassIn EDB app icon">
  <defs>
    <linearGradient id="bg" x1="150" y1="70" x2="880" y2="940" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fbfff8"/>
      <stop offset=".58" stop-color="#ebfff3"/>
      <stop offset="1" stop-color="#eef8ff"/>
    </linearGradient>
    <linearGradient id="flow" x1="250" y1="230" x2="800" y2="770" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#b8f76b"/>
      <stop offset=".52" stop-color="#44dcc7"/>
      <stop offset="1" stop-color="#78b8ff"/>
    </linearGradient>
    <filter id="softShadow" x="-10%" y="-10%" width="120%" height="125%">
      <feDropShadow dx="0" dy="24" stdDeviation="18" flood-color="#0f4e3d" flood-opacity=".18"/>
    </filter>
  </defs>
  <rect x="0" y="0" width="1024" height="1024" rx="232" fill="url(#bg)"/>
  <rect x="40" y="40" width="944" height="944" rx="205" fill="none" stroke="#d7f4e7" stroke-width="8"/>
  <g fill="#fbfff8" opacity=".9" filter="url(#softShadow)">
    <rect x="150" y="300" width="126" height="424" rx="54"/>
    <rect x="150" y="300" width="290" height="126" rx="54"/>
    <rect x="150" y="450" width="250" height="122" rx="52"/>
    <rect x="150" y="598" width="304" height="126" rx="54"/>
  </g>
  <g fill="none" stroke="#fbfff8" stroke-width="94" stroke-linecap="round" stroke-linejoin="round" opacity=".9" filter="url(#softShadow)">
    <path d="M464 358V666M464 358C700 358 700 666 464 666"/>
    <path d="M704 358V666M704 358C878 358 878 512 704 512M704 512C902 512 902 666 704 666"/>
  </g>
  <g fill="url(#flow)" filter="url(#softShadow)">
    <rect x="190" y="342" width="58" height="340" rx="29"/>
    <rect x="190" y="342" width="205" height="58" rx="29"/>
    <rect x="190" y="483" width="180" height="58" rx="29"/>
    <rect x="190" y="624" width="222" height="58" rx="29"/>
  </g>
  <g fill="none" stroke="url(#flow)" stroke-width="58" stroke-linecap="round" stroke-linejoin="round" filter="url(#softShadow)">
    <path d="M464 358V666M464 358C700 358 700 666 464 666"/>
    <path d="M704 358V666M704 358C878 358 878 512 704 512M704 512C902 512 902 666 704 666"/>
  </g>
</svg>
"""
    SVG_PATH.write_text(app_svg, encoding="utf-8")
    MARK_SVG_PATH.write_text(mark_svg, encoding="utf-8")


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

    letter_underlay = ImageDraw.Draw(image)
    letter_width = s(58)
    underlay_width = s(94)
    halo = (251, 255, 248, 220)
    d_segments = [((464, 358), (700, 358), (700, 666), (464, 666))]
    b_top_segments = [((704, 358), (878, 358), (878, 512), (704, 512))]
    b_bottom_segments = [((704, 512), (902, 512), (902, 666), (704, 666))]
    d_curve = scaled_points(sample_bezier_path(d_segments, 64), scale)
    b_top_curve = scaled_points(sample_bezier_path(b_top_segments, 48), scale)
    b_bottom_curve = scaled_points(sample_bezier_path(b_bottom_segments, 48), scale)
    e_halo_shapes = [
        (150, 300, 276, 724, 54),
        (150, 300, 440, 426, 54),
        (150, 450, 400, 572, 52),
        (150, 598, 454, 724, 54),
    ]
    for x1, y1, x2, y2, radius in e_halo_shapes:
        letter_underlay.rounded_rectangle((s(x1), s(y1), s(x2), s(y2)), radius=s(radius), fill=halo)
    for points in [
        [(s(464), s(358)), (s(464), s(666))],
        d_curve,
        [(s(704), s(358)), (s(704), s(666))],
        b_top_curve,
        b_bottom_curve,
    ]:
        letter_underlay.line(points, fill=halo, width=underlay_width, joint="curve")
        for x, y in points:
            r = underlay_width // 2
            letter_underlay.ellipse((x - r, y - r, x + r, y + r), fill=halo)

    letter_stops = [(0.0, "#9EF35B"), (0.46, "#35D8C6"), (1.0, "#73B7FF")]
    e_shapes = [
        (190, 342, 248, 682, 29, "#9EF35B"),
        (190, 342, 395, 400, 29, "#8FF166"),
        (190, 483, 370, 541, 29, "#67E99A"),
        (190, 624, 412, 682, 29, "#45DCC1"),
    ]
    for x1, y1, x2, y2, radius, color in e_shapes:
        letter_underlay.rounded_rectangle((s(x1), s(y1), s(x2), s(y2)), radius=s(radius), fill=color)
    draw_soft_path(letter_underlay, [(s(464), s(358)), (s(464), s(666))], width=letter_width, stops=letter_stops)
    draw_soft_path(letter_underlay, d_curve, width=letter_width, stops=letter_stops)
    draw_soft_path(letter_underlay, [(s(704), s(358)), (s(704), s(666))], width=letter_width, stops=letter_stops)
    draw_soft_path(letter_underlay, b_top_curve, width=letter_width, stops=letter_stops)
    draw_soft_path(letter_underlay, b_bottom_curve, width=letter_width, stops=letter_stops)

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
    write_svg_files()
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
