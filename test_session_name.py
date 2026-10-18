"""Lesson renaming is metadata-only and uses the session revision guard."""
import copy
import unittest

import app_server


class TestSessionName(unittest.TestCase):
    def test_rename_preserves_sources_images_layout_and_problem_numbers(self):
        session = {
            "session_name": "기존 수업", "input_files": ["/source/원본.pdf"],
            "pages": [{"id": "page-1", "sourceImagePath": "/source/page.png"}],
            "problems": [{"id": "a", "problemNumber": 12, "processingStep": "s2",
                          "manualCrop": {"topRatio": .1}, "startYPages": 2.4,
                          "imagePath": "/crop/a.png", "boardRenderPath": "/crop/a-board.png"}],
        }
        before = copy.deepcopy(session)
        result = app_server._mutate_session_name(session, "  수학 · 새 수업  ")
        self.assertEqual("수학 · 새 수업", result["session_name"])
        result["session_name"] = before["session_name"]
        self.assertEqual(before, result)

    def test_invalid_names_leave_the_entire_session_unchanged(self):
        for value in [None, 42, [], "", "  ", "a" * 161, "잘못\x00된 이름", "첫 줄\n다음 줄"]:
            with self.subTest(value=value):
                session = {"session_name": "기존", "problems": [{"id": "a"}]}
                before = copy.deepcopy(session)
                with self.assertRaises(ValueError):
                    app_server._mutate_session_name(session, value)
                self.assertEqual(before, session)

    def test_route_persists_the_name_and_rejects_stale_or_missing_input(self):
        class Server:
            def __init__(self):
                self.latest_session = {"session_name": "기존", "problems": [{"id": "a"}], "pages": []}
                self.allowed_files = set()

            def session_snapshot_with_revision(self):
                return copy.deepcopy(self.latest_session), 3

            def session_epoch(self):
                return "name-test"

            def remember_session_if_current(self, expected, updated, *, expected_revision=None):
                self.latest_session = copy.deepcopy(updated)
                return 4

        for extra, status in [({"sessionName": "새 수업"}, 200), ({}, 400),
                              ({"sessionName": "새 수업", "expectedSessionRevision": 2}, 409)]:
            with self.subTest(extra=extra):
                handler = object.__new__(app_server.AppRequestHandler)
                handler.server = Server()
                handler._read_json_body = lambda: {
                    "action": "session-name", "expectedSessionRevision": 3,
                    "expectedSessionEpoch": "name-test", **extra,
                }
                responses = []
                handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
                handler._handle_session_mutate()
                payload, kwargs = responses[0]
                self.assertEqual(status, kwargs.get("status", 200))
                if status == 200:
                    self.assertEqual("새 수업", payload["session"]["session_name"])
                    self.assertEqual(4, payload["sessionRevision"])
                else:
                    self.assertEqual("기존", handler.server.latest_session["session_name"])


if __name__ == "__main__":
    unittest.main()
