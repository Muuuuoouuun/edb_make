import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from scripts.work3_pdf_probe import (
    ROUTE_PDF_STRUCTURE,
    ROUTE_RASTER_OCR,
    profile_pdf,
)


class TestWork3PdfProbe(unittest.TestCase):
    def _native_text_pdf(self, path: Path) -> None:
        document = fitz.open()
        page = document.new_page(width=595, height=842)
        for row in range(18):
            page.insert_text(
                (48, 80 + row * 34),
                f"Passage line {row:02d}: structure-aware extraction keeps reading order and bounds.",
                fontsize=11,
            )
        document.save(path)
        document.close()

    def _scanned_pdf(self, path: Path) -> None:
        image = Image.new("RGB", (900, 1200), "white")
        draw = ImageDraw.Draw(image)
        for row in range(20):
            draw.text((60, 70 + row * 48), f"scanned passage line {row:02d}", fill="black")
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "scan.png"
            image.save(image_path)
            document = fitz.open()
            page = document.new_page(width=595, height=842)
            page.insert_image(page.rect, filename=image_path)
            document.save(path)
            document.close()

    def test_native_text_page_routes_to_pdf_structure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "native.pdf"
            self._native_text_pdf(pdf_path)

            result = profile_pdf(pdf_path, subject="korean", render_dpis=(72,))

            self.assertEqual("pdf-structure-first", result["recommended_pipeline"])
            self.assertEqual(1.0, result["structure_ready_page_ratio"])
            self.assertEqual(ROUTE_PDF_STRUCTURE, result["pages"][0]["route"])
            self.assertGreater(result["pages"][0]["char_count"], 80)
            self.assertEqual(0.0, result["external_api_cost_usd"])

    def test_scanned_page_routes_to_raster_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan.pdf"
            self._scanned_pdf(pdf_path)

            result = profile_pdf(pdf_path, subject="english", render_dpis=(72, 144))

            self.assertEqual("raster-ocr", result["recommended_pipeline"])
            self.assertEqual(ROUTE_RASTER_OCR, result["pages"][0]["route"])
            self.assertEqual(0, result["pages"][0]["char_count"])
            self.assertEqual(1, result["estimated_ocr_page_count"])
            self.assertEqual([72, 144], [row["dpi"] for row in result["render_summary"]])

    def test_mixed_document_recommends_page_level_hybrid_routing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            native_path = Path(temp_dir) / "native.pdf"
            scan_path = Path(temp_dir) / "scan.pdf"
            combined_path = Path(temp_dir) / "combined.pdf"
            self._native_text_pdf(native_path)
            self._scanned_pdf(scan_path)
            combined = fitz.open()
            for source_path in (native_path, scan_path):
                with fitz.open(source_path) as source:
                    combined.insert_pdf(source)
            combined.save(combined_path)
            combined.close()

            result = profile_pdf(combined_path, subject="korean", render_dpis=(72,))

            self.assertEqual("hybrid-page-routing", result["recommended_pipeline"])
            self.assertEqual(1, result["route_counts"][ROUTE_PDF_STRUCTURE])
            self.assertEqual(1, result["route_counts"][ROUTE_RASTER_OCR])

    def test_scope_is_limited_to_korean_and_english(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "native.pdf"
            self._native_text_pdf(pdf_path)

            with self.assertRaisesRegex(ValueError, "korean, english"):
                profile_pdf(pdf_path, subject="math", render_dpis=(72,))


if __name__ == "__main__":
    unittest.main()
