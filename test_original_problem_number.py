"""Original printed numbers must not be confused with board order or guesses."""
from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import app_server
import build_problem_board_edb as board
from layout_template_schema import LayoutTemplate
from structured_schema import PageModel, ProblemUnit, Subject


ROOT = Path(__file__).resolve().parent


class TestOriginalProblemNumber(unittest.TestCase):
    def test_labels_distinguish_read_inferred_unknown_and_passage(self):
        subprocess.run(["node", "-e", r"""
const assert = require('assert');
const {originalProblemNumberInfo: info, problemDisplayName: name, reorderItemsForDrop: reorder} = require('./ui_prototype/reorder.js');
for (const source of ['manual', 'ocr_top_left', 'ocr_line', 'ocr_internal_line', 'text_prefix', 'text_stem', 'pdf_text_marker', 'hwp_text_snippet']) {
  const item = {id:'a', name:'QA 1', problemNumber:12, problemNumberSource:source};
  assert.equal(info(item).label, '원문 12번');
  assert.equal(name(item, 0), '원문 12번');
  assert.equal(name(item, 9), '원문 12번', 'reordering must not renumber the original');
}
assert.equal(info({problem_number:'12', metadata:{problem_number_source:'ocr_top_left'}}).label, '원문 12번');
assert.equal(info({problemNumber:12, problemNumberSource:'inferred_sequence'}).label, '추정 12번');
assert.equal(info({problemNumber:12, problemNumberSource:'inferred_before_next_number'}).status, 'inferred');
assert.equal(info({problemNumber:1, problemNumberSource:'user_intent'}).label, '번호 미확인');
assert.equal(info({problemNumber:1, problemNumberSource:'user_intent'}).number, null);
assert.equal(info({problemNumber:12}).label, '번호 미확인');
assert.equal(info({name:'문항 12'}).label, '번호 미확인', 'a generated title is not OCR evidence');
assert.equal(info({problemNumber:12,problemNumberSource:'manual',passageRole:'passage_fragment'}).label, '공통 지문');
for (const raw of [null, undefined, '', 0, -1, 1.5, NaN, Infinity, true, {}, '12번', '1e3']) {
  assert.equal(info({problemNumber:raw,problemNumberSource:'manual'}).status, 'unknown', String(raw));
}
assert.equal(name({name:'자료 (위)',problemNumber:12,problemNumberSource:'manual'},0),'원문 12번 · 위쪽');
const first = {id:'a',problemNumber:12,problemNumberSource:'manual'};
const second = {id:'b',problemNumber:12,problemNumberSource:'ocr_line'};
const changed = reorder([first,second], 'a', 'b', 'after');
assert.deepEqual(changed.map(x => x.id), ['b','a']);
assert.deepEqual(changed.map(x => info(x).label), ['원문 12번','원문 12번'], 'duplicate numbers are allowed across sources');
"""], cwd=ROOT, check=True)

    def test_manual_edit_is_metadata_only_and_can_clear(self):
        session = {"problems": [{"id": "a", "title": "자료", "problemNumber": 4,
                                 "imagePath": "unchanged.png", "startYPages": 2.4,
                                 "metadata": {"problem_number": 4, "problem_number_source": "inferred_sequence"}},
                                {"id": "b", "problemNumber": 12}], "pages": []}
        before = copy.deepcopy(session)
        with mock.patch.object(app_server, "_problems_to_entries", side_effect=AssertionError("no image pipeline")):
            result = app_server._mutate_problem_number(session, "a", "0012")
        self.assertEqual(["a", "b"], [item["id"] for item in result["problems"]])
        problem = result["problems"][0]
        self.assertEqual(12, problem["problemNumber"])
        self.assertEqual("manual", problem["problemNumberSource"])
        self.assertEqual("manual", problem["metadata"]["problem_number_source"])
        self.assertEqual(before["problems"][1], result["problems"][1])
        for key in ["title", "imagePath", "startYPages"]:
            self.assertEqual(before["problems"][0][key], problem[key])
        app_server._mutate_problem_number(session, "a", None)
        self.assertIsNone(problem["problemNumber"])
        self.assertIsNone(problem["metadata"]["problem_number"])

    def test_invalid_number_or_missing_item_leaves_session_unchanged(self):
        for value in [True, 0, -3, 1.5, "1e3", "12번", "1000000", {}, [], "１２"]:
            with self.subTest(value=value):
                session = {"problems": [{"id": "a", "problemNumber": 4}]}
                before = copy.deepcopy(session)
                with self.assertRaises(ValueError):
                    app_server._mutate_problem_number(session, "a", value)
                self.assertEqual(before, session)
        with self.assertRaises(ValueError):
            app_server._mutate_problem_number({"problems": []}, "missing", 12)

    def test_number_source_survives_publish_and_split_metadata(self):
        for source in ["manual", "inferred_sequence", "ocr_top_left"]:
            with self.subTest(source=source):
                prior = {"id": "a", "problemNumber": 12, "metadata": {"problem_number_source": source}}
                published = {"problemNumber": 12, "problemNumberSource": ""}
                app_server._copy_publish_problem_metadata(published, prior)
                self.assertEqual(source, published["problemNumberSource"])
                split = app_server._problem_skeleton_from_parent(prior)
                self.assertEqual(12, split["problemNumber"])
                self.assertEqual(source, split["problemNumberSource"])

    def test_new_session_retains_number_recognition_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "problem.png"
            Image.new("RGB", (400, 500), "white").save(image)
            entries = app_server._problems_to_entries([{
                "id": "a", "title": "자료", "problemNumber": 12,
                "imagePath": image.as_uri(), "boardRenderPath": image.as_uri(),
                "sourcePageId": "page-1", "processingStep": "s1",
                "bbox": {"left": 0, "top": 0, "width": 400, "height": 500},
            }])
            _, placements = board.build_image_only_records(entries, LayoutTemplate(name="number"), dark_board=False)
            page = PageModel(page_id="page-1", width_px=400, height_px=500, subject=Subject.UNKNOWN,
                             problems=[ProblemUnit(unit_id="a", title="자료", subject=Subject.UNKNOWN,
                                                   metadata={"problem_number": 12, "problem_number_source": "ocr_top_left"})])
            session = board.build_ui_session([], placements, root, None, [], record_mode="image-only", pages=[page])
            self.assertEqual(12, session["problems"][0]["problemNumber"])
            self.assertEqual("ocr_top_left", session["problems"][0]["problemNumberSource"])

    def test_mutation_route_commits_and_rejects_missing_value(self):
        class Server:
            def __init__(self):
                self.latest_session = {"problems": [{"id": "a", "problemNumber": 4}], "pages": []}
                self.allowed_files = set()
            def session_snapshot_with_revision(self):
                return copy.deepcopy(self.latest_session), 1
            def session_epoch(self):
                return "test-epoch"
            def remember_session_if_current(self, expected, updated, *, expected_revision=None):
                self.latest_session = copy.deepcopy(updated)
                return 2

        for args, expected_ok in [({"problemNumber": 12}, True), ({"problemNumber": None}, True), ({}, False)]:
            with self.subTest(args=args):
                handler = object.__new__(app_server.AppRequestHandler)
                handler.server = Server()
                handler._read_json_body = lambda: {"action": "problem-number", "problemId": "a",
                                                   "expectedSessionRevision": 1, "expectedSessionEpoch": "test-epoch", **args}
                responses = []
                handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
                handler._handle_session_mutate()
                payload, kwargs = responses[0]
                self.assertEqual(expected_ok, payload["ok"])
                if expected_ok:
                    self.assertEqual(args["problemNumber"], payload["session"]["problems"][0]["problemNumber"])
                    self.assertEqual(2, payload["sessionRevision"])
                else:
                    self.assertEqual(400, kwargs["status"])
                    self.assertEqual(4, handler.server.latest_session["problems"][0]["problemNumber"])


if __name__ == "__main__":
    unittest.main()
