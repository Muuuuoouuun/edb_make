from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(shutil.which("zsh"), "macOS packager uses zsh")
class TestMacOSDmgRetry(unittest.TestCase):
    def run_create(self, mode: str) -> tuple[subprocess.CompletedProcess[str], int, list[str], bool]:
        script = (ROOT / "package_macos_app.sh").read_text()
        helper = re.search(r"create_dmg_with_retry\(\) \(\n.*?\n\)\n", script, re.S)
        self.assertIsNotNone(helper)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "tools"
            tools.mkdir()
            output = root / "출력 with spaces"
            output.mkdir()
            app = root / "Test App.app"
            app.mkdir()
            (app / "Contents.txt").write_text("immutable app payload")
            counter = root / "attempts"
            sleep_log = root / "sleeps"
            staging_log = root / "staging-paths"
            for name, content in {
                "ditto": '#!/bin/sh\ncp -R "$1" "$2"\n',
                "sleep": '#!/bin/sh\nprintf "%s\\n" "$1" >> "$SLEEP_LOG"\n',
                "hdiutil": r'''#!/bin/sh
count=0
if [ -f "$COUNTER" ]; then count=$(cat "$COUNTER"); fi
count=$((count + 1))
printf '%s' "$count" > "$COUNTER"
previous=''
for argument in "$@"; do
  if [ "$previous" = '-srcfolder' ]; then printf '%s\n' "$argument" >> "$STAGING_LOG"; fi
  target="$argument"
  previous="$argument"
done
printf 'partial dmg' > "$target"
case "$RETRY_TEST_MODE" in
  success) exit 0 ;;
  busy_then_success) if [ "$count" -ge 3 ]; then exit 0; fi ;;
  empty_success) : > "$target"; exit 0 ;;
  permission) printf 'hdiutil: create failed - Permission denied\n' >&2; exit 13 ;;
  other_busy) printf 'unrelated helper: Resource busy\n' >&2; exit 17 ;;
esac
printf 'hdiutil: create failed - Resource busy\n' >&2
exit 16
''',
            }.items():
                path = tools / name
                path.write_text(content)
                path.chmod(0o755)
            # Only replace the fixed Apple ditto executable in the test copy.
            # The real production script continues to use /usr/bin/ditto.
            body = helper.group(0).replace("/usr/bin/ditto", '"$STUB_DITTO"')
            harness = root / "run.zsh"
            harness.write_text("set -euo pipefail\n" + body + '\ncreate_dmg_with_retry "$APP" "$DMG" "Test App" "$OUTPUT"\n')
            result = subprocess.run(
                [shutil.which("zsh"), str(harness)], capture_output=True, text=True,
                env={**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                     "STUB_DITTO": str(tools / "ditto"), "COUNTER": str(counter),
                     "SLEEP_LOG": str(sleep_log), "STAGING_LOG": str(staging_log), "RETRY_TEST_MODE": mode,
                     "APP": str(app), "DMG": str(output / "Test App.dmg"), "OUTPUT": str(output)},
            )
            attempts = int(counter.read_text())
            staged_paths = staging_log.read_text().splitlines()
            self.assertEqual(attempts, len(set(staged_paths)), "each attempt must use fresh staging")
            self.assertTrue(all(not Path(path).exists() for path in staged_paths), "staging must be removed")
            remaining = sorted(path.name for path in output.iterdir())
            app_unchanged = (app / "Contents.txt").read_text() == "immutable app payload"
            if sleep_log.exists():
                self.assertEqual(["2", "4"][:attempts - 1], sleep_log.read_text().splitlines())
            return result, attempts, remaining, app_unchanged

    def test_busy_create_retries_with_fresh_staging_and_keeps_verified_candidate(self):
        result, attempts, remaining, unchanged = self.run_create("busy_then_success")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(3, attempts)
        self.assertEqual(["Test App.dmg"], remaining)
        self.assertTrue(unchanged)
        self.assertEqual(2, result.stderr.count("hdiutil: create failed - Resource busy"))

    def test_persistent_busy_is_bounded_and_removes_partial_output(self):
        result, attempts, remaining, unchanged = self.run_create("always_busy")
        self.assertEqual(16, result.returncode)
        self.assertEqual(3, attempts)
        self.assertEqual([], remaining)
        self.assertTrue(unchanged)
        self.assertEqual(3, result.stderr.count("hdiutil: create failed - Resource busy"))

    def test_nonbusy_failure_and_unrelated_busy_are_not_retried(self):
        for mode, status, message in (("permission", 13, "Permission denied"),
                                      ("other_busy", 17, "unrelated helper: Resource busy")):
            with self.subTest(mode=mode):
                result, attempts, remaining, unchanged = self.run_create(mode)
                self.assertEqual(status, result.returncode)
                self.assertEqual(1, attempts)
                self.assertEqual([], remaining)
                self.assertTrue(unchanged)
                self.assertIn(message, result.stderr)

    def test_first_success_and_false_success_both_clean_staging(self):
        for mode, expected_status, expected_files in (("success", 0, ["Test App.dmg"]),
                                                     ("empty_success", 1, [])):
            with self.subTest(mode=mode):
                result, attempts, remaining, unchanged = self.run_create(mode)
                self.assertEqual(expected_status, result.returncode, result.stderr)
                self.assertEqual(1, attempts)
                self.assertEqual(expected_files, remaining)
                self.assertTrue(unchanged)

    def test_packager_still_verifies_created_image_and_its_app_contents(self):
        script = (ROOT / "package_macos_app.sh").read_text()
        section = script.split('if [[ "$DMG" == "1" && -d "$APP_PATH" ]]; then', 1)[1]
        created = section.index('create_dmg_with_retry "$APP_PATH" "$DMG_PATH"')
        self.assertLess(created, section.index('require_nonempty_file "$DMG_PATH"'))
        self.assertLess(created, section.index('hdiutil verify "$DMG_PATH"'))
        self.assertLess(created, section.index('verify_dmg_contains_app "$DMG_PATH" "$APP_NAME"'))
