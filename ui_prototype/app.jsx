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
  };
});

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
  check:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 13l4 4L19 7"/></svg>,
  board:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="13" rx="1"/><path d="M8 21h8M12 17v4"/></svg>,
  zoomIn: <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 11h6M11 8v6"/></svg>,
  undo:   <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3"/></svg>,
  refresh:<svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3.5-7.1M21 4v5h-5"/></svg>,
  reset:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/></svg>,
  pen:    <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M16 3l5 5L8 21H3v-5L16 3z"/></svg>,
  align:  <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 6h10M4 12h16M4 18h7"/></svg>,
};

const stepLabel = s => s === 's1' ? '1단계' : s === 's2' ? '2단계 · AI' : '대기';

// ─── TOP BAR ──────────────────────────────────────────────────────────────
function TopBar({ fileName, setFileName, progress, processed, total, onPublish, published, onReset, onRefresh, refreshing, hasSession, view, setView, reviewAvailable, onUndo, canUndo }){
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
      <button className="btn ghost" onClick={onReset} disabled={!hasSession} title="세션과 업로드 자료를 모두 비웁니다">
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
function ReviewStage({ session, items, activeId, setActive, mutateSession, mutating }){
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

  const riskyCount = (session?.problems || []).filter(p => (p.riskFlags || []).length > 0).length;

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
      <span className="hint">박스를 클릭해서 선택. Shift+클릭으로 여러 박스 선택.</span>
      <div className="spacer" />
      {riskyCount > 0 && (
        <span className="pg-risk" title="인식이 의심되는 박스 수">
          ⚠ 위험 의심 {riskyCount}건
        </span>
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
            const pageProblems = (page.problemIds || [])
              .map(pid => problemsById.get(pid))
              .filter(Boolean);
            const pageRiskFlags = page.riskFlags || [];
            const hasRisk = pageRiskFlags.length > 0;
            return (
              <div key={page.id} className="review-page">
                <div className="review-page-hd">
                  <span className="pg-num">{page.id}</span>
                  <span className="pg-count">{pageProblems.length}개 검출</span>
                  {hasRisk && (
                    <span className="pg-risk" title={pageRiskFlags.join(', ')}>
                      ⚠ 위험 · {pageRiskFlags.join(' · ')}
                    </span>
                  )}
                  <div className="spacer" />
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
                    const isRisky = (prob.riskFlags || []).length > 0;
                    const isSplitting = splitTarget === prob.id;
                    const order = orderMap.get(prob.id);
                    const tooltipParts = [prob.title || ''];
                    if (isRisky) tooltipParts.push(`위험: ${prob.riskFlags.join(', ')}`);
                    const classes = [
                      'review-bbox',
                      isSelected ? 'selected' : '',
                      isActive ? 'active' : '',
                      isRisky ? 'risky' : '',
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
                          {isRisky && <span className="review-bbox-risk">⚠</span>}
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
function ItemsRail({ items, activeId, setActive, reorder, removeItem, addSample, bulkApply, handleFiles }){
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
        <button className="icon-btn" title="모두 2단계로 변환" onClick={() => bulkApply('s2')}>{Icon.wand}</button>
        <button className="icon-btn" title="추가" onClick={addSample}>{Icon.upload}</button>
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
          <strong style={{marginTop:6}}>이미지·PDF 끌어다 놓기</strong>
          <small>JPG · PNG · HEIC · PDF · 최대 50MB</small>
        </div>

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
                {(it.riskFlags && it.riskFlags.length > 0) && (
                  <span
                    className="risk-pip"
                    title={`인식 의심: ${it.riskFlags.join(', ')}`}
                  >⚠</span>
                )}
                {it.name}
              </div>
              <div className="sub">
                {it.step === 's1' && <span className="tag s1">1단계</span>}
                {it.step === 's2' && <span className="tag s2">AI</span>}
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
function BoardStage({ items, activeId, setActive, boardColor, fileName, addSample, reorder }){
  const scrollRef = useRef(null);
  const tileRefs = useRef({});
  const syncLock = useRef(0);
  const dragId = useRef(null);
  const [overId, setOverId] = useState(null);
  const [draggingId, setDraggingId] = useState(null);
  const [pageH, setPageH] = useState(400);

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

  // compute page-flow positions: each item starts at next page boundary
  // after the previous item ends.  e.g. item ending at 1.8p → next at 2p.
  const layout = useMemo(() => {
    const EPS = 0.001;
    const positions = [];
    let cursor = 0;
    items.forEach((it, i) => {
      const top = i === 0 ? 0 : Math.ceil(cursor / pageH - EPS) * pageH;
      const height = (it.heightFrac || 0.8) * pageH;
      positions.push({ top, height, page: Math.floor(top / pageH) + 1, spans: Math.ceil(height / pageH) });
      cursor = top + height;
    });
    const endTop = items.length === 0 ? 0 : Math.ceil(cursor / pageH - EPS) * pageH;
    const endH = pageH * 0.42;
    const totalH = endTop + endH;
    const totalPages = Math.max(1, Math.ceil(totalH / pageH));
    return { positions, endTop, endH, totalH, totalPages };
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
    syncLock.current = Date.now();
    setActive(id);
  };

  const processedCount = items.filter(i => i.step !== 'raw').length;
  const aiCount = items.filter(i => i.step === 's2').length;
  const rawCount = items.filter(i => i.step === 'raw').length;
  const s1Count = items.filter(i => i.step === 's1').length;

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
              <div className="stage-content" style={{ height: layout.totalH }}>
                {/* page boundary dividers — scroll with content */}
                {dividers.map((top, i) => (
                  <div key={i} className="page-divider" style={{ top }}>
                    <span className="label">— {i + 2} 페이지 —</span>
                  </div>
                ))}

                {items.map((it, i) => {
                  const p = layout.positions[i];
                  if (!p) return null;
                  return (
                    <button
                      key={it.id}
                      ref={el => { tileRefs.current[it.id] = el; }}
                      className={`stage-tile ${activeId === it.id ? 'active' : ''} ${it.step === 's1' ? 'paper' : ''} ${draggingId === it.id ? 'dragging' : ''} ${overId === it.id ? 'drop-target' : ''}`}
                      onClick={() => onTileClick(it.id)}
                      title={it.name}
                      style={{ top: p.top, height: p.height }}
                      draggable
                      onDragStart={e => {
                        dragId.current = it.id;
                        setDraggingId(it.id);
                        e.dataTransfer.effectAllowed = 'move';
                      }}
                      onDragOver={e => {
                        e.preventDefault();
                        if (dragId.current && dragId.current !== it.id) {
                          setOverId(it.id);
                        }
                      }}
                      onDragLeave={() => setOverId(prev => prev === it.id ? null : prev)}
                      onDrop={e => {
                        e.preventDefault();
                        if (dragId.current && dragId.current !== it.id && reorder) {
                          reorder(dragId.current, it.id);
                        }
                        dragId.current = null;
                        setOverId(null);
                        setDraggingId(null);
                      }}
                      onDragEnd={() => {
                        dragId.current = null;
                        setOverId(null);
                        setDraggingId(null);
                      }}
                    >
                      <div className="tile-hd">
                        <span className="n">{String(i+1).padStart(2,'0')}</span>
                        <span className="nm">{it.name}</span>
                        {p.spans > 1 && (
                          <span className="span-mark">{p.page}–{p.page + p.spans - 1}p</span>
                        )}
                        <span className={`step-mark ${it.step}`}>
                          {it.step === 's1' ? '1' : it.step === 's2' ? 'AI' : '··'}
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
          <span className="chip">1문제 / 1페이지 · 자동 페이지 나눔</span>
          <span className="chip">
            <span style={{width:8, height:8, borderRadius:2, background:'#aa6516'}} />
            1단계 {s1Count}
          </span>
          <span className="chip">
            <span style={{width:8, height:8, borderRadius:2, background:'linear-gradient(135deg,#6d3df0,#2f6fed)'}} />
            AI {aiCount}
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
  boardColumns, setBoardColumns,
  boardColor, setBoardColor,
  accent, setAccent,
  onConfirm,
  userSettings, onSaveGeminiKey,
  aiEnabled, setAiEnabled,
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
                    <div className="s">{item.source} · {item.type.toUpperCase()}</div>
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
                    <button className="icon-btn" title="확대">{Icon.zoomIn}</button>
                    <div className="spacer" />
                    <span className="scale">100%</span>
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
                if (bulk) applyToAll(item.step);
                onConfirm(item.id);
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
              <span style={{display:'flex', alignItems:'center', gap:8}}>{Icon.wand} 전체를 AI 변환</span>
              <span style={{fontFamily:'JetBrains Mono, monospace', fontSize:11, color:'var(--muted)'}}>~ {items.length * 4}s</span>
            </button>
            <button className="btn" style={{justifyContent:'space-between'}} onClick={() => applyToAll('s1')}>
              <span style={{display:'flex', alignItems:'center', gap:8}}>{Icon.check} 전체를 1단계로</span>
              <span style={{fontFamily:'JetBrains Mono, monospace', fontSize:11, color:'var(--muted)'}}>즉시</span>
            </button>

            <div className="panel-section-hd" style={{marginTop:4}}>업로드 옵션 <span className="line" /></div>

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
  return {
    id: problem.id || `p${idx + 1}`,
    name: name === '' ? fallbackName : name,
    source: problem.sourcePageId || problem.subject || '업로드',
    type: 'image',
    kind: KIND_BY_SUBJECT[problem.subject] || 'paragraph',
    step: 'raw',
    heightFrac: typeof problem.actualHeightPages === 'number' && problem.actualHeightPages > 0
      ? problem.actualHeightPages
      : 0.8,
    imageUrl: problem.imagePath || null,
    chalkUrl: problem.boardRenderPath || null,
    subject: problem.subject || 'unknown',
    riskFlags: Array.isArray(problem.riskFlags) ? problem.riskFlags : [],
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
  model: 'gemini-2.5-pro',
  threshold: 0.72,
  maxRegions: 30,
  timeoutMs: 18000,
  saveDebug: false,
};

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

function requestedInitialView(){
  try {
    return new URLSearchParams(window.location.search).get('view') === 'review' ? 'review' : 'board';
  } catch (_err) {
    return 'board';
  }
}

async function postExport(files, aiFallback){
  const filesPayload = await Promise.all(files.map(async (f) => ({
    fileName: f.name,
    fileDataBase64: await fileToBase64(f),
  })));
  const resp = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      files: filesPayload,
      exportMode: 'question',
      inputIntent: 'auto',
      input_intent: 'auto',
      sourceMode: 'auto',
      source_mode: 'auto',
      subject: 'unknown',
      ocr: 'auto',
      exportEdb: true,
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

  const [items, setItems] = useState(INITIAL_ITEMS);
  const [activeId, setActiveId] = useState('i2');
  const [fileName, setFileName] = useState('6월 모의고사 오답풀이 — 6/12 (수)');
  const [bulk, setBulk] = useState(false);
  const [toast, setToast] = useState(null);
  const [published, setPublished] = useState(false);
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(null); // {label, hint, startedAt} when busy
  const [usingMock, setUsingMock] = useState(true);
  const [userSettings, setUserSettings] = useState(null);
  const [aiEnabled, setAiEnabled] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const initialViewRef = useRef(requestedInitialView());
  const initialViewConsumedRef = useRef(false);
  const [view, setView] = useState(initialViewRef.current);
  const [mutating, setMutating] = useState(false);
  // Undo history: each entry is a prior session snapshot. Pushed before
  // any successful mutation; popped by Ctrl/Cmd+Z (wired in Step 7).
  const [historyStack, setHistoryStack] = useState([]);
  const fileInputRef = useRef(null);

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

  const showToast = msg => {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
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

  // initial session fetch — falls back silently to INITIAL_ITEMS on 404
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
    if (!window.confirm('세션을 초기화할까요? 업로드한 자료가 모두 보드에서 사라집니다.')) return;
    try {
      await clearSession();
      setSession(null);
      setItems(INITIAL_ITEMS);
      setActiveId(INITIAL_ITEMS[0]?.id || null);
      setUsingMock(true);
      setPublished(false);
      setFileName('새 세션');
      showToast('초기화 완료');
    } catch (e) {
      showToast('초기화 실패: ' + e.message);
    }
  }, []);

  const refreshSession = useCallback(async () => {
    setRefreshing(true);
    try {
      const s = await fetchLatestSession();
      if (s && Array.isArray(s.problems) && s.problems.length) {
        applySession(s);
        showToast(`새로고침 완료 · ${s.problems.length}개 문항`);
      } else {
        // backend cleared the session — fall back to mock items
        setSession(null);
        setItems(INITIAL_ITEMS);
        setActiveId(INITIAL_ITEMS[0]?.id || null);
        setUsingMock(true);
        setPublished(false);
        showToast('저장된 세션이 없습니다');
      }
    } catch (e) {
      showToast('새로고침 실패: ' + e.message);
    } finally {
      setRefreshing(false);
    }
  }, [applySession]);

  const triggerUpload = () => fileInputRef.current?.click();

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const aiFallback = aiEnabled && userSettings?.hasGeminiApiKey
      ? AI_FALLBACK_ON
      : AI_FALLBACK_OFF;
    setLoading({
      label: `${files.length}개 파일 파싱 중...`,
      hint: aiFallback.enabled
        ? 'AI 보정 사용. 사진 한 장당 15~40초.'
        : 'AI 없이 빠른 파싱. 사진 한 장당 5~15초.',
      startedAt: Date.now(),
    });
    try {
      const s = await postExport(files, aiFallback);
      applySession(s);
      showToast(`파싱 완료 · ${(s.problems || []).length}개 문항`);
      // open the output folder in Windows Explorer so the user can grab the .edb
      const folder = s?.output_dir || s?.outputDir;
      if (folder) openOutputFolder(folder);
    } catch (e) {
      showToast(`파싱 실패: ${e.message}`);
    } finally {
      setLoading(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const setStep = (id, step) => {
    setItems(it => it.map(x => x.id === id ? { ...x, step } : x));
    setPublished(false);
  };
  const applyToAll = (step) => {
    setItems(it => it.map(x => ({ ...x, step })));
    showToast(`전체 ${items.length}개 항목에 ${step === 's1' ? '1단계' : '2단계 AI 변환'}을(를) 적용했어요`);
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
  };
  const removeItem = (id) => {
    setItems(it => it.filter(x => x.id !== id));
    if (activeId === id) {
      const next = items.find(x => x.id !== id);
      setActiveId(next ? next.id : null);
    }
  };
  // mock-only fallback: when no backend session, addSample stays as a visual mock
  const addMockSample = () => {
    const pool = ['geometry-circle','equation','table','graph','geometry-triangles','paragraph'];
    const kind = pool[Math.floor(Math.random() * pool.length)];
    const id = 'i' + (Date.now() % 100000);
    const name = '새 자료 ' + (items.length + 1);
    setItems(it => [...it, { id, name, source: '방금 업로드', type: 'image', kind, step: 'raw', heightFrac: heightForKind(kind) }]);
    setActiveId(id);
    showToast('자료 1개 추가됨 (mock)');
  };

  // when backend is reachable, addSample opens the real file picker → /api/export
  const addSample = () => {
    if (usingMock && !session) {
      // still try real upload — picker will be a no-op if user cancels
    }
    triggerUpload();
  };

  const onConfirm = (id) => {
    showToast(`"${items.find(i=>i.id===id)?.name}" 처리 완료`);
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
        body: JSON.stringify({ order, excluded }),
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
        hasSession={!!session}
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
        />
        {view === 'review' ? (
          <ReviewStage
            session={session}
            items={items}
            activeId={activeId}
            setActive={setActiveId}
            mutateSession={mutateSession}
            mutating={mutating}
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
        />
      </div>

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
