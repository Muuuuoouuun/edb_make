from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import probe_hwp_methods


class TestProbeHwpMethods(unittest.TestCase):
    def test_summarize_text_scores_numbered_problem_lines(self) -> None:
        result = probe_hwp_methods.summarize_text(
            "sample",
            "1. 첫 문제\n본문\n2. 둘째 문제",
            elapsed_s=0.1234,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(2, result["numbered"])
        self.assertEqual(0, result["stem"])
        self.assertEqual(2, result["score"])
        self.assertEqual(0.123, result["elapsed_s"])

    def test_probe_file_selects_best_method_by_score_then_chars(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            source = Path(raw_tmp) / "sample.hwp"
            source.write_bytes(b"hwp")
            methods = [
                probe_hwp_methods.ProbeMethod("short", lambda path, timeout: "1. 문제"),
                probe_hwp_methods.ProbeMethod("better", lambda path, timeout: "1. 문제\n2. 문제"),
                probe_hwp_methods.ProbeMethod("longer-tie", lambda path, timeout: "1. 문제\n2. 문제\n해설 본문"),
            ]

            row = probe_hwp_methods.probe_file(source, methods, timeout_seconds=3)

        self.assertEqual("longer-tie", row["best_method"])
        self.assertEqual(2, row["best_score"])
        self.assertEqual(3, len(row["methods"]))

    def test_probe_file_ignores_rhwp_text_spike_when_other_signal_is_strong(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            source = Path(raw_tmp) / "sample.hwp"
            source.write_bytes(b"hwp")
            methods = [
                probe_hwp_methods.ProbeMethod("hwp-hwpx-parser", lambda path, timeout: "\n".join("다음 자료에 대한 설명으로 옳은 것은?" for _ in range(20))),
                probe_hwp_methods.ProbeMethod("rhwp-text", lambda path, timeout: "\n".join(f"{number}. 선택지처럼 보이는 줄" for number in range(1, 32))),
            ]

            row = probe_hwp_methods.probe_file(source, methods, timeout_seconds=3)

        self.assertEqual("hwp-hwpx-parser", row["best_method"])
        self.assertEqual(20, row["best_score"])

    def test_write_probe_outputs_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            output_dir = Path(raw_tmp)
            rows = [
                {
                    "file": "sample.hwp",
                    "path": "/tmp/sample.hwp",
                    "best_method": "kordoc",
                    "best_score": 45,
                    "best_numbered": 45,
                    "best_stem": 0,
                    "best_chars": 1234,
                    "methods": [],
                }
            ]

            probe_hwp_methods.write_outputs(rows, output_dir)

            payload = json.loads((output_dir / "probe_summary.json").read_text(encoding="utf-8"))
            markdown = (output_dir / "probe_summary.md").read_text(encoding="utf-8")

        self.assertEqual("sample.hwp", payload["files"][0]["file"])
        self.assertIn("| sample.hwp | kordoc | 45 | 45 | 0 | 1234 |", markdown)

    def test_build_methods_includes_hwp_hwpx_parser_when_available(self) -> None:
        with (
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_pyhwp_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_unhwp_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_hwp_hwpx_parser_text_converter_commands", return_value=[["/venv/bin/python", "-c", "script"]]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_rhwp_python_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_rhwp_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_hwpilot_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_kordoc_text_converter_commands", return_value=[]),
        ):
            methods = probe_hwp_methods.build_methods()

        self.assertEqual(["hwp-hwpx-parser"], [method.name for method in methods])

    def test_build_methods_includes_rhwp_python_when_available(self) -> None:
        with (
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_pyhwp_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_unhwp_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_hwp_hwpx_parser_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_rhwp_python_text_converter_commands", return_value=[["/venv/bin/python", "-c", "script"]]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_rhwp_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_hwpilot_text_converter_commands", return_value=[]),
            mock.patch.object(probe_hwp_methods.preprocess, "_iter_kordoc_text_converter_commands", return_value=[]),
        ):
            methods = probe_hwp_methods.build_methods()

        self.assertEqual(["rhwp-python"], [method.name for method in methods])


if __name__ == "__main__":
    unittest.main()
