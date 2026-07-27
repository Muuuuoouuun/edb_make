#!/usr/bin/env python3
"""Evaluate private or synthetic EDB quality observations against release gates.

The aggregate evaluator is stdlib-only. Session-to-signature extraction uses
Pillow when validating user-visible crop/render artifacts for release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import zipfile
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_INVALID_INPUT = 2

SUPPORTED_SCHEMA_VERSION = 1
PRODUCTION_MINIMUM_CASES = 30
PRODUCTION_REQUIRED_FORMATS = frozenset({"pdf", "hwp", "hwpx", "image"})
PRODUCTION_REQUIRED_SUBJECTS = frozenset({"korean", "english", "math"})
PRODUCTION_REQUIRED_TAGS = frozenset(
    {"multi-column", "low-resolution-scan", "cross-page-passage"}
)

SUPPORTED_SOURCE_FORMATS = {"pdf", "hwp", "hwpx", "image", "synthetic"}
GROUND_TRUTH_STATUSES = {"pending", "labeled", "approved"}

CASE_THRESHOLD_METRICS = {
    "missing_question_count_max": ("missing_question_count", "max"),
    "duplicate_question_count_max": ("duplicate_question_count", "max"),
    "extra_question_count_max": ("extra_question_count", "max"),
    "question_recall_min": ("question_recall", "min"),
    "question_precision_min": ("question_precision", "min"),
    "missing_passage_range_count_max": ("missing_passage_range_count", "max"),
    "extra_passage_range_count_max": ("extra_passage_range_count", "max"),
    "passage_range_recall_min": ("passage_range_recall", "min"),
    "passage_range_precision_min": ("passage_range_precision", "min"),
    "preflight_issue_count_max": ("preflight_issue_count", "max"),
    "manual_review_rate_max": ("manual_review_rate", "max"),
    "processing_ms_max": ("processing_ms", "max"),
    "problem_signature_mismatch_count_max": ("problem_signature_mismatch_count", "max"),
    "artifact_invalid_count_max": ("artifact_invalid_count", "max"),
}

AGGREGATE_THRESHOLD_METRICS = {
    key: value
    for key, value in CASE_THRESHOLD_METRICS.items()
    if key != "processing_ms_max"
}
AGGREGATE_THRESHOLD_METRICS.update(
    {
        "processing_ms_p50_max": ("processing_ms_p50", "max"),
        "processing_ms_p95_max": ("processing_ms_p95", "max"),
        "case_failure_count_max": ("case_failure_count", "max"),
    }
)

REGRESSION_METRICS = {
    "question_recall_drop_max": ("question_recall", "drop"),
    "question_precision_drop_max": ("question_precision", "drop"),
    "passage_range_recall_drop_max": ("passage_range_recall", "drop"),
    "passage_range_precision_drop_max": ("passage_range_precision", "drop"),
    "manual_review_rate_increase_max": ("manual_review_rate", "increase"),
    "preflight_issue_count_increase_max": ("preflight_issue_count", "increase"),
    "processing_ms_p95_increase_ratio_max": ("processing_ms_p95", "increase_ratio"),
}

REQUIRED_RELEASE_CASE_THRESHOLDS = frozenset(CASE_THRESHOLD_METRICS)
REQUIRED_RELEASE_AGGREGATE_THRESHOLDS = frozenset(AGGREGATE_THRESHOLD_METRICS)
REQUIRED_RELEASE_REGRESSION_TOLERANCES = frozenset(REGRESSION_METRICS)

# Release manifests may tighten these code-owned limits, but cannot weaken them.
# Changing this policy requires a reviewed code change instead of a private-manifest edit.
RELEASE_CASE_POLICY = {
    "missing_question_count_max": 0.0,
    "duplicate_question_count_max": 0.0,
    "extra_question_count_max": 0.0,
    "question_recall_min": 1.0,
    "question_precision_min": 1.0,
    "missing_passage_range_count_max": 0.0,
    "extra_passage_range_count_max": 0.0,
    "passage_range_recall_min": 1.0,
    "passage_range_precision_min": 1.0,
    "preflight_issue_count_max": 0.0,
    "manual_review_rate_max": 0.25,
    "processing_ms_max": 300000.0,
    "problem_signature_mismatch_count_max": 0.0,
    "artifact_invalid_count_max": 0.0,
}

RELEASE_AGGREGATE_POLICY = {
    "missing_question_count_max": 0.0,
    "duplicate_question_count_max": 0.0,
    "extra_question_count_max": 0.0,
    "question_recall_min": 1.0,
    "question_precision_min": 1.0,
    "missing_passage_range_count_max": 0.0,
    "extra_passage_range_count_max": 0.0,
    "passage_range_recall_min": 1.0,
    "passage_range_precision_min": 1.0,
    "preflight_issue_count_max": 0.0,
    "manual_review_rate_max": 0.20,
    "processing_ms_p50_max": 300000.0,
    "processing_ms_p95_max": 300000.0,
    "case_failure_count_max": 0.0,
    "problem_signature_mismatch_count_max": 0.0,
    "artifact_invalid_count_max": 0.0,
}

RELEASE_REGRESSION_POLICY = {
    "question_recall_drop_max": 0.0,
    "question_precision_drop_max": 0.0,
    "passage_range_recall_drop_max": 0.0,
    "passage_range_precision_drop_max": 0.0,
    "manual_review_rate_increase_max": 0.02,
    "preflight_issue_count_increase_max": 0.0,
    "processing_ms_p95_increase_ratio_max": 0.10,
}

GROUND_TRUTH_ALLOWED_FIELDS = {
    "status",
    "annotation_revision",
    "annotator_id",
    "labeled_at",
    "reviewer_id",
    "reviewed_at",
    "expected_sha256",
    "allow_empty_document",
}

OBSERVATION_PROVENANCE_REQUIRED_FIELDS = {
    "schemaVersion",
    "runner",
    "runId",
    "gitCommit",
    "pipelineFingerprint",
    "environmentFingerprint",
    "sourceSha256",
    "timingMethod",
    "cachePolicy",
    "cacheHitCount",
}


class CorpusError(ValueError):
    """Raised when a corpus or observation is incomplete or malformed."""


@dataclass(frozen=True)
class ProblemSignature:
    number: int
    source_page_id: str
    bbox_sha256: str
    crop_sha256: str
    render_sha256: str
    visual_sha256: str
    content_sha256: str
    choice_count: int
    choice_order: tuple[int, ...]
    artifact_valid: bool
    artifact_size_bytes: int


@dataclass(frozen=True)
class Observation:
    question_numbers: tuple[int, ...]
    passage_ranges: tuple[tuple[int, int], ...]
    preflight_issue_count: int
    manual_review_count: int
    review_population: int
    processing_ms: float
    problem_signatures: tuple[ProblemSignature, ...] = ()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"invalid JSON in {path}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise CorpusError(f"could not read {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CorpusError(f"{label} must be a JSON object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CorpusError(f"{label} must be a JSON array")
    return value


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise CorpusError(f"{label} contains unsupported field(s): {', '.join(unknown)}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CorpusError(f"{label} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise CorpusError(f"{label} must be finite")
    return numeric


def _nonnegative_number(value: Any, label: str) -> float:
    numeric = _number(value, label)
    if numeric < 0:
        raise CorpusError(f"{label} must be non-negative")
    return numeric


def _positive_int(value: Any, label: str) -> int:
    numeric = _number(value, label)
    integer = int(numeric)
    if numeric != integer or integer <= 0:
        raise CorpusError(f"{label} must be a positive integer")
    return integer


def _nonnegative_int(value: Any, label: str) -> int:
    numeric = _number(value, label)
    integer = int(numeric)
    if numeric != integer or integer < 0:
        raise CorpusError(f"{label} must be a non-negative integer")
    return integer


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"{label} must be a non-empty string")
    return value.strip()


def _normalize_question_numbers(value: Any, label: str) -> tuple[int, ...]:
    return tuple(_positive_int(item, f"{label}[{index}]") for index, item in enumerate(_sequence(value, label)))


def _normalize_passage_range(value: Any, label: str) -> tuple[int, int]:
    if isinstance(value, Mapping):
        start = _positive_int(_first(value, "start", "from"), f"{label}.start")
        end = _positive_int(_first(value, "end", "to"), f"{label}.end")
    else:
        sequence = _sequence(value, label)
        if len(sequence) != 2:
            raise CorpusError(f"{label} must contain exactly two question numbers")
        start = _positive_int(sequence[0], f"{label}[0]")
        end = _positive_int(sequence[1], f"{label}[1]")
    if end < start:
        raise CorpusError(f"{label}.end must be greater than or equal to start")
    return start, end


def _normalize_passage_ranges(value: Any, label: str) -> tuple[tuple[int, int], ...]:
    ranges = {
        _normalize_passage_range(item, f"{label}[{index}]")
        for index, item in enumerate(_sequence(value, label))
    }
    return tuple(sorted(ranges))


def expected_fingerprint(expected: Any, label: str = "expected") -> str:
    """Hash normalized human labels without including source content or paths."""

    expected_mapping = _mapping(expected, label)
    _reject_unknown(
        expected_mapping,
        {"questionNumbers", "question_numbers", "passageRanges", "passage_ranges", "problemSignatures", "problem_signatures"},
        label,
    )
    questions = _normalize_question_numbers(
        _first(expected_mapping, "questionNumbers", "question_numbers"),
        f"{label}.question_numbers",
    )
    if len(set(questions)) != len(questions):
        raise CorpusError(f"{label}.question_numbers contains duplicates")
    raw_ranges = _sequence(
        _first(expected_mapping, "passageRanges", "passage_ranges"),
        f"{label}.passage_ranges",
    )
    ranges = _normalize_passage_ranges(raw_ranges, f"{label}.passage_ranges")
    if len(ranges) != len(raw_ranges):
        raise CorpusError(f"{label}.passage_ranges contains duplicates")
    canonical = json.dumps(
        {
            "question_numbers": list(questions),
            "passage_ranges": [list(item) for item in ranges],
            "problem_signatures": [
                _signature_payload(
                    _normalize_problem_signature(
                        item, f"{label}.problem_signatures[{index}]"
                    )
                )
                for index, item in enumerate(
                    _sequence(
                        _first(expected_mapping, "problemSignatures", "problem_signatures") or [],
                        f"{label}.problem_signatures",
                    )
                )
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _string_set(value: Any, label: str) -> set[str]:
    sequence = _sequence(value, label)
    normalized: set[str] = set()
    for index, item in enumerate(sequence):
        text = _nonempty_string(item, f"{label}[{index}]").lower()
        if text in normalized:
            raise CorpusError(f"{label} contains duplicate value {text!r}")
        normalized.add(text)
    return normalized


def _validate_ground_truth(
    raw: Any,
    *,
    expected: Any,
    label: str,
    require_approved: bool,
) -> tuple[str, bool]:
    if raw is None:
        if require_approved:
            raise CorpusError(f"{label}.ground_truth is required for release evidence")
        return "untracked", False
    ground_truth = _mapping(raw, f"{label}.ground_truth")
    _reject_unknown(ground_truth, GROUND_TRUTH_ALLOWED_FIELDS, f"{label}.ground_truth")
    status = _nonempty_string(ground_truth.get("status"), f"{label}.ground_truth.status").lower()
    if status not in GROUND_TRUTH_STATUSES:
        raise CorpusError(
            f"{label}.ground_truth.status must be one of {', '.join(sorted(GROUND_TRUTH_STATUSES))}"
        )
    allow_empty = ground_truth.get("allow_empty_document", False)
    if not isinstance(allow_empty, bool):
        raise CorpusError(f"{label}.ground_truth.allow_empty_document must be a boolean")
    expected_mapping = _mapping(expected, f"{label}.expected")
    questions = _normalize_question_numbers(
        _first(expected_mapping, "questionNumbers", "question_numbers"),
        f"{label}.expected.question_numbers",
    )
    if not questions and not allow_empty and status != "pending":
        raise CorpusError(
            f"{label}.expected.question_numbers is empty; set allow_empty_document only for an intentionally empty fixture"
        )
    if status == "pending":
        if require_approved:
            raise CorpusError(f"{label}.ground_truth.status is pending")
        return status, False

    revision = _positive_int(
        ground_truth.get("annotation_revision"),
        f"{label}.ground_truth.annotation_revision",
    )
    del revision
    annotator_id = _nonempty_string(
        ground_truth.get("annotator_id"), f"{label}.ground_truth.annotator_id"
    )
    _nonempty_string(ground_truth.get("labeled_at"), f"{label}.ground_truth.labeled_at")
    expected_sha256 = _nonempty_string(
        ground_truth.get("expected_sha256"), f"{label}.ground_truth.expected_sha256"
    )
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise CorpusError(f"{label}.ground_truth.expected_sha256 must be a lowercase SHA-256")
    if expected_sha256 != expected_fingerprint(expected_mapping, f"{label}.expected"):
        raise CorpusError(f"{label}.ground_truth.expected_sha256 does not match expected labels")

    if status == "approved":
        reviewer_id = _nonempty_string(
            ground_truth.get("reviewer_id"), f"{label}.ground_truth.reviewer_id"
        )
        _nonempty_string(ground_truth.get("reviewed_at"), f"{label}.ground_truth.reviewed_at")
        if reviewer_id == annotator_id:
            raise CorpusError(f"{label}.ground_truth reviewer must differ from annotator")
        return status, True
    if require_approved:
        raise CorpusError(f"{label}.ground_truth.status is labeled, not approved")
    return status, False


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _path_from_session_uri(raw: Any) -> Path | None:
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


def _inspect_artifact(raw_path: Any) -> tuple[str, str, bool, int]:
    path = _path_from_session_uri(raw_path)
    if path is None:
        return "0" * 64, "0" * 64, False, 0
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        return "0" * 64, "0" * 64, False, 0
    visual_sha256 = "0" * 64
    artifact_valid = False
    if size > 0:
        try:
            from PIL import Image

            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                if rgba.width > 0 and rgba.height > 0:
                    preview = rgba.copy()
                    preview.thumbnail((256, 256))
                    alpha = preview.getchannel("A")
                    alpha_min, alpha_max = alpha.getextrema()
                    if alpha_min < 255:
                        alpha_histogram = alpha.histogram()
                        total = sum(alpha_histogram)
                        visible = sum(alpha_histogram[9:])
                        non_dominant = total - max(alpha_histogram, default=0)
                        required_signal = max(2, int(total * 0.001))
                        artifact_valid = (
                            alpha_max > 8
                            and visible >= required_signal
                            and non_dominant >= required_signal
                        )
                    else:
                        gray = preview.convert("RGB").convert("L")
                        histogram = gray.histogram()
                        total = sum(histogram)
                        non_dominant = total - max(histogram, default=0)
                        artifact_valid = non_dominant >= max(2, int(total * 0.001))
                    visual_sha256 = _sha256_bytes(
                        json.dumps(
                            {"width": rgba.width, "height": rgba.height, "mode": "RGBA"},
                            sort_keys=True,
                        ).encode("ascii")
                        + rgba.tobytes()
                    )
        except (ImportError, OSError, ValueError):
            artifact_valid = False
    return digest.hexdigest(), visual_sha256, artifact_valid, size


def _choice_order(problem: Mapping[str, Any]) -> tuple[int, ...]:
    raw_order = _problem_field(problem, "choiceOrder", "choice_order")
    if raw_order is None:
        choices = _problem_field(problem, "choices")
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)):
            return tuple(range(1, len(choices) + 1))
        count_raw = _problem_field(problem, "choiceCount", "choice_count")
        if count_raw is None:
            return ()
        count = _nonnegative_int(count_raw, "problem.choice_count")
        return tuple(range(1, count + 1))
    sequence = _sequence(raw_order, "problem.choice_order")
    return tuple(
        _positive_int(item, f"problem.choice_order[{index}]")
        for index, item in enumerate(sequence)
    )


def _session_problem_signature(
    problem: Mapping[str, Any], *, label: str
) -> ProblemSignature:
    number = _positive_int(
        _problem_field(problem, "problemNumber", "problem_number", "number"),
        f"{label}.number",
    )
    raw_source_page_id = _nonempty_string(
        _problem_field(problem, "sourcePageId", "source_page_id"),
        f"{label}.source_page_id",
    )
    source_page_id = _canonical_hash({"source_page_id": raw_source_page_id})
    bbox_raw = _problem_field(problem, "bbox")
    bbox = _mapping(bbox_raw, f"{label}.bbox")
    bbox_payload = {
        key: round(_number(bbox.get(key), f"{label}.bbox.{key}"), 3)
        for key in ("left", "top", "width", "height")
    }
    crop_sha256, _, crop_valid, crop_size = _inspect_artifact(
        _problem_field(
            problem,
            "originalImagePath",
            "original_image_path",
            "imagePath",
            "image_path",
        )
    )
    render_sha256, render_visual_sha256, render_valid, render_size = _inspect_artifact(
        _problem_field(
            problem,
            "boardRenderPath",
            "board_render_path",
            "imagePath",
            "image_path",
            "originalImagePath",
            "original_image_path",
        )
    )
    choice_order = _choice_order(problem)
    content_parts: list[str] = []
    for key in (
        "text",
        "ocrText",
        "ocr_text",
        "recognizedText",
        "recognized_text",
        "stemText",
        "stem_text",
    ):
        value = _problem_field(problem, key)
        if isinstance(value, str) and value.strip():
            content_parts.append(" ".join(value.split()))
    content_sha256 = _canonical_hash(
        {"structured_text": content_parts, "visual_sha256": render_visual_sha256}
    )
    return ProblemSignature(
        number=number,
        source_page_id=source_page_id,
        bbox_sha256=_canonical_hash(
            {"source_page_id": source_page_id, "bbox": bbox_payload}
        ),
        crop_sha256=crop_sha256,
        render_sha256=render_sha256,
        visual_sha256=render_visual_sha256,
        content_sha256=content_sha256,
        choice_count=len(choice_order),
        choice_order=choice_order,
        artifact_valid=crop_valid and render_valid,
        artifact_size_bytes=crop_size + render_size,
    )


def _normalize_problem_signature(raw: Any, label: str) -> ProblemSignature:
    signature = _mapping(raw, label)
    allowed = {
        "number",
        "sourcePageId",
        "source_page_id",
        "bboxSha256",
        "bbox_sha256",
        "cropSha256",
        "crop_sha256",
        "renderSha256",
        "render_sha256",
        "visualSha256",
        "visual_sha256",
        "contentSha256",
        "content_sha256",
        "choiceCount",
        "choice_count",
        "choiceOrder",
        "choice_order",
        "artifactValid",
        "artifact_valid",
        "artifactSizeBytes",
        "artifact_size_bytes",
    }
    _reject_unknown(signature, allowed, label)
    digests: dict[str, str] = {}
    for canonical, aliases in {
        "bbox": ("bboxSha256", "bbox_sha256"),
        "crop": ("cropSha256", "crop_sha256"),
        "render": ("renderSha256", "render_sha256"),
        "visual": ("visualSha256", "visual_sha256"),
        "content": ("contentSha256", "content_sha256"),
    }.items():
        value = _nonempty_string(_first(signature, *aliases), f"{label}.{canonical}_sha256")
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise CorpusError(f"{label}.{canonical}_sha256 must be a lowercase SHA-256")
        digests[canonical] = value
    raw_order = _first(signature, "choiceOrder", "choice_order")
    choice_order = tuple(
        _positive_int(item, f"{label}.choice_order[{index}]")
        for index, item in enumerate(_sequence(raw_order, f"{label}.choice_order"))
    )
    choice_count = _nonnegative_int(
        _first(signature, "choiceCount", "choice_count"), f"{label}.choice_count"
    )
    if choice_count != len(choice_order):
        raise CorpusError(f"{label}.choice_count must equal choice_order length")
    artifact_valid = _first(signature, "artifactValid", "artifact_valid")
    if not isinstance(artifact_valid, bool):
        raise CorpusError(f"{label}.artifact_valid must be a boolean")
    source_page_id = _nonempty_string(
        _first(signature, "sourcePageId", "source_page_id"),
        f"{label}.source_page_id",
    )
    if re.fullmatch(r"[0-9a-f]{64}", source_page_id) is None:
        raise CorpusError(f"{label}.source_page_id must be a privacy-safe SHA-256")
    return ProblemSignature(
        number=_positive_int(signature.get("number"), f"{label}.number"),
        source_page_id=source_page_id,
        bbox_sha256=digests["bbox"],
        crop_sha256=digests["crop"],
        render_sha256=digests["render"],
        visual_sha256=digests["visual"],
        content_sha256=digests["content"],
        choice_count=choice_count,
        choice_order=choice_order,
        artifact_valid=artifact_valid,
        artifact_size_bytes=_nonnegative_int(
            _first(signature, "artifactSizeBytes", "artifact_size_bytes"),
            f"{label}.artifact_size_bytes",
        ),
    )


def _signature_payload(signature: ProblemSignature) -> dict[str, Any]:
    return {
        "number": signature.number,
        "sourcePageId": signature.source_page_id,
        "bboxSha256": signature.bbox_sha256,
        "cropSha256": signature.crop_sha256,
        "renderSha256": signature.render_sha256,
        "visualSha256": signature.visual_sha256,
        "contentSha256": signature.content_sha256,
        "choiceCount": signature.choice_count,
        "choiceOrder": list(signature.choice_order),
        "artifactValid": signature.artifact_valid,
        "artifactSizeBytes": signature.artifact_size_bytes,
    }


def _problem_metadata(problem: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = problem.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _problem_field(problem: Mapping[str, Any], *keys: str) -> Any:
    value = _first(problem, *keys)
    if value is not None:
        return value
    return _first(_problem_metadata(problem), *keys)


def _is_passage_fragment(problem: Mapping[str, Any]) -> bool:
    role = _problem_field(problem, "passageRole", "passage_role")
    return str(role or "").strip().lower() == "passage_fragment"


def _problem_requires_review(problem: Mapping[str, Any]) -> bool:
    explicit = _problem_field(problem, "manualReviewRequired", "manual_review_required")
    if isinstance(explicit, bool):
        return explicit
    status = str(_problem_field(problem, "reviewStatus", "review_status") or "").strip().lower()
    if status in {"check_needed", "failed", "needs_review", "review_required"}:
        return True
    if status in {"confirmed", "passed", "approved"}:
        return False
    flags = _problem_field(problem, "riskFlags", "risk_flags")
    return isinstance(flags, Sequence) and not isinstance(flags, (str, bytes)) and bool(flags)


def _extract_explicit_observation(raw: Mapping[str, Any], label: str) -> Observation:
    question_numbers = _normalize_question_numbers(
        _first(raw, "questionNumbers", "question_numbers"), f"{label}.question_numbers"
    )
    passage_ranges = _normalize_passage_ranges(
        _first(raw, "passageRanges", "passage_ranges"), f"{label}.passage_ranges"
    )
    preflight_raw = _first(raw, "preflightIssueCount", "preflight_issue_count")
    if preflight_raw is None:
        issues = _sequence(_first(raw, "preflightIssues", "preflight_issues"), f"{label}.preflight_issues")
        preflight_issue_count = len(issues)
    else:
        preflight_issue_count = _nonnegative_int(preflight_raw, f"{label}.preflight_issue_count")
    manual_review_count = _nonnegative_int(
        _first(raw, "manualReviewCount", "manual_review_count"), f"{label}.manual_review_count"
    )
    review_population = _nonnegative_int(
        _first(raw, "reviewPopulation", "review_population"), f"{label}.review_population"
    )
    if manual_review_count > review_population:
        raise CorpusError(f"{label}.manual_review_count cannot exceed review_population")
    processing_ms = _nonnegative_number(
        _first(raw, "processingMs", "processing_ms"), f"{label}.processing_ms"
    )
    signatures_raw = _first(raw, "problemSignatures", "problem_signatures") or []
    problem_signatures = tuple(
        _normalize_problem_signature(item, f"{label}.problem_signatures[{index}]")
        for index, item in enumerate(
            _sequence(signatures_raw, f"{label}.problem_signatures")
        )
    )
    return Observation(
        question_numbers=question_numbers,
        passage_ranges=passage_ranges,
        preflight_issue_count=preflight_issue_count,
        manual_review_count=manual_review_count,
        review_population=review_population,
        processing_ms=processing_ms,
        problem_signatures=problem_signatures,
    )


def _find_preflight(raw: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = _first(raw, "classinPreflight", "classin_preflight", "preflight")
    if isinstance(value, Mapping):
        return value
    session = raw.get("session")
    if isinstance(session, Mapping):
        value = _first(session, "classinPreflight", "classin_preflight", "preflight")
        if isinstance(value, Mapping):
            return value
    return None


def _find_processing_ms(raw: Mapping[str, Any]) -> Any:
    direct = _first(raw, "processingMs", "processing_ms", "elapsedMs", "elapsed_ms")
    if direct is not None:
        return direct
    metrics = _first(raw, "qualityMetrics", "quality_metrics", "metrics", "timing", "timing_ms")
    if isinstance(metrics, Mapping):
        value = _first(metrics, "processingMs", "processing_ms", "elapsedMs", "elapsed_ms", "total")
        if value is not None:
            return value
    session = raw.get("session")
    if isinstance(session, Mapping):
        return _find_processing_ms(session)
    return None


def _session_passage_ranges(
    session: Mapping[str, Any], label: str
) -> set[tuple[int, int]]:
    """Read ranges summarized at session level when problem records omit them."""

    groups_raw = _first(session, "passageGroups", "passage_groups")
    if groups_raw is None:
        return set()
    groups = _sequence(groups_raw, f"{label}.passage_groups")
    ranges: set[tuple[int, int]] = set()
    for index, item in enumerate(groups):
        group = _mapping(item, f"{label}.passage_groups[{index}]")
        range_raw = _first(group, "passageRange", "passage_range")
        if range_raw is None:
            start = _first(group, "numberStart", "number_start", "start")
            end = _first(group, "numberEnd", "number_end", "end")
            if start is None and end is None:
                continue
            range_raw = {"start": start, "end": end}
        ranges.add(
            _normalize_passage_range(
                range_raw, f"{label}.passage_groups[{index}].passage_range"
            )
        )
    return ranges


def _extract_session_observation(
    raw: Mapping[str, Any],
    label: str,
    *,
    processing_ms_override: float | None = None,
    preflight_issue_count_override: int | None = None,
) -> Observation:
    session = raw.get("session") if isinstance(raw.get("session"), Mapping) else raw
    problems_raw = _first(session, "problems", "records")
    problems = _sequence(problems_raw, f"{label}.problems")
    question_numbers: list[int] = []
    passage_ranges = _session_passage_ranges(session, label)
    manual_review_count = 0
    review_population = 0
    problem_signatures: list[ProblemSignature] = []
    for index, item in enumerate(problems):
        problem = _mapping(item, f"{label}.problems[{index}]")
        passage_raw = _problem_field(problem, "passageRange", "passage_range")
        if passage_raw is not None:
            passage_ranges.add(_normalize_passage_range(passage_raw, f"{label}.problems[{index}].passage_range"))
        if _is_passage_fragment(problem):
            continue
        number_raw = _problem_field(problem, "problemNumber", "problem_number", "number")
        if number_raw is None:
            continue
        question_numbers.append(_positive_int(number_raw, f"{label}.problems[{index}].problem_number"))
        if _problem_field(problem, "sourcePageId", "source_page_id") is not None and _problem_field(problem, "bbox") is not None:
            problem_signatures.append(
                _session_problem_signature(problem, label=f"{label}.problems[{index}]")
            )
        review_population += 1
        if _problem_requires_review(problem):
            manual_review_count += 1

    if preflight_issue_count_override is not None:
        preflight_issue_count = _nonnegative_int(
            preflight_issue_count_override, f"{label}.preflight_issue_count_override"
        )
    else:
        preflight = _find_preflight(raw)
        if preflight is None:
            raise CorpusError(f"{label} is missing classinPreflight/classin_preflight")
        issue_count_raw = _first(preflight, "issueCount", "issue_count")
        if issue_count_raw is None:
            issues = _sequence(preflight.get("issues"), f"{label}.preflight.issues")
            preflight_issue_count = len(issues)
        else:
            preflight_issue_count = _nonnegative_int(
                issue_count_raw, f"{label}.preflight.issue_count"
            )

    processing_raw = (
        processing_ms_override
        if processing_ms_override is not None
        else _find_processing_ms(raw)
    )
    if processing_raw is None:
        raise CorpusError(
            f"{label} is missing processingMs/processing_ms; "
            "supply a measured processing time when creating an observation sidecar"
        )
    processing_ms = _nonnegative_number(processing_raw, f"{label}.processing_ms")
    return Observation(
        question_numbers=tuple(question_numbers),
        passage_ranges=tuple(sorted(passage_ranges)),
        preflight_issue_count=preflight_issue_count,
        manual_review_count=manual_review_count,
        review_population=review_population,
        processing_ms=processing_ms,
        problem_signatures=tuple(problem_signatures),
    )


def extract_observation(
    raw: Any,
    label: str,
    *,
    processing_ms_override: float | None = None,
    preflight_issue_count_override: int | None = None,
) -> Observation:
    root = _mapping(raw, label)
    explicit = _first(root, "qualityObservation", "quality_observation")
    if explicit is not None:
        return _extract_explicit_observation(_mapping(explicit, f"{label}.qualityObservation"), label)
    return _extract_session_observation(
        root,
        label,
        processing_ms_override=processing_ms_override,
        preflight_issue_count_override=preflight_issue_count_override,
    )


def observation_payload(
    observation: Observation,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a privacy-minimized sidecar without OCR text or source paths."""

    payload: dict[str, Any] = {
        "qualityObservation": {
            "questionNumbers": list(observation.question_numbers),
            "passageRanges": [list(item) for item in observation.passage_ranges],
            "preflightIssueCount": observation.preflight_issue_count,
            "manualReviewCount": observation.manual_review_count,
            "reviewPopulation": observation.review_population,
            "processingMs": observation.processing_ms,
            "problemSignatures": [
                _signature_payload(item) for item in observation.problem_signatures
            ],
        }
    }
    if provenance is not None:
        payload["measurementProvenance"] = dict(provenance)
    return payload


def _validate_observation_provenance(
    raw: Any,
    *,
    source_sha256: str | None,
    expected_provenance: Mapping[str, Any] | None,
    label: str,
) -> bool:
    root = _mapping(raw, label)
    provenance_raw = _first(root, "measurementProvenance", "measurement_provenance")
    if provenance_raw is None:
        return False
    provenance = _mapping(provenance_raw, f"{label}.measurementProvenance")
    missing = sorted(OBSERVATION_PROVENANCE_REQUIRED_FIELDS - set(provenance))
    if missing:
        raise CorpusError(
            f"{label}.measurementProvenance is missing field(s): {', '.join(missing)}"
        )
    if _nonnegative_int(
        provenance.get("schemaVersion"), f"{label}.measurementProvenance.schemaVersion"
    ) != 1:
        raise CorpusError(f"{label}.measurementProvenance.schemaVersion must be 1")
    for field in (
        "runner",
        "runId",
        "gitCommit",
        "pipelineFingerprint",
        "environmentFingerprint",
        "sourceSha256",
        "timingMethod",
        "cachePolicy",
    ):
        _nonempty_string(
            provenance.get(field), f"{label}.measurementProvenance.{field}"
        )
    for field in (
        "pipelineFingerprint",
        "environmentFingerprint",
        "sourceSha256",
    ):
        if re.fullmatch(r"[0-9a-f]{64}", str(provenance[field])) is None:
            raise CorpusError(
                f"{label}.measurementProvenance.{field} must be a lowercase SHA-256"
            )
    if source_sha256 is not None and provenance["sourceSha256"] != source_sha256:
        raise CorpusError(
            f"{label}.measurementProvenance.sourceSha256 does not match manifest source"
        )
    if provenance["timingMethod"] != "monotonic_wall_clock":
        raise CorpusError(
            f"{label}.measurementProvenance.timingMethod must be monotonic_wall_clock"
        )
    if provenance["cachePolicy"] != "isolated_empty_per_case":
        raise CorpusError(
            f"{label}.measurementProvenance.cachePolicy must be isolated_empty_per_case"
        )
    if _nonnegative_int(
        provenance.get("cacheHitCount"),
        f"{label}.measurementProvenance.cacheHitCount",
    ) != 0:
        raise CorpusError(f"{label}.measurementProvenance.cacheHitCount must be 0")
    if expected_provenance is not None:
        expected_fields = {
            "runner": "runner",
            "run_id": "runId",
            "git_commit": "gitCommit",
            "pipeline_fingerprint": "pipelineFingerprint",
            "environment_fingerprint": "environmentFingerprint",
        }
        for expected_field, observation_field in expected_fields.items():
            expected_value = expected_provenance.get(expected_field)
            if expected_value is not None and provenance[observation_field] != expected_value:
                raise CorpusError(
                    f"{label}.measurementProvenance.{observation_field} does not match this run"
                )
    return True


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _evaluate_case(case: Mapping[str, Any], observation: Observation, label: str) -> dict[str, Any]:
    expected = _mapping(case.get("expected"), f"{label}.expected")
    _reject_unknown(
        expected,
        {"questionNumbers", "question_numbers", "passageRanges", "passage_ranges", "problemSignatures", "problem_signatures"},
        f"{label}.expected",
    )
    expected_questions = _normalize_question_numbers(
        _first(expected, "questionNumbers", "question_numbers"), f"{label}.expected.question_numbers"
    )
    if len(set(expected_questions)) != len(expected_questions):
        raise CorpusError(f"{label}.expected.question_numbers contains duplicates")
    expected_passages_raw = _sequence(
        _first(expected, "passageRanges", "passage_ranges"), f"{label}.expected.passage_ranges"
    )
    expected_passages = _normalize_passage_ranges(expected_passages_raw, f"{label}.expected.passage_ranges")
    if len(expected_passages) != len(expected_passages_raw):
        raise CorpusError(f"{label}.expected.passage_ranges contains duplicates")

    expected_question_set = set(expected_questions)
    actual_question_counts = Counter(observation.question_numbers)
    actual_question_set = set(actual_question_counts)
    matched_questions = expected_question_set & actual_question_set
    missing_questions = sorted(expected_question_set - actual_question_set)
    extra_questions = sorted(actual_question_set - expected_question_set)
    duplicates = [
        {"number": number, "count": count}
        for number, count in sorted(actual_question_counts.items())
        if count > 1
    ]
    duplicate_count = sum(item["count"] - 1 for item in duplicates)

    expected_passage_set = set(expected_passages)
    actual_passage_set = set(observation.passage_ranges)
    missing_passages = sorted(expected_passage_set - actual_passage_set)
    extra_passages = sorted(actual_passage_set - expected_passage_set)
    matched_passages = expected_passage_set & actual_passage_set

    expected_signatures_raw = _first(
        expected, "problemSignatures", "problem_signatures"
    ) or []
    expected_signatures = {
        item.number: item
        for item in (
            _normalize_problem_signature(
                raw_signature, f"{label}.expected.problem_signatures[{index}]"
            )
            for index, raw_signature in enumerate(
                _sequence(
                    expected_signatures_raw,
                    f"{label}.expected.problem_signatures",
                )
            )
        )
    }
    actual_signatures = {item.number: item for item in observation.problem_signatures}
    signature_mismatches: list[int] = []
    if expected_signatures:
        for number in sorted(set(expected_signatures) | set(actual_signatures)):
            if expected_signatures.get(number) != actual_signatures.get(number):
                signature_mismatches.append(number)
    artifact_invalid_count = sum(
        not signature.artifact_valid or signature.artifact_size_bytes <= 0
        for signature in observation.problem_signatures
    )

    metrics = {
        "expected_question_count": len(expected_question_set),
        "detected_question_count": len(observation.question_numbers),
        "missing_question_count": len(missing_questions),
        "duplicate_question_count": duplicate_count,
        "extra_question_count": len(extra_questions),
        "question_recall": _ratio(len(matched_questions), len(expected_question_set)),
        "question_precision": _ratio(len(matched_questions), len(observation.question_numbers)),
        "expected_passage_range_count": len(expected_passage_set),
        "detected_passage_range_count": len(actual_passage_set),
        "missing_passage_range_count": len(missing_passages),
        "extra_passage_range_count": len(extra_passages),
        "passage_range_recall": _ratio(len(matched_passages), len(expected_passage_set)),
        "passage_range_precision": _ratio(len(matched_passages), len(actual_passage_set)),
        "preflight_issue_count": observation.preflight_issue_count,
        "manual_review_count": observation.manual_review_count,
        "review_population": observation.review_population,
        "manual_review_rate": (
            observation.manual_review_count / observation.review_population
            if observation.review_population
            else 0.0
        ),
        "processing_ms": observation.processing_ms,
        "problem_signature_mismatch_count": len(signature_mismatches),
        "artifact_invalid_count": artifact_invalid_count,
    }
    return {
        "case_id": str(case.get("id") or ""),
        "metrics": metrics,
        "details": {
            "missing_questions": missing_questions,
            "duplicate_questions": duplicates,
            "extra_questions": extra_questions,
            "missing_passage_ranges": [list(item) for item in missing_passages],
            "extra_passage_ranges": [list(item) for item in extra_passages],
            "problem_signature_mismatch_numbers": signature_mismatches,
        },
        "failures": [],
    }


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _aggregate(case_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = [report["metrics"] for report in case_reports]
    expected_questions = sum(item["expected_question_count"] for item in metrics)
    detected_questions = sum(item["detected_question_count"] for item in metrics)
    missing_questions = sum(item["missing_question_count"] for item in metrics)
    duplicate_questions = sum(item["duplicate_question_count"] for item in metrics)
    extra_questions = sum(item["extra_question_count"] for item in metrics)
    matched_questions = expected_questions - missing_questions
    expected_passages = sum(item["expected_passage_range_count"] for item in metrics)
    detected_passages = sum(item["detected_passage_range_count"] for item in metrics)
    missing_passages = sum(item["missing_passage_range_count"] for item in metrics)
    extra_passages = sum(item["extra_passage_range_count"] for item in metrics)
    matched_passages = expected_passages - missing_passages
    manual_count = sum(item["manual_review_count"] for item in metrics)
    review_population = sum(item["review_population"] for item in metrics)
    processing_times = [item["processing_ms"] for item in metrics]
    return {
        "case_count": len(case_reports),
        "expected_question_count": expected_questions,
        "detected_question_count": detected_questions,
        "missing_question_count": missing_questions,
        "duplicate_question_count": duplicate_questions,
        "extra_question_count": extra_questions,
        "question_recall": _ratio(matched_questions, expected_questions),
        "question_precision": _ratio(matched_questions, detected_questions),
        "expected_passage_range_count": expected_passages,
        "detected_passage_range_count": detected_passages,
        "missing_passage_range_count": missing_passages,
        "extra_passage_range_count": extra_passages,
        "passage_range_recall": _ratio(matched_passages, expected_passages),
        "passage_range_precision": _ratio(matched_passages, detected_passages),
        "preflight_issue_count": sum(item["preflight_issue_count"] for item in metrics),
        "manual_review_count": manual_count,
        "review_population": review_population,
        "manual_review_rate": manual_count / review_population if review_population else 0.0,
        "processing_ms_p50": _percentile(processing_times, 0.50),
        "processing_ms_p95": _percentile(processing_times, 0.95),
        "processing_ms_max": max(processing_times, default=0.0),
        "problem_signature_mismatch_count": sum(
            item["problem_signature_mismatch_count"] for item in metrics
        ),
        "artifact_invalid_count": sum(item["artifact_invalid_count"] for item in metrics),
    }


def _validate_thresholds(raw: Any, allowed: Mapping[str, tuple[str, str]], label: str) -> Mapping[str, float]:
    thresholds = _mapping(raw or {}, label)
    unknown = sorted(set(thresholds) - set(allowed))
    if unknown:
        raise CorpusError(f"{label} contains unsupported threshold(s): {', '.join(unknown)}")
    normalized = {key: _nonnegative_number(value, f"{label}.{key}") for key, value in thresholds.items()}
    for key, value in normalized.items():
        if any(token in key for token in ("recall", "precision", "review_rate")) and value > 1:
            raise CorpusError(f"{label}.{key} must be between 0 and 1")
    return normalized


def _release_policy_errors(
    configured: Mapping[str, float],
    policy: Mapping[str, float],
    definitions: Mapping[str, tuple[str, str]],
    *,
    label: str,
) -> list[str]:
    errors: list[str] = []
    for rule, policy_limit in policy.items():
        if rule not in configured:
            continue
        configured_limit = float(configured[rule])
        _, direction = definitions[rule]
        weakened = (
            configured_limit < policy_limit
            if direction == "min"
            else configured_limit > policy_limit
        )
        if weakened:
            comparison = "at least" if direction == "min" else "at most"
            errors.append(
                f"{label}.{rule}={configured_limit:g} weakens release policy; "
                f"it must be {comparison} {policy_limit:g}"
            )
    return errors


def _threshold_failures(
    metrics: Mapping[str, Any],
    thresholds: Mapping[str, float],
    definitions: Mapping[str, tuple[str, str]],
    *,
    scope: str,
    case_id: str | None = None,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for threshold_name, limit in thresholds.items():
        metric_name, direction = definitions[threshold_name]
        actual = float(metrics[metric_name])
        failed = actual < limit if direction == "min" else actual > limit
        if failed:
            failure = {
                "scope": scope,
                "metric": metric_name,
                "rule": threshold_name,
                "actual": actual,
                "limit": limit,
            }
            if case_id is not None:
                failure["case_id"] = case_id
            failures.append(failure)
    return failures


def _baseline_failures(
    aggregate: Mapping[str, Any], baseline_report: Mapping[str, Any], tolerances: Mapping[str, Any]
) -> list[dict[str, Any]]:
    baseline = baseline_report.get("aggregate")
    if not isinstance(baseline, Mapping):
        raise CorpusError("baseline report is missing aggregate metrics")
    unknown = sorted(set(tolerances) - set(REGRESSION_METRICS))
    if unknown:
        raise CorpusError(f"regression_tolerance contains unsupported rule(s): {', '.join(unknown)}")
    failures: list[dict[str, Any]] = []
    for rule, raw_limit in tolerances.items():
        limit = _nonnegative_number(raw_limit, f"regression_tolerance.{rule}")
        metric_name, direction = REGRESSION_METRICS[rule]
        if metric_name not in baseline:
            raise CorpusError(f"baseline report is missing aggregate.{metric_name}")
        actual = float(aggregate[metric_name])
        baseline_value = _number(baseline[metric_name], f"baseline.aggregate.{metric_name}")
        if direction == "drop":
            regression = baseline_value - actual
        elif direction == "increase":
            regression = actual - baseline_value
        else:
            if baseline_value <= 0:
                raise CorpusError(f"baseline.aggregate.{metric_name} must be greater than zero")
            regression = (actual - baseline_value) / baseline_value
        if regression > limit:
            failures.append(
                {
                    "scope": "baseline",
                    "metric": metric_name,
                    "rule": rule,
                    "actual": actual,
                    "baseline": baseline_value,
                    "regression": regression,
                    "limit": limit,
                }
            )
    return failures


def _resolve_result_path(case: Mapping[str, Any], corpus_root: Path, label: str) -> Path:
    result = case.get("result")
    if isinstance(result, str):
        raw_path = result
    elif isinstance(result, Mapping):
        _reject_unknown(result, {"path", "observation_path"}, f"{label}.result")
        raw_path = _first(result, "path", "observation_path")
    else:
        raw_path = None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CorpusError(f"{label}.result.path must be a non-empty string")
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    return path if path.is_absolute() else corpus_root / path


def _resolve_private_path(raw_path: str, corpus_root: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    return path if path.is_absolute() else corpus_root / path


def _verify_source_digest(
    source: Mapping[str, Any], corpus_root: Path, label: str
) -> bool:
    raw_path = source.get("path")
    expected_digest = source.get("sha256")
    if raw_path is not None and (not isinstance(raw_path, str) or not raw_path.strip()):
        raise CorpusError(f"{label}.source.path must be a non-empty string")
    if expected_digest is None:
        return False
    if not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise CorpusError(f"{label}.source.sha256 must be 64 lowercase hexadecimal characters")
    if raw_path is None:
        raise CorpusError(f"{label}.source.sha256 requires source.path")

    digest = hashlib.sha256()
    source_path = _resolve_private_path(raw_path, corpus_root)
    try:
        with source_path.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CorpusError(f"could not verify {label}.source: {exc}") from exc
    if digest.hexdigest() != expected_digest:
        raise CorpusError(f"{label}.source.sha256 does not match source.path")
    return True


def _verify_declared_source_format(
    source: Mapping[str, Any], corpus_root: Path, label: str
) -> None:
    raw_path = _nonempty_string(source.get("path"), f"{label}.source.path")
    declared = _nonempty_string(
        source.get("format"), f"{label}.source.format"
    ).lower()
    path = _resolve_private_path(raw_path, corpus_root)
    suffix = path.suffix.lower()
    try:
        with path.open("rb") as input_file:
            header = input_file.read(16)
    except OSError as exc:
        raise CorpusError(f"could not probe {label}.source format: {exc}") from exc
    valid = False
    if declared == "pdf":
        valid = suffix == ".pdf" and header.startswith(b"%PDF-")
    elif declared == "hwp":
        valid = suffix == ".hwp" and header.startswith(
            bytes.fromhex("d0cf11e0a1b11ae1")
        )
    elif declared == "hwpx":
        valid = suffix == ".hwpx" and zipfile.is_zipfile(path)
        if valid:
            try:
                with zipfile.ZipFile(path) as archive:
                    names = set(archive.namelist())
                valid = "[Content_Types].xml" in names and any(
                    name.startswith("Contents/") for name in names
                )
            except (OSError, zipfile.BadZipFile):
                valid = False
    elif declared == "image":
        suffix_valid = suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".tif",
            ".tiff",
            ".bmp",
            ".webp",
        }
        magic_valid = (
            header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith(b"\xff\xd8\xff")
            or header.startswith((b"II*\x00", b"MM\x00*"))
            or header.startswith(b"BM")
            or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
        )
        valid = suffix_valid and magic_valid
    if not valid:
        raise CorpusError(
            f"{label}.source.format={declared!r} does not match the file suffix and content signature"
        )


def _corpus_fingerprint(corpus_id: str, cases: Sequence[Any]) -> str:
    """Identify the approved case set independently of result observations."""
    normalized_cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        case = _mapping(raw_case, f"manifest.cases[{index}]")
        source = case.get("source")
        source_sha256 = None
        if source is not None:
            source_sha256 = _mapping(source, f"manifest.cases[{index}].source").get("sha256")
        ground_truth = case.get("ground_truth")
        ground_truth_identity = None
        if ground_truth is not None:
            ground_truth_mapping = _mapping(
                ground_truth, f"manifest.cases[{index}].ground_truth"
            )
            ground_truth_identity = {
                "status": ground_truth_mapping.get("status"),
                "annotation_revision": ground_truth_mapping.get("annotation_revision"),
                "expected_sha256": ground_truth_mapping.get("expected_sha256"),
                "allow_empty_document": ground_truth_mapping.get(
                    "allow_empty_document", False
                ),
            }
        normalized_cases.append(
            {
                "id": case.get("id"),
                "expected": case.get("expected"),
                "source_sha256": source_sha256,
                "ground_truth": ground_truth_identity,
            }
        )
    normalized_cases.sort(key=lambda item: str(item.get("id") or ""))
    payload = json.dumps(
        {"corpus_id": corpus_id, "cases": normalized_cases},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_manifest_readiness(
    manifest: Any,
    *,
    corpus_root: Path,
    minimum_cases: int | None = None,
    require_approved: bool = True,
    verify_sources: bool = True,
    enforce_code_policy: bool = True,
) -> dict[str, Any]:
    """Validate release-corpus inputs without executing or exposing documents."""

    root = _mapping(manifest, "manifest")
    schema_version = _nonnegative_int(
        _first(root, "schemaVersion", "schema_version"), "manifest.schema_version"
    )
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise CorpusError(
            f"unsupported schema_version {schema_version}; expected {SUPPORTED_SCHEMA_VERSION}"
        )
    corpus_id = _nonempty_string(
        _first(root, "corpusId", "corpus_id"), "manifest.corpus_id"
    )
    cases = _sequence(root.get("cases"), "manifest.cases")
    coverage_raw = _first(root, "coverageRequirements", "coverage_requirements") or {}
    coverage = _mapping(coverage_raw, "manifest.coverage_requirements")
    _reject_unknown(
        coverage,
        {"minimumCases", "minimum_cases", "requiredFormats", "required_formats", "requiredSubjects", "required_subjects", "requiredTags", "required_tags"},
        "manifest.coverage_requirements",
    )
    configured_minimum_raw = _first(coverage, "minimumCases", "minimum_cases")
    configured_minimum = (
        _positive_int(configured_minimum_raw, "manifest.coverage_requirements.minimum_cases")
        if configured_minimum_raw is not None
        else PRODUCTION_MINIMUM_CASES
    )
    effective_minimum = max(
        configured_minimum,
        minimum_cases or 0,
        PRODUCTION_MINIMUM_CASES if enforce_code_policy else 0,
    )
    required_formats = _string_set(
        _first(coverage, "requiredFormats", "required_formats") or [],
        "manifest.coverage_requirements.required_formats",
    )
    required_subjects = _string_set(
        _first(coverage, "requiredSubjects", "required_subjects") or [],
        "manifest.coverage_requirements.required_subjects",
    )
    required_tags = _string_set(
        _first(coverage, "requiredTags", "required_tags") or [],
        "manifest.coverage_requirements.required_tags",
    )
    if enforce_code_policy:
        required_formats.update(PRODUCTION_REQUIRED_FORMATS)
        required_subjects.update(PRODUCTION_REQUIRED_SUBJECTS)
        required_tags.update(PRODUCTION_REQUIRED_TAGS)
    unsupported_formats = sorted(required_formats - SUPPORTED_SOURCE_FORMATS)
    if unsupported_formats:
        raise CorpusError(
            "manifest.coverage_requirements.required_formats contains unsupported "
            f"format(s): {', '.join(unsupported_formats)}"
        )

    errors: list[str] = []
    warnings: list[str] = []
    if len(cases) < effective_minimum:
        errors.append(
            f"corpus has {len(cases)} cases; release evidence requires at least {effective_minimum}"
        )

    try:
        case_thresholds = _validate_thresholds(
            _first(root, "caseThresholds", "case_thresholds"),
            CASE_THRESHOLD_METRICS,
            "manifest.case_thresholds",
        )
        missing_case_thresholds = sorted(
            REQUIRED_RELEASE_CASE_THRESHOLDS - set(case_thresholds)
        )
        if missing_case_thresholds:
            errors.append(
                "case_thresholds is missing release rule(s): "
                + ", ".join(missing_case_thresholds)
            )
        if enforce_code_policy:
            errors.extend(_release_policy_errors(
                case_thresholds,
                RELEASE_CASE_POLICY,
                CASE_THRESHOLD_METRICS,
                label="manifest.case_thresholds",
            ))
    except CorpusError as exc:
        errors.append(str(exc))
    try:
        aggregate_thresholds = _validate_thresholds(
            _first(root, "aggregateThresholds", "aggregate_thresholds"),
            AGGREGATE_THRESHOLD_METRICS,
            "manifest.aggregate_thresholds",
        )
        missing_aggregate_thresholds = sorted(
            REQUIRED_RELEASE_AGGREGATE_THRESHOLDS - set(aggregate_thresholds)
        )
        if missing_aggregate_thresholds:
            errors.append(
                "aggregate_thresholds is missing release rule(s): "
                + ", ".join(missing_aggregate_thresholds)
            )
        if enforce_code_policy:
            errors.extend(_release_policy_errors(
                aggregate_thresholds,
                RELEASE_AGGREGATE_POLICY,
                AGGREGATE_THRESHOLD_METRICS,
                label="manifest.aggregate_thresholds",
            ))
    except CorpusError as exc:
        errors.append(str(exc))

    seen_ids: set[str] = set()
    approved_count = 0
    digest_verified_count = 0
    observed_formats: set[str] = set()
    observed_subjects: set[str] = set()
    observed_tags: set[str] = set()
    observed_source_digests: set[str] = set()
    for index, raw_case in enumerate(cases):
        case_label = f"manifest.cases[{index}]"
        try:
            case = _mapping(raw_case, case_label)
            case_id = _nonempty_string(case.get("id"), f"{case_label}.id")
            if case_id in seen_ids:
                raise CorpusError(f"duplicate case id: {case_id}")
            seen_ids.add(case_id)
            tags = _string_set(case.get("tags") or [], f"{case_label}.tags")
            observed_tags.update(tags)
            source = _mapping(case.get("source"), f"{case_label}.source")
            source_format = _nonempty_string(
                source.get("format"), f"{case_label}.source.format"
            ).lower()
            if source_format not in SUPPORTED_SOURCE_FORMATS - {"synthetic"}:
                raise CorpusError(
                    f"{case_label}.source.format must be a real supported document format"
                )
            observed_formats.add(source_format)
            subject = _nonempty_string(
                source.get("subject"), f"{case_label}.source.subject"
            ).lower()
            if subject == "unknown":
                raise CorpusError(f"{case_label}.source.subject must be classified")
            observed_subjects.add(subject)
            sha256 = _nonempty_string(
                source.get("sha256"), f"{case_label}.source.sha256"
            )
            if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
                raise CorpusError(f"{case_label}.source.sha256 must be a lowercase SHA-256")
            observed_source_digests.add(sha256)
            _nonempty_string(source.get("path"), f"{case_label}.source.path")
            if enforce_code_policy:
                _verify_declared_source_format(source, corpus_root, case_label)
            if verify_sources:
                if not _verify_source_digest(source, corpus_root, case_label):
                    raise CorpusError(f"{case_label}.source.sha256 is required")
                digest_verified_count += 1
            expected = _mapping(case.get("expected"), f"{case_label}.expected")
            expected_fingerprint(expected, f"{case_label}.expected")
            expected_questions = set(
                _normalize_question_numbers(
                    _first(expected, "questionNumbers", "question_numbers"),
                    f"{case_label}.expected.question_numbers",
                )
            )
            expected_signatures_raw = _first(
                expected, "problemSignatures", "problem_signatures"
            )
            if expected_signatures_raw is None:
                raise CorpusError(
                    f"{case_label}.expected.problem_signatures is required for release evidence"
                )
            expected_signatures = [
                _normalize_problem_signature(
                    item, f"{case_label}.expected.problem_signatures[{signature_index}]"
                )
                for signature_index, item in enumerate(
                    _sequence(
                        expected_signatures_raw,
                        f"{case_label}.expected.problem_signatures",
                    )
                )
            ]
            signature_numbers = [item.number for item in expected_signatures]
            if len(set(signature_numbers)) != len(signature_numbers):
                raise CorpusError(
                    f"{case_label}.expected.problem_signatures contains duplicate numbers"
                )
            if set(signature_numbers) != expected_questions:
                raise CorpusError(
                    f"{case_label}.expected.problem_signatures must cover every expected question exactly once"
                )
            if any(
                not item.artifact_valid or item.artifact_size_bytes <= 0
                for item in expected_signatures
            ):
                raise CorpusError(
                    f"{case_label}.expected.problem_signatures contains an invalid artifact"
                )
            _, approved = _validate_ground_truth(
                case.get("ground_truth"),
                expected=expected,
                label=case_label,
                require_approved=require_approved,
            )
            approved_count += int(approved)
        except CorpusError as exc:
            errors.append(str(exc))

    missing_formats = sorted(required_formats - observed_formats)
    missing_subjects = sorted(required_subjects - observed_subjects)
    missing_tags = sorted(required_tags - observed_tags)
    if missing_formats:
        errors.append("coverage is missing format(s): " + ", ".join(missing_formats))
    if missing_subjects:
        errors.append("coverage is missing subject(s): " + ", ".join(missing_subjects))
    if missing_tags:
        errors.append("coverage is missing tag(s): " + ", ".join(missing_tags))
    if len(observed_source_digests) < effective_minimum:
        errors.append(
            f"corpus has only {len(observed_source_digests)} unique source digests; "
            f"release evidence requires {effective_minimum} unique documents"
        )
    regression_tolerances = _first(root, "regressionTolerance", "regression_tolerance")
    if not regression_tolerances:
        errors.append("regression_tolerance is required for release evidence")
    else:
        try:
            normalized_regressions = _mapping(
                regression_tolerances, "manifest.regression_tolerance"
            )
            unknown_regressions = sorted(
                set(normalized_regressions) - set(REGRESSION_METRICS)
            )
            if unknown_regressions:
                raise CorpusError(
                    "manifest.regression_tolerance contains unsupported rule(s): "
                    + ", ".join(unknown_regressions)
                )
            missing_regressions = sorted(
                REQUIRED_RELEASE_REGRESSION_TOLERANCES - set(normalized_regressions)
            )
            if missing_regressions:
                errors.append(
                    "regression_tolerance is missing release rule(s): "
                    + ", ".join(missing_regressions)
                )
            for name, value in normalized_regressions.items():
                _nonnegative_number(value, f"manifest.regression_tolerance.{name}")
            if enforce_code_policy:
                errors.extend(_release_policy_errors(
                    {
                        name: _nonnegative_number(
                            value, f"manifest.regression_tolerance.{name}"
                        )
                        for name, value in normalized_regressions.items()
                    },
                    RELEASE_REGRESSION_POLICY,
                    REGRESSION_METRICS,
                    label="manifest.regression_tolerance",
                ))
        except CorpusError as exc:
            errors.append(str(exc))

    fingerprint = None
    try:
        fingerprint = _corpus_fingerprint(corpus_id, cases)
    except CorpusError as exc:
        errors.append(str(exc))
    return {
        "status": "ready" if not errors else "not_ready",
        "corpus_id": corpus_id,
        "corpus_fingerprint": fingerprint,
        "case_count": len(cases),
        "minimum_case_count": effective_minimum,
        "approved_case_count": approved_count,
        "source_digest_verified_count": digest_verified_count,
        "unique_source_digest_count": len(observed_source_digests),
        "coverage": {
            "formats": sorted(observed_formats),
            "subjects": sorted(observed_subjects),
            "tags": sorted(observed_tags),
        },
        "errors": errors,
        "warnings": warnings,
    }


def baseline_report_fingerprint(report: Mapping[str, Any]) -> str:
    """Fingerprint a baseline report while excluding its detachable approval."""

    canonical_report = dict(report)
    canonical_report.pop("baseline_approval", None)
    payload = json.dumps(
        canonical_report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_baseline_approval(
    baseline_report: Mapping[str, Any], *, corpus_fingerprint: str
) -> None:
    approval = _mapping(
        baseline_report.get("baseline_approval"), "baseline.baseline_approval"
    )
    _reject_unknown(
        approval,
        {
            "status",
            "reviewer_id",
            "approved_at",
            "corpus_fingerprint",
            "report_sha256",
        },
        "baseline.baseline_approval",
    )
    if approval.get("status") != "approved":
        raise CorpusError("baseline.baseline_approval.status must be 'approved'")
    _nonempty_string(approval.get("reviewer_id"), "baseline.baseline_approval.reviewer_id")
    _nonempty_string(approval.get("approved_at"), "baseline.baseline_approval.approved_at")
    if approval.get("corpus_fingerprint") != corpus_fingerprint:
        raise CorpusError("baseline approval does not match the current corpus_fingerprint")
    expected_report_digest = baseline_report_fingerprint(baseline_report)
    if approval.get("report_sha256") != expected_report_digest:
        raise CorpusError("baseline approval report_sha256 does not match the baseline report")
    provenance = _mapping(
        baseline_report.get("pipeline_provenance"), "baseline.pipeline_provenance"
    )
    if provenance.get("fresh_pipeline_execution") is not True:
        raise CorpusError("approved baseline must come from a fresh pipeline execution")
    if provenance.get("observation_provenance_verified") is not True:
        raise CorpusError("approved baseline must have verified observation provenance")
    if provenance.get("worktree_clean") is not True:
        raise CorpusError("approved baseline must come from a clean checkout")
    if provenance.get("private_artifacts_retained") is True:
        raise CorpusError("approved baseline must not retain private intermediate artifacts")


def evaluate_corpus(
    manifest: Any,
    *,
    manifest_path: Path,
    corpus_root: Path | None = None,
    baseline_report: Mapping[str, Any] | None = None,
    include_paths: bool = False,
    require_approved_ground_truth: bool = False,
    require_observation_provenance: bool = False,
    expected_observation_provenance: Mapping[str, Any] | None = None,
    require_approved_baseline: bool = False,
) -> dict[str, Any]:
    root = _mapping(manifest, "manifest")
    _reject_unknown(
        root,
        {
            "$schema",
            "schemaVersion",
            "schema_version",
            "corpusId",
            "corpus_id",
            "description",
            "caseThresholds",
            "case_thresholds",
            "aggregateThresholds",
            "aggregate_thresholds",
            "regressionTolerance",
            "regression_tolerance",
            "coverageRequirements",
            "coverage_requirements",
            "cases",
        },
        "manifest",
    )
    schema_version = _nonnegative_int(_first(root, "schemaVersion", "schema_version"), "manifest.schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise CorpusError(
            f"unsupported schema_version {schema_version}; expected {SUPPORTED_SCHEMA_VERSION}"
        )
    corpus_id = _first(root, "corpusId", "corpus_id")
    if not isinstance(corpus_id, str) or not corpus_id.strip():
        raise CorpusError("manifest.corpus_id must be a non-empty string")
    cases = _sequence(root.get("cases"), "manifest.cases")
    if not cases:
        raise CorpusError("manifest.cases must not be empty")
    corpus_fingerprint = _corpus_fingerprint(corpus_id, cases)
    configured_root = corpus_root or manifest_path.parent
    case_thresholds = _validate_thresholds(
        _first(root, "caseThresholds", "case_thresholds"), CASE_THRESHOLD_METRICS, "manifest.case_thresholds"
    )
    aggregate_thresholds = _validate_thresholds(
        _first(root, "aggregateThresholds", "aggregate_thresholds"),
        AGGREGATE_THRESHOLD_METRICS,
        "manifest.aggregate_thresholds",
    )
    if not case_thresholds or not aggregate_thresholds:
        raise CorpusError(
            "case_thresholds and aggregate_thresholds must both be non-empty; "
            "an unbounded observation cannot pass a quality gate"
        )

    seen_ids: set[str] = set()
    case_reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        case = _mapping(raw_case, f"manifest.cases[{index}]")
        _reject_unknown(
            case,
            {"id", "description", "tags", "source", "result", "expected", "ground_truth"},
            f"manifest.cases[{index}]",
        )
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise CorpusError(f"manifest.cases[{index}].id must be a non-empty string")
        if case_id in seen_ids:
            raise CorpusError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        source = case.get("source")
        source_digest_verified = False
        source_sha256 = None
        if source is not None:
            source_mapping = _mapping(source, f"manifest.cases[{index}].source")
            _reject_unknown(
                source_mapping,
                {"path", "sha256", "format", "subject"},
                f"manifest.cases[{index}].source",
            )
            source_digest_verified = _verify_source_digest(
                source_mapping, configured_root, f"manifest.cases[{index}]"
            )
            source_sha256 = source_mapping.get("sha256")
        ground_truth_status, ground_truth_approved = _validate_ground_truth(
            case.get("ground_truth"),
            expected=case.get("expected"),
            label=f"manifest.cases[{index}]",
            require_approved=require_approved_ground_truth,
        )
        result_path = _resolve_result_path(case, configured_root, f"manifest.cases[{index}]")
        raw_observation = _load_json(result_path)
        observation = extract_observation(raw_observation, f"case {case_id} result")
        provenance_verified = _validate_observation_provenance(
            raw_observation,
            source_sha256=source_sha256,
            expected_provenance=expected_observation_provenance,
            label=f"case {case_id} result",
        )
        if require_observation_provenance and not provenance_verified:
            raise CorpusError(f"case {case_id} result is missing measurementProvenance")
        case_report = _evaluate_case(case, observation, f"manifest.cases[{index}]")
        case_report["source_digest_verified"] = source_digest_verified
        case_report["measurement_complete"] = True
        case_report["observation_provenance_verified"] = provenance_verified
        case_report["ground_truth_status"] = ground_truth_status
        case_report["ground_truth_approved"] = ground_truth_approved
        case_failures = _threshold_failures(
            case_report["metrics"],
            case_thresholds,
            CASE_THRESHOLD_METRICS,
            scope="case",
            case_id=case_id,
        )
        case_report["failures"] = case_failures
        case_report["passed"] = not case_failures
        if include_paths:
            case_report["result_path"] = str(result_path)
        failures.extend(case_failures)
        case_reports.append(case_report)

    aggregate = _aggregate(case_reports)
    aggregate["measurement_complete_case_count"] = sum(
        bool(report["measurement_complete"]) for report in case_reports
    )
    aggregate["observation_provenance_verified_case_count"] = sum(
        bool(report["observation_provenance_verified"]) for report in case_reports
    )
    aggregate["ground_truth_approved_case_count"] = sum(
        bool(report["ground_truth_approved"]) for report in case_reports
    )
    aggregate["case_failure_count"] = sum(not report["passed"] for report in case_reports)
    aggregate_failures = _threshold_failures(
        aggregate,
        aggregate_thresholds,
        AGGREGATE_THRESHOLD_METRICS,
        scope="aggregate",
    )
    failures.extend(aggregate_failures)

    tolerances = _first(root, "regressionTolerance", "regression_tolerance") or {}
    if baseline_report is not None:
        baseline_corpus_id = baseline_report.get("corpus_id")
        if baseline_corpus_id != corpus_id:
            raise CorpusError(
                f"baseline corpus_id {baseline_corpus_id!r} does not match manifest corpus_id {corpus_id!r}"
            )
        if baseline_report.get("status") != "passed":
            raise CorpusError("baseline report must have status='passed'")
        baseline_fingerprint = baseline_report.get("corpus_fingerprint")
        if baseline_fingerprint != corpus_fingerprint:
            raise CorpusError(
                "baseline corpus_fingerprint does not match the current case ids, expected values, and source digests"
            )
        if require_approved_baseline:
            validate_baseline_approval(
                baseline_report, corpus_fingerprint=corpus_fingerprint
            )
        failures.extend(_baseline_failures(aggregate, baseline_report, _mapping(tolerances, "regression_tolerance")))
    elif tolerances:
        raise CorpusError("regression_tolerance requires --baseline")

    report = {
        "report_schema_version": 1,
        "corpus_id": corpus_id,
        "corpus_fingerprint": corpus_fingerprint,
        "status": "passed" if not failures else "failed",
        "aggregate": aggregate,
        "failures": failures,
        "cases": case_reports,
    }
    if include_paths:
        report["manifest_path"] = str(manifest_path)
        report["corpus_root"] = str(configured_root)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    aggregate = report["aggregate"]
    status = "PASS" if report["status"] == "passed" else "FAIL"
    lines = [
        f"# Quality gate: {report['corpus_id']}",
        "",
        f"**{status}** — {aggregate['case_count']} cases, {len(report['failures'])} gate failures",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Question recall / precision | {aggregate['question_recall']:.3f} / {aggregate['question_precision']:.3f} |",
        f"| Missing / duplicate / extra questions | {aggregate['missing_question_count']} / {aggregate['duplicate_question_count']} / {aggregate['extra_question_count']} |",
        f"| Passage-range recall / precision | {aggregate['passage_range_recall']:.3f} / {aggregate['passage_range_precision']:.3f} |",
        f"| Preflight issues | {aggregate['preflight_issue_count']} |",
        f"| Manual-review rate | {aggregate['manual_review_rate']:.1%} |",
        f"| Processing p50 / p95 | {aggregate['processing_ms_p50']:.0f} ms / {aggregate['processing_ms_p95']:.0f} ms |",
        f"| Structural signature mismatches / invalid artifacts | {aggregate['problem_signature_mismatch_count']} / {aggregate['artifact_invalid_count']} |",
        f"| Complete measurements | {aggregate['measurement_complete_case_count']} / {aggregate['case_count']} cases |",
        f"| Verified ground truth | {aggregate['ground_truth_approved_case_count']} / {aggregate['case_count']} cases |",
        f"| Verified run provenance | {aggregate['observation_provenance_verified_case_count']} / {aggregate['case_count']} cases |",
        "",
    ]
    if report["failures"]:
        lines.extend(["## Failures", ""])
        for failure in report["failures"]:
            case_suffix = f" ({failure['case_id']})" if failure.get("case_id") else ""
            lines.append(
                f"- `{failure['metric']}`{case_suffix}: actual {failure['actual']}, "
                f"limit {failure['limit']} via `{failure['rule']}`"
            )
        lines.append("")
    return "\n".join(lines)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _print_console_safe(content: str, *, stream: Any | None = None) -> None:
    """Print without letting a legacy Windows console encoding fail the gate."""

    output = stream if stream is not None else sys.stdout
    encoding = getattr(output, "encoding", None) or "utf-8"
    try:
        printable = content.encode(encoding, errors="backslashreplace").decode(encoding)
    except LookupError:
        printable = content
    print(printable, file=output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate an EDB quality corpus. Exit 0=pass, 1=gate failure, 2=invalid input."
    )
    parser.add_argument("manifest", type=Path, help="Corpus manifest JSON")
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=None,
        help="Root for result paths (default: EDB_QUALITY_CORPUS_ROOT or manifest directory)",
    )
    parser.add_argument("--baseline", type=Path, help="Prior JSON report used for regression checks")
    parser.add_argument("--json-report", type=Path, help="Write the machine-readable report")
    parser.add_argument("--markdown-report", type=Path, help="Write a human-readable report")
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="Include private manifest/result paths in reports (off by default)",
    )
    args = parser.parse_args(argv)

    corpus_root = args.corpus_root
    if corpus_root is None and os.environ.get("EDB_QUALITY_CORPUS_ROOT"):
        corpus_root = Path(os.environ["EDB_QUALITY_CORPUS_ROOT"])
    try:
        manifest = _load_json(args.manifest)
        baseline = _mapping(_load_json(args.baseline), "baseline") if args.baseline else None
        report = evaluate_corpus(
            manifest,
            manifest_path=args.manifest,
            corpus_root=corpus_root,
            baseline_report=baseline,
            include_paths=args.include_paths,
        )
        json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.json_report:
            _write_text(args.json_report, json_text)
        if args.markdown_report:
            _write_text(args.markdown_report, render_markdown(report))
        _print_console_safe(render_markdown(report))
        return EXIT_OK if report["status"] == "passed" else EXIT_GATE_FAILED
    except (CorpusError, OSError) as exc:
        _print_console_safe(f"[quality-gate] INVALID: {exc}", stream=sys.stderr)
        return EXIT_INVALID_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
