"""Three-panel alignment and mobile navigation stay usable without losing edits."""
from __future__ import annotations

import re
import unittest

from test_ui_sidebar_hierarchy import NODE_SETUP, RENDER_SETUP, run_node
from test_ui_side_panel_settings import APP, CSS, component


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
          + section('          <nav className="mobile-workspace-nav"', '        {recognitionReview ? (')
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


if __name__ == "__main__":
    unittest.main()
