"""Report updater configuration without printing PINs or their verifiers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from .build_app_update_config import parse_update_pin_verifier, verify_update_pin
except ImportError:
    from build_app_update_config import parse_update_pin_verifier, verify_update_pin


def safe_url(value):
    parsed = urlsplit(str(value or ""))
    if not parsed.hostname:
        return ""
    netloc = parsed.hostname
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def audit_config(root: Path, *, build_pin: str = "") -> dict:
    candidates = [root / "_internal/app_update_config.json",
                  root / "Contents/Resources/app_update_config.json",
                  root / "Contents/Frameworks/app_update_config.json",
                  root / "app_update_config.json"]
    files = {p.resolve() for p in candidates if p.is_file()}
    if len(files) != 1:
        raise ValueError("Expected one unambiguous packaged app_update_config.json")
    config = json.loads(next(iter(files)).read_text(encoding="utf-8-sig"))
    verifier = str(config.get("updatePinHash") or config.get("update_pin_hash") or "").strip()
    parsed = parse_update_pin_verifier(verifier)
    feed = str(config.get("updateFeedUrl") or config.get("update_feed_url") or "").strip()
    download = str(config.get("downloadUrl") or config.get("download_url") or "").strip()
    return {
        "version": str(config.get("version") or ""),
        "appId": str(config.get("appId") or config.get("app_id") or ""),
        "updateFeedConfigured": bool(feed), "updateFeedUrlWithoutCredentials": safe_url(feed),
        "downloadConfigured": bool(download), "downloadUrlWithoutCredentials": safe_url(download),
        "pinConfigured": bool(verifier), "pinVerifierValid": parsed is not None,
        "pinIterations": parsed[0] if parsed else None,
        "currentBuildSecretConfigured": bool(build_pin),
        "matchesCurrentBuildSecret": verify_update_pin(build_pin, verifier) if build_pin and parsed else None,
        "defaultPinExists": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-pin-env", default="EDB_AUDIT_UPDATE_PIN")
    args = parser.parse_args()
    report = audit_config(args.package, build_pin=os.environ.get(args.build_pin_env, ""))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
