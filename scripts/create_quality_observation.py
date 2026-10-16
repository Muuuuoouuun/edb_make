#!/usr/bin/env python3
"""Create a privacy-minimized quality observation from an existing UI session."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_quality_corpus import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    PRODUCTION_MINIMUM_CASES,
    CorpusError,
    baseline_report_fingerprint,
    expected_fingerprint,
    extract_observation,
    observation_payload,
    validate_manifest_readiness,
)


SOURCE_FORMATS = {
    ".pdf": "pdf",
    ".hwp": "hwp",
    ".hwpx": "hwpx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError("session file was not found") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"session contains invalid JSON: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise CorpusError(f"session could not be read: {exc}") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CorpusError(f"could not hash source file: {exc}") from exc
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: Any, *, replace: bool) -> None:
    if path.exists() and not replace:
        raise CorpusError(f"output already exists: {path}; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False, indent=2, sort_keys=False)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _private_manifest_path(source_path: Path, manifest_path: Path) -> str:
    try:
        return str(source_path.relative_to(manifest_path.parent))
    except ValueError:
        return str(source_path)


def scaffold_manifest(
    source_dir: Path,
    *,
    manifest_path: Path,
    corpus_id: str,
    subject: str,
    tags: list[str],
    recursive: bool,
    minimum_cases: int,
    required_formats: list[str],
    required_subjects: list[str],
    required_tags: list[str],
    processing_ms_max: float,
) -> dict[str, Any]:
    source_dir = source_dir.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if not source_dir.is_dir():
        raise CorpusError(f"source directory was not found: {source_dir}")
    iterator = source_dir.rglob("*") if recursive else source_dir.iterdir()
    sources = sorted(
        (path.resolve() for path in iterator if path.is_file() and path.suffix.lower() in SOURCE_FORMATS),
        key=lambda path: str(path.relative_to(source_dir)).lower(),
    )
    if not sources:
        raise CorpusError("source directory contains no supported PDF/HWP/HWPX/image files")
    normalized_subject = subject.strip().lower() or "unknown"
    normalized_tags = sorted({item.strip().lower() for item in tags if item.strip()})
    cases: list[dict[str, Any]] = []
    for index, source_path in enumerate(sources):
        source_sha256 = _sha256_file(source_path)
        case_id = f"case-{index + 1:03d}"
        cases.append(
            {
                "id": case_id,
                "description": "Private corpus document; source name intentionally omitted from case metadata.",
                "tags": sorted(set(normalized_tags + [SOURCE_FORMATS[source_path.suffix.lower()]])),
                "source": {
                    "path": _private_manifest_path(source_path, manifest_path),
                    "sha256": source_sha256,
                    "format": SOURCE_FORMATS[source_path.suffix.lower()],
                    "subject": normalized_subject,
                },
                "result": f"observations/{case_id}.json",
                "expected": {"question_numbers": [], "passage_ranges": []},
                "ground_truth": {"status": "pending"},
            }
        )
    return {
        "$schema": str((PROJECT_ROOT / "quality" / "corpus.schema.json").resolve()),
        "schema_version": 1,
        "corpus_id": corpus_id.strip(),
        "description": "Protected release corpus. Do not commit this manifest or its sources.",
        "coverage_requirements": {
            "minimum_cases": minimum_cases,
            "required_formats": sorted(set(required_formats)),
            "required_subjects": sorted(set(required_subjects)),
            "required_tags": sorted(set(required_tags)),
        },
        "case_thresholds": {
            "missing_question_count_max": 0,
            "duplicate_question_count_max": 0,
            "extra_question_count_max": 0,
            "question_recall_min": 1.0,
            "question_precision_min": 1.0,
            "missing_passage_range_count_max": 0,
            "extra_passage_range_count_max": 0,
            "passage_range_recall_min": 1.0,
            "passage_range_precision_min": 1.0,
            "preflight_issue_count_max": 0,
            "manual_review_rate_max": 0.25,
            "processing_ms_max": processing_ms_max,
            "problem_signature_mismatch_count_max": 0,
            "artifact_invalid_count_max": 0,
        },
        "aggregate_thresholds": {
            "missing_question_count_max": 0,
            "duplicate_question_count_max": 0,
            "extra_question_count_max": 0,
            "question_recall_min": 1.0,
            "question_precision_min": 1.0,
            "missing_passage_range_count_max": 0,
            "extra_passage_range_count_max": 0,
            "passage_range_recall_min": 1.0,
            "passage_range_precision_min": 1.0,
            "preflight_issue_count_max": 0,
            "manual_review_rate_max": 0.20,
            "processing_ms_p50_max": processing_ms_max,
            "processing_ms_p95_max": processing_ms_max,
            "case_failure_count_max": 0,
            "problem_signature_mismatch_count_max": 0,
            "artifact_invalid_count_max": 0,
        },
        "regression_tolerance": {
            "question_recall_drop_max": 0.0,
            "question_precision_drop_max": 0.0,
            "passage_range_recall_drop_max": 0.0,
            "passage_range_precision_drop_max": 0.0,
            "manual_review_rate_increase_max": 0.02,
            "preflight_issue_count_increase_max": 0.0,
            "processing_ms_p95_increase_ratio_max": 0.10,
        },
        "cases": cases,
    }


def _parse_questions(spec: str) -> list[int]:
    normalized = spec.strip().lower()
    if normalized in {"", "none", "empty", "없음"}:
        return []
    values: list[int] = []
    for token in normalized.split(","):
        token = token.strip()
        match = re.fullmatch(r"([1-9][0-9]*)\s*-\s*([1-9][0-9]*)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if end < start:
                raise CorpusError(f"question range ends before it starts: {token}")
            values.extend(range(start, end + 1))
        elif re.fullmatch(r"[1-9][0-9]*", token):
            values.append(int(token))
        else:
            raise CorpusError(f"invalid question token: {token!r}")
    if len(set(values)) != len(values):
        raise CorpusError("question specification contains duplicates")
    return sorted(values)


def _parse_passage_ranges(spec: str) -> list[list[int]]:
    normalized = spec.strip().lower()
    if normalized in {"", "none", "empty", "없음"}:
        return []
    ranges: list[list[int]] = []
    for token in normalized.split(","):
        match = re.fullmatch(r"\s*([1-9][0-9]*)\s*-\s*([1-9][0-9]*)\s*", token)
        if not match:
            raise CorpusError(f"invalid passage range: {token!r}; use START-END")
        start, end = int(match.group(1)), int(match.group(2))
        if end < start:
            raise CorpusError(f"passage range ends before it starts: {token}")
        ranges.append([start, end])
    if len({tuple(item) for item in ranges}) != len(ranges):
        raise CorpusError("passage range specification contains duplicates")
    return sorted(ranges)


def _find_case(manifest: Any, case_id: str) -> dict[str, Any]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise CorpusError("manifest.cases must be an array")
    matches = [case for case in manifest["cases"] if isinstance(case, dict) and case.get("id") == case_id]
    if len(matches) != 1:
        raise CorpusError(f"case id was not found or is duplicated: {case_id}")
    return matches[0]


def _observation_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract question/range/review/preflight/timing metrics from a UI session "
            "without copying OCR text or source paths."
        )
    )
    parser.add_argument("session", type=Path, help="Private UI session JSON")
    parser.add_argument("--output", type=Path, required=True, help="Observation sidecar JSON")
    parser.add_argument(
        "--processing-ms",
        type=float,
        default=None,
        help="Measured elapsed time when the session does not contain one",
    )
    parser.add_argument(
        "--preflight-issue-count",
        type=int,
        default=None,
        help="Measured preflight issue count when the session does not contain preflight data",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing output file")
    args = parser.parse_args(argv)

    try:
        if args.output.exists() and not args.force:
            raise CorpusError("output already exists; pass --force to replace it")
        observation = extract_observation(
            _load_json(args.session),
            "session",
            processing_ms_override=args.processing_ms,
            preflight_issue_count_override=args.preflight_issue_count,
        )
        payload = observation_payload(observation)
        _write_json_atomic(args.output, payload, replace=args.force)
        print(
            "[quality-observation] "
            f"questions={len(observation.question_numbers)} "
            f"passage_ranges={len(observation.passage_ranges)} "
            f"manual_review={observation.manual_review_count}/{observation.review_population} "
            f"preflight_issues={observation.preflight_issue_count} "
            f"processing_ms={observation.processing_ms:g}"
        )
        return EXIT_OK
    except (CorpusError, OSError) as exc:
        print(f"[quality-observation] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT


def _scaffold_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create a protected private-corpus manifest from source files.")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--subject", default="unknown")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--minimum-cases", type=int, default=PRODUCTION_MINIMUM_CASES)
    parser.add_argument("--required-format", action="append", default=[])
    parser.add_argument("--required-subject", action="append", default=[])
    parser.add_argument("--required-tag", action="append", default=[])
    parser.add_argument("--processing-ms-max", type=float, default=300000.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.minimum_cases <= 0 or args.processing_ms_max <= 0:
            raise CorpusError("minimum-cases and processing-ms-max must be positive")
        manifest = scaffold_manifest(
            args.source_dir,
            manifest_path=args.manifest,
            corpus_id=args.corpus_id,
            subject=args.subject,
            tags=args.tag,
            recursive=args.recursive,
            minimum_cases=args.minimum_cases,
            required_formats=[item.strip().lower() for item in args.required_format],
            required_subjects=[item.strip().lower() for item in args.required_subject],
            required_tags=[item.strip().lower() for item in args.required_tag],
            processing_ms_max=args.processing_ms_max,
        )
        _write_json_atomic(args.manifest, manifest, replace=args.force)
        print(f"[quality-corpus] scaffolded {len(manifest['cases'])} private cases at {args.manifest}")
        return EXIT_OK
    except (CorpusError, OSError) as exc:
        print(f"[quality-corpus] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT


def _label_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Record human ground truth for one protected corpus case.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("case_id")
    parser.add_argument("--questions", required=True, help="Example: 1-20,22 or none")
    parser.add_argument("--passages", required=True, help="Example: 1-3,18-21 or none")
    parser.add_argument("--annotator-id", required=True, help="Pseudonymous operator id; do not use a personal name")
    parser.add_argument(
        "--observation",
        type=Path,
        help="Privacy-safe observation containing approved bbox/crop/content signatures",
    )
    parser.add_argument("--subject")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--allow-empty-document", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = _load_json(args.manifest)
        case = _find_case(manifest, args.case_id)
        questions = _parse_questions(args.questions)
        passages = _parse_passage_ranges(args.passages)
        if not questions and not args.allow_empty_document:
            raise CorpusError("empty question labels require --allow-empty-document")
        annotator_id = args.annotator_id.strip()
        if not annotator_id:
            raise CorpusError("annotator-id must be non-empty")
        old_ground_truth = case.get("ground_truth") if isinstance(case.get("ground_truth"), dict) else {}
        revision = int(old_ground_truth.get("annotation_revision") or 0) + 1
        problem_signatures: list[dict[str, Any]] = []
        if args.observation:
            structural_observation = extract_observation(
                _load_json(args.observation), "structural observation"
            )
            signature_numbers = [
                signature.number for signature in structural_observation.problem_signatures
            ]
            if sorted(signature_numbers) != questions or len(set(signature_numbers)) != len(signature_numbers):
                raise CorpusError(
                    "observation problem signatures must cover every labeled question exactly once"
                )
            if any(
                not signature.artifact_valid or signature.artifact_size_bytes <= 0
                for signature in structural_observation.problem_signatures
            ):
                raise CorpusError("observation contains an invalid or missing crop artifact")
            problem_signatures = observation_payload(structural_observation)[
                "qualityObservation"
            ]["problemSignatures"]
        expected = {
            "question_numbers": questions,
            "passage_ranges": passages,
            "problem_signatures": problem_signatures,
        }
        case["expected"] = expected
        case["ground_truth"] = {
            "status": "labeled",
            "annotation_revision": revision,
            "annotator_id": annotator_id,
            "labeled_at": _utc_now(),
            "expected_sha256": expected_fingerprint(expected),
            "allow_empty_document": args.allow_empty_document,
        }
        if args.subject:
            if not isinstance(case.get("source"), dict):
                raise CorpusError("case.source must be an object")
            case["source"]["subject"] = args.subject.strip().lower()
        if args.tag:
            case["tags"] = sorted(
                set(case.get("tags") or [])
                | {item.strip().lower() for item in args.tag if item.strip()}
            )
        _write_json_atomic(args.manifest, manifest, replace=True)
        print(f"[quality-corpus] labeled {args.case_id} revision={revision}; independent approval is still required")
        return EXIT_OK
    except (CorpusError, OSError, ValueError) as exc:
        print(f"[quality-corpus] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT


def _approve_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Independently approve one labeled ground-truth case.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("case_id")
    parser.add_argument("--reviewer-id", required=True, help="Pseudonymous reviewer id")
    args = parser.parse_args(argv)
    try:
        manifest = _load_json(args.manifest)
        case = _find_case(manifest, args.case_id)
        ground_truth = case.get("ground_truth")
        if not isinstance(ground_truth, dict) or ground_truth.get("status") != "labeled":
            raise CorpusError("case must be labeled before independent approval")
        reviewer_id = args.reviewer_id.strip()
        if not reviewer_id or reviewer_id == ground_truth.get("annotator_id"):
            raise CorpusError("reviewer-id must be non-empty and differ from annotator-id")
        if ground_truth.get("expected_sha256") != expected_fingerprint(case.get("expected")):
            raise CorpusError("expected labels changed after annotation; label the case again")
        ground_truth["status"] = "approved"
        ground_truth["reviewer_id"] = reviewer_id
        ground_truth["reviewed_at"] = _utc_now()
        _write_json_atomic(args.manifest, manifest, replace=True)
        print(f"[quality-corpus] approved {args.case_id} revision={ground_truth['annotation_revision']}")
        return EXIT_OK
    except (CorpusError, OSError) as exc:
        print(f"[quality-corpus] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT


def _validate_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check whether a protected corpus is release-ready without running it.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--minimum-cases", type=int, default=PRODUCTION_MINIMUM_CASES)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--skip-source-digest", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = _load_json(args.manifest)
        root = (args.corpus_root or args.manifest.parent).expanduser().resolve()
        report = validate_manifest_readiness(
            manifest,
            corpus_root=root,
            minimum_cases=args.minimum_cases,
            require_approved=True,
            verify_sources=not args.skip_source_digest,
        )
        if args.json_report:
            _write_json_atomic(args.json_report, report, replace=True)
        print(
            f"[quality-corpus] {report['status'].upper()} "
            f"approved={report['approved_case_count']}/{report['case_count']} "
            f"source_digests={report['source_digest_verified_count']}/{report['case_count']}"
        )
        for error in report["errors"]:
            print(f"- ERROR: {error}")
        for warning in report["warnings"]:
            print(f"- WARNING: {warning}")
        return EXIT_OK if report["status"] == "ready" else EXIT_GATE_FAILED
    except (CorpusError, OSError) as exc:
        print(f"[quality-corpus] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT


def _approve_baseline_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Attach a tamper-evident human approval to a passed baseline candidate.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer-id", required=True, help="Pseudonymous release reviewer id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = _load_json(args.report)
        if not isinstance(report, dict) or report.get("status") != "passed":
            raise CorpusError("only a passed quality report can become a baseline")
        provenance = report.get("pipeline_provenance")
        if not isinstance(provenance, dict):
            raise CorpusError("baseline candidate is missing pipeline_provenance")
        if provenance.get("fresh_pipeline_execution") is not True:
            raise CorpusError("baseline candidate must come from a fresh pipeline execution")
        if provenance.get("observation_provenance_verified") is not True:
            raise CorpusError("baseline candidate must verify every observation provenance")
        if provenance.get("worktree_clean") is not True:
            raise CorpusError("baseline candidate must come from a clean checkout")
        if provenance.get("private_artifacts_retained") is True:
            raise CorpusError("baseline candidate retained private intermediate artifacts")
        reviewer_id = args.reviewer_id.strip()
        if not reviewer_id:
            raise CorpusError("reviewer-id must be non-empty")
        corpus_fingerprint = report.get("corpus_fingerprint")
        if not isinstance(corpus_fingerprint, str):
            raise CorpusError("baseline candidate is missing corpus_fingerprint")
        report["baseline_approval"] = {
            "status": "approved",
            "reviewer_id": reviewer_id,
            "approved_at": _utc_now(),
            "corpus_fingerprint": corpus_fingerprint,
            "report_sha256": baseline_report_fingerprint(report),
        }
        _write_json_atomic(args.output, report, replace=args.force)
        print(f"[quality-corpus] approved baseline written to {args.output}")
        return EXIT_OK
    except (CorpusError, OSError) as exc:
        print(f"[quality-corpus] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        command = arguments[0]
        command_arguments = arguments[1:]
        if command == "scaffold":
            return _scaffold_main(command_arguments)
        if command == "label":
            return _label_main(command_arguments)
        if command == "approve":
            return _approve_main(command_arguments)
        if command == "validate":
            return _validate_main(command_arguments)
        if command == "approve-baseline":
            return _approve_baseline_main(command_arguments)
    return _observation_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
