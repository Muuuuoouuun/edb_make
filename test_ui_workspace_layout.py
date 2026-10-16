"""Three-panel alignment and mobile navigation stay usable without losing edits."""
from __future__ import annotations

import re
import unittest

from test_ui_sidebar_hierarchy import NODE_SETUP, RENDER_SETUP, run_node
from test_ui_side_panel_settings import CSS, component


class TestWorkspaceLayout(unittest.TestCase):
    def test_three_desktop_headers_share_one_height_token(self) -> None:
        self.assertIn("--panel-header-height: 48px;", CSS)
        for selector in (".col-hd", ".stage-toolbar", ".tab-bar"):
            rule = re.search(r"(?m)^  " + re.escape(selector) + r"\{([^}]+)\}", CSS)
            self.assertIsNotNone(rule, selector)
            self.assertIn("min-height: var(--panel-header-height)", rule[1], selector)

    def test_sidebars_are_bounded_and_main_preview_can_shrink(self) -> None:
        self.assertTrue("clamp(220px, 17vw, 248px) minmax(0, 1fr) clamp(320px, 25vw, 360px)" in CSS)
        self.assertIn(".tab-body{", CSS)
        body = CSS.split(".tab-body{", 1)[1].split("}", 1)[0]
        self.assertIn("scrollbar-gutter: stable", body)
        self.assertIn("min-height: 0", body)
        self.assertIn("overflow-y: auto", body)

    def test_empty_home_has_intrinsic_height_in_the_single_page_mobile_flow(self) -> None:
        rule = CSS.split(".main.home-mode .home-dashboard{", 1)[1].split("}", 1)[0]
        self.assertIn("flex: 0 0 auto", rule)
        self.assertIn("height: auto", rule)
        self.assertIn("overflow: visible", rule)

    def test_short_landscape_footer_keeps_status_and_touch_actions_in_one_row(self) -> None:
        rule = CSS.split("@media (min-width: 701px) and (max-width: 900px) and (max-height: 500px){", 1)[1]
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto auto", rule)
        self.assertIn(".placement-tab-foot .placement-save-state{ grid-column: auto; }", rule)
        self.assertIn(".placement-tab-foot .btn{ min-height: var(--control-touch); }", rule)

    def test_mobile_navigation_controls_the_mounted_named_regions(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        Object.assign(sandbox, { mobilePane: 'stage', items: [{ id: 'p1' }], pendingFiles: [],
          view: 'board', reviewEditorActive: false,
          setMobilePane: pane => { sandbox.mobilePane = pane; } });
        compile(sandbox, 'globalThis.navigation = () => ('
          + section('          <nav className="mobile-workspace-nav"', '        <ItemsRail')
            .split('</nav>')[0] + '</nav>);');
        for (const pane of ['rail', 'stage', 'panel']) {
          const tree = sandbox.navigation();
          const buttons = walk(tree).filter(node => node.type === 'button');
          assert.equal(buttons.filter(button => button.props['aria-pressed']).length, 1);
          const button = buttons.find(button => button.props['aria-controls'] === `workspace-${pane}`);
          assert.ok(button, 'each pane must have a matching control');
          assert.ok(source.includes(`id="workspace-${pane}" role="region" aria-label=`),
            'each control must reference a mounted, named region');
          button.props.onClick();
          assert.equal(sandbox.mobilePane, pane);
        }
        sandbox.reviewEditorActive = true;
        const buttons = walk(sandbox.navigation()).filter(node => node.type === 'button');
        assert.equal(buttons.find(button => button.props['aria-controls'] === 'workspace-stage').props.disabled, false);
        assert.ok(buttons.filter(button => button.props['aria-controls'] !== 'workspace-stage')
          .every(button => button.props.disabled), 'the active review editor must not be hidden');
        """)

    def test_alt_digit_reveals_the_panel_and_preserves_form_shortcuts(self) -> None:
        run_node(NODE_SETUP + r"""
        let handler, cleanup, prevented = 0, revealed = 0;
        const sandbox = { RIGHT_PANEL_TAB_SHORTCUTS: { Digit1: 'item', Digit2: 'placement', Digit3: 'board' },
          window: { addEventListener: (_, fn) => { handler = fn; }, removeEventListener: (_, fn) => {
            assert.equal(fn, handler); handler = null;
          } },
          setTab: tab => { sandbox.tab = tab; }, onRevealPanel: () => revealed++,
          useEffect: effect => { cleanup = effect(); } };
        vm.createContext(sandbox);
        vm.runInContext(section('function isEditableKeyboardTarget(', 'function resetItemPlacement(')
          + section('  useEffect(() => {\n    const onKeyDown = (evt) => {\n      if (!evt.altKey',
            '  useEffect(() => {\n    setCropDraft('), sandbox);
        const press = (code, extra = {}) => handler({ altKey: true, code, target: { tagName: 'BUTTON' },
          preventDefault: () => prevented++, ...extra });
        for (const [code, tab] of Object.entries(sandbox.RIGHT_PANEL_TAB_SHORTCUTS)) {
          press(code); assert.equal(sandbox.tab, tab);
        }
        assert.equal(revealed, 3); assert.equal(prevented, 3);
        for (const target of [{ tagName: 'INPUT' }, { tagName: 'TEXTAREA' }, { tagName: 'SELECT' },
          { tagName: 'DIV', isContentEditable: true }]) press('Digit1', { target });
        for (const extra of [{ altKey: false }, { metaKey: true }, { ctrlKey: true }, { shiftKey: true }])
          press('Digit2', extra);
        press('Digit9');
        assert.equal(revealed, 3); assert.equal(prevented, 3);
        sandbox.onRevealPanel = undefined;
        press('Digit1'); assert.equal(sandbox.tab, 'item');
        cleanup(); assert.equal(handler, null);
        """)

    def test_panel_shortcut_does_not_hide_an_active_review_editor(self) -> None:
        run_node(NODE_SETUP + r"""
        let pane = 'stage';
        const sandbox = { reviewEditorActive: false, useCallback: callback => callback,
          setMobilePane: value => { pane = value; } };
        vm.createContext(sandbox);
        const callback = section('  const revealSidePanel = useCallback(', '  const [mutating, setMutating]');
        const invoke = () => vm.runInContext('(() => {' + callback + 'revealSidePanel(); })()', sandbox);
        invoke(); assert.equal(pane, 'panel');
        pane = 'stage'; sandbox.reviewEditorActive = true;
        invoke(); assert.equal(pane, 'stage');
        """)

    def test_all_review_and_board_stage_branches_expose_the_navigation_target(self) -> None:
        self.assertEqual(component("ReviewStage").count('id="workspace-stage"'), 2)
        self.assertEqual(component("BoardStage").count('id="workspace-stage"'), 1)


class TestCompactRailAndBoundaries(unittest.TestCase):
    def test_named_rail_actions_keep_upload_drop_and_preserve_callbacks(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        let uploads = 0, preserved, dropped;
        Object.assign(sandbox, { hasSessionItems: true, pendingFiles: [], dropZoneActive: false,
          addSample: () => uploads++, bulkApply: step => { preserved = step; },
          setDropZoneActive() {}, handleFiles: files => { dropped = files; } });
        compile(sandbox, 'globalThis.actions = () => (<>'
          + section('        {(hasSessionItems || pendingFiles.length > 0) && <div className={`rail-primary-actions',
            '        {!hasSessionItems && pendingFiles.length === 0 && (') + '</>);');
        let tree = sandbox.actions();
        const buttons = walk(tree).filter(node => node.type === 'button');
        assert.equal(buttons.length, 2);
        const upload = buttons.find(node => node.props['aria-label'] === '파일 추가');
        const preserve = findClass(tree, 'rail-preserve-all');
        assert.ok(text(upload).includes('파일 추가'));
        assert.ok(text(preserve).includes('전체 보존'));
        assert.ok(preserve.props['data-tooltip'].includes('AI 실행 없이'));
        upload.props.onClick(); assert.equal(uploads, 1);
        preserve.props.onClick(); assert.equal(preserved, 's2');
        let prevented = 0;
        upload.props.onDrop({ preventDefault: () => prevented++, dataTransfer: { files: ['sample.png'] } });
        assert.deepEqual(Array.from(dropped), ['sample.png']); assert.equal(prevented, 1);
        sandbox.hasSessionItems = false; sandbox.pendingFiles = [{ name: 'sample.png' }];
        tree = sandbox.actions();
        assert.equal(walk(tree).filter(node => node.type === 'button').length, 1);
        assert.equal(findClass(tree, 'rail-preserve-all'), undefined);
        sandbox.pendingFiles = [];
        assert.equal(walk(sandbox.actions()).filter(node => node.type === 'button').length, 0);
        """)

    def test_rail_header_no_longer_duplicates_upload_or_uses_the_ai_icon_for_preservation(self) -> None:
        rail = component("ItemsRail")
        header = rail.split('<div className="col-hd">', 1)[1].split("{hasSessionItems && (", 1)[0]
        self.assertEqual(header.count("<button"), 1)
        self.assertIn('aria-label="자료 목록 접기"', header)
        primary = rail.split('className={`rail-primary-actions', 1)[1].split('        {!hasSessionItems && pendingFiles.length === 0 && (', 1)[0]
        self.assertIn("Icon.folderPlus", primary)
        self.assertIn("Icon.filesCheck", primary)
        self.assertNotIn("Icon.aiBatch", primary)

    def test_card_actions_use_the_full_width_without_wrapping_and_keep_touch_targets(self) -> None:
        card = re.search(r"(?m)^  \.item\{([^}]+)\}", CSS)[1]
        actions = CSS.split(".item .actions{", 1)[1].split("}", 1)[0]
        self.assertIn('grid-template-areas: "grip thumb meta" "actions actions actions"', card)
        self.assertIn("contain-intrinsic-size: auto 88px", card)
        self.assertIn("flex-wrap: nowrap", actions)
        self.assertIn(".item .actions .icon-btn{ width: var(--control-touch); height: var(--control-touch); }", CSS)

    def test_divider_labels_pair_classin_start_page_with_exact_internal_height(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        compile(sandbox, section('function buildClassinPageBands(', 'function BoardStage(')
          + 'globalThis.bands = buildClassinPageBands;');
        const layout = { positions: [
          { startPages: 0, renderedHeightPages: 2.4 },
          { startPages: 2.4, renderedHeightPages: 0.8 },
        ] };
        const bands = sandbox.bands(layout, 240, 7);
        assert.equal(bands.length, 7);
        bands.forEach((band, index) => {
          assert.equal(band.page, index + 1);
          assert.ok(Math.abs(band.top - index * 1.2 * 240) < 1e-8);
          assert.equal(band.offsetPages.toFixed(1), (index * 1.2).toFixed(1));
        });
        assert.equal(bands[1].label, '앞 문항 이어짐');
        assert.equal(bands[2].label, '02번');
        assert.equal(bands[3].label, '판서 공간');
        assert.ok(source.includes('ClassIn {band.page}쪽 · {band.offsetPages.toFixed(1)}p'));
        assert.ok(source.includes('(nextPage - 1) * DEFAULT_SLOT_HEIGHT_PAGES * pageH'));
        """)

    def test_both_boundary_units_remain_visible_on_small_screens(self) -> None:
        rules = re.findall(r"\.page-divider \.label \.classin-page\{([^}]+)\}", CSS)
        self.assertTrue(rules)
        self.assertTrue(all("display: none" not in rule for rule in rules))


if __name__ == "__main__":
    unittest.main()
