from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiPublishSummaryHelper(unittest.TestCase):
    def test_format_record_count_label_splits_core_and_supplemental_counts(self) -> None:
        run_node(
            """
            const { formatRecordCountLabel } = require('./ui_prototype/publish_summary.js');
            const label = formatRecordCountLabel({ coreProblemCount: 45, supplementalItemCount: 1, recordCount: 46 });
            if (label !== '45문항 + 자료 1') {
              throw new Error(`unexpected label ${label}`);
            }
            """
        )

    def test_format_record_count_label_falls_back_to_record_count(self) -> None:
        run_node(
            """
            const { formatRecordCountLabel } = require('./ui_prototype/publish_summary.js');
            const label = formatRecordCountLabel({ recordCountActual: 8 });
            if (label !== '8개 자료') {
              throw new Error(`unexpected fallback label ${label}`);
            }
            """
        )

    def test_normalize_publish_summary_keeps_count_label(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'lesson.edb',
              recordCount: 46,
              coreProblemCount: 45,
              supplementalItemCount: 1,
              recordCountLabel: '45문항 + 자료 1',
            });
            if (summary.recordCountLabel !== '45문항 + 자료 1') {
              throw new Error(`label not preserved: ${summary.recordCountLabel}`);
            }
            if (summary.coreProblemCount !== 45 || summary.supplementalItemCount !== 1) {
              throw new Error('core/supplemental counts not normalized');
            }
            """
        )

    def test_normalize_publish_summary_exposes_artifact_availability(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'missing.edb',
              edbPath: '/tmp/missing.edb',
              edbFileUri: '/api/file?path=missing',
              edbFileExists: false,
              outputDir: '/tmp/missing-output',
              outputDirExists: false,
            });
            if (summary.edbFileExists !== false || summary.outputDirExists !== false) {
              throw new Error('artifact existence flags were not preserved');
            }
            if (summary.canDownload !== false || summary.canOpenOutputDir !== false) {
              throw new Error('missing artifacts should disable publish actions');
            }
            if (summary.canOpenEdbFile !== false) {
              throw new Error('missing EDB file should disable local open action');
            }
            """
        )

    def test_normalize_publish_summary_enables_local_edb_open_when_path_exists(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'lesson.edb',
              edbPath: '/tmp/lesson.edb',
              edbFileUri: '/api/file?path=lesson',
              edbFileExists: true,
            });
            if (!summary.canOpenEdbFile) {
              throw new Error('existing EDB path should enable local open action');
            }
            """
        )

    def test_normalize_publish_summary_exposes_classin_handoff_actions(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'lesson.edb',
              edbFileUri: '/api/file?path=lesson',
              classinHandoffUri: '/api/file?path=handoff-json',
              classinHandoffMarkdownUri: '/api/file?path=handoff-md',
            });
            if (summary.classinHandoffUri !== '/api/file?path=handoff-json') {
              throw new Error('handoff json uri not normalized');
            }
            if (summary.classinHandoffMarkdownUri !== '/api/file?path=handoff-md') {
              throw new Error('handoff markdown uri not normalized');
            }
            if (!summary.canOpenClassinHandoff) {
              throw new Error('handoff action should be enabled');
            }
            """
        )

    def test_normalize_publish_summary_exposes_classin_review_status(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'lesson.edb',
              edbPath: '/tmp/lesson.edb',
              edbFileUri: '/api/file?path=lesson',
              edbFileExists: true,
              classinReview: {
                status: 'passed',
                statusLabel: 'ClassIn 확인 완료',
                reviewedAt: '2026-06-14T00:30:00+09:00',
              },
            });
            if (summary.classinReviewStatus !== 'passed') {
              throw new Error(`review status not normalized: ${summary.classinReviewStatus}`);
            }
            if (summary.classinReviewStatusLabel !== 'ClassIn 확인 완료') {
              throw new Error(`review label not normalized: ${summary.classinReviewStatusLabel}`);
            }
            if (!summary.classinReviewPassed) {
              throw new Error('passed review should be exposed');
            }
            if (summary.canMarkClassinReviewComplete !== false) {
              throw new Error('already passed review should not offer completion action');
            }
            """
        )

    def test_normalize_publish_summary_exposes_classin_preflight_status(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'lesson.edb',
              edbFileUri: '/api/file?path=lesson',
              classinPreflight: {
                status: 'needs_attention',
                passed: false,
                issueCount: 2,
                checkedProblemCount: 3,
              },
            });
            if (summary.classinPreflightStatus !== 'needs_attention') {
              throw new Error(`preflight status not normalized: ${summary.classinPreflightStatus}`);
            }
            if (summary.classinPreflightIssueCount !== 2) {
              throw new Error(`preflight issue count not normalized: ${summary.classinPreflightIssueCount}`);
            }
            if (summary.classinPreflightPassed !== false) {
              throw new Error('preflight passed flag should be false');
            }
            if (!summary.classinPreflightStatusLabel.includes('주의 2')) {
              throw new Error(`preflight label missing issue count: ${summary.classinPreflightStatusLabel}`);
            }
            """
        )

    def test_normalize_publish_summary_exposes_classin_handoff_readiness(self) -> None:
        run_node(
            """
            const { normalizePublishSummary } = require('./ui_prototype/publish_summary.js');
            const summary = normalizePublishSummary({
              edbFileName: 'lesson.edb',
              edbFileUri: '/api/file?path=lesson',
              classinHandoffStatus: 'needs_attention_before_classin',
              readyForClassIn: false,
            });
            if (summary.classinHandoffStatus !== 'needs_attention_before_classin') {
              throw new Error(`handoff status not normalized: ${summary.classinHandoffStatus}`);
            }
            if (summary.readyForClassIn !== false) {
              throw new Error('readyForClassIn should remain false');
            }
            if (!summary.classinHandoffStatusLabel.includes('주의')) {
              throw new Error(`handoff status label missing warning copy: ${summary.classinHandoffStatusLabel}`);
            }
            """
        )

    def test_format_publish_history_meta_includes_time_and_record_label(self) -> None:
        run_node(
            """
            const { formatPublishHistoryMeta } = require('./ui_prototype/publish_summary.js');
            const label = formatPublishHistoryMeta({
              publishedAt: '2026-06-13T20:18:25+09:00',
              recordCountLabel: '45문항 + 자료 1',
            });
            if (!label.includes('45문항 + 자료 1')) {
              throw new Error(`missing record label: ${label}`);
            }
            if (!label.includes('20:18') && !label.includes('08:18') && !label.includes('오후') && !label.includes('PM')) {
              throw new Error(`missing time: ${label}`);
            }
            """
        )

    def test_format_publish_history_meta_falls_back_to_record_label(self) -> None:
        run_node(
            """
            const { formatPublishHistoryMeta } = require('./ui_prototype/publish_summary.js');
            const label = formatPublishHistoryMeta({
              recordCountActual: 8,
            });
            if (label !== '8개 자료') {
              throw new Error(`unexpected fallback label ${label}`);
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
