"""Interaction checks for panel navigation and explicit setting/save states."""
from __future__ import annotations

import unittest

from test_ui_sidebar_hierarchy import RENDER_SETUP, NODE_SETUP, run_node


class TestSidePanelControls(unittest.TestCase):
    def test_tab_keyboard_moves_selection_and_focus_without_consuming_other_keys(self) -> None:
        run_node(NODE_SETUP + r"""
        const focused = [];
        const sandbox = {
          tab: 'item', RIGHT_PANEL_TABS: ['item', 'placement', 'board'],
          panelTabRefs: { current: Object.fromEntries(['item', 'placement', 'board']
            .map(tab => [tab, { focus: () => focused.push(tab) }])) },
          setTab: tab => { sandbox.tab = tab; },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const handlePanelTabKeyDown =', '  const hangulDiagnostics =')
          + '\nglobalThis.pressTab = handlePanelTabKeyDown;', sandbox);
        let prevented = 0;
        const press = (key, extra = {}) => sandbox.pressTab({ key, preventDefault: () => prevented++, ...extra });
        for (const [key, expected] of [['ArrowRight', 'placement'], ['ArrowRight', 'board'],
          ['ArrowRight', 'item'], ['ArrowLeft', 'board'], ['Home', 'item'], ['End', 'board']]) {
          press(key);
          assert.equal(sandbox.tab, expected);
          assert.equal(focused.at(-1), expected);
        }
        const before = prevented;
        press('Tab'); press('Enter'); press('ArrowDown'); press('ArrowLeft', { altKey: true });
        assert.equal(prevented, before);
        assert.equal(sandbox.tab, 'board');
        """)

    def test_rendered_tabs_have_one_tab_stop_and_control_the_matching_hidden_panel(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        Object.assign(sandbox, { tab: 'item', itemPosLabel: '01 / 02', panelTabRefs: { current: {} },
          setTab: tab => { sandbox.tab = tab; }, handlePanelTabKeyDown() {},
          panelSaveState: { dirty: true, label: '미저장 변경' }, panelBusy: false, session: {},
        });
        const bar = section('      <div className="tab-bar" role="tablist"', '      <PublishPlanSummary');
        const openings = source.match(/<section className="side-tab-panel"[^\n]+>/g);
        assert.equal(openings.length, 3);
        compile(sandbox, 'globalThis.renderNavigation = () => (<>' + bar
          + openings.map(opening => opening + '</section>').join('') + '</>);');
        for (const selected of ['item', 'placement', 'board']) {
          sandbox.tab = selected;
          const tree = sandbox.renderNavigation();
          const tabs = walk(tree).filter(node => node.props.role === 'tab');
          const panels = walk(tree).filter(node => node.props.role === 'tabpanel');
          assert.equal(tabs.filter(node => node.props.tabIndex === 0).length, 1);
          for (const tab of tabs) {
            const panel = panels.find(node => node.props.id === tab.props['aria-controls']);
            assert.ok(panel, 'every tab must reference a mounted panel');
            assert.equal(panel.props['aria-labelledby'], tab.props.id);
            assert.equal(panel.props.hidden, !tab.props['aria-selected']);
            assert.equal(tab.props.tabIndex, tab.props['aria-selected'] ? 0 : -1);
          }
          assert.ok(text(findClass(tree, 'panel-session-state')).includes('미저장 변경'),
            'switching to app settings cannot hide unsaved lesson edits');
          tabs.find(node => node.props.id === 'side-tab-board').props.onClick();
          assert.equal(sandbox.tab, 'board');
        }
        sandbox.panelSaveState = { dirty: false, label: '샘플 미리보기 · 저장 안 됨' };
        sandbox.session = null;
        assert.ok(text(findClass(sandbox.renderNavigation(), 'panel-session-state')).includes('저장 안 됨'));
        """)

    def test_ai_preference_and_key_readiness_are_separate_and_named(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const choices = [];
        Object.assign(sandbox, { aiEnabled: true, userSettings: {}, aiToggleBusy: false, panelBusy: false,
          aiSaveError: '', onToggleAi() {}, persistAiPreference: value => choices.push(value) });
        const ai = section('            <div className="panel-section-hd settings-group-label">AI 사용·연결',
          '            <div className="row-control api-key-status">');
        compile(sandbox, 'globalThis.renderAi = () => (<' + '>' + ai + '</>);');
        const render = () => {
          Object.assign(sandbox, vm.runInContext('(() => {'
            + section('  const aiReady =', '  const placementLayoutItems =')
            + 'return { aiReady, aiConnectionLabel }; })()', sandbox));
          return sandbox.renderAi();
        };
        let tree = render();
        let toggle = walk(tree).find(node => node.type === 'input');
        assert.equal(toggle.props['aria-label'], 'AI 기능 사용 허용');
        assert.equal(toggle.props.checked, true, 'permission can be enabled before a key is entered');
        assert.ok(text(tree).includes('로컬 처리 중 · AI 연결 설정 필요'));
        toggle.props.onChange({ target: { checked: false } });
        assert.deepEqual(choices, [false], 'changing permission must not run recognition');
        sandbox.userSettings = { hasGeminiApiKey: true };
        assert.ok(text(render()).includes('AI 설정 준비됨'));
        sandbox.aiEnabled = false;
        assert.ok(text(render()).includes('로컬 처리 중 · AI 사용 꺼짐'));
        sandbox.aiToggleBusy = true;
        tree = render();
        assert.equal(walk(tree).find(node => node.type === 'input').props.disabled, true);
        assert.ok(text(tree).includes('저장 중…'));
        """)

    def test_ai_save_error_survives_failure_and_clears_only_on_retry(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = { aiToggleBusy: false, panelBusy: false, error: '', calls: 0,
          setAiSaveError: value => { sandbox.error = value; },
          onToggleAi: async () => { sandbox.calls++; return false; } };
        vm.createContext(sandbox);
        vm.runInContext(section('  const persistAiPreference =', '  const selectProcessingStep =')
          + '\nglobalThis.persistAi = persistAiPreference;', sandbox);
        (async () => {
          await sandbox.persistAi(true);
          assert.ok(sandbox.error.includes('저장하지 못했습니다'));
          sandbox.panelBusy = true;
          await sandbox.persistAi(false);
          assert.equal(sandbox.calls, 1);
          assert.ok(sandbox.error, 'an ignored click cannot erase the failure');
          sandbox.panelBusy = false;
          sandbox.onToggleAi = async () => { throw new Error('offline'); };
          await sandbox.persistAi(true);
          assert.ok(sandbox.error);
          sandbox.onToggleAi = async () => true;
          await sandbox.persistAi(true);
          assert.equal(sandbox.error, '');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_save_uses_lesson_dirty_state_and_blocks_edits_while_busy(self) -> None:
        run_node(NODE_SETUP + r"""
        const calls = [], patches = [];
        const sandbox = { session: {}, sessionEditState: { canSave: true },
          placementSaveState: { canSave: false }, panelBusy: false,
          savePlacement: async () => { calls.push('saved'); return true; }, placementTargets: [{ id: 'p1' }],
          setPlacementSaveError() {},
          setPlacements: updates => patches.push(updates),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const commitPlacement =', '  const persistAiPreference =')
          + section('  const updatePlacementTargets =', '  const selectPlacementScope =')
          + '\nglobalThis.commit = commitPlacement; globalThis.edit = updatePlacementTargets;', sandbox);
        (async () => {
          await sandbox.commit();
          assert.equal(calls.length, 1, 'name/processing-only drafts must be saveable');
          sandbox.panelBusy = true;
          await sandbox.commit(); sandbox.edit({ scaleRatio: 0.8 });
          assert.equal(calls.length, 1); assert.equal(patches.length, 0);
          sandbox.panelBusy = false; sandbox.sessionEditState.canSave = false;
          await sandbox.commit();
          assert.equal(calls.length, 1, 'saved lesson must not post another save');
          sandbox.edit({ scaleRatio: 0.8 });
          assert.equal(patches.length, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_failed_placement_save_stays_visible_across_tabs_until_retry_succeeds(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const item = { id: 'p1', placementScaleRatio: 0.8 };
        Object.assign(sandbox, { session: {}, sessionEditState: { canSave: true },
          placementSaveState: { canSave: true }, panelSaveState: { dirty: true, label: '미저장 변경' },
          panelBusy: false, item, items: [item], tab: 'placement', placementSaveError: '',
          placementTargetLabel: '현재 자료 1개', resetPlacement() {},
          savePlacement: async () => false,
          setPlacementSaveError: value => { sandbox.placementSaveError = value; },
        });
        vm.runInContext(section('  const commitPlacement =', '  const persistAiPreference =')
          + '\nglobalThis.commit = commitPlacement;', sandbox);
        const footer = section('          {items.length > 0 && <div className="tab-foot placement-tab-foot">',
          '\n        </>');
        compile(sandbox, 'globalThis.renderFooter = () => (<>{tab === "placement" && <>' + footer + '</>}</>);');
        const getError = () => findClass(sandbox.renderFooter(), 'placement-save-error');
        (async () => {
          await sandbox.commit();
          assert.equal(item.placementScaleRatio, 0.8, 'failed saves must not reset the local draft');
          assert.equal(sandbox.panelSaveState.dirty, true);
          assert.equal(getError().props.role, 'alert');
          assert.ok(text(getError()).includes('조정값은 유지됩니다'));
          sandbox.tab = 'board';
          assert.equal(getError(), undefined);
          sandbox.tab = 'placement';
          assert.ok(getError(), 'returning to the panel must retain the save failure');
          sandbox.savePlacement = async () => { throw new Error('offline'); };
          await sandbox.commit();
          assert.ok(getError());
          let finish;
          sandbox.savePlacement = () => new Promise(resolve => { finish = resolve; });
          const retry = sandbox.commit();
          assert.ok(getError(), 'starting a retry cannot claim the failed save recovered');
          finish(true); await retry;
          assert.equal(getError(), undefined);
          assert.equal(item.placementScaleRatio, 0.8);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_api_key_visibility_control_names_its_value_and_changes_input_type(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        Object.assign(sandbox, { showKey: false, keyDraft: 'entered-placeholder', keySaveBusy: false,
          userSettings: {}, setKeyDraft() {}, setKeySaveError() {},
          setShowKey: update => { sandbox.showKey = update(sandbox.showKey); } });
        const keyRow = section('                <div className="key-input-row">',
          '                <div className="settings-action-row"');
        compile(sandbox, 'globalThis.renderKey = () => (' + keyRow + ');');
        let tree = sandbox.renderKey();
        let button = walk(tree).find(node => node.type === 'button');
        assert.equal(button.props['aria-label'], 'Gemini API 키 보기');
        assert.equal(walk(tree).find(node => node.type === 'input').props.type, 'password');
        button.props.onClick();
        tree = sandbox.renderKey();
        assert.equal(walk(tree).find(node => node.type === 'button').props['aria-label'], 'Gemini API 키 숨기기');
        assert.equal(walk(tree).find(node => node.type === 'input').props.type, 'text');
        sandbox.keySaveBusy = true;
        assert.equal(walk(sandbox.renderKey()).find(node => node.type === 'button').props.disabled, true);
        """)


if __name__ == '__main__':
    unittest.main()
