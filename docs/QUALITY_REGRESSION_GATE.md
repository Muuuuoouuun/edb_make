# 실문서 품질 회귀 게이트

이 게이트의 목적은 “테스트가 통과한다”가 아니라 사용자가 받는 결과의 완전성, 검수 부담, 게시 안전성, 처리 속도를 릴리스 전에 수치로 확인하는 것이다. 저작권이 있는 시험지나 학생 자료는 저장소에 넣지 않는다. 저장소의 `quality/synthetic-corpus.json`은 하네스 자체만 검증하는 합성 smoke 자료이며 실제 품질을 보증하지 않는다.

## 측정 지표

| 지표 | 사용자 영향 | 권장 초기 게이트 |
|---|---|---:|
| 누락·중복·추가 문항 | 수업 자료의 완전성 | 각각 0건 |
| 문항 recall / precision | 문항 보존과 오검출 | 각각 1.0 |
| 지문 범위 recall / precision | 공통 지문과 자식 문항 연결 | 각각 1.0 |
| ClassIn preflight issue | 게시 실패·잘못된 게시 예방 | 0건 |
| 수동 검수 필요율 | 교사 작업시간과 만족도 | 코퍼스 기준선을 세운 뒤 지속 하향 |
| 처리시간 p50 / p95 | 체감 속도와 긴 꼬리 지연 | 동일 장비 기준선 대비 p95 증가 제한 |
| 문항 구조 signature / artifact validity | 문항 swap, 잘못된 bbox/crop, 선택지 순서·본문 오염 | mismatch 0건 / invalid 0건 |

문항 수가 많은 파일 하나가 작은 파일의 실패를 가리지 않도록 `case_thresholds`와 `aggregate_thresholds`를 함께 사용한다. 속도 회귀는 장비 영향을 크게 받으므로 같은 CI runner나 고정 벤치마크 장비에서 비교한다.

## 비공개 코퍼스 구성

접근이 통제된 저장소 외부 디렉터리에 원문, private manifest, baseline을 둔다. Git 저장소에는 원문, manifest, crop, OCR 텍스트, 관측 sidecar를 넣지 않는다. 외부에서 준비할 것은 원문 30개 이상과 원문을 대조할 두 명의 독립 검수자뿐이다.

### 1. 원문에서 manifest 만들기

다음 명령은 지원 확장자를 검색하고 SHA-256을 스트리밍 계산하며 strict threshold와 개인정보 없는 `case-001` 형식 ID를 가진 manifest를 만든다. 파일명과 원문 경로는 보호된 manifest 안에만 남는다. production readiness는 선언한 format을 확장자와 PDF/OLE-HWP/HWPX-ZIP/image magic으로 다시 확인한다.

```bash
python3 scripts/create_quality_observation.py scaffold /secure/edb-quality/sources \
  --manifest /secure/edb-quality/corpus.json \
  --corpus-id academy-release-v1 \
  --recursive \
  --minimum-cases 30 \
  --required-format pdf --required-format hwp --required-format hwpx --required-format image \
  --required-subject korean --required-subject english --required-subject math \
  --required-tag single-column --required-tag multi-column \
  --required-tag cross-page-passage --required-tag low-resolution-scan
```

모든 원문이 같은 과목이면 `--subject korean`처럼 지정한다. 혼합 corpus는 아래 label 명령의 `--subject`로 case별 분류한다. `--tag`를 반복하면 레이아웃·스캔 특성을 기록할 수 있다. 실제 corpus 구성상 필요하지 않은 coverage 항목은 scaffold 명령에서 요구하지 않되, 릴리스 모집단과의 차이를 리뷰 기록에 남긴다.

### 2. 정답 라벨과 독립 승인

첫 번째 검수자는 원문과 현재 파이프라인의 case session/crop을 대조한다. 먼저 session을 privacy-safe observation으로 변환한다. 이때 raw crop과 실제 EDB에 들어가는 `boardRenderPath`가 session 경로에 존재해야 한다. sidecar에는 경로·이미지·OCR 본문 대신 문항별 source-page+bbox hash, raw crop SHA-256, user-visible render SHA-256, decoded RGB visual SHA-256, text+visual content fingerprint, 선택지 개수·순서, artifact size/validity만 남는다. 확장자나 파일 크기만 보지 않고 Pillow decode/verify, 양수 dimensions, 최소 비공백 픽셀을 확인하므로 비어 있거나 손상된 PNG는 승인할 수 없다.

```bash
python3 scripts/create_quality_observation.py /secure/label-runs/case-001/ui_session.json \
  --output /secure/edb-quality/label-observations/case-001.json \
  --processing-ms 1280 \
  --preflight-issue-count 0
```

그 다음 문항 번호와 공통 지문 범위를 입력하고, 사람이 확인한 structural observation을 결합한다. 범위 문법은 `1-20,22`, 지문이 없으면 `none`이다. 개인 이름 대신 내부 pseudonymous operator ID를 쓴다.

```bash
python3 scripts/create_quality_observation.py label /secure/edb-quality/corpus.json case-001 \
  --questions '1-20,22' \
  --passages '1-3,18-20' \
  --observation /secure/edb-quality/label-observations/case-001.json \
  --annotator-id qa-a \
  --subject korean \
  --tag cross-page-passage
```

두 번째 검수자는 원문과 입력값을 다시 대조한 뒤 승인한다. 같은 ID가 annotation과 approval을 겸할 수 없고, 승인 뒤 expected 값이 바뀌면 SHA-256 검증이 실패해 다시 label해야 한다.

```bash
python3 scripts/create_quality_observation.py approve /secure/edb-quality/corpus.json case-001 \
  --reviewer-id qa-b
```

의도적으로 문항이 없는 negative fixture만 label 시 `--questions none --allow-empty-document`를 사용한다. 그 외 빈 정답은 readiness 오류다.

### 3. 실행 전 readiness 확인

```bash
python3 scripts/create_quality_observation.py validate /secure/edb-quality/corpus.json \
  --corpus-root /secure/edb-quality \
  --minimum-cases 30 \
  --json-report /tmp/edb-quality-readiness.json
```

readiness는 다음 항목을 전부 fail-closed로 확인한다.

- 30개 이상의 고유 source SHA-256 (같은 원문의 alias 30개는 1개로 계산)
- 코드가 고정한 PDF/HWP/HWPX/image, korean/english/math, multi-column/low-resolution-scan/cross-page-passage coverage와 manifest 추가 coverage
- 모든 원문의 존재 및 SHA-256 일치
- 모든 case의 2인 ground-truth 승인과 expected fingerprint
- 모든 expected 문항의 승인된 bbox/crop/content/choice signature와 유효 artifact
- 모든 case/aggregate/regression rule의 존재
- manifest가 코드 소유 release policy보다 threshold를 완화하지 않았는지 여부

production policy는 문항·지문 recall/precision 1.0, 누락·중복·추가·preflight·case failure 0건, case/aggregate 수동 검수율 25%/20% 이하, p50/p95 및 case 처리시간 300초 이하이다. 기준선 대비 recall/precision 하락과 preflight 증가는 0, 수동 검수율 증가는 2%p, p95 증가는 10%까지만 허용한다. private manifest는 이 값보다 엄격하게 만들 수 있지만 느슨하게 만들 수 없다. 정책 변경은 manifest 편집이 아니라 코드 리뷰가 필요한 변경이다.

### 기존 session을 privacy-safe observation으로 변환

결과 파일에는 기존 UI session과 함께 `processingMs`를 넣거나, 아래처럼 개인정보 없는 `qualityObservation` sidecar만 둔다. 이 기능은 진단·라벨 보조용이며 production runner는 현재 checkout에서 sidecar를 새로 만든다.

```bash
python3 scripts/create_quality_observation.py /secure/session/ui_session.json \
  --output /secure/edb-quality/observations/case-001.json \
  --processing-ms 1280 \
  --preflight-issue-count 0
```

session에 처리시간과 preflight가 이미 있으면 두 override 옵션을 생략한다. override 값은 추정값이 아니라 같은 실행에서 측정한 값이어야 한다. sidecar에는 문항 번호·지문 범위·집계 카운트·처리시간만 기록되며 OCR 텍스트, 원문 경로, crop 경로는 복사하지 않는다. 기존 출력 파일을 교체할 때만 `--force`를 명시한다.

```json
{
  "qualityObservation": {
    "questionNumbers": [18, 19, 20, 21],
    "passageRanges": [{"start": 18, "end": 21}],
    "preflightIssueCount": 0,
    "manualReviewCount": 1,
    "reviewPopulation": 4,
    "processingMs": 1280
  }
}
```

기존 session을 직접 평가할 때는 `problems`, `classinPreflight` 또는 `classin_preflight`, 그리고 `processingMs`, `qualityMetrics.processingMs`, `timing_ms.total` 중 하나가 필요하다. 지문 범위는 문제별 `passageRange`와 session의 `passageGroups[].numberStart/numberEnd`를 모두 읽고 중복 제거한다. `passage_fragment` 레코드는 문항 수에서 제외한다. `reviewStatus`가 `check_needed`/`failed`이거나 명시적인 `manualReviewRequired`가 참이면 수동 검수 대상으로 센다.

`expected.question_numbers`와 `expected.passage_ranges`는 사람이 원문과 대조해 확정한 정답이어야 한다. 관측 결과에서 expected를 자동 복사하면 실행 경로만 점검할 수 있을 뿐 누락·중복·오검출 품질은 측정할 수 없다.

실문서는 최소 30~50개로 시작하고 PDF/HWP/HWPX/이미지, 국어·영어·수학, 단일·다단, 페이지 연결 지문, 저해상도 스캔을 태그별로 고르게 포함한다. 문서가 교체돼도 같은 샘플인지 확인하려면 private manifest에 `source.path`와 `source.sha256`을 함께 둔다. evaluator는 원문을 업로드하거나 내용을 report에 복사하지 않으며, SHA-256 검증을 위해 로컬 파일을 스트리밍으로 읽기만 한다.

## 실행과 종료 코드

릴리스 판정은 현재 checkout의 실제 파이프라인을 모든 원문에 다시 실행한 뒤 평가해야 한다. `--work-dir`은 저장소 밖의 비어 있는 새 디렉터리여야 한다. runner는 각 원문을 그 아래 isolated input으로 복제해 원문 옆 `.pipeline_cache`를 사용할 수 없게 하고, 실행 결과의 cache hit가 0인지 검증한다.

최초 1회는 clean checkout에서 baseline candidate를 만든다.

```bash
python3 scripts/run_quality_corpus.py /secure/edb-quality/corpus.json \
  --corpus-root /secure/edb-quality \
  --work-dir /secure/edb-quality/runs/first-candidate \
  --establish-baseline \
  --json-report /secure/edb-quality/baseline-candidate.json \
  --markdown-report /tmp/edb-quality-baseline-candidate.md
```

candidate가 PASS이고 case별 결과가 타당한지 사람이 확인한 다음 tamper-evident approval을 별도 파일에 붙인다.

```bash
python3 scripts/create_quality_observation.py approve-baseline \
  /secure/edb-quality/baseline-candidate.json \
  --output /secure/edb-quality/baseline-approved.json \
  --reviewer-id release-qa
```

이후 모든 production 실행은 승인된 baseline이 필수다.

```bash
python3 scripts/run_quality_corpus.py /secure/edb-quality/corpus.json \
  --corpus-root /secure/edb-quality \
  --work-dir /secure/edb-quality/runs/run-current-empty \
  --baseline /secure/edb-quality/baseline-approved.json \
  --json-report /tmp/edb-quality-report.json \
  --markdown-report /tmp/edb-quality-report.md
```

production report의 provenance는 Git commit, pipeline source fingerprint, OS/Python/OCR 모드, `requirements-release.lock`·`requirements-release-bootstrap.lock`·`requirements-ci.lock`을 포함한 requirements 원문 SHA-256, 설치된 Pillow/PyMuPDF/numpy/OpenCV/OCR/HWP 패키지 버전, Tesseract/LibreOffice/hwp5/rhwp/Node 버전, isolated-cache policy를 포함한다. 잠금 파일이 한 바이트라도 바뀌면 environment fingerprint도 바뀌므로 기존 baseline을 재사용하지 않고 같은 보호 runner에서 candidate를 다시 생성·검토·승인한다. baseline과 현재 실행의 environment fingerprint가 다르면 속도 비교를 거부한다. runner는 성공·일반 예외 시 isolated 원문 복제본과 OCR/render 출력을 자동 삭제한다. `--retain-private-artifacts`와 `--allow-dirty-checkout`은 로컬 진단 전용이며 그 report는 baseline으로 승인할 수 없다. CI 취소·강제 종료까지 보장하려면 workflow에서도 `if: always()` cleanup으로 전용 RUNNER_TEMP 디렉터리 전체를 삭제해야 한다.

공개 릴리스 workflow의 private gate는 self-hosted runner의 전역 Python을 사용하지 않는다. `RUNNER_TEMP` 아래 새 venv에 bootstrap/release hash lock만 설치하고 미등록 distribution을 거부하며, 선택적 Paddle/Tesseract Python backend 혼입도 별도로 차단한다. OCR은 `gemini-3.5-flash`와 thinking level `low`로 고정되고 보호된 `EDB_QUALITY_GEMINI_API_KEY` GitHub secret이 없으면 실행 전에 실패한다. venv와 모든 비공개 파생물·report는 `if: always()` 단계에서 안전한 경로인지 확인한 뒤 함께 삭제한다.

이미 생성한 관측값을 진단하거나 evaluator 자체를 점검할 때만 아래의 읽기 전용 평가 명령을 사용한다. 이 명령만으로는 현재 코드가 원문을 올바르게 처리했다는 릴리스 증거가 되지 않는다.

```bash
python3 scripts/evaluate_quality_corpus.py /secure/edb-quality/corpus.json \
  --corpus-root /secure/edb-quality \
  --json-report /tmp/edb-quality-report.json \
  --markdown-report /tmp/edb-quality-report.md
```

환경변수 `EDB_QUALITY_CORPUS_ROOT`로도 외부 루트를 주입할 수 있다.

JSON report는 기본적으로 manifest, corpus root, result 절대경로를 포함하지 않는다. 제한된 로컬 진단에서 경로가 꼭 필요할 때만 `--include-paths`를 사용하고 해당 report를 CI artifact나 저장소에 올리지 않는다.

| 종료 코드 | 의미 |
|---:|---|
| 0 | 모든 threshold와 baseline 회귀 허용치 통과 |
| 1 | 입력은 유효하지만 품질 또는 속도 게이트 실패 |
| 2 | manifest/result 누락, JSON 오류, 필수 측정값 누락 |

승인 baseline은 PASS 상태, 동일 corpus fingerprint, fresh pipeline provenance, clean checkout, cache hit 0, 동일 dependency environment를 모두 만족해야 한다. 승인 뒤 report가 한 글자라도 바뀌면 `report_sha256` 검증이 실패한다.

## 릴리스 운영 권장안

- PR에서는 합성 corpus, 전체 pytest, 프런트 패키지·bundle digest를 읽기 전용으로 검증한다.
- 비공개 실문서 corpus는 접근 권한이 있는 별도 runner에서 같은 CLI로 실행하고 report만 제한적으로 보관한다.
- 실패 report의 원문이나 OCR 텍스트를 공개 CI artifact에 올리지 않는다. case ID와 집계 지표만 공유한다.
- threshold 변경은 실패를 숨기는 수단이 아니라 코퍼스 변경, 장비 변경, 사용자 검수 결과 같은 근거와 함께 리뷰한다.
- private `--work-dir`에는 원문 복제본과 OCR 중간 산출물이 있으므로 접근을 제한하고 보존 정책에 따라 삭제한다.
