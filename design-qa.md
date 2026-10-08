# 최신 재검증 — 2026-10-04

현재 구현의 검증 근거는 이 절에 기록한다. 아래의 기존 Design QA는 당시 구현과 화면의 이력으로 보존한다.

첫 번째 「우측 사이드바 3탭 심화 배치」 절에 있는 지문 규칙 체크박스 토글과 `배치 적용` → `적용됨` 검증은 **현재 구현에서는 대체된 근거**다. 그 체크박스들은 실제 배치 규칙을 바꾸지 않아 삭제했으며, 자동 배치의 실제 동작을 설명한다. 저장 상태는 버튼을 눌렀는지가 아니라 현재 배치와 저장된 세션의 차이로 판단한다. 미저장 변경, 저장 성공, 저장 실패 후 조절값 보존을 새 회귀 검사와 격리된 브라우저 흐름으로 확인한다.

이번 개선 직전 기준 검사에서 UI 314개, crop·merge·API·export 회귀 23개, 설정·캐시·입력·AI·투명도·Upscayl 검사 190개와 서브테스트 12개가 통과했다. 이번 변경 후 UI 367개와 최종 번들에 대한 패키징 manifest 74개가 통과했다. 이는 아래 과거 테스트 수와 별개인 현재 검사 결과다.

추가 UI 회귀 53개는 실제 JSX를 Babel로 렌더링하거나 소스 핸들러를 Node VM에서 실행하여 다음 상태를 확인한다.

- 미저장 배치·이름·처리 방식·여백이 있는 새로고침의 취소/승인, 승인 후 실행 취소, 통신 실패와 잘못된 복원 응답에서 초안·히스토리 보존, 새로고침 후 화면과 선택 유지 및 삭제된 선택의 안전한 대체.
- 접힌 제작 결과에서도 EDB 다운로드 사용 가능, 2·3열 세션의 1열 복원, 빈 패널의 실제 업로드 진입, API 키 저장 실패 후 입력 보존, 상세 편집 접기와 탭 이동 후 crop 초안 보존.
- 12·34.3·60쪽 길이와 작은 viewport의 자동 맞춤, resize와 선택 변경 시 맞춤 재계산, 수동 확대의 범위 유지, 실제 tile 위치 기준 포커스, 맞춤 중 스크롤의 선택 보존, 빈 홈에서 자료를 불러온 뒤 ResizeObserver 연결.
- 한 페이지를 한 문제로 등록하는 선택이 API 요청과 최종 세션에 반영되는지, 수동 쪼개기/AI/공통 지문 분기의 의도 유지, 원본 파일명에서 생성된 업로드 식별자만 제거하는지, 여러 입력 파일에서 digest가 유일하게 일치하는 원본 이름만 연결하는지.
- V1 최대 표시 폭과 V2 배율이 실제 이미지 record 높이와 일치하는지, 긴 페이지 다음 문항 시작점과 기본 20px 여백이 맞는지, compact와 개별 여백의 우선순위, 일반 문항의 예약 공간과 실제 표시 높이 분리, 요청 배율 3 보존과 서버 geometry 갱신 후 허위 미저장 상태 방지, 구형 실제 이미지 세션과 샘플 데이터의 호환성.
- 제작 전 겹침 검사에서 실제 표시 폭 제한과 원본 geometry를 반영해 정상 배치를 통과시키고 실제 겹침은 거부하는지, 제작 성공 응답의 정밀 geometry와 편집 값을 화면에 적용해 저장 상태·선택·요청 배율을 유지하는지.
- 같은 클릭에서 전체 처리 방식을 바꾸고 처리 확인을 실행할 때 재렌더 전 closure가 이전 배열을 참조해도 새 처리 값을 저장하는지, 저장/확인 각각의 실패와 busy 상태에서 초안·저장 기준·히스토리를 보존하는지, 실행 취소가 이전 처리 방식과 crop·배치를 복구하는지.
- 실제 SidePanel에서 V1/V2 긴 이미지 높이 24.2p/19.1p와 일반 문항의 표시 높이를 요약하는지, 전체 배치 위치를 기준으로 겹침을 판단하는지, 자석 열을 `null`로 지운 선택이 오래된 별칭 값 때문에 되살아나지 않는지, 유효한 열 0과 잘못된 열의 fallback을 구분하는지.
- 수동 확대에서 선택 자료로 부드럽게 이동하는 동안 명시적 선택을 유지하면서 페이지 counter는 갱신하는지, 사용자의 휠·포인터 입력이 프로그램 이동을 취소하고 정상 스크롤 선택으로 돌아가는지.

추가 재현 결함은 다음과 같이 분류해 수정했다.

| 분류 | 재현된 문제 | 수정 및 검증 |
| --- | --- | --- |
| 처리 상태 전달 | 전체 처리 방식 적용 직후 확인하면 이전 처리 값으로 저장됨 | 같은 클릭의 변경 배열을 mutation에 전달하고, 실제 frozen closure와 실행 취소/실패 경계를 검사 |
| 배치 표시 값 | 긴 자료 요약이 실제 24.2p 대신 기본 슬롯 1.2p로 표시됨 | 표시 높이를 실제 record geometry로 계산하고 V1/V2·일반 자료·그룹 위치 검사 |
| 편집 값 보존 | 지운 자석 열이 오래된 별칭 값으로 다시 연결됨 | 명시적 `null`을 우선하며 map/reflow/저장/제작 후 복원까지 검사 |
| 원본 파일 보관 | 제작 후 crop 되돌리기용 원본이 정리 대상이 될 수 있음 | 기준 이미지 경로를 활성 세션·히스토리·복원의 보호 목록에 포함하고 실제 crop 초기화 검사 |
| 선택 상태 경합 | 수동 확대에서 자료 2를 선택한 뒤 부드러운 이동 중 44~104ms 동안 자료 1로 되돌아가 빠른 편집이 다른 자료에 적용될 수 있음 | 프로그램 이동 중 선택 목표를 유지하며 사용자 스크롤은 허용하고, 실제 RAF 회귀 및 브라우저 3회 probe 검사 |

별도 서버 회귀 5개는 제작 후 요청 배율과 편집 controls를 보존하면서 최종 측정 위치를 덮어쓰지 않는지, `false`·`0`·`null`과 필드 별칭을 정확하게 보존하는지, manual crop의 되돌리기 context를 독립 복사하고 빈 값으로 초기화하는지 확인한다. 원본 crop 파일을 정리 작업과 세션 복원에서 보존하고, 제작 후 JSON 복원과 정리를 거쳐도 실제 crop 초기화가 원본 이미지와 기준 영역을 복구하는지 확인한다.

실제 브라우저에서 다음 흐름도 확인했다. 파일을 한 문제로 등록한 최종 intent, 드래그 crop 적용 후 ID·순서 유지와 Esc 취소, 비교 슬라이더 키보드 조절, 미저장 새로고침 취소·승인과 실행 취소, 배치·API 키 저장 500 실패 후 초안 보존과 재시도, 키보드 재정렬과 실행 취소, 복원 500 실패 후 히스토리 보존과 재시도, 비어 있는 배치 탭의 실제 파일 선택기 진입이 통과했다. 1440×900·1280×800·1024×768·800×800·600×900px 창에서 선택 전체 맞춤의 이미지가 viewport 안에 들어오고, 우측 하단 버튼과 패널의 가로 넘침이 없음을 확인했다. 각 체크포인트에서 제목과 주요 내용이 표시됐고 예상하지 않은 overlay는 0개였으며 실제 interaction이 통과했다.

현재 실제 화면 근거는 `/tmp/edb-ui-flow/fit-{w}.png`, `placement-final-{w}.png`, `settings-final-{w}.png`, `manual-crop.png`, `empty-placement.png`다. 혼합된 긴 페이지와 일반 문항에 `topRatio=0.02` 자르기를 적용한 뒤 처리 확인, 제작 및 EDB 다운로드가 성공했다. 제작 후 저장 표시·선택한 두 번째 자료·요청 배율 3과 1.26·manual crop 및 기준 영역/이미지/칠판 이미지 경로가 유지됐다. 다운로드한 EDB는 디스크 파일과 바이트가 같았고, 파싱 결과 record 2개와 header 50쪽을 확인했다. 최종 실제 제작 검사에서 console 오류·HTTP 오류·브라우저 crash는 각각 0개였다.

혼합 처리 `s2,s1` → 전체 처리 `s1,s1` → 실행 취소 `s2,s1` → 재적용 `s1,s1` → 제작 및 다운로드를 실제 화면에서 확인했다. 실행 취소 요청과 응답에 이전 처리 값이 들어갔으며, 재제작 후 배율·crop 기준 정보·저장 표시가 유지됐다. 최종 번들 `bcd59ba3…`에서는 기본 수동 100% 상태에서 자료 2 선택 → 빠르게 처리 S1 선택 → 전체 처리 checkbox → 처리 확인도 `s1,s1`과 자료 2 선택을 유지했다. 이어서 제작 200 응답과 저장 표시, 배율 3/1.26, `topRatio=0.02` 및 crop 기준 경로 유지, 468,740bytes 다운로드의 디스크 바이트 일치를 재확인했다.

최종 번들 `bcd59ba3…`에서 수동 확대 선택 경합의 실제 브라우저 probe 3회도 통과했다. 두 번째 자료가 활성화된 뒤 300ms와 1초까지 선택과 해당 사이드바 제목이 유지됐고, 이전 자료가 잘못 선택된 frame은 0개였다. 이 probe에서 GET 외 요청은 0개였다.

최종 전체 non-trial pytest에서는 1,460개와 서브테스트 209개가 70.66초에 통과했다. 실패는 0개이며 SWIG deprecation 경고 5개가 있었다. 최종 번들에서 5개 viewport의 전체 이미지 맞춤과 가로 넘침 없음, 제목 `칠판 자료 편집기`, 예상하지 않은 overlay 0개, console 오류/경고 및 HTTP 오류 0개를 확인했다. `git diff --check`와 최종 source/CSS 재빌드 후 `scripts/verify_frontend_package.py`의 digest·deterministic bundle 검사는 통과했다. 실제 브라우저 결과 요약은 `/tmp/edb-ui-flow/qa-result.json`에 기록했다.

검증은 `/tmp/edb-ui-flow` 및 `/tmp/edb-ui-polish`의 합성 세션과 격리된 로컬 서버를 사용한다. 실제 사용자 runtime, 설치된 앱, 외부 AI 호출은 이번 UI 검사에 사용하지 않는다.

이 환경에는 Browser plugin이 없어 기존 설치된 Playwright 1.63과 Chromium revision 1243으로 검사했다. 실제 QA URL은 `http://127.0.0.1:8877/board.html`, 빈 세션의 업로드 진입 검사는 `http://127.0.0.1:8878/board.html`이었다.

---

# Design QA — 우측 사이드바 3탭 심화 배치

final result: passed

## Visual truth

- Source visual truth: `.design-audit/selected-sidebar-three-tabs.png` (1672×941)
- Final implementation: `.design-audit/sidebar-implementation-final-1404x944.jpg` (1404×944)
- Responsive implementation: `.design-audit/sidebar-implementation-responsive-1100x720.jpg` (1100×720)
- Full comparison: `.design-audit/sidebar-comparison-full-final.jpg`
- Focused sidebar comparison: `.design-audit/sidebar-comparison-focused-final.jpg`

## Viewport and state

- Primary QA viewport: 1404×944, `http://127.0.0.1:8765/board.html`
- Responsive QA viewport: 1100×720
- Session state: latest 16-page saved session, page 1 selected
- Panel state: `배치` active, `자동 추천` active, `50%` width selected
- Reference-specific passage-group title was unavailable in this saved session, so the implementation correctly rendered the single-page fallback context and disabled the unavailable group scope.

## Comparison evidence

- Full-frame comparison confirms the existing editor chrome, three equal tabs, right-panel hierarchy, persistent footer, and board canvas remain aligned with the selected direction.
- Focused comparison confirms the intended order: context → edit scope → layout preset → size/position → passage rules → diagnosis → apply action.
- The implementation intentionally preserves the product's existing directional pad and native checkbox controls instead of copying the reference's purely visual alignment glyphs and custom switches.

## Interaction evidence

- `자료 → 설정 → 배치` transitions each returned `aria-selected="true"` for the active tab.
- The `50%` width preset returned `aria-pressed="true"` and updated the placement scale.
- The `공간 부족 시 다음 칠판` rule toggled off and restored to a checked state.
- The primary action changed from `배치 적용` to `적용됨` without marking the review item as confirmed.
- Browser runtime logs were empty after desktop and responsive interaction passes.

## Defect ledger

- P0: none
- P1: none
- P2: none
- P3: native checkbox visuals differ from the reference's custom switches; retained for platform consistency, accessibility, and lower interaction risk.

## QA history

1. Initial pass: implemented three tabs and moved scale/position/group controls into the new `배치` tab.
2. Visual pass: widened the desktop panel, added scoped presets, diagnosis, and a sticky apply footer.
3. Interaction pass: verified tabs, presets, rules, responsive layout, and clean runtime logs.
4. Final fix: removed the unrelated review-confirm side effect from `배치 적용` and increased passage-rule rows to a 44px touch target.
5. Automated verification: frontend package check passed; 124 focused UI tests passed; `git diff --check` passed.

---

# Design QA — 검수 완료 및 수동 편집 UX

final result: passed

## Visual truth

- Accepted source direction: `/Users/clmagi/.codex/generated_images/019f88e7-f56b-7a42-9c33-32aa09aa6e4c/exec-f83b593f-b15c-479b-8ab5-0a3484893a76.png` (1586×992)
- Selection basis: option 1's persistent completion bar combined with option 3's plain-language task labels, as explicitly approved by the user.
- Final implementation: `.audit/review-flow-2026-07-22/05-implemented-review.png` (1280×720)
- Full comparison: `.audit/review-flow-2026-07-22/06-design-comparison.png`
- Focused completion comparison: `.audit/review-flow-2026-07-22/07-completion-bar-comparison.png`

## Viewport and state

- Primary QA viewport: 1280×720, `http://127.0.0.1:8765/board.html?view=review`
- Session state: 16 pages, 16 detected problems, all 16 confirmed, page 1 selected.
- Panel state: default review view with the completion bar visible; manual split state checked separately.
- The source direction shows the final-item state at 15/16, while the saved QA session is already complete at 16/16. The comparison therefore verifies the same component in its adjacent state, and the exact last-confirm transition is covered by regression tests.

## Comparison evidence

- Full-frame comparison confirms that the existing editor chrome remains intact while the completion action gains a stable, prominent location at the bottom of the work area.
- Focused comparison confirms the same information hierarchy: progress, remaining/completed state, short guidance, and one primary route to the board preview.
- The implementation deliberately maps the approved last-item copy to a completion-state variant: `검수 16 / 16`, `모두 확인됨`, and `칠판 미리보기`.
- Selection actions use the approved plain-language grouping: `보정`, `구조`, and `처리`, with `빠른 자르기`, `영역 조정`, `2개로 나누기`, and `여러 문제로 나누기`.

## Interaction evidence

- `칠판 미리보기` switched the active header view to `칠판` and removed the review completion bar.
- Returning through the `검수` header restored the review view and completion bar.
- Selecting a problem exposed the new grouped actions and the contextual `그대로 확인 1` action.
- The per-page `수동 쪼개기` action opened the manual split editor, hid the completion bar while editing, and restored it after `취소`.
- Browser runtime errors and warnings remained empty after the interaction pass.
- The final-confirm auto-transition is regression-tested: the last successful confirmation switches directly to the board view and shows the completion toast.

## Defect ledger

- P0: none
- P1: none
- P2: none
- P3: none

## QA history

1. Flow audit: identified the dead end after final confirmation and the scattered manual-edit actions.
2. Direction pass: generated three alternatives and selected the option 1 + option 3 hybrid.
3. Implementation pass: added state-aware completion logic, the persistent bottom action bar, automatic board transition, and plain-language action groups.
4. Browser pass: verified completion, board navigation, selection actions, manual split entry/cancel, and clean runtime logs.
5. Automated verification: 834 tests passed with 92 subtests; `git diff --check` passed.
6. Bulk-completion follow-up: made completed bulk confirmation transition unconditionally to the board preview, including the local mock-session path, with a dedicated completion toast; 835 tests and 92 subtests passed.
7. Review-toolbar refinement: grouped the center header into `문항 검수 / 배율` and the sticky action bar into reusable `상태 보기 / 일괄 작업` components. Verified 57px/58px stable heights, no horizontal overflow at the default viewport, working filter pressed states, clean browser logs, and 837 passing tests with 92 subtests. Final screenshot: `.audit/review-flow-2026-07-22/09-review-toolbar-final.png`.

---

# Design QA — 영역 다시 잡기 하이브리드 편집기

final result: passed

## Visual truth

- Accepted source direction: `/Users/clmagi/.codex/generated_images/019f88d7-314c-7743-bc76-78f5f7a80d4b/exec-6c3f59be-3bf0-4d00-aeeb-965d41dc9688.png` (1487×1058).
- Selection basis: option 2's right-side confirmation panel combined with option 1's inline border editing, as explicitly approved by the user.
- Final implementation: `.audit/area-reset-2026-07-22/implementation-1440x1024.png` (1404×1024 capture).
- Full side-by-side comparison: `.audit/area-reset-2026-07-22/comparison.png`.

## Viewport and state

- Browser viewport override: 1440×1024; captured tab content: 1404×1024.
- URL: `http://127.0.0.1:8765/board.html?view=review`.
- Session state: latest 16-page saved session, first detected problem selected.
- Editor state: right edge moved left with the inline handle so the before/after previews and enabled apply state are visible.
- The source visual uses a multi-problem page while the saved QA session contains one full-page problem per source page. This is expected data variance; the frame editor and confirmation panel use the same responsive layout.

## Comparison evidence

- The implementation matches the selected structure: editable blue frame and eight handles on the source page, contextual right inspector, before/after previews, explicit mode choice, edge warning, impact summary, and persistent cancel/apply footer.
- The existing product chrome, typography, spacing, neutral surfaces, blue selection color, and compact desktop density are retained.
- `이 영역 다시 인식` is visible but disabled until number/order preservation is validated; the active path is the safe `자르기만 적용` mode.
- `2개로 나누기` and `두 문제로 나누기` are absent from the review flow.

## Interaction evidence

- Selecting one problem exposed `빠른 자르기` and the primary `영역 다시 잡기` action.
- The selection toolbar now wraps into two rows when the center column is narrow; element hit testing no longer places `그대로 확인` over `영역 다시 잡기`.
- Entering the editor exposed all eight frame handles and kept `적용` disabled while unchanged.
- Dragging the right handle enabled `원래 영역으로` and `적용`, and refreshed the adjusted preview.
- Pressing `Esc` closed the editor without a mutation, kept the problem selected, and reopening the editor restored the original frame with `적용` disabled.
- A fresh final browser tab reported no warnings or errors.

## Defect ledger

- P0: none
- P1: none
- P2: none
- P3: source and saved-session problem density differ; accepted as content variance rather than a component defect.

## QA history

1. Flow audit: confirmed that the existing eight-handle frame editor could support direct manipulation and that server-side crop preserves problem identity, number, and order.
2. Implementation pass: added the right inspector, explicit apply/cancel semantics, before/after previews, edge and impact guidance, selection restoration, and the safe crop-only mode.
3. Simplification pass: removed the review-stage two-way split state, controls, overlays, and styles; renamed the page-level entry to `새 영역 추가`.
4. Browser fix: found and removed a real click-target overlap in the narrow center column by wrapping selection actions and preventing flex-group shrink.
5. Automated verification: 799 tests passed; the 39 focused UI tests, 12 crop mutation tests, frontend package verification, bundle build, and `git diff --check` also passed.
