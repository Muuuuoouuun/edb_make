import os
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import build_problem_board_edb as problem_board
import upscayl_backend
from upscayl_backend import UpscaylAutoResult, UpscaylInstallation


class TestUpscaylBackend(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_auto = os.environ.get("EDB_AUTO_UPSCAYL")
        os.environ["EDB_AUTO_UPSCAYL"] = "1"
        upscayl_backend.clear_upscayl_runtime_cache()

    def tearDown(self) -> None:
        if self._previous_auto is None:
            os.environ.pop("EDB_AUTO_UPSCAYL", None)
        else:
            os.environ["EDB_AUTO_UPSCAYL"] = self._previous_auto
        upscayl_backend.clear_upscayl_runtime_cache()

    def _installation(self, root: Path) -> UpscaylInstallation:
        binary = root / ("upscayl-bin.exe" if os.name == "nt" else "upscayl-bin")
        binary.write_bytes(b"fake")
        models = root / "models"
        models.mkdir()
        (models / "upscayl-lite-4x.bin").write_bytes(b"model")
        (models / "upscayl-lite-4x.param").write_text("model", encoding="utf-8")
        return UpscaylInstallation(binary_path=binary, models_dir=models)

    def test_low_resolution_image_runs_lite_with_bounded_command(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            installation = self._installation(Path(raw_tmp))
            source = Image.new("RGBA", (800, 450), (255, 255, 255, 0))

            def fake_run(command, **kwargs):
                output_path = Path(command[command.index("-o") + 1])
                Image.new("RGBA", (1600, 900), (248, 248, 246, 128)).save(output_path)
                self.assertIsInstance(command, list)
                self.assertNotIn("shell", kwargs)
                self.assertEqual(kwargs["timeout"], 30.0)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(upscayl_backend.subprocess, "run", side_effect=fake_run) as run:
                result = upscayl_backend.auto_upscale_image(source, installation=installation)

            self.assertTrue(result.applied)
            self.assertEqual((1600, 900), result.image.size)
            command = run.call_args.args[0]
            self.assertEqual("upscayl-lite-4x", command[command.index("-n") + 1])
            self.assertEqual("1600", command[command.index("-w") + 1])

    def test_large_image_skips_without_discovery_or_process(self) -> None:
        source = Image.new("RGB", (900, 500), "white")
        with (
            patch.object(upscayl_backend, "discover_upscayl_installation") as discover,
            patch.object(upscayl_backend.subprocess, "run") as run,
        ):
            result = upscayl_backend.auto_upscale_image(source)

        self.assertEqual("skipped", result.status)
        self.assertEqual("source_already_large", result.reason)
        self.assertIs(source, result.image)
        discover.assert_not_called()
        run.assert_not_called()

    def test_missing_installation_keeps_original_image(self) -> None:
        source = Image.new("RGB", (640, 360), "white")
        with patch.object(upscayl_backend, "discover_upscayl_installation", return_value=None):
            result = upscayl_backend.auto_upscale_image(source)

        self.assertEqual("unavailable", result.status)
        self.assertEqual("installation_not_found", result.reason)
        self.assertIs(source, result.image)

    def test_timeout_keeps_original_image(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            installation = self._installation(Path(raw_tmp))
            source = Image.new("RGB", (640, 360), "white")
            with patch.object(
                upscayl_backend.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("upscayl-bin", 30),
            ):
                result = upscayl_backend.auto_upscale_image(source, installation=installation)

        self.assertEqual("failed", result.status)
        self.assertEqual("timeout", result.reason)
        self.assertIs(source, result.image)
        self.assertGreater(result.cooldown_remaining_ms, 0)
        self.assertIn("original enhancement path", result.to_metadata()["fallback_message"])

    def test_failure_backoff_prevents_repeated_process_launches(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            installation = self._installation(Path(raw_tmp))
            source = Image.new("RGB", (640, 360), "white")
            completed = subprocess.CompletedProcess(
                [str(installation.binary_path)],
                1,
                "",
                "gpu unavailable",
            )
            with patch.object(
                upscayl_backend.subprocess,
                "run",
                return_value=completed,
            ) as run:
                first = upscayl_backend.auto_upscale_image(source, installation=installation)
                second = upscayl_backend.auto_upscale_image(source, installation=installation)

        self.assertEqual(1, run.call_count)
        self.assertEqual("failed", first.status)
        self.assertEqual("backoff", second.status)
        self.assertEqual("temporary_backoff:process_failed", second.reason)
        self.assertGreater(second.cooldown_remaining_ms, 0)

    def test_parallel_failures_are_serialized_before_backoff_check(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            installation = self._installation(Path(raw_tmp))
            source = Image.new("RGB", (640, 360), "white")
            completed = subprocess.CompletedProcess(
                [str(installation.binary_path)],
                1,
                "",
                "vulkan initialization failed",
            )
            with patch.object(
                upscayl_backend.subprocess,
                "run",
                return_value=completed,
            ) as run:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(
                        executor.map(
                            lambda _: upscayl_backend.auto_upscale_image(
                                source,
                                installation=installation,
                            ),
                            range(2),
                        )
                    )

        self.assertEqual(1, run.call_count)
        self.assertEqual({"failed", "backoff"}, {result.status for result in results})

    def test_environment_paths_are_discovered_without_user_ui(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            installation = self._installation(Path(raw_tmp))
            with patch.dict(
                os.environ,
                {
                    "UPSCAYL_BIN": str(installation.binary_path),
                    "UPSCAYL_MODELS_DIR": str(installation.models_dir),
                },
                clear=False,
            ):
                upscayl_backend.clear_upscayl_discovery_cache()
                discovered = upscayl_backend.discover_upscayl_installation()

        self.assertIsNotNone(discovered)
        self.assertEqual(installation.binary_path.resolve(), discovered.binary_path)
        self.assertEqual(installation.models_dir.resolve(), discovered.models_dir)

    def test_discovery_can_refresh_after_resources_are_installed(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            binary = root / ("upscayl-bin.exe" if os.name == "nt" else "upscayl-bin")
            models = root / "models"
            with patch.dict(
                os.environ,
                {"UPSCAYL_BIN": str(binary), "UPSCAYL_MODELS_DIR": str(models)},
                clear=False,
            ):
                self.assertIsNone(upscayl_backend.discover_upscayl_installation())
                binary.write_bytes(b"fake")
                models.mkdir()
                (models / "upscayl-lite-4x.bin").write_bytes(b"model")
                (models / "upscayl-lite-4x.param").write_text("model", encoding="utf-8")

                self.assertIsNone(upscayl_backend.discover_upscayl_installation())
                refreshed = upscayl_backend.discover_upscayl_installation(refresh=True)

        self.assertIsNotNone(refreshed)
        self.assertEqual(binary.resolve(), refreshed.binary_path)

    def test_negative_discovery_cache_expires_after_runtime_install(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            binary = root / ("upscayl-bin.exe" if os.name == "nt" else "upscayl-bin")
            models = root / "models"
            clock = {"now": 100.0}
            with (
                patch.dict(
                    os.environ,
                    {
                        "UPSCAYL_BIN": str(binary),
                        "UPSCAYL_MODELS_DIR": str(models),
                        "EDB_UPSCAYL_NEGATIVE_DISCOVERY_TTL_SECONDS": "30",
                    },
                    clear=False,
                ),
                patch.object(upscayl_backend.time, "monotonic", side_effect=lambda: clock["now"]),
            ):
                upscayl_backend.clear_upscayl_discovery_cache()
                self.assertIsNone(upscayl_backend.discover_upscayl_installation())
                binary.write_bytes(b"fake")
                models.mkdir()
                (models / "upscayl-lite-4x.bin").write_bytes(b"model")
                (models / "upscayl-lite-4x.param").write_text("model", encoding="utf-8")

                clock["now"] = 110.0
                self.assertIsNone(upscayl_backend.discover_upscayl_installation())
                clock["now"] = 131.0
                discovered = upscayl_backend.discover_upscayl_installation()

        self.assertIsNotNone(discovered)
        self.assertEqual(binary.resolve(), discovered.binary_path)


class TestStage3UpscaylIntegration(unittest.TestCase):
    def test_stage3_uses_auto_upscale_result_before_existing_postprocess(self) -> None:
        source = Image.new("RGB", (800, 450), "white")
        upscaled = Image.new("RGB", (1600, 900), "white")
        result = UpscaylAutoResult(
            image=upscaled,
            status="applied",
            reason="low_resolution_source",
            source_width=800,
            output_width=1600,
        )

        with patch.object(upscayl_backend, "auto_upscale_image", return_value=result) as auto:
            rendered = problem_board._build_transparent_reconstruction_image(source)

        auto.assert_called_once()
        self.assertEqual(1600, rendered.width)
        self.assertIn("A", rendered.getbands())

    def test_stage3_keeps_legacy_path_when_backend_raises(self) -> None:
        source = Image.new("RGB", (800, 450), "white")
        with patch.object(upscayl_backend, "auto_upscale_image", side_effect=RuntimeError("gpu unavailable")):
            rendered = problem_board._build_transparent_reconstruction_image(source)

        self.assertEqual(1600, rendered.width)
        self.assertIn("A", rendered.getbands())


if __name__ == "__main__":
    unittest.main()
