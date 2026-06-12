# Reorder and HWP Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make left-side item reordering predictable with clearer animation, and let HWP/HWPX uploads attempt conversion into the existing PDF/image recognition pipeline.

**Architecture:** Extract drag/drop ordering math into a small browser/Node-compatible helper, then wire the React rail to use explicit dragging state, before/after targets, and FLIP list animation. For HWP, add a preprocessing document-conversion boundary that tries available command-line converters and routes converted PDFs through the existing PDF renderer.

**Tech Stack:** React UMD + Babel in `ui_prototype/board.html`, plain JavaScript helper loaded before `app.jsx`, Python preprocessing with `pytest`, optional external converters such as LibreOffice or `hwp5pdf`.

---

### Task 1: Left Rail Reorder Helper and UI

**Files:**
- Create: `ui_prototype/reorder.js`
- Modify: `ui_prototype/app.jsx`
- Modify: `ui_prototype/board.html`
- Test: `test_ui_reorder_helper.py`

- [ ] **Step 1: Write failing tests**

```python
def test_reorder_helper_inserts_before_and_after_targets():
    script = """
    const { reorderItemsForDrop } = require('./ui_prototype/reorder.js');
    const ids = xs => xs.map(x => x.id).join(',');
    const items = ['a', 'b', 'c', 'd'].map(id => ({ id }));
    if (ids(reorderItemsForDrop(items, 'a', 'c', 'before')) !== 'b,a,c,d') throw new Error('before failed');
    if (ids(reorderItemsForDrop(items, 'a', 'c', 'after')) !== 'b,c,a,d') throw new Error('after failed');
    if (ids(reorderItemsForDrop(items, 'c', 'a', 'after')) !== 'a,c,b,d') throw new Error('upward after failed');
    if (ids(items) !== 'a,b,c,d') throw new Error('mutated original');
    """
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_ui_reorder_helper.py -q`
Expected: FAIL because `ui_prototype/reorder.js` does not exist.

- [ ] **Step 3: Implement helper**

Add a UMD-style `reorderItemsForDrop(items, fromId, toId, position)` and `dropPositionFromClientY(rect, clientY)` helper in `ui_prototype/reorder.js`.

- [ ] **Step 4: Wire React rail**

Use `useState` for `draggingId`, `dropTarget`, and `dropPosition`; set `dataTransfer` payload on drag start; calculate before/after in drag-over; pass the position into `reorder`.

- [ ] **Step 5: Improve animation**

Add `useLayoutEffect` FLIP animation in `ItemsRail` for moved list items and add CSS for `.item.dragging`, `.item.drop-before`, and `.item.drop-after` in `board.html`.

- [ ] **Step 6: Verify**

Run: `pytest test_ui_reorder_helper.py -q`
Expected: PASS.

### Task 2: HWP/HWPX Upload Recognition Attempt

**Files:**
- Modify: `preprocess.py`
- Modify: `ui_prototype/app.jsx`
- Test: `test_preprocess_hwp.py`

- [ ] **Step 1: Write failing tests**

```python
def test_hwp_routes_through_converted_pdf(monkeypatch, tmp_path):
    source = tmp_path / "exam.hwp"
    source.write_bytes(b"hwp")
    converted = tmp_path / "converted" / "exam.pdf"
    rendered = tmp_path / "rendered.png"
    Image.new("RGB", (40, 50), "white").save(rendered)

    monkeypatch.setattr(preprocess, "convert_hwp_to_pdf", lambda src, out: converted)
    monkeypatch.setattr(preprocess, "render_pdf_pages", lambda src, out, dpi: [
        preprocess.NormalizedPageImage("page-001", str(src), str(rendered), 0, 40, 50, {"source_type": "pdf"})
    ])

    pages = preprocess.normalize_source_pages(source, tmp_path / "out")
    assert pages[0].metadata["source_type"] == "hwp"
    assert pages[0].metadata["source_hwp_path"] == str(source)
    assert pages[0].metadata["converted_pdf_path"] == str(converted)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_preprocess_hwp.py -q`
Expected: FAIL because `convert_hwp_to_pdf` and HWP routing do not exist.

- [ ] **Step 3: Implement conversion boundary**

Add `HWP_DOCUMENT_EXTENSIONS`, converter discovery for `soffice`, LibreOffice macOS app path, and `hwp5pdf`, and a `convert_hwp_to_pdf` function that writes into `normalized_dir / "converted"`.

- [ ] **Step 4: Route HWP/HWPX in preprocessing**

When suffix is `.hwp` or `.hwpx`, call `convert_hwp_to_pdf`, render/normalize the resulting PDF, and preserve metadata for original HWP and converted PDF paths.

- [ ] **Step 5: Update UI upload affordances**

Allow `.hwp,.hwpx` in the file input, label queued HWP files as `HWP`, and avoid perspective detection for document-like files.

- [ ] **Step 6: Verify**

Run: `pytest test_preprocess_hwp.py test_ui_reorder_helper.py -q`
Expected: PASS.

### Task 3: Browser QA and Review

**Files:**
- No new production files unless fixes are required.

- [ ] **Step 1: Run targeted Python tests**

Run: `pytest test_preprocess_hwp.py test_ui_reorder_helper.py -q`
Expected: PASS.

- [ ] **Step 2: Start local app**

Run: `python3 app_server.py --no-open --port 8784`
Expected: server starts and serves `http://127.0.0.1:8784/`.

- [ ] **Step 3: Browser-check the UI**

Open the app, verify HWP appears in the file picker accept string, and verify drag/drop order changes visually with before/after markers.

- [ ] **Step 4: Final review**

Review `git diff`, confirm unrelated `.DS_Store` is untouched, and report tests/QA result.
