# EDB Maker 0.2.14 설치 안내

KMA 문항 분할 수정이 포함된 내부 설치 검증용 빌드입니다. 기존 앱을 종료한 뒤 PC에 맞는 파일을 설치하세요.

| 사용 환경 | 설치파일 |
| --- | --- |
| Windows 64비트 | `ClassInEDBMVP-Setup.exe` |
| Apple Silicon Mac (M1/M2/M3/M4 등) | `ClassInEDBMVP-macOS-arm64.dmg` |
| Intel Mac | `ClassInEDBMVP-macOS-x64.dmg` |

Windows는 Setup.exe를 실행하여 기존 앱을 교체합니다. Mac은 DMG를 열어 앱을 Applications에 복사해 기존 앱을 교체합니다. 설치파일은 개발용 단일 exe나 업데이트 시험용 bootstrap과 다릅니다.

기존 앱의 사용자 데이터, API 키, 세션, 업로드 폴더를 삭제하지 마세요. 설치 검증 과정에서는 업데이트 후 사용자 설정과 세션 보존도 확인합니다.

검증한 기존 0.2.4 설치본에는 업데이트 주소는 있지만 PIN 검증값이 없습니다. 이 설치본에서는 새 PIN만 입력해서 자동 설치를 시작할 수 없습니다. 한 번 수동 설치가 필요합니다.

현재 이 초안의 목적은 수동 설치입니다. 앱 자동 업데이트를 활성화하려면 GitHub Actions Secrets의 `EDB_UPDATE_PIN`과 운영용 품질검사 설정을 준비하고, 해당 설정을 포함한 설치파일 및 공개 업데이트 채널을 별도로 검증해야 합니다. 초안 릴리스는 공개 업데이트 피드가 아닙니다.

소스 커밋: `4b3a2ff5db0155feab7f14217d888a0783913856`

설치파일의 SHA-256은 `SHA256SUMS.txt`, 소스·버전·네이티브 검증 근거는 `artifact-verification.json` 및 `release-evidence.zip`에서 확인할 수 있습니다.
