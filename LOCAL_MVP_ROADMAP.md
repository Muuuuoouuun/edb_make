# Local Offline MVP Roadmap

## Product Direction
- Primary milestone: a deployable offline local tool for Windows
- Core job: take PDF or image input and generate a ClassIn `.edb`
- First priority: validate that generated `.edb` files open and render correctly in ClassIn
- Interface priority: CLI first, minimal local UI second

## Why This Order
- The biggest risk in this project is `.edb` generation fidelity, not frontend delivery
- A web service adds deployment and infra work before the conversion pipeline is proven
- A local offline flow matches the current Python-first implementation and lowers iteration cost

## Non-Goals For MVP
- Cloud sync
- User accounts
- Server-based OCR or AI as a requirement
- Mobile app support
- Full document editor UX

## User Flow For MVP
1. User selects a PDF or image set locally
2. The tool preprocesses pages and builds structured page data
3. The tool writes a `.edb` output file and related logs
4. User opens the result in ClassIn and checks layout, ordering, and readability

## Milestones

### v0.1 Controlled Sample Writer
Goal:
Lock down minimal `.edb` writing against known-good samples

Scope:
- keep `inspect_edb.py` as the inspection baseline
- harden text-only record generation
- harden image-only record generation
- preserve reproducible sample outputs for regression checks

Done when:
- generated text-only sample opens in ClassIn
- generated image-only sample opens in ClassIn
- output matches expected structure closely enough for repeatable regression testing

Primary files:
- `inspect_edb.py`
- `edb_builder.py`

### v0.2 Single-Page Local CLI
Goal:
Convert one input image into one usable `.edb` page on a local machine

Scope:
- implement preprocessing for skew, crop, and cleanup
- implement initial segmentation for text regions and figure regions
- add OCR backend hooks with local-first behavior
- export intermediate `PageModel` JSON
- map one page into mixed text and image `.edb` records
- provide a simple CLI entrypoint

Done when:
- a single image input can produce a `.edb`
- the run produces logs plus an intermediate JSON artifact
- the result can be opened in ClassIn for visual inspection

Primary files:
- `preprocess.py`
- `segment.py`
- `ocr_backend.py`
- `structured_schema.py`
- `assemble_page.py`
- `build_page_model.py`

### v0.3 PDF Local Pipeline
Goal:
Convert a local PDF into a multi-page `.edb`

Scope:
- render PDF pages to images locally
- run per-page preprocessing and page-model assembly
- support page ordering and output folder management
- allow image fallback for complex blocks that are not safely reconstructed as text

Done when:
- a multi-page PDF produces one `.edb`
- page order is correct in ClassIn
- failures on one page are surfaced clearly in logs

Primary deliverables:
- PDF input handling
- batch page processing
- multi-page `.edb` writer behavior

### v0.4 Deployable Local Package
Goal:
Ship the local pipeline to a non-developer Windows machine

Scope:
- package the tool as a Windows executable or installer
- bundle model and runtime dependencies where practical
- standardize input, output, temp, and log directories
- define a simple support checklist for failed conversions

Done when:
- another Windows PC can run the tool without a Python setup
- the user can choose an input and receive a saved `.edb`
- logs are available for troubleshooting

Preferred packaging options:
1. `PyInstaller` single-folder package
2. lightweight installer after the package layout is stable

### v1.0 Local MVP Release
Goal:
Release a stable offline local product for real user testing

Scope:
- add a minimal GUI for drag-and-drop or file picker flow
- expose a small set of settings only
- improve recovery for OCR or segmentation failures
- document supported input quality and known limitations

Done when:
- a non-technical tester can run the tool end-to-end
- common exam-style PDFs and images complete with predictable output
- failure cases are understandable instead of silent

## Recommended Tech Shape

### Runtime
- Python 3.11+
- local-first processing only

### Conversion stack
- `PyMuPDF` for PDF rendering
- `Pillow` and optionally `OpenCV` for preprocessing
- local OCR backend
- current `.edb` writer modules for output

### Packaging
- CLI first
- local GUI second, using a thin wrapper over the CLI pipeline

## Release Gates

### Technical gate
- `.edb` opens in ClassIn
- page order is stable
- no corrupted payloads on repeated runs

### UX gate
- user can identify where outputs were saved
- error messages explain which page or step failed
- rerunning the same input is predictable

### Quality gate
- sample set passes regression checks
- at least one math-heavy, one science-heavy, and one text-heavy sample are tested

## Suggested Execution Order
1. Finish `v0.1` and treat sample parity as the hard baseline
2. Build `v0.2` as a single-page local CLI
3. Extend to `v0.3` multi-page PDF flow
4. Package as `v0.4` for Windows distribution
5. Add the thin GUI and call that `v1.0`

## After MVP
- Consider a local desktop app wrapper if users want a friendlier UI
- Consider a web product only after conversion quality is stable and remote processing has a clear benefit
- Keep AI enhancement optional so the core product remains offline-capable
