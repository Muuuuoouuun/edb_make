// 칠판 자료 편집기 — main app
const { useState, useRef, useEffect, useLayoutEffect, useMemo, useCallback } = React;

function reportRuntimeDiagnostic(error, detail = {}) {
  const payload = {
    type: detail.type || 'runtime',
    message: error?.message || String(error || '알 수 없는 오류'),
    error,
    ...detail,
  };
  try {
    console.error('[board-runtime]', payload.message, payload);
  } catch (_err) {
    // Console logging is best-effort; visible diagnostics are handled below.
  }
  if (typeof window.EDB_REPORT_RUNTIME_ERROR === 'function') {
    window.EDB_REPORT_RUNTIME_ERROR(payload);
  }
  return payload;
}

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    reportRuntimeDiagnostic(error, {
      type: 'react-render',
      componentStack: info?.componentStack || '',
    });
  }

  render() {
    if (this.state.error) {
      return React.createElement(
        'div',
        {
          className: 'runtime-crash-panel',
          style: {
            margin: 16,
            padding: 16,
            border: '1px solid #ef4444',
            background: '#fff5f5',
            color: '#1f2937',
            font: '13px/1.5 system-ui,-apple-system,sans-serif',
            whiteSpace: 'pre-wrap',
          },
        },
        `앱 화면을 렌더링하지 못했습니다.\n\n${this.state.error?.message || this.state.error}`
      );
    }
    return this.props.children;
  }
}

function requiredWindowHelper(namespace, helperName, fallback) {
  const helper = namespace && namespace[helperName];
  if (typeof helper === 'function') return helper;
  reportRuntimeDiagnostic(new Error(`필수 프론트엔드 helper 누락: ${helperName}`), {
    type: 'missing-helper',
    helperName,
  });
  return fallback;
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "dark": false,
  "boardColor": "#1d3a2c",
  "accent": "#2f6fed",
  "boardColumns": 2
}/*EDITMODE-END*/;

const BOARD_COLORS = ['#1d3a2c', '#101418', '#264653', '#3a2f24'];
const ACCENTS = ['#2f6fed', '#6d3df0', '#1f7a4a', '#d97757'];

const SAMPLE_NAMES = [
  '원과 접선의 성질', '근의 공식 유도', '삼각비 특수각 표', '이차함수 그래프', '닮음 도형 예제',
  '영어 24번 지문', '함수의 극한', '미분계수의 정의', '적분 기본 정리', '벡터 내적 활용',
  '확률 조건부', '이항분포 표', '수열의 합', '점화식 풀이', '복소평면 회전',
  '도함수와 접선', '함수의 연속', '평균값의 정리', '곱의 미분법', '몫의 미분법',
  '삼각함수 합성', '로그 성질', '지수 방정식', '부등식 영역', '원의 방정식',
];
const SAMPLE_SOURCES = ['고1 기하 · p.42', '개념원리 · p.15', 'PDF · 3쪽', '함수 단원 · p.78', '교재 자체 촬영', '모의고사 · 6월', '쎈 · 78번', '마플 · 단원평가', '판서노트 · 4/12', '학생 질문 캡처'];
const SAMPLE_KINDS = ['geometry-circle','equation','table','graph','geometry-triangles','paragraph'];
const SAMPLE_STEPS = ['raw','raw','raw','s1','s2','raw'];

const REORDER_HELPERS = window.EDB_REORDER || {};
const PUBLISH_GUARD = window.EDB_PUBLISH_GUARD || {};
const findBoardPlacementOverlaps = requiredWindowHelper(PUBLISH_GUARD, 'findBoardPlacementOverlaps', () => []);
const findPassageGroupSourceReuse = requiredWindowHelper(PUBLISH_GUARD, 'findPassageGroupSourceReuse', () => []);
const findSourceProblemOverlaps = requiredWindowHelper(PUBLISH_GUARD, 'findSourceProblemOverlaps', () => []);
const reorderItemsForDrop = REORDER_HELPERS.reorderItemsForDrop || ((items, fromId, toId, position = 'before') => {
  const sourceId = fromId == null ? '' : String(fromId);
  const targetId = toId == null ? '' : String(toId);
  if (!Array.isArray(items) || !sourceId || !targetId || sourceId === targetId) return items;
  const fromIndex = items.findIndex(item => String(item.id) === sourceId);
  const toIndex = items.findIndex(item => String(item.id) === targetId);
  if (fromIndex < 0 || toIndex < 0) return items;
  const next = items.slice();
  const [moved] = next.splice(fromIndex, 1);
  const targetIndex = next.findIndex(item => String(item.id) === targetId);
  if (targetIndex < 0) return items;
  next.splice(targetIndex + (position === 'after' ? 1 : 0), 0, moved);
  return next;
});
const dropPositionFromClientY = REORDER_HELPERS.dropPositionFromClientY || ((rect, clientY) => (
  clientY > rect.top + rect.height / 2 ? 'after' : 'before'
));

// 자료별 자연 높이 (1.0 = 한 페이지)
const HEIGHT_BY_KIND = {
  'equation': 0.55,
  'geometry-triangles': 0.75,
  'graph': 0.85,
  'geometry-circle': 0.95,
  'table': 1.4,
  'paragraph': 1.85,
};
const heightForKind = k => HEIGHT_BY_KIND[k] || 0.8;
const FIXED_LEFT_ZONE_RATIO = 1 / 3;
const BOARD_COLUMN_MIN = 1;
const BOARD_COLUMN_MAX = 3;
const BOARD_COLUMN_MAGNET_THRESHOLD_PX = 34;
const DEFAULT_SLOT_HEIGHT_PAGES = 1.2;
const DEFAULT_PLACEMENT_X_RATIO = 0;
const DEFAULT_PLACEMENT_Y_RATIO = 0;
const DEFAULT_PLACEMENT_SCALE_RATIO = 1;
const PLACEMENT_SCALE_MIN = 0.6;
const PLACEMENT_SCALE_MAX = 1.6;
const PLACEMENT_NUDGE_STEP = 0.04;
const PLACEMENT_SCALE_STEP = 0.05;
const ADJACENT_RETRY_PADDING_RATIO = 0.16;
const ADJACENT_RETRY_MIN_PADDING_PX = 28;
const BOARD_DRAG_REORDER_THRESHOLD_PX = 28;
const BOARD_DRAG_AUTOSCROLL_EDGE_PX = 58;
const BOARD_DRAG_AUTOSCROLL_MAX_PX = 22;
const MANUAL_CROP_EDGE_MAX = 0.45;
const MANUAL_CROP_OUTSET_MAX = 0.60;
const MANUAL_CROP_EDGE_STEP = 0.01;
const MANUAL_SPLIT_ROW_TOLERANCE_RATIO = 0.03;
const MANUAL_SPLIT_DRAW_THRESHOLD_PX = 8;
const MANUAL_SPLIT_NUDGE_PX = 1;
const MANUAL_SPLIT_FAST_NUDGE_PX = 10;
const REVIEW_ZOOM_MIN = 0.72;
const REVIEW_ZOOM_MAX = 1.65;
const REVIEW_ZOOM_STEP = 0.08;
const RECENT_SESSIONS_COLLAPSED_KEY = 'edb.recentSessionsCollapsed';
const EMPTY_MANUAL_CROP = Object.freeze({
  leftRatio: 0,
  rightRatio: 0,
  topRatio: 0,
  bottomRatio: 0,
});

function normalizePlacementXRatio(value){
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : DEFAULT_PLACEMENT_X_RATIO;
}

function normalizePlacementYRatio(value){
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : DEFAULT_PLACEMENT_Y_RATIO;
}

function normalizePlacementScaleRatio(value, maxRatio = PLACEMENT_SCALE_MAX){
  const n = Number(value);
  const resolvedMax = Number.isFinite(Number(maxRatio))
    ? Math.max(PLACEMENT_SCALE_MIN, Math.min(PLACEMENT_SCALE_MAX, Number(maxRatio)))
    : PLACEMENT_SCALE_MAX;
  return Number.isFinite(n)
    ? Math.max(PLACEMENT_SCALE_MIN, Math.min(resolvedMax, n))
    : Math.min(DEFAULT_PLACEMENT_SCALE_RATIO, resolvedMax);
}

function normalizeBoardColumns(value){
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return BOARD_COLUMN_MIN;
  return Math.max(BOARD_COLUMN_MIN, Math.min(BOARD_COLUMN_MAX, n));
}

function boardColumnXRatio(columnIndex, columnCount){
  const resolvedCount = normalizeBoardColumns(columnCount);
  if (resolvedCount <= 1) return DEFAULT_PLACEMENT_X_RATIO;
  const resolvedIndex = Math.max(0, Math.min(resolvedCount - 1, Math.round(Number(columnIndex) || 0)));
  return Number((resolvedIndex / (resolvedCount - 1)).toFixed(6));
}

function boardColumnRatios(columnCount){
  const resolvedCount = normalizeBoardColumns(columnCount);
  return Array.from({ length: resolvedCount }, (_, index) => boardColumnXRatio(index, resolvedCount));
}

function nearestBoardColumnMagnet(xRatio, columnCount, contentWidthPx = 0, tileWidthPx = 0){
  const ratios = boardColumnRatios(columnCount);
  if (ratios.length <= 1) {
    return { index: 0, ratio: DEFAULT_PLACEMENT_X_RATIO, snapped: false, distancePx: 0 };
  }
  const travelPx = Math.max(1, Number(contentWidthPx || 0) - Number(tileWidthPx || 0));
  const thresholdRatio = BOARD_COLUMN_MAGNET_THRESHOLD_PX / travelPx;
  const normalized = normalizePlacementXRatio(xRatio);
  let nearest = { index: 0, ratio: ratios[0], distance: Math.abs(normalized - ratios[0]) };
  ratios.forEach((ratio, index) => {
    const distance = Math.abs(normalized - ratio);
    if (distance < nearest.distance) nearest = { index, ratio, distance };
  });
  const snapped = nearest.distance <= thresholdRatio;
  return {
    index: nearest.index,
    ratio: snapped ? nearest.ratio : normalized,
    snapped,
    distancePx: Math.round(nearest.distance * travelPx),
  };
}

function normalizeManualCropEdgeRatio(value){
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(-MANUAL_CROP_OUTSET_MAX, Math.min(MANUAL_CROP_EDGE_MAX, n)) : 0;
}

function manualCropValue(rawCrop, camelKey, snakeKey, plainKey){
  if (!rawCrop || typeof rawCrop !== 'object') return 0;
  const topLevelKey = `crop${camelKey.charAt(0).toUpperCase()}${camelKey.slice(1)}`;
  return rawCrop[camelKey] ?? rawCrop[topLevelKey] ?? rawCrop[snakeKey] ?? rawCrop[plainKey] ?? 0;
}

function normalizeManualCrop(rawCrop){
  return {
    leftRatio: normalizeManualCropEdgeRatio(manualCropValue(rawCrop, 'leftRatio', 'crop_left_ratio', 'left')),
    rightRatio: normalizeManualCropEdgeRatio(manualCropValue(rawCrop, 'rightRatio', 'crop_right_ratio', 'right')),
    topRatio: normalizeManualCropEdgeRatio(manualCropValue(rawCrop, 'topRatio', 'crop_top_ratio', 'top')),
    bottomRatio: normalizeManualCropEdgeRatio(manualCropValue(rawCrop, 'bottomRatio', 'crop_bottom_ratio', 'bottom')),
  };
}

function manualCropIsActive(crop){
  const normalized = normalizeManualCrop(crop);
  return Object.values(normalized).some(value => Math.abs(value) > 0.0001);
}

function manualCropEquals(a, b){
  const left = normalizeManualCrop(a);
  const right = normalizeManualCrop(b);
  return ['leftRatio', 'rightRatio', 'topRatio', 'bottomRatio']
    .every(key => Math.abs(left[key] - right[key]) < 0.0001);
}

function manualCropPercent(value){
  const normalized = normalizeManualCropEdgeRatio(value);
  if (normalized < -0.0001) return `+${Math.round(Math.abs(normalized) * 100)}%`;
  if (normalized > 0.0001) return `-${Math.round(normalized * 100)}%`;
  return '0%';
}

function finiteNumber(value, fallback = 0){
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function expandBoxWithinPage(rawBox, page, ratio = ADJACENT_RETRY_PADDING_RATIO){
  if (!rawBox || !page) return rawBox || null;
  const pageWidth = Math.max(1, finiteNumber(page.width, 1));
  const pageHeight = Math.max(1, finiteNumber(page.height, 1));
  const left = finiteNumber(rawBox.left, 0);
  const top = finiteNumber(rawBox.top, 0);
  const width = Math.max(1, finiteNumber(rawBox.width, 1));
  const height = Math.max(1, finiteNumber(rawBox.height, 1));
  const padX = Math.max(ADJACENT_RETRY_MIN_PADDING_PX, width * ratio);
  const padY = Math.max(ADJACENT_RETRY_MIN_PADDING_PX, height * ratio);
  const nextLeft = Math.max(0, left - padX);
  const nextTop = Math.max(0, top - padY);
  const nextRight = Math.min(pageWidth, left + width + padX);
  const nextBottom = Math.min(pageHeight, top + height + padY);
  return {
    left: nextLeft,
    top: nextTop,
    width: Math.max(1, nextRight - nextLeft),
    height: Math.max(1, nextBottom - nextTop),
  };
}

function manualSplitTitle(index){
  return `문항 ${String(index + 1).padStart(2, '0')}`;
}

function renumberManualSplitRegions(regions){
  return (regions || []).map((region, index) => ({
    ...region,
    order: index + 1,
    title: manualSplitTitle(index),
  }));
}

function sortManualSplitRegions(regions, page){
  const pageHeight = Math.max(1, finiteNumber(page?.height, 1));
  const rowTolerance = pageHeight * MANUAL_SPLIT_ROW_TOLERANCE_RATIO;
  return renumberManualSplitRegions([...(regions || [])].sort((a, b) => {
    const aBox = a?.bbox || {};
    const bBox = b?.bbox || {};
    const topDelta = finiteNumber(aBox.top, 0) - finiteNumber(bBox.top, 0);
    if (Math.abs(topDelta) > rowTolerance) return topDelta;
    return finiteNumber(aBox.left, 0) - finiteNumber(bBox.left, 0);
  }));
}

function serializeManualSplitRegions(regions){
  return renumberManualSplitRegions(regions).map((region, index) => {
    const bbox = region?.bbox || {};
    return {
      bbox: {
        left: Math.round(finiteNumber(bbox.left, 0)),
        top: Math.round(finiteNumber(bbox.top, 0)),
        width: Math.round(Math.max(1, finiteNumber(bbox.width, 1))),
        height: Math.round(Math.max(1, finiteNumber(bbox.height, 1))),
      },
      title: region.title || manualSplitTitle(index),
    };
  });
}

function manualSplitBoxesOverlap(a, b){
  const aBox = a?.bbox || {};
  const bBox = b?.bbox || {};
  const aLeft = finiteNumber(aBox.left, 0);
  const aTop = finiteNumber(aBox.top, 0);
  const aRight = aLeft + Math.max(0, finiteNumber(aBox.width, 0));
  const aBottom = aTop + Math.max(0, finiteNumber(aBox.height, 0));
  const bLeft = finiteNumber(bBox.left, 0);
  const bTop = finiteNumber(bBox.top, 0);
  const bRight = bLeft + Math.max(0, finiteNumber(bBox.width, 0));
  const bBottom = bTop + Math.max(0, finiteNumber(bBox.height, 0));
  return aLeft < bRight && aRight > bLeft && aTop < bBottom && aBottom > bTop;
}

function manualSplitHasOverlaps(regions){
  const list = regions || [];
  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      if (manualSplitBoxesOverlap(list[i], list[j])) return true;
    }
  }
  return false;
}

function replacementSourceIdFor(problem){
  const retry = problem?.aiRetry || problem?.ai_retry || {};
  return String(
    retry.replacesProblemId
    || retry.replaces_problem_id
    || problem?.replacesProblemId
    || problem?.replaces_problem_id
    || ''
  ).trim();
}

function isEditableKeyboardTarget(target){
  const tagName = String(target?.tagName || '').toLowerCase();
  return ['input', 'textarea', 'select'].includes(tagName) || Boolean(target?.isContentEditable);
}

function resetItemPlacement(item){
  return {
    ...item,
    placementXRatio: DEFAULT_PLACEMENT_X_RATIO,
    placementYRatio: DEFAULT_PLACEMENT_Y_RATIO,
    placementScaleRatio: DEFAULT_PLACEMENT_SCALE_RATIO,
    placementXEdited: false,
  };
}

function snapUpPages(value, slotHeight = DEFAULT_SLOT_HEIGHT_PAGES){
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.ceil((value - 0.001) / slotHeight) * slotHeight;
}

function itemHeightPages(item){
  return Math.max(0.12, item?.heightFrac || item?.actualHeightPages || item?.actual_height_pages || 0.8);
}

function itemRenderedHeightPages(item, scaleRatio = item?.placementScaleRatio){
  const heightPages = itemHeightPages(item);
  const rawScale = Number.isFinite(Number(scaleRatio)) ? Number(scaleRatio) : DEFAULT_PLACEMENT_SCALE_RATIO;
  const scale = Math.max(PLACEMENT_SCALE_MIN, Math.min(PLACEMENT_SCALE_MAX, rawScale));
  return heightPages * scale;
}

function placementSlotHeightPages(item){
  if (!item) return 0;
  const heightPages = itemHeightPages(item);
  const renderedHeightPages = itemRenderedHeightPages(item);
  const startPages = Number.isFinite(item.startYPages) ? Math.max(0, item.startYPages) : 0;
  const snappedNext = Number.isFinite(item.snappedNextStartYPages)
    ? Math.max(startPages + renderedHeightPages, item.snappedNextStartYPages)
    : snapUpPages(startPages + renderedHeightPages);
  return Math.max(heightPages, renderedHeightPages, snappedNext - startPages);
}

function maxPlacementScaleRatio(item){
  if (!item) return PLACEMENT_SCALE_MAX;
  const heightPages = itemHeightPages(item);
  const slotHeightPages = placementSlotHeightPages(item);
  return Math.max(PLACEMENT_SCALE_MIN, Math.min(PLACEMENT_SCALE_MAX, slotHeightPages / heightPages));
}

function verticalPlacementRoomPages(item, scaleRatio = item?.placementScaleRatio){
  if (!item) return 0;
  const heightPages = itemHeightPages(item);
  const scale = normalizePlacementScaleRatio(scaleRatio, maxPlacementScaleRatio(item));
  return Math.max(0, placementSlotHeightPages(item) - (heightPages * scale));
}

function isContinuousPlacementItem(item){
  if (!item) return false;
  const intent = item.inputIntent ? normalizeInputIntent(item.inputIntent) : null;
  if (intent) return intent === 'page-as-is';
  return item.placementMode === 'continuous-page-as-is';
}

function itemSlotSpanPages(item, slotHeight = DEFAULT_SLOT_HEIGHT_PAGES){
  if (!item) return slotHeight;
  const heightPages = itemHeightPages(item);
  const renderedHeightPages = itemRenderedHeightPages(item);
  const startPages = Number.isFinite(item.startYPages) ? Math.max(0, item.startYPages) : null;
  const snappedNext = Number.isFinite(item.snappedNextStartYPages) ? Math.max(0, item.snappedNextStartYPages) : null;
  const savedSpan = startPages !== null && snappedNext !== null && snappedNext > startPages
    ? snappedNext - startPages
    : 0;
  if (isContinuousPlacementItem(item)) {
    return Math.max(heightPages, renderedHeightPages, savedSpan);
  }
  return Math.max(heightPages, renderedHeightPages, savedSpan || snapUpPages(renderedHeightPages, slotHeight));
}

function reflowItemsForBoardOrder(items, slotHeight = DEFAULT_SLOT_HEIGHT_PAGES, boardColumns = BOARD_COLUMN_MIN){
  if (!Array.isArray(items)) return items;
  const columnCount = normalizeBoardColumns(boardColumns);
  let cursorPages = 0;
  const reflowed = [];
  let index = 0;

  while (index < items.length) {
    const first = items[index];
    const firstContinuous = isContinuousPlacementItem(first);
    const rowItems = [];
    if (firstContinuous) {
      rowItems.push(first);
      index += 1;
    } else {
      while (index < items.length && rowItems.length < columnCount) {
        const candidate = items[index];
        if (rowItems.length > 0 && isContinuousPlacementItem(candidate)) break;
        rowItems.push(candidate);
        index += 1;
      }
    }

    const rowContinuous = rowItems.length === 1 && isContinuousPlacementItem(rowItems[0]);
    const rowStartPages = rowContinuous ? cursorPages : snapUpPages(cursorPages, slotHeight);
    const rowMetrics = rowItems.map(item => {
      const heightPages = itemHeightPages(item);
      const renderedHeightPages = itemRenderedHeightPages(item);
      const slotSpanPages = itemSlotSpanPages(item, slotHeight);
      return { heightPages, renderedHeightPages, slotSpanPages };
    });
    const rowFlowSpanPages = Math.max(
      0,
      ...rowMetrics.map(metric => Math.max(metric.renderedHeightPages, metric.slotSpanPages))
    );
    const flowEndPages = rowStartPages + rowFlowSpanPages;
    const snappedNextStartYPages = rowContinuous ? flowEndPages : snapUpPages(flowEndPages, slotHeight);
    const boardRowHeightPages = Math.max(0, snappedNextStartYPages - rowStartPages);
    const rowColumnCount = rowContinuous ? BOARD_COLUMN_MIN : columnCount;

    rowItems.forEach((item, rowColumnIndex) => {
      const metric = rowMetrics[rowColumnIndex];
      const actualBottomPages = rowStartPages + metric.heightPages;
      const renderedBottomPages = rowStartPages + metric.renderedHeightPages;
      const slotSpanCount = Math.max(1, Math.ceil((snappedNextStartYPages - rowStartPages - 0.001) / slotHeight));
      const autoXRatio = boardColumnXRatio(rowColumnIndex, rowColumnCount);
      const xEdited = Boolean(item.placementXEdited || item.placement_x_edited);
      const rawMagnetColumnIndex = Number(item.placementMagnetColumnIndex ?? item.placement_magnet_column_index);
      const magnetColumnIndex = Number.isFinite(rawMagnetColumnIndex)
        ? Math.max(0, Math.min(rowColumnCount - 1, Math.round(rawMagnetColumnIndex)))
        : null;
      const resolvedXRatio = xEdited ? normalizePlacementXRatio(item.placementXRatio) : autoXRatio;
      reflowed.push({
        ...item,
        heightFrac: metric.heightPages,
        startYPages: Number(rowStartPages.toFixed(6)),
        snappedNextStartYPages: Number(snappedNextStartYPages.toFixed(6)),
        actualBottomYPages: Number(actualBottomPages.toFixed(6)),
        renderedBottomYPages: Number(renderedBottomPages.toFixed(6)),
        slotSpanCount,
        overflowAmountPages: Math.max(0, metric.renderedHeightPages - slotHeight),
        boardColumns: rowColumnCount,
        boardColumnCount: rowColumnCount,
        boardColumnIndex: rowColumnIndex,
        boardColumnXRatio: autoXRatio,
        boardRowHeightPages: Number(boardRowHeightPages.toFixed(6)),
        placementXRatio: resolvedXRatio,
        placementXEdited: xEdited,
        placementMagnetColumnIndex: xEdited ? magnetColumnIndex : null,
      });
    });

    cursorPages = snappedNextStartYPages;
  }

  return reflowed;
}

const INITIAL_ITEMS = Array.from({ length: 12 }).map((_, i) => {
  const kind = SAMPLE_KINDS[i % SAMPLE_KINDS.length];
  return {
    id: 'i' + (i + 1),
    name: SAMPLE_NAMES[i % SAMPLE_NAMES.length],
    source: SAMPLE_SOURCES[i % SAMPLE_SOURCES.length],
    type: i % 4 === 2 ? 'pdf' : 'image',
    kind,
    step: SAMPLE_STEPS[i % SAMPLE_STEPS.length],
    heightFrac: heightForKind(kind),
    placementXRatio: DEFAULT_PLACEMENT_X_RATIO,
    placementYRatio: DEFAULT_PLACEMENT_Y_RATIO,
    placementScaleRatio: DEFAULT_PLACEMENT_SCALE_RATIO,
  };
});

const freshInitialItems = () => INITIAL_ITEMS.map(item => ({ ...item }));

// ─── icons ────────────────────────────────────────────────────────────────
// smooth scroll helper (rAF easing — works around iframe smooth-scroll quirks)
function smoothScrollTo(el, top, duration = 380){
  if (!el) return;
  const start = el.scrollTop;
  const delta = top - start;
  if (Math.abs(delta) < 4){ el.scrollTop = top; return; }
  if (el.__scrollRaf) cancelAnimationFrame(el.__scrollRaf);
  const t0 = performance.now();
  const ease = u => 1 - Math.pow(1 - u, 3);
  const step = (now) => {
    const u = Math.min(1, (now - t0) / duration);
    el.scrollTop = start + delta * ease(u);
    if (u < 1) el.__scrollRaf = requestAnimationFrame(step);
    else el.__scrollRaf = null;
  };
  el.__scrollRaf = requestAnimationFrame(step);
}

const Icon = {
  upload: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>,
  trash:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16M10 11v6M14 11v6M5 7l1 13a2 2 0 002 2h8a2 2 0 002-2l1-13M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg>,
  crop:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 2v15h15M2 7h15v15"/></svg>,
  rotate: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 0114.5-7.2M21 4v5h-5"/></svg>,
  wand:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M15 4l1.5 3L20 8.5 16.5 10 15 13l-1.5-3L10 8.5 13.5 7 15 4zM5 19l8-8M5 19l1.5-1.5"/></svg>,
  aiBatch:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="4" width="7" height="5.5" rx="1.2"/><rect x="3.5" y="14.5" width="7" height="5.5" rx="1.2"/><path d="M13 12h5.7M16.4 8.8L19.6 12l-3.2 3.2"/><path d="M15.2 4.2l.7 1.5 1.6.7-1.6.7-.7 1.5-.7-1.5-1.6-.7 1.6-.7.7-1.5z" fill="currentColor" stroke="none"/><text x="14.2" y="20.3" fill="currentColor" stroke="none" fontSize="5.2" fontWeight="800" fontFamily="JetBrains Mono, monospace">AI</text></svg>,
  pagePng:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M7 3.5h7l4 4V19a1.8 1.8 0 0 1-1.8 1.8H7A1.8 1.8 0 0 1 5.2 19V5.3A1.8 1.8 0 0 1 7 3.5z"/><path d="M14 3.7v4h4"/><path d="M8.5 12.4h7M8.5 15.3h5.5"/><path d="M3.6 7.3v12.2a2.8 2.8 0 0 0 2.8 2.8h8.2"/></svg>,
  stamp: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 4h6l1 5a3 3 0 0 1-3 3h-2a3 3 0 0 1-3-3l1-5z"/><path d="M8 14h8l1 5H7l1-5z"/><path d="M5 21h14"/><path d="M12 4v8"/></svg>,
  copy:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 16H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>,
  check:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 13l4 4L19 7"/></svg>,
  board:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="13" rx="1"/><path d="M8 21h8M12 17v4"/></svg>,
  download:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>,
  folder: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 6.5h6l2 2h9v9.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M3.5 8.5V6a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v.5"/></svg>,
  zoomIn: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 11h6M11 8v6"/></svg>,
  zoomOut:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 11h6"/></svg>,
  undo:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3"/></svg>,
  refresh:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3.5-7.1M21 4v5h-5"/></svg>,
  reset:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/></svg>,
  power:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v9"/><path d="M6.3 7.5a8 8 0 1 0 11.4 0"/></svg>,
  more:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h.01M12 12h.01M19 12h.01"/></svg>,
  close:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>,
  split:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M5 12h14M9 8h6M9 16h6"/></svg>,
  pen:    <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M16 3l5 5L8 21H3v-5L16 3z"/></svg>,
  align:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 6h10M4 12h16M4 18h7"/></svg>,
  scan:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M7 9h10M7 13h7M7 17h5"/></svg>,
  fileText:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 3.5h7l4 4V19a1.8 1.8 0 0 1-1.8 1.8H7A1.8 1.8 0 0 1 5.2 19V5.3A1.8 1.8 0 0 1 7 3.5z"/><path d="M14 3.7v4h4M8.5 12h7M8.5 15.2h5.5"/></svg>,
  rows3:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="3.8" width="14" height="4.4" rx="1.2"/><rect x="5" y="9.8" width="14" height="4.4" rx="1.2"/><rect x="5" y="15.8" width="14" height="4.4" rx="1.2"/></svg>,
  stretchHorizontal:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12h16M8 8l-4 4 4 4M16 8l4 4-4 4M10 5h4M10 19h4"/></svg>,
  arrowUp:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>,
  arrowDown:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>,
  arrowLeft:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>,
  arrowRight:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>,
};

const PROCESSING_STEPS = new Set(['raw', 's1', 's2', 's3']);
function normalizeProcessingStep(step){
  const normalized = String(step || 'raw').trim().toLowerCase();
  return PROCESSING_STEPS.has(normalized) ? normalized : 'raw';
}
const stepLabel = s => {
  const step = normalizeProcessingStep(s);
  if (step === 's1') return '1단계';
  if (step === 's2') return '2단계 · AI';
  if (step === 's3') return '3단계 · 재구성';
  return '대기';
};

function manualSplitBoxStyle(box, page){
  const pageWidth = Math.max(1, finiteNumber(page?.width, 1));
  const pageHeight = Math.max(1, finiteNumber(page?.height, 1));
  return {
    left: `${(finiteNumber(box?.left, 0) / pageWidth) * 100}%`,
    top: `${(finiteNumber(box?.top, 0) / pageHeight) * 100}%`,
    width: `${(Math.max(1, finiteNumber(box?.width, 1)) / pageWidth) * 100}%`,
    height: `${(Math.max(1, finiteNumber(box?.height, 1)) / pageHeight) * 100}%`,
  };
}

function manualSplitBoxSizeLabel(box){
  return `${Math.round(finiteNumber(box?.width, 0))}×${Math.round(finiteNumber(box?.height, 0))}`;
}

function clampManualSplitStampDimension(value, maxValue){
  const max = Math.max(1, finiteNumber(maxValue, 1));
  const min = Math.min(24, max);
  const n = Number(value);
  return Math.round(Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : min);
}

function clampManualSplitStampBox(box, page){
  return {
    width: clampManualSplitStampDimension(box?.width, page?.width),
    height: clampManualSplitStampDimension(box?.height, page?.height),
  };
}

function clampReviewZoom(value){
  const next = Number.isFinite(value) ? value : 1;
  return Math.max(REVIEW_ZOOM_MIN, Math.min(REVIEW_ZOOM_MAX, next));
}

function reviewZoomPercent(value){
  return Math.round(clampReviewZoom(value) * 100);
}

function ReviewCanvasZoomShell({ children }){
  return (
    <div className="review-canvas-scroll">
      <div className="review-canvas-zoom-shell">
        {children}
      </div>
    </div>
  );
}

function ManualSplitEditor({
  page,
  regions,
  draftBox,
  mode,
  stampBox,
  selectedRegionIds,
  hasOverlap,
  mutating,
  onCanvasMouseDown,
  onCanvasMouseMove,
  onCanvasMouseLeave,
  onModeChange,
  onSaveStampFromSelection,
  onRegionMouseDown,
  onHandleMouseDown,
  onListSelect,
  onDeleteRegion,
  onDuplicateRegion,
  onMoveRegion,
  onReorderRegion,
  onApply,
  onStampSizeChange,
}){
  const dragRegionIdRef = useRef(null);
  const selectedCount = selectedRegionIds?.size || 0;
  const regionList = regions || [];
  const activeMode = mode === 'stamp' ? 'stamp' : 'draw';
  const selectedRegion = regionList.find(region => selectedRegionIds?.has(region.id)) || null;
  const focusShadeRegionId = draftBox ? null : selectedRegion?.id;
  const canSaveStamp = Boolean(selectedRegion?.bbox?.width && selectedRegion?.bbox?.height);
  const stampWidth = clampManualSplitStampDimension(stampBox?.width, page?.width);
  const stampHeight = clampManualSplitStampDimension(stampBox?.height, page?.height);
  const pageWidth = Math.round(Math.max(1, finiteNumber(page?.width, 1)));
  const pageHeight = Math.round(Math.max(1, finiteNumber(page?.height, 1)));
  const handleDrop = (evt, targetId) => {
    evt.preventDefault();
    const sourceId = evt.dataTransfer?.getData('text/plain') || dragRegionIdRef.current;
    dragRegionIdRef.current = null;
    if (!sourceId || sourceId === targetId) return;
    const position = dropPositionFromClientY(evt.currentTarget.getBoundingClientRect(), evt.clientY);
    onReorderRegion?.(sourceId, targetId, position);
  };

  return (
    <div className="manual-split-layout">
      <div className="manual-split-canvas-shell">
        <ReviewCanvasZoomShell>
          <div
            className={`review-page-canvas manual-split-canvas ${activeMode === 'stamp' ? 'stamp-mode' : 'draw-mode'}`}
            onMouseDown={(evt) => onCanvasMouseDown?.(evt, page)}
            onMouseMove={(evt) => onCanvasMouseMove?.(evt, page)}
            onMouseLeave={(evt) => onCanvasMouseLeave?.(evt, page)}
          >
            {page.sourceImageUri ? (
              <img src={page.sourceImageUri} alt={page.id} draggable={false} />
            ) : (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
                페이지 이미지를 불러올 수 없어요.
              </div>
            )}
            {regionList.map(region => {
              const isSelected = selectedRegionIds?.has(region.id);
              return (
                <div
                  key={region.id}
                  className={[
                    'manual-split-box',
                    isSelected ? 'selected' : '',
                    focusShadeRegionId === region.id ? 'focus-shade' : '',
                  ].filter(Boolean).join(' ')}
                  style={manualSplitBoxStyle(region.bbox, page)}
                  onMouseDown={(evt) => onRegionMouseDown?.(region.id, evt)}
                  title={`${region.title} · ${Math.round(region.bbox.width)}×${Math.round(region.bbox.height)}`}
                >
                  <div className="review-bbox-label manual-split-badge">
                    {String(region.order || 0).padStart(2, '0')}
                  </div>
                  {isSelected && (
                    <>
                      <div className="crop-frame-label">영역</div>
                      {['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].map(mode => (
                        <button
                          key={mode}
                          type="button"
                          className={`crop-frame-handle ${mode}`}
                          aria-label={`수동 분할 영역 ${mode}`}
                          onMouseDown={(evt) => onHandleMouseDown?.(region.id, mode, evt)}
                          onClick={(evt) => evt.stopPropagation()}
                        />
                      ))}
                    </>
                  )}
                </div>
              );
            })}
            {draftBox && (
              <div
                className={`manual-split-box draft focus-shade ${activeMode === 'stamp' ? 'stamp-preview' : ''}`}
                style={manualSplitBoxStyle(draftBox, page)}
              >
                <div className="review-bbox-label manual-split-badge">
                  {activeMode === 'stamp' ? '스탬프' : '새 영역'}
                </div>
              </div>
            )}
          </div>
        </ReviewCanvasZoomShell>
      </div>
      <aside className="manual-split-panel" aria-label="수동 분할 영역 목록">
        <div className="manual-split-panel-head">
          <strong>영역 {regionList.length}개</strong>
          {hasOverlap && <span className="manual-split-warning">겹침 있음</span>}
        </div>
        <div className="manual-split-toolset" aria-label="영역 생성 방식">
          <button
            type="button"
            className={`manual-split-tool ${activeMode === 'draw' ? 'on' : ''}`}
            title="드래그해서 자유 영역 만들기"
            aria-pressed={activeMode === 'draw'}
            onClick={() => onModeChange?.('draw')}
          >
            {Icon.crop}
            <span>드래그</span>
          </button>
          <button
            type="button"
            className={`manual-split-tool ${activeMode === 'stamp' ? 'on' : ''}`}
            title="같은 크기 틀을 클릭으로 찍기 · Esc로 스탬프 종료"
            aria-pressed={activeMode === 'stamp'}
            aria-keyshortcuts="Escape"
            onClick={() => onModeChange?.('stamp')}
          >
            {Icon.stamp}
            <span>스탬프</span>
          </button>
        </div>
        <div className="manual-stamp-card">
          <div className="manual-stamp-card-head">
            <span className="manual-stamp-size">
              {Icon.stamp}
              <strong>{manualSplitBoxSizeLabel({ width: stampWidth, height: stampHeight })}</strong>
            </span>
            <span className="manual-stamp-scale-actions" aria-label="스탬프 크기 빠른 조절">
              <button
                type="button"
                className="icon-btn"
                title="스탬프 10% 축소"
                disabled={mutating}
                onClick={() => onStampSizeChange?.({
                  width: Math.round(stampWidth * 0.9),
                  height: Math.round(stampHeight * 0.9),
                })}
              >
                {Icon.zoomOut}
              </button>
              <button
                type="button"
                className="icon-btn"
                title="스탬프 10% 확대"
                disabled={mutating}
                onClick={() => onStampSizeChange?.({
                  width: Math.round(stampWidth * 1.1),
                  height: Math.round(stampHeight * 1.1),
                })}
              >
                {Icon.zoomIn}
              </button>
            </span>
          </div>
          <div className="manual-stamp-fields" aria-label="스탬프 크기 조절">
            <label className="manual-stamp-field">
              <span>가로</span>
              <input
                type="number"
                min="24"
                max={pageWidth}
                step="10"
                value={stampWidth}
                disabled={mutating}
                onChange={(evt) => onStampSizeChange?.({ width: evt.target.value })}
              />
            </label>
            <label className="manual-stamp-field">
              <span>세로</span>
              <input
                type="number"
                min="24"
                max={pageHeight}
                step="10"
                value={stampHeight}
                disabled={mutating}
                onChange={(evt) => onStampSizeChange?.({ height: evt.target.value })}
              />
            </label>
          </div>
          <button
            type="button"
            className="btn"
            title="선택 영역 크기를 다음 스탬프로 저장"
            disabled={mutating || !canSaveStamp}
            onClick={() => onSaveStampFromSelection?.(selectedRegion?.id)}
          >
            {Icon.check}
            이 크기로 계속
          </button>
        </div>
        {regionList.length === 0 ? (
          <div className="manual-split-empty">
            {activeMode === 'stamp'
              ? '원본 위를 클릭해 같은 크기 영역을 찍으세요.'
              : '원본 위에서 드래그해 자를 영역을 만드세요.'}
          </div>
        ) : (
          <div className="manual-split-list">
            {regionList.map((region, index) => {
              const isSelected = selectedRegionIds?.has(region.id);
              const box = region.bbox || {};
              return (
                <div
                  key={region.id}
                  className={`manual-split-row ${isSelected ? 'selected' : ''}`}
                  draggable={!mutating}
                  onClick={(evt) => onListSelect?.(region.id, evt)}
                  onDragStart={(evt) => {
                    dragRegionIdRef.current = region.id;
                    evt.dataTransfer.effectAllowed = 'move';
                    evt.dataTransfer.setData('text/plain', region.id);
                  }}
                  onDragOver={(evt) => {
                    evt.preventDefault();
                    evt.dataTransfer.dropEffect = 'move';
                  }}
                  onDrop={(evt) => handleDrop(evt, region.id)}
                  onDragEnd={() => { dragRegionIdRef.current = null; }}
                >
                  <span className="manual-split-row-order">{String(region.order || index + 1).padStart(2, '0')}</span>
                  <button
                    type="button"
                    className="manual-split-row-main"
                    onClick={(evt) => {
                      evt.stopPropagation();
                      onListSelect?.(region.id, evt);
                    }}
                  >
                    <strong>{region.title}</strong>
                    <small>
                      {Math.round(finiteNumber(box.left, 0))}, {Math.round(finiteNumber(box.top, 0))}
                      {' · '}
                      {Math.round(finiteNumber(box.width, 0))}×{Math.round(finiteNumber(box.height, 0))}
                    </small>
                  </button>
                  <div className="manual-split-row-actions">
                    <button
                      type="button"
                      className="icon-btn"
                      title="위로"
                      disabled={mutating || index === 0}
                      onClick={(evt) => {
                        evt.stopPropagation();
                        onMoveRegion?.(region.id, -1);
                      }}
                    >{Icon.arrowUp}</button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="아래로"
                      disabled={mutating || index === regionList.length - 1}
                      onClick={(evt) => {
                        evt.stopPropagation();
                        onMoveRegion?.(region.id, 1);
                      }}
                    >{Icon.arrowDown}</button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="복제"
                      disabled={mutating}
                      onClick={(evt) => {
                        evt.stopPropagation();
                        onDuplicateRegion?.(region.id);
                      }}
                    >{Icon.copy}</button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="삭제"
                      disabled={mutating}
                      onClick={(evt) => {
                        evt.stopPropagation();
                        onDeleteRegion?.(region.id);
                      }}
                    >{Icon.trash}</button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {selectedCount > 1 && (
          <div className="manual-split-note">{selectedCount}개 선택됨</div>
        )}
        <div className="manual-split-panel-actions">
          <button
            type="button"
            className="btn primary"
            title="Enter로 분할 적용"
            aria-keyshortcuts="Enter"
            disabled={mutating || regionList.length === 0}
            onClick={() => onApply?.()}
          >
            {Icon.check}
            분할 적용 {regionList.length}
          </button>
        </div>
      </aside>
    </div>
  );
}

// ─── TOP BAR ──────────────────────────────────────────────────────────────
function BrandMark(){
  return (
    <span className="logo" aria-hidden="true">
      <svg viewBox="0 0 192 128" focusable="false">
        <defs>
          <linearGradient id="brandFlow" x1="24" y1="18" x2="168" y2="112" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#b8f76b" />
            <stop offset=".52" stopColor="#44dcc7" />
            <stop offset="1" stopColor="#78b8ff" />
          </linearGradient>
        </defs>
        <g fill="#fbfff8" opacity=".82">
          <rect x="26" y="35" width="20" height="58" rx="8" />
          <rect x="26" y="35" width="40" height="20" rx="8" />
          <rect x="26" y="55" width="34" height="18" rx="7" />
          <rect x="26" y="73" width="42" height="20" rx="8" />
        </g>
        <g fill="none" stroke="#fbfff8" strokeWidth="17" strokeLinecap="round" strokeLinejoin="round" opacity=".82">
          <path d="M76 43V85M76 43C111 43 111 85 76 85" />
          <path d="M122 43V85M122 43C153 43 153 64 122 64M122 64C158 64 158 85 122 85" />
        </g>
        <g fill="url(#brandFlow)">
          <rect x="31" y="40" width="10" height="48" rx="5" />
          <rect x="31" y="40" width="31" height="10" rx="5" />
          <rect x="31" y="59" width="25" height="10" rx="5" />
          <rect x="31" y="78" width="33" height="10" rx="5" />
        </g>
        <g fill="none" stroke="url(#brandFlow)" strokeWidth="10" strokeLinecap="round" strokeLinejoin="round">
          <path d="M76 43V85M76 43C111 43 111 85 76 85" />
          <path d="M122 43V85M122 43C153 43 153 64 122 64M122 64C158 64 158 85 122 85" />
        </g>
      </svg>
    </span>
  );
}

function TooltipLayer(){
  const [tooltip, setTooltip] = useState(null);
  const activeRef = useRef(null);

  const restoreNativeTitle = useCallback((element) => {
    if (!element?.dataset || element.dataset.nativeTooltipTitle == null) return;
    element.setAttribute('title', element.dataset.nativeTooltipTitle);
    delete element.dataset.nativeTooltipTitle;
  }, []);

  const hideTooltip = useCallback(() => {
    restoreNativeTitle(activeRef.current);
    activeRef.current = null;
    setTooltip(null);
  }, [restoreNativeTitle]);

  const showTooltipFor = useCallback((element) => {
    if (!element || typeof element.getBoundingClientRect !== 'function') return;
    const text = element.dataset?.tooltip
      || element.getAttribute?.('title')
      || element.dataset?.nativeTooltipTitle
      || '';
    if (!text.trim()) {
      hideTooltip();
      return;
    }
    if (activeRef.current && activeRef.current !== element) {
      restoreNativeTitle(activeRef.current);
    }
    if (element.hasAttribute?.('title') && element.dataset?.nativeTooltipTitle == null) {
      element.dataset.nativeTooltipTitle = element.getAttribute('title') || '';
      element.removeAttribute('title');
    }
    activeRef.current = element;

    const rect = element.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    let side = element.dataset?.tooltipSide || (element.closest?.('.topbar') ? 'bottom' : 'top');
    if (side === 'top' && rect.top < 42) side = 'bottom';
    if (side === 'bottom' && viewportHeight - rect.bottom < 54) side = 'top';
    const centerX = rect.left + rect.width / 2;
    const align = centerX < 150
      ? 'start'
      : viewportWidth - centerX < 150
        ? 'end'
        : 'center';
    const left = align === 'start' ? rect.left : align === 'end' ? rect.right : centerX;
    const top = side === 'bottom' ? rect.bottom + 8 : rect.top - 8;
    setTooltip({ text, left, top, side, align });
  }, [hideTooltip, restoreNativeTitle]);

  useEffect(() => {
    const tooltipTarget = target => (
      target?.closest?.('[data-tooltip], [title]')
    );
    const onPointerOver = (event) => {
      const target = tooltipTarget(event.target);
      if (target) showTooltipFor(target);
    };
    const onPointerMove = (event) => {
      const target = tooltipTarget(event.target);
      if (target && target !== activeRef.current) {
        showTooltipFor(target);
      } else if (!target && activeRef.current) {
        hideTooltip();
      }
    };
    const onPointerOut = (event) => {
      const next = event.relatedTarget;
      if (activeRef.current && next instanceof Node && activeRef.current.contains(next)) return;
      hideTooltip();
    };
    const onFocusIn = (event) => {
      const target = tooltipTarget(event.target);
      if (target) showTooltipFor(target);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') hideTooltip();
    };
    document.addEventListener('pointerover', onPointerOver, true);
    document.addEventListener('pointermove', onPointerMove, true);
    document.addEventListener('pointerout', onPointerOut, true);
    document.addEventListener('focusin', onFocusIn, true);
    document.addEventListener('focusout', hideTooltip, true);
    document.addEventListener('pointerdown', hideTooltip, true);
    document.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('scroll', hideTooltip, true);
    window.addEventListener('resize', hideTooltip);
    return () => {
      document.removeEventListener('pointerover', onPointerOver, true);
      document.removeEventListener('pointermove', onPointerMove, true);
      document.removeEventListener('pointerout', onPointerOut, true);
      document.removeEventListener('focusin', onFocusIn, true);
      document.removeEventListener('focusout', hideTooltip, true);
      document.removeEventListener('pointerdown', hideTooltip, true);
      document.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('scroll', hideTooltip, true);
      window.removeEventListener('resize', hideTooltip);
      hideTooltip();
    };
  }, [hideTooltip, showTooltipFor]);

  if (!tooltip) return null;
  return (
    <div
      className={`ui-tooltip ${tooltip.side} ${tooltip.align}`}
      role="tooltip"
      style={{ left: tooltip.left, top: tooltip.top }}
    >
      {tooltip.text}
    </div>
  );
}

function TopBar({ fileName, setFileName, progress, processed, total, onPublish, published, onReset, onRefresh, refreshing, canReset, view, setView, reviewAvailable, onUndo, canUndo, onShutdown, onExportImages, exportingImages, canExportImages }){
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef(null);
  useEffect(() => {
    if (!moreOpen) return;
    const close = (event) => {
      if (!moreRef.current || moreRef.current.contains(event.target)) return;
      setMoreOpen(false);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') setMoreOpen(false);
    };
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [moreOpen]);

  return (
    <div className="topbar">
      <div className="brand">
        <BrandMark />
        칠판 자료 편집기
      </div>
      <div className="crumb">
        <span>수업 ›</span>
        <input value={fileName} onChange={e => setFileName(e.target.value)} />
      </div>
      <div className="spacer" />
      <div className="view-toggle" title={reviewAvailable ? '' : '먼저 자료를 업로드하세요'}>
        <button
          className={view === 'board' ? 'on' : ''}
          data-tooltip="보드 배치 화면으로 이동"
          onClick={() => setView && setView('board')}
        >칠판</button>
        <button
          className={view === 'review' ? 'on' : ''}
          data-tooltip={reviewAvailable ? 'AI 인식 박스와 문제 분할을 검수' : '먼저 자료를 업로드하세요'}
          onClick={() => reviewAvailable && setView && setView('review')}
          disabled={!reviewAvailable}
          style={!reviewAvailable ? { cursor: 'not-allowed', opacity: .5 } : null}
        >검수</button>
      </div>
      <div className="progress" title={`${processed} / ${total} 처리됨`}>
        <div className="bar"><i style={{ width: `${Math.round(progress*100)}%` }} /></div>
        <span>{processed}/{total} 처리됨</span>
      </div>
      <div className="topbar-actions" aria-label="보조 작업">
        <button
          className="btn ghost icon"
          type="button"
          title={canReset ? '초기화' : '초기화할 내용이 없습니다'}
          data-tooltip={canReset ? '현재 세션, 대기열, 최근 작업 초기화' : '초기화할 내용이 없습니다'}
          aria-label="초기화"
          onClick={onReset}
          disabled={!canReset}
        >
          {Icon.reset}
        </button>
        <button
          className="btn ghost icon"
          title={canUndo ? '되돌리기 (Ctrl/Cmd+Z)' : '되돌릴 변경이 없습니다'}
          data-tooltip={canUndo ? '마지막 편집 되돌리기' : '되돌릴 변경이 없습니다'}
          aria-label="되돌리기"
          onClick={onUndo}
          disabled={!canUndo}
        >{Icon.undo}</button>
        <button
          className="btn ghost icon"
          onClick={onRefresh}
          disabled={refreshing}
          title={refreshing ? '세션 새로고침 중' : '저장된 최신 세션 다시 읽기'}
          data-tooltip={refreshing ? '저장된 최신 세션을 읽는 중' : '디스크에 저장된 최신 세션을 다시 읽기'}
          aria-label={refreshing ? '세션 새로고침 중' : '세션 새로고침'}
        >
          <span className={refreshing ? 'spin-ic' : ''} style={{display:'inline-flex'}}>{Icon.refresh}</span>
        </button>
        <button
          className="btn ghost icon"
          type="button"
          title={canExportImages ? '현재 선택 단계 기준 PNG ZIP' : '다운로드할 이미지가 없습니다'}
          data-tooltip={canExportImages ? '현재 선택한 처리 단계 기준으로 최종 PNG 묶음 다운로드' : '다운로드할 이미지가 없습니다'}
          aria-label={exportingImages ? '이미지 다운로드 준비 중' : '이미지 다운로드'}
          onClick={onExportImages}
          disabled={!canExportImages || exportingImages}
        >
          {Icon.download}
        </button>
        <div className="topbar-more" ref={moreRef}>
          <button
            className={`btn ghost icon ${moreOpen ? 'on' : ''}`}
            type="button"
            aria-haspopup="menu"
            aria-expanded={moreOpen}
            title="더보기"
            data-tooltip="종료 등 추가 작업"
            onClick={() => setMoreOpen(open => !open)}
          >
            {Icon.more}
          </button>
          {moreOpen && (
            <div className="topbar-more-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMoreOpen(false);
                  onShutdown?.();
                }}
              >
                {Icon.power}<span>종료</span>
              </button>
            </div>
          )}
        </div>
      </div>
      <button
        className={`btn primary ${published ? 'done' : ''}`}
        data-tooltip={published ? '최근 제작 완료 상태' : '현재 배치로 EDB 파일 제작'}
        onClick={onPublish}
      >
        {published ? <>{Icon.check} 제작 완료</> : <>{Icon.board} EDB 제작</>}
      </button>
    </div>
  );
}

// ─── REVIEW STAGE: detected-box overlay with split / merge / exclude ─────
function ReviewStage({ session, items, activeId, setActive, mutateSession, retryAiSession, mutating, aiAvailable, aiBusy, onConfirm, reviewFocus }){
  const pages = Array.isArray(session?.pages) ? session.pages : [];
  const problemsById = useMemo(() => {
    const map = new Map();
    (session?.problems || []).forEach(p => { if (p && p.id) map.set(p.id, p); });
    return map;
  }, [session]);
  // Problem display number reflects the current rail order (user reordering /
  // excluding is honoured here, so the chip on each bbox matches the rail).
  const orderMap = useMemo(() => {
    const map = new Map();
    items.forEach((it, idx) => map.set(it.id, idx + 1));
    return map;
  }, [items]);

  // Multi-select state: ids of bboxes currently selected. Clicking without
  // shift replaces selection; shift-click toggles.
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  // Split mode: when set to a problem id, that bbox shows a draggable
  // horizontal guideline. The ratio is in (0, 1).
  const [splitTarget, setSplitTarget] = useState(null);
  const [splitRatio, setSplitRatio] = useState(0.5);
  const [boxEdit, setBoxEdit] = useState(null);
  const [manualSplit, setManualSplit] = useState(null);
  const [manualSplitDraftBox, setManualSplitDraftBox] = useState(null);
  const [reviewFilter, setReviewFilter] = useState('all');
  const [reviewRiskFilter, setReviewRiskFilter] = useState(null);
  const [reviewScopeProblemIds, setReviewScopeProblemIds] = useState([]);
  const [reviewScopePageIds, setReviewScopePageIds] = useState([]);
  const [reviewZoom, setReviewZoom] = useState(1);
  const splitDraggingRef = useRef(false);
  const splitBoxRef = useRef(null);
  const boxEditDragRef = useRef(null);
  const boxEditCommitRef = useRef(false);
  const manualSplitDragRef = useRef(null);
  const manualSplitCommitRef = useRef(false);
  const manualSplitSeqRef = useRef(1);
  const manualSplitFocusRef = useRef('');
  const reviewWrapRef = useRef(null);

  // Cancel split mode if the session changes underneath (e.g. after a mutation).
  useEffect(() => {
    setSplitTarget(null);
    boxEditCommitRef.current = false;
    manualSplitCommitRef.current = false;
    setBoxEdit(null);
    setManualSplit(null);
    setManualSplitDraftBox(null);
    setSelectedIds(new Set());
    setReviewRiskFilter(null);
  }, [session]);
  useEffect(() => {
    if (reviewFocus === null) {
      setReviewScopeProblemIds([]);
      setReviewScopePageIds([]);
      return;
    }
    const nextFilter = String(reviewFocus?.filter || '').trim();
    const focusedProblemIds = Array.isArray(reviewFocus?.problemIds)
      ? reviewFocus.problemIds.map(id => String(id || '').trim()).filter(Boolean)
      : [];
    const scopedProblemIds = Array.isArray(reviewFocus?.scopeProblemIds)
      ? listUnique(reviewFocus.scopeProblemIds.map(id => String(id || '').trim()).filter(Boolean))
      : [];
    const scopedPageIds = Array.isArray(reviewFocus?.scopePageIds)
      ? listUnique(reviewFocus.scopePageIds.map(id => String(id || '').trim()).filter(Boolean))
      : [];
    if (!nextFilter && !focusedProblemIds.length && !scopedProblemIds.length && !scopedPageIds.length) return;
    if (scopedProblemIds.length || scopedPageIds.length) {
      setReviewScopeProblemIds(scopedProblemIds);
      setReviewScopePageIds(scopedPageIds);
    } else {
      setReviewScopeProblemIds([]);
      setReviewScopePageIds([]);
    }
    setReviewFilter(nextFilter || 'all');
    setReviewRiskFilter(null);
    setSelectedIds(new Set(focusedProblemIds));
    if (focusedProblemIds.length && setActive) setActive(focusedProblemIds[0]);
  }, [reviewFocus, setActive]);

  const reviewScopeProblemIdSet = useMemo(() => new Set(reviewScopeProblemIds), [reviewScopeProblemIds]);
  const reviewScopePageIdSet = useMemo(() => new Set(reviewScopePageIds), [reviewScopePageIds]);
  const reviewScopeActive = reviewScopeProblemIdSet.size > 0 || reviewScopePageIdSet.size > 0;
  const problemInReviewScope = useCallback((problem) => {
    if (!reviewScopeActive) return true;
    const problemId = String(problem?.id || problem?.problem_id || '').trim();
    const pageId = String(problem?.sourcePageId || problem?.source_page_id || '').trim();
    return Boolean(
      (problemId && reviewScopeProblemIdSet.has(problemId))
      || (pageId && reviewScopePageIdSet.has(pageId))
    );
  }, [reviewScopeActive, reviewScopeProblemIdSet, reviewScopePageIdSet]);
  const scopedProblems = useMemo(
    () => (session?.problems || []).filter(problemInReviewScope),
    [session, problemInReviewScope]
  );
  const clearReviewScope = useCallback(() => {
    setReviewScopeProblemIds([]);
    setReviewScopePageIds([]);
  }, []);

  useEffect(() => {
    setSelectedIds(prev => {
      const filtered = Array.from(prev).filter(id => problemsById.has(id));
      return filtered.length === prev.size ? prev : new Set(filtered);
    });
  }, [problemsById]);

  const onBoxClick = (probId, evt) => {
    if (manualSplit) return;
    if (splitTarget) return;  // ignore clicks while splitting
    if (boxEdit) return;  // keep the crop frame stable while editing
    if (evt.shiftKey) {
      setSelectedIds(prev => {
        const next = new Set(prev);
        if (next.has(probId)) next.delete(probId);
        else next.add(probId);
        return next;
      });
    } else {
      setSelectedIds(new Set([probId]));
    }
    if (setActive) setActive(probId);
  };

  const selectedList = Array.from(selectedIds).filter(id => problemsById.has(id));
  const selectedActionKey = selectedList.join('|');
  const selectedActionIds = useMemo(() => selectedList, [selectedActionKey]);
  const selectedProblems = selectedList.map(id => problemsById.get(id)).filter(Boolean);
  const selectedSingleProblem = selectedProblems.length === 1 ? selectedProblems[0] : null;
  const selectedSinglePage = selectedSingleProblem
    ? pages.find(page => page.id === selectedSingleProblem.sourcePageId)
    : null;
  const sameSourcePage = selectedProblems.length >= 2
    && selectedProblems.every(p => p.sourcePageId === selectedProblems[0].sourcePageId);
  const statusCounts = useMemo(() => {
    const helperCounts = globalThis.EDB_REVIEW_FILTERS?.countReviewFilters?.(scopedProblems);
    if (helperCounts) return helperCounts;
    const counts = { all: 0, normal: 0, check_needed: 0, failed: 0, passage: 0, passageGroups: 0 };
    const passageGroups = new Set();
    scopedProblems.forEach(problem => {
      const status = deriveProblemStatus(problem);
      counts.all += 1;
      counts[status] = (counts[status] || 0) + 1;
      const passageGroupId = passageGroupIdFor(problem);
      if (passageGroupId) {
        counts.passage += 1;
        passageGroups.add(passageGroupId);
      }
    });
    counts.supplemental = scopedProblems.filter(isSupplementalProblem).length;
    counts.passageGroups = passageGroups.size;
    return counts;
  }, [scopedProblems]);
  const sessionCounts = useMemo(
    () => reviewScopeActive ? countSessionProblems(scopedProblems) : sessionProblemCounts(session),
    [reviewScopeActive, scopedProblems, session]
  );
  const reviewSummary = useMemo(() => sessionReviewSummary(session), [session]);
  const passageReviewProblemIds = useMemo(
    () => new Set(reviewSummary.passageReviewProblemIds || []),
    [reviewSummary.passageReviewProblemIds]
  );
  const riskFilterHasProblemMatches = useMemo(() => {
    if (!reviewRiskFilter) return false;
    return scopedProblems.some(problem => hasRiskFlag(problem, reviewRiskFilter));
  }, [scopedProblems, reviewRiskFilter]);
  const pageRetryIds = useMemo(() => {
    const ids = [];
    const byId = problemsById;
    pages.forEach(page => {
      const pageId = String(page?.id || '').trim();
      const scopedPageProblems = (page.problemIds || [])
        .map(pid => byId.get(pid))
        .filter(Boolean)
        .filter(problemInReviewScope);
      const pageInScope = !reviewScopeActive || reviewScopePageIdSet.has(pageId) || scopedPageProblems.length > 0;
      if (!pageInScope) return;
      const pageFlags = riskFlagsFor(page);
      const pageStatus = normalizeReviewStatus(page.reviewStatus || page.review_status);
      const hasProblemRisk = scopedPageProblems
        .some(problem => deriveProblemStatus(problem) !== 'normal');
      if (pageStatus === 'failed' || (Array.isArray(pageFlags) && pageFlags.length) || !scopedPageProblems.length || hasProblemRisk) {
        ids.push(page.id);
      }
    });
    return listUnique(ids.filter(Boolean));
  }, [pages, problemsById, problemInReviewScope, reviewScopeActive, reviewScopePageIdSet]);
  const activeReviewFilter = reviewFilter !== 'all' || Boolean(reviewRiskFilter) || reviewScopeActive;
  const visibleReviewScope = useMemo(() => {
    const retryPageIdSet = new Set();
    const problemIdSet = new Set();
    let problemCount = 0;
    pages.forEach(page => {
      const allPageProblems = (page.problemIds || [])
        .map(pid => problemsById.get(pid))
        .filter(Boolean)
        .filter(problemInReviewScope);
      const pageId = String(page?.id || '').trim();
      const pageInScope = !reviewScopeActive || reviewScopePageIdSet.has(pageId) || allPageProblems.length > 0;
      if (!pageInScope) return;
      const pageMatchesRiskFilter = reviewRiskFilter && !riskFilterHasProblemMatches
        ? hasRiskFlag(page, reviewRiskFilter)
        : false;
      const pageProblems = allPageProblems
        .filter(problem => problemMatchesReviewFilter(problem, reviewFilter, { passageReviewProblemIds }))
        .filter(problem => !reviewRiskFilter || pageMatchesRiskFilter || hasRiskFlag(problem, reviewRiskFilter));
      problemCount += pageProblems.length;
      pageProblems.forEach(problem => {
        if (problem?.id) problemIdSet.add(problem.id);
      });
      if (pageProblems.length && pageRetryIds.includes(page.id)) {
        retryPageIdSet.add(page.id);
      }
    });
    return {
      problemCount,
      problemIds: Array.from(problemIdSet),
      retryPageIds: Array.from(retryPageIdSet),
    };
  }, [pages, problemsById, pageRetryIds, passageReviewProblemIds, reviewFilter, reviewRiskFilter, riskFilterHasProblemMatches, problemInReviewScope, reviewScopeActive, reviewScopePageIdSet]);
  const actionableProblemIds = useMemo(() => scopedProblems
    .filter(problem => problem?.id && deriveProblemStatus(problem) !== 'normal')
    .map(problem => problem.id), [scopedProblems]);
  const selectedRetryPageIds = listUnique(selectedProblems.map(problem => problem.sourcePageId).filter(Boolean));
  const selectedHasRetryable = selectedProblems.some(problem => deriveProblemStatus(problem) !== 'normal');
  const selectedCanBoxEdit = Boolean(
    selectedSingleProblem?.bbox?.width
    && selectedSingleProblem?.bbox?.height
    && selectedSinglePage?.width
    && selectedSinglePage?.height
    && selectedSinglePage?.sourceImageUri
  );
  const selectedSourcePage = selectedSinglePage || (sameSourcePage
    ? pages.find(page => page.id === selectedProblems[0]?.sourcePageId)
    : null);
  const selectedCanManualSplit = Boolean(
    selectedProblems.length
    && selectedSourcePage?.width
    && selectedSourcePage?.height
    && selectedSourcePage?.sourceImageUri
    && selectedProblems.every(problem => problem.sourcePageId === selectedSourcePage.id)
  );
  const manualSplitPage = manualSplit
    ? pages.find(page => page.id === manualSplit.pageId)
    : null;
  const manualSplitSelectedIds = useMemo(
    () => new Set(manualSplit?.selectedRegionIds || []),
    [manualSplit?.selectedRegionIds]
  );
  const manualSplitOverlaps = useMemo(
    () => manualSplitHasOverlaps(manualSplit?.regions || []),
    [manualSplit?.regions]
  );

  const clampReviewBox = useCallback((rawBox, page) => {
    const pageWidth = Math.max(1, Number(page?.width) || 1);
    const pageHeight = Math.max(1, Number(page?.height) || 1);
    const minWidth = Math.min(pageWidth, Math.max(12, Math.min(36, pageWidth * 0.02)));
    const minHeight = Math.min(pageHeight, Math.max(12, Math.min(36, pageHeight * 0.02)));
    let left = Number(rawBox?.left);
    let top = Number(rawBox?.top);
    let width = Number(rawBox?.width);
    let height = Number(rawBox?.height);
    if (!Number.isFinite(left)) left = 0;
    if (!Number.isFinite(top)) top = 0;
    if (!Number.isFinite(width) || width <= 0) width = minWidth;
    if (!Number.isFinite(height) || height <= 0) height = minHeight;
    left = Math.max(0, Math.min(pageWidth - minWidth, left));
    top = Math.max(0, Math.min(pageHeight - minHeight, top));
    width = Math.max(minWidth, Math.min(pageWidth - left, width));
    height = Math.max(minHeight, Math.min(pageHeight - top, height));
    return { left, top, width, height };
  }, []);

  const manualSplitPointFromClient = (clientX, clientY, page, rect) => {
    const pageWidth = Math.max(1, Number(page?.width) || 1);
    const pageHeight = Math.max(1, Number(page?.height) || 1);
    const x = ((clientX - rect.left) / Math.max(1, rect.width)) * pageWidth;
    const y = ((clientY - rect.top) / Math.max(1, rect.height)) * pageHeight;
    return {
      x: Math.max(0, Math.min(pageWidth, x)),
      y: Math.max(0, Math.min(pageHeight, y)),
    };
  };

  const manualSplitBoxFromPoints = (startPoint, endPoint, page) => clampReviewBox({
    left: Math.min(startPoint.x, endPoint.x),
    top: Math.min(startPoint.y, endPoint.y),
    width: Math.abs(endPoint.x - startPoint.x),
    height: Math.abs(endPoint.y - startPoint.y),
  }, page);

  const manualSplitDefaultStampBox = (page, replacementIds = []) => {
    const pageWidth = Math.max(1, Number(page?.width) || 1);
    const pageHeight = Math.max(1, Number(page?.height) || 1);
    const sourceProblem = (replacementIds || [])
      .map(id => problemsById.get(id))
      .find(problem => problem?.bbox?.width && problem?.bbox?.height);
    const sourceBox = sourceProblem?.bbox || null;
    const sourceArea = Math.max(0, finiteNumber(sourceBox?.width, 0)) * Math.max(0, finiteNumber(sourceBox?.height, 0));
    const pageArea = pageWidth * pageHeight;
    const sourceLooksLikeQuestion = sourceBox
      && sourceArea > 0
      && sourceArea <= pageArea * 0.55
      && finiteNumber(sourceBox.width, 0) <= pageWidth * 0.88
      && finiteNumber(sourceBox.height, 0) <= pageHeight * 0.70;
    const width = sourceLooksLikeQuestion ? sourceBox.width : pageWidth * 0.44;
    const height = sourceLooksLikeQuestion ? sourceBox.height : pageHeight * 0.24;
    const box = clampReviewBox({ left: 0, top: 0, width, height }, page);
    return { width: box.width, height: box.height };
  };

  const updateManualSplitStampSize = (patch) => {
    if (!manualSplitPage) return;
    setManualSplitDraftBox(null);
    setManualSplit(prev => {
      if (!prev) return prev;
      const current = prev.stampBox || manualSplitDefaultStampBox(manualSplitPage, prev.replaceProblemIds || []);
      return {
        ...prev,
        stampBox: clampManualSplitStampBox({
          width: Object.prototype.hasOwnProperty.call(patch || {}, 'width') ? patch.width : current.width,
          height: Object.prototype.hasOwnProperty.call(patch || {}, 'height') ? patch.height : current.height,
        }, manualSplitPage),
      };
    });
  };

  const manualSplitStampBoxFromPoint = (point, page, stampBox) => {
    const pageWidth = Math.max(1, Number(page?.width) || 1);
    const pageHeight = Math.max(1, Number(page?.height) || 1);
    const width = Math.min(pageWidth, Math.max(1, finiteNumber(stampBox?.width, pageWidth * 0.36)));
    const height = Math.min(pageHeight, Math.max(1, finiteNumber(stampBox?.height, pageHeight * 0.20)));
    return clampReviewBox({
      left: Math.max(0, Math.min(pageWidth - width, point.x - width / 2)),
      top: Math.max(0, Math.min(pageHeight - height, point.y - height / 2)),
      width,
      height,
    }, page);
  };

  const beginManualPageSplit = (page, replaceProblemIds = []) => {
    if (!page?.id || !page?.sourceImageUri || !page.width || !page.height) return;
    const replacementIds = listUnique((replaceProblemIds || []).filter(Boolean));
    const stampBox = manualSplitDefaultStampBox(page, replacementIds);
    manualSplitSeqRef.current = 1;
    manualSplitDragRef.current = null;
    manualSplitCommitRef.current = false;
    setManualSplitDraftBox(null);
    setSplitTarget(null);
    setBoxEdit(null);
    setSelectedIds(new Set(replacementIds));
    setManualSplit({
      pageId: page.id,
      replaceProblemIds: replacementIds,
      mode: 'draw',
      stampBox,
      regions: [],
      selectedRegionIds: [],
    });
  };

  const beginManualSplitForSelection = () => {
    if (!selectedCanManualSplit || !selectedSourcePage) return;
    beginManualPageSplit(selectedSourcePage, selectedList);
  };

  useEffect(() => {
    const pageId = String(reviewFocus?.manualSplitPageId || '').trim();
    if (!pageId) return;
    const focusKey = [
      pageId,
      String(reviewFocus?.source || ''),
      (reviewFocus?.scopeProblemIds || []).join(','),
      (reviewFocus?.scopePageIds || []).join(','),
    ].join('|');
    if (manualSplitFocusRef.current === focusKey) return;
    const page = pages.find(item => item.id === pageId);
    if (!page?.sourceImageUri || !page.width || !page.height) return;
    manualSplitFocusRef.current = focusKey;
    const scopedIds = new Set((reviewFocus?.scopeProblemIds || []).map(id => String(id || '').trim()).filter(Boolean));
    const replacementIds = (page.problemIds || []).filter(id => !scopedIds.size || scopedIds.has(String(id)));
    beginManualPageSplit(page, replacementIds);
  }, [reviewFocus, pages]);

  const centerReviewZoomScrollers = useCallback(() => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        reviewWrapRef.current?.querySelectorAll?.('.review-canvas-scroll').forEach(scroller => {
          const maxScrollLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
          if (maxScrollLeft > 1) scroller.scrollLeft = maxScrollLeft / 2;
        });
      });
    });
  }, []);

  const setReviewZoomValue = useCallback((value) => {
    setReviewZoom(clampReviewZoom(value));
    centerReviewZoomScrollers();
  }, [centerReviewZoomScrollers]);

  const adjustReviewZoom = useCallback((delta) => {
    setReviewZoom(prev => clampReviewZoom(prev + delta));
    centerReviewZoomScrollers();
  }, [centerReviewZoomScrollers]);

  const resetReviewZoom = useCallback(() => {
    setReviewZoomValue(1);
  }, [setReviewZoomValue]);

  const updateReviewZoomPercent = useCallback((event) => {
    setReviewZoomValue(Number(event.target.value) / 100);
  }, [setReviewZoomValue]);

  useEffect(() => {
    if (Math.abs(reviewZoom - 1) < 0.001) return;
    centerReviewZoomScrollers();
  }, [manualSplit?.pageId, reviewZoom, centerReviewZoomScrollers]);

  const handleReviewWheel = useCallback((evt) => {
    if (!evt.ctrlKey && !evt.metaKey) return;
    evt.preventDefault();
    adjustReviewZoom(evt.deltaY > 0 ? -REVIEW_ZOOM_STEP : REVIEW_ZOOM_STEP);
  }, [adjustReviewZoom]);

  const cancelManualPageSplit = () => {
    manualSplitDragRef.current = null;
    manualSplitCommitRef.current = false;
    setManualSplitDraftBox(null);
    setManualSplit(null);
  };

  const setManualSplitMode = (mode) => {
    const nextMode = mode === 'stamp' ? 'stamp' : 'draw';
    manualSplitDragRef.current = null;
    setManualSplitDraftBox(null);
    setManualSplit(prev => prev ? { ...prev, mode: nextMode } : prev);
  };

  const selectManualSplitRegion = (regionId, evt) => {
    if (!regionId) return;
    setManualSplit(prev => {
      if (!prev) return prev;
      const current = new Set(prev.selectedRegionIds || []);
      if (evt?.shiftKey) {
        if (current.has(regionId)) current.delete(regionId);
        else current.add(regionId);
      } else {
        current.clear();
        current.add(regionId);
      }
      return { ...prev, selectedRegionIds: Array.from(current) };
    });
  };

  const deleteManualSplitRegion = (regionId) => {
    if (!regionId) return;
    setManualSplit(prev => {
      if (!prev) return prev;
      return {
        ...prev,
        regions: renumberManualSplitRegions((prev.regions || []).filter(region => region.id !== regionId)),
        selectedRegionIds: (prev.selectedRegionIds || []).filter(id => id !== regionId),
      };
    });
  };

  const deleteManualSplitSelected = () => {
    setManualSplit(prev => {
      const selected = new Set(prev?.selectedRegionIds || []);
      if (!prev || !selected.size) return prev;
      return {
        ...prev,
        regions: renumberManualSplitRegions((prev.regions || []).filter(region => !selected.has(region.id))),
        selectedRegionIds: [],
      };
    });
  };

  const duplicateManualSplitRegion = (regionId) => {
    if (!manualSplitPage) return;
    setManualSplit(prev => {
      if (!prev) return prev;
      const index = (prev.regions || []).findIndex(region => region.id === regionId);
      if (index < 0) return prev;
      const original = prev.regions[index];
      const offset = Math.max(12, Math.min(36, Math.min(manualSplitPage.width, manualSplitPage.height) * 0.025));
      const copy = {
        ...original,
        id: `draft-region-${manualSplitSeqRef.current++}`,
        bbox: clampReviewBox({
          ...original.bbox,
          left: finiteNumber(original.bbox?.left, 0) + offset,
          top: finiteNumber(original.bbox?.top, 0) + offset,
        }, manualSplitPage),
      };
      const next = prev.regions.slice();
      next.splice(index + 1, 0, copy);
      return {
        ...prev,
        regions: renumberManualSplitRegions(next),
        selectedRegionIds: [copy.id],
      };
    });
  };

  const moveManualSplitRegionInList = (regionId, delta) => {
    setManualSplit(prev => {
      if (!prev) return prev;
      const next = prev.regions.slice();
      const index = next.findIndex(region => region.id === regionId);
      const targetIndex = index + delta;
      if (index < 0 || targetIndex < 0 || targetIndex >= next.length) return prev;
      const [moved] = next.splice(index, 1);
      next.splice(targetIndex, 0, moved);
      return {
        ...prev,
        regions: renumberManualSplitRegions(next),
        selectedRegionIds: [regionId],
      };
    });
  };

  const reorderManualSplitRegion = (fromId, toId, position = 'before') => {
    setManualSplit(prev => {
      if (!prev) return prev;
      const reordered = reorderItemsForDrop(prev.regions || [], fromId, toId, position);
      if (reordered === prev.regions) return prev;
      return {
        ...prev,
        regions: renumberManualSplitRegions(reordered),
        selectedRegionIds: [fromId],
      };
    });
  };

  const autoSortManualSplitRegions = () => {
    if (!manualSplitPage) return;
    setManualSplit(prev => prev ? {
      ...prev,
      regions: sortManualSplitRegions(prev.regions || [], manualSplitPage),
    } : prev);
  };

  const saveManualSplitStampFromSelection = (regionId) => {
    setManualSplit(prev => {
      if (!prev) return prev;
      const selected = new Set(prev.selectedRegionIds || []);
      const region = (prev.regions || []).find(item => (
        item.id === regionId || (!regionId && selected.has(item.id))
      ));
      if (!region?.bbox?.width || !region?.bbox?.height) return prev;
      return {
        ...prev,
        mode: 'stamp',
        stampBox: {
          width: finiteNumber(region.bbox.width, prev.stampBox?.width || 1),
          height: finiteNumber(region.bbox.height, prev.stampBox?.height || 1),
        },
      };
    });
  };

  const nudgeManualSplitSelected = (dx, dy) => {
    if (!manualSplitPage) return;
    setManualSplit(prev => {
      const selected = new Set(prev?.selectedRegionIds || []);
      if (!prev || !selected.size) return prev;
      return {
        ...prev,
        regions: (prev.regions || []).map(region => (
          selected.has(region.id)
            ? {
                ...region,
                bbox: clampReviewBox({
                  ...region.bbox,
                  left: finiteNumber(region.bbox?.left, 0) + dx,
                  top: finiteNumber(region.bbox?.top, 0) + dy,
                }, manualSplitPage),
              }
            : region
        )),
      };
    });
  };

  const applyManualPageSplit = useCallback(async () => {
    if (!manualSplit?.pageId || !(manualSplit.regions || []).length || manualSplitCommitRef.current) return;
    manualSplitCommitRef.current = true;
    try {
      const payload = {
        pageId: manualSplit.pageId,
        replaceProblemIds: manualSplit.replaceProblemIds || [],
        regions: serializeManualSplitRegions(manualSplit.regions || []),
      };
      const nextSession = await mutateSession?.('bulk-crop', payload);
      if (!nextSession) return;
      setManualSplit(null);
      setManualSplitDraftBox(null);
      manualSplitDragRef.current = null;
    } finally {
      manualSplitCommitRef.current = false;
    }
  }, [manualSplit, mutateSession]);

  const stampManualSplitRegion = (evt, page) => {
    if (!manualSplit || manualSplit.pageId !== page?.id || evt.button !== 0) return;
    if (!evt.currentTarget?.getBoundingClientRect) return;
    evt.preventDefault();
    const rect = evt.currentTarget.getBoundingClientRect();
    const point = manualSplitPointFromClient(evt.clientX, evt.clientY, page, rect);
    const box = manualSplitStampBoxFromPoint(point, page, manualSplit.stampBox);
    const id = `draft-region-${manualSplitSeqRef.current++}`;
    setManualSplit(prev => {
      if (!prev || prev.pageId !== page.id) return prev;
      const nextRegions = sortManualSplitRegions([
        ...(prev.regions || []),
        { id, pageId: page.id, bbox: box },
      ], page);
      return {
        ...prev,
        regions: nextRegions,
        selectedRegionIds: [id],
      };
    });
  };

  const updateManualSplitStampPreview = (evt, page) => {
    if (!manualSplit || manualSplit.mode !== 'stamp' || manualSplit.pageId !== page?.id) return;
    if (!evt.currentTarget?.getBoundingClientRect) return;
    const rect = evt.currentTarget.getBoundingClientRect();
    const point = manualSplitPointFromClient(evt.clientX, evt.clientY, page, rect);
    setManualSplitDraftBox(manualSplitStampBoxFromPoint(point, page, manualSplit.stampBox));
  };

  const clearManualSplitStampPreview = () => {
    if (manualSplit?.mode === 'stamp') setManualSplitDraftBox(null);
  };

  const beginManualSplitCanvasAction = (evt, page) => {
    if (manualSplit?.mode === 'stamp') {
      stampManualSplitRegion(evt, page);
      return;
    }
    beginManualSplitDraw(evt, page);
  };

  const beginManualSplitDraw = (evt, page) => {
    if (!manualSplit || manualSplit.pageId !== page?.id || evt.button !== 0) return;
    if (!evt.currentTarget?.getBoundingClientRect) return;
    evt.preventDefault();
    const rect = evt.currentTarget.getBoundingClientRect();
    const startPoint = manualSplitPointFromClient(evt.clientX, evt.clientY, page, rect);
    manualSplitDragRef.current = {
      mode: 'draw',
      page,
      rect,
      startPoint,
      startX: evt.clientX,
      startY: evt.clientY,
      latestBox: null,
      moved: false,
    };
  };

  const beginManualSplitRegionDrag = (regionId, evt) => {
    if (!manualSplit || evt.button !== 0) return;
    evt.preventDefault();
    evt.stopPropagation();
    if (evt.shiftKey) {
      selectManualSplitRegion(regionId, evt);
      return;
    }
    const canvas = evt.currentTarget.closest?.('.review-page-canvas');
    const rect = canvas?.getBoundingClientRect?.();
    const page = manualSplitPage;
    if (!rect?.width || !rect?.height || !page) return;
    const currentSelection = new Set(manualSplit.selectedRegionIds || []);
    const regionIds = currentSelection.has(regionId)
      ? Array.from(currentSelection)
      : [regionId];
    const initialBoxes = new Map();
    (manualSplit.regions || []).forEach(region => {
      if (regionIds.includes(region.id)) initialBoxes.set(region.id, { ...region.bbox });
    });
    setManualSplit(prev => prev ? { ...prev, selectedRegionIds: regionIds } : prev);
    manualSplitDragRef.current = {
      mode: 'move',
      page,
      startX: evt.clientX,
      startY: evt.clientY,
      scaleX: (Number(page.width) || 1) / rect.width,
      scaleY: (Number(page.height) || 1) / rect.height,
      regionIds,
      initialBoxes,
    };
  };

  const beginManualSplitResize = (regionId, mode, evt) => {
    if (!manualSplit || evt.button !== 0) return;
    evt.preventDefault();
    evt.stopPropagation();
    const canvas = evt.currentTarget.closest?.('.review-page-canvas');
    const rect = canvas?.getBoundingClientRect?.();
    const page = manualSplitPage;
    const region = (manualSplit.regions || []).find(item => item.id === regionId);
    if (!rect?.width || !rect?.height || !page || !region) return;
    setManualSplit(prev => prev ? { ...prev, selectedRegionIds: [regionId] } : prev);
    manualSplitDragRef.current = {
      mode: 'resize',
      resizeMode: mode,
      page,
      regionId,
      startX: evt.clientX,
      startY: evt.clientY,
      scaleX: (Number(page.width) || 1) / rect.width,
      scaleY: (Number(page.height) || 1) / rect.height,
      initialBox: { ...region.bbox },
    };
  };

  const cancelManualSplitGesture = () => {
    const drag = manualSplitDragRef.current;
    if (!drag) return false;
    if (drag.mode === 'move') {
      setManualSplit(prev => prev ? {
        ...prev,
        regions: (prev.regions || []).map(region => {
          const initialBox = drag.initialBoxes.get(region.id);
          return initialBox ? { ...region, bbox: initialBox } : region;
        }),
      } : prev);
    } else if (drag.mode === 'resize') {
      setManualSplit(prev => prev ? {
        ...prev,
        regions: (prev.regions || []).map(region => (
          region.id === drag.regionId ? { ...region, bbox: drag.initialBox } : region
        )),
      } : prev);
    }
    manualSplitDragRef.current = null;
    setManualSplitDraftBox(null);
    return true;
  };

  const beginBoxEdit = () => {
    if (!selectedCanBoxEdit || !selectedSingleProblem || !selectedSinglePage) return;
    setSplitTarget(null);
    setManualSplit(null);
    setManualSplitDraftBox(null);
    boxEditCommitRef.current = false;
    setBoxEdit({
      problemId: selectedSingleProblem.id,
      pageId: selectedSinglePage.id,
      box: clampReviewBox(selectedSingleProblem.bbox, selectedSinglePage),
    });
  };
  const cancelBoxEdit = () => {
    boxEditDragRef.current = null;
    boxEditCommitRef.current = false;
    setBoxEdit(null);
  };
  const applyBoxEdit = useCallback(async () => {
    if (!boxEdit?.problemId || !boxEdit?.box || boxEditCommitRef.current) return;
    boxEditCommitRef.current = true;
    try {
      const nextSession = await mutateSession?.('crop', { problemId: boxEdit.problemId, cropBox: boxEdit.box });
      if (nextSession) setBoxEdit(null);
    } finally {
      boxEditCommitRef.current = false;
    }
  }, [boxEdit, mutateSession]);
  const retryPartialAi = async (problem = selectedSingleProblem, page = selectedSinglePage, cropBox = null) => {
    if (!problem?.id || !page?.id) return;
    const retryBox = expandBoxWithinPage(cropBox || problem.bbox, page);
    await retryAiSession?.({
      partial: true,
      problemIds: [problem.id],
      cropBoxes: { [problem.id]: retryBox },
      inputIntent: 'single-problem',
    });
  };
  const retryBoxEdit = async () => {
    if (!boxEdit?.problemId || !boxEdit?.box) return;
    const problem = problemsById.get(boxEdit.problemId);
    const page = pages.find(item => item.id === (problem?.sourcePageId || boxEdit.pageId));
    await retryPartialAi(problem, page, boxEdit.box);
    setBoxEdit(null);
  };
  const beginBoxDrag = (evt, mode, page) => {
    if (!boxEdit?.box || evt.button !== 0) return;
    evt.preventDefault();
    evt.stopPropagation();
    const canvas = evt.currentTarget.closest?.('.review-page-canvas');
    const rect = canvas?.getBoundingClientRect?.();
    if (!rect?.width || !rect?.height) return;
    boxEditDragRef.current = {
      mode,
      startX: evt.clientX,
      startY: evt.clientY,
      initialBox: { ...boxEdit.box },
      pageWidth: Number(page?.width) || 1,
      pageHeight: Number(page?.height) || 1,
      scaleX: (Number(page?.width) || 1) / rect.width,
      scaleY: (Number(page?.height) || 1) / rect.height,
    };
  };

  const beginSplit = () => {
    if (selectedList.length !== 1) return;
    setBoxEdit(null);
    setManualSplit(null);
    setManualSplitDraftBox(null);
    setSplitTarget(selectedList[0]);
    setSplitRatio(0.5);
  };
  const cancelSplit = () => setSplitTarget(null);
  const confirmSplit = async () => {
    if (!splitTarget) return;
    const ratio = Math.max(0.06, Math.min(0.94, splitRatio));
    await mutateSession?.('split', { problemId: splitTarget, splitYRatio: ratio });
  };
  const doMerge = async () => {
    if (!sameSourcePage) return;
    await mutateSession?.('merge', { problemIds: selectedList });
  };
  const doExclude = useCallback(async () => {
    if (selectedActionIds.length === 0 || mutating) return;
    setSplitTarget(null);
    setBoxEdit(null);
    if (selectedActionIds.length === 1) {
      await mutateSession?.('exclude', { problemId: selectedActionIds[0] });
      return;
    }
    await mutateSession?.('exclude', { problemIds: selectedActionIds });
  }, [selectedActionIds, mutating, mutateSession]);
  const doRetryAi = async (pageIds) => {
    if (!pageIds?.length) return;
    await retryAiSession?.({ pageIds });
  };
  const toggleRiskFilter = (flag) => {
    const nextFlag = String(flag || '').trim();
    if (!nextFlag) return;
    setReviewRiskFilter(prev => (prev === nextFlag ? null : nextFlag));
    setReviewFilter('all');
    setSelectedIds(new Set());
  };
  const selectVisibleProblems = () => {
    if (!visibleReviewScope.problemIds.length) return;
    setSelectedIds(new Set(visibleReviewScope.problemIds));
    if (setActive) setActive(visibleReviewScope.problemIds[0]);
  };

  useEffect(() => {
    if (manualSplit || selectedActionIds.length === 0) return undefined;
    const onReviewDeleteKey = (evt) => {
      if (evt.defaultPrevented || evt.repeat || mutating) return;
      if (evt.key !== 'Delete' && evt.key !== 'Backspace') return;
      if (evt.ctrlKey || evt.metaKey || evt.altKey) return;
      if (isEditableKeyboardTarget(evt.target)) return;
      evt.preventDefault();
      evt.stopPropagation();
      void doExclude();
    };
    window.addEventListener('keydown', onReviewDeleteKey);
    return () => window.removeEventListener('keydown', onReviewDeleteKey);
  }, [manualSplit, selectedActionIds, mutating, doExclude]);

  // Drag handler for the split guideline. Tracks against the splitting bbox
  // element so the ratio is relative to the box, not the page image.
  useEffect(() => {
    if (!splitTarget) return;
    const onMove = (evt) => {
      if (!splitDraggingRef.current || !splitBoxRef.current) return;
      const rect = splitBoxRef.current.getBoundingClientRect();
      const y = evt.clientY - rect.top;
      const ratio = Math.max(0.06, Math.min(0.94, y / rect.height));
      setSplitRatio(ratio);
    };
    const onUp = () => { splitDraggingRef.current = false; };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [splitTarget]);

  useEffect(() => {
    if (!boxEdit || mutating) return undefined;
    const onManualCropOutsideMouseDown = (evt) => {
      if (boxEditDragRef.current || boxEditCommitRef.current) return;
      const target = evt.target;
      if (target?.closest?.('.review-bbox.editing')) return;
      if (target?.closest?.('.crop-frame-handle')) return;
      if (target?.closest?.('.review-actionbar')) return;
      evt.preventDefault();
      evt.stopPropagation();
      void applyBoxEdit();
    };
    window.addEventListener('mousedown', onManualCropOutsideMouseDown, true);
    return () => window.removeEventListener('mousedown', onManualCropOutsideMouseDown, true);
  }, [boxEdit, mutating, applyBoxEdit]);

  useEffect(() => {
    if (!manualSplit || mutating || !(manualSplit.regions || []).length) return undefined;
    const onManualSplitOutsideMouseDown = (evt) => {
      if (manualSplitDragRef.current || manualSplitCommitRef.current) return;
      const target = evt.target;
      if (target?.closest?.('.manual-split-layout')) return;
      if (target?.closest?.('.manual-split-actionbar')) return;
      evt.preventDefault();
      evt.stopPropagation();
      void applyManualPageSplit();
    };
    window.addEventListener('mousedown', onManualSplitOutsideMouseDown, true);
    return () => window.removeEventListener('mousedown', onManualSplitOutsideMouseDown, true);
  }, [manualSplit, mutating, applyManualPageSplit]);

  useEffect(() => {
    const onMove = (evt) => {
      const drag = boxEditDragRef.current;
      if (!drag) return;
      const dx = (evt.clientX - drag.startX) * drag.scaleX;
      const dy = (evt.clientY - drag.startY) * drag.scaleY;
      const minWidth = Math.min(drag.pageWidth, Math.max(12, Math.min(36, drag.pageWidth * 0.02)));
      const minHeight = Math.min(drag.pageHeight, Math.max(12, Math.min(36, drag.pageHeight * 0.02)));
      const initial = drag.initialBox;
      let left = initial.left;
      let top = initial.top;
      let right = initial.left + initial.width;
      let bottom = initial.top + initial.height;

      if (drag.mode === 'move') {
        const nextLeft = Math.max(0, Math.min(drag.pageWidth - initial.width, initial.left + dx));
        const nextTop = Math.max(0, Math.min(drag.pageHeight - initial.height, initial.top + dy));
        left = nextLeft;
        top = nextTop;
        right = nextLeft + initial.width;
        bottom = nextTop + initial.height;
      } else {
        if (drag.mode.includes('w')) left = Math.max(0, Math.min(right - minWidth, initial.left + dx));
        if (drag.mode.includes('e')) right = Math.min(drag.pageWidth, Math.max(left + minWidth, initial.left + initial.width + dx));
        if (drag.mode.includes('n')) top = Math.max(0, Math.min(bottom - minHeight, initial.top + dy));
        if (drag.mode.includes('s')) bottom = Math.min(drag.pageHeight, Math.max(top + minHeight, initial.top + initial.height + dy));
      }

      const nextBox = clampReviewBox(
        { left, top, width: right - left, height: bottom - top },
        { width: drag.pageWidth, height: drag.pageHeight }
      );
      setBoxEdit(prev => prev ? { ...prev, box: nextBox } : prev);
    };
    const onUp = () => {
      boxEditDragRef.current = null;
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [clampReviewBox]);

  useEffect(() => {
    const onMove = (evt) => {
      const drag = manualSplitDragRef.current;
      if (!drag) return;
      if (drag.mode === 'draw') {
        const point = manualSplitPointFromClient(evt.clientX, evt.clientY, drag.page, drag.rect);
        const box = manualSplitBoxFromPoints(drag.startPoint, point, drag.page);
        drag.latestBox = box;
        drag.moved = Math.hypot(evt.clientX - drag.startX, evt.clientY - drag.startY) >= MANUAL_SPLIT_DRAW_THRESHOLD_PX;
        setManualSplitDraftBox(box);
        return;
      }

      if (drag.mode === 'move') {
        const dx = (evt.clientX - drag.startX) * drag.scaleX;
        const dy = (evt.clientY - drag.startY) * drag.scaleY;
        setManualSplit(prev => {
          if (!prev) return prev;
          return {
            ...prev,
            regions: (prev.regions || []).map(region => {
              const initialBox = drag.initialBoxes.get(region.id);
              if (!initialBox) return region;
              return {
                ...region,
                bbox: clampReviewBox({
                  ...initialBox,
                  left: finiteNumber(initialBox.left, 0) + dx,
                  top: finiteNumber(initialBox.top, 0) + dy,
                }, drag.page),
              };
            }),
          };
        });
        return;
      }

      if (drag.mode === 'resize') {
        const dx = (evt.clientX - drag.startX) * drag.scaleX;
        const dy = (evt.clientY - drag.startY) * drag.scaleY;
        const initial = drag.initialBox;
        let left = initial.left;
        let top = initial.top;
        let right = initial.left + initial.width;
        let bottom = initial.top + initial.height;
        const minWidth = Math.min(drag.page.width, Math.max(12, Math.min(36, drag.page.width * 0.02)));
        const minHeight = Math.min(drag.page.height, Math.max(12, Math.min(36, drag.page.height * 0.02)));

        if (drag.resizeMode.includes('w')) left = Math.max(0, Math.min(right - minWidth, initial.left + dx));
        if (drag.resizeMode.includes('e')) right = Math.min(drag.page.width, Math.max(left + minWidth, initial.left + initial.width + dx));
        if (drag.resizeMode.includes('n')) top = Math.max(0, Math.min(bottom - minHeight, initial.top + dy));
        if (drag.resizeMode.includes('s')) bottom = Math.min(drag.page.height, Math.max(top + minHeight, initial.top + initial.height + dy));

        const nextBox = clampReviewBox({
          left,
          top,
          width: right - left,
          height: bottom - top,
        }, drag.page);
        setManualSplit(prev => prev ? {
          ...prev,
          regions: (prev.regions || []).map(region => (
            region.id === drag.regionId ? { ...region, bbox: nextBox } : region
          )),
        } : prev);
      }
    };
    const onUp = (evt) => {
      const drag = manualSplitDragRef.current;
      if (!drag) return;
      if (drag.mode === 'draw') {
        const endPoint = manualSplitPointFromClient(evt.clientX, evt.clientY, drag.page, drag.rect);
        const box = manualSplitBoxFromPoints(drag.startPoint, endPoint, drag.page);
        const moved = Math.hypot(evt.clientX - drag.startX, evt.clientY - drag.startY) >= MANUAL_SPLIT_DRAW_THRESHOLD_PX;
        if (moved) {
          const id = `draft-region-${manualSplitSeqRef.current++}`;
          setManualSplit(prev => {
            if (!prev || prev.pageId !== drag.page.id) return prev;
            const nextRegions = sortManualSplitRegions([
              ...(prev.regions || []),
              { id, pageId: drag.page.id, bbox: box },
            ], drag.page);
            return {
              ...prev,
              regions: nextRegions,
              selectedRegionIds: [id],
            };
          });
        }
      }
      manualSplitDragRef.current = null;
      setManualSplitDraftBox(null);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [clampReviewBox]);

  useEffect(() => {
    if (!manualSplit) return undefined;
    const onKeyDown = (evt) => {
      const isFormControl = isEditableKeyboardTarget(evt.target);
      if (evt.key === 'Escape' && manualSplit.mode === 'stamp') {
        evt.preventDefault();
        setManualSplitMode('draw');
        return;
      }
      if (isFormControl) return;
      if (evt.key === 'Delete' || evt.key === 'Backspace') {
        if ((manualSplit.selectedRegionIds || []).length) {
          evt.preventDefault();
          deleteManualSplitSelected();
        }
        return;
      }
      if (evt.key === 'Escape') {
        evt.preventDefault();
        if (cancelManualSplitGesture()) return;
        if ((manualSplit.selectedRegionIds || []).length) {
          setManualSplit(prev => prev ? { ...prev, selectedRegionIds: [] } : prev);
          return;
        }
        cancelManualPageSplit();
        return;
      }
      if (evt.key === 'Enter') {
        evt.preventDefault();
        if ((manualSplit.regions || []).length && !mutating) {
          applyManualPageSplit();
        }
        return;
      }
      const arrowDelta = {
        ArrowLeft: [-1, 0],
        ArrowRight: [1, 0],
        ArrowUp: [0, -1],
        ArrowDown: [0, 1],
      }[evt.key];
      if (arrowDelta && (manualSplit.selectedRegionIds || []).length) {
        evt.preventDefault();
        const step = evt.shiftKey ? MANUAL_SPLIT_FAST_NUDGE_PX : MANUAL_SPLIT_NUDGE_PX;
        nudgeManualSplitSelected(arrowDelta[0] * step, arrowDelta[1] * step);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [manualSplit, mutating, manualSplitPage]);

  if (!pages.length) {
    return (
      <div className="col center">
        <div className="stage">
          <div className="stage-toolbar">
            <span className="name">검수 — 검출된 문제 박스</span>
          </div>
          <div className="review-empty">
            먼저 자료를 업로드해 주세요.<br />
            업로드한 페이지 원본 위에 검출된 박스를 보여드립니다.
          </div>
        </div>
      </div>
    );
  }

  const actionableStatusCount = Math.max(0, Number(reviewSummary.actionableNeedsReviewCount) || 0);
  const riskyCount = actionableStatusCount || statusCounts.failed;
  const filterOptions = [
    ['all', '전체', statusCounts.all],
    ['normal', '정상', statusCounts.normal],
    ['check_needed', '확인 필요', actionableStatusCount],
    ['failed', '실패', statusCounts.failed],
  ];
  if (sessionCounts.supplemental > 0) {
    filterOptions.push(['supplemental', '자료', sessionCounts.supplemental]);
  }
  if (statusCounts.passage > 0) {
    filterOptions.push(['passage', '긴 지문', statusCounts.passage]);
  }
  const retryDisabledReason = !aiAvailable
    ? 'Gemini API 키를 먼저 저장해 주세요'
    : aiBusy
      ? 'AI 인식 중입니다'
      : mutating
        ? '처리 중입니다'
      : '';
  const bulkRetryPageIds = activeReviewFilter ? visibleReviewScope.retryPageIds : pageRetryIds;
  const bulkRetryProblemCount = activeReviewFilter ? visibleReviewScope.problemCount : riskyCount;
  const showBulkRetry = reviewFilter !== 'normal' && reviewFilter !== 'supplemental' && bulkRetryProblemCount > 0 && bulkRetryPageIds.length > 0;

  const actionBar = boxEdit ? (
    <div className="review-actionbar">
      <span className="count-chip">틀 조정 중</span>
      <span className="hint">모서리와 변을 끌어 맞춘 뒤 바깥을 클릭해 바로 적용하거나, 주변 여백까지 포함해 다시 인식하세요.</span>
      <div className="spacer" />
      <button className="btn" type="button" onClick={cancelBoxEdit} disabled={mutating}>취소</button>
      <button
        className="btn"
        type="button"
        title={retryDisabledReason || '조정한 영역 주변까지 AI로 다시 인식'}
        onClick={retryBoxEdit}
        disabled={!aiAvailable || aiBusy || mutating}
      >
        주변 영역 AI 재인식
      </button>
      <button className="btn primary" type="button" onClick={applyBoxEdit} disabled={mutating}>
        자르기 적용
      </button>
    </div>
  ) : splitTarget ? (
    <div className="review-actionbar">
      <span className="count-chip">가르기 중</span>
      <span className="hint">박스 안의 파란 선을 드래그해서 위치를 정한 다음 [가르기]를 눌러주세요.</span>
      <div className="spacer" />
      <button className="btn" onClick={cancelSplit} disabled={mutating}>취소</button>
      <button className="btn primary" onClick={confirmSplit} disabled={mutating}>
        ✂ {(splitRatio * 100).toFixed(0)}% 위치에서 가르기
      </button>
    </div>
  ) : manualSplit ? (
    <div className="review-actionbar manual-split-actionbar">
      <span className="count-chip">수동 분할</span>
      <span className="hint">
        드래그로 그리고, 스탬프로 찍고, 선택 영역은 바로 조정하세요.
        {' '}
        {(manualSplit.regions || []).length}개 영역
        {(manualSplit.replaceProblemIds || []).length
          ? ` · ${(manualSplit.replaceProblemIds || []).length}개 항목 교체`
          : ' · 페이지에 추가'}
      </span>
      {manualSplitOverlaps && (
        <span className="review-summary-chip warn">겹침 허용됨</span>
      )}
      <div className="spacer" />
      <button
        className="btn"
        type="button"
        onClick={autoSortManualSplitRegions}
        disabled={mutating || !(manualSplit.regions || []).length}
      >
        {Icon.align} 자동 정렬
      </button>
      <button
        className="btn danger"
        type="button"
        onClick={deleteManualSplitSelected}
        disabled={mutating || !(manualSplit.selectedRegionIds || []).length}
      >
        {Icon.trash} 선택 삭제
      </button>
      <button className="btn" type="button" onClick={cancelManualPageSplit} disabled={mutating}>취소</button>
      <button
        className="btn primary"
        type="button"
        onClick={applyManualPageSplit}
        title="Enter로 분할 적용"
        aria-keyshortcuts="Enter"
        disabled={mutating || !(manualSplit.regions || []).length}
      >
        {Icon.check} 분할 적용 {(manualSplit.regions || []).length}
      </button>
    </div>
  ) : selectedList.length === 0 ? (
    <div className="review-actionbar">
      <div className="review-filters" aria-label="검수 상태 필터">
        {filterOptions.map(([value, label, count]) => (
          <button
            key={value}
            className={reviewFilter === value ? 'on' : ''}
            type="button"
            onClick={() => setReviewFilter(value)}
          >
            {label} <span>{count}</span>
          </button>
        ))}
      </div>
      {reviewRiskFilter && (
        <span className="review-risk-filter-active">
          원인 필터 · {riskFlagLabel(reviewRiskFilter)}
          <button type="button" onClick={() => setReviewRiskFilter(null)}>해제</button>
        </span>
      )}
      {reviewScopeActive && (
        <span className="review-risk-filter-active">
          최근 추가 묶음 · {formatProblemCount(sessionCounts)}
          <button type="button" onClick={clearReviewScope}>전체 세션 보기</button>
        </span>
      )}
      <span className="hint">문제 박스를 확인하고, 이상한 페이지만 AI로 다시 인식하세요.</span>
      <div className="spacer" />
      {activeReviewFilter && visibleReviewScope.problemIds.length > 0 && (
        <>
          <button
            className="btn"
            type="button"
            onClick={selectVisibleProblems}
            disabled={mutating}
          >
            표시 항목 선택 {visibleReviewScope.problemIds.length}
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => onConfirm?.(null, { problemIds: visibleReviewScope.problemIds, bulk: true })}
            disabled={mutating}
          >
            표시 항목 확인 완료 {visibleReviewScope.problemIds.length}
          </button>
        </>
      )}
      {!activeReviewFilter && actionableProblemIds.length > 0 && (
        <button
          className="btn"
          type="button"
          onClick={() => onConfirm?.(null, { problemIds: actionableProblemIds, bulk: true })}
          disabled={mutating}
        >
          확인 필요 전체 확인 {actionableProblemIds.length}
        </button>
      )}
      {showBulkRetry && (
        <button
          className="btn primary"
          type="button"
          title={retryDisabledReason || `${bulkRetryPageIds.length}개 페이지 재인식`}
          onClick={() => doRetryAi(bulkRetryPageIds)}
          disabled={!aiAvailable || aiBusy || mutating || !bulkRetryPageIds.length}
        >
          페이지 전체 AI 재인식 {bulkRetryProblemCount}
        </button>
      )}
    </div>
  ) : (
    <div className="review-actionbar is-selection">
      <div className="review-actionbar-copy">
        <span className="count-chip">{selectedList.length}개 선택됨</span>
        <span className="hint">
          {selectedList.length === 1
            ? '선택 박스 주변만 다시 인식하거나, 틀을 조정해 자르고, 필요하면 두 문제로 나눌 수 있어요.'
            : sameSourcePage
              ? '같은 페이지의 박스들을 하나로 합치거나, 모두 제외할 수 있어요.'
              : '같은 페이지의 박스만 합칠 수 있어요. (현재 선택은 페이지가 다름)'}
        </span>
      </div>
      <div className="review-actionbar-actions">
        <div className="review-action-group review-action-group-main">
          {selectedList.length >= 2 && (
            <button
              className="btn"
              type="button"
              title={retryDisabledReason || `${selectedRetryPageIds.length}개 페이지 전체 재인식`}
              onClick={() => doRetryAi(selectedRetryPageIds)}
              disabled={!aiAvailable || aiBusy || mutating || !selectedHasRetryable || !selectedRetryPageIds.length}
            >
              {Icon.wand} 페이지 전체 AI 재인식 {selectedList.length}
            </button>
          )}
          {selectedList.length === 1 && (
            <>
              <button
                className="btn primary"
                type="button"
                onClick={() => retryPartialAi()}
                disabled={!aiAvailable || aiBusy || mutating || !selectedCanBoxEdit}
                title={retryDisabledReason || '선택한 박스 주변 여백까지 AI로 다시 인식'}
              >
                {Icon.wand} 주변 영역 AI 재인식
              </button>
              <button
                className="btn"
                type="button"
                onClick={beginBoxEdit}
                disabled={mutating || !selectedCanBoxEdit}
                title={selectedCanBoxEdit ? '원본 페이지 위에서 자르기 틀을 직접 조정' : '원본 페이지 이미지가 있어야 합니다'}
              >
                {Icon.crop} 틀 조정/자르기
              </button>
            </>
          )}
        </div>
        <div className="review-action-group review-action-group-reshape">
          {selectedList.length === 1 && (
            <>
              <button
                className="btn"
                type="button"
                onClick={beginSplit}
                disabled={mutating}
              >
                {Icon.split} 두 문제로 나누기
              </button>
              <button
                className="btn"
                type="button"
                onClick={beginManualSplitForSelection}
                disabled={mutating || !selectedCanManualSplit}
                title={selectedCanManualSplit ? '선택한 원본 페이지를 직접 여러 영역으로 자르기' : '같은 원본 페이지의 항목을 선택해야 합니다'}
              >
                {Icon.pen} 수동 쪼개기
              </button>
            </>
          )}
          {selectedList.length >= 2 && (
            <>
              <button
                className="btn primary"
                type="button"
                onClick={doMerge}
                disabled={!sameSourcePage || mutating}
              >
                {Icon.align} 합치기
              </button>
              <button
                className="btn"
                type="button"
                onClick={beginManualSplitForSelection}
                disabled={mutating || !selectedCanManualSplit}
                title={selectedCanManualSplit ? '선택한 항목들을 직접 다시 자르기' : '같은 원본 페이지의 항목만 수동 분할할 수 있습니다'}
              >
                {Icon.pen} 수동 쪼개기
              </button>
            </>
          )}
        </div>
        <div className="review-action-group review-action-group-status">
          <button
            className="btn soft-success"
            type="button"
            onClick={() => onConfirm?.(null, { problemIds: selectedList, bulk: true })}
            disabled={mutating}
          >
            {Icon.check} 확인 완료 {selectedList.length}
          </button>
          <button className="btn danger" type="button" onClick={doExclude} disabled={mutating}>
            {Icon.trash} 제외 {selectedList.length}
          </button>
          <button
            className="btn ghost-action"
            type="button"
            onClick={() => setSelectedIds(new Set())}
            disabled={mutating}
            title="선택 해제"
          >
            {Icon.close} 선택 해제
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="col center">
      <div className="stage">
        <div className="stage-toolbar">
          <span className="name">검수 — 검출된 문제 박스</span>
          <span className="pill"><span className="dotc" /> {pages.length} 페이지 · {formatProblemCount(sessionCounts)}</span>
          <div className="spacer" />
          <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>
            빨간 박스는 인식이 의심됩니다. 클릭 후 가르기·합치기·제외하세요.
          </span>
          <div className="review-zoom-controls" aria-label="검수 화면 확대">
            <button
              type="button"
              className="icon-btn"
              title="문제 이미지 축소"
              onClick={() => adjustReviewZoom(-REVIEW_ZOOM_STEP)}
              disabled={reviewZoom <= REVIEW_ZOOM_MIN}
            >
              {Icon.zoomOut}
            </button>
            <input
              className="review-zoom-range"
              type="range"
              min={reviewZoomPercent(REVIEW_ZOOM_MIN)}
              max={reviewZoomPercent(REVIEW_ZOOM_MAX)}
              step="1"
              value={reviewZoomPercent(reviewZoom)}
              aria-label="검수 이미지 확대율"
              title="문제 이미지만 확대/축소"
              onChange={updateReviewZoomPercent}
            />
            <button
              type="button"
              className="review-zoom-reset"
              title="100%로 되돌리기"
              onClick={resetReviewZoom}
              disabled={Math.abs(reviewZoom - 1) < 0.001}
            >
              {reviewZoomPercent(reviewZoom)}%
            </button>
            <button
              type="button"
              className="icon-btn"
              title="문제 이미지 확대"
              onClick={() => adjustReviewZoom(REVIEW_ZOOM_STEP)}
              disabled={reviewZoom >= REVIEW_ZOOM_MAX}
            >
              {Icon.zoomIn}
            </button>
          </div>
        </div>
        <div
          className={`review-wrap ${manualSplit ? 'manual-split-open' : ''}`}
          style={{ '--review-zoom': String(reviewZoom) }}
          onWheel={handleReviewWheel}
          ref={reviewWrapRef}
        >
          {actionBar}
          <div className="review-summary-strip">
            <span className="review-summary-title">검수 요약</span>
            <span className="review-summary-chip">{formatProblemCount(reviewSummary.counts)}</span>
            <span className={`review-summary-chip ${reviewSummary.warningCount ? 'warn' : 'ok'}`}>
              주의 {reviewSummary.warningCount}
            </span>
            {reviewSummary.aiStages.map(stage => (
              <span
                key={stage.stage}
                className={`review-summary-chip ${stage.usedPageCount || stage.attemptedBlockCount || stage.appliedPageCount ? 'ok' : ''}`}
                title={aiStageTooltip(stage)}
              >
                {aiStageChipText(stage)}
              </span>
            ))}
            {reviewSummary.actionableNeedsReviewCount > 0 && (
              <span className="review-summary-chip warn">
                확인 {reviewSummary.actionableNeedsReviewCount}
                {reviewSummary.reviewStatusCounts.failed ? ` · 실패 ${reviewSummary.reviewStatusCounts.failed}` : ''}
              </span>
            )}
            {reviewSummary.hwpOversegmentationCount > 0 && (
              <span className="review-summary-chip warn" title="HWP 내부 문항 수보다 최종 분할 수가 크게 많습니다.">
                HWP 과분할 {reviewSummary.hwpOversegmentationCount}
              </span>
            )}
            {reviewSummary.hwpProblemCountMismatchCount > 0 && reviewSummary.hwpOversegmentationCount <= 0 && (
              <span className="review-summary-chip warn" title="HWP 내부 문항 수와 최종 분할 수가 다릅니다.">
                HWP 문항 차이 {reviewSummary.hwpProblemCountMismatchCount}
              </span>
            )}
            {reviewSummary.duplicateProblemNumberGroups.length > 0 && (
              <span
                className="review-summary-chip"
                title="같은 문항 번호가 여러 구간에 보존되어 있습니다."
              >
                중복 번호 {reviewSummary.duplicateProblemNumberLabel}
              </span>
            )}
            {reviewSummary.sourceProblemOverlapGroups.length > 0 && (
              <span
                className="review-summary-chip warn"
                title="같은 원본 페이지에서 문항 인식 영역이 크게 겹칩니다."
              >
                원본 겹침 {reviewSummary.sourceProblemOverlapLabel}
              </span>
            )}
            {reviewSummary.passageGroupSourceReuseGroups.length > 0 && (
              <button
                type="button"
                className={`review-summary-chip warn risk-filter-chip ${reviewRiskFilter === 'passage_group_source_reuse' ? 'on' : ''}`}
                title="같은 긴 지문 그룹의 하위 문항 원본 영역이 크게 겹칩니다."
                aria-pressed={reviewRiskFilter === 'passage_group_source_reuse'}
                data-risk-flag="passage_group_source_reuse"
                onClick={() => toggleRiskFilter('passage_group_source_reuse')}
              >
                지문 원본 중복 {reviewSummary.passageGroupSourceReuseLabel}
              </button>
            )}
            {reviewSummary.passageGroupCount > 0 && (
              <button
                type="button"
                className={`review-summary-chip risk-filter-chip ${reviewFilter === 'passage' ? 'on' : ''}`}
                title={`${reviewSummary.passageProblemCount}개 문항이 긴 지문 그룹에 연결되어 있습니다.${
                  reviewSummary.passageContinuationBlockCount > 0
                    ? ` 이어짐 블록 ${reviewSummary.passageContinuationBlockCount}개 포함.`
                    : ''
                }`}
                aria-pressed={reviewFilter === 'passage'}
                onClick={() => setReviewFilter(prev => (prev === 'passage' ? 'all' : 'passage'))}
              >
                긴 지문 그룹 {reviewSummary.passageGroupCount}
                {reviewSummary.passageContinuationBlockCount > 0 ? ` · 이어짐 ${reviewSummary.passageContinuationBlockCount}` : ''}
              </button>
            )}
            {reviewSummary.passageReviewItemCount > 0 && (
              <button
                type="button"
                className={`review-summary-chip warn risk-filter-chip ${reviewFilter === 'passage-review' ? 'on' : ''}`}
                title={[
                  '긴 지문 검수 큐',
                  reviewSummary.passageReviewLabel,
                  reviewSummary.passageReviewReasonLabel,
                  reviewSummary.passageReviewPreview ? `대상 ${reviewSummary.passageReviewPreview}` : '',
                ].filter(Boolean).join(' · ')}
                aria-pressed={reviewFilter === 'passage-review'}
                onClick={() => setReviewFilter(prev => (prev === 'passage-review' ? 'all' : 'passage-review'))}
              >
                {reviewSummary.passageReviewLabel}
                {reviewSummary.passageReviewReasonLabel ? ` · ${reviewSummary.passageReviewReasonLabel}` : ''}
              </button>
            )}
            {reviewSummary.topRiskFlags.map(item => (
              <button
                key={item.flag}
                type="button"
                className={`review-summary-chip risk-filter-chip ${reviewRiskFilter === item.flag ? 'on' : ''}`}
                title={`${riskFlagLabel(item.flag)} 항목만 보기`}
                aria-pressed={reviewRiskFilter === item.flag}
                data-risk-flag={item.flag}
                onClick={() => toggleRiskFilter(item.flag)}
              >
                {riskFlagLabel(item.flag)} {item.count}
              </button>
            ))}
            {reviewSummary.hwpTextProblemSignalCount > 0 && (
              <span className="review-summary-chip">
                HWP 텍스트 {reviewSummary.hwpTextProblemSignalCount}
                {reviewSummary.hwpTextExtractorLabel ? ` · ${reviewSummary.hwpTextExtractorLabel}` : ''}
              </span>
            )}
            {reviewSummary.hwpTextProblemSignalCount > 0 && reviewSummary.hwpTextProblemCountStatus !== 'unknown' && (
              <span
                className={`review-summary-chip ${reviewSummary.hwpTextProblemCountStatus === 'match' ? 'ok' : 'warn'}`}
                title={reviewSummary.hwpTextProblemCountMessage}
              >
                {reviewSummary.hwpTextProblemCountStatus === 'match'
                  ? '문항 수 일치'
                  : `문항 수 차이 ${reviewSummary.hwpTextProblemDelta > 0 ? '+' : ''}${reviewSummary.hwpTextProblemDelta}`}
              </span>
            )}
            {reviewSummary.hwpLayoutProblemSignalCount > 0 && (
              <span className="review-summary-chip">
                HWP 레이아웃 {reviewSummary.hwpLayoutProblemSignalCount}
                {reviewSummary.hwpLayoutExtractorLabel ? ` · ${reviewSummary.hwpLayoutExtractorLabel}` : ''}
              </span>
            )}
            {reviewSummary.hwpLayoutProblemSignalCount > 0 && reviewSummary.hwpLayoutProblemCountStatus !== 'unknown' && (
              <span
                className={`review-summary-chip ${reviewSummary.hwpLayoutProblemCountStatus === 'match' ? 'ok' : 'warn'}`}
                title={reviewSummary.hwpLayoutProblemCountMessage}
              >
                {reviewSummary.hwpLayoutProblemCountStatus === 'match'
                  ? '레이아웃 일치'
                  : `레이아웃 차이 ${reviewSummary.hwpLayoutProblemDelta > 0 ? '+' : ''}${reviewSummary.hwpLayoutProblemDelta}`}
              </span>
            )}
            {reviewSummary.hwpCacheHitPageCount > 0 && (
              <span
                className="review-summary-chip ok"
                title={`렌더 캐시 ${reviewSummary.hwpRendererCacheHitCount} · 정규화 캐시 ${reviewSummary.hwpNormalizedCacheHitCount}`}
              >
                HWP 캐시 {reviewSummary.hwpCacheHitPageCount}
              </span>
            )}
            {reviewSummary.warningPreview && (
              <span className="review-summary-note">{reviewSummary.warningPreview}</span>
            )}
          </div>
          {pages.map(page => {
            const allPageProblems = (page.problemIds || [])
              .map(pid => problemsById.get(pid))
              .filter(Boolean)
              .filter(problemInReviewScope);
            const pageId = String(page?.id || '').trim();
            const pageInScope = !reviewScopeActive || reviewScopePageIdSet.has(pageId) || allPageProblems.length > 0;
            if (!pageInScope) return null;
            if (manualSplit && page.id !== manualSplit.pageId) return null;
            const pageMatchesRiskFilter = reviewRiskFilter && !riskFilterHasProblemMatches
              ? hasRiskFlag(page, reviewRiskFilter)
              : false;
            const pageProblems = allPageProblems
              .filter(problem => problemMatchesReviewFilter(problem, reviewFilter, { passageReviewProblemIds }))
              .filter(problem => !reviewRiskFilter || pageMatchesRiskFilter || hasRiskFlag(problem, reviewRiskFilter));
            if ((reviewFilter !== 'all' || reviewRiskFilter) && pageProblems.length === 0) return null;
            const pageCounts = countSessionProblems(allPageProblems);
            const pageRiskFlags = riskFlagsFor(page);
            const pageStatus = normalizeReviewStatus(page.reviewStatus || page.review_status)
              || (!allPageProblems.length ? 'failed' : pageRiskFlags.length ? 'check_needed' : 'normal');
            const hasRisk = pageRiskFlags.length > 0 || pageStatus !== 'normal';
            const pageCanRetry = pageRetryIds.includes(page.id);
            return (
              <div key={page.id} className={`review-page ${reviewStatusClass(pageStatus)}`}>
                <div className="review-page-hd">
                  <span className="pg-num">{page.id}</span>
                  <span className="pg-count">
                    {reviewFilter === 'all' && !reviewRiskFilter
                      ? formatProblemCount(pageCounts)
                      : `${pageProblems.length}/${allPageProblems.length} 표시`}
                  </span>
                  <span className={`status-badge ${reviewStatusClass(pageStatus)}`}>
                    {reviewStatusMeta(pageStatus).label}
                  </span>
                  {hasRisk && (
                    <span className="pg-risk" title={pageRiskFlags.join(', ')}>
                      {pageRiskFlags.length ? `위험 · ${pageRiskFlags.join(' · ')}` : '문제 인식 실패'}
                    </span>
                  )}
                  <div className="spacer" />
                  {pageCanRetry && (
                    <button
                      className="mini-action"
                      type="button"
                      title={retryDisabledReason || '이 페이지만 AI로 다시 인식'}
                      onClick={() => doRetryAi([page.id])}
                      disabled={!aiAvailable || aiBusy || mutating}
                    >
                      AI 재인식
                    </button>
                  )}
                  <button
                    className="mini-action"
                    type="button"
                    title={page.sourceImageUri ? '이 페이지를 직접 여러 문제로 자르기' : '페이지 원본 이미지가 있어야 합니다'}
                    onClick={() => beginManualPageSplit(page, allPageProblems.map(problem => problem.id))}
                    disabled={mutating || !!manualSplit || !page.sourceImageUri}
                  >
                    수동 쪼개기
                  </button>
                  <span style={{ fontSize: 11, color: 'var(--muted-2)', fontFamily: "'JetBrains Mono', monospace" }}>
                    {page.width}×{page.height}
                  </span>
                </div>
                {manualSplit?.pageId === page.id ? (
                  <ManualSplitEditor
                    page={page}
                    regions={manualSplit.regions || []}
                    draftBox={manualSplitDraftBox}
                    mode={manualSplit.mode || 'draw'}
                    stampBox={manualSplit.stampBox || manualSplitDefaultStampBox(page, manualSplit.replaceProblemIds || [])}
                    selectedRegionIds={manualSplitSelectedIds}
                    hasOverlap={manualSplitOverlaps}
                    mutating={mutating}
                    onCanvasMouseDown={beginManualSplitCanvasAction}
                    onCanvasMouseMove={updateManualSplitStampPreview}
                    onCanvasMouseLeave={clearManualSplitStampPreview}
                    onModeChange={setManualSplitMode}
                    onSaveStampFromSelection={saveManualSplitStampFromSelection}
                    onRegionMouseDown={beginManualSplitRegionDrag}
                    onHandleMouseDown={beginManualSplitResize}
                    onListSelect={selectManualSplitRegion}
                    onDeleteRegion={deleteManualSplitRegion}
                    onDuplicateRegion={duplicateManualSplitRegion}
                    onMoveRegion={moveManualSplitRegionInList}
                    onReorderRegion={reorderManualSplitRegion}
                    onApply={applyManualPageSplit}
                    onStampSizeChange={updateManualSplitStampSize}
                  />
                ) : (
                  <ReviewCanvasZoomShell>
                    <div className="review-page-canvas">
                    {page.sourceImageUri ? (
                      <img src={page.sourceImageUri} alt={page.id} draggable={false} />
                    ) : (
                      <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
                        페이지 이미지를 불러올 수 없어요.
                      </div>
                    )}
                    {pageProblems.map(prob => {
                    const isEditing = boxEdit?.problemId === prob.id;
                    const bbox = isEditing ? boxEdit.box : (prob.bbox || {});
                    const w = page.width || 1;
                    const h = page.height || 1;
                    if (!bbox.width || !bbox.height) return null;
                    const leftPct = (bbox.left / w) * 100;
                    const topPct = (bbox.top / h) * 100;
                    const widthPct = (bbox.width / w) * 100;
                    const heightPct = (bbox.height / h) * 100;
                    const isSelected = selectedIds.has(prob.id);
                    const isActive = prob.id === activeId;
                    const status = deriveProblemStatus(prob);
                    const statusMeta = reviewStatusMeta(status);
                    const isRisky = status !== 'normal';
                    const isSplitting = splitTarget === prob.id;
                    const passageGroupId = passageGroupIdFor(prob);
                    const isPassage = Boolean(passageGroupId);
                    const order = orderMap.get(prob.id);
                    const tooltipParts = [prob.title || ''];
                    const problemRiskFlags = riskFlagsFor(prob);
                    if (isRisky) tooltipParts.push(`${statusMeta.label}: ${problemRiskFlags.join(', ') || '경계 확인 필요'}`);
                    if (isPassage) tooltipParts.push(`긴 지문 ${passageGroupId}`);
                    const classes = [
                      'review-bbox',
                      isPassage ? 'review-bbox-passage' : '',
                      isSelected ? 'selected' : '',
                      isActive ? 'active' : '',
                      isRisky ? 'risky' : '',
                      reviewStatusClass(status),
                      isSplitting ? 'splitting' : '',
                      isEditing ? 'editing' : '',
                    ].filter(Boolean).join(' ');
                    return (
                      <div
                        key={prob.id}
                        ref={isSplitting ? splitBoxRef : null}
                        className={classes}
                        style={{
                          left: `${leftPct}%`,
                          top: `${topPct}%`,
                          width: `${widthPct}%`,
                          height: `${heightPct}%`,
                        }}
                        onMouseDown={isEditing ? (evt) => beginBoxDrag(evt, 'move', page) : undefined}
                        onClick={(evt) => {
                          if (isEditing) {
                            evt.stopPropagation();
                            return;
                          }
                          onBoxClick(prob.id, evt);
                        }}
                        title={tooltipParts.filter(Boolean).join(' · ')}
                      >
                        <div className="review-bbox-label">
                          {String(order || '?').padStart(2, '0')}
                          {isPassage && <span className="review-bbox-passage-tag">지문</span>}
                          {isRisky && <span className="review-bbox-risk">{statusMeta.shortLabel}</span>}
                        </div>
                        {isEditing && (
                          <>
                            <div className="crop-frame-label">틀 조정</div>
                            {['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].map(mode => (
                              <button
                                key={mode}
                                type="button"
                                className={`crop-frame-handle ${mode}`}
                                aria-label={`자르기 틀 ${mode}`}
                                onMouseDown={(evt) => beginBoxDrag(evt, mode, page)}
                                onClick={(evt) => evt.stopPropagation()}
                              />
                            ))}
                          </>
                        )}
                        {isSplitting && (
                          <div
                            className="split-guide"
                            style={{ top: `${splitRatio * 100}%` }}
                            onMouseDown={(evt) => {
                              evt.stopPropagation();
                              splitDraggingRef.current = true;
                            }}
                          />
                        )}
                      </div>
                    );
                    })}
                    </div>
                  </ReviewCanvasZoomShell>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── LEFT: items rail ─────────────────────────────────────────────────────
function ItemsRail({
  items, activeId, setActive, reorder, removeItem, addSample, bulkApply, handleFiles,
  pendingFiles, selectedPendingFileKey, onSelectPendingFile,
  removePendingFile, clearPendingFiles, processQueuedFiles, queueBusy, aiAvailable,
  addMockSample, canAddDummy, recentSessions, restoringSessionId, onRestoreRecentSession,
  onDownloadItemImage, downloadingItemId,
}){
  const dragId = useRef(null);
  const [draggingId, setDraggingId] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const [dropZoneActive, setDropZoneActive] = useState(false);
  const railRef = useRef(null);
  const itemRefs = useRef({});
  const previousItemRects = useRef(new Map());
  const pointerDragRef = useRef(null);
  const suppressClickRef = useRef(false);
  const dropTargetRef = useRef(null);
  const [recentSessionsCollapsed, setRecentSessionsCollapsed] = useState(() => {
    try {
      const stored = window.localStorage?.getItem(RECENT_SESSIONS_COLLAPSED_KEY);
      return stored == null ? true : stored === '1';
    } catch (_err) {
      return true;
    }
  });
  const hasSessionItems = items.length > 0;

  useLayoutEffect(() => {
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    const nextRects = new Map();
    items.forEach(it => {
      const el = itemRefs.current[it.id];
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const prevRect = previousItemRects.current.get(it.id);
      if (prevRect && !reduceMotion) {
        const dx = prevRect.left - rect.left;
        const dy = prevRect.top - rect.top;
        if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
          el.animate(
            [
              { transform: `translate(${dx}px, ${dy}px)` },
              { transform: 'translate(0, 0)' },
            ],
            { duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)' }
          );
        }
      }
      nextRects.set(it.id, { top: rect.top, left: rect.left, width: rect.width, height: rect.height });
    });
    previousItemRects.current = nextRects;
  }, [items]);

  const clearDragState = () => {
    dragId.current = null;
    setDraggingId(null);
    dropTargetRef.current = null;
    setDropTarget(null);
  };

  const setCurrentDropTarget = (target) => {
    dropTargetRef.current = target;
    setDropTarget(prev => (
      prev?.id === target?.id && prev?.position === target?.position ? prev : target
    ));
  };

  const updateDropTarget = (event, targetId) => {
    if (!dragId.current || dragId.current === targetId) {
      setCurrentDropTarget(null);
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const position = dropPositionFromClientY(event.currentTarget.getBoundingClientRect(), event.clientY);
    setCurrentDropTarget({ id: targetId, position });
  };

  const findPointerDropTarget = (clientX, clientY, sourceId) => {
    const rail = railRef.current;
    if (!rail) return null;
    const hit = document.elementFromPoint(clientX, clientY);
    const row = hit?.closest?.('.item[data-item-id]');
    if (row && rail.contains(row)) {
      const id = row.getAttribute('data-item-id');
      if (!id || id === sourceId) return null;
      return {
        id,
        position: dropPositionFromClientY(row.getBoundingClientRect(), clientY),
      };
    }

    const rows = Array.from(rail.querySelectorAll('.item[data-item-id]'));
    if (!rows.length) return null;
    const first = rows[0].getBoundingClientRect();
    if (clientY < first.top) {
      const id = rows[0].getAttribute('data-item-id');
      return id && id !== sourceId ? { id, position: 'before' } : null;
    }
    for (const candidate of rows) {
      const rect = candidate.getBoundingClientRect();
      const id = candidate.getAttribute('data-item-id');
      if (clientY < rect.top + rect.height / 2) {
        return id && id !== sourceId ? { id, position: 'before' } : null;
      }
    }
    const last = rows[rows.length - 1];
    const id = last.getAttribute('data-item-id');
    return id && id !== sourceId ? { id, position: 'after' } : null;
  };

  const startPointerDrag = (event, itemId) => {
    if (event.button !== 0 || event.target.closest?.('button')) return;
    pointerDragRef.current = {
      id: itemId,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    dragId.current = itemId;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const movePointerDrag = (event) => {
    const drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    drag.moved = true;
    event.preventDefault();
    setDraggingId(drag.id);
    const target = findPointerDropTarget(event.clientX, event.clientY, drag.id);
    setCurrentDropTarget(target);
  };

  const finishPointerDrag = (event) => {
    const drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const target = drag.moved
      ? findPointerDropTarget(event.clientX, event.clientY, drag.id) || dropTargetRef.current
      : null;
    if (drag.moved) {
      event.preventDefault();
      suppressClickRef.current = true;
      window.setTimeout(() => { suppressClickRef.current = false; }, 0);
      if (target) reorder(drag.id, target.id, target.position, { resetPlacement: true });
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    pointerDragRef.current = null;
    clearDragState();
  };

  const toggleRecentSessionsCollapsed = () => {
    setRecentSessionsCollapsed(prev => {
      const next = !prev;
      try {
        window.localStorage?.setItem(RECENT_SESSIONS_COLLAPSED_KEY, next ? '1' : '0');
      } catch (_err) {
        // Local storage is optional; the in-memory toggle still works.
      }
      return next;
    });
  };

  // keep active item visible
  useEffect(() => {
    const el = itemRefs.current[activeId];
    const rail = railRef.current;
    if (!el || !rail) return;
    const top = el.offsetTop;
    const bot = top + el.offsetHeight;
    const vTop = rail.scrollTop;
    const vBot = vTop + rail.clientHeight;
    if (top < vTop + 16) {
      smoothScrollTo(rail, top - 16);
    } else if (bot > vBot - 16) {
      smoothScrollTo(rail, bot - rail.clientHeight + 24);
    }
  }, [activeId]);

  return (
    <div className="col left">
      <div className="col-hd">
        <h2>자료</h2>
        <span className="count">{items.length}</span>
        <div className="spacer" />
        <button className="icon-btn" title="전체를 2단계 AI 변환" data-tooltip="모든 자료를 2단계 AI 변환으로 지정" onClick={() => bulkApply('s2')} disabled={!items.length}>{Icon.aiBatch}</button>
        <button
          className="icon-btn"
          title={canAddDummy ? '더미 추가' : '실제 세션 또는 대기열이 있을 때는 더미를 추가하지 않습니다'}
          data-tooltip={canAddDummy ? '테스트용 더미 자료 추가' : '실제 세션 또는 대기열이 있을 때는 더미를 추가하지 않습니다'}
          onClick={addMockSample}
          disabled={!canAddDummy}
        >{Icon.wand}</button>
        <button className="icon-btn" title="파일 추가" data-tooltip="PDF, 이미지, 한글 파일 추가" onClick={addSample}>{Icon.upload}</button>
      </div>

      <div className="items" ref={railRef}>
        <div
          className={`drop-zone ${hasSessionItems ? 'is-compact' : ''} ${dropZoneActive ? 'is-active' : ''}`}
          onClick={addSample}
          onDragEnter={e => {
            if (!e.dataTransfer?.types?.includes('Files')) return;
            e.preventDefault();
            setDropZoneActive(true);
          }}
          onDragOver={e => {
            if (!e.dataTransfer?.types?.includes('Files')) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            setDropZoneActive(true);
          }}
          onDragLeave={e => {
            // only clear when truly leaving (relatedTarget outside the zone)
            if (e.currentTarget.contains(e.relatedTarget)) return;
            setDropZoneActive(false);
          }}
          onDrop={e => {
            e.preventDefault();
            setDropZoneActive(false);
            const files = Array.from(e.dataTransfer?.files || []);
            if (files.length && handleFiles) handleFiles(files);
          }}
        >
          {Icon.upload}
          <strong style={hasSessionItems ? null : {marginTop:6}}>
            {hasSessionItems ? '파일 추가' : '이미지·PDF·HWP 대기열에 추가'}
          </strong>
          <small>{hasSessionItems ? 'PNG 등록 / AI 인식' : '페이지 PNG로 바로 만들거나 문항을 AI 인식합니다'}</small>
        </div>

        {!!recentSessions?.length && (
          <div className={`session-history-card ${recentSessionsCollapsed ? 'is-collapsed' : ''}`}>
            <div className="source-queue-head session-history-head">
              <strong>최근 작업</strong>
              <span>{recentSessions.length}개</span>
              <div className="spacer" />
              <button
                className="icon-btn"
                type="button"
                title={recentSessionsCollapsed ? '최근 작업 펼치기' : '최근 작업 접기'}
                data-tooltip={recentSessionsCollapsed ? '최근 작업 목록 펼치기' : '최근 작업 목록 접기'}
                aria-expanded={!recentSessionsCollapsed}
                aria-controls="recent-session-history-list"
                onClick={toggleRecentSessionsCollapsed}
              >
                {recentSessionsCollapsed ? Icon.arrowDown : Icon.arrowUp}
              </button>
            </div>
            {!recentSessionsCollapsed && (
              <div className="session-history-list" id="recent-session-history-list">
                {recentSessions.slice(0, 5).map(entry => {
                  const publish = normalizePublishSummary(entry.publishSummary || entry.publish_summary, entry);
                  return (
                    <div className="session-history-row" key={entry.id}>
                      <div className="session-history-main" title={entry.outputDir || entry.sessionName}>
                        <div className="name">{entry.sessionName || '이름 없는 작업'}</div>
                        <div className="meta">
                          {formatProblemCount({
                            core: entry.coreProblemCount,
                            supplemental: entry.supplementalItemCount,
                          })}
                          {entry.updatedAt ? ` · ${formatPublishTime(entry.updatedAt)}` : ''}
                          {publish?.recordCountLabel ? ` · ${publish.recordCountLabel}` : ''}
                          {publish?.classinReviewStatusLabel ? ` · ${publish.classinReviewStatusLabel}` : ''}
                        </div>
                      </div>
                      <div className="session-history-actions">
                        {publish && (
                          <>
                            <button
                              className="icon-btn"
                              type="button"
                              disabled={!publish.canDownload}
                              onClick={() => downloadPublishSummary(publish)}
                              title={publish.edbFileExists === false ? '최근 제작본 파일이 없습니다' : '최근 제작본 다운로드'}
                            >
                              {Icon.download}
                            </button>
                            <button
                              className="icon-btn"
                              type="button"
                              disabled={!publish.canOpenEdbFile}
                              onClick={() => openPublishedEdb(publish)}
                              title={publish.edbFileExists === false ? '최근 제작본 파일이 없습니다' : 'ClassIn 또는 기본 앱으로 열기'}
                            >
                              {Icon.board}
                            </button>
                            <button
                              className="icon-btn"
                              type="button"
                              disabled={!publish.canOpenOutputDir}
                              onClick={() => openOutputFolder(publish.outputDir)}
                              title={publish.outputDirExists === false ? '최근 작업 출력 폴더가 없습니다' : '최근 작업 출력 폴더 열기'}
                            >
                              {Icon.folder}
                            </button>
                            <button
                              className="icon-btn"
                              type="button"
                              disabled={!publish.canOpenClassinHandoff}
                              onClick={() => openClassinHandoff(publish)}
                              title={publish.canOpenClassinHandoff ? 'ClassIn 검수 파일 열기' : 'ClassIn 검수 파일이 없습니다'}
                            >
                              {Icon.check}
                            </button>
                          </>
                        )}
                        <button
                          className="btn"
                          type="button"
                          disabled={restoringSessionId === entry.id}
                          onClick={() => onRestoreRecentSession?.(entry.id)}
                          title="이 작업을 다시 엽니다"
                        >
                          {Icon.refresh}<span>{restoringSessionId === entry.id ? '여는 중' : '열기'}</span>
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {!!pendingFiles?.length && (
          <div className="source-queue-card">
            <div className="source-queue-head">
              <strong>업로드 대기열</strong>
              <span>{pendingFiles.length}개</span>
              <div className="spacer" />
              <button className="icon-btn" title="대기열 비우기" onClick={clearPendingFiles} disabled={queueBusy}>
                {Icon.trash}
              </button>
            </div>
            <div className="source-queue-list">
              {pendingFiles.map((file, index) => {
                const key = fileQueueKey(file);
                const selected = selectedPendingFileKey === key;
                return (
                <div
                  className={`source-queue-row ${selected ? 'is-selected' : ''}`}
                  key={key}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selected ? 'true' : 'false'}
                  aria-label={`${file.name || '이름 없는 파일'} 미리보기`}
                  onClick={() => onSelectPendingFile?.(key)}
                  onKeyDown={e => {
                    if (e.key !== 'Enter' && e.key !== ' ') return;
                    e.preventDefault();
                    onSelectPendingFile?.(key);
                  }}
                >
                  <span className="idx">{String(index + 1).padStart(2, '0')}</span>
                  <div className="file">
                    <div className="name">{file.name || '이름 없는 파일'}</div>
                    <div className="meta">{sourceFileKindLabel(file)} · {formatBytes(file.size)}</div>
                  </div>
                  <button
                    className="icon-btn queue-row-action"
                    title="이 파일을 문제 파싱 없이 페이지 PNG로 등록"
                    aria-label="이 파일 페이지 PNG 등록"
                    onClick={e => { e.stopPropagation(); processQueuedFiles('register', key); }}
                    disabled={queueBusy}
                  >
                    {Icon.pagePng}
                  </button>
                  <button
                    className="icon-btn queue-row-action"
                    title="이 파일을 인식 없이 열고 직접 문제 영역을 그리기"
                    aria-label="이 파일 수동 쪼개기"
                    onClick={e => { e.stopPropagation(); processQueuedFiles('manual-split', key); }}
                    disabled={queueBusy}
                  >
                    {Icon.pen}
                  </button>
                  <button
                    className="icon-btn queue-row-action"
                    title="이 파일만 문항 AI 인식"
                    aria-label="이 파일 문항 AI 인식"
                    onClick={e => { e.stopPropagation(); processQueuedFiles('recognize', key); }}
                    disabled={queueBusy}
                  >
                    {Icon.aiBatch}
                  </button>
                  <button
                    className="icon-btn"
                    title="대기열에서 제거"
                    onClick={e => { e.stopPropagation(); removePendingFile(key); }}
                    disabled={queueBusy}
                  >
                    {Icon.trash}
                  </button>
                </div>
              );})}
            </div>
            <div className="source-queue-actions">
              <button
                className="btn queue-action-card queue-action-page"
                type="button"
                title="대기열 전체를 문제 파싱 없이 페이지 PNG 자료로 등록"
                onClick={() => processQueuedFiles('register')}
                disabled={queueBusy}
              >
                <span className="queue-action-icon">{Icon.pagePng}</span>
                <span className="queue-action-copy">
                  <strong>페이지 PNG</strong>
                  <small>문제 파싱 없음</small>
                </span>
              </button>
              <button
                className="btn primary queue-action-card queue-action-ai"
                type="button"
                title="대기열 전체를 문제별로 문항 AI 인식"
                onClick={() => processQueuedFiles('recognize')}
                disabled={queueBusy}
              >
                <span className="queue-action-icon">{Icon.aiBatch}</span>
                <span className="queue-action-copy">
                  <strong>문항 AI 인식</strong>
                  <small>문제별 자동 분리</small>
                </span>
              </button>
              <button
                className="btn queue-action-card queue-action-manual"
                type="button"
                title="대기열 전체를 인식 없이 열고 수동으로 문제 영역을 나누기"
                onClick={() => processQueuedFiles('manual-split')}
                disabled={queueBusy}
              >
                <span className="queue-action-icon">{Icon.pen}</span>
                <span className="queue-action-copy">
                  <strong>수동 쪼개기</strong>
                  <small>인식 없이 직접 분할</small>
                </span>
              </button>
              {!aiAvailable && (
                <div className="source-queue-note full">
                  Gemini 키 없음 · 기본 인식으로 실행
                </div>
              )}
            </div>

          </div>
        )}

        {items.map((it, i) => {
          const dropPosition = dropTarget?.id === it.id ? dropTarget.position : null;
          const isDownloading = downloadingItemId === it.id;
          const canDownloadItem = Boolean(it.chalkUrl || it.imageUrl);
          const downloadTitle = isDownloading
            ? '다운로드 준비 중'
            : canDownloadItem
              ? '이 자료 PNG 다운로드'
              : '다운로드할 이미지가 아직 없습니다';
          return (
          <div
            key={it.id}
            ref={el => {
              if (el) itemRefs.current[it.id] = el;
              else delete itemRefs.current[it.id];
            }}
            className={`item ${activeId === it.id ? 'active' : ''} ${draggingId === it.id ? 'dragging' : ''} ${dropPosition === 'before' ? 'drop-before' : ''} ${dropPosition === 'after' ? 'drop-after' : ''}`}
            data-item-id={it.id}
            onClick={() => {
              if (suppressClickRef.current) return;
              setActive(it.id);
            }}
            onPointerDown={e => startPointerDrag(e, it.id)}
            onPointerMove={movePointerDrag}
            onPointerUp={finishPointerDrag}
            onPointerCancel={finishPointerDrag}
            onDragStart={e => {
              dragId.current = it.id;
              setDraggingId(it.id);
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', it.id);
            }}
            onDragEnter={e => updateDropTarget(e, it.id)}
            onDragOver={e => updateDropTarget(e, it.id)}
            onDragLeave={e => {
              if (e.currentTarget.contains(e.relatedTarget)) return;
              if (dropTargetRef.current?.id === it.id) setCurrentDropTarget(null);
            }}
            onDrop={e => {
              e.preventDefault();
              const sourceId = e.dataTransfer.getData('text/plain') || dragId.current;
              const position = dropPositionFromClientY(e.currentTarget.getBoundingClientRect(), e.clientY);
              if (sourceId && sourceId !== it.id) reorder(sourceId, it.id, position, { resetPlacement: true });
              clearDragState();
            }}
            onDragEnd={clearDragState}
          >
            <div className="grip" title="끌어 옮기기">
              <span className="idx">{String(i+1).padStart(2,'0')}</span>
            </div>
            <div className="thumb">
              <TileImage item={it} forceMode="raw" />
            </div>
            <div className="meta">
              <div className="name">
                <span
                  className={`status-dot ${reviewStatusClass(it.reviewStatus)}`}
                  title={it.statusReason || it.statusLabel}
                />
                {it.name}
              </div>
              <div className="sub">
                {it.step === 's1' && <span className="tag s1">1단계</span>}
                {it.step === 's2' && <span className="tag s2">AI</span>}
                {it.step === 's3' && <span className="tag s3">재구성</span>}
                {it.step === 'raw' && <span className="tag">대기</span>}
                <span className="source-label">{it.source}</span>
              </div>
            </div>
            <div className="actions">
              <button
                className="icon-btn item-download-action"
                type="button"
                title={downloadTitle}
                data-tooltip={downloadTitle}
                aria-label={`${it.name} PNG 다운로드`}
                disabled={!canDownloadItem || isDownloading}
                onClick={e => {
                  e.stopPropagation();
                  if (!canDownloadItem || isDownloading) return;
                  onDownloadItemImage?.(it);
                }}
              >
                {Icon.download}
              </button>
              <button className="icon-btn" title="삭제" onClick={e => { e.stopPropagation(); removeItem(it.id); }}>
                {Icon.trash}
              </button>
            </div>
          </div>
        );})}
      </div>
    </div>
  );
}

// ─── CENTER: big scrollable board stage ──
function BoardStage({ items, activeId, setActive, boardColor, boardColumns, fileName, addSample, setPlacement, reorder }){
  const scrollRef = useRef(null);
  const contentRef = useRef(null);
  const tileRefs = useRef({});
  const syncLock = useRef(0);
  const positionDragRef = useRef(null);
  const suppressClickRef = useRef(null);
  const boardDropTargetRef = useRef(null);
  const autoScrollRef = useRef({ raf: null, clientY: null });
  const [positioningId, setPositioningId] = useState(null);
  const [boardDropTarget, setBoardDropTarget] = useState(null);
  const [dragMagnet, setDragMagnet] = useState(null);
  const [pageH, setPageH] = useState(400);
  const [contentW, setContentW] = useState(0);
  const columnCount = normalizeBoardColumns(boardColumns);

  // measure page (viewport) height
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setPageH(el.clientHeight || 400);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    const measure = () => setContentW(el.clientWidth || 0);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Compute board positions with row height shared by neighboring columns.
  const layout = useMemo(() => {
    const EPS = 0.001;
    const layoutItems = reflowItemsForBoardOrder(items, DEFAULT_SLOT_HEIGHT_PAGES, columnCount);
    const positions = layoutItems.map((it) => {
      const startPages = Math.max(0, it.startYPages || 0);
      const heightPages = itemHeightPages(it);
      const renderedHeightPages = itemRenderedHeightPages(it);
      const snappedNext = Math.max(startPages + renderedHeightPages, it.snappedNextStartYPages || 0);
      const top = startPages * pageH;
      const height = heightPages * pageH;
      const rowHeightPages = Math.max(0, snappedNext - startPages);
      const rawMagnetColumnIndex = Number(it.placementMagnetColumnIndex ?? it.placement_magnet_column_index);
      const displayColumnIndex = Number.isFinite(rawMagnetColumnIndex)
        ? Math.max(0, Math.min((it.boardColumnCount || columnCount) - 1, Math.round(rawMagnetColumnIndex)))
        : (it.boardColumnIndex || 0);
      return {
        top,
        height,
        page: Math.floor(top / pageH) + 1,
        spans: Math.max(1, Math.ceil(height / pageH)),
        startPages,
        heightPages,
        renderedHeightPages,
        snappedNext,
        rowHeightPages,
        columnIndex: displayColumnIndex,
        columnCount: it.boardColumnCount || columnCount,
        xRatio: normalizePlacementXRatio(it.placementXRatio),
        yRatio: normalizePlacementYRatio(it.placementYRatio),
      };
    });
    let maxBottom = 0;
    positions.forEach((placement) => {
      maxBottom = Math.max(
        maxBottom,
        placement.snappedNext * pageH,
        placement.top + placement.renderedHeightPages * pageH
      );
    });
    const endTop = items.length === 0 ? 0 : Math.ceil(maxBottom / pageH - EPS) * pageH;
    const endH = pageH * 0.42;
    const totalH = endTop + endH;
    const totalPages = Math.max(1, Math.ceil(totalH / pageH));
    return { items: layoutItems, positions, endTop, endH, totalH, totalPages, usesPlacement: true };
  }, [items, pageH, columnCount]);

  const [scrollTop, setScrollTop] = useState(0);
  const currentPage = Math.min(layout.totalPages, Math.floor(scrollTop / pageH) + 1);

  // when activeId changes externally, scroll the board to it
  useEffect(() => {
    if (Date.now() - syncLock.current < 450) return;
    const container = scrollRef.current;
    const idx = items.findIndex(x => x.id === activeId);
    if (!container || idx < 0) return;
    const target = Math.max(0, layout.positions[idx].top - 8);
    if (Math.abs(container.scrollTop - target) < 6) return;
    smoothScrollTo(container, target);
  }, [activeId, layout]);

  const onScroll = () => {
    const c = scrollRef.current;
    if (!c) return;
    setScrollTop(c.scrollTop);
    // find item whose start is closest to (just below) scrollTop
    let nearestIdx = 0;
    let nearestDist = Infinity;
    layout.positions.forEach((p, idx) => {
      const dist = Math.abs(p.top - c.scrollTop - 24);
      if (dist < nearestDist){ nearestDist = dist; nearestIdx = idx; }
    });
    const id = items[nearestIdx]?.id;
    if (id && id !== activeId){
      syncLock.current = Date.now();
      setActive(id);
    }
  };

  const onTileClick = (id) => {
    if (suppressClickRef.current === id) return;
    syncLock.current = Date.now();
    setActive(id);
  };

  const fitActiveTileInView = () => {
    const container = scrollRef.current;
    const idx = items.findIndex(x => x.id === activeId);
    if (!container || idx < 0) return;
    const target = Math.max(0, layout.positions[idx].top - 8);
    syncLock.current = Date.now();
    smoothScrollTo(container, target);
  };

  const setCurrentBoardDropTarget = (target) => {
    boardDropTargetRef.current = target;
    setBoardDropTarget(prev => (
      prev?.id === target?.id && prev?.position === target?.position ? prev : target
    ));
  };

  const findBoardDropTarget = (clientY, sourceId) => {
    const content = contentRef.current;
    if (!content || !items.length) return null;
    const rect = content.getBoundingClientRect();
    const contentY = clientY - rect.top;
    const candidates = layout.positions
      .map((placement, index) => ({ placement, item: items[index] }))
      .filter(row => row.item?.id && row.item.id !== sourceId);
    if (!candidates.length) return null;
    for (const row of candidates) {
      const midpoint = row.placement.top + (row.placement.height / 2);
      if (contentY < midpoint) {
        return { id: row.item.id, position: 'before' };
      }
    }
    const last = candidates[candidates.length - 1];
    return last?.item?.id ? { id: last.item.id, position: 'after' } : null;
  };

  const updateBoardDropTarget = (clientY, sourceId, force = false) => {
    const drag = positionDragRef.current;
    if (!force && drag && Math.abs(clientY - drag.startY) < BOARD_DRAG_REORDER_THRESHOLD_PX) {
      setCurrentBoardDropTarget(null);
      return null;
    }
    const target = findBoardDropTarget(clientY, sourceId);
    setCurrentBoardDropTarget(target);
    return target;
  };

  const stopBoardAutoScroll = () => {
    if (autoScrollRef.current.raf) {
      cancelAnimationFrame(autoScrollRef.current.raf);
    }
    autoScrollRef.current = { raf: null, clientY: null };
  };

  const stepBoardAutoScroll = () => {
    const drag = positionDragRef.current;
    const scroll = scrollRef.current;
    const clientY = autoScrollRef.current.clientY;
    if (!drag || !scroll || clientY == null) {
      stopBoardAutoScroll();
      return;
    }
    const rect = scroll.getBoundingClientRect();
    let delta = 0;
    if (clientY < rect.top + BOARD_DRAG_AUTOSCROLL_EDGE_PX) {
      const strength = 1 - Math.max(0, clientY - rect.top) / BOARD_DRAG_AUTOSCROLL_EDGE_PX;
      delta = -Math.ceil(strength * BOARD_DRAG_AUTOSCROLL_MAX_PX);
    } else if (clientY > rect.bottom - BOARD_DRAG_AUTOSCROLL_EDGE_PX) {
      const strength = 1 - Math.max(0, rect.bottom - clientY) / BOARD_DRAG_AUTOSCROLL_EDGE_PX;
      delta = Math.ceil(strength * BOARD_DRAG_AUTOSCROLL_MAX_PX);
    }
    if (delta) {
      scroll.scrollTop = Math.max(0, Math.min(scroll.scrollHeight - scroll.clientHeight, scroll.scrollTop + delta));
      setScrollTop(scroll.scrollTop);
      updateBoardDropTarget(clientY, drag.id);
      autoScrollRef.current.raf = requestAnimationFrame(stepBoardAutoScroll);
    } else {
      autoScrollRef.current.raf = null;
    }
  };

  const applyBoardAutoScroll = (clientY) => {
    autoScrollRef.current.clientY = clientY;
    if (!autoScrollRef.current.raf) {
      autoScrollRef.current.raf = requestAnimationFrame(stepBoardAutoScroll);
    }
  };

  useEffect(() => () => stopBoardAutoScroll(), []);

  const beginPositionDrag = (evt, item, placement) => {
    if (evt.button !== 0 || !contentRef.current) return;
    const contentRect = contentRef.current.getBoundingClientRect();
    const tileRect = evt.currentTarget.getBoundingClientRect();
    const maxLeft = Math.max(1, contentRect.width - tileRect.width);
    const maxTopOffset = Math.max(0, (placement.snappedNext * pageH) - placement.top - tileRect.height);
    const startXRatio = normalizePlacementXRatio(placement.xRatio);
    const startYRatio = normalizePlacementYRatio(placement.yRatio);
    positionDragRef.current = {
      id: item.id,
      pointerId: evt.pointerId,
      startX: evt.clientX,
      startY: evt.clientY,
      startLeft: startXRatio * maxLeft,
      startTopOffset: startYRatio * maxTopOffset,
      maxLeft,
      maxTopOffset,
      tileWidth: tileRect.width,
      columnCount: placement.columnCount || columnCount,
      lastXRatio: startXRatio,
      lastYRatio: startYRatio,
      moved: false,
    };
    setPositioningId(item.id);
    setCurrentBoardDropTarget(null);
    syncLock.current = Date.now();
    setActive(item.id);
    evt.currentTarget.setPointerCapture?.(evt.pointerId);
  };

  const movePositionDrag = (evt) => {
    const drag = positionDragRef.current;
    if (!drag || drag.pointerId !== evt.pointerId) return;
    const dx = evt.clientX - drag.startX;
    const dy = evt.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    evt.preventDefault();
    applyBoardAutoScroll(evt.clientY);
    updateBoardDropTarget(evt.clientY, drag.id);
    const nextLeft = Math.max(0, Math.min(drag.maxLeft, drag.startLeft + dx));
    const nextTopOffset = Math.max(0, Math.min(drag.maxTopOffset, drag.startTopOffset + dy));
    const rawXRatio = drag.maxLeft > 0 ? nextLeft / drag.maxLeft : DEFAULT_PLACEMENT_X_RATIO;
    const magnet = nearestBoardColumnMagnet(rawXRatio, drag.columnCount, contentW, drag.tileWidth);
    const nextXRatio = magnet.ratio;
    const nextYRatio = drag.maxTopOffset > 0 ? nextTopOffset / drag.maxTopOffset : DEFAULT_PLACEMENT_Y_RATIO;
    setDragMagnet(magnet.snapped ? {
      index: magnet.index,
      ratio: magnet.ratio,
      distancePx: magnet.distancePx,
    } : null);
    if (
      Math.abs(nextXRatio - drag.lastXRatio) < 0.002 &&
      Math.abs(nextYRatio - drag.lastYRatio) < 0.002
    ) {
      return;
    }
    drag.lastXRatio = nextXRatio;
    drag.lastYRatio = nextYRatio;
    setPlacement?.(drag.id, {
      xRatio: nextXRatio,
      yRatio: nextYRatio,
      magnetColumnIndex: magnet.snapped ? magnet.index : null,
    });
  };

  const endPositionDrag = (evt) => {
    const drag = positionDragRef.current;
    if (!drag || drag.pointerId !== evt.pointerId) return;
    const canReorder = drag.moved && Math.abs(evt.clientY - drag.startY) >= BOARD_DRAG_REORDER_THRESHOLD_PX;
    const target = canReorder
      ? findBoardDropTarget(evt.clientY, drag.id) || boardDropTargetRef.current
      : null;
    if (drag.moved) {
      suppressClickRef.current = drag.id;
      window.setTimeout(() => {
        if (suppressClickRef.current === drag.id) suppressClickRef.current = null;
      }, 0);
      if (target && target.id && target.id !== drag.id) {
        reorder?.(drag.id, target.id, target.position, { resetPlacement: false });
      }
    }
    evt.currentTarget.releasePointerCapture?.(evt.pointerId);
    positionDragRef.current = null;
    setPositioningId(null);
    setCurrentBoardDropTarget(null);
    setDragMagnet(null);
    stopBoardAutoScroll();
  };

  const processedCount = items.filter(i => i.step !== 'raw').length;
  const aiCount = items.filter(i => i.step === 's2').length;
  const reconstructCount = items.filter(i => i.step === 's3').length;
  const rawCount = items.filter(i => i.step === 'raw').length;
  const s1Count = items.filter(i => i.step === 's1').length;
  const leftZonePercent = `${FIXED_LEFT_ZONE_RATIO * 100}%`;
  const activeIndex = items.findIndex(x => x.id === activeId);
  const activePlacement = layout.positions[activeIndex] || null;
  const activeLayoutItem = layout.items[activeIndex] || null;
  const activeContinuousFlow = isContinuousPlacementItem(activeLayoutItem);
  const columnGuideWidth = contentW > 0 ? Math.max(120, (contentW * FIXED_LEFT_ZONE_RATIO) - 10) : 0;
  const columnGuides = contentW > 0
    ? boardColumnRatios(columnCount).map((ratio, index) => ({
        index,
        ratio,
        left: ratio * Math.max(0, contentW - columnGuideWidth),
        width: columnGuideWidth,
      }))
    : [];

  // page-boundary divider lines (between page N and N+1)
  const dividers = [];
  for (let i = 1; i < Math.min(layout.totalPages, 200); i++){
    dividers.push(i * pageH);
  }

  return (
    <div className="col center">
      <div className="stage">
        <div className="stage-toolbar">
          <span className="name">실시간 칠판 미리보기</span>
          <span className="pill"><span className="dotc" /> {fileName.length > 32 ? fileName.slice(0,30)+'…' : fileName}</span>
          <div className="spacer" />
          <button
            className="btn ghost stage-fit-btn"
            type="button"
            title="화면 맞춤"
            data-tooltip="현재 칠판을 화면 안에 맞춰 보기"
            disabled={!items.length}
            onClick={fitActiveTileInView}
          >화면 맞춤</button>
        </div>

        <div className="stage-wrap">
          <div className="stage-board" style={{ background: boardColor }}>

            <div className="stage-scroll" ref={scrollRef} onScroll={onScroll}>
              <div
                className={`stage-content ${columnCount > 1 ? 'has-column-guides' : ''} ${positioningId ? 'is-positioning' : ''}`}
                ref={contentRef}
                style={{ height: layout.totalH, '--left-zone-width': leftZonePercent }}
              >
                {/* page boundary dividers — scroll with content */}
                {dividers.map((top, i) => (
                  <div key={i} className="page-divider" style={{ top }}>
                    <span className="label">— {i + 2} 페이지 —</span>
                  </div>
                ))}

                {columnGuides.map(guide => (
                  <div
                    key={`column-guide-${guide.index}`}
                    className={`stage-column-guide ${dragMagnet?.index === guide.index ? 'active' : ''}`}
                    style={{ left: guide.left, width: guide.width }}
                    aria-hidden="true"
                  >
                    <span>칸 {guide.index + 1}</span>
                  </div>
                ))}

                {layout.items.map((it, i) => {
                  const p = layout.positions[i];
                  if (!p) return null;
                  const tileWidth = contentW > 0
                    ? Math.max(120, (contentW * FIXED_LEFT_ZONE_RATIO) - 10)
                    : null;
                  const maxScale = tileWidth
                    ? Math.max(
                        PLACEMENT_SCALE_MIN,
                        Math.min(
                          PLACEMENT_SCALE_MAX,
                          contentW / Math.max(tileWidth, 1),
                          ((p.snappedNext * pageH) - p.top) / Math.max(p.height, 1)
                        )
                      )
                    : PLACEMENT_SCALE_MAX;
                  const scaleRatio = normalizePlacementScaleRatio(it.placementScaleRatio, maxScale);
                  const scaledWidth = tileWidth ? tileWidth * scaleRatio : null;
                  const scaledHeight = p.height * scaleRatio;
                  const maxLeft = scaledWidth ? Math.max(0, contentW - scaledWidth) : 0;
                  const maxTopOffset = Math.max(0, (p.snappedNext * pageH) - p.top - scaledHeight);
                  const xRatio = normalizePlacementXRatio(p.xRatio);
                  const yRatio = normalizePlacementYRatio(p.yRatio);
                  const dropPosition = boardDropTarget?.id === it.id ? boardDropTarget.position : null;
                  const tileStyle = {
                    top: p.top + (yRatio * maxTopOffset),
                    height: scaledHeight,
                    ...(scaledWidth ? { left: xRatio * maxLeft, width: scaledWidth } : null),
                  };
                  return (
                    <button
                      key={it.id}
                      ref={el => { tileRefs.current[it.id] = el; }}
                      className={`stage-tile ${activeId === it.id ? 'active' : ''} ${it.step === 's1' ? 'paper' : ''} ${positioningId === it.id ? 'positioning' : ''} ${dropPosition === 'before' ? 'drop-before' : ''} ${dropPosition === 'after' ? 'drop-after' : ''}`}
                      onClick={() => onTileClick(it.id)}
                      title={it.name}
                      style={tileStyle}
                      onPointerDown={e => beginPositionDrag(e, it, p)}
                      onPointerMove={movePositionDrag}
                      onPointerUp={endPositionDrag}
                      onPointerCancel={endPositionDrag}
                    >
                      <div className="tile-hd">
                        <span className="n">{String(i+1).padStart(2,'0')}</span>
                        <span className="nm">{it.name}</span>
                        {p.spans > 1 && (
                          <span className="span-mark">{p.page}–{p.page + p.spans - 1}p</span>
                        )}
                        {p.columnCount > 1 && (
                          <span className="column-mark">칸 {p.columnIndex + 1}</span>
                        )}
                        {p.rowHeightPages > p.renderedHeightPages + 0.05 && (
                          <span className="row-mark">{p.rowHeightPages.toFixed(1)}p</span>
                        )}
                        <span className={`step-mark ${it.step}`}>
                          {it.step === 's1' ? '1' : it.step === 's2' ? 'AI' : it.step === 's3' ? 'HQ' : '··'}
                        </span>
                      </div>
                      <div className="tile-art">
                        <TileImage item={it} />
                      </div>
                    </button>
                  );
                })}

                {/* end slot */}
                <div
                  className="stage-tile-end"
                  onClick={addSample}
                  style={{ top: layout.endTop, height: layout.endH }}
                >
                  <span className="plus">＋</span>
                  자료 추가
                  <small>{layout.totalPages > 50 ? `${layout.totalPages}p` : 'auto pages'}</small>
                </div>
              </div>
            </div>

            <div className="board-pageind">
              <span>{String(currentPage).padStart(2,'0')}</span>
              <span className="sep">/</span>
              <span className="total">{String(layout.totalPages).padStart(2,'0')}</span>
              <span style={{marginLeft:6, opacity:.6}}>p</span>
            </div>

            <div className="vignette" />
          </div>
        </div>

        <div className="stage-status">
          <span className="chip">{layout.usesPlacement ? 'Export 배치 기준' : '1문제 / 1.2페이지 · 자동 페이지 나눔'}</span>
          <span className="chip">
            <span className="pip" />
            {activeContinuousFlow ? '연속 이어붙임' : `한 줄 ${columnCount}개`}
          </span>
          {activePlacement && (
            <span className="chip">
              행 높이 {activePlacement.rowHeightPages.toFixed(1)}p
            </span>
          )}
          <span className="chip">
            <span style={{width:8, height:8, borderRadius:2, background:'#aa6516'}} />
            1단계 {s1Count}
          </span>
          <span className="chip">
            <span style={{width:8, height:8, borderRadius:2, background:'linear-gradient(135deg,#6d3df0,#2f6fed)'}} />
            AI {aiCount}
          </span>
          <span className="chip">
            <span style={{width:8, height:8, borderRadius:2, background:'linear-gradient(135deg,#10b981,#22d3ee)'}} />
            재구성 {reconstructCount}
          </span>
          {rawCount > 0 && (
            <span className="chip" style={{color:'var(--danger)', borderColor: 'rgba(213,72,72,.3)'}}>
              미처리 {rawCount}
            </span>
          )}
          <div className="spacer" />
          <span style={{fontFamily:'JetBrains Mono, monospace'}}>{processedCount}/{items.length} 처리됨 · {layout.totalPages}p</span>
        </div>
      </div>
    </div>
  );
}

function downloadPublishSummary(target){
  if (!target?.canDownload) return;
  const parts = Array.isArray(target.edbParts) && target.edbParts.length
    ? target.edbParts
    : [target];
  parts
    .filter(part => (part.edbFileUri || part.edb_file_uri) && (part.edbFileExists ?? part.edb_file_exists) !== false)
    .forEach((part, index) => {
      window.setTimeout(() => {
        const a = document.createElement('a');
        a.href = part.edbFileUri || part.edb_file_uri;
        a.download = part.edbFileName || part.edb_file_name || target.edbFileName || 'classin.edb';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }, index * 150);
    });
}

async function openPublishedEdb(target){
  const firstPart = Array.isArray(target?.edbParts)
    ? target.edbParts.find(part => (part.edbPath || part.edb_path) && (part.edbFileExists ?? part.edb_file_exists) !== false)
    : null;
  const edbPath = firstPart?.edbPath || firstPart?.edb_path || target?.edbPath;
  if (!target?.canOpenEdbFile || !edbPath) return;
  try {
    const resp = await fetch('/api/system/open-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: edbPath }),
    });
    const json = await readJsonResponse(resp, '파일 열기 실패').catch(() => ({}));
    if (!resp.ok || !json.ok) {
      console.warn('[board] open-file failed:', json.error || resp.status);
    }
  } catch (e) {
    console.warn('[board] open-file error:', e.message);
  }
}

function openClassinHandoff(target){
  const url = target?.classinHandoffMarkdownUri || target?.classinHandoffUri;
  if (!url) return;
  window.open(url, '_blank', 'noopener');
}

function PublishResultPanel({ session, visible, onClassinReviewComplete, onExportImages, exportingImages, canExportImages }){
  const summary = useMemo(() => visible ? sessionPublishSummary(session) : null, [session, visible]);
  const history = useMemo(() => visible ? sessionPublishHistory(session) : [], [session, visible]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (summary) setOpen(false);
  }, [summary?.edbFileName, summary?.publishedAt]);
  if (!summary) return null;
  return (
    <div className={`publish-result-panel ${open ? 'open' : 'is-collapsed'}`}>
      <button
        className="publish-result-head"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
        title={open ? '제작 결과 접기' : '제작 결과 펼치기'}
      >
        <div className="publish-result-title">
          <strong>제작 결과</strong>
          <span>{summary.statusLabel} · {summary.recordCountLabel || `${summary.recordCount || summary.recordCountActual}개 자료`}</span>
        </div>
        <span className={`publish-result-status ${summary.validated ? 'ok' : 'warn'}`}>
          {summary.validated ? '검증 완료' : '확인 필요'}
        </span>
        <span className="publish-result-state">{open ? '접기' : '펼치기'}</span>
      </button>
      {open && (
        <>
          <div className="publish-result-file" title={summary.edbPath || summary.edbFileName}>
            {summary.edbSplit ? `${summary.edbFileName} + ${summary.edbPartCount - 1} parts` : summary.edbFileName}
          </div>
          <div className="publish-result-metrics">
            <span>{summary.recordCountActual || summary.recordCount} records</span>
            {summary.edbSplit && <span>{summary.edbPartCount} EDB files</span>}
            {summary.pageCountHint > 0 && <span>{summary.pageCountHint}p hint</span>}
            {summary.outerSize > 0 && <span>{formatBytes(summary.outerSize)}</span>}
            {summary.classinHandoffStatusLabel && <span title="ClassIn 전달 상태">{summary.classinHandoffStatusLabel}</span>}
            {summary.classinPreflightStatusLabel && <span title="ClassIn 사전점검">{summary.classinPreflightStatusLabel}</span>}
            {summary.classinPreflightIssueSummaryLabel && <span title="ClassIn 사전점검 이슈">{summary.classinPreflightIssueSummaryLabel}</span>}
            {summary.passageGroupLabel && <span title="긴 지문/공통 지문 그룹">{summary.passageGroupLabel}</span>}
            {summary.passageReviewLabel && <span title="긴 지문 검수 큐">{summary.passageReviewLabel}</span>}
            {summary.passageReviewReasonLabel && <span title="긴 지문 검수 사유">{summary.passageReviewReasonLabel}</span>}
            {summary.sourceProblemOverlapLabel && <span title="원본 문제 영역 겹침">{summary.sourceProblemOverlapLabel}</span>}
            {summary.passageGroupSourceReuseLabel && <span title="지문 원본 중복">{summary.passageGroupSourceReuseLabel}</span>}
            {summary.layoutDiagnosticsLabel && <span title="긴 이미지 배치 진단">{summary.layoutDiagnosticsLabel}</span>}
            {summary.classinReviewStatusLabel && <span>{summary.classinReviewStatusLabel}</span>}
          </div>
          <div className="publish-result-actions">
            <button
              className="btn"
              type="button"
              onClick={() => downloadPublishSummary(summary)}
              disabled={!summary.canDownload}
              title={summary.edbFileExists === false ? 'EDB 파일이 없습니다' : 'EDB 다시 다운로드'}
            >
              {Icon.download}<span>{summary.edbFileExists === false ? '파일 없음' : '다운로드'}</span>
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => openPublishedEdb(summary)}
              disabled={!summary.canOpenEdbFile}
              title={summary.edbFileExists === false ? 'EDB 파일이 없습니다' : 'ClassIn 또는 기본 앱으로 열기'}
            >
              {Icon.board}<span>ClassIn 열기</span>
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => summary.outputDir && openOutputFolder(summary.outputDir)}
              disabled={!summary.canOpenOutputDir}
              title={summary.outputDirExists === false ? '출력 폴더가 없습니다' : '출력 폴더 열기'}
            >
              {Icon.folder}<span>{summary.outputDirExists === false ? '폴더 없음' : '폴더'}</span>
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => openClassinHandoff(summary)}
              disabled={!summary.canOpenClassinHandoff}
              title={summary.canOpenClassinHandoff ? 'ClassIn 검수 파일 열기' : 'ClassIn 검수 파일이 없습니다'}
            >
              {Icon.check}<span>ClassIn 검수</span>
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => onExportImages?.()}
              disabled={!canExportImages || exportingImages || !onExportImages}
              title={canExportImages ? '현재 선택 단계 기준 최종 PNG 묶음 다운로드' : '다운로드할 이미지가 없습니다'}
            >
              {Icon.download}<span>{exportingImages ? '준비 중' : 'PNG 묶음'}</span>
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => onClassinReviewComplete?.()}
              disabled={!summary.canMarkClassinReviewComplete || !onClassinReviewComplete}
              title={summary.classinReviewPassed ? '이미 ClassIn 검수를 완료했습니다' : 'ClassIn에서 확인한 결과를 완료로 저장'}
            >
              {Icon.check}<span>{summary.classinReviewPassed ? '검수 완료됨' : '검수 완료'}</span>
            </button>
          </div>
          {history.length > 1 && (
            <div className="publish-history">
              <div className="publish-history-title">최근 제작</div>
              {history.slice(0, 5).map((item, index) => (
                <div className="publish-history-row" key={`${item.edbPath || item.edbFileName}-${index}`}>
                  <div className="publish-history-main" title={item.edbPath || item.edbFileName}>
                    <strong>{index === 0 ? '최신' : `${index + 1}`}</strong>
                    <span>{item.edbFileName}</span>
                  </div>
                  <small>{formatPublishHistoryMeta(item)}{item.classinReviewStatusLabel ? ` · ${item.classinReviewStatusLabel}` : ''}</small>
                  <button
                    className="icon-btn"
                    type="button"
                    title="이 제작본 다운로드"
                    disabled={!item.canDownload}
                    onClick={() => downloadPublishSummary(item)}
                  >{Icon.download}</button>
                  <button
                    className="icon-btn"
                    type="button"
                    title="ClassIn 또는 기본 앱으로 열기"
                    disabled={!item.canOpenEdbFile}
                    onClick={() => openPublishedEdb(item)}
                  >{Icon.board}</button>
                  <button
                    className="icon-btn"
                    type="button"
                    title="ClassIn 검수 파일 열기"
                    disabled={!item.canOpenClassinHandoff}
                    onClick={() => openClassinHandoff(item)}
                  >{Icon.check}</button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── RIGHT: tabbed panel ──────────────────────────────────────────────────
function SidePanel({
  item, items, activeIndex,
  setStep, applyToAll, bulk, setBulk,
  setPlacement, mutateSession, mutating,
  boardColumns, setBoardColumns,
  boardColor, setBoardColor,
  accent, setAccent,
  onConfirm,
  userSettings, runtimeDiagnostics, onSaveGeminiKey,
  onSaveOpenAiKey, onEnhanceImage, imageEnhanceBusy,
  aiEnabled, setAiEnabled,
  inputIntent, setInputIntent,
  onRecognizeSession, canRecognizeSession,
  session, published,
  onClassinReviewComplete,
  onExportImages, exportingImages, canExportImages,
  updateInfo, updateBusy, onCheckUpdate, onOpenUpdate,
  view,
  pendingFile, pendingFileKey, processQueuedFiles, queueBusy, onPendingPreviewError,
}){
  const [tab, setTab] = useState('item');
  const [previewMode, setPreviewMode] = useState('raw'); // raw | chalk | compare
  const [compareX, setCompareX] = useState(50);
  const [keyDraft, setKeyDraft] = useState('');
  const [openAiKeyDraft, setOpenAiKeyDraft] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [showOpenAiKey, setShowOpenAiKey] = useState(false);
  const [hangulDetailsExpanded, setHangulDetailsExpanded] = useState(false);
  const [advancedSettingsOpen, setAdvancedSettingsOpen] = useState(false);
  const [cropPresetsOpen, setCropPresetsOpen] = useState(false);
  const [cropDraft, setCropDraft] = useState({ ...EMPTY_MANUAL_CROP });
  const dragging = useRef(false);
  const wrapRef = useRef(null);
  const cropControlRef = useRef(null);
  const hangulDiagnostics = runtimeDiagnostics?.hangul || null;
  const hangulStatusMeta = hangulRuntimeStatusMeta(hangulDiagnostics);
  const hangulToolRows = hangulRuntimeToolRows(hangulDiagnostics);
  const hangulDetailsOpen = !!hangulDiagnostics && hangulDiagnostics.status !== 'ready';

  useEffect(() => {
    const onMove = e => {
      if (!dragging.current || !wrapRef.current) return;
      const r = wrapRef.current.getBoundingClientRect();
      const x = ((e.clientX - r.left) / r.width) * 100;
      setCompareX(Math.max(6, Math.min(94, x)));
    };
    const onUp = () => { dragging.current = false; };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  useEffect(() => {
    if (hangulDiagnostics) setHangulDetailsExpanded(hangulDetailsOpen);
  }, [hangulDiagnostics?.status]);

  useEffect(() => {
    setCropDraft(item ? normalizeManualCrop(item.manualCrop) : { ...EMPTY_MANUAL_CROP });
  }, [
    item?.id,
    item?.manualCrop?.leftRatio,
    item?.manualCrop?.rightRatio,
    item?.manualCrop?.topRatio,
    item?.manualCrop?.bottomRatio,
  ]);

  useEffect(() => {
    if (view !== 'review') return;
    setAdvancedSettingsOpen(false);
    setCropPresetsOpen(false);
  }, [view, item?.id]);

  const itemPosLabel = item
    ? `${String(activeIndex+1).padStart(2,'0')} / ${String(items.length).padStart(2,'0')}`
    : pendingFile
      ? '대기'
      : '— / —';
  const maxScale = maxPlacementScaleRatio(item);
  const placementScale = item ? normalizePlacementScaleRatio(item.placementScaleRatio, maxScale) : DEFAULT_PLACEMENT_SCALE_RATIO;
  const placementScalePercent = Math.round(placementScale * 100);
  const maxScalePercent = Math.round(maxScale * 100);
  const scaleRangeProgress = item
    ? Math.round(
        ((placementScale - PLACEMENT_SCALE_MIN) / Math.max(0.01, maxScale - PLACEMENT_SCALE_MIN)) * 100
      )
    : 0;
  const scaleLimitBySlot = item && maxScale < PLACEMENT_SCALE_MAX - 0.001;
  const scaleLimitLabel = item
    ? scaleLimitBySlot
      ? `현재 칸 높이 기준 최대 ${maxScalePercent}%까지 확대됩니다`
      : `최대 ${maxScalePercent}%까지 확대됩니다`
    : '자료를 선택하면 확대 범위가 표시됩니다';
  const placementX = item ? normalizePlacementXRatio(item.placementXRatio) : DEFAULT_PLACEMENT_X_RATIO;
  const placementY = item ? normalizePlacementYRatio(item.placementYRatio) : DEFAULT_PLACEMENT_Y_RATIO;
  const hasVerticalRoom = verticalPlacementRoomPages(item, placementScale) > 0.001;
  const currentInputIntent = normalizeInputIntent(session?.inputIntent || session?.input_intent || inputIntent);
  const itemInputIntent = normalizeInputIntent(item?.inputIntent || currentInputIntent);
  const itemIntentMeta = inputIntentMeta(itemInputIntent);
  const showFitWidth = !!item && itemInputIntent === 'page-as-is';
  const canZoomOut = item && placementScale > PLACEMENT_SCALE_MIN + 0.001;
  const canZoomIn = item && placementScale < maxScale - 0.001;
  const canEnhanceCurrent = !!item && !!userSettings?.hasGeminiApiKey && !imageEnhanceBusy;
  const updateStatus = updateInfo?.channelStatus || 'unknown';
  const updateDownloadUrl = updateInfo?.downloadUrl || updateInfo?.latest?.downloadUrl || '';
  const updateStatusLabel = updateBusy
    ? '확인 중'
    : updateInfo?.updateAvailable
      ? '새 버전'
      : updateStatus === 'up_to_date'
        ? '최신'
        : updateStatus === 'manual_download'
          ? '수동'
          : updateStatus === 'not_configured'
            ? '미설정'
            : updateStatus === 'error'
              ? '오류'
              : updateStatus === 'invalid_feed'
                ? '피드 오류'
              : updateStatus === 'unsupported_platform'
                ? '미지원'
                : '확인 전';
  const updateStatusTone = updateInfo?.updateAvailable
    ? 'var(--accent)'
    : updateStatus === 'up_to_date'
      ? 'var(--ok)'
      : updateStatus === 'error' || updateStatus === 'invalid_feed'
        ? 'var(--danger)'
        : 'var(--muted)';
  const updateVersionLine = updateInfo?.currentVersion
    ? `현재 ${updateInfo.currentVersion}${updateInfo?.latest?.version ? ` · 최신 ${updateInfo.latest.version}` : ''}`
    : '버전 정보를 불러오지 않았습니다';
  const savedCrop = item ? normalizeManualCrop(item.manualCrop) : { ...EMPTY_MANUAL_CROP };
  const cropChanged = item && !manualCropEquals(cropDraft, savedCrop);
  const savedCropActive = manualCropIsActive(savedCrop);
  const draftCropActive = manualCropIsActive(cropDraft);
  const showItemConfirmBar = !!item && (bulk || item.step !== 'raw');
  const updatePlacement = (patch) => {
    if (!item) return;
    setPlacement?.(item.id, patch);
  };
  const nudgePlacement = (dx, dy) => {
    if (!item) return;
    updatePlacement({
      xRatio: placementX + dx,
      yRatio: placementY + dy,
    });
  };
  const nudgeScale = (delta) => {
    if (!item) return;
    updatePlacement({ scaleRatio: placementScale + delta });
  };
  const resetPlacement = () => {
    updatePlacement({
      xRatio: DEFAULT_PLACEMENT_X_RATIO,
      yRatio: DEFAULT_PLACEMENT_Y_RATIO,
    });
  };
  const resetScale = () => {
    updatePlacement({ scaleRatio: DEFAULT_PLACEMENT_SCALE_RATIO });
  };
  const fitPlacementWidth = () => {
    updatePlacement({
      xRatio: DEFAULT_PLACEMENT_X_RATIO,
      xEdited: false,
      yRatio: DEFAULT_PLACEMENT_Y_RATIO,
      scaleRatio: PLACEMENT_SCALE_MAX,
      fitWidth: true,
    });
  };
  const updateCropDraft = (key, value) => {
    setCropDraft(prev => normalizeManualCrop({ ...prev, [key]: value }));
  };
  const adjustCropDraft = (patch) => {
    setCropDraft(prev => normalizeManualCrop({
      ...prev,
      ...Object.fromEntries(
        Object.entries(patch).map(([key, delta]) => [key, (prev[key] || 0) + delta])
      ),
    }));
  };
  const focusManualCrop = () => {
    setAdvancedSettingsOpen(true);
    setPreviewMode('raw');
    window.requestAnimationFrame(() => {
      cropControlRef.current?.scrollIntoView({ block: 'center', inline: 'nearest' });
    });
  };
  const applyManualCrop = (nextCrop = cropDraft) => {
    if (!item || mutating) return;
    const normalized = normalizeManualCrop(nextCrop);
    setCropDraft(normalized);
    mutateSession?.('crop', { problemId: item.id, crop: normalized });
  };
  const resetManualCrop = () => {
    const reset = { ...EMPTY_MANUAL_CROP };
    setCropDraft(reset);
    if (savedCropActive) applyManualCrop(reset);
  };

  return (
    <div className={`col right ${view === 'review' ? 'review-context' : ''}`}>
      <div className="tab-bar">
        <button
          className={tab==='item' ? 'on' : ''}
          onClick={() => setTab('item')}
          title="선택 자료"
          data-tooltip="선택한 자료의 처리 방식과 세부 편집"
        >
          자료 <span className="badge">{itemPosLabel}</span>
        </button>
        <button
          className={tab==='board' ? 'on' : ''}
          onClick={() => setTab('board')}
          title="칠판 설정"
          data-tooltip="칠판 색상, 한 줄 자료 수, AI 인식 설정"
        >
          칠판
        </button>
      </div>

      <PublishResultPanel
        session={session}
        visible={published}
        onClassinReviewComplete={onClassinReviewComplete}
        onExportImages={onExportImages}
        exportingImages={exportingImages}
        canExportImages={canExportImages}
      />

      {tab === 'item' && (
        <>
          <div className="tab-body">
            {item ? (
              <>
                <div className="item-meta">
                  <div className="nm">
                    <div className="t">{item.name}</div>
                    <div className="s">
                      <span className={`status-badge ${reviewStatusClass(item.reviewStatus)}`}>{item.statusLabel}</span>
                      <span className={`intent-badge intent-${itemInputIntent}`} title={itemIntentMeta.description}>
                        {Icon[itemIntentMeta.icon] || Icon.scan}
                        {itemIntentMeta.badgeLabel || itemIntentMeta.label}
                      </span>
                      {item.source} · {item.type.toUpperCase()}
                    </div>
                  </div>
                  <div className="pos-tag">{itemPosLabel}</div>
                </div>

                <div className="item-preview" ref={wrapRef}>
                  <div className="ptab">
                    <button className={previewMode==='raw' ? 'on' : ''} onClick={() => setPreviewMode('raw')}>원본</button>
                    <button className={previewMode==='chalk' ? 'on' : ''} onClick={() => setPreviewMode('chalk')}>칠판용</button>
                    <button className={previewMode==='compare' ? 'on' : ''} onClick={() => setPreviewMode('compare')}>비교</button>
                  </div>

                  {previewMode === 'compare' ? (
                    <div className="canvas-mini" style={{ background: 'white' }}>
                      <div className="inner"><TileImage item={item} forceMode="raw" /></div>
                      <div style={{
                        position: 'absolute', inset: 0,
                        clipPath: `inset(0 0 0 ${compareX}%)`,
                        background: boardColor,
                      }}>
                        <div className="inner"><TileImage item={item} forceMode="chalk" /></div>
                      </div>
                      <div className="compare-handle"
                           style={{ left: `${compareX}%` }}
                           onMouseDown={() => { dragging.current = true; }}/>
                    </div>
                  ) : (
                    <div className={`canvas-mini ${previewMode==='chalk' ? 'board' : ''}`}
                         style={previewMode==='chalk' ? { background: boardColor } : null}>
                      <div className="inner"><TileImage item={item} forceMode={previewMode==='chalk' ? 'chalk' : 'raw'} /></div>
                    </div>
                  )}

                </div>

                <div className="panel-section-hd">
                  처리 방식 선택 <span className="line" />
                </div>

                <div className="steps">
                  <button
                    className={`step-row ${item.step === 's1' ? 'on' : ''}`}
                    data-tooltip="원본 이미지를 빠르게 그대로 배치"
                    onClick={() => setStep(item.id, 's1')}
                  >
                    <span className="radio" />
                    <div>
                      <div className="t">1단계 · 그대로 붙이기</div>
                      <div className="d">원본 색·여백 유지</div>
                    </div>
                    <div className="meta-r">즉시<strong>~ 0.3s</strong></div>
                  </button>
                  <button
                    className={`step-row ${item.step === 's2' ? 'on' : ''}`}
                    data-tooltip="AI로 배경을 정리하고 칠판 색상에 맞게 변환"
                    onClick={() => setStep(item.id, 's2')}
                  >
                    <span className="radio" />
                    <div>
                      <div className="t">2단계 · AI 변환 <span className="ai-badge">AI</span></div>
                      <div className="d">배경 제거 · 칠판 색상 자동 배치</div>
                    </div>
                    <div className="meta-r">자동<strong>~ 4s</strong></div>
                  </button>
                  <button
                    className={`step-row ${item.step === 's3' ? 'on' : ''}`}
                    data-tooltip="업스케일 후 투명 PNG로 고화질 재구성"
                    onClick={() => setStep(item.id, 's3')}
                  >
                    <span className="radio" />
                    <div>
                      <div className="t">3단계 · 고화질 <span className="ai-badge">HQ</span></div>
                      <div className="d">업스케일 · 투명 PNG 재구성</div>
                    </div>
                    <div className="meta-r">제작시<strong>~ 8s</strong></div>
                  </button>
                </div>

                <div className={`detail-settings ${advancedSettingsOpen ? 'open' : ''}`}>
                  <button
                    className="detail-settings-toggle"
                    type="button"
                    data-tooltip="위치 이동, 여백 자르기, 확대, 업스케일 상세 설정"
                    aria-expanded={advancedSettingsOpen}
                    onClick={() => setAdvancedSettingsOpen(open => !open)}
                  >
                    <span>
                      <strong>상세 설정</strong>
                      <small>이동 · 자르기 · 확대 · 업스케일</small>
                    </span>
                    <span className="detail-settings-state">
                      {savedCropActive && <em>crop</em>}
                      {Math.abs(placementScale - DEFAULT_PLACEMENT_SCALE_RATIO) >= 0.001 && <em>{placementScalePercent}%</em>}
                      <i aria-hidden="true">{advancedSettingsOpen ? '접기' : '펼치기'}</i>
                    </span>
                  </button>

                  {advancedSettingsOpen && (
                    <div className="detail-settings-body">
                      <div className="panel-section-hd">
                        빠른 편집 <span className="line" />
                      </div>

                      <div className="ptools detail-tools">
                        <button className="icon-btn" title="회전">{Icon.rotate}</button>
                        <button
                          className={`icon-btn ${savedCropActive ? 'on' : ''}`}
                          type="button"
                          title="상하좌우 여백 자르기"
                          onClick={focusManualCrop}
                        >{Icon.crop}</button>
                        <button
                          className="icon-btn"
                          title="축소"
                          disabled={!canZoomOut}
                          onClick={() => nudgeScale(-PLACEMENT_SCALE_STEP)}
                        >{Icon.zoomOut}</button>
                        <button
                          className="icon-btn"
                          title="확대"
                          disabled={!canZoomIn}
                          onClick={() => nudgeScale(PLACEMENT_SCALE_STEP)}
                        >{Icon.zoomIn}</button>
                        {showFitWidth && (
                          <button
                            className="btn fit-width-action"
                            type="button"
                            title="너비 맞춤 후 아래로 이어붙이기"
                            disabled={!item}
                            onClick={fitPlacementWidth}
                          >{Icon.stretchHorizontal} 너비 맞춤 이어붙임</button>
                        )}
                        <div className="spacer" />
                        <span className="scale">{placementScalePercent}%</span>
                      </div>
                      <div className="quick-scale-control">
                        <input
                          className="scale-range compact"
                          type="range"
                          min={Math.round(PLACEMENT_SCALE_MIN * 100)}
                          max={Math.round(maxScale * 100)}
                          value={placementScalePercent}
                          style={{ '--range-progress': `${Math.max(0, Math.min(100, scaleRangeProgress))}%` }}
                          title={scaleLimitLabel}
                          data-tooltip={scaleLimitLabel}
                          aria-label={`선택 자료 빠른 크기 조절 ${placementScalePercent}%`}
                          onChange={e => updatePlacement({ scaleRatio: Number(e.target.value) / 100 })}
                        />
                        <strong>{placementScalePercent}%</strong>
                        <small>{scaleLimitBySlot ? `최대 ${maxScalePercent}% · 칸 제한` : `최대 ${maxScalePercent}%`}</small>
                      </div>

                      <div className="panel-section-hd">
                        여백 자르기 <span className="line" />
                      </div>

                      <div className="manual-crop-control" ref={cropControlRef}>
                        <div className="manual-crop-note">
                          +값은 영역을 바깥으로 넓히고, -값은 안쪽으로 잘라냅니다.
                        </div>
                        <button
                          type="button"
                          className="subtle-toggle"
                          data-tooltip="자주 쓰는 여백 자르기 값을 빠르게 적용"
                          aria-expanded={cropPresetsOpen}
                          onClick={() => setCropPresetsOpen(open => !open)}
                        >
                          <span>자르기 프리셋</span>
                          <strong>{cropPresetsOpen ? '접기' : '펼치기'}</strong>
                        </button>
                        {cropPresetsOpen && (
                          <div className="manual-crop-presets">
                            <button type="button" className="btn" disabled={!item || mutating} onClick={() => adjustCropDraft({ leftRatio: -0.05, rightRatio: -0.05, topRatio: -0.05, bottomRatio: -0.05 })}>
                              사방 +5%
                            </button>
                            <button type="button" className="btn" disabled={!item || mutating} onClick={() => adjustCropDraft({ topRatio: -0.05, bottomRatio: -0.05 })}>
                              위아래 +5%
                            </button>
                            <button type="button" className="btn" disabled={!item || mutating} onClick={() => adjustCropDraft({ leftRatio: -0.05, rightRatio: -0.05 })}>
                              좌우 +5%
                            </button>
                            <button type="button" className="btn" disabled={!item || mutating} onClick={() => adjustCropDraft({ leftRatio: 0.05, rightRatio: 0.05, topRatio: 0.05, bottomRatio: 0.05 })}>
                              사방 -5%
                            </button>
                          </div>
                        )}
                        <div className="manual-crop-grid">
                          <label>
                            <span>왼쪽</span>
                            <input
                              type="range"
                              min={-Math.round(MANUAL_CROP_OUTSET_MAX * 100)}
                              max={Math.round(MANUAL_CROP_EDGE_MAX * 100)}
                              step={Math.round(MANUAL_CROP_EDGE_STEP * 100)}
                              value={Math.round(cropDraft.leftRatio * 100)}
                              onChange={e => updateCropDraft('leftRatio', Number(e.target.value) / 100)}
                            />
                            <strong>{manualCropPercent(cropDraft.leftRatio)}</strong>
                          </label>
                          <label>
                            <span>오른쪽</span>
                            <input
                              type="range"
                              min={-Math.round(MANUAL_CROP_OUTSET_MAX * 100)}
                              max={Math.round(MANUAL_CROP_EDGE_MAX * 100)}
                              step={Math.round(MANUAL_CROP_EDGE_STEP * 100)}
                              value={Math.round(cropDraft.rightRatio * 100)}
                              onChange={e => updateCropDraft('rightRatio', Number(e.target.value) / 100)}
                            />
                            <strong>{manualCropPercent(cropDraft.rightRatio)}</strong>
                          </label>
                          <label>
                            <span>위</span>
                            <input
                              type="range"
                              min={-Math.round(MANUAL_CROP_OUTSET_MAX * 100)}
                              max={Math.round(MANUAL_CROP_EDGE_MAX * 100)}
                              step={Math.round(MANUAL_CROP_EDGE_STEP * 100)}
                              value={Math.round(cropDraft.topRatio * 100)}
                              onChange={e => updateCropDraft('topRatio', Number(e.target.value) / 100)}
                            />
                            <strong>{manualCropPercent(cropDraft.topRatio)}</strong>
                          </label>
                          <label>
                            <span>아래</span>
                            <input
                              type="range"
                              min={-Math.round(MANUAL_CROP_OUTSET_MAX * 100)}
                              max={Math.round(MANUAL_CROP_EDGE_MAX * 100)}
                              step={Math.round(MANUAL_CROP_EDGE_STEP * 100)}
                              value={Math.round(cropDraft.bottomRatio * 100)}
                              onChange={e => updateCropDraft('bottomRatio', Number(e.target.value) / 100)}
                            />
                            <strong>{manualCropPercent(cropDraft.bottomRatio)}</strong>
                          </label>
                        </div>
                        <div className="manual-crop-actions">
                          <button
                            className="btn"
                            type="button"
                            disabled={!item || mutating || (!draftCropActive && !savedCropActive)}
                            onClick={resetManualCrop}
                          >초기화</button>
                          <button
                            className="btn primary"
                            type="button"
                            disabled={!item || mutating || !cropChanged}
                            onClick={() => applyManualCrop()}
                          >자르기 적용</button>
                        </div>
                      </div>

                      <div className="panel-section-hd">
                        위치·크기 <span className="line" />
                      </div>

                      <div className="position-control">
                        <div className="position-pad" aria-label="선택 자료 위치 미세 조절">
                          <button
                            className="pos-btn up"
                            type="button"
                            title="위로"
                            disabled={!hasVerticalRoom}
                            onClick={() => nudgePlacement(0, -PLACEMENT_NUDGE_STEP)}
                          >{Icon.arrowUp}</button>
                          <button
                            className="pos-btn left"
                            type="button"
                            title="왼쪽으로"
                            onClick={() => nudgePlacement(-PLACEMENT_NUDGE_STEP, 0)}
                          >{Icon.arrowLeft}</button>
                          <button
                            className="pos-btn reset"
                            type="button"
                            title="위치 초기화"
                            onClick={resetPlacement}
                          >{Icon.reset}</button>
                          <button
                            className="pos-btn right"
                            type="button"
                            title="오른쪽으로"
                            onClick={() => nudgePlacement(PLACEMENT_NUDGE_STEP, 0)}
                          >{Icon.arrowRight}</button>
                          <button
                            className="pos-btn down"
                            type="button"
                            title="아래로"
                            disabled={!hasVerticalRoom}
                            onClick={() => nudgePlacement(0, PLACEMENT_NUDGE_STEP)}
                          >{Icon.arrowDown}</button>
                        </div>
                        <div className="position-sliders">
                          <label>
                            <span>좌우</span>
                            <input
                              type="range"
                              min="0"
                              max="100"
                              value={Math.round(placementX * 100)}
                              onChange={e => updatePlacement({ xRatio: Number(e.target.value) / 100 })}
                            />
                            <strong>{Math.round(placementX * 100)}%</strong>
                          </label>
                          <label>
                            <span>상하</span>
                            <input
                              type="range"
                              min="0"
                              max="100"
                              value={Math.round((hasVerticalRoom ? placementY : 0) * 100)}
                              disabled={!hasVerticalRoom}
                              onChange={e => updatePlacement({ yRatio: Number(e.target.value) / 100 })}
                            />
                            <strong>{Math.round((hasVerticalRoom ? placementY : 0) * 100)}%</strong>
                          </label>
                          <label className="scale-slider-row">
                            <span>크기</span>
                            <input
                              className="scale-range"
                              type="range"
                              min={Math.round(PLACEMENT_SCALE_MIN * 100)}
                              max={Math.round(maxScale * 100)}
                              value={placementScalePercent}
                              style={{ '--range-progress': `${Math.max(0, Math.min(100, scaleRangeProgress))}%` }}
                              title={scaleLimitLabel}
                              data-tooltip={scaleLimitLabel}
                              aria-label={`선택 자료 크기 ${placementScalePercent}%`}
                              onChange={e => updatePlacement({ scaleRatio: Number(e.target.value) / 100 })}
                            />
                            <strong>{placementScalePercent}%</strong>
                          </label>
                          <div className={`scale-limit-note ${scaleLimitBySlot ? 'limited' : ''}`}>
                            <span>최대 {maxScalePercent}%</span>
                            <small>{scaleLimitBySlot ? '칸 높이 제한' : '전체 허용 한도'}</small>
                          </div>
                          <div className="scale-actions">
                            <button
                              className="icon-btn"
                              type="button"
                              title="축소"
                              disabled={!canZoomOut}
                              onClick={() => nudgeScale(-PLACEMENT_SCALE_STEP)}
                            >{Icon.zoomOut}</button>
                            <button
                              className="scale-reset"
                              type="button"
                              onClick={resetScale}
                              disabled={!item || Math.abs(placementScale - DEFAULT_PLACEMENT_SCALE_RATIO) < 0.001}
                            >100%</button>
                            <button
                              className="icon-btn"
                              type="button"
                              title="확대"
                              disabled={!canZoomIn}
                              onClick={() => nudgeScale(PLACEMENT_SCALE_STEP)}
                            >{Icon.zoomIn}</button>
                          </div>
                        </div>
                      </div>

                      <div className="panel-section-hd">
                        추가 업스케일 <span className="line" />
                      </div>
                      <button
                        className="btn primary"
                        type="button"
                        style={{width: '100%', justifyContent: 'space-between'}}
                        onClick={() => onEnhanceImage?.([item.id])}
                        disabled={!canEnhanceCurrent}
                        title={userSettings?.hasGeminiApiKey ? 'Nano Banana 2로 선택 문항을 투명 PNG로 재구성합니다' : 'Gemini API 키를 저장하면 사용할 수 있습니다'}
                      >
                        <span style={{display:'flex', alignItems:'center', gap:8}}>{Icon.wand} AI 업스케일 재구성</span>
                        <span style={{fontFamily:'JetBrains Mono, monospace', fontSize:11, opacity:.82}}>Nano Banana 2</span>
                      </button>
                      <div style={{fontSize: 11.5, lineHeight: 1.45, color: 'var(--muted)', marginTop: 7}}>
                        원문은 유지하고 문자·숫자 선명도와 투명 배경을 개선합니다. 적용 후 텍스트 검토 표시가 남습니다.
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : pendingFile ? (
              <PendingFilePreview
                file={pendingFile}
                fileKey={pendingFileKey}
                queueBusy={queueBusy}
                processQueuedFiles={processQueuedFiles}
                onPreviewError={onPendingPreviewError}
              />
            ) : (
              <div style={{
                padding: 40, textAlign: 'center',
                color: 'var(--muted)', fontSize: 13,
                border: '1px dashed var(--line)',
                borderRadius: 10,
              }}>
                왼쪽 목록 또는 칠판에서<br/>자료를 선택하세요
              </div>
            )}
          </div>

          {showItemConfirmBar && (
          <div className="tab-foot">
            <label className="check">
              <input type="checkbox" checked={bulk} onChange={e => setBulk(e.target.checked)} />
              전체 적용
            </label>
            <div className="spacer" />
            <button className="btn" disabled={!item}>건너뛰기</button>
            <button
              className="btn primary"
              disabled={!item || item.step === 'raw'}
              onClick={() => {
                if (!item) return;
                if (bulk) applyToAll(item.step, { silent: true });
                onConfirm(item.id, { bulk });
              }}
            >
              {Icon.check} {bulk ? '일괄 확인' : '확인'}
            </button>
          </div>
          )}
        </>
      )}

      {tab === 'board' && (
        <>
          <div className="tab-body">
            <div className="panel-section-hd">레이아웃 <span className="line" /></div>

            <div className="row-control">
              <div className="lbl">한 줄 자료 수<small>너비 맞춤 아님 · 한 줄 배치 개수</small></div>
              <div className="seg-mini">
                {[1,2,3].map(n => (
                  <button key={n} className={boardColumns===n ? 'on' : ''} onClick={() => setBoardColumns(n)}>{n}개</button>
                ))}
              </div>
            </div>

            <div className="row-control">
              <div className="lbl">스크롤 모드<small>밑으로 무한 스크롤</small></div>
              <span className="pos-tag" style={{background:'var(--ok)'}}>ON</span>
            </div>

            <div className="row-control">
              <div className="lbl">드래그 마그넷<small>배치 칸 가이드에 자동 정렬</small></div>
              <span className="pos-tag" style={{background:'var(--ok)'}}>ON</span>
            </div>

            <div className="row-control">
              <div className="lbl">주변 사진 높이<small>같은 행의 가장 큰 자료 기준</small></div>
              <span className="pos-tag" style={{background:'var(--ok)'}}>ON</span>
            </div>

            <div className="panel-section-hd" style={{marginTop:4}}>칠판 색상 <span className="line" /></div>

            <div className="row-control">
              <div className="lbl">칠판 배경<small>실제 교실 칠판과 맞추기</small></div>
              <div className="swatches">
                {BOARD_COLORS.map(c => (
                  <div key={c}
                       className={`sw ${boardColor === c ? 'on' : ''}`}
                       style={{ background: c }}
                       onClick={() => setBoardColor(c)}
                       title={c} />
                ))}
              </div>
            </div>

            <div className="row-control">
              <div className="lbl">강조색<small>UI 강조에 쓰이는 색</small></div>
              <div className="swatches">
                {ACCENTS.map(c => (
                  <div key={c}
                       className={`sw ${accent === c ? 'on' : ''}`}
                       style={{ background: c }}
                       onClick={() => setAccent(c)}
                       title={c} />
                ))}
              </div>
            </div>

            <div className="panel-section-hd" style={{marginTop:4}}>일괄 작업 <span className="line" /></div>

            <button className="btn" style={{justifyContent:'space-between'}} onClick={() => applyToAll('s2')}>
              <span style={{display:'flex', alignItems:'center', gap:8}}>{Icon.aiBatch} 전체를 2단계 AI 변환</span>
              <span style={{fontFamily:'JetBrains Mono, monospace', fontSize:11, color:'var(--muted)'}}>~ {items.length * 4}s</span>
            </button>
            <button
              className="btn primary"
              style={{justifyContent:'space-between'}}
              onClick={onRecognizeSession}
              disabled={!canRecognizeSession}
              title={userSettings?.hasGeminiApiKey ? '현재 세션의 모든 원본 페이지를 문제 단위로 다시 인식' : 'Gemini API 키를 저장하면 AI 인식을 실행할 수 있습니다'}
            >
              <span style={{display:'flex', alignItems:'center', gap:8}}>{Icon.aiBatch} 현재 자료 문제 인식</span>
              <span style={{fontFamily:'JetBrains Mono, monospace', fontSize:11, opacity:.82}}>AI</span>
            </button>
            <button className="btn" style={{justifyContent:'space-between'}} onClick={() => applyToAll('s1')}>
              <span style={{display:'flex', alignItems:'center', gap:8}}>{Icon.check} 전체를 1단계로</span>
              <span style={{fontFamily:'JetBrains Mono, monospace', fontSize:11, color:'var(--muted)'}}>즉시</span>
            </button>

            <div className="panel-section-hd" style={{marginTop:4}}>업로드 옵션 <span className="line" /></div>

            <div className="row-control">
              <div className="lbl">
                HWP/HWPX
                <small>{hangulRuntimeSummary(hangulDiagnostics)}</small>
              </div>
              <span
                className="pos-tag"
                style={{
                  background: hangulStatusMeta.tone,
                  minWidth: 58,
                  textAlign: 'center',
                }}
                title={(hangulDiagnostics?.recommendedActions || [])[0] || '한글 문서 변환 상태'}
              >
                {hangulStatusMeta.label}
              </span>
            </div>

            {hangulDiagnostics && (
              <div className={`runtime-details ${hangulDetailsExpanded ? 'open' : ''}`}>
                <button
                  className="runtime-details-summary"
                  type="button"
                  aria-expanded={hangulDetailsExpanded ? 'true' : 'false'}
                  onClick={() => setHangulDetailsExpanded(open => !open)}
                >
                  변환 환경
                </button>
                {hangulDetailsExpanded && (
                  <>
                    <div className="runtime-tool-grid">
                      {hangulToolRows.map(row => (
                        <div key={row.label} className="runtime-tool-row">
                          <span>{row.label}</span>
                          <strong>{row.count}</strong>
                          <small>{row.names || '없음'}</small>
                        </div>
                      ))}
                    </div>
                    {Array.isArray(hangulDiagnostics?.warnings) && hangulDiagnostics.warnings.length > 0 && (
                      <div className="runtime-note warn">
                        {hangulDiagnostics.warnings[0]}
                      </div>
                    )}
                    {Array.isArray(hangulDiagnostics?.recommendedActions) && hangulDiagnostics.recommendedActions.length > 0 && (
                      <div className="runtime-note">
                        {hangulDiagnostics.recommendedActions[0]}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            <div className="intent-control">
              {INPUT_INTENT_OPTIONS.map(option => (
                <button
                  key={option.value}
                  className={`intent-choice ${normalizeInputIntent(inputIntent) === option.value ? 'on' : ''}`}
                  type="button"
                  onClick={() => setInputIntent?.(option.value)}
                  title={option.description}
                >
                  <span className="intent-choice-head">
                    <i aria-hidden="true">{Icon[option.icon] || Icon.scan}</i>
                    <strong>{option.label}</strong>
                  </span>
                  <small>{option.description}</small>
                  {Array.isArray(option.pills) && option.pills.length > 0 && (
                    <em className="intent-choice-pills">
                      {option.pills.map(pill => <b key={pill}>{pill}</b>)}
                    </em>
                  )}
                </button>
              ))}
            </div>

            <div className="row-control">
              <div className="lbl">
                AI 보정 사용
                <small>{userSettings?.hasGeminiApiKey ? 'Gemini 키 설정됨' : 'Gemini 키 없음 — 자동 비활성화'}</small>
              </div>
              <label className="check" style={{cursor: userSettings?.hasGeminiApiKey ? 'pointer' : 'not-allowed', opacity: userSettings?.hasGeminiApiKey ? 1 : .5}}>
                <input
                  type="checkbox"
                  checked={aiEnabled && !!userSettings?.hasGeminiApiKey}
                  disabled={!userSettings?.hasGeminiApiKey}
                  onChange={e => setAiEnabled && setAiEnabled(e.target.checked)}
                />
                <span style={{fontSize: 12, color: 'var(--muted)'}}>
                  {aiEnabled && userSettings?.hasGeminiApiKey ? '켜짐' : '꺼짐'}
                </span>
              </label>
            </div>

            <div className="panel-section-hd" style={{marginTop:4}}>Gemini API 키 <span className="line" /></div>

            <div className="row-control" style={{gridTemplateColumns: '1fr'}}>
              <div className="lbl">
                <span style={{display:'flex', alignItems:'center', gap:8}}>
                  <span className={`pos-tag`} style={{background: userSettings?.hasGeminiApiKey ? 'var(--ok)' : 'var(--danger)'}}>
                    {userSettings?.hasGeminiApiKey ? '설정됨' : '미설정'}
                  </span>
                  {userSettings?.hasGeminiApiKey && (
                    <span style={{fontSize: 11, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace'}}>
                      {userSettings.geminiApiKeyPreview}
                    </span>
                  )}
                </span>
                <small>
                  {userSettings?.geminiApiKeySource === 'env'
                    ? '환경변수의 GEMINI_API_KEY 사용 중. 저장하면 그 값이 우선.'
                    : 'GEMINI_API_KEY로 자동 적용. .app_runtime/user_settings.json에 저장.'}
                </small>
              </div>
            </div>
            <div className="key-input-row">
              <input
                type={showKey ? 'text' : 'password'}
                className="key-input"
                placeholder={userSettings?.hasGeminiApiKey ? `현재 ${userSettings.geminiApiKeyPreview} (덮어쓰기)` : 'AIza...'}
                value={keyDraft}
                onChange={e => setKeyDraft(e.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
              <button className="btn icon" type="button" onClick={() => setShowKey(s => !s)} title={showKey ? '숨기기' : '보기'}>
                {showKey ? '🙈' : '👁'}
              </button>
            </div>
            <div style={{display: 'flex', gap: 6}}>
              <button
                className="btn primary"
                style={{flex: 1, justifyContent: 'center'}}
                onClick={() => { onSaveGeminiKey?.(keyDraft.trim()); setKeyDraft(''); }}
                disabled={!keyDraft.trim()}
              >
                키 저장
              </button>
              <button
                className="btn"
                style={{flex: 1, justifyContent: 'center'}}
                onClick={() => { if (window.confirm('저장된 Gemini API 키를 삭제할까요?')) onSaveGeminiKey?.(''); }}
                disabled={!userSettings?.hasStoredGeminiApiKey}
              >
                저장된 키 삭제
              </button>
            </div>

            <div className="panel-section-hd" style={{marginTop:4}}>OpenAI API 키 <span className="line" /></div>

            <div className="row-control" style={{gridTemplateColumns: '1fr'}}>
              <div className="lbl">
                <span style={{display:'flex', alignItems:'center', gap:8}}>
                  <span className={`pos-tag`} style={{background: userSettings?.hasOpenAiApiKey ? 'var(--ok)' : 'var(--danger)'}}>
                    {userSettings?.hasOpenAiApiKey ? '설정됨' : '미설정'}
                  </span>
                  {userSettings?.hasOpenAiApiKey && (
                    <span style={{fontSize: 11, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace'}}>
                      {userSettings.openAiApiKeyPreview}
                    </span>
                  )}
                </span>
                <small>OpenAI 기반 업스케일 재구성 fallback에만 사용합니다. 기본 3단계 업스케일은 Gemini를 사용합니다.</small>
              </div>
            </div>
            <div className="key-input-row">
              <input
                type={showOpenAiKey ? 'text' : 'password'}
                className="key-input"
                placeholder={userSettings?.hasOpenAiApiKey ? `현재 ${userSettings.openAiApiKeyPreview} (덮어쓰기)` : 'sk-...'}
                value={openAiKeyDraft}
                onChange={e => setOpenAiKeyDraft(e.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
              <button className="btn icon" type="button" onClick={() => setShowOpenAiKey(s => !s)} title={showOpenAiKey ? '숨기기' : '보기'}>
                {showOpenAiKey ? '숨' : '보기'}
              </button>
            </div>
            <div style={{display: 'flex', gap: 6}}>
              <button
                className="btn primary"
                style={{flex: 1, justifyContent: 'center'}}
                onClick={() => { onSaveOpenAiKey?.(openAiKeyDraft.trim()); setOpenAiKeyDraft(''); }}
                disabled={!openAiKeyDraft.trim()}
              >
                키 저장
              </button>
              <button
                className="btn"
                style={{flex: 1, justifyContent: 'center'}}
                onClick={() => { if (window.confirm('저장된 OpenAI API 키를 삭제할까요?')) onSaveOpenAiKey?.(''); }}
                disabled={!userSettings?.hasStoredOpenAiApiKey}
              >
                저장된 키 삭제
              </button>
            </div>

            <div className="panel-section-hd" style={{marginTop:4}}>앱 업데이트 <span className="line" /></div>

            <div className="row-control">
              <div className="lbl">
                현재 버전
                <small>{updateVersionLine}</small>
              </div>
              <span
                className="pos-tag"
                style={{
                  background: updateStatusTone,
                  minWidth: 58,
                  textAlign: 'center',
                }}
              >
                {updateStatusLabel}
              </span>
            </div>
            {updateInfo?.error && (
              <div className="runtime-note warn">
                {updateInfo.error}
              </div>
            )}
            <div style={{display: 'flex', gap: 6}}>
              <button
                className="btn"
                style={{flex: 1, justifyContent: 'center'}}
                type="button"
                onClick={() => onCheckUpdate?.()}
                disabled={updateBusy}
              >
                {updateBusy ? '확인 중...' : '업데이트 확인'}
              </button>
              <button
                className="btn primary"
                style={{flex: 1, justifyContent: 'center'}}
                type="button"
                onClick={() => onOpenUpdate?.()}
                disabled={updateBusy || !updateDownloadUrl}
                title={updateDownloadUrl ? '다운로드 페이지 열기' : '업데이트 다운로드 URL이 없습니다'}
              >
                {Icon.download} 다운로드 열기
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function LoadingOverlay({ label, hint, startedAt }){
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!startedAt) { setElapsed(0); return; }
    const tick = () => setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    tick();
    const id = setInterval(tick, 500);
    return () => clearInterval(id);
  }, [startedAt]);
  const fmt = (s) => s >= 60 ? `${Math.floor(s/60)}분 ${s%60}초` : `${s}초`;
  return (
    <div className="loading-overlay">
      <div className="loading-card">
        <div className="loading-spinner" />
        <div className="loading-label">{label}</div>
        {startedAt && <div className="loading-elapsed">{fmt(elapsed)} 경과</div>}
        {hint && <div className="loading-hint">{hint}</div>}
      </div>
    </div>
  );
}

function BackgroundJobsPanel({ jobs, onCancel, onDismiss }){
  const visibleJobs = (jobs || []).filter(job => job.status !== 'dismissed');
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!visibleJobs.some(job => job.status === 'running')) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [visibleJobs]);
  if (!visibleJobs.length) return null;

  const elapsedLabel = (startedAt) => {
    const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
    return seconds >= 60 ? `${Math.floor(seconds / 60)}분 ${seconds % 60}초` : `${seconds}초`;
  };
  const statusLabel = (status) => {
    if (status === 'running') return '진행 중';
    if (status === 'failed') return '실패';
    if (status === 'canceled') return '취소됨';
    return '완료';
  };

  return (
    <div className="bg-jobs" aria-live="polite">
      {visibleJobs.map(job => {
        const isRecognition = String(job.scope || '').includes('recognition');
        return (
          <div key={job.id} className={`bg-job ${job.status}`}>
            <div className="bg-job-mark">
              {job.status === 'running' ? <span className="mini-spinner" /> : <span>{job.status === 'failed' ? '!' : Icon.check}</span>}
            </div>
            <div className="bg-job-main">
              <div className="bg-job-title">
                <strong>{job.label}</strong>
                <span>{statusLabel(job.status)}</span>
              </div>
              {job.hint && <div className="bg-job-hint">{job.hint}</div>}
              {job.status === 'running' && <div className="bg-job-time">{elapsedLabel(job.startedAt)} 경과</div>}
            </div>
            {job.status === 'running' ? (
              <button className="bg-job-action" type="button" onClick={() => onCancel?.(job.id)}>
                {isRecognition ? '인식 중단' : '취소'}
              </button>
            ) : (
              <button className="bg-job-action" type="button" onClick={() => onDismiss?.(job.id)}>닫기</button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function RecognitionCancelBanner({ job, onCancel }){
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!job?.startedAt) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [job?.startedAt]);

  if (!job || job.status !== 'running') return null;

  const elapsedSeconds = Math.max(0, Math.floor((now - job.startedAt) / 1000));
  const elapsedLabel = elapsedSeconds >= 60
    ? `${Math.floor(elapsedSeconds / 60)}분 ${elapsedSeconds % 60}초`
    : `${elapsedSeconds}초`;

  return (
    <div className="recognition-cancel-banner" role="status" aria-live="polite">
      <div className="recognition-cancel-mark">
        <span className="mini-spinner" />
      </div>
      <div className="recognition-cancel-copy">
        <strong>{job.label || 'AI 인식 중'}</strong>
        <span>{elapsedLabel} 경과 · 잘못 눌렀다면 지금 취소할 수 있습니다</span>
      </div>
      <button
        className="btn danger recognition-cancel-action"
        type="button"
        onClick={() => onCancel?.(job.id)}
      >
        인식 취소
      </button>
    </div>
  );
}

function RecognitionReviewModal({ review, confirming, onConfirm, onCancel }){
  const previewSession = review?.session;
  const targetPageIds = review?.pageIds || null;
  const pages = useMemo(() => {
    const allPages = Array.isArray(previewSession?.pages) ? previewSession.pages : [];
    if (!targetPageIds?.length) return allPages;
    const allowed = new Set(targetPageIds);
    return allPages.filter(page => allowed.has(page.id));
  }, [previewSession, targetPageIds]);
  const problemsById = useMemo(() => {
    const map = new Map();
    (previewSession?.problems || []).forEach(problem => {
      if (problem?.id) map.set(problem.id, problem);
    });
    return map;
  }, [previewSession]);
  const summary = useMemo(
    () => summarizeRecognitionSession(previewSession, targetPageIds),
    [previewSession, targetPageIds]
  );

  if (!review) return null;
  const title = review.title || 'AI 인식 결과 확인';
  const movesToReview = review?.kind === 'queue-recognition';
  const subtitle = review.subtitle || (
    movesToReview
      ? `${summary.problemLabel}로 분할했습니다. 맞으면 검수 화면에서 경계를 확인합니다.`
      : `${summary.problemLabel}로 분할했습니다. 맞으면 바로 칠판에 붙입니다.`
  );
  const confirmLabel = movesToReview ? '맞아요, 검수로 이동' : '맞아요, 칠판에 붙이기';
  const confirmingLabel = movesToReview ? '검수로 이동 중...' : '붙이는 중...';
  const cancelLabel = movesToReview ? '적용 안 함' : '취소';

  return (
    <div className="recognition-modal-shell" role="dialog" aria-modal="true" aria-labelledby="recognition-review-title">
      <div className="recognition-modal-backdrop" onClick={confirming ? undefined : onCancel} />
      <div className="recognition-modal">
        <div className="recognition-modal-hd">
          <div>
            <span className="recognition-eyebrow">AI 인식 결과</span>
            <h2 id="recognition-review-title">{title}</h2>
            <p>{subtitle}</p>
          </div>
          <button className="modal-x" type="button" onClick={onCancel} disabled={confirming} title="닫기">×</button>
        </div>

        <div className="recognition-summary">
          {review.fileCount ? <span>{review.fileCount}개 파일</span> : null}
          <span>{summary.pages} 페이지</span>
          <span>{summary.problemLabel}</span>
          <span className={summary.riskCount ? 'warn' : ''}>
            {summary.riskCount ? `${summary.riskCount}개 확인 필요` : '위험 표시 없음'}
          </span>
        </div>

        <div className="recognition-preview">
          {pages.length ? pages.map(page => {
            const pageProblems = (page.problemIds || page.problem_ids || [])
              .map(id => problemsById.get(id))
              .filter(Boolean);
            return (
              <div key={page.id} className="recognition-page">
                <div className="recognition-page-hd">
                  <strong>{page.id}</strong>
                  <span>{formatProblemCount(countSessionProblems(pageProblems))}</span>
                  <span>{page.width}×{page.height}</span>
                </div>
                <div className="recognition-page-canvas">
                  {page.sourceImageUri ? (
                    <img src={page.sourceImageUri} alt={page.id} draggable={false} />
                  ) : (
                    <div className="recognition-page-empty">페이지 이미지를 불러올 수 없어요.</div>
                  )}
                  {pageProblems.map((problem, index) => {
                    const bbox = problem.bbox || {};
                    const w = page.width || 1;
                    const h = page.height || 1;
                    if (!bbox.width || !bbox.height) return null;
                    const status = deriveProblemStatus(problem);
                    return (
                      <div
                        key={problem.id}
                        className={`recognition-box ${reviewStatusClass(status)}`}
                        style={{
                          left: `${(bbox.left / w) * 100}%`,
                          top: `${(bbox.top / h) * 100}%`,
                          width: `${(bbox.width / w) * 100}%`,
                          height: `${(bbox.height / h) * 100}%`,
                        }}
                        title={problem.title || problem.id}
                      >
                        <span>{String(index + 1).padStart(2, '0')}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          }) : (
            <div className="recognition-empty">확인할 페이지가 없습니다.</div>
          )}
        </div>

        <div className="recognition-modal-foot">
          <button className="btn" type="button" onClick={onCancel} disabled={confirming}>{cancelLabel}</button>
          <button className="btn primary" type="button" onClick={onConfirm} disabled={confirming || !summary.problems}>
            {confirming ? confirmingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// renders a real image when the item came from the backend; falls back to
// the SVG ItemArt for mock data. forceMode overrides the step→variant mapping
// (useful for side-panel preview tabs).
function TileImage({ item, forceMode }){
  const wantChalk = forceMode ? forceMode === 'chalk' : item.step !== 's1';
  const chalk = item.chalkUrl;
  const raw = item.imageUrl;
  const url = wantChalk
    ? (chalk || raw)
    : (raw || chalk);
  if (url) {
    return (
      <img src={url} alt={item.name || ''}
           className={`tile-img ${wantChalk ? 'is-chalk' : 'is-raw'}`}
           draggable={false} />
    );
  }
  return <ItemArt kind={item.kind} mode={wantChalk ? 'chalk' : 'raw'} />;
}

function canPreviewImageFile(file){
  const ext = sourceFileExtension(file);
  return Boolean(file?.type?.startsWith('image/')) && !['tif', 'tiff'].includes(ext);
}

function pendingFilePreviewLabel(file){
  if (canPreviewImageFile(file)) return '이미지 미리보기';
  if (isPdfFile(file)) return 'PDF 미리보기';
  if (isHwpFile(file)) return '한글 문서';
  return '파일 미리보기';
}

function PendingFilePreview({ file, fileKey, queueBusy, processQueuedFiles, onPreviewError }){
  const [objectUrl, setObjectUrl] = useState('');
  const [previewError, setPreviewError] = useState('');
  const isImagePreview = canPreviewImageFile(file);
  const isPdfPreview = isPdfFile(file);
  const isHangulFile = isHwpFile(file);
  const canCreatePreview = Boolean(file && (isImagePreview || isPdfPreview));
  const extension = sourceFileExtension(file);

  useEffect(() => {
    setPreviewError('');
    setObjectUrl('');
    if (!canCreatePreview) return;
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
      const err = new Error('브라우저 미리보기를 만들 수 없습니다');
      setPreviewError('미리보기를 열 수 없습니다');
      onPreviewError?.(err);
      return;
    }
    let url = '';
    try {
      url = URL.createObjectURL(file);
      setObjectUrl(url);
    } catch (err) {
      setPreviewError('미리보기를 열 수 없습니다');
      onPreviewError?.(err);
    }
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [file, canCreatePreview, onPreviewError]);

  if (!file) return null;

  const runQueueAction = (mode) => {
    if (!fileKey || queueBusy) return;
    processQueuedFiles?.(mode, fileKey);
  };
  const metaParts = [
    sourceFileKindLabel(file),
    formatBytes(file.size),
    extension ? `.${extension}` : '',
  ].filter(Boolean);

  return (
    <div className="pending-preview-panel">
      <div className="pending-preview-head">
        <span className="pending-preview-icon">{isImagePreview ? Icon.scan : Icon.fileText}</span>
        <div className="pending-preview-copy">
          <div className="pending-preview-eyebrow">업로드 대기 파일</div>
          <div className="pending-preview-title" title={file.name || ''}>{file.name || '이름 없는 파일'}</div>
          <div className="pending-preview-meta">{metaParts.join(' · ')}</div>
        </div>
      </div>

      <div className={`pending-preview-surface ${isPdfPreview ? 'pdf' : ''} ${previewError ? 'has-error' : ''}`}>
        {isImagePreview && objectUrl && !previewError ? (
          <img
            className="pending-preview-image"
            src={objectUrl}
            alt={file.name || '업로드 파일 미리보기'}
            draggable={false}
            onError={() => {
              const err = new Error('이미지 미리보기 실패');
              setPreviewError('이미지 미리보기를 열 수 없습니다');
              onPreviewError?.(err);
            }}
          />
        ) : isPdfPreview && objectUrl && !previewError ? (
          <object
            className="pending-preview-pdf"
            data={objectUrl}
            type="application/pdf"
            aria-label={file.name || 'PDF 미리보기'}
          >
            <div className="pending-preview-empty">
              <strong>PDF 미리보기를 열 수 없습니다</strong>
              <span>등록하거나 검수에서 확인해 주세요.</span>
            </div>
          </object>
        ) : (
          <div className="pending-preview-empty">
            <strong>{previewError || pendingFilePreviewLabel(file)}</strong>
            <span>
              {previewError
                ? '등록하거나 검수에서 확인해 주세요.'
                : isHangulFile
                  ? '처리하면 페이지 이미지로 확인할 수 있습니다.'
                  : '이 파일은 바로 미리보기를 지원하지 않습니다.'}
            </span>
          </div>
        )}
      </div>

      <div className="pending-preview-actions">
        <button
          className="btn"
          type="button"
          onClick={() => runQueueAction('manual-split')}
          disabled={queueBusy}
          title="인식 없이 열고 검수 화면에서 직접 문제 영역을 나눕니다"
        >
          {Icon.pen}<span>직접 쪼개기</span>
        </button>
        <button
          className="btn"
          type="button"
          onClick={() => runQueueAction('register')}
          disabled={queueBusy}
          title="문제 파싱 없이 페이지 PNG 자료로 등록"
        >
          {Icon.pagePng}<span>페이지 PNG</span>
        </button>
        <button
          className="btn primary"
          type="button"
          onClick={() => runQueueAction('recognize')}
          disabled={queueBusy}
          title="이 파일만 문항 AI 인식"
        >
          {Icon.aiBatch}<span>AI 인식</span>
        </button>
      </div>
    </div>
  );
}

// ─── backend helpers ──────────────────────────────────────────────────────

function fileToBase64(file){
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const comma = result.indexOf(',');
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error('file read failed'));
    reader.readAsDataURL(file);
  });
}

function fileQueueKey(file){
  return [file?.name || 'file', file?.size || 0, file?.lastModified || 0].join('::');
}

function sourceFileExtension(file){
  const match = String(file?.name || '').toLowerCase().match(/\.([^.]+)$/);
  return match ? match[1] : '';
}

function isPdfFile(file){
  return sourceFileExtension(file) === 'pdf';
}

function isHwpFile(file){
  return ['hwp', 'hwpx'].includes(sourceFileExtension(file));
}

function isDocumentLikeFile(file){
  return isPdfFile(file) || isHwpFile(file);
}

function sourceFileKindLabel(file){
  if (isPdfFile(file)) return 'PDF';
  if (isHwpFile(file)) return 'HWP';
  return 'IMG';
}

function formatBytes(bytes){
  const size = Number(bytes);
  if (!Number.isFinite(size) || size <= 0) return '0KB';
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)}MB`;
  return `${Math.max(1, Math.round(size / 1024))}KB`;
}

function cloneSession(session){
  return session ? JSON.parse(JSON.stringify(session)) : null;
}

function makeUniqueId(baseId, existingIds){
  const raw = String(baseId || 'item').trim() || 'item';
  if (!existingIds.has(raw)) {
    existingIds.add(raw);
    return raw;
  }
  let counter = 2;
  while (existingIds.has(`${raw}-add-${counter}`)) counter += 1;
  const next = `${raw}-add-${counter}`;
  existingIds.add(next);
  return next;
}

function applyItemStateToProblem(problem, item){
  const next = { ...problem };
  const actualHeightPages = Math.max(0.12, item.heightFrac || next.actualHeightPages || next.actual_height_pages || 0.8);
  const startYPages = Number.isFinite(item.startYPages) ? Math.max(0, item.startYPages) : null;
  const snappedNextStartYPages = Number.isFinite(item.snappedNextStartYPages)
    ? Math.max(startYPages || 0, item.snappedNextStartYPages)
    : null;
  next.title = item.name || next.title || '';
  next.riskFlags = Array.isArray(item.riskFlags) ? [...item.riskFlags] : [];
  next.risk_flags = next.riskFlags;
  next.reviewStatus = normalizeReviewStatus(item.reviewStatus) || deriveProblemStatus(next);
  next.review_status = next.reviewStatus;
  next.step = normalizeProcessingStep(item.step);
  next.processingStep = next.step;
  next.processing_step = next.step;
  next.inputIntent = normalizeInputIntent(item.inputIntent || next.inputIntent || next.input_intent);
  next.input_intent = next.inputIntent;
  next.forceFullPageBounds = Boolean(item.forceFullPageBounds || next.forceFullPageBounds || next.force_full_page_bounds);
  next.force_full_page_bounds = next.forceFullPageBounds;
  if (item.placementMode || next.placementMode || next.placement_mode) {
    next.placementMode = item.placementMode || next.placementMode || next.placement_mode;
    next.placement_mode = next.placementMode;
  }
  next.placementXRatio = normalizePlacementXRatio(item.placementXRatio);
  next.placement_x_ratio = next.placementXRatio;
  next.placementXEdited = Boolean(item.placementXEdited || item.placement_x_edited);
  next.placement_x_edited = next.placementXEdited;
  next.boardColumns = normalizeBoardColumns(item.boardColumnCount || item.boardColumns || BOARD_COLUMN_MIN);
  next.board_columns = next.boardColumns;
  next.boardColumnIndex = Math.max(0, Number.isFinite(Number(item.boardColumnIndex)) ? Math.round(Number(item.boardColumnIndex)) : 0);
  next.board_column_index = next.boardColumnIndex;
  const rawMagnetColumnIndex = Number(item.placementMagnetColumnIndex ?? item.placement_magnet_column_index);
  const placementMagnetColumnIndex = Number.isFinite(rawMagnetColumnIndex)
    ? Math.max(0, Math.min(next.boardColumns - 1, Math.round(rawMagnetColumnIndex)))
    : null;
  next.placementMagnetColumnIndex = next.placementXEdited ? placementMagnetColumnIndex : null;
  next.placement_magnet_column_index = next.placementMagnetColumnIndex;
  next.boardRowHeightPages = Number.isFinite(Number(item.boardRowHeightPages)) ? Number(Number(item.boardRowHeightPages).toFixed(6)) : null;
  next.board_row_height_pages = next.boardRowHeightPages;
  next.placementYRatio = verticalPlacementRoomPages(item) > 0.001
    ? normalizePlacementYRatio(item.placementYRatio)
    : DEFAULT_PLACEMENT_Y_RATIO;
  next.placement_y_ratio = next.placementYRatio;
  next.placementScaleRatio = normalizePlacementScaleRatio(item.placementScaleRatio, maxPlacementScaleRatio(item));
  next.placement_scale_ratio = next.placementScaleRatio;
  const renderedHeightPages = actualHeightPages * next.placementScaleRatio;
  next.actualHeightPages = actualHeightPages;
  next.actual_height_pages = actualHeightPages;
  if (startYPages !== null) {
    next.startYPages = Number(startYPages.toFixed(6));
    next.start_y_pages = next.startYPages;
  }
  if (snappedNextStartYPages !== null) {
    next.snappedNextStartYPages = Number(snappedNextStartYPages.toFixed(6));
    next.snapped_next_start_y_pages = next.snappedNextStartYPages;
    next.slotSpanCount = Math.max(1, Math.round((snappedNextStartYPages - (startYPages || 0)) / DEFAULT_SLOT_HEIGHT_PAGES));
    next.slot_span_count = next.slotSpanCount;
  }
  if (startYPages !== null) {
    next.actualBottomYPages = Number((startYPages + actualHeightPages).toFixed(6));
    next.actual_bottom_y_pages = next.actualBottomYPages;
    next.renderedBottomYPages = Number((startYPages + renderedHeightPages).toFixed(6));
    next.rendered_bottom_y_pages = next.renderedBottomYPages;
  }
  next.overflowAmountPages = Math.max(0, renderedHeightPages - DEFAULT_SLOT_HEIGHT_PAGES);
  next.overflow_amount_pages = next.overflowAmountPages;
  return next;
}

function confirmedItemState(item){
  const statusMeta = reviewStatusMeta('normal');
  return {
    ...item,
    riskFlags: [],
    reviewStatus: 'normal',
    statusLabel: statusMeta.label,
    statusShortLabel: statusMeta.shortLabel || statusMeta.label,
    statusReason: '',
    retryable: false,
  };
}

function confirmedProblemState(problem){
  return {
    ...problem,
    riskFlags: [],
    risk_flags: [],
    reviewStatus: 'normal',
    review_status: 'normal',
    parseFailed: false,
    parse_failed: false,
  };
}

function markSessionProblemsConfirmed(rawSession, targetIds){
  const snapshot = cloneSession(rawSession);
  if (!snapshot || !Array.isArray(snapshot.problems)) return snapshot;
  const confirmedIds = new Set([...targetIds].filter(Boolean));
  snapshot.problems = snapshot.problems.map(problem => (
    confirmedIds.has(problem?.id) ? confirmedProblemState(problem) : problem
  ));

  if (Array.isArray(snapshot.pages)) {
    const byId = new Map(snapshot.problems.map(problem => [problem.id, problem]));
    snapshot.pages = snapshot.pages.map(page => {
      const problemIds = page.problemIds || page.problem_ids || [];
      const pageProblems = problemIds.map(id => byId.get(id)).filter(Boolean);
      const pageIsConfirmed = pageProblems.length > 0
        && pageProblems.every(problem => deriveProblemStatus(problem) === 'normal');
      if (!pageIsConfirmed) return page;
      return {
        ...page,
        riskFlags: [],
        risk_flags: [],
        reviewStatus: 'normal',
        review_status: 'normal',
      };
    });
  }
  return snapshot;
}

function materializeSessionForItems(rawSession, items, fileName, boardColumns = BOARD_COLUMN_MIN){
  const snapshot = cloneSession(rawSession);
  if (!snapshot || !Array.isArray(snapshot.problems)) return null;
  const byId = new Map(snapshot.problems.map(problem => [problem.id, problem]));
  const reflowedItems = reflowItemsForBoardOrder(items, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);
  const orderedProblems = reflowedItems
    .filter(item => byId.has(item.id))
    .map(item => applyItemStateToProblem(byId.get(item.id), item));
  const activeIds = new Set(orderedProblems.map(problem => problem.id));
  snapshot.problems = orderedProblems;
  applyProblemCounts(snapshot, orderedProblems);
  snapshot.session_name = fileName || snapshot.session_name || '새 세션';
  snapshot.edb_path = null;
  snapshot.edb_file_uri = null;
  snapshot.edbPath = null;
  snapshot.edbFileUri = null;
  if (Array.isArray(snapshot.pages)) {
    const orderIndex = new Map(orderedProblems.map((problem, index) => [problem.id, index]));
    snapshot.pages = snapshot.pages.map(page => ({
      ...page,
      problemIds: (page.problemIds || page.problem_ids || [])
        .filter(id => activeIds.has(id))
        .sort((a, b) => (orderIndex.get(a) ?? 999999) - (orderIndex.get(b) ?? 999999)),
    }));
  }
  return snapshot;
}

function mergeSessions(baseSession, incomingSession, fileName, boardColumns = BOARD_COLUMN_MIN){
  const base = cloneSession(baseSession);
  const incoming = cloneSession(incomingSession);
  if (!base) return incoming;
  if (!incoming) return base;

  const existingProblemIds = new Set((base.problems || []).map(problem => problem.id).filter(Boolean));
  const existingPageIds = new Set((base.pages || []).map(page => page.id).filter(Boolean));
  const pageIdMap = new Map();

  const incomingPages = (incoming.pages || []).map(page => {
    const oldId = page.id;
    const nextId = makeUniqueId(oldId, existingPageIds);
    pageIdMap.set(oldId, nextId);
    return {
      ...page,
      id: nextId,
      problemIds: [...(page.problemIds || page.problem_ids || [])],
    };
  });

  const problemIdMap = new Map();
  const incomingProblems = (incoming.problems || []).map(problem => {
    const oldId = problem.id;
    const nextId = makeUniqueId(oldId, existingProblemIds);
    problemIdMap.set(oldId, nextId);
    return {
      ...problem,
      id: nextId,
      sourcePageId: pageIdMap.get(problem.sourcePageId) || problem.sourcePageId,
    };
  });

  incomingPages.forEach(page => {
    page.problemIds = page.problemIds.map(id => problemIdMap.get(id) || id);
  });

  const mergedProblems = [...(base.problems || []), ...incomingProblems];
  const mergedProblemsById = new Map(mergedProblems.map(problem => [problem.id, problem]));
  const reflowedProblems = reflowItemsForBoardOrder(
    mergedProblems.map((problem, idx) => mapProblemToItem(problem, idx)),
    DEFAULT_SLOT_HEIGHT_PAGES,
    boardColumns
  ).map(item => applyItemStateToProblem(mergedProblemsById.get(item.id) || {}, item));
  const mergedPages = [...(base.pages || []), ...incomingPages];
  const concatUnique = (...lists) => Array.from(new Set(lists.flat().filter(Boolean)));
  const merged = {
    ...base,
    session_name: fileName || base.session_name || incoming.session_name || '새 세션',
    data_source: 'question_export',
    source_mode: 'batch',
    input_file_count: concatUnique(base.input_files || base.inputFiles || [], incoming.input_files || incoming.inputFiles || []).length,
    input_files: concatUnique(base.input_files || base.inputFiles || [], incoming.input_files || incoming.inputFiles || []),
    source_page_count: mergedPages.length,
    rendered_page_paths: concatUnique(base.rendered_page_paths || [], incoming.rendered_page_paths || []),
    rendered_page_file_uris: concatUnique(base.rendered_page_file_uris || [], incoming.rendered_page_file_uris || []),
    warning_messages: [...(base.warning_messages || base.warningMessages || []), ...(incoming.warning_messages || incoming.warningMessages || [])],
    problems: reflowedProblems,
    pages: mergedPages,
    edb_path: null,
    edb_file_uri: null,
    edbPath: null,
    edbFileUri: null,
  };
  applyProblemCounts(merged, reflowedProblems);
  return merged;
}

function reviewScopeForNewSession(baseSession, nextSession){
  const existingProblemIds = new Set((baseSession?.problems || []).map(problem => String(problem?.id || '').trim()).filter(Boolean));
  const existingPageIds = new Set((baseSession?.pages || []).map(page => String(page?.id || '').trim()).filter(Boolean));
  const scopeProblemIds = (nextSession?.problems || [])
    .map(problem => String(problem?.id || '').trim())
    .filter(id => id && !existingProblemIds.has(id));
  const scopePageIds = (nextSession?.pages || [])
    .map(page => String(page?.id || '').trim())
    .filter(id => id && !existingPageIds.has(id));
  return {
    scopeProblemIds: listUnique(scopeProblemIds),
    scopePageIds: listUnique(scopePageIds),
  };
}

function reviewFocusForNewSession(baseSession, nextSession, source){
  const scope = reviewScopeForNewSession(baseSession, nextSession);
  if (!scope.scopeProblemIds.length && !scope.scopePageIds.length) return null;
  return {
    ...scope,
    source,
  };
}

const KIND_BY_SUBJECT = {
  math: 'equation',
  science: 'graph',
  korean: 'paragraph',
  english: 'paragraph',
  social: 'paragraph',
};

function mapProblemToItem(problem, idx){
  const title = (problem.title || '').trim();
  const fallbackName = `문항 ${idx + 1}`;
  // strip the noisy "...page-001 problem 1" suffix when present
  const cleanTitle = title.replace(/page-\d+\s+problem\s+\d+\s*$/i, '').trim();
  const name = cleanTitle || `문항 ${idx + 1}`;
  const riskFlags = Array.isArray(problem.riskFlags) ? problem.riskFlags : [];
  const reviewStatus = deriveProblemStatus(problem);
  const statusMeta = reviewStatusMeta(reviewStatus);
  const initialScale = normalizePlacementScaleRatio(problem.placementScaleRatio ?? problem.placement_scale_ratio);
  const step = normalizeProcessingStep(problem.step || problem.processingStep || problem.processing_step);
  const manualCrop = normalizeManualCrop(problem.manualCrop || problem.manual_crop || problem.cropAdjustments || problem);
  const problemInputIntent = problem.inputIntent || problem.input_intent;
  return {
    id: problem.id || `p${idx + 1}`,
    name: name === '' ? fallbackName : name,
    source: problem.sourcePageId || problem.subject || '업로드',
    type: 'image',
    kind: KIND_BY_SUBJECT[problem.subject] || 'paragraph',
    step,
    heightFrac: typeof problem.actualHeightPages === 'number' && problem.actualHeightPages > 0
      ? problem.actualHeightPages
      : 0.8,
    imageUrl: problem.imagePath || null,
    chalkUrl: problem.boardRenderPath || null,
    subject: problem.subject || 'unknown',
    riskFlags,
    reviewStatus,
    statusLabel: statusMeta.label,
    statusShortLabel: statusMeta.shortLabel || statusMeta.label,
    statusReason: riskFlags.length ? riskFlags.join(', ') : '',
    retryable: reviewStatus !== 'normal',
    parseConfidence: typeof problem.parseConfidence === 'number' ? problem.parseConfidence : null,
    confidence: problem.confidence || null,
    aiStatus: problem.aiStatus || 'unknown',
    inputIntent: problemInputIntent ? normalizeInputIntent(problemInputIntent) : null,
    forceFullPageBounds: Boolean(problem.forceFullPageBounds || problem.force_full_page_bounds),
    placementMode: problem.placementMode || problem.placement_mode || null,
    startYPages: typeof problem.startYPages === 'number' ? problem.startYPages : null,
    snappedNextStartYPages: typeof problem.snappedNextStartYPages === 'number' ? problem.snappedNextStartYPages : null,
    overflowAmountPages: typeof problem.overflowAmountPages === 'number' ? problem.overflowAmountPages : 0,
    overflowViolation: Boolean(problem.overflowViolation),
    slotSpanCount: Number.isInteger(problem.slotSpanCount) ? problem.slotSpanCount : null,
    placementXRatio: normalizePlacementXRatio(problem.placementXRatio ?? problem.placement_x_ratio),
    placementXEdited: Boolean(problem.placementXEdited || problem.placement_x_edited),
    placementYRatio: normalizePlacementYRatio(problem.placementYRatio ?? problem.placement_y_ratio),
    placementScaleRatio: initialScale < 0.95 ? DEFAULT_PLACEMENT_SCALE_RATIO : initialScale,
    boardColumnCount: normalizeBoardColumns(problem.boardColumnCount ?? problem.boardColumns ?? problem.board_columns ?? BOARD_COLUMN_MIN),
    boardColumnIndex: Number.isFinite(Number(problem.boardColumnIndex ?? problem.board_column_index))
      ? Math.max(0, Math.round(Number(problem.boardColumnIndex ?? problem.board_column_index)))
      : 0,
    placementMagnetColumnIndex: Number.isFinite(Number(problem.placementMagnetColumnIndex ?? problem.placement_magnet_column_index))
      ? Math.max(0, Math.round(Number(problem.placementMagnetColumnIndex ?? problem.placement_magnet_column_index)))
      : null,
    boardRowHeightPages: Number.isFinite(Number(problem.boardRowHeightPages ?? problem.board_row_height_pages))
      ? Number(problem.boardRowHeightPages ?? problem.board_row_height_pages)
      : null,
    manualCrop,
  };
}

async function fetchLatestSession(){
  const resp = await fetch('/api/session/latest');
  if (resp.status === 404) return null;
  const json = await expectOkJson(resp, '세션 로드 실패');
  return json.session;
}

async function fetchSessionHistory(){
  const resp = await fetch('/api/session/history');
  const json = await expectOkJson(resp, '작업 이력 로드 실패');
  return Array.isArray(json.history) ? json.history : [];
}

async function postRestoreSessionHistory(id){
  const resp = await fetch('/api/session/history/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
  const json = await expectOkJson(resp, '작업 열기 실패');
  return json;
}

const AI_FALLBACK_OFF = { enabled: false, mode: 'off' };
const AI_FALLBACK_ON = {
  enabled: true,
  mode: 'auto',
  provider: 'gemini',
  model: 'gemini-3.1-pro-preview',
  threshold: 0.72,
  maxRegions: 48,
  maxTokens: 4096,
  timeoutMs: 30000,
  saveDebug: false,
};
const AI_MODEL_LABELS = {
  'gemini-3.1-pro-preview': 'Gemini 3.1 Pro',
  'gemini-3-pro-preview': 'Gemini 3 Pro',
  'gemini-2.5-pro': 'Gemini 2.5 Pro',
};
const DEFAULT_INPUT_INTENT = 'multi-problem';
const INPUT_INTENT_OPTIONS = [
  {
    value: 'multi-problem',
    label: '문항 자동 분리',
    description: '문항을 찾아 각각 잘라 배치',
    icon: 'scan',
    badgeLabel: '자동 분리',
    exportMode: 'question',
  },
  {
    value: 'single-problem',
    label: '한 페이지를 한 문제로',
    description: '페이지 전체를 한 문제 칸에 배치',
    icon: 'fileText',
    badgeLabel: '페이지 한 문제',
    exportMode: 'question',
  },
  {
    value: 'page-as-is',
    label: '원본 페이지 이어붙이기',
    description: '너비에 맞춰 키우고 높이는 비율대로 이어 배치',
    icon: 'rows3',
    badgeLabel: '원본 이어붙임',
    pills: ['3단계 고화질', '너비 맞춤', '비율 높이', '연속 이어붙이기'],
    exportMode: 'question',
  },
];
const INPUT_INTENT_BY_VALUE = Object.freeze(
  INPUT_INTENT_OPTIONS.reduce((acc, option) => {
    acc[option.value] = option;
    return acc;
  }, {})
);
const INPUT_INTENT_META_BY_VALUE = Object.freeze({
  auto: {
    value: 'auto',
    label: '문항 자동 분리',
    description: '문항을 찾아 각각 잘라 배치',
    icon: 'scan',
    badgeLabel: '자동 분리',
    exportMode: 'question',
  },
  ...INPUT_INTENT_BY_VALUE,
});

function normalizeInputIntent(value){
  const normalized = String(value || DEFAULT_INPUT_INTENT).trim().toLowerCase().replace(/_/g, '-');
  if (normalized === 'auto') return DEFAULT_INPUT_INTENT;
  return INPUT_INTENT_META_BY_VALUE[normalized] ? normalized : DEFAULT_INPUT_INTENT;
}

function inputIntentMeta(value){
  return INPUT_INTENT_META_BY_VALUE[normalizeInputIntent(value)] || INPUT_INTENT_BY_VALUE[DEFAULT_INPUT_INTENT];
}

const REVIEW_STATUS_META = {
  normal: { label: '정상', shortLabel: '정상', tone: 'normal' },
  check_needed: { label: '확인 필요', shortLabel: '확인', tone: 'check' },
  failed: { label: '인식 실패', shortLabel: '실패', tone: 'failed' },
};

const RISK_FLAG_META = {
  ai_image_missing_source: '이미지 원본 없음',
  ai_image_reconstructed_check_text: 'AI 보정 확인',
  ai_image_reconstruction_failed: 'AI 보정 실패',
  ai_retry_missing_source: '재인식 원본 없음',
  duplicate_problem_number: '중복 번호',
  fallback_grouping: '문항 경계 추정',
  hwp_oversegmentation: 'HWP 과분할',
  hwp_problem_count_mismatch: 'HWP 문항 수 차이',
  large_block_dominance: '큰 블록 우세',
  marker_document_continuation: '지문·자료 분리',
  needs_review: '검토 필요',
  no_problem_markers: '문항 번호 부족',
  ocr_disabled: 'OCR 미사용',
  passage_missing_child_questions: '지문 하위 문항 누락',
  passage_group_source_reuse: '지문 그룹 원본 중복',
  passage_cross_page_merge_check: '긴 지문 병합 확인',
  problem_per_block: '블록 단위 분리',
  sparse_segmentation: '성긴 분할',
  source_problem_bbox_overlap: '원본 영역 겹침',
};

const CLASSIN_PREFLIGHT_ISSUE_LABELS = {
  board_placement_overlap: '판서 배치 겹침',
  duplicate_problem_number: '중복 번호',
  low_ink_problem_image: '이미지 내용 부족',
  missing_problem_image: '문항 이미지 없음',
  passage_missing_child_questions: '지문 하위 문항 누락',
  passage_review_queue_remaining: '긴 지문 검수 남음',
  passage_group_source_reuse: '지문 그룹 원본 중복',
  review_flags_remaining: '검수 플래그 남음',
  small_problem_image: '문항 이미지 작음',
  source_problem_bbox_overlap: '원본 영역 겹침',
  unreadable_problem_image: '문항 이미지 흐림',
};
const PASSAGE_REVIEW_REASON_LABELS = {
  cross_page_passage_group: '페이지 넘김 긴 지문',
  hwp_text_fallback_problem: 'HWP 텍스트 fallback',
  marker_document_continuation: '문서 이어짐 표시',
  passage_cross_page_merge_check: '긴 지문 병합 확인',
  passage_fragment: '이어짐 자료',
  passage_group_source_reuse: '지문 그룹 원본 중복',
  passage_missing_child_questions: '지문 하위 문항 누락',
  source_problem_bbox_overlap: '원본 영역 겹침',
};

function classinPreflightIssueLabel(type){
  const normalized = String(type || '').trim();
  return CLASSIN_PREFLIGHT_ISSUE_LABELS[normalized] || normalized || '기타 주의';
}

function passageReviewReasonLabel(reason){
  const normalized = String(reason || '').trim();
  return PASSAGE_REVIEW_REASON_LABELS[normalized] || normalized;
}

function classinPreflightIssueLabels(preflight){
  const issues = Array.isArray(preflight?.issues) ? preflight.issues : [];
  const counts = new Map();
  issues.forEach(issue => {
    const type = String(issue?.type || issue?.issueType || issue?.issue_type || '').trim();
    const key = type || 'unknown';
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return Array.from(counts.entries()).map(([type, count]) => `${classinPreflightIssueLabel(type)} ${count}`);
}

function normalizePublishPreflightBlock(raw){
  const helper = globalThis.EDB_PUBLISH_SUMMARY?.normalizePublishPreflightBlock;
  if (typeof helper === 'function') return helper(raw);
  if (!raw || typeof raw !== 'object') return null;
  const errorKind = String(raw.errorKind || raw.error_kind || '').trim();
  const classinPreflight = raw.classinPreflight || raw.classin_preflight || {};
  const preflightStatus = String(
    raw.classinPreflightStatus
    || raw.classin_preflight_status
    || classinPreflight.status
    || ''
  ).trim();
  if (errorKind !== 'publish_preflight_blocked' && preflightStatus !== 'blocked') return null;
  const issueLabels = classinPreflightIssueLabels(classinPreflight);
  const issueSummaryLabel = String(
    raw.classinPreflightIssueSummaryLabel
    || raw.classin_preflight_issue_summary_label
    || issueLabels.join(' · ')
  ).trim();
  const issueCount = Number(
    raw.classinPreflightIssueCount
    ?? raw.classin_preflight_issue_count
    ?? classinPreflight.issueCount
    ?? classinPreflight.issue_count
    ?? 0
  );
  const issues = Array.isArray(classinPreflight.issues) ? classinPreflight.issues : [];
  const blockingIssueTypes = Array.isArray(raw.blockingIssueTypes)
    ? raw.blockingIssueTypes
    : Array.isArray(raw.blocking_issue_types)
      ? raw.blocking_issue_types
      : [];
  const issueTypes = Array.from(new Set([
    ...issues.map(issue => String(issue?.type || issue?.issueType || issue?.issue_type || '').trim()).filter(Boolean),
    ...blockingIssueTypes.map(type => String(type || '').trim()).filter(Boolean),
  ]));
  const blockingProblemIds = Array.from(new Set(
    (Array.isArray(raw.blockingProblemIds)
      ? raw.blockingProblemIds
      : Array.isArray(raw.blocking_problem_ids)
        ? raw.blocking_problem_ids
        : []
    ).map(id => String(id || '').trim()).filter(Boolean)
  ));
  const message = String(
    raw.error
    || 'ClassIn 사전점검에서 제작 전 확인 문제가 발견되어 EDB 제작을 중단했습니다.'
  ).trim();
  return {
    blocked: true,
    errorKind: errorKind || 'publish_preflight_blocked',
    message,
    classinPreflight,
    issueCount: Number.isFinite(issueCount) ? Math.max(0, issueCount) : 0,
    issueTypes,
    blockingProblemIds,
    blocking_problem_ids: blockingProblemIds,
    issueLabels,
    issueSummaryLabel,
    toastLabel: [message, issueSummaryLabel].filter(Boolean).join(' '),
  };
}

function publishBlockedTarget(blockedPublish){
  const issueTypes = Array.isArray(blockedPublish?.issueTypes)
    ? blockedPublish.issueTypes.map(type => String(type || '').trim()).filter(Boolean)
    : [];
  const issues = Array.isArray(blockedPublish?.classinPreflight?.issues)
    ? blockedPublish.classinPreflight.issues.filter(issue => issue && typeof issue === 'object')
    : [];
  const focusIssueTypes = [
    'source_problem_bbox_overlap',
    'duplicate_problem_number',
    'passage_group_source_reuse',
    'passage_missing_child_questions',
  ];
  const passageReviewIssueTypes = [
    'passage_review_queue_remaining',
    'passage_missing_child_questions',
  ];
  const topLevelProblemIds = Array.isArray(blockedPublish?.blockingProblemIds)
    ? blockedPublish.blockingProblemIds
    : Array.isArray(blockedPublish?.blocking_problem_ids)
      ? blockedPublish.blocking_problem_ids
      : [];
  const focusedProblemIds = Array.from(new Set(
    (topLevelProblemIds.length
      ? topLevelProblemIds
      : issues
        .filter(issue => focusIssueTypes.includes(String(issue?.type || issue?.issueType || issue?.issue_type || '').trim()))
        .flatMap(issue => [
          ...(Array.isArray(issue.problemIds || issue.problem_ids) ? (issue.problemIds || issue.problem_ids) : []),
          issue.problemId || issue.problem_id,
          issue.nextProblemId || issue.next_problem_id,
        ])
    )
      .map(id => String(id || '').trim())
      .filter(Boolean)
  ));
  const onlyBoardPlacement = issueTypes.length > 0
    && issueTypes.every(type => type === 'board_placement_overlap');
  if (onlyBoardPlacement) return { view: 'board', reviewFocus: null };
  if (issueTypes.some(type => passageReviewIssueTypes.includes(type))) {
    return {
      view: 'review',
      reviewFocus: {
        filter: 'passage-review',
        ...(focusedProblemIds.length ? { problemIds: focusedProblemIds } : {}),
        source: 'publish-preflight',
      },
    };
  }
  if (focusedProblemIds.length) {
    return {
      view: 'review',
      reviewFocus: { filter: 'all', problemIds: focusedProblemIds, source: 'publish-preflight' },
    };
  }
  return { view: 'review', reviewFocus: null };
}

const NON_ACTIONABLE_RISK_FLAGS = new Set([
  'marker_document_continuation',
  'ocr_disabled',
]);

const HWP_COUNT_MATCH_DISMISSIBLE_RISK_FLAGS = new Set([
  'fallback_grouping',
  'large_block_dominance',
  'no_problem_markers',
  'problem_per_block',
  'sparse_segmentation',
]);

function normalizeReviewStatus(value){
  const normalized = String(value || '').trim().toLowerCase().replace(/-/g, '_');
  return REVIEW_STATUS_META[normalized] ? normalized : null;
}

function deriveProblemStatus(problem){
  const explicit = normalizeReviewStatus(problem?.reviewStatus || problem?.review_status);
  if (explicit) return explicit;
  const bbox = problem?.bbox || {};
  const hasBox = Number(bbox.width || 0) > 0 && Number(bbox.height || 0) > 0;
  if (!hasBox || problem?.parseFailed || problem?.parse_failed) return 'failed';
  const flags = problem?.riskFlags || problem?.risk_flags || [];
  return Array.isArray(flags) && flags.length > 0 ? 'check_needed' : 'normal';
}

function reviewStatusMeta(status){
  return REVIEW_STATUS_META[status] || REVIEW_STATUS_META.normal;
}

function reviewStatusClass(status){
  return `status-${String(status || 'normal').replace(/_/g, '-')}`;
}

function riskFlagLabel(flag){
  const key = String(flag || '').trim();
  if (!key) return '검토 사유';
  if (RISK_FLAG_META[key]) return RISK_FLAG_META[key];
  return key
    .split('_')
    .filter(Boolean)
    .map(part => part.length > 8 ? part.slice(0, 8) : part)
    .join(' ');
}

function hangulRuntimeStatusMeta(hangul){
  const status = String(hangul?.status || '').toLowerCase();
  if (!hangul) return { label: '점검 중', tone: 'var(--muted)' };
  if (status === 'ready') return { label: hangul.label || '준비됨', tone: 'var(--ok)' };
  if (status === 'partial') return { label: hangul.label || '부분 준비', tone: '#aa6516' };
  return { label: hangul.label || '확인 필요', tone: 'var(--danger)' };
}

function hangulRuntimeSummary(hangul){
  if (!hangul) return 'HWP/HWPX 변환 환경 확인 중';
  if (hangul.summary) return String(hangul.summary);
  const pdfCount = (hangul.pdfConverters || []).length;
  const textCount = (hangul.textExtractors || []).length;
  const bridgeCount = (hangul.hwpToHwpxConverters || []).length;
  const rendererCount = (hangul.hwpRenderers || []).length;
  const parts = [`PDF ${pdfCount}`, `텍스트 ${textCount}`, `브리지 ${bridgeCount}`];
  if (rendererCount) parts.push(`렌더 ${rendererCount}`);
  if (hangul.htmlPdfFallbackReady) parts.push('HTML fallback');
  if (Array.isArray(hangul.warnings) && hangul.warnings.length) parts.push(`주의 ${hangul.warnings.length}`);
  return parts.join(' · ');
}

function hangulRuntimeToolRows(hangul){
  if (!hangul) return [];
  return [
    ['PDF 변환', hangul.pdfConverters || []],
    ['HWP 렌더', hangul.hwpRenderers || []],
    ['HWP→HWPX', hangul.hwpToHwpxConverters || []],
    ['텍스트', hangul.textExtractors || []],
    ['HTML', hangul.htmlConverters || []],
    ['Chrome PDF', hangul.chromePdfConverters || []],
  ].map(([label, tools]) => ({
    label,
    count: Array.isArray(tools) ? tools.length : 0,
    names: Array.isArray(tools) ? tools.map(tool => tool?.name).filter(Boolean).join(', ') : '',
  }));
}

function listUnique(values){
  return Array.from(new Set(values));
}

function riskFlagsFor(entity){
  const flags = entity?.riskFlags || entity?.risk_flags || [];
  if (!Array.isArray(flags)) return [];
  return flags.map(flag => String(flag || '').trim()).filter(Boolean);
}

function hasRiskFlag(entity, flag){
  const key = String(flag || '').trim();
  return key ? riskFlagsFor(entity).includes(key) : false;
}

function isSupplementalProblem(problem){
  const helper = globalThis.EDB_REVIEW_FILTERS?.isSupplementalProblem;
  if (typeof helper === 'function') return helper(problem);
  if (hasRiskFlag(problem, 'marker_document_continuation')) return true;
  if (problem?.metadata?.marker_document_continuation) return true;
  const id = String(problem?.id || problem?.problem_id || '');
  return id.endsWith('-continuation');
}

function passageGroupIdFor(problem){
  const helper = globalThis.EDB_REVIEW_FILTERS?.passageGroupIdFor;
  if (typeof helper === 'function') return helper(problem);
  return String(
    problem?.passageGroupId
    || problem?.passage_group_id
    || problem?.metadata?.passageGroupId
    || problem?.metadata?.passage_group_id
    || ''
  ).trim();
}

function isPassageProblem(problem){
  const helper = globalThis.EDB_REVIEW_FILTERS?.isPassageProblem;
  if (typeof helper === 'function') return helper(problem);
  return Boolean(passageGroupIdFor(problem));
}

function problemMatchesReviewFilter(problem, filter, options = {}){
  const helper = globalThis.EDB_REVIEW_FILTERS?.problemMatchesReviewFilter;
  if (typeof helper === 'function') return helper(problem, filter, options);
  const normalizedFilter = String(filter || 'all').trim() || 'all';
  if (normalizedFilter === 'all') return true;
  if (normalizedFilter === 'supplemental') return isSupplementalProblem(problem);
  if (normalizedFilter === 'passage') return isPassageProblem(problem);
  if (normalizedFilter === 'passage-review') {
    const rawIds = options?.passageReviewProblemIds || options?.passage_review_problem_ids || [];
    const idSet = rawIds instanceof Set
      ? rawIds
      : new Set(Array.isArray(rawIds) ? rawIds.map(id => String(id || '').trim()).filter(Boolean) : []);
    const problemId = String(problem?.id || problem?.problem_id || '').trim();
    return Boolean(problemId && idSet.has(problemId));
  }
  return deriveProblemStatus(problem) === normalizedFilter;
}

function countSessionProblems(problems){
  const list = Array.isArray(problems) ? problems : [];
  const supplemental = list.filter(isSupplementalProblem).length;
  return {
    total: list.length,
    core: Math.max(0, list.length - supplemental),
    supplemental,
  };
}

function sessionProblemCounts(session, problemOverride = null){
  if (Array.isArray(problemOverride)) return countSessionProblems(problemOverride);
  const fallback = countSessionProblems(session?.problems || []);
  const total = Number(session?.detected_problem_count ?? session?.detectedProblemCount);
  const supplemental = Number(session?.supplemental_item_count ?? session?.supplementalItemCount);
  const core = Number(session?.core_problem_count ?? session?.coreProblemCount);
  if (Number.isFinite(total) && Number.isFinite(core) && Number.isFinite(supplemental)) {
    return {
      total: Math.max(0, total),
      core: Math.max(0, core),
      supplemental: Math.max(0, supplemental),
    };
  }
  return fallback;
}

function sessionWarningMessages(session){
  const warnings = Array.isArray(session?.warning_messages)
    ? session.warning_messages
    : Array.isArray(session?.warningMessages)
      ? session.warningMessages
      : [];
  return warnings.map(message => String(message || '').trim()).filter(Boolean);
}

function collectReviewStatusCounts(session){
  const counts = { all: 0, normal: 0, check_needed: 0, failed: 0 };
  (session?.problems || []).forEach(problem => {
    const status = deriveProblemStatus(problem);
    counts.all += 1;
    counts[status] = (counts[status] || 0) + 1;
  });
  return counts;
}

function collectRiskFlagCounts(session){
  const counts = {};
  const addFlags = (flags) => {
    if (!Array.isArray(flags)) return;
    flags.forEach(flag => {
      const key = String(flag || '').trim();
      if (key) counts[key] = (counts[key] || 0) + 1;
    });
  };
  (session?.problems || []).forEach(problem => addFlags(riskFlagsFor(problem)));
  (session?.pages || []).forEach(page => addFlags(riskFlagsFor(page)));
  return counts;
}

function normalizeRiskFlagItems(rawItems, fallbackCounts){
  const sourceItems = Array.isArray(rawItems)
    ? rawItems
    : Object.entries(fallbackCounts || {}).map(([flag, count]) => ({ flag, count }));
  return sourceItems
    .map(item => ({
      flag: String(item?.flag || '').trim(),
      count: Number(item?.count || 0),
    }))
    .filter(item => item.flag && Number.isFinite(item.count) && item.count > 0)
    .sort((a, b) => b.count - a.count || a.flag.localeCompare(b.flag))
    .slice(0, 3);
}

function countRiskFilterMatches(session, flag){
  const key = String(flag || '').trim();
  if (!key) return 0;
  const problems = Array.isArray(session?.problems) ? session.problems : [];
  const problemMatchCount = problems.filter(problem => hasRiskFlag(problem, key)).length;
  if (problemMatchCount > 0) return problemMatchCount;
  const pageById = new Map((session?.pages || []).map(page => [String(page?.id || ''), page]));
  return problems.filter(problem => hasRiskFlag(pageById.get(String(problem?.sourcePageId || '')), key)).length;
}

function normalizeFilterableRiskFlagItems(session, fallbackCounts){
  const problems = Array.isArray(session?.problems) ? session.problems : [];
  const total = problems.length;
  const items = Object.keys(fallbackCounts || {})
    .map(flag => ({
      flag,
      count: countRiskFilterMatches(session, flag),
      diagnosticCount: Number(fallbackCounts[flag] || 0),
    }))
    .filter(item => item.flag && item.count > 0 && (!total || item.count < total))
    .sort((a, b) => b.count - a.count || b.diagnosticCount - a.diagnosticCount || a.flag.localeCompare(b.flag))
    .slice(0, 3);
  return items.length ? items : normalizeRiskFlagItems(null, fallbackCounts);
}

function hasHwpCountMatch(summary){
  return Boolean(
    summary?.hwpTextProblemCountMatches
    || summary?.hwpLayoutProblemCountMatches
    || summary?.hwp_text_problem_count_matches
    || summary?.hwp_layout_problem_count_matches
    || summary?.hwpTextProblemCountStatus === 'match'
    || summary?.hwpLayoutProblemCountStatus === 'match'
    || summary?.hwp_text_problem_count_status === 'match'
    || summary?.hwp_layout_problem_count_status === 'match'
  );
}

function filterActionableRiskFlagCounts(counts, options = {}){
  const dismissed = new Set(NON_ACTIONABLE_RISK_FLAGS);
  if (options.hwpCountsMatch) {
    HWP_COUNT_MATCH_DISMISSIBLE_RISK_FLAGS.forEach(flag => dismissed.add(flag));
  }
  return Object.fromEntries(
    Object.entries(counts || {})
      .filter(([flag, count]) => !dismissed.has(flag) && Number(count) > 0)
  );
}

function collectActionableReviewProblemIds(session, actionableRiskFlagCounts){
  const actionableFlags = new Set(
    Object.entries(actionableRiskFlagCounts || {})
      .filter(([, count]) => Number(count) > 0)
      .map(([flag]) => String(flag || '').trim())
      .filter(Boolean)
  );
  const matched = new Set();
  const problems = Array.isArray(session?.problems) ? session.problems : [];
  problems.forEach((problem, index) => {
    const id = String(problem?.id || problem?.problem_id || `problem-index-${index}`);
    if (deriveProblemStatus(problem) === 'failed') matched.add(id);
    const flags = riskFlagsFor(problem);
    if (flags.some(flag => actionableFlags.has(flag))) matched.add(id);
  });
  (session?.pages || []).forEach(page => {
    const flags = riskFlagsFor(page);
    if (!flags.some(flag => actionableFlags.has(flag))) return;
    const ids = Array.isArray(page?.problemIds || page?.problem_ids)
      ? (page.problemIds || page.problem_ids).map(id => String(id || '')).filter(Boolean)
      : [];
    if (ids.length) {
      ids.forEach(id => matched.add(id));
    } else {
      matched.add(`page:${String(page?.id || matched.size)}`);
    }
  });
  return matched;
}

function collectUnresolvedReviewProblemIds(session, actionableRiskFlagCounts){
  const matched = collectActionableReviewProblemIds(session, actionableRiskFlagCounts);
  const problems = Array.isArray(session?.problems) ? session.problems : [];
  problems.forEach((problem, index) => {
    const id = String(problem?.id || problem?.problem_id || `problem-index-${index}`);
    if (deriveProblemStatus(problem) !== 'normal') matched.add(id);
  });
  (session?.pages || []).forEach(page => {
    const status = normalizeReviewStatus(page?.reviewStatus || page?.review_status);
    if (!status || status === 'normal') return;
    const ids = Array.isArray(page?.problemIds || page?.problem_ids)
      ? (page.problemIds || page.problem_ids).map(id => String(id || '')).filter(Boolean)
      : [];
    if (ids.length) {
      ids.forEach(id => matched.add(id));
    } else {
      matched.add(`page:${String(page?.id || matched.size)}`);
    }
  });
  return matched;
}

function countActionableReviewMatches(session, actionableRiskFlagCounts, failedCount = 0){
  const matched = collectActionableReviewProblemIds(session, actionableRiskFlagCounts);
  return Math.max(matched.size, Math.max(0, Number(failedCount) || 0));
}

function collectPassageGroupSummary(session){
  const groups = new Map();
  const problems = Array.isArray(session?.problems) ? session.problems : [];
  problems.forEach(problem => {
    const groupId = passageGroupIdFor(problem);
    if (!groupId) return;
    const group = groups.get(groupId) || {
      id: groupId,
      problemCount: 0,
      detectedProblemCount: 0,
      fragmentProblemCount: 0,
      problemNumbers: new Set(),
      sourcePageIds: new Set(),
      range: problem.passageRange || problem.passage_range || null,
      continuesAcrossPages: false,
      continuationBlockIds: new Set(),
    };
    group.detectedProblemCount += 1;
    const role = String(problem.passageRole || problem.passage_role || '').trim();
    const problemId = String(problem.id || problem.problem_id || '').trim();
    const riskFlags = Array.isArray(problem.riskFlags || problem.risk_flags) ? (problem.riskFlags || problem.risk_flags) : [];
    const isFragment = role === 'passage_fragment'
      || problemId.endsWith('-continuation')
      || riskFlags.map(flag => String(flag || '').trim()).includes('marker_document_continuation');
    if (isFragment) {
      group.fragmentProblemCount += 1;
    } else {
      group.problemCount += 1;
    }
    const explicitNumbers = problem.passageChildProblemNumbers || problem.passage_child_problem_numbers || [];
    if (Array.isArray(explicitNumbers)) {
      explicitNumbers.forEach(number => {
        const normalized = Number(number);
        if (Number.isFinite(normalized) && normalized > 0) group.problemNumbers.add(normalized);
      });
    }
    const problemNumber = Number(problem.problemNumber ?? problem.problem_number);
    if (!isFragment && Number.isFinite(problemNumber) && problemNumber > 0) {
      group.problemNumbers.add(problemNumber);
    }
    const rawPageIds = problem.passageSourcePageIds || problem.passage_source_page_ids || [];
    if (Array.isArray(rawPageIds)) {
      rawPageIds.forEach(pageId => {
        const normalized = String(pageId || '').trim();
        if (normalized) group.sourcePageIds.add(normalized);
      });
    }
    const sourcePageId = String(problem.sourcePageId || problem.source_page_id || '').trim();
    if (sourcePageId) group.sourcePageIds.add(sourcePageId);
    group.continuesAcrossPages = group.continuesAcrossPages
      || Boolean(problem.passageContinuesAcrossPages || problem.passage_continues_across_pages);
    const rawContinuationBlockIds = problem.passagePreQuestionContinuationBlockIds
      || problem.passage_pre_question_continuation_block_ids
      || [];
    if (Array.isArray(rawContinuationBlockIds)) {
      rawContinuationBlockIds.forEach(blockId => {
        const normalized = String(blockId || '').trim();
        if (normalized) group.continuationBlockIds.add(normalized);
      });
    }
    groups.set(groupId, group);
  });
  const items = Array.from(groups.values()).map(group => {
    const problemNumbers = Array.from(group.problemNumbers).sort((a, b) => a - b);
    const problemCount = problemNumbers.length || group.problemCount;
    return {
      id: group.id,
      problemCount,
      detectedProblemCount: group.detectedProblemCount,
      fragmentProblemCount: group.fragmentProblemCount,
      problemNumbers,
      sourcePageIds: Array.from(group.sourcePageIds),
      sourcePageCount: group.sourcePageIds.size,
      range: group.range,
      continuesAcrossPages: group.continuesAcrossPages || group.sourcePageIds.size > 1,
      continuationBlockCount: group.continuationBlockIds.size,
    };
  });
  return {
    passageGroups: items,
    passageGroupCount: items.length,
    passageProblemCount: items.reduce((total, group) => total + group.problemCount, 0),
    crossPagePassageGroupCount: items.filter(group => group.continuesAcrossPages).length,
    passageContinuationBlockCount: items.reduce((total, group) => total + group.continuationBlockCount, 0),
  };
}

function passageReviewItemProblemIds(item){
  return [
    ...(Array.isArray(item?.problemIds || item?.problem_ids) ? (item.problemIds || item.problem_ids) : []),
    ...(Array.isArray(item?.fragmentProblemIds || item?.fragment_problem_ids) ? (item.fragmentProblemIds || item.fragment_problem_ids) : []),
  ]
    .map(id => String(id || '').trim())
    .filter(Boolean);
}

function passageReviewItemReasonCodes(item){
  const rawCodes = Array.isArray(item?.reviewReasonCodes)
    ? item.reviewReasonCodes
    : Array.isArray(item?.review_reason_codes)
      ? item.review_reason_codes
      : [];
  return rawCodes.map(code => String(code || '').trim()).filter(Boolean);
}

function passageReviewReasonSummary(items){
  const seen = new Set();
  const labels = [];
  items.forEach(item => {
    passageReviewItemReasonCodes(item).forEach(code => {
      if (seen.has(code)) return;
      seen.add(code);
      const label = passageReviewReasonLabel(code);
      if (label) labels.push(label);
    });
  });
  return labels.join(', ');
}

function collectPassageReviewSummary(session, options = {}){
  const rawItems = Array.isArray(session?.passageReviewItems)
    ? session.passageReviewItems
    : Array.isArray(session?.passage_review_items)
      ? session.passage_review_items
      : [];
  const passageReviewItems = rawItems.filter(item => item && typeof item === 'object');
  const actionableProblemIds = options.actionableProblemIds instanceof Set
    ? options.actionableProblemIds
    : null;
  const unresolvedPassageReviewItems = actionableProblemIds
    ? passageReviewItems.filter(item => {
      const ids = passageReviewItemProblemIds(item);
      return ids.length === 0 || ids.some(id => actionableProblemIds.has(id));
    })
    : passageReviewItems;
  const explicitCount = Number(session?.passageReviewItemCount ?? session?.passage_review_item_count);
  const passageReviewItemCount = actionableProblemIds
    ? unresolvedPassageReviewItems.length
    : (
      Number.isFinite(explicitCount)
        ? Math.max(0, explicitCount)
        : unresolvedPassageReviewItems.length
    );
  const explicitCrossPageCount = Number(
    session?.crossPagePassageReviewItemCount
    ?? session?.cross_page_passage_review_item_count
  );
  const crossPagePassageReviewItemCount = actionableProblemIds
    ? unresolvedPassageReviewItems.filter(item => item.continuesAcrossPages || item.continues_across_pages).length
    : (
      Number.isFinite(explicitCrossPageCount)
        ? Math.max(0, explicitCrossPageCount)
        : unresolvedPassageReviewItems.filter(item => item.continuesAcrossPages || item.continues_across_pages).length
    );
  const passageReviewLabel = passageReviewItemCount > 0
    ? [
      `긴 지문 검수 ${passageReviewItemCount}`,
      crossPagePassageReviewItemCount > 0 ? `페이지 넘김 ${crossPagePassageReviewItemCount}` : '',
    ].filter(Boolean).join(' · ')
    : '';
  const passageReviewPreview = unresolvedPassageReviewItems
    .map(item => String(item.numberLabel || item.number_label || item.groupId || item.group_id || '').trim())
    .filter(Boolean)
    .slice(0, 5)
    .join(', ');
  const passageReviewReasonLabel = passageReviewReasonSummary(unresolvedPassageReviewItems);
  const passageReviewProblemIds = Array.from(new Set(
    unresolvedPassageReviewItems.flatMap(item => passageReviewItemProblemIds(item))
  ));
  return {
    passageReviewItems: unresolvedPassageReviewItems,
    passageReviewProblemIds,
    passageReviewItemCount,
    crossPagePassageReviewItemCount,
    passageReviewLabel,
    passageReviewPreview,
    passageReviewReasonLabel,
  };
}

function nonNegativeNumber(value){
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
}

function normalizeAiStageSummaries(session){
  const rawSummary = session?.ai_summary || session?.aiSummary || {};
  const rawStages = Array.isArray(rawSummary?.stages || rawSummary?.aiStages)
    ? (rawSummary.stages || rawSummary.aiStages)
    : [];
  return rawStages
    .filter(stage => stage && typeof stage === 'object')
    .map(stage => {
      const statusCounts = stage.status_counts && typeof stage.status_counts === 'object'
        ? stage.status_counts
        : (stage.statusCounts && typeof stage.statusCounts === 'object' ? stage.statusCounts : {});
      return {
        stage: String(stage.stage || '').trim(),
        order: nonNegativeNumber(stage.order || 999),
        label: String(stage.label || stage.stage || 'AI 단계').trim(),
        provider: String(stage.provider || '').trim(),
        pageCount: nonNegativeNumber(stage.page_count ?? stage.pageCount),
        usedPageCount: nonNegativeNumber(stage.used_page_count ?? stage.usedPageCount),
        attemptedPageCount: nonNegativeNumber(stage.attempted_page_count ?? stage.attemptedPageCount),
        appliedPageCount: nonNegativeNumber(stage.applied_page_count ?? stage.appliedPageCount),
        eligibleBlockCount: nonNegativeNumber(stage.eligible_block_count ?? stage.eligibleBlockCount),
        processedBlockCount: nonNegativeNumber(stage.processed_block_count ?? stage.processedBlockCount),
        apiCallBlockCount: nonNegativeNumber(stage.api_call_block_count ?? stage.apiCallBlockCount),
        cacheHitCount: nonNegativeNumber(stage.cache_hit_count ?? stage.cacheHitCount),
        cacheMissCount: nonNegativeNumber(stage.cache_miss_count ?? stage.cacheMissCount),
        skippedBlockCount: nonNegativeNumber(stage.skipped_block_count ?? stage.skippedBlockCount),
        attemptedBlockCount: nonNegativeNumber(stage.attempted_block_count ?? stage.attemptedBlockCount),
        appliedBlockCount: nonNegativeNumber(stage.applied_block_count ?? stage.appliedBlockCount),
        statusCounts,
      };
    })
    .filter(stage => stage.stage)
    .sort((a, b) => a.order - b.order || a.stage.localeCompare(b.stage));
}

function aiStageChipText(stage){
  if (stage.stage === 'ocr') {
    return `${stage.label} · 블록 ${stage.processedBlockCount || stage.eligibleBlockCount}`;
  }
  if (stage.stage === 'ocr_escalation') {
    return `${stage.label} · 보강 ${stage.appliedBlockCount}/${stage.attemptedBlockCount}`;
  }
  if (stage.stage === 'page_repair') {
    return `${stage.label} · 적용 ${stage.appliedPageCount}/${stage.usedPageCount || stage.pageCount}`;
  }
  return `${stage.label} · ${stage.usedPageCount || stage.pageCount}`;
}

function aiStageTooltip(stage){
  const statuses = Object.entries(stage.statusCounts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([status, count]) => `${status} ${count}`)
    .join(', ');
  return [
    stage.label,
    stage.provider ? `provider: ${stage.provider}` : '',
    `pages: ${stage.usedPageCount}/${stage.pageCount}`,
    stage.eligibleBlockCount ? `eligible blocks: ${stage.eligibleBlockCount}` : '',
    stage.processedBlockCount ? `processed blocks: ${stage.processedBlockCount}` : '',
    stage.apiCallBlockCount ? `API calls: ${stage.apiCallBlockCount}` : '',
    stage.cacheHitCount ? `cache hits: ${stage.cacheHitCount}` : '',
    stage.attemptedBlockCount ? `escalation attempts: ${stage.attemptedBlockCount}` : '',
    stage.appliedBlockCount ? `applied blocks: ${stage.appliedBlockCount}` : '',
    stage.attemptedPageCount ? `attempted pages: ${stage.attemptedPageCount}` : '',
    stage.appliedPageCount ? `applied pages: ${stage.appliedPageCount}` : '',
    statuses ? `status: ${statuses}` : '',
  ].filter(Boolean).join(' · ');
}

function sessionReviewSummary(session){
  const raw = session?.review_summary || session?.reviewSummary || {};
  const counts = sessionProblemCounts(session);
  const passageSummary = collectPassageGroupSummary(session);
  const fallbackStatusCounts = collectReviewStatusCounts(session);
  const reviewStatusCounts = {
    all: Number(fallbackStatusCounts.all) || 0,
    normal: Number(fallbackStatusCounts.normal) || 0,
    check_needed: Number(fallbackStatusCounts.check_needed) || 0,
    failed: Number(fallbackStatusCounts.failed) || 0,
  };
  const rawSupplementalStatusCounts = raw.supplementalReviewStatusCounts && typeof raw.supplementalReviewStatusCounts === 'object'
    ? raw.supplementalReviewStatusCounts
    : {};
  const rawCoreStatusCounts = raw.coreReviewStatusCounts && typeof raw.coreReviewStatusCounts === 'object'
    ? raw.coreReviewStatusCounts
    : {};
  const fallbackRiskFlagCounts = collectRiskFlagCounts(session);
  const riskFlagCounts = fallbackRiskFlagCounts;
  const actionableRiskFlagCounts = filterActionableRiskFlagCounts(riskFlagCounts, { hwpCountsMatch: hasHwpCountMatch(raw) });
  const actionableReviewProblemIds = collectActionableReviewProblemIds(session, actionableRiskFlagCounts);
  const unresolvedReviewProblemIds = collectUnresolvedReviewProblemIds(session, actionableRiskFlagCounts);
  const actionableNeedsReviewCount = countActionableReviewMatches(session, actionableRiskFlagCounts, reviewStatusCounts.failed);
  const passageReviewSummary = collectPassageReviewSummary(session, { actionableProblemIds: unresolvedReviewProblemIds });
  const topRiskFlags = normalizeFilterableRiskFlagItems(session, actionableRiskFlagCounts);
  const warningMessages = Array.isArray(raw.warningMessages)
    ? raw.warningMessages.map(message => String(message || '').trim()).filter(Boolean)
    : sessionWarningMessages(session);
  const extractorMap = raw.hwpTextExtractors && typeof raw.hwpTextExtractors === 'object'
    ? raw.hwpTextExtractors
    : {};
  const layoutExtractorMap = raw.hwpLayoutExtractors && typeof raw.hwpLayoutExtractors === 'object'
    ? raw.hwpLayoutExtractors
    : {};
  const extractorNames = Object.entries(extractorMap)
    .filter(([, count]) => Number(count) > 0)
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(([name, count]) => `${name} ${count}`);
  const layoutExtractorNames = Object.entries(layoutExtractorMap)
    .filter(([, count]) => Number(count) > 0)
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(([name, count]) => `${name} ${count}`);
  const hwpTextProblemSignalCount = Number(raw.hwpTextProblemSignalCount || 0);
  const hwpTextProblemDelta = Number(raw.hwpTextProblemDelta || 0);
  const hwpTextProblemCountStatus = String(raw.hwpTextProblemCountStatus || 'unknown');
  const hwpLayoutProblemSignalCount = Number(raw.hwpLayoutProblemSignalCount || 0);
  const hwpLayoutProblemDelta = Number(raw.hwpLayoutProblemDelta || 0);
  const hwpLayoutProblemCountStatus = String(raw.hwpLayoutProblemCountStatus || 'unknown');
  const hwpCacheHitPageCount = Number(raw.hwpCacheHitPageCount ?? raw.hwp_cache_hit_page_count ?? 0);
  const hwpRendererCacheHitCount = Number(raw.hwpRendererCacheHitCount ?? raw.hwp_renderer_cache_hit_count ?? 0);
  const hwpNormalizedCacheHitCount = Number(raw.hwpNormalizedCacheHitCount ?? raw.hwp_normalized_cache_hit_count ?? 0);
  const hwpProblemCountMismatchCount = Number(
    raw.hwpProblemCountMismatchCount
    ?? raw.hwp_problem_count_mismatch_count
    ?? riskFlagCounts.hwp_problem_count_mismatch
    ?? 0
  );
  const hwpOversegmentationCount = Number(
    raw.hwpOversegmentationCount
    ?? raw.hwp_oversegmentation_count
    ?? riskFlagCounts.hwp_oversegmentation
    ?? 0
  );
  const aiStages = normalizeAiStageSummaries(session);
  const duplicateProblemNumberGroups = Array.isArray(session?.duplicateProblemNumberGroups)
    ? session.duplicateProblemNumberGroups
    : Array.isArray(session?.duplicate_problem_number_groups)
      ? session.duplicate_problem_number_groups
      : [];
  const blockingDuplicateProblemNumberGroups = Array.isArray(session?.blockingDuplicateProblemNumberGroups)
    ? session.blockingDuplicateProblemNumberGroups
    : Array.isArray(session?.blocking_duplicate_problem_number_groups)
      ? session.blocking_duplicate_problem_number_groups
      : duplicateProblemNumberGroups.filter(group => group?.blocking !== false);
  const duplicateProblemNumberLabel = duplicateProblemNumberGroups
    .map(group => {
      const label = String(group?.numberLabel || group?.number_label || '').trim();
      const occurrences = Number(group?.occurrencesPerNumber ?? group?.occurrences_per_number ?? 0);
      return label && Number.isFinite(occurrences) && occurrences > 1 ? `${label} x${occurrences}` : '';
    })
    .filter(Boolean)
    .join(', ');
  const sourceProblemOverlapGroups = Array.isArray(session?.sourceProblemOverlapGroups)
    ? session.sourceProblemOverlapGroups
    : Array.isArray(session?.source_problem_overlap_groups)
      ? session.source_problem_overlap_groups
      : [];
  const sourceProblemOverlapLabel = sourceProblemOverlapGroups
    .map(group => {
      const pageId = String(group?.sourcePageId || group?.source_page_id || '').trim();
      const ratio = Number(group?.overlapAreaRatio ?? group?.overlap_area_ratio ?? 0);
      const percent = Number.isFinite(ratio) && ratio > 0 ? `${Math.round(ratio * 100)}%` : '';
      return [pageId, percent].filter(Boolean).join(' ');
    })
    .filter(Boolean)
    .join(', ') || `${sourceProblemOverlapGroups.length}`;
  const passageGroupSourceReuseGroups = Array.isArray(session?.passageGroupSourceReuseGroups)
    ? session.passageGroupSourceReuseGroups
    : Array.isArray(session?.passage_group_source_reuse_groups)
      ? session.passage_group_source_reuse_groups
      : [];
  const explicitPassageGroupSourceReuseCount = Number(
    session?.passageGroupSourceReuseGroupCount
    ?? session?.passage_group_source_reuse_group_count
    ?? passageGroupSourceReuseGroups.length
  );
  const passageGroupSourceReuseGroupCount = Number.isFinite(explicitPassageGroupSourceReuseCount)
    ? Math.max(0, explicitPassageGroupSourceReuseCount)
    : passageGroupSourceReuseGroups.length;
  const passageGroupSourceReuseLabel = passageGroupSourceReuseGroups
    .map(group => {
      const groupId = String(group?.passageGroupId || group?.passage_group_id || '').trim();
      const ratio = Number(group?.overlapAreaRatio ?? group?.overlap_area_ratio ?? 0);
      const percent = Number.isFinite(ratio) && ratio > 0 ? `${Math.round(ratio * 100)}%` : '';
      return [groupId, percent].filter(Boolean).join(' ');
    })
    .filter(Boolean)
    .join(', ') || `${passageGroupSourceReuseGroupCount}`;
  return {
    counts,
    reviewStatusCounts,
    coreReviewStatusCounts: rawCoreStatusCounts,
    supplementalReviewStatusCounts: rawSupplementalStatusCounts,
    needsReviewCount: Math.max(0, reviewStatusCounts.check_needed + reviewStatusCounts.failed),
    actionableNeedsReviewCount,
    riskFlagCounts,
    actionableRiskFlagCounts,
    topRiskFlags,
    warningCount: Number.isFinite(Number(raw.warningCount)) ? Number(raw.warningCount) : warningMessages.length,
    warningPreview: warningMessages[0] || '',
    hwpTextExtractorLabel: extractorNames.join(', '),
    hwpTextProblemSignalCount: Number.isFinite(hwpTextProblemSignalCount) ? Math.max(0, hwpTextProblemSignalCount) : 0,
    hwpTextProblemCountStatus,
    hwpTextProblemCountMessage: String(raw.hwpTextProblemCountMessage || ''),
    hwpTextProblemDelta: Number.isFinite(hwpTextProblemDelta) ? hwpTextProblemDelta : 0,
    hwpLayoutExtractorLabel: layoutExtractorNames.join(', '),
    hwpLayoutProblemSignalCount: Number.isFinite(hwpLayoutProblemSignalCount) ? Math.max(0, hwpLayoutProblemSignalCount) : 0,
    hwpLayoutProblemCountStatus,
    hwpLayoutProblemCountMessage: String(raw.hwpLayoutProblemCountMessage || ''),
    hwpLayoutProblemDelta: Number.isFinite(hwpLayoutProblemDelta) ? hwpLayoutProblemDelta : 0,
    hwpCacheHitPageCount: Number.isFinite(hwpCacheHitPageCount) ? Math.max(0, hwpCacheHitPageCount) : 0,
    hwpRendererCacheHitCount: Number.isFinite(hwpRendererCacheHitCount) ? Math.max(0, hwpRendererCacheHitCount) : 0,
    hwpNormalizedCacheHitCount: Number.isFinite(hwpNormalizedCacheHitCount) ? Math.max(0, hwpNormalizedCacheHitCount) : 0,
    aiStages,
    hwpProblemCountMismatchCount: Number.isFinite(hwpProblemCountMismatchCount) ? Math.max(0, hwpProblemCountMismatchCount) : 0,
    hwpOversegmentationCount: Number.isFinite(hwpOversegmentationCount) ? Math.max(0, hwpOversegmentationCount) : 0,
    duplicateProblemNumberGroups,
    blockingDuplicateProblemNumberGroups,
    duplicateProblemNumberLabel,
    sourceProblemOverlapGroups,
    sourceProblemOverlapLabel,
    passageGroupSourceReuseGroups,
    passageGroupSourceReuseGroupCount,
    passageGroupSourceReuseLabel,
    passageGroups: passageSummary.passageGroups,
    passageGroupCount: passageSummary.passageGroupCount,
    passageProblemCount: passageSummary.passageProblemCount,
    crossPagePassageGroupCount: passageSummary.crossPagePassageGroupCount,
    passageContinuationBlockCount: passageSummary.passageContinuationBlockCount,
    passageReviewItems: passageReviewSummary.passageReviewItems,
    passageReviewProblemIds: passageReviewSummary.passageReviewProblemIds,
    passageReviewItemCount: passageReviewSummary.passageReviewItemCount,
    crossPagePassageReviewItemCount: passageReviewSummary.crossPagePassageReviewItemCount,
    passageReviewLabel: passageReviewSummary.passageReviewLabel,
    passageReviewPreview: passageReviewSummary.passageReviewPreview,
    passageReviewReasonLabel: passageReviewSummary.passageReviewReasonLabel,
  };
}

function publishReviewWarningMessage(session, publishReviewSummary){
  const actionableNeedsReviewCount = Math.max(0, Number(publishReviewSummary?.actionableNeedsReviewCount) || 0);
  const passageReviewItemCount = Math.max(0, Number(publishReviewSummary?.passageReviewItemCount) || 0);
  const passageReviewProblemIds = new Set(
    Array.isArray(publishReviewSummary?.passageReviewProblemIds)
      ? publishReviewSummary.passageReviewProblemIds.map(id => String(id || '').trim()).filter(Boolean)
      : []
  );
  const unresolvedReviewProblemIds = collectUnresolvedReviewProblemIds(
    session,
    publishReviewSummary?.actionableRiskFlagCounts
  );
  const hasUnresolvedPassageReview = passageReviewItemCount > 0
    && (
      passageReviewProblemIds.size === 0
      || Array.from(passageReviewProblemIds).some(id => unresolvedReviewProblemIds.has(id))
    );
  if (actionableNeedsReviewCount <= 0 && !hasUnresolvedPassageReview) return null;
  const passageReviewLine = hasUnresolvedPassageReview
    ? [
      publishReviewSummary?.passageReviewLabel || `긴 지문 검수 ${passageReviewItemCount}`,
      publishReviewSummary?.passageReviewReasonLabel || '',
      publishReviewSummary?.passageReviewPreview ? `대상 ${publishReviewSummary.passageReviewPreview}` : '',
    ].filter(Boolean).join(' · ')
    : '';
  const reviewCountLine = actionableNeedsReviewCount > 0
    ? `검수 화면에 확인 필요 ${actionableNeedsReviewCount}개가 남아 있습니다.`
    : '검수 화면에 긴 지문 확인 항목이 남아 있습니다.';
  return {
    message: [
      passageReviewLine,
      reviewCountLine,
      '그래도 EDB를 제작할까요?',
    ].filter(Boolean).join('\n'),
    cancelToast: passageReviewLine
      ? '제작을 멈췄어요. 긴 지문 검수 큐를 먼저 확인하세요.'
      : '제작을 멈췄어요. 검수 화면에서 확인 필요 항목을 먼저 확인하세요.',
    reviewFilter: passageReviewLine ? 'passage-review' : 'check_needed',
  };
}

function normalizeEdbParts(raw, fallback = {}){
  const rawParts = Array.isArray(raw?.edbParts)
    ? raw.edbParts
    : Array.isArray(raw?.edb_parts)
      ? raw.edb_parts
      : [];
  const parts = rawParts
    .filter(part => part && typeof part === 'object')
    .map((part, index) => {
      const edbPath = String(part.edbPath || part.edb_path || '').trim();
      const edbFileUri = String(part.edbFileUri || part.edb_file_uri || '').trim();
      const edbFileName = String(
        part.edbFileName
        || part.edb_file_name
        || (edbPath ? edbPath.split(/[\\/]/).pop() : '')
        || `classin_part${String(index + 1).padStart(2, '0')}.edb`
      ).trim();
      const exists = part.edbFileExists ?? part.edb_file_exists;
      return {
        ...part,
        partIndex: Number(part.partIndex ?? part.part_index ?? index + 1) || index + 1,
        part_index: Number(part.partIndex ?? part.part_index ?? index + 1) || index + 1,
        partCount: Number(part.partCount ?? part.part_count ?? rawParts.length) || rawParts.length,
        part_count: Number(part.partCount ?? part.part_count ?? rawParts.length) || rawParts.length,
        edbFileName,
        edb_file_name: edbFileName,
        edbPath,
        edb_path: edbPath,
        edbFileUri,
        edb_file_uri: edbFileUri,
        edbFileExists: exists === undefined ? true : exists !== false,
        edb_file_exists: exists === undefined ? true : exists !== false,
        recordCount: Math.max(0, Number(part.recordCount ?? part.record_count ?? 0) || 0),
        record_count: Math.max(0, Number(part.recordCount ?? part.record_count ?? 0) || 0),
        pageCountHint: Math.max(0, Number(part.pageCountHint ?? part.page_count_hint ?? 0) || 0),
        page_count_hint: Math.max(0, Number(part.pageCountHint ?? part.page_count_hint ?? 0) || 0),
      };
    });
  if (!parts.length && (fallback.edbFileName || fallback.edbPath || fallback.edbFileUri)) {
    const fallbackName = fallback.edbFileName || (fallback.edbPath ? fallback.edbPath.split(/[\\/]/).pop() : 'classin.edb');
    const fallbackExists = fallback.edbFileExists ?? fallback.edb_file_exists;
    parts.push({
      partIndex: 1,
      part_index: 1,
      partCount: 1,
      part_count: 1,
      edbFileName: fallbackName,
      edb_file_name: fallbackName,
      edbPath: fallback.edbPath || '',
      edb_path: fallback.edbPath || '',
      edbFileUri: fallback.edbFileUri || '',
      edb_file_uri: fallback.edbFileUri || '',
      edbFileExists: fallbackExists === undefined ? true : fallbackExists !== false,
      edb_file_exists: fallbackExists === undefined ? true : fallbackExists !== false,
      recordCount: Math.max(0, Number(fallback.recordCount || 0) || 0),
      record_count: Math.max(0, Number(fallback.recordCount || 0) || 0),
      pageCountHint: Math.max(0, Number(fallback.pageCountHint || 0) || 0),
      page_count_hint: Math.max(0, Number(fallback.pageCountHint || 0) || 0),
    });
  }
  return parts;
}

function normalizePublishSummary(raw, session = null){
  const helper = globalThis.EDB_PUBLISH_SUMMARY?.normalizePublishSummary;
  if (typeof helper === 'function') return helper(raw, session);
  if (!raw || typeof raw !== 'object') return null;
  const recordCount = Number(raw.recordCount ?? raw.record_count ?? raw.recordCountActual ?? raw.record_count_actual ?? 0);
  const recordCountActual = Number(raw.recordCountActual ?? raw.record_count_actual ?? recordCount);
  const coreProblemCount = Number(raw.coreProblemCount ?? raw.core_problem_count ?? 0);
  const supplementalItemCount = Number(raw.supplementalItemCount ?? raw.supplemental_item_count ?? 0);
  const pageCountHint = Number(raw.pageCountHint ?? raw.page_count_hint ?? 0);
  const outerSize = Number(raw.outerSize ?? raw.outer_size ?? 0);
  const edbFileName = String(raw.edbFileName || raw.edb_file_name || '').trim();
  const edbPath = String(raw.edbPath || raw.edb_path || '').trim();
  const outputDir = String(raw.outputDir || raw.output_dir || session?.output_dir || session?.outputDir || '').trim();
  const edbFileUri = String(raw.edbFileUri || raw.edb_file_uri || session?.edb_file_uri || session?.edbFileUri || '').trim();
  const edbFileExists = raw.edbFileExists ?? raw.edb_file_exists;
  const edbParts = normalizeEdbParts(raw, {
    edbFileName,
    edbPath,
    edbFileUri,
    edbFileExists,
    recordCount: recordCountActual || recordCount,
    pageCountHint,
  });
  const classinReview = raw.classinReview || raw.classin_review || session?.classinReview || session?.classin_review || {};
  const classinReviewStatus = String(
    raw.classinReviewStatus
    || raw.classin_review_status
    || classinReview.status
    || ''
  ).trim();
  const classinReviewStatusLabel = String(
    raw.classinReviewStatusLabel
    || raw.classin_review_status_label
    || classinReview.statusLabel
    || classinReview.status_label
    || (classinReviewStatus === 'passed' ? 'ClassIn 확인 완료' : '')
  ).trim();
  const classinHandoffUri = String(
    raw.classinHandoffUri
    || raw.classin_handoff_uri
    || session?.classin_handoff_uri
    || session?.classinHandoffUri
    || ''
  ).trim();
  const classinHandoffMarkdownUri = String(
    raw.classinHandoffMarkdownUri
    || raw.classin_handoff_markdown_uri
    || session?.classin_handoff_markdown_uri
    || session?.classinHandoffMarkdownUri
    || ''
  ).trim();
  const classinHandoffStatus = String(
    raw.classinHandoffStatus
    || raw.classin_handoff_status
    || session?.classin_handoff_status
    || session?.classinHandoffStatus
    || ''
  ).trim();
  const rawReadyForClassIn = raw.readyForClassIn ?? raw.ready_for_classin ?? session?.readyForClassIn ?? session?.ready_for_classin;
  const readyForClassIn = rawReadyForClassIn === undefined
    ? classinHandoffStatus === 'ready_for_classin_review'
    : rawReadyForClassIn !== false;
  const classinHandoffStatusLabel = String(
    raw.classinHandoffStatusLabel
    || raw.classin_handoff_status_label
    || (classinHandoffStatus
      ? (readyForClassIn ? 'ClassIn 전달 준비' : 'ClassIn 전달 주의')
      : '')
  ).trim();
  const classinPreflight = raw.classinPreflight || raw.classin_preflight || session?.classinPreflight || session?.classin_preflight || {};
  const classinPreflightStatus = String(
    raw.classinPreflightStatus
    || raw.classin_preflight_status
    || classinPreflight.status
    || ''
  ).trim();
  const classinPreflightIssueCount = Number(
    raw.classinPreflightIssueCount
    ?? raw.classin_preflight_issue_count
    ?? classinPreflight.issueCount
    ?? classinPreflight.issue_count
    ?? 0
  );
  const rawClassinPreflightPassed = raw.classinPreflightPassed ?? raw.classin_preflight_passed ?? classinPreflight.passed;
  const classinPreflightPassed = rawClassinPreflightPassed === undefined
    ? classinPreflightStatus === 'passed'
    : rawClassinPreflightPassed !== false;
  const classinPreflightStatusLabel = String(
    raw.classinPreflightStatusLabel
    || raw.classin_preflight_status_label
    || (classinPreflightStatus
      ? (classinPreflightPassed ? 'ClassIn 사전점검 OK' : `ClassIn 사전점검 주의 ${Number.isFinite(classinPreflightIssueCount) ? Math.max(0, classinPreflightIssueCount) : 0}`)
      : '')
  ).trim();
  const classinPreflightIssueLabelList = Array.isArray(raw.classinPreflightIssueLabels)
    ? raw.classinPreflightIssueLabels.map(label => String(label || '').trim()).filter(Boolean)
    : classinPreflightIssueLabels(classinPreflight);
  const classinPreflightIssueSummaryLabel = String(
    raw.classinPreflightIssueSummaryLabel
    || raw.classin_preflight_issue_summary_label
    || classinPreflightIssueLabelList.join(' · ')
  ).trim();
  const passageGroupsRaw = raw.passageGroups || raw.passage_groups || session?.passageGroups || session?.passage_groups || [];
  const passageGroups = Array.isArray(passageGroupsRaw)
    ? passageGroupsRaw.filter(group => group && typeof group === 'object')
    : [];
  const passageGroupCount = Number(
    raw.passageGroupCount
    ?? raw.passage_group_count
    ?? session?.passageGroupCount
    ?? session?.passage_group_count
    ?? passageGroups.length
  );
  const passageProblemCountFallback = passageGroups.reduce((total, group) => {
    const count = Number(group.problemCount ?? group.problem_count ?? 0);
    return total + (Number.isFinite(count) ? Math.max(0, count) : 0);
  }, 0);
  const passageProblemCount = Number(
    raw.passageProblemCount
    ?? raw.passage_problem_count
    ?? session?.passageProblemCount
    ?? session?.passage_problem_count
    ?? passageProblemCountFallback
  );
  const crossPagePassageGroupCountFallback = passageGroups.filter(group => (
    group.continuesAcrossPages || group.continues_across_pages
  )).length;
  const crossPagePassageGroupCount = Number(
    raw.crossPagePassageGroupCount
    ?? raw.cross_page_passage_group_count
    ?? session?.crossPagePassageGroupCount
    ?? session?.cross_page_passage_group_count
    ?? crossPagePassageGroupCountFallback
  );
  const normalizedPassageGroupCount = Number.isFinite(passageGroupCount) ? Math.max(0, passageGroupCount) : 0;
  const normalizedPassageProblemCount = Number.isFinite(passageProblemCount) ? Math.max(0, passageProblemCount) : 0;
  const normalizedCrossPagePassageGroupCount = Number.isFinite(crossPagePassageGroupCount)
    ? Math.max(0, crossPagePassageGroupCount)
    : 0;
  const passageGroupLabel = String(
    raw.passageGroupLabel
    || raw.passage_group_label
    || (normalizedPassageGroupCount > 0
      ? [
        `긴 지문 그룹 ${normalizedPassageGroupCount}`,
        `${normalizedPassageProblemCount}문항`,
        normalizedCrossPagePassageGroupCount > 0 ? `페이지 넘김 ${normalizedCrossPagePassageGroupCount}` : '',
      ].filter(Boolean).join(' · ')
      : '')
  ).trim();
  const passageReviewItemsRaw = raw.passageReviewItems || raw.passage_review_items || session?.passageReviewItems || session?.passage_review_items || [];
  const passageReviewItems = Array.isArray(passageReviewItemsRaw)
    ? passageReviewItemsRaw.filter(item => item && typeof item === 'object')
    : [];
  const passageReviewItemCount = Number(
    raw.passageReviewItemCount
    ?? raw.passage_review_item_count
    ?? session?.passageReviewItemCount
    ?? session?.passage_review_item_count
    ?? passageReviewItems.length
  );
  const crossPagePassageReviewItemCountFallback = passageReviewItems.filter(item => (
    item.continuesAcrossPages || item.continues_across_pages
  )).length;
  const crossPagePassageReviewItemCount = Number(
    raw.crossPagePassageReviewItemCount
    ?? raw.cross_page_passage_review_item_count
    ?? session?.crossPagePassageReviewItemCount
    ?? session?.cross_page_passage_review_item_count
    ?? crossPagePassageReviewItemCountFallback
  );
  const normalizedPassageReviewItemCount = Number.isFinite(passageReviewItemCount)
    ? Math.max(0, passageReviewItemCount)
    : 0;
  const normalizedCrossPagePassageReviewItemCount = Number.isFinite(crossPagePassageReviewItemCount)
    ? Math.max(0, crossPagePassageReviewItemCount)
    : 0;
  const passageReviewLabel = String(
    raw.passageReviewLabel
    || raw.passage_review_label
    || (normalizedPassageReviewItemCount > 0
      ? [
        `긴 지문 검수 ${normalizedPassageReviewItemCount}`,
        normalizedCrossPagePassageReviewItemCount > 0 ? `페이지 넘김 ${normalizedCrossPagePassageReviewItemCount}` : '',
      ].filter(Boolean).join(' · ')
      : '')
  ).trim();
  const passageReviewReasonLabel = String(
    raw.passageReviewReasonLabel
    || raw.passage_review_reason_label
    || passageReviewReasonSummary(passageReviewItems)
  ).trim();
  const sourceProblemOverlapGroupsRaw = raw.sourceProblemOverlapGroups
    || raw.source_problem_overlap_groups
    || session?.sourceProblemOverlapGroups
    || session?.source_problem_overlap_groups
    || [];
  const sourceProblemOverlapGroups = Array.isArray(sourceProblemOverlapGroupsRaw)
    ? sourceProblemOverlapGroupsRaw.filter(group => group && typeof group === 'object')
    : [];
  const sourceProblemOverlapGroupCount = Number(
    raw.sourceProblemOverlapGroupCount
    ?? raw.source_problem_overlap_group_count
    ?? session?.sourceProblemOverlapGroupCount
    ?? session?.source_problem_overlap_group_count
    ?? sourceProblemOverlapGroups.length
  );
  const normalizedSourceProblemOverlapGroupCount = Number.isFinite(sourceProblemOverlapGroupCount)
    ? Math.max(0, sourceProblemOverlapGroupCount)
    : 0;
  const sourceProblemOverlapDetails = sourceProblemOverlapGroups
    .map(group => {
      const pageId = String(group?.sourcePageId || group?.source_page_id || '').trim();
      const ratio = Number(group?.overlapAreaRatio ?? group?.overlap_area_ratio ?? 0);
      const percent = Number.isFinite(ratio) && ratio > 0 ? `${Math.round(ratio * 100)}%` : '';
      return [pageId, percent].filter(Boolean).join(' ');
    })
    .filter(Boolean)
    .join(', ');
  const sourceProblemOverlapLabel = String(
    raw.sourceProblemOverlapLabel
    || raw.source_problem_overlap_label
    || (normalizedSourceProblemOverlapGroupCount > 0
      ? [`원본 겹침 ${normalizedSourceProblemOverlapGroupCount}`, sourceProblemOverlapDetails]
        .filter(Boolean)
        .join(' · ')
      : '')
  ).trim();
  const passageGroupSourceReuseGroupsRaw = raw.passageGroupSourceReuseGroups
    || raw.passage_group_source_reuse_groups
    || session?.passageGroupSourceReuseGroups
    || session?.passage_group_source_reuse_groups
    || [];
  const passageGroupSourceReuseGroups = Array.isArray(passageGroupSourceReuseGroupsRaw)
    ? passageGroupSourceReuseGroupsRaw.filter(group => group && typeof group === 'object')
    : [];
  const passageGroupSourceReuseGroupCount = Number(
    raw.passageGroupSourceReuseGroupCount
    ?? raw.passage_group_source_reuse_group_count
    ?? session?.passageGroupSourceReuseGroupCount
    ?? session?.passage_group_source_reuse_group_count
    ?? passageGroupSourceReuseGroups.length
  );
  const normalizedPassageGroupSourceReuseGroupCount = Number.isFinite(passageGroupSourceReuseGroupCount)
    ? Math.max(0, passageGroupSourceReuseGroupCount)
    : 0;
  const passageGroupSourceReuseDetails = passageGroupSourceReuseGroups
    .map(group => {
      const groupId = String(group?.passageGroupId || group?.passage_group_id || '').trim();
      const ratio = Number(group?.overlapAreaRatio ?? group?.overlap_area_ratio ?? 0);
      const percent = Number.isFinite(ratio) && ratio > 0 ? `${Math.round(ratio * 100)}%` : '';
      return [groupId, percent].filter(Boolean).join(' ');
    })
    .filter(Boolean)
    .join(', ');
  const passageGroupSourceReuseLabel = String(
    raw.passageGroupSourceReuseLabel
    || raw.passage_group_source_reuse_label
    || (normalizedPassageGroupSourceReuseGroupCount > 0
      ? [`지문 원본 중복 ${normalizedPassageGroupSourceReuseGroupCount}`, passageGroupSourceReuseDetails]
        .filter(Boolean)
        .join(' · ')
      : '')
  ).trim();
  const layoutDiagnostics = (
    raw.layoutDiagnostics
    || raw.layout_diagnostics
    || session?.layoutDiagnostics
    || session?.layout_diagnostics
    || {}
  );
  const layoutAutoExtendedCount = Number(
    layoutDiagnostics.autoExtendedCount
    ?? layoutDiagnostics.auto_extended_count
    ?? 0
  );
  const layoutOverlapRiskCount = Number(
    layoutDiagnostics.overlapRiskCount
    ?? layoutDiagnostics.overlap_risk_count
    ?? 0
  );
  const layoutMaxRenderedPages = Number(
    layoutDiagnostics.maxRenderedHeightPages
    ?? layoutDiagnostics.max_rendered_height_pages
    ?? 0
  );
  const layoutDiagnosticsFallbackLabel = [
    Number.isFinite(layoutAutoExtendedCount) && layoutAutoExtendedCount > 0 ? `긴 이미지 자동 확장 ${layoutAutoExtendedCount}` : '',
    (Number.isFinite(layoutAutoExtendedCount) && layoutAutoExtendedCount > 0
      || Number.isFinite(layoutOverlapRiskCount) && layoutOverlapRiskCount > 0)
      && Number.isFinite(layoutMaxRenderedPages)
      && layoutMaxRenderedPages > 0
      ? `최대 ${layoutMaxRenderedPages.toFixed(2)}p`
      : '',
    Number.isFinite(layoutOverlapRiskCount) && layoutOverlapRiskCount > 0 ? `겹침 위험 ${layoutOverlapRiskCount}` : '',
  ].filter(Boolean).join(' · ');
  const layoutDiagnosticsLabel = String(
    raw.layoutDiagnosticsLabel
    || raw.layout_diagnostics_label
    || layoutDiagnostics.label
    || layoutDiagnosticsFallbackLabel
    || ''
  ).trim();
  const outputDirExists = raw.outputDirExists ?? raw.output_dir_exists;
  if (!edbFileName && !edbPath && !edbFileUri && !edbParts.length) return null;
  const normalizedCore = Number.isFinite(coreProblemCount) ? Math.max(0, coreProblemCount) : 0;
  const normalizedSupplemental = Number.isFinite(supplementalItemCount) ? Math.max(0, supplementalItemCount) : 0;
  const fallbackRecordCount = Number.isFinite(recordCount) ? Math.max(0, recordCount) : 0;
  const explicitRecordCountLabel = String(raw.recordCountLabel || raw.record_count_label || '').trim();
  const normalizedEdbPartCount = edbParts.length
    ? edbParts.length
    : Math.max(0, Number(raw.edbPartCount ?? raw.edb_part_count) || 0);
  const normalizedEdbSplit = edbParts.length
    ? edbParts.length > 1
    : Boolean(raw.edbSplit ?? raw.edb_split);
  const summary = {
    validated: raw.validated !== false,
    statusLabel: String(raw.statusLabel || raw.status_label || '제작 완료'),
    edbFileName: edbFileName || (edbPath ? edbPath.split('/').pop() : 'classin.edb'),
    edbPath,
    edbFileUri,
    edbParts,
    edbPartCount: normalizedEdbPartCount,
    edbSplit: normalizedEdbSplit,
    outputDir,
    classinReview,
    classinReviewStatus,
    classinReviewStatusLabel,
    classinReviewPassed: (raw.classinReviewPassed ?? raw.classin_review_passed) === undefined
      ? classinReviewStatus === 'passed'
      : (raw.classinReviewPassed ?? raw.classin_review_passed) !== false,
    classinHandoffUri,
    classinHandoffMarkdownUri,
    classinHandoffStatus,
    classinHandoffStatusLabel,
    readyForClassIn,
    classinPreflight,
    classinPreflightStatus,
    classinPreflightStatusLabel,
    classinPreflightPassed,
    classinPreflightIssueCount: Number.isFinite(classinPreflightIssueCount) ? Math.max(0, classinPreflightIssueCount) : 0,
    classinPreflightIssueLabels: classinPreflightIssueLabelList,
    classinPreflightIssueSummaryLabel,
    passageGroups,
    passageGroupCount: normalizedPassageGroupCount,
    passageProblemCount: normalizedPassageProblemCount,
    crossPagePassageGroupCount: normalizedCrossPagePassageGroupCount,
    passageGroupLabel,
    passageReviewItems,
    passageReviewItemCount: normalizedPassageReviewItemCount,
    crossPagePassageReviewItemCount: normalizedCrossPagePassageReviewItemCount,
    passageReviewLabel,
    passageReviewReasonLabel,
    sourceProblemOverlapGroups,
    sourceProblemOverlapGroupCount: normalizedSourceProblemOverlapGroupCount,
    sourceProblemOverlapLabel,
    passageGroupSourceReuseGroups,
    passageGroupSourceReuseGroupCount: normalizedPassageGroupSourceReuseGroupCount,
    passageGroupSourceReuseLabel,
    layoutDiagnostics,
    layoutDiagnosticsLabel,
    edbFileExists: edbFileExists === undefined ? true : edbFileExists !== false,
    outputDirExists: outputDirExists === undefined ? Boolean(outputDir) : outputDirExists !== false,
    recordCount: fallbackRecordCount,
    recordCountActual: Number.isFinite(recordCountActual) ? Math.max(0, recordCountActual) : 0,
    coreProblemCount: normalizedCore,
    supplementalItemCount: normalizedSupplemental,
    recordCountLabel: explicitRecordCountLabel || (normalizedSupplemental > 0 ? `${normalizedCore}문항 + 자료 ${normalizedSupplemental}` : `${fallbackRecordCount}개 자료`),
    pageCountHint: Number.isFinite(pageCountHint) ? Math.max(0, pageCountHint) : 0,
    outerSize: Number.isFinite(outerSize) ? Math.max(0, outerSize) : 0,
    publishedAt: String(raw.publishedAt || raw.published_at || '').trim(),
  };
  summary.canDownload = summary.edbParts.some(part => Boolean(part.edbFileUri) && part.edbFileExists !== false)
    || (Boolean(summary.edbFileUri) && summary.edbFileExists !== false);
  summary.canOpenEdbFile = summary.edbParts.some(part => Boolean(part.edbPath) && part.edbFileExists !== false)
    || (Boolean(summary.edbPath) && summary.edbFileExists !== false);
  summary.canOpenOutputDir = Boolean(summary.outputDir) && summary.outputDirExists !== false;
  summary.canOpenClassinHandoff = Boolean(summary.classinHandoffMarkdownUri || summary.classinHandoffUri);
  summary.canMarkClassinReviewComplete = summary.canOpenEdbFile && !summary.classinReviewPassed;
  return summary;
}

function sessionPublishSummary(session){
  return normalizePublishSummary(session?.publish_summary || session?.publishSummary, session);
}

function sessionPublishHistory(session){
  const rawHistory = Array.isArray(session?.publish_history)
    ? session.publish_history
    : Array.isArray(session?.publishHistory)
      ? session.publishHistory
      : [];
  const history = rawHistory
    .map(item => normalizePublishSummary(item, session))
    .filter(Boolean);
  const latest = sessionPublishSummary(session);
  const latestKey = latest ? (latest.edbPath || latest.edbFileName) : '';
  if (latest && !history.some(item => (item.edbPath || item.edbFileName) === latestKey)) {
    history.unshift(latest);
  }
  return history.slice(0, 5);
}

function formatPublishTime(value){
  const helper = globalThis.EDB_PUBLISH_SUMMARY?.formatPublishTime;
  if (typeof helper === 'function') return helper(value);
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatPublishHistoryMeta(summary){
  const helper = globalThis.EDB_PUBLISH_SUMMARY?.formatPublishHistoryMeta;
  if (typeof helper === 'function') return helper(summary);
  const label = summary?.recordCountLabel || `${summary?.recordCountActual || summary?.recordCount || 0}개 자료`;
  const time = formatPublishTime(summary?.publishedAt);
  return [time, label].filter(Boolean).join(' · ') || label;
}

function applyProblemCounts(session, problems = null){
  const counts = sessionProblemCounts({ problems: problems || session?.problems || [] }, problems || session?.problems || []);
  session.detected_problem_count = counts.total;
  session.detectedProblemCount = counts.total;
  session.core_problem_count = counts.core;
  session.coreProblemCount = counts.core;
  session.supplemental_item_count = counts.supplemental;
  session.supplementalItemCount = counts.supplemental;
  return counts;
}

function formatProblemCount(counts){
  const core = Number(counts?.core ?? counts?.problems ?? counts?.total ?? 0);
  const supplemental = Number(counts?.supplemental ?? counts?.supplementalItems ?? 0);
  if (supplemental > 0) return `${core}문항 + 자료 ${supplemental}`;
  return `${core}문항`;
}

function hasReviewPages(session){
  return Array.isArray(session?.pages) && session.pages.length > 0;
}

function sessionRiskCount(session){
  const problemRiskCount = (session?.problems || []).filter(p => Array.isArray(p?.riskFlags) && p.riskFlags.length > 0).length;
  const pageRiskCount = (session?.pages || []).filter(page => {
    const flags = page?.riskFlags || page?.risk_flags || [];
    return Array.isArray(flags) && flags.length > 0;
  }).length;
  const warningCount = Array.isArray(session?.warning_messages)
    ? session.warning_messages.length
    : Array.isArray(session?.warningMessages)
      ? session.warningMessages.length
      : 0;
  return problemRiskCount + pageRiskCount + warningCount;
}

function shouldOpenReview(session){
  if (!hasReviewPages(session)) return false;
  const problemCount = Array.isArray(session?.problems) ? session.problems.length : 0;
  return problemCount > 1 || sessionRiskCount(session) > 0;
}

function summarizeRecognitionSession(session, pageIds){
  const pages = Array.isArray(session?.pages) ? session.pages : [];
  const targetIds = Array.isArray(pageIds) && pageIds.length ? new Set(pageIds) : null;
  const visiblePages = targetIds
    ? pages.filter(page => targetIds.has(page.id))
    : pages;
  const visiblePageIds = new Set(visiblePages.map(page => page.id));
  const problems = (session?.problems || []).filter(problem => {
    if (!targetIds) return true;
    return visiblePageIds.has(problem?.sourcePageId);
  });
  const riskCount = problems.filter(problem => deriveProblemStatus(problem) !== 'normal').length;
  const counts = countSessionProblems(problems);
  return {
    pages: visiblePages.length,
    problems: counts.total,
    coreProblems: counts.core,
    supplementalItems: counts.supplemental,
    problemLabel: formatProblemCount(counts),
    riskCount,
  };
}

function aiModelFallbackToast(session){
  const summary = session?.ai_summary || session?.aiSummary || {};
  const fallbacks = Array.isArray(summary.model_fallbacks || summary.modelFallbacks)
    ? (summary.model_fallbacks || summary.modelFallbacks)
    : [];
  const first = fallbacks.find(item => item && item.from && item.to);
  if (!first) return '';
  const from = AI_MODEL_LABELS[first.from] || first.from;
  const to = AI_MODEL_LABELS[first.to] || first.to;
  return `${from} 호출 오류 · ${to}로 재시도했어요`;
}

function mergeRetryCandidateIntoCurrent(currentSession, candidateSession, pageIds, options = {}){
  const base = cloneSession(currentSession);
  const candidate = cloneSession(candidateSession);
  if (!base || !candidate || !Array.isArray(candidate.problems)) return candidate;

  const targetProblemIds = new Set(
    (Array.isArray(options.problemIds) ? options.problemIds : [])
      .map(id => String(id || '').trim())
      .filter(Boolean)
  );
  if (options.partial && targetProblemIds.size) {
    const replacementsByProblemId = new Map();
    candidate.problems.forEach(problem => {
      const sourceId = replacementSourceIdFor(problem);
      if (!sourceId || !targetProblemIds.has(sourceId)) return;
      const replacements = replacementsByProblemId.get(sourceId) || [];
      replacements.push(problem);
      replacementsByProblemId.set(sourceId, replacements);
    });
    if (replacementsByProblemId.size) {
      const insertedReplacementIds = new Set();
      const nextProblems = [];
      (base.problems || []).forEach(problem => {
        const problemId = String(problem?.id || '');
        if (!targetProblemIds.has(problemId)) {
          nextProblems.push(problem);
          return;
        }
        const replacements = replacementsByProblemId.get(problemId) || [];
        if (!replacements.length) {
          nextProblems.push(problem);
          return;
        }
        replacements.forEach(replacement => {
          if (replacement?.id) insertedReplacementIds.add(String(replacement.id));
          nextProblems.push(replacement);
        });
      });
      replacementsByProblemId.forEach(replacements => {
        replacements.forEach(replacement => {
          if (replacement?.id && !insertedReplacementIds.has(String(replacement.id))) {
            insertedReplacementIds.add(String(replacement.id));
            nextProblems.push(replacement);
          }
        });
      });

      base.problems = nextProblems;
      base.pages = (base.pages || []).map(page => {
        const ids = page.problemIds || page.problem_ids || [];
        const nextIds = [];
        ids.forEach(rawId => {
          const id = String(rawId || '');
          const replacements = replacementsByProblemId.get(id);
          if (replacements?.length) {
            nextIds.push(...replacements.map(problem => problem.id).filter(Boolean));
          } else {
            nextIds.push(rawId);
          }
        });
        return {
          ...page,
          problemIds: nextIds,
        };
      });
      applyProblemCounts(base, nextProblems);
      base.ai_retry_summary = candidate.ai_retry_summary || candidate.aiRetrySummary || [];
      base.edb_path = null;
      base.edb_file_uri = null;
      base.edbPath = null;
      base.edbFileUri = null;
      return base;
    }
  }

  const targetPageIds = new Set(
    (Array.isArray(pageIds) && pageIds.length
      ? pageIds
      : (candidate.pages || []).map(page => page.id)
    ).filter(Boolean)
  );
  if (!targetPageIds.size) return candidate;

  const candidatePagesById = new Map();
  (candidate.pages || []).forEach(page => {
    if (page?.id && targetPageIds.has(page.id)) candidatePagesById.set(page.id, page);
  });

  const candidateProblemsById = new Map();
  candidate.problems.forEach(problem => {
    if (problem?.id) candidateProblemsById.set(problem.id, problem);
  });

  const replacementsByPageId = new Map();
  candidatePagesById.forEach((page, pageId) => {
    const ids = page.problemIds || page.problem_ids || [];
    const replacements = ids
      .map(id => candidateProblemsById.get(id))
      .filter(Boolean);
    replacementsByPageId.set(pageId, replacements);
  });

  const insertedPages = new Set();
  const nextProblems = [];
  (base.problems || []).forEach(problem => {
    const pageId = problem?.sourcePageId;
    if (targetPageIds.has(pageId)) {
      if (!insertedPages.has(pageId)) {
        nextProblems.push(...(replacementsByPageId.get(pageId) || []));
        insertedPages.add(pageId);
      }
      return;
    }
    nextProblems.push(problem);
  });
  targetPageIds.forEach(pageId => {
    if (!insertedPages.has(pageId)) {
      nextProblems.push(...(replacementsByPageId.get(pageId) || []));
    }
  });

  base.problems = nextProblems;
  base.pages = (base.pages || []).map(page => {
    const candidatePage = candidatePagesById.get(page.id);
    if (!candidatePage) return page;
    return {
      ...page,
      ...candidatePage,
      id: page.id,
      sourceImagePath: page.sourceImagePath || candidatePage.sourceImagePath,
      sourceImageUri: page.sourceImageUri || candidatePage.sourceImageUri,
      problemIds: (candidatePage.problemIds || candidatePage.problem_ids || []).filter(Boolean),
    };
  });
  applyProblemCounts(base, nextProblems);
  base.ai_retry_summary = candidate.ai_retry_summary || candidate.aiRetrySummary || [];
  base.edb_path = null;
  base.edb_file_uri = null;
  base.edbPath = null;
  base.edbFileUri = null;
  return base;
}

function requestedInitialView(){
  try {
    return new URLSearchParams(window.location.search).get('view') === 'review' ? 'review' : 'board';
  } catch (_err) {
    return 'board';
  }
}

async function readJsonResponse(resp, fallbackMessage){
  const text = await resp.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (err) {
    const excerpt = text.replace(/\s+/g, ' ').trim().slice(0, 180);
    throw new Error(`${fallbackMessage || '응답 파싱 실패'} (${resp.status}) · JSON 응답 아님${excerpt ? `: ${excerpt}` : ''}`);
  }
}

function assertOkJson(resp, json, fallbackMessage){
  if (!resp.ok || !json.ok) {
    throw new Error(json?.error || `${fallbackMessage || '요청 실패'} (${resp.status})`);
  }
  return json;
}

function edbFileNameFromSessionName(value, fallback = 'classin'){
  const raw = String(value || fallback || 'classin').trim();
  const baseName = raw.split(/[\\/]/).pop() || fallback || 'classin';
  const withoutSuffix = baseName.toLowerCase().endsWith('.edb')
    ? baseName.slice(0, -4)
    : baseName;
  const safeStem = withoutSuffix
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/^[ ._]+|[ ._]+$/g, '')
    .slice(0, 150) || 'classin';
  return `${safeStem}.edb`;
}

async function expectOkJson(resp, fallbackMessage){
  const json = await readJsonResponse(resp, fallbackMessage);
  return assertOkJson(resp, json, fallbackMessage);
}

async function postExport(files, aiFallback, inputIntent = DEFAULT_INPUT_INTENT, options = {}){
  const resolvedInputIntent = normalizeInputIntent(inputIntent);
  const inputIntentConfig = INPUT_INTENT_BY_VALUE[resolvedInputIntent] || INPUT_INTENT_BY_VALUE[DEFAULT_INPUT_INTENT];
  const filesPayload = await Promise.all(files.map(async (f) => ({
    fileName: f.name,
    fileDataBase64: await fileToBase64(f),
  })));
  const edbName = edbFileNameFromSessionName(options.edbName, files[0]?.name || 'classin');
  const resp = await fetch('/api/export', {
    method: 'POST',
    signal: options.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      files: filesPayload,
      preview: !!options.preview,
      exportMode: inputIntentConfig.exportMode,
      inputIntent: resolvedInputIntent,
      input_intent: resolvedInputIntent,
      sourceMode: 'auto',
      source_mode: 'auto',
      subject: 'unknown',
      ocr: 'auto',
      edbName,
      exportEdb: Object.prototype.hasOwnProperty.call(options, 'exportEdb') ? !!options.exportEdb : !options.preview,
      detectPerspective: files.some(f => !isDocumentLikeFile(f)),
      maxDimension: 2400,
      aiFallback: aiFallback || AI_FALLBACK_OFF,
    }),
  });
  const json = await readJsonResponse(resp, '파싱 실행 실패');
  if (!resp.ok || !json.ok) throw new Error(formatApiError(json, `파싱 실행 실패 (${resp.status})`));
  return json.session;
}

function formatApiError(payload, fallbackMessage){
  const baseMessage = String(payload?.error || fallbackMessage || '요청에 실패했습니다').trim();
  const steps = Array.isArray(payload?.recoverySteps)
    ? payload.recoverySteps.map(step => String(step || '').trim()).filter(Boolean)
    : [];
  if (!steps.length) return baseMessage;
  return `${baseMessage}\n\n다음 조치:\n${steps.map((step, index) => `${index + 1}. ${step}`).join('\n')}`;
}

function simpleToastErrorMessage(error, fallbackMessage = '처리 실패'){
  const raw = String(error?.message || error || '').trim();
  if (/failed to fetch|networkerror|load failed|network/i.test(raw)) {
    return `${fallbackMessage} · 로컬 앱 연결 확인`;
  }
  if (/413|too large|payload|용량|크기/i.test(raw)) {
    return `${fallbackMessage} · 파일이 너무 큽니다`;
  }
  if (/api key|apikey|gemini|openai|unauthorized|인증|키/i.test(raw)) {
    return `${fallbackMessage} · API 키 확인 필요`;
  }
  if (/hwp|hwpx|한글|libreoffice|rhwp|pdf 변환|converter/i.test(raw)) {
    return `${fallbackMessage} · HWP 변환 환경 확인`;
  }
  if (/pdf/i.test(raw)) {
    return `${fallbackMessage} · PDF 상태 확인`;
  }
  return `${fallbackMessage} · 다시 시도해 주세요`;
}

async function fetchUserSettings(){
  const resp = await fetch('/api/user-settings');
  const json = await expectOkJson(resp, '설정 로드 실패');
  return json.settings;
}

async function fetchRuntimeDiagnostics(){
  const resp = await fetch('/api/runtime-diagnostics');
  const json = await expectOkJson(resp, '진단 로드 실패');
  return json;
}

async function fetchAppUpdateStatus(){
  const resp = await fetch('/api/app/update');
  const json = await expectOkJson(resp, '업데이트 확인 실패');
  return json;
}

async function clearSession(){
  const resp = await fetch('/api/session/latest', { method: 'DELETE' });
  if (resp.status === 404) return { history: [] }; // already cleared
  const json = await expectOkJson(resp, '세션 초기화 실패');
  return json;
}

async function postShutdown(){
  const resp = await fetch('/api/system/shutdown', { method: 'POST' });
  const json = await expectOkJson(resp, '앱 종료 실패');
  return json;
}

async function postOpenUrl(url){
  const resp = await fetch('/api/system/open-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  const json = await expectOkJson(resp, '브라우저 열기 실패');
  return json;
}

async function postMutate(action, args){
  const resp = await fetch('/api/session/mutate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...args }),
  });
  const json = await expectOkJson(resp, '검수 수정 실패');
  return json.session;
}

async function postExportImages(args = {}){
  const resp = await fetch('/api/session/export-images', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args || {}),
  });
  const json = await expectOkJson(resp, 'PNG 묶음 생성 실패');
  return json;
}

function filenameFromContentDisposition(header, fallbackName = 'problem.png'){
  const text = String(header || '');
  const encoded = text.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1]).trim() || fallbackName;
    } catch (_err) {
      return fallbackName;
    }
  }
  const quoted = text.match(/filename="([^"]+)"/i);
  return quoted?.[1]?.trim() || fallbackName;
}

async function fetchProblemImageDownload(problemId, args = {}){
  const id = String(problemId || '').trim();
  if (!id) throw new Error('자료 ID가 없습니다');
  const payloadSession = args?.session;
  const resp = payloadSession
    ? await fetch('/api/session/problem-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          problemId: id,
          variant: args?.variant || 'board',
          session: payloadSession,
        }),
      })
    : await fetch(`/api/session/problem-image?problemId=${encodeURIComponent(id)}&variant=${encodeURIComponent(args?.variant || 'board')}`);
  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(body.trim() || `HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  return {
    blob,
    fileName: filenameFromContentDisposition(resp.headers.get('Content-Disposition'), `${id}.png`),
  };
}

function triggerBlobDownload(blob, fileName){
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName || 'problem.png';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function postRetryAi(args, options = {}){
  const resp = await fetch('/api/session/retry-ai', {
    method: 'POST',
    signal: options.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...(args || {}), preview: !!options.preview }),
  });
  const json = await expectOkJson(resp, 'AI 재인식 실패');
  return json;
}

async function postEnhanceImage(args, options = {}){
  const resp = await fetch('/api/session/enhance-image', {
    method: 'POST',
    signal: options.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args || {}),
  });
  const json = await expectOkJson(resp, 'AI 업스케일 실패');
  return json;
}

async function postRestore(snapshot){
  const resp = await fetch('/api/session/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: snapshot }),
  });
  const json = await expectOkJson(resp, '이전 상태 복원 실패');
  return json.session;
}

async function postClassinReviewResult(payload){
  const resp = await fetch('/api/session/classin-review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  const json = await expectOkJson(resp, 'ClassIn 검수 저장 실패');
  return json.session;
}

async function openOutputFolder(path){
  if (!path) return;
  try {
    const resp = await fetch('/api/system/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const json = await readJsonResponse(resp, '폴더 열기 실패').catch(() => ({}));
    if (!resp.ok || !json.ok) {
      console.warn('[board] open-folder failed:', json.error || resp.status);
    }
  } catch (e) {
    console.warn('[board] open-folder error:', e.message);
  }
}

async function saveUserSettings(settings){
  const payload = typeof settings === 'string'
    ? { geminiApiKey: settings }
    : { ...(settings || {}) };
  const resp = await fetch('/api/user-settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const json = await expectOkJson(resp, '설정 저장 실패');
  return json.settings;
}

// ─── APP ──────────────────────────────────────────────────────────────────
function App(){
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  useEffect(() => {
    document.body.classList.toggle('dark', !!t.dark);
    document.documentElement.style.setProperty('--accent', t.accent);
    document.documentElement.style.setProperty('--board', t.boardColor);
  }, [t.dark, t.accent, t.boardColor]);

  const [items, setItems] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [fileName, setFileName] = useState('새 세션');
  const [bulk, setBulk] = useState(false);
  const [toast, setToast] = useState(null);
  const [published, setPublished] = useState(false);
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(null); // {label, hint, startedAt} when busy
  const [backgroundJobs, setBackgroundJobs] = useState([]);
  const [recognitionReview, setRecognitionReview] = useState(null);
  const [confirmingRecognition, setConfirmingRecognition] = useState(false);
  const [usingMock, setUsingMock] = useState(false);
  const [userSettings, setUserSettings] = useState(null);
  const [runtimeDiagnostics, setRuntimeDiagnostics] = useState(null);
  const [updateInfo, setUpdateInfo] = useState(null);
  const [updateBusy, setUpdateBusy] = useState(false);
  const [exportingImages, setExportingImages] = useState(false);
  const [downloadingItemId, setDownloadingItemId] = useState(null);
  const [aiEnabled, setAiEnabled] = useState(true);
  const [inputIntent, setInputIntent] = useState(DEFAULT_INPUT_INTENT);
  const [refreshing, setRefreshing] = useState(false);
  const [recentSessions, setRecentSessions] = useState([]);
  const [restoringSessionId, setRestoringSessionId] = useState(null);
  const [pendingFiles, setPendingFiles] = useState([]);
  const [selectedPendingFileKey, setSelectedPendingFileKey] = useState(null);
  const initialViewRef = useRef(requestedInitialView());
  const initialViewConsumedRef = useRef(false);
  const [view, setView] = useState(initialViewRef.current);
  const [reviewFocus, setReviewFocus] = useState(null);
  const [mutating, setMutating] = useState(false);
  const mutatingRef = useRef(false);
  // Undo history: each entry is a prior session snapshot. Pushed before
  // any successful mutation; popped by Ctrl/Cmd+Z (wired in Step 7).
  const [historyStack, setHistoryStack] = useState([]);
  const boardColumns = normalizeBoardColumns(t.boardColumns);
  const fileInputRef = useRef(null);
  const toastTimerRef = useRef(null);
  const jobControllersRef = useRef(new Map());
  const sessionHistoryRequestRef = useRef(0);
  const pendingFileKeysRef = useRef(new Set());
  const queueGenerationRef = useRef(0);

  const reviewAvailable = Array.isArray(session?.pages) && session.pages.length > 0;
  // auto-revert to board view if the session is cleared or never had pages
  useEffect(() => {
    if (view === 'review' && !reviewAvailable) setView('board');
  }, [view, reviewAvailable]);
  const canUndo = historyStack.length > 0 && !mutating;

  const activeIndex = items.findIndex(i => i.id === activeId);
  const active = activeIndex >= 0 ? items[activeIndex] : null;
  const selectedPendingFile = useMemo(
    () => pendingFiles.find(file => fileQueueKey(file) === selectedPendingFileKey) || null,
    [pendingFiles, selectedPendingFileKey]
  );
  const processed = items.filter(i => i.step !== 'raw').length;
  const progress = items.length ? processed / items.length : 0;
  const hasRunningQueueRecognition = backgroundJobs.some(job => job.status === 'running' && job.scope === 'queue-recognition');
  const hasRunningSessionRecognition = backgroundJobs.some(job => job.status === 'running' && job.scope === 'session-recognition');
  const hasRunningImageEnhance = backgroundJobs.some(job => job.status === 'running' && job.scope === 'image-enhance');
  const runningRecognitionJob = backgroundJobs.find(job => (
    job.status === 'running' && String(job.scope || '').includes('recognition')
  )) || null;

  const showToast = useCallback((msg) => {
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    setToast(msg);
    toastTimerRef.current = window.setTimeout(() => setToast(null), 2200);
  }, []);

  const showSimpleErrorToast = useCallback((error, fallbackMessage) => {
    console.warn(`[board] ${fallbackMessage || '작업 실패'}:`, error);
    showToast(simpleToastErrorMessage(error, fallbackMessage));
  }, [showToast]);

  const selectBoardItem = useCallback((id) => {
    setSelectedPendingFileKey(null);
    setActiveId(id);
  }, []);

  const selectPendingFile = useCallback((key) => {
    if (!key) return;
    setActiveId(null);
    setSelectedPendingFileKey(key);
  }, []);

  const showPendingPreviewError = useCallback((error) => {
    showSimpleErrorToast(error, '미리보기 실패');
  }, [showSimpleErrorToast]);

  const setPendingFilesTracked = useCallback((updater) => {
    setPendingFiles(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      pendingFileKeysRef.current = new Set((next || []).map(fileQueueKey));
      queueGenerationRef.current += 1;
      return next || [];
    });
  }, []);

  const queueRequestIsCurrent = useCallback((generation, fileKeys) => (
    generation === queueGenerationRef.current
    && (fileKeys || []).every(key => pendingFileKeysRef.current.has(key))
  ), []);

  useEffect(() => {
    if (!pendingFiles.length) {
      if (selectedPendingFileKey) setSelectedPendingFileKey(null);
      return;
    }
    if (!selectedPendingFileKey || !pendingFiles.some(file => fileQueueKey(file) === selectedPendingFileKey)) {
      setSelectedPendingFileKey(fileQueueKey(pendingFiles[0]));
    }
  }, [pendingFiles, selectedPendingFileKey]);

  const setRecentSessionsAuthoritative = useCallback((history) => {
    sessionHistoryRequestRef.current += 1;
    setRecentSessions(Array.isArray(history) ? history : []);
  }, []);

  const updateStatusToast = (info) => {
    if (!info) return '업데이트 정보를 확인하지 못했습니다';
    if (info.updateAvailable) {
      const latestVersion = info.latest?.version || '새 버전';
      return `업데이트 가능 · ${latestVersion}`;
    }
    if (info.channelStatus === 'up_to_date') return '현재 최신 버전입니다';
    if (info.channelStatus === 'manual_download') return '다운로드 페이지를 열 수 있습니다';
    if (info.channelStatus === 'not_configured') return '업데이트 채널이 아직 설정되지 않았습니다';
    if (info.channelStatus === 'unsupported_platform') return '이 OS용 업데이트가 아직 없습니다';
    if (info.channelStatus === 'invalid_feed') return `업데이트 피드 오류: ${info.error || '채널 정보가 올바르지 않습니다'}`;
    if (info.channelStatus === 'error') return `업데이트 확인 오류: ${info.error || '채널 확인 실패'}`;
    return '업데이트 정보를 확인했습니다';
  };

  const checkForUpdates = useCallback(async (options = {}) => {
    setUpdateBusy(true);
    try {
      const info = await fetchAppUpdateStatus();
      setUpdateInfo(info);
      if (!options.silent) showToast(updateStatusToast(info));
      return info;
    } catch (e) {
      if (!options.silent) showSimpleErrorToast(e, '업데이트 확인 실패');
      console.warn('[board] update check skipped:', e.message);
      return null;
    } finally {
      setUpdateBusy(false);
    }
  }, [showSimpleErrorToast, showToast]);

  const openUpdatePage = useCallback(async () => {
    if (updateBusy) {
      showToast('업데이트 확인이 끝난 뒤 다시 눌러 주세요');
      return;
    }
    const info = updateInfo || await checkForUpdates({ silent: true });
    const url = info?.downloadUrl || info?.latest?.downloadUrl || '';
    if (!url) {
      showToast('업데이트 다운로드 URL이 없습니다');
      return;
    }
    try {
      await postOpenUrl(url);
      showToast('다운로드 페이지를 열었어요');
    } catch (e) {
      showSimpleErrorToast(e, '다운로드 페이지 열기 실패');
    }
  }, [updateInfo, updateBusy, checkForUpdates, showSimpleErrorToast, showToast]);

  const refreshSessionHistory = useCallback(async () => {
    const requestId = sessionHistoryRequestRef.current + 1;
    sessionHistoryRequestRef.current = requestId;
    try {
      const history = await fetchSessionHistory();
      if (requestId === sessionHistoryRequestRef.current) {
        setRecentSessions(history);
      }
      return history;
    } catch (e) {
      console.warn('[board] session history skipped:', e.message);
      return [];
    }
  }, []);

  const dismissBackgroundJob = useCallback((id) => {
    setBackgroundJobs(prev => prev.filter(job => job.id !== id));
    jobControllersRef.current.delete(id);
  }, []);

  const settleBackgroundJob = useCallback((id, patch, autoDismissMs = 1800) => {
    jobControllersRef.current.delete(id);
    setBackgroundJobs(prev => prev.map(job => job.id === id ? { ...job, ...patch } : job));
    if (autoDismissMs) {
      window.setTimeout(() => {
        setBackgroundJobs(prev => prev.filter(job => job.id !== id));
      }, autoDismissMs);
    }
  }, []);

  const startBackgroundJob = useCallback((job) => {
    const id = `job-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const controller = new AbortController();
    jobControllersRef.current.set(id, controller);
    setBackgroundJobs(prev => [
      {
        id,
        status: 'running',
        startedAt: Date.now(),
        label: job.label || '백그라운드 작업',
        hint: job.hint || '',
        scope: job.scope || 'general',
      },
      ...prev.filter(item => item.status === 'running').slice(0, 2),
      ...prev.filter(item => item.status !== 'running').slice(0, 1),
    ]);
    return { id, controller };
  }, []);

  const cancelBackgroundJob = useCallback((id) => {
    const job = backgroundJobs.find(item => item.id === id);
    const isRecognition = String(job?.scope || '').includes('recognition');
    const controller = jobControllersRef.current.get(id);
    if (controller) controller.abort();
    settleBackgroundJob(id, {
      status: 'canceled',
      label: isRecognition ? 'AI 인식 취소됨' : '작업 취소됨',
      hint: '결과를 적용하지 않았습니다.',
    }, 2200);
    showToast(isRecognition ? 'AI 인식을 취소했어요' : '작업을 취소했어요');
  }, [backgroundJobs, settleBackgroundJob, showToast]);

  useEffect(() => {
    return () => {
      jobControllersRef.current.forEach(controller => controller.abort());
      jobControllersRef.current.clear();
      if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    };
  }, []);

  const showMockItems = (message = '더미 자료를 표시했어요') => {
    const mockItems = freshInitialItems();
    setRecognitionReview(null);
    setSession(null);
    setItems(mockItems);
    setActiveId(mockItems[0]?.id || null);
    setUsingMock(true);
    setPublished(false);
    setFileName('더미 세션');
    setHistoryStack([]);
    setView('board');
    showToast(message);
  };

  const hideMockItems = (message = '빈 세션으로 전환했어요') => {
    setRecognitionReview(null);
    setSession(null);
    setItems([]);
    setActiveId(null);
    setUsingMock(false);
    setPublished(false);
    setFileName('새 세션');
    setHistoryStack([]);
    setView('board');
    showToast(message);
  };

  const applySession = useCallback((rawSession) => {
    if (!rawSession || !Array.isArray(rawSession.problems) || rawSession.problems.length === 0) {
      return false;
    }
    const mapped = reflowItemsForBoardOrder(
      rawSession.problems.map((p, idx) => mapProblemToItem(p, idx)),
      DEFAULT_SLOT_HEIGHT_PAGES,
      boardColumns
    );
    setItems(mapped);
    setActiveId(mapped[0].id);
    setSession(rawSession);
    setUsingMock(false);
    setPublished(!!sessionPublishSummary(rawSession));
    if (rawSession.session_name) setFileName(rawSession.session_name);
    const wantsReview = (!initialViewConsumedRef.current && initialViewRef.current === 'review') || shouldOpenReview(rawSession);
    if (hasReviewPages(rawSession) && wantsReview) {
      setView('review');
      initialViewConsumedRef.current = true;
    }
    return true;
  }, [boardColumns]);

  // Replace state from a mutation response: similar to applySession but
  // tries to preserve the user's current item ordering when the mutation
  // only affects a subset of problem ids (e.g. exclude leaves order intact).
  const adoptMutatedSession = useCallback((nextSession, prevSession) => {
    if (!nextSession || !Array.isArray(nextSession.problems)) return;
    const nextProblemsById = new Map();
    nextSession.problems.forEach(p => { if (p && p.id) nextProblemsById.set(p.id, p); });
    const replacementsBySourceId = new Map();
    nextSession.problems.forEach(problem => {
      const sourceId = replacementSourceIdFor(problem);
      if (!sourceId || !problem?.id) return;
      const replacements = replacementsBySourceId.get(sourceId) || [];
      replacements.push(problem.id);
      replacementsBySourceId.set(sourceId, replacements);
    });
    // Start from the previous items order, drop missing ids, insert explicit
    // replacements at their old position, then append any remaining server
    // ids. This keeps partial AI retry results from jumping to the top.
    const prevItemIds = items.map(it => it.id);
    const seen = new Set();
    const orderedIds = [];
    for (const id of prevItemIds) {
      if (nextProblemsById.has(id) && !seen.has(id)) {
        orderedIds.push(id);
        seen.add(id);
        continue;
      }
      const replacements = replacementsBySourceId.get(id) || [];
      replacements.forEach(replacementId => {
        if (nextProblemsById.has(replacementId) && !seen.has(replacementId)) {
          orderedIds.push(replacementId);
          seen.add(replacementId);
        }
      });
      if (replacements.length) {
        continue;
      }
    }
    for (const prob of nextSession.problems) {
      if (prob && prob.id && !seen.has(prob.id)) {
        orderedIds.push(prob.id);
        seen.add(prob.id);
      }
    }
    const orderedProblems = orderedIds.map(id => nextProblemsById.get(id)).filter(Boolean);
    const mapped = reflowItemsForBoardOrder(
      orderedProblems.map((p, idx) => mapProblemToItem(p, idx)),
      DEFAULT_SLOT_HEIGHT_PAGES,
      boardColumns
    );
    setItems(mapped);
    setSession(nextSession);
    setPublished(false);
    if (mapped.length === 0) {
      setActiveId(null);
      return;
    }
    if (!nextProblemsById.has(activeId)) {
      const replacementActiveId = (replacementsBySourceId.get(activeId) || [])
        .find(id => nextProblemsById.has(id));
      setActiveId(replacementActiveId || mapped[0]?.id || null);
    }
  }, [items, activeId, boardColumns]);

  // Run a server-side mutation (split / merge / crop / exclude). Captures the
  // current session into the undo history *before* the request goes out
  // so that a failed mutation does not clutter the stack.
  const mutateSession = useCallback(async (action, args) => {
    if (!session) {
      showToast('변경할 세션이 없습니다');
      return;
    }
    if (mutatingRef.current) {
      showToast('이전 변경을 적용하는 중입니다');
      return;
    }
    mutatingRef.current = true;
    setMutating(true);
    setLoading({
      label: action === 'split' ? '문제를 가르는 중…'
        : action === 'merge' ? '문제를 합치는 중…'
        : action === 'crop' ? '이미지를 자르는 중…'
        : action === 'bulk-crop' ? '수동 분할을 적용하는 중…'
        : action === 'exclude' ? '문제를 제외하는 중…'
        : '변경 중…',
      startedAt: Date.now(),
    });
    const snapshotBefore = materializeSessionForItems(session, items, fileName, boardColumns) || session;
    try {
      await postRestore(snapshotBefore);
      const next = await postMutate(action, args);
      setHistoryStack(prev => [...prev, snapshotBefore]);
      adoptMutatedSession(next, snapshotBefore);
      refreshSessionHistory();
      showToast(
        action === 'split' ? '문제를 두 개로 갈랐어요'
        : action === 'merge' ? '문제를 합쳤어요'
        : action === 'crop' ? '자르기를 적용했어요'
        : action === 'bulk-crop' ? '수동 분할을 적용했어요'
        : '문제를 제외했어요'
      );
      return next;
    } catch (e) {
      showSimpleErrorToast(e, '수정 실패');
      return null;
    } finally {
      mutatingRef.current = false;
      setMutating(false);
      setLoading(null);
    }
  }, [session, items, fileName, boardColumns, adoptMutatedSession, refreshSessionHistory, showSimpleErrorToast, showToast]);

  const retryAiSession = useCallback(async (args) => {
    if (!session) {
      showToast('변경할 세션이 없습니다');
      return;
    }
    if (!userSettings?.hasGeminiApiKey) {
      showToast('Gemini API 키를 먼저 저장해 주세요');
      return;
    }
    const pageIds = listUnique((args?.pageIds || args?.page_ids || []).filter(Boolean));
    const problemIds = listUnique((args?.problemIds || args?.problem_ids || []).filter(Boolean));
    const isPartialRetry = Boolean(args?.partial || args?.partialRetry || args?.partial_retry) && problemIds.length > 0;
    const targetPageIds = isPartialRetry
      ? listUnique(problemIds
        .map(id => (session.problems || []).find(problem => problem?.id === id)?.sourcePageId)
        .filter(Boolean))
      : pageIds;
    const snapshotBefore = materializeSessionForItems(session, items, fileName, boardColumns) || cloneSession(session);
    const job = startBackgroundJob({
      scope: 'session-recognition',
      label: isPartialRetry
        ? (problemIds.length === 1 ? '주변 영역 AI 재인식 중' : `${problemIds.length}개 주변 영역 AI 재인식 중`)
        : (pageIds.length === 1 ? 'AI 문제 인식 중' : `${pageIds.length || '전체'}개 페이지 AI 인식 중`),
      hint: isPartialRetry
        ? '선택한 박스 주변 여백까지 다시 자릅니다. 완료되면 확인 팝업이 열립니다.'
        : '보드 작업은 계속할 수 있습니다. 완료되면 확인 팝업이 열립니다.',
    });
    try {
      await postRestore(snapshotBefore);
      const result = await postRetryAi(args, { signal: job.controller.signal, preview: true });
      if (job.controller.signal.aborted) return;
      const next = result.session;
      const fallbackMessage = aiModelFallbackToast(next);
      if (fallbackMessage) showToast(fallbackMessage);
      const applied = (result.retry || []).filter(row => row.status === 'applied').length;
      settleBackgroundJob(job.id, {
        status: 'done',
        label: 'AI 인식 완료',
        hint: '결과 확인 팝업에서 문제 경계를 확인하세요.',
      });
      setRecognitionReview({
        id: `review-${job.id}`,
        kind: 'retry-ai',
        partial: isPartialRetry,
        title: isPartialRetry
          ? (applied ? `${applied}개 주변 영역을 다시 인식했어요` : '부분 AI 인식 결과를 확인해 주세요')
          : (applied ? `${applied}개 페이지를 다시 인식했어요` : 'AI 인식 결과를 확인해 주세요'),
        subtitle: isPartialRetry
          ? '선택한 박스 주변에서 다시 찾은 문제 경계입니다. 맞으면 기존 자리만 바꿔 반영합니다.'
          : '문제 경계가 맞으면 바로 칠판에 분할해서 붙입니다.',
        session: next,
        pageIds: targetPageIds,
        problemIds,
        snapshotBefore,
        retrySummary: result.retry || [],
      });
    } catch (e) {
      if (e?.name === 'AbortError') {
        settleBackgroundJob(job.id, {
          status: 'canceled',
          label: 'AI 인식 취소됨',
          hint: '결과를 적용하지 않았습니다.',
        });
        return;
      }
      settleBackgroundJob(job.id, {
        status: 'failed',
        label: 'AI 인식 실패',
        hint: e.message,
      }, 5000);
      showSimpleErrorToast(e, 'AI 재인식 실패');
    }
  }, [session, userSettings, items, fileName, boardColumns, startBackgroundJob, settleBackgroundJob, showSimpleErrorToast]);

  const recognizeCurrentSession = useCallback(async () => {
    if (!session || !Array.isArray(session.pages) || session.pages.length === 0) {
      showToast('문제 인식할 원본 페이지가 없습니다');
      return;
    }
    if (!userSettings?.hasGeminiApiKey) {
      showToast('Gemini API 키를 먼저 저장해 주세요');
      return;
    }
    const pageIds = listUnique(session.pages.map(page => page.id).filter(Boolean));
    await retryAiSession({ pageIds });
  }, [session, userSettings, retryAiSession]);

  const exportSessionImages = useCallback(async () => {
    if (!session) {
      showToast('다운로드할 세션이 없습니다');
      return;
    }
    const itemsForExport = reflowItemsForBoardOrder(items, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);
    const sessionForExport = materializeSessionForItems(session, itemsForExport, fileName, boardColumns) || session;
    const problemIds = listUnique(itemsForExport
      .filter(item => item?.id && !item.excluded)
      .map(item => item.id));
    if (!problemIds.length) {
      showToast('다운로드할 이미지가 없습니다');
      return;
    }
    setExportingImages(true);
    try {
      const result = await postExportImages({ mode: 'both', problemIds, session: sessionForExport });
      if (!result?.downloadUrl) {
        throw new Error('다운로드 URL이 없습니다');
      }
      const a = document.createElement('a');
      a.href = result.downloadUrl;
      a.download = result.fileName || `${(session.session_name || fileName || 'classin').trim() || 'classin'}_images.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      const missingCount = Array.isArray(result.missing) ? result.missing.length : 0;
      showToast(missingCount
        ? `PNG 묶음을 준비했어요 · 누락 ${missingCount}개는 manifest에서 확인`
        : `PNG 묶음 다운로드 시작 · ${result.count || problemIds.length}개`);
    } catch (e) {
      showSimpleErrorToast(e, 'PNG 묶음 실패');
    } finally {
      setExportingImages(false);
    }
  }, [session, items, fileName, boardColumns, showSimpleErrorToast]);

  const downloadItemImage = useCallback(async (item) => {
    if (!session) {
      showToast('다운로드할 세션이 없습니다');
      return;
    }
    if (!item?.id) {
      showToast('다운로드할 자료가 없습니다');
      return;
    }
    setDownloadingItemId(item.id);
    try {
      const itemsForDownload = reflowItemsForBoardOrder(items, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);
      const sessionForDownload = materializeSessionForItems(session, itemsForDownload, fileName, boardColumns) || session;
      const result = await fetchProblemImageDownload(item.id, { session: sessionForDownload });
      triggerBlobDownload(result.blob, result.fileName);
      showToast('PNG 다운로드 시작');
    } catch (e) {
      showSimpleErrorToast(e, '다운로드 실패');
    } finally {
      setDownloadingItemId(null);
    }
  }, [session, items, fileName, boardColumns, showSimpleErrorToast]);

  const undoMutation = useCallback(async () => {
    if (historyStack.length === 0) return;
    const snapshot = historyStack[historyStack.length - 1];
    setMutating(true);
    setLoading({ label: '되돌리는 중…', startedAt: Date.now() });
    try {
      const restored = await postRestore(snapshot);
      setHistoryStack(prev => prev.slice(0, -1));
      applySession(restored);
      refreshSessionHistory();
      showToast('이전 상태로 되돌렸어요');
    } catch (e) {
      showSimpleErrorToast(e, '되돌리기 실패');
    } finally {
      setMutating(false);
      setLoading(null);
    }
  }, [historyStack, applySession, refreshSessionHistory, showSimpleErrorToast]);

  // Ctrl/Cmd+Z → undo. Skipped when focus is inside a text input so the
  // browser's native undo still works for editable fields (file-name crumb).
  useEffect(() => {
    const onKey = (evt) => {
      if (!(evt.key === 'z' || evt.key === 'Z')) return;
      if (!(evt.ctrlKey || evt.metaKey)) return;
      if (evt.shiftKey) return;  // reserve Ctrl/Cmd+Shift+Z for future redo
      const target = evt.target;
      if (isEditableKeyboardTarget(target)) return;
      if (historyStack.length === 0 || mutating) return;
      evt.preventDefault();
      undoMutation();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [historyStack.length, mutating, undoMutation]);

  // initial session fetch — empty editor on 404, no automatic dummy data.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await fetchLatestSession();
        if (cancelled) return;
        if (s) applySession(s);
      } catch (e) {
        if (!cancelled) console.warn('[board] session load skipped:', e.message);
      }
    })();
    return () => { cancelled = true; };
  }, [applySession]);

  useEffect(() => {
    refreshSessionHistory();
  }, [refreshSessionHistory]);

  // load user settings (Gemini key status) on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await fetchUserSettings();
        if (cancelled) return;
        setUserSettings(s);
        // default AI on if key is present
        setAiEnabled(!!s?.hasGeminiApiKey);
      } catch (e) {
        if (!cancelled) console.warn('[board] user-settings load skipped:', e.message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const diagnostics = await fetchRuntimeDiagnostics();
        if (!cancelled) setRuntimeDiagnostics(diagnostics);
      } catch (e) {
        if (!cancelled) console.warn('[board] runtime diagnostics skipped:', e.message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const onSaveGeminiKey = useCallback(async (key) => {
    try {
      const s = await saveUserSettings({ geminiApiKey: key || '' });
      setUserSettings(s);
      showToast(key ? 'Gemini 키 저장됨' : 'Gemini 키 삭제됨');
    } catch (e) {
      showSimpleErrorToast(e, '저장 실패');
    }
  }, [showSimpleErrorToast, showToast]);

  const onSaveOpenAiKey = useCallback(async (key) => {
    try {
      const s = await saveUserSettings({ openAiApiKey: key || '' });
      setUserSettings(s);
      showToast(key ? 'OpenAI 키 저장됨' : 'OpenAI 키 삭제됨');
    } catch (e) {
      showSimpleErrorToast(e, '저장 실패');
    }
  }, [showSimpleErrorToast, showToast]);

  const enhanceImageSession = useCallback(async (problemIds) => {
    if (!session) {
      showToast('변경할 세션이 없습니다');
      return;
    }
    if (!userSettings?.hasGeminiApiKey) {
      showToast('Gemini API 키를 먼저 저장해 주세요');
      return;
    }
    const ids = listUnique((problemIds || []).filter(Boolean));
    if (!ids.length) {
      showToast('업스케일할 문항을 선택해 주세요');
      return;
    }
    const snapshotBefore = materializeSessionForItems(session, items, fileName, boardColumns) || cloneSession(session);
    const job = startBackgroundJob({
      scope: 'image-enhance',
      label: ids.length === 1 ? 'AI 업스케일 재구성 중' : `${ids.length}개 문항 AI 업스케일 중`,
      hint: 'Nano Banana 2로 투명 배경과 문자·숫자 선명도를 개선합니다.',
    });
    try {
      const result = await postEnhanceImage({ problemIds: ids, provider: 'gemini' }, { signal: job.controller.signal });
      if (job.controller.signal.aborted) return;
      const next = result.session;
      const applied = (result.enhance || []).filter(row => row.status === 'applied').length;
      setHistoryStack(prev => [...prev, snapshotBefore]);
      adoptMutatedSession(next, snapshotBefore);
      settleBackgroundJob(job.id, {
        status: 'done',
        label: 'AI 업스케일 완료',
        hint: applied ? `${applied}개 문항을 재구성했습니다.` : '적용된 문항이 없습니다. 결과를 확인해 주세요.',
      });
      showToast(applied ? `AI 업스케일 적용 · ${applied}개 문항` : 'AI 업스케일 결과를 확인해 주세요');
    } catch (e) {
      if (e?.name === 'AbortError') {
        settleBackgroundJob(job.id, {
          status: 'canceled',
          label: 'AI 업스케일 취소됨',
          hint: '결과를 적용하지 않았습니다.',
        });
        return;
      }
      settleBackgroundJob(job.id, {
        status: 'failed',
        label: 'AI 업스케일 실패',
        hint: e.message,
      }, 5000);
      showSimpleErrorToast(e, 'AI 업스케일 실패');
    }
  }, [session, userSettings, items, fileName, boardColumns, startBackgroundJob, settleBackgroundJob, adoptMutatedSession, showSimpleErrorToast]);

  const resetSession = useCallback(async () => {
    if (loading) {
      showToast('작업 중에는 초기화할 수 없습니다');
      return;
    }
    if (!session && items.length === 0 && pendingFiles.length === 0 && recentSessions.length === 0) {
      showToast('이미 빈 세션입니다');
      return;
    }
    if (!window.confirm('세션을 초기화할까요? 업로드 대기열, 보드 자료, 최근 작업 목록이 모두 사라집니다.')) return;
    try {
      jobControllersRef.current.forEach(controller => controller.abort());
      jobControllersRef.current.clear();
      setBackgroundJobs([]);
      setRecognitionReview(null);
      setConfirmingRecognition(false);
      if (session || recentSessions.length) {
        const result = await clearSession();
        setRecentSessionsAuthoritative(result?.history);
      }
      setPendingFilesTracked([]);
      setReviewFocus(null);
      hideMockItems('초기화 완료 · 빈 세션');
    } catch (e) {
      showSimpleErrorToast(e, '초기화 실패');
    }
  }, [loading, session, items.length, pendingFiles.length, recentSessions.length, setPendingFilesTracked, setRecentSessionsAuthoritative, showSimpleErrorToast, showToast]);

  const shutdownApp = useCallback(async () => {
    if (!window.confirm('로컬 앱을 종료할까요? 브라우저 창은 직접 닫으면 됩니다.')) return;
    try {
      await postShutdown();
      showToast('앱을 종료합니다. 브라우저 창을 닫아도 됩니다.');
    } catch (e) {
      showSimpleErrorToast(e, '앱 종료 실패');
    }
  }, [showSimpleErrorToast, showToast]);

  const refreshSession = useCallback(async () => {
    setRefreshing(true);
    try {
      const s = await fetchLatestSession();
      if (s && Array.isArray(s.problems) && s.problems.length) {
        applySession(s);
        refreshSessionHistory();
        showToast(`새로고침 완료 · ${formatProblemCount(sessionProblemCounts(s))}`);
      } else {
        if (!usingMock) {
          setSession(null);
          setItems([]);
          setActiveId(null);
          setPublished(false);
          setView('board');
        }
        showToast(usingMock ? '저장된 세션 없음 · 더미 유지' : '저장된 세션 없음 · 빈 세션');
      }
    } catch (e) {
      showSimpleErrorToast(e, '새로고침 실패');
    } finally {
      setRefreshing(false);
    }
  }, [applySession, usingMock, refreshSessionHistory, showSimpleErrorToast, showToast]);

  const restoreRecentSession = useCallback(async (id) => {
    if (!id || restoringSessionId) return;
    setRestoringSessionId(id);
    setLoading({ label: '최근 작업을 여는 중…', startedAt: Date.now() });
    try {
      const result = await postRestoreSessionHistory(id);
      if (Array.isArray(result.history)) setRecentSessionsAuthoritative(result.history);
      setRecognitionReview(null);
      applySession(result.session);
      setHistoryStack([]);
      setReviewFocus(null);
      showToast('최근 작업을 열었어요');
    } catch (e) {
      showSimpleErrorToast(e, '작업 열기 실패');
    } finally {
      setRestoringSessionId(null);
      setLoading(null);
    }
  }, [applySession, restoringSessionId, setRecentSessionsAuthoritative, showSimpleErrorToast, showToast]);

  const triggerUpload = () => fileInputRef.current?.click();

  const handleFiles = (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setRecognitionReview(null);
    const existingKeys = new Set(pendingFiles.map(fileQueueKey));
    const firstAddedKey = files.map(fileQueueKey).find(key => !existingKeys.has(key)) || null;
    setPendingFilesTracked(prev => {
      const seen = new Set(prev.map(fileQueueKey));
      const next = [...prev];
      files.forEach(file => {
        const key = fileQueueKey(file);
        if (!seen.has(key)) {
          seen.add(key);
          next.push(file);
        }
      });
      return next;
    });
    if (firstAddedKey) selectPendingFile(firstAddedKey);
    showToast(`${files.length}개 파일을 대기열에 추가했어요`);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removePendingFile = useCallback((key) => {
    setPendingFilesTracked(prev => prev.filter(file => fileQueueKey(file) !== key));
  }, [setPendingFilesTracked]);

  const clearPendingFiles = useCallback(() => {
    setRecognitionReview(null);
    setSelectedPendingFileKey(null);
    setPendingFilesTracked([]);
    showToast('업로드 대기열을 비웠어요');
  }, [setPendingFilesTracked]);

  useEffect(() => {
    if (recognitionReview?.kind !== 'queue-recognition') return;
    if (!queueRequestIsCurrent(recognitionReview.queueGeneration, recognitionReview.fileKeys || [])) {
      setRecognitionReview(null);
    }
  }, [recognitionReview, pendingFiles, queueRequestIsCurrent]);

  const processQueuedFiles = useCallback(async (mode, targetKey = null) => {
    const files = targetKey
      ? pendingFiles.filter(file => fileQueueKey(file) === targetKey)
      : [...pendingFiles];
    if (!files.length) {
      showToast(targetKey ? '해당 파일이 대기열에 없습니다' : '대기열에 파일이 없습니다');
      return;
    }
    const isRecognition = mode === 'recognize';
    const isManualSplit = mode === 'manual-split';
    const resolvedInputIntent = isRecognition ? 'multi-problem' : 'page-as-is';
    const aiFallback = isRecognition && aiEnabled && userSettings?.hasGeminiApiKey
      ? AI_FALLBACK_ON
      : AI_FALLBACK_OFF;
    if (isRecognition) {
      const fileKeys = files.map(fileQueueKey);
      const queueGeneration = queueGenerationRef.current;
      const job = startBackgroundJob({
        scope: 'queue-recognition',
        label: files.length === 1 ? '1개 파일 AI 문제 인식 중' : `${files.length}개 파일 AI 문제 인식 중`,
        hint: aiFallback.enabled
          ? 'Gemini AI 보정으로 문항 경계를 확인합니다.'
          : '기본 문항 인식으로 실행 중입니다.',
      });
      try {
        const incomingSession = await postExport(files, aiFallback, resolvedInputIntent, {
          signal: job.controller.signal,
          preview: true,
          edbName: fileName,
        });
        if (job.controller.signal.aborted) return;
        if (!queueRequestIsCurrent(queueGeneration, fileKeys)) {
          settleBackgroundJob(job.id, {
            status: 'canceled',
            label: 'AI recognition skipped',
            hint: 'Upload queue changed before this result was reviewed.',
          }, 2200);
          return;
        }
        const fallbackMessage = aiModelFallbackToast(incomingSession);
        if (fallbackMessage) showToast(fallbackMessage);
        const summary = summarizeRecognitionSession(incomingSession);
        settleBackgroundJob(job.id, {
          status: 'done',
          label: '문제 인식 완료',
          hint: `${summary.problemLabel}을 찾았습니다.`,
        });
        setRecognitionReview({
          id: `review-${job.id}`,
          kind: 'queue-recognition',
          title: files.length === 1
            ? `${files[0].name || '파일'} · ${summary.problemLabel}로 인식했어요`
            : `${summary.problemLabel}로 인식했어요`,
          subtitle: '문제 경계가 맞으면 검수 화면에서 원본 위 박스를 확인합니다.',
          session: incomingSession,
          incomingSession,
          fileKeys,
          queueGeneration,
          fileCount: files.length,
          outputFolder: incomingSession?.output_dir || incomingSession?.outputDir,
        });
      } catch (e) {
        if (e?.name === 'AbortError') {
          settleBackgroundJob(job.id, {
            status: 'canceled',
            label: '문제 인식 취소됨',
            hint: '대기열은 그대로 유지했습니다.',
          });
          return;
        }
        settleBackgroundJob(job.id, {
          status: 'failed',
          label: '문제 인식 실패',
          hint: e.message,
        }, 5000);
        showSimpleErrorToast(e, '문제 인식 실패');
      } finally {
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
      return;
    }
    setLoading({
      label: isRecognition
        ? `${files.length}개 파일에서 문제 인식 중...`
        : isManualSplit
          ? (files.length === 1 ? '1개 파일을 수동 쪼개기용으로 여는 중...' : `${files.length}개 파일을 수동 쪼개기용으로 여는 중...`)
          : files.length === 1 ? '1개 파일을 페이지 PNG로 등록 중...' : `${files.length}개 파일을 페이지 PNG로 등록 중...`,
      hint: isRecognition
        ? (aiFallback.enabled
            ? 'Gemini AI 보정으로 문항 경계를 다시 확인합니다.'
            : 'Gemini 키가 없어 기본 문항 인식만 실행합니다.')
        : isManualSplit
          ? '인식 없이 원본 페이지를 열고 검수 화면에서 직접 영역을 그립니다.'
        : '문제 파싱 없이 각 이미지와 PDF/HWP 페이지를 하나의 PNG 자료로 등록합니다.',
      startedAt: Date.now(),
    });
    const previousItemIds = new Set(items.map(item => item.id).filter(Boolean));
    try {
      const s = await postExport(files, aiFallback, resolvedInputIntent, { edbName: fileName });
      let sessionToApply = s;
      let baseSnapshotForReviewScope = null;
      if (session && !usingMock) {
        const currentSnapshot = materializeSessionForItems(session, items, fileName, boardColumns);
        baseSnapshotForReviewScope = currentSnapshot;
        const merged = mergeSessions(currentSnapshot, s, fileName, boardColumns);
        sessionToApply = await postRestore(merged);
      }
      const firstInserted = (sessionToApply?.problems || []).find(problem => problem?.id && !previousItemIds.has(problem.id));
      const applied = applySession(sessionToApply);
      if (applied && firstInserted?.id) setActiveId(firstInserted.id);
      const nextReviewFocus = reviewFocusForNewSession(
        baseSnapshotForReviewScope,
        sessionToApply,
        isManualSplit ? 'queue-manual-split' : 'queue-register'
      );
      if (isManualSplit) {
        const manualSplitPageId = firstInserted?.sourcePageId
          || nextReviewFocus?.scopePageIds?.[0]
          || (sessionToApply?.pages || []).find(page => (page.problemIds || []).some(id => !previousItemIds.has(id)))?.id
          || sessionToApply?.pages?.[0]?.id
          || null;
        setReviewFocus({
          ...(nextReviewFocus || { source: 'queue-manual-split' }),
          filter: 'all',
          manualSplitPageId,
        });
        setView('review');
      } else {
        setReviewFocus(nextReviewFocus);
      }
      refreshSessionHistory();
      const appliedKeys = new Set(files.map(fileQueueKey));
      setPendingFilesTracked(prev => prev.filter(file => !appliedKeys.has(fileQueueKey(file))));
      const intentLabel = isRecognition ? '문항 AI 인식' : isManualSplit ? '수동 쪼개기 준비' : '페이지 PNG 등록';
      showToast(`${intentLabel} 완료 · ${formatProblemCount(sessionProblemCounts(sessionToApply))}`);
    } catch (e) {
      showSimpleErrorToast(e, isManualSplit ? '수동 쪼개기 실패' : '등록 실패');
    } finally {
      setLoading(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [pendingFiles, aiEnabled, userSettings, session, usingMock, items, fileName, boardColumns, applySession, startBackgroundJob, settleBackgroundJob, refreshSessionHistory, setPendingFilesTracked, queueRequestIsCurrent, showSimpleErrorToast]);

  const cancelRecognitionReview = useCallback(() => {
    if (confirmingRecognition) return;
    setRecognitionReview(null);
    showToast('인식 결과를 적용하지 않았어요');
  }, [confirmingRecognition]);

  const confirmRecognitionReview = useCallback(async () => {
    const review = recognitionReview;
    if (!review) return;
    setConfirmingRecognition(true);
    try {
      if (review.kind === 'queue-recognition') {
        if (!queueRequestIsCurrent(review.queueGeneration, review.fileKeys || [])) {
          setRecognitionReview(null);
          showToast('Upload queue changed. Please run recognition again.');
          return;
        }
        const incomingSession = review.incomingSession || review.session;
        const currentSnapshot = session && !usingMock
          ? materializeSessionForItems(session, items, fileName, boardColumns)
          : null;
        const candidate = currentSnapshot
          ? mergeSessions(currentSnapshot, incomingSession, fileName, boardColumns)
          : cloneSession(incomingSession);
        const restored = await postRestore(candidate);
        applySession(restored);
        setReviewFocus(reviewFocusForNewSession(currentSnapshot, restored, 'queue-recognition'));
        refreshSessionHistory();
        setView('review');
        const appliedKeys = new Set(review.fileKeys || []);
        setPendingFilesTracked(prev => prev.filter(file => !appliedKeys.has(fileQueueKey(file))));
        const summary = summarizeRecognitionSession(incomingSession);
        showToast(`검수로 이동 · ${summary.problemLabel}을 확인하세요`);
      } else if (review.kind === 'retry-ai') {
        const currentSnapshot = session
          ? (materializeSessionForItems(session, items, fileName, boardColumns) || cloneSession(session))
          : cloneSession(review.snapshotBefore);
        const candidate = mergeRetryCandidateIntoCurrent(currentSnapshot, review.session, review.pageIds, {
          partial: !!review.partial,
          problemIds: review.problemIds || [],
        });
        const restored = await postRestore(candidate);
        setHistoryStack(prev => [...prev, review.snapshotBefore || currentSnapshot].filter(Boolean));
        if (session) {
          adoptMutatedSession(restored, session);
        } else {
          applySession(restored);
        }
        refreshSessionHistory();
        setView('board');
        const summary = summarizeRecognitionSession(restored, review.pageIds);
        showToast(`AI 인식 적용 · ${summary.problemLabel}`);
      }
      setRecognitionReview(null);
    } catch (e) {
      showSimpleErrorToast(e, '적용 실패');
    } finally {
      setConfirmingRecognition(false);
    }
  }, [
    recognitionReview,
    confirmingRecognition,
    session,
    usingMock,
    items,
    fileName,
    boardColumns,
    applySession,
    adoptMutatedSession,
    refreshSessionHistory,
    queueRequestIsCurrent,
    setPendingFilesTracked,
    showSimpleErrorToast,
  ]);

  const setStep = (id, step) => {
    const nextStep = normalizeProcessingStep(step);
    setItems(it => it.map(x => x.id === id ? { ...x, step: nextStep } : x));
    setPublished(false);
  };
  const applyToAll = (step, options = {}) => {
    if (!items.length) {
      showToast('적용할 자료가 없습니다');
      return;
    }
    const nextStep = normalizeProcessingStep(step);
    setItems(it => it.map(x => ({ ...x, step: nextStep })));
    if (options.silent) return;
    showToast(`전체 ${items.length}개 항목에 ${stepLabel(nextStep)}을(를) 적용했어요`);
  };
  const setPlacement = (id, patch) => {
    const wantsFitWidth = !!patch?.fitWidth;
    const scaleChanged = wantsFitWidth || Object.prototype.hasOwnProperty.call(patch || {}, 'scaleRatio');
    setItems(it => {
      const nextItems = it.map(x => {
        if (x.id !== id) return x;
        const next = { ...x };
        if (Object.prototype.hasOwnProperty.call(patch || {}, 'xRatio')) {
          next.placementXRatio = normalizePlacementXRatio(patch.xRatio);
          next.placementXEdited = patch.xEdited === false ? false : true;
          if (Object.prototype.hasOwnProperty.call(patch || {}, 'magnetColumnIndex')) {
            next.placementMagnetColumnIndex = patch.magnetColumnIndex;
          }
        }
        if (wantsFitWidth) {
          const heightPages = Math.max(0.12, next.heightFrac || 0.8);
          const startPages = Number.isFinite(next.startYPages) ? Math.max(0, next.startYPages) : 0;
          const targetScale = normalizePlacementScaleRatio(
            Object.prototype.hasOwnProperty.call(patch || {}, 'scaleRatio') ? patch.scaleRatio : PLACEMENT_SCALE_MAX,
            PLACEMENT_SCALE_MAX
          );
          next.inputIntent = 'page-as-is';
          next.input_intent = 'page-as-is';
          next.placementMode = 'continuous-page-as-is';
          next.placement_mode = 'continuous-page-as-is';
          next.forceFullPageBounds = true;
          next.force_full_page_bounds = true;
          const slotSpanPages = heightPages * targetScale;
          next.snappedNextStartYPages = Number((startPages + slotSpanPages).toFixed(6));
          next.slotSpanCount = Math.max(1, Math.ceil(slotSpanPages / DEFAULT_SLOT_HEIGHT_PAGES));
          next.placementScaleRatio = targetScale;
        } else if (Object.prototype.hasOwnProperty.call(patch || {}, 'scaleRatio')) {
          next.placementScaleRatio = normalizePlacementScaleRatio(patch.scaleRatio, maxPlacementScaleRatio(next));
        }
        if (Object.prototype.hasOwnProperty.call(patch || {}, 'yRatio')) {
          next.placementYRatio = verticalPlacementRoomPages(next, next.placementScaleRatio) > 0.001
            ? normalizePlacementYRatio(patch.yRatio)
            : DEFAULT_PLACEMENT_Y_RATIO;
        } else if (Object.prototype.hasOwnProperty.call(patch || {}, 'scaleRatio') && verticalPlacementRoomPages(next, next.placementScaleRatio) <= 0.001) {
          next.placementYRatio = DEFAULT_PLACEMENT_Y_RATIO;
        }
        return next;
      });
      return scaleChanged ? reflowItemsForBoardOrder(nextItems, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns) : nextItems;
    });
    setPublished(false);
  };
  const reorder = (fromId, toId, dropPosition = 'before', options = {}) => {
    const reordered = reorderItemsForDrop(items, fromId, toId, dropPosition);
    if (reordered === items) return;
    const resetItems = reordered.map(item => String(item.id) === String(fromId) ? resetItemPlacement(item) : item);
    const nextItems = reflowItemsForBoardOrder(options?.resetPlacement ? resetItems : reordered, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);
    setItems(nextItems);
    if (session) {
      const nextSession = materializeSessionForItems(session, nextItems, fileName, boardColumns) || session;
      setSession(nextSession);
      postRestore(nextSession).catch(e => console.warn('[board] reorder persist failed:', e.message));
    }
    setPublished(false);
  };
  const removeItem = (id) => {
    if (session) {
      if (mutating) {
        showToast('이전 변경을 적용하는 중입니다');
        return;
      }
      void mutateSession('exclude', { problemId: id });
      return;
    }
    const nextItems = reflowItemsForBoardOrder(items.filter(x => x.id !== id), DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);
    setItems(nextItems);
    if (activeId === id) {
      setActiveId(nextItems[0]?.id || null);
    }
    if (!session && usingMock && nextItems.length === 0) {
      setUsingMock(false);
      setFileName('새 세션');
    }
    setPublished(false);
  };
  const addMockSample = () => {
    if (session) {
      showToast('실제 세션에는 더미를 추가하지 않습니다');
      return;
    }
    if (loading || pendingFiles.length > 0) {
      showToast('대기열 처리 전에는 더미를 추가하지 않습니다');
      return;
    }
    const pool = ['geometry-circle','equation','table','graph','geometry-triangles','paragraph'];
    const kind = pool[Math.floor(Math.random() * pool.length)];
    const id = 'i' + (Date.now() % 100000);
    const name = '새 자료 ' + (items.length + 1);
    setItems(it => [...it, {
      id,
      name,
      source: '방금 업로드',
      type: 'image',
      kind,
      step: 'raw',
      heightFrac: heightForKind(kind),
      placementXRatio: DEFAULT_PLACEMENT_X_RATIO,
      placementYRatio: DEFAULT_PLACEMENT_Y_RATIO,
      placementScaleRatio: DEFAULT_PLACEMENT_SCALE_RATIO,
    }]);
    setUsingMock(true);
    setActiveId(id);
    setPublished(false);
    if (!items.length) {
      setFileName('더미 세션');
    }
    showToast('더미 자료 1개 추가됨');
  };

  // when backend is reachable, addSample opens the real file picker → /api/export
  const addSample = () => {
    triggerUpload();
  };

  const onConfirm = (id, options = {}) => {
    const explicitProblemIds = Array.isArray(options.problemIds) ? options.problemIds : null;
    const targetIds = explicitProblemIds?.length
      ? explicitProblemIds
      : options.bulk ? items.map(item => item.id) : [id];
    const confirmedIds = new Set(targetIds.filter(Boolean));
    if (!confirmedIds.size) return;
    const nextItemsForSnapshot = items.map(item => (
      confirmedIds.has(item.id) ? confirmedItemState(item) : item
    ));
    const confirmedSession = markSessionProblemsConfirmed(session, confirmedIds);
    const nextSession = confirmedSession
      ? (materializeSessionForItems(confirmedSession, nextItemsForSnapshot, fileName, boardColumns) || confirmedSession)
      : null;
    setItems(prev => prev.map(item => (
      confirmedIds.has(item.id) ? confirmedItemState(item) : item
    )));
    setSession(prev => nextSession || markSessionProblemsConfirmed(prev, confirmedIds));
    if (nextSession) {
      postRestore(nextSession).catch(e => console.warn('[board] confirm persist failed:', e.message));
    }
    setPublished(false);
    if (options.bulk) {
      showToast(`전체 ${confirmedIds.size}개 확인 완료`);
      return;
    }
    showToast(`"${items.find(i=>i.id===id)?.name}" 확인 완료`);
  };

  const markClassinReviewComplete = useCallback(async () => {
    if (!session) {
      showToast('저장할 제작 세션이 없습니다');
      return;
    }
    try {
      const updated = await postClassinReviewResult({
        status: 'passed',
        notes: '',
      });
      setSession(updated);
      refreshSessionHistory();
      setPublished(true);
      showToast('ClassIn 검수 완료로 저장했어요');
    } catch (e) {
      showSimpleErrorToast(e, 'ClassIn 검수 저장 실패');
    }
  }, [session, refreshSessionHistory, showSimpleErrorToast]);

  const onPublish = async () => {
    if (!session || !Array.isArray(session.problems)) {
      showToast('내보낼 자료가 없습니다. 먼저 파싱해 주세요.');
      return;
    }
    const sessionIds = new Set(session.problems.map(p => p.id));
    const currentIds = items.map(i => i.id);
    const order = currentIds.filter(id => sessionIds.has(id));
    const excluded = [...sessionIds].filter(id => !currentIds.includes(id));
    const itemsForPublish = reflowItemsForBoardOrder(items, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);
    const sessionForPublish = materializeSessionForItems(session, itemsForPublish, fileName, boardColumns) || session;
    const publishReviewSummary = sessionReviewSummary(sessionForPublish);
    const duplicateProblemNumberGroups = Array.isArray(publishReviewSummary.blockingDuplicateProblemNumberGroups)
      ? publishReviewSummary.blockingDuplicateProblemNumberGroups
      : [];
    if (duplicateProblemNumberGroups.length > 0) {
      setReviewFocus({
        filter: 'all',
        problemIds: duplicateProblemNumberGroups.flatMap(group => (
          Array.isArray(group?.problemIds || group?.problem_ids) ? (group.problemIds || group.problem_ids) : []
        )),
        source: 'duplicate-number-preflight',
      });
      setView('review');
      const duplicateLabel = publishReviewSummary.duplicateProblemNumberLabel || `${duplicateProblemNumberGroups.length}그룹`;
      showToast(`중복 문항 번호가 있어 제작을 멈췄어요. ${duplicateLabel}`);
      return;
    }
    const passageSourceReuseIssues = findPassageGroupSourceReuse(sessionForPublish.problems || [])
      .filter(issue => issue.type === 'passage_group_source_reuse');
    if (passageSourceReuseIssues.length > 0) {
      const firstIssue = passageSourceReuseIssues[0];
      const focusProblemIds = Array.isArray(firstIssue.problemIds || firstIssue.problem_ids)
        ? (firstIssue.problemIds || firstIssue.problem_ids)
        : [firstIssue.problemId, firstIssue.nextProblemId];
      setReviewFocus({
        filter: 'all',
        problemIds: focusProblemIds,
        source: 'passage-source-reuse-preflight',
      });
      setView('review');
      showToast(
        `긴 지문 그룹 안에서 원본 영역이 반복될 수 있어 제작을 멈췄어요. ${firstIssue.problemTitle || firstIssue.problemId} → ${firstIssue.nextProblemTitle || firstIssue.nextProblemId}`
      );
      return;
    }
    const sourceOverlapIssues = findSourceProblemOverlaps(sessionForPublish.problems || [])
      .filter(issue => issue.type === 'source_problem_bbox_overlap');
    if (sourceOverlapIssues.length > 0) {
      const firstIssue = sourceOverlapIssues[0];
      const focusProblemIds = Array.isArray(firstIssue.problemIds || firstIssue.problem_ids)
        ? (firstIssue.problemIds || firstIssue.problem_ids)
        : [firstIssue.problemId, firstIssue.nextProblemId];
      setReviewFocus({
        filter: 'all',
        problemIds: focusProblemIds,
        source: 'source-overlap-preflight',
      });
      setView('review');
      showToast(
        `문항 원본 영역이 겹칠 수 있어 제작을 멈췄어요. ${firstIssue.problemTitle || firstIssue.problemId} → ${firstIssue.nextProblemTitle || firstIssue.nextProblemId}`
      );
      return;
    }
    const placementOverlapIssues = findBoardPlacementOverlaps(items, { sessionProblemIds: sessionIds })
      .filter(issue => issue.type === 'board_placement_overlap');
    if (placementOverlapIssues.length > 0) {
      const firstIssue = placementOverlapIssues[0];
      setView('board');
      showToast(
        `문항 배치가 겹칠 수 있어 제작을 멈췄어요. ${firstIssue.problemTitle || firstIssue.problemId} → ${firstIssue.nextProblemTitle || firstIssue.nextProblemId}`
      );
      return;
    }
    const publishReviewWarning = publishReviewWarningMessage(sessionForPublish, publishReviewSummary);
    if (publishReviewWarning) {
      const confirmedPublish = window.confirm(publishReviewWarning.message);
      if (!confirmedPublish) {
        setReviewFocus({ filter: publishReviewWarning.reviewFilter, source: 'publish-warning' });
        setView('review');
        showToast(publishReviewWarning.cancelToast);
        return;
      }
    }
    const placements = Object.fromEntries(
      itemsForPublish
        .filter(item => sessionIds.has(item.id))
        .map(item => [item.id, {
          xRatio: normalizePlacementXRatio(item.placementXRatio),
          yRatio: verticalPlacementRoomPages(item) > 0.001
            ? normalizePlacementYRatio(item.placementYRatio)
            : DEFAULT_PLACEMENT_Y_RATIO,
          scaleRatio: normalizePlacementScaleRatio(item.placementScaleRatio, maxPlacementScaleRatio(item)),
        }])
    );
    setLoading({
      label: '편집된 .edb 파일 생성 중...',
      hint: order.length === sessionIds.size
        ? `${order.length}개 자료를 칠판 순서대로 재배치합니다.`
        : `${order.length}개 자료 (${excluded.length}개 제외)`,
      startedAt: Date.now(),
    });
    try {
      const resp = await fetch('/api/session/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order,
          excluded,
          placements,
          edbName: edbFileNameFromSessionName(fileName, sessionForPublish?.session_name || 'classin'),
          session: sessionForPublish,
        }),
      });
      const json = await readJsonResponse(resp, 'publish 실패');
      if (!resp.ok || !json.ok) {
        const blockedPublish = normalizePublishPreflightBlock(json);
        if (blockedPublish) {
          const blockedTarget = publishBlockedTarget(blockedPublish);
          setReviewFocus(blockedTarget.reviewFocus);
          setView(blockedTarget.view);
          showToast(
            `서버 사전점검에서 제작을 멈췄어요. ${blockedPublish.issueSummaryLabel || blockedPublish.message}`
          );
          return;
        }
        throw new Error(json.error || `publish 실패 (${resp.status})`);
      }
      setSession(json.session);
      refreshSessionHistory();
      const publishSummary = json.publishSummary || json.publish_summary || json.session?.publishSummary || json.session?.publish_summary;
      const normalizedPublishSummary = normalizePublishSummary(publishSummary, json.session);
      if (normalizedPublishSummary?.canDownload) {
        downloadPublishSummary(normalizedPublishSummary);
      }
      setPublished(true);
      const publishLabel = publishSummary?.recordCountLabel || publishSummary?.record_count_label || `${publishSummary?.recordCount || publishSummary?.record_count || order.length}개 자료`;
      showToast(`${publishLabel}로 EDB 제작 완료 · 다운로드 시작`);
    } catch (e) {
      showSimpleErrorToast(e, '제작 실패');
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="app">
      <TopBar
        fileName={fileName}
        setFileName={setFileName}
        progress={progress}
        processed={processed}
        total={items.length}
        onPublish={onPublish}
        published={published}
        onReset={resetSession}
        onRefresh={refreshSession}
        refreshing={refreshing}
        canReset={(!!session || items.length > 0 || pendingFiles.length > 0 || recentSessions.length > 0) && !loading}
        view={view}
        setView={setView}
        reviewAvailable={reviewAvailable}
        onUndo={undoMutation}
        canUndo={canUndo}
        onShutdown={shutdownApp}
        onExportImages={exportSessionImages}
        exportingImages={exportingImages}
        canExportImages={!!session && items.some(item => item?.id && !item.excluded)}
      />
      <div className="main">
        <ItemsRail
          items={items}
          activeId={activeId}
          setActive={selectBoardItem}
          reorder={reorder}
          removeItem={removeItem}
          addSample={addSample}
          bulkApply={applyToAll}
          handleFiles={handleFiles}
          pendingFiles={pendingFiles}
          selectedPendingFileKey={selectedPendingFileKey}
          onSelectPendingFile={selectPendingFile}
          removePendingFile={removePendingFile}
          clearPendingFiles={clearPendingFiles}
          processQueuedFiles={processQueuedFiles}
          queueBusy={!!loading || hasRunningQueueRecognition}
          aiAvailable={!!userSettings?.hasGeminiApiKey}
          addMockSample={addMockSample}
          canAddDummy={!session && !loading && pendingFiles.length === 0}
          recentSessions={recentSessions}
          restoringSessionId={restoringSessionId}
          onRestoreRecentSession={restoreRecentSession}
          onDownloadItemImage={downloadItemImage}
          downloadingItemId={downloadingItemId}
        />
        {view === 'review' ? (
          <ReviewStage
            session={session}
            items={items}
            activeId={activeId}
            setActive={selectBoardItem}
            mutateSession={mutateSession}
            retryAiSession={retryAiSession}
            mutating={mutating}
            aiAvailable={!!userSettings?.hasGeminiApiKey}
            aiBusy={hasRunningSessionRecognition}
            onConfirm={onConfirm}
            reviewFocus={reviewFocus}
          />
        ) : (
          <BoardStage
            items={items}
            activeId={activeId}
            setActive={selectBoardItem}
            boardColor={t.boardColor}
            boardColumns={boardColumns}
            fileName={fileName}
            addSample={addSample}
            setPlacement={setPlacement}
            reorder={reorder}
          />
        )}
        <SidePanel
          item={active}
          items={items}
          activeIndex={activeIndex}
          setStep={setStep}
          applyToAll={applyToAll}
          bulk={bulk}
          setBulk={setBulk}
          setPlacement={setPlacement}
          mutateSession={mutateSession}
          mutating={mutating}
          boardColumns={boardColumns}
          setBoardColumns={v => setTweak('boardColumns', v)}
          boardColor={t.boardColor}
          setBoardColor={v => setTweak('boardColor', v)}
          accent={t.accent}
          setAccent={v => setTweak('accent', v)}
          onConfirm={onConfirm}
          userSettings={userSettings}
          runtimeDiagnostics={runtimeDiagnostics}
          onSaveGeminiKey={onSaveGeminiKey}
          onSaveOpenAiKey={onSaveOpenAiKey}
          onEnhanceImage={enhanceImageSession}
          imageEnhanceBusy={hasRunningImageEnhance}
          aiEnabled={aiEnabled}
          setAiEnabled={setAiEnabled}
          inputIntent={inputIntent}
          setInputIntent={setInputIntent}
          onRecognizeSession={recognizeCurrentSession}
          canRecognizeSession={!!session && pendingFiles.length === 0 && !!userSettings?.hasGeminiApiKey && !mutating && !hasRunningSessionRecognition}
          session={session}
          published={published}
          onClassinReviewComplete={markClassinReviewComplete}
          onExportImages={exportSessionImages}
          exportingImages={exportingImages}
          canExportImages={!!session && items.some(item => item?.id && !item.excluded)}
          updateInfo={updateInfo}
          updateBusy={updateBusy}
          onCheckUpdate={checkForUpdates}
          onOpenUpdate={openUpdatePage}
          view={view}
          pendingFile={selectedPendingFile}
          pendingFileKey={selectedPendingFileKey}
          processQueuedFiles={processQueuedFiles}
          queueBusy={!!loading || hasRunningQueueRecognition}
          onPendingPreviewError={showPendingPreviewError}
        />
      </div>

      <BackgroundJobsPanel
        jobs={backgroundJobs}
        onCancel={cancelBackgroundJob}
        onDismiss={dismissBackgroundJob}
      />

      <RecognitionCancelBanner
        job={runningRecognitionJob}
        onCancel={cancelBackgroundJob}
      />

      <RecognitionReviewModal
        review={recognitionReview}
        confirming={confirmingRecognition}
        onConfirm={confirmRecognitionReview}
        onCancel={cancelRecognitionReview}
      />

      <TooltipLayer />

      {toast && <div className="toast">{Icon.check}<span>{toast}</span></div>}

      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.hwp,.hwpx,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,image/*"
        multiple
        style={{ display: 'none' }}
        onChange={e => handleFiles(e.target.files)}
      />

      {loading && (
        <LoadingOverlay
          label={loading.label}
          hint={loading.hint}
          startedAt={loading.startedAt}
        />
      )}

      <TweaksPanel title="Tweaks">
        <TweakSection label="테마" />
        <TweakToggle label="다크 모드" value={t.dark} onChange={v => setTweak('dark', v)} />
        <TweakColor label="강조색" value={t.accent} options={ACCENTS} onChange={v => setTweak('accent', v)} />
        <TweakSection label="칠판" />
        <TweakColor label="칠판 색" value={t.boardColor} options={BOARD_COLORS} onChange={v => setTweak('boardColor', v)} />
        <TweakRadio label="한 줄 자료 수" value={String(t.boardColumns)} options={['1','2','3']} onChange={v => setTweak('boardColumns', parseInt(v))} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <AppErrorBoundary>
    <App />
  </AppErrorBoundary>
);
