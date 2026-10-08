from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


GEOMETRY_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
function problemFor(format, id = format) {
  const baseWidth = format === 'v2' ? 301 : 540;
  const baseHeight = baseWidth * (6000 / 480) / 590;
  return { id, title: id, inputIntent: 'page-as-is', placementMode: 'continuous-page-as-is',
    actualHeightPages: baseHeight, placementScaleRatio: 3,
    imageRecordGeometry: { cropFormat: format, baseDisplayWidthPx: baseWidth, maxDisplayWidthPx: 1142,
      canvasPageHeightPx: 590, continuousGapPages: 20 / 590, baseHeightPages: baseHeight,
      reservedHeightPages: baseHeight } };
}
function sessionFor(problems) {
  return { session_name: '실제 이미지 배치', record_mode: 'image-only', crop_format: 'v1',
    problems, pages: [{ id: 'source', problemIds: problems.map(problem => problem.id) }] };
}
const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 0.000002,
  `${message}: ${actual} != ${expected}`);
"""


SIDEBAR_RENDER_SETUP = GEOMETRY_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
sandbox.React = { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) };
sandbox.reviewStatusClass = () => 'normal';
sandbox.problemDisplayName = item => item.name || item.id;
function text(node) {
  return typeof node === 'string' || typeof node === 'number' ? String(node) : node?.children?.map(text).join('') || '';
}
function inspectSidebarGroup(items, selectedGroupItems, isGroup = false) {
  Object.assign(sandbox, { items, selectedGroupItems, item: selectedGroupItems[0], activeIndex: 0,
    placementLayoutItems: sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'classin-grid'),
    useMemo: derive => derive(),
    boardColumns: 1, layoutGapMode: 'classin-grid', selectedPassageGroupId: isGroup ? 'group' : '',
    selectedPassageRangeLabel: '', passageFragmentCount: isGroup ? 1 : 0,
    passageQuestionCount: Math.max(0, selectedGroupItems.length - 1) });
  const calculations = section('  const placementGroupLayoutItems =', '  const currentInputIntent =');
  const markup = section('            <section className="placement-context"', '            <section className="placement-section">');
  const body = `(() => { ${calculations}
    globalThis.sidebarGeometryReport = { height: placementGroupHeightPages, intervals: placementIntervals,
      overlap: placementHasOverlap, element: (${markup}) }; })()`;
  vm.runInContext(Babel.transform(body, { presets: ['react'] }).code, sandbox);
  return sandbox.sidebarGeometryReport;
}
"""


class TestUiImageRecordGeometry(unittest.TestCase):
    def test_sidebar_rendered_height_uses_native_geometry_and_keeps_reservation_separate(self) -> None:
        run_node(SIDEBAR_RENDER_SETUP + r"""
        for (const [format, label, height] of [['v1', '24.2', 24.194915254237287], ['v2', '19.1', 19.13135593220339]]) {
          const problem = problemFor(format);
          problem.actualHeightPages = 1.2;
          const item = sandbox.mapProblemToItem(problem, 0);
          const report = inspectSidebarGroup([item], [item]);
          near(report.height, height, `${format} sidebar rendered height`);
          assert.ok(text(report.element).includes(`자료 1개 · EDB 높이 ${label}p`), text(report.element));
          assert.equal(report.overlap, false);
        }
        const regular = problemFor('v1', 'regular');
        regular.inputIntent = 'single-problem'; regular.placementMode = null;
        regular.imageRecordGeometry.baseHeightPages = 0.8;
        regular.imageRecordGeometry.reservedHeightPages = 2;
        regular.placementAutoFitDisabled = true;
        const item = sandbox.mapProblemToItem(regular, 0);
        const report = inspectSidebarGroup([item], [item]);
        near(report.height, 0.8 * 1142 / 540, 'the sidebar reports image height rather than six reserved pages');
        near(report.intervals[0].next, 6, 'the interval keeps the actual reserved cursor');
        assert.ok(text(report.element).includes('EDB 높이 1.7p'));
        """)

    def test_sidebar_group_diagnostics_use_current_reflow_without_false_width_cap_overlap(self) -> None:
        run_node(SIDEBAR_RENDER_SETUP + r"""
        const long = sandbox.mapProblemToItem(problemFor('v1', 'long'), 0);
        const shortProblem = problemFor('v1', 'short');
        shortProblem.inputIntent = 'single-problem'; shortProblem.placementMode = null;
        shortProblem.placementScaleRatio = 1;
        shortProblem.imageRecordGeometry.baseHeightPages = 0.8;
        shortProblem.imageRecordGeometry.reservedHeightPages = 0.8;
        const short = sandbox.mapProblemToItem(shortProblem, 1);
        // The saved/raw heights and coordinates may predate the current
        // geometry projection. The panel must use the same fresh reflow as
        // BoardStage rather than rawHeight * requested300%.
        long.startYPages = 20; long.renderedTopYPages = NaN; long.renderedBottomYPages = Infinity;
        short.startYPages = 1; short.renderedTopYPages = -1;
        const items = [long, short];
        const before = JSON.stringify(items);
        const report = inspectSidebarGroup(items, items, true);
        assert.equal(report.overlap, false, 'width-capped long image does not overlap its following problem');
        near(report.intervals[0].bottom, 24.194915254237287, 'native long bottom');
        near(report.intervals[1].start, 24.228814, 'short starts after the real continuous gap');
        near(report.height, 24.994915254237287, 'group total rendered height');
        assert.ok(text(report.element).includes('EDB 높이 25.0p'));
        assert.equal(JSON.stringify(items), before, 'summary and diagnostics cannot rewrite editor items');
        const selectedShort = inspectSidebarGroup(items, [short]);
        near(selectedShort.intervals[0].start, 24.228814, 'a subset keeps its position in the complete board');
        """)

    def test_cleared_magnet_stays_null_through_mapping_reflow_materialize_and_publish(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        vm.runInContext(section('function prepareSessionPublishRequest(', 'function publishPlanRequestSignature('), sandbox);
        for (const value of [null, undefined, '', '   ', NaN, Infinity]) {
          assert.equal(sandbox.normalizePlacementMagnetColumnIndex(value, 3), null);
        }
        assert.equal(sandbox.normalizePlacementMagnetColumnIndex(0, 3), 0);
        assert.equal(sandbox.normalizePlacementMagnetColumnIndex('0', 3), 0);
        assert.equal(sandbox.normalizePlacementMagnetColumnIndex(9, 3), 2);
        for (const [camel, snake, expected] of [[null, null, null], [null, 0, null], [0, null, 0]]) {
          const problem = problemFor('v1', 'p1');
          problem.inputIntent = 'single-problem'; problem.placementMode = null;
          problem.imageRecordGeometry.baseHeightPages = 0.8;
          problem.imageRecordGeometry.reservedHeightPages = 0.8;
          problem.placementScaleRatio = 1;
          problem.placementAutoFitDisabled = true;
          problem.placementXEdited = true; problem.placementXRatio = 0.37;
          problem.placementMagnetColumnIndex = camel; problem.placement_magnet_column_index = snake;
          const raw = sessionFor([problem]);
          const mapped = sandbox.mapProblemToItem(problem, 0, raw);
          assert.equal(mapped.placementMagnetColumnIndex, expected, 'explicit camel null wins over a stale snake zero');
          const items = sandbox.reflowItemsForBoardOrder([mapped], 1.2, 1, 'classin-grid');
          assert.equal(items[0].placementMagnetColumnIndex, expected);
          assert.equal(items[0].placement_magnet_column_index, expected);
          const materialized = sandbox.materializeSessionForItems(raw, items, raw.session_name, 1, 'classin-grid');
          for (const field of ['placementMagnetColumnIndex', 'placement_magnet_column_index']) {
            assert.equal(materialized.problems[0][field], expected, field);
          }
          const request = sandbox.prepareSessionPublishRequest(materialized, items, raw.session_name, 1, 'classin-grid');
          assert.equal(request.sessionForPublish.problems[0].placementMagnetColumnIndex, expected);
          const reloaded = request.sessionForPublish.problems.map(problem => sandbox.mapProblemToItem(problem, 0, request.sessionForPublish));
          assert.equal(sandbox.reflowItemsForBoardOrder(reloaded, 1.2, 1, 'classin-grid')[0].placementMagnetColumnIndex, expected);
        }
        const patched = sandbox.applyPlacementPatchToItem({ placementXEdited: true, placement_x_edited: true,
          placementMagnetColumnIndex: 0, placement_magnet_column_index: 0 },
          { xRatio: 0.4, xEdited: false, magnetColumnIndex: null });
        assert.equal(patched.placementMagnetColumnIndex, null);
        assert.equal(patched.placement_magnet_column_index, null);
        assert.equal(patched.placementXEdited, false);
        assert.equal(patched.placement_x_edited, false);
        """)

    def test_board_stage_cleared_magnet_uses_actual_column_and_keeps_explicit_zero(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        const calculation = section('      const displayColumnIndex = normalizePlacementMagnetColumnIndex(', '      return {');
        for (const [camel, snake, expected] of [[null, null, 2], [null, 0, 2], [0, null, 0]]) {
          Object.assign(sandbox, { it: { boardColumnCount: 3, boardColumnIndex: 2,
            placementMagnetColumnIndex: camel, placement_magnet_column_index: snake }, columnCount: 3 });
          vm.runInContext(`(() => { ${calculation} globalThis.actualColumn = displayColumnIndex; })()`, sandbox);
          assert.equal(sandbox.actualColumn, expected, 'cleared magnets cannot silently snap a right-column item left');
        }
        """)

    def test_actual_publish_success_adopts_response_geometry_and_controls_without_false_unsaved_state(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        const onPublishStart = source.indexOf('  const onPublish = async () => {');
        const adoptionStart = source.indexOf('      captureSessionRevision(json);', onPublishStart);
        const adoptionEnd = source.indexOf('      refreshSessionHistory();', adoptionStart);
        assert.ok(onPublishStart >= 0 && adoptionStart > onPublishStart && adoptionEnd > adoptionStart);
        for (const preservedAutoFit of [false, true]) {
          const response = sessionFor([problemFor('v1', 'p1'), problemFor('v1', 'p2')]);
          for (const problem of response.problems) problem.placementAutoFitDisabled = preservedAutoFit;
          const oldProblems = JSON.parse(JSON.stringify(response.problems));
          for (const problem of oldProblems) {
            problem.placementAutoFitDisabled = true;
            problem.imageRecordGeometry.baseHeightPages = 11.44;
          }
          const oldItems = sandbox.reflowItemsForBoardOrder(oldProblems.map(problem =>
            sandbox.mapProblemToItem(problem, 0, response)), 1.2, 1, 'classin-grid');
          assert.equal(sandbox.derivePlacementSaveState(response, oldItems, 1, 'classin-grid').dirty, true,
            'retaining pre-publish flags or rounded geometry falsely marks a saved response dirty');
          Object.assign(sandbox, {
            json: { session: response }, session: response, items: oldItems,
            boardColumns: 1, layoutGapMode: 'classin-grid', activeId: 'p2', view: 'board',
            captureSessionRevision() {},
            setSession: value => { sandbox.session = value; },
            setItems: value => { sandbox.items = value; },
            setActiveId: update => { sandbox.activeId = update(sandbox.activeId); },
            setLayoutGapModeState: value => { sandbox.layoutGapMode = value; },
          });
          vm.runInContext(`(() => {${source.slice(adoptionStart, adoptionEnd)}})()`, sandbox);
          assert.notEqual(sandbox.items, oldItems, 'adopt the returned problem projection after successful publishing');
          assert.equal(sandbox.activeId, 'p2');
          assert.equal(sandbox.view, 'board');
          assert.equal(sandbox.items[0].placementAutoFitDisabled, preservedAutoFit);
          assert.equal(sandbox.items[0].placementScaleRatio, 3);
          near(sandbox.items[0].imageRecordGeometry.baseHeightPages, 11.440677966101696,
            'use the exact server geometry rather than the rounded local estimate');
          const state = sandbox.derivePlacementSaveState(sandbox.session, sandbox.items, 1, sandbox.layoutGapMode);
          assert.equal(state.dirty, false);
          assert.equal(state.label, '저장됨');
        }
        """)

    def test_regular_image_reservation_remains_separate_from_its_width_capped_display_height(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        const problem = problemFor('v1');
        problem.inputIntent = 'single-problem';
        problem.placementMode = null;
        problem.actualHeightPages = 0.8;
        problem.imageRecordGeometry.baseHeightPages = 0.8;
        problem.imageRecordGeometry.reservedHeightPages = 2;
        const item = sandbox.mapProblemToItem(problem, 0);
        near(sandbox.itemRenderedHeightPages(item), 1.6918518518518518, 'width-capped displayed image');
        near(sandbox.itemFlowHeightPages(item), 6, 'native reservation keeps the requested scale');
        const reflowed = sandbox.reflowItemsForBoardOrder([item], 1.2, 1, 'classin-grid')[0];
        near(reflowed.renderedBottomYPages, 1.6918518518518518, 'displayed bottom');
        near(reflowed.snappedNextStartYPages, 6, 'reserved next cursor');
        assert.equal(reflowed.placementScaleRatio, 3);
        """)

    def test_fresh_geometry_projection_does_not_mark_stale_legacy_coordinates_as_unsaved_edits(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        const raw = sessionFor([problemFor('v1'), problemFor('v2')]);
        raw.problems[0].startYPages = 0;
        raw.problems[0].snappedNextStartYPages = 34.355932;
        raw.problems[1].startYPages = 34.355932;
        const items = sandbox.reflowItemsForBoardOrder(raw.problems.map(sandbox.mapProblemToItem), 1.2, 1, 'classin-grid');
        const loaded = sandbox.derivePlacementSaveState(raw, items, 1, 'classin-grid');
        assert.equal(loaded.dirty, false, 'server geometry changes may reflow old coordinates without a user edit');
        assert.equal(loaded.label, '저장됨');
        const adjusted = [sandbox.applyPlacementPatchToItem(items[0], { scaleRatio: 2.8 }), items[1]];
        near(sandbox.itemRenderedHeightPages(adjusted[0]), sandbox.itemRenderedHeightPages(items[0]),
          'both requested widths exceed the native cap');
        assert.equal(sandbox.derivePlacementSaveState(raw, adjusted, 1, 'classin-grid').dirty, true,
          'a real requested-scale edit must remain dirty even when its displayed size is capped');
        """)

    def test_actual_v1_width_cap_and_v2_scale_match_native_image_record_height(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        for (const [format, expectedHeight, expectedScale] of [
          ['v1', 24.194915254237287, 1142 / 540],
          ['v2', 19.13135593220339, 3],
        ]) {
          const problem = problemFor(format);
          const item = sandbox.mapProblemToItem(problem, 0);
          assert.equal(item.placementScaleRatio, 3, 'preserve the requested placement value');
          near(sandbox.itemRenderedScaleRatio(item), expectedScale, 'effective record scale');
          near(sandbox.itemRenderedHeightPages(item), expectedHeight, 'native display height');
          assert.equal(item.placementScaleRatio, 3, 'calculating the display size must not rewrite the request');
        }
        """)

    def test_exporter_geometry_height_overrides_stale_estimates_and_supports_both_field_names(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        for (const field of ['imageRecordGeometry', 'image_record_geometry']) {
          const problem = problemFor('v1');
          problem.actualHeightPages = 60;
          const geometry = problem.imageRecordGeometry;
          delete problem.imageRecordGeometry;
          problem[field] = geometry;
          const item = sandbox.mapProblemToItem(problem, 0);
          near(sandbox.itemHeightPages(item), 11.440677966101696, 'actual image geometry wins over stale estimates');
          near(sandbox.itemRenderedHeightPages(item), 24.194915254237287, 'capped native display height');
        }
        """)

    def test_mixed_native_formats_reflow_using_actual_height_and_default_continuous_gap(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        const problems = [problemFor('v1'), problemFor('v2')];
        const items = sandbox.reflowItemsForBoardOrder(problems.map(sandbox.mapProblemToItem), 1.2, 1, 'classin-grid');
        near(items[0].renderedBottomYPages, 24.194915254237287, 'first record end');
        near(items[1].startYPages, 24.228813559322034, 'next native record starts after the 20px gap');
        near(items[1].renderedBottomYPages, 43.360169491525426, 'second native record end');
        near(items[1].snappedNextStartYPages, 43.39406779661017, 'next native flow cursor');
        const estimate = sandbox.deriveBoardPreviewEstimate(items, 'v1', 1.2);
        near(estimate.active.renderedBottomPages, 24.194915254237287, 'preview uses native end');
        near(estimate.active.blankHeightPages, 20 / 590, 'preview reports the actual continuous gap');
        assert.equal(estimate.active.scaleRatio, 3, 'requested scale stays available for editing');
        assert.equal(estimate.classinPageCount, 37);
        """)

    def test_compact_and_explicit_gap_override_take_priority_over_native_default_gap(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        for (const [mode, override, expectedGap] of [
          ['compact', undefined, 0], ['classin-grid', 0, 0],
          ['classin-grid', 0.2, 0.2], ['compact', 0.2, 0.2],
        ]) {
          const problem = problemFor('v1');
          if (override !== undefined) problem.placementGapAfterPages = override;
          const item = sandbox.reflowItemsForBoardOrder([sandbox.mapProblemToItem(problem, 0)], 1.2, 1, mode)[0];
          near(item.snappedNextStartYPages - item.renderedBottomYPages, expectedGap, `${mode} gap override`);
        }
        """)

    def test_publish_and_materialize_preserve_requested_scale_and_native_cursor_on_reload(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        vm.runInContext(section('function prepareSessionPublishRequest(', 'function publishPlanRequestSignature('), sandbox);
        const raw = sessionFor([problemFor('v1'), problemFor('v2')]);
        const items = raw.problems.map(sandbox.mapProblemToItem);
        const request = sandbox.prepareSessionPublishRequest(raw, items, raw.session_name, 1, 'classin-grid');
        assert.equal(request.placements.v1.scaleRatio, 3);
        assert.equal(request.sessionForPublish.problems[0].placementScaleRatio, 3);
        near(request.sessionForPublish.problems[0].renderedBottomYPages, 24.194915254237287, 'serialized native end');
        near(request.sessionForPublish.problems[1].startYPages, 24.228813559322034, 'serialized next cursor');
        assert.ok(request.sessionForPublish.problems[0].imageRecordGeometry);
        const reloaded = request.sessionForPublish.problems.map((problem, index) =>
          sandbox.mapProblemToItem(problem, index, request.sessionForPublish));
        const afterReload = sandbox.reflowItemsForBoardOrder(reloaded, 1.2, 1, 'classin-grid');
        near(afterReload[1].startYPages, 24.228813559322034, 'reload preserves native geometry');
        assert.equal(afterReload[0].placementScaleRatio, 3);
        """)

    def test_real_legacy_image_only_context_recovers_geometry_but_mock_items_keep_old_behaviour(self) -> None:
        run_node(GEOMETRY_SETUP + r"""
        for (const format of ['v1', 'v2']) {
          const problem = problemFor(format);
          delete problem.imageRecordGeometry;
          problem.recordMode = 'image-only';
          problem.imagePath = 'file:///tmp/synthetic-cutout.png';
          problem.layoutDiagnostics = { actualHeightPages: problem.actualHeightPages };
          const session = { ...sessionFor([problem]), crop_format: format,
            template: { fixed_left_zone_ratio: 0.4, metadata: {} } };
          const item = sandbox.mapProblemToItem(problem, 0, session);
          assert.ok(item.imageRecordGeometry, 'real legacy image-only sessions can use their existing geometry diagnostics');
          near(sandbox.itemRenderedHeightPages(item), format === 'v1' ? 24.194915254237287 : 19.13135593220339,
            'legacy native display height');
        }
        const mock = { id: 'mock', actualHeightPages: 11, inputIntent: 'page-as-is', placementScaleRatio: 3 };
        const item = sandbox.mapProblemToItem(mock, 0);
        assert.ok(!item.imageRecordGeometry, 'mock data must not acquire inferred exporter geometry');
        assert.equal(sandbox.itemRenderedHeightPages(item), 33);
        const arrayMapped = [mock].map(sandbox.mapProblemToItem)[0];
        assert.ok(!arrayMapped.imageRecordGeometry, 'Array.map third argument is not a raw session');
        assert.equal(sandbox.itemRenderedHeightPages(arrayMapped), 33);
        """)


if __name__ == '__main__':
    unittest.main()
