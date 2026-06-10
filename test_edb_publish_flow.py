import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from PIL import Image, ImageDraw

from app_server import content_disposition_attachment, validate_edb_file
from build_problem_board_edb import (
    ONE_PROBLEM_SLOT_HEIGHT_PAGES,
    ProblemEntry,
    build_problem_entries,
    build_image_only_records,
    run_problem_export,
    _pad_problem_crop_bottom,
    _trim_edge_vertical_guides,
)
from edb_builder import CROP_FORMAT_V1
from layout_template_schema import LayoutTemplate
from preprocess import PreparedPage
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject


def _path_from_file_uri(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return Path(value)
    return Path(url2pathname(parsed.path))


class TestEdbPublishFlow(unittest.TestCase):
    def _make_source_image(self, path: Path) -> None:
        image = Image.new("RGB", (860, 620), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((48, 48, 812, 572), outline="black", width=4)
        draw.text((80, 92), "1. Smoke problem", fill="black")
        draw.text((80, 160), "A generated EDB should validate.", fill="black")
        image.save(path)

    def _make_problem_entry(self, root: Path, name: str, bounds: Box) -> ProblemEntry:
        crop_path = root / f"{name}.png"
        Image.new("RGB", (int(bounds.width), int(bounds.height)), "white").save(crop_path)
        prepared = PreparedPage(
            page_id="page-1",
            source_path=str(root / "source.png"),
            page_number=1,
            image=Image.new("RGB", (900, 1200), "white"),
            original_size=(900, 1200),
        )
        return ProblemEntry(
            problem_id=name,
            title=name,
            problem_number=None,
            subject=Subject.UNKNOWN,
            source_page_id="page-1",
            source_path=str(root / "source.png"),
            prepared_page=prepared,
            bounds=bounds,
            crop_path=crop_path,
            board_render_path=crop_path,
            blocks=[],
            actual_height_pages=0.72,
            overflow_allowed=False,
            reading_heavy=False,
            risk_flags=[],
        )

    def test_generated_single_problem_edb_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=True,
            )

            validation = validate_edb_file(result["edb_path"], expected_min_records=1)
            self.assertEqual(validation["recordCountActual"], 1)
            self.assertGreater(validation["outerSize"], 0)

    def test_korean_edb_filename_download_header_is_http_safe(self):
        header = content_disposition_attachment(
            "20260610_223707_1781098627053740000_고1_샘플_7f796ebe63.edb"
        )
        header.encode("latin-1")
        self.assertIn('filename="20260610_223707_1781098627053740000__1____7f796ebe63.edb"', header)
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%EA%B3%A01_%EC%83%98%ED%94%8C", header)

    def test_v1_multi_problem_export_uses_one_problem_per_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [
                self._make_problem_entry(root, "problem-1", Box(0, 40, 380, 300)),
                self._make_problem_entry(root, "problem-2", Box(410, 40, 380, 300)),
            ]
            template = LayoutTemplate(
                name="academy-default",
                base_slot_height_pages=ONE_PROBLEM_SLOT_HEIGHT_PAGES,
            )

            _records, placements = build_image_only_records(
                entries,
                template,
                crop_format=CROP_FORMAT_V1,
            )

            self.assertEqual([0.0, 1.2], [item["start_y_pages"] for item in placements])
            self.assertEqual([1.2, 2.4], [item["snapped_next_start_y_pages"] for item in placements])
            self.assertEqual([1.0, 1.0], [item["placement_scale_ratio"] for item in placements])

    def test_problem_crops_use_same_column_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (1000, 1400), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(1000, 1400),
            )

            blocks: list[ContentBlock] = []
            problems: list[ProblemUnit] = []

            def add_problem(number: int, column: int, top: float, bottom: float, order: int) -> None:
                block_id = f"b-{number}"
                blocks.append(
                    ContentBlock(
                        block_id=block_id,
                        block_type=BlockType.STEM,
                        bbox=Box(80 + column * 460, top, 360, bottom - top),
                        reading_order=order,
                        text=f"{number}. problem",
                        metadata={"column_index": column, "question_band_index": order},
                    )
                )
                problems.append(
                    ProblemUnit(
                        unit_id=f"problem-{number}",
                        subject=Subject.MATH,
                        title=f"{number}.",
                        stem_block_ids=[block_id],
                        metadata={
                            "problem_number": number,
                            "column_index": column,
                            "question_band_index": order,
                        },
                    )
                )

            add_problem(7, 0, 100, 250, 0)
            add_problem(8, 0, 500, 650, 1)
            add_problem(9, 0, 900, 1050, 2)
            add_problem(10, 1, 100, 280, 3)
            add_problem(11, 1, 520, 894, 4)
            add_problem(12, 1, 900, 1080, 5)

            page = PageModel(
                page_id="page-1",
                width_px=1000,
                height_px=1400,
                subject=Subject.MATH,
                source_path=str(source),
                blocks=blocks,
                problems=problems,
            )

            entries = build_problem_entries(
                [prepared],
                [page],
                root / "out",
                LayoutTemplate(name="academy-default"),
            )

            problem_11 = next(entry for entry in entries if entry.problem_number == 11)
            problem_12 = next(entry for entry in entries if entry.problem_number == 12)
            self.assertLessEqual(problem_11.bounds.bottom, 895)
            self.assertLessEqual(problem_12.bounds.top, 886)

    def test_edge_vertical_guides_are_trimmed_without_removing_internal_lines(self):
        image = Image.new("RGB", (120, 90), "white")
        draw = ImageDraw.Draw(image)
        draw.line((8, 0, 8, 89), fill=(170, 170, 170), width=2)
        draw.line((112, 0, 112, 89), fill=(170, 170, 170), width=2)
        draw.line((60, 10, 60, 80), fill="black", width=2)
        trimmed = _trim_edge_vertical_guides(image)

        self.assertLess(trimmed.width, image.width)
        self.assertEqual(trimmed.height, image.height)
        gray = trimmed.convert("L")
        internal_dark_columns = [
            x
            for x in range(10, trimmed.width - 10)
            if sum(1 for y in range(trimmed.height) if gray.getpixel((x, y)) < 80) >= 50
        ]
        self.assertTrue(internal_dark_columns)

    def test_problem_crop_bottom_padding_preserves_last_choice(self):
        image = Image.new("RGB", (120, 80), "white")
        draw = ImageDraw.Draw(image)
        draw.text((12, 64), "⑤", fill="black")

        padded = _pad_problem_crop_bottom(image, padding_px=18)

        self.assertEqual(padded.size, (120, 98))
        self.assertEqual(padded.getpixel((12, 96)), (255, 255, 255))

    def test_session_source_images_point_to_rendered_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=False,
            )

            session = result["ui_session"]
            page_source = Path(session["pages"][0]["sourceImagePath"])
            problem_source = _path_from_file_uri(session["problems"][0]["sourceImagePath"])
            self.assertEqual(page_source.suffix.lower(), ".png")
            self.assertEqual(problem_source.suffix.lower(), ".png")
            self.assertTrue(page_source.exists())
            self.assertTrue(problem_source.exists())


if __name__ == "__main__":
    unittest.main()
