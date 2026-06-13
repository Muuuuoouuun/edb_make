# Long Passage Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect long shared passages for Korean, English, and 탐구 documents, preserve their child-question relationship, and surface EDB placement overlap risks before ClassIn handoff.

**Architecture:** Extend the existing same-page set-range grouping in `assemble_page.py` with explicit passage metadata, propagate that metadata through `build_problem_board_edb.py`, and add ClassIn preflight checks for board placement collisions. This keeps the current image-only EDB path intact while making long-passage failures visible and safer.

**Tech Stack:** Python dataclasses/models, `unittest`, PIL image checks, existing EDB placement engine.

---

### Task 1: Passage Metadata

**Files:**
- Modify: `assemble_page.py`
- Test: `test_assemble_page.py`

- [ ] **Step 1: Write the failing test**

Add a test asserting `[13~14]` creates `passage_group_id`, `passage_range`, `passage_role`, and `shared_passage_block_ids` on both child questions.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_assemble_page.TestAssemblePageKoreanEnhancements.test_set_problem_range_marks_child_question_metadata`

- [ ] **Step 3: Write minimal implementation**

When shared blocks are attached to a problem, add explicit metadata describing the shared-passage group and child-question relationship.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_assemble_page.TestAssemblePageKoreanEnhancements`

### Task 2: UI/Handoff Propagation

**Files:**
- Modify: `build_problem_board_edb.py`
- Test: `test_edb_publish_flow.py`

- [ ] **Step 1: Write the failing test**

Build a tiny page model with passage metadata and assert `build_ui_session()` exposes `passageGroupId`, `passageRange`, `passageRole`, and `sharedPassageBlockIds`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_ui_session_exposes_shared_passage_metadata`

- [ ] **Step 3: Write minimal implementation**

Propagate `ProblemEntry` passage metadata into placement summaries and UI problem payloads.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_ui_session_exposes_shared_passage_metadata`

### Task 3: ClassIn Placement Collision Preflight

**Files:**
- Modify: `build_problem_board_edb.py`
- Test: `test_edb_publish_flow.py`

- [ ] **Step 1: Write the failing test**

Create a `ui_session` with two board placements whose rendered intervals overlap and assert handoff preflight emits `board_placement_overlap`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_classin_preflight_flags_board_placement_overlap`

- [ ] **Step 3: Write minimal implementation**

Compare each problem's `startYPages` and rendered bottom (`startYPages + actualHeightPages * placementScaleRatio`) against the next problem start, adding an overlap warning if it crosses.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_edb_publish_flow.TestEdbPublishFlow.test_classin_preflight_flags_board_placement_overlap`

### Task 4: Verify And Commit

**Files:**
- Stage source, tests, scripts, and plan files only.
- Do not stage runtime outputs such as `.app_runtime/`, `_app_runtime*/`, `.DS_Store`, caches, or generated media.

- [ ] **Step 1: Run focused tests**

Run: `python3 -m unittest test_assemble_page.py test_edb_publish_flow.py`

- [ ] **Step 2: Run full verification**

Run: `python3 -m unittest discover -p 'test_*.py'`
Run: `python3 -m pytest test_recognition_speed_quality.py -q`
Run: `git diff --check`

- [ ] **Step 3: Commit**

Run: `git add` on source/test/plan files only, then `git commit -m "Improve HWP and ClassIn handoff pipeline"`.
