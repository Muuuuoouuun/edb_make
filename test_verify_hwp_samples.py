import unittest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import verify_hwp_samples


class TestVerifyHwpSamples(unittest.TestCase):
    def test_infer_subject_from_evaluation_style_names(self):
        cases = {
            "평가원 국어 양식.hwp": "국어",
            "2024-06-고3-모평(평가원)화법과 작문.hwp": "국어",
            "평가원 영어 양식.hwp": "영어",
            "평가원 수학 양식.hwp": "수학",
            "평가원 과탐 양식.hwp": "과학",
            "평가원 사탐 양식.hwp": "사회",
            "학교 기출 시험지 양식.hwp": "unknown",
        }

        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(expected, verify_hwp_samples.infer_subject(Path(name)))

    def test_iter_hangul_files_filters_by_include_and_limit(self):
        with TemporaryDirectory() as raw_tmp:
            source_dir = Path(raw_tmp)
            for name in [
                "평가원 국어 양식.hwp",
                "평가원 영어 양식.hwp",
                "평가원 수학 양식.hwpx",
                "메모.txt",
            ]:
                (source_dir / name).write_bytes(b"sample")

            selected = verify_hwp_samples.iter_hangul_files(
                source_dir,
                include=["평가원", "양식"],
                limit=2,
            )

        self.assertEqual(
            ["평가원 국어 양식.hwp", "평가원 수학 양식.hwpx"],
            [verify_hwp_samples.normalized_text(path.name) for path in selected],
        )

    def test_summarize_export_response_surfaces_review_risk(self):
        payload = {
            "ok": True,
            "session": {
                "pages": [
                    {
                        "id": "p1",
                        "riskFlags": ["sparse_segmentation", "ocr_disabled"],
                        "reviewStatus": "check_needed",
                    },
                    {
                        "id": "p2",
                        "riskFlags": [],
                        "reviewStatus": "normal",
                    },
                ],
                "problems": [
                    {
                        "id": "problem-1",
                        "riskFlags": ["fallback_grouping"],
                        "reviewStatus": "check_needed",
                    },
                    {
                        "id": "problem-1-continuation",
                        "riskFlags": ["marker_document_continuation"],
                        "reviewStatus": "check_needed",
                    }
                ],
                "reviewSummary": {
                    "riskFlagCounts": {
                        "fallback_grouping": 3,
                        "marker_document_continuation": 1,
                        "ocr_disabled": 2,
                        "sparse_segmentation": 4,
                    },
                    "hwpCacheHitPageCount": 2,
                    "hwpRendererCacheHitCount": 1,
                    "hwpNormalizedCacheHitCount": 2,
                },
                "warnings": [{"message": "check me"}],
                "warning_messages": ["also check me"],
                "hwp_problem_count_mismatch_flags": ["source.hwp"],
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("source.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=1.23,
        )

        self.assertTrue(summary["ok"])
        self.assertEqual(2, summary["problem_count"])
        self.assertEqual(1, summary["core_problem_count"])
        self.assertEqual(1, summary["supplemental_item_count"])
        self.assertEqual(2, summary["pages"])
        self.assertEqual(["check me", "also check me"], summary["warnings"])
        self.assertEqual(["source.hwp"], summary["hwp_problem_count_mismatch_flags"])
        self.assertEqual({"check_needed": 3, "normal": 1}, summary["review_status_counts"])
        self.assertEqual(
            ["fallback_grouping", "marker_document_continuation", "ocr_disabled", "sparse_segmentation"],
            summary["risk_flags"],
        )
        self.assertEqual(
            {
                "fallback_grouping": 3,
                "marker_document_continuation": 1,
                "ocr_disabled": 2,
                "sparse_segmentation": 4,
            },
            summary["risk_flag_counts"],
        )
        self.assertEqual(
            {
                "fallback_grouping": 3,
                "sparse_segmentation": 4,
            },
            summary["actionable_risk_flag_counts"],
        )
        self.assertEqual(2, summary["hwp_cache_hit_page_count"])
        self.assertEqual(1, summary["hwp_renderer_cache_hit_count"])
        self.assertEqual(2, summary["hwp_normalized_cache_hit_count"])
        self.assertEqual(1.0, summary["hwp_cache_hit_rate"])
        self.assertTrue(summary["needs_review"])

    def test_summarize_export_response_demotes_fallback_when_hwp_counts_match(self):
        payload = {
            "ok": True,
            "session": {
                "pages": [
                    {
                        "id": "page-1",
                        "reviewStatus": "normal",
                        "riskFlags": ["sparse_segmentation", "no_problem_markers"],
                    }
                ],
                "problems": [
                    {
                        "id": "problem-1",
                        "riskFlags": ["fallback_grouping", "ocr_disabled"],
                        "reviewStatus": "check_needed",
                    },
                    {
                        "id": "problem-2",
                        "riskFlags": ["fallback_grouping", "ocr_disabled"],
                        "reviewStatus": "check_needed",
                    },
                ],
                "reviewSummary": {
                    "riskFlagCounts": {
                        "fallback_grouping": 2,
                        "no_problem_markers": 1,
                        "ocr_disabled": 2,
                        "sparse_segmentation": 1,
                    },
                    "hwpTextProblemCountMatches": True,
                    "hwpLayoutProblemCountMatches": True,
                },
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("matched.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
        )

        self.assertEqual(
            {
                "fallback_grouping": 2,
                "no_problem_markers": 1,
                "ocr_disabled": 2,
                "sparse_segmentation": 1,
            },
            summary["risk_flag_counts"],
        )
        self.assertTrue(summary["hwp_text_problem_count_matches"])
        self.assertTrue(summary["hwp_layout_problem_count_matches"])
        self.assertEqual({}, summary["actionable_risk_flag_counts"])
        self.assertFalse(summary["needs_review"])

    def test_summarize_export_response_records_edb_validation_when_expected(self):
        payload = {
            "ok": True,
            "edbPath": "/tmp/out.edb",
            "edbValidation": {
                "validated": True,
                "recordCountActual": 45,
                "recordCountHint": 45,
                "pageCountHint": 90,
            },
            "session": {
                "pages": [],
                "problems": [],
                "reviewSummary": {},
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("source.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
            expect_edb=True,
        )

        self.assertTrue(summary["edb_expected"])
        self.assertTrue(summary["edb_validated"])
        self.assertEqual("/tmp/out.edb", summary["edb_path"])
        self.assertEqual(45, summary["edb_record_count_actual"])
        self.assertFalse(summary["needs_review"])

    def test_summarize_export_response_captures_classin_preflight_issues(self):
        payload = {
            "ok": True,
            "edbPath": "/tmp/out.edb",
            "edbValidation": {
                "validated": True,
                "recordCountActual": 2,
                "recordCountHint": 2,
            },
            "classinPreflight": {
                "passed": False,
                "status": "needs_attention",
                "issueCount": 2,
                "issues": [
                    {"type": "board_placement_overlap", "problemId": "p13", "nextProblemId": "p14"},
                    {"type": "source_problem_bbox_overlap", "problemId": "p21", "nextProblemId": "p22"},
                ],
            },
            "session": {
                "pages": [],
                "problems": [],
                "reviewSummary": {},
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("preflight.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
            expect_edb=True,
        )

        self.assertTrue(summary["classin_preflight_expected"])
        self.assertFalse(summary["classin_preflight_passed"])
        self.assertEqual("needs_attention", summary["classin_preflight_status"])
        self.assertEqual(2, summary["classin_preflight_issue_count"])
        self.assertEqual(2, summary["classin_preflight_blocking_issue_count"])
        self.assertEqual(
            ["board_placement_overlap", "source_problem_bbox_overlap"],
            summary["classin_preflight_issue_types"],
        )
        self.assertTrue(summary["needs_review"])

    def test_summarize_export_response_counts_hwp_segmentation_risks(self):
        payload = {
            "ok": True,
            "session": {
                "pages": [
                    {
                        "id": "page-1",
                        "riskFlags": ["hwp_problem_count_mismatch", "hwp_oversegmentation"],
                        "reviewStatus": "check_needed",
                    },
                    {
                        "id": "page-2",
                        "riskFlags": ["hwp_problem_count_mismatch"],
                        "reviewStatus": "check_needed",
                    },
                ],
                "problems": [],
                "reviewSummary": {
                    "riskFlagCounts": {
                        "hwp_problem_count_mismatch": 2,
                        "hwp_oversegmentation": 1,
                    }
                },
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("overseg.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
        )

        self.assertEqual(2, summary["hwp_problem_count_mismatch_count"])
        self.assertEqual(1, summary["hwp_oversegmentation_count"])
        self.assertEqual(
            {
                "hwp_problem_count_mismatch": 2,
                "hwp_oversegmentation": 1,
            },
            summary["actionable_risk_flag_counts"],
        )
        self.assertTrue(summary["needs_review"])

    def test_summarize_export_response_counts_source_problem_overlap_risks(self):
        payload = {
            "ok": True,
            "session": {
                "pages": [],
                "problems": [
                    {
                        "id": "problem-1",
                        "riskFlags": ["source_problem_bbox_overlap"],
                        "reviewStatus": "check_needed",
                    },
                    {
                        "id": "problem-2",
                        "riskFlags": ["source_problem_bbox_overlap"],
                        "reviewStatus": "check_needed",
                    },
                ],
                "sourceProblemOverlapGroupCount": 1,
                "sourceProblemOverlapGroups": [
                    {
                        "sourcePageId": "page-001",
                        "problemIds": ["problem-1", "problem-2"],
                        "overlapAreaRatio": 0.91,
                    }
                ],
                "reviewSummary": {
                    "riskFlagCounts": {
                        "source_problem_bbox_overlap": 2,
                    }
                },
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("overlap.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
        )

        self.assertEqual(2, summary["source_problem_bbox_overlap_count"])
        self.assertEqual(1, summary["source_problem_overlap_group_count"])
        self.assertEqual(
            {"source_problem_bbox_overlap": 2},
            summary["actionable_risk_flag_counts"],
        )
        self.assertTrue(summary["needs_review"])

    def test_summarize_export_response_counts_passage_groups_and_fragments(self):
        payload = {
            "ok": True,
            "session": {
                "pages": [],
                "problems": [
                    {"id": "p22", "problemNumber": 22, "passageGroupId": "hwp-continuation-passage-22-26"},
                    {"id": "p23", "problemNumber": 23, "passageGroupId": "hwp-continuation-passage-22-26"},
                    {
                        "id": "page-008-continuation",
                        "passageGroupId": "hwp-continuation-passage-22-26",
                        "passageRole": "passage_fragment",
                        "riskFlags": ["marker_document_continuation"],
                    },
                ],
                "passageGroups": [
                    {
                        "groupId": "hwp-continuation-passage-22-26",
                        "problemCount": 5,
                        "detectedProblemCount": 6,
                        "fragmentProblemCount": 1,
                        "problemNumbers": [22, 23, 24, 25, 26],
                        "continuesAcrossPages": True,
                    }
                ],
                "passageGroupCount": 1,
                "passageProblemCount": 5,
                "crossPagePassageGroupCount": 1,
                "reviewSummary": {},
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("passage.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
        )

        self.assertEqual(1, summary["passage_group_count"])
        self.assertEqual(5, summary["passage_problem_count"])
        self.assertEqual(1, summary["passage_fragment_count"])
        self.assertEqual(1, summary["cross_page_passage_group_count"])
        self.assertFalse(summary["needs_review"])

    def test_summarize_export_response_prefers_review_summary_hwp_segmentation_counts(self):
        payload = {
            "ok": True,
            "session": {
                "pages": [],
                "problems": [],
                "reviewSummary": {
                    "hwpProblemCountMismatchCount": 3,
                    "hwpOversegmentationCount": 2,
                },
            },
        }

        summary = verify_hwp_samples.summarize_export_response(
            payload,
            source_path=Path("summary-counts.hwp"),
            subject="국어",
            output_dir=Path("out"),
            elapsed_s=0.5,
        )

        self.assertEqual(3, summary["hwp_problem_count_mismatch_count"])
        self.assertEqual(2, summary["hwp_oversegmentation_count"])
        self.assertTrue(summary["needs_review"])

    def test_actionable_risk_flags_keep_sparse_marker_risks_without_hwp_count_match(self):
        counts = verify_hwp_samples.actionable_risk_flag_counts(
            {
                "fallback_grouping": 3,
                "no_problem_markers": 1,
                "ocr_disabled": 3,
                "sparse_segmentation": 2,
            }
        )

        self.assertEqual(
            {
                "fallback_grouping": 3,
                "no_problem_markers": 1,
                "sparse_segmentation": 2,
            },
            counts,
        )

    def test_run_batch_uses_absolute_output_directories(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source_dir = root / "samples"
            source_dir.mkdir()
            sample = source_dir / "평가원 수학 양식.hwp"
            sample.write_bytes(b"hwp")
            output_dir = Path("relative-looking")
            seen_output_dirs = []
            seen_export_flags = []

            def fake_post_export(**kwargs):
                seen_output_dirs.append(kwargs["output_dir"])
                seen_export_flags.append(kwargs["export_edb"])
                return {"ok": True, "session": {"pages": [], "problems": []}}

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(verify_hwp_samples, "post_export", side_effect=fake_post_export):
                    verify_hwp_samples.run_batch(source_dir=source_dir, output_dir=output_dir, export_edb=True)
                expected_root = (root / output_dir).resolve()
            finally:
                os.chdir(old_cwd)

            self.assertEqual(1, len(seen_output_dirs))
            self.assertTrue(seen_output_dirs[0].is_absolute())
            self.assertTrue(seen_output_dirs[0].resolve().is_relative_to(expected_root))
            self.assertEqual([True], seen_export_flags)

    def test_run_batch_can_verify_selected_samples_only(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source_dir = root / "samples"
            source_dir.mkdir()
            for name in [
                "평가원 국어 양식.hwp",
                "평가원 영어 양식.hwp",
                "학교 기출 시험지 양식.hwp",
            ]:
                (source_dir / name).write_bytes(b"hwp")
            seen_sources = []

            def fake_post_export(**kwargs):
                seen_sources.append(verify_hwp_samples.normalized_text(kwargs["source_path"].name))
                return {"ok": True, "session": {"pages": [], "problems": []}}

            with mock.patch.object(verify_hwp_samples, "post_export", side_effect=fake_post_export):
                rows = verify_hwp_samples.run_batch(
                    source_dir=source_dir,
                    output_dir=root / "out",
                    include=["평가원"],
                    limit=1,
                )

        self.assertEqual(["평가원 국어 양식.hwp"], seen_sources)
        self.assertEqual(["평가원 국어 양식.hwp"], [row["file"] for row in rows])

    def test_run_batch_writes_markdown_report(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source_dir = root / "samples"
            source_dir.mkdir()
            sample = source_dir / "평가원 영어 양식.hwp"
            sample.write_bytes(b"hwp")
            output_dir = root / "out"

            def fake_post_export(**_kwargs):
                return {
                    "ok": True,
                    "edbPath": str(output_dir / "mvp_board.edb"),
                    "edbValidation": {
                        "validated": True,
                        "recordCountActual": 45,
                        "recordCountHint": 45,
                    },
                    "session": {
                        "pages": [],
                        "problems": [],
                        "reviewSummary": {},
                    },
                }

            with mock.patch.object(verify_hwp_samples, "post_export", side_effect=fake_post_export):
                verify_hwp_samples.run_batch(
                    source_dir=source_dir,
                    output_dir=output_dir,
                    export_edb=True,
                )

            markdown = (output_dir / "summary.md").read_text(encoding="utf-8")

        self.assertIn("# HWP Batch Verification", markdown)
        self.assertIn("Batch summary: samples 1/1 OK", markdown)
        self.assertIn("| file | ok | problems | pages | cache | review | risk | edb | elapsed |", markdown)
        self.assertIn("| 평가원 영어 양식.hwp | OK | 0 | 0 | - | - | - | OK 45/45 |", markdown)

    def test_post_export_sends_export_edb_flag(self):
        requests = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok": true, "session": {"pages": [], "problems": []}}'

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeResponse()

        with mock.patch.object(verify_hwp_samples.urllib.request, "urlopen", side_effect=fake_urlopen):
            verify_hwp_samples.post_export(
                app_url="http://127.0.0.1:8765",
                source_path=Path("sample.hwp"),
                output_dir=Path("out"),
                subject="국어",
                timeout_seconds=123,
                export_edb=True,
            )

        request, timeout = requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(123, timeout)
        self.assertTrue(body["exportEdb"])
        self.assertTrue(body["preview"])

    def test_summarize_batch_rolls_up_status_and_top_risks(self):
        rows = [
            {
                "ok": True,
                "needs_review": True,
                "problem_count": 46,
                "core_problem_count": 45,
                "supplemental_item_count": 1,
                "pages": 23,
                "elapsed_s": 41.29,
                "hwp_cache_hit_page_count": 20,
                "hwp_renderer_cache_hit_count": 0,
                "hwp_normalized_cache_hit_count": 20,
                "risk_flags": ["problem_per_block", "ocr_disabled"],
                "risk_flag_counts": {"problem_per_block": 39, "ocr_disabled": 23},
                "actionable_risk_flag_counts": {"problem_per_block": 39},
                "source_problem_bbox_overlap_count": 0,
                "source_problem_overlap_group_count": 0,
                "warnings": [],
                "hwp_problem_count_mismatch_flags": [],
                "edb_expected": True,
                "edb_validated": True,
                "classin_preflight_expected": True,
                "classin_preflight_passed": True,
                "classin_preflight_issue_count": 0,
                "classin_preflight_blocking_issue_count": 0,
                "classin_preflight_issue_types": [],
            },
            {
                "ok": True,
                "needs_review": False,
                "problem_count": 45,
                "core_problem_count": 45,
                "supplemental_item_count": 0,
                "pages": 12,
                "elapsed_s": 24.29,
                "hwp_cache_hit_page_count": 6,
                "hwp_renderer_cache_hit_count": 6,
                "hwp_normalized_cache_hit_count": 0,
                "risk_flags": ["ocr_disabled"],
                "risk_flag_counts": {"ocr_disabled": 12},
                "actionable_risk_flag_counts": {},
                "source_problem_bbox_overlap_count": 0,
                "source_problem_overlap_group_count": 0,
                "warnings": [],
                "hwp_problem_count_mismatch_flags": [],
                "edb_expected": True,
                "edb_validated": False,
                "classin_preflight_expected": True,
                "classin_preflight_passed": False,
                "classin_preflight_issue_count": 1,
                "classin_preflight_blocking_issue_count": 1,
                "classin_preflight_issue_types": ["board_placement_overlap"],
            },
            {
                "ok": False,
                "needs_review": True,
                "elapsed_s": 1.0,
                "risk_flags": ["hwp_problem_count_mismatch", "hwp_oversegmentation"],
                "risk_flag_counts": {
                    "hwp_problem_count_mismatch": 2,
                    "hwp_oversegmentation": 1,
                    "source_problem_bbox_overlap": 4,
                },
                "source_problem_bbox_overlap_count": 4,
                "source_problem_overlap_group_count": 2,
                "warnings": ["conversion failed"],
                "classin_preflight_expected": True,
                "classin_preflight_passed": False,
                "classin_preflight_issue_count": 2,
                "classin_preflight_blocking_issue_count": 2,
                "classin_preflight_issue_types": ["board_placement_overlap", "source_problem_bbox_overlap"],
            },
        ]

        summary = verify_hwp_samples.summarize_batch(rows)

        self.assertEqual(3, summary["sample_count"])
        self.assertEqual(2, summary["ok_count"])
        self.assertEqual(1, summary["failed_count"])
        self.assertEqual(2, summary["needs_review_count"])
        self.assertEqual(91, summary["problem_count"])
        self.assertEqual(90, summary["core_problem_count"])
        self.assertEqual(1, summary["supplemental_item_count"])
        self.assertEqual(35, summary["page_count"])
        self.assertEqual(26, summary["hwp_cache_hit_page_count"])
        self.assertEqual(6, summary["hwp_renderer_cache_hit_count"])
        self.assertEqual(20, summary["hwp_normalized_cache_hit_count"])
        self.assertEqual(0.743, summary["hwp_cache_hit_rate"])
        self.assertEqual(66.58, summary["elapsed_s"])
        self.assertEqual(1, summary["warning_count"])
        self.assertEqual(2, summary["hwp_problem_count_mismatch_count"])
        self.assertEqual(1, summary["hwp_oversegmentation_count"])
        self.assertEqual(4, summary["source_problem_bbox_overlap_count"])
        self.assertEqual(2, summary["source_problem_overlap_group_count"])
        self.assertEqual(2, summary["edb_expected_count"])
        self.assertEqual(1, summary["edb_validated_count"])
        self.assertEqual(1, summary["edb_missing_count"])
        self.assertEqual(3, summary["classin_preflight_expected_count"])
        self.assertEqual(1, summary["classin_preflight_passed_count"])
        self.assertEqual(3, summary["classin_preflight_issue_count"])
        self.assertEqual(3, summary["classin_preflight_blocking_issue_count"])
        self.assertEqual(
            [
                {"flag": "problem_per_block", "count": 39},
                {"flag": "ocr_disabled", "count": 35},
                {"flag": "source_problem_bbox_overlap", "count": 4},
                {"flag": "hwp_problem_count_mismatch", "count": 2},
                {"flag": "hwp_oversegmentation", "count": 1},
            ],
            summary["top_risk_flags"],
        )
        self.assertEqual(
            [
                {"flag": "problem_per_block", "count": 39},
                {"flag": "source_problem_bbox_overlap", "count": 4},
                {"flag": "hwp_problem_count_mismatch", "count": 2},
                {"flag": "hwp_oversegmentation", "count": 1},
            ],
            summary["top_actionable_risk_flags"],
        )
        self.assertEqual(
            [
                {"type": "board_placement_overlap", "count": 2},
                {"type": "source_problem_bbox_overlap", "count": 1},
            ],
            summary["top_classin_preflight_issue_types"],
        )

    def test_main_fails_when_export_edb_is_missing_even_without_fail_on_review(self):
        args = type(
            "Args",
            (),
            {
                "source_dir": Path("samples"),
                "output_dir": Path("out"),
                "app_url": "http://127.0.0.1:8765",
                "timeout_seconds": 240,
                "export_edb": True,
                "include": [],
                "limit": None,
                "fail_on_review": False,
            },
        )()
        rows = [
            {
                "ok": True,
                "needs_review": True,
                "edb_expected": True,
                "edb_validated": False,
            }
        ]

        with (
            mock.patch.object(verify_hwp_samples, "parse_args", return_value=args),
            mock.patch.object(verify_hwp_samples, "run_batch", return_value=rows),
            mock.patch.object(verify_hwp_samples, "format_markdown_table", return_value="table"),
            mock.patch.object(verify_hwp_samples, "format_batch_summary", return_value="summary"),
            mock.patch("builtins.print"),
        ):
            exit_code = verify_hwp_samples.main()

        self.assertEqual(3, exit_code)

    def test_main_fails_when_classin_preflight_has_issues_even_without_fail_on_review(self):
        args = type(
            "Args",
            (),
            {
                "source_dir": Path("samples"),
                "output_dir": Path("out"),
                "app_url": "http://127.0.0.1:8765",
                "timeout_seconds": 240,
                "export_edb": True,
                "include": [],
                "limit": None,
                "fail_on_review": False,
            },
        )()
        rows = [
            {
                "ok": True,
                "needs_review": False,
                "edb_expected": True,
                "edb_validated": True,
                "classin_preflight_expected": True,
                "classin_preflight_passed": False,
                "classin_preflight_issue_count": 1,
                "classin_preflight_issue_types": ["board_placement_overlap"],
            }
        ]

        with (
            mock.patch.object(verify_hwp_samples, "parse_args", return_value=args),
            mock.patch.object(verify_hwp_samples, "run_batch", return_value=rows),
            mock.patch.object(verify_hwp_samples, "format_markdown_table", return_value="table"),
            mock.patch.object(verify_hwp_samples, "format_batch_summary", return_value="summary"),
            mock.patch("builtins.print"),
        ):
            exit_code = verify_hwp_samples.main()

        self.assertEqual(4, exit_code)

    def test_main_allows_nonblocking_classin_preflight_warnings_without_fail_on_review(self):
        args = type(
            "Args",
            (),
            {
                "source_dir": Path("samples"),
                "output_dir": Path("out"),
                "app_url": "http://127.0.0.1:8765",
                "timeout_seconds": 240,
                "export_edb": True,
                "include": [],
                "limit": None,
                "fail_on_review": False,
            },
        )()
        rows = [
            {
                "ok": True,
                "needs_review": True,
                "edb_expected": True,
                "edb_validated": True,
                "classin_preflight_expected": True,
                "classin_preflight_passed": False,
                "classin_preflight_issue_count": 4,
                "classin_preflight_issue_types": ["review_flags_remaining"],
            }
        ]

        with (
            mock.patch.object(verify_hwp_samples, "parse_args", return_value=args),
            mock.patch.object(verify_hwp_samples, "run_batch", return_value=rows),
            mock.patch.object(verify_hwp_samples, "format_markdown_table", return_value="table"),
            mock.patch.object(verify_hwp_samples, "format_batch_summary", return_value="summary"),
            mock.patch("builtins.print"),
        ):
            exit_code = verify_hwp_samples.main()

        self.assertEqual(0, exit_code)

    def test_summarize_batch_demotes_fallback_grouping_for_hwp_count_matches(self):
        rows = [
            {
                "ok": True,
                "needs_review": False,
                "problem_count": 45,
                "core_problem_count": 45,
                "pages": 23,
                "elapsed_s": 41.29,
                "risk_flags": [
                    "fallback_grouping",
                    "large_block_dominance",
                    "ocr_disabled",
                    "problem_per_block",
                    "no_problem_markers",
                    "sparse_segmentation",
                ],
                "risk_flag_counts": {
                    "fallback_grouping": 23,
                    "large_block_dominance": 8,
                    "no_problem_markers": 1,
                    "ocr_disabled": 23,
                    "problem_per_block": 8,
                    "sparse_segmentation": 2,
                },
                "hwp_text_problem_count_matches": True,
                "hwp_layout_problem_count_matches": True,
                "warnings": [],
                "hwp_problem_count_mismatch_flags": [],
            }
        ]

        summary = verify_hwp_samples.summarize_batch(rows)

        self.assertEqual(
            [
                {"flag": "fallback_grouping", "count": 23},
                {"flag": "ocr_disabled", "count": 23},
                {"flag": "large_block_dominance", "count": 8},
                {"flag": "problem_per_block", "count": 8},
                {"flag": "sparse_segmentation", "count": 2},
                {"flag": "no_problem_markers", "count": 1},
            ],
            summary["top_risk_flags"],
        )
        self.assertEqual(0, summary["needs_review_count"])
        self.assertEqual([], summary["top_actionable_risk_flags"])

    def test_summarize_batch_rolls_up_passage_metrics(self):
        rows = [
            {
                "ok": True,
                "needs_review": False,
                "passage_group_count": 3,
                "passage_problem_count": 14,
                "passage_fragment_count": 1,
                "cross_page_passage_group_count": 2,
                "risk_flags": [],
                "risk_flag_counts": {},
                "actionable_risk_flag_counts": {},
            },
            {
                "ok": True,
                "needs_review": False,
                "passage_group_count": 2,
                "passage_problem_count": 11,
                "passage_fragment_count": 1,
                "cross_page_passage_group_count": 2,
                "risk_flags": [],
                "risk_flag_counts": {},
                "actionable_risk_flag_counts": {},
            },
        ]

        summary = verify_hwp_samples.summarize_batch(rows)
        text = verify_hwp_samples.format_batch_summary(summary)

        self.assertEqual(5, summary["passage_group_count"])
        self.assertEqual(25, summary["passage_problem_count"])
        self.assertEqual(2, summary["passage_fragment_count"])
        self.assertEqual(4, summary["cross_page_passage_group_count"])
        self.assertIn("passage groups 5", text)
        self.assertIn("passage questions 25", text)
        self.assertIn("fragments 2", text)
        self.assertIn("cross-page 4", text)

    def test_format_batch_summary_mentions_review_and_top_risk(self):
        summary = {
            "sample_count": 8,
            "ok_count": 8,
            "failed_count": 0,
            "needs_review_count": 8,
            "problem_count": 293,
            "core_problem_count": 282,
            "supplemental_item_count": 11,
            "page_count": 118,
            "hwp_cache_hit_page_count": 118,
            "hwp_renderer_cache_hit_count": 8,
            "hwp_normalized_cache_hit_count": 110,
            "hwp_cache_hit_rate": 1.0,
            "elapsed_s": 219.07,
            "warning_count": 1,
            "hwp_problem_count_mismatch_count": 0,
            "hwp_oversegmentation_count": 0,
            "source_problem_bbox_overlap_count": 2,
            "source_problem_overlap_group_count": 1,
            "top_risk_flags": [
                {"flag": "problem_per_block", "count": 8},
                {"flag": "large_block_dominance", "count": 6},
            ],
            "top_actionable_risk_flags": [
                {"flag": "problem_per_block", "count": 8},
            ],
            "edb_expected_count": 8,
            "edb_validated_count": 8,
            "edb_missing_count": 0,
            "classin_preflight_expected_count": 8,
            "classin_preflight_passed_count": 7,
            "classin_preflight_issue_count": 2,
            "classin_preflight_blocking_issue_count": 2,
            "top_classin_preflight_issue_types": [
                {"type": "board_placement_overlap", "count": 2},
            ],
        }

        text = verify_hwp_samples.format_batch_summary(summary)

        self.assertIn("samples 8/8 OK", text)
        self.assertIn("review 8", text)
        self.assertIn("problems 282+11", text)
        self.assertIn("cache 118/118", text)
        self.assertIn("mismatch 0", text)
        self.assertIn("overseg 0", text)
        self.assertIn("source overlap 2/1", text)
        self.assertIn("edb 8/8", text)
        self.assertIn("preflight 7/8", text)
        self.assertIn("blocking 2", text)
        self.assertIn("preflight issues board_placement_overlap:2", text)
        self.assertIn("top risk problem_per_block:8", text)
        self.assertIn("actionable problem_per_block:8", text)

    def test_format_markdown_table_uses_risk_flag_counts_when_available(self):
        rows = [
            {
                "file": "sample.hwp",
                "ok": True,
                "problem_count": 46,
                "pages": 23,
                "hwp_cache_hit_page_count": 20,
                "hwp_renderer_cache_hit_count": 5,
                "hwp_normalized_cache_hit_count": 15,
                "review_status_counts": {"check_needed": 24, "normal": 45},
                "risk_flags": ["problem_per_block", "ocr_disabled"],
                "risk_flag_counts": {"problem_per_block": 39, "ocr_disabled": 23},
                "edb_expected": True,
                "edb_validated": True,
                "edb_record_count_actual": 46,
                "edb_record_count_hint": 46,
                "elapsed_s": 41.29,
            }
        ]

        table = verify_hwp_samples.format_markdown_table(rows)

        self.assertIn("| file | ok | problems | pages | cache | review | risk | edb | elapsed |", table)
        self.assertIn("problem_per_block:39, ocr_disabled:23", table)
        self.assertIn("| sample.hwp | OK | 46 | 23 | 20/23 · r5/n15 | check_needed:24, normal:45 | problem_per_block:39, ocr_disabled:23 | OK 46/46 | 41.29 |", table)

    def test_format_markdown_report_lists_artifact_paths(self):
        rows = [
            {
                "file": "sample.hwp",
                "ok": True,
                "problem_count": 45,
                "pages": 12,
                "edb_expected": True,
                "edb_validated": True,
                "edb_path": "/tmp/hwp out/sample/mvp_board.edb",
                "edb_record_count_actual": 45,
                "edb_record_count_hint": 45,
                "output_dir": "/tmp/hwp out/sample",
                "elapsed_s": 3.2,
            }
        ]

        report = verify_hwp_samples.format_markdown_report(rows)

        self.assertIn("## Artifacts", report)
        self.assertIn("sample.hwp", report)
        self.assertIn("Output: `/tmp/hwp out/sample`", report)
        self.assertIn("EDB: `/tmp/hwp out/sample/mvp_board.edb`", report)


if __name__ == "__main__":
    unittest.main()
