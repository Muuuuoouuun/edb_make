from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


class TestSessionEditState(unittest.TestCase):
    def test_empty_and_sample_never_claim_saved_data(self) -> None:
        run_node(NODE_SETUP + r"""
        const api = placementSandbox();
        for (const items of [[], [{ id: 'sample' }]]) {
          const state = api.deriveSessionEditState(null, items, '미리보기', 1, 'classin-grid');
          assert.equal(state.canSave, false);
          assert.equal(state.dirty, false);
          assert.ok(!state.label.endsWith('저장됨'));
        }
        """)

    def test_name_processing_and_layout_changes_share_a_persisted_baseline(self) -> None:
        run_node(NODE_SETUP + r"""
        const api = placementSandbox();
        const { session, items } = savedFixture(api);
        const before = JSON.stringify(session);
        assert.equal(api.deriveSessionEditState(session, items, session.session_name, 1, 'classin-grid').dirty, false);
        const variants = [
          { items, name: '수학 수업 2차', gap: 'classin-grid' },
          { items: [{ ...items[0], step: 's1' }, items[1]], name: session.session_name, gap: 'classin-grid' },
          { items: [api.applyPlacementPatchToItem(items[0], { scaleRatio: 0.8 }), items[1]], name: session.session_name, gap: 'classin-grid' },
          { items, name: session.session_name, gap: 'compact' },
        ];
        for (const draft of variants) {
          const dirty = api.deriveSessionEditState(session, draft.items, draft.name, 1, draft.gap);
          assert.equal(dirty.dirty, true, JSON.stringify(draft));
          assert.equal(dirty.canSave, true);
          const busy = api.deriveSessionEditState(session, draft.items, draft.name, 1, draft.gap, true);
          assert.equal(busy.dirty, true, 'starting a save must not clear the dirty baseline');
          assert.equal(busy.canSave, false);
          assert.equal(busy.tone, 'busy');
          const restored = api.materializeSessionForItems(session, draft.items, draft.name, 1, draft.gap);
          const reloaded = api.reflowItemsForBoardOrder(restored.problems.map(api.mapProblemToItem), 1.2, 1, draft.gap);
          assert.equal(api.deriveSessionEditState(restored, reloaded, restored.session_name, 1, draft.gap).dirty, false);
        }
        assert.equal(JSON.stringify(session), before, 'rendering save state must not mutate the server baseline');
        """)

    def test_name_only_save_persists_and_failure_keeps_the_draft(self) -> None:
        run_node(NODE_SETUP + r"""
        (async () => {
          for (const outcome of ['saved', 'failed', 'layout-rebased']) {
            const fail = outcome === 'failed';
            const conflict = outcome === 'layout-rebased';
            const api = placementSandbox();
            const { session, items } = savedFixture(api);
            const requests = [], errors = [];
            Object.assign(api, { session, items: conflict ? [{ ...items[0], step: 's1' }, items[1]] : items, fileName: '변경한 수업명', boardColumns: 1,
              layoutGapMode: 'classin-grid', mutatingRef: { current: false },
              appendBoundedHistory: require('./ui_prototype/reorder.js').appendBoundedHistory,
              UNDO_HISTORY_LIMIT: 20, setHistoryStack() {}, setLoading() {}, setMutating() {},
              setItems: value => { api.items = value; }, setActiveId() {},
              setSession: value => { api.session = value; }, setPublished() {}, refreshSessionHistory() {},
              showToast() {}, showSimpleErrorToast: error => errors.push(error),
              postRestoreBoardLayoutWithConflictRetry: async candidate => {
                requests.push(candidate);
                if (fail) throw new Error('save failed');
                return { session: conflict ? session : candidate, conflictBase: conflict ? session : null };
              },
            });
            vm.runInContext(section('  const savePlacement = async () =>', '  const reorder =')
              + '\nglobalThis.savePlacement = savePlacement;', api);
            assert.equal(await api.savePlacement(), !fail);
            assert.equal(requests.length, 1, 'a name-only change must not be skipped as unchanged layout');
            assert.equal(requests[0].session_name, '변경한 수업명');
            const status = api.deriveSessionEditState(api.session, api.items, api.fileName, 1, api.layoutGapMode);
            assert.equal(status.dirty, fail || conflict);
            if (conflict) {
              assert.equal(api.items[0].step, 's1', 'layout-only rebase must retain unsaved processing changes');
              assert.equal(api.fileName, '변경한 수업명');
              assert.equal(api.session.session_name, session.session_name, 'status must keep the actual server baseline');
            }
            assert.equal(errors.length, fail ? 1 : 0);
            assert.equal(api.mutatingRef.current, false);
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == '__main__':
    unittest.main()
