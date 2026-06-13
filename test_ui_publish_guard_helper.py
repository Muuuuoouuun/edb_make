from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiPublishGuardHelper(unittest.TestCase):
    def test_detects_source_problem_bbox_overlap_before_publish(self) -> None:
        run_node(
            """
            const { findSourceProblemOverlaps } = require('./ui_prototype/publish_guard.js');
            const overlaps = findSourceProblemOverlaps([
              {
                id: 'p21',
                title: '21.',
                sourcePageId: 'page-001',
                bbox: { left: 40, top: 100, width: 520, height: 320 },
              },
              {
                id: 'p22',
                title: '22.',
                sourcePageId: 'page-001',
                bbox: { left: 60, top: 125, width: 500, height: 300 },
              },
            ]);
            if (overlaps.length !== 1) {
              throw new Error(`expected 1 source overlap, got ${overlaps.length}`);
            }
            const issue = overlaps[0];
            if (issue.type !== 'source_problem_bbox_overlap') {
              throw new Error(`unexpected issue type ${issue.type}`);
            }
            if (issue.problemId !== 'p21' || issue.nextProblemId !== 'p22') {
              throw new Error(`unexpected ids ${issue.problemId}/${issue.nextProblemId}`);
            }
            if (issue.sourcePageId !== 'page-001') {
              throw new Error(`unexpected page ${issue.sourcePageId}`);
            }
            if (!(issue.overlapAreaRatio >= 0.8)) {
              throw new Error(`expected high overlap ratio, got ${issue.overlapAreaRatio}`);
            }
            """
        )

    def test_ignores_source_bbox_overlap_across_different_pages(self) -> None:
        run_node(
            """
            const { findSourceProblemOverlaps } = require('./ui_prototype/publish_guard.js');
            const overlaps = findSourceProblemOverlaps([
              {
                id: 'p21',
                title: '21.',
                sourcePageId: 'page-001',
                bbox: { left: 40, top: 100, width: 520, height: 320 },
              },
              {
                id: 'p22',
                title: '22.',
                sourcePageId: 'page-002',
                bbox: { left: 60, top: 125, width: 500, height: 300 },
              },
            ]);
            if (overlaps.length !== 0) {
              throw new Error(`expected no cross-page overlap, got ${JSON.stringify(overlaps)}`);
            }
            """
        )

    def test_detects_requested_scale_overlap_before_publish(self) -> None:
        run_node(
            """
            const { findBoardPlacementOverlaps } = require('./ui_prototype/publish_guard.js');
            const overlaps = findBoardPlacementOverlaps([
              { id: 'p13', name: '13. 긴 지문', heightFrac: 1.1, placementScaleRatio: 1.4 },
              { id: 'p14', name: '14. 하위 문항', heightFrac: 0.8, placementScaleRatio: 1.0 },
            ]);
            if (overlaps.length !== 1) {
              throw new Error(`expected 1 overlap, got ${overlaps.length}`);
            }
            const issue = overlaps[0];
            if (issue.type !== 'board_placement_overlap') {
              throw new Error(`unexpected issue type ${issue.type}`);
            }
            if (issue.problemId !== 'p13' || issue.nextProblemId !== 'p14') {
              throw new Error(`unexpected ids ${issue.problemId}/${issue.nextProblemId}`);
            }
            if (!(issue.renderedBottomYPages > issue.nextTopYPages)) {
              throw new Error('rendered bottom should exceed next top');
            }
            """
        )

    def test_allows_safe_adjacent_placements(self) -> None:
        run_node(
            """
            const { findBoardPlacementOverlaps } = require('./ui_prototype/publish_guard.js');
            const overlaps = findBoardPlacementOverlaps([
              { id: 'p13', name: '13. 긴 지문', heightFrac: 1.1, placementScaleRatio: 1.0 },
              { id: 'p14', name: '14. 하위 문항', heightFrac: 0.8, placementScaleRatio: 1.0 },
            ]);
            if (overlaps.length !== 0) {
              throw new Error(`expected no overlap, got ${JSON.stringify(overlaps)}`);
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
