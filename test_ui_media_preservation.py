from test_ui_placement_details import NODE_SETUP, run_node


EDITOR_SETUP = NODE_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
const hooks = []; let cursor = 0;
const editorSandbox = {
  useState(initial) {
    const index = cursor++;
    if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
    return [hooks[index], value => { hooks[index] = typeof value === 'function' ? value(hooks[index]) : value; }];
  },
  useRef(initial) { const index = cursor++; return hooks[index] ||= { current: initial }; },
  useLayoutEffect() {},
  document: { body: {} },
  ReactDOM: { createPortal: element => element },
  React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) },
};
vm.createContext(editorSandbox);
vm.runInContext(Babel.transform(section('function MediaPreservationEditor(', 'function HomeUpdateDialog('), { presets: ['react'] }).code, editorSandbox);
let result = null; const requests = [];
const item = { id: 'p7', name: '7번', step: 's2', imageUrl: '/api/file?path=math.png', preserveMediaRegions: [] };
const props = { item, busy: false, onSave: async (action, payload) => { requests.push({action, payload}); return result; } };
function render() { cursor = 0; return editorSandbox.MediaPreservationEditor(props); }
function all(node) { return node && typeof node === 'object' ? [node, ...node.children.flatMap(all)] : []; }
function text(node) { return typeof node === 'string' || typeof node === 'number' ? String(node) : node?.children?.map(text).join('') || ''; }
function button(tree, label) { return all(tree).find(node => node.type === 'button' && text(node).includes(label)); }
function loadedEditor() {
  button(render(), '그림 원본 보존').props.onClick();
  let tree = render();
  all(tree).find(node => node.type === 'img').props.onLoad({currentTarget: {naturalWidth: 2200, naturalHeight: 1400}});
  return render();
}
function pointer(x, y) { return {button: 0, pointerId: 1, clientX: x, clientY: y, preventDefault() {},
  currentTarget: {setPointerCapture() {}, getBoundingClientRect: () => ({left: 10, top: 20, width: 550, height: 350})}}; }
"""


def test_editor_maps_display_drag_and_numeric_adjustments_to_source_pixels():
    run_node(EDITOR_SETUP + r"""
    (async () => {
      let tree = loadedEditor();
      let canvas = all(tree).find(node => node.props.className === 'media-editor-canvas');
      canvas.props.onPointerDown(pointer(110, 100));
      canvas.props.onPointerMove(pointer(210, 200));
      canvas.props.onPointerUp(pointer(210, 200));
      tree = render();
      const number = field => all(tree).find(node => node.type === 'label' && text(node).startsWith(field)).children.find(node => node.type === 'input');
      assert.equal(number('왼쪽').props.value, 400);
      assert.equal(number('위쪽').props.value, 320);
      assert.equal(number('너비').props.value, 400);
      number('왼쪽').props.onChange({target: {valueAsNumber: 2100}});
      tree = render();
      assert.equal(number('너비').props.value, 100, 'numeric edits stay within source bounds');
      await button(tree, '지정 영역 저장').props.onClick();
      assert.equal(requests[0].action, 'preserve-media');
      assert.deepEqual(JSON.parse(JSON.stringify(requests[0].payload)), {problemId: 'p7', mode: 'manual', regions: [{left: 2100, top: 320, width: 100, height: 400}]});
      assert.ok(all(render()).some(node => node.type === 'dialog'), 'a failed save keeps the edit open');
      result = {problems: []};
      await button(render(), '지정 영역 저장').props.onClick();
      assert.ok(!all(render()).some(node => node.type === 'dialog'), 'a successful save closes the editor');
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_keyboard_controls_delete_auto_off_and_stage_availability():
    run_node(EDITOR_SETUP + r"""
    (async () => {
      let tree = loadedEditor();
      button(tree, '영역 추가').props.onClick();
      tree = render();
      assert.ok(text(tree).includes('1개 영역'));
      button(tree, '선택 영역 삭제').props.onClick();
      tree = render();
      assert.ok(text(tree).includes('0개 영역'));
      await button(tree, '자동 다시 감지').props.onClick();
      await button(render(), '보존 끄기').props.onClick();
      assert.deepEqual(requests.map(request => request.payload.mode), ['auto', 'off']);
      props.item = {...item, step: 's3'};
      assert.equal(button(render(), '그림 원본 보존').props.disabled, true);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_layout_save_and_reload_keep_media_settings_and_exact_source_geometry():
    run_node(NODE_SETUP + r"""
    const sandbox = placementSandbox();
    const problem = { id: 'p7', title: '7', step: 's2', preserveMediaMode: 'manual',
      preserveMediaRegions: [{source: 'manual', bbox: {left: 100, top: 80, width: 400, height: 300}}],
      preserveMediaSourceRegions: [{sourcePageId: 'page1', bbox: {left: 80, top: 40, width: 400, height: 300}}],
      cropSourceOffset: {left: -20, top: -40} };
    const session = {problems: [problem], pages: [{id: 'page1', problemIds: ['p7']}]};
    const item = sandbox.mapProblemToItem(problem, 0, session);
    assert.equal(item.preserveMediaMode, 'manual');
    const saved = sandbox.materializeSessionForItems(session, [{...item, placementScaleRatio: 1.2}], '수업');
    const reloaded = JSON.parse(JSON.stringify(saved));
    for (const key of ['preserveMediaMode', 'preserveMediaRegions', 'preserveMediaSourceRegions', 'cropSourceOffset']) {
      assert.deepEqual(reloaded.problems[0][key], problem[key], key);
    }
    """)
