# EDB Maker 0.2.14 배포 상태

2026-11-03 현재 KMA 수정본의 **수동 설치용 초안**을 준비했다. 공개 자동 업데이트 채널은 게시하지 않았다.

- 소스: master `4b3a2ff5db0155feab7f14217d888a0783913856`, PR #28.
- Windows x64, Apple Silicon Mac, Intel Mac 설치파일을 각각 검증했다.
- 동일 버전·커밋, 전송 ZIP 해시, 설치파일 해시, 패키지 메타데이터가 일치한다.
- 실제 설치, PDF/EDB 내보내기, 임시 PIN/로컬 시험 피드의 업데이트, 재실행, 설정·세션 보존이 통과했다.
- Windows는 unsigned, macOS는 ad-hoc 서명이며 공증되지 않았다. 정식 배포 서명·공증을 검증한 산출물로 간주하지 않는다.

## 공개 자동 업데이트가 아직 활성화되지 않은 이유

[Deployment Readiness](https://github.com/Muuuuoouuun/edb_make/actions/runs/41551210732)에서 `EDB_UPDATE_PIN`과 `EDB_QUALITY_GEMINI_API_KEY`가 미등록으로 확인됐다. 실제 이번 설치파일도 PIN·업데이트 피드·다운로드 주소가 미설정인 수동 설치용이다.

공개 빌드에는 기존 저장소의 실문서 품질검사, 서명·공증, 배포 라이선스 확인 조건도 적용된다. 해당 조건을 내부 시험 모드로 대체하여 공개 배포하지 않았다. 설정 등록과 공개 배포 검증 후 업데이트 설정을 포함한 설치본 및 피드를 다시 만들어야 한다.

검증된 기존 0.2.4에는 공개 피드 주소는 있지만 PIN 검증값이 없다. 이 설치본에 새 PIN만 안내해 자동 설치를 활성화할 수는 없고, 설정이 포함된 설치본으로 한 번 수동 교체해야 한다.

## 근거

- [Windows·Apple Silicon 실행](https://github.com/Muuuuoouuun/edb_make/actions/runs/41551396654)
- [Intel Mac 실행](https://github.com/Muuuuoouuun/edb_make/actions/runs/41551402432)
- [배포 초안](https://github.com/Muuuuoouuun/edb_make/releases/tag/untagged-858ac239be2ef2afcb77)
- `artifact-verification.json`, `SHA256SUMS.txt`, `release-evidence.zip`
