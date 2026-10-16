#!/usr/bin/env python3
"""Build and verify deterministic release compliance metadata.

The release lock is treated as the source of truth for Python package versions.
This script fails when the active environment differs from the lock, an active
runtime dependency is missing from the lock, or the reviewed inventory is not
version-aligned.  It then emits an SPDX 2.3 SBOM, copied license texts, an exact
file manifest, and build provenance suitable for bundling into the app.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


SCHEMA_VERSION = 1
LOCK_PATH = Path("requirements-release.lock")
BOOTSTRAP_LOCK_PATH = Path("requirements-release-bootstrap.lock")
CI_LOCK_PATH = Path("requirements-ci.lock")
INVENTORY_PATH = Path("release/dependency_inventory.json")
NOTICES_PATH = Path("release/THIRD_PARTY_NOTICES.md")
REQUIRED_METADATA_FILES = (
    "dependency-inventory.json",
    "sbom.spdx.json",
    "THIRD_PARTY_NOTICES.md",
    "release-provenance.json",
    "metadata-manifest.json",
)
METADATA_OUTPUT_SENTINEL = ".edb-release-metadata-output"
LICENSE_BASENAME_RE = re.compile(r"^(?:license|copying|notice)(?:[._-].*)?$", re.IGNORECASE)
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_HASH_OPTION_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})(?:\s*\\)?$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _load_lock_entries_from_path(
    lock_path: Path,
    *,
    label: Path,
) -> list[tuple[Requirement, tuple[str, ...]]]:
    entries: list[tuple[Requirement, tuple[str, ...]]] = []
    current_requirement: Requirement | None = None
    current_hashes: list[str] = []

    def finish_entry() -> None:
        nonlocal current_requirement, current_hashes
        if current_requirement is not None:
            entries.append((current_requirement, tuple(current_hashes)))
        current_requirement = None
        current_hashes = []

    for line_number, raw_line in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash="):
            if current_requirement is None:
                raise ValueError(f"{label}:{line_number} has a hash without a requirement")
            match = SHA256_HASH_OPTION_RE.fullmatch(line)
            if match is None:
                raise ValueError(f"{label}:{line_number} must use --hash=sha256:<64 lowercase hex>")
            current_hashes.append(match.group(1))
            continue
        finish_entry()
        if line.endswith("\\"):
            line = line[:-1].rstrip()
        requirement = Requirement(line)
        pins = list(requirement.specifier)
        if len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            raise ValueError(f"{label}:{line_number} must use one exact == version pin")
        if requirement.url or requirement.extras:
            raise ValueError(f"{label}:{line_number} must not use URLs or extras")
        current_requirement = requirement
    finish_entry()
    if not entries:
        raise ValueError(f"{label} contains no dependencies")
    requirements = [requirement for requirement, _hashes in entries]
    names = [canonicalize_name(requirement.name) for requirement in requirements]
    if len(names) != len(set(names)):
        raise ValueError(f"{label} contains duplicate dependency names")
    return entries


def load_release_lock_entries(project_root: Path) -> list[tuple[Requirement, tuple[str, ...]]]:
    return _load_lock_entries_from_path(project_root / LOCK_PATH, label=LOCK_PATH)


def load_release_lock(project_root: Path) -> list[Requirement]:
    return [requirement for requirement, _hashes in load_release_lock_entries(project_root)]


def active_locked_requirements(
    requirements: Iterable[Requirement],
    *,
    marker_environment: dict[str, str] | None = None,
) -> list[Requirement]:
    environment = marker_environment or default_environment()
    return [
        requirement
        for requirement in requirements
        if requirement.marker is None or requirement.marker.evaluate(environment)
    ]


def _pinned_version(requirement: Requirement) -> str:
    return next(iter(requirement.specifier)).version


def _runtime_requirements(distribution: importlib.metadata.Distribution) -> list[Requirement]:
    environment = default_environment()
    result: list[Requirement] = []
    for raw_requirement in distribution.requires or ():
        requirement = Requirement(raw_requirement)
        if requirement.marker is not None:
            marker_environment = dict(environment)
            marker_environment["extra"] = ""
            if not requirement.marker.evaluate(marker_environment):
                continue
        result.append(requirement)
    return result


def collect_locked_environment_errors(
    project_root: Path,
    *,
    reject_unlocked: bool = False,
) -> list[str]:
    errors: list[str] = []
    try:
        all_requirements = load_release_lock(project_root)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    active_requirements = active_locked_requirements(all_requirements)
    locked = {canonicalize_name(requirement.name): requirement for requirement in active_requirements}
    distributions: dict[str, importlib.metadata.Distribution] = {}
    for normalized_name, requirement in locked.items():
        expected_version = _pinned_version(requirement)
        try:
            distribution = importlib.metadata.distribution(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"locked release dependency is not installed: {requirement.name}=={expected_version}")
            continue
        distributions[normalized_name] = distribution
        if distribution.version != expected_version:
            errors.append(
                f"locked release dependency mismatch: {requirement.name} expected "
                f"{expected_version}, installed {distribution.version}"
            )

    for owner_name, distribution in distributions.items():
        for dependency in _runtime_requirements(distribution):
            dependency_name = canonicalize_name(dependency.name)
            if dependency_name not in locked:
                errors.append(
                    f"release lock is not closed: {owner_name} requires {dependency}, "
                    f"but {dependency.name} is not actively pinned"
                )
                continue
            pinned_version = _pinned_version(locked[dependency_name])
            if pinned_version not in dependency.specifier:
                errors.append(
                    f"release lock conflict: {owner_name} requires {dependency}, "
                    f"but pins {dependency.name}=={pinned_version}"
                )
    if reject_unlocked:
        installed_names = {
            canonicalize_name(str(distribution.metadata.get("Name") or ""))
            for distribution in importlib.metadata.distributions()
            if str(distribution.metadata.get("Name") or "").strip()
        }
        unlocked_names = sorted(installed_names - set(locked))
        if unlocked_names:
            errors.append(
                "release environment contains unlocked distributions that could affect packaging: "
                + ", ".join(unlocked_names)
            )
    return errors


def _load_inventory(project_root: Path) -> dict[str, Any]:
    payload = json.loads((project_root / INVENTORY_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("components"), list):
        raise ValueError(f"{INVENTORY_PATH} must contain a components array")
    return payload


def collect_release_policy_errors(project_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        lock_entries = load_release_lock_entries(project_root)
        requirements = [requirement for requirement, _hashes in lock_entries]
        inventory = _load_inventory(project_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    notices_path = project_root / NOTICES_PATH
    if not notices_path.is_file() or notices_path.stat().st_size <= 0:
        errors.append(f"missing non-empty release notices: {NOTICES_PATH}")
    for requirement, hashes in lock_entries:
        if not hashes:
            errors.append(f"release lock dependency has no SHA-256 hashes: {requirement.name}")
        elif len(hashes) != len(set(hashes)):
            errors.append(f"release lock dependency has duplicate SHA-256 hashes: {requirement.name}")
    try:
        bootstrap_entries = _load_lock_entries_from_path(
            project_root / BOOTSTRAP_LOCK_PATH,
            label=BOOTSTRAP_LOCK_PATH,
        )
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        bootstrap_entries = []
    release_entries_by_name = {
        canonicalize_name(requirement.name): (requirement, hashes)
        for requirement, hashes in lock_entries
    }
    bootstrap_names = {canonicalize_name(requirement.name) for requirement, _hashes in bootstrap_entries}
    if bootstrap_names != {"pip", "setuptools", "wheel", "packaging"}:
        errors.append("release bootstrap lock must contain exactly pip, setuptools, wheel, and packaging")
    for bootstrap_requirement, bootstrap_hashes in bootstrap_entries:
        normalized_name = canonicalize_name(bootstrap_requirement.name)
        release_entry = release_entries_by_name.get(normalized_name)
        if release_entry is None or str(release_entry[0].specifier) != str(bootstrap_requirement.specifier):
            errors.append(f"release bootstrap version does not match main lock: {bootstrap_requirement.name}")
        elif set(release_entry[1]) != set(bootstrap_hashes):
            errors.append(f"release bootstrap hashes do not match main lock: {bootstrap_requirement.name}")
    try:
        ci_entries = _load_lock_entries_from_path(project_root / CI_LOCK_PATH, label=CI_LOCK_PATH)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        ci_entries = []
    expected_ci_names = {
        "pytest",
        "iniconfig",
        "packaging",
        "pluggy",
        "pygments",
        "colorama",
    }
    ci_names = {canonicalize_name(requirement.name) for requirement, _hashes in ci_entries}
    if ci_names != expected_ci_names:
        errors.append(f"CI lock dependency set mismatch: expected {sorted(expected_ci_names)}, found {sorted(ci_names)}")
    for ci_requirement, ci_hashes in ci_entries:
        if not ci_hashes:
            errors.append(f"CI lock dependency has no SHA-256 hashes: {ci_requirement.name}")
        if canonicalize_name(ci_requirement.name) == "packaging":
            release_entry = release_entries_by_name.get("packaging")
            if release_entry is None or str(release_entry[0].specifier) != str(ci_requirement.specifier):
                errors.append("CI lock packaging version does not match main release lock")
            elif set(release_entry[1]) != set(ci_hashes):
                errors.append("CI lock packaging hashes do not match main release lock")

    inventory_by_name: dict[str, dict[str, Any]] = {}
    for component in inventory["components"]:
        if not isinstance(component, dict):
            errors.append(f"{INVENTORY_PATH} contains a non-object component")
            continue
        name = str(component.get("name") or "").strip()
        version = str(component.get("version") or "").strip()
        license_expression = str(component.get("licenseExpression") or "").strip()
        disposition = str(component.get("disposition") or "").strip()
        if not all((name, version, license_expression, disposition)):
            errors.append(f"{INVENTORY_PATH} component is missing name/version/license/disposition: {component}")
            continue
        normalized_name = canonicalize_name(name)
        if normalized_name in inventory_by_name:
            errors.append(f"{INVENTORY_PATH} contains duplicate component: {name}")
        inventory_by_name[normalized_name] = component

    for requirement in requirements:
        normalized_name = canonicalize_name(requirement.name)
        component = inventory_by_name.get(normalized_name)
        if component is None:
            errors.append(f"release inventory is missing locked component: {requirement.name}")
            continue
        expected_version = _pinned_version(requirement)
        if str(component.get("version")) != expected_version:
            errors.append(
                f"release inventory version mismatch for {requirement.name}: "
                f"lock {expected_version}, inventory {component.get('version')}"
            )

    for required_vendored in ("react", "react-dom"):
        if required_vendored not in inventory_by_name:
            errors.append(f"release inventory is missing vendored component: {required_vendored}")
    return errors


def _license_files(distribution: importlib.metadata.Distribution) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for relative_path in distribution.files or ():
        if not LICENSE_BASENAME_RE.match(relative_path.name):
            continue
        resolved = Path(distribution.locate_file(relative_path)).resolve()
        if resolved.is_file() and resolved not in seen:
            seen.add(resolved)
            paths.append(resolved)
    return sorted(paths, key=lambda path: path.as_posix().lower())


def _created_at() -> str:
    raw_epoch = os.environ.get("SOURCE_DATE_EPOCH", "0").strip() or "0"
    try:
        epoch = max(0, int(raw_epoch))
    except ValueError as exc:
        raise ValueError("SOURCE_DATE_EPOCH must be a non-negative integer") from exc
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_git_commit(raw_value: str) -> str:
    commit = str(raw_value or "").strip().lower()
    if not commit:
        return "unknown"
    if not GIT_COMMIT_RE.fullmatch(commit):
        raise ValueError("git commit must be a full 40-character lowercase SHA-1")
    return commit


def _assert_safe_metadata_output(project_root: Path, output_dir: Path) -> None:
    root = project_root.resolve()
    destination = output_dir.resolve()
    protected = {Path(destination.anchor), Path.home().resolve(), root, *root.parents}
    if destination in protected:
        raise ValueError(f"refusing unsafe release metadata output directory: {destination}")
    if any(part.lower() == ".git" for part in destination.parts):
        raise ValueError(f"refusing release metadata output inside .git: {destination}")
    if destination.name not in {"release-metadata", "release_metadata"}:
        raise ValueError(
            "release metadata output directory must be named release-metadata or release_metadata: "
            f"{destination}"
        )
    try:
        relative = destination.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None:
        top_level = relative.parts[0] if relative.parts else ""
        if top_level not in {"build", "dist"}:
            raise ValueError(
                "release metadata output inside the project must be under build or exact dist: "
                f"{destination}"
            )
    if not destination.exists():
        return
    if not destination.is_dir():
        raise ValueError(f"release metadata output exists but is not a directory: {destination}")
    existing_names = {path.name for path in destination.iterdir()}
    allowed_names = {
        METADATA_OUTPUT_SENTINEL,
        "license-files",
        *REQUIRED_METADATA_FILES,
    }
    if existing_names - allowed_names:
        raise ValueError(
            "refusing to replace non-dedicated release metadata directory; unexpected entries: "
            f"{sorted(existing_names - allowed_names)}"
        )
    if existing_names and METADATA_OUTPUT_SENTINEL not in existing_names:
        raise ValueError(f"release metadata output is not marked as generated: {destination}")


def build_release_metadata(
    project_root: Path,
    output_dir: Path,
    *,
    version: str,
    git_commit: str = "",
    strict_environment: bool = False,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    destination = output_dir.expanduser().resolve()
    _assert_safe_metadata_output(root, destination)
    errors = collect_release_policy_errors(root) + collect_locked_environment_errors(
        root, reject_unlocked=strict_environment
    )
    if errors:
        raise ValueError("\n".join(errors))
    normalized_version = str(version or "").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", normalized_version):
        raise ValueError("release metadata version must contain exactly three numeric components")
    commit = _safe_git_commit(git_commit)
    created_at = _created_at()

    requirements = active_locked_requirements(load_release_lock(root))
    inventory = _load_inventory(root)
    inventory_by_name = {
        canonicalize_name(str(component["name"])): component for component in inventory["components"]
    }
    if destination.exists():
        shutil.rmtree(destination)
    license_root = destination / "license-files"
    license_root.mkdir(parents=True)
    (destination / METADATA_OUTPUT_SENTINEL).write_text("generated; safe to replace\n", encoding="utf-8")

    active_components: list[dict[str, Any]] = []
    spdx_packages: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements, start=1):
        normalized_name = canonicalize_name(requirement.name)
        policy = inventory_by_name[normalized_name]
        distribution = importlib.metadata.distribution(requirement.name)
        copied_files: list[dict[str, Any]] = []
        for license_index, source in enumerate(_license_files(distribution), start=1):
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source.name)
            relative_target = Path("license-files") / normalized_name / f"{license_index:02d}-{safe_name}"
            target = destination / relative_target
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied_files.append(
                {
                    "path": relative_target.as_posix(),
                    "sha256": _sha256(target),
                    "sizeBytes": target.stat().st_size,
                }
            )
        if not copied_files and not bool(policy.get("licenseFileOptional")):
            raise ValueError(f"installed distribution has no discoverable license file: {requirement.name}")

        component = {
            "name": str(policy["name"]),
            "normalizedName": normalized_name,
            "version": distribution.version,
            "licenseExpression": str(policy["licenseExpression"]),
            "disposition": str(policy["disposition"]),
            "licenseFiles": copied_files,
        }
        active_components.append(component)
        spdx_packages.append(
            {
                "SPDXID": f"SPDXRef-Package-{index:03d}-{normalized_name}",
                "name": str(policy["name"]),
                "versionInfo": distribution.version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": str(policy["licenseExpression"]),
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/{normalized_name}@{distribution.version}",
                    }
                ],
            }
        )

    for vendored_name in ("react", "react-dom"):
        policy = inventory_by_name[vendored_name]
        component = {
            "name": str(policy["name"]),
            "normalizedName": vendored_name,
            "version": str(policy["version"]),
            "licenseExpression": str(policy["licenseExpression"]),
            "disposition": str(policy["disposition"]),
            "licenseFiles": [{"path": "THIRD_PARTY_NOTICES.md"}],
        }
        active_components.append(component)
        index = len(spdx_packages) + 1
        spdx_packages.append(
            {
                "SPDXID": f"SPDXRef-Package-{index:03d}-{vendored_name}",
                "name": vendored_name,
                "versionInfo": str(policy["version"]),
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "Copyright (c) Facebook, Inc. and its affiliates.",
            }
        )

    lock_sha = _sha256(root / LOCK_PATH)
    bootstrap_lock_sha = _sha256(root / BOOTSTRAP_LOCK_PATH)
    inventory_sha = _sha256(root / INVENTORY_PATH)
    notices_sha = _sha256(root / NOTICES_PATH)
    environment_label = f"{sys.platform}-{platform.machine().lower()}-cp{sys.version_info.major}{sys.version_info.minor}"
    dependency_fingerprint = hashlib.sha256(
        _json_bytes(
            [
                {"name": component["normalizedName"], "version": component["version"]}
                for component in active_components
            ]
        )
    ).hexdigest()
    try:
        pip_version = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError:
        pip_version = "unknown"
    tool_inventory = {
        "python": platform.python_version(),
        "pythonImplementation": platform.python_implementation(),
        "pip": pip_version,
        "pyinstaller": importlib.metadata.version("PyInstaller"),
        "platform": platform.platform(),
    }
    tool_fingerprint = hashlib.sha256(_json_bytes(tool_inventory)).hexdigest()
    namespace_seed = hashlib.sha256(
        f"{normalized_version}|{commit}|{environment_label}|{lock_sha}|{inventory_sha}".encode("utf-8")
    ).hexdigest()
    _write_json(
        destination / "dependency-inventory.json",
        {
            "schemaVersion": SCHEMA_VERSION,
            "appVersion": normalized_version,
            "environment": environment_label,
            "components": active_components,
        },
    )
    _write_json(
        destination / "sbom.spdx.json",
        {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": f"ClassInEDBMVP-{normalized_version}-{environment_label}",
            "documentNamespace": f"https://classin-edb.invalid/spdx/{namespace_seed}",
            "creationInfo": {"created": created_at, "creators": ["Tool: ClassInEDBMVP-build_release_metadata-1"]},
            "packages": spdx_packages,
            "relationships": [
                {
                    "spdxElementId": "SPDXRef-DOCUMENT",
                    "relationshipType": "DESCRIBES",
                    "relatedSpdxElement": package["SPDXID"],
                }
                for package in spdx_packages
            ],
        },
    )
    shutil.copyfile(root / NOTICES_PATH, destination / "THIRD_PARTY_NOTICES.md")
    _write_json(
        destination / "release-provenance.json",
        {
            "schemaVersion": SCHEMA_VERSION,
            "appVersion": normalized_version,
            "gitCommit": commit,
            "createdAt": created_at,
            "environment": environment_label,
            "pythonVersion": platform.python_version(),
            "dependencyFingerprintSha256": dependency_fingerprint,
            "toolInventory": tool_inventory,
            "toolFingerprintSha256": tool_fingerprint,
            "inputs": {
                LOCK_PATH.as_posix(): lock_sha,
                BOOTSTRAP_LOCK_PATH.as_posix(): bootstrap_lock_sha,
                INVENTORY_PATH.as_posix(): inventory_sha,
                NOTICES_PATH.as_posix(): notices_sha,
            },
        },
    )
    manifest_files: list[dict[str, Any]] = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != "metadata-manifest.json":
            manifest_files.append(
                {
                    "path": path.relative_to(destination).as_posix(),
                    "sha256": _sha256(path),
                    "sizeBytes": path.stat().st_size,
                }
            )
    _write_json(
        destination / "metadata-manifest.json",
        {"schemaVersion": SCHEMA_VERSION, "appVersion": normalized_version, "files": manifest_files},
    )
    verification_errors = collect_release_metadata_errors(
        destination, expected_version=normalized_version, expected_git_commit=commit
    )
    if verification_errors:
        raise ValueError("\n".join(verification_errors))
    return {"outputDir": str(destination), "componentCount": len(active_components)}


def collect_release_metadata_errors(
    metadata_root: Path,
    *,
    expected_version: str = "",
    expected_git_commit: str = "",
) -> list[str]:
    root = metadata_root.resolve()
    errors: list[str] = []
    for file_name in REQUIRED_METADATA_FILES:
        path = root / file_name
        if not path.is_file() or path.stat().st_size <= 0:
            errors.append(f"missing non-empty release metadata file: {file_name}")
    if errors:
        return errors
    try:
        manifest = json.loads((root / "metadata-manifest.json").read_text(encoding="utf-8"))
        provenance = json.loads((root / "release-provenance.json").read_text(encoding="utf-8"))
        inventory = json.loads((root / "dependency-inventory.json").read_text(encoding="utf-8"))
        sbom = json.loads((root / "sbom.spdx.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid release metadata JSON: {exc}"]
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("release metadata manifest schemaVersion mismatch")
    manifest_entries = manifest.get("files")
    if not isinstance(manifest_entries, list) or not manifest_entries:
        errors.append("release metadata manifest must contain files")
        manifest_entries = []
    seen_paths: set[str] = set()
    for entry in manifest_entries:
        if not isinstance(entry, dict):
            errors.append("release metadata manifest contains a non-object entry")
            continue
        relative_name = str(entry.get("path") or "")
        relative_path = Path(relative_name)
        if not relative_name or relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"unsafe release metadata manifest path: {relative_name!r}")
            continue
        if relative_name in seen_paths:
            errors.append(f"duplicate release metadata manifest path: {relative_name}")
            continue
        seen_paths.add(relative_name)
        target = root / relative_path
        if not target.is_file():
            errors.append(f"release metadata manifest target is missing: {relative_name}")
            continue
        if target.stat().st_size != entry.get("sizeBytes"):
            errors.append(f"release metadata size mismatch: {relative_name}")
        if _sha256(target) != entry.get("sha256"):
            errors.append(f"release metadata SHA-256 mismatch: {relative_name}")
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "metadata-manifest.json"
    }
    if actual_files != seen_paths:
        missing = sorted(actual_files - seen_paths)
        extra = sorted(seen_paths - actual_files)
        errors.append(f"release metadata manifest file set mismatch: unlisted={missing}, missing={extra}")

    version_values = {
        str(manifest.get("appVersion") or ""),
        str(provenance.get("appVersion") or ""),
        str(inventory.get("appVersion") or ""),
    }
    if len(version_values) != 1 or "" in version_values:
        errors.append(f"release metadata app versions disagree: {sorted(version_values)}")
    elif expected_version and version_values != {expected_version}:
        errors.append(f"release metadata version mismatch: expected {expected_version}, found {version_values.pop()}")
    if sbom.get("spdxVersion") != "SPDX-2.3" or sbom.get("dataLicense") != "CC0-1.0":
        errors.append("release SBOM must declare SPDX-2.3 and CC0-1.0")
    components = inventory.get("components")
    packages = sbom.get("packages")
    if not isinstance(components, list) or not components:
        errors.append("release dependency inventory is empty")
    if not isinstance(packages, list) or len(packages) != len(components or ()):
        errors.append("release SBOM package count does not match dependency inventory")
    commit = str(provenance.get("gitCommit") or "")
    if expected_git_commit and commit != expected_git_commit:
        errors.append(f"release provenance git commit mismatch: expected {expected_git_commit}, found {commit}")
    if commit != "unknown" and not GIT_COMMIT_RE.fullmatch(commit):
        errors.append("release provenance git commit must be unknown or a full lowercase SHA-1")
    for fingerprint_name in ("dependencyFingerprintSha256", "toolFingerprintSha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get(fingerprint_name) or "")):
            errors.append(f"release provenance {fingerprint_name} must be lowercase SHA-256")
    tool_inventory = provenance.get("toolInventory")
    if not isinstance(tool_inventory, dict) or not all(
        str(tool_inventory.get(name) or "").strip()
        for name in ("python", "pythonImplementation", "pip", "pyinstaller", "platform")
    ):
        errors.append("release provenance toolInventory is incomplete")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify release SBOM/license/provenance metadata.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--root", type=Path, default=Path.cwd())
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--version", required=True)
    build_parser.add_argument("--git-commit", default=os.environ.get("EDB_RELEASE_GIT_COMMIT", ""))
    build_parser.add_argument("--strict-environment", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("metadata_root", type=Path)
    verify_parser.add_argument("--expected-version", default="")
    verify_parser.add_argument("--expected-git-commit", default="")
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            result = build_release_metadata(
                args.root,
                args.output_dir,
                version=args.version,
                git_commit=args.git_commit,
                strict_environment=args.strict_environment,
            )
            print(
                f"[release-metadata] OK: {result['componentCount']} components -> {result['outputDir']}"
            )
            return 0
        errors = collect_release_metadata_errors(
            args.metadata_root,
            expected_version=args.expected_version,
            expected_git_commit=args.expected_git_commit,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = str(exc).splitlines()
    if errors:
        for error in errors:
            print(f"[release-metadata] ERROR: {error}", file=sys.stderr)
        return 1
    print(f"[release-metadata] OK: {args.metadata_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
