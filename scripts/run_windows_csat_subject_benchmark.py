#!/usr/bin/env python3
"""Strict Windows benchmark for every KICE CSAT subject and both import modes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_quality_corpus import (  # noqa: E402
    CorpusError,
    Observation,
    extract_observation,
)
from scripts.run_windows_exam_benchmark import (  # noqa: E402
    CaseScore as RecognitionScore,
    WindowsBenchmarkError,
    score_observation,
)


@dataclass(frozen=True)
class PipelineResult:
    mode: str
    output_dir: str
    elapsed_ms: float
    exit_code: int
    session: Mapping[str, Any] | None
    observation: Observation | None
    error: str | None


@dataclass(frozen=True)
class SubjectCaseScore:
    case_id: str
    year: int
    subject: str
    subject_display_name: str
    subject_area: str
    subject_area_display_name: str
    score: float
    recognition_score: float
    question_recall: float
    question_precision: float
    passage_recall: float
    passage_precision: float
    artifact_valid_rate: float
    manual_review_rate: float
    preflight_issue_count: int
    input_page_recall: float
    whole_page_recall: float
    source_resolution_valid_rate: float
    problem_resolution_valid_rate: float
    whole_resolution_valid_rate: float
    completion_rate: float
    problem_processing_ms: float
    problem_processing_ms_max: float
    whole_processing_ms: float
    whole_processing_ms_max: float
    time_factor: float
    within_time_budget: bool
    expected_question_count: int
    detected_question_count: int
    expected_page_count: int
    problem_output_dir: str
    whole_output_dir: str
    dimension_failures: tuple[str, ...]
    failures: tuple[str, ...]


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WindowsBenchmarkError(f"could not read JSON {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WindowsBenchmarkError(f"{label} must be a JSON object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise WindowsBenchmarkError(f"{label} must be a JSON array")
    return value


def _bounded_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 1.0
    return max(0.0, min(1.0, numerator / denominator))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_from_session(raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.startswith("file:"):
        parsed = urlparse(value)
        if parsed.scheme != "file":
            return None
        path = Path(url2pathname(unquote(parsed.path)))
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            path = Path(f"//{parsed.netloc}{path}")
        return path
    if "://" in value:
        return None
    return Path(value)


def _image_dimensions(raw: Any) -> tuple[int, int] | None:
    path = _path_from_session(raw)
    if path is None:
        return None
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (ImportError, OSError, ValueError):
        return None


def _field(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _positive_file(raw: Any) -> bool:
    path = _path_from_session(raw)
    if path is None:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _empty_observation(processing_ms: float) -> Observation:
    return Observation(
        question_numbers=(),
        passage_ranges=(),
        preflight_issue_count=1,
        manual_review_count=0,
        review_population=0,
        processing_ms=processing_ms,
        problem_signatures=(),
    )


def _validate_case(raw_case: Any, index: int) -> Mapping[str, Any]:
    case = _mapping(raw_case, f"catalog.cases[{index}]")
    required = {
        "case_id",
        "year",
        "subject",
        "subject_display_name",
        "subject_area",
        "subject_area_display_name",
        "pipeline_subject",
        "level",
        "local_path",
        "sha256",
        "processing_ms_max",
        "whole_processing_ms_max",
        "resolution_gate",
        "expected",
    }
    missing = sorted(required - set(case))
    if missing:
        raise WindowsBenchmarkError(
            f"catalog.cases[{index}] is missing fields: {', '.join(missing)}"
        )
    case_id = str(case["case_id"])
    if not case_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in case_id
    ):
        raise WindowsBenchmarkError(f"unsafe case id {case_id!r}")
    source_path = Path(str(case["local_path"])).expanduser().resolve()
    if not source_path.is_file():
        raise WindowsBenchmarkError(f"source PDF is missing for {case_id}: {source_path}")
    if _sha256_file(source_path) != str(case["sha256"]).lower():
        raise WindowsBenchmarkError(f"source digest mismatch for {case_id}")
    expected = _mapping(case["expected"], f"{case_id}.expected")
    questions = _sequence(expected.get("question_numbers"), f"{case_id}.question_numbers")
    _sequence(expected.get("passage_ranges"), f"{case_id}.passage_ranges")
    if not questions or int(expected.get("source_page_count") or 0) <= 0:
        raise WindowsBenchmarkError(f"{case_id} has incomplete expected labels")
    return case


def run_pipeline(
    case: Mapping[str, Any],
    *,
    output_dir: Path,
    python_executable: str,
    ocr: str,
    mode: str,
) -> PipelineResult:
    output_dir.mkdir(parents=True, exist_ok=False)
    command = [
        python_executable,
        str(PROJECT_ROOT / "build_problem_board_edb.py"),
        str(Path(str(case["local_path"])).resolve()),
        "--output-dir",
        str(output_dir),
        "--subject",
        str(case.get("pipeline_subject") or case["subject"]),
        "--ocr",
        ocr,
        "--input-intent",
        "page-as-is" if mode == "whole" else "auto",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    (output_dir / "pipeline.log").write_text(
        completed.stdout or "",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout or "").splitlines()[-20:])
        return PipelineResult(
            mode=mode,
            output_dir=str(output_dir.resolve()),
            elapsed_ms=elapsed_ms,
            exit_code=completed.returncode,
            session=None,
            observation=None,
            error=f"pipeline exit {completed.returncode}: {tail}",
        )
    session_path = output_dir / "ui_session.json"
    if not session_path.is_file():
        return PipelineResult(
            mode=mode,
            output_dir=str(output_dir.resolve()),
            elapsed_ms=elapsed_ms,
            exit_code=0,
            session=None,
            observation=None,
            error="pipeline did not create ui_session.json",
        )
    try:
        session = _mapping(_load_json(session_path), f"{case['case_id']}.{mode}.session")
        observation = (
            extract_observation(
                session,
                str(case["case_id"]),
                processing_ms_override=elapsed_ms,
            )
            if mode == "problem"
            else None
        )
    except (CorpusError, WindowsBenchmarkError) as exc:
        return PipelineResult(
            mode=mode,
            output_dir=str(output_dir.resolve()),
            elapsed_ms=elapsed_ms,
            exit_code=0,
            session=None,
            observation=None,
            error=str(exc),
        )
    return PipelineResult(
        mode=mode,
        output_dir=str(output_dir.resolve()),
        elapsed_ms=elapsed_ms,
        exit_code=0,
        session=session,
        observation=observation,
        error=None,
    )


def _source_metrics(
    session: Mapping[str, Any] | None,
    *,
    expected_pages: int,
    minimum_width: int,
    minimum_height: int,
) -> tuple[float, float, bool]:
    if session is None:
        return 0.0, 0.0, False
    pages = _field(session, "pages")
    rendered = _field(session, "rendered_page_paths", "renderedPagePaths")
    page_list = pages if isinstance(pages, list) else []
    rendered_list = rendered if isinstance(rendered, list) else []
    source_count = int(_field(session, "source_page_count", "sourcePageCount") or 0)
    count = min(source_count, len(page_list), len(rendered_list))
    input_recall = _bounded_ratio(count, expected_pages)
    valid = 0
    for raw_path in rendered_list[:expected_pages]:
        dimensions = _image_dimensions(raw_path)
        if dimensions and dimensions[0] >= minimum_width and dimensions[1] >= minimum_height:
            valid += 1
    resolution_rate = _bounded_ratio(valid, expected_pages)
    exact = (
        source_count == expected_pages
        and len(page_list) == expected_pages
        and len(rendered_list) == expected_pages
    )
    return input_recall, resolution_rate, exact


def _problem_resolution_rate(
    session: Mapping[str, Any] | None,
    *,
    expected_questions: Iterable[int],
    minimum_width: int,
    minimum_height: int,
    bbox_scale_minimum: float,
) -> float:
    expected = Counter(int(item) for item in expected_questions)
    expected_total = sum(expected.values())
    if session is None:
        return 0.0
    problems = _field(session, "problems")
    if not isinstance(problems, list):
        return 0.0
    valid = 0
    remaining = expected.copy()
    for raw_problem in problems:
        if not isinstance(raw_problem, Mapping):
            continue
        number_raw = _field(raw_problem, "problemNumber", "problem_number", "number")
        try:
            number = int(number_raw)
        except (TypeError, ValueError):
            continue
        if remaining[number] <= 0:
            continue
        remaining[number] -= 1
        dimensions = _image_dimensions(
            _field(
                raw_problem,
                "originalImagePath",
                "original_image_path",
                "imagePath",
                "image_path",
            )
        )
        bbox = _field(raw_problem, "bbox")
        if not dimensions or not isinstance(bbox, Mapping):
            continue
        try:
            bbox_width = float(bbox.get("width") or 0)
            bbox_height = float(bbox.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if (
            dimensions[0] >= minimum_width
            and dimensions[1] >= minimum_height
            and dimensions[0] >= bbox_width * bbox_scale_minimum
            and dimensions[1] >= bbox_height * bbox_scale_minimum
        ):
            valid += 1
    return _bounded_ratio(valid, expected_total)


def _whole_metrics(
    session: Mapping[str, Any] | None,
    *,
    expected_pages: int,
    minimum_width: int,
    minimum_height: int,
) -> tuple[float, float, bool]:
    if session is None:
        return 0.0, 0.0, False
    problems = _field(session, "problems")
    problem_list = problems if isinstance(problems, list) else []
    valid_mode = 0
    valid_resolution = 0
    unique_sources: set[str] = set()
    for raw_problem in problem_list:
        if not isinstance(raw_problem, Mapping):
            continue
        intent = str(_field(raw_problem, "inputIntent", "input_intent") or "")
        placement = str(
            _field(raw_problem, "placementMode", "placement_mode") or ""
        )
        full_bounds = bool(
            _field(raw_problem, "forceFullPageBounds", "force_full_page_bounds")
        )
        source_id = str(
            _field(raw_problem, "sourcePageId", "source_page_id") or ""
        )
        dimensions = _image_dimensions(
            _field(
                raw_problem,
                "imagePath",
                "image_path",
                "sourceImagePath",
                "source_image_path",
            )
        )
        mode_ok = (
            intent == "page-as-is"
            and placement == "continuous-page-as-is"
            and full_bounds
            and bool(source_id)
            and source_id not in unique_sources
        )
        if mode_ok:
            unique_sources.add(source_id)
            valid_mode += 1
            if (
                dimensions
                and dimensions[0] >= minimum_width
                and dimensions[1] >= minimum_height
            ):
                valid_resolution += 1
    recall = _bounded_ratio(valid_mode, expected_pages)
    resolution_rate = _bounded_ratio(valid_resolution, expected_pages)
    exact = (
        str(_field(session, "input_intent", "inputIntent") or "") == "page-as-is"
        and len(problem_list) == expected_pages
        and valid_mode == expected_pages
    )
    return recall, resolution_rate, exact


def _edb_valid(session: Mapping[str, Any] | None) -> bool:
    if session is None:
        return False
    return _positive_file(_field(session, "edb_path", "edbPath", "edb_file_uri"))


def _completion_rate(checks: Iterable[bool]) -> float:
    values = tuple(bool(item) for item in checks)
    return _bounded_ratio(sum(values), len(values))


def score_case(
    case: Mapping[str, Any],
    *,
    problem: PipelineResult,
    whole: PipelineResult,
    dimension_gate: Mapping[str, Any],
) -> SubjectCaseScore:
    case_id = str(case["case_id"])
    expected = _mapping(case["expected"], f"{case_id}.expected")
    expected_questions = tuple(int(item) for item in expected["question_numbers"])
    expected_passages = tuple(expected["passage_ranges"])
    expected_pages = int(expected["source_page_count"])
    resolution_gate = _mapping(case["resolution_gate"], f"{case_id}.resolution_gate")
    observation = problem.observation or _empty_observation(problem.elapsed_ms)
    scored_passages = (
        expected_passages
        if str(case["subject"]) == "korean"
        else observation.passage_ranges
    )
    recognition: RecognitionScore = score_observation(
        case_id=case_id,
        subject=str(case["subject"]),
        level=str(case["level"]),
        observation=observation,
        expected_questions=expected_questions,
        expected_passages=scored_passages,
        processing_ms=problem.elapsed_ms,
        output_dir=Path(problem.output_dir),
        pipeline_exit_code=problem.exit_code,
    )
    input_page_recall, source_resolution_rate, source_exact = _source_metrics(
        problem.session,
        expected_pages=expected_pages,
        minimum_width=int(resolution_gate["source_page_min_width"]),
        minimum_height=int(resolution_gate["source_page_min_height"]),
    )
    problem_resolution_rate = _problem_resolution_rate(
        problem.session,
        expected_questions=expected_questions,
        minimum_width=int(resolution_gate["problem_crop_min_width"]),
        minimum_height=int(resolution_gate["problem_crop_min_height"]),
        bbox_scale_minimum=float(resolution_gate["problem_bbox_scale_min"]),
    )
    whole_page_recall, whole_resolution_rate, whole_exact = _whole_metrics(
        whole.session,
        expected_pages=expected_pages,
        minimum_width=int(resolution_gate["source_page_min_width"]),
        minimum_height=int(resolution_gate["source_page_min_height"]),
    )
    problem_budget = float(case["processing_ms_max"])
    whole_budget = float(case["whole_processing_ms_max"])
    problem_time_factor = (
        _bounded_ratio(problem_budget, problem.elapsed_ms)
        if not problem.error
        else 0.0
    )
    whole_time_factor = (
        _bounded_ratio(whole_budget, whole.elapsed_ms)
        if not whole.error
        else 0.0
    )
    time_factor = (problem_time_factor + whole_time_factor) / 2.0
    within_time_budget = (
        not problem.error
        and not whole.error
        and problem.elapsed_ms <= problem_budget
        and whole.elapsed_ms <= whole_budget
    )
    problem_complete = (
        not problem.error
        and source_exact
        and _edb_valid(problem.session)
        and recognition.question_recall == 1.0
        and recognition.passage_recall == 1.0
        and recognition.artifact_valid_rate == 1.0
    )
    whole_complete = (
        not whole.error
        and whole_exact
        and _edb_valid(whole.session)
        and whole_page_recall == 1.0
        and whole_resolution_rate == 1.0
    )
    completion_rate = _completion_rate(
        (
            not problem.error,
            source_exact,
            _edb_valid(problem.session),
            recognition.question_recall == 1.0,
            recognition.passage_recall == 1.0,
            recognition.artifact_valid_rate == 1.0,
            not whole.error,
            whole_exact,
            _edb_valid(whole.session),
            whole_page_recall == 1.0,
            whole_resolution_rate == 1.0,
            problem_complete,
            whole_complete,
        )
    )
    score = (
        0.80 * recognition.score
        + 3.0 * input_page_recall
        + 3.0 * whole_page_recall
        + 3.0 * source_resolution_rate
        + 3.0 * problem_resolution_rate
        + 2.0 * whole_resolution_rate
        + 2.0 * time_factor
        + 4.0 * completion_rate
    )
    metrics = {
        "input_page_recall": input_page_recall,
        "whole_page_recall": whole_page_recall,
        "source_resolution_valid_rate": source_resolution_rate,
        "problem_resolution_valid_rate": problem_resolution_rate,
        "whole_resolution_valid_rate": whole_resolution_rate,
        "completion_rate": completion_rate,
    }
    dimension_failures: list[str] = []
    for metric, value in metrics.items():
        minimum = float(dimension_gate.get(f"{metric}_min", 1.0))
        if value < minimum:
            dimension_failures.append(metric)
    if bool(dimension_gate.get("within_time_budget_required", True)) and not within_time_budget:
        dimension_failures.append("time_budget")
    if problem.error:
        dimension_failures.append("problem_pipeline_error")
    if whole.error:
        dimension_failures.append("whole_pipeline_error")
    failures = [*recognition.failures, *dimension_failures]
    return SubjectCaseScore(
        case_id=case_id,
        year=int(case["year"]),
        subject=str(case["subject"]),
        subject_display_name=str(case["subject_display_name"]),
        subject_area=str(case["subject_area"]),
        subject_area_display_name=str(case["subject_area_display_name"]),
        score=round(score, 3),
        recognition_score=recognition.score,
        question_recall=recognition.question_recall,
        question_precision=recognition.question_precision,
        passage_recall=recognition.passage_recall,
        passage_precision=recognition.passage_precision,
        artifact_valid_rate=recognition.artifact_valid_rate,
        manual_review_rate=recognition.manual_review_rate,
        preflight_issue_count=recognition.preflight_issue_count,
        input_page_recall=input_page_recall,
        whole_page_recall=whole_page_recall,
        source_resolution_valid_rate=source_resolution_rate,
        problem_resolution_valid_rate=problem_resolution_rate,
        whole_resolution_valid_rate=whole_resolution_rate,
        completion_rate=completion_rate,
        problem_processing_ms=round(problem.elapsed_ms, 3),
        problem_processing_ms_max=problem_budget,
        whole_processing_ms=round(whole.elapsed_ms, 3),
        whole_processing_ms_max=whole_budget,
        time_factor=time_factor,
        within_time_budget=within_time_budget,
        expected_question_count=len(expected_questions),
        detected_question_count=len(observation.question_numbers),
        expected_page_count=expected_pages,
        problem_output_dir=problem.output_dir,
        whole_output_dir=whole.output_dir,
        dimension_failures=tuple(dict.fromkeys(dimension_failures)),
        failures=tuple(dict.fromkeys(failures)),
    )


def _summary_group(
    cases: Iterable[SubjectCaseScore],
    key,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[SubjectCaseScore]] = {}
    for case in cases:
        grouped.setdefault(str(key(case)), []).append(case)
    return {
        name: {
            "case_count": len(items),
            "average_score": round(
                sum(item.score for item in items) / len(items),
                3,
            ),
            "minimum_score": min(item.score for item in items),
        }
        for name, items in sorted(grouped.items())
    }


def build_report(
    *,
    catalog_path: Path,
    cases: list[SubjectCaseScore],
    average_min: float,
    case_min: float,
    dimension_gate: Mapping[str, Any],
    required_case_ids: set[str],
    require_complete_coverage: bool,
    started_at: float,
) -> dict[str, Any]:
    if not cases:
        raise WindowsBenchmarkError("benchmark produced no case scores")
    average_score = sum(case.score for case in cases) / len(cases)
    minimum_score = min(case.score for case in cases)
    actual_ids = {case.case_id for case in cases}
    missing_case_ids = sorted(required_case_ids - actual_ids)
    score_failures = [case.case_id for case in cases if case.score < case_min]
    dimension_failures = [
        case.case_id for case in cases if case.dimension_failures
    ]
    coverage_pass = not require_complete_coverage or not missing_case_ids
    gate_pass = (
        average_score >= average_min
        and minimum_score >= case_min
        and not dimension_failures
        and coverage_pass
    )
    return {
        "schema_version": 2,
        "benchmark": "windows-csat-all-subjects-v2",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "catalog_path": str(catalog_path.resolve()),
        "case_count": len(cases),
        "average_score": round(average_score, 3),
        "minimum_score": round(minimum_score, 3),
        "summary_by_area": _summary_group(
            cases,
            lambda case: case.subject_area_display_name,
        ),
        "summary_by_subject": _summary_group(
            cases,
            lambda case: case.subject_display_name,
        ),
        "gate": {
            "average_min": average_min,
            "case_min": case_min,
            "dimension_gate": dict(dimension_gate),
            "average_pass": average_score >= average_min,
            "minimum_pass": minimum_score >= case_min,
            "dimension_pass": not dimension_failures,
            "coverage_required": require_complete_coverage,
            "coverage_pass": coverage_pass,
            "missing_case_ids": missing_case_ids,
            "score_failures": score_failures,
            "dimension_failures": dimension_failures,
            "pass": gate_pass,
        },
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "cases": [asdict(case) for case in cases],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    status = "PASS" if report["gate"]["pass"] else "FAIL"
    lines = [
        "# Windows CSAT all-subject benchmark",
        "",
        f"**{status}** - average {report['average_score']:.3f}, "
        f"minimum {report['minimum_score']:.3f}, {report['case_count']} cases",
        "",
        "| Case | Area / subject | Score | Recognition | Q R/P | Passage R/P | Input / whole | Source / crop / whole res. | Problem / whole sec. | Complete |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in sorted(
        report["cases"],
        key=lambda item: (item["score"], item["case_id"]),
    ):
        lines.append(
            f"| {case['case_id']} | {case['subject_area_display_name']} / "
            f"{case['subject_display_name']} | {case['score']:.3f} | "
            f"{case['recognition_score']:.3f} | "
            f"{case['question_recall']:.3f}/{case['question_precision']:.3f} | "
            f"{case['passage_recall']:.3f}/{case['passage_precision']:.3f} | "
            f"{case['input_page_recall']:.3f}/{case['whole_page_recall']:.3f} | "
            f"{case['source_resolution_valid_rate']:.3f}/"
            f"{case['problem_resolution_valid_rate']:.3f}/"
            f"{case['whole_resolution_valid_rate']:.3f} | "
            f"{case['problem_processing_ms'] / 1000:.1f}/"
            f"{case['whole_processing_ms'] / 1000:.1f} | "
            f"{case['completion_rate']:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _print_console_safe(content: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    printable = content.encode(encoding, errors="backslashreplace").decode(encoding)
    print(printable)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run strict Windows problem-recognition and whole-page tests for "
            "every KICE CSAT subject."
        )
    )
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--ocr", default="auto")
    parser.add_argument("--average-min", type=float, default=None)
    parser.add_argument("--case-min", type=float, default=None)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    args = parser.parse_args(argv)
    if platform.system() != "Windows":
        parser.error("this benchmark must run on Windows")
    started_at = time.perf_counter()
    try:
        catalog = _mapping(_load_json(args.catalog), "catalog")
        raw_cases = _sequence(catalog.get("cases"), "catalog.cases")
        score_gate = _mapping(catalog.get("score_gate"), "catalog.score_gate")
        dimension_gate = _mapping(
            catalog.get("dimension_gate"),
            "catalog.dimension_gate",
        )
        average_min = float(
            args.average_min
            if args.average_min is not None
            else score_gate["average_min"]
        )
        case_min = float(
            args.case_min if args.case_min is not None else score_gate["case_min"]
        )
        if not (0 <= average_min <= 100 and 0 <= case_min <= 100):
            raise WindowsBenchmarkError("score gates must be between 0 and 100")
        validated = [
            _validate_case(raw_case, index)
            for index, raw_case in enumerate(raw_cases)
        ]
        all_case_ids = {str(case["case_id"]) for case in validated}
        requested = set(args.case_id)
        unknown = sorted(requested - all_case_ids)
        if unknown:
            raise WindowsBenchmarkError(
                f"unknown --case-id values: {', '.join(unknown)}"
            )
        selected = [
            case for case in validated
            if not requested or str(case["case_id"]) in requested
        ]
        if args.max_cases > 0:
            selected = selected[: args.max_cases]
        work_dir = args.work_dir.expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=False)
        scores: list[SubjectCaseScore] = []
        for index, case in enumerate(selected):
            case_id = str(case["case_id"])
            print(
                f"[windows-csat] {index + 1}/{len(selected)} {case_id} "
                "problem",
                flush=True,
            )
            case_dir = work_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=False)
            problem = run_pipeline(
                case,
                output_dir=case_dir / "problem",
                python_executable=args.python_executable,
                ocr=args.ocr,
                mode="problem",
            )
            print(
                f"[windows-csat] {index + 1}/{len(selected)} {case_id} whole",
                flush=True,
            )
            whole = run_pipeline(
                case,
                output_dir=case_dir / "whole",
                python_executable=args.python_executable,
                ocr=args.ocr,
                mode="whole",
            )
            score = score_case(
                case,
                problem=problem,
                whole=whole,
                dimension_gate=dimension_gate,
            )
            scores.append(score)
            print(
                f"[windows-csat] {case_id}: score={score.score:.3f}, "
                f"questions={score.detected_question_count}/"
                f"{score.expected_question_count}, "
                f"pages={score.input_page_recall:.3f}/"
                f"{score.whole_page_recall:.3f}",
                flush=True,
            )
        report = build_report(
            catalog_path=args.catalog,
            cases=scores,
            average_min=average_min,
            case_min=case_min,
            dimension_gate=dimension_gate,
            required_case_ids=all_case_ids,
            require_complete_coverage=not requested and args.max_cases <= 0,
            started_at=started_at,
        )
        json_text = json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        markdown_text = render_markdown(report)
        if args.json_report:
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(json_text, encoding="utf-8")
        if args.markdown_report:
            args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_report.write_text(markdown_text, encoding="utf-8")
        _print_console_safe(markdown_text)
        return 0 if report["gate"]["pass"] else 1
    except (WindowsBenchmarkError, OSError, ValueError, KeyError) as exc:
        print(f"[windows-csat] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
