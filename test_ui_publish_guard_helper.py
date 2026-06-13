from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiPublishGuardHelper(unittest.TestCase):
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
