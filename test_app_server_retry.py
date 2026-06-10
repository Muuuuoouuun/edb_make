import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server


def _build_session(tmpdir: Path, *, present_page_ids: set[str]) -> dict:
    pages = []
    problems = []
    for page_id in ("page-1", "page-2"):
        if page_id in present_page_ids:
            image_path = tmpdir / f"{page_id}.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            source_uri = image_path.resolve().as_uri()
        else:
            source_uri = (tmpdir / f"missing_{page_id}.png").resolve().as_uri()
        problem_id = f"{page_id}-p1"
        pages.append({
            "id": page_id,
            "sourceImageUri": source_uri,
            "problemIds": [problem_id],
            "riskFlags": ["needs_review"],
        })
        problems.append({
            "id": problem_id,
            "sourcePageId": page_id,
            "bbox": {"left": 0, "top": 0, "width": 100, "height": 100},
            "riskFlags": ["needs_review"],
        })
    return {
        "pages": pages,
        "problems": problems,
        "ai_fallback": {"provider": "gemini"},
    }


def _fake_run_problem_export(source_path, **_kwargs):
    page_id = Path(source_path).stem
    return {
        "ui_session": {
            "pages": [{
                "id": page_id,
                "riskFlags": [],
            }],
            "problems": [{
                "id": f"{page_id}-new",
                "sourcePageId": page_id,
                "bbox": {"left": 0, "top": 0, "width": 80, "height": 80},
                "riskFlags": [],
            }],
            "ai_summary": {"applied": True},
        }
    }


class TestRetryAiResilience(unittest.TestCase):
    def setUp(self):
        self._prev_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key"

    def tearDown(self):
        if self._prev_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._prev_key

    def test_missing_source_marks_page_failed_and_continues(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            session = _build_session(tmpdir, present_page_ids={"page-2"})

            with patch.object(app_server, "run_problem_export", side_effect=_fake_run_problem_export):
                new_session = app_server._mutate_retry_ai(
                    session,
                    {"pageIds": ["page-1", "page-2"]},
                )

            summaries = new_session.get("ai_retry_summary") or []
            statuses = {row["pageId"]: row["status"] for row in summaries}
            self.assertEqual(statuses, {"page-1": "missing_source", "page-2": "applied"})

            page_1 = next(p for p in new_session["pages"] if p["id"] == "page-1")
            self.assertEqual(page_1["reviewStatus"], "failed")
            self.assertIn("ai_retry_missing_source", page_1["riskFlags"])
            self.assertEqual(page_1["aiRetry"]["status"], "missing_source")
            # page-1's original problem is untouched (no replacement on missing-source)
            page_1_problem_ids = page_1["problemIds"]
            self.assertEqual(page_1_problem_ids, ["page-1-p1"])

            page_2 = next(p for p in new_session["pages"] if p["id"] == "page-2")
            self.assertEqual(page_2["aiRetry"]["status"], "applied")
            self.assertEqual(page_2["aiRetry"]["replacedProblemCount"], 1)
            # page-2's old problem was replaced
            new_problem_ids = {prob["id"] for prob in new_session["problems"]}
            self.assertNotIn("page-2-p1", new_problem_ids)
            self.assertIn("page-1-p1", new_problem_ids)

    def test_missing_key_rejects_before_any_mutation(self):
        os.environ.pop("GEMINI_API_KEY", None)
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            session = _build_session(tmpdir, present_page_ids={"page-1", "page-2"})
            with self.assertRaises(ValueError) as ctx:
                app_server._mutate_retry_ai(session, {"pageIds": ["page-1"]})
            self.assertIn("Gemini API", str(ctx.exception))
            # No ai_retry_summary, no page-level aiRetry should have been written.
            self.assertNotIn("ai_retry_summary", session)
            for page in session["pages"]:
                self.assertNotIn("aiRetry", page)


if __name__ == "__main__":
    unittest.main()
