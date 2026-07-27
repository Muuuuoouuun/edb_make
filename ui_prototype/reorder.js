(function attachReorderHelpers(root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = helpers;
  }
  root.EDB_REORDER = helpers;
})(typeof globalThis !== "undefined" ? globalThis : window, function createReorderHelpers() {
  const BEFORE = "before";
  const AFTER = "after";

  function normalizeDropPosition(position) {
    return position === AFTER ? AFTER : BEFORE;
  }

  function itemId(item) {
    return item && item.id != null ? String(item.id) : "";
  }

  function reorderItemsForDrop(items, fromId, toId, position) {
    if (!Array.isArray(items)) return items;
    const sourceId = fromId == null ? "" : String(fromId);
    const targetId = toId == null ? "" : String(toId);
    if (!sourceId || !targetId || sourceId === targetId) return items;

    const fromIndex = items.findIndex(item => itemId(item) === sourceId);
    const targetIndex = items.findIndex(item => itemId(item) === targetId);
    if (fromIndex < 0 || targetIndex < 0) return items;

    const next = items.slice();
    const moved = next.splice(fromIndex, 1)[0];
    const withoutSourceTargetIndex = next.findIndex(item => itemId(item) === targetId);
    if (withoutSourceTargetIndex < 0) return items;

    const insertAfterTarget = normalizeDropPosition(position) === AFTER;
    const insertIndex = withoutSourceTargetIndex + (insertAfterTarget ? 1 : 0);
    next.splice(insertIndex, 0, moved);
    return next;
  }

  function dropPositionFromClientY(rect, clientY) {
    if (!rect || !Number.isFinite(Number(clientY))) return BEFORE;
    const top = Number(rect.top) || 0;
    const height = Math.max(0, Number(rect.height) || 0);
    return Number(clientY) > top + height / 2 ? AFTER : BEFORE;
  }

  function scrollContainerContentTop(itemRect, containerRect, scrollTop = 0) {
    if (!itemRect || !containerRect) return 0;
    const itemTop = Number(itemRect.top) || 0;
    const containerTop = Number(containerRect.top) || 0;
    const containerScrollTop = Number(scrollTop) || 0;
    return itemTop - containerTop + containerScrollTop;
  }

  function edgeAutoScrollDelta(rect, clientY, edgePx = 64, maxPx = 22) {
    if (!rect || !Number.isFinite(Number(clientY))) return 0;
    const top = Number(rect.top) || 0;
    const bottom = Number(rect.bottom);
    const height = Math.max(0, Number(rect.height) || 0);
    const safeBottom = Number.isFinite(bottom) ? bottom : top + height;
    const safeEdge = Math.max(1, Number(edgePx) || 64);
    const safeMax = Math.max(0, Number(maxPx) || 0);
    let strength = 0;
    let direction = 0;
    if (clientY < top + safeEdge) {
      strength = 1 - Math.max(0, clientY - top) / safeEdge;
      direction = -1;
    } else if (clientY > safeBottom - safeEdge) {
      strength = 1 - Math.max(0, safeBottom - clientY) / safeEdge;
      direction = 1;
    }
    if (!direction || strength <= 0 || safeMax <= 0) return 0;
    const accelerated = Math.min(1, strength) ** 2;
    return direction * Math.max(1, Math.ceil(accelerated * safeMax));
  }

  function acceleratedEdgeAutoScrollDelta(
    rect,
    clientY,
    edgePx = 64,
    maxPx = 22,
    holdDurationMs = 0,
    frameDurationMs = 1000 / 60
  ) {
    const baseDelta = edgeAutoScrollDelta(rect, clientY, edgePx, maxPx);
    if (!baseDelta) return 0;
    const safeHoldMs = Math.max(0, Number(holdDurationMs) || 0);
    const holdProgress = Math.min(1, safeHoldMs / 900);
    const holdMultiplier = 1 + holdProgress;
    const safeFrameMs = Math.max(4, Math.min(42, Number(frameDurationMs) || (1000 / 60)));
    const frameMultiplier = safeFrameMs / (1000 / 60);
    const magnitude = Math.max(1, Math.round(Math.abs(baseDelta) * holdMultiplier * frameMultiplier));
    return Math.sign(baseDelta) * magnitude;
  }

  function appendBoundedHistory(history, entry, limit = 20) {
    const current = Array.isArray(history) ? history : [];
    if (!entry) return current;
    const numericLimit = Math.floor(Number(limit));
    const safeLimit = Number.isFinite(numericLimit) && numericLimit > 0 ? numericLimit : 20;
    if (current.length < safeLimit) return [...current, entry];
    return [...current.slice(current.length - safeLimit + 1), entry];
  }

  function nearestPlacementIndex(positions, targetTop) {
    if (!Array.isArray(positions) || positions.length === 0) return -1;
    const target = Number(targetTop) || 0;
    let low = 0;
    let high = positions.length;
    while (low < high) {
      const mid = low + Math.floor((high - low) / 2);
      const top = Number(positions[mid]?.top) || 0;
      if (top < target) low = mid + 1;
      else high = mid;
    }
    if (low <= 0) return 0;
    if (low >= positions.length) return positions.length - 1;
    const previousTop = Number(positions[low - 1]?.top) || 0;
    const nextTop = Number(positions[low]?.top) || 0;
    return target - previousTop <= nextTop - target ? low - 1 : low;
  }

  function adjacentReorderCommand(items, itemIdValue, direction) {
    if (!Array.isArray(items) || !items.length) return null;
    const sourceId = itemIdValue == null ? '' : String(itemIdValue);
    const sourceIndex = items.findIndex(item => itemId(item) === sourceId);
    if (sourceIndex < 0) return null;
    const delta = direction === 'up' ? -1 : direction === 'down' ? 1 : 0;
    if (!delta) return null;
    const targetIndex = sourceIndex + delta;
    if (targetIndex < 0 || targetIndex >= items.length) return null;
    return {
      sourceId,
      targetId: itemId(items[targetIndex]),
      position: delta < 0 ? BEFORE : AFTER,
      nextIndex: targetIndex,
    };
  }

  function problemDisplayName(item, index) {
    const raw = String(item?.name ?? item?.title ?? '').trim();
    const order = Math.max(1, Number(index) + 1 || 1);
    const splitMatch = raw.match(/\((위|아래)\)\s*$/);
    const splitLabel = splitMatch?.[1] === '위' ? '위쪽' : splitMatch?.[1] === '아래' ? '아래쪽' : '';
    const withoutSplit = splitMatch ? raw.slice(0, splitMatch.index).trim() : raw;
    const looksLikePath = /(?:^file:|[\\/])/.test(withoutSplit);
    const looksLikeFile = /\.(?:pdf|hwp|hwpx|png|jpe?g|webp|tiff?|bmp)(?:\s|$)/i.test(withoutSplit);
    const looksGenerated = /(?:^|[\s_-])page[-_ ]?\d+\s+problem[-_ ]?\d+/i.test(withoutSplit)
      || /^[a-f0-9]{20,}[_-]/i.test(withoutSplit);
    const isNoisy = !withoutSplit || looksLikePath || looksLikeFile || looksGenerated;
    if (!isNoisy) return raw;
    return `문제 ${order}${splitLabel ? ` · ${splitLabel}` : ''}`;
  }

  function problemSourceLabel(item) {
    const raw = String(item?.source ?? item?.sourcePageId ?? '').trim();
    if (!raw) return '업로드 원본';
    const pageMatch = raw.match(/(?:^|[-_\s])page[-_ ]?(\d+)(?:$|[-_\s])/i);
    if (pageMatch) return `원본 ${Number(pageMatch[1]) || 1}쪽`;
    const looksLikePath = /(?:^file:|[\\/])/.test(raw);
    const looksLikeFile = /\.(?:pdf|hwp|hwpx|png|jpe?g|webp|tiff?|bmp)(?:\s|$)/i.test(raw);
    const looksHashed = /^[a-f0-9]{20,}[_-]/i.test(raw);
    if (looksLikePath || looksLikeFile || looksHashed || raw.length > 44) return '업로드 원본';
    return raw;
  }

  return {
    AFTER,
    BEFORE,
    acceleratedEdgeAutoScrollDelta,
    adjacentReorderCommand,
    appendBoundedHistory,
    dropPositionFromClientY,
    edgeAutoScrollDelta,
    nearestPlacementIndex,
    normalizeDropPosition,
    problemDisplayName,
    problemSourceLabel,
    reorderItemsForDrop,
    scrollContainerContentTop,
  };
});
