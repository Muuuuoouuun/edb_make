#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_problem_board_edb import run_problem_export
from user_settings import apply_to_env, load_user_settings


ROOT = Path("/Users/clmagi/Library/CloudStorage/GoogleDrive-seoulmentoss@gmail.com/내 드라이브/ai-proj/문제/수업 자료 샘플/고3 시험지")
SCIENCE_ROOT = ROOT / "4교시2_과학탐구"
IMAGE_ROOT = Path("/Users/clmagi/Downloads/문제 예씨")


SAMPLES: list[dict[str, Any]] = [
    {"label": "2025_korean", "path": ROOT / "국어영역_문제지_홀수형_2025학년도.pdf", "expected": 45},
    {"label": "2025_math", "path": ROOT / "수학영역_문제지_홀수형_2025학년도.pdf", "expected": 30},
    {"label": "2025_english", "path": ROOT / "영어영역_문제지_홀수형_2025학년도.pdf", "expected": 45},
    {"label": "2026_june_math", "path": ROOT / "26-6월 수학영역_문제지.pdf", "expected": 30},
    {"label": "physics1", "path": SCIENCE_ROOT / "01 물리학Ⅰ_문제지.pdf", "expected": 20},
    {"label": "chemistry1", "path": SCIENCE_ROOT / "02 화학Ⅰ_문제지.pdf", "expected": 20},
    {"label": "biology1", "path": SCIENCE_ROOT / "03 생명과학Ⅰ_문제지.pdf", "expected": 20},
    {"label": "earth1", "path": SCIENCE_ROOT / "04 지구과학Ⅰ_문제지.pdf", "expected": 20},
    {"label": "physics2", "path": SCIENCE_ROOT / "05 물리학Ⅱ_문제지.pdf", "expected": 20},
    {"label": "chemistry2", "path": SCIENCE_ROOT / "06 화학Ⅱ_문제지.pdf", "expected": 20},
    {"label": "biology2", "path": SCIENCE_ROOT / "07 생명과학Ⅱ_문제지.pdf", "expected": 20},
    {"label": "earth2", "path": SCIENCE_ROOT / "08 지구과학Ⅱ_문제지.pdf", "expected": 20},
    {"label": "image_english_grammar", "path": IMAGE_ROOT / "1 (1).webp", "expected": 5},
    {"label": "image_math_position", "path": IMAGE_ROOT / "img.jpg", "expected": 5},
    {"label": "image_english_midterm", "path": IMAGE_ROOT / "1.webp", "expected": 6},
    {"label": "image_math_lines", "path": IMAGE_ROOT / "img (1).jpg", "expected": 7},
]


def _collect_flags(session_path: Path) -> dict[str, Any]:
    if not session_path.exists():
        return {}
    try:
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    flag_counts: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for problem in session.get("problems") or []:
        for flag in problem.get("riskFlags") or problem.get("risk_flags") or []:
            flag_counts[str(flag)] = flag_counts.get(str(flag), 0) + 1
        status = str(problem.get("reviewStatus") or problem.get("review_status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {"risk_flag_counts": flag_counts, "review_status_counts": statuses}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _record_count_from_output(output_dir: Path, result: dict[str, Any]) -> int:
    placements = _load_json(output_dir / "placements.json")
    raw_count = placements.get("record_count")
    if isinstance(raw_count, int):
        return raw_count
    problems = placements.get("problems")
    if isinstance(problems, list):
        return len(problems)
    session = _load_json(output_dir / "ui_session.json")
    session_problems = session.get("problems")
    if isinstance(session_problems, list):
        return len(session_problems)
    return int(result.get("record_count") or 0)


def _row_from_result(sample: dict[str, Any], result: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    output_dir = Path(str(result.get("output_dir") or ""))
    session_metrics = _collect_flags(output_dir / "ui_session.json") if output_dir else {}
    record_count = _record_count_from_output(output_dir, result) if output_dir else int(result.get("record_count") or 0)
    expected = int(sample.get("expected") or 0)
    ai_summary = result.get("ai_summary") if isinstance(result.get("ai_summary"), dict) else {}
    ocr_summary = result.get("ocr_summary") if isinstance(result.get("ocr_summary"), dict) else {}
    return {
        "label": sample["label"],
        "path": str(sample["path"]),
        "expected_count": expected,
        "record_count": record_count,
        "count_delta": record_count - expected,
        "elapsed_ms": elapsed_ms,
        "timing_ms": result.get("timing_ms") or {},
        "ocr_summary": ocr_summary,
        "ai_summary": ai_summary,
        "output_dir": str(output_dir) if output_dir else "",
        **session_metrics,
    }


def main() -> int:
    apply_to_env(load_user_settings(Path(".app_runtime")), overwrite=False)
    out_root = Path("tmp/training_eval")
    out_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, sample in enumerate(SAMPLES, start=1):
        path = Path(sample["path"])
        started = time.perf_counter()
        print(f"[{index}/{len(SAMPLES)}] {sample['label']} -> {path.name}", flush=True)
        if not path.exists():
            rows.append({"label": sample["label"], "path": str(path), "error": "missing_file"})
            continue
        output_dir = out_root / f"{index:02d}_{sample['label']}"
        try:
            result = run_problem_export(
                path,
                output_dir=output_dir,
                subject_name="unknown",
                ocr="auto",
                pdf_dpi=160,
                detect_perspective=path.suffix.lower() not in {".pdf"},
                skip_deskew=True,
                skip_crop=False,
                max_dimension=2400,
                export_edb=False,
                sync_ui=False,
                record_mode="image-only",
                input_intent="multi-problem",
                ai_fallback_enabled=False,
            )
            elapsed_ms = int(round((time.perf_counter() - started) * 1000))
            row = _row_from_result(sample, result, elapsed_ms)
            print(
                f"  count={row['record_count']}/{row['expected_count']} delta={row['count_delta']} elapsed={elapsed_ms}ms ocr={row['ocr_summary'].get('resolved_backend')}",
                flush=True,
            )
            rows.append(row)
        except Exception as exc:
            elapsed_ms = int(round((time.perf_counter() - started) * 1000))
            rows.append({
                "label": sample["label"],
                "path": str(path),
                "expected_count": sample.get("expected"),
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
            })
            print(f"  ERROR after {elapsed_ms}ms: {exc}", flush=True)
    summary = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sample_count": len(rows),
        "ok_count": sum(1 for row in rows if not row.get("error")),
        "mismatch_count": sum(1 for row in rows if not row.get("error") and row.get("count_delta") != 0),
        "error_count": sum(1 for row in rows if row.get("error")),
        "rows": rows,
    }
    out_path = out_root / "summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("sample_count", "ok_count", "mismatch_count", "error_count")}, ensure_ascii=False, indent=2))
    print(f"summary={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
