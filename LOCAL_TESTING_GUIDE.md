# Local Testing Guide

## Do You Need An API Key?

No API key is required for the current local MVP flow.

The project can be tested fully in local mode with:

- image or PDF input
- local preprocessing
- local segmentation
- optional local OCR
- local `.edb` generation
- local preview prototype

## What You Do Need

### Core Python packages

Install the local requirements:

```powershell
python -m pip install -r requirements-local.txt
```

### Optional OCR

You can run without OCR by using `--ocr noop`.

If you want OCR later:

- `pytesseract` needs the Windows Tesseract binary installed separately
- `paddleocr` is optional and heavier

## Fastest Test Path

### 1. Build one local test EDB

This command does all of the following:

- preprocesses the source
- builds `pages.json`
- crops problem images
- places them onto the board layout
- writes a test `.edb`
- refreshes `ui_prototype/prototype_data.js`

```powershell
python build_problem_board_edb.py tmp_test_inputs\earth_input.pdf --output-dir local_test_output\sample_run --ocr noop --subject science --record-mode mixed
```

Main outputs:

- `local_test_output\sample_run\pages.json`
- `local_test_output\sample_run\board_run_summary.json`
- `local_test_output\sample_run\earth_input.edb`
- `ui_prototype\prototype_data.js`

### Record modes

`build_problem_board_edb.py` now supports two export modes:

- `--record-mode image-only`
  - one image record per placed problem crop
  - most stable fallback
- `--record-mode mixed`
  - text-capable blocks become text records when OCR confidence is high enough
  - figures, formulas, and low-confidence blocks remain image records

Recommended first comparison:

```powershell
python build_problem_board_edb.py tmp_test_inputs\earth_input.pdf --output-dir local_test_output\image_only --ocr noop --subject science --record-mode image-only
python build_problem_board_edb.py tmp_test_inputs\earth_input.pdf --output-dir local_test_output\mixed --ocr noop --subject science --record-mode mixed
```

### 2. Inspect the generated EDB structure

```powershell
python inspect_edb.py .\local_test_output\sample_run\earth_input.edb
```

For a mixed export, check whether text and image records both appear in the summary.

### 3. Open the preview prototype

Open this file in a browser:

- `ui_prototype\index.html`

The prototype will read `ui_prototype\prototype_data.js` and show the latest generated problem sequence and board preview.

## Structured JSON Only

If you want only the page analysis output without building an EDB:

```powershell
python build_structured_page_json.py tmp_test_inputs\physics_input.pdf --output-dir local_test_output\inspect_only --ocr noop --subject science
```

This writes:

- `pages.json`
- `run_summary.json`

## Testing With Your Own File

Replace the sample source with your own image or PDF path:

```powershell
python build_problem_board_edb.py C:\path\to\your_file.pdf --output-dir local_test_output\my_run --ocr noop --subject unknown --record-mode mixed
```

## Current Recommended Defaults

- use `--ocr noop` first
- test with one representative input first
- open the prototype and the generated `.edb` together
- use Korean subject hint for long reading passages

## Notes

- Current pipeline is local-first and offline-capable
- OCR quality is still optional and not required for fallback image export
- `mixed` mode is now available, but real ClassIn verification should be used before treating it as stable
