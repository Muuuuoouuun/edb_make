from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from scripts.evaluate_quality_corpus import (
    EXIT_GATE_FAILED,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    CorpusError,
    baseline_report_fingerprint,
    evaluate_corpus,
    expected_fingerprint,
    extract_observation,
    main,
    observation_payload,
    validate_baseline_approval,
    validate_manifest_readiness,
)
from scripts.create_quality_observation import (
    main as create_observation_main,
    scaffold_manifest,
)
from scripts.run_quality_corpus import (
    _stable_fingerprint,
    build_environment_descriptor,
    build_fresh_manifest,
    main as run_quality_main,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def _observation(
    questions: list[int],
    passages: list[list[int]],
    *,
    issues: int = 0,
    manual: int = 0,
    elapsed: int = 500,
    signatures: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "qualityObservation": {
            "questionNumbers": questions,
            "passageRanges": passages,
            "preflightIssueCount": issues,
            "manualReviewCount": manual,
            "reviewPopulation": len(questions),
            "processingMs": elapsed,
            "problemSignatures": signatures or [],
        }
    }


def _signature(number: int, token: str = "a") -> dict[str, object]:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return {
        "number": number,
        "sourcePageId": hashlib.sha256(b"page-001").hexdigest(),
        "bboxSha256": digest,
        "cropSha256": digest,
        "renderSha256": digest,
        "visualSha256": digest,
        "contentSha256": digest,
        "choiceCount": 0,
        "choiceOrder": [],
        "artifactValid": True,
        "artifactSizeBytes": 10,
    }


def _manifest(result_name: str = "result.json") -> dict[str, object]:
    return {
        "schema_version": 1,
        "corpus_id": "unit-corpus",
        "case_thresholds": {
            "missing_question_count_max": 0,
            "duplicate_question_count_max": 0,
            "extra_question_count_max": 0,
            "question_recall_min": 1,
            "question_precision_min": 1,
            "missing_passage_range_count_max": 0,
            "extra_passage_range_count_max": 0,
            "passage_range_recall_min": 1,
            "passage_range_precision_min": 1,
            "preflight_issue_count_max": 0,
            "manual_review_rate_max": 0.25,
            "processing_ms_max": 1000,
        },
        "aggregate_thresholds": {
            "case_failure_count_max": 0,
            "processing_ms_p95_max": 1000,
        },
        "cases": [
            {
                "id": "case-1",
                "result": result_name,
                "expected": {
                    "question_numbers": [1, 2, 3],
                    "passage_ranges": [[1, 3]],
                },
            }
        ],
    }


class TestQualityCorpusEvaluator(unittest.TestCase):
    def test_committed_synthetic_corpus_passes(self) -> None:
        manifest_path = PROJECT_ROOT / "quality" / "synthetic-corpus.json"
        report = evaluate_corpus(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            manifest_path=manifest_path,
        )

        self.assertEqual("passed", report["status"])
        self.assertEqual(1.0, report["aggregate"]["question_recall"])
        self.assertEqual(1.0, report["aggregate"]["passage_range_recall"])
        self.assertEqual(0, report["aggregate"]["preflight_issue_count"])
        self.assertAlmostEqual(1 / 6, report["aggregate"]["manual_review_rate"])
        self.assertEqual(1280, report["aggregate"]["processing_ms_p95"])

    def test_gate_reports_missing_duplicate_extra_and_operational_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            result_path = root / "result.json"
            result_path.write_text(
                json.dumps(
                    _observation(
                        [1, 1, 4],
                        [[2, 4]],
                        issues=2,
                        manual=2,
                        elapsed=1400,
                    )
                ),
                encoding="utf-8",
            )
            report = evaluate_corpus(_manifest(), manifest_path=root / "manifest.json")

        self.assertEqual("failed", report["status"])
        metrics = report["cases"][0]["metrics"]
        self.assertEqual(2, metrics["missing_question_count"])
        self.assertEqual(1, metrics["duplicate_question_count"])
        self.assertEqual(1, metrics["extra_question_count"])
        self.assertEqual(1, metrics["missing_passage_range_count"])
        self.assertEqual(1, metrics["extra_passage_range_count"])
        failed_metrics = {failure["metric"] for failure in report["failures"]}
        self.assertTrue(
            {
                "missing_question_count",
                "duplicate_question_count",
                "extra_question_count",
                "passage_range_recall",
                "preflight_issue_count",
                "manual_review_rate",
                "processing_ms",
            }.issubset(failed_metrics)
        )

    def test_session_shape_is_extracted_without_counting_passage_fragment_as_question(self) -> None:
        observation = extract_observation(
            {
                "problems": [
                    {
                        "problemNumber": 7,
                        "passageRole": "passage_fragment",
                        "passageRange": {"start": 7, "end": 8},
                    },
                    {
                        "problemNumber": 7,
                        "passageRole": "child_question",
                        "passageRange": {"start": 7, "end": 8},
                        "reviewStatus": "confirmed",
                    },
                    {
                        "problem_number": 8,
                        "metadata": {
                            "passage_range": {"start": 7, "end": 8},
                            "review_status": "check_needed",
                        },
                    },
                ],
                "classinPreflight": {"issues": []},
                "timing_ms": {"source_build": 111, "total": 321},
            },
            "session",
        )

        self.assertEqual((7, 8), observation.question_numbers)
        self.assertEqual(((7, 8),), observation.passage_ranges)
        self.assertEqual(1, observation.manual_review_count)
        self.assertEqual(2, observation.review_population)
        self.assertEqual(321, observation.processing_ms)

    def test_session_group_ranges_and_measured_overrides_create_an_observation(self) -> None:
        observation = extract_observation(
            {
                "problems": [
                    {"problemNumber": 7, "reviewStatus": "confirmed"},
                    {"problemNumber": 8, "reviewStatus": "check_needed"},
                ],
                "passageGroups": [{"numberStart": 7, "numberEnd": 8}],
            },
            "session",
            processing_ms_override=842,
            preflight_issue_count_override=0,
        )

        self.assertEqual((7, 8), observation.question_numbers)
        self.assertEqual(((7, 8),), observation.passage_ranges)
        self.assertEqual(1, observation.manual_review_count)
        self.assertEqual(842, observation.processing_ms)

    def test_observation_cli_does_not_copy_session_paths_or_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session_path = root / "private-session.json"
            output_path = root / "observation.json"
            crop_path = root / "private-crop.png"
            image = Image.new("RGB", (80, 40), "white")
            ImageDraw.Draw(image).rectangle((10, 10, 70, 30), fill="black")
            image.save(crop_path)
            session_path.write_text(
                json.dumps(
                    {
                        "sourcePath": "/private/source/exam.pdf",
                        "ocrText": "private recognized text",
                        "problems": [
                            {
                                "problemNumber": 1,
                                "reviewStatus": "confirmed",
                                "text": "private problem text",
                                "sourcePageId": "page-001",
                                "bbox": {"left": 1, "top": 2, "width": 30, "height": 40},
                                "originalImagePath": crop_path.resolve().as_uri(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = create_observation_main(
                    [
                        str(session_path),
                        "--output",
                        str(output_path),
                        "--processing-ms",
                        "123",
                        "--preflight-issue-count",
                        "0",
                    ]
                )
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            serialized = json.dumps(payload)

        self.assertEqual(EXIT_OK, exit_code)
        self.assertEqual({"qualityObservation"}, set(payload))
        self.assertNotIn("private", serialized)
        self.assertNotIn("sourcePath", serialized)
        self.assertNotIn("ocrText", serialized)
        signature = payload["qualityObservation"]["problemSignatures"][0]
        self.assertTrue(signature["artifactValid"])
        self.assertNotIn(str(crop_path), serialized)

    def test_baseline_regression_tolerance_fails_slower_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            manifest = _manifest()
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]], elapsed=1000)),
                encoding="utf-8",
            )
            baseline = evaluate_corpus(manifest, manifest_path=root / "manifest.json")
            manifest["regression_tolerance"] = {"processing_ms_p95_increase_ratio_max": 0.10}
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]], elapsed=1210)),
                encoding="utf-8",
            )
            report = evaluate_corpus(
                manifest,
                manifest_path=root / "manifest.json",
                baseline_report=baseline,
            )

        self.assertEqual("failed", report["status"])
        baseline_failure = next(item for item in report["failures"] if item["scope"] == "baseline")
        self.assertEqual("processing_ms_p95", baseline_failure["metric"])
        self.assertAlmostEqual(0.21, baseline_failure["regression"])

    def test_missing_processing_measurement_is_invalid_input(self) -> None:
        with self.assertRaisesRegex(CorpusError, "missing processingMs"):
            extract_observation(
                {
                    "problems": [],
                    "classin_preflight": {"issue_count": 0},
                },
                "session",
            )

    def test_threshold_typo_is_rejected_instead_of_silently_bypassing_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]])), encoding="utf-8"
            )
            manifest = _manifest()
            manifest["case_thresholds"] = {"question_recal_min": 1}
            with self.assertRaisesRegex(CorpusError, "unsupported threshold"):
                evaluate_corpus(manifest, manifest_path=root / "manifest.json")

    def test_baseline_from_another_corpus_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]])), encoding="utf-8"
            )
            manifest = _manifest()
            with self.assertRaisesRegex(CorpusError, "does not match"):
                evaluate_corpus(
                    manifest,
                    manifest_path=root / "manifest.json",
                    baseline_report={"corpus_id": "other", "aggregate": {}},
                )

    def test_failed_or_different_case_baseline_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]])), encoding="utf-8"
            )
            manifest = _manifest()
            baseline = evaluate_corpus(manifest, manifest_path=root / "manifest.json")

            failed_baseline = dict(baseline)
            failed_baseline["status"] = "failed"
            with self.assertRaisesRegex(CorpusError, "status='passed'"):
                evaluate_corpus(
                    manifest,
                    manifest_path=root / "manifest.json",
                    baseline_report=failed_baseline,
                )

            changed_manifest = json.loads(json.dumps(manifest))
            changed_manifest["cases"][0]["expected"]["question_numbers"] = [1, 2, 4]
            with self.assertRaisesRegex(CorpusError, "corpus_fingerprint"):
                evaluate_corpus(
                    changed_manifest,
                    manifest_path=root / "manifest.json",
                    baseline_report=baseline,
                )

    def test_report_paths_are_private_by_default_and_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]])), encoding="utf-8"
            )
            manifest_path = root / "manifest.json"
            safe_report = evaluate_corpus(_manifest(), manifest_path=manifest_path)
            path_report = evaluate_corpus(
                _manifest(), manifest_path=manifest_path, include_paths=True
            )

        self.assertNotIn("manifest_path", safe_report)
        self.assertNotIn("corpus_root", safe_report)
        self.assertNotIn("result_path", safe_report["cases"][0])
        self.assertEqual(str(manifest_path), path_report["manifest_path"])
        self.assertIn("result_path", path_report["cases"][0])

    def test_source_sha256_is_verified_without_copying_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "private-source.bin"
            source.write_bytes(b"stable private fixture")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / "result.json").write_text(
                json.dumps(_observation([1, 2, 3], [[1, 3]])), encoding="utf-8"
            )
            manifest = _manifest()
            manifest["cases"][0]["source"] = {
                "path": source.name,
                "sha256": digest,
                "format": "pdf",
            }
            report = evaluate_corpus(manifest, manifest_path=root / "manifest.json")

            manifest["cases"][0]["source"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(CorpusError, "does not match"):
                evaluate_corpus(manifest, manifest_path=root / "manifest.json")

        self.assertTrue(report["cases"][0]["source_digest_verified"])
        self.assertNotIn(str(source), json.dumps(report))
        self.assertNotIn(digest, json.dumps(report))

    def test_private_runner_rebuilds_case_and_uses_fresh_observation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "private.pdf"
            source.write_bytes(b"private fixture")
            session_path = root / "fresh-ui-session.json"
            session_path.write_text(
                json.dumps(
                    {
                        "problems": [{"problemNumber": 1, "reviewStatus": "confirmed"}],
                        "classinPreflight": {"issueCount": 0},
                    }
                ),
                encoding="utf-8",
            )
            manifest = _manifest()
            manifest["cases"][0]["expected"] = {
                "question_numbers": [1],
                "passage_ranges": [],
            }
            manifest["cases"][0]["source"] = {
                "path": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "subject": "korean",
            }

            with patch(
                "scripts.run_quality_corpus.run_pipeline_case",
                return_value=(session_path, 4321.0),
            ) as run_case:
                fresh = build_fresh_manifest(
                    manifest,
                    corpus_root=root,
                    work_dir=root / "work",
                    ocr="auto",
                    python_executable="python",
                )

            observation_path = Path(fresh["cases"][0]["result"])
            observation = json.loads(observation_path.read_text(encoding="utf-8"))

        run_case.assert_called_once()
        isolated_source = run_case.call_args.kwargs["source_path"]
        self.assertNotEqual(source.resolve(), isolated_source.resolve())
        self.assertIn("isolated-inputs", isolated_source.parts)
        self.assertEqual([1], observation["qualityObservation"]["questionNumbers"])
        self.assertEqual(4321.0, observation["qualityObservation"]["processingMs"])
        self.assertNotIn("private.pdf", json.dumps(observation))

    def test_thresholdless_extreme_observation_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / "result.json").write_text(
                json.dumps(
                    _observation(
                        [],
                        [],
                        issues=12,
                        manual=0,
                        elapsed=999999,
                    )
                ),
                encoding="utf-8",
            )
            manifest = _manifest()
            manifest["case_thresholds"] = {}
            manifest["aggregate_thresholds"] = {}
            with self.assertRaisesRegex(CorpusError, "unbounded observation"):
                evaluate_corpus(manifest, manifest_path=root / "manifest.json")

    def test_problem_crop_or_content_swap_fails_structural_signature_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            expected_signature = _signature(1, "approved-crop")
            swapped_signature = _signature(1, "swapped-or-contaminated-crop")
            (root / "result.json").write_text(
                json.dumps(
                    _observation(
                        [1],
                        [],
                        signatures=[swapped_signature],
                    )
                ),
                encoding="utf-8",
            )
            manifest = _manifest()
            manifest["cases"][0]["expected"] = {
                "question_numbers": [1],
                "passage_ranges": [],
                "problem_signatures": [expected_signature],
            }
            manifest["case_thresholds"] = {
                "problem_signature_mismatch_count_max": 0,
                "artifact_invalid_count_max": 0,
            }
            manifest["aggregate_thresholds"] = {
                "problem_signature_mismatch_count_max": 0,
                "artifact_invalid_count_max": 0,
            }
            report = evaluate_corpus(manifest, manifest_path=root / "manifest.json")

        self.assertEqual("failed", report["status"])
        self.assertEqual(1, report["cases"][0]["metrics"]["problem_signature_mismatch_count"])
        self.assertEqual(
            [1],
            report["cases"][0]["details"]["problem_signature_mismatch_numbers"],
        )

    def test_session_signature_hashes_user_visible_render_and_rejects_corrupt_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            crop = root / "crop.png"
            render = root / "render.png"
            corrupt = root / "corrupt.png"
            crop_image = Image.new("RGB", (60, 40), "white")
            ImageDraw.Draw(crop_image).rectangle((5, 5, 30, 30), fill="black")
            crop_image.save(crop)
            render_image = Image.new("RGB", (60, 40), "black")
            ImageDraw.Draw(render_image).rectangle((30, 5, 55, 30), fill="white")
            render_image.save(render)
            corrupt.write_bytes(b"not a decodable PNG despite being non-empty")
            base_problem = {
                "problemNumber": 1,
                "sourcePageId": "private-file-page-001",
                "bbox": {"left": 1, "top": 2, "width": 30, "height": 40},
                "originalImagePath": crop.resolve().as_uri(),
                "boardRenderPath": render.resolve().as_uri(),
            }
            valid = extract_observation(
                {"problems": [base_problem]},
                "session",
                processing_ms_override=1,
                preflight_issue_count_override=0,
            ).problem_signatures[0]
            corrupt_problem = dict(base_problem)
            corrupt_problem["boardRenderPath"] = corrupt.resolve().as_uri()
            invalid = extract_observation(
                {"problems": [corrupt_problem]},
                "session",
                processing_ms_override=1,
                preflight_issue_count_override=0,
            ).problem_signatures[0]

        self.assertNotEqual(valid.crop_sha256, valid.render_sha256)
        self.assertNotEqual(valid.crop_sha256, valid.visual_sha256)
        self.assertTrue(valid.artifact_valid)
        self.assertFalse(invalid.artifact_valid)
        self.assertEqual("0" * 64, invalid.visual_sha256)
        self.assertNotIn("private-file", valid.source_page_id)

    def test_private_manifest_scaffold_label_approve_and_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source_dir = root / "private-sources"
            source_dir.mkdir()
            source_path = source_dir / "student-name-exam.pdf"
            source_path.write_bytes(b"private document bytes")
            manifest_path = root / "protected" / "corpus.json"
            manifest = scaffold_manifest(
                source_dir,
                manifest_path=manifest_path,
                corpus_id="private-release-v1",
                subject="korean",
                tags=["single-column"],
                recursive=False,
                minimum_cases=1,
                required_formats=["pdf"],
                required_subjects=["korean"],
                required_tags=["single-column"],
                processing_ms_max=10000,
            )
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            case_id = manifest["cases"][0]["id"]
            observation_path = root / "approved-structure.json"
            observation_path.write_text(
                json.dumps(
                    _observation(
                        [1, 2, 3, 5],
                        [[1, 3]],
                        signatures=[_signature(number, f"sig-{number}") for number in (1, 2, 3, 5)],
                    )
                ),
                encoding="utf-8",
            )
            self.assertNotIn("student", case_id)

            with contextlib.redirect_stdout(io.StringIO()):
                label_exit = create_observation_main(
                    [
                        "label",
                        str(manifest_path),
                        case_id,
                        "--questions",
                        "1-3,5",
                        "--passages",
                        "1-3",
                        "--annotator-id",
                        "operator-a",
                        "--observation",
                        str(observation_path),
                    ]
                )
                same_reviewer_exit = create_observation_main(
                    [
                        "approve",
                        str(manifest_path),
                        case_id,
                        "--reviewer-id",
                        "operator-a",
                    ]
                )
                approve_exit = create_observation_main(
                    [
                        "approve",
                        str(manifest_path),
                        case_id,
                        "--reviewer-id",
                        "operator-b",
                    ]
                )
            approved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            readiness = validate_manifest_readiness(
                approved_manifest,
                corpus_root=manifest_path.parent,
                minimum_cases=1,
                require_approved=True,
                verify_sources=True,
                enforce_code_policy=False,
            )

        self.assertEqual(EXIT_OK, label_exit)
        self.assertEqual(EXIT_INVALID_INPUT, same_reviewer_exit)
        self.assertEqual(EXIT_OK, approve_exit)
        self.assertEqual("ready", readiness["status"])
        self.assertEqual(1, readiness["approved_case_count"])
        self.assertEqual(1, readiness["source_digest_verified_count"])
        ground_truth = approved_manifest["cases"][0]["ground_truth"]
        self.assertEqual("approved", ground_truth["status"])
        self.assertEqual(
            expected_fingerprint(approved_manifest["cases"][0]["expected"]),
            ground_truth["expected_sha256"],
        )

    def test_readiness_fails_closed_on_missing_release_controls_and_source_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.pdf"
            source.write_bytes(b"first")
            expected = {
                "question_numbers": [1],
                "passage_ranges": [],
                "problem_signatures": [_signature(1)],
            }
            manifest = {
                "schema_version": 1,
                "corpus_id": "unsafe",
                "coverage_requirements": {"minimum_cases": 1},
                "case_thresholds": {},
                "aggregate_thresholds": {},
                "cases": [
                    {
                        "id": "case-1",
                        "source": {
                            "path": source.name,
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            "format": "pdf",
                            "subject": "korean",
                        },
                        "result": "result.json",
                        "expected": expected,
                        "ground_truth": {
                            "status": "approved",
                            "annotation_revision": 1,
                            "annotator_id": "a",
                            "labeled_at": "2026-01-01T00:00:00Z",
                            "reviewer_id": "b",
                            "reviewed_at": "2026-01-01T00:01:00Z",
                            "expected_sha256": expected_fingerprint(expected),
                        },
                    }
                ],
            }
            controls_report = validate_manifest_readiness(
                manifest,
                corpus_root=root,
                minimum_cases=1,
                require_approved=True,
                verify_sources=True,
            )
            source.write_bytes(b"tampered")
            tamper_report = validate_manifest_readiness(
                manifest,
                corpus_root=root,
                minimum_cases=1,
                require_approved=True,
                verify_sources=True,
            )

        self.assertEqual("not_ready", controls_report["status"])
        self.assertTrue(any("case_thresholds" in item for item in controls_report["errors"]))
        self.assertTrue(any("aggregate_thresholds" in item for item in controls_report["errors"]))
        self.assertTrue(any("regression_tolerance" in item for item in controls_report["errors"]))
        self.assertTrue(any("does not match" in item for item in tamper_report["errors"]))

    def test_readiness_rejects_manifest_that_weakens_code_owned_release_policy(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.pdf"
            source.write_bytes(b"source")
            manifest = scaffold_manifest(
                root,
                manifest_path=root / "corpus.json",
                corpus_id="weak-policy",
                subject="korean",
                tags=[],
                recursive=False,
                minimum_cases=1,
                required_formats=[],
                required_subjects=[],
                required_tags=[],
                processing_ms_max=300000,
            )
            expected = {
                "question_numbers": [1],
                "passage_ranges": [],
                "problem_signatures": [_signature(1)],
            }
            case = manifest["cases"][0]
            case["expected"] = expected
            case["ground_truth"] = {
                "status": "approved",
                "annotation_revision": 1,
                "annotator_id": "a",
                "labeled_at": "2026-01-01T00:00:00Z",
                "reviewer_id": "b",
                "reviewed_at": "2026-01-01T00:01:00Z",
                "expected_sha256": expected_fingerprint(expected),
            }
            manifest["case_thresholds"]["question_recall_min"] = 0
            manifest["case_thresholds"]["missing_question_count_max"] = 999999
            manifest["aggregate_thresholds"]["preflight_issue_count_max"] = 999999
            manifest["regression_tolerance"]["question_recall_drop_max"] = 1
            report = validate_manifest_readiness(
                manifest,
                corpus_root=root,
                minimum_cases=1,
                require_approved=True,
                verify_sources=True,
            )

        self.assertEqual("not_ready", report["status"])
        weakened = "\n".join(report["errors"])
        self.assertIn("question_recall_min=0 weakens release policy", weakened)
        self.assertIn("missing_question_count_max=999999 weakens release policy", weakened)
        self.assertIn("preflight_issue_count_max=999999 weakens release policy", weakened)
        self.assertIn("question_recall_drop_max=1 weakens release policy", weakened)

    def test_production_coverage_counts_unique_sources_and_cannot_be_emptied(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.pdf"
            source.write_bytes(b"one document aliased thirty times")
            manifest = scaffold_manifest(
                root,
                manifest_path=root / "corpus.json",
                corpus_id="alias-attack",
                subject="korean",
                tags=["multi-column", "low-resolution-scan", "cross-page-passage"],
                recursive=False,
                minimum_cases=30,
                required_formats=[],
                required_subjects=[],
                required_tags=[],
                processing_ms_max=300000,
            )
            expected = {
                "question_numbers": [1],
                "passage_ranges": [],
                "problem_signatures": [_signature(1)],
            }
            base_case = manifest["cases"][0]
            cases = []
            formats = ["pdf", "hwp", "hwpx", "image"]
            subjects = ["korean", "english", "math"]
            for index in range(30):
                case = json.loads(json.dumps(base_case))
                case["id"] = f"case-{index + 1:03d}"
                case["source"]["format"] = formats[index % len(formats)]
                case["source"]["subject"] = subjects[index % len(subjects)]
                case["expected"] = expected
                case["ground_truth"] = {
                    "status": "approved",
                    "annotation_revision": 1,
                    "annotator_id": "a",
                    "labeled_at": "2026-01-01T00:00:00Z",
                    "reviewer_id": "b",
                    "reviewed_at": "2026-01-01T00:01:00Z",
                    "expected_sha256": expected_fingerprint(expected),
                }
                cases.append(case)
            manifest["cases"] = cases
            report = validate_manifest_readiness(
                manifest,
                corpus_root=root,
                minimum_cases=30,
                require_approved=True,
                verify_sources=True,
                enforce_code_policy=True,
            )

        self.assertEqual("not_ready", report["status"])
        self.assertEqual(1, report["unique_source_digest_count"])
        self.assertTrue(any("unique source digests" in item for item in report["errors"]))
        self.assertTrue(
            any("does not match the file suffix and content signature" in item for item in report["errors"])
        )

    def test_private_runner_rejects_any_cache_hit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "private.pdf"
            source.write_bytes(b"private fixture")
            session_path = root / "cached-ui-session.json"
            session_path.write_text(
                json.dumps(
                    {
                        "problems": [{"problemNumber": 1}],
                        "classinPreflight": {"issueCount": 0},
                        "qualityMetrics": {"ocr_cache_hit_count": 1},
                    }
                ),
                encoding="utf-8",
            )
            manifest = _manifest()
            manifest["cases"][0]["source"] = {
                "path": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "format": "pdf",
                "subject": "korean",
            }
            with patch(
                "scripts.run_quality_corpus.run_pipeline_case",
                return_value=(session_path, 100.0),
            ):
                with self.assertRaisesRegex(CorpusError, "reported 1 cache hit"):
                    build_fresh_manifest(
                        manifest,
                        corpus_root=root,
                        work_dir=root / "work",
                        ocr="auto",
                        python_executable="python",
                    )

    def test_fresh_observation_provenance_is_bound_to_source_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "private.pdf"
            source.write_bytes(b"private fixture")
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            crop = root / "crop.png"
            image = Image.new("RGB", (80, 40), "white")
            ImageDraw.Draw(image).rectangle((10, 10, 70, 30), fill="black")
            image.save(crop)
            session_path = root / "fresh-ui-session.json"
            session_payload = {
                "problems": [
                    {
                        "problemNumber": 1,
                        "reviewStatus": "confirmed",
                        "sourcePageId": "page-001",
                        "bbox": {"left": 10, "top": 20, "width": 300, "height": 200},
                        "originalImagePath": crop.resolve().as_uri(),
                    }
                ],
                "classinPreflight": {"issueCount": 0},
            }
            session_path.write_text(json.dumps(session_payload), encoding="utf-8")
            structural = extract_observation(
                session_payload,
                "approved structure",
                processing_ms_override=123,
            )
            manifest = _manifest()
            manifest["cases"][0]["expected"] = {
                "question_numbers": [1],
                "passage_ranges": [],
                "problem_signatures": observation_payload(structural)[
                    "qualityObservation"
                ]["problemSignatures"],
            }
            manifest["cases"][0]["source"] = {
                "path": source.name,
                "sha256": source_sha,
                "format": "pdf",
                "subject": "korean",
            }
            expected = manifest["cases"][0]["expected"]
            manifest["cases"][0]["ground_truth"] = {
                "status": "approved",
                "annotation_revision": 1,
                "annotator_id": "a",
                "labeled_at": "2026-01-01T00:00:00Z",
                "reviewer_id": "b",
                "reviewed_at": "2026-01-01T00:01:00Z",
                "expected_sha256": expected_fingerprint(expected),
            }
            provenance = {
                "runner": "scripts/run_quality_corpus.py",
                "run_id": "run-1",
                "git_commit": "a" * 40,
                "pipeline_fingerprint": "b" * 64,
                "environment_fingerprint": "c" * 64,
            }
            with patch(
                "scripts.run_quality_corpus.run_pipeline_case",
                return_value=(session_path, 123.0),
            ):
                fresh = build_fresh_manifest(
                    manifest,
                    corpus_root=root,
                    work_dir=root / "work",
                    ocr="auto",
                    python_executable="python",
                    provenance=provenance,
                )
            report = evaluate_corpus(
                fresh,
                manifest_path=root / "work" / "fresh.json",
                corpus_root=root,
                require_approved_ground_truth=True,
                require_observation_provenance=True,
                expected_observation_provenance=provenance,
            )

        self.assertEqual("passed", report["status"])
        self.assertEqual(1, report["aggregate"]["measurement_complete_case_count"])
        self.assertEqual(1, report["aggregate"]["observation_provenance_verified_case_count"])

    def test_baseline_requires_tamper_evident_approval(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            candidate_path = root / "candidate.json"
            approved_path = root / "approved.json"
            candidate = {
                "status": "passed",
                "corpus_fingerprint": "d" * 64,
                "aggregate": {},
                "pipeline_provenance": {
                    "fresh_pipeline_execution": True,
                    "observation_provenance_verified": True,
                    "worktree_clean": True,
                },
            }
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                approval_exit = create_observation_main(
                    [
                        "approve-baseline",
                        str(candidate_path),
                        "--output",
                        str(approved_path),
                        "--reviewer-id",
                        "release-reviewer",
                    ]
                )
            approved = json.loads(approved_path.read_text(encoding="utf-8"))
            validate_baseline_approval(approved, corpus_fingerprint="d" * 64)
            approved["aggregate"]["tampered"] = True
            with self.assertRaisesRegex(CorpusError, "report_sha256"):
                validate_baseline_approval(approved, corpus_fingerprint="d" * 64)

        self.assertEqual(EXIT_OK, approval_exit)
        self.assertEqual(
            approved["baseline_approval"]["report_sha256"],
            baseline_report_fingerprint(
                {key: value for key, value in approved.items() if key != "aggregate"}
                | {"aggregate": {}}
            ),
        )

    def test_production_runner_refuses_missing_baseline_and_lower_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                missing_baseline_exit = run_quality_main(
                    [
                        str(manifest_path),
                        "--corpus-root",
                        str(root),
                        "--work-dir",
                        str(root / "work"),
                    ]
                )
                lowered_minimum_exit = run_quality_main(
                    [
                        str(manifest_path),
                        "--corpus-root",
                        str(root),
                        "--work-dir",
                        str(root / "work"),
                        "--establish-baseline",
                        "--minimum-cases",
                        "1",
                    ]
                )

        self.assertEqual(EXIT_INVALID_INPUT, missing_baseline_exit)
        self.assertEqual(EXIT_INVALID_INPUT, lowered_minimum_exit)

    def test_environment_fingerprint_changes_with_dependency_or_converter_version(self) -> None:
        inventory_a = {
            "inventory_schema_version": 1,
            "python_packages": {"Pillow": "10.0"},
            "external_tools": {"tesseract": "5.3.0", "soffice": "24.2"},
            "rhwp_core_versions": ["1.0.0"],
            "requirements_sha256": {"requirements-local.txt": "a" * 64},
        }
        inventory_b = json.loads(json.dumps(inventory_a))
        inventory_b["external_tools"]["tesseract"] = "5.4.0"
        with patch(
            "scripts.run_quality_corpus.build_dependency_inventory",
            side_effect=[inventory_a, inventory_b],
        ):
            environment_a = build_environment_descriptor(
                ocr="auto", python_executable="python3"
            )
            environment_b = build_environment_descriptor(
                ocr="auto", python_executable="python3"
            )

        self.assertNotEqual(
            _stable_fingerprint(environment_a), _stable_fingerprint(environment_b)
        )

    def test_environment_fingerprint_includes_release_lock_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            lock_payloads = {
                "requirements-release.lock": b"runtime==1.0 --hash=sha256:aaa\n",
                "requirements-release-bootstrap.lock": b"pip==1.0 --hash=sha256:bbb\n",
                "requirements-ci.lock": b"pytest==1.0 --hash=sha256:ccc\n",
            }
            for name, payload in lock_payloads.items():
                (root / name).write_bytes(payload)
            inventory_result = Mock(returncode=0, stdout="{}", stderr="")
            with (
                patch("scripts.run_quality_corpus.PROJECT_ROOT", root),
                patch("scripts.run_quality_corpus._command_version", return_value=None),
                patch("scripts.run_quality_corpus.subprocess.run", return_value=inventory_result),
            ):
                environment_before = build_environment_descriptor(
                    ocr="gemini-3.5-flash", python_executable="python3"
                )
                (root / "requirements-release.lock").write_bytes(
                    b"runtime==1.0 --hash=sha256:changed\n"
                )
                environment_after = build_environment_descriptor(
                    ocr="gemini-3.5-flash", python_executable="python3"
                )

        requirements_before = environment_before["dependency_inventory"][
            "requirements_sha256"
        ]
        self.assertEqual(set(lock_payloads), set(requirements_before))
        for name, payload in lock_payloads.items():
            self.assertEqual(hashlib.sha256(payload).hexdigest(), requirements_before[name])
        self.assertNotEqual(
            _stable_fingerprint(environment_before),
            _stable_fingerprint(environment_after),
        )

    def test_cli_uses_distinct_exit_codes_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            manifest_path = root / "manifest.json"
            result_path = root / "result.json"
            json_report = root / "reports" / "quality.json"
            markdown_report = root / "reports" / "quality.md"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            result_path.write_text(json.dumps(_observation([1, 2, 3], [[1, 3]])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                passed_exit = main(
                    [
                        str(manifest_path),
                        "--json-report",
                        str(json_report),
                        "--markdown-report",
                        str(markdown_report),
                    ]
                )

            failing = _manifest()
            failing["aggregate_thresholds"] = {"processing_ms_p95_max": 100}
            manifest_path.write_text(json.dumps(failing), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                failed_exit = main([str(manifest_path)])
            with contextlib.redirect_stderr(io.StringIO()):
                invalid_exit = main([str(root / "missing.json")])

            written_status = json.loads(json_report.read_text(encoding="utf-8"))["status"]
            written_markdown = markdown_report.read_text(encoding="utf-8")

        self.assertEqual(EXIT_OK, passed_exit)
        self.assertEqual(EXIT_GATE_FAILED, failed_exit)
        self.assertEqual(EXIT_INVALID_INPUT, invalid_exit)
        self.assertEqual("passed", written_status)
        self.assertIn("**PASS**", written_markdown)


if __name__ == "__main__":
    unittest.main()
