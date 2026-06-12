# Recognition Speed Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recognition faster without reducing quality by skipping trusted OCR work, improving cache reuse, and exposing timing diagnostics.

**Architecture:** Keep the current local-first pipeline and add conservative fast paths inside the existing boundaries. PDF text-marker blocks skip OCR, OCR cache reads can use stable sidecar indexes with legacy image-hash fallback, and page metadata records worker counts plus stage timings.

**Tech Stack:** Python 3.11, Pillow, pytest/unittest, existing EDB pipeline modules.

---

## File Structure

- Modify: `build_structured_page_json.py`
  - trusted OCR skip
  - stable cache-key creation
  - adaptive block OCR worker resolver
  - page recognition timing metadata
- Modify: `pipeline_cache.py`
  - optional stable OCR cache identity support
  - sidecar `ocr_index` lookup pointing at existing image-hash payloads
  - image-hash legacy fallback support
- Create: `test_recognition_speed_quality.py`
  - focused regression tests for skip/cache/workers/metadata

## Task 1: Trusted PDF Marker OCR Skip

**Files:**
- Modify: `build_structured_page_json.py`
- Create: `test_recognition_speed_quality.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pdf_text_marker_blocks_skip_backend_ocr(monkeypatch, tmp_path):
    from PIL import Image

    import build_structured_page_json as pipeline
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage
    from structured_schema import Subject

    class ExplodingBackend:
        engine_name = "gemini"

        def recognize(self, image):
            raise AssertionError("trusted PDF marker block should not call OCR")

    source = tmp_path / "page.png"
    Image.new("RGB", (600, 800), "white").save(source)
    prepared = PreparedPage(
        page_id="pdf-page-001",
        source_path=str(source),
        page_number=1,
        image=Image.open(source).convert("RGB"),
        original_size=(600, 800),
        metadata={
            "source_type": "pdf",
            "pdf_problem_markers": [
                {
                    "number": 1,
                    "text": "1. problem stem",
                    "bbox": {"left": 60, "top": 120, "right": 90, "bottom": 140},
                }
            ],
        },
    )
    monkeypatch.setattr(pipeline, "create_ocr_backend", lambda _mode: ExplodingBackend())

    page = pipeline.build_page_model(
        prepared,
        subject=Subject.MATH,
        ocr_mode="gemini",
        ai_config=build_ai_fallback_config(mode="off"),
    )

    assert len(page.problems) == 1
    assert page.blocks[0].text == "1."
    assert page.blocks[0].metadata["ocr_skipped_reason"] == "trusted_pdf_text_marker"
    assert page.blocks[0].metadata["ocr_backend"] == "pdf_text_marker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_recognition_speed_quality.py::test_pdf_text_marker_blocks_skip_backend_ocr -q`

Expected: FAIL because the fake backend raises before the skip path exists.

- [ ] **Step 3: Implement minimal trusted skip**

Add a helper in `build_structured_page_json.py`:

```python
def _should_skip_ocr_for_trusted_block(block: ContentBlock, *, page_segmenter: str) -> bool:
    return (
        page_segmenter == "pdf-text-markers"
        and block.metadata.get("segmenter") == "pdf-text-markers"
        and bool(block.metadata.get("force_image_record"))
        and bool(block.metadata.get("force_problem_start"))
        and isinstance(block.metadata.get("problem_number"), int)
        and block.metadata.get("problem_number_source") == "pdf_text_marker"
        and bool((block.text or "").strip())
        and block.confidence is not None
    )
```

At the top of `_process_block`, before cropping/cache lookup, set metadata and return:

```python
if _should_skip_ocr_for_trusted_block(block, page_segmenter=page_segmenter):
    block.metadata["ocr_backend"] = "pdf_text_marker"
    block.metadata["ocr_skipped"] = True
    block.metadata["ocr_skipped_reason"] = "trusted_pdf_text_marker"
    block.metadata["ocr_empty_text"] = False
    block.metadata["ocr_text_length"] = len((block.text or "").strip())
    block.metadata["ocr_line_count"] = len(block.ocr_lines)
    return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test_recognition_speed_quality.py::test_pdf_text_marker_blocks_skip_backend_ocr -q`

Expected: PASS.

## Task 2: Stable OCR Cache Key With Legacy Fallback

**Files:**
- Modify: `pipeline_cache.py`
- Modify: `build_structured_page_json.py`
- Modify: `test_recognition_speed_quality.py`

- [ ] **Step 1: Write failing cache tests**

```python
def test_ocr_cache_uses_stable_index_and_can_fallback_to_legacy_image_hash(tmp_path):
    from PIL import Image

    from ocr_backend import OCRResult
    from pipeline_cache import PipelineCache

    cache = PipelineCache(tmp_path / "cache")
    image = Image.new("RGB", (40, 20), "white")
    result = OCRResult(text="cached", confidence=0.9, backend_name="gemini")

    identity = {"version": "ocr_block_identity_v1", "source": {"fingerprint": "source-a"}, "block": {"id": "block-1"}}
    payload_path = cache.save_ocr_result(image, result, backend_name="gemini", cache_identity=identity)
    assert "ocr" in payload_path.parts
    assert "ocr_index" not in payload_path.parts

    changed_crop = Image.new("RGB", (41, 20), "white")
    loaded = cache.load_ocr_result(changed_crop, backend_name="gemini", cache_identity=identity)
    assert loaded is not None
    assert loaded.text == "cached"
    assert loaded.metadata["cache_key_kind"] == "stable_index"

    legacy_cache = PipelineCache(tmp_path / "legacy")
    legacy_cache.save_ocr_result(image, result, backend_name="gemini")
    legacy_loaded = legacy_cache.load_ocr_result(image, backend_name="gemini", cache_identity={"version": "missing"})
    assert legacy_loaded is not None
    assert legacy_loaded.text == "cached"
```

- [ ] **Step 2: Run cache test to verify it fails**

Run: `pytest test_recognition_speed_quality.py::test_ocr_cache_uses_stable_index_and_can_fallback_to_legacy_image_hash -q`

Expected: FAIL because `cache_identity` is not accepted.

- [ ] **Step 3: Implement optional stable index**

In `pipeline_cache.py`:

```python
from collections.abc import Mapping

OCRCacheIdentity = Mapping[str, Any]

def _stable_ocr_key(identity: OCRCacheIdentity) -> str:
    return _sha1_text(json.dumps(identity, ensure_ascii=False, sort_keys=True))

def _ocr_cache_path(self, image: Image.Image, backend_name: str) -> Path:
    image_key = _image_hash(image)
    return self.root_dir / "ocr" / _safe_slug(backend_name) / f"{image_key}.json"

def _ocr_stable_index_path(self, *, backend_name: str, cache_identity: OCRCacheIdentity) -> Path:
    return self.root_dir / "ocr_index" / _safe_slug(backend_name) / f"{_stable_ocr_key(cache_identity)}.json"
```

Update load/save signatures to accept `cache_identity: OCRCacheIdentity | None = None`. `save_ocr_result` keeps writing the existing image-hash payload and writes an index pointer when identity is present. `load_ocr_result` checks the index first and then falls back to the existing image-hash path.

- [ ] **Step 4: Add pipeline stable-key helper**

In `build_structured_page_json.py`, add a helper that builds a deterministic key from `PreparedPage` and `ContentBlock` using normalized path stat, page id, page number, page size, and bbox rounded to 4 px.

- [ ] **Step 5: Use stable key for primary and escalated OCR cache calls**

Pass `cache_identity=_build_ocr_cache_identity(...)` to primary backend cache calls and to escalated cache calls. The backend namespace keeps primary and escalated cache entries separate.

- [ ] **Step 6: Run cache test**

Run: `pytest test_recognition_speed_quality.py::test_ocr_cache_uses_stable_index_and_can_fallback_to_legacy_image_hash -q`

Expected: PASS.

## Task 3: Adaptive Block Worker Count And Timing Metadata

**Files:**
- Modify: `build_structured_page_json.py`
- Modify: `test_recognition_speed_quality.py`

- [ ] **Step 1: Write failing worker/timing tests**

```python
def test_block_worker_count_defaults_and_env_override(monkeypatch):
    import build_structured_page_json as pipeline

    monkeypatch.delenv("EDB_RECOGNITION_BLOCK_WORKERS", raising=False)
    assert pipeline.resolve_block_ocr_worker_count(5, ocr_mode="none", backend_name="none") == 1
    assert pipeline.resolve_block_ocr_worker_count(10, ocr_mode="gemini", backend_name="gemini") == 3

    monkeypatch.setenv("EDB_RECOGNITION_BLOCK_WORKERS", "6")
    assert pipeline.resolve_block_ocr_worker_count(10, ocr_mode="gemini", backend_name="gemini") == 6
```

```python
def test_page_model_records_recognition_timing_metadata(monkeypatch, tmp_path):
    from PIL import Image

    import build_structured_page_json as pipeline
    from ocr_backend import OCRResult
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage
    from structured_schema import Subject

    class EmptyBackend:
        engine_name = "none"

        def recognize(self, image):
            return OCRResult(text="", confidence=None, backend_name="none")

    source = tmp_path / "page.png"
    image = Image.new("RGB", (240, 160), "white")
    image.save(source)
    prepared = PreparedPage(
        page_id="image-page-001",
        source_path=str(source),
        page_number=1,
        image=image,
        original_size=image.size,
        metadata={},
    )
    monkeypatch.setattr(pipeline, "create_ocr_backend", lambda _mode: EmptyBackend())

    page = pipeline.build_page_model(
        prepared,
        subject=Subject.UNKNOWN,
        ocr_mode="none",
        ai_config=build_ai_fallback_config(mode="off"),
    )

    timing = page.metadata["recognition_timing_ms"]
    assert timing["segmentation"] >= 0
    assert timing["block_ocr"] >= 0
    assert timing["total_before_repair"] >= timing["segmentation"]
    assert page.metadata["ocr_block_worker_count"] == 1
    assert "ocr_eligible_block_count" in page.metadata
    assert "ocr_skipped_block_count" in page.metadata
```

- [ ] **Step 2: Run worker/timing tests to verify they fail**

Run: `pytest test_recognition_speed_quality.py::test_block_worker_count_defaults_and_env_override test_recognition_speed_quality.py::test_page_model_records_recognition_timing_metadata -q`

Expected: FAIL because resolver and metadata do not exist.

- [ ] **Step 3: Implement block worker resolver**

Add `resolve_block_ocr_worker_count(item_count, *, ocr_mode, backend_name)` in `build_structured_page_json.py`. Respect `EDB_RECOGNITION_BLOCK_WORKERS`; default to `1` for none/noop, `3` for Gemini/network backends, and `min(8, item_count, os.cpu_count() or 2)` for local engines.

- [ ] **Step 4: Use resolver in block OCR executor**

Replace fixed `ThreadPoolExecutor(max_workers=8)` with the resolved worker count.

- [ ] **Step 5: Record timing/count metadata before repair**

Measure segmentation, block OCR, and total pre-repair elapsed time. Add count metadata based on blocks after OCR processing.

- [ ] **Step 6: Run worker/timing tests**

Run: `pytest test_recognition_speed_quality.py::test_block_worker_count_defaults_and_env_override test_recognition_speed_quality.py::test_page_model_records_recognition_timing_metadata -q`

Expected: PASS.

## Task 4: Focused Regression Suite

**Files:**
- Read: changed files from Tasks 1-3

- [ ] **Step 1: Run focused tests**

Run: `pytest test_recognition_speed_quality.py test_pdf_text_marker_segmentation.py test_page_repair.py test_app_server_retry.py -q`

Expected: PASS.

- [ ] **Step 2: Inspect diff**

Run: `git diff -- build_structured_page_json.py pipeline_cache.py test_recognition_speed_quality.py docs/superpowers/specs/2026-06-13-recognition-speed-quality-design.md docs/superpowers/plans/2026-06-13-recognition-speed-quality.md`

Expected: only recognition speed/quality changes and docs.

- [ ] **Step 3: Commit scoped changes only**

```bash
git add build_structured_page_json.py pipeline_cache.py test_recognition_speed_quality.py docs/superpowers/specs/2026-06-13-recognition-speed-quality-design.md docs/superpowers/plans/2026-06-13-recognition-speed-quality.md
git commit -m "perf: speed up recognition hot path"
```

Expected: commit succeeds without staging unrelated dirty files.
