# 기본 Home UI 개발 기준

## 통합 기준

- 대상: Windows 개발 브랜치 `up2_win`.
- 가져온 브랜치: `origin/feat/home-ui-develop`, `7d1d6c6`.
- 기존 Windows 기준점: `60e488f`.
- 기본 Home UI와 이어지는 자료 준비 → 검수 → 칠판·내보내기 흐름을 개발 기준으로 사용한다.
- 프리미엄 상품, 결제, 업셀, 사용량 제한 변경은 이번 개발 범위에 포함하지 않는다. 함께 들어온 웹 체험 코드의 기존 정책은 변경하지 않는다.

## 작업할 화면과 코드

로컬 앱의 `/`는 `ui_prototype/index.html`을 통해 `/board.html`로 이동한다.
홈과 편집 화면은 `ui_prototype/app.jsx`, 디자인 토큰과 반응형 스타일은 `ui_prototype/board.css`에 있다.
`public/`의 웹 체험 화면은 별도 진입점이며 로컬 편집기의 Home UI와 혼동하지 않는다.

생성된 `ui_prototype/app.bundle.js`를 직접 편집하지 않는다. 소스를 수정한 뒤 아래 명령으로 번들과 캐시 식별자를 갱신한다.

```powershell
node scripts/build_frontend_bundle.mjs
node scripts/build_frontend_bundle.mjs --check
.venv\Scripts\python.exe scripts/verify_frontend_package.py
```

## 유지할 통합 동작

- 새 홈의 파일 추가, 샘플 진입, 최근 작업 복원, 업로드 방식 선택을 유지한다.
- 기존 Windows의 원문 문항 번호 편집, 본문 드래그, Alt+드래그 순서 이동, Esc 취소를 유지한다.
- 세로 이동 공간은 명시적인 추가 칸으로 저장하고, `1.2 맞춤`과 `빈틈 없이` 배치 양쪽에서 반영한다.
- 미리보기, 제작 전 검사(`publish_guard.js`), 서버의 제작 계획, 실제 EDB 출력에 같은 배치 값을 전달한다.
- Windows 경로 제한 대응, 복구·보고서 기능, 설정 파일 원자적 저장을 유지한다.
- 프런트엔드 소스는 `.gitattributes`의 LF 규칙을 지킨다. 운영체제별 줄바꿈 차이로 번들 해시가 달라지면 안 된다.

## 변경 후 검증

```powershell
.venv\Scripts\python.exe -m pytest -q
```

홈은 데스크톱과 좁은 화면에서 확인하고, 샘플 진입 후 배치 방식·세로 이동 공간·위치 조절을 실제로 조작한다.
실제 AI 처리나 사용자 자료를 이용한 검증은 별도로 범위를 정하며, 테스트용 작업 데이터는 사용자 런타임과 분리한다.
