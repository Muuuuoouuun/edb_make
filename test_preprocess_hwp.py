from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import preprocess


class TestPreprocessHwp(unittest.TestCase):
    def test_hwp_routes_through_converted_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "exam.hwp"
            source.write_bytes(b"hwp")
            converted = tmp_path / "converted" / "exam.pdf"
            rendered = tmp_path / "rendered.png"
            Image.new("RGB", (40, 50), "white").save(rendered)

            def fake_convert_hwp_to_pdf(src, out_dir):
                self.assertEqual(source, src)
                self.assertEqual(tmp_path / "out" / "converted", out_dir)
                return converted

            def fake_render_pdf_pages(src, out_dir, dpi):
                self.assertEqual(converted, src)
                self.assertEqual(tmp_path / "out" / "rendered", out_dir)
                self.assertEqual(144, dpi)
                return [
                    preprocess.NormalizedPageImage(
                        page_id="page-001",
                        source_path=str(src),
                        normalized_path=str(rendered),
                        page_index=0,
                        width_px=40,
                        height_px=50,
                        metadata={"source_type": "pdf"},
                    )
                ]

            with (
                mock.patch.object(preprocess, "convert_hwp_to_pdf", side_effect=fake_convert_hwp_to_pdf, create=True),
                mock.patch.object(preprocess, "render_pdf_pages", side_effect=fake_render_pdf_pages),
            ):
                pages = preprocess.prepare_pages(source, tmp_path / "out", dpi=144)

            self.assertEqual(1, len(pages))
            self.assertEqual("hwp", pages[0].metadata["source_type"])
            self.assertIs(True, pages[0].metadata["document_like"])
            self.assertEqual(str(source), pages[0].metadata["source_hwp_path"])
            self.assertEqual(str(converted), pages[0].metadata["converted_pdf_path"])

    def test_hwp_records_conversion_quality_from_rendered_pdf_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "exam.hwpx"
            source.write_bytes(b"hwpx")
            converted = tmp_path / "converted" / "exam.pdf"
            page_with_marker = tmp_path / "page_with_marker.png"
            blank_page = tmp_path / "blank_page.png"
            image = Image.new("RGB", (80, 80), "white")
            for x in range(8, 24):
                for y in range(8, 24):
                    image.putpixel((x, y), (0, 0, 0))
            image.save(page_with_marker)
            Image.new("RGB", (80, 80), "white").save(blank_page)

            rendered_pages = [
                preprocess.NormalizedPageImage(
                    page_id="page-001",
                    source_path=str(converted),
                    normalized_path=str(page_with_marker),
                    page_index=0,
                    width_px=80,
                    height_px=80,
                    metadata={
                        "source_type": "pdf",
                        "pdf_problem_markers": [{"number": 1}, {"number": 2}],
                    },
                ),
                preprocess.NormalizedPageImage(
                    page_id="page-002",
                    source_path=str(converted),
                    normalized_path=str(blank_page),
                    page_index=1,
                    width_px=80,
                    height_px=80,
                    metadata={"source_type": "pdf", "pdf_problem_markers": []},
                ),
            ]

            with (
                mock.patch.object(preprocess, "convert_hwp_to_pdf", return_value=converted, create=True),
                mock.patch.object(preprocess, "render_pdf_pages", return_value=rendered_pages),
            ):
                pages = preprocess.prepare_pages(source, tmp_path / "out", dpi=144)

            quality = pages[0].metadata["hwp_conversion_quality"]
            self.assertEqual(2, quality["page_count"])
            self.assertEqual(2, quality["pdf_text_marker_count"])
            self.assertEqual(1, quality["pdf_pages_with_text_markers"])
            self.assertEqual(1, quality["pdf_pages_without_text_markers"])
            self.assertEqual(1, quality["blank_page_count"])
            self.assertTrue(quality["has_pdf_text_markers"])
            self.assertIn("blank_pages_detected", quality["warnings"])
            self.assertEqual(quality, pages[1].metadata["hwp_conversion_quality"])

    def test_convert_hwp_to_pdf_uses_soffice_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "worksheet.hwp"
            source.write_bytes(b"hwp")
            output_dir = tmp_path / "converted"
            expected_pdf = output_dir / "worksheet.pdf"

            def fake_run(cmd, check, stdout, stderr, text, timeout):
                self.assertEqual(["/usr/bin/soffice", "--headless"], cmd[:2])
                self.assertIn("--convert-to", cmd)
                self.assertIn("pdf", cmd)
                self.assertIn(str(output_dir), cmd)
                output_dir.mkdir(parents=True, exist_ok=True)
                expected_pdf.write_bytes(b"%PDF-1.4")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["/usr/bin/soffice", "--headless"]], create=True),
                mock.patch.object(preprocess.subprocess, "run", side_effect=fake_run),
            ):
                self.assertEqual(expected_pdf, preprocess.convert_hwp_to_pdf(source, output_dir))

    def test_convert_hwp_to_pdf_reuses_cache_for_unchanged_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "worksheet.hwp"
            source.write_bytes(b"hwp v1")
            output_dir = tmp_path / "converted"
            expected_pdf = output_dir / "worksheet.pdf"
            run_count = 0

            def fake_run(cmd, check, stdout, stderr, text, timeout):
                nonlocal run_count
                run_count += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                expected_pdf.write_bytes(b"%PDF-1.4")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["/usr/bin/soffice", "--headless"]], create=True),
                mock.patch.object(preprocess.subprocess, "run", side_effect=fake_run),
            ):
                self.assertEqual(expected_pdf, preprocess.convert_hwp_to_pdf(source, output_dir))
                self.assertEqual(expected_pdf, preprocess.convert_hwp_to_pdf(source, output_dir))

            self.assertEqual(1, run_count)

    def test_convert_hwp_to_pdf_invalidates_cache_when_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "worksheet.hwp"
            source.write_bytes(b"hwp v1")
            output_dir = tmp_path / "converted"
            expected_pdf = output_dir / "worksheet.pdf"
            run_count = 0

            def fake_run(cmd, check, stdout, stderr, text, timeout):
                nonlocal run_count
                run_count += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                expected_pdf.write_bytes(f"%PDF run {run_count}".encode("ascii"))
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["/usr/bin/soffice", "--headless"]], create=True),
                mock.patch.object(preprocess.subprocess, "run", side_effect=fake_run),
            ):
                self.assertEqual(expected_pdf, preprocess.convert_hwp_to_pdf(source, output_dir))
                source.write_bytes(b"hwp v2")
                self.assertEqual(expected_pdf, preprocess.convert_hwp_to_pdf(source, output_dir))

            self.assertEqual(2, run_count)
            self.assertEqual(b"%PDF run 2", expected_pdf.read_bytes())

    def test_convert_hwp_to_pdf_uses_hwpilot_hwpx_bridge_after_direct_pdf_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "worksheet.hwp"
            source.write_bytes(b"hwp")
            output_dir = tmp_path / "converted"
            expected_pdf = output_dir / "worksheet.pdf"
            calls = []

            def fake_run(cmd, check, stdout, stderr, text, timeout):
                calls.append(cmd)
                if cmd[0] == "/usr/local/bin/hwpilot":
                    Path(cmd[-1]).write_bytes(b"hwpx")
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if len(calls) == 3:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    expected_pdf.write_bytes(b"%PDF-1.4")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["/usr/bin/soffice", "--headless"]], create=True),
                mock.patch.object(preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[["/usr/local/bin/hwpilot"]], create=True),
                mock.patch.object(preprocess.subprocess, "run", side_effect=fake_run),
            ):
                self.assertEqual(expected_pdf, preprocess.convert_hwp_to_pdf(source, output_dir))

            self.assertEqual("/usr/bin/soffice", calls[0][0])
            self.assertEqual(["/usr/local/bin/hwpilot", "convert", str(source)], calls[1][:3])
            self.assertEqual(output_dir / "_hwpilot" / "worksheet.hwpx", Path(calls[1][3]))
            self.assertEqual("/usr/bin/soffice", calls[2][0])
            self.assertEqual(str(output_dir / "_hwpilot" / "worksheet.hwpx"), calls[2][-1])

    def test_convert_hwp_to_pdf_without_converter_raises_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "worksheet.hwp"
            source.write_bytes(b"hwp")

            with mock.patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[], create=True):
                with self.assertRaisesRegex(ValueError, "HWP.*LibreOffice|LibreOffice.*HWP"):
                    preprocess.convert_hwp_to_pdf(source, tmp_path / "converted")


if __name__ == "__main__":
    unittest.main()
