# GitHub 업데이트 배포 연결

확인일: 2026-10-16. `Muuuuoouuun/edb_make`는 공개 저장소이고 GitHub Releases는
아직 없습니다. GitHub Actions의 다운로드 가능한 빌드 산출물은 공개 릴리스가
아니므로 설치 앱의 업데이트 주소로 사용하지 않습니다.

현재 `app_update_config.json`의 업데이트 주소·다운로드 주소·PIN 검증값은
비어 있습니다. 버그 신고는 별도 서비스
`https://reports.classin.cloud/v1/edb-reports`를 사용합니다. 해당 서비스의
읽기 전용 `/health` 검사에서 DB·요청 제한·스키마 v3가 준비된 상태를 확인했습니다.
실제 운영 신고는 이 점검에서 보내지 않았습니다.

## 다음 공개 릴리스의 주소

다음 버전을 `0.2.4`, 태그를 `v0.2.4`로 정할 경우의 예입니다.
아래 주소는 **아직 게시되지 않았으며 앱의 활성 설정으로 넣지 않았습니다.**

| Build Installers 입력 | 값 |
| --- | --- |
| `version` | `0.2.4` |
| `update_feed_url` | `https://github.com/Muuuuoouuun/edb_make/releases/latest/download/update.json` |
| `release_notes_url` | `https://github.com/Muuuuoouuun/edb_make/releases/tag/v0.2.4` |
| `manifest_url` | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.4/manifest.json` |
| `macos_download_url` | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.4/ClassInEDBMVP-macOS.dmg` |
| `windows_download_url` | `https://github.com/Muuuuoouuun/edb_make/releases/download/v0.2.4/ClassInEDBMVP-Setup.exe` |

`latest` 주소는 안정 버전 릴리스용입니다. Draft·Prerelease는 이 주소로
공개되지 않습니다. 설치 파일과 manifest는 위와 같이 **버전별 주소**를 사용해
다음 릴리스가 올라와도 다운로드 도중 파일과 해시가 서로 바뀌지 않게 합니다.

## 필요한 순서

1. 최신 UI 수정이 포함된 커밋과 새 버전을 확정합니다. 기존 `0.2.3` 산출물에
   `0.2.4` 메타데이터를 붙여 배포하지 않습니다.
2. 배포 권한·동봉 라이선스를 검토하고 기존 production quality gate를 통과합니다.
   Windows 코드 서명, macOS Developer ID·공증·staple 설정을 준비합니다.
   자세한 절차는 [PACKAGING_MVP.md](PACKAGING_MVP.md)를 따릅니다.
3. 자동 설치를 사용할 경우 보호된 GitHub Actions secret `EDB_UPDATE_PIN`을
   설정합니다. PIN은 4~12자리 숫자이며 저장소나 문서에 평문으로 쓰지 않습니다.
   현재 세션은 repository secret 목록을 읽을 권한이 없어 등록 여부는 확인하지
   못했습니다(HTTP 403). PIN 없이도 설정된 다운로드 주소로 수동 설치는 가능합니다.
4. 위 URL 입력으로 `Build Installers`를 실행합니다. `production_release=true`와
   검토 완료된 라이선스 승인을 사용합니다. `macos_arch=arm64`는 Apple Silicon용입니다.
   Intel용은 별도 네이티브 빌드와 해당 아키텍처의 피드 항목이 필요합니다.
5. 성공한 **같은 커밋·버전의 같은 실행**에서 Windows·macOS 설치 파일과
   `update-feed` 산출물의 `update.json`, `manifest.json`, `checksums.txt`를 받습니다.
   워크플로는 URL 두 개가 입력됐을 때 피드를 생성하지만, 현재 GitHub Release를
   만들거나 게시하는 단계는 없습니다.
6. 해당 커밋을 대상으로 GitHub에서 **Draft Release** `v0.2.4`를 만들고 위 다섯
   파일을 첨부합니다. 설치 파일 이름이 표의 URL과 정확히 일치하는지, manifest와
   피드가 빌드 증거의 버전·커밋·크기·SHA-256과 일치하는지 확인합니다.
7. 공개 배포가 승인되면 안정 버전으로 게시하고 latest release로 지정합니다.
   피드·manifest·설치 파일 URL이 로그인 없이 HTTP 200으로 열리는지 확인합니다.
8. 실제 Windows·Mac 설치본에서 업데이트 확인, PIN 오류·정상 입력, 교체 후 재실행,
   세션·API 키 보존을 확인합니다. `/api/app/update`에서 `configured`와
   `automaticUpdateReady` 상태도 점검합니다.

## 이미 설치된 0.2.3

주소가 비어 있는 기존 앱은 새 피드를 자동으로 발견할 수 없습니다. 주소가 포함된
첫 설치본을 한 번 수동 설치하거나, 관리자가 앱의 runtime 폴더에
`app_update_config.json` 로컬 override를 배포해야 합니다. PIN verifier도 필요한
경우 안전한 배포 경로로 설정합니다. API 키·세션이 있는 runtime 폴더를 삭제하지 않습니다.

## 버그 신고 운영

신고 창의 전송 성공은 접수 번호가 반환됐을 때만 표시합니다. `/health` 정상은
서비스 준비 상태를 뜻하며 실제 신고 저장·회신 완료를 뜻하지 않습니다.
운영 신고 조회·해결 상태·회신 절차는 [reports_worker/README.md](reports_worker/README.md)를
따릅니다. 연락처가 없는 신고도 허용하며 회신 동의가 있는 경우에만 연락처를 보냅니다.
