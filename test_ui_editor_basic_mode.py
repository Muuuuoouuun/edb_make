"""5a editor chrome preserves working controls and explicit legacy recovery."""
from pathlib import Path
import re
import unittest

from test_ui_design_system import contrast_ratio
from test_ui_sidebar_hierarchy import RENDER_SETUP, run_node


ROOT = Path(__file__).resolve().parent
STYLE = (ROOT / "ui_prototype" / "editor-layout.css").read_text(encoding="utf-8")
SOURCE = (ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")


class TestEditorBasicMode(unittest.TestCase):
    def test_empty_problem_review_does_not_hide_its_panels_as_home(self):
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const mainClass = source.match(/className=\{(`main editor-basic-mode[^\n]+`)\}/)[1];
        Object.assign(sandbox, { focusEditorActive: false, focusPanelCollapsed: false,
          effectiveRailCollapsed: false, reviewEditorActive: false,
          view: 'review', items: [], pendingFiles: [] });
        compile(sandbox, 'globalThis.mainClass = () => ' + mainClass + ';');
        assert.ok(!sandbox.mainClass().includes('home-mode'));
        sandbox.view = 'board';
        assert.ok(sandbox.mainClass().includes('home-mode'));
        assert.ok(!sandbox.mainClass().includes('home-settings-open'));
        sandbox.pendingFiles = [{}];
        assert.ok(!sandbox.mainClass().includes('home-mode'));
        """)

    def test_normal_layout_has_single_row_guidance_without_multicolumn_options(self):
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const tree = sandbox.BoardLayoutControls({ boardColumns: 1,
          layoutGapMode: 'classin-grid', manualGapCount: 0 });
        assert.ok(text(findClass(tree, 'single-row-layout-note')).includes('판서 공간'));
        assert.ok(text(findClass(tree, 'placement-layout-details')).includes('한 행 · 문제 1개'));
        assert.equal(findClass(tree, 'placement-column-warning'), undefined);
        const labels = walk(tree).filter(node => node.type === 'button').map(text);
        assert.ok(!labels.some(label => ['2열', '3열'].includes(label)));
        assert.ok(labels.includes('1.2 맞춤'));
        assert.ok(labels.includes('빈틈 없이'));
        """)
        self.assertNotIn('TweakRadio label="한 줄 자료 수"', SOURCE)

    def test_focus_heading_names_tools_without_repeating_the_selected_item(self):
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        let toggles = 0;
        Object.assign(sandbox, { focusMode: true,
          item: { id: 'p1' }, activeIndex: 0,
          problemDisplayName: () => '7번 문제', onToggleFocusPanel: () => toggles++ });
        compile(sandbox, 'globalThis.heading = () => (<>'
          + section('      {focusMode && (',
            '      <div className="inspector-pill-tabs"') + '</>);');
        let tree = sandbox.heading();
        assert.ok(text(tree).includes('편집 도구'));
        assert.ok(!text(tree).includes('7번 문제'), 'the item metadata owns the selected title');
        const toggle = findClass(tree, 'focus-panel-toggle');
        assert.equal(toggle.props['aria-label'], '편집 패널 접기');
        assert.equal(toggle.props['aria-controls'], 'workspace-panel');
        toggle.props.onClick(); assert.equal(toggles, 1);
        sandbox.focusMode = false;
        assert.equal(findClass(sandbox.heading(), 'focus-panel-heading'), undefined);
        sandbox.focusMode = true; sandbox.item = null;
        assert.ok(text(sandbox.heading()).includes('편집 도구'));
        """)

    def test_reference_palette_is_scoped_and_offline(self):
        self.assertIn('body:not(.dark) .app.editor-basic-mode,', STYLE)
        self.assertIn('body:not(.dark) .main.editor-basic-mode,', STYLE)
        for value in ('#f5ead8', '#ebddc5', '#c67139', '#7a8a5e'):
            self.assertIn(value, STYLE)
        self.assertNotIn('@import', STYLE)
        self.assertNotIn('https://', STYLE)
        self.assertNotRegex(STYLE, r'font-family:\s*(?!var\(--font-sans\))\S')
        self.assertGreaterEqual(contrast_ratio('#ffffff', '#8c491a'), 4.5)
        self.assertGreaterEqual(contrast_ratio('#645c50', '#ebddc5'), 4.5)
        self.assertIn('.btn.primary:not(:disabled)', STYLE)

    def test_layout_keeps_scroll_regions_controls_and_reduced_motion(self):
        focus_style = (ROOT / 'ui_prototype' / 'editor-focus.css').read_text(encoding='utf-8')
        self.assertIn('.main.editor-basic-mode:not(.editor-focus-mode) .focus-panel-heading', focus_style)
        self.assertIn('grid-template-columns: 220px minmax(0, 1fr) 300px', STYLE)
        self.assertIn('.main.editor-basic-mode.side-panel-hidden', STYLE)
        self.assertIn('.main.editor-basic-mode.left-collapsed', STYLE)
        self.assertIn('.main.editor-basic-mode.home-mode > .col.right', STYLE)
        self.assertNotIn('home-settings-open', STYLE)
        self.assertIn('@media (prefers-reduced-motion: reduce)', STYLE)
        self.assertIn('outline: 2px solid var(--accent-ink)', STYLE)
        self.assertNotIn('.item .actions {\n  display: none', STYLE)
        self.assertNotIn('height: 900px', STYLE)

    def test_laptop_rail_overrides_legacy_grid_areas_and_hidden_thumbnail(self):
        legacy = (ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")
        compact = legacy.split('@media (min-width: 701px) and (max-width: 1280px){', 1)[1]
        compact = compact.split('@media', 1)[0]
        self.assertIn('grid-template-areas: "grip meta" "actions actions"', compact)
        item = re.search(r'\.main\.editor-basic-mode \.item \{([^}]+)\}', STYLE).group(1)
        thumb = re.search(r'\.main\.editor-basic-mode \.item \.thumb \{([^}]+)\}', STYLE).group(1)
        self.assertIn('grid-template-columns: 16px 28px minmax(0, 1fr) 48px', item)
        self.assertIn('grid-template-areas: "grip thumb meta actions"', item)
        self.assertIn('display: grid', thumb)
        # The action cluster keeps its own track; the thumbnail override also
        # beats the legacy laptop display:none rule.
        self.assertNotIn('grid-template-areas: "grip meta"', STYLE)

    def test_compact_rail_preserves_visible_actions_and_focus_mode_starts_expanded(self):
        self.assertIn('const [focusRailExpanded, setFocusRailExpanded] = useState(true)', SOURCE)
        self.assertIn('contain-intrinsic-size: auto 52px', STYLE)
        self.assertIn('grid-template-columns: repeat(2, 24px)', STYLE)
        self.assertIn(':is(.item .icon-btn, .rail-reextract-action):focus-visible', STYLE)
        self.assertNotIn('.item .actions {\n  display: none', STYLE)

    def test_narrow_rail_keeps_readable_metadata_and_action_hit_targets(self):
        focus = (ROOT / 'ui_prototype' / 'editor-focus.css').read_text(encoding='utf-8')
        self.assertIn('--focus-rail-width: 220px', focus)
        self.assertIn('--focus-rail-width: 208px', focus)
        self.assertIn('grid-template-columns: 208px minmax(0, 1fr) 300px', STYLE)
        row = re.search(r'\.main\.editor-basic-mode \.item \{([^}]+)\}', STYLE).group(1)
        self.assertIn('min-height: 52px', row)
        self.assertNotIn('height: 52px;', row.replace('min-height: 52px;', ''))
        label = re.search(r'\.main\.editor-basic-mode \.item \.meta \.name \.original-number-label \{([^}]+)\}', STYLE).group(1)
        self.assertIn('white-space: nowrap', label)
        self.assertIn('text-overflow: ellipsis', label)
        action = re.search(r'\.main\.editor-basic-mode \.item \.actions \.icon-btn \{([^}]+)\}', STYLE).group(1)
        self.assertIn('width: 24px', action)
        self.assertIn('height: 24px', action)

    def test_compact_rows_reuse_selection_reorder_download_delete_and_drag_handlers(self):
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox(), calls = [];
        const items = [{id:'a',name:'문항 A',step:'raw',imageUrl:'a.png'},
          {id:'b',name:'문항 B',step:'s2',imageUrl:'b.png'}];
        Object.assign(sandbox, {
          items, displayedItemRows: items.map((item,displayIndex)=>({item,displayIndex})),
          editScope:null, editScopeIds:new Set(), selectedItemIds: new Set(), dropTarget:null, downloadingItemId:null,
          hasPageChromeArtifactFlag:()=>false,
          originalProblemNumberInfo:()=>({status:'known',description:'원문 번호 확인'}),
          moveFeedback:null, activeId:'a', pressedItemId:null, itemRefs:{current:{}},
          reorderBusy:false, filterActive:false, PRIMARY_MODIFIER_NAME:'Control',
          ALTERNATE_MODIFIER_NAME:'Alt', problemDisplayName:it=>it.name,
          reviewStatusClass:()=> 'status-normal', problemSourceLabel:()=> '원본 1쪽',
          TileImage:()=>null, suppressClickRef:{current:false}, selectedItemCount:0,
          railRowHandlersRef:{current:sandbox},
          selectRailItem:(id,e)=>calls.push(['select',id,e.ctrlKey]),
          applyRailSelectionKeyboard:()=>false,
          moveItemByKeyboard:(it,direction)=>calls.push(['move',it.id,direction]),
          startPointerDrag:(event,id)=>calls.push(['drag',id]),
          onDownloadItemImage:it=>calls.push(['download',it.id]),
          removeItem:id=>calls.push(['remove',id]),
          removeSelectedItems:()=>calls.push(['removeSelected']),
          setSelectedItemIds:update=>{sandbox.selectedItemIds=update(sandbox.selectedItemIds);},
        });
        sandbox.React.memo = fn => fn;
        compile(sandbox, section('function RailItemDetails(', 'function BoardTile(')
          + section('function RailRow(', '// ─── CENTER:')
          + 'globalThis.rows = () => (<>'
          + section('        {displayedItemRows.map(', '        {hasSessionItems && visibleItemRows.length === 0 && (') + '</>);');
        const rows = ()=>walk(sandbox.rows()).filter(node=>node.props['data-item-id']);
        const first = rows()[0];
        const buttons = walk(first).filter(node=>node.type==='button');
        assert.equal(buttons.length,4);
        assert.ok(buttons.every(button=>button.props['aria-label'] && button.props['data-tooltip']));
        assert.equal(buttons[0].props.disabled,true);
        assert.equal(buttons[1].props.disabled,false);
        let stops=0;
        const event = {ctrlKey:true,stopPropagation:()=>stops++,preventDefault(){}};
        buttons[1].props.onClick(event);
        buttons[2].props.onClick(event);
        buttons[3].props.onClick(event);
        assert.equal(stops,3);
        assert.deepEqual(calls,[['move','a','down'],['download','a'],['remove','a']]);
        first.props.onClick(event);
        findClass(first,'grip').props.onPointerDown(event);
        first.props.onKeyDown({...event,target:first,currentTarget:first,altKey:true,key:'ArrowDown'});
        assert.deepEqual(calls.slice(3),[['select','a',true],['drag','a'],['move','a','down']]);
        sandbox.filterActive=true;
        assert.ok(walk(rows()[0]).filter(node=>String(node.props.className).includes('item-move-action'))
          .every(button=>button.props.disabled));
        sandbox.editScope={problemIds:['a']}; sandbox.editScopeIds=new Set(['a']);
        const scoped=rows()[0]; assert.equal(scoped.props['data-edit-target'],'true');
        assert.ok(walk(scoped).filter(node=>node.type==='button').every(button=>button.props.disabled));
        sandbox.editScope=null; sandbox.editScopeIds=new Set();
        sandbox.filterActive=false; sandbox.reorderBusy=true;
        assert.ok(walk(rows()[1]).filter(node=>String(node.props.className).includes('item-move-action'))
          .every(button=>button.props.disabled));
        """)


if __name__ == '__main__':
    unittest.main()
