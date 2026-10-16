# AI 기능 평가표

- 평가 버전: `ai-capability-v1`
- 가중 평균: **9.36/10** (목표 8.0)
- 단순 평균: **9.42/10**
- 최저 영역: **경제성·비용 8.80/10** (목표 6.8)
- 판정: **통과**
- 실측 OCR 근거: `C:\Projects\Class_project\edb_make\.audit\ai-ocr-benchmark-current.json`

## 영역별 점수

| 영역 | 비중 | 점수 |
|---|---:|---:|
| 인식 | 30% | 9.30 |
| 결과 품질 | 25% | 9.00 |
| 효율·신뢰성 | 15% | 10.00 |
| 경제성·비용 | 15% | 8.80 |
| 사용자 통제 | 15% | 10.00 |

## 인식

| 지표 | 배점 | 획득 | 근거 |
|---|---:|---:|---|
| 정확 전사 프롬프트와 구조화 응답 스키마 | 1.4 | 1.40 | ocr_backend.py: structured Gemini OCR contract |
| 로컬 OCR이 클라우드로 우회하지 않음 | 1.2 | 1.20 | test_ai_global_settings.py: hard offline route regression |
| 경제형 OCR의 고신뢰도 표기 변형을 품질 모델로 재검증 | 1.6 | 1.60 | build_structured_page_json.py + test_recognition_speed_quality.py |
| 소스 해시·모델 동작 버전을 포함한 OCR 캐시 식별 | 1.0 | 1.00 | build_structured_page_json.py: stable OCR cache identity |
| 균형형 OCR 실측 최저 유사도 ≥ 0.98 | 2.2 | 2.20 | C:\Projects\Class_project\edb_make\.audit\ai-ocr-benchmark-current.json: min_similarity=1.0 |
| 경제형+선택 재검증 경로 실측 최저 유사도 ≥ 0.98 | 1.2 | 1.20 | C:\Projects\Class_project\edb_make\.audit\ai-ocr-benchmark-current.json: routed_min_similarity=1.0, escalations=1 |
| 균형형·경제형 실측 API 오류 0건 | 0.7 | 0.70 | C:\Projects\Class_project\edb_make\.audit\ai-ocr-benchmark-current.json: calls=8, errors=0 |
| 실제 문서 OCR 코퍼스가 벤치마크에 포함됨 | 0.7 | 0.00 | C:\Projects\Class_project\edb_make\.audit\ai-ocr-benchmark-current.json: real_source_count=0 |

## 결과 품질

| 지표 | 배점 | 획득 | 근거 |
|---|---:|---:|---|
| 자동 이미지 개선은 비생성 보존 모드 | 1.4 | 1.40 | app_server.py: auto -> deterministic preserve |
| 반복 개선도 최초 원본에서 재시작 | 1.0 | 1.00 | app_server.py: original image provenance |
| 생성 결과의 누락·구조 변화 콘텐츠 게이트 | 1.3 | 1.30 | image_reconstruction_backend.py: content-preservation analysis |
| 게이트 실패 시 1회 재시도 후 결정론적 폴백 | 1.5 | 1.50 | app_server.py: bounded recovery path |
| 생성형 결과는 의미 동일성 미검증 상태를 사용자 검토로 노출 | 1.2 | 1.20 | app_server.py: semantic review guardrail |
| 수학·과학 수식 누락 전용 위험 플래그 | 0.9 | 0.90 | app_server.py: subject-aware formula-loss review |
| 문자·수식 불변 exact-copy 생성 프롬프트 | 0.9 | 0.90 | image_reconstruction_backend.py: exact-copy prompt |
| 누락·문자 구조·폴백·의미 검토 회귀 테스트 | 0.8 | 0.80 | test_openai_image_reconstruction.py |
| 원본/결과 OCR 의미 비교 자동 판정 | 1.0 | 0.00 | not implemented; current code correctly marks it unverified |

## 효율·신뢰성

| 지표 | 배점 | 획득 | 근거 |
|---|---:|---:|---|
| 신뢰 가능한 PDF 텍스트 블록 OCR 생략 | 1.3 | 1.30 | build_structured_page_json.py |
| 1차·재검증·페이지 보정 캐시 | 1.3 | 1.30 | pipeline cache at OCR and repair stages |
| 페이지/블록 호출 동시성 상한 | 1.2 | 1.20 | build_structured_page_json.py |
| 캐시 적중 시 AI 백엔드 지연 생성 | 1.0 | 1.00 | build_structured_page_json.py |
| 연속 provider 실패 회로 차단 | 1.2 | 1.20 | ocr_backend.py |
| 로컬 OCR 미설치 탐지 결과 단기 캐시 | 1.0 | 1.00 | ocr_backend.py |
| 큰 OCR 이미지는 JPEG로 전송량 절감 | 1.0 | 1.00 | ocr_backend.py |
| 단계별 지연·캐시·호출량 메타데이터 | 1.0 | 1.00 | build_structured_page_json.py |
| 속도 경로·캐시·동시성 회귀 테스트 | 1.0 | 1.00 | test_recognition_speed_quality.py |

## 경제성·비용

| 지표 | 배점 | 획득 | 근거 |
|---|---:|---:|---|
| 모델별 단가표 버전 고정 | 1.2 | 1.20 | ai_usage.py |
| 캐시 입력·추론 토큰을 구분한 비용 계산 | 0.8 | 0.80 | ai_usage.py |
| 모델·단계별 USD/KRW 비용 집계 | 1.2 | 1.20 | ai_usage.py |
| 사용자 화면에 실행 비용 표시 | 0.8 | 0.80 | ui_prototype/app.jsx |
| 저비용 OCR 프로필과 선택적 품질 승격 | 1.0 | 1.00 | ocr_backend.py + build_structured_page_json.py |
| 자동 이미지 개선의 생성 비용 0원 경로 | 1.2 | 1.20 | app_server.py |
| 생성 이미지 해상도 비용 상한 | 0.8 | 0.80 | app_server.py |
| 전체 AI 비용을 즉시 차단하는 전역 OFF | 1.0 | 1.00 | app_server.py |
| 가격 미등록 요청을 별도 집계 | 0.8 | 0.80 | ai_usage.py |
| 실행 전 사용자 지정 비용 예산 상한 | 1.2 | 0.00 | not implemented; costs are estimated after usage |

## 사용자 통제

| 지표 | 배점 | 획득 | 근거 |
|---|---:|---:|---|
| AI ON/OFF 설정 영속 저장 | 1.5 | 1.50 | user_settings.py |
| 서버가 재시도·생성·인식에서 OFF 강제 | 2.0 | 2.00 | app_server.py |
| 설정 화면 ON/OFF 버튼과 상태 표시 | 1.2 | 1.20 | ui_prototype/app.jsx |
| OFF 상태에서 AI 작업 버튼 비활성화 | 1.0 | 1.00 | ui_prototype/app.jsx |
| OFF 전환 시 저장 API 키 유지 | 1.0 | 1.00 | test_ai_global_settings.py |
| 설정 응답에 API 키 원문 미노출 | 1.0 | 1.00 | test_ai_global_settings.py |
| 생성형 이미지 개선은 명시적 선택만 허용 | 1.0 | 1.00 | app_server.py |
| 세션 응답에 실제 적용 AI 상태 기록 | 0.5 | 0.50 | app_server.py |
| 전역 OFF·로컬 강제·키 보존 회귀 테스트 | 0.8 | 0.80 | test_ai_global_settings.py |

## 우선 개발 항목

1. `cost.hard_budget` (+1.20점): 세션별 예상 비용 상한과 초과 전 확인/중단 정책을 추가한다.
2. `quality.automatic_semantic_diff` (+1.00점): 원본과 생성 결과를 독립 OCR로 비교하되 오판 시 원본을 유지하는 게이트를 추가한다.
3. `rec.real_corpus` (+0.70점): 개인정보를 제거한 실제 시험지 표본을 정답 전사와 함께 추가한다.

## 해석 가드레일

- 정적 코드·테스트 존재는 실데이터 정확도를 증명하지 않는다.
- 인식 정확도 점수에는 제공된 live OCR benchmark만 사용한다.
- 실문서 source_count=0이면 real-corpus 항목은 반드시 0점이다.
- 생성 이미지의 자동 의미 비교가 없으면 해당 항목은 반드시 0점이다.
- 비용은 provider 공개 단가 기반 추정치이며 청구액과 다를 수 있다.
