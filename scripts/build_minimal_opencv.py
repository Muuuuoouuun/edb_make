#!/usr/bin/env python3
"""Build the pinned image-only OpenCV wheel in an isolated, hashed toolchain."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "release/opencv-image-profile.json"
TOOLCHAIN_LOCK = ROOT / "requirements-opencv-build.lock"
RECEIPT_NAME = "edb-build-profile.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_key() -> str:
    digest = hashlib.sha256()
    for path in (PROFILE, TOOLCHAIN_LOCK, Path(__file__)):
        digest.update(path.read_bytes())
    digest.update(f"{sys.platform}|{platform.machine()}|{sys.version_info[:2]}".encode())
    return digest.hexdigest()


def patch_wrapper(source: str) -> str:
    """Remove packaging assumptions for modules absent from BUILD_LIST.

    OpenCV's generic stub generator assumes Feature2D exists, even for a
    core/imgproc build. No stubs or py.typed marker are advertised here. The
    native OpenCV algorithms and bindings are unchanged.
    """
    source = source.replace('packages = ["cv2", "cv2.data"]', 'packages = ["cv2"]')
    source = source.replace('        "cv2.data": ["*.xml"],\n', "")
    start = source.index('        "cv2": (\n')
    end = source.index("        +\n", start)
    source = source[:start] + '        "cv2": []\n' + source[end:]
    source = source.replace('[ r"python/cv2/py.typed" ] if sys.version_info >= (3, 6) else []', "[]")
    start = source.index('        "cv2.data": [  # OPENCV_OTHER_INSTALL_PATH')
    end = source.index('        "cv2.mat_wrapper": [', start)
    source = source[:start] + source[end:]
    start = source.index('    if sys.version_info >= (3, 6):\n        rearrange_cmake_output_data["cv2.typing"]')
    end = source.index("    # Files in sourcetree", start)
    return source[:start] + source[end:]


def extract_source(archive: Path, destination: Path, expected_sha256: str) -> Path:
    if sha256(archive) != expected_sha256:
        raise ValueError(f"Source archive SHA-256 mismatch: {archive.name}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        roots = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
        if len(roots) != 1:
            raise ValueError("Expected one source archive root")
        source.extractall(destination, filter="data")
    return destination / roots.pop()


def add_receipt(wheel: Path, receipt: dict) -> None:
    """Include the source/flag provenance and update the wheel RECORD hashes."""
    output = wheel.with_suffix(".tmp")
    payload = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    with zipfile.ZipFile(wheel) as source:
        records = [name for name in source.namelist() if name.endswith(".dist-info/RECORD")]
        if len(records) != 1:
            raise ValueError("Wheel must have exactly one RECORD")
        record_name = records[0]
        receipt_name = record_name.rsplit("/", 1)[0] + "/" + RECEIPT_NAME
        rows = list(csv.reader(io.StringIO(source.read(record_name).decode())))
        rows = [row for row in rows if row[0] != receipt_name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
        rows.append([receipt_name, "sha256=" + digest, str(len(payload))])
        record = io.StringIO(newline="")
        csv.writer(record, lineterminator="\n").writerows(rows)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                if entry.filename not in (record_name, receipt_name):
                    target.writestr(entry, source.read(entry))
            target.writestr(receipt_name, payload)
            target.writestr(record_name, record.getvalue())
    output.replace(wheel)


def cached_wheel(directory: Path, key: str) -> Path | None:
    manifest = directory / "wheel-manifest.json"
    if not manifest.exists():
        return None
    entry = json.loads(manifest.read_text())
    name = str(entry.get("fileName", ""))
    if entry.get("buildKey") != key or Path(name).name != name or not name.endswith(".whl"):
        raise ValueError("Invalid cached OpenCV wheel manifest")
    wheel = directory / name
    if not wheel.is_file() or sha256(wheel) != entry.get("sha256"):
        raise ValueError("Cached OpenCV wheel SHA-256 mismatch")
    return wheel


def build_wheel(work: Path, cache: Path) -> Path:
    profile = json.loads(PROFILE.read_text())
    key = build_key()
    output = cache / key
    found = cached_wheel(output, key)
    if found:
        print(f"[opencv-image] verified cached wheel: {found.name}", flush=True)
        return found
    work.mkdir(parents=True, exist_ok=True)
    # A fresh short path also makes retries independent of interrupted builds.
    work = Path(tempfile.mkdtemp(prefix="b-", dir=work))
    tools = work / "toolchain"
    python = tools / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(tools)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--require-hashes", "-r", str(TOOLCHAIN_LOCK)], check=True)
    source_roots = []
    for index, entry in enumerate(profile["sources"]):
        archive = work / f"source-{index}.tar.gz"
        if not archive.exists():
            url = f"https://codeload.github.com/{entry['repo']}/tar.gz/{entry['commit']}"
            with urlopen(url, timeout=120) as response, archive.open("wb") as target:
                shutil.copyfileobj(response, target)
        source_roots.append(extract_source(archive, work / f"source-{index}", entry["sha256"]))
    wrapper, opencv = source_roots
    short_wrapper = work / "src"
    shutil.move(str(wrapper), short_wrapper)
    wrapper = short_wrapper
    target = wrapper / "opencv"
    target.rmdir()  # Pinned GitHub wrapper archive contains an empty submodule dir.
    shutil.move(str(opencv), target)
    setup = wrapper / "setup.py"
    setup.write_text(patch_wrapper(setup.read_text()), encoding="utf-8")
    (wrapper / "cv2/version.py").write_text(
        f'opencv_version = "{profile["version"]}"\ncontrib = False\nheadless = True\nrolling = False\nci_build = False\n', encoding="utf-8",
    )
    wheel_dir = work / "wheels"
    tool_bin = str(python.parent)
    environment = {
        **os.environ, "PATH": tool_bin + os.pathsep + os.environ.get("PATH", ""),
        "ENABLE_HEADLESS": "1", "ENABLE_CONTRIB": "0", "CI_BUILD": "1" if os.name == "nt" else "0",
        "CMAKE_BUILD_PARALLEL_LEVEL": "3", "CMAKE_ARGS": " ".join(profile["cmakeArgs"]),
    }
    if sys.platform == "darwin":
        environment["MACOSX_DEPLOYMENT_TARGET"] = profile["minimumMacOS"]
    subprocess.run([str(python), "-m", "pip", "wheel", str(wrapper), "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheel_dir), "-v"], env=environment, check=True)
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one image-only wheel")
    receipt = {
        "schemaVersion": 1, "buildKey": key, "profile": profile,
        "profileSha256": sha256(PROFILE), "toolchainLockSha256": sha256(TOOLCHAIN_LOCK),
        "builderSha256": sha256(Path(__file__)), "pythonVersion": platform.python_version(),
        "platform": sys.platform, "architecture": platform.machine(),
    }
    add_receipt(wheels[0], receipt)
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / wheels[0].name
    shutil.copyfile(wheels[0], wheel)
    (output / "wheel-manifest.json").write_text(json.dumps({"buildKey": key, "fileName": wheel.name, "sha256": sha256(wheel)}, indent=2) + "\n")
    return wheel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--install", action="store_true", help="Replace the full wheel in this Python environment")
    args = parser.parse_args(argv)
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        parser.error("The pinned source-build toolchain supports Python 3.11 and 3.12")
    wheel = build_wheel(args.work_dir.resolve(), args.cache_dir.resolve())
    print(f"[opencv-image] wheel: {wheel.name} ({wheel.stat().st_size} bytes)", flush=True)
    if args.install:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)], check=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/verify_minimal_opencv.py"), "--root", str(ROOT), "--require-profile"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
