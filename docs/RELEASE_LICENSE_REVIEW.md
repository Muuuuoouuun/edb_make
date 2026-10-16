# Release license review gate

This inventory is a technical release gate, not legal advice. The current local packaging environment reports dependencies with materially different redistribution terms:

| Component | Declared distribution metadata | Release concern |
|---|---|---|
| PyMuPDF | AGPL-3.0 or Artifex commercial license | A public proprietary distribution needs a confirmed commercial-license or AGPL compliance strategy. |
| pyhwp | AGPL-3.0-or-later | Confirm corresponding-source and application-level obligations before distribution. |
| PyInstaller | GPL-2.0-or-later with the PyInstaller non-free application exception | Preserve the applicable license/exception notice in release documentation. |
| Upscayl runtime, when explicitly bundled | AGPL-3.0 ecosystem | Bundling is off by default and separately gated for notices and corresponding source. |
| React / ReactDOM | MIT | The vendored files carry license headers, but the release notice set must include the MIT license text. |
| NumPy, OpenCV, Pillow, olefile, six, HWP/HWPX helpers | BSD/MIT/Apache and bundled third-party terms vary by installed version | Review the generated exact-version inventory, copied license files, and SPDX SBOM. |

The technical inventory is now reproducible and fail-closed:

- `requirements-release-bootstrap.lock`, `requirements-release.lock`, and `requirements-ci.lock` use exact versions and reviewed PyPI SHA-256 hashes.
- `release/dependency_inventory.json` binds every release dependency to a reviewed license expression and disposition.
- `scripts/build_release_metadata.py` rejects version drift or an incomplete transitive lock, copies installed license files, and generates a deterministic SPDX 2.3 SBOM, dependency/tool fingerprints, provenance, and a SHA-256 metadata manifest.
- The generated `release_metadata` directory and `release/THIRD_PARTY_NOTICES.md` are embedded in both platform packages and checked by `scripts/verify_packaged_app.py`.
- `scripts/build_release_evidence.py` binds exact installer hashes to the version, full Git commit, and verified metadata set.

Before selecting `license_compliance_approved=true` in the installer workflow:

1. Decide and document the PyMuPDF licensing path and the pyhwp AGPL distribution position.
2. Review changes to all three hashed lock files and the machine-readable dependency inventory.
3. Verify the packaged SBOM, copied license files, notices, dependency/tool fingerprints, and release evidence manifest from both runners.
4. If Upscayl is bundled, confirm `resources/upscayl/LICENSE`, `THIRD_PARTY_NOTICES.md`, and `CORRESPONDING_SOURCE.txt` match the exact shipped binary/model versions.
5. Record reviewer, date, release version, and evidence location outside this repository if the review contains commercial contract details.

The workflow checkbox is intentionally an explicit human attestation. It does not infer that copying license files alone satisfies copyleft obligations.
The only unresolved license inputs are the PyMuPDF commercial/AGPL choice, the pyhwp AGPL distribution position, and any release-specific Upscayl bundle review. Do not set the checkbox until those decisions have auditable evidence.
