import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

import build_problem_board_edb as problem_board
import export_mvp_edb
from inspect_edb import parse_edb
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from placement_engine import place_problems
from preprocess import PreparedPage
from structured_schema import AssetRef, AssetType, BlockType, Box, ContentBlock, PageModel, Subject


class TestExportRegressions(unittest.TestCase):
    def _legacy_page(self, root, *, block_type=BlockType.IMAGE, text=None):
        source = root / "source.png"
        image = Image.new("RGB", (200, 200), "white")
        ImageDraw.Draw(image).rectangle((20, 20, 180, 180), outline="black", width=3)
        image.save(source)
        block = ContentBlock(
            block_id="block-1",
            block_type=block_type,
            bbox=Box(0, 0, 200, 200),
            reading_order=0,
            text=text,
            asset=AssetRef("asset-1", AssetType.CROP, source_path=str(source)),
        )
        return PageModel("page-1", 200, 200, Subject.MATH, source_path=str(source), blocks=[block])

    def _entry(self, page, prepared, index):
        return problem_board.ProblemEntry(
            problem_id=f"problem-{index:02d}", title=f"Problem {index + 1}", problem_number=index + 1,
            subject=Subject.MATH, source_page_id=page.page_id, source_path=page.source_path,
            prepared_page=prepared, bounds=Box(0, 0, 200, 200), crop_path=Path(page.source_path),
            board_render_path=Path(page.source_path), blocks=[], actual_height_pages=1.2,
            overflow_allowed=False, reading_heavy=False, risk_flags=[],
        )

    def _image_entry(self, root, problem_id, size, *, intent="single-problem", scale=1.0):
        source = root / f"{problem_id}.png"
        image = Image.new("RGB", size, "white")
        ImageDraw.Draw(image).rectangle((2, 2, size[0] - 3, size[1] - 3), outline="black", width=2)
        image.save(source)
        return problem_board.ProblemEntry(
            problem_id=problem_id, title=problem_id, problem_number=None, subject=Subject.MATH,
            source_page_id=f"page-{problem_id}", source_path=str(source), prepared_page=None,
            bounds=Box(0, 0, *size), crop_path=source, board_render_path=source, blocks=[],
            actual_height_pages=problem_board.MAX_HEIGHT_PAGES if size[1] > size[0] * 5 else 0.72,
            overflow_allowed=True, reading_heavy=False, risk_flags=[], input_intent=intent,
            placement_scale_ratio=scale, force_full_page_bounds=intent == "page-as-is",
        )

    def _run_cli(self, root, page, prepared, count):
        stdout = io.StringIO()
        with (
            mock.patch.object(problem_board, "build_pages", return_value=([prepared], [page])),
            mock.patch.object(problem_board, "build_problem_entries", return_value=[
                self._entry(page, prepared, index) for index in range(count)
            ]),
            mock.patch("sys.argv", ["build_problem_board_edb.py", page.source_path, "--output-dir", str(root / "out"),
                                     "--record-mode", "image-only", "--light-board", "--board-pages", "100"]),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, problem_board.main())
        return json.loads(stdout.getvalue())

    def test_legacy_export_uses_one_page_scale_for_header_records_and_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            page = self._legacy_page(root)
            with mock.patch.object(export_mvp_edb, "_prepare_pages_with_assets", return_value=([page], {})):
                result = export_mvp_edb.export_source_to_mvp_edb(
                    page.source_path, root / "out", subject=Subject.MATH, page_count_hint=25
                )
            parsed = parse_edb(Path(result["output_edb"]))
            plan = json.loads((root / "out" / "placements.json").read_text())
            self.assertEqual(25, parsed.page_count_hint)
            self.assertEqual(25, plan["template"]["board_page_count"])
            record = parsed.records[0]
            self.assertAlmostEqual(80.0, record.pos_y * 590 * parsed.page_count_hint, places=4)
            self.assertAlmostEqual(1280 / 3, record.height_hint * 590 * parsed.page_count_hint, places=4)

    def test_legacy_export_marks_diagrams_tables_and_empty_text_as_images(self):
        for block_type, text in ((BlockType.DIAGRAM, "diagram"), (BlockType.TABLE, "table"), (BlockType.STEM, None)):
            with self.subTest(block_type=block_type), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                page = self._legacy_page(root, block_type=block_type, text=text)
                with mock.patch.object(export_mvp_edb, "_prepare_pages_with_assets", return_value=([page], {})):
                    result = export_mvp_edb.export_source_to_mvp_edb(page.source_path, root / "out", subject=Subject.MATH)
                parsed = parse_edb(Path(result["output_edb"]))
                self.assertEqual(4, parsed.header_flag)
                self.assertEqual(2, len(parsed.records[0].embedded_images))

    def test_problem_board_cli_splits_edbs_and_propagates_part_metadata(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            page = self._legacy_page(root)
            with Image.open(page.source_path) as source_image:
                image = source_image.copy()
            prepared = PreparedPage(page.page_id, page.source_path, 1, image, image.size)
            output = root / "out"
            result = self._run_cli(root, page, prepared, 43)
            edbs = sorted(output.glob("*.edb"))
            self.assertEqual(2, len(edbs))
            self.assertEqual(2, result["edb_part_count"])
            self.assertEqual({str(path.resolve()) for path in edbs}, set(result["edb_paths"]))
            self.assertEqual(43, sum(parse_edb(path).record_count_actual for path in edbs))
            for path in edbs:
                parsed = parse_edb(path)
                self.assertEqual(50, parsed.page_count_hint)
                for record in parsed.records:
                    self.assertLessEqual((record.pos_y + record.height_hint) * parsed.page_count_hint, 50 + 1e-5)
            summary = json.loads((output / "board_run_summary.json").read_text())
            session = json.loads((output / "ui_session.json").read_text())
            handoff = json.loads((output / "classin_handoff.json").read_text())
            self.assertTrue(summary["edb_split"])
            self.assertEqual(2, session["edbPartCount"])
            self.assertEqual(2, len(handoff["edbParts"]))
            self.assertEqual({1, 2}, {problem["edbPartIndex"] for problem in session["problems"]})

    def test_cli_archives_previous_single_and_split_edbs_without_deleting_them(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            page = self._legacy_page(root)
            with Image.open(page.source_path) as source_image:
                image = source_image.copy()
            prepared = PreparedPage(page.page_id, page.source_path, 1, image, image.size)
            single = self._run_cli(root, page, prepared, 1)
            single_path = Path(single["edb_path"])
            single_bytes = single_path.read_bytes()
            unrelated = root / "out" / "unrelated.edb"
            unrelated.write_bytes(b"user-owned-file")

            split = self._run_cli(root, page, prepared, 43)
            self.assertFalse(single_path.exists())
            self.assertEqual(1, len(split["archived_edb_paths"]))
            self.assertEqual(single_bytes, Path(split["archived_edb_paths"][0]).read_bytes())
            split_paths = [Path(path) for path in split["edb_paths"]]
            split_bytes = {path.name: path.read_bytes() for path in split_paths}

            restored_single = self._run_cli(root, page, prepared, 1)
            self.assertEqual(2, len(restored_single["archived_edb_paths"]))
            for archived in restored_single["archived_edb_paths"]:
                path = Path(archived)
                self.assertEqual(split_bytes[path.name], path.read_bytes())
                self.assertTrue((path.parent / "board_run_summary.json").is_file())
            self.assertFalse(any(path.exists() for path in split_paths))
            self.assertEqual(b"user-owned-file", unrelated.read_bytes())
            self.assertEqual(1, parse_edb(Path(restored_single["edb_path"])).record_count_actual)

    def test_cli_preserves_previous_files_and_summary_when_new_export_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            page = self._legacy_page(root)
            with Image.open(page.source_path) as source_image:
                image = source_image.copy()
            prepared = PreparedPage(page.page_id, page.source_path, 1, image, image.size)
            single = self._run_cli(root, page, prepared, 1)
            single_path = Path(single["edb_path"])
            previous_bytes = single_path.read_bytes()
            summary_path = root / "out" / "board_run_summary.json"
            previous_summary = summary_path.read_bytes()
            with mock.patch.object(problem_board, "write_ui_session_bundle", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    self._run_cli(root, page, prepared, 43)
            self.assertEqual(previous_bytes, single_path.read_bytes())
            self.assertEqual(previous_summary, summary_path.read_bytes())
            self.assertFalse((root / "out" / "edb_archive").exists())

    def test_cli_archive_ignores_invalid_unowned_and_external_summary_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "out"
            output.mkdir()
            source = root / "source.png"
            owned = output / "source.edb"
            owned.write_bytes(b"previous-edb")
            external = root / "external.edb"
            external.write_bytes(b"external-edb")
            valid = {"source": str(source), "output_dir": str(output), "edb_path": str(owned)}
            for previous in (
                None, [], {}, {**valid, "source": str(root / "other.png")},
                {**valid, "output_dir": str(root / "other")}, {**valid, "edb_paths": "invalid"},
                {**valid, "edb_paths": [str(owned), str(external)]},
            ):
                with self.subTest(previous=previous):
                    archived = problem_board._archive_previous_cli_edbs(
                        previous, source_path=source, output_dir=output, current_edb_paths=[]
                    )
                    self.assertEqual([], archived)
                    self.assertEqual(b"previous-edb", owned.read_bytes())
                    self.assertEqual(b"external-edb", external.read_bytes())
            self.assertFalse((output / "edb_archive").exists())

    def test_cli_archive_preserves_other_and_nested_edbs_even_if_summary_names_them(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "out"
            output.mkdir()
            source = root / "source.png"
            unrelated = output / "user_notes.edb"
            nested = output / "nested" / "source.edb"
            nested.parent.mkdir()
            for path in (unrelated, nested):
                path.write_bytes(b"user-owned-edb")
                previous = {"source": str(source), "output_dir": str(output), "edb_path": str(path)}
                self.assertEqual([], problem_board._archive_previous_cli_edbs(
                    previous, source_path=source, output_dir=output, current_edb_paths=[]
                ))
                self.assertEqual(b"user-owned-edb", path.read_bytes())
            self.assertFalse((output / "edb_archive").exists())

    def test_cli_archive_failure_preserves_old_file_and_keeps_successful_export(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            page = self._legacy_page(root)
            with Image.open(page.source_path) as source_image:
                image = source_image.copy()
            prepared = PreparedPage(page.page_id, page.source_path, 1, image, image.size)
            single = self._run_cli(root, page, prepared, 1)
            old_path = Path(single["edb_path"])
            old_bytes = old_path.read_bytes()
            with mock.patch.object(Path, "replace", side_effect=OSError("file locked")):
                split = self._run_cli(root, page, prepared, 43)
            self.assertEqual(2, split["edb_part_count"])
            self.assertEqual([], split["archived_edb_paths"])
            self.assertEqual(old_bytes, old_path.read_bytes())
            self.assertIn("file locked", split["edb_archive_warnings"][0])
            self.assertEqual(43, sum(parse_edb(Path(path)).record_count_actual for path in split["edb_paths"]))
            summary = json.loads((root / "out" / "board_run_summary.json").read_text())
            self.assertEqual(split["edb_archive_warnings"], summary["edb_archive_warnings"])

    def test_cli_archive_moves_only_obsolete_part_when_part_count_decreases(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "out"
            output.mkdir()
            source = root / "source.png"
            paths = [output / f"source_part{index:02d}.edb" for index in range(1, 4)]
            for path in paths:
                path.write_bytes(path.name.encode())
            previous = {"source": str(source), "output_dir": str(output), "edb_paths": [str(path) for path in paths]}
            archived = problem_board._archive_previous_cli_edbs(
                previous, source_path=source, output_dir=output, current_edb_paths=paths[:2]
            )
            self.assertEqual(1, len(archived))
            self.assertEqual(paths[2].name.encode(), Path(archived[0]).read_bytes())
            self.assertTrue(all(path.is_file() for path in paths[:2]))
            self.assertFalse(paths[2].exists())

    def test_continuous_placement_reserves_an_explicit_trailing_gap(self):
        placements = place_problems(
            [
                ProblemLayoutInput("first", actual_content_height_pages=1.0,
                                   metadata={"input_intent": "page-as-is", "placement_gap_after_pages": 0.25}),
                ProblemLayoutInput("second", actual_content_height_pages=1.0,
                                   metadata={"input_intent": "page-as-is"}),
            ],
            template=LayoutTemplate("continuous"),
        )
        self.assertEqual(1.0, placements[0].actual_bottom_y_pages)
        self.assertEqual(1.25, placements[0].snapped_next_start_y_pages)
        self.assertEqual(1.25, placements[1].start_y_pages)

    def test_continuous_image_records_preserve_an_explicit_trailing_gap(self):
        for crop_format in ("v1", "v2"):
            with self.subTest(crop_format=crop_format), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                page = self._legacy_page(root)
                with Image.open(page.source_path) as source_image:
                    image = source_image.copy()
                prepared = PreparedPage(page.page_id, page.source_path, 1, image, image.size)
                entries = [self._entry(page, prepared, index) for index in range(2)]
                for entry in entries:
                    entry.input_intent = "page-as-is"
                entries[0].placement_gap_after_pages = 0.25
                _records, placements = problem_board.build_image_only_records(
                    entries, LayoutTemplate("continuous"), dark_board=False, crop_format=crop_format,
                )
                self.assertEqual(147.5, placements[0]["record_gap_px"])
                self.assertAlmostEqual(
                    0.25, placements[1]["record_top_y_pages"] - placements[0]["record_bottom_y_pages"], places=5,
                )

    def test_mixed_long_page_and_regular_problem_follow_final_rendered_cursor(self):
        for crop_format in ("v1", "v2"):
            with self.subTest(crop_format=crop_format), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                entries = [
                    self._image_entry(root, "long-page", (128, 1600), intent="page-as-is", scale=3),
                    self._image_entry(root, "regular", (140, 188), scale=1.26),
                    self._image_entry(root, "second-page", (128, 1600), intent="page-as-is", scale=3),
                ]
                template = LayoutTemplate("mixed-intents", board_page_count=50)
                _, placements = problem_board.build_image_only_records(
                    entries, template, dark_board=False, crop_format=crop_format,
                    generate_records=False, expand_board_capacity=False,
                )
                self.assertGreater(placements[0]["actual_content_height_pages"], problem_board.MAX_HEIGHT_PAGES)
                for previous, current in zip(placements, placements[1:]):
                    self.assertAlmostEqual(previous["snapped_next_start_y_pages"], current["start_y_pages"], places=5)
                    self.assertGreaterEqual(current["record_top_y_pages"] + 1e-5, previous["record_bottom_y_pages"])
                # The regular problem must snap on the board's absolute slot
                # grid after the width-limited continuous record, rather than
                # translating an obsolete, undersized initial placement.
                if placements[1]["actual_content_height_pages"] * 1.26 > 1.2:
                    self.assertAlmostEqual(
                        round(placements[1]["snapped_next_start_y_pages"] / 1.2),
                        placements[1]["snapped_next_start_y_pages"] / 1.2,
                        places=5,
                    )
                else:
                    self.assertAlmostEqual(1.2, placements[1]["snapped_next_start_y_pages"] - placements[1]["start_y_pages"], places=5)

    def test_mixed_long_flow_plan_and_written_parts_agree_without_overlap(self):
        for crop_format in ("v1", "v2"):
            with self.subTest(crop_format=crop_format), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                entries = [
                    self._image_entry(root, f"item-{index}", (128, 1600) if index % 2 == 0 else (140, 188),
                                      intent="page-as-is" if index % 2 == 0 else "single-problem",
                                      scale=3 if index % 2 == 0 else 1.26)
                    for index in range(5)
                ]
                template = LayoutTemplate("mixed-intents", board_page_count=50)
                kwargs = dict(record_mode="image-only", text_confidence_threshold=0.8, dark_board=False,
                              board_theme="green", crop_format=crop_format)
                plan = problem_board.plan_classin_limited_edb_parts(entries, template, root, **kwargs)
                parts = problem_board.write_classin_limited_edb_files(entries, template, root, "mixed.edb", **kwargs)
                self.assertGreaterEqual(len(parts), 2)
                self.assertEqual([part["problemIds"] for part in plan], [part["problemIds"] for part in parts])
                self.assertEqual(5, sum(parse_edb(Path(part["edbPath"])).record_count_actual for part in parts))
                for planned, part in zip(plan, parts):
                    self.assertAlmostEqual(planned["estimatedFlowEndPages"], part["flowEndPages"], places=5)
                    parsed = parse_edb(Path(part["edbPath"]))
                    self.assertEqual(50, parsed.page_count_hint)
                    for record in parsed.records:
                        self.assertLessEqual((record.pos_y + record.height_hint) * 50, 50 + 1e-5)
                    for previous, current in zip(parsed.records, parsed.records[1:]):
                        self.assertGreaterEqual(current.pos_y + 1e-6, previous.pos_y + previous.height_hint)

    def test_tall_regular_and_continuous_image_flows_keep_real_height_reservations(self):
        for crop_format in ("v1", "v2"):
            for intent in ("single-problem", "page-as-is"):
                with self.subTest(crop_format=crop_format, intent=intent), tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    entries = [self._image_entry(root, f"tall-{i}", (128, 1600), intent=intent) for i in range(2)]
                    _, placements = problem_board.build_image_only_records(
                        entries, LayoutTemplate("tall"), dark_board=False, crop_format=crop_format,
                        generate_records=False, expand_board_capacity=False,
                    )
                    self.assertGreater(placements[0]["rendered_height_pages"], problem_board.MAX_HEIGHT_PAGES)
                    self.assertGreaterEqual(placements[1]["record_top_y_pages"], placements[0]["record_bottom_y_pages"])

    def test_mixed_flow_preserves_explicit_zero_and_positive_continuous_gaps(self):
        for crop_format in ("v1", "v2"):
            for gap in (0, 0.25):
                with self.subTest(crop_format=crop_format, gap=gap), tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    entries = [self._image_entry(root, "long", (128, 1600), intent="page-as-is", scale=3),
                               self._image_entry(root, "short", (140, 188))]
                    entries[0].placement_gap_after_pages = gap
                    _, placements = problem_board.build_image_only_records(
                        entries, LayoutTemplate("gaps"), dark_board=False, crop_format=crop_format,
                        generate_records=False, expand_board_capacity=False,
                    )
                    self.assertAlmostEqual(gap, placements[1]["record_top_y_pages"] - placements[0]["record_bottom_y_pages"], places=5)

    def test_image_record_geometry_matches_final_output_and_restored_crop(self):
        from app_server import rewrite_session_for_http

        for crop_format in ("v1", "v2"):
            with self.subTest(crop_format=crop_format), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                entry = self._image_entry(root, "long", (128, 1600), intent="page-as-is", scale=3)
                template = LayoutTemplate("geometry", board_page_count=50)
                _, placements = problem_board.build_image_only_records(
                    [entry], template, dark_board=False, crop_format=crop_format,
                    generate_records=False, expand_board_capacity=False,
                )
                session = problem_board.build_ui_session(
                    [], placements, root, None, [entry.source_path], record_mode="image-only",
                    template=template, input_intent="page-as-is", crop_format=crop_format,
                )
                geometry = session["problems"][0]["imageRecordGeometry"]
                effective_scale = min(3, geometry["maxDisplayWidthPx"] / geometry["baseDisplayWidthPx"])
                self.assertAlmostEqual(placements[0]["rendered_height_pages"], geometry["baseHeightPages"] * effective_scale, places=5)
                self.assertAlmostEqual(placements[0]["snapped_next_start_y_pages"],
                                       geometry["baseHeightPages"] * effective_scale + geometry["continuousGapPages"], places=5)

                # An old session may carry a capped extraction estimate. The
                # HTTP projection reads the current crop, adds geometry, and
                # leaves the persisted placement and source session intact.
                old_problem = {"id": "legacy", "imagePath": entry.crop_path.as_uri(), "actualHeightPages": 4.8,
                               "placementScaleRatio": 3, "inputIntent": "page-as-is"}
                old_session = {"problems": [old_problem], "crop_format": crop_format, "record_mode": "image-only"}
                restored = rewrite_session_for_http(old_session)
                self.assertNotIn("imageRecordGeometry", old_problem)
                self.assertEqual(4.8, restored["problems"][0]["actualHeightPages"])
                self.assertAlmostEqual(geometry["baseHeightPages"], restored["problems"][0]["imageRecordGeometry"]["baseHeightPages"], places=5)
                Image.new("RGB", (128, 800), "white").save(entry.crop_path)
                cropped = rewrite_session_for_http(old_session)
                self.assertAlmostEqual(geometry["baseHeightPages"] / 2,
                                       cropped["problems"][0]["imageRecordGeometry"]["baseHeightPages"], places=5)

    def test_geometry_refresh_skips_missing_assets_text_and_preserved_source_layout(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            entry = self._image_entry(root, "present", (128, 1600))
            problems = [{"id": "missing", "imagePath": str(root / "missing.png")},
                        {"id": "text", "imagePath": entry.crop_path.as_uri(), "recordMode": "mixed"}]
            session = {"problems": problems, "crop_format": "v1"}
            problem_board.annotate_session_image_record_geometry(session)
            self.assertTrue(all("imageRecordGeometry" not in problem for problem in problems))
            session["problems"] = [{"id": "source-layout", "imagePath": entry.crop_path.as_uri()}]
            session["template"] = {"metadata": {"preserve_source_layout": True}}
            problem_board.annotate_session_image_record_geometry(session)
            self.assertNotIn("imageRecordGeometry", session["problems"][0])

    def test_geometry_refresh_ignores_nonfinite_legacy_template_ratios(self):
        from app_server import rewrite_session_for_http

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            entry = self._image_entry(root, "present", (128, 1600))
            for ratio in ("nan", "inf", "-inf", -0.5, 2):
                with self.subTest(ratio=ratio):
                    session = {"problems": [{"id": "legacy", "imagePath": entry.crop_path.as_uri()}],
                               "template": {"fixed_left_zone_ratio": ratio}, "crop_format": "v1"}
                    projected = rewrite_session_for_http(session)
                    self.assertNotIn("imageRecordGeometry", projected["problems"][0])
                    json.dumps(projected, allow_nan=False)

    def test_publish_preserves_requested_scale_and_preflight_uses_effective_geometry(self):
        from app_server import _copy_publish_problem_layout_metadata

        effective_scale = 1142 / 540
        target = {"id": "long", "startYPages": 0, "actualHeightPages": 540 * 12.5 / 590,
                  "placementScaleRatio": effective_scale,
                  "layoutDiagnostics": {"placementScaleRatio": effective_scale},
                  "imageRecordGeometry": {"baseDisplayWidthPx": 540, "baseHeightPages": 540 * 12.5 / 590,
                                          "maxDisplayWidthPx": 1142}}
        _copy_publish_problem_layout_metadata(target, {"placementScaleRatio": 3, "preserveLegacyPlacementScale": True})
        self.assertEqual(3, target["placementScaleRatio"])
        self.assertEqual(3, target["placement_scale_ratio"])
        self.assertTrue(target["preserveLegacyPlacementScale"])
        self.assertEqual(effective_scale, target["layoutDiagnostics"]["placementScaleRatio"])
        next_problem = {"id": "short", "startYPages": 1142 * 12.5 / 590 + 20 / 590,
                        "actualHeightPages": 1.2, "placementScaleRatio": 1}
        self.assertEqual([], problem_board._classin_board_placement_overlap_issues([target, next_problem]))
        target["preserveLegacyPlacementScale"] = False
        target["preserve_legacy_placement_scale"] = False
        next_problem["startYPages"] = target["actualHeightPages"] * problem_board.PLACEMENT_SCALE_MAX + 20 / 590
        self.assertEqual([], problem_board._classin_board_placement_overlap_issues([target, next_problem]))
        target.pop("imageRecordGeometry")
        self.assertEqual(1, len(problem_board._classin_board_placement_overlap_issues([target, next_problem])))

    def test_true_rendered_single_image_over_classin_limit_is_still_rejected(self):
        for crop_format in ("v1", "v2"):
            with self.subTest(crop_format=crop_format), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                entries = [self._image_entry(root, "oversize", (128, 6400), intent="page-as-is", scale=3)]
                kwargs = dict(record_mode="image-only", text_confidence_threshold=0.8, dark_board=False,
                              board_theme="green", crop_format=crop_format)
                with self.assertRaisesRegex(ValueError, "50-page limit"):
                    problem_board.plan_classin_limited_edb_parts(entries, LayoutTemplate("limit"), root, **kwargs)
                with self.assertRaisesRegex(ValueError, "50-page limit"):
                    problem_board.write_classin_limited_edb_files(entries, LayoutTemplate("limit"), root, "oversize.edb", **kwargs)
                self.assertEqual([], list(root.glob("*.edb")))


if __name__ == "__main__":
    unittest.main()
