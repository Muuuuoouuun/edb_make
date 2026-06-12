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

  return {
    AFTER,
    BEFORE,
    dropPositionFromClientY,
    normalizeDropPosition,
    reorderItemsForDrop,
  };
});
