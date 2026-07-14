#!/usr/bin/env python3
"""Optional local Upscayl Lite backend used transparently by stage 3.

The backend is deliberately fail-open: an unavailable binary, incompatible
GPU, timeout, or malformed output returns the original image so stage 3 can
continue with its existing Lanczos/sharpen pipeline.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


DEFAULT_UPSCAYL_MODEL = "upscayl-lite-4x"
DEFAULT_TARGET_WIDTH_PX = 1600
DEFAULT_MAX_SOURCE_WIDTH_PX = 900
DEFAULT_MAX_OUTPUT_PIXELS = 16_000_000
DEFAULT_TIMEOUT_SECONDS = 30.0

_UPSCAYL_RUN_LOCK = threading.Semaphore(1)


@dataclass(frozen=True, slots=True)
class UpscaylInstallation:
    binary_path: Path
    models_dir: Path
    model: str = DEFAULT_UPSCAYL_MODEL


@dataclass(slots=True)
class UpscaylAutoResult:
    image: Image.Image
    status: str
    reason: str
    source_width: int
    output_width: int
    latency_ms: int = 0
    binary_path: Path | None = None
    model: str = DEFAULT_UPSCAYL_MODEL

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "source_width": self.source_width,
            "output_width": self.output_width,
            "latency_ms": self.latency_ms,
            "binary_path": str(self.binary_path) if self.binary_path else None,
            "model": self.model,
        }


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _platform_resource_name() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def _binary_filename() -> str:
    return "upscayl-bin.exe" if sys.platform.startswith("win") else "upscayl-bin"


def _runtime_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parent]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(str(frozen_root)))
    executable = Path(sys.executable).resolve()
    roots.extend([executable.parent, executable.parent.parent])
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def _models_near_binary(binary: Path) -> Iterable[Path]:
    parents = list(binary.parents)
    candidates = [
        binary.parent / "models",
        binary.parent.parent / "models" if len(parents) >= 2 else binary.parent / "models",
        binary.parent.parent.parent / "models" if len(parents) >= 3 else binary.parent / "models",
    ]
    yield from candidates


def _candidate_installations(env_binary: str, env_models: str, path_env: str) -> Iterable[tuple[Path, Path]]:
    binary_name = _binary_filename()
    platform_name = _platform_resource_name()

    if env_binary:
        binary = Path(env_binary).expanduser()
        if env_models:
            yield binary, Path(env_models).expanduser()
        else:
            for models in _models_near_binary(binary):
                yield binary, models

    for root in _runtime_roots():
        for resource_root in (
            root / "resources" / "upscayl",
            root / "upscayl",
            root / "resources",
        ):
            yield (
                resource_root / platform_name / "bin" / binary_name,
                resource_root / "models",
            )

    path_binary = shutil.which(binary_name, path=path_env or None)
    if path_binary:
        binary = Path(path_binary)
        if env_models:
            yield binary, Path(env_models).expanduser()
        for models in _models_near_binary(binary):
            yield binary, models

    if sys.platform == "darwin":
        for applications in (Path("/Applications"), Path.home() / "Applications"):
            resource_root = applications / "Upscayl.app" / "Contents" / "Resources" / "resources"
            yield resource_root / "mac" / "bin" / binary_name, resource_root / "models"
    elif sys.platform.startswith("win"):
        for env_name in ("LOCALAPPDATA", "PROGRAMFILES"):
            base = os.environ.get(env_name)
            if not base:
                continue
            for app_name in ("Upscayl", "upscayl"):
                resource_root = Path(base) / app_name / "resources" / "resources"
                yield resource_root / "win" / "bin" / binary_name, resource_root / "models"
    else:
        for app_root in (Path("/opt/Upscayl"), Path("/opt/upscayl"), Path.home() / ".local" / "opt" / "upscayl"):
            resource_root = app_root / "resources" / "resources"
            yield resource_root / "linux" / "bin" / binary_name, resource_root / "models"


def _valid_installation(binary: Path, models_dir: Path, model: str) -> UpscaylInstallation | None:
    try:
        binary = binary.expanduser().resolve()
        models_dir = models_dir.expanduser().resolve()
    except OSError:
        return None
    if not binary.is_file() or not models_dir.is_dir():
        return None
    if not (models_dir / f"{model}.bin").is_file():
        return None
    if not (models_dir / f"{model}.param").is_file():
        return None
    return UpscaylInstallation(binary_path=binary, models_dir=models_dir, model=model)


@lru_cache(maxsize=16)
def _discover_cached(env_binary: str, env_models: str, path_env: str, model: str) -> UpscaylInstallation | None:
    seen: set[tuple[str, str]] = set()
    for binary, models_dir in _candidate_installations(env_binary, env_models, path_env):
        key = (str(binary), str(models_dir))
        if key in seen:
            continue
        seen.add(key)
        installation = _valid_installation(binary, models_dir, model)
        if installation is not None:
            return installation
    return None


def clear_upscayl_discovery_cache() -> None:
    _discover_cached.cache_clear()


def discover_upscayl_installation(*, model: str = DEFAULT_UPSCAYL_MODEL) -> UpscaylInstallation | None:
    return _discover_cached(
        os.environ.get("UPSCAYL_BIN", "").strip(),
        os.environ.get("UPSCAYL_MODELS_DIR", "").strip(),
        os.environ.get("PATH", ""),
        model,
    )


def auto_upscale_eligible(
    image: Image.Image,
    *,
    target_width: int = DEFAULT_TARGET_WIDTH_PX,
    max_source_width: int = DEFAULT_MAX_SOURCE_WIDTH_PX,
    max_output_pixels: int = DEFAULT_MAX_OUTPUT_PIXELS,
) -> tuple[bool, str]:
    if not _env_enabled("EDB_AUTO_UPSCAYL", True):
        return False, "disabled"
    width, height = image.size
    if width <= 0 or height <= 0:
        return False, "invalid_source_size"
    if width >= max_source_width:
        return False, "source_already_large"
    if width >= target_width:
        return False, "target_already_met"
    target_height = max(1, round(height * target_width / width))
    if target_width * target_height > max_output_pixels:
        return False, "output_pixel_limit"
    return True, "low_resolution_source"


def _unchanged_result(image: Image.Image, *, status: str, reason: str) -> UpscaylAutoResult:
    return UpscaylAutoResult(
        image=image,
        status=status,
        reason=reason,
        source_width=image.width,
        output_width=image.width,
    )


def auto_upscale_image(
    image: Image.Image,
    *,
    installation: UpscaylInstallation | None = None,
    target_width: int = DEFAULT_TARGET_WIDTH_PX,
    max_source_width: int = DEFAULT_MAX_SOURCE_WIDTH_PX,
    max_output_pixels: int = DEFAULT_MAX_OUTPUT_PIXELS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> UpscaylAutoResult:
    """Try Upscayl Lite for an undersized image and otherwise return it unchanged."""

    eligible, reason = auto_upscale_eligible(
        image,
        target_width=target_width,
        max_source_width=max_source_width,
        max_output_pixels=max_output_pixels,
    )
    if not eligible:
        return _unchanged_result(image, status="skipped", reason=reason)

    resolved = installation or discover_upscayl_installation()
    if resolved is None:
        return _unchanged_result(image, status="unavailable", reason="installation_not_found")

    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="edb-upscayl-") as raw_tmp:
            work_dir = Path(raw_tmp)
            source_path = work_dir / "source.png"
            output_path = work_dir / "output.png"
            source_mode = "RGBA" if "A" in image.getbands() else "RGB"
            image.convert(source_mode).save(source_path, format="PNG")
            command = [
                str(resolved.binary_path),
                "-i",
                str(source_path),
                "-o",
                str(output_path),
                "-m",
                str(resolved.models_dir),
                "-n",
                resolved.model,
                "-w",
                str(target_width),
                "-f",
                "png",
            ]
            with _UPSCAYL_RUN_LOCK:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=max(1.0, float(timeout_seconds)),
                    check=False,
                )
            if completed.returncode != 0 or not output_path.is_file():
                detail = (completed.stderr or completed.stdout or "unknown Upscayl failure").strip()
                reason = f"process_failed:{detail[-300:]}" if detail else "process_failed"
                return _unchanged_result(image, status="failed", reason=reason)
            with Image.open(output_path) as loaded:
                output_mode = "RGBA" if "A" in loaded.getbands() or "A" in image.getbands() else "RGB"
                output = loaded.convert(output_mode).copy()
            if output.width < image.width or output.width * output.height > max_output_pixels:
                return _unchanged_result(image, status="failed", reason="invalid_output_size")
    except subprocess.TimeoutExpired:
        return _unchanged_result(image, status="failed", reason="timeout")
    except (OSError, ValueError) as exc:
        return _unchanged_result(image, status="failed", reason=f"runtime_error:{type(exc).__name__}")

    latency_ms = int(round((time.perf_counter() - started) * 1000.0))
    return UpscaylAutoResult(
        image=output,
        status="applied",
        reason="low_resolution_source",
        source_width=image.width,
        output_width=output.width,
        latency_ms=latency_ms,
        binary_path=resolved.binary_path,
        model=resolved.model,
    )
