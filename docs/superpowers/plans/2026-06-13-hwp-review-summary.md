# HWP Review Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a compact review summary for HWP/PDF sessions so the prototype user can quickly see problem counts, supplemental material counts, warning count, and HWP text QA details before publishing.

**Architecture:** Add a backend session summary payload derived from existing session/page/problem metadata, then render it in the review UI without changing the export pipeline. Keep the summary deterministic and recomputable from the session so restore, publish, and export all remain consistent.

**Tech Stack:** Python `unittest` for backend behavior, existing React-in-browser `ui_prototype/app.jsx` for frontend display, existing local HTTP app server.

---

### Task 1: Backend Session Review Summary

**Files:**
- Modify: `app_server.py`
- Test: `test_app_server_retry.py`

- [ ] **Step 1: Write the failing test**

Add a test that builds a session with `problems`, `pages`, `warning_messages`, and page-level `hwp_conversion_quality`, calls the new summary helper, and asserts:

```python
summary = app_server._session_review_summary(session)
self.assertEqual(3, summary["detectedProblemCount"])
self.assertEqual(2, summary["coreProblemCount"])
self.assertEqual(1, summary["supplementalItemCount"])
self.assertEqual(1, summary["warningCount"])
self.assertEqual({"hwp5txt": 1, "rhwp": 1}, summary["hwpTextExtractors"])
self.assertEqual(45, summary["hwpTextProblemSignalCount"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/bigmac_moon/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest test_app_server_retry.TestReviewSummary.test_session_review_summary_collects_hwp_text_qa
```

Expected: FAIL because `_session_review_summary` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add `_session_review_summary(session)` in `app_server.py`; use `_session_problem_count_payload` for counts, inspect page metadata `hwp_conversion_quality`, count extractors, and keep the maximum numbered/stem HWP text count because document-level HWP quality metadata can repeat on every rendered page.

- [ ] **Step 4: Attach summary during session refresh**

Update `_refresh_session_problem_counts(session)` so it writes:

```python
session["review_summary"] = _session_review_summary(session)
session["reviewSummary"] = session["review_summary"]
```

- [ ] **Step 5: Run test to verify it passes**

Run the same unittest command and expect OK.

### Task 2: UI Review Summary Display

**Files:**
- Modify: `ui_prototype/app.jsx`

- [ ] **Step 1: Write a browser-level expectation**

Use Playwright after restoring a synthetic session with `review_summary` and assert the body contains:

```text
검수 요약
2문항 + 자료 1
HWP 텍스트
```

- [ ] **Step 2: Implement display**

In `ReviewStage`, compute `reviewSummary` from `session.review_summary || session.reviewSummary`, fall back to existing count helpers, and render a compact inline summary near the toolbar.

- [ ] **Step 3: Verify browser behavior**

Run Playwright against `http://127.0.0.1:8765/` and expect the above text with no page errors.

### Task 3: Full Verification

**Files:**
- Existing test files only

- [ ] **Step 1: Run targeted tests**

```bash
/Users/bigmac_moon/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest test_app_server_retry.TestReviewSummary test_preprocess_hwp.TestPreprocessHwp.test_hwp_pdf_converter_commands_include_rhwp_env
```

- [ ] **Step 2: Run full test suite**

```bash
/Users/bigmac_moon/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -p 'test_*.py'
```

- [ ] **Step 3: Check whitespace**

```bash
git diff --check
```

- [ ] **Step 4: Recheck real latest HWP session**

Call `/api/session/latest` and confirm latest HWP session still reports `45문항 + 자료 1` plus review summary fields.
