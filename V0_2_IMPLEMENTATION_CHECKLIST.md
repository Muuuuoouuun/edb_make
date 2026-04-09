# v0.2 Single-Page Local CLI Checklist

## Goal
- Deliver a local offline CLI that takes one image or one PDF page input
- Produce:
  - normalized page assets
  - cropped block assets
  - structured `PageModel` JSON
  - one testable single-page `.edb`
- Keep the flow local-first and Windows-friendly

## Definition Of Done
- One local source file can be processed from CLI with a single command
- The run writes `pages.json`, cropped assets, logs, and one output `.edb`
- The generated `.edb` opens in ClassIn
- The page is readable even when OCR is weak because image fallback remains available

## First Cleanup Before New Features

### Consolidate duplicate or conflicting definitions
- `preprocess.py`
  - choose one `prepare_source_pages()` shape and remove the duplicate definition
  - keep one clear return model for downstream consumers
- `segment.py`
  - remove duplicate `blocks_from_page()` definitions
  - remove duplicate `crop_block_image()` definitions
  - decide whether segmentation should consume `PreparedPage` or image path as the primary interface
- `ocr_backend.py`
  - keep one factory API only
  - choose one naming convention for `NoOpOCRBackend` vs `NoOcrBackend`
  - keep one `create_ocr_backend()` or `build_ocr_backend()`, not both
- CLI naming
  - `build_structured_page_json.py` is currently the real entrypoint candidate
  - decide whether to keep that name or rename to `build_page_model.py`
  - avoid having both names active for the same role

## File-By-File Work

### `structured_schema.py`
Target:
Lock the minimum schema that all downstream steps can rely on

Checklist:
- freeze required `ContentBlock` fields for v0.2
- confirm OCR line serialization shape stays stable in JSON
- confirm `AssetRef` fields are enough for crop-image fallback
- keep `classify_text_block()` simple and deterministic
- add a schema version string in page metadata for future migration safety

Done when:
- `pages.json` can be regenerated repeatedly with the same structure
- OCR, segmentation, and writer code no longer need ad hoc extra fields

### `preprocess.py`
Target:
Turn any single source into a normalized image page for downstream work

Checklist:
- keep one public API for page preparation
- verify PDF rendering output directory structure
- verify image normalization output directory structure
- make perspective correction optional and off by default for rendered PDFs
- keep deskew and margin crop flags explicit in metadata
- add stable filenames so repeated runs are easy to compare
- fail clearly when `PyMuPDF`, `opencv-python`, or `numpy` are missing

Done when:
- a single input always yields a predictable normalized page artifact
- downstream code does not need to guess where prepared images live

### `segment.py`
Target:
Produce usable text/image block candidates from one normalized page

Checklist:
- keep one segmentation entrypoint
- keep one crop helper API
- tune merge logic so neighboring text lines do not collapse into one giant block too easily
- add a simple decorative/noise rejection rule
- preserve `fill_ratio` and any segmentation hints in `block.metadata`
- verify fallback behavior when OpenCV or NumPy are unavailable

Done when:
- one page produces a reasonable first-pass block list
- block crops are saved and traceable by `block_id`

### `ocr_backend.py`
Target:
Provide one clean OCR abstraction with local-first fallback

Checklist:
- unify the backend factory function
- normalize return metadata across PaddleOCR, Tesseract, and noop
- map OCR lines into `structured_schema.OcrLine` consistently
- keep `noop` behavior explicit so downstream fallback rules are reliable
- expose backend name, confidence, and availability in metadata
- keep subject or language configuration injectable later without changing the API again

Done when:
- the pipeline can switch OCR backends without changing page assembly logic
- low-confidence and noop cases are easy to detect

### `assemble_page.py`
Target:
Turn raw blocks into problem-aware page structure

Checklist:
- keep reading order relabeling deterministic
- verify problem start detection against numbered questions and section headers
- verify choice grouping on Korean exam-style markers
- keep figure/image blocks attached to the active problem unit
- avoid promoting every short text block into a new problem title

Done when:
- one page summary produces stable problem grouping
- common false splits are reduced

### `build_structured_page_json.py`
Target:
Be the main v0.2 CLI entrypoint unless renamed

Checklist:
- define the final CLI contract for source path, output dir, subject hint, OCR backend, and DPI
- add a small run summary at the end
- write outputs into a predictable folder layout:
  - `preprocess/`
  - `assets/`
  - `pages.json`
  - `run_summary.json` or `run_summary.txt`
- log which backend was used and how many blocks were converted to image fallback
- catch per-page failures cleanly and continue only if that behavior is intentional

Done when:
- another developer can run one command and inspect all artifacts easily

### `edb_builder.py`
Target:
Extend the existing controlled writer into a single-page mixed writer path

Checklist:
- keep controlled sample builders intact for regression
- add a mapping layer from `PageModel` blocks to text and image records
- use text records only for blocks with acceptable OCR confidence
- use image records for figures, diagrams, and low-confidence formula/text fallback
- keep coordinate normalization deterministic
- save at least one single-page `.edb` from real pipeline output

Done when:
- `PageModel` output can be converted into one ClassIn-openable `.edb`

### New file: `build_single_page_edb.py` or equivalent
Target:
Wrap the structured pipeline and writer into one local smoke-test command

Checklist:
- read one source file
- build normalized assets and `PageModel`
- call the mixed writer
- save output `.edb`
- print where artifacts were written

Done when:
- one command covers the full v0.2 happy path

## Suggested Task Order
1. Resolve duplicate functions and naming conflicts first
2. Freeze the `structured_schema.py` surface area
3. Stabilize `preprocess.py`
4. Stabilize `segment.py`
5. Stabilize `ocr_backend.py`
6. Verify `assemble_page.py` grouping on sample pages
7. Finalize `build_structured_page_json.py` as the inspection CLI
8. Add the single-page mixed `.edb` path on top of that output

## Acceptance Checks

### CLI smoke check
- Input:
  - one sample image
- Output:
  - normalized page image
  - block crops
  - `pages.json`
  - one `.edb`

### JSON quality check
- every block has a stable `block_id`
- every non-empty OCR block has text or explicit fallback reason
- every image fallback block has an asset path

### EDB quality check
- file opens in ClassIn
- page order is correct
- text blocks are visible
- image fallback blocks are visible
- there is no obviously corrupted layout

## Nice-To-Have, But Not Required For v0.2
- subject-specific OCR tuning
- confidence heatmap or debug overlay
- block-level QA screenshots
- template-based board placement
- multi-page PDF handling beyond smoke testing

## Recommended Next Commit Theme
- `cleanup pipeline interfaces`
- `stabilize single-page structured cli`
- `add single-page mixed edb export`
