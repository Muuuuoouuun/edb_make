#!/usr/bin/env python3
"""Verify the app's OpenCV API surface and deterministic page-image operations."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import statistics
import time


def used_symbols(root: Path) -> set[str]:
    result = set()
    for path in [*root.glob("*.py"), *(root / "scripts").rglob("*.py")]:
        if path.name.startswith("test"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "cv2":
                result.add(node.attr)
    return result


def page_probe() -> dict:
    import cv2
    import numpy as np

    previous_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    image = np.full((1700, 1200, 3), 255, dtype=np.uint8)
    for y in range(30, 1650, 32):
        for x in range(35, 1150, 25):
            image[y:y + 11, x:x + 13] = [20, 40, 90]
    elapsed = []
    try:
        for _ in range(8):
            started = time.perf_counter()
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3)))
            count, _labels, stats, _centers = cv2.connectedComponentsWithStats(closed, 8)
            edges = cv2.Canny(gray, 50, 150)
            elapsed.append((time.perf_counter() - started) * 1000)
    finally:
        cv2.setNumThreads(previous_threads)
    return {
        "version": cv2.__version__, "componentCount": int(count),
        "medianMs": statistics.median(elapsed[1:]),
        "hashes": {name: hashlib.sha256(array.tobytes()).hexdigest() for name, array in (
            ("blur", blur), ("binary", binary), ("closed", closed), ("stats", stats), ("edges", edges),
        )},
    }


def validate_image_profile(root: Path) -> dict:
    import cv2

    profile_path = root / "release/opencv-image-profile.json"
    expected = json.loads(profile_path.read_text())
    distribution = importlib.metadata.distribution("opencv-python-headless")
    raw = distribution.read_text("edb-build-profile.json")
    if not raw:
        raise ValueError("Image-only OpenCV build receipt is missing")
    receipt = json.loads(raw)
    if receipt.get("profile") != expected or distribution.version != expected["version"]:
        raise ValueError("Installed OpenCV build profile/version mismatch")
    for field, path in (
        ("profileSha256", profile_path),
        ("toolchainLockSha256", root / "requirements-opencv-build.lock"),
        ("builderSha256", root / "scripts/build_minimal_opencv.py"),
    ):
        if receipt.get(field) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"OpenCV receipt input mismatch: {field}")
    built = re.search(r"^\s*To be built:\s*(.+)$", cv2.getBuildInformation(), re.MULTILINE)
    if not built or set(built.group(1).split()) != set(expected["modules"]):
        raise ValueError("OpenCV contains an unexpected native module set")
    missing = sorted(name for name in used_symbols(root) if not hasattr(cv2, name))
    if missing:
        raise ValueError(f"App requires missing OpenCV symbols: {missing}")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-profile", action="store_true")
    parser.add_argument("--reference-out", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args(argv)
    if args.require_profile:
        validate_image_profile(args.root)
    result = page_probe()
    if args.compare:
        reference = json.loads(args.compare.read_text())
        if result["hashes"] != reference["hashes"] or result["componentCount"] != reference["componentCount"]:
            raise ValueError("Image-only OpenCV page output differs from the full wheel")
        result["referenceMedianMs"] = reference["medianMs"]
        result["timingRatio"] = result["medianMs"] / reference["medianMs"]
    if args.reference_out:
        args.reference_out.parent.mkdir(parents=True, exist_ok=True)
        args.reference_out.write_text(json.dumps(result, indent=2) + "\n")
    print("[opencv-image] " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
