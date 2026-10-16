"""Manual crops replace exactly their declared scope and reuse the decoded page."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from PIL import Image

import app_server
from test_ui_home_flows import NODE_SETUP, run_node


class TestManualCropWorkflow(TestCase):
    def test_bulk_crops_reuse_source_and_preserve_pixels_and_untargeted_problems(self):
        for mode in ('RGB', 'RGBA', 'L', 'P'):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'page.png'
                image = Image.new('RGB', (120, 160), 'white')
                for y in range(160):
                    for x in range(120):
                        image.putpixel((x, y), (x * 2, y, (x + y) % 256))
                image = image.convert(mode)
                image.save(source)
                session = {
                    'output_dir': str(root),
                    'pages': [
                        {'id': 'a', 'sourceImagePath': str(source), 'problemIds': ['p1', 'p2']},
                        {'id': 'b', 'sourceImagePath': str(source), 'problemIds': ['p3']},
                    ],
                    'problems': [
                        {'id': 'p1', 'sourcePageId': 'a', 'problemNumber': '8'},
                        {'id': 'p2', 'sourcePageId': 'a', 'problemNumber': '9'},
                        {'id': 'p3', 'sourcePageId': 'b', 'problemNumber': '10'},
                    ],
                }
                original = copy.deepcopy(session)
                regions = [{'bbox': {'left': 2 + i, 'top': 3 + i * 2, 'width': 31, 'height': 27}} for i in range(8)]
                with mock.patch.object(app_server, '_crop_loaded_image_by_bbox', wraps=app_server._crop_loaded_image_by_bbox) as crop:
                    updated = app_server._mutate_bulk_crop(session, 'a', regions, ['p1'])
                self.assertEqual(crop.call_count, 8)
                self.assertEqual(len({id(call.args[0]) for call in crop.call_args_list}), 1)
                self.assertEqual(updated['problems'][-2:], original['problems'][1:])
                self.assertEqual(updated['pages'][1], original['pages'][1])
                for problem, region in zip(updated['problems'][:8], regions):
                    box = region['bbox']
                    expected = image.crop((box['left'], box['top'], box['left'] + 31, box['top'] + 27)).convert('RGBA')
                    with Image.open(app_server._resolve_session_path(problem['imagePath'])) as actual:
                        self.assertEqual(actual.size, (31, 27))
                        self.assertEqual(actual.convert('RGBA').tobytes(), expected.tobytes())

    def test_scope_summary_uses_actual_order_and_does_not_include_gaps(self):
        run_node(NODE_SETUP + r"""
        const sandbox = {}; vm.createContext(sandbox);
        vm.runInContext(section('function manualSplitScopeSummary(', 'function ManualSplitEditor('), sandbox);
        const page = { id: 'a', pageNumber: 2 };
        const items = ['p4', 'p1', 'p2', 'p5', 'p3'].map(id => ({ id }));
        const summary = sandbox.manualSplitScopeSummary(page, items, ['p4', 'p2', 'p5'], 'selection');
        assert.equal(summary.rangeLabel, '목록 1번, 3–4번');
        assert.equal(summary.label, '선택 3개만 교체');
        assert.equal(summary.pageLabel, '2페이지');
        assert.equal(sandbox.manualSplitScopeSummary(page, items, [], 'append').label, '기존 문제 유지 · 새 영역 추가');
        assert.equal(sandbox.manualSplitScopeSummary(page, items, ['p1', 'p2'], 'page').label, '페이지 전체 2개 교체');
        """)

    def test_switching_scope_retains_drafts_and_uses_only_the_active_page(self):
        run_node(NODE_SETUP + r"""
        const regions = [{ id: 'new', bbox: { left: 1, top: 2, width: 30, height: 40 } }];
        let state = { pageId: 'a', selectionProblemIds: ['p2'], replaceProblemIds: ['p2'], regions, stampBox: {width:320,height:240} };
        const sandbox = { manualSplitPage: {id:'a'}, mutating:false,
          session: {problems:[{id:'p1',sourcePageId:'a'},{id:'p2',sourcePageId:'a'},{id:'p3',sourcePageId:'b'}]},
          setManualSplit: update => {state=update(state)} };
        vm.createContext(sandbox);
        vm.runInContext(section('  const changeManualSplitScope =', '  const guidedSplitReplacementIds =')+'\nglobalThis.change = changeManualSplitScope;', sandbox);
        for (const [scope,expected] of [['page',['p1','p2']],['append',[]],['selection',['p2']]]) {
          sandbox.change(scope); assert.equal(JSON.stringify(state.replaceProblemIds),JSON.stringify(expected));
          assert.equal(state.regions,regions); assert.equal(state.stampBox.width,320);
        }
        sandbox.mutating=true; sandbox.change('page'); assert.equal(state.scope,'selection');
        """)

    def test_escape_preserves_completed_regions_and_blurs_size_input(self):
        run_node(NODE_SETUP + r"""
        for (const mode of ['draw','stamp','select']) {
          let handler, selectedMode, cancellations=0, blur=0;
          const regions=[{id:'saved'}];
          const sandbox={manualSplit:{mode,regions},manualSplitShortcutHelpOpen:false,mutating:false,
            useEffect: fn => fn(), manualSplitPage:{}, isEditableKeyboardTarget:()=>true,
            window:{addEventListener:(_,fn)=>{handler=fn}},
            cancelManualSplitGesture:()=>cancellations++, setManualSplitMode:value=>{selectedMode=value},
          };
          vm.createContext(sandbox);
          vm.runInContext(section('  useEffect(() => {\n    if (!manualSplit) return undefined;', '\n  if (!pages.length)'),sandbox);
          handler({key:'Escape',preventDefault(){},target:{blur(){blur++}}});
          assert.equal(selectedMode,'select');assert.equal(cancellations,1);assert.equal(blur,1);
          assert.equal(sandbox.manualSplit.regions,regions);
        }
        """)
