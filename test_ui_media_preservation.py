from test_ui_placement_details import NODE_SETUP, run_node


def test_inline_figure_toggle_is_controlled_accessible_and_opens_no_dialog():
    run_node(NODE_SETUP + r"""
    const Babel = require('./scripts/vendor/babel.min.js');
    const sandbox = {React: {createElement: (type, props, ...children) => ({type, props: props || {}, children: children.flat(Infinity)})}};
    vm.createContext(sandbox);
    vm.runInContext(Babel.transform(section('function FigureColorToggle(', 'function HomeUpdateDialog('), {presets: ['react']}).code, sandbox);
    const changes = [];
    const tree = sandbox.FigureColorToggle({checked: true, label: '문제 속 그림 원본 유지', onChange: value => changes.push(value)});
    const input = tree.children.find(node => node.type === 'input');
    assert.equal(tree.type, 'label');
    assert.equal(input.props.role, 'switch');
    assert.equal(input.props['aria-label'], '문제 속 그림 원본 유지');
    assert.equal(input.props.checked, true);
    input.props.onChange({target: {checked: false}});
    assert.deepEqual(changes, [false]);
    assert.equal(input.props.checked, true, 'server state controls the switch until a successful response');
    assert.equal(sandbox.FigureColorToggle({checked: false, busy: true}).children.find(node => node.type === 'input').props.disabled, true);
    for (const mode of ['grayscale', 'off']) assert.equal(sandbox.keepsFigureColors({preserveMediaMode: mode}), false);
    for (const mode of ['auto', 'manual', undefined]) assert.equal(sandbox.keepsFigureColors({preserveMediaMode: mode}), true);
    assert.ok(!source.includes('function MediaPreservationEditor('));
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
