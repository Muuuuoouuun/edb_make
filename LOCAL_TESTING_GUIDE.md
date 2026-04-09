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
python build_problem_board_edb.py out_images_sample4\record_0001_img_0.jpg --output-dir local_test_output\sample_run --ocr noop --subject korean
```

Main outputs:

- `local_test_output\sample_run\pages.json`
- `local_test_output\sample_run\board_run_summary.json`
- `local_test_output\sample_run\record_0001_img_0.edb`
- `ui_prototype\prototype_data.js`

### 2. Inspect the generated EDB structure

```powershell
python inspect_edb.py .\local_test_output\sample_run\record_0001_img_0.edb
```

### 3. Open the preview prototype

Open this file in a browser:

- `ui_prototype\index.html`

The prototype will read `ui_prototype\prototype_data.js` and show the latest generated problem sequence and board preview.

## Structured JSON Only

If you want only the page analysis output without building an EDB:

```powershell
python build_structured_page_json.py out_images_sample4\record_0001_img_0.jpg --output-dir local_test_output\inspect_only --ocr noop --subject korean
```

This writes:

- `pages.json`
- `run_summary.json`

## Testing With Your Own File

Replace the sample source with your own image or PDF path:

```powershell
python build_problem_board_edb.py C:\path\to\your_file.pdf --output-dir local_test_output\my_run --ocr noop --subject unknown
```

## Current Recommended Defaults

- use `--ocr noop` first
- test with one representative input first
- open the prototype and the generated `.edb` together
- use Korean subject hint for long reading passages

## Notes

- Current pipeline is local-first and offline-capable
- OCR quality is still optional and not required for the image-based export path
- The generated `.edb` is currently image-record based for reliability
