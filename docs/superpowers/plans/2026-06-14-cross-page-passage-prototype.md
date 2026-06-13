# Cross Page Passage Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognize long passage groups that continue onto later pages so Korean, English, and 탐구 child questions stay tied to one passage unit in the prototype.

**Architecture:** Keep single-page extraction unchanged, then add a multi-page post-processing pass in `build_problem_board_edb.py` that carries active `passage_range` metadata forward to later pages. UI session payloads expose cross-page fields so review and ClassIn handoff can distinguish one long passage from overlapping unrelated problems.

**Tech Stack:** Python dataclasses/models, existing `PageModel`/`ProblemUnit`, `unittest`, current UI session JSON shape.

---

### Task 1: Cross-Page Passage Metadata

**Files:**
- Modify: `build_problem_board_edb.py`
- Test: `test_edb_publish_flow.py`

- [ ] **Step 1: Write the failing test**

Add `test_ui_session_links_cross_page_passage_child_questions` with two pages: page 1 has problem 13 carrying `passage_range={"start": 13, "end": 16}`, and page 2 has problem 15 without passage metadata. Assert the UI session exposes the same `passageGroupId` on problem 15 plus `passageContinuesAcrossPages=True` and `passageSourcePageIds=["page-1", "page-2"]`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_ui_session_links_cross_page_passage_child_questions`

- [ ] **Step 3: Write minimal implementation**

Add `_annotate_cross_page_passage_groups(pages)` in `build_problem_board_edb.py`. Sort pages in source order, track passage ranges whose end number is still ahead, and update child problems within that range with the active passage metadata plus cross-page source page ids.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_ui_session_links_cross_page_passage_child_questions`

### Task 2: Payload Propagation And Verification

**Files:**
- Modify: `build_problem_board_edb.py`
- Test: `test_edb_publish_flow.py`

- [ ] **Step 1: Propagate payload fields**

Extend `_problem_passage_payload()` to include `passageContinuesAcrossPages`, `passageSourcePageIds`, and snake_case equivalents.

- [ ] **Step 2: Run focused tests**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_ui_session_exposes_shared_passage_metadata test_edb_publish_flow.TestEdbPublishFlow.test_ui_session_links_cross_page_passage_child_questions`

- [ ] **Step 3: Run broader regression**

Run: `python3 -m unittest test_edb_publish_flow.py test_assemble_page.py`
Run: `python3 -m pytest test_recognition_speed_quality.py -q`
Run: `git diff --check`
