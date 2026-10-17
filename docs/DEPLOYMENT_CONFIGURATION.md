# 배포 환경 등록 및 확인

현재 배포 workflow는 repository Actions secrets/variables를 사용한다.
GitHub Environment에만 등록하면 environment를 지정하지 않은 현재 작업에는 전달되지 않는다.

## 비밀값

저장소 Settings → Secrets and variables → Actions → Secrets에서 등록한다.
값은 채팅, 소스, 문서, 커맨드라인 인자로 보내지 않는다.

- `EDB_UPDATE_PIN`: 운영자가 정한 ASCII 숫자 4~12자리. 암호 관리 도구에 보관한다.
  패키징 시 PBKDF2 검증값으로 변환된다.
- `EDB_QUALITY_GEMINI_API_KEY`: 실제 문서 품질 검사에 사용할 Gemini API 키.
  클라우드 프록시의 인증 대체값을 복사하지 않는다.

등록 후 Actions → Deployment Readiness → Run workflow로 설정 존재 여부를 확인한다.
이 작업은 PIN 형식과 키 존재 여부만 확인하며 실제 API 인증 성공을 보장하지 않는다.
비밀값이 없거나 PIN 형식이 틀리면 실패한다. 원문과 실제 경로는 보고하지 않는다.

## 비공개 문서 검사

보호된 실행기에 `self-hosted`, `edb-quality-corpus` 레이블이 필요하다.
원문 접근 권한이 있는 관리자가 해당 실행기에 자료와 승인된 baseline을 배치한다.
공개 저장소에서 임의 PR 코드가 이 실행기에 접근하지 않도록 접근을 제한한다.

Actions → Variables에 다음 경로를 등록한다.

- `EDB_QUALITY_CORPUS_MANIFEST`: 실문서 corpus manifest의 절대경로
- `EDB_QUALITY_CORPUS_ROOT`: 원문 자료 디렉터리의 절대경로
- `EDB_QUALITY_CORPUS_BASELINE`: 검토·승인된 baseline의 절대경로

manifest/root는 실행기 환경변수로도 제공할 수 있다. 저장소 변수 미등록만으로
실행기에 자료가 없다고 단정하지 않는다. baseline 생성과 사람의 검토 절차는
[품질 검사 문서](QUALITY_REGRESSION_GATE.md)를 따른다. 합성 자료 결과로 대체하지 않는다.
실제 품질 검사는 OCR API 사용 비용이 발생하며 원문이 설정된 OCR 제공자에게 전달된다.

## 이후 순서

1. Deployment Readiness를 다시 실행한다.
2. 전용 실행기 online 상태, 원문 접근 및 승인 baseline을 확인한다.
3. 서명 인증서 및 라이선스 근거까지 준비되면 Build Installers의 production release를 실행한다.
4. 실문서 품질 검사와 Windows/macOS 검증이 모두 통과한 결과로 배포 자료를 다시 만든다.
5. 검증된 draft를 공개하고 업데이트 feed 및 다운로드 주소를 다시 확인한다.

설정 검사 성공만으로 production 릴리스 조건을 충족한 것은 아니다.
현재 unsigned/ad-hoc 내부 검증용 설치 파일을 정식 서명본으로 간주하지 않는다.
