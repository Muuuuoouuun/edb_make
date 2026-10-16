"""Crop editing preserves geometry at page edges and accepts partial size input."""
import unittest

from test_ui_home_flows import NODE_SETUP, run_node


GEOMETRY_SETUP = NODE_SETUP + r"""
const sandbox = { Map, finiteNumber: (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback };
vm.createContext(sandbox);
vm.runInContext(section('function translateManualSplitBoxes(', 'function replacementSourceIdFor('), sandbox);
const translate = sandbox.translateManualSplitBoxes;
"""


class TestManualSplitInteractions(unittest.TestCase):
    def test_boundary_translation_preserves_sizes_spacing_and_original_boxes(self):
        run_node(GEOMETRY_SETUP + r"""
        const page = { width: 2000, height: 2800 };
        const boxes = new Map([
          ['a', { left: 100, top: 150, width: 320, height: 240 }],
          ['b', { left: 850, top: 600, width: 640, height: 400 }],
        ]);
        const original = JSON.stringify([...boxes]);
        for (const dx of [-4000, -100, 0, 120, 2000]) {
          for (const dy of [-5000, -150, 0, 300, 6000]) {
            const next = translate(boxes, page, dx, dy);
            for (const [id, box] of next) {
              assert.equal(box.width, boxes.get(id).width);
              assert.equal(box.height, boxes.get(id).height);
              assert.ok(box.left >= 0 && box.top >= 0);
              assert.ok(box.left + box.width <= page.width);
              assert.ok(box.top + box.height <= page.height);
            }
            assert.equal(next.get('b').left - next.get('a').left, 750);
            assert.equal(next.get('b').top - next.get('a').top, 450);
          }
        }
        assert.equal(JSON.stringify([...boxes]), original, 'drag cancellation still needs the untouched initial boxes');
        const empty = new Map(); assert.equal(translate(empty, page, 1, 1), empty);
        """)

    def test_repeated_keyboard_nudges_at_the_edge_do_not_shrink_a_crop(self):
        run_node(GEOMETRY_SETUP + r"""
        sandbox.manualSplitPage = { width: 1000, height: 800 };
        let state = { selectedRegionIds: ['a'], regions: [
          { id: 'a', bbox: { left: 675, top: 555, width: 320, height: 240 } },
          { id: 'b', bbox: { left: 10, top: 10, width: 50, height: 50 } },
        ] };
        const untouched = state.regions[1];
        sandbox.setManualSplit = update => { state = update(state); };
        vm.runInContext(section('  const nudgeManualSplitSelected =', '  const applyManualPageSplit =')
          + '\nglobalThis.nudge = nudgeManualSplitSelected;', sandbox);
        for (let i = 0; i < 100; i++) sandbox.nudge(10, 10);
        assert.equal(JSON.stringify(state.regions[0].bbox), JSON.stringify({ left: 680, top: 560, width: 320, height: 240 }));
        assert.equal(state.regions[1], untouched);
        """)

    def test_stamp_size_typing_keeps_partial_input_and_enter_only_commits_the_field(self):
        run_node(NODE_SETUP + r"""
        const Babel = require('./scripts/vendor/babel.min.js');
        let draft, effects = [], changes = [], prevented = 0, stopped = 0, blurred = 0, selected = 0;
        const focused = { current: false };
        const sandbox = {
          React: { createElement: (type, props) => ({ type, props }) },
          useState: initial => { if (draft === undefined) draft = initial; return [draft, value => { draft = value; }]; },
          useRef: () => focused, useEffect: effect => effects.push(effect),
          clampManualSplitStampDimension: (value, max) => Math.round(Math.max(24, Math.min(max, Number(value) || 24))),
        };
        vm.createContext(sandbox);
        vm.runInContext(Babel.transform(section('function ManualSplitStampDimensionInput(', 'function ManualSplitEditor('),
          { presets: ['react'] }).code, sandbox);
        const render = (value = 880) => {
          const tree = sandbox.ManualSplitStampDimensionInput({ value, maxValue: 2000, onChange: value => changes.push(value) });
          effects.splice(0).forEach(effect => effect()); return tree;
        };
        let input = render(); input.props.onFocus({currentTarget:{select(){selected++}}}); assert.equal(selected,1);
        for (const text of ['3', '32', '320']) {
          input.props.onChange({ target: { value: text } }); input = render(changes.at(-1) || 880);
          assert.equal(input.props.value, text, 'controlled parent updates must not replace the typed number');
        }
        assert.deepEqual(changes, [32, 320], 'partial 3 cannot be clamped to 24 during typing');
        input.props.onKeyDown({ key: 'Enter', preventDefault: () => prevented++, stopPropagation: () => stopped++,
          currentTarget: { blur: () => { blurred++; input.props.onBlur(); } } });
        assert.equal(changes.at(-1), 320); assert.equal(prevented, 1); assert.equal(stopped, 1); assert.equal(blurred, 1);
        input = render(320); input.props.onFocus(); input.props.onChange({ target: { value: '99999' } });
        input = render(320); assert.equal(input.props.value, '99999'); input.props.onBlur();
        assert.equal(changes.at(-1), 2000, 'finished out-of-range input must still respect source dimensions');
        """)


if __name__ == '__main__':
    unittest.main()
