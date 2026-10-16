# Upscayl 이미지 업스케일링 활용성 검토

- 검토 브랜치: `Up1`
- 검토일: 2026-07-14
- 검토 대상: [upscayl/upscayl](https://github.com/upscayl/upscayl)
- 확인한 upstream 커밋: `a00d55fee90e0f9435d5eaa86e76700df8199af8` (2026-03-27)

## 결론

**조건부 도입 가능(Conditional Go)** 이다.

Upscayl 전체 Electron 앱을 포함하지 않고, `upscayl-bin` CLI와 모델을 별도 로컬 엔진으로 호출하면 현재 EDB의 S3 이미지 향상 흐름에 연결할 수 있다. 다만 다음 이유로 초기 인식/OCR의 기본 전처리로 강제하면 안 된다.

1. Vulkan 호환 GPU가 필요해 모든 사용자 PC에서 작동한다고 보장할 수 없다.
2. AI 업스케일은 원본에 없는 획과 디테일을 추정하므로 작은 한글, 수식, 보기 번호를 바꿀 위험이 있다.
3. 앱과 CLI는 AGPL-3.0이고, 바이너리와 기본 모델을 함께 배포하면 라이선스 고지·소스 제공 범위를 법무 관점에서 확정해야 한다.
4. macOS 기준 CLI 약 27 MB, 기본 모델 전체 약 171 MB가 추가된다. 세 플랫폼 패키지를 모두 포함하면 설치 파일이 더 커진다.

따라서 1차 적용 범위는 **저해상도로 판정된 문항 이미지 또는 그림/도표에 Lite 모델을 선택 적용하는 로컬 기능**으로 제한하는 것이 적합하다. 3단계는 계속 기본 경로로 유지한다.

세부 실측, 비용 시나리오, 단계적 적용안은 [Upscayl 상세 분석 보고서](upscayl_benchmark/upscayl_resource_report.html)와 [재현 노트북](upscayl_benchmark/upscayl_analysis.ipynb)에 정리했다.

## 현재 EDB와의 접점

현재 앱에는 이미 다음 흐름이 있다.

- `app_server.py::_mutate_enhance_image`: 선택 문항의 이미지 향상 요청, 결과 교체, 실패/검토 상태 관리
- `image_reconstruction_backend.py::reconstruct_problem_image`: Gemini/OpenAI provider 실행
- `image_reconstruction_backend.py::postprocess_reconstructed_problem_image`: 선명화, 배경 투명화, Lanczos 확대
- `ocr_backend.py::_prep_crop_for_ocr`: 작은 OCR crop을 Lanczos로 제한 확대

구현은 클라우드 `reconstruct_problem_image`와 분리된 로컬 backend를 3단계 렌더 함수 안에서 자동 호출하는 방식으로 확정했다. 따라서 사용자가 별도 provider나 모델을 고르지 않아도 미리보기·이미지 다운로드·EDB 내보내기가 같은 자동 경로를 사용한다.

권장 처리 순서는 다음과 같다.

```text
원본 crop 보존
  -> upscayl-bin 2x/4x 처리
  -> 기존 투명 배경/잉크 선명화 후처리
  -> 결과를 S3 이미지로 연결
  -> 텍스트·수식 육안 검토 필요 플래그 유지
```

## 권장 적용 범위

| 사용 사례 | 판단 | 이유 |
|---|---|---|
| 저해상도 사진, 삽화, 그래프 crop | 적합 | 확대 시 픽셀 계단과 압축 흔적 개선 가능 |
| 최종 칠판용 문항 이미지 | 조건부 적합 | 투명 PNG를 유지할 수 있으나 글자/수식 검토 필요 |
| 손상된 스캔 이미지의 선택적 복원 | 조건부 적합 | 흐림 제거 도구는 아니며 추정 디테일이 생길 수 있음 |
| PDF 전체 페이지의 기본 전처리 | 비권장 | PDF 재렌더 DPI를 높이는 편이 더 정확하고 저렴함 |
| OCR 직전 모든 crop의 강제 확대 | 비권장 | 작은 글자 획을 바꿔 OCR 오인식을 늘릴 수 있음 |
| 수식/한글 텍스트의 무검수 자동 교체 | 금지 권장 | 교육 콘텐츠 의미가 바뀌는 위험이 있음 |

## 기술 확인 결과

Upscayl은 내부적으로 Real-ESRGAN 계열 모델과 NCNN/Vulkan 기반 CLI를 사용한다. Electron UI를 실행하지 않아도 다음 형태로 호출할 수 있다.

```bash
upscayl-bin \
  -i input.png \
  -o output.png \
  -m /path/to/models \
  -n upscayl-standard-4x \
  -s 4 \
  -f png
```

2026-07-14 Apple M4 개발기에서 upstream의 macOS universal binary로 smoke test를 수행했다.

| 항목 | 결과 |
|---|---|
| 장치 감지 | Apple M4 GPU 정상 감지 |
| 320×180 RGB -> 4x | 1280×720, 성공, cold run 약 2.56초 |
| 160×90 RGBA -> 4x | 640×360, 성공, 약 0.63초 |
| 투명도 | 출력 RGBA 및 alpha 범위 유지 |
| macOS 바이너리 | arm64/x86_64 universal, 약 27 MB |
| 기본 모델 | 7종, 약 171 MB |

이 수치는 합성 소형 이미지 1개 기준이므로 실제 시험지 crop, Windows GPU, 대량 처리 성능을 대표하지 않는다.

### 상세 벤치마크 요약

Apple M4에서 폭 1600px를 목표로 합성 정답 세트 8건, 실제 crop 6건, 모델 파일럿 3건을 추가 측정했다.

| 비교 | 결과 |
|---|---|
| 합성 기술 충실도 | 2단계 76.62점, 3단계 64.06점, Standard 92.36점 |
| 실제 처리 중앙값 | 2단계 0.0673초, 3단계 0.1494초, Standard 11.0899초 |
| 동일 3개 crop 모델 비교 | Lite 1.0016초, Standard 11.3217초, Ultrasharp 11.3840초 |
| Lite 속도 이점 | 동일 표본 중앙값 기준 Standard 대비 약 11.3배 |
| 단일 crop 최대 RSS | Lite 약 141 MiB, Standard 약 443 MiB |

기술 충실도는 edge F1 40%, 잉크 IoU 35%, alpha 유사도 25%의 합성 지표다. 3단계는 페이지 장식 제거와 획 강화를 의도하므로 점수가 낮다고 가독성이 나쁘다는 뜻은 아니다. 실제 crop에는 고해상도 정답 원본이 없어 구조 보존만 평가했다.

## 도입 설계

### 1단계: 자동 Lite 경로 — 구현 완료

- 3단계가 폭 900px 미만 crop만 내부적으로 감지한다.
- `upscayl-lite-4x`로 폭 1600px를 목표로 처리하고, 이후 기존 투명 배경·획 강화 후처리를 그대로 수행한다.
- 별도 버튼, 모델 선택, 성공 알림을 추가하지 않는다.
- 바이너리·모델 미탐지, GPU/Vulkan 오류, 30초 timeout, 비정상 출력은 모두 기존 3단계 Lanczos 경로로 자동 복귀한다.
- 한 번에 하나의 Upscayl 프로세스만 실행하고 출력은 1,600만 픽셀로 제한한다.
- 앱 내 리소스, 설치된 Upscayl 앱, `PATH`, 운영용 `UPSCAYL_BIN`·`UPSCAYL_MODELS_DIR` 순으로 엔진을 자동 탐지한다.

배포 패키저는 외부 설치형 Upscayl 탐색을 기본으로 하며 `resources/upscayl`을 자동 포함하지 않는다. 번들은 macOS `--bundle-upscayl`, Windows `-BundleUpscayl`, direct spec의 `EDB_BUNDLE_UPSCAYL=1`로 명시적으로 활성화해야 한다. 이때 `LICENSE`, `THIRD_PARTY_NOTICES.md`, `CORRESPONDING_SOURCE.txt`가 없거나 비어 있으면 패키징이 중단된다. 릴리스 리소스 구조는 다음과 같다.

```text
resources/upscayl/
  LICENSE                       # 배포 대상 Upscayl 라이선스 전문
  THIRD_PARTY_NOTICES.md        # 모델·NCNN 등 제3자 고지
  CORRESPONDING_SOURCE.txt      # 정확한 버전의 소스 제공 위치/방법
  mac/bin/upscayl-bin          # macOS 빌드
  win/bin/upscayl-bin.exe      # Windows 빌드
  linux/bin/upscayl-bin        # Linux 빌드
  models/upscayl-lite-4x.bin
  models/upscayl-lite-4x.param
```

플랫폼별 릴리스에는 해당 플랫폼 바이너리와 Lite 모델만 넣는다. 바이너리를 직접 배포하기 전 AGPL 고지와 Corresponding Source 제공 방식을 확정해야 한다.

### 2단계: 품질 게이트

실제 과목별 표본으로 다음을 비교한다.

- 국어/영어 작은 본문: OCR CER/WER 변화
- 수학 수식: 기호, 지수, 분수선 변형률
- 과학 그래프: 축, 눈금, 범례, 가는 선 보존률
- 사진/삽화: 블라인드 선호도와 처리 시간
- 원본 대비 결과 이미지의 글자·수식 의미 변경 건수

채택 기준은 사진 품질 향상만이 아니라 **의미 변경 0건에 가까운 안전성**이어야 한다. 텍스트가 포함된 결과는 기존 `check_needed` 검토 상태를 유지한다.

### 3단계: 배포 결정

- 외부 설치형 PoC에서 품질과 호환성이 확인된 뒤 번들 여부를 결정한다.
- 번들 시 Windows x64, macOS universal, Linux x64를 각각 패키징하고 GPU 미지원 안내와 CPU fallback을 준비한다.
- AGPL-3.0 고지, Corresponding Source 제공 방식, 모델별 라이선스를 배포 전에 검토한다.

## 주요 리스크와 대응

| 리스크 | 대응 |
|---|---|
| Vulkan/GPU 미지원 | capability probe, 기능 비활성화 사유 표시, 기존 provider 유지 |
| VRAM 부족·대형 이미지 실패 | tile size와 최대 픽셀 제한, 단건 큐 처리 |
| 글자/수식 왜곡 | 원본 보존, 결과 비교 UI, `check_needed` 강제 |
| 앱 용량 증가 | 1차 외부 설치형, 채택 모델 1~2개만 별도 다운로드 검토 |
| AGPL 배포 의무 | 직접 코드 복사 금지, 프로세스 경계 유지, 법무 검토 후 번들 결정 |
| subprocess 보안 | 인수 배열 호출, shell 미사용, 경로 검증, timeout·프로세스 종료 처리 |

## 구현 상태와 다음 단위

2026-07-14 `Up1`에 다음을 구현했다.

1. `upscayl_backend.py`: 자동 탐지, 저해상도 판정, Lite 실행, timeout·픽셀 상한·단일 큐, fail-open
2. `build_problem_board_edb.py`: 3단계 최종 렌더에 자동 연결
3. `test_upscayl_backend.py`: 명령 안전성, 조건부 실행, 미설치·timeout fallback, 3단계 통합 테스트
4. macOS/Windows/PyInstaller 패키저: backend와 선택적 `resources/upscayl` 포함
5. 실제 Apple M4 CLI smoke: 640×360 RGBA → 1600×900 RGBA, alpha 유지, 약 0.51초
6. 회귀 테스트: 이미지 재구성·내보내기·세션·패키징 293건 통과

남은 출시 단위는 플랫폼별 서명된 CLI·Lite 모델 리소스 준비, AGPL 배포 검토, Windows GPU 매트릭스, 100~300개 블라인드 의미 보존 검증이다. 이 작업 전에도 설치된 Upscayl이 있으면 사용자 UI 없이 자동 적용되고, 없으면 기존 3단계가 그대로 동작한다.

예상 일정은 backend·테스트 3~5 개발일, smart routing·비교 UI·QA 3~5 개발일, 플랫폼 패키징·라이선스 정리 4~8 개발일이다. 1인 기준 총 2~3주이며 법률 검토와 광범위한 Windows GPU 매트릭스는 제외한다.

## 참고

- [Upscayl README](https://github.com/upscayl/upscayl#readme)
- [Upscayl AGPL-3.0 license](https://github.com/upscayl/upscayl/blob/main/LICENSE)
- [Upscayl CLI backend](https://github.com/upscayl/upscayl-ncnn)
- [Real-ESRGAN license notice](https://github.com/upscayl/upscayl/blob/main/Real-ESRGAN_LICENSE.txt)

이 문서의 라이선스 평가는 기술 도입 검토를 위한 참고이며 법률 자문이 아니다.
