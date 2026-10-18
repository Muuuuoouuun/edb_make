# Main integration — 2026-10-19

Repository default branch: `master`. Starting main: `a1b5a117ea4e0da9c7f139bec173f6e869737e3d` (PR #18). Current remote work: PR #17, `52b3cfc8de3fa61103e599424fda492ce4a20fe9`.

## Scope and remote branch audit

- Integrate PR #17's crop/stitch source-coordinate preservation with the latest compact inspector, original crop workspace and native PDF focus fix from PR #18.
- Replace the separate figure-region editor with inline original-color switches in attachment, selected-problem and whole-session application controls. Preserve the old manual-region session format. Turning the new switch off keeps figure shading in grayscale; it does not flatten the figure into a single chalk patch. Store the same policy for preview, session reload and final export.
- `codex/color-media-preservation-20261018` at `34fbdf4` is already contained in the PR #17 branch.
- `feat/right-panel-ia` at `9fba92f` is the historical closed PR #4. Its inspector work was selectively reconciled in PR #14 and subsequently revised by PR #18; retain the latest implementation as requested. See the 2026-10-18 release-integration audit.
- `claude/ui-app-integration-qrBnX` at `d294bdc` is the 2026-04-10 UI snapshot, superseded by the current workspace. It introduces no missing current work and is not used to replace the latest UI.

## Conflict handling

The merge of current master produced conflicts only in generated `app.bundle.js` and `board.html` cache references. The user explicitly selected regeneration from the latest sources. Authored sources merged automatically; regenerate using the current build script and preserve the latest `original-editor.css` packaging and cache checks.

## Verification

- Cover color/grayscale, crop/reset, single/bulk changes, atomic batch failure, JSON reload, attachment options and final PNG encoding in regression tests.
- Use the user-authorized Playwright fallback at local `127.0.0.1:8765`: selected and all-item switches, reload persistence, 1440×1000 and 390×844 layouts, no extra figure dialog/tab, and attachment → local image recognition with the grayscale request and saved result.
- Browser evidence and temporary scripts are outside the repository in `/workspace/shared/edb-inline-qa`. The browser server has independent runtime constants and settings; provider-backed AI is disabled only in that isolated profile.
- Real worksheet detection accuracy and native installer execution are separate from this integration.
