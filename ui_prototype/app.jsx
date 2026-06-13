// 칠판 자료 편집기 — main app
const { useState, useRef, useEffect, useLayoutEffect, useMemo, useCallback } = React;

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
const findBoardPlacementOverlaps = PUBLISH_GUARD.findBoardPlacementOverlaps || (() => []);
const findSourceProblemOverlaps = PUBLISH_GUARD.findSourceProblemOverlaps || (() => []);
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
const DEFAULT_SLOT_HEIGHT_PAGES = 1.2;
const DEFAULT_PLACEMENT_X_RATIO = 0;
const DEFAULT_PLACEMENT_Y_RATIO = 0;
const DEFAULT_PLACEMENT_SCALE_RATIO = 1;
const PLACEMENT_SCALE_MIN = 0.6;
const PLACEMENT_SCALE_MAX = 1.6;
const PLACEMENT_NUDGE_STEP = 0.04;
const PLACEMENT_SCALE_STEP = 0.05;

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

function snapUpPages(value, slotHeight = DEFAULT_SLOT_HEIGHT_PAGES){
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.ceil((value - 0.001) / slotHeight) * slotHeight;
}

function placementSlotHeightPages(item){
  if (!item) return 0;
  const heightPages = Math.max(0.12, item.heightFrac || 0.8);
  const startPages = Number.isFinite(item.startYPages) ? Math.max(0, item.startYPages) : 0;
  const snappedNext = Number.isFinite(item.snappedNextStartYPages)
    ? Math.max(startPages + heightPages, item.snappedNextStartYPages)
    : snapUpPages(startPages + heightPages);
  return Math.max(heightPages, snappedNext - startPages);
}

function maxPlacementScaleRatio(item){
  if (!item) return PLACEMENT_SCALE_MAX;
  const heightPages = Math.max(0.12, item.heightFrac || 0.8);
  const slotHeightPages = placementSlotHeightPages(item);
  return Math.max(PLACEMENT_SCALE_MIN, Math.min(PLACEMENT_SCALE_MAX, slotHeightPages / heightPages));
}

function verticalPlacementRoomPages(item, scaleRatio = item?.placementScaleRatio){
  if (!item) return 0;
  const heightPages = Math.max(0.12, item.heightFrac || 0.8);
  const scale = normalizePlacementScaleRatio(scaleRatio, maxPlacementScaleRatio(item));
  return Math.max(0, placementSlotHeightPages(item) - (heightPages * scale));
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
  check:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 13l4 4L19 7"/></svg>,
  board:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="13" rx="1"/><path d="M8 21h8M12 17v4"/></svg>,
  download:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>,
  folder: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 6.5h6l2 2h9v9.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M3.5 8.5V6a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v.5"/></svg>,
  zoomIn: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 11h6M11 8v6"/></svg>,
  zoomOut:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 11h6"/></svg>,
  undo:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3"/></svg>,
  refresh:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3.5-7.1M21 4v5h-5"/></svg>,
  reset:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/></svg>,
  pen:    <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M16 3l5 5L8 21H3v-5L16 3z"/></svg>,
  align:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 6h10M4 12h16M4 18h7"/></svg>,
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

// ─── TOP BAR ──────────────────────────────────────────────────────────────
function TopBar({ fileName, setFileName, progress, processed, total, onPublish, published, onReset, onRefresh, refreshing, canReset, view, setView, reviewAvailable, onUndo, canUndo }){
  return (
    <div className="topbar">
      <div className="brand">
        <span className="logo">板</span>
        칠판 자료 편집기
      </div>
      <div className="crumb">
        <span>수업 ›</span>
        <input value={fileName} onChange={e => setFileName(e.target.value)} />
      </div>
      <div className="spacer" />
      <div className="view-toggle" title={reviewAvailable ? '' : '먼저 자료를 업로드하세요'}>
        <button className={view === 'board' ? 'on' : ''} onClick={() => setView && setView('board')}>칠판</button>
        <button
          className={view === 'review' ? 'on' : ''}
          onClick={() => reviewAvailable && setView && setView('review')}
          disabled={!reviewAvailable}
          style={!reviewAvailable ? { cursor: 'not-allowed', opacity: .5 } : null}
        >검수</button>
      </div>
      <div className="progress" title={`${processed} / ${total} 처리됨`}>
        <div className="bar"><i style={{ width: `${Math.round(progress*100)}%` }} /></div>
        <span>{processed}/{total} 처리됨</span>
      </div>
      <button
        className="btn ghost"
        onClick={onRefresh}
        disabled={refreshing}
        title="저장된 세션을 다시 불러옵니다"
      >
        <span className={refreshing ? 'spin-ic' : ''} style={{display:'inline-flex'}}>{Icon.refresh}</span>
        <span style={{marginLeft:4}}>{refreshing ? '불러오는 중…' : '새로고침'}</span>
      </button>
      <button className="btn ghost" onClick={onReset} disabled={!canReset} title="세션, 더미, 업로드 대기열을 비웁니다">
        {Icon.reset}<span style={{marginLeft:4}}>초기화</span>
      </button>
      <button
        className="btn ghost icon"
        title={canUndo ? '검수 변경 되돌리기 (Ctrl/Cmd+Z)' : '되돌릴 변경이 없습니다'}
        onClick={onUndo}
        disabled={!canUndo}
      >{Icon.undo}</button>
      <button className={`btn primary ${published ? 'done' : ''}`} onClick={onPublish}>
        {published ? <>{Icon.check} 제작 완료</> : <>{Icon.board} EDB 제작</>}
      </button>
    </div>
  );
}

// ─── REVIEW STAGE: detected-box overlay with split / merge / exclude ─────
function ReviewStage({ session, items, activeId, setActive, mutateSession, retryAiSession, mutating, aiAvailable, aiBusy, onConfirm }){
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
  const [reviewFilter, setReviewFilter] = useState('all');
  const [reviewRiskFilter, setReviewRiskFilter] = useState(null);
  const splitDraggingRef = useRef(false);
  const splitBoxRef = useRef(null);

  // Cancel split mode if the session changes underneath (e.g. after a mutation).
  useEffect(() => {
    setSplitTarget(null);
    setSelectedIds(new Set());
    setReviewRiskFilter(null);
  }, [session]);

  const onBoxClick = (probId, evt) => {
    if (splitTarget) return;  // ignore clicks while splitting
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

  const selectedList = Array.from(selectedIds);
  const selectedProblems = selectedList.map(id => problemsById.get(id)).filter(Boolean);
  const sameSourcePage = selectedProblems.length >= 2
    && selectedProblems.every(p => p.sourcePageId === selectedProblems[0].sourcePageId);
  const statusCounts = useMemo(() => {
    const helperCounts = globalThis.EDB_REVIEW_FILTERS?.countReviewFilters?.(session?.problems || []);
    if (helperCounts) return helperCounts;
    const counts = { all: 0, normal: 0, check_needed: 0, failed: 0, passage: 0, passageGroups: 0 };
    const passageGroups = new Set();
    (session?.problems || []).forEach(problem => {
      const status = deriveProblemStatus(problem);
      counts.all += 1;
      counts[status] = (counts[status] || 0) + 1;
      const passageGroupId = passageGroupIdFor(problem);
      if (passageGroupId) {
        counts.passage += 1;
        passageGroups.add(passageGroupId);
      }
    });
    counts.supplemental = (session?.problems || []).filter(isSupplementalProblem).length;
    counts.passageGroups = passageGroups.size;
    return counts;
  }, [session]);
  const sessionCounts = useMemo(() => sessionProblemCounts(session), [session]);
  const reviewSummary = useMemo(() => sessionReviewSummary(session), [session]);
  const riskFilterHasProblemMatches = useMemo(() => {
    if (!reviewRiskFilter) return false;
    return (session?.problems || []).some(problem => hasRiskFlag(problem, reviewRiskFilter));
  }, [session, reviewRiskFilter]);
  const pageRetryIds = useMemo(() => {
    const ids = [];
    const byId = problemsById;
    pages.forEach(page => {
      const pageFlags = riskFlagsFor(page);
      const pageStatus = normalizeReviewStatus(page.reviewStatus || page.review_status);
      const hasProblemRisk = (page.problemIds || [])
        .map(pid => byId.get(pid))
        .filter(Boolean)
        .some(problem => deriveProblemStatus(problem) !== 'normal');
      if (pageStatus === 'failed' || (Array.isArray(pageFlags) && pageFlags.length) || !(page.problemIds || []).length || hasProblemRisk) {
        ids.push(page.id);
      }
    });
    return listUnique(ids.filter(Boolean));
  }, [pages, problemsById]);
  const activeReviewFilter = reviewFilter !== 'all' || Boolean(reviewRiskFilter);
  const visibleReviewScope = useMemo(() => {
    const retryPageIdSet = new Set();
    const problemIdSet = new Set();
    let problemCount = 0;
    pages.forEach(page => {
      const allPageProblems = (page.problemIds || [])
        .map(pid => problemsById.get(pid))
        .filter(Boolean);
      const pageMatchesRiskFilter = reviewRiskFilter && !riskFilterHasProblemMatches
        ? hasRiskFlag(page, reviewRiskFilter)
        : false;
      const pageProblems = allPageProblems
        .filter(problem => problemMatchesReviewFilter(problem, reviewFilter))
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
  }, [pages, problemsById, pageRetryIds, reviewFilter, reviewRiskFilter, riskFilterHasProblemMatches]);
  const actionableProblemIds = useMemo(() => (session?.problems || [])
    .filter(problem => problem?.id && deriveProblemStatus(problem) !== 'normal')
    .map(problem => problem.id), [session]);
  const selectedRetryPageIds = listUnique(selectedProblems.map(problem => problem.sourcePageId).filter(Boolean));
  const selectedHasRetryable = selectedProblems.some(problem => deriveProblemStatus(problem) !== 'normal');

  const beginSplit = () => {
    if (selectedList.length !== 1) return;
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
  const doExclude = async () => {
    if (selectedList.length === 0) return;
    if (selectedList.length === 1) {
      await mutateSession?.('exclude', { problemId: selectedList[0] });
      return;
    }
    await mutateSession?.('exclude', { problemIds: selectedList });
  };
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

  const actionBar = splitTarget ? (
    <div className="review-actionbar">
      <span className="count-chip">가르기 중</span>
      <span className="hint">박스 안의 파란 선을 드래그해서 위치를 정한 다음 [가르기]를 눌러주세요.</span>
      <div className="spacer" />
      <button className="btn" onClick={cancelSplit} disabled={mutating}>취소</button>
      <button className="btn primary" onClick={confirmSplit} disabled={mutating}>
        ✂ {(splitRatio * 100).toFixed(0)}% 위치에서 가르기
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
          AI 재인식 {bulkRetryProblemCount}
        </button>
      )}
    </div>
  ) : (
    <div className="review-actionbar">
      <span className="count-chip">{selectedList.length}개 선택됨</span>
      <span className="hint">
        {selectedList.length === 1
          ? '한 박스를 두 문제로 가르거나, 이 박스를 제외할 수 있어요.'
          : sameSourcePage
            ? '같은 페이지의 박스들을 하나로 합치거나, 모두 제외할 수 있어요.'
            : '같은 페이지의 박스만 합칠 수 있어요. (현재 선택은 페이지가 다름)'}
      </span>
      <div className="spacer" />
      <button
        className="btn"
        type="button"
        onClick={() => onConfirm?.(null, { problemIds: selectedList, bulk: true })}
        disabled={mutating}
      >
        확인 완료 {selectedList.length}
      </button>
      <button
        className="btn primary"
        type="button"
        title={retryDisabledReason || `${selectedRetryPageIds.length}개 페이지 재인식`}
        onClick={() => doRetryAi(selectedRetryPageIds)}
        disabled={!aiAvailable || aiBusy || mutating || !selectedHasRetryable || !selectedRetryPageIds.length}
      >
        AI 재인식 {selectedList.length}
      </button>
      {selectedList.length === 1 && (
        <button className="btn primary" onClick={beginSplit} disabled={mutating}>✂ 가르기</button>
      )}
      {selectedList.length >= 2 && (
        <button className="btn primary" onClick={doMerge} disabled={!sameSourcePage || mutating}>
          ⇲ 합치기
        </button>
      )}
      <button className="btn danger" onClick={doExclude} disabled={mutating}>
        {Icon.trash} 제외 {selectedList.length}
      </button>
      <button className="btn" onClick={() => setSelectedIds(new Set())} disabled={mutating}>선택 해제</button>
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
        </div>
        <div className="review-wrap">
          {actionBar}
          <div className="review-summary-strip">
            <span className="review-summary-title">검수 요약</span>
            <span className="review-summary-chip">{formatProblemCount(reviewSummary.counts)}</span>
            <span className={`review-summary-chip ${reviewSummary.warningCount ? 'warn' : 'ok'}`}>
              주의 {reviewSummary.warningCount}
            </span>
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
              .filter(Boolean);
            const pageMatchesRiskFilter = reviewRiskFilter && !riskFilterHasProblemMatches
              ? hasRiskFlag(page, reviewRiskFilter)
              : false;
            const pageProblems = allPageProblems
              .filter(problem => problemMatchesReviewFilter(problem, reviewFilter))
              .filter(problem => !reviewRiskFilter || pageMatchesRiskFilter || hasRiskFlag(problem, reviewRiskFilter));
            if ((reviewFilter !== 'all' || reviewRiskFilter) && pageProblems.length === 0) return null;
            const pageCounts = countSessionProblems(allPageProblems);
            const pageRiskFlags = riskFlagsFor(page);
            const pageStatus = normalizeReviewStatus(page.reviewStatus || page.review_status)
              || (!(page.problemIds || []).length ? 'failed' : pageRiskFlags.length ? 'check_needed' : 'normal');
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
                  <span style={{ fontSize: 11, color: 'var(--muted-2)', fontFamily: "'JetBrains Mono', monospace" }}>
                    {page.width}×{page.height}
                  </span>
                </div>
                <div className="review-page-canvas">
                  {page.sourceImageUri ? (
                    <img src={page.sourceImageUri} alt={page.id} draggable={false} />
                  ) : (
                    <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
                      페이지 이미지를 불러올 수 없어요.
                    </div>
                  )}
                  {pageProblems.map(prob => {
                    const bbox = prob.bbox || {};
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
                        onClick={(evt) => onBoxClick(prob.id, evt)}
                        title={tooltipParts.filter(Boolean).join(' · ')}
                      >
                        <div className="review-bbox-label">
                          {String(order || '?').padStart(2, '0')}
                          {isPassage && <span className="review-bbox-passage-tag">지문</span>}
                          {isRisky && <span className="review-bbox-risk">{statusMeta.shortLabel}</span>}
                        </div>
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
  pendingFiles, removePendingFile, clearPendingFiles, processQueuedFiles, queueBusy, aiAvailable,
  addMockSample, canAddDummy, recentSessions, restoringSessionId, onRestoreRecentSession,
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
      if (target) reorder(drag.id, target.id, target.position);
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    pointerDragRef.current = null;
    clearDragState();
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
        <button className="icon-btn" title="전체를 2단계 AI 변환" onClick={() => bulkApply('s2')} disabled={!items.length}>{Icon.aiBatch}</button>
        <button
          className="icon-btn"
          title={canAddDummy ? '더미 추가' : '실제 세션 또는 대기열이 있을 때는 더미를 추가하지 않습니다'}
          onClick={addMockSample}
          disabled={!canAddDummy}
        >{Icon.wand}</button>
        <button className="icon-btn" title="파일 추가" onClick={addSample}>{Icon.upload}</button>
      </div>

      <div className="items" ref={railRef}>
        <div
          className={`drop-zone ${dropZoneActive ? 'is-active' : ''}`}
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
          <strong style={{marginTop:6}}>이미지·PDF·HWP 대기열에 추가</strong>
          <small>파일별로 그대로 등록하거나 AI 인식합니다</small>
        </div>

        {!!recentSessions?.length && (
          <div className="session-history-card">
            <div className="source-queue-head">
              <strong>최근 작업</strong>
              <span>{recentSessions.length}개</span>
            </div>
            <div className="session-history-list">
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
                return (
                <div className="source-queue-row" key={key}>
                  <span className="idx">{String(index + 1).padStart(2, '0')}</span>
                  <div className="file">
                    <div className="name">{file.name || '이름 없는 파일'}</div>
                    <div className="meta">{sourceFileKindLabel(file)} · {formatBytes(file.size)}</div>
                  </div>
                  <button
                    className="icon-btn queue-row-action"
                    title="이 파일을 한 문제/한 자료로 그대로 등록"
                    onClick={() => processQueuedFiles('register', key)}
                    disabled={queueBusy}
                  >
                    {Icon.check}
                  </button>
                  <button
                    className="icon-btn queue-row-action"
                    title="이 파일만 AI 문제 인식"
                    onClick={() => processQueuedFiles('recognize', key)}
                    disabled={queueBusy}
                  >
                    {Icon.aiBatch}
                  </button>
                  <button
                    className="icon-btn"
                    title="대기열에서 제거"
                    onClick={() => removePendingFile(key)}
                    disabled={queueBusy}
                  >
                    {Icon.trash}
                  </button>
                </div>
              );})}
            </div>
            <div className="source-queue-actions">
              <button
                className="btn"
                type="button"
                title="대기열 전체를 한 문제 또는 한 자료로 그대로 등록"
                onClick={() => processQueuedFiles('register')}
                disabled={queueBusy}
              >
                {Icon.check}<span>전체 그대로 등록</span>
              </button>
              <button
                className="btn primary"
                type="button"
                title="대기열 전체를 문제별로 AI 인식"
                onClick={() => processQueuedFiles('recognize')}
                disabled={queueBusy}
              >
                {Icon.aiBatch}<span>전체 AI 인식</span>
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
              if (sourceId && sourceId !== it.id) reorder(sourceId, it.id, position);
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
                <span
                  className={`tag status-tag ${reviewStatusClass(it.reviewStatus)}`}
                  title={it.statusLabel}
                  aria-label={it.statusLabel}
                >
                  {it.statusShortLabel || it.statusLabel}
                </span>
                {it.step === 's1' && <span className="tag s1">1단계</span>}
                {it.step === 's2' && <span className="tag s2">AI</span>}
                {it.step === 's3' && <span className="tag s3">재구성</span>}
                {it.step === 'raw' && <span className="tag">대기</span>}
                <span style={{whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>{it.source}</span>
              </div>
            </div>
            <div className="actions">
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

// ─── CENTER: big scrollable board stage (page-flow: 1 problem per page) ──
function BoardStage({ items, activeId, setActive, boardColor, fileName, addSample, setPlacement }){
  const scrollRef = useRef(null);
  const contentRef = useRef(null);
  const tileRefs = useRef({});
  const syncLock = useRef(0);
  const positionDragRef = useRef(null);
  const suppressClickRef = useRef(null);
  const [positioningId, setPositioningId] = useState(null);
  const [pageH, setPageH] = useState(400);
  const [contentW, setContentW] = useState(0);

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

  // compute page-flow positions: each item starts at next page boundary
  // after the previous item ends.  e.g. item ending at 1.8p → next at 2p.
  const layout = useMemo(() => {
    const EPS = 0.001;
    const positions = [];
    const usesPlacement = items.some(it => Number.isFinite(it.startYPages));
    let cursorPages = 0;
    let maxBottom = 0;
    items.forEach((it) => {
      const heightPages = Math.max(0.12, it.heightFrac || 0.8);
      const startPages = usesPlacement && Number.isFinite(it.startYPages)
        ? Math.max(0, it.startYPages)
        : cursorPages;
      const top = startPages * pageH;
      const height = heightPages * pageH;
      const snappedNext = Number.isFinite(it.snappedNextStartYPages)
        ? Math.max(startPages + heightPages, it.snappedNextStartYPages)
        : snapUpPages(startPages + heightPages);
      positions.push({
        top,
        height,
        page: Math.floor(top / pageH) + 1,
        spans: Math.max(1, Math.ceil(height / pageH)),
        startPages,
        heightPages,
        snappedNext,
      });
      cursorPages = snappedNext;
      maxBottom = Math.max(maxBottom, snappedNext * pageH, top + height);
    });
    const endTop = items.length === 0 ? 0 : Math.ceil(maxBottom / pageH - EPS) * pageH;
    const endH = pageH * 0.42;
    const totalH = endTop + endH;
    const totalPages = Math.max(1, Math.ceil(totalH / pageH));
    return { positions, endTop, endH, totalH, totalPages, usesPlacement };
  }, [items, pageH]);

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

  const beginPositionDrag = (evt, item, placement) => {
    if (evt.button !== 0 || !contentRef.current) return;
    const contentRect = contentRef.current.getBoundingClientRect();
    const tileRect = evt.currentTarget.getBoundingClientRect();
    const maxLeft = Math.max(1, contentRect.width - tileRect.width);
    const maxTopOffset = Math.max(0, (placement.snappedNext * pageH) - placement.top - tileRect.height);
    const startXRatio = normalizePlacementXRatio(item.placementXRatio);
    const startYRatio = normalizePlacementYRatio(item.placementYRatio);
    positionDragRef.current = {
      id: item.id,
      pointerId: evt.pointerId,
      startX: evt.clientX,
      startY: evt.clientY,
      startLeft: startXRatio * maxLeft,
      startTopOffset: startYRatio * maxTopOffset,
      maxLeft,
      maxTopOffset,
      lastXRatio: startXRatio,
      lastYRatio: startYRatio,
      moved: false,
    };
    setPositioningId(item.id);
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
    const nextLeft = Math.max(0, Math.min(drag.maxLeft, drag.startLeft + dx));
    const nextTopOffset = Math.max(0, Math.min(drag.maxTopOffset, drag.startTopOffset + dy));
    const nextXRatio = drag.maxLeft > 0 ? nextLeft / drag.maxLeft : DEFAULT_PLACEMENT_X_RATIO;
    const nextYRatio = drag.maxTopOffset > 0 ? nextTopOffset / drag.maxTopOffset : DEFAULT_PLACEMENT_Y_RATIO;
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
    });
  };

  const endPositionDrag = (evt) => {
    const drag = positionDragRef.current;
    if (!drag || drag.pointerId !== evt.pointerId) return;
    if (drag.moved) {
      suppressClickRef.current = drag.id;
      window.setTimeout(() => {
        if (suppressClickRef.current === drag.id) suppressClickRef.current = null;
      }, 0);
    }
    evt.currentTarget.releasePointerCapture?.(evt.pointerId);
    positionDragRef.current = null;
    setPositioningId(null);
  };

  const processedCount = items.filter(i => i.step !== 'raw').length;
  const aiCount = items.filter(i => i.step === 's2').length;
  const reconstructCount = items.filter(i => i.step === 's3').length;
  const rawCount = items.filter(i => i.step === 'raw').length;
  const s1Count = items.filter(i => i.step === 's1').length;
  const leftZonePercent = `${FIXED_LEFT_ZONE_RATIO * 100}%`;

  // page-boundary divider lines (between page N and N+1)
  const dividers = [];
  for (let i = 1; i < Math.min(layout.totalPages, 50); i++){
    dividers.push(i * pageH);
  }

  return (
    <div className="col center">
      <div className="stage">
        <div className="stage-toolbar">
          <span className="name">실시간 칠판 미리보기</span>
          <span className="pill"><span className="dotc" /> {fileName.length > 32 ? fileName.slice(0,30)+'…' : fileName}</span>
          <div className="spacer" />
          <button className="btn ghost icon" title="자동 정렬">{Icon.align}</button>
          <button className="btn ghost icon" title="확대">{Icon.zoomIn}</button>
          <div style={{ width: 1, height: 22, background: 'var(--line)', margin: '0 4px' }} />
          <button className="btn ghost"><span style={{opacity:.6, marginRight:4}}>화면</span>맞춤</button>
        </div>

        <div className="stage-wrap">
          <div className="stage-board" style={{ background: boardColor }}>

            <div className="stage-scroll" ref={scrollRef} onScroll={onScroll}>
              <div
                className="stage-content"
                ref={contentRef}
                style={{ height: layout.totalH, '--left-zone-width': leftZonePercent }}
              >
                {/* page boundary dividers — scroll with content */}
                {dividers.map((top, i) => (
                  <div key={i} className="page-divider" style={{ top }}>
                    <span className="label">— {i + 2} 페이지 —</span>
                  </div>
                ))}

                {items.map((it, i) => {
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
                  const xRatio = normalizePlacementXRatio(it.placementXRatio);
                  const yRatio = normalizePlacementYRatio(it.placementYRatio);
                  const tileStyle = {
                    top: p.top + (yRatio * maxTopOffset),
                    height: scaledHeight,
                    ...(scaledWidth ? { left: xRatio * maxLeft, width: scaledWidth } : null),
                  };
                  return (
                    <button
                      key={it.id}
                      ref={el => { tileRefs.current[it.id] = el; }}
                      className={`stage-tile ${activeId === it.id ? 'active' : ''} ${it.step === 's1' ? 'paper' : ''} ${positioningId === it.id ? 'positioning' : ''}`}
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
                  <small>최대 50 페이지</small>
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
  const a = document.createElement('a');
  a.href = target.edbFileUri;
  a.download = target.edbFileName || 'classin.edb';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function openPublishedEdb(target){
  if (!target?.canOpenEdbFile || !target.edbPath) return;
  try {
    const resp = await fetch('/api/system/open-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: target.edbPath }),
    });
    const json = await resp.json().catch(() => ({}));
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

function PublishResultPanel({ session, visible, onClassinReviewComplete }){
  const summary = useMemo(() => visible ? sessionPublishSummary(session) : null, [session, visible]);
  const history = useMemo(() => visible ? sessionPublishHistory(session) : [], [session, visible]);
  if (!summary) return null;
  return (
    <div className="publish-result-panel">
      <div className="publish-result-head">
        <div className="publish-result-title">
          <strong>제작 결과</strong>
          <span>{summary.statusLabel} · {summary.recordCountLabel || `${summary.recordCount || summary.recordCountActual}개 자료`}</span>
        </div>
        <span className={`publish-result-status ${summary.validated ? 'ok' : 'warn'}`}>
          {summary.validated ? '검증 완료' : '확인 필요'}
        </span>
      </div>
      <div className="publish-result-file" title={summary.edbPath || summary.edbFileName}>
        {summary.edbFileName}
      </div>
      <div className="publish-result-metrics">
        <span>{summary.recordCountActual || summary.recordCount} records</span>
        {summary.pageCountHint > 0 && <span>{summary.pageCountHint}p hint</span>}
        {summary.outerSize > 0 && <span>{formatBytes(summary.outerSize)}</span>}
        {summary.classinHandoffStatusLabel && <span title="ClassIn 전달 상태">{summary.classinHandoffStatusLabel}</span>}
        {summary.classinPreflightStatusLabel && <span title="ClassIn 사전점검">{summary.classinPreflightStatusLabel}</span>}
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
    </div>
  );
}

// ─── RIGHT: tabbed panel ──────────────────────────────────────────────────
function SidePanel({
  item, items, activeIndex,
  setStep, applyToAll, bulk, setBulk,
  setPlacement,
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
}){
  const [tab, setTab] = useState('item');
  const [previewMode, setPreviewMode] = useState('raw'); // raw | chalk | compare
  const [compareX, setCompareX] = useState(50);
  const [keyDraft, setKeyDraft] = useState('');
  const [openAiKeyDraft, setOpenAiKeyDraft] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [showOpenAiKey, setShowOpenAiKey] = useState(false);
  const [hangulDetailsExpanded, setHangulDetailsExpanded] = useState(false);
  const dragging = useRef(false);
  const wrapRef = useRef(null);
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

  const itemPosLabel = item ? `${String(activeIndex+1).padStart(2,'0')} / ${String(items.length).padStart(2,'0')}` : '— / —';
  const maxScale = maxPlacementScaleRatio(item);
  const placementScale = item ? normalizePlacementScaleRatio(item.placementScaleRatio, maxScale) : DEFAULT_PLACEMENT_SCALE_RATIO;
  const placementX = item ? normalizePlacementXRatio(item.placementXRatio) : DEFAULT_PLACEMENT_X_RATIO;
  const placementY = item ? normalizePlacementYRatio(item.placementYRatio) : DEFAULT_PLACEMENT_Y_RATIO;
  const hasVerticalRoom = verticalPlacementRoomPages(item, placementScale) > 0.001;
  const canZoomOut = item && placementScale > PLACEMENT_SCALE_MIN + 0.001;
  const canZoomIn = item && placementScale < maxScale - 0.001;
  const canEnhanceCurrent = !!item && !!userSettings?.hasGeminiApiKey && !imageEnhanceBusy;
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

  return (
    <div className="col right">
      <div className="tab-bar">
        <button className={tab==='item' ? 'on' : ''} onClick={() => setTab('item')}>
          선택 자료 <span className="badge">{itemPosLabel}</span>
        </button>
        <button className={tab==='board' ? 'on' : ''} onClick={() => setTab('board')}>
          칠판 설정
        </button>
      </div>

      <PublishResultPanel
        session={session}
        visible={published}
        onClassinReviewComplete={onClassinReviewComplete}
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

                  <div className="ptools">
                    <button className="icon-btn" title="회전">{Icon.rotate}</button>
                    <button className="icon-btn" title="자르기">{Icon.crop}</button>
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
                    <div className="spacer" />
                    <span className="scale">{Math.round(placementScale * 100)}%</span>
                  </div>
                </div>

                <div className="panel-section-hd">
                  위치·크기 미세 조절 <span className="line" />
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
                    <label>
                      <span>크기</span>
                      <input
                        type="range"
                        min={Math.round(PLACEMENT_SCALE_MIN * 100)}
                        max={Math.round(maxScale * 100)}
                        value={Math.round(placementScale * 100)}
                        onChange={e => updatePlacement({ scaleRatio: Number(e.target.value) / 100 })}
                      />
                      <strong>{Math.round(placementScale * 100)}%</strong>
                    </label>
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
                  처리 방식 선택 <span className="line" />
                </div>

                <div className="steps">
                  <button
                    className={`step-row ${item.step === 's1' ? 'on' : ''}`}
                    onClick={() => setStep(item.id, 's1')}
                  >
                    <span className="radio" />
                    <div>
                      <div className="t">1단계 · 그대로 붙이기</div>
                      <div className="d">스캔한 모양 그대로. 원본 색·여백 유지.</div>
                    </div>
                    <div className="meta-r">즉시<strong>~ 0.3s</strong></div>
                  </button>
                  <button
                    className={`step-row ${item.step === 's2' ? 'on' : ''}`}
                    onClick={() => setStep(item.id, 's2')}
                  >
                    <span className="radio" />
                    <div>
                      <div className="t">2단계 · AI 칠판 변환 <span className="ai-badge">AI</span></div>
                      <div className="d">배경 제거 · 분필 색상 자동 배치 · 가독성 최적화.</div>
                    </div>
                    <div className="meta-r">자동<strong>~ 4s</strong></div>
                  </button>
                  <button
                    className={`step-row ${item.step === 's3' ? 'on' : ''}`}
                    onClick={() => setStep(item.id, 's3')}
                  >
                    <span className="radio" />
                    <div>
                      <div className="t">3단계 · 고화질 재구성 <span className="ai-badge">HQ</span></div>
                      <div className="d">선택한 문제만 업스케일링 · 배경제거 · 완전 투명 PNG로 제작.</div>
                    </div>
                    <div className="meta-r">제작시<strong>~ 8s</strong></div>
                  </button>
                </div>
                <div className="panel-section-hd" style={{marginTop: 6}}>
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
              </>
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

          <div className="tab-foot">
            <label className="check">
              <input type="checkbox" checked={bulk} onChange={e => setBulk(e.target.checked)} />
              전체 {items.length}개 일괄
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
        </>
      )}

      {tab === 'board' && (
        <>
          <div className="tab-body">
            <div className="panel-section-hd">레이아웃 <span className="line" /></div>

            <div className="row-control">
              <div className="lbl">열 수<small>한 행에 나란히 둘 자료 수</small></div>
              <div className="seg-mini">
                {[1,2,3].map(n => (
                  <button key={n} className={boardColumns===n ? 'on' : ''} onClick={() => setBoardColumns(n)}>{n}</button>
                ))}
              </div>
            </div>

            <div className="row-control">
              <div className="lbl">스크롤 모드<small>밑으로 무한 스크롤 (최대 50p)</small></div>
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
                  <span>{option.label}</span>
                  <small>{option.description}</small>
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
      {visibleJobs.map(job => (
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
            <button className="bg-job-action" type="button" onClick={() => onCancel?.(job.id)}>취소</button>
          ) : (
            <button className="bg-job-action" type="button" onClick={() => onDismiss?.(job.id)}>닫기</button>
          )}
        </div>
      ))}
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
          <button className="btn" type="button" onClick={onCancel} disabled={confirming}>취소</button>
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
  next.title = item.name || next.title || '';
  next.riskFlags = Array.isArray(item.riskFlags) ? [...item.riskFlags] : [];
  next.risk_flags = next.riskFlags;
  next.reviewStatus = normalizeReviewStatus(item.reviewStatus) || deriveProblemStatus(next);
  next.review_status = next.reviewStatus;
  next.step = normalizeProcessingStep(item.step);
  next.processingStep = next.step;
  next.processing_step = next.step;
  next.placementXRatio = normalizePlacementXRatio(item.placementXRatio);
  next.placementYRatio = verticalPlacementRoomPages(item) > 0.001
    ? normalizePlacementYRatio(item.placementYRatio)
    : DEFAULT_PLACEMENT_Y_RATIO;
  next.placementScaleRatio = normalizePlacementScaleRatio(item.placementScaleRatio, maxPlacementScaleRatio(item));
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

function materializeSessionForItems(rawSession, items, fileName){
  const snapshot = cloneSession(rawSession);
  if (!snapshot || !Array.isArray(snapshot.problems)) return null;
  const byId = new Map(snapshot.problems.map(problem => [problem.id, problem]));
  const orderedProblems = items
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
    snapshot.pages = snapshot.pages.map(page => ({
      ...page,
      problemIds: (page.problemIds || page.problem_ids || []).filter(id => activeIds.has(id)),
    }));
  }
  return snapshot;
}

function mergeSessions(baseSession, incomingSession, fileName){
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
    problems: mergedProblems,
    pages: mergedPages,
    edb_path: null,
    edb_file_uri: null,
    edbPath: null,
    edbFileUri: null,
  };
  applyProblemCounts(merged, mergedProblems);
  return merged;
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
    startYPages: typeof problem.startYPages === 'number' ? problem.startYPages : null,
    snappedNextStartYPages: typeof problem.snappedNextStartYPages === 'number' ? problem.snappedNextStartYPages : null,
    overflowAmountPages: typeof problem.overflowAmountPages === 'number' ? problem.overflowAmountPages : 0,
    overflowViolation: Boolean(problem.overflowViolation),
    slotSpanCount: Number.isInteger(problem.slotSpanCount) ? problem.slotSpanCount : null,
    placementXRatio: normalizePlacementXRatio(problem.placementXRatio ?? problem.placement_x_ratio),
    placementYRatio: normalizePlacementYRatio(problem.placementYRatio ?? problem.placement_y_ratio),
    placementScaleRatio: initialScale < 0.95 ? DEFAULT_PLACEMENT_SCALE_RATIO : initialScale,
  };
}

async function fetchLatestSession(){
  const resp = await fetch('/api/session/latest');
  if (resp.status === 404) return null;
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `세션 로드 실패 (${resp.status})`);
  return json.session;
}

async function fetchSessionHistory(){
  const resp = await fetch('/api/session/history');
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `작업 이력 로드 실패 (${resp.status})`);
  return Array.isArray(json.history) ? json.history : [];
}

async function postRestoreSessionHistory(id){
  const resp = await fetch('/api/session/history/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `작업 열기 실패 (${resp.status})`);
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
    value: 'auto',
    label: '자동 판별',
    description: '사진 내용에 맞춰 문항 경계를 판단',
    exportMode: 'question',
  },
  {
    value: 'single-problem',
    label: '한 문제',
    description: '한 이미지나 페이지를 한 문항으로 처리',
    exportMode: 'question',
  },
  {
    value: 'multi-problem',
    label: '여러 문제',
    description: '한 페이지 안의 여러 문항을 분리',
    exportMode: 'question',
  },
  {
    value: 'page-as-is',
    label: '페이지 그대로',
    description: '문항 분리 없이 페이지 단위로 변환',
    exportMode: 'question',
  },
];
const INPUT_INTENT_BY_VALUE = Object.freeze(
  INPUT_INTENT_OPTIONS.reduce((acc, option) => {
    acc[option.value] = option;
    return acc;
  }, {})
);

function normalizeInputIntent(value){
  const normalized = String(value || DEFAULT_INPUT_INTENT).trim().toLowerCase().replace(/_/g, '-');
  return INPUT_INTENT_BY_VALUE[normalized] ? normalized : DEFAULT_INPUT_INTENT;
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
  passage_cross_page_merge_check: '긴 지문 병합 확인',
  problem_per_block: '블록 단위 분리',
  sparse_segmentation: '성긴 분할',
  source_problem_bbox_overlap: '원본 영역 겹침',
};

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

function problemMatchesReviewFilter(problem, filter){
  const helper = globalThis.EDB_REVIEW_FILTERS?.problemMatchesReviewFilter;
  if (typeof helper === 'function') return helper(problem, filter);
  const normalizedFilter = String(filter || 'all').trim() || 'all';
  if (normalizedFilter === 'all') return true;
  if (normalizedFilter === 'supplemental') return isSupplementalProblem(problem);
  if (normalizedFilter === 'passage') return isPassageProblem(problem);
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

function countActionableReviewMatches(session, actionableRiskFlagCounts, failedCount = 0){
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
      sourcePageIds: new Set(),
      range: problem.passageRange || problem.passage_range || null,
      continuesAcrossPages: false,
      continuationBlockIds: new Set(),
    };
    group.problemCount += 1;
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
  const items = Array.from(groups.values()).map(group => ({
    id: group.id,
    problemCount: group.problemCount,
    sourcePageIds: Array.from(group.sourcePageIds),
    sourcePageCount: group.sourcePageIds.size,
    range: group.range,
    continuesAcrossPages: group.continuesAcrossPages || group.sourcePageIds.size > 1,
    continuationBlockCount: group.continuationBlockIds.size,
  }));
  return {
    passageGroups: items,
    passageGroupCount: items.length,
    passageProblemCount: items.reduce((total, group) => total + group.problemCount, 0),
    crossPagePassageGroupCount: items.filter(group => group.continuesAcrossPages).length,
    passageContinuationBlockCount: items.reduce((total, group) => total + group.continuationBlockCount, 0),
  };
}

function sessionReviewSummary(session){
  const raw = session?.review_summary || session?.reviewSummary || {};
  const counts = sessionProblemCounts(session);
  const passageSummary = collectPassageGroupSummary(session);
  const fallbackStatusCounts = collectReviewStatusCounts(session);
  const rawStatusCounts = raw.reviewStatusCounts && typeof raw.reviewStatusCounts === 'object'
    ? raw.reviewStatusCounts
    : {};
  const reviewStatusCounts = {
    all: Number(rawStatusCounts.all ?? fallbackStatusCounts.all) || 0,
    normal: Number(rawStatusCounts.normal ?? fallbackStatusCounts.normal) || 0,
    check_needed: Number(rawStatusCounts.check_needed ?? fallbackStatusCounts.check_needed) || 0,
    failed: Number(rawStatusCounts.failed ?? fallbackStatusCounts.failed) || 0,
  };
  const rawSupplementalStatusCounts = raw.supplementalReviewStatusCounts && typeof raw.supplementalReviewStatusCounts === 'object'
    ? raw.supplementalReviewStatusCounts
    : {};
  const rawCoreStatusCounts = raw.coreReviewStatusCounts && typeof raw.coreReviewStatusCounts === 'object'
    ? raw.coreReviewStatusCounts
    : {};
  const fallbackRiskFlagCounts = collectRiskFlagCounts(session);
  const riskFlagCounts = raw.riskFlagCounts && typeof raw.riskFlagCounts === 'object'
    ? raw.riskFlagCounts
    : fallbackRiskFlagCounts;
  const actionableRiskFlagCounts = raw.actionableRiskFlagCounts && typeof raw.actionableRiskFlagCounts === 'object'
    ? raw.actionableRiskFlagCounts
    : filterActionableRiskFlagCounts(riskFlagCounts, { hwpCountsMatch: hasHwpCountMatch(raw) });
  const rawActionableNeedsReviewCount = Number(raw.actionableNeedsReviewCount ?? raw.actionable_needs_review_count);
  const actionableNeedsReviewCount = Number.isFinite(rawActionableNeedsReviewCount)
    ? Math.max(0, rawActionableNeedsReviewCount)
    : countActionableReviewMatches(session, actionableRiskFlagCounts, reviewStatusCounts.failed);
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
  return {
    counts,
    reviewStatusCounts,
    coreReviewStatusCounts: rawCoreStatusCounts,
    supplementalReviewStatusCounts: rawSupplementalStatusCounts,
    needsReviewCount: Number.isFinite(Number(raw.needsReviewCount))
      ? Math.max(0, Number(raw.needsReviewCount))
      : Math.max(0, reviewStatusCounts.check_needed + reviewStatusCounts.failed),
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
    hwpProblemCountMismatchCount: Number.isFinite(hwpProblemCountMismatchCount) ? Math.max(0, hwpProblemCountMismatchCount) : 0,
    hwpOversegmentationCount: Number.isFinite(hwpOversegmentationCount) ? Math.max(0, hwpOversegmentationCount) : 0,
    duplicateProblemNumberGroups,
    blockingDuplicateProblemNumberGroups,
    duplicateProblemNumberLabel,
    sourceProblemOverlapGroups,
    sourceProblemOverlapLabel,
    passageGroups: passageSummary.passageGroups,
    passageGroupCount: passageSummary.passageGroupCount,
    passageProblemCount: passageSummary.passageProblemCount,
    crossPagePassageGroupCount: passageSummary.crossPagePassageGroupCount,
    passageContinuationBlockCount: passageSummary.passageContinuationBlockCount,
  };
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
  const edbFileExists = raw.edbFileExists ?? raw.edb_file_exists;
  const outputDirExists = raw.outputDirExists ?? raw.output_dir_exists;
  if (!edbFileName && !edbPath && !edbFileUri) return null;
  const normalizedCore = Number.isFinite(coreProblemCount) ? Math.max(0, coreProblemCount) : 0;
  const normalizedSupplemental = Number.isFinite(supplementalItemCount) ? Math.max(0, supplementalItemCount) : 0;
  const fallbackRecordCount = Number.isFinite(recordCount) ? Math.max(0, recordCount) : 0;
  const explicitRecordCountLabel = String(raw.recordCountLabel || raw.record_count_label || '').trim();
  const summary = {
    validated: raw.validated !== false,
    statusLabel: String(raw.statusLabel || raw.status_label || '제작 완료'),
    edbFileName: edbFileName || (edbPath ? edbPath.split('/').pop() : 'classin.edb'),
    edbPath,
    edbFileUri,
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
  summary.canDownload = Boolean(summary.edbFileUri) && summary.edbFileExists !== false;
  summary.canOpenEdbFile = Boolean(summary.edbPath) && summary.edbFileExists !== false;
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

function mergeRetryCandidateIntoCurrent(currentSession, candidateSession, pageIds){
  const base = cloneSession(currentSession);
  const candidate = cloneSession(candidateSession);
  if (!base || !candidate || !Array.isArray(candidate.problems)) return candidate;

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

async function postExport(files, aiFallback, inputIntent = DEFAULT_INPUT_INTENT, options = {}){
  const resolvedInputIntent = normalizeInputIntent(inputIntent);
  const inputIntentConfig = INPUT_INTENT_BY_VALUE[resolvedInputIntent] || INPUT_INTENT_BY_VALUE[DEFAULT_INPUT_INTENT];
  const filesPayload = await Promise.all(files.map(async (f) => ({
    fileName: f.name,
    fileDataBase64: await fileToBase64(f),
  })));
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
      exportEdb: Object.prototype.hasOwnProperty.call(options, 'exportEdb') ? !!options.exportEdb : !options.preview,
      detectPerspective: files.some(f => !isDocumentLikeFile(f)),
      maxDimension: 2400,
      aiFallback: aiFallback || AI_FALLBACK_OFF,
    }),
  });
  const json = await resp.json();
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

async function fetchUserSettings(){
  const resp = await fetch('/api/user-settings');
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `설정 로드 실패 (${resp.status})`);
  return json.settings;
}

async function fetchRuntimeDiagnostics(){
  const resp = await fetch('/api/runtime-diagnostics');
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `진단 로드 실패 (${resp.status})`);
  return json;
}

async function clearSession(){
  const resp = await fetch('/api/session/latest', { method: 'DELETE' });
  if (resp.status === 404) return; // already cleared
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `세션 초기화 실패 (${resp.status})`);
}

async function postMutate(action, args){
  const resp = await fetch('/api/session/mutate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...args }),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `검수 수정 실패 (${resp.status})`);
  return json.session;
}

async function postRetryAi(args, options = {}){
  const resp = await fetch('/api/session/retry-ai', {
    method: 'POST',
    signal: options.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...(args || {}), preview: !!options.preview }),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `AI 재인식 실패 (${resp.status})`);
  return json;
}

async function postEnhanceImage(args, options = {}){
  const resp = await fetch('/api/session/enhance-image', {
    method: 'POST',
    signal: options.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args || {}),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `AI 업스케일 실패 (${resp.status})`);
  return json;
}

async function postRestore(snapshot){
  const resp = await fetch('/api/session/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: snapshot }),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `이전 상태 복원 실패 (${resp.status})`);
  return json.session;
}

async function postClassinReviewResult(payload){
  const resp = await fetch('/api/session/classin-review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `ClassIn 검수 저장 실패 (${resp.status})`);
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
    const json = await resp.json().catch(() => ({}));
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
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `설정 저장 실패 (${resp.status})`);
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
  const [aiEnabled, setAiEnabled] = useState(true);
  const [inputIntent, setInputIntent] = useState(DEFAULT_INPUT_INTENT);
  const [refreshing, setRefreshing] = useState(false);
  const [recentSessions, setRecentSessions] = useState([]);
  const [restoringSessionId, setRestoringSessionId] = useState(null);
  const [pendingFiles, setPendingFiles] = useState([]);
  const initialViewRef = useRef(requestedInitialView());
  const initialViewConsumedRef = useRef(false);
  const [view, setView] = useState(initialViewRef.current);
  const [mutating, setMutating] = useState(false);
  // Undo history: each entry is a prior session snapshot. Pushed before
  // any successful mutation; popped by Ctrl/Cmd+Z (wired in Step 7).
  const [historyStack, setHistoryStack] = useState([]);
  const fileInputRef = useRef(null);
  const jobControllersRef = useRef(new Map());

  const reviewAvailable = Array.isArray(session?.pages) && session.pages.length > 0;
  // auto-revert to board view if the session is cleared or never had pages
  useEffect(() => {
    if (view === 'review' && !reviewAvailable) setView('board');
  }, [view, reviewAvailable]);
  const canUndo = historyStack.length > 0 && !mutating;

  const activeIndex = items.findIndex(i => i.id === activeId);
  const active = activeIndex >= 0 ? items[activeIndex] : null;
  const processed = items.filter(i => i.step !== 'raw').length;
  const progress = items.length ? processed / items.length : 0;
  const hasRunningQueueRecognition = backgroundJobs.some(job => job.status === 'running' && job.scope === 'queue-recognition');
  const hasRunningSessionRecognition = backgroundJobs.some(job => job.status === 'running' && job.scope === 'session-recognition');
  const hasRunningImageEnhance = backgroundJobs.some(job => job.status === 'running' && job.scope === 'image-enhance');

  const showToast = msg => {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  };

  const refreshSessionHistory = useCallback(async () => {
    try {
      const history = await fetchSessionHistory();
      setRecentSessions(history);
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
    const controller = jobControllersRef.current.get(id);
    if (controller) controller.abort();
    settleBackgroundJob(id, {
      status: 'canceled',
      label: 'AI 인식 취소됨',
      hint: '결과를 적용하지 않았습니다.',
    }, 2200);
    showToast('AI 인식을 취소했어요');
  }, [settleBackgroundJob]);

  useEffect(() => {
    return () => {
      jobControllersRef.current.forEach(controller => controller.abort());
      jobControllersRef.current.clear();
    };
  }, []);

  const showMockItems = (message = '더미 자료를 표시했어요') => {
    const mockItems = freshInitialItems();
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
    const mapped = rawSession.problems.map((p, idx) => mapProblemToItem(p, idx));
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
  }, []);

  // Replace state from a mutation response: similar to applySession but
  // tries to preserve the user's current item ordering when the mutation
  // only affects a subset of problem ids (e.g. exclude leaves order intact).
  const adoptMutatedSession = useCallback((nextSession, prevSession) => {
    if (!nextSession || !Array.isArray(nextSession.problems)) return;
    const nextProblemsById = new Map();
    nextSession.problems.forEach(p => { if (p && p.id) nextProblemsById.set(p.id, p); });
    // Start from the previous items order, drop missing ids, append any new
    // ids in their server-side order (this keeps split children adjacent to
    // where the parent was, and lets reordered exclusion preserve the rest).
    const prevItemIds = items.map(it => it.id);
    const seen = new Set();
    const orderedIds = [];
    for (const id of prevItemIds) {
      if (nextProblemsById.has(id) && !seen.has(id)) {
        orderedIds.push(id);
        seen.add(id);
      }
    }
    for (const prob of nextSession.problems) {
      if (prob && prob.id && !seen.has(prob.id)) {
        orderedIds.push(prob.id);
        seen.add(prob.id);
      }
    }
    const orderedProblems = orderedIds.map(id => nextProblemsById.get(id)).filter(Boolean);
    const mapped = orderedProblems.map((p, idx) => mapProblemToItem(p, idx));
    setItems(mapped);
    setSession(nextSession);
    setPublished(false);
    if (!nextProblemsById.has(activeId)) {
      setActiveId(mapped[0]?.id || null);
    }
  }, [items, activeId]);

  // Run a server-side mutation (split / merge / exclude). Captures the
  // current session into the undo history *before* the request goes out
  // so that a failed mutation does not clutter the stack.
  const mutateSession = useCallback(async (action, args) => {
    if (!session) {
      showToast('변경할 세션이 없습니다');
      return;
    }
    setMutating(true);
    setLoading({
      label: action === 'split' ? '문제를 가르는 중…'
        : action === 'merge' ? '문제를 합치는 중…'
        : action === 'exclude' ? '문제를 제외하는 중…'
        : '변경 중…',
      startedAt: Date.now(),
    });
    const snapshotBefore = session;
    try {
      const next = await postMutate(action, args);
      setHistoryStack(prev => [...prev, snapshotBefore]);
      adoptMutatedSession(next, snapshotBefore);
      refreshSessionHistory();
      showToast(
        action === 'split' ? '문제를 두 개로 갈랐어요'
        : action === 'merge' ? '문제를 합쳤어요'
        : '문제를 제외했어요'
      );
    } catch (e) {
      showToast(`수정 실패: ${e.message}`);
    } finally {
      setMutating(false);
      setLoading(null);
    }
  }, [session, adoptMutatedSession, refreshSessionHistory]);

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
    const snapshotBefore = materializeSessionForItems(session, items, fileName) || cloneSession(session);
    const job = startBackgroundJob({
      scope: 'session-recognition',
      label: pageIds.length === 1 ? 'AI 문제 인식 중' : `${pageIds.length || '전체'}개 페이지 AI 인식 중`,
      hint: '보드 작업은 계속할 수 있습니다. 완료되면 확인 팝업이 열립니다.',
    });
    try {
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
        title: applied ? `${applied}개 페이지를 다시 인식했어요` : 'AI 인식 결과를 확인해 주세요',
        subtitle: '문제 경계가 맞으면 바로 칠판에 분할해서 붙입니다.',
        session: next,
        pageIds,
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
      showToast(`AI 재인식 실패: ${e.message}`);
    }
  }, [session, userSettings, items, fileName, startBackgroundJob, settleBackgroundJob]);

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

  const undoMutation = useCallback(async () => {
    if (historyStack.length === 0) return;
    const snapshot = historyStack[historyStack.length - 1];
    setMutating(true);
    setLoading({ label: '되돌리는 중…', startedAt: Date.now() });
    try {
      const restored = await postRestore(snapshot);
      setHistoryStack(prev => prev.slice(0, -1));
      adoptMutatedSession(restored, session);
      refreshSessionHistory();
      showToast('이전 상태로 되돌렸어요');
    } catch (e) {
      showToast(`되돌리기 실패: ${e.message}`);
    } finally {
      setMutating(false);
      setLoading(null);
    }
  }, [historyStack, session, adoptMutatedSession, refreshSessionHistory]);

  // Ctrl/Cmd+Z → undo. Skipped when focus is inside a text input so the
  // browser's native undo still works for editable fields (file-name crumb).
  useEffect(() => {
    const onKey = (evt) => {
      if (!(evt.key === 'z' || evt.key === 'Z')) return;
      if (!(evt.ctrlKey || evt.metaKey)) return;
      if (evt.shiftKey) return;  // reserve Ctrl/Cmd+Shift+Z for future redo
      const target = evt.target;
      const tag = (target?.tagName || '').toUpperCase();
      const isEditable = tag === 'INPUT' || tag === 'TEXTAREA' || (target && target.isContentEditable);
      if (isEditable) return;
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
      showToast('저장 실패: ' + e.message);
    }
  }, []);

  const onSaveOpenAiKey = useCallback(async (key) => {
    try {
      const s = await saveUserSettings({ openAiApiKey: key || '' });
      setUserSettings(s);
      showToast(key ? 'OpenAI 키 저장됨' : 'OpenAI 키 삭제됨');
    } catch (e) {
      showToast('저장 실패: ' + e.message);
    }
  }, []);

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
    const snapshotBefore = materializeSessionForItems(session, items, fileName) || cloneSession(session);
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
      showToast(`AI 업스케일 실패: ${e.message}`);
    }
  }, [session, userSettings, items, fileName, startBackgroundJob, settleBackgroundJob, adoptMutatedSession]);

  const resetSession = useCallback(async () => {
    if (loading) {
      showToast('작업 중에는 초기화할 수 없습니다');
      return;
    }
    if (!session && items.length === 0 && pendingFiles.length === 0) {
      showToast('이미 빈 세션입니다');
      return;
    }
    if (!window.confirm('세션을 초기화할까요? 업로드 대기열과 보드 자료가 모두 사라집니다.')) return;
    try {
      if (session) {
        await clearSession();
      }
      setPendingFiles([]);
      hideMockItems('초기화 완료 · 빈 세션');
    } catch (e) {
      showToast('초기화 실패: ' + e.message);
    }
  }, [loading, session, items.length, pendingFiles.length]);

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
      showToast('새로고침 실패: ' + e.message);
    } finally {
      setRefreshing(false);
    }
  }, [applySession, usingMock, refreshSessionHistory]);

  const restoreRecentSession = useCallback(async (id) => {
    if (!id || restoringSessionId) return;
    setRestoringSessionId(id);
    setLoading({ label: '최근 작업을 여는 중…', startedAt: Date.now() });
    try {
      const result = await postRestoreSessionHistory(id);
      if (Array.isArray(result.history)) setRecentSessions(result.history);
      applySession(result.session);
      setHistoryStack([]);
      showToast('최근 작업을 열었어요');
    } catch (e) {
      showToast('작업 열기 실패: ' + e.message);
    } finally {
      setRestoringSessionId(null);
      setLoading(null);
    }
  }, [applySession, restoringSessionId]);

  const triggerUpload = () => fileInputRef.current?.click();

  const handleFiles = (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setPendingFiles(prev => {
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
    showToast(`${files.length}개 파일을 대기열에 추가했어요`);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removePendingFile = useCallback((key) => {
    setPendingFiles(prev => prev.filter(file => fileQueueKey(file) !== key));
  }, []);

  const clearPendingFiles = useCallback(() => {
    setPendingFiles([]);
    showToast('업로드 대기열을 비웠어요');
  }, []);

  const processQueuedFiles = useCallback(async (mode, targetKey = null) => {
    const files = targetKey
      ? pendingFiles.filter(file => fileQueueKey(file) === targetKey)
      : [...pendingFiles];
    if (!files.length) {
      showToast(targetKey ? '해당 파일이 대기열에 없습니다' : '대기열에 파일이 없습니다');
      return;
    }
    const isRecognition = mode === 'recognize';
    const resolvedInputIntent = isRecognition ? 'multi-problem' : 'single-problem';
    const aiFallback = isRecognition && aiEnabled && userSettings?.hasGeminiApiKey
      ? AI_FALLBACK_ON
      : AI_FALLBACK_OFF;
    if (isRecognition) {
      const fileKeys = files.map(fileQueueKey);
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
        });
        if (job.controller.signal.aborted) return;
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
        showToast(`문제 인식 실패: ${e.message}`);
      } finally {
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
      return;
    }
    setLoading({
      label: isRecognition
        ? `${files.length}개 파일에서 문제 인식 중...`
        : files.length === 1 ? '1개 파일을 그대로 등록 중...' : `${files.length}개 파일을 순서대로 등록 중...`,
      hint: isRecognition
        ? (aiFallback.enabled
            ? 'Gemini AI 보정으로 문항 경계를 다시 확인합니다.'
            : 'Gemini 키가 없어 기본 문항 인식만 실행합니다.')
        : '각 이미지와 PDF 페이지를 하나의 자료로 등록합니다.',
      startedAt: Date.now(),
    });
    try {
      const s = await postExport(files, aiFallback, resolvedInputIntent);
      let sessionToApply = s;
      if (session && !usingMock) {
        const currentSnapshot = materializeSessionForItems(session, items, fileName);
        const merged = mergeSessions(currentSnapshot, s, fileName);
        sessionToApply = await postRestore(merged);
      }
      applySession(sessionToApply);
      refreshSessionHistory();
      const appliedKeys = new Set(files.map(fileQueueKey));
      setPendingFiles(prev => prev.filter(file => !appliedKeys.has(fileQueueKey(file))));
      const intentLabel = isRecognition ? '문제 인식' : '순서 등록';
      showToast(`${intentLabel} 완료 · ${formatProblemCount(sessionProblemCounts(sessionToApply))}`);
      const folder = sessionToApply?.output_dir || sessionToApply?.outputDir || s?.output_dir || s?.outputDir;
      if (folder) openOutputFolder(folder);
    } catch (e) {
      showToast(`${isRecognition ? '문제 인식' : '등록'} 실패: ${e.message}`);
    } finally {
      setLoading(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [pendingFiles, aiEnabled, userSettings, session, usingMock, items, fileName, applySession, startBackgroundJob, settleBackgroundJob, refreshSessionHistory]);

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
        const incomingSession = review.incomingSession || review.session;
        const currentSnapshot = session && !usingMock
          ? materializeSessionForItems(session, items, fileName)
          : null;
        const candidate = currentSnapshot
          ? mergeSessions(currentSnapshot, incomingSession, fileName)
          : cloneSession(incomingSession);
        const restored = await postRestore(candidate);
        applySession(restored);
        refreshSessionHistory();
        setView('review');
        const appliedKeys = new Set(review.fileKeys || []);
        setPendingFiles(prev => prev.filter(file => !appliedKeys.has(fileQueueKey(file))));
        const summary = summarizeRecognitionSession(incomingSession);
        showToast(`검수로 이동 · ${summary.problemLabel}을 확인하세요`);
        if (review.outputFolder) openOutputFolder(review.outputFolder);
      } else if (review.kind === 'retry-ai') {
        const currentSnapshot = session
          ? (materializeSessionForItems(session, items, fileName) || cloneSession(session))
          : cloneSession(review.snapshotBefore);
        const candidate = mergeRetryCandidateIntoCurrent(currentSnapshot, review.session, review.pageIds);
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
      showToast(`적용 실패: ${e.message}`);
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
    applySession,
    adoptMutatedSession,
    refreshSessionHistory,
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
    setItems(it => it.map(x => {
      if (x.id !== id) return x;
      const next = { ...x };
      if (Object.prototype.hasOwnProperty.call(patch || {}, 'xRatio')) {
        next.placementXRatio = normalizePlacementXRatio(patch.xRatio);
      }
      if (Object.prototype.hasOwnProperty.call(patch || {}, 'scaleRatio')) {
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
    }));
    setPublished(false);
  };
  const reorder = (fromId, toId, dropPosition = 'before') => {
    setItems(it => {
      return reorderItemsForDrop(it, fromId, toId, dropPosition);
    });
    setPublished(false);
  };
  const removeItem = (id) => {
    const nextItems = items.filter(x => x.id !== id);
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
      ? (materializeSessionForItems(confirmedSession, nextItemsForSnapshot, fileName) || confirmedSession)
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
      showToast('ClassIn 검수 저장 실패: ' + e.message);
    }
  }, [session, refreshSessionHistory]);

  const onPublish = async () => {
    if (!session || !Array.isArray(session.problems)) {
      showToast('내보낼 자료가 없습니다. 먼저 파싱해 주세요.');
      return;
    }
    const sessionIds = new Set(session.problems.map(p => p.id));
    const currentIds = items.map(i => i.id);
    const order = currentIds.filter(id => sessionIds.has(id));
    const excluded = [...sessionIds].filter(id => !currentIds.includes(id));
    const sessionForPublish = materializeSessionForItems(session, items, fileName) || session;
    const publishReviewSummary = sessionReviewSummary(sessionForPublish);
    const duplicateProblemNumberGroups = Array.isArray(publishReviewSummary.blockingDuplicateProblemNumberGroups)
      ? publishReviewSummary.blockingDuplicateProblemNumberGroups
      : [];
    if (duplicateProblemNumberGroups.length > 0) {
      setView('review');
      const duplicateLabel = publishReviewSummary.duplicateProblemNumberLabel || `${duplicateProblemNumberGroups.length}그룹`;
      showToast(`중복 문항 번호가 있어 제작을 멈췄어요. ${duplicateLabel}`);
      return;
    }
    const sourceOverlapIssues = findSourceProblemOverlaps(sessionForPublish.problems || [])
      .filter(issue => issue.type === 'source_problem_bbox_overlap');
    if (sourceOverlapIssues.length > 0) {
      const firstIssue = sourceOverlapIssues[0];
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
    const actionableNeedsReviewCount = Math.max(0, Number(publishReviewSummary.actionableNeedsReviewCount) || 0);
    if (actionableNeedsReviewCount > 0) {
      const confirmedPublish = window.confirm(
        `검수 화면에 확인 필요 ${actionableNeedsReviewCount}개가 남아 있습니다.\n그래도 EDB를 제작할까요?`
      );
      if (!confirmedPublish) {
        setView('review');
        showToast('제작을 멈췄어요. 검수 화면에서 확인 필요 항목을 먼저 확인하세요.');
        return;
      }
    }
    const placements = Object.fromEntries(
      items
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
          session: sessionForPublish,
        }),
      });
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.error || `publish 실패 (${resp.status})`);
      setSession(json.session);
      refreshSessionHistory();
      const publishSummary = json.publishSummary || json.publish_summary || json.session?.publishSummary || json.session?.publish_summary;
      const url = publishSummary?.edbFileUri || publishSummary?.edb_file_uri || json.session?.edb_file_uri;
      if (url) {
        const a = document.createElement('a');
        a.href = url;
        a.download = publishSummary?.edbFileName || publishSummary?.edb_file_name || (json.session.session_name || 'classin') + '.edb';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      setPublished(true);
      const publishLabel = publishSummary?.recordCountLabel || publishSummary?.record_count_label || `${publishSummary?.recordCount || publishSummary?.record_count || order.length}개 자료`;
      showToast(`${publishLabel}로 EDB 제작 완료 · 다운로드 시작`);
    } catch (e) {
      showToast('제작 실패: ' + e.message);
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
        canReset={(!!session || items.length > 0 || pendingFiles.length > 0) && !loading}
        view={view}
        setView={setView}
        reviewAvailable={reviewAvailable}
        onUndo={undoMutation}
        canUndo={canUndo}
      />
      <div className="main">
        <ItemsRail
          items={items}
          activeId={activeId}
          setActive={setActiveId}
          reorder={reorder}
          removeItem={removeItem}
          addSample={addSample}
          bulkApply={applyToAll}
          handleFiles={handleFiles}
          pendingFiles={pendingFiles}
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
        />
        {view === 'review' ? (
          <ReviewStage
            session={session}
            items={items}
            activeId={activeId}
            setActive={setActiveId}
            mutateSession={mutateSession}
            retryAiSession={retryAiSession}
            mutating={mutating}
            aiAvailable={!!userSettings?.hasGeminiApiKey}
            aiBusy={hasRunningSessionRecognition}
            onConfirm={onConfirm}
          />
        ) : (
          <BoardStage
            items={items}
            activeId={activeId}
            setActive={setActiveId}
            boardColor={t.boardColor}
            boardColumns={t.boardColumns}
            fileName={fileName}
            addSample={addSample}
            setPlacement={setPlacement}
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
          boardColumns={t.boardColumns}
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
          canRecognizeSession={!!session && !!userSettings?.hasGeminiApiKey && !mutating && !hasRunningSessionRecognition}
          session={session}
          published={published}
          onClassinReviewComplete={markClassinReviewComplete}
        />
      </div>

      <BackgroundJobsPanel
        jobs={backgroundJobs}
        onCancel={cancelBackgroundJob}
        onDismiss={dismissBackgroundJob}
      />

      <RecognitionReviewModal
        review={recognitionReview}
        confirming={confirmingRecognition}
        onConfirm={confirmRecognitionReview}
        onCancel={cancelRecognitionReview}
      />

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
        <TweakRadio label="열 수" value={String(t.boardColumns)} options={['1','2','3']} onChange={v => setTweak('boardColumns', parseInt(v))} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
