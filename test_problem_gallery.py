from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import zipfile
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

import fitz
from PIL import Image, ImageDraw

import app_server
from problem_gallery import BATCH_PAGES, CONTEXT_PAGES, GalleryStore, MAX_UPLOAD_BYTES, gallery_image_filename
from problem_parser import ParseResult, ParsedPage, ParsedProblem, ParsedRegion
from structured_schema import Box


def pdf_bytes(pages=1):
    with fitz.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page(width=300, height=400)
            page.insert_text((30, 50), f"{number}. Calculate the following expression.")
            page.insert_text((30, 80), "2 + 3 = ?")
        return document.tobytes()


def fake_parse(source, **kwargs):
    pages, problems = [], []
    with fitz.open(source) as document:
        for index, page in enumerate(document):
            number = int(page.get_text().split(".")[0])
            page_id = f"page-{index}"
            pages.append(ParsedPage(page_id, index, 30, 40, Image.new("RGB", (30, 40), "white")))
            problems.append(ParsedProblem(
                f"p-{number}", number, f"Question {number}",
                [ParsedRegion(page_id, Box(left=0, top=0, width=30, height=40))],
                [], Image.new("RGB", (30, 40), "white"),
                number_source="text_prefix",
            ))
    return ParseResult(pages, problems, len(pages), "test", {})


class TestProblemGallery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = GalleryStore(Path(self.temporary.name) / "gallery")

    def upload_pdf(self, pages):
        data = pdf_bytes(pages)
        return self.store.upload(io.BytesIO(data), len(data), "교재.pdf")

    def test_large_document_is_bounded_resumable_and_does_not_duplicate_overlap(self):
        job = self.upload_pdf(39)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse) as parser:
            first = self.store.parse_next(job["id"])
            self.assertEqual(BATCH_PAGES, first["processedPages"])
            self.assertEqual(BATCH_PAGES, len(first["problems"]))
            # Reconstructing the store models an app refresh or process restart.
            self.store = GalleryStore(self.store.root)
            while first["processedPages"] < 39:
                first = self.store.parse_next(job["id"])
            count = parser.call_count
            self.store.parse_next(job["id"])
            self.assertEqual(count, parser.call_count, "completed jobs must be idempotent")
            for call in parser.call_args_list:
                self.assertFalse(call.kwargs["render_board_assets"])
                self.assertTrue(call.kwargs["preserve_source_pixels"])
        problems = first["problems"]
        self.assertEqual(list(range(1, 40)), [problem["number"] for problem in problems])
        self.assertEqual([[i] for i in range(1, 40)], [problem["pageNumbers"] for problem in problems])
        self.assertEqual(39, len(list(self.store.root.rglob("*.png"))))
        self.assertFalse(list(self.store.root.rglob("*.edb")))
        self.assertFalse(list(self.store.root.rglob("batch-*")))

    def test_batch_page_window_is_bounded_and_cross_page_crop_is_kept_once(self):
        windows = []

        def crossing_parser(source, **kwargs):
            result = fake_parse(source, **kwargs)
            numbers = [problem.number for problem in result.problems]
            windows.append(len(result.pages))
            if BATCH_PAGES in numbers and BATCH_PAGES + 1 in numbers:
                left = numbers.index(BATCH_PAGES)
                right = numbers.index(BATCH_PAGES + 1)
                result.problems.append(ParsedProblem(
                    "crossing", 999, "Cross-page passage", [
                        ParsedRegion(result.pages[index].page_id, Box(left=0, top=0, width=30, height=40))
                        for index in (left, right)
                    ], [], Image.new("RGB", (30, 80), "white"),
                    source_page_id=result.pages[right].page_id,
                ))
            # A later child question first becomes visible in the next batch,
            # but its crop still includes a passage owned by the previous batch.
            if BATCH_PAGES in numbers and BATCH_PAGES + 3 in numbers:
                passage = numbers.index(BATCH_PAGES)
                child = numbers.index(BATCH_PAGES + 3)
                result.problems.append(ParsedProblem(
                    "later-child", 998, "Later child with earlier passage", [
                        ParsedRegion(result.pages[index].page_id, Box(left=0, top=0, width=30, height=40))
                        for index in (passage, child)
                    ], [], Image.new("RGB", (30, 80), "white"),
                    source_page_id=result.pages[child].page_id,
                ))
            return result

        job = self.upload_pdf(39)
        with mock.patch("problem_parser.parse_problems", side_effect=crossing_parser):
            while job["processedPages"] < job["sourcePageCount"]:
                job = self.store.parse_next(job["id"])
        self.assertLessEqual(max(windows), BATCH_PAGES + 2 * CONTEXT_PAGES)
        crossing = [problem for problem in job["problems"] if problem["number"] == 999]
        self.assertEqual(1, len(crossing))
        self.assertEqual([BATCH_PAGES, BATCH_PAGES + 1], crossing[0]["pageNumbers"])
        later = [problem for problem in job["problems"] if problem["number"] == 998]
        self.assertEqual(1, len(later))
        self.assertEqual([BATCH_PAGES, BATCH_PAGES + 3], later[0]["pageNumbers"])

    def test_failed_batch_preserves_completed_crops_and_retry_has_no_duplicates(self):
        job = self.upload_pdf(25)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            first = self.store.parse_next(job["id"])
        with mock.patch("problem_parser.parse_problems", side_effect=RuntimeError("temporary failure")):
            with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                self.store.parse_next(job["id"])
        failed = self.store.load(job["id"])
        self.assertEqual("failed", failed["status"])
        self.assertEqual(first["problems"], failed["problems"])
        self.assertEqual(BATCH_PAGES, failed["processedPages"])
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            while failed["processedPages"] < 25:
                failed = self.store.parse_next(job["id"])
        self.assertEqual(25, len(failed["problems"]))
        self.assertIsNone(failed["error"])

    def test_zip_contains_only_selected_unique_problems_and_source_coordinates(self):
        job = self.upload_pdf(2)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            job = self.store.parse_next(job["id"])
        selected = job["problems"][1]["id"]
        path, count = self.store.export_images([selected, selected])
        self.assertEqual(1, count)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(2, len(archive.namelist()))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual([2], manifest[0]["pageNumbers"])
            self.assertEqual("교재.pdf", manifest[0]["sourceName"])
            self.assertEqual(2, manifest[0]["regions"][0]["page"])
        with self.assertRaises(ValueError):
            self.store.export_images([job["id"] + ":not-a-problem"])
        self.assertEqual(1, len(list(self.store.root.rglob("*.zip"))))

    def test_save_options_preserve_raw_pixels_and_transparent_ink_and_reuse_cache(self):
        from image_reconstruction_backend import clean_problem_image_transparency

        job = self.upload_pdf(1)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            job = self.store.parse_next(job["id"])
        problem = job["problems"][0]
        source = self.store.job_dir(job["id"]) / problem["imageFile"]
        with Image.new("RGB", (100, 100), "white") as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 40, 40), fill="black")
            draw.rectangle((60, 20, 80, 40), fill=(180, 0, 0))
            image.save(source)
        original = source.read_bytes()
        with mock.patch("image_reconstruction_backend.clean_problem_image_transparency", wraps=clean_problem_image_transparency) as clean:
            raw, count = self.store.export_images([problem["id"]], "raw", single=True)
            self.assertEqual(source, raw)
            self.assertEqual(0, clean.call_count)
            transparent, count = self.store.export_images([problem["id"]], "transparent", single=True)
            self.assertEqual(1, count)
            with Image.open(transparent) as image:
                self.assertEqual("RGBA", image.mode)
                self.assertEqual((100, 100), image.size)
                self.assertEqual(0, image.getpixel((0, 0))[3])
                self.assertEqual((0, 0, 0, 255), image.getpixel((30, 30)))
                self.assertEqual((180, 0, 0, 255), image.getpixel((70, 30)))
            paths = []
            for mode, expected in [("raw", {"raw"}), ("transparent", {"transparent"}), ("both", {"raw", "transparent"})]:
                path, count = self.store.export_images([problem["id"]], mode)
                paths.append(path)
                with zipfile.ZipFile(path) as archive:
                    record = json.loads(archive.read("manifest.json"))[0]
                    self.assertEqual(mode, record["mode"])
                    self.assertEqual(expected, set(record["files"]))
                    self.assertEqual(len(expected), len([name for name in archive.namelist() if name.endswith(".png")]))
                    if "raw" in expected:
                        self.assertEqual(original, archive.read(record["files"]["raw"]))
                stat = path.stat()
                self.assertEqual(path, self.store.export_images([problem["id"]], mode)[0])
                self.assertEqual(stat.st_mtime_ns, path.stat().st_mtime_ns)
            self.assertEqual(1, clean.call_count, "all export entry points share the lazy image cache")
            self.assertEqual(3, len(set(paths)))
        self.assertEqual(original, source.read_bytes())

    def test_export_names_use_printed_numbers_passages_and_unique_duplicates(self):
        job = self.upload_pdf(5)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            job = self.store.parse_next(job["id"])
        job["sourceName"] = "2026 중간고사 수학.최종본.pdf"
        records = job["problems"]
        records[0]["number"] = records[1]["number"] = 7
        records[2].update(number=None, passageRole="passage_fragment", passageRange={"start":12,"end":15})
        records[3].update(number=99, problemNumberSource="inferred_sequence")
        records[4]["number"] = 123
        self.store.save(job)
        expected = ["2026 중간고사 수학.최종본_07번.png", "2026 중간고사 수학.최종본_07번_(2).png",
            "2026 중간고사 수학.최종본_지문_12~15번.png", "2026 중간고사 수학.최종본_번호미확인_000004.png",
            "2026 중간고사 수학.최종본_123번.png"]
        ids = [p["id"] for p in records]
        path, _ = self.store.export_images(ids, "both")
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for folder in ("raw_crops", "transparent_images"):
                self.assertEqual([f"{folder}/{name}" for name in expected], [name for name in archive.namelist() if name.startswith(folder + "/")])
            self.assertEqual(f"raw_crops/{expected[0]}", manifest[0]["files"]["raw"])
        self.assertEqual(expected[0], self.store.image_download_name(ids[0]))
        single, _ = self.store.export_images([ids[0]], "both", single=True)
        self.assertEqual("2026 중간고사 수학.최종본_07번.zip", single.name)
        job["sourceName"] = "수정된 교재.pdf"
        self.store.save(job)
        changed, _ = self.store.export_images(ids, "both")
        self.assertNotEqual(path, changed, "a renamed source must not reuse stale ZIP entry names")
        long_name = gallery_image_filename({"sourceName":"한글" * 150 + ".pdf"}, records[0])
        self.assertLessEqual(len(long_name.encode("utf-8")), 220)
        self.assertTrue(long_name.endswith("_07번.png"))

    def test_failed_background_save_is_atomic_and_can_retry_without_reparsing(self):
        job = self.upload_pdf(2)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            job = self.store.parse_next(job["id"])
        ids = [problem["id"] for problem in job["problems"]]
        with mock.patch("image_reconstruction_backend.clean_problem_image_transparency", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.store.export_images(ids, "both")
        self.assertFalse(list(self.store.root.rglob("*.tmp")))
        self.assertFalse(list(self.store.root.rglob("*.zip")))
        self.assertEqual(job, self.store.load(job["id"]))
        self.assertEqual(2, len(list(self.store.root.rglob("p*.png"))))
        with mock.patch("problem_parser.parse_problems", side_effect=AssertionError("must not reparse")):
            path, count = self.store.export_images(ids, "both")
        self.assertEqual(2, count)
        self.assertTrue(path.is_file())
        for mode in ["unknown", None, ["raw"]]:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.store.export_images(ids, mode)
        with self.assertRaises(ValueError):
            self.store.export_images(ids, single=True)

    def test_retry_with_same_progress_does_not_start_a_second_batch(self):
        job = self.upload_pdf(25)
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse) as parser:
            first = self.store.parse_next(job["id"], expected_processed_pages=0)
            retried = self.store.parse_next(job["id"], expected_processed_pages=0)
            self.assertEqual(first, retried)
            self.assertEqual(1, parser.call_count)
            for progress in [-1, 99, "12", True]:
                with self.subTest(progress=progress), self.assertRaises(ValueError):
                    self.store.parse_next(job["id"], progress)
            next_job = self.store.parse_next(job["id"], first["processedPages"])
            self.assertEqual(24, next_job["processedPages"])

    def test_corrupt_or_unsafe_job_record_does_not_break_other_sources(self):
        good = self.upload_pdf(1)
        bad = self.upload_pdf(1)
        path = self.store.job_dir(bad["id"]) / "manifest.json"
        for invalid in [None, [], {}, {**bad, "createdAt": "yesterday"}, {**bad, "sourceFile": "../outside.pdf"}, {**bad, "problems": [None]}]:
            with self.subTest(record=invalid):
                path.write_text(json.dumps(invalid))
                self.assertEqual([good], self.store.list_jobs())
                with self.assertRaises(ValueError):
                    self.store.load(bad["id"])
                self.assertTrue(path.exists(), "never remove a damaged record or its source automatically")

    def test_invalid_and_incomplete_uploads_leave_no_job_and_paths_are_confined(self):
        for data, size, name in [(b"bad", 3, "bad.pdf"), (b"x", 10, "short.pdf"), (b"x", 1, "file.hwp"), (b"", MAX_UPLOAD_BYTES + 1, "large.pdf")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.upload(io.BytesIO(data), size, name)
        self.assertEqual([], self.store.list_jobs())
        with self.assertRaises(ValueError):
            self.store.load("../outside")

    def test_real_parser_accepts_image_input_without_pdf_conversion(self):
        from problem_parser import parse_problems

        buffer = io.BytesIO()
        Image.new("RGB", (200, 300), "white").save(buffer, format="PNG")
        data = buffer.getvalue()
        job = self.store.upload(io.BytesIO(data), len(data), "image.png")
        source = self.store.job_dir(job["id"]) / job["sourceFile"]
        result = parse_problems(source, work_dir=source.parent / "work", ocr_mode="none")
        self.assertEqual(1, result.source_page_count)
        self.assertEqual(1, len(result.pages))

    def test_real_parser_preserves_three_digit_question_numbers(self):
        data = pdf_bytes(3)
        with fitz.open(stream=data, filetype="pdf") as document:
            for index, page in enumerate(document):
                page.add_redact_annot(page.rect)
                page.apply_redactions()
                page.insert_text((30, 50), f"{99 + index}. Calculate the following expression.")
                page.insert_text((30, 80), "2 + 3 = ?")
            data = document.tobytes()
        job = self.store.upload(io.BytesIO(data), len(data), "three-digit.pdf")
        job = self.store.parse_next(job["id"])
        self.assertEqual([99, 100, 101], [problem["number"] for problem in job["problems"]])
        self.assertTrue(all(problem["problemNumberSource"] == "pdf_text_marker" for problem in job["problems"]))

    def test_plain_crop_keeps_wide_question_line_and_complete_colored_figures(self):
        from problem_parser import parse_problems

        with fitz.open() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((40, 80), "1. Calculate the following expression.", fontsize=15)
            page.insert_text((40, 120), "2 + 3 = ?", fontsize=20)
            page.draw_rect(fitz.Rect(45, 150, 100, 205), color=(0.8, 0.2, 0.2))
            page.draw_circle(fitz.Point(160, 178), 27, color=(0.2, 0.3, 0.8))
            data = document.tobytes()
        job = self.store.upload(io.BytesIO(data), len(data), "figures.pdf")
        source = self.store.job_dir(job["id"]) / job["sourceFile"]
        result = parse_problems(source, work_dir=source.parent / "check", preserve_source_pixels=True)
        try:
            self.assertEqual(1, len(result.problems))
            crop = result.problems[0].image
            page = result.pages[0].image
            # This single-question sheet has no unrelated content. Neither a
            # false two-column split nor board watermark cleanup may erase it.
            self.assertEqual(page.size, crop.size)
            self.assertEqual(page.tobytes(), crop.tobytes())
        finally:
            for page in result.pages:
                page.image.close()
            for problem in result.problems:
                problem.image.close()


class TestGalleryHttp(unittest.TestCase):
    def test_upload_extract_restore_download_and_editor_session_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(app_server, "RUNTIME_DIR", Path(directory)), mock.patch.object(app_server, "load_latest_session", return_value=None), mock.patch.object(app_server, "load_session_history", return_value=[]), mock.patch.object(app_server, "_recover_interrupted_session_reset"):
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

            def request(method, path, body=None, content_type=None, expected_status=None):
                connection = HTTPConnection(*server.server_address, timeout=60)
                headers = {"Content-Type": content_type} if content_type else {}
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                connection.close()
                if expected_status is None:
                    self.assertLess(response.status, 300, data)
                else:
                    self.assertEqual(expected_status, response.status, data)
                return data

            initial = server.session_snapshot_with_revision()
            job = json.loads(request("POST", "/api/parser/upload?name=exam.pdf", pdf_bytes(1), "application/octet-stream"))["job"]
            parsed = json.loads(request("POST", "/api/parser/next", json.dumps({"jobId": job["id"]}), "application/json"))["job"]
            self.assertEqual("complete", parsed["status"])
            self.assertEqual(1, len(parsed["problems"]))
            problem = parsed["problems"][0]
            with Image.open(io.BytesIO(request("GET", problem["previewUrl"]))) as preview:
                self.assertLessEqual(max(preview.size), 768)
            self.assertTrue(request("GET", problem["imageUrl"]).startswith(b"\x89PNG"))
            self.assertTrue(request("GET", parsed["sourceUrl"]).startswith(b"%PDF"))
            restored = json.loads(request("GET", "/api/parser/jobs"))["jobs"]
            self.assertEqual(parsed, restored[0])
            store = GalleryStore(app_server.RUNTIME_DIR / "problem-gallery")
            legacy = store.load(job["id"])
            legacy["problems"][0].pop("problemNumberSource", None)
            store.save(legacy)
            with mock.patch("gallery_recognition.local_label_backend"), mock.patch("gallery_recognition.read_image_label", return_value={
                    "number":123,"problemNumberSource":"ocr_top_left","passageRole":"","passageRange":None}):
                labeled = json.loads(request("POST", "/api/parser/recognize-labels", json.dumps({"problemIds":[problem["id"]]}),"application/json"))
            self.assertEqual(1,labeled["recognized"])
            self.assertEqual(123,labeled["jobs"][0]["problems"][0]["number"])
            self.assertEqual("ocr_top_left",store.load(job["id"])["problems"][0]["problemNumberSource"])
            exported = json.loads(request("POST", "/api/parser/export-images", json.dumps({"problemIds": [problem["id"]]}), "application/json"))
            with zipfile.ZipFile(io.BytesIO(request("GET", exported["url"]))) as archive:
                self.assertEqual(2, len(archive.namelist()))
            for mode in ("raw", "transparent", "both"):
                exported = json.loads(request("POST", "/api/parser/export-images", json.dumps({
                    "problemIds": [problem["id"]], "mode": mode, "single": True,
                }), "application/json"))
                data = request("GET", exported["url"])
                self.assertEqual(mode, exported["mode"])
                self.assertEqual(2 if mode == "both" else 1, exported["fileCount"])
                self.assertEqual("exam_123번.zip" if mode == "both" else "exam_123번.png", exported["fileName"])
                if mode == "both":
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        self.assertEqual(3, len(archive.namelist()))
                else:
                    with Image.open(io.BytesIO(data)) as image:
                        self.assertEqual("RGBA" if mode == "transparent" else "RGB", image.mode)
            for payload in [[], {"problemIds": [problem["id"]], "mode": "unsupported"}]:
                error = json.loads(request("POST", "/api/parser/export-images", json.dumps(payload), "application/json", expected_status=400))
                self.assertFalse(error["ok"])
            self.assertEqual(initial, server.session_snapshot_with_revision())
            self.assertFalse(list(Path(directory).rglob("*.edb")))


if __name__ == "__main__":
    unittest.main()
