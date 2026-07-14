#!/usr/bin/env python3
"""Reproducible quality/latency benchmark for EDB S2, S3, and Upscayl.

The benchmark deliberately avoids cloud API calls. It evaluates:

1. Synthetic degraded exam crops with a known high-resolution reference.
2. An anonymized sample of existing Korean, math, and science problem crops.
3. A small Upscayl model pilot on representative real crops.

Only metrics and anonymous sample ids are written to the result directory.
Rendered comparison images stay in the temporary work directory unless
``--keep-images`` is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

import cv2  # type: ignore
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from build_problem_board_edb import (
    _build_transparent_reconstruction_image,
    _enhance_problem_crop,
    _extract_problem_cutout,
)


TARGET_WIDTH = 1600
UPSCAYL_DEFAULT_MODEL = "upscayl-standard-4x"
REAL_SAMPLE_SPECS = (
    (
        "korean",
        "K1",
        "7e4a0ffee49d6cce86fdee14d1abcb79abc85660_25수능_국어_7f0db7ddbd/problem_crops/problem_050_0f0e42ad.png",
    ),
    (
        "korean",
        "K2",
        "7e4a0ffee49d6cce86fdee14d1abcb79abc85660_25수능_국어_7f0db7ddbd/problem_crops/problem_095_66dc093e.png",
    ),
    (
        "math",
        "M1",
        "fc058765608cbee67f2e7f2344d37dbe3de186b9_수학영역_문제지_홀수형_2025학년도_0899a166a0/problem_crops/problem_005_49f10e2c.png",
    ),
    (
        "math",
        "M2",
        "fc058765608cbee67f2e7f2344d37dbe3de186b9_수학영역_문제지_홀수형_2025학년도_0899a166a0/problem_crops/problem_035_1abec005.png",
    ),
    (
        "science",
        "S1",
        "4510f6ec85cb5a6b6d4ff5932d1743a4dabbddc1_25수능_화학_2b70e9e4b4/problem_crops/problem_014_373a946d.png",
    ),
    (
        "science",
        "S2",
        "4510f6ec85cb5a6b6d4ff5932d1743a4dabbddc1_25수능_화학_2b70e9e4b4/problem_crops/problem_012_ec3623e5.png",
    ),
)


def _font(size: int, *, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/System/Library/Fonts/NewYork.ttf",
        ]
        if serif
        else [
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _synthetic_reference(kind: str) -> Image.Image:
    image = Image.new("RGB", (TARGET_WIDTH, 920), "white")
    draw = ImageDraw.Draw(image)
    sans = _font(43)
    small = _font(31)
    serif = _font(48, serif=True)
    draw.text((60, 45), "17. 다음 자료를 읽고 물음에 답하시오.", font=sans, fill="black")

    if kind == "text":
        lines = [
            "언어의 의미는 문맥과 사용 방식에 따라 달라질 수 있다.",
            "각 문장은 원문의 글자와 문장 부호를 정확히 보존해야 한다.",
            "① 첫 번째 설명   ② 두 번째 설명   ③ 세 번째 설명",
            "The quick brown fox jumps over the lazy dog. 2026",
        ]
        for index, line in enumerate(lines):
            draw.text((80, 150 + index * 115), line, font=sans if index < 3 else serif, fill="black")
        draw.rectangle((70, 125, 1510, 650), outline="black", width=3)
    elif kind == "math":
        draw.text((90, 165), "f(x) = (x² + 1)(3x² − 2x)", font=serif, fill="black")
        draw.text((90, 280), "∫₀¹ (x³ + 2x) dx = 5/4", font=serif, fill="black")
        draw.text((90, 395), "aₙ₊₁ = 2aₙ − 3,   Σᵏᵢ₌₁ i²", font=serif, fill="black")
        draw.line((90, 535, 650, 535), fill="black", width=3)
        draw.text((265, 545), "x + 1", font=serif, fill="black")
        draw.text((260, 475), "x² − 1", font=serif, fill="black")
        draw.text((90, 705), "① 1/36   ② 1/18   ③ 1/12   ④ 1/9   ⑤ 5/36", font=small, fill="black")
    elif kind == "table":
        left, top, right, bottom = 85, 150, 1515, 760
        rows, cols = 6, 5
        for row in range(rows + 1):
            y = top + round((bottom - top) * row / rows)
            draw.line((left, y, right, y), fill="black", width=2)
        for col in range(cols + 1):
            x = left + round((right - left) * col / cols)
            draw.line((x, top, x, bottom), fill="black", width=2)
        labels = ["원소", "X", "Y", "Z", "합계", "질량(g)", "8a−b", "8a+b", "9a+7b", "10a+b"]
        for index, label in enumerate(labels):
            row, col = divmod(index, cols)
            draw.text((left + col * 286 + 22, top + row * 102 + 26), label, font=small, fill="black")
    elif kind == "diagram":
        draw.line((180, 730, 1430, 730), fill="black", width=3)
        draw.line((250, 790, 250, 140), fill="black", width=3)
        points = [(250, 700), (470, 570), (720, 610), (980, 360), (1320, 235)]
        draw.line(points, fill="black", width=4)
        for index, (x, y) in enumerate(points):
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill="black")
            draw.text((x + 12, y - 45), chr(65 + index), font=small, fill="black")
        draw.arc((480, 170, 1080, 720), start=205, end=350, fill="black", width=3)
        draw.text((1150, 770), "시간 t", font=small, fill="black")
        draw.text((80, 120), "농도", font=small, fill="black")
    else:
        raise ValueError(f"unknown synthetic kind: {kind}")
    return image


def _degrade(image: Image.Image, width: int, *, jpeg_quality: int) -> Image.Image:
    height = max(1, round(image.height * width / image.width))
    degraded = image.resize((width, height), Image.Resampling.LANCZOS)
    degraded = degraded.filter(ImageFilter.GaussianBlur(radius=0.45 if width >= 800 else 0.7))
    with tempfile.NamedTemporaryFile(suffix=".jpg") as handle:
        degraded.save(handle.name, format="JPEG", quality=jpeg_quality)
        return Image.open(handle.name).convert("RGB")


def _render_s2(image: Image.Image) -> Image.Image:
    return _extract_problem_cutout(_enhance_problem_crop(image))


def _render_s3(image: Image.Image) -> Image.Image:
    return _build_transparent_reconstruction_image(image)


def _run_upscayl(
    image: Image.Image,
    *,
    binary: Path,
    models_dir: Path,
    model: str,
    work_dir: Path,
    stem: str,
) -> Image.Image:
    source_path = work_dir / f"{stem}-source.png"
    output_path = work_dir / f"{stem}-{model}.png"
    image.convert("RGBA" if "A" in image.getbands() else "RGB").save(source_path, format="PNG")
    command = [
        str(binary),
        "-i",
        str(source_path),
        "-o",
        str(output_path),
        "-m",
        str(models_dir),
        "-n",
        model,
        "-w",
        str(TARGET_WIDTH),
        "-f",
        "png",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode != 0 or not output_path.exists():
        detail = (completed.stderr or completed.stdout or "unknown Upscayl failure").strip()
        raise RuntimeError(f"Upscayl failed ({completed.returncode}): {detail[-1000:]}")
    with Image.open(output_path) as loaded:
        upscaled = loaded.convert("RGBA" if "A" in loaded.getbands() else "RGB")
    return _extract_problem_cutout(upscaled)


def _alpha(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    converted = image.convert("RGBA")
    alpha = converted.getchannel("A")
    if alpha.getextrema() == (255, 255):
        gray = converted.convert("L")
        alpha = gray.point(lambda value: 255 - value)
    if alpha.size != size:
        alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(alpha, dtype=np.float32) / 255.0


def _edge_f1(reference: np.ndarray, candidate: np.ndarray, tolerance_px: int = 2) -> float:
    ref_edge = cv2.Canny((reference * 255).astype(np.uint8), 40, 120) > 0
    cand_edge = cv2.Canny((candidate * 255).astype(np.uint8), 40, 120) > 0
    kernel_size = tolerance_px * 2 + 1
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    ref_dilated = cv2.dilate(ref_edge.astype(np.uint8), kernel) > 0
    cand_dilated = cv2.dilate(cand_edge.astype(np.uint8), kernel) > 0
    precision = float((cand_edge & ref_dilated).sum()) / max(1, int(cand_edge.sum()))
    recall = float((ref_edge & cand_dilated).sum()) / max(1, int(ref_edge.sum()))
    return 2.0 * precision * recall / max(1e-9, precision + recall)


def _metrics(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    reference_mask = reference >= 0.18
    candidate_mask = candidate >= 0.18
    intersection = int((reference_mask & candidate_mask).sum())
    union = int((reference_mask | candidate_mask).sum())
    ink_iou = intersection / max(1, union)
    alpha_similarity = 1.0 - float(np.mean(np.abs(reference - candidate)))
    edge_f1 = _edge_f1(reference, candidate)
    sharpness = float(cv2.Laplacian((candidate * 255).astype(np.uint8), cv2.CV_64F).var())
    fidelity_score = 100.0 * (0.40 * edge_f1 + 0.35 * ink_iou + 0.25 * alpha_similarity)
    return {
        "edge_f1": round(edge_f1, 5),
        "ink_iou": round(ink_iou, 5),
        "alpha_similarity": round(alpha_similarity, 5),
        "sharpness": round(sharpness, 3),
        "technical_fidelity_score": round(fidelity_score, 2),
        "foreground_ratio": round(float(candidate_mask.mean()), 6),
    }


def _timed(callable_: Callable[[], Image.Image]) -> tuple[Image.Image, float]:
    started = time.perf_counter()
    result = callable_()
    return result, time.perf_counter() - started


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _summarize(rows: list[dict[str, Any]], score_field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["method"])].append(row)
    result = []
    for method, items in grouped.items():
        result.append(
            {
                "method": method,
                "samples": len(items),
                "median_seconds": round(median(float(item["seconds"]) for item in items), 4),
                "mean_seconds": round(mean(float(item["seconds"]) for item in items), 4),
                f"mean_{score_field}": round(mean(float(item[score_field]) for item in items), 2),
                "mean_edge_f1": round(mean(float(item["edge_f1"]) for item in items), 4),
                "mean_ink_iou": round(mean(float(item["ink_iou"]) for item in items), 4),
            }
        )
    return sorted(result, key=lambda item: item["method"])


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    binary = args.upscayl_bin.expanduser().resolve()
    models_dir = args.models_dir.expanduser().resolve()
    if not binary.exists():
        raise FileNotFoundError(f"Upscayl binary not found: {binary}")
    if not (models_dir / f"{UPSCAYL_DEFAULT_MODEL}.bin").exists():
        raise FileNotFoundError(f"Upscayl models not found: {models_dir}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="edb-upscayl-benchmark-"))
    synthetic_rows: list[dict[str, Any]] = []
    real_rows: list[dict[str, Any]] = []
    model_rows: list[dict[str, Any]] = []

    try:
        methods: list[tuple[str, Callable[[Image.Image, str], Image.Image]]] = [
            ("stage2", lambda image, _stem: _render_s2(image)),
            ("stage3", lambda image, _stem: _render_s3(image)),
            (
                "upscayl-standard-4x",
                lambda image, stem: _run_upscayl(
                    image,
                    binary=binary,
                    models_dir=models_dir,
                    model=UPSCAYL_DEFAULT_MODEL,
                    work_dir=temporary,
                    stem=stem,
                ),
            ),
        ]

        for kind in ("text", "math", "table", "diagram"):
            reference_image = _synthetic_reference(kind)
            reference_cutout = _extract_problem_cutout(reference_image)
            reference = _alpha(reference_cutout, reference_cutout.size)
            for width, jpeg_quality in ((800, 58), (400, 38)):
                degraded = _degrade(reference_image, width, jpeg_quality=jpeg_quality)
                case_id = f"synthetic-{kind}-{width}px"
                for method, render in methods:
                    result, seconds = _timed(lambda r=render, i=degraded, c=case_id: r(i.copy(), c))
                    candidate = _alpha(result, reference_cutout.size)
                    row = {
                        "case_id": case_id,
                        "kind": kind,
                        "input_width": width,
                        "method": method,
                        "seconds": round(seconds, 4),
                        **_metrics(reference, candidate),
                    }
                    synthetic_rows.append(row)
                    if args.keep_images:
                        result.save(output_dir / f"{case_id}-{method}.png")

        available_real_samples = []
        for subject, sample_id, relative_path in REAL_SAMPLE_SPECS:
            path = PROJECT_ROOT / relative_path
            if path.exists():
                available_real_samples.append((subject, sample_id, path))

        for subject, sample_id, path in available_real_samples:
            with Image.open(path) as loaded:
                source = loaded.convert("RGBA" if "A" in loaded.getbands() else "RGB")
            baseline = _extract_problem_cutout(source)
            baseline_size = (
                TARGET_WIDTH,
                max(1, round(baseline.height * TARGET_WIDTH / max(1, baseline.width))),
            )
            reference = _alpha(baseline, baseline_size)
            for method, render in methods:
                result, seconds = _timed(lambda r=render, i=source, c=f"real-{sample_id}": r(i.copy(), c))
                candidate = _alpha(result, baseline_size)
                real_rows.append(
                    {
                        "sample_id": sample_id,
                        "subject": subject,
                        "input_width": source.width,
                        "input_height": source.height,
                        "method": method,
                        "seconds": round(seconds, 4),
                        **_metrics(reference, candidate),
                    }
                )
                if args.keep_images:
                    result.save(output_dir / f"real-{sample_id}-{method}.png")

        pilot_samples = available_real_samples[1::2]
        for subject, sample_id, path in pilot_samples:
            with Image.open(path) as loaded:
                source = loaded.convert("RGBA" if "A" in loaded.getbands() else "RGB")
            baseline = _extract_problem_cutout(source)
            baseline_size = (
                TARGET_WIDTH,
                max(1, round(baseline.height * TARGET_WIDTH / max(1, baseline.width))),
            )
            reference = _alpha(baseline, baseline_size)
            for model in ("upscayl-lite-4x", "upscayl-standard-4x", "ultrasharp-4x"):
                result, seconds = _timed(
                    lambda i=source, m=model, s=sample_id: _run_upscayl(
                        i.copy(),
                        binary=binary,
                        models_dir=models_dir,
                        model=m,
                        work_dir=temporary,
                        stem=f"pilot-{s}",
                    )
                )
                candidate = _alpha(result, baseline_size)
                model_rows.append(
                    {
                        "sample_id": sample_id,
                        "subject": subject,
                        "model": model,
                        "method": model,
                        "seconds": round(seconds, 4),
                        **_metrics(reference, candidate),
                    }
                )

        synthetic_summary = _summarize(synthetic_rows, "technical_fidelity_score")
        real_summary = _summarize(real_rows, "technical_fidelity_score")
        model_summary = _summarize(model_rows, "technical_fidelity_score")
        summary = {
            "as_of": "2026-07-14",
            "device": "Apple M4 (10-core GPU), macOS arm64",
            "target_width": TARGET_WIDTH,
            "methodology": {
                "synthetic_cases": len({row["case_id"] for row in synthetic_rows}),
                "real_samples": len({row["sample_id"] for row in real_rows}),
                "technical_fidelity_formula": "40% tolerant edge F1 + 35% ink IoU + 25% alpha similarity",
                "real_sample_caveat": "No high-resolution ground truth; score measures structural retention to the input, not subjective quality.",
                "cloud_api_calls": 0,
            },
            "synthetic_summary": synthetic_summary,
            "real_summary": real_summary,
            "model_summary": model_summary,
        }
        _write_csv(output_dir / "synthetic_results.csv", synthetic_rows)
        _write_csv(output_dir / "real_results.csv", real_rows)
        _write_csv(output_dir / "model_pilot_results.csv", model_rows)
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        if args.keep_images:
            retained = output_dir / "rendered"
            if retained.exists():
                shutil.rmtree(retained)
            shutil.copytree(temporary, retained)
        shutil.rmtree(temporary, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upscayl-bin",
        type=Path,
        default=Path("/tmp/upscayl-review/resources/mac/bin/upscayl-bin"),
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("/tmp/upscayl-review/resources/models"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "docs" / "upscayl_benchmark",
    )
    parser.add_argument("--keep-images", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run_benchmark(parse_args()), ensure_ascii=False, indent=2))
