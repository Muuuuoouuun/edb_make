"""Compact inspector organization keeps the existing editing actions reachable."""
from __future__ import annotations

import unittest

from test_ui_sidebar_hierarchy import RENDER_SETUP, run_node


INSPECTOR_SETUP = RENDER_SETUP + r"""
const panelSource = section('function SidePanel(', '\nfunction BugReportDialog(');
const panelAst = Babel.transform(panelSource, {
  ast: true, code: false, parserOpts: { plugins: ['jsx'] },
}).ast;
const entries = [];
function visitAst(node, ancestors = []) {
  if (!node || typeof node !== 'object') return;
  if (node.type) entries.push({ node, ancestors });
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'start', 'end', 'comments', 'tokens'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => visitAst(child, [...ancestors, node]));
    else if (value && typeof value === 'object') visitAst(value, [...ancestors, node]);
  }
}
visitAst(panelAst);
function jsxName(node) { return node?.openingElement?.name?.name; }
function attr(node, name) {
  return node?.openingElement?.attributes?.find(value => value.name?.name === name)?.value;
}
function attrText(node, name) { return attr(node, name)?.value; }
function hasClass(node, name) { return String(attrText(node, 'className') || '').split(' ').includes(name); }
function findElement(predicate) {
  const found = entries.find(entry => entry.node.type === 'JSXElement' && predicate(entry.node));
  assert.ok(found, 'expected inspector element exists');
  return found;
}
function snippet(node) { return panelSource.slice(node.start, node.end); }
"""


class TestInspectorReorganization(unittest.TestCase):
    def test_original_number_editor_is_optional_and_advanced_body_stays_mounted(self) -> None:
        run_node(INSPECTOR_SETUP + r"""
        const editors = entries.filter(entry => jsxName(entry.node) === 'OriginalProblemNumberEditor');
        assert.equal(editors.length, 1, 'keep one correction form, not duplicate number controls');
        const editor = editors[0];
        const disclosure = editor.ancestors.find(node => jsxName(node) === 'SidebarDisclosure'
          && attrText(node, 'title') === '원문 번호 수정');
        assert.ok(disclosure, 'number correction belongs to an optional disclosure');
        assert.equal(attr(disclosure, 'open'), undefined);
        const body = editor.ancestors.find(node => hasClass(node, 'detail-settings-body'));
        assert.ok(body, 'number correction remains part of material details');
        assert.equal(attr(body, 'hidden').expression.type, 'UnaryExpression');
        assert.equal(attr(body, 'hidden').expression.operator, '!');
        assert.equal(attr(body, 'hidden').expression.argument.name, 'advancedSettingsOpen');
        assert.ok(!editor.ancestors.some(node => node.type === 'LogicalExpression'
          && node.operator === '&&' && node.left?.name === 'advancedSettingsOpen'),
          'folding details must not unmount the number input and discard its draft');
        assert.match(snippet(editor.node), /onSave=\{mutateSession\}/);
        assert.match(snippet(editor.node), /disabled=\{panelBusy \|\| !session\}/);

        const sandbox = renderSandbox();
        sandbox.advancedSettingsOpen = false;
        const opening = panelSource.slice(body.openingElement.start, body.openingElement.end);
        compile(sandbox, 'globalThis.detailBody = () => (' + opening + 'draft child</div>);');
        assert.equal(sandbox.detailBody().props.hidden, true);
        assert.equal(text(sandbox.detailBody()), 'draft child');
        sandbox.advancedSettingsOpen = true;
        assert.equal(sandbox.detailBody().props.hidden, false);
        assert.equal(text(sandbox.detailBody()), 'draft child');
        """)

    def test_number_correction_keeps_validation_clear_and_explicit_save_contract(self) -> None:
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox(), values = [], calls = [];
        let cursor = 0;
        sandbox.originalProblemNumberInfo = require('./ui_prototype/reorder.js').originalProblemNumberInfo;
        sandbox.useState = initial => {
          const index = cursor++;
          if (!(index in values)) values[index] = initial;
          return [values[index], value => { values[index] = typeof value === 'function' ? value(values[index]) : value; }];
        };
        sandbox.useEffect = () => {};
        compile(sandbox, section('function OriginalProblemNumberEditor(', 'function SidebarDisclosure('));
        const item = {id:'p1',problemNumber:4,problemNumberSource:'manual'};
        const props = {item, disabled:false,
          onSave: async (action,payload) => { calls.push({action,...payload}); }};
        const render = () => { cursor = 0; return sandbox.OriginalProblemNumberEditor(props); };
        const change = value => walk(render()).find(node => node.type === 'input').props.onChange({target:{value}});
        (async () => {
          change('0012');
          assert.equal(calls.length,0,'typing is not a save');
          assert.equal(walk(render()).find(node => node.type === 'input').props.value,'0012');
          await render().props.onSubmit({preventDefault(){}});
          assert.deepEqual(calls,[{action:'problem-number',problemId:'p1',problemNumber:12}]);
          change(''); await render().props.onSubmit({preventDefault(){}});
          assert.equal(calls[1].problemNumber,null,'blank explicitly clears number metadata');
          for (const value of ['0','-1','1.5','12번','1000000']) {
            change(value); await render().props.onSubmit({preventDefault(){}});
            assert.equal(calls.length,2,'invalid input cannot reach session mutation');
            assert.equal(walk(render()).find(node => node.type === 'input').props['aria-invalid'],true);
            assert.ok(text(findClass(render(),'original-number-error')).includes('1~999999'));
          }
          props.disabled = true; change('9');
          assert.equal(walk(render()).find(node => node.type === 'input').props.disabled,true);
          await render().props.onSubmit({preventDefault(){}});
          assert.equal(calls.length,2,'busy or unsaved sample cannot mutate number metadata');
          props.item = {...item,passageRole:'passage_fragment'};
          assert.equal(render(),null,'shared passages still have no individual number form');
          assert.deepEqual(item,{id:'p1',problemNumber:4,problemNumberSource:'manual'},
            'the editor must not mutate the source item directly');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_quick_actions_open_existing_crop_controls_or_placement_without_mutation(self) -> None:
        run_node(INSPECTOR_SETUP + r"""
        const quick = findElement(node => attrText(node,'aria-label') === '선택 자료 빠른 편집');
        const sandbox = renderSandbox(), calls = [];
        Object.assign(sandbox, {panelBusy:false,
          focusManualCrop:()=>calls.push('crop'),setTab:tab=>calls.push(tab),
          document:{getElementById:id=>({focus:()=>calls.push(['focus',id])})}});
        compile(sandbox,'globalThis.quick = () => ('+snippet(quick.node)+');');
        const buttons = walk(sandbox.quick()).filter(node=>node.type==='button');
        const crop = buttons.find(node=>text(node).includes('여백 자르기'));
        const placement = buttons.find(node=>text(node).includes('크기·간격'));
        assert.ok(crop && placement);
        crop.props.onClick(); placement.props.onClick();
        assert.deepEqual(calls,['crop','placement',['focus','side-panel-placement']]);
        sandbox.panelBusy=true;
        assert.equal(walk(sandbox.quick()).find(node=>node.type==='button' && text(node).includes('여백 자르기')).props.disabled,true);

        let scheduled;
        Object.assign(sandbox, {setAdvancedSettingsOpen:value=>{sandbox.advanced=value;},
          setPreviewExpanded:value=>{sandbox.preview=value;},setPreviewMode:value=>{sandbox.mode=value;},
          window:{requestAnimationFrame:callback=>{scheduled=callback;}},
          cropControlRef:{current:{scrollIntoView:options=>{sandbox.scroll=options;}}}});
        vm.runInContext(section('  const focusManualCrop =', '  const applyManualCrop =')
          +'\nglobalThis.openCrop = focusManualCrop;',sandbox);
        sandbox.openCrop();
        assert.equal(sandbox.advanced,true);
        assert.equal(sandbox.preview,true);
        assert.equal(sandbox.mode,'raw');
        scheduled();
        assert.equal(sandbox.scroll.block,'center');
        assert.equal(sandbox.scroll.inline,'nearest');
        """)

    def test_layout_choice_is_primary_and_publish_estimate_is_in_placement_details(self) -> None:
        run_node(INSPECTOR_SETUP + r"""
        const plans = entries.filter(entry=>jsxName(entry.node)==='PublishPlanSummary');
        assert.equal(plans.length,1);
        assert.ok(plans[0].ancestors.some(node=>hasClass(node,'placement-advanced-details')),
          'estimated output details must not create a global status row');
        assert.ok(plans[0].ancestors.some(node=>attrText(node,'id')==='side-panel-placement'));
        const layout = findElement(node=>jsxName(node)==='BoardLayoutControls');
        assert.ok(layout.ancestors.some(node=>attrText(node,'id')==='side-panel-placement'));
        assert.ok(!layout.ancestors.some(node=>jsxName(node)==='SidebarDisclosure'),
          'the common spacing choice must remain immediately available');
        const parent = [...layout.ancestors].reverse().find(node=>Array.isArray(node.children)
          && node.children.includes(layout.node));
        const siblings = parent.children.filter(node=>node.type==='JSXElement');
        const index = siblings.indexOf(layout.node);
        assert.ok(index>0 && hasClass(siblings[index-1],'placement-size-section'),
          'page spacing follows size before fine-position details');
        assert.equal(attr(layout.node,'boardColumns').expression.name,'boardColumns');
        assert.equal(attr(layout.node,'layoutGapMode').expression.name,'layoutGapMode');
        assert.equal(attr(layout.node,'disabled').expression.name,'panelBusy');
        """)


if __name__ == '__main__':
    unittest.main()
