from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import app_server
import build_mvp_export
from inspect_edb import parse_edb
from preprocess import PreparedPage
from structured_schema import PageModel, ProblemUnit, Subject


class TestMvpPageExportLimits(unittest.TestCase):
    def test_twenty_six_pages_keep_single_file_with_fifty_page_header(self):
        with TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "lesson.edb"
            images = [Image.new("RGB", (16, 24), "white")] * 26

            parts = build_mvp_export.export_board_edb(images, path, "test")

            self.assertEqual(1, len(parts))
            self.assertEqual(path.resolve(), Path(parts[0]["edbPath"]))
            parsed = parse_edb(path)
            self.assertEqual(50, parsed.page_count_hint)
            self.assertEqual(26, parsed.record_count_actual)

    def test_large_page_exports_split_without_losing_order_or_exceeding_canvas(self):
        with TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "lesson.edb"
            images = [Image.new("RGB", (16, 24), (index, 0, 0)) for index in range(83)]

            parts = build_mvp_export.export_board_edb(images, path, "test")

            self.assertEqual([28, 28, 27], [part["recordCount"] for part in parts])
            self.assertEqual(["lesson_part01.edb", "lesson_part02.edb", "lesson_part03.edb"], [part["edbFileName"] for part in parts])
            self.assertFalse(path.exists())
            self.assertEqual([1, 29, 57], [part["sourcePageStartIndex"] for part in parts])
            self.assertEqual([28, 56, 83], [part["sourcePageEndIndex"] for part in parts])
            for part in parts:
                parsed = parse_edb(Path(part["edbPath"]))
                self.assertEqual(50, parsed.page_count_hint)
                self.assertEqual(part["recordCount"], parsed.record_count_actual)
                self.assertEqual(0.0, parsed.records[0].pos_y)
                self.assertEqual(list(range(part["recordCount"])), [record.id16 for record in parsed.records])
                for record in parsed.records:
                    self.assertLessEqual(record.pos_y + record.height_hint, 1.0 + 1e-6)
                    self.assertAlmostEqual(1.2 / 50, record.height_hint, places=6)
            validation, _ = app_server._validate_edb_parts(parts)
            self.assertEqual(83, validation["recordCountActual"])
            self.assertEqual(3, validation["edbPartCount"])

    def _prepared_models(self, source: Path, count: int):
        prepared = []
        models = []
        for index in range(count):
            page_id = f"page-{index + 1}"
            prepared.append(PreparedPage(page_id, str(source), index + 1, Image.new("RGB", (16, 24), "white"), (16, 24)))
            models.append(PageModel(
                page_id=page_id,
                width_px=16,
                height_px=24,
                subject=Subject.UNKNOWN,
                source_path=str(source),
                problems=[ProblemUnit(unit_id=f"problem-{index + 1}", subject=Subject.UNKNOWN, title=f"문항 {index + 1}")],
            ))
        return prepared, models

    def _run_small_export(self, source, output_dir: Path, count: int, *, export_edb: bool = True):
        primary_source = source[0] if isinstance(source, list) else source
        prepared, models = self._prepared_models(primary_source, count)
        with (
            patch.object(build_mvp_export, "prepare_source_pages", return_value=prepared),
            patch.object(build_mvp_export, "prepare_source_pages_batch", return_value=prepared),
            patch.object(build_mvp_export, "build_page_model", side_effect=models),
            patch.object(build_mvp_export, "render_board_page", side_effect=lambda _model, image, _ratio: image.copy()),
        ):
            return build_mvp_export.run_export(source, output_dir=output_dir, export_edb=export_edb, edb_name="lesson.edb", ocr="none")

    def test_repeated_page_exports_archive_obsolete_single_and_split_files_recoverably(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.png"
            Image.new("RGB", (16, 24), "white").save(source)
            output_dir = root / "out"
            first = self._run_small_export(source, output_dir, 1)
            previous_single = first["edb_path"].read_bytes()

            split = self._run_small_export(source, output_dir, 42)

            self.assertFalse((output_dir / "lesson.edb").exists())
            self.assertEqual(1, len(split["archived_edb_paths"]))
            self.assertEqual(previous_single, Path(split["archived_edb_paths"][0]).read_bytes())
            previous_parts = {path.name: path.read_bytes() for path in split["edb_paths"]}

            single = self._run_small_export(source, output_dir, 1)

            self.assertEqual(["lesson.edb"], [path.name for path in output_dir.glob("*.edb")])
            self.assertEqual(2, len(single["archived_edb_paths"]))
            for raw_path in single["archived_edb_paths"]:
                path = Path(raw_path)
                self.assertEqual(previous_parts[path.name], path.read_bytes())
                snapshot = json.loads((path.parent / "board_run_summary.json").read_text(encoding="utf-8"))
                self.assertEqual([str(source.resolve())], snapshot["input_files"])
                self.assertEqual(2, snapshot["edbPartCount"])
            self.assertEqual([], single["warnings"])

    def test_changed_batch_source_does_not_archive_previous_results(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            sources = [root / name for name in ("first.png", "second.png", "other.png")]
            for source in sources:
                Image.new("RGB", (16, 24), "white").save(source)
            previous = self._run_small_export(sources[:2], root / "out", 42)
            previous_parts = {path: path.read_bytes() for path in previous["edb_paths"]}

            current = self._run_small_export([sources[0], sources[2]], root / "out", 1)

            self.assertEqual([], current["archived_edb_paths"])
            for path, payload in previous_parts.items():
                self.assertEqual(payload, path.read_bytes())
            self.assertFalse((root / "out" / "edb_archive").exists())

    def test_preview_and_failed_session_save_preserve_previous_edbs(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.png"
            Image.new("RGB", (16, 24), "white").save(source)
            previous = self._run_small_export(source, root / "out", 1)
            original = previous["edb_path"].read_bytes()

            preview = self._run_small_export(source, root / "out", 2, export_edb=False)

            self.assertEqual([], preview["archived_edb_paths"])
            self.assertEqual(original, previous["edb_path"].read_bytes())
            # Restore ownership metadata before exercising a failed new export.
            self._run_small_export(source, root / "out", 1)
            original = previous["edb_path"].read_bytes()
            with patch.object(build_mvp_export, "write_ui_session_bundle", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    self._run_small_export(source, root / "out", 42)
            self.assertEqual(original, previous["edb_path"].read_bytes())
            self.assertFalse((root / "out" / "edb_archive").exists())

    def test_page_export_api_returns_all_parts_and_persists_matching_session_metadata(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.png"
            Image.new("RGB", (16, 24), "white").save(source)
            prepared, models = self._prepared_models(source, 42)
            handler = object.__new__(app_server.AppRequestHandler)
            handler._current_session_state = lambda: (None, None)
            handler._read_json_body = lambda: {"exportMode": "page", "files": [str(source)], "exportEdb": True, "ocr": "none", "edbName": "lesson.edb"}
            handler._resolve_output_dir = lambda _payload, _sources: (root / "out").resolve()
            responses = []
            committed_sessions = []
            handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

            def adopt(_base, session, *, staging_dir, final_dir, expected_revision):
                final_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging_dir, final_dir)
                committed_sessions.append(session)
                return True, 1

            handler._adopt_staged_publish_if_current = adopt
            with (
                patch.object(build_mvp_export, "prepare_source_pages", return_value=prepared),
                patch.object(build_mvp_export, "build_page_model", side_effect=models),
                patch.object(build_mvp_export, "render_board_page", side_effect=lambda _model, image, _ratio: image.copy()),
                patch.object(app_server, "_global_ai_enabled", return_value=False),
            ):
                handler._handle_export()

            self.assertEqual(1, len(responses))
            response = responses[0][0]
            self.assertTrue(response["ok"], response)
            self.assertEqual(2, response["edbPartCount"])
            self.assertTrue(response["edbSplit"])
            self.assertEqual(42, response["edbValidation"]["recordCountActual"])
            self.assertEqual([21, 21], [part["recordCount"] for part in response["edbParts"]])
            self.assertEqual(1, len(committed_sessions))
            session = committed_sessions[0]
            self.assertEqual([part["edbPath"] for part in response["edbParts"]], session["edb_paths"])
            self.assertEqual(1, session["problems"][0]["edbPartIndex"])
            self.assertEqual(2, session["problems"][-1]["edbPartIndex"])
            self.assertEqual([f"page-{index + 1}" for index in range(21)], session["edb_parts"][0]["pageIds"])
            for path in response["edbPaths"]:
                self.assertTrue(Path(path).is_file())
                self.assertEqual(50, parse_edb(Path(path)).page_count_hint)
            persisted_session = json.loads(Path(response["uiSessionPath"]).read_text(encoding="utf-8"))
            self.assertEqual(2, persisted_session["edbPartCount"])
            self.assertEqual(session["edb_paths"], persisted_session["edb_paths"])
            placements = json.loads(Path(session["placements_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(session["edb_paths"], placements["edb_paths"])
            for session_part, placement_part in zip(session["edb_parts"], placements["edb_parts"]):
                for key in ("partIndex", "partCount", "recordCount", "pageIds", "problemIds"):
                    self.assertEqual(session_part[key], placement_part[key])


if __name__ == "__main__":
    unittest.main()
