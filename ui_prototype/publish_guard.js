(function(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.EDB_PUBLISH_GUARD = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  const DEFAULT_SLOT_HEIGHT_PAGES = 1.2;
  const DEFAULT_HEIGHT_PAGES = 0.8;
  const DEFAULT_SCALE_RATIO = 1.0;
  const PLACEMENT_SCALE_MAX = 1.6;
  const MIN_HEIGHT_PAGES = 0.12;
  const OVERLAP_TOLERANCE_PAGES = 0.01;
  const SOURCE_BBOX_OVERLAP_RATIO = 0.65;

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function firstNumber(value, keys, fallback) {
    for (const key of keys) {
      if (value && Object.prototype.hasOwnProperty.call(value, key)) {
        return finiteNumber(value[key], fallback);
      }
    }
    return fallback;
  }

  function clamp01(value) {
    return Math.max(0, Math.min(1, finiteNumber(value, 0)));
  }

  function snapUpPages(value, slotHeightPages = DEFAULT_SLOT_HEIGHT_PAGES) {
    const slot = finiteNumber(slotHeightPages, DEFAULT_SLOT_HEIGHT_PAGES);
    if (slot <= 0 || !Number.isFinite(value) || value <= 0) return 0;
    return Math.ceil((value - 0.001) / slot) * slot;
  }

  function problemIdFor(item, index) {
    const raw = item?.id ?? item?.problemId ?? item?.problem_id ?? `item-${index}`;
    return String(raw || "").trim();
  }

  function problemTitleFor(item, fallback) {
    return String(item?.name || item?.title || item?.problemNumber || item?.problem_number || fallback || "").trim();
  }

  function sourcePageIdFor(item) {
    return String(item?.sourcePageId || item?.source_page_id || item?.pageId || item?.page_id || "").trim();
  }

  function numberOrNull(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function bboxFor(item) {
    const raw = item?.bbox || item?.sourceBbox || item?.source_bbox;
    if (!raw || typeof raw !== "object") return null;
    const left = numberOrNull(raw.left ?? raw.x);
    const top = numberOrNull(raw.top ?? raw.y);
    const width = numberOrNull(raw.width ?? raw.w);
    const height = numberOrNull(raw.height ?? raw.h);
    let right = numberOrNull(raw.right);
    let bottom = numberOrNull(raw.bottom);
    if (left === null || top === null) return null;
    if (width !== null) right = left + width;
    if (height !== null) bottom = top + height;
    if (right === null || bottom === null || right <= left || bottom <= top) return null;
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }

  function rounded(value) {
    return Number(value.toFixed(6));
  }

  function bboxPayload(bbox) {
    return {
      left: rounded(bbox.left),
      top: rounded(bbox.top),
      width: rounded(bbox.width),
      height: rounded(bbox.height),
    };
  }

  function normalizeIdFilter(value) {
    if (!value) return null;
    const raw = value instanceof Set ? Array.from(value) : Array.isArray(value) ? value : [];
    if (!raw.length) return null;
    return new Set(raw.map(item => String(item || "").trim()).filter(Boolean));
  }

  function simulatedBoardPlacements(items, options = {}) {
    const slotHeightPages = finiteNumber(options.slotHeightPages, DEFAULT_SLOT_HEIGHT_PAGES);
    const problemIds = normalizeIdFilter(options.sessionProblemIds);
    const placements = [];
    let cursorPages = 0;

    (Array.isArray(items) ? items : []).forEach((item, index) => {
      if (!item || typeof item !== "object") return;
      const problemId = problemIdFor(item, index);
      if (!problemId || (problemIds && !problemIds.has(problemId))) return;

      const heightPages = Math.max(
        MIN_HEIGHT_PAGES,
        firstNumber(item, ["heightFrac", "actualHeightPages", "actual_height_pages"], DEFAULT_HEIGHT_PAGES)
      );
      const startYPages = snapUpPages(cursorPages, slotHeightPages);
      const snappedNextStartYPages = snapUpPages(startYPages + heightPages, slotHeightPages);
      const slotSpanPages = Math.max(heightPages, snappedNextStartYPages - startYPages);
      const requestedScale = Math.max(
        0,
        Math.min(
          PLACEMENT_SCALE_MAX,
          firstNumber(item, ["placementScaleRatio", "placement_scale_ratio", "scaleRatio"], DEFAULT_SCALE_RATIO)
        )
      );
      const renderedHeightPages = heightPages * requestedScale;
      const verticalRoomPages = Math.max(0, slotSpanPages - renderedHeightPages);
      const yRatio = verticalRoomPages > 0.001
        ? clamp01(firstNumber(item, ["placementYRatio", "placement_y_ratio", "yRatio"], 0))
        : 0;
      const renderedTopYPages = startYPages + yRatio * verticalRoomPages;
      const renderedBottomYPages = renderedTopYPages + renderedHeightPages;

      placements.push({
        problemId,
        problemTitle: problemTitleFor(item, problemId),
        startYPages,
        renderedTopYPages,
        renderedBottomYPages,
        snappedNextStartYPages,
        heightPages,
        requestedScale,
      });
      cursorPages = snappedNextStartYPages;
    });

    return placements;
  }

  function findSourceProblemOverlaps(problems, options = {}) {
    const threshold = finiteNumber(options.overlapAreaRatio, SOURCE_BBOX_OVERLAP_RATIO);
    const groups = new Map();
    (Array.isArray(problems) ? problems : []).forEach((problem, index) => {
      if (!problem || typeof problem !== "object") return;
      const sourcePageId = sourcePageIdFor(problem);
      const bbox = bboxFor(problem);
      if (!sourcePageId || !bbox) return;
      const group = groups.get(sourcePageId) || [];
      group.push({ problem, bbox, index });
      groups.set(sourcePageId, group);
    });

    const issues = [];
    groups.forEach((group, sourcePageId) => {
      group.sort((a, b) => (
        a.bbox.top - b.bbox.top
        || a.bbox.left - b.bbox.left
        || String(problemIdFor(a.problem, a.index)).localeCompare(String(problemIdFor(b.problem, b.index)))
      ));
      for (let index = 0; index < group.length; index += 1) {
        const current = group[index];
        const currentArea = current.bbox.width * current.bbox.height;
        if (currentArea <= 0) continue;
        for (let nextIndex = index + 1; nextIndex < group.length; nextIndex += 1) {
          const next = group[nextIndex];
          const nextArea = next.bbox.width * next.bbox.height;
          if (nextArea <= 0) continue;
          const intersectionWidth = Math.max(0, Math.min(current.bbox.right, next.bbox.right) - Math.max(current.bbox.left, next.bbox.left));
          const intersectionHeight = Math.max(0, Math.min(current.bbox.bottom, next.bbox.bottom) - Math.max(current.bbox.top, next.bbox.top));
          const intersectionArea = intersectionWidth * intersectionHeight;
          if (intersectionArea <= 0) continue;
          const overlapAreaRatio = intersectionArea / Math.min(currentArea, nextArea);
          if (overlapAreaRatio < threshold) continue;
          const unionArea = currentArea + nextArea - intersectionArea;
          issues.push({
            type: "source_problem_bbox_overlap",
            severity: "warning",
            problemId: problemIdFor(current.problem, current.index),
            problemTitle: problemTitleFor(current.problem, problemIdFor(current.problem, current.index)),
            nextProblemId: problemIdFor(next.problem, next.index),
            nextProblemTitle: problemTitleFor(next.problem, problemIdFor(next.problem, next.index)),
            sourcePageId,
            overlapAreaRatio: rounded(overlapAreaRatio),
            intersectionOverUnion: unionArea > 0 ? rounded(intersectionArea / unionArea) : 0,
            intersectionAreaPx: rounded(intersectionArea),
            sourceBBoxOverlapThreshold: threshold,
            bbox: bboxPayload(current.bbox),
            nextBbox: bboxPayload(next.bbox),
          });
        }
      }
    });
    return issues;
  }

  function findBoardPlacementOverlaps(items, options = {}) {
    const tolerancePages = finiteNumber(options.tolerancePages, OVERLAP_TOLERANCE_PAGES);
    const placements = simulatedBoardPlacements(items, options);
    const issues = [];
    for (let index = 0; index < placements.length - 1; index += 1) {
      const current = placements[index];
      const next = placements[index + 1];
      const overlapPages = current.renderedBottomYPages - next.renderedTopYPages;
      if (overlapPages <= tolerancePages) continue;
      issues.push({
        type: "board_placement_overlap",
        severity: "warning",
        problemId: current.problemId,
        problemTitle: current.problemTitle,
        nextProblemId: next.problemId,
        nextProblemTitle: next.problemTitle,
        renderedBottomYPages: Number(current.renderedBottomYPages.toFixed(6)),
        nextTopYPages: Number(next.renderedTopYPages.toFixed(6)),
        overlapPages: Number(overlapPages.toFixed(6)),
      });
    }
    return issues;
  }

  return {
    findBoardPlacementOverlaps,
    findSourceProblemOverlaps,
    simulatedBoardPlacements,
  };
});
