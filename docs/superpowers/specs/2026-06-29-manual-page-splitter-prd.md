# Manual Page Splitter PRD

## Goal

Allow the user to upload one full source image/page and rapidly split it into multiple problem images by drawing or placing crop regions manually.

The feature should solve the case where automatic recognition is unnecessary, wrong, or slower than the user simply cutting the page by hand.

## User Problem

Today the app can recognize detected regions, crop an existing detected region, split one problem into two, and merge detected problems. This works when the automatic detector already produced a useful first set of boxes.

The missing workflow is:

1. User uploads one full image or rendered page.
2. User does not want the app to guess problem boundaries.
3. User wants to draw several crop frames quickly, like macOS screenshot selection.
4. The app turns those frames into separate problem images for the board/EDB flow.

## Current Context

Useful foundations already exist:

- `ReviewStage` renders page images and problem bbox overlays.
- Single bbox editing is already driven by page pixel coordinates.
- `/api/session/mutate` supports `split`, `merge`, `crop`, and `exclude`.
- `app_server.py` already has bbox crop helpers and problem-list replacement helpers.
- Problem ordering, board placement, publish, undo, and history are already session-based.

This should be implemented as a session mutation, not as a separate export pipeline.

## Primary User Flow

1. User drops a PNG/JPG/PDF/HWP into the upload queue.
2. For a queue item, user chooses `수동 분할`.
3. The app registers the selected source as a page-level item and opens the review screen in manual split mode.
4. The source page fills the main canvas.
5. User draws crop boxes by drag-selecting rectangles.
6. Each box receives an order badge: `01`, `02`, `03`.
7. User can move, resize, delete, duplicate, or reorder boxes.
8. User clicks `분할 적용`.
9. The app crops each box into PNG files, replaces the one full-page item with multiple problem items, and returns to normal board/review flow.

## Interaction Model

### Box Mode

Box mode is the MVP.

- Drag on empty page area creates a new crop box.
- Clicking a box selects it.
- Dragging inside selected box moves it.
- Dragging handles resizes it.
- `Delete` removes selected box.
- `Esc` cancels the active draw/edit gesture.
- `Enter` applies the split when at least one valid box exists.
- Shift-click multi-selects boxes.
- Selected boxes can be nudged by arrow keys.

### Split-Line Mode

Split-line mode is a follow-up but should be designed now.

The user first defines one outer crop frame, then clicks horizontal or vertical split lines inside it. The app generates rectangular cells from the line grid.

This is useful for worksheet pages with two columns or regular stacked problems.

MVP can omit this mode if implementation time is tight, but the UI should leave room for a segmented control:

- `박스`
- `분할선`

### Auto Ordering

Default ordering should be automatic:

- Sort by top position.
- For similar top rows, sort by left position.
- Use a row tolerance of roughly 3 percent of page height.

The right-side crop list should support drag reordering after automatic ordering.

This gives good defaults for most Korean worksheet scans while preserving manual control.

## Product Requirements

### Functional Requirements

- User can create multiple crop boxes on one source page.
- User can edit crop boxes before applying.
- User can apply all boxes in one action.
- The resulting problem list uses the crop boxes as `bbox`.
- Cropped outputs are saved as PNG.
- `imagePath` points to the raw adjusted crop.
- `boardRenderPath` points to the EDB-ready board image when available, otherwise the same crop.
- Existing undo should restore the pre-split session.
- Existing publish flow should treat the new problem items like normal recognized problems.
- Manual boxes should clear automatic risk flags for the replaced items.
- Generated problem titles should be predictable: `문항 01`, `문항 02`, etc., unless the user edits titles.

### Non-Functional Requirements

- Drawing should feel instant on large images.
- Coordinate math must use original page pixels, not displayed CSS pixels.
- Repeated apply/cancel should not leave orphaned session state.
- The feature should work offline.
- No AI key should be required.

## Data Model

Add a temporary frontend-only structure while editing:

```js
{
  id: "draft-region-01",
  pageId: "page-001",
  bbox: { left: 120, top: 80, width: 720, height: 260 },
  title: "문항 01",
  order: 1
}
```

Persist only after apply through a mutation:

```js
{
  action: "bulk-crop",
  pageId: "page-001",
  replaceProblemIds: ["problem-001"],
  regions: [
    {
      bbox: { left: 120, top: 80, width: 720, height: 260 },
      title: "문항 01"
    }
  ]
}
```

## Backend Requirements

Add `_mutate_bulk_crop(session, page_id, regions, replace_problem_ids=None)`.

Behavior:

1. Resolve source page image from `page.sourceImagePath` or `page.sourceImageUri`.
2. Validate each region with `_coerce_crop_box`.
3. Create one crop PNG per region under the session crop directory.
4. Build one problem entry per region using `_problem_skeleton_from_parent`.
5. Set each entry:
   - `id`
   - `title`
   - `sourcePageId`
   - `sourceImagePath`
   - `bbox`
   - `imagePath`
   - `boardRenderPath`
   - `recordMode: image-only`
   - `imageRecordCount: 1`
   - `riskFlags: []`
6. Replace the requested source problem ids or append to page problems if no replacement id is supplied.
7. Update `page.problemIds`.
8. Refresh problem counts and review summary.

## Frontend Requirements

Add `ManualSplitEditor` inside or adjacent to `ReviewStage`.

Suggested entry points:

- Upload queue row action: `수동 분할`
- Review toolbar action for a selected full-page item: `수동 쪼개기`
- Recognition review modal secondary action: `직접 자르기`

Key UI elements:

- Main page canvas with draft boxes.
- Toolbar: mode toggle, apply, cancel, delete selected, auto-sort.
- Side list: crop thumbnails or box labels with drag reorder.
- Status text: `N개 영역`.

## Edge Cases

- Zero boxes: apply disabled.
- Tiny boxes: clamp to minimum dimensions.
- Overlapping boxes: allowed but show warning because duplicate source content may be intentional.
- Boxes outside page: clamp to page bounds.
- Very large pages: canvas can zoom/pan later; MVP can rely on scroll and fit-width.
- Multi-page PDF: manual split operates one page at a time.
- Two-column worksheets: auto-order must not scramble columns; row-tolerance sort is important.

## Acceptance Criteria

- A full-page uploaded image can be manually split into at least five separate problem items.
- The resulting images open from `imagePath`.
- The resulting EDB publish includes each split item.
- Undo returns to the original full-page item.
- Crops preserve the user-selected region within a small pixel tolerance.
- User can delete or reorder boxes before apply.
- The feature works without AI settings.

## Implementation Plan

1. Backend `bulk-crop` mutation with tests around crop count, bbox clamping, page problem id replacement, and undo compatibility.
2. Frontend draft-region state and canvas coordinate helpers.
3. Box draw/move/resize interactions.
4. Apply/cancel controls wired to `mutateSession("bulk-crop", payload)`.
5. Upload queue and review toolbar entry points.
6. Manual QA with one tall scan, one two-column scan, and one PDF-rendered page.

## Open Decisions

- Default sort should be automatic top-left sorting, with manual reorder available.
- MVP should ship box mode first; split-line mode should remain phase two.
- The user-visible label should likely be `수동 쪼개기` in review and `수동 분할` in upload queue.

