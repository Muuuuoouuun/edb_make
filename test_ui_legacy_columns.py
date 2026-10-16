"""Loading a legacy multi-column lesson must not silently convert its layout."""
from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


LEGACY_SETUP = NODE_SETUP + r"""
function legacyFixture(sandbox, columns) {
  const raw = { session_name: '기존 배치', boardColumns: columns,
    problems: Array.from({ length: 6 }, (_, index) => ({ id: `legacy-${index}`,
      title: `문제 ${index + 1}`, actualHeightPages: 0.8,
      boardColumnCount: columns, boardColumnIndex: index % columns,
    })) };
  const flow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, columns, 'classin-grid');
  const session = sandbox.materializeSessionForItems(raw,
    flow(raw.problems.map((problem, index) => sandbox.mapProblemToItem(problem, index, raw))),
    raw.session_name, columns, 'classin-grid');
  return { session, items: flow(session.problems.map((problem, index) =>
    sandbox.mapProblemToItem(problem, index, session))) };
}
"""


class TestLegacyColumns(unittest.TestCase):
    def test_column_count_recovers_from_session_and_problem_aliases(self):
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        assert.equal(sandbox.sessionBoardColumns(null), 1);
        assert.equal(sandbox.sessionBoardColumns({ problems: [] }), 1);
        for (const columns of [2, 3]) {
          for (const field of ['boardColumns', 'board_columns']) {
            assert.equal(sandbox.sessionBoardColumns({ [field]: columns }), columns);
          }
          for (const field of ['boardColumnCount', 'board_column_count', 'boardColumns', 'board_columns']) {
            assert.equal(sandbox.sessionBoardColumns({ problems: [{ [field]: columns }] }), columns, field);
          }
        }
        assert.equal(sandbox.sessionBoardColumns({ boardColumns: 1,
          problems: [{ boardColumnCount: 3 }] }), 1, 'explicit session layout wins');
        """)

    def test_actual_session_loader_preserves_rows_and_source_snapshot(self):
        run_node(LEGACY_SETUP + r"""
        const sandbox = placementSandbox();
        Object.assign(sandbox, { useCallback: callback => callback,
          boardColumnsRef: { current: 1 }, initialViewConsumedRef: { current: false },
          initialViewRef: { current: 'board' }, sessionPublishSummary: () => null,
          hasReviewPages: () => false, setUsingMock() {}, setPublished() {}, setView() {},
          setTweak: (key, value) => { sandbox[key] = value; },
          setItems: value => { sandbox.loadedItems = value; },
          setActiveId: value => { sandbox.activeId = value; },
          setSession: value => { sandbox.loadedSession = value; },
          setLayoutGapModeState: value => { sandbox.gapMode = value; },
          setFileName: value => { sandbox.fileName = value; },
        });
        vm.runInContext(section('  const applySession = useCallback(',
          '  // Replace state from a mutation response:')
          + '\nglobalThis.load = applySession;', sandbox);
        for (const columns of [2, 3]) {
          const { session, items } = legacyFixture(sandbox, columns);
          const before = JSON.stringify(session);
          assert.equal(sandbox.load(session), true);
          assert.equal(sandbox.boardColumns, columns);
          assert.equal(sandbox.boardColumnsRef.current, columns);
          const positions = values => JSON.stringify(values.map(item => [
            item.id, item.startYPages, item.boardColumnIndex, item.boardColumnCount,
          ]));
          assert.equal(positions(sandbox.loadedItems), positions(items));
          assert.equal(JSON.stringify(session), before, 'load must not rewrite saved data');
          assert.equal(sandbox.derivePlacementSaveState(session, sandbox.loadedItems,
            columns, 'classin-grid').dirty, false, 'opening alone is not a migration');
        }
        """)

    def test_save_round_trip_retains_legacy_columns_and_grouping(self):
        run_node(LEGACY_SETUP + r"""
        const sandbox = placementSandbox();
        for (const columns of [2, 3]) {
          const { session, items } = legacyFixture(sandbox, columns);
          const saved = sandbox.materializeSessionForItems(session, items,
            '이름만 바꾼 수업', columns, 'classin-grid');
          assert.equal(saved.boardColumns, columns);
          assert.equal(saved.board_columns ?? saved.boardColumns, columns);
          const restored = JSON.parse(JSON.stringify(saved));
          const reloaded = sandbox.reflowItemsForBoardOrder(restored.problems.map((problem, index) =>
            sandbox.mapProblemToItem(problem, index, restored)), 1.2,
            sandbox.sessionBoardColumns(restored), 'classin-grid');
          reloaded.forEach((item, index) => {
            assert.equal(item.boardColumnCount, columns);
            assert.equal(item.boardColumnIndex, items[index].boardColumnIndex);
            assert.equal(item.startYPages, items[index].startYPages);
          });
          assert.equal(reloaded[0].startYPages, reloaded[1].startYPages,
            'problems originally sharing a row must still share a row');
        }
        """)


if __name__ == '__main__':
    unittest.main()
