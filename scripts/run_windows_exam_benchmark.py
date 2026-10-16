#!/usr/bin/env python3
"""Run and score the private real-exam corpus on Windows."""

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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_quality_corpus import (  # noqa: E402
    CorpusError,
    Observation,
    extract_observation,
)


class WindowsBenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    subject: str
    level: str
    score: float
    question_recall: float
    question_precision: float
    passage_recall: float
    passage_precision: float
    artifact_valid_rate: float
    manual_review_rate: float
    preflight_issue_count: int
    expected_question_count: int
    detected_question_count: int
    expected_passage_count: int
    detected_passage_count: int
    processing_ms: float
    pipeline_exit_code: int
    output_dir: str
    failures: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _bounded_ratio(numerator: int, denominator: int, *, empty: float) -> float:
    if denominator <= 0:
        return empty
    return max(0.0, min(1.0, numerator / denominator))


def _multiset_precision_recall(
    detected: Iterable[Any],
    expected: Iterable[Any],
) -> tuple[float, float]:
    detected_counter = Counter(detected)
    expected_counter = Counter(expected)
    matched = sum(
        min(count, expected_counter.get(item, 0))
        for item, count in detected_counter.items()
    )
    detected_total = sum(detected_counter.values())
    expected_total = sum(expected_counter.values())
    precision = _bounded_ratio(matched, detected_total, empty=1.0 if not expected_total else 0.0)
    recall = _bounded_ratio(matched, expected_total, empty=1.0)
    return precision, recall


def score_observation(
    *,
    case_id: str,
    subject: str,
    level: str,
    observation: Observation,
    expected_questions: Iterable[int],
    expected_passages: Iterable[Iterable[int]],
    processing_ms: float,
    output_dir: Path,
    pipeline_exit_code: int = 0,
) -> CaseScore:
    questions = tuple(int(item) for item in expected_questions)
    passages = tuple(tuple(int(value) for value in item) for item in expected_passages)
    question_precision, question_recall = _multiset_precision_recall(
        observation.question_numbers,
        questions,
    )
    passage_precision, passage_recall = _multiset_precision_recall(
        observation.passage_ranges,
        passages,
    )
    valid_artifact_count = sum(
        1 for signature in observation.problem_signatures if signature.artifact_valid
    )
    artifact_valid_rate = _bounded_ratio(
        valid_artifact_count,
        len(questions),
        empty=1.0,
    )
    manual_review_rate = _bounded_ratio(
        observation.manual_review_count,
        observation.review_population,
        empty=0.0,
    )
    preflight_factor = 1.0 if observation.preflight_issue_count == 0 else 0.0
    score = (
        35.0 * question_recall
        + 15.0 * question_precision
        + 20.0 * passage_recall
        + 10.0 * passage_precision
        + 10.0 * artifact_valid_rate
        + 5.0 * (1.0 - manual_review_rate)
        + 5.0 * preflight_factor
    )
    failures: list[str] = []
    if question_recall < 1.0:
        failures.append("missing_questions")
    if question_precision < 1.0:
        failures.append("extra_or_duplicate_questions")
    if passage_recall < 1.0:
        failures.append("missing_passage_ranges")
    if passage_precision < 1.0:
        failures.append("extra_or_duplicate_passage_ranges")
    if artifact_valid_rate < 1.0:
        failures.append("missing_or_invalid_artifacts")
    if manual_review_rate > 0:
        failures.append("manual_review_required")
    if observation.preflight_issue_count:
        failures.append("preflight_issues")
    return CaseScore(
        case_id=case_id,
        subject=subject,
        level=level,
        score=round(score, 3),
        question_recall=question_recall,
        question_precision=question_precision,
        passage_recall=passage_recall,
        passage_precision=passage_precision,
        artifact_valid_rate=artifact_valid_rate,
        manual_review_rate=manual_review_rate,
        preflight_issue_count=observation.preflight_issue_count,
        expected_question_count=len(questions),
        detected_question_count=len(observation.question_numbers),
        expected_passage_count=len(passages),
        detected_passage_count=len(observation.passage_ranges),
        processing_ms=round(processing_ms, 3),
        pipeline_exit_code=pipeline_exit_code,
        output_dir=str(output_dir.resolve()),
        failures=tuple(failures),
    )


def _validate_case(raw_case: Any, index: int) -> Mapping[str, Any]:
    case = _mapping(raw_case, f"catalog.cases[{index}]")
    for field in ("case_id", "subject", "level", "local_path", "sha256", "expected"):
        if field not in case:
            raise WindowsBenchmarkError(
                f"catalog.cases[{index}] is missing field {field}"
            )
    case_id = str(case["case_id"])
    if not case_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in case_id):
        raise WindowsBenchmarkError(f"unsafe case id {case_id!r}")
    source_path = Path(str(case["local_path"])).expanduser().resolve()
    if not source_path.is_file():
        raise WindowsBenchmarkError(f"source PDF is missing for {case_id}: {source_path}")
    if _sha256_file(source_path) != str(case["sha256"]).lower():
        raise WindowsBenchmarkError(f"source digest mismatch for {case_id}")
    expected = _mapping(case["expected"], f"catalog.cases[{index}].expected")
    questions = _sequence(
        expected.get("question_numbers"),
        f"catalog.cases[{index}].expected.question_numbers",
    )
    if not questions:
        raise WindowsBenchmarkError(f"{case_id} has no expected question numbers")
    _sequence(
        expected.get("passage_ranges"),
        f"catalog.cases[{index}].expected.passage_ranges",
    )
    return case


def run_pipeline_case(
    case: Mapping[str, Any],
    *,
    output_dir: Path,
    python_executable: str,
    ocr: str,
) -> tuple[Observation, float]:
    source_path = Path(str(case["local_path"])).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    command = [
        python_executable,
        str(PROJECT_ROOT / "build_problem_board_edb.py"),
        str(source_path),
        "--output-dir",
        str(output_dir),
        "--subject",
        str(case["subject"]),
        "--ocr",
        ocr,
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
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
        tail = "\n".join((completed.stdout or "").splitlines()[-40:])
        raise WindowsBenchmarkError(
            f"pipeline failed for {case['case_id']} with exit {completed.returncode}:\n{tail}"
        )
    session_path = output_dir / "ui_session.json"
    if not session_path.is_file():
        raise WindowsBenchmarkError(f"pipeline did not create {session_path}")
    try:
        observation = extract_observation(
            _load_json(session_path),
            str(case["case_id"]),
            processing_ms_override=elapsed_ms,
        )
    except CorpusError as exc:
        raise WindowsBenchmarkError(
            f"could not extract observation for {case['case_id']}: {exc}"
        ) from exc
    return observation, elapsed_ms


def build_report(
    *,
    catalog_path: Path,
    cases: list[CaseScore],
    average_min: float,
    case_min: float,
    started_at: float,
) -> dict[str, Any]:
    if not cases:
        raise WindowsBenchmarkError("benchmark produced no case scores")
    average_score = sum(case.score for case in cases) / len(cases)
    minimum_score = min(case.score for case in cases)
    case_failures = [case.case_id for case in cases if case.score < case_min]
    report = {
        "schema_version": 1,
        "benchmark": "windows-real-exams-v1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "catalog_path": str(catalog_path.resolve()),
        "case_count": len(cases),
        "average_score": round(average_score, 3),
        "minimum_score": round(minimum_score, 3),
        "gate": {
            "average_min": average_min,
            "case_min": case_min,
            "average_pass": average_score >= average_min,
            "minimum_pass": minimum_score >= case_min,
            "case_failures": case_failures,
            "pass": average_score >= average_min and minimum_score >= case_min,
        },
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "cases": [asdict(case) for case in cases],
    }
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    status = "PASS" if report["gate"]["pass"] else "FAIL"
    lines = [
        "# Windows real-exam recognition benchmark",
        "",
        f"**{status}** - average {report['average_score']:.3f}, "
        f"minimum {report['minimum_score']:.3f}, {report['case_count']} cases",
        "",
        "| Case | Subject | Level | Score | Q recall/precision | Passage recall/precision | Artifact | Review | ms |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in sorted(report["cases"], key=lambda item: (item["score"], item["case_id"])):
        lines.append(
            f"| {case['case_id']} | {case['subject']} | {case['level']} | "
            f"{case['score']:.3f} | {case['question_recall']:.3f}/{case['question_precision']:.3f} | "
            f"{case['passage_recall']:.3f}/{case['passage_precision']:.3f} | "
            f"{case['artifact_valid_rate']:.3f} | {case['manual_review_rate']:.3f} | "
            f"{case['processing_ms']:.0f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _print_console_safe(content: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    printable = content.encode(encoding, errors="backslashreplace").decode(encoding)
    print(printable)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Windows-only real-exam recognition benchmark."
    )
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--ocr", default="auto")
    parser.add_argument("--average-min", type=float, default=None)
    parser.add_argument("--case-min", type=float, default=None)
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
        gate = _mapping(catalog.get("score_gate"), "catalog.score_gate")
        average_min = float(
            args.average_min if args.average_min is not None else gate["average_min"]
        )
        case_min = float(args.case_min if args.case_min is not None else gate["case_min"])
        if not (0 <= average_min <= 100 and 0 <= case_min <= 100):
            raise WindowsBenchmarkError("score gates must be between 0 and 100")
        selected_cases = raw_cases[: args.max_cases] if args.max_cases > 0 else raw_cases
        work_dir = args.work_dir.expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=False)
        scores: list[CaseScore] = []
        for index, raw_case in enumerate(selected_cases):
            case = _validate_case(raw_case, index)
            case_id = str(case["case_id"])
            print(f"[windows-benchmark] {index + 1}/{len(selected_cases)} {case_id}")
            output_dir = work_dir / case_id
            observation, elapsed_ms = run_pipeline_case(
                case,
                output_dir=output_dir,
                python_executable=args.python_executable,
                ocr=args.ocr,
            )
            expected = _mapping(case["expected"], f"{case_id}.expected")
            score = score_observation(
                case_id=case_id,
                subject=str(case["subject"]),
                level=str(case["level"]),
                observation=observation,
                expected_questions=expected["question_numbers"],
                expected_passages=expected["passage_ranges"],
                processing_ms=elapsed_ms,
                output_dir=output_dir,
            )
            scores.append(score)
            print(
                f"[windows-benchmark] {case_id}: score={score.score:.3f} "
                f"questions={score.detected_question_count}/{score.expected_question_count}"
            )
        report = build_report(
            catalog_path=args.catalog,
            cases=scores,
            average_min=average_min,
            case_min=case_min,
            started_at=started_at,
        )
        json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
        print(f"[windows-benchmark] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
