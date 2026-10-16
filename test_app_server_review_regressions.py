from __future__ import annotations

import copy
import io
import unittest
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

import app_server


class TestCropBoundaryValidation(unittest.TestCase):
    def test_bulk_crop_intersects_selection_with_original_page_bounds(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "page.png"
            image = Image.new("RGB", (100, 80), "white")
            # This marker is beyond the requested right edge (x=40).
            image.putpixel((50, 20), (255, 0, 0))
            image.save(source)
            session = {
                "output_dir": str(root),
                "pages": [{"id": "page-1", "sourceImagePath": str(source), "problemIds": []}],
                "problems": [],
            }

            updated = app_server._mutate_bulk_crop(
                session,
                "page-1",
                [{"left": -20, "top": -10, "width": 60, "height": 40}],
            )

            problem = updated["problems"][0]
            self.assertEqual({"left": 0.0, "top": 0.0, "width": 40.0, "height": 30.0}, problem["bbox"])
            with Image.open(app_server.decode_file_reference(problem["imagePath"])) as crop:
                self.assertEqual((40, 30), crop.size)
                self.assertEqual((255, 255, 255), crop.getpixel((39, 20)))

    def test_crop_rejects_boxes_outside_page_and_nonfinite_coordinates(self):
        boxes = [
            {"left": -50, "top": 0, "width": 20, "height": 20},
            {"left": 150, "top": 0, "width": 20, "height": 20},
            {"left": 0, "top": 90, "width": 20, "height": 20},
            {"left": float("nan"), "top": 0, "width": 20, "height": 20},
            {"left": 0, "top": 0, "width": float("inf"), "height": 20},
        ]
        for box in boxes:
            with self.subTest(box=box):
                with self.assertRaises(ValueError):
                    app_server._coerce_crop_box(box, image_width=100, image_height=80)


class TestRequestBodyValidation(unittest.TestCase):
    def test_restore_returns_bad_request_for_nonobject_json_and_invalid_utf8(self):
        for raw_body in (b"[]", b"null", b"123", b'"text"', b'{"session":"\xff"}'):
            with self.subTest(raw_body=raw_body):
                handler = object.__new__(app_server.AppRequestHandler)
                handler.headers = {"Content-Length": str(len(raw_body))}
                handler.rfile = io.BytesIO(raw_body)
                handler._current_session_state = lambda: (None, 0)
                responses = []
                handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

                handler._handle_session_restore()

                self.assertEqual(1, len(responses))
                payload, kwargs = responses[0]
                self.assertFalse(payload["ok"])
                self.assertEqual(HTTPStatus.BAD_REQUEST, kwargs["status"])

    def test_json_object_and_empty_body_still_parse(self):
        for raw_body, expected in ((b"", {}), ('{"title":"문항"}'.encode("utf-8"), {"title": "문항"})):
            with self.subTest(raw_body=raw_body):
                handler = object.__new__(app_server.AppRequestHandler)
                handler.headers = {"Content-Length": str(len(raw_body))}
                handler.rfile = io.BytesIO(raw_body)
                self.assertEqual(expected, handler._read_json_body())


class TestMergeInputValidation(unittest.TestCase):
    def test_duplicate_selection_cannot_merge_one_problem_with_itself(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "page.png"
            Image.new("RGB", (100, 100), "white").save(source)
            session = {
                "output_dir": str(root),
                "pages": [{"id": "page-1", "sourceImagePath": str(source), "problemIds": ["p1"]}],
                "problems": [{"id": "p1", "sourcePageId": "page-1", "bbox": {"width": 20, "height": 20}}],
            }
            original = copy.deepcopy(session)

            with self.assertRaisesRegex(ValueError, "at least 2"):
                app_server._mutate_merge(session, ["p1", "p1"])

            self.assertEqual(original, session)
            self.assertFalse((root / "problem_crops").exists())

    def test_merge_deduplicates_ids_before_counting_and_creating_title(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "page.png"
            Image.new("RGB", (100, 100), "white").save(source)
            session = {
                "output_dir": str(root),
                "pages": [{"id": "page-1", "sourceImagePath": str(source), "problemIds": ["p1", "p2"]}],
                "problems": [
                    {"id": "p1", "title": "1번", "sourcePageId": "page-1", "bbox": {"left": 0, "top": 0, "width": 20, "height": 20}},
                    {"id": "p2", "sourcePageId": "page-1", "bbox": {"left": 0, "top": 20, "width": 20, "height": 20}},
                ],
            }

            updated = app_server._mutate_merge(session, ["p1", "p2", "p1"])

            self.assertEqual(1, len(updated["problems"]))
            self.assertEqual("1번 외 1건", updated["problems"][0]["title"])
            self.assertEqual([updated["problems"][0]["id"]], updated["pages"][0]["problemIds"])


if __name__ == "__main__":
    unittest.main()
