#!/usr/bin/env python3
"""Strictly score Korean passage detection, stitching, and crop isolation."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.work3_korean_corpus_audit import (  # noqa: E402
    _detected_groups,
    _fragment_integrity,
    derive_cross_page_ground_truth,
)


MIN_OUTER_GUIDE_CLEANUP_INK_RECALL = 0.95


def _normalized_filename(value: str | Path) -> str:
    return unicodedata.normalize("NFC", Path(value).name)


def _load_manifest(path: Path) -> tuple[dict[str, Mapping[str, Any]], float, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents") or []
    by_name = {
        _normalized_filename(item["filename"]): item
        for item in documents
        if isinstance(item, Mapping) and item.get("filename")
    }
    return (
        by_name,
        float(payload.get("targetAverageScore") or 96.0),
        float(payload.get("targetMinimumScore") or 93.5),
    )


def _matched_cross_page_count(
    expected: Sequence[Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
) -> int:
    return sum(
        any(
            group["label"] == item["label"]
            and int(item["marker_page"]) in group["pages"]
            and int(item["continuation_page"]) in group["pages"]
            and bool(group["has_inferred_continuation"])
            for group in groups
        )
        for item in expected
    )


def score_document(benchmark_path: Path, annotation: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    source = Path(str(payload["source"]))
    groups = _detected_groups(payload)
    labels = [str(group["label"]) for group in groups]
    label_counts = Counter(labels)
    detected = set(labels)
    expected = {str(value) for value in annotation.get("expectedRanges") or []}
    forbidden = {str(value) for value in annotation.get("forbiddenRanges") or []}
    missing = sorted(expected - detected)
    extra = sorted(detected - expected)
    forbidden_detected = sorted(detected & forbidden)
    duplicate_labels = sorted(label for label, count in label_counts.items() if count > 1)
    matched = len(expected & detected)
    recall = matched / max(1, len(expected))
    precision = matched / max(1, len(labels))

    cross_page_expected = derive_cross_page_ground_truth(source)
    cross_page_matched = _matched_cross_page_count(cross_page_expected, groups)
    cross_page_recall = cross_page_matched / max(1, len(cross_page_expected))
    if not cross_page_expected:
        cross_page_recall = 1.0

    quality = payload.get("quality_summary") or {}
    minimum_char_recall = float(quality.get("minimum_char_bbox_recall") or 0.0)
    clipped_chars = int(quality.get("clipped_char_bbox_count") or 0)
    divider_checked = int(quality.get("center_divider_checked_fragment_count") or 0)
    divider_violations = int(quality.get("center_divider_violation_count") or 0)
    fragment_count = int(payload.get("passage_fragment_count") or 0)
    divider_ratio = (
        max(0.0, 1.0 - divider_violations / divider_checked)
        if divider_checked > 0 and divider_checked == fragment_count
        else 0.0
    )
    page_chrome = int(quality.get("page_chrome_fragment_count") or 0)
    marker_intrusions = int(quality.get("problem_marker_intrusion_count") or 0)
    guide_cleanup_recall = float(
        quality.get("minimum_outer_guide_cleanup_ink_recall") or 0.0
    )
    guide_cleanup_safe = guide_cleanup_recall >= MIN_OUTER_GUIDE_CLEANUP_INK_RECALL
    integrity = _fragment_integrity(groups)

    components = {
        "range_recall": round(30.0 * recall, 3),
        "range_precision": round(20.0 * precision, 3),
        "cross_page_recovery": round(15.0 * cross_page_recall, 3),
        "character_completeness": round(
            max(0.0, 15.0 * minimum_char_recall - min(5.0, clipped_chars * 0.2)),
            3,
        ),
        "center_divider_exclusion": round(15.0 * divider_ratio, 3),
        "artifact_cleanliness": round(
            5.0
            * (
                int(page_chrome == 0)
                + int(bool(integrity.get("pass")))
                + int(guide_cleanup_safe)
            )
            / 3.0,
            3,
        ),
    }
    score = round(sum(components.values()), 3)
    strict_pass = bool(
        score >= 93.5
        and not missing
        and not extra
        and not forbidden_detected
        and not duplicate_labels
        and cross_page_recall == 1.0
        and clipped_chars == 0
        and divider_checked == fragment_count
        and divider_violations == 0
        and page_chrome == 0
        and marker_intrusions == 0
        and guide_cleanup_safe
        and integrity.get("pass")
    )
    return {
        "source": str(source),
        "benchmark": str(benchmark_path),
        "score": score,
        "pass": strict_pass,
        "components": components,
        "expected_ranges": sorted(expected),
        "detected_ranges": sorted(detected),
        "missing_ranges": missing,
        "extra_ranges": extra,
        "forbidden_ranges_detected": forbidden_detected,
        "duplicate_range_labels": duplicate_labels,
        "range_recall": round(recall, 6),
        "range_precision": round(precision, 6),
        "cross_page_expected_count": len(cross_page_expected),
        "cross_page_matched_count": cross_page_matched,
        "cross_page_recall": round(cross_page_recall, 6),
        "minimum_char_bbox_recall": minimum_char_recall,
        "clipped_char_bbox_count": clipped_chars,
        "center_divider_checked_fragment_count": divider_checked,
        "center_divider_violation_count": divider_violations,
        "page_chrome_fragment_count": page_chrome,
        "problem_marker_intrusion_count": marker_intrusions,
        "minimum_outer_guide_cleanup_ink_recall": guide_cleanup_recall,
        "fragment_integrity": integrity,
    }


def evaluate(
    benchmark_paths: Sequence[Path],
    *,
    manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    annotations, target_average, target_minimum = _load_manifest(manifest_path)
    documents: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for benchmark_path in benchmark_paths:
        payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
        name = _normalized_filename(str(payload["source"]))
        annotation = annotations.get(name)
        if annotation is None:
            raise ValueError(f"no annotation for benchmark source: {name}")
        if name in seen_names:
            raise ValueError(f"duplicate benchmark source: {name}")
        seen_names.add(name)
        documents.append(score_document(benchmark_path, annotation))
    missing_documents = sorted(set(annotations) - seen_names)
    average = round(sum(item["score"] for item in documents) / max(1, len(documents)), 3)
    minimum = round(min((item["score"] for item in documents), default=0.0), 3)
    result = {
        "schema_version": 1,
        "experiment": "strict-korean-passage-corpus",
        "manifest": str(manifest_path.resolve()),
        "document_count": len(documents),
        "missing_documents": missing_documents,
        "average_score": average,
        "minimum_score": minimum,
        "target_average_score": target_average,
        "target_minimum_score": target_minimum,
        "false_positive_range_count": sum(len(item["extra_ranges"]) for item in documents),
        "false_negative_range_count": sum(len(item["missing_ranges"]) for item in documents),
        "forbidden_range_detection_count": sum(
            len(item["forbidden_ranges_detected"]) for item in documents
        ),
        "center_divider_violation_count": sum(
            int(item["center_divider_violation_count"]) for item in documents
        ),
        "clipped_char_bbox_count": sum(
            int(item["clipped_char_bbox_count"]) for item in documents
        ),
        "problem_marker_intrusion_count": sum(
            int(item["problem_marker_intrusion_count"]) for item in documents
        ),
        "minimum_outer_guide_cleanup_ink_recall": min(
            (
                float(item["minimum_outer_guide_cleanup_ink_recall"])
                for item in documents
            ),
            default=0.0,
        ),
        "gate": {
            "average_pass": average >= target_average,
            "minimum_pass": minimum >= target_minimum,
            "all_documents_pass": bool(documents) and all(item["pass"] for item in documents),
            "all_manifest_documents_present": not missing_documents,
            "zero_false_positives": all(not item["extra_ranges"] for item in documents),
            "zero_center_divider_violations": all(
                int(item["center_divider_violation_count"]) == 0 for item in documents
            ),
            "zero_problem_marker_intrusions": all(
                int(item["problem_marker_intrusion_count"]) == 0 for item in documents
            ),
            "safe_outer_guide_cleanup": all(
                float(item["minimum_outer_guide_cleanup_ink_recall"])
                >= MIN_OUTER_GUIDE_CLEANUP_INK_RECALL
                for item in documents
            ),
        },
        "documents": documents,
    }
    result["pass"] = bool(result["gate"]) and all(result["gate"].values())

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "korean-passage-corpus-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 국어 지문 코퍼스 엄격 평가",
        "",
        f"- 문서: {len(documents)}개",
        f"- 평균: {average:.1f}/100 (목표 {target_average:.1f})",
        f"- 최저: {minimum:.1f}/100 (목표 {target_minimum:.1f})",
        f"- 비지문 오탐: {result['false_positive_range_count']}건",
        f"- 중앙 분할선 침범: {result['center_divider_violation_count']}건",
        f"- 판정: {'PASS' if result['pass'] else 'FAIL'}",
        "",
        "| 문서 | 점수 | 범위 정밀도/재현율 | 교차 페이지 | 잘린 글자 | 중앙선 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in documents:
        lines.append(
            "| {name} | {score:.1f} | {precision:.1%}/{recall:.1%} | {matched}/{expected} | {clipped} | {divider} |".format(
                name=Path(item["source"]).name,
                score=item["score"],
                precision=item["range_precision"],
                recall=item["range_recall"],
                matched=item["cross_page_matched_count"],
                expected=item["cross_page_expected_count"],
                clipped=item["clipped_char_bbox_count"],
                divider=item["center_divider_violation_count"],
            )
        )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmarks", nargs="+", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT / "quality" / "korean-passage-corpus.json",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(
        args.benchmarks,
        manifest_path=args.manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
