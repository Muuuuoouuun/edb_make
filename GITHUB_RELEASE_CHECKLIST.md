# GitHub 업데이트 배포 연결

갱신일: 2026-11-03. 업데이트 채널은 공개 저장소 `Muuuuoouuun/edb_make`의
GitHub Releases입니다. 설치된 앱은 로그인 없이 피드를 읽습니다. 그래서 게시된
설치 파일은 누구나 받을 수 있습니다. GitHub Actions 산출물과 Draft 릴리스는
앱이 읽을 수 없으므로 업데이트 주소로 쓰지 않습니다.

2026-11-03 확인: 게시된 릴리스는 없습니다(모두 Draft). 등록된 repository
secret도 없습니다. 버그 신고는 별도 서비스
`https://reports.classin.cloud/v1/edb-reports`를 사용합니다.

## 주소는 워크플로가 만듭니다

`Build Installers`에서 `update_channel`(기본 켜짐)을 켜면 워크플로가 저장소
이름과 `version`으로 아래 주소를 만들어 앱에 넣습니다. 주소를 직접 입력하는
칸은 없습니다. `version=0.2.15`일 때의 예입니다.

| 용도 | 주소 |
| --- | --- |
| 피드 | `https://github.com/Muuuuoouuun/edb_make/releases/latest/download/update.json` |
| 릴리스 노트 | `https://github.com/Muuuuoouuun/edb_make/releases/tag/v0.2.15` |
| manifest | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.15/manifest.json` |
| Apple Silicon Mac | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.15/ClassInEDBMVP-macOS-arm64.dmg` |
| Intel Mac | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.15/ClassInEDBMVP-macOS-x64.dmg` |
| Windows | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.15/ClassInEDBMVP-Setup.exe` |

피드 하나에 세 설치 파일이 모두 들어갑니다. Intel Mac 앱은 피드의 `macos-x64`
항목을 읽고, Apple Silicon Mac 앱은 `macos` 항목을 읽습니다. 그래서 피드는
`macos_arch=both`(기본)로 두 Mac과 Windows를 한 번에 빌드할 때만 만들어집니다.

`latest` 주소는 Latest로 지정된 안정 버전 릴리스만 가리킵니다. Draft와
Prerelease는 이 주소에 나오지 않습니다. 설치 파일과 manifest는 버전별 주소라서,
다음 릴리스가 올라와도 내려받는 도중에 파일과 해시가 바뀌지 않습니다.

## 순서

1. 저장소 **Settings → Secrets and variables → Actions**에 `EDB_UPDATE_PIN`을
   등록합니다. PIN은 4~12자리 숫자이고, 저장소나 문서에 평문으로 적지 않습니다.
   이 PIN이 없는 설치본은 자동 설치를 할 수 없고 `다운로드 페이지 열기`만 됩니다.
   PIN은 수업 중 실수로 업데이트하는 일을 막는 잠금입니다. 보안 장치는 아닙니다.
2. `Build Installers`를 master에서 실행합니다. `version`은 기본값이 없습니다.
   이미 설치된 어떤 버전보다 높은 번호를 넣습니다. `update_channel=true`,
   `macos_arch=both`로 둡니다. 내부 배포는 `production_release=false`로 합니다.
   공개 서명·공증·라이선스 게이트는 [PACKAGING_MVP.md](PACKAGING_MVP.md)를 따릅니다.
3. 빌드 커밋을 대상으로 Draft Release `v<version>`을 만들고 다음 명령을 실행합니다.

   ```bash
   python scripts/stage_draft_release.py --repository Muuuuoouuun/edb_make --run-id <실행 ID> --version <version>
   ```

   이 스크립트는 같은 실행의 설치 파일 세 개(`ClassInEDBMVP-macOS-arm64.dmg`,
   `ClassInEDBMVP-macOS-x64.dmg`, `ClassInEDBMVP-Setup.exe`)와 `update.json`,
   `manifest.json`, `checksums.txt`가 버전·커밋·크기·SHA-256과 맞는지 확인합니다.
   확인이 끝나면 Draft에 첨부만 하고 게시하지 않습니다.
4. 게시가 승인되면 Draft를 게시하고 **Latest**로 지정합니다. 피드, manifest,
   설치 파일 주소가 로그인 없이 HTTP 200으로 열리는지 확인합니다.
5. Windows, Apple Silicon Mac, Intel Mac 설치본에서 각각 확인합니다. 업데이트
   확인, PIN 오류와 정상 입력, 교체 후 재실행, 세션·API 키 보존을 봅니다.
   `/api/app/update`의 `configured`와 `automaticUpdateReady` 값도 확인합니다.

## 이미 설치된 0.2.14 이하

이 버전들은 업데이트 주소가 비어 있어 새 피드를 찾지 못합니다. PC마다 업데이트
채널이 들어간 첫 설치본을 **한 번 직접 설치**해야 합니다. 그다음 버전부터는 앱이
알아서 찾습니다. API 키와 세션이 있는 runtime 폴더는 지우지 않습니다.

Windows 설치 파일은 서명되지 않았고, Mac 설치 파일은 ad-hoc 서명이며 공증되지
않았습니다. 그래서 첫 직접 설치 때 Windows SmartScreen이나 macOS Gatekeeper
경고가 뜰 수 있습니다.

## 버그 신고 운영

신고 창의 전송 성공은 접수 번호가 반환됐을 때만 표시합니다. `/health` 정상은
서비스 준비 상태를 뜻하며 실제 신고 저장·회신 완료를 뜻하지 않습니다.
운영 신고 조회·해결 상태·회신 절차는 [reports_worker/README.md](reports_worker/README.md)를
따릅니다. 연락처가 없는 신고도 허용하며 회신 동의가 있는 경우에만 연락처를 보냅니다.
