# EDB branch integration review — 2026-10-17

## Sources and preservation

- Starting master: `e8f898c7db48cdecef29c112ab56b018e10f5feb` (PR #9 and #11 already merged).
- PR #10: `248c61809aadb28fe0d8d2f65c16f39669254c29`, open and ready for review at inspection time, superseding the previously reported `79dc1509`.
- Passage/scan work: `fix/passage-box-fragments` at `a86a0ff9031dfa4502cb9c107f5bccfb164e24fb`, 27 commits ahead of its common ancestor with master.
- Review branch: `integration/edb-sync-20261017`; draft PR #12. Its initial merge commit has both source heads as parents. Existing branches are retained; no reset, force push or branch deletion was used.
- Direct GitHub/PyPI access from the executor returns network proxy 403. Source import/publication uses the existing official GitHub connector. The locally reconstructed PR #10 tree is exactly `bd3eea88734df538e20c6fdd64e0a4b39bc06e48`, matching upstream. Local integration snapshots have different commit IDs from the server merge, but their trees were checked for equality.

## Semantic conflicts and proposed choice

Three conflict areas in `segment.py` cannot be resolved by taking one side wholesale:

| Area | Image branch | Passage/scan branch | Proposal in this PR |
| --- | --- | --- | --- |
| Number punctuation | Reject choice fragments using a small baseline dot | Stricter period size, baseline, density, spacing and left-margin guards | Keep the stricter shared period guard; run both image and scan regressions |
| Marker evidence | Repeated colored question numbers take precedence over black choices | Scan margin and whitespace checks reject prose-line false positives | Keep colored precedence for images; use scan checks only for scanned PDFs |
| Resolution and incomplete columns | Analyze at up to 800px; reject partially recognized column sets | Keep native scan resolution; recover unnumbered regions and single-marker pages | Select by source type; uncovered-region recovery is restricted to PDFs |

The proposed choice preserves both source-specific behaviors. An alternative is to merge only PR #10 and keep the passage branch separate until original scan/image samples can be rerun. Applying scan policy to every image upload would change image fallback and needs separate validation. This draft does not select that alternative or merge into master.

The first integration CI caught four unchanged image regressions: right-column label margins were clipped at 800/1600/2400px, and an unnumbered right column lost its independent fallback block. Column detection, visual-column tightening and crop gutters are now selected by source type as well. The image tests were retained unchanged; scan PDF column and crop improvements remain enabled.

## Other remote branches

| Branch / head | Relation to starting master | Finding / disposition |
| --- | --- | --- |
| `ccr-2b36ddf3-atsojm` / `28aa1d09` | 2 ahead, 237 behind | Review-confirmation unification and detail folds; text conflicts in guide, queue/review tests, HTML and review filters. Preserve for product-flow review. |
| `ccr-94881ca6-dlrujf` / `6c38ce94` | 2 ahead, 236 behind | Blank-row crop trim, preview placement and default processing-step changes; conflicts in export builder, publish tests, JSX and HTML. Preserve as a separate crop/processing decision. |
| `feat/right-panel-ia` / `9fba92ff` | 1 ahead, 26 behind | PR #4 is closed without merge. State-based inspector/settings dialog conflicts with current JSX/CSS/HTML. Do not reopen its design decision implicitly. |
| `claude/ui-app-integration-qrBnX` / `d294bdc1` | 1 ahead, 484 behind | Legacy UI styling; current index redirects to board.html, and styles.css no longer exists. Preserve; do not resurrect the old page. |

Conflict findings above concern available source patches, excluding generated bundles; the first branch's oversized JSX patch was not part of this patch-level conflict check. No claim is made that all older work is already incorporated.

## Validation and limits

- Exact PR #10 head: GitHub Actions run `39048336255` passed: **2,062 tests, 16 skips, 353 subtests**, locked dependencies and pip check, deterministic frontend package/digest, static synthetic quality gate.
- Integration CI is recorded by the checks and final validation summary on PR #12; do not substitute PR #10's result for the integrated head.
- Local integration: frontend regenerated and package/digest verified; Node syntax and Python compilation checks passed. 121 UI tests and a selected 40 passage/parser/assembly tests passed.
- Local 119-test pipeline subset: 116 passed and 3 image-marker failures. The same three tests fail on untouched starting master with the same missing OpenCV dependency. Local dependency installation is blocked; these results do not replace locked-environment CI.
- A newly generated synthetic image exported offline (`--ocr noop --ai-fallback off`) to an EDB with two image records. Structural inspection succeeded. This is an export smoke test, not a ClassIn acceptance test.
- Existing static synthetic-corpus gate passed (2 cases). Its ground truth and run provenance are unverified fixtures; its recorded accuracy/timing numbers are not new measurements on original documents.
- Scan regression fixtures use PyMuPDF's bundled Korean font when no system Korean font is available, avoiding silent skips on generic CI runners. Both native installer regression lists include image-boundary and scan-layout tests; native installers were not built during this task.

No original user attachment, private exam corpus, Windows/macOS installer runtime, actual ClassIn import, or paid provider repair was exercised. Historical real-exam claims in the imported passage worklog were not reproduced in this task. No official Claude/Gemini review integration was available; none was configured or invoked.

## Merge/release boundary

- App source version remains **0.2.4**. The existing draft v0.2.4 release still targets `224e615dd7531c1b715e1b557c0302afc003d01f`; its installers do not represent this integration.
- Repository installer, readiness and draft-staging workflows are manual-only; ordinary push/PR workflow performs tests and artifact upload. No installer/release dispatch was started.
- Branch listing reports master unprotected and repository rulesets are empty. Legacy branch-protection API inspection is denied to the GitHub integration (403); no rule was changed or bypassed.
- No matching Vercel project or teams were returned by the connected account. A Vercel manifest exists in the repository; external automatic production deployment on master cannot be conclusively ruled out from available access. Keep this as a reviewable draft until that configuration is verified.
- No master merge, public release/deployment, credential/signing/security-setting change, production-data change or paid AI call was performed.
