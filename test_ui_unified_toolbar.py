"""Single-row board chrome retains the actual editing callbacks and guards."""
from pathlib import Path
import unittest

from test_ui_placement_details import ESTIMATE_RENDER_SETUP, run_node


class UnifiedBoardToolbarTests(unittest.TestCase):
    def test_toolbar_is_the_only_persistent_board_command_row(self):
        source = (Path(__file__).parent / 'ui_prototype/app.jsx').read_text(encoding='utf-8')
        board = source.split('function BoardStage({', 1)[1].split('function HomeDashboard(', 1)[0]
        self.assertEqual(board.count('className="stage-toolbar stage-unified-toolbar"'), 1)
        for removed in ('focus-review-strip', 'stage-smart-help', 'stage-estimate-bar',
                        '<SessionRecognitionAction', 'className="stage-status"'):
            self.assertNotIn(removed, board)
        self.assertIn('smartMove && boardDropTarget &&', board)
        self.assertIn('className="sr-only" id="smart-move-help"', board)

    def test_zoom_fit_smart_move_and_panel_callbacks_stay_connected(self):
        run_node(ESTIMATE_RENDER_SETUP + r"""
        const zooms = []; let fits = 0, opens = 0, reviews = 0;
        Object.assign(sandbox, {
          focusActiveTileAtPreviewZoom: zoom => zooms.push(zoom),
          fitActiveTileInView: () => fits++, onOpenPanel: () => opens++,
          onOpenPendingReview: () => reviews++,
          activePlacement: { rowHeightPages: 1.2 },
          setSmartMove: update => { sandbox.smartMove = update(sandbox.smartMove); },
          focusMode: true, panelCollapsed: true,
        });
        let tree = renderEstimate();
        const byLabel = label => walk(tree).find(n => n.props['aria-label'] === label);
        byLabel('미리보기 축소').props.onClick();
        byLabel('미리보기 확대').props.onClick();
        findClass(tree, 'stage-preview-zoom-value').props.onClick();
        assert.deepEqual(zooms, [0.9, 1.1, 1]);
        byLabel('선택 문항 맞춤').props.onClick(); assert.equal(fits, 1);
        findClass(tree, 'stage-smart-move').props.onClick();
        assert.equal(sandbox.smartMove, true);
        byLabel('편집·설정 펼치기').props.onClick(); assert.equal(opens, 1);
        assert.equal(findClass(tree, 'stage-review-shortcut'), undefined, 'no empty review badge');
        sandbox.reviewAttentionCount = 3; tree = renderEstimate();
        findClass(tree, 'stage-review-shortcut').props.onClick(); assert.equal(reviews, 1);
        sandbox.panelCollapsed = false;
        assert.equal(findClass(renderEstimate(), 'focus-panel-reopen'), undefined);
        sandbox.panelCollapsed = true; sandbox.focusMode = false;
        assert.equal(findClass(renderEstimate(), 'focus-panel-reopen'), undefined);
        sandbox.interactionBusy = true; tree = renderEstimate();
        assert.equal(findClass(tree, 'stage-smart-move').props.disabled, true);
        assert.equal(findClass(tree, 'stage-review-shortcut').props.disabled, true);
        sandbox.interactionBusy = false; sandbox.positioningId = 'p1';
        assert.equal(findClass(renderEstimate(), 'stage-smart-move').props.disabled, true);
        """)

    def test_more_menu_keeps_page_guides_gap_and_session_recognition_guards(self):
        run_node(ESTIMATE_RENDER_SETUP + r"""
        let guides = 0, recognitions = 0; const gaps = [];
        Object.assign(sandbox, {
          togglePageGuides: () => guides++, sourcePageCount: 2,
          onRecognizeSession: () => recognitions++, canRecognizeSession: false,
          recognitionDisabledReason: '인식 진행 중', sessionEditState: { dirty: true },
          reducePlacementGap: id => gaps.push(id),
          derivePlacementGapActionFromLayout: () => ({ ids: ['p2'] }),
        });
        const menu = () => walk(renderEstimate()).find(n => n.props.id === 'board-tools').props.items;
        const action = id => menu().find(item => item.id === id);
        assert.ok(action('guides').label.includes('숨기기'));
        action('guides').onSelect(); assert.equal(guides, 1);
        sandbox.pageGuides = false; assert.ok(action('guides').label.includes('보이기'));
        assert.equal(action('gap').disabled, true, 'gap action is opt-in');
        sandbox.smartMove = true; assert.equal(action('gap').disabled, false);
        action('gap').onSelect(); assert.deepEqual(gaps, ['p1']);
        for (const key of ['interactionBusy', 'positioningId']) {
          sandbox[key] = true; assert.equal(action('gap').disabled, true); sandbox[key] = false;
        }
        sandbox.derivePlacementGapActionFromLayout = () => ({ ids: [] });
        assert.equal(action('gap').disabled, true, 'no removable space');
        assert.equal(action('recognize').disabled, true);
        assert.equal(action('recognize').description, '인식 진행 중');
        sandbox.canRecognizeSession = true;
        assert.ok(action('recognize').description.includes('변경한 수업을 먼저 저장'));
        action('recognize').onSelect(); assert.equal(recognitions, 1);
        sandbox.sourcePageCount = 0; assert.equal(action('recognize'), undefined);
        """)


if __name__ == '__main__':
    unittest.main()
