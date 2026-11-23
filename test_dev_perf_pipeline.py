"""Regression checks for the perf-pipeline changes.

- channel_spread / channel_max and the in-place _box_mean stay bit-identical
  to the numpy expressions they replaced.
- Disk-backed problem assets use the memory-aware worker cap (max 4).
- Conversion progress flows from the pipeline to the server to the UI hint.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

import app_server
import build_problem_board_edb as board
import image_reconstruction_backend as backend
from structured_schema import Box, Subject
from test_large_pdf_batches import make_pdf
from test_ui_placement_details import NODE_SETUP, run_node


# --- C19: channel helpers ---------------------------------------------------

def test_channel_helpers_match_axis_reductions_bit_for_bit():
    rng = np.random.default_rng(19)
    rgba = rng.integers(0, 256, size=(37, 53, 4), dtype=np.uint8)
    candidates = [
        rgba[..., :3],  # non-contiguous view of an RGBA array
        rgba[..., :3].astype(np.int16),
        rgba[..., :3].astype(np.float32),
        rgba[..., :3].astype(np.float32) / 255.0,
        rng.random((64, 41, 3), dtype=np.float32) * 255.0,
        rgba[5:20, 3:40, :3],
    ]
    for rgb in candidates:
        old_spread = rgb.max(axis=2) - rgb.min(axis=2)
        new_spread = backend.channel_spread(rgb)
        assert new_spread.dtype == old_spread.dtype
        assert np.array_equal(new_spread, old_spread)
        old_max = rgb.max(axis=2)
        new_max = backend.channel_max(rgb)
        assert new_max.dtype == old_max.dtype
        assert np.array_equal(new_max, old_max)


# --- C22: in-place _box_mean ------------------------------------------------

def _box_mean_before_inplace(values, window):
    """Verbatim copy of the previous _box_mean; the new one must match it exactly."""
    pad = window // 2
    padded = np.pad(values.astype(np.float64), pad, mode="edge")
    integral = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)))
    height, width = values.shape
    total = (
        integral[window : window + height, window : window + width]
        - integral[:height, window : window + width]
        - integral[window : window + height, :width]
        + integral[:height, :width]
    )
    return (total / float(window * window)).astype(np.float32)


@pytest.mark.parametrize("window", [7, 9])
def test_box_mean_matches_previous_implementation(window):
    rng = np.random.default_rng(22)
    luminance = rng.random((120, 90), dtype=np.float32) * 255.0
    inputs = [
        rng.random((37, 53), dtype=np.float32) * 255.0,
        luminance,
        luminance * luminance,
        (rng.random((120, 90)) > 0.6).astype(np.float32),
        rng.random((5, 4), dtype=np.float32),  # smaller than the window
    ]
    for values in inputs:
        result = backend._box_mean(values, window)
        assert result.dtype == np.float32
        assert np.array_equal(result, _box_mean_before_inplace(values, window))


# --- C18: problem asset worker cap -----------------------------------------

class _RecordingPool:
    created: list[int] = []

    def __init__(self, max_workers):
        _RecordingPool.created.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def map(self, fn, items):
        return [fn(item) for item in items]


def _asset_worker_count(tmp_path, source_path, *, available, env_workers=""):
    tasks = [
        board._ProblemAssetTask(
            source_image=None,
            bounds=Box(0, index * 10, 10, 10),
            crop_path=tmp_path / f"crop-{index}.png",
            board_render_path=tmp_path / f"board-{index}.png",
            chalk_color=(255, 255, 255),
            source_image_path=str(source_path) if source_path else None,
        )
        for index in range(6)
    ]
    if source_path is None:
        for task in tasks:
            task.source_image = Image.new("RGB", (8, 8))
    _RecordingPool.created = []
    with (
        patch.object(board.os, "cpu_count", return_value=8),
        patch.object(board, "_available_image_record_memory_bytes", return_value=available),
        patch.object(board.concurrent.futures, "ThreadPoolExecutor", _RecordingPool),
        patch.object(board, "_render_problem_asset", return_value=(1, 1)),
        patch.dict(os.environ, {"EDB_PROBLEM_ASSET_WORKERS": env_workers, "EDB_IMAGE_RECORD_WORKERS": ""}),
    ):
        assert board._render_problem_assets(tasks) == [(1, 1)] * 6
    return _RecordingPool.created[0] if _RecordingPool.created else 1


def test_disk_backed_problem_assets_use_memory_aware_worker_cap(tmp_path):
    page = tmp_path / "page.png"
    Image.new("L", (1000, 1000), 255).save(page)  # 1 Mpx -> ~96 MB per worker
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    gib = 1024 ** 3

    assert _asset_worker_count(tmp_path, page, available=16 * gib) == 4  # resolver ceiling, was 2
    assert _asset_worker_count(tmp_path, page, available=int(2.3 * gib)) == 3  # 0.3 GiB headroom / 96 MB
    assert _asset_worker_count(tmp_path, page, available=int(2.1 * gib)) == 2  # tight memory: old cap, not 1
    assert _asset_worker_count(tmp_path, broken, available=16 * gib) == 2  # unknown size keeps old cap
    assert _asset_worker_count(tmp_path, page, available=16 * gib, env_workers="1") == 1
    assert _asset_worker_count(tmp_path, None, available=16 * gib) == 6  # in-memory pages unchanged


# --- C6: conversion progress ------------------------------------------------

def test_build_pages_reports_each_segmented_page_and_ignores_broken_callbacks(tmp_path):
    source = tmp_path / "exam.pdf"
    make_pdf(source, 12, badges=True)
    kwargs = dict(subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None, pdf_dpi=72,
                  detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    calls = []
    _prepared, models = board.build_pages(source, progress=lambda *args: calls.append(args), **kwargs)
    assert [stage for stage, _done, _total in calls] == ["segment"] * 13
    assert [done for _stage, done, _total in calls] == list(range(13))  # 0/12 .. 12/12 across 2 batches
    assert {total for _stage, _done, total in calls} == {12}

    def broken(*_args):
        raise RuntimeError("display only")

    _prepared, broken_models = board.build_pages(source, progress=broken, **kwargs)
    _prepared, plain_models = board.build_pages(source, **kwargs)
    assert [m.page_id for m in broken_models] == [m.page_id for m in plain_models] == [m.page_id for m in models]


def test_run_problem_export_sums_pages_across_files_in_stage_order(tmp_path):
    first, second = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(first, 3, badges=True)
    make_pdf(second, 2, badges=True)
    calls = []
    board.run_problem_export(
        [first, second], output_dir=tmp_path / "out", ocr="none", pdf_dpi=72, export_edb=True,
        progress=lambda *args: calls.append(args),
    )
    stages = [stage for stage, _done, _total in calls]
    assert stages[0] == "read"
    assert stages[-3:] == ["problem_assets", "records", "edb_write"]
    segment = [(done, total) for stage, done, total in calls if stage == "segment"]
    assert segment[-1] == (5, 5)
    assert [done for done, _total in segment] == sorted(done for done, _total in segment)


def _export_handler(root, payload):
    handler = object.__new__(app_server.AppRequestHandler)
    handler._current_session_state = lambda: (None, None)
    handler._read_json_body = lambda: payload
    handler._resolve_source_paths = lambda _payload, **_kwargs: [root / "source.png"]
    handler._resolve_preview_output_dir = lambda _payload, _sources: root / "preview"
    responses = []
    handler._send_json = lambda body, **kwargs: responses.append(body)
    return handler, responses


def _get_progress(progress_id):
    replies = []
    handler = SimpleNamespace(path=f"/api/export/progress?id={progress_id}",
                              _rewrite_legacy_ui_asset_path=lambda _path: False, _send_json=replies.append)
    app_server.AppRequestHandler.do_GET(handler)
    return replies[0]


def test_export_progress_is_served_while_running_and_cleared_after(tmp_path):
    seen = {}

    def fake_export(_source, **kwargs):
        seen["has_progress"] = "progress" in kwargs
        if "progress" in kwargs:
            kwargs["progress"]("segment", 3, 24)
            seen["mid_run"] = _get_progress("job-1")
        raise RuntimeError("stop after progress")

    base = {"preview": True, "exportEdb": False, "ocr": "none"}
    with patch.object(app_server, "run_problem_export", side_effect=fake_export):
        handler, responses = _export_handler(tmp_path, {**base, "progressId": "job-1"})
        handler._handle_export()
        assert responses and responses[-1]["ok"] is False
        assert seen == {"has_progress": True, "mid_run": {"ok": True, "stage": "segment", "done": 3, "total": 24}}
        assert _get_progress("job-1") == {"ok": True}  # cleared even though the export failed
        assert app_server._EXPORT_PROGRESS == {}

        seen.clear()
        handler, _responses = _export_handler(tmp_path / "second", base)
        handler._handle_export()
        assert seen == {"has_progress": False}  # older callers never receive the new kwarg

    assert _get_progress("") == {"ok": True}


def test_ui_turns_progress_into_teacher_hint_and_stops_polling():
    run_node(NODE_SETUP + r"""
    process.exitCode = 1;  // only a run that reaches the end passes
    (async () => {
      const timers = [];
      const progressFetches = [];
      let exportBody = null;
      let resolveExport;
      const sandbox = {
        console, encodeURIComponent, Math, Number, Date, JSON, Object, Promise,
        setInterval: (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length - 1; },
        clearInterval: id => { timers[id].cleared = true; },
        fetch: async (url, init) => {
          if (url.startsWith('/api/export/progress')) {
            progressFetches.push(url);
            if (progressFetches.length === 1) throw new Error('offline');
            return { json: async () => ({ ok: true, stage: 'segment', done: 3, total: 24 }) };
          }
          exportBody = JSON.parse(init.body);
          return new Promise(resolve => { resolveExport = resolve; });
        },
        normalizeInputIntent: value => value, normalizeContentTarget: value => value || 'all',
        INPUT_INTENT_BY_VALUE: { 'multi-problem': { exportMode: 'question' } }, DEFAULT_INPUT_INTENT: 'multi-problem',
        assertExportPayloadFits() {}, uploadSourceFiles: async () => [{ path: 'a.pdf' }],
        edbFileNameFromSessionName: () => 'a.edb', withExpectedSessionRevision: payload => payload,
        isDocumentLikeFile: () => true, DEFAULT_RECOGNITION_MAX_DIMENSION: 4096, AI_FALLBACK_OFF: { enabled: false },
        readJsonResponse: async resp => resp.body, sessionResponseRevisionIsStale: () => false,
        captureSessionRevision() {}, operationErrorFromResponse: () => new Error('failed'),
      };
      vm.createContext(sandbox);
      vm.runInContext(section('const EXPORT_STAGE_HINTS =', 'async function postPublishPlan(')
        + '\nglobalThis.exportProgressHint = exportProgressHint; globalThis.postExport = postExport;', sandbox);

      const hint = sandbox.exportProgressHint;
      assert.equal(hint({ stage: 'segment', done: 3, total: 24 }), '3/24쪽 문항 찾는 중');
      assert.equal(hint({ stage: 'segment', done: 30, total: 24 }), '24/24쪽 문항 찾는 중');
      assert.equal(hint({ stage: 'segment', done: 0, total: 0 }), '문항 찾는 중');
      assert.equal(hint({ stage: 'problem_assets', done: 0, total: 0 }), '칠판용 이미지 만드는 중');
      assert.equal(hint({ stage: 'edb_write' }), 'EDB 파일 만드는 중');
      assert.equal(hint({ stage: 'unknown' }), '');
      assert.equal(hint(null), '');

      const updates = [];
      const pending = sandbox.postExport([{ name: 'a.pdf' }], null, 'multi-problem', { onProgress: p => updates.push(p) });
      await new Promise(resolve => setImmediate(resolve));
      assert.match(exportBody.progressId, /^export-/);
      assert.equal(timers.length, 1);
      assert.equal(timers[0].ms, 1500);
      await timers[0].fn();  // a failed poll is ignored
      await timers[0].fn();
      assert.deepEqual(updates.map(p => p.done), [3]);
      assert.ok(progressFetches[0].endsWith(encodeURIComponent(exportBody.progressId)));
      resolveExport({ ok: true, body: { ok: true, session: { id: 's' } } });
      assert.deepEqual(await pending, { id: 's' });
      assert.equal(timers[0].cleared, true, 'polling stops once the export answers');
      await timers[0].fn();
      assert.equal(updates.length, 1, 'a late poll never writes after the stop');

      exportBody = null;
      const plain = sandbox.postExport([{ name: 'a.pdf' }], null, 'multi-problem', {});
      await new Promise(resolve => setImmediate(resolve));
      assert.equal('progressId' in exportBody, false);
      assert.equal(timers.length, 1, 'no poller without onProgress');
      resolveExport({ ok: true, body: { ok: true, session: {} } });
      await plain;
      process.exitCode = 0;
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)
