# Recent branch integration — 2026-10-18

Base: master `1b609d8364ade75f6cd2b09795b6e800b726b76e` (PR #14).

## Remote branch audit

- `integration/edb-sync-20261017` (`cbc7d65`, PR #12): merge scanned passage recovery, visual-number detection, column geometry and scan regression suite. Includes `fix/passage-box-fragments` (`a86a0ff`).
- `codex/color-media-preservation-20261018` (`31493c9`, PR #13): merge dense raster color-media preservation and its tests.
- `codex/image-question-boundaries-20261017` (`248c618`): already an ancestor of master; retained.
- `ccr-94881ca6-dlrujf` (`6c38ce9`): merge blank-row trimming and Stage-2 defaults for detected questions. Preserve the newer existing Stage-2 default for page-as-is items and explicit saved processing choices. Retain the memoized rail, windowed canvas, current page-guide geometry and right inspector rather than restoring the older UI.
- `ccr-2b36ddf3-atsojm` (`28aa1d0`): reconcile with the newer review flow and state-based inspector. Retain the existing page-level review targets, scoped bulk confirmation and current settings sections; older tab-specific advanced folds and their obsolete structure assertions are superseded. Bring forward the optional unresolved-problem filter helper and behavioral tests. This merge does not claim the old UI redesign is present.
- `feat/right-panel-ia` (`9fba92f`, closed PR #4): do not reopen the closed PR or merge its old ancestry. Its requested inspector design was already selectively integrated and corrected in PR #14 (`e1dfbec`).
- `claude/ui-app-integration-qrBnX` (`d294bdc`, 2026-04-10): historical UI snapshot, outside the recent-work scope.

## Integration corrections

Both native package jobs run scan-layout, image-boundary, selective-media and raster-media regressions. Combining vertical blank-row trimming with selective media restoration requires translating media coordinates by the removed top rows; preserve that offset and distinguish trimmed/untrimmed tasks in asset deduplication. A regression renders both versions of the same source and checks the restored source pixels.

Keep the approved left original-file picker, full lesson-name editor, state-driven inspector, offline assets and current performance optimizations. Explicitly cycle Tab inside settings to prevent embedded PDF focus escape.

## Packaging

Build internal version 0.2.6 from the merged master commit on native Windows and both macOS architectures. Verify uploaded release evidence against that exact commit, version, artifact size and SHA-256. Internal packages do not publish the updater feed or attest public distribution signing, notarization, licensing approval or private-corpus evidence.
