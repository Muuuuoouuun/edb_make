# Problem Cutout Guidelines

## Goal
- Split source pages at the **problem level only**.
- Export each problem as a **background-removed cutout image**.
- Let ClassIn apply the final board background automatically after EDB upload.

## Core Rules
- Do **not** split a problem into `stem / figure / choice` unless a separate task explicitly requires it.
- Do **not** pre-render black, charcoal, or green board backgrounds into the exported problem image.
- Do keep the full problem content together:
  - problem number
  - stem text
  - diagram / figure
  - answer choices
- Do remove paper-like white backgrounds so the problem can sit naturally on the ClassIn board.

## Current Export Policy
- `problem_crops/`
  - Original rectangular problem crops for debugging and inspection.
- `problem_cutouts/`
  - PNG cutouts with transparency.
  - These are the images intended for EDB image records.
- EDB image records should prefer the cutout PNG when dark-board mode is enabled.

## Why This Direction
- Problem-level grouping is already working well for the current math samples.
- Internal problem splitting increases false splits and adds maintenance cost.
- Pre-coloring the board background is unnecessary because ClassIn already handles board appearance.
- Transparent cutouts produce cleaner results on black, charcoal, and green boards.

## Quality Bar
- One detected problem should correspond to one exported cutout.
- White page background should be mostly transparent.
- Text, geometry lines, and colored labels should remain visible after background removal.
- Problem bounds should not cut off answer choices or diagram edges.

## Preferred Workflow
1. Detect problem regions on the page.
2. Save rectangular debug crops to `problem_crops/`.
3. Convert each crop into an RGBA cutout.
4. Save cutouts to `problem_cutouts/`.
5. Pack those cutouts into EDB image records.
6. Verify a few samples visually before broader use.

## Non-Goals
- Fine-grained OCR layout reconstruction inside each problem.
- Rebuilding the problem as editable text objects by default.
- Matching board color inside the exported image itself.

## Review Checklist
- Is the page split correctly into problem units?
- Does each cutout preserve the full problem?
- Is the white paper background removed?
- Does the result still read clearly on a dark preview?
- Are there any clipped formulas, labels, or answer options?

## Command Example
```powershell
python build_problem_board_edb.py "C:\path\to\input.png" --output-dir out_cutout --ocr none --record-mode image-only
```

## Notes
- Existing `board_theme` options may remain in the CLI for compatibility, but they are not the main output strategy anymore.
- The authoritative output should be the transparent problem cutout, not a board-colored render.

## Deferred Consideration
- Keep `2-stage split` as a future architecture option rather than implementing it immediately.
- Recommended future split:
  - Stage 1: problem splitting, ordering, batch handling, and rectangular crop export
  - Stage 2: background removal, white-family conversion, and final application/export
- When revisiting parsing stability work, consider this split first if parsing, recognition, conversion, and apply logic keep colliding in one flow.
