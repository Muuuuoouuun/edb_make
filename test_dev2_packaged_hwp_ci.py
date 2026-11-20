"""Round-2 packaged-hwp-ci chain: release gate, installed-app HWP, converter timeouts,
LibreOffice readiness, protected HWP copy and the testing docs."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from packaging.utils import canonicalize_name
from PIL import Image

import app_server
import preprocess
from scripts.build_release_metadata import load_release_lock


PROJECT_ROOT = Path(__file__).resolve().parent


# R18 -------------------------------------------------------------------------------------------

class TestPrivateQualityGate(unittest.TestCase):
    def test_private_gate_does_not_forbid_locked_release_packages(self) -> None:
        source = (PROJECT_ROOT / ".github" / "workflows" / "build-installers.yml").read_text(encoding="utf-8")
        block = re.search(r"forbidden = \{(.*?)\}", source, re.S)
        self.assertIsNotNone(block)
        forbidden = {canonicalize_name(name) for name in re.findall(r'"([^"]+)"', block.group(1))}
        self.assertTrue(forbidden)
        locked = {canonicalize_name(requirement.name) for requirement in load_release_lock(PROJECT_ROOT)}
        self.assertEqual(set(), forbidden & locked, "private quality gate refuses a package the release lock ships")


# R25 -------------------------------------------------------------------------------------------

class _Panic(BaseException):  # stands in for pyo3's PanicException, which is not an Exception
    pass


def _fake_rhwp(*, fail: bool = False, parsed: list | None = None) -> types.SimpleNamespace:
    class Document:
        def extract_text(self) -> str:
            return "1. 첫 문제\x00\n2. 둘째 문제"

        def export_png(self, output_dir: str, prefix: str | None = None) -> list[str]:
            path = Path(output_dir) / f"{prefix}_001.png"
            Image.new("RGB", (30, 40), "white").save(path)
            return [str(path)]

    def parse(path: str) -> Document:
        if parsed is not None:
            parsed.append(path)
        if fail:
            raise _Panic("rust panic")
        return Document()

    return types.SimpleNamespace(parse=parse, version=lambda: "0.7.0", rhwp_core_version=lambda: "0.7.13")


class TestInstalledAppHwp(unittest.TestCase):
    """sys.frozen: sys.executable is the app itself, so it must never be run as Python."""

    def frozen(self, rhwp: object):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        app = Path(temp.name) / "ClassInEDBMVP.exe"
        app.write_bytes(b"MZ")
        self.app_path = str(app)
        stack = mock.patch.multiple(sys, create=True, frozen=True, executable=str(app))
        stack.start()
        self.addCleanup(stack.stop)
        for patcher in (
            mock.patch.dict(sys.modules, {"rhwp": rhwp}),
            mock.patch.dict(os.environ, {name: "" for name in os.environ if name.startswith("EDB_")}),
            mock.patch.object(preprocess.subprocess, "run", side_effect=AssertionError("the app ran itself as Python")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_installed_app_renders_and_reads_hwp_in_process(self) -> None:
        parsed: list[str] = []
        self.frozen(_fake_rhwp(parsed=parsed))
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "2학기 기말.hwp"
            source.write_bytes(b"hwp")
            pages = preprocess._render_hwp_pages_with_rhwp_python(source, Path(raw) / "out", dpi=144)
            self.assertEqual(1, len(pages))
            page = pages[0]
            self.assertEqual("2학기 기말-page-001", page.page_id)
            self.assertEqual((0, 30, 40), (page.page_index, page.width_px, page.height_px))
            self.assertEqual("rhwp-python", page.metadata["hwp_renderer"])
            self.assertEqual(("0.7.0", "0.7.13", 1, 144), tuple(page.metadata[key] for key in (
                "hwp_renderer_version", "hwp_renderer_core_version", "hwp_renderer_page_count", "dpi")))
            cached = preprocess._render_hwp_pages_with_rhwp_python(source, Path(raw) / "out", dpi=144)
            self.assertIs(True, cached[0].metadata["hwp_renderer_cache_hit"])
            self.assertEqual(1, len(parsed), "the second render must come from the render cache")
            self.assertEqual("1. 첫 문제\n2. 둘째 문제", preprocess._extract_hwp_text_with_rhwp_python(source))

    def test_installed_app_survives_a_rhwp_panic(self) -> None:
        self.frozen(_fake_rhwp(fail=True))
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "exam.hwp"
            source.write_bytes(b"hwp")
            self.assertEqual([], preprocess._render_hwp_pages_with_rhwp_python(source, Path(raw) / "out"))
            self.assertEqual("", preprocess._extract_hwp_text_with_rhwp_python(source))

    def test_installed_app_never_probes_itself_as_python(self) -> None:
        self.frozen(_fake_rhwp())
        commands = [*preprocess._iter_hwp_text_converter_commands(), *preprocess._iter_rhwp_python_renderer_commands()]
        self.assertNotIn(self.app_path, [part for command in commands for part in command])

    def test_installed_app_diagnostics_count_the_bundled_rhwp(self) -> None:
        for rhwp, status in ((_fake_rhwp(), "ready"), (None, "blocked")):
            with self.subTest(bundled=rhwp is not None):
                self.frozen(rhwp)
                with (
                    mock.patch.object(preprocess, "_iter_libreoffice_executables", return_value=[]),
                    mock.patch.object(preprocess.shutil, "which", return_value=None),
                ):
                    hangul = app_server.describe_runtime_diagnostics()["hangul"]
                self.assertEqual(status, hangul["status"])
                if rhwp is not None:
                    self.assertTrue(hangul["hwpReady"] and hangul["hwpxReady"])
                    self.assertEqual([], hangul["warnings"])
                    self.assertIn("rhwp-python", [tool["name"] for tool in hangul["hwpRenderers"]])


# R27 -------------------------------------------------------------------------------------------

class TestConverterTimeout(unittest.TestCase):
    def test_hung_converter_and_its_child_are_killed_at_the_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            marker = tmp / "child-survived"
            child = "import sys, time; time.sleep(5); open(sys.argv[1], 'w').close()"
            launcher = tmp / "launcher.py"
            # Like soffice.exe: start soffice.bin with our stdout/stderr and wait for it.
            launcher.write_text(
                "import subprocess, sys\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}, {str(marker)!r}], "
                "stdout=sys.stdout, stderr=sys.stderr).wait()\n",
                encoding="utf-8",
            )
            source = tmp / "worksheet.hwp"
            source.write_bytes(b"hwp")
            started = time.monotonic()
            converted, errors = preprocess._run_hwp_pdf_converter_commands(
                source, tmp, [[sys.executable, str(launcher)]], timeout_seconds=1
            )
            elapsed = time.monotonic() - started
            self.assertIsNone(converted)
            self.assertIn("timed out", " ".join(errors))
            self.assertLess(elapsed, 4, "the timeout must not wait for the converter's child")
            time.sleep(max(0.0, 6.5 - (time.monotonic() - started)))
            self.assertFalse(marker.exists(), "the converter's child outlived the timeout")

    def test_run_tool_returns_exit_code_and_output(self) -> None:
        result = preprocess._run_tool(
            [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], timeout=10
        )
        self.assertEqual((3, "out", "err"), (result.returncode, result.stdout.strip(), result.stderr.strip()))


# R63 -------------------------------------------------------------------------------------------

H2ORESTART = Path("user") / "uno_packages" / "cache" / "uno_packages" / "lu1a2b.tmp_" / "H2Orestart-v0.8.0.oxt"


class TestLibreOfficeHwpSupport(unittest.TestCase):
    def libreoffice(self) -> tuple[Path, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tmp = Path(temp.name)
        exe = tmp / "LibreOffice" / "program" / "soffice.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        profile = tmp / "profile"
        profile.mkdir()
        for patcher in (
            mock.patch.object(preprocess, "_libreoffice_profile_dirs", return_value=[profile]),
            mock.patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[[str(exe), "--headless"]]),
            mock.patch.object(preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[]),
            mock.patch.object(preprocess, "_iter_pyhwp_html_converter_commands", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return tmp, profile

    def test_runtime_diagnostics_count_libreoffice_only_with_h2orestart(self) -> None:
        _tmp, profile = self.libreoffice()
        with (
            mock.patch.object(preprocess, "_iter_hwp_text_converter_commands", return_value=[]),
            mock.patch.object(preprocess, "_iter_chrome_pdf_commands", return_value=[]),
            mock.patch.object(preprocess, "_iter_rhwp_core_renderer_commands", return_value=[]),
        ):
            stock = app_server.describe_runtime_diagnostics()["hangul"]
            (profile / H2ORESTART).mkdir(parents=True)
            extended = app_server.describe_runtime_diagnostics()["hangul"]
        self.assertEqual(("blocked", "PDF로 저장 필요"), (stock["status"], stock["label"]))
        self.assertIn("H2Orestart", " ".join(stock["technicalNotes"]))
        self.assertNotIn("LibreOffice", " ".join(stock["warnings"] + stock["recommendedActions"]))
        self.assertEqual("soffice.exe", stock["pdfConverters"][0]["name"])
        self.assertEqual(("ready", "준비됨"), (extended["status"], extended["label"]))

    def test_hwp_conversion_with_running_libreoffice_offers_quit_step(self) -> None:
        tmp, profile = self.libreoffice()
        (profile / H2ORESTART).mkdir(parents=True)
        (profile / ".lock").write_text("host,user", encoding="utf-8")
        source = tmp / "worksheet.hwp"
        source.write_bytes(b"hwp")

        def convert(output: str) -> dict:
            with self.assertRaises(ValueError) as raised:
                preprocess.convert_hwp_to_pdf(source, tmp / output)
            return app_server._export_error_payload(raised.exception)

        silent = lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", "")
        with mock.patch.object(preprocess, "_run_tool", side_effect=silent):
            locked = convert("out1")
            (profile / ".lock").unlink()
            closed = convert("out2")
        self.assertEqual("hwp_conversion_failed", locked["code"])
        self.assertEqual("열려 있는 LibreOffice를 완전히 종료한 뒤 다시 시도해 주세요.", locked["recoverySteps"][0])
        self.assertIs(True, locked["retryable"])
        self.assertNotIn("LibreOffice", locked["error"])
        self.assertNotIn("libreoffice_profile_in_use", closed["detail"])
        self.assertTrue(closed["recoverySteps"][0].startswith("한컴오피스"))
        self.assertIs(False, closed["retryable"])


# R64 -------------------------------------------------------------------------------------------

def test_protected_hwp_says_which_protection_blocks_it() -> None:
    def failure(**flags: bool) -> dict:
        diagnosis = preprocess._format_hwp_conversion_diagnosis(
            {"hwp_signature": "HWP Document File", "hwp_flags": flags})
        return app_server._export_error_payload(
            ValueError(f"HWP/HWPX conversion failed. Details: soffice exited 1 Diagnosis: {diagnosis}"))

    password, distributed, drm = failure(password=True), failure(distribution=True, copy_protection=True), failure(drm=True)
    assert password["code"] == distributed["code"] == drm["code"] == "hwp_protected_document"
    assert password["error"].startswith("암호가 걸린 한글 파일이에요.")
    assert distributed["error"].startswith("배포용으로 보호된 한글 파일이에요.")
    assert drm["error"].startswith("문서 보안이 걸린 한글 파일이에요.")
    assert "protection flags are set (distribution, copy_protection)" in distributed["detail"]
    assert (password["retryable"], app_server._export_error_status(ValueError(), password)) == (False, 400)
    # Bit 9 is the e-signature reserve flag, not a protection; plain failures keep the generic copy.
    for other in (failure(copy_protection=True), failure(compressed=True)):
        assert other["code"] == "hwp_conversion_failed"


# R91 -------------------------------------------------------------------------------------------

def test_testing_docs_name_only_committed_inputs_and_current_controls() -> None:
    guide = (PROJECT_ROOT / "LOCAL_TESTING_GUIDE.md").read_text(encoding="utf-8")
    packaging = (PROJECT_ROOT / "PACKAGING_MVP.md").read_text(encoding="utf-8")
    assert "out_images_sample4\\" not in guide and "record_0001_img_0" not in guide
    for removed in ("Choose source", "Run export", "칠판 설정", "power button", "does not self-replace"):
        assert removed not in packaging, removed
    app = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
    for label in ("어떻게 넣을까요?", "파일 선택", "EDB 제작", "ClassIn에서 열기", "AI 사용·연결", "다운로드 열기"):
        assert f"`{label}`" in packaging and label in app, label


if __name__ == "__main__":
    unittest.main()
