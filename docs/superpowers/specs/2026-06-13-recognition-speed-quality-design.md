# Recognition Speed With Quality Guardrails Design

## Goal

Improve recognition speed while preserving or improving output quality for the ClassIn EDB pipeline.

The product should show usable problem boundaries quickly, avoid unnecessary AI/OCR work, and keep original image crops as the quality fallback when text confidence is low.

## Current Context

The current pipeline already has the right foundation:

- `preprocess.py` renders/normalizes source pages.
- `segment.py` detects document regions and can use PDF text-layer problem markers.
- `build_structured_page_json.py` segments pages, OCRs blocks, caches OCR results, and calls page repair.
- `pipeline_router.py` grades pages into green/yellow/red risk tiers.
- `page_repair.py` applies Gemini repair only when routing says it is needed.
- `pipeline_cache.py` caches OCR and AI repair outputs.
- The UI already has a recognition review modal and background job model.

The main speed loss now comes from doing OCR on blocks that already have trusted PDF-marker text, cache keys that are sensitive to crop-byte changes, fixed block OCR worker counts, and limited timing visibility.

## Design

### Fast Trusted Path

PDF text-marker segmentation blocks are trusted anchors. When a block is produced from `pdf_text_marker`, has `force_image_record`, and already has text/confidence, block OCR should be skipped.

The block keeps its original text, problem-number metadata, and grouping hints. Metadata records the skip reason so summaries do not confuse trusted skips with failed OCR.

### Stable OCR Cache

Keep the existing image-hash OCR cache as the canonical payload for compatibility, but add an optional stable sidecar index:

- normalized source path
- source file size and mtime
- page id/page number
- rendered page dimensions
- quantized block bbox
- backend/model namespace remains in the cache path

Reads should check the stable index first, then fall back to the legacy image-hash cache. Writes should always save the existing image-hash payload and, when stable identity is available, write an `ocr_index/<backend>/<stable-key>.json` pointer to that payload.

This improves reuse when the same page/block is recognized again while preserving compatibility for old code and old cache entries.

### Adaptive Worker Counts

Add a block OCR worker resolver:

- `none/noop`: one worker, because no real work is needed
- Gemini/network OCR: low default concurrency to avoid rate-limit thrash
- local OCR: bounded CPU-oriented concurrency
- env override: `EDB_RECOGNITION_BLOCK_WORKERS`

Page-level workers remain controlled by the existing resolver.

### Performance Metadata

Each page should expose low-risk metadata:

- `recognition_timing_ms.segmentation`
- `recognition_timing_ms.block_ocr`
- `recognition_timing_ms.total_before_repair`
- `ocr_block_worker_count`
- `ocr_eligible_block_count`
- `ocr_skipped_block_count`
- `ocr_cache_hit_count`
- `ocr_cache_miss_count`
- `ocr_escalated_block_count`

CLI/API summaries can aggregate these fields in a separate summary step without requiring UI changes in this implementation.

### Quality Guardrails

The pipeline must not lower output quality:

- Never discard original crop fallback.
- Never replace trusted PDF marker text with lower-confidence OCR text.
- Only accept Gemini escalation when it produces text and beats or fills the primary result.
- Route risky pages to AI repair using existing red/yellow decisions.

## Non-Goals

- No large UI rewrite.
- No model/provider switch.
- No removal of the existing image-hash cache.
- No forced AI image reconstruction during initial recognition.

## Testing

Add focused tests for:

- trusted PDF marker blocks skip OCR and preserve grouping text
- stable OCR cache key reads/writes and legacy fallback compatibility
- block worker resolver defaults and env override
- page metadata includes timing/count fields

## Rollout

1. Add tests first.
2. Implement trusted OCR skip and adaptive block workers.
3. Implement optional stable OCR cache key.
4. Add page-level recognition timing metadata.
5. Run focused tests, then a broader safe subset.
