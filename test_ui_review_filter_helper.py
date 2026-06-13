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
            ];
            if (!cases.every(problem => problemMatchesReviewFilter(problem, 'supplemental'))) {
              throw new Error('supplemental filter should match continuation variants');
            }
            if (problemMatchesReviewFilter({ id: 'p4', reviewStatus: 'normal' }, 'supplemental')) {
              throw new Error('normal core problem should not match supplemental filter');
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


if __name__ == "__main__":
    unittest.main()
