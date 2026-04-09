# Structured Problem Board Pipeline

## 목표
- 단순 `PDF -> image-based .edb`가 아니라
- 수학, 과학, 국어 문제 자료를
- `텍스트 블록 + 수식 블록 + 선택지 + 도표/그림 + 표`로 분해한 뒤
- ClassIn `.edb` 객체로 재구성한다.

## 핵심 원칙
- `.edb` 작성보다 먼저 `페이지 구조 이해`를 완성한다.
- OCR 결과만 믿지 말고, 모든 블록은 원본 crop을 fallback으로 가진다.
- 수식은 일반 본문과 분리한다.
- 도형/실험 그림/그래프는 v1에서 이미지 객체로 유지한다.
- subject-aware 후처리를 둔다.

## 권장 파이프라인
1. Ingest
- PDF:
- `PyMuPDF`로 페이지 렌더링
- 가능한 경우 PDF 내부 텍스트/이미지 메타도 함께 추출
- Image:
- `OpenCV`로 perspective correction, deskew, denoise, margin crop

2. Region Segmentation
- OCR보다 먼저 레이아웃 블록을 잡는다.
- 분류 대상:
- header/badge
- problem number
- title/section
- stem
- formula
- choice list
- table
- diagram/image
- explanation/note

3. OCR / Parsing
- OCR은 whole-page가 아니라 region 단위로 수행한다.
- 한국어 혼합 자료는 로컬 OCR 기준으로 `PaddleOCR` 우선 검토
- 페이지 전체를 한 번에 OCR하면 구조가 무너진다.

4. Formula Handling
- 수식은 별도 블록으로 분리
- PDF에 텍스트가 있으면 원문 우선 보존
- raster-only 자료는 formula OCR을 붙이되 confidence를 저장
- confidence가 낮으면 억지 텍스트화하지 않고 crop image fallback 유지

5. Figure / Diagram Handling
- 과학 실험 그림, 수학 도형, 그래프, 국어 지문 삽화는 image block으로 추출
- 캡션/라벨은 가능한 경우 주변 텍스트와 relation으로 연결

6. Reading Order Reconstruction
- raw OCR line order가 아니라 block graph 기준으로 순서를 복원
- 기본 규칙:
- top-to-bottom
- same band에서는 left-to-right
- boxed note, caption, two-column choice는 예외 규칙 적용

7. Structured Schema Export
- OCR 결과를 바로 `.edb`로 쓰지 않는다.
- 먼저 subject-agnostic intermediate schema로 저장한다.

8. EDB Object Mapping
- intermediate schema를 ClassIn 객체로 매핑
- text block -> text record
- formula block -> text or image record
- figure block -> image record
- choice group -> aligned text block set

## 추천 모듈 구성
```text
edb_make/
├─ inspect_edb.py
├─ structured_schema.py
├─ preprocess.py
├─ segment.py
├─ ocr_backend.py
├─ formula_backend.py
├─ assemble_page.py
├─ edb_writer.py
└─ build_structured_edb.py
```

## 모듈 역할
### `preprocess.py`
- deskew
- perspective correction
- noise reduction
- margin detection

### `segment.py`
- text region / figure region / table / formula 후보 추출
- line/box/whitespace 기반 규칙 우선

### `ocr_backend.py`
- region별 OCR
- line 단위 confidence 보존
- 언어 설정과 subject별 후처리

### `formula_backend.py`
- 수식 후보 재분류
- formula OCR 또는 fallback crop 생성

### `assemble_page.py`
- block 병합
- choice grouping
- caption relation
- reading order 정렬
- `structured_schema.PageModel` 생성

### `edb_writer.py`
- schema를 `.edb` text/image object로 매핑
- 좌표 정규화
- thumbnail 생성
- binary payload 구성

## 과목별 전략
### 수학
- 본문과 수식을 분리
- 선택지 내부 수식 허용
- 그래프/도형은 image block 우선
- 잘못 읽은 수식보다 이미지 fallback이 낫다

### 과학
- apparatus diagram, label, arrow, table을 따로 취급
- figure와 label이 OCR에서 분리되어도 relation으로 묶는다

### 국어
- 문단 경계, 번호, 들여쓰기 보존이 중요
- 글자 단위 정확도보다 paragraph block 안정성이 우선

## v1 성공 기준
- 문제 번호가 분리된다
- 본문 텍스트 블록이 안정적으로 잡힌다
- 수식이 별도 블록 또는 fallback image로 저장된다
- 그림/도표가 crop image로 유지된다
- 선택지가 그룹으로 묶인다
- 이 intermediate schema를 `.edb` 배치로 옮길 수 있다

## 권장 구현 순서
1. `structured_schema.py` 확정
2. `preprocess.py` + `segment.py` 초안
3. region 단위 OCR
4. math/science/korean subject heuristic 추가
5. page assembler 구현
6. intermediate JSON 저장
7. `.edb` writer 연결

## AI/API 전략
### core
- 로컬 처리만으로 동작
- `PyMuPDF`, `OpenCV`, `PaddleOCR`, `numpy`, `Pillow`

### optional AI enhancement
- low-confidence region만 외부 API로 재해석
- formula-heavy page
- 복잡한 표/다단 지문
- 손상된 스캔 이미지

## 절대 피할 것
- whole-page OCR 한 번으로 끝내기
- 모든 수식을 plain text로 강제 변환
- 복잡한 도형을 v1에서 벡터 객체로 재구성하려 하기
- schema 없이 OCR 결과를 곧바로 `.edb`로 기록하기
