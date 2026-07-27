#!/usr/bin/env python3
"""Score real KICE Korean passage recognition and cross-page merging.

The gate is intentionally stricter than image similarity alone.  It derives
cross-page ground truth from the PDF text layer, verifies that every unmarked
continuation was recovered, audits character clipping and right margin, checks
fragment order/overlap, and runs the tallest passage through the production S2
dense-text visual path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.work3_passage_merge_audit import render_actual_s2_preview


RANGE_RE = re.compile(r"\[(?P<start>\d{1,2})\s*[～~\-]\s*(?P<end>\d{1,2})\]")


def _question_re(number: int) -> re.Pattern[str]:
    return re.compile(rf"(?m)^\s*{number}\.\s")


def _page_text_lines(page: fitz.Page) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(str(span.get("text") or "") for span in line.get("spans", []))
            bbox = line.get("bbox")
            if text.strip() and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                lines.append({"text": text.strip(), "bbox": tuple(float(value) for value in bbox)})
    return lines


def _has_substantive_pre_question_text(page: fitz.Page, problem_number: int) -> bool:
    lines = _page_text_lines(page)
    marker = next(
        (line for line in lines if _question_re(problem_number).match(line["text"])),
        None,
    )
    if marker is None:
        return False
    marker_box = marker["bbox"]
    midpoint = page.rect.width * 0.5
    marker_in_left_column = (marker_box[0] + marker_box[2]) * 0.5 < midpoint
    candidates: list[str] = []
    for line in lines:
        box = line["bbox"]
        center_x = (box[0] + box[2]) * 0.5
        if marker_in_left_column:
            before_marker = center_x < midpoint and box[1] < marker_box[1]
        else:
            before_marker = center_x < midpoint or (center_x >= midpoint and box[1] < marker_box[1])
        if not before_marker:
            continue
        compact = re.sub(r"\s+", "", line["text"])
        if box[1] <= page.rect.height * 0.12 and (
            compact in {"국어", "영어", "화법과작문", "언어와매체"}
            or re.fullmatch(r"\d{1,2}", compact)
            or any(token in compact for token in ("영역", "학년도", "문제지"))
        ):
            continue
        if box[1] >= page.rect.height * 0.82 and (
            re.fullmatch(r"\d{1,3}", compact)
            or any(token in compact for token in ("저작권", "확인사항", "답안지"))
        ):
            continue
        if compact:
            candidates.append(compact)
    return len(candidates) >= 2 and sum(len(value) for value in candidates) >= 16


def derive_cross_page_ground_truth(source: Path) -> list[dict[str, Any]]:
    with fitz.open(source) as document:
        page_texts = [page.get_text("text") for page in document]
        expected: list[dict[str, Any]] = []
        for marker_index, text in enumerate(page_texts):
            for match in RANGE_RE.finditer(text):
                start = int(match.group("start"))
                end = int(match.group("end"))
                first_question_index = next(
                    (
                        page_index
                        for page_index in range(marker_index, len(page_texts))
                        if _question_re(start).search(page_texts[page_index])
                    ),
                    None,
                )
                if first_question_index is None or first_question_index <= marker_index:
                    continue
                if not _has_substantive_pre_question_text(document[first_question_index], start):
                    continue
                expected.append(
                    {
                        "label": f"{start}-{end}",
                        "start": start,
                        "end": end,
                        "marker_page": marker_index + 1,
                        "continuation_page": first_question_index + 1,
                    }
                )
    return expected


def _detected_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fragments_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fragment in payload.get("fragments") or []:
        fragments_by_group[str(fragment.get("group_id") or "")].append(fragment)
    rows: list[dict[str, Any]] = []
    for group in payload.get("groups") or []:
        group_id = str(group.get("group_id") or "")
        fragments = fragments_by_group.get(group_id, [])
        rows.append(
            {
                "group_id": group_id,
                "label": str(group.get("label") or ""),
                "pages": sorted({int(item["page_number"]) for item in fragments}),
                "fragments": fragments,
                "has_inferred_continuation": any(
                    bool(item.get("cross_page_passage_inferred")) for item in fragments
                ),
            }
        )
    return rows


def _fragment_integrity(groups: Sequence[dict[str, Any]]) -> dict[str, Any]:
    duplicate_count = 0
    out_of_order_count = 0
    overlap_count = 0
    for group in groups:
        fragments = list(group["fragments"])
        keys = [
            (
                int(fragment["page_number"]),
                int(fragment["fragment_index"]),
                tuple(float(value) for value in fragment["clip_points"]),
            )
            for fragment in fragments
        ]
        duplicate_count += len(keys) - len(set(keys))
        ordered = sorted(
            fragments,
            key=lambda fragment: (
                int(fragment["page_number"]),
                int(fragment["fragment_index"]),
            ),
        )
        page_numbers = [int(fragment["page_number"]) for fragment in ordered]
        if page_numbers != sorted(page_numbers):
            out_of_order_count += 1
        by_page: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        for fragment in fragments:
            by_page[int(fragment["page_number"])].append(
                tuple(float(value) for value in fragment["clip_points"])
            )
        for boxes in by_page.values():
            for index, first in enumerate(boxes):
                for second in boxes[index + 1 :]:
                    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
                    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
                    if width * height > 4.0:
                        overlap_count += 1
    return {
        "duplicate_fragment_count": duplicate_count,
        "out_of_order_group_count": out_of_order_count,
        "overlap_count": overlap_count,
        "pass": duplicate_count == 0 and out_of_order_count == 0 and overlap_count == 0,
    }


def score_benchmark(benchmark_path: Path, output_dir: Path) -> dict[str, Any]:
    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    source = Path(str(payload["source"]))
    ground_truth = derive_cross_page_ground_truth(source)
    groups = _detected_groups(payload)

    matched_group_ids: set[str] = set()
    expected_rows: list[dict[str, Any]] = []
    for expected in ground_truth:
        candidates = [
            group
            for group in groups
            if group["label"] == expected["label"]
            and expected["marker_page"] in group["pages"]
            and expected["continuation_page"] in group["pages"]
            and group["has_inferred_continuation"]
        ]
        matched = candidates[0] if candidates else None
        if matched is not None:
            matched_group_ids.add(matched["group_id"])
        expected_rows.append(
            {
                **expected,
                "matched": matched is not None,
                "detected_group_id": matched["group_id"] if matched else None,
            }
        )

    inferred_groups = [group for group in groups if group["has_inferred_continuation"]]
    coverage = len(matched_group_ids) / max(1, len(ground_truth))
    precision = len(matched_group_ids) / max(1, len(inferred_groups))
    quality = payload.get("quality_summary") or {}
    min_char_recall = float(quality.get("minimum_char_bbox_recall") or 0.0)
    clipped_chars = int(quality.get("clipped_char_bbox_count") or 0)
    min_ink_f1 = float(quality.get("minimum_ink_f1") or 0.0)
    min_margin = float(quality.get("minimum_horizontal_char_margin_px") or 0.0)
    page_chrome_fragments = int(quality.get("page_chrome_fragment_count") or 0)
    integrity = _fragment_integrity(groups)

    preview_path = output_dir / f"{source.stem}-s2.png"
    s2 = render_actual_s2_preview(benchmark_path, preview_path)
    s2_checks = (s2 or {}).get("quality_checks") or {}
    s2_ratio = sum(bool(value) for value in s2_checks.values()) / max(1, len(s2_checks))

    components = {
        "cross_page_recall": round(40.0 * coverage, 3),
        "cross_page_precision": round(10.0 * precision, 3),
        "character_completeness": round(
            max(0.0, 20.0 * min_char_recall - min(20.0, clipped_chars * 2.0)),
            3,
        ),
        "visual_fidelity": round(10.0 * min(1.0, min_ink_f1 / 0.999), 3),
        "right_margin_safety": round(5.0 * min(1.0, min_margin / 24.0), 3),
        "s2_visual_cleanliness": round(10.0 * s2_ratio, 3),
        "fragment_integrity": 5.0 if integrity["pass"] and page_chrome_fragments == 0 else 0.0,
    }
    score = round(sum(components.values()), 3)
    strict_pass = bool(
        score >= 94.0
        and coverage == 1.0
        and precision == 1.0
        and clipped_chars == 0
        and page_chrome_fragments == 0
        and integrity["pass"]
        and (s2 or {}).get("pass")
    )
    return {
        "source": str(source),
        "benchmark": str(benchmark_path),
        "score": score,
        "pass_94": strict_pass,
        "components": components,
        "cross_page_ground_truth_count": len(ground_truth),
        "cross_page_matched_count": len(matched_group_ids),
        "inferred_group_count": len(inferred_groups),
        "cross_page_coverage": round(coverage, 6),
        "cross_page_precision": round(precision, 6),
        "expected_cross_page_ranges": expected_rows,
        "quality_summary": quality,
        "fragment_integrity": integrity,
        "page_chrome_fragment_count": page_chrome_fragments,
        "s2": s2,
    }


def audit_corpus(benchmark_paths: Sequence[Path], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = [score_benchmark(path, output_dir) for path in benchmark_paths]
    average = round(sum(item["score"] for item in documents) / max(1, len(documents)), 3)
    minimum = round(min((item["score"] for item in documents), default=0.0), 3)
    result = {
        "schema_version": 1,
        "experiment": "work3-real-kice-korean-corpus",
        "document_count": len(documents),
        "expected_cross_page_range_count": sum(
            int(item["cross_page_ground_truth_count"]) for item in documents
        ),
        "matched_cross_page_range_count": sum(
            int(item["cross_page_matched_count"]) for item in documents
        ),
        "average_score": average,
        "minimum_score": minimum,
        "gate": {
            "target": 94.0,
            "average_pass": average >= 94.0,
            "minimum_pass": minimum >= 94.0,
            "all_documents_pass": bool(documents) and all(item["pass_94"] for item in documents),
            "all_cross_page_ranges_recovered": bool(documents)
            and all(item["cross_page_coverage"] == 1.0 for item in documents),
        },
        "documents": documents,
    }
    result["pass"] = bool(result["gate"]) and all(result["gate"].values())
    (output_dir / "corpus-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Work 3 실제 평가원 국어 지문 엄격 평가",
        "",
        f"- 문서: {len(documents)}개",
        f"- 교차 페이지 지문: {result['matched_cross_page_range_count']}/{result['expected_cross_page_range_count']}",
        f"- 평균 점수: {average:.1f}/100",
        f"- 최저 점수: {minimum:.1f}/100",
        f"- 94점 게이트: {'PASS' if result['pass'] else 'FAIL'}",
        "",
        "| 문서 | 점수 | 교차 페이지 | 잘린 글자 | S2 |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in documents:
        quality = item["quality_summary"]
        lines.append(
            "| {name} | {score:.1f} | {matched}/{expected} | {clipped} | {s2} |".format(
                name=Path(item["source"]).name,
                score=item["score"],
                matched=item["cross_page_matched_count"],
                expected=item["cross_page_ground_truth_count"],
                clipped=quality.get("clipped_char_bbox_count"),
                s2="PASS" if (item.get("s2") or {}).get("pass") else "FAIL",
            )
        )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmarks", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = audit_corpus(args.benchmarks, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
