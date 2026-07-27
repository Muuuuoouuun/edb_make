from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "evaluate_ai_capability_scorecard.py"


def _load_scorecard_module():
    spec = importlib.util.spec_from_file_location("ai_capability_scorecard", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _benchmark(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "source_count": 1,
                "summary": {
                    "gemini-3.5-flash": {
                        "call_count": 4,
                        "error_count": 0,
                        "minimum_similarity_to_expected": 1.0,
                    },
                    "gemini-3.5-flash-lite": {
                        "call_count": 4,
                        "error_count": 0,
                        "minimum_similarity_to_expected": 0.94,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_scorecard_dimensions_have_fixed_ten_point_rubrics(tmp_path: Path) -> None:
    scorecard = _load_scorecard_module()
    benchmark = tmp_path / "benchmark.json"
    _benchmark(benchmark)

    metrics = scorecard.build_metrics(ROOT, benchmark)

    assert set(metrics) == set(scorecard.DIMENSION_WEIGHTS)
    assert all(round(sum(metric.weight for metric in rows), 4) == 10.0 for rows in metrics.values())


def test_scorecard_keeps_missing_real_corpus_and_semantic_diff_visible(tmp_path: Path) -> None:
    scorecard = _load_scorecard_module()
    benchmark = tmp_path / "benchmark.json"
    _benchmark(benchmark)
    payload = json.loads(benchmark.read_text(encoding="utf-8"))
    payload["source_count"] = 0
    benchmark.write_text(json.dumps(payload), encoding="utf-8")

    report = scorecard.evaluate(ROOT, benchmark)
    recognition = {
        metric["metric_id"]: metric
        for metric in report["dimensions"]["recognition"]["metrics"]
    }
    quality = {
        metric["metric_id"]: metric
        for metric in report["dimensions"]["output_quality"]["metrics"]
    }

    assert recognition["rec.real_corpus"]["points"] == 0
    assert quality["quality.automatic_semantic_diff"]["points"] == 0
    backlog_ids = {row["metric_id"] for row in report["priority_backlog"]}
    assert {"rec.real_corpus", "quality.automatic_semantic_diff"}.issubset(backlog_ids)


def test_scorecard_targets_use_weighted_average_and_minimum_dimension(tmp_path: Path) -> None:
    scorecard = _load_scorecard_module()
    benchmark = tmp_path / "benchmark.json"
    _benchmark(benchmark)

    report = scorecard.evaluate(
        ROOT,
        benchmark,
        target_average=8.0,
        target_minimum=6.8,
    )

    expected = round(
        sum(
            report["dimensions"][name]["score"] * weight
            for name, weight in scorecard.DIMENSION_WEIGHTS.items()
        ),
        2,
    )
    assert report["overall_score"] == expected
    assert report["minimum_score"] == min(
        row["score"] for row in report["dimensions"].values()
    )
    assert report["passed"] is (
        report["overall_score"] >= 8.0 and report["minimum_score"] >= 6.8
    )
