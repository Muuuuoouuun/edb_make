from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


RENDER_SETUP = NODE_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
function createElement(type, props, ...children) {
  const resolvedChildren = children.flat(Infinity).filter(child => child != null && child !== false);
  if (typeof type === 'function') return type({ ...(props || {}), children: resolvedChildren });
  return { type, props: props || {}, children: resolvedChildren };
}
function walk(node) {
  return node && typeof node === 'object' ? [node, ...node.children.flatMap(walk)] : [];
}
function text(node) {
  return typeof node === 'string' || typeof node === 'number' ? String(node) : node?.children?.map(text).join('') || '';
}
function findClass(tree, className) {
  return walk(tree).find(node => String(node.props.className || '').split(' ').includes(className));
}
function compile(sandbox, code) {
  vm.runInContext(Babel.transform(code, { presets: ['react'] }).code, sandbox);
}
function renderSandbox() {
  const sandbox = placementSandbox();
  sandbox.React = { createElement, Fragment: 'fragment' };
  sandbox.Icon = new Proxy({}, { get: (_, name) => String(name) });
  compile(sandbox, section('function SidebarDisclosure(', 'function derivePlacementSaveState('));
  return sandbox;
}
"""


class TestUiSidebarHierarchy(unittest.TestCase):
    def test_crop_draft_survives_closing_details_and_tab_switches_for_the_same_item(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        Object.assign(sandbox, {
          item: { id: 'p1', manualCrop: { leftRatio: 0.01 } },
          advancedSettingsOpen: true, savedCropActive: false,
          setCropDraft: update => { sandbox.cropDraft = typeof update === 'function' ? update(sandbox.cropDraft) : update; },
          setAdvancedSettingsOpen: update => { sandbox.advancedSettingsOpen = update(sandbox.advancedSettingsOpen); },
        });
        let previousDependencies;
        sandbox.useEffect = (effect, dependencies) => {
          if (!previousDependencies || dependencies.some((value, index) => value !== previousDependencies[index])) {
            effect();
            previousDependencies = dependencies;
          }
        };
        const cropEffect = section('  useEffect(() => {\n    setCropDraft(item ?', '  const previousViewRef =');
        const renderEffect = () => vm.runInContext(cropEffect, sandbox);
        renderEffect();
        vm.runInContext(section('  const updateCropDraft =', '  const adjustCropDraft =')
          + '\nglobalThis.updateCropDraft = updateCropDraft;', sandbox);
        compile(sandbox, 'globalThis.renderCropToggle = () => ('
          + section('                  <button\n                    className="detail-settings-toggle"',
            '                  {advancedSettingsOpen && (') + ');');
        sandbox.updateCropDraft('leftRatio', 0.18);
        const draft = JSON.stringify(sandbox.cropDraft);
        sandbox.renderCropToggle().props.onClick();
        renderEffect();
        assert.equal(sandbox.advancedSettingsOpen, false);
        assert.equal(JSON.stringify(sandbox.cropDraft), draft);
        sandbox.tab = 'placement'; renderEffect();
        sandbox.tab = 'item'; renderEffect();
        sandbox.renderCropToggle().props.onClick(); renderEffect();
        assert.equal(sandbox.advancedSettingsOpen, true);
        assert.equal(JSON.stringify(sandbox.cropDraft), draft, 'folds and navigation cannot discard unsaved crop values');
        sandbox.item = { id: 'p2', manualCrop: { leftRatio: 0.05 } };
        renderEffect();
        assert.equal(sandbox.cropDraft.leftRatio, 0.05, 'changing the selected item must load that item\'s crop');
        """)

    def test_native_disclosure_keeps_children_mounted_and_does_not_force_open_on_rerender(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const draft = createElement('input', { value: '입력 중인 값' });
        const closed = sandbox.SidebarDisclosure({ title: '상세 설정', children: draft });
        assert.equal(closed.type, 'details');
        assert.equal(closed.props.open, undefined, 'the browser owns open state without render-time resets');
        assert.equal(closed.props.onToggle, undefined, 'closing details must not mutate draft state');
        assert.equal(findClass(closed, 'sidebar-disclosure-body').children[0], draft,
          'closed disclosure must keep form controls mounted');
        const rerendered = sandbox.SidebarDisclosure({ title: '상세 설정', badge: '설정됨', children: draft });
        assert.equal(findClass(rerendered, 'sidebar-disclosure-body').children[0], draft);
        assert.equal(rerendered.props.open, undefined);
        """)

    def test_legacy_columns_have_a_working_one_column_recovery_and_visible_gap_controls(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        for (const legacyColumns of [2, 3]) {
          const state = { columns: legacyColumns, gap: 'classin-grid', resets: 0 };
          const render = () => sandbox.BoardLayoutControls({ boardColumns: state.columns,
            setBoardColumns: columns => { state.columns = columns; },
            layoutGapMode: state.gap, setLayoutGapMode: gap => { state.gap = gap; },
            manualGapCount: 1, resetPlacementGaps: () => { state.resets += 1; },
          });
          let tree = render();
          const warning = findClass(tree, 'placement-column-warning');
          assert.ok(text(warning).includes(`${legacyColumns}열`));
          walk(warning).find(node => node.type === 'button').props.onClick();
          assert.equal(state.columns, 1);
          tree = render();
          assert.equal(findClass(tree, 'placement-column-warning'), undefined);
          const gapControl = findClass(tree, 'layout-gap-mode-control');
          assert.ok(gapControl, 'the common spacing choice remains visible without opening details');
          walk(gapControl).find(node => node.type === 'button' && text(node).includes('빈틈 없이')).props.onClick();
          assert.equal(state.gap, 'compact');
          const reset = walk(render()).find(node => node.type === 'button' && text(node) === '전체 해제');
          assert.equal(reset.props.disabled, false);
          reset.props.onClick();
          assert.equal(state.resets, 1);
        }
        """)

    def test_publish_download_is_available_while_secondary_results_are_collapsed(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const summary = { statusLabel: '제작 완료', validated: true, recordCount: 2, edbFileName: '수업.edb',
          edbParts: [], edbSplit: true, edbPartCount: 2, canDownload: true };
        const downloads = [];
        Object.assign(sandbox, {
          publishOpen: false, useMemo: fn => fn(), useEffect() {},
          useState: initial => [sandbox.publishOpen, update => { sandbox.publishOpen = update(sandbox.publishOpen); }],
          sessionPublishSummary: () => summary, sessionPublishHistory: () => [],
          openPublishedEdb() {}, openOutputFolder() {}, openClassinHandoff() {},
        });
        compile(sandbox, section('function PublishResultPanel(', 'function SidebarDisclosure('));
        const props = { session: {}, visible: true, onDownloadPublish: value => downloads.push(value), publishDownloadBusy: false };
        let tree = sandbox.PublishResultPanel(props);
        assert.equal(findClass(tree, 'publish-result-detail-body'), undefined);
        const primary = walk(findClass(tree, 'publish-result-primary-actions')).find(node => node.type === 'button');
        assert.equal(primary.props.disabled, false);
        assert.ok(text(primary).includes('EDB 묶음 다운로드'));
        primary.props.onClick();
        assert.equal(downloads[0], summary);
        findClass(tree, 'publish-result-head').props.onClick();
        tree = sandbox.PublishResultPanel(props);
        assert.ok(findClass(tree, 'publish-result-detail-body'));
        assert.ok(findClass(tree, 'publish-result-primary-actions'));
        sandbox.publishOpen = false;
        assert.equal(walk(findClass(sandbox.PublishResultPanel({ ...props, publishDownloadBusy: true }),
          'publish-result-primary-actions')).find(node => node.type === 'button').props.disabled, true);
        summary.edbFileExists = false;
        summary.canDownload = false;
        assert.equal(walk(findClass(sandbox.PublishResultPanel(props), 'publish-result-primary-actions'))
          .find(node => node.type === 'button').props.disabled, true);
        """)

    def test_failed_api_key_save_keeps_the_draft_open_and_success_clears_it(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = { keyDraft: '  entered-placeholder  ', keySaveBusy: false, keySaveError: '', keyEditorOpen: true,
          onSaveGeminiKey: async key => { assert.equal(key, 'entered-placeholder'); return false; },
          setKeySaveBusy: value => { sandbox.keySaveBusy = value; },
          setKeySaveError: value => { sandbox.keySaveError = value; },
          setKeyDraft: value => { sandbox.keyDraft = value; },
          setKeyEditorOpen: value => { sandbox.keyEditorOpen = value; },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const persistGeminiKeyDraft =', '  const updateCropDraft =')
          + '\nglobalThis.persistGeminiKeyDraft = persistGeminiKeyDraft;', sandbox);
        (async () => {
          await sandbox.persistGeminiKeyDraft();
          assert.equal(sandbox.keyDraft, '  entered-placeholder  ');
          assert.equal(sandbox.keyEditorOpen, true);
          assert.ok(sandbox.keySaveError);
          assert.equal(sandbox.keySaveBusy, false);
          sandbox.onSaveGeminiKey = async () => { throw new Error('offline'); };
          await sandbox.persistGeminiKeyDraft();
          assert.equal(sandbox.keyDraft, '  entered-placeholder  ');
          assert.equal(sandbox.keyEditorOpen, true);
          sandbox.onSaveGeminiKey = async () => true;
          await sandbox.persistGeminiKeyDraft();
          assert.equal(sandbox.keyDraft, '');
          assert.equal(sandbox.keyEditorOpen, false);
          assert.equal(sandbox.keySaveError, '');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_processing_step_selects_its_matching_preview_and_respects_busy_state(self) -> None:
        run_node(NODE_SETUP + r"""
        const steps = [];
        const sandbox = { item: { id: 'p1' }, panelBusy: false,
          setStep: (id, step) => steps.push([id, step]), setPreviewMode: mode => { sandbox.previewMode = mode; } };
        vm.createContext(sandbox);
        vm.runInContext(section('  const selectProcessingStep =', '  const persistGeminiKeyDraft =')
          + '\nglobalThis.selectProcessingStep = selectProcessingStep;', sandbox);
        for (const [step, mode] of [['s1', 'raw'], ['s2', 'chalk'], ['s3', 'chalk']]) {
          sandbox.selectProcessingStep(step);
          assert.equal(sandbox.previewMode, mode);
        }
        assert.equal(steps.length, 3);
        sandbox.panelBusy = true;
        sandbox.selectProcessingStep('s1');
        assert.equal(steps.length, 3);
        sandbox.panelBusy = false;
        sandbox.item = null;
        sandbox.selectProcessingStep('s1');
        assert.equal(steps.length, 3);
        """)

    def test_compare_slider_keyboard_clamps_its_range_without_stealing_other_keys(self) -> None:
        run_node(NODE_SETUP + r"""
        const slider = section('className="compare-handle"', 'onMouseDown={() => { dragging.current = true; }}');
        const body = slider.split('onKeyDown={event => {')[1].split('\n                               }}')[0];
        const sandbox = { compareX: 50, setCompareX: update => { sandbox.compareX = update(sandbox.compareX); } };
        const onKeyDown = vm.runInNewContext('(event => {' + body + '})', sandbox);
        let prevented = 0;
        const press = key => onKeyDown({ key, preventDefault: () => { prevented += 1; } });
        press('ArrowLeft'); assert.equal(sandbox.compareX, 48);
        press('ArrowRight'); assert.equal(sandbox.compareX, 50);
        press('Home'); press('ArrowLeft'); assert.equal(sandbox.compareX, 6);
        press('End'); press('ArrowRight'); assert.equal(sandbox.compareX, 94);
        const handled = prevented;
        press('Tab'); press('Enter');
        assert.equal(prevented, handled);
        """)

    def test_empty_sidebar_explains_selection_without_a_competing_upload_action(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        compile(sandbox, 'globalThis.renderEmpty = () => ('
          + section('              <div className="side-panel-home-empty">', '            ) : (') + ');');
        const empty = sandbox.renderEmpty();
        assert.equal(walk(empty).filter(node => node.type === 'button').length, 0);
        assert.ok(text(empty).includes('목록이나 칠판에서 선택'));
        assert.ok(text(empty).includes('처리 방식과 세부 설정'));
        """)


if __name__ == '__main__':
    unittest.main()
