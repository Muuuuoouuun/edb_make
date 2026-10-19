"""Right panel contract: every control lives where its decision is made.

The settings tab used to open on three hard-coded "ON" badges and a 368px
conversion table while the AI switch sat two screens down; session-wide
layout sat on top of a single problem's options. These tests pin the layout
that replaced it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_ui_placement_details import run_node


PROJECT_ROOT = Path(__file__).resolve().parent
APP = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
CSS = (PROJECT_ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")


def component(name: str) -> str:
    """Source of one top-level component, up to the next top-level function."""

    return APP.split(f"function {name}(", 1)[1].split("\nfunction ", 1)[0]


SIDE_PANEL = component("SidePanel")
LAYOUT_CONTROLS = component("BoardLayoutControls")
# The right panel is now a state-driven inspector (item + placement sections)
# and app settings use an inline panel opened from the settings tab.
ITEM_TAB = SIDE_PANEL.split('id="side-panel-item"', 1)[1].split('id="side-panel-placement"', 1)[0]
PLACEMENT_TAB = SIDE_PANEL.split('id="side-panel-placement"', 1)[1].split('id="side-panel-board"', 1)[0]
SETTINGS_TAB = SIDE_PANEL.split("const settingsPanel = (", 1)[1].split("  return (", 1)[0]


class TestSettingsTabHoldsRealSettings(unittest.TestCase):
    def test_no_hardcoded_badges_pose_as_settings(self) -> None:
        self.assertNotIn(">ON</span>", SETTINGS_TAB)
        self.assertNotIn("칠판 동작", SETTINGS_TAB)

    def test_bulk_actions_left_the_settings_tab(self) -> None:
        self.assertNotIn("일괄 작업", SETTINGS_TAB)
        self.assertNotIn("applyToAll('s1')", SIDE_PANEL)
        self.assertNotIn("applyToAll('s2')", SIDE_PANEL)

    def test_accent_picker_no_longer_overrides_the_token_pairs(self) -> None:
        self.assertNotIn("setAccent", SIDE_PANEL)
        self.assertNotIn("ACCENTS.map", SIDE_PANEL)

    def test_sections_are_ordered_by_how_often_they_matter(self) -> None:
        order = (
            'title="분할 방식"', 'title="AI 사용·연결"', 'title="미리보기"', 'title="도움말 및 문제 신고"',
            "앱 관리 <span", 'title="한글 문서 변환 환경"', 'title="앱 업데이트"',
        )
        positions = [SETTINGS_TAB.index(label) for label in order]

        self.assertEqual(sorted(positions), positions)

    def test_ai_preferences_stay_in_settings_and_session_recognition_does_not(self) -> None:
        ai_section = SETTINGS_TAB.split('title="AI 사용·연결"', 1)[1].split('title="미리보기"', 1)[0]

        self.assertIn("AI 기능 사용 허용", ai_section)
        self.assertIn("Gemini API 키", ai_section)
        self.assertNotIn("onRecognizeSession", SETTINGS_TAB)
        self.assertNotIn("현재 자료 문제 인식", SETTINGS_TAB)
        self.assertIn("aiConnectionLabel", ai_section)

    def test_saved_key_collapses_to_a_status_row(self) -> None:
        self.assertIn("const [keyEditorOpen, setKeyEditorOpen] = useState(false);", SIDE_PANEL)
        self.assertIn("{keyEditorOpen && (", SETTINGS_TAB)
        self.assertIn("setKeySaveError(''); setKeyEditorOpen(true);", SETTINGS_TAB)
        self.assertIn("void persistGeminiKeyDraft();", SETTINGS_TAB)
        self.assertNotIn("!userSettings?.hasGeminiApiKey || keyEditorOpen", SETTINGS_TAB)

    def test_conversion_diagnostics_never_open_on_their_own(self) -> None:
        self.assertNotIn("setHangulDetailsExpanded(hangulDetailsOpen)", APP)
        self.assertNotIn("hangulDetailsExpanded", SIDE_PANEL)
        self.assertIn('title="한글 문서 변환 환경"', SETTINGS_TAB)
        self.assertIn('className="settings-environment-disclosure"', SETTINGS_TAB)
        self.assertIn("<SidebarDisclosure", SETTINGS_TAB)

    def test_upload_method_moved_out_of_settings(self) -> None:
        self.assertNotIn("intent-control", SETTINGS_TAB)
        self.assertNotIn("업로드 옵션", SETTINGS_TAB)


class TestBoardColour(unittest.TestCase):
    def test_swatches_are_keyboard_reachable_radios(self) -> None:
        self.assertIn('role="radiogroup" aria-label="칠판 배경"', SETTINGS_TAB)
        swatch = SETTINGS_TAB.split("BOARD_COLORS.map(c => (", 1)[1].split("))}", 1)[0]

        self.assertIn("<button", swatch)
        self.assertNotIn("<div", swatch)
        self.assertIn('role="radio"', swatch)
        self.assertIn("aria-checked={boardColor === c}", swatch)
        self.assertIn("aria-label={BOARD_COLOR_LABELS[c] || c}", swatch)
        self.assertIn(".swatches .sw:focus-visible{", CSS)

    def test_board_colour_survives_a_reload(self) -> None:
        init = APP.split("function initialTweakValues(){", 1)[1].split("\n}", 1)[0]

        self.assertIn("useTweaks(useMemo(initialTweakValues, []))", APP)
        self.assertIn("writeStoredPreference(BOARD_COLOR_KEY, v)", APP)
        self.assertIn("BOARD_COLORS.includes(value)", init)


class TestPlacementOwnsSessionLayout(unittest.TestCase):
    def test_common_layout_controls_stay_visible_and_secondary_rules_are_collapsible(self) -> None:
        self.assertIn("<BoardLayoutControls", PLACEMENT_TAB)
        self.assertNotIn("<BoardLayoutControls", ITEM_TAB)
        self.assertIn('className="row-control layout-gap-mode-control"', LAYOUT_CONTROLS)
        self.assertIn('<SidebarDisclosure title="배치 규칙 및 개별 여백" summary={summary}', LAYOUT_CONTROLS)
        self.assertLess(LAYOUT_CONTROLS.index('className="row-control layout-gap-mode-control"'), LAYOUT_CONTROLS.index("<SidebarDisclosure"))
        self.assertNotIn("open={boardLayoutOpen", PLACEMENT_TAB)

    def test_permanently_disabled_column_buttons_are_gone(self) -> None:
        self.assertNotIn("[1,2,3].map", LAYOUT_CONTROLS)
        self.assertIn("기존 {columns}칸 배치를 유지합니다.", LAYOUT_CONTROLS)
        self.assertNotIn("제작 가능한 1열로 복원", LAYOUT_CONTROLS)
        self.assertNotIn("side-by-side", PLACEMENT_TAB)
        self.assertNotIn("setBoardColumns?.(2)", PLACEMENT_TAB)

    def test_compact_gap_warning_only_shows_in_compact_mode(self) -> None:
        self.assertIn("gapMode === LAYOUT_GAP_MODE_COMPACT ? (", LAYOUT_CONTROLS)
        self.assertIn('className="layout-gap-hint"', LAYOUT_CONTROLS)

    def test_publish_guard_preserves_legacy_work_without_suggesting_conversion(self) -> None:
        publish_guard = APP.split("  const onPublish = async () => {", 1)[1].split("    clearOperationRecovery();", 1)[0]
        self.assertIn("normalizeBoardColumns(boardColumns) !== BOARD_COLUMN_MIN", publish_guard)
        self.assertIn("기존 다중 칸 배치는 그대로 저장할 수 있습니다", publish_guard)
        self.assertIn("현재 EDB 제작은 1칸 배치만 지원", publish_guard)
        self.assertNotIn("setTweak", publish_guard)
        self.assertNotIn("1열로 복원", publish_guard)

    def test_legacy_publish_guard_stops_before_any_write_and_keeps_columns(self) -> None:
        run_node(r"""
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
        const start = source.indexOf('  const onPublish = async () => {');
        const end = source.indexOf('    clearOperationRecovery();', start);
        assert.ok(start >= 0 && end > start);
        (async () => {
          for (const columns of [1, 2, 3]) {
            const messages = [];
            const state = { hasRunningQueueRecognition:false, confirmingRecognition:false, queueRegistrationInFlightRef:{current:false}, pendingFiles:[], session: { problems: [{ id: 'legacy' }] },
              boardColumns: columns, BOARD_COLUMN_MIN: 1,
              publishInFlightRef: { current: false }, operationRecovery: null,
              normalizeBoardColumns: value => value, showToast: message => messages.push(message),
              proceeded: false };
            const originalSession = JSON.stringify(state.session);
            vm.createContext(state);
            vm.runInContext(source.slice(start, end)
              + 'proceeded = true; }; globalThis.publish = onPublish;', state);
            await state.publish();
            assert.equal(state.proceeded, columns === 1);
            assert.equal(state.boardColumns, columns);
            assert.equal(JSON.stringify(state.session), originalSession);
            if (columns > 1) {
              assert.equal(messages.length, 1);
              assert.ok(messages[0].includes('그대로 저장할 수 있습니다'));
              assert.ok(messages[0].includes('제작은 실행하지 않았습니다'));
            }
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestItemTab(unittest.TestCase):
    def test_bulk_apply_sits_on_the_step_choice_it_applies(self) -> None:
        header = ITEM_TAB.split("처리 방식 선택 <span", 1)[1].split('<div className="steps">', 1)[0]

        self.assertIn('className="section-hd-action"', header)
        self.assertIn("onClick={() => applyToAll(item.step)}", header)
        self.assertIn("{items.length > 1 && (", header)
        self.assertNotIn("!showItemConfirmBar", header)
        self.assertIn(">전체 적용</button>", header)


class TestRememberedPanelState(unittest.TestCase):
    def test_tab_and_open_sections_are_remembered(self) -> None:
        self.assertIn(
            "useStoredPreference(RIGHT_PANEL_TAB_KEY, 'item', value => RIGHT_PANEL_TABS.includes(value))",
            SIDE_PANEL,
        )
        self.assertIn("useStoredPreference(ADVANCED_SETTINGS_OPEN_KEY, false, isStoredBoolean)", SIDE_PANEL)
        self.assertNotIn("boardLayoutOpen", SIDE_PANEL)
        disclosure = component("SidebarDisclosure")
        self.assertIn("<details", disclosure)
        self.assertNotIn("open={", disclosure)

    def test_storage_failures_never_break_rendering(self) -> None:
        for name in ("readStoredPreference", "writeStoredPreference"):
            with self.subTest(helper=name):
                body = APP.split(f"function {name}(", 1)[1].split("\n}", 1)[0]
                self.assertIn("try {", body)
                self.assertIn("catch (_error)", body)


class TestTabNavigation(unittest.TestCase):
    def test_tabs_announce_and_honour_alt_digit_shortcuts(self) -> None:
        for digit in ("1", "2", "3"):
            with self.subTest(digit=digit):
                self.assertEqual(1, SIDE_PANEL.count(f'aria-keyshortcuts="Alt+{digit}"'))
        self.assertIn("{ Digit1: 'item', Digit2: 'placement', Digit3: 'board' }", APP)

    def test_shortcuts_stay_out_of_text_fields_and_other_chords(self) -> None:
        handler = SIDE_PANEL.split("const onKeyDown = (evt) => {", 1)[1].split("};", 1)[0]

        self.assertIn("isEditableKeyboardTarget(evt.target)", handler)
        self.assertIn("evt.metaKey || evt.ctrlKey || evt.shiftKey", handler)

    def test_settings_uses_inline_panel_without_modal_focus_trap(self) -> None:
        self.assertIn("const settingsTabActive = tab === 'board';", SIDE_PANEL)
        self.assertIn('id="side-panel-board" role="region"', SIDE_PANEL)
        self.assertIn('hidden={!settingsTabActive}', SIDE_PANEL)
        self.assertNotIn('settingsDialogRef', SIDE_PANEL)
        self.assertNotIn('settings-dialog-backdrop', SIDE_PANEL)


class TestStatusChips(unittest.TestCase):
    def test_no_status_chip_paints_its_own_background_inline(self) -> None:
        inline = re.findall(r"className=\{?[`\"]pos-tag[^>]*?style=\{\{\s*background", APP)

        self.assertEqual([], inline, "status chips must use the tone-* classes")

    def test_conversion_status_returns_tone_names(self) -> None:
        body = APP.split("function hangulRuntimeStatusMeta(hangul){", 1)[1].split("\n}", 1)[0]
        tones = set(re.findall(r"tone: '([^']+)'", body))

        self.assertEqual({"neutral", "ok", "warn", "danger"}, tones)
        for tone in tones:
            with self.subTest(tone=tone):
                self.assertIn(f".pos-tag.tone-{tone}{{", CSS)

    def test_update_status_returns_tone_names(self) -> None:
        body = SIDE_PANEL.split("const updateStatusTone = ", 1)[1].split(";", 1)[0]
        # Only the values each branch yields, not the statuses it compares against.
        tones = set(re.findall(r"[?:]\s*'([a-z]+)'", body))

        self.assertEqual({"danger", "accent", "ok", "neutral"}, tones)
        self.assertNotIn("var(--", body)
        for tone in tones:
            with self.subTest(tone=tone):
                self.assertIn(f".pos-tag.tone-{tone}{{", CSS)


if __name__ == "__main__":
    unittest.main()
