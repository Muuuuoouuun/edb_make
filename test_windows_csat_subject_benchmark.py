from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from scripts.evaluate_quality_corpus import Observation, ProblemSignature
from scripts.run_windows_csat_subject_benchmark import (
    PipelineResult,
    build_report,
    score_case,
)


DIMENSION_GATE = {
    "input_page_recall_min": 1.0,
    "whole_page_recall_min": 1.0,
    "source_resolution_valid_rate_min": 1.0,
    "problem_resolution_valid_rate_min": 1.0,
    "whole_resolution_valid_rate_min": 1.0,
    "completion_rate_min": 1.0,
    "within_time_budget_required": True,
}


def _image(path: Path, width: int, height: int) -> str:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, width - 10, height - 10), outline="black", width=8)
    image.save(path)
    return path.resolve().as_uri()


def _signature(number: int) -> ProblemSignature:
    digest = f"{number:064x}"
    return ProblemSignature(
        number=number,
        source_page_id=digest,
        bbox_sha256=digest,
        crop_sha256=digest,
        render_sha256=digest,
        visual_sha256=digest,
        content_sha256=digest,
        choice_count=0,
        choice_order=(),
        artifact_valid=True,
        artifact_size_bytes=1024,
    )


def _fixture(tmp_path: Path):
    page_1 = _image(tmp_path / "page-1.png", 1600, 2100)
    page_2 = _image(tmp_path / "page-2.png", 1600, 2100)
    crop_1 = _image(tmp_path / "crop-1.png", 400, 200)
    crop_2 = _image(tmp_path / "crop-2.png", 400, 200)
    problem_edb = tmp_path / "problem.edb"
    whole_edb = tmp_path / "whole.edb"
    problem_edb.write_bytes(b"problem-edb")
    whole_edb.write_bytes(b"whole-edb")
    case = {
        "case_id": "kice-2026-test",
        "year": 2026,
        "subject": "test",
        "subject_display_name": "테스트",
        "subject_area": "test-area",
        "subject_area_display_name": "테스트 영역",
        "level": "csat",
        "processing_ms_max": 20_000,
        "whole_processing_ms_max": 12_000,
        "resolution_gate": {
            "source_page_min_width": 1400,
            "source_page_min_height": 1900,
            "problem_crop_min_width": 320,
            "problem_crop_min_height": 120,
            "problem_bbox_scale_min": 0.9,
        },
        "expected": {
            "question_numbers": [1, 2],
            "passage_ranges": [],
            "source_page_count": 2,
            "whole_page_count": 2,
        },
    }
    problem_session = {
        "source_page_count": 2,
        "pages": [{"id": "page-1"}, {"id": "page-2"}],
        "rendered_page_paths": [page_1, page_2],
        "edb_path": str(problem_edb),
        "problems": [
            {
                "problemNumber": number,
                "originalImagePath": crop,
                "bbox": {"left": 0, "top": 0, "width": 390, "height": 190},
            }
            for number, crop in ((1, crop_1), (2, crop_2))
        ],
    }
    whole_session = {
        "input_intent": "page-as-is",
        "source_page_count": 2,
        "pages": [{"id": "page-1"}, {"id": "page-2"}],
        "rendered_page_paths": [page_1, page_2],
        "edb_path": str(whole_edb),
        "problems": [
            {
                "sourcePageId": f"page-{number}",
                "inputIntent": "page-as-is",
                "placementMode": "continuous-page-as-is",
                "forceFullPageBounds": True,
                "imagePath": page,
            }
            for number, page in ((1, page_1), (2, page_2))
        ],
    }
    observation = Observation(
        question_numbers=(1, 2),
        # Passage grouping is a required label only for Korean. Other subjects
        # may expose incidental set-question ranges without being penalized.
        passage_ranges=((1, 2),),
        preflight_issue_count=0,
        manual_review_count=0,
        review_population=2,
        processing_ms=10_000,
        problem_signatures=(_signature(1), _signature(2)),
    )
    problem = PipelineResult(
        mode="problem",
        output_dir=str(tmp_path / "problem"),
        elapsed_ms=10_000,
        exit_code=0,
        session=problem_session,
        observation=observation,
        error=None,
    )
    whole = PipelineResult(
        mode="whole",
        output_dir=str(tmp_path / "whole"),
        elapsed_ms=8_000,
        exit_code=0,
        session=whole_session,
        observation=None,
        error=None,
    )
    return case, problem, whole


def test_perfect_problem_and_whole_pipeline_scores_100(tmp_path: Path):
    case, problem, whole = _fixture(tmp_path)
    score = score_case(
        case,
        problem=problem,
        whole=whole,
        dimension_gate=DIMENSION_GATE,
    )
    assert score.score == 100.0
    assert score.input_page_recall == 1.0
    assert score.whole_page_recall == 1.0
    assert score.problem_resolution_valid_rate == 1.0
    assert score.completion_rate == 1.0
    assert score.within_time_budget is True
    assert score.failures == ()


def test_crop_resolution_and_time_are_strict_gate_failures(tmp_path: Path):
    case, problem, whole = _fixture(tmp_path)
    tiny = _image(tmp_path / "tiny.png", 100, 80)
    problem.session["problems"][0]["originalImagePath"] = tiny
    problem = PipelineResult(
        **{
            **problem.__dict__,
            "elapsed_ms": 25_000,
        }
    )
    score = score_case(
        case,
        problem=problem,
        whole=whole,
        dimension_gate=DIMENSION_GATE,
    )
    assert score.problem_resolution_valid_rate == 0.5
    assert score.within_time_budget is False
    assert "problem_resolution_valid_rate" in score.failures
    assert "time_budget" in score.failures
    assert score.dimension_failures == (
        "problem_resolution_valid_rate",
        "time_budget",
    )


def test_full_report_requires_all_catalog_cases(tmp_path: Path):
    case, problem, whole = _fixture(tmp_path)
    score = score_case(
        case,
        problem=problem,
        whole=whole,
        dimension_gate=DIMENSION_GATE,
    )
    report = build_report(
        catalog_path=tmp_path / "catalog.json",
        cases=[score],
        average_min=93,
        case_min=89,
        dimension_gate=DIMENSION_GATE,
        required_case_ids={score.case_id, "kice-2025-test"},
        require_complete_coverage=True,
        started_at=0,
    )
    assert report["gate"]["coverage_pass"] is False
    assert report["gate"]["missing_case_ids"] == ["kice-2025-test"]
    assert report["gate"]["pass"] is False
