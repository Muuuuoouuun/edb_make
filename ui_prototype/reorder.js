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

  function orderedItemIds(items, ids) {
    if (!Array.isArray(items)) return [];
    const requested = new Set((Array.isArray(ids) ? ids : [ids])
      .map(value => value == null ? "" : String(value))
      .filter(Boolean));
    return items.map(itemId).filter(id => requested.has(id));
  }

  function reorderItemGroupForDrop(items, fromIds, toId, position) {
    if (!Array.isArray(items)) return items;
    const sourceIds = orderedItemIds(items, fromIds);
    const targetId = toId == null ? "" : String(toId);
    if (!sourceIds.length || !targetId) return items;

    const sourceIdSet = new Set(sourceIds);
    if (sourceIdSet.has(targetId)) return items;

    const moved = items.filter(item => sourceIdSet.has(itemId(item)));
    const remaining = items.filter(item => !sourceIdSet.has(itemId(item)));
    const targetIndex = remaining.findIndex(item => itemId(item) === targetId);
    if (targetIndex < 0) return items;

    const insertAfterTarget = normalizeDropPosition(position) === AFTER;
    const insertIndex = targetIndex + (insertAfterTarget ? 1 : 0);
    const next = remaining.slice();
    next.splice(insertIndex, 0, ...moved);

    const unchanged = next.length === items.length
      && next.every((item, index) => itemId(item) === itemId(items[index]));
    return unchanged ? items : next;
  }

  function reorderItemsForDrop(items, fromId, toId, position) {
    return reorderItemGroupForDrop(items, [fromId], toId, position);
  }

  function dropPositionFromClientY(rect, clientY) {
    if (!rect || !Number.isFinite(Number(clientY))) return BEFORE;
    const top = Number(rect.top) || 0;
    const height = Math.max(0, Number(rect.height) || 0);
    return Number(clientY) > top + height / 2 ? AFTER : BEFORE;
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

  function adjacentGroupReorderCommand(items, itemIds, direction) {
    if (!Array.isArray(items) || !items.length) return null;
    const sourceIds = orderedItemIds(items, itemIds);
    if (!sourceIds.length) return null;

    const sourceIdSet = new Set(sourceIds);
    const selectedIndexes = items
      .map((item, index) => sourceIdSet.has(itemId(item)) ? index : -1)
      .filter(index => index >= 0);
    const delta = direction === 'up' ? -1 : direction === 'down' ? 1 : 0;
    if (!delta) return null;

    const edgeIndex = delta < 0
      ? Math.min(...selectedIndexes)
      : Math.max(...selectedIndexes);
    const targetIndex = edgeIndex + delta;
    if (targetIndex < 0 || targetIndex >= items.length) return null;

    const targetId = itemId(items[targetIndex]);
    if (!targetId || sourceIdSet.has(targetId)) return null;
    const position = delta < 0 ? BEFORE : AFTER;
    const reordered = reorderItemGroupForDrop(items, sourceIds, targetId, position);
    if (reordered === items) return null;

    return {
      sourceId: sourceIds[0],
      sourceIds,
      targetId,
      position,
      nextIndex: reordered.findIndex(item => itemId(item) === sourceIds[0]),
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
    adjacentGroupReorderCommand,
    adjacentReorderCommand,
    appendBoundedHistory,
    dropPositionFromClientY,
    edgeAutoScrollDelta,
    nearestPlacementIndex,
    normalizeDropPosition,
    problemDisplayName,
    problemSourceLabel,
    reorderItemGroupForDrop,
    reorderItemsForDrop,
  };
});
