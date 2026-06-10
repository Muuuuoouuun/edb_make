// 칠판 자료 편집기 — main app
const { useState, useRef, useEffect, useMemo, useCallback } = React;

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
function ReviewStage({ session, items, activeId, setActive, mutateSession, retryAiSession, mutating, aiAvailable, aiBusy }){
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
  const splitDraggingRef = useRef(false);
  const splitBoxRef = useRef(null);

  // Cancel split mode if the session changes underneath (e.g. after a mutation).
  useEffect(() => {
    setSplitTarget(null);
    setSelectedIds(new Set());
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
    const counts = { all: 0, normal: 0, check_needed: 0, failed: 0 };
    (session?.problems || []).forEach(problem => {
      const status = deriveProblemStatus(problem);
      counts.all += 1;
      counts[status] = (counts[status] || 0) + 1;
    });
    return counts;
  }, [session]);
  const pageRetryIds = useMemo(() => {
    const ids = [];
    const byId = problemsById;
    pages.forEach(page => {
      const pageFlags = page.riskFlags || page.risk_flags || [];
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
    // sequential calls — exclude is a single-id operation
    for (const id of selectedList) {
      await mutateSession?.('exclude', { problemId: id });
    }
  };
  const doRetryAi = async (pageIds) => {
    if (!pageIds?.length) return;
    await retryAiSession?.({ pageIds });
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

  const riskyCount = statusCounts.check_needed + statusCounts.failed;
  const filterOptions = [
    ['all', '전체', statusCounts.all],
    ['normal', '정상', statusCounts.normal],
    ['check_needed', '확인 필요', statusCounts.check_needed],
    ['failed', '실패', statusCounts.failed],
  ];
  const retryDisabledReason = !aiAvailable
    ? 'Gemini API 키를 먼저 저장해 주세요'
    : aiBusy
      ? 'AI 인식 중입니다'
      : mutating
        ? '처리 중입니다'
      : '';

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
      <span className="hint">문제 박스를 확인하고, 이상한 페이지만 AI로 다시 인식하세요.</span>
      <div className="spacer" />
      {riskyCount > 0 && (
        <button
          className="btn primary"
          type="button"
          title={retryDisabledReason || `${pageRetryIds.length}개 페이지 재인식`}
          onClick={() => doRetryAi(pageRetryIds)}
          disabled={!aiAvailable || aiBusy || mutating || !pageRetryIds.length}
        >
          AI 재인식 {riskyCount}
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
        className="btn primary"
        type="button"
        title={retryDisabledReason || `${selectedRetryPageIds.length}개 페이지 재인식`}
        onClick={() => doRetryAi(selectedRetryPageIds)}
        disabled={!aiAvailable || aiBusy || mutating || !selectedHasRetryable || !selectedRetryPageIds.length}
      >
        AI 재인식
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
        {Icon.trash} 제외
      </button>
      <button className="btn" onClick={() => setSelectedIds(new Set())} disabled={mutating}>선택 해제</button>
    </div>
  );

  return (
    <div className="col center">
      <div className="stage">
        <div className="stage-toolbar">
          <span className="name">검수 — 검출된 문제 박스</span>
          <span className="pill"><span className="dotc" /> {pages.length} 페이지 · {session?.problems?.length || 0} 문제</span>
          <div className="spacer" />
          <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>
            빨간 박스는 인식이 의심됩니다. 클릭 후 가르기·합치기·제외하세요.
          </span>
        </div>
        <div className="review-wrap">
          {actionBar}
          {pages.map(page => {
            const allPageProblems = (page.problemIds || [])
              .map(pid => problemsById.get(pid))
              .filter(Boolean);
            const pageProblems = allPageProblems
              .filter(problem => reviewFilter === 'all' || deriveProblemStatus(problem) === reviewFilter);
            const pageRiskFlags = page.riskFlags || [];
            const pageStatus = normalizeReviewStatus(page.reviewStatus || page.review_status)
              || (!(page.problemIds || []).length ? 'failed' : pageRiskFlags.length ? 'check_needed' : 'normal');
            const hasRisk = pageRiskFlags.length > 0 || pageStatus !== 'normal';
            const pageCanRetry = pageRetryIds.includes(page.id);
            return (
              <div key={page.id} className={`review-page ${reviewStatusClass(pageStatus)}`}>
                <div className="review-page-hd">
                  <span className="pg-num">{page.id}</span>
                  <span className="pg-count">
                    {reviewFilter === 'all'
                      ? `${allPageProblems.length}개 검출`
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
                    const order = orderMap.get(prob.id);
                    const tooltipParts = [prob.title || ''];
                    if (isRisky) tooltipParts.push(`${statusMeta.label}: ${(prob.riskFlags || []).join(', ') || '경계 확인 필요'}`);
                    const classes = [
                      'review-bbox',
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
  addMockSample, canAddDummy,
}){
  const dragId = useRef(null);
  const [overId, setOverId] = useState(null);
  const [dropZoneActive, setDropZoneActive] = useState(false);
  const railRef = useRef(null);
  const itemRefs = useRef({});

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
          <strong style={{marginTop:6}}>이미지·PDF 대기열에 추가</strong>
          <small>파일별로 그대로 등록하거나 AI 인식합니다</small>
        </div>

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
                    <div className="meta">{/\.pdf$/i.test(file.name || '') ? 'PDF' : 'IMG'} · {formatBytes(file.size)}</div>
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
              <div className="btn full" style={{cursor:'default', justifyContent:'center', color:'var(--muted)'}}>
                각 파일 행에서 처리 방식을 선택하세요
              </div>
              {!aiAvailable && (
                <div className="btn full" style={{cursor:'default', justifyContent:'center', color:'var(--muted)'}}>
                  Gemini 키 없음 · 기본 인식으로 실행
                </div>
              )}
            </div>
          </div>
        )}

        {items.map((it, i) => (
          <div
            key={it.id}
            ref={el => { itemRefs.current[it.id] = el; }}
            className={`item ${activeId === it.id ? 'active' : ''} ${dragId.current === it.id ? 'dragging' : ''} ${overId === it.id ? 'drop-target' : ''}`}
            draggable
            onClick={() => setActive(it.id)}
            onDragStart={e => { dragId.current = it.id; e.dataTransfer.effectAllowed = 'move'; }}
            onDragOver={e => { e.preventDefault(); setOverId(it.id); }}
            onDragLeave={() => setOverId(null)}
            onDrop={e => {
              e.preventDefault();
              if (dragId.current && dragId.current !== it.id) reorder(dragId.current, it.id);
              dragId.current = null; setOverId(null);
            }}
            onDragEnd={() => { dragId.current = null; setOverId(null); }}
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
        ))}
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

// ─── RIGHT: tabbed panel ──────────────────────────────────────────────────
function SidePanel({
  item, items, activeIndex,
  setStep, applyToAll, bulk, setBulk,
  setPlacement,
  boardColumns, setBoardColumns,
  boardColor, setBoardColor,
  accent, setAccent,
  onConfirm,
  userSettings, onSaveGeminiKey,
  aiEnabled, setAiEnabled,
  inputIntent, setInputIntent,
  onRecognizeSession, canRecognizeSession,
}){
  const [tab, setTab] = useState('item');
  const [previewMode, setPreviewMode] = useState('raw'); // raw | chalk | compare
  const [compareX, setCompareX] = useState(50);
  const [keyDraft, setKeyDraft] = useState('');
  const [showKey, setShowKey] = useState(false);
  const dragging = useRef(false);
  const wrapRef = useRef(null);

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

  const itemPosLabel = item ? `${String(activeIndex+1).padStart(2,'0')} / ${String(items.length).padStart(2,'0')}` : '— / —';
  const maxScale = maxPlacementScaleRatio(item);
  const placementScale = item ? normalizePlacementScaleRatio(item.placementScaleRatio, maxScale) : DEFAULT_PLACEMENT_SCALE_RATIO;
  const placementX = item ? normalizePlacementXRatio(item.placementXRatio) : DEFAULT_PLACEMENT_X_RATIO;
  const placementY = item ? normalizePlacementYRatio(item.placementYRatio) : DEFAULT_PLACEMENT_Y_RATIO;
  const hasVerticalRoom = verticalPlacementRoomPages(item, placementScale) > 0.001;
  const canZoomOut = item && placementScale > PLACEMENT_SCALE_MIN + 0.001;
  const canZoomIn = item && placementScale < maxScale - 0.001;
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
  const subtitle = review.subtitle || `${summary.problems}개 문제로 분할했습니다. 맞으면 바로 칠판에 붙입니다.`;

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
          <span>{summary.problems} 문제</span>
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
                  <span>{pageProblems.length}개 문제</span>
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
            {confirming ? '붙이는 중...' : '맞아요, 칠판에 붙이기'}
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
  snapshot.detected_problem_count = orderedProblems.length;
  snapshot.detectedProblemCount = orderedProblems.length;
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
  return {
    ...base,
    session_name: fileName || base.session_name || incoming.session_name || '새 세션',
    data_source: 'question_export',
    source_mode: 'batch',
    input_file_count: concatUnique(base.input_files || base.inputFiles || [], incoming.input_files || incoming.inputFiles || []).length,
    input_files: concatUnique(base.input_files || base.inputFiles || [], incoming.input_files || incoming.inputFiles || []),
    source_page_count: mergedPages.length,
    detected_problem_count: mergedProblems.length,
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

function listUnique(values){
  return Array.from(new Set(values));
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
  return {
    pages: visiblePages.length,
    problems: problems.length,
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
  base.detected_problem_count = nextProblems.length;
  base.detectedProblemCount = nextProblems.length;
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
      detectPerspective: files.some(f => !/\.pdf$/i.test(f.name)),
      maxDimension: 2400,
      aiFallback: aiFallback || AI_FALLBACK_OFF,
    }),
  });
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `파싱 실행 실패 (${resp.status})`);
  return json.session;
}

async function fetchUserSettings(){
  const resp = await fetch('/api/user-settings');
  const json = await resp.json();
  if (!resp.ok || !json.ok) throw new Error(json.error || `설정 로드 실패 (${resp.status})`);
  return json.settings;
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

async function saveUserSettings(geminiApiKey){
  const resp = await fetch('/api/user-settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ geminiApiKey }),
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
  const [aiEnabled, setAiEnabled] = useState(true);
  const [inputIntent, setInputIntent] = useState(DEFAULT_INPUT_INTENT);
  const [refreshing, setRefreshing] = useState(false);
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

  const showToast = msg => {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  };

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
    setPublished(false);
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
  }, [session, adoptMutatedSession]);

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
      showToast('이전 상태로 되돌렸어요');
    } catch (e) {
      showToast(`되돌리기 실패: ${e.message}`);
    } finally {
      setMutating(false);
      setLoading(null);
    }
  }, [historyStack, session, adoptMutatedSession]);

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

  const onSaveGeminiKey = useCallback(async (key) => {
    try {
      const s = await saveUserSettings(key || '');
      setUserSettings(s);
      showToast(key ? 'Gemini 키 저장됨' : 'Gemini 키 삭제됨');
    } catch (e) {
      showToast('저장 실패: ' + e.message);
    }
  }, []);

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
        showToast(`새로고침 완료 · ${s.problems.length}개 문항`);
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
  }, [applySession, usingMock]);

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
          hint: `${summary.problems}개 문제를 찾았습니다.`,
        });
        setRecognitionReview({
          id: `review-${job.id}`,
          kind: 'queue-recognition',
          title: files.length === 1
            ? `${files[0].name || '파일'} · ${summary.problems}개 문제로 인식했어요`
            : `${summary.problems}개 문제로 인식했어요`,
          subtitle: '문제 경계가 맞으면 바로 칠판에 분할해서 붙입니다.',
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
      const appliedKeys = new Set(files.map(fileQueueKey));
      setPendingFiles(prev => prev.filter(file => !appliedKeys.has(fileQueueKey(file))));
      const intentLabel = isRecognition ? '문제 인식' : '순서 등록';
      showToast(`${intentLabel} 완료 · ${(sessionToApply.problems || []).length}개 문항`);
      const folder = sessionToApply?.output_dir || sessionToApply?.outputDir || s?.output_dir || s?.outputDir;
      if (folder) openOutputFolder(folder);
    } catch (e) {
      showToast(`${isRecognition ? '문제 인식' : '등록'} 실패: ${e.message}`);
    } finally {
      setLoading(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [pendingFiles, aiEnabled, userSettings, session, usingMock, items, fileName, applySession, startBackgroundJob, settleBackgroundJob]);

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
        setView('board');
        const appliedKeys = new Set(review.fileKeys || []);
        setPendingFiles(prev => prev.filter(file => !appliedKeys.has(fileQueueKey(file))));
        const summary = summarizeRecognitionSession(incomingSession);
        showToast(`칠판에 ${summary.problems}개 문제를 붙였어요`);
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
        setView('board');
        const summary = summarizeRecognitionSession(restored, review.pageIds);
        showToast(`AI 인식 적용 · ${summary.problems}개 문제`);
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
  const reorder = (fromId, toId) => {
    setItems(it => {
      const a = it.findIndex(x => x.id === fromId);
      const b = it.findIndex(x => x.id === toId);
      if (a < 0 || b < 0) return it;
      const arr = [...it];
      const [moved] = arr.splice(a, 1);
      arr.splice(b, 0, moved);
      return arr;
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
    const targetIds = options.bulk ? items.map(item => item.id) : [id];
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

  const onPublish = async () => {
    if (!session || !Array.isArray(session.problems)) {
      showToast('내보낼 자료가 없습니다. 먼저 파싱해 주세요.');
      return;
    }
    const sessionIds = new Set(session.problems.map(p => p.id));
    const currentIds = items.map(i => i.id);
    const order = currentIds.filter(id => sessionIds.has(id));
    const excluded = [...sessionIds].filter(id => !currentIds.includes(id));
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
          session: materializeSessionForItems(session, items, fileName) || session,
        }),
      });
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.error || `publish 실패 (${resp.status})`);
      setSession(json.session);
      const url = json.session?.edb_file_uri;
      if (url) {
        const a = document.createElement('a');
        a.href = url;
        a.download = (json.session.session_name || 'classin') + '.edb';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      setPublished(true);
      showToast(`${order.length}개 자료로 EDB 제작 완료 · 다운로드 시작`);
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
          onSaveGeminiKey={onSaveGeminiKey}
          aiEnabled={aiEnabled}
          setAiEnabled={setAiEnabled}
          inputIntent={inputIntent}
          setInputIntent={setInputIntent}
          onRecognizeSession={recognizeCurrentSession}
          canRecognizeSession={!!session && !!userSettings?.hasGeminiApiKey && !mutating && !hasRunningSessionRecognition}
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
        accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,image/*"
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
