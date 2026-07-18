#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = PROJECT_ROOT / "assets"
UI_DIR = PROJECT_ROOT / "ui_prototype"
SOURCE_PATH = ASSET_DIR / "app_icon_source.png"
FAVICON_PATH = UI_DIR / "favicon.png"
PNG_PATH = ASSET_DIR / "app_icon.png"
ICO_PATH = ASSET_DIR / "app_icon.ico"
ICNS_PATH = ASSET_DIR / "app_icon.icns"
ICONSET_DIR = ASSET_DIR / "app_icon.iconset"


def load_source_icon(size: int = 1024) -> Image.Image:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing generated source icon: {SOURCE_PATH}")
    icon = Image.open(SOURCE_PATH).convert("RGBA")
    if icon.size != (size, size):
        icon = icon.resize((size, size), Image.Resampling.LANCZOS)
    return icon


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


def write_icns(source: Image.Image) -> None:
    try:
        source.save(
            ICNS_PATH,
            format="ICNS",
            sizes=[(16, 16), (32, 32), (128, 128), (256, 256), (512, 512), (1024, 1024)],
        )
    except Exception as exc:
        print(f"Skipping .icns generation: {exc}", file=sys.stderr)


def main() -> int:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    UI_DIR.mkdir(parents=True, exist_ok=True)
    icon = load_source_icon()
    icon.save(PNG_PATH)
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(FAVICON_PATH)
    icon.save(
        ICO_PATH,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    write_iconset(icon)
    write_icns(icon)
    shutil.rmtree(ICONSET_DIR, ignore_errors=True)
    print(f"Wrote {FAVICON_PATH}")
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH}")
    print(f"Wrote {ICNS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
