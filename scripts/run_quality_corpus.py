#!/usr/bin/env python3
"""Run private corpus sources through the real EDB pipeline, then evaluate.

The committed synthetic observations only validate the evaluator. Production
release evidence must be produced from the current checkout, so this runner
rebuilds every case and writes all private intermediates below ``--work-dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_quality_corpus import (  # noqa: E402
    EXIT_GATE_FAILED,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    PRODUCTION_MINIMUM_CASES,
    CorpusError,
    evaluate_corpus,
    extract_observation,
    observation_payload,
    render_markdown,
    validate_manifest_readiness,
)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CorpusError(f"file was not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"could not read JSON {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CorpusError(f"{label} must be an object")
    return value


def _resolve_path(raw: Any, root: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise CorpusError(f"{label} must be a non-empty path")
    expanded = Path(os.path.expandvars(os.path.expanduser(raw.strip())))
    return expanded.resolve() if expanded.is_absolute() else (root / expanded).resolve()


def _safe_case_dir(case_id: str, index: int) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", case_id).strip("-.")[:80]
    return f"{index + 1:03d}-{slug or 'case'}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _cache_hit_count(raw: Any) -> int:
    """Count cache-hit evidence without retaining any source/session content."""

    count = 0
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            normalized_key = str(key).strip().lower()
            if normalized_key.endswith("cache_hit_count"):
                if isinstance(value, bool):
                    count += int(value)
                elif isinstance(value, (int, float)) and value > 0:
                    count += int(value)
                continue
            if normalized_key in {
                "cache_hit",
                "ocr_cache_hit",
                "ai_cache_hit",
                "image_normalized_cache_hit",
                "imagenormalizedcachehit",
            } and value is True:
                count += 1
                continue
            count += _cache_hit_count(value)
    elif isinstance(raw, list):
        count += sum(_cache_hit_count(item) for item in raw)
    return count


def _command_version(command_name: str) -> str | None:
    executable = shutil.which(command_name)
    if not executable:
        return None
    for flag in ("--version", "-version", "-v"):
        try:
            completed = subprocess.run(
                [executable, flag],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        first_line = next(
            (line.strip() for line in completed.stdout.splitlines() if line.strip()), ""
        )
        if first_line:
            return first_line[:300]
    return "available-version-unknown"


def build_dependency_inventory(python_executable: str) -> dict[str, Any]:
    package_names = [
        "Pillow",
        "PyMuPDF",
        "numpy",
        "opencv-python-headless",
        "olefile",
        "pyhwp",
        "hwp-hwpx-parser",
        "unhwp",
        "rhwp-python",
        "six",
        "pytesseract",
        "paddleocr",
    ]
    inventory_script = (
        "import importlib.metadata as m,json\n"
        f"names={package_names!r}\n"
        "versions={}\n"
        "for name in names:\n"
        "    try: versions[name]=m.version(name)\n"
        "    except m.PackageNotFoundError: versions[name]=None\n"
        "print(json.dumps(versions,sort_keys=True))\n"
    )
    try:
        completed = subprocess.run(
            [python_executable, "-c", inventory_script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CorpusError(f"could not inventory Python dependencies: {exc}") from exc
    if completed.returncode != 0:
        raise CorpusError("could not inventory Python dependencies for release provenance")
    try:
        python_packages = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CorpusError("Python dependency inventory returned invalid JSON") from exc

    command_versions = {
        command: _command_version(command)
        for command in ("tesseract", "hwp5txt", "hwp5html", "soffice", "libreoffice", "rhwp", "node")
    }
    rhwp_core_versions: list[str] = []
    rhwp_package_candidates = [
        PROJECT_ROOT / "node_modules" / "@rhwp" / "core" / "package.json",
        PROJECT_ROOT
        / ".app_runtime"
        / "rhwp_core"
        / "node_modules"
        / "@rhwp"
        / "core"
        / "package.json",
    ]
    for package_path in rhwp_package_candidates:
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        version = payload.get("version") if isinstance(payload, Mapping) else None
        if isinstance(version, str) and version not in rhwp_core_versions:
            rhwp_core_versions.append(version)
    requirements_hashes = {
        path.name: _sha256_file(path)
        for path in (
            PROJECT_ROOT / "requirements-local.txt",
            PROJECT_ROOT / "requirements-dev.txt",
            PROJECT_ROOT / "requirements-release.lock",
            PROJECT_ROOT / "requirements-release-bootstrap.lock",
            PROJECT_ROOT / "requirements-ci.lock",
        )
        if path.is_file()
    }
    return {
        "inventory_schema_version": 1,
        "python_packages": python_packages,
        "external_tools": command_versions,
        "rhwp_core_versions": sorted(rhwp_core_versions),
        "requirements_sha256": requirements_hashes,
    }


def build_environment_descriptor(
    *, ocr: str, python_executable: str
) -> dict[str, Any]:
    return {
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable_name": Path(python_executable).name,
        "ocr_mode": ocr,
        "dependency_inventory": build_dependency_inventory(python_executable),
    }


def collect_pipeline_provenance(*, ocr: str, python_executable: str) -> dict[str, Any]:
    git_commit = os.environ.get(
        "GITHUB_SHA", os.environ.get("EDB_QUALITY_GIT_COMMIT", "")
    ).strip()
    if not git_commit:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        git_commit = completed.stdout.strip() if completed.returncode == 0 else ""
    if not git_commit:
        raise CorpusError("could not determine the git commit for release provenance")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if status.returncode != 0:
        raise CorpusError("could not determine whether the checkout is clean")
    pipeline_files = [
        PROJECT_ROOT / "build_problem_board_edb.py",
        Path(__file__).resolve(),
        PROJECT_ROOT / "scripts" / "evaluate_quality_corpus.py",
    ]
    pipeline_components = {
        str(path.relative_to(PROJECT_ROOT)): _sha256_file(path) for path in pipeline_files
    }
    environment = build_environment_descriptor(
        ocr=ocr, python_executable=python_executable
    )
    pipeline_fingerprint = _stable_fingerprint(pipeline_components)
    environment_fingerprint = _stable_fingerprint(environment)
    run_id = (
        os.environ.get("EDB_QUALITY_RUN_ID")
        or os.environ.get("GITHUB_RUN_ID")
        or f"local-{int(time.time())}-{git_commit[:12]}"
    )
    return {
        "schema_version": 1,
        "runner": "scripts/run_quality_corpus.py",
        "run_id": run_id,
        "git_commit": git_commit,
        "pipeline_fingerprint": pipeline_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "environment": environment,
        "fresh_pipeline_execution": True,
        "worktree_clean": not bool(status.stdout.strip()),
        "started_at": _utc_now(),
    }


def run_pipeline_case(
    *,
    source_path: Path,
    output_dir: Path,
    subject: str,
    ocr: str,
    python_executable: str,
) -> tuple[Path, float]:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        str(PROJECT_ROOT / "build_problem_board_edb.py"),
        str(source_path),
        "--output-dir",
        str(output_dir),
        "--subject",
        subject or "unknown",
        "--ocr",
        ocr,
    ]
    started_at = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout or "").splitlines()[-30:])
        raise CorpusError(
            f"pipeline failed for {source_path.name} with exit {completed.returncode}:\n{tail}"
        )
    session_path = output_dir / "ui_session.json"
    if not session_path.is_file():
        raise CorpusError(f"pipeline did not create {session_path}")
    return session_path, elapsed_ms


def build_fresh_manifest(
    manifest: Mapping[str, Any],
    *,
    corpus_root: Path,
    work_dir: Path,
    ocr: str,
    python_executable: str,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CorpusError("manifest.cases must be a non-empty array")
    fresh_manifest = json.loads(json.dumps(manifest))
    fresh_cases = fresh_manifest["cases"]
    observations_dir = work_dir / "observations"
    outputs_dir = work_dir / "pipeline-outputs"
    inputs_dir = work_dir / "isolated-inputs"
    observations_dir.mkdir(parents=True, exist_ok=True)

    for index, raw_case in enumerate(raw_cases):
        case = _mapping(raw_case, f"manifest.cases[{index}]")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise CorpusError(f"manifest.cases[{index}].id must be non-empty")
        source = _mapping(case.get("source"), f"manifest.cases[{index}].source")
        source_path = _resolve_path(
            source.get("path"), corpus_root, f"manifest.cases[{index}].source.path"
        )
        if not source_path.is_file():
            raise CorpusError(f"private corpus source was not found: {source_path}")
        subject = str(source.get("subject") or "unknown").strip().lower() or "unknown"
        case_dir = _safe_case_dir(case_id, index)
        isolated_case_dir = inputs_dir / case_dir
        isolated_case_dir.mkdir(parents=True, exist_ok=False)
        isolated_source_path = isolated_case_dir / f"source{source_path.suffix.lower()}"
        shutil.copyfile(source_path, isolated_source_path)
        if _sha256_file(isolated_source_path) != source.get("sha256"):
            raise CorpusError(f"isolated source copy digest mismatch for case {case_id}")
        isolated_cache_dir = isolated_case_dir / ".pipeline_cache"
        if isolated_cache_dir.exists():
            raise CorpusError(f"isolated pipeline cache was not empty for case {case_id}")
        session_path, elapsed_ms = run_pipeline_case(
            source_path=isolated_source_path,
            output_dir=outputs_dir / case_dir,
            subject=subject,
            ocr=ocr,
            python_executable=python_executable,
        )
        session = _load_json(session_path)
        cache_hit_count = _cache_hit_count(session)
        if cache_hit_count:
            raise CorpusError(
                f"fresh pipeline case {case_id} reported {cache_hit_count} cache hit(s)"
            )
        observation = extract_observation(
            session,
            f"fresh pipeline case {case_id}",
            processing_ms_override=elapsed_ms,
        )
        observation_path = observations_dir / f"{case_dir}.json"
        observation_provenance = None
        if provenance is not None:
            source_sha256 = source.get("sha256")
            if not isinstance(source_sha256, str):
                raise CorpusError(f"manifest.cases[{index}].source.sha256 is required")
            observation_provenance = {
                "schemaVersion": 1,
                "runner": provenance["runner"],
                "runId": provenance["run_id"],
                "gitCommit": provenance["git_commit"],
                "pipelineFingerprint": provenance["pipeline_fingerprint"],
                "environmentFingerprint": provenance["environment_fingerprint"],
                "sourceSha256": source_sha256,
                "timingMethod": "monotonic_wall_clock",
                "cachePolicy": "isolated_empty_per_case",
                "cacheHitCount": 0,
            }
        observation_path.write_text(
            json.dumps(
                observation_payload(observation, provenance=observation_provenance),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        fresh_case = fresh_cases[index]
        fresh_case["result"] = str(observation_path.resolve())
        fresh_case["source"]["path"] = str(source_path)
        print(
            f"[quality-pipeline] {index + 1}/{len(raw_cases)} {case_id}: "
            f"questions={len(observation.question_numbers)} elapsed_ms={elapsed_ms:.0f}"
        )

    return fresh_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild a private corpus with the current EDB pipeline and enforce its quality gates."
    )
    parser.add_argument("manifest", type=Path, help="Protected corpus manifest")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ocr", default="auto")
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--establish-baseline",
        action="store_true",
        help="Explicit first-run mode; emits an unapproved candidate and does not compare a baseline",
    )
    parser.add_argument("--minimum-cases", type=int, default=PRODUCTION_MINIMUM_CASES)
    parser.add_argument(
        "--allow-dirty-checkout",
        action="store_true",
        help="Diagnostic only; the resulting report cannot be approved as a baseline",
    )
    parser.add_argument(
        "--retain-private-artifacts",
        action="store_true",
        help="Diagnostic only; keep isolated source copies and OCR/render outputs",
    )
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    args = parser.parse_args(argv)

    cleanup_root: Path | None = None
    try:
        manifest_path = args.manifest.expanduser().resolve()
        corpus_root = args.corpus_root.expanduser().resolve()
        work_dir = args.work_dir.expanduser().resolve()
        cleanup_root = work_dir
        if args.minimum_cases < PRODUCTION_MINIMUM_CASES:
            raise CorpusError(
                f"production minimum-cases cannot be below {PRODUCTION_MINIMUM_CASES}"
            )
        if args.establish_baseline and args.baseline:
            raise CorpusError("--establish-baseline and --baseline are mutually exclusive")
        if not args.establish_baseline and not args.baseline:
            raise CorpusError(
                "production quality runs require an approved --baseline; "
                "use --establish-baseline only for the explicit first-run bootstrap"
            )
        if work_dir.exists() and any(work_dir.iterdir()):
            raise CorpusError(
                "--work-dir must be empty for each release run so stale artifacts cannot be reused"
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        manifest = _mapping(_load_json(manifest_path), "manifest")
        readiness = validate_manifest_readiness(
            manifest,
            corpus_root=corpus_root,
            minimum_cases=args.minimum_cases,
            require_approved=True,
            verify_sources=True,
        )
        if readiness["status"] != "ready":
            raise CorpusError(
                "private corpus is not release-ready: " + "; ".join(readiness["errors"])
            )
        provenance = collect_pipeline_provenance(
            ocr=args.ocr, python_executable=args.python_executable
        )
        provenance["private_artifacts_retained"] = bool(args.retain_private_artifacts)
        if not provenance["worktree_clean"] and not args.allow_dirty_checkout:
            raise CorpusError(
                "release evidence requires a clean checkout; commit or remove local changes first"
            )
        fresh_manifest = build_fresh_manifest(
            manifest,
            corpus_root=corpus_root,
            work_dir=work_dir,
            ocr=args.ocr,
            python_executable=args.python_executable,
            provenance=provenance,
        )
        derived_manifest_path = work_dir / "fresh-manifest.json"
        derived_manifest_path.write_text(
            json.dumps(fresh_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        baseline = _mapping(_load_json(args.baseline), "baseline") if args.baseline else None
        if baseline is not None:
            baseline_provenance = _mapping(
                baseline.get("pipeline_provenance"), "baseline.pipeline_provenance"
            )
            if baseline_provenance.get("environment_fingerprint") != provenance["environment_fingerprint"]:
                raise CorpusError(
                    "baseline environment_fingerprint does not match this runner; "
                    "speed regressions require the same measurement environment"
                )
        evaluation_manifest = fresh_manifest
        if args.establish_baseline and (
            "regression_tolerance" in fresh_manifest or "regressionTolerance" in fresh_manifest
        ):
            evaluation_manifest = dict(fresh_manifest)
            evaluation_manifest.pop("regression_tolerance", None)
            evaluation_manifest.pop("regressionTolerance", None)
        report = evaluate_corpus(
            evaluation_manifest,
            manifest_path=derived_manifest_path,
            corpus_root=corpus_root,
            baseline_report=baseline,
            require_approved_ground_truth=True,
            require_observation_provenance=True,
            expected_observation_provenance=provenance,
            require_approved_baseline=baseline is not None,
        )
        provenance["completed_at"] = _utc_now()
        provenance["observation_provenance_verified"] = (
            report["aggregate"]["observation_provenance_verified_case_count"]
            == report["aggregate"]["case_count"]
        )
        report["pipeline_provenance"] = provenance
        report["baseline_candidate"] = bool(args.establish_baseline)
        markdown = render_markdown(report)
        if args.json_report:
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if args.markdown_report:
            args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_report.write_text(markdown, encoding="utf-8")
        print(markdown)
        return EXIT_OK if report["status"] == "passed" else EXIT_GATE_FAILED
    except (CorpusError, OSError) as exc:
        print(f"[quality-pipeline] INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    finally:
        if cleanup_root is not None and not args.retain_private_artifacts:
            for private_dir_name in ("isolated-inputs", "pipeline-outputs"):
                private_dir = cleanup_root / private_dir_name
                if private_dir.is_dir():
                    shutil.rmtree(private_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
