# Cropped Image Export PRD

## Goal

Let the user download every image that will be inserted into the EDB, plus the raw adjusted crop images, as PNG files in a single ZIP.

This gives the user a clean backup, inspection package, and reuse path for all manually adjusted or automatically recognized problem images.

## User Problem

The app currently produces cropped problem images and EDB-ready board images during recognition, manual crop, split, merge, enhancement, and publish flows. These images are available internally through session paths, but the user cannot download the full set in one obvious action.

The desired workflow is:

1. User finishes recognition/manual cropping/manual splitting.
2. User wants all final images as PNG.
3. User clicks one button.
4. The app downloads a ZIP containing ordered PNGs and a manifest.

## Current Context

Session problem entries already expose the important asset paths:

- `imagePath`: raw crop or manually adjusted crop.
- `boardRenderPath`: EDB-ready image when generated, otherwise usually same as `imagePath`.
- `originalImagePath`: pre-enhancement source when available.
- `bbox`: source page coordinates.
- `sourcePageId` and `sourceImagePath`: provenance.

Existing `/api/file` can serve individual files, and the app already supports EDB download through `publishSummary`.

The missing piece is an artifact packaging endpoint and a visible UI action.

## Product Definition

Add a `PNG 묶음 다운로드` action that exports the current session's problem images as a ZIP.

Default export should include only active/current problem items in the same order that the board/EDB publish uses.

The ZIP should include both final EDB images and raw adjusted crops when they differ.

## Export Modes

### Default: EDB Images

Folder:

```text
edb_images/
```

Contents:

- One PNG per included problem.
- Uses `boardRenderPath` if it exists.
- Falls back to `imagePath`.
- File names are ordered and safe:
  - `001_문항_01.png`
  - `002_문항_02.png`

### Raw Crops

Folder:

```text
raw_crops/
```

Contents:

- One PNG per included problem.
- Uses `imagePath`.
- Represents the user-adjusted crop before board rendering/enhancement when available.

### Manifest

File:

```text
manifest.json
```

Contents:

```json
{
  "sessionName": "example",
  "exportedAt": "2026-06-29T12:00:00+09:00",
  "mode": "both",
  "count": 2,
  "items": [
    {
      "index": 1,
      "problemId": "problem-001",
      "title": "문항 01",
      "sourcePageId": "page-001",
      "bbox": { "left": 120, "top": 80, "width": 720, "height": 260 },
      "edbImage": "edb_images/001_문항_01.png",
      "rawCrop": "raw_crops/001_문항_01.png"
    }
  ]
}
```

## Product Requirements

### Functional Requirements

- User can download all current session problem images as a ZIP.
- ZIP includes PNG images only for image assets.
- Export respects current board order after user reorder.
- Excluded/deleted items are not included by default.
- Supplemental/passage items should follow the same inclusion rules as publish unless a future setting says otherwise.
- If `boardRenderPath` is missing, use `imagePath`.
- If raw and EDB image paths point to the same file, either duplicate into both folders for clarity or include once and mark both manifest fields. MVP should duplicate for user clarity.
- Manifest must be included.
- File names must be filesystem-safe on Windows/macOS.
- Export should work before EDB publish.

### Non-Functional Requirements

- No AI key should be required.
- Export should not mutate the session.
- Export should not require opening the output folder.
- ZIP generation should stream or write to a temporary runtime artifact without loading huge files into memory unnecessarily.
- Missing images should not break the entire ZIP if at least one image exists; missing items should be listed in `manifest.json`.

## API Design

Add:

```http
POST /api/session/export-images
Content-Type: application/json

{
  "mode": "both",
  "problemIds": ["problem-001", "problem-002"],
  "includeMissing": true
}
```

Response:

```json
{
  "ok": true,
  "downloadUrl": "/api/file?path=/.../exports/session_images.zip",
  "zipPath": "/.../session_images.zip",
  "fileName": "session_images.zip",
  "count": 20,
  "missing": []
}
```

Supported modes:

- `edb`: only `edb_images`.
- `raw`: only `raw_crops`.
- `both`: both folders plus manifest. Default.

## Backend Requirements

Add helper functions:

- `_session_problem_image_export_items(session, problem_ids=None)`
- `_safe_export_filename(index, title, problem_id)`
- `_write_session_image_export_zip(session, mode, problem_ids=None)`

Implementation notes:

- Use Python `zipfile`.
- Resolve all paths with `_resolve_session_path` or `_file_uri_to_path`.
- Use `path_to_api_url` for the returned download URL.
- Save under a runtime/export directory, not inside source folders.
- Add the ZIP path to `allowed_files` before responding.
- Prefer current session order as sent by the frontend, because frontend item order may differ from original session problem order after drag reorder.

## Frontend Requirements

Add visible actions:

- In publish result panel: `PNG 묶음`
- In board/review toolbar: `이미지 다운로드`
- Optional dropdown later: `EDB용`, `원본 crop`, `둘 다`

Default click should call `mode: "both"` and trigger browser download.

Download naming:

```js
`${sessionName || "classin"}_images.zip`
```

Use existing download-anchor behavior used by EDB download.

## UX Copy

Button labels:

- `PNG 묶음`
- `이미지 다운로드`

Toast examples:

- `PNG 묶음을 준비했어요`
- `다운로드할 이미지가 없습니다`
- `일부 이미지를 찾지 못했어요. manifest에서 확인할 수 있어요`

## Edge Cases

- No session: show error.
- Session has zero problems: show error.
- A problem has no image path: list as missing in manifest.
- `boardRenderPath` missing: fallback to `imagePath`.
- `imagePath` points to deleted temp file: list as missing.
- Duplicate titles: filenames still differ by index.
- Korean titles: keep names readable but strip path-unsafe characters.

## Acceptance Criteria

- User can download a ZIP before publishing EDB.
- ZIP contains one `edb_images/*.png` file per included problem.
- ZIP contains one `raw_crops/*.png` file per included problem in `both` mode.
- `manifest.json` maps every problem to exported filenames and source bbox.
- Exported image order matches board order.
- Missing assets are reported in the response and manifest.
- Existing EDB publish behavior is unchanged.

## Implementation Plan

1. Add backend ZIP export helper and API endpoint.
2. Add frontend `postExportImages` helper.
3. Add toolbar/publish-panel button and trigger browser download.
4. Add tests for mode selection, fallback path, missing image manifest, and filename safety.
5. Manual QA with:
   - normal recognized session
   - manually cropped session
   - manually split session
   - enhanced image session

## Open Decisions

- Default mode should be `both` because the user asked for EDB images and adjusted crops.
- MVP should duplicate identical raw/EDB files into both folders for clarity.
- Later, add a settings dropdown if ZIP size becomes a concern.

