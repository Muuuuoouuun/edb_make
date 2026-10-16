from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiReviewFilterHelper(unittest.TestCase):
    def test_supplemental_filter_matches_continuation_problem_variants(self) -> None:
        run_node(
            """
            const { problemMatchesReviewFilter } = require('./ui_prototype/review_filters.js');
            const cases = [
              { id: 'p1-continuation' },
              { id: 'p2', riskFlags: ['marker_document_continuation'] },
              { id: 'p3', metadata: { marker_document_continuation: true } },
              { id: 'p4', passageRole: 'passage_fragment' },
              { id: 'p5', metadata: { supplemental_item: true } },
            ];
            if (!cases.every(problem => problemMatchesReviewFilter(problem, 'supplemental'))) {
              throw new Error('supplemental filter should match continuation and passage-fragment variants');
            }
            if (problemMatchesReviewFilter({ id: 'p6', reviewStatus: 'normal' }, 'supplemental')) {
              throw new Error('normal core problem should not match supplemental filter');
            }
            """
        )

    def test_passage_filter_matches_shared_passage_problem_variants(self) -> None:
        run_node(
            """
            const { problemMatchesReviewFilter } = require('./ui_prototype/review_filters.js');
            const cases = [
              { id: 'p1', passageGroupId: 'page-1-passage-13-16' },
              { id: 'p2', passage_group_id: 'page-1-passage-13-16' },
              { id: 'p3', metadata: { passage_group_id: 'page-1-passage-13-16' } },
            ];
            if (!cases.every(problem => problemMatchesReviewFilter(problem, 'passage'))) {
              throw new Error('passage filter should match shared passage variants');
            }
            if (problemMatchesReviewFilter({ id: 'p4', reviewStatus: 'normal' }, 'passage')) {
              throw new Error('normal ungrouped problem should not match passage filter');
            }
            """
        )

    def test_passage_review_filter_matches_only_review_queue_problem_ids(self) -> None:
        run_node(
            """
            const { problemMatchesReviewFilter } = require('./ui_prototype/review_filters.js');
            const options = { passageReviewProblemIds: ['p31', 'p32-fragment'] };
            if (!problemMatchesReviewFilter({ id: 'p31', passageGroupId: 'hwp-text-passage-31-34' }, 'passage-review', options)) {
              throw new Error('passage-review filter should match queued core problem');
            }
            if (!problemMatchesReviewFilter({ problem_id: 'p32-fragment', passageGroupId: 'hwp-text-passage-31-34' }, 'passage-review', options)) {
              throw new Error('passage-review filter should match queued fragment problem');
            }
            if (problemMatchesReviewFilter({ id: 'p33', passageGroupId: 'hwp-text-passage-31-34' }, 'passage-review', options)) {
              throw new Error('passage-review filter should not match non-queued passage problem');
            }
            if (problemMatchesReviewFilter({ id: 'p1', reviewStatus: 'check_needed' }, 'passage-review', options)) {
              throw new Error('passage-review filter should not match generic review risks');
            }
            """
        )

    def test_status_filters_still_match_review_status(self) -> None:
        run_node(
            """
            const { problemMatchesReviewFilter } = require('./ui_prototype/review_filters.js');
            const problem = { id: 'p1', reviewStatus: 'check_needed' };
            if (!problemMatchesReviewFilter(problem, 'check_needed')) {
              throw new Error('check_needed filter should match reviewStatus');
            }
            if (problemMatchesReviewFilter(problem, 'normal')) {
              throw new Error('normal filter should not match check_needed item');
            }
            if (!problemMatchesReviewFilter(problem, 'all')) {
              throw new Error('all filter should match every problem');
            }
            """
        )

    def test_count_review_filters_includes_supplemental_count(self) -> None:
        run_node(
            """
            const { countReviewFilters } = require('./ui_prototype/review_filters.js');
            const counts = countReviewFilters([
              { id: 'p1', reviewStatus: 'normal' },
              { id: 'p2-continuation', reviewStatus: 'check_needed' },
              { id: 'p3', reviewStatus: 'failed' },
            ]);
            const expected = { all: 3, normal: 1, check_needed: 1, failed: 1, supplemental: 1 };
            for (const [key, value] of Object.entries(expected)) {
              if (counts[key] !== value) {
                throw new Error(`${key} expected ${value}, got ${counts[key]}`);
              }
            }
            """
        )

    def test_count_review_filters_includes_passage_problem_and_group_counts(self) -> None:
        run_node(
            """
            const { countReviewFilters } = require('./ui_prototype/review_filters.js');
            const counts = countReviewFilters([
              { id: 'p13', reviewStatus: 'normal', passageGroupId: 'page-1-passage-13-16' },
              { id: 'p14', reviewStatus: 'normal', passage_group_id: 'page-1-passage-13-16' },
              { id: 'p21', reviewStatus: 'normal', passageGroupId: 'page-2-passage-21-22' },
              { id: 'p1', reviewStatus: 'normal' },
            ]);
            if (counts.passage !== 3) {
              throw new Error(`passage expected 3, got ${counts.passage}`);
            }
            if (counts.passageGroups !== 2) {
              throw new Error(`passageGroups expected 2, got ${counts.passageGroups}`);
            }
            """
        )

    def test_review_mode_copy_uses_content_target_before_input_intent(self) -> None:
        run_node(
            """
            const { reviewModeCopy } = require('./ui_prototype/review_filters.js');
            const copy = reviewModeCopy({
              content_target: 'shared-passages',
              input_intent: 'page-as-is',
              pages: [{}, {}],
            }, { core: 0, supplemental: 3, total: 3 });
            if (copy.mode !== 'shared-passages' || copy.title !== '지문 검수') {
              throw new Error(`unexpected review mode: ${JSON.stringify(copy)}`);
            }
            if (copy.countLabel !== '공통 지문 3개') {
              throw new Error(`unexpected passage count: ${copy.countLabel}`);
            }
            """
        )

    def test_review_mode_copy_labels_page_as_is_without_questions(self) -> None:
        run_node(
            """
            const { formatReviewModeCount, reviewModeCopy } = require('./ui_prototype/review_filters.js');
            const copy = reviewModeCopy({
              inputIntent: 'page-as-is',
              pages: Array.from({ length: 16 }, () => ({})),
            }, { core: 16, supplemental: 0, total: 16 });
            if (copy.title !== '페이지 검수' || copy.headerCountLabel !== '16페이지 원본') {
              throw new Error(`unexpected page-as-is copy: ${JSON.stringify(copy)}`);
            }
            const compact = formatReviewModeCount(copy.mode, { total: 1 }, { compact: true, pageCount: 1 });
            if (compact !== '페이지 원본') {
              throw new Error(`unexpected compact page label: ${compact}`);
            }
            if (JSON.stringify(copy).includes('문항')) {
              throw new Error(`page-as-is copy should not mention questions: ${JSON.stringify(copy)}`);
            }
            """
        )

    def test_review_mode_copy_keeps_problem_count_semantics(self) -> None:
        run_node(
            """
            const { reviewModeCopy } = require('./ui_prototype/review_filters.js');
            const copy = reviewModeCopy({
              input_intent: 'multi-problem',
              pages: [{}],
            }, { core: 4, supplemental: 1, total: 5 });
            if (copy.title !== '문항 검수' || copy.countLabel !== '4문항 + 자료 1') {
              throw new Error(`unexpected problem copy: ${JSON.stringify(copy)}`);
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
