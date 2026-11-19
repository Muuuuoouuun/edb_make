"""Regression checks for the ui-copy pass: Korean-only labels, the OS-aware
launch name, readable status colours, screen-reader announcements, the PNG
download error and the user guide wording."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_ui_home_flows import NODE_SETUP, run_node


PROJECT_ROOT = Path(__file__).resolve().parent
APP = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
CSS = (PROJECT_ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")
GUIDE = (PROJECT_ROOT / "EDB_MAKE_USER_GUIDE.md").read_text(encoding="utf-8")
ISS = (PROJECT_ROOT / "installer" / "windows" / "ClassInEDBMVP.iss").read_text(encoding="utf-8")


class TestDevUiCopy(unittest.TestCase):
    def test_launch_name_follows_the_installed_app_on_each_os(self) -> None:
        run_node(NODE_SETUP + r"""
        const header = section('const RUNTIME_PLATFORM', 'const PRIMARY_MODIFIER_LABEL');
        const nameOn = platform => {
          const sandbox = { navigator: { platform } };
          vm.createContext(sandbox);
          vm.runInContext(header + '\nglobalThis.name = APP_LAUNCH_NAME;', sandbox);
          return sandbox.name;
        };
        assert.equal(nameOn('MacIntel'), 'ClassInEDBMVP');
        assert.equal(nameOn('Win32'), 'ClassIn EDB');
        const sandbox = {
          APP_LAUNCH_NAME: 'ClassInEDBMVP',
          isNetworkRequestError: () => true,
          probeLocalServerHealth: async () => false,
        };
        vm.createContext(sandbox);
        vm.runInContext(section('async function classifyNetworkRequestError(', 'function simpleToastErrorMessage('), sandbox);
        (async () => {
          const error = await sandbox.classifyNetworkRequestError(new TypeError('Failed to fetch'), 'x');
          assert.equal(error.message, 'ClassInEDBMVP 앱이 꺼져 있거나 응답하지 않습니다. 앱을 다시 실행해 주세요.');
          assert.equal(error.recoverySteps[0], 'ClassInEDBMVP 앱을 다시 실행해 주세요.');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)
        # A quoted (non-template) string would show the placeholder text on screen:
        # every use must sit after an odd number of backticks on its line.
        for line in APP.splitlines():
            for match in re.finditer(r"\$\{APP_LAUNCH_NAME\}", line):
                self.assertEqual(line[:match.start()].count("`") % 2, 1, line.strip())
        self.assertGreaterEqual(APP.count("${APP_LAUNCH_NAME} 앱을 다시 실행"), 5)
        self.assertNotIn("ClassIn EDB 앱을", APP)
        self.assertNotIn("ClassIn EDB 앱이", APP)

    def test_board_and_home_labels_are_korean_and_use_one_name_per_thing(self) -> None:
        for gone in (
            "'ON' : 'OFF'", "스마트 이동 ON", "'HQ'", "return 'IMG'", "`예상 C$", "C{tileClassinStartPage}",
            "1.2 맞춤", "로컬 처리", "로컬로", "파싱", "최신 세션", "유효한 세션", "현재 세션을",
            "서버의", "서버 내용", "서버는", "보드 순서", "보드 자료", "보드 배치", "보드 너비",
            "EDB 만들기", "리포트 보내기", "리포트를 보내지", "리포트 전송", "리포트를 전송",
            'aria-label="되돌리기"', "'되돌리기'", "이름 없는 작업", "작업 이름으로", "최근 작업",
            "ID, 번호", "고객을 확인할 수 있는 ID", "'OCR 미사용'", "로컬 2K",
            "<code>{errorSnapshot.code || 'unknown'}</code>",
        ):
            self.assertNotIn(gone, APP, gone)
        for kept in (
            "{smartMove ? '켜짐' : '꺼짐'}", "스마트 이동을 켜면:", "`예상 ${previewEstimate.classinPageCount}쪽`",
            "{tileClassinStartPage}–{tileClassinEndPage}쪽", "it.step === 's3' ? '고화질'",
            ">쪽 맞춤</button>", "<strong>EDB 제작</strong>", "'신고 보내기'", 'aria-label="실행 취소"',
            "'이름 없는 수업'", 'placeholder="수업 이름으로 검색"',
        ):
            self.assertIn(kept, APP, kept)

    def test_image_kind_label_is_korean_but_keeps_its_css_class(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = {
          isPdfFile: file => file.name.endsWith('.pdf'),
          isHwpFile: file => /\.hwpx?$/.test(file.name),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function sourceFileKindLabel(', 'function formatBytes('), sandbox);
        assert.equal(sandbox.sourceFileKindLabel({ name: 'a.png' }), '이미지');
        assert.equal(sandbox.sourceFileKindLabel({ name: 'a.pdf' }), 'PDF');
        assert.equal(sandbox.sourceFileKindLabel({ name: 'a.hwpx' }), 'HWP');
        """)
        self.assertIn("kind-${{ PDF: 'pdf', HWP: 'hwp' }[kind] || 'img'}", APP)

    def test_png_download_error_shows_the_apps_korean_sentence(self) -> None:
        run_node(NODE_SETUP + r"""
        let reply;
        const sandbox = {
          fetch: async () => reply,
          withExpectedSessionRevision: payload => payload,
          filenameFromContentDisposition: (_header, fallback) => fallback,
        };
        vm.createContext(sandbox);
        vm.runInContext(section('async function fetchProblemImageDownload(', 'function triggerBlobDownload('), sandbox);
        const respond = (status, body) => ({ ok: false, status, text: async () => body });
        (async () => {
          reply = respond(500, JSON.stringify({ ok: false, code: 'problem_image_unavailable',
            error: '문항 이미지를 만들지 못했습니다.', detail: 'OSError: disk', recoverySteps: ['잠시 후 다시 시도해 주세요.'] }));
          let error = await sandbox.fetchProblemImageDownload('p1', { session: {} }).then(() => null, e => e);
          assert.equal(error.message, '문항 이미지를 만들지 못했습니다.');
          assert.equal(error.code, 'problem_image_unavailable');
          assert.equal(error.detail, 'OSError: disk');
          assert.equal(JSON.stringify(error.recoverySteps), '["잠시 후 다시 시도해 주세요."]');
          reply = respond(404, '<html><body>problem not found</body></html>');
          error = await sandbox.fetchProblemImageDownload('p1').then(() => null, e => e);
          assert.equal(error.message, 'PNG를 내려받지 못했습니다');
          assert.equal(error.code, 'http_404');
          assert.ok(error.detail.includes('problem not found'));
          assert.ok(!error.message.includes('{') && !error.message.includes('HTTP'));
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)
        caller = APP.split("const result = await fetchProblemImageDownload(", 1)[1].split("finally {", 1)[0]
        self.assertIn("/[가-힣]/.test(reason) ? reason.replace(", caller)

    def test_undo_toast_pauses_while_hovered_or_focused(self) -> None:
        run_node(NODE_SETUP + r"""
        const timers = [];
        let cleared = 0;
        const sandbox = {
          useCallback: fn => fn, toastTimerRef: { current: null }, actionToastTimerRef: { current: null },
          setToast() {}, setActionToast() {},
          window: { setTimeout: (_fn, ms) => { timers.push(ms); return timers.length; }, clearTimeout() { cleared += 1; } },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const armActionToastTimer = useCallback(', '  const showToast = useCallback(')
          + section('  const showActionToast = useCallback(', '  const showSimpleErrorToast')
          + '\nglobalThis.arm = armActionToastTimer; globalThis.showActionToast = showActionToast;', sandbox);
        sandbox.showActionToast('삭제했어요', '실행 취소', () => {});
        assert.deepEqual(timers, [6500]);
        sandbox.arm(0);
        assert.equal(sandbox.actionToastTimerRef.current, null, 'hover/focus stops the close timer');
        assert.ok(cleared >= 1);
        sandbox.arm(4000);
        assert.deepEqual(timers, [6500, 4000]);
        """)
        undo_toast = APP.split('className="undo-toast"', 1)[1].split('className="undo-toast-copy"', 1)[0]
        self.assertIn("onMouseEnter={() => armActionToastTimer(0)}", undo_toast)
        self.assertIn("onFocus={() => armActionToastTimer(0)}", undo_toast)
        self.assertIn("!toastEl.contains(event.relatedTarget)", undo_toast)
        self.assertNotIn("role=", undo_toast)

    def test_one_global_status_region_and_no_ticking_live_regions(self) -> None:
        app = APP.split("function App()", 1)[1]
        self.assertEqual(1, app.count("{statusAnnouncement}</div>"))
        self.assertIn('<div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{statusAnnouncement}</div>', app)
        announcement = app.split("const statusAnnouncement =", 1)[1].split(";\n", 1)[0]
        self.assertNotIn("경과", announcement)
        self.assertIn("loading.label", announcement)
        self.assertIn("toast.type !== 'error'", announcement)
        self.assertIn("role={isError ? 'alert' : undefined}", app)
        jobs = APP.split("function BackgroundJobsPanel", 1)[1].split("const CENTER_PANEL_PREVIEW_MAX_DIMENSION", 1)[0]
        self.assertNotIn("aria-live", jobs)
        overlay = APP.split("function LoadingOverlay", 1)[1].split("function BackgroundJobsPanel", 1)[0]
        self.assertNotIn("aria-live", overlay)

    def test_status_labels_use_the_readable_solid_tokens(self) -> None:
        for rule in (
            ".review-bbox.risky .review-bbox-label{ background: var(--warn-solid); }",
            ".review-bbox.status-failed .review-bbox-label{ background: var(--danger-solid); }",
            ".review-bbox.page-chrome-artifact .review-bbox-label{ background: var(--danger-solid); }",
            ".recognition-box.status-check-needed span{ background: var(--warn-solid); }",
            ".recognition-box.status-failed span{ background: var(--danger-solid); }",
            "body.dark .recognition-summary span.warn{ color: #e8956f; }",
        ):
            self.assertIn(rule, CSS)
        self.assertNotIn("color: #b85737;", CSS)
        self.assertNotIn("    color: #aa6516;\n    border-color: rgba(217,119,87,.35);", CSS)

    def test_long_toasts_wrap_in_a_rounded_box(self) -> None:
        toast = CSS.split("  .toast{", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: min(560px, calc(100vw - 32px));", toast)
        self.assertIn("width: max-content;", toast)
        self.assertIn("line-height: 1.45;", toast)
        self.assertNotIn("border-radius: 999px;", toast)

    def test_guide_matches_installed_names_and_macos_requirement(self) -> None:
        display_name = re.search(r'#define AppDisplayName "([^"]+)"', ISS).group(1)
        self.assertEqual(display_name, "ClassIn EDB")
        self.assertIn(f"시작 메뉴의 `{display_name}`를 실행합니다", GUIDE)
        self.assertIn(f"`{display_name}`를 제거합니다", GUIDE)
        self.assertIn("macOS 14 이상에서 설치할 수 있습니다", GUIDE)
        self.assertNotIn("macOS 12·13", GUIDE)
        self.assertIn("브라우저 탭을 실수로 닫았다면 `응용 프로그램` 폴더에서 `ClassInEDBMVP`를 다시 열면", GUIDE)
        for gone in ("EDB 메이크는 기본", "EDB 메이크를 실행", "`EDB 만들기`", "`리포트 보내기`", "세션", "서버의", "로컬"):
            self.assertNotIn(gone, GUIDE, gone)


if __name__ == "__main__":
    unittest.main()
