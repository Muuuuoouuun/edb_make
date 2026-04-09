# ClassIn EDB 개발 정리

## 현재까지 확인한 것
- `.edb`는 단순 문서 파일이 아니라 `고정 outer header + gzip payload` 구조다.
- gzip 내부에는 `고정 헤더 + 길이 포함 레코드들`이 들어 있다.
- 텍스트형 레코드와 이미지형 레코드가 다르게 존재한다.
- 이미지형 레코드 안에는 보통 `원본 이미지 + 썸네일 이미지`가 같이 들어 있다.
- PDF를 직접 감싸는 것보다 `페이지를 이미지화해서 edb 레코드로 조립`하는 방식이 1차 구현에 가장 현실적이다.

## 목표 권장안
### 1차 목표
- `PDF -> page images -> image-based .edb`
- ClassIn에서 열리고, 페이지별로 보이고, 썸네일/배치가 정상 동작하는 상태

### 2차 목표
- `이미지 여러 장 -> .edb`
- JPG/PNG 폴더를 입력으로 받아 edb 생성

### 3차 목표
- 텍스트/도형/판서 객체까지 복원하는 고급 생성기
- 이 단계는 역공학 범위가 커서 1차 성공 뒤에 진행 권장

## 바로 해야 할 다음 단계
1. 샘플 확보
- ClassIn에서 직접 만든 `.edb`를 더 모은다.
- 특히 아래 2개가 중요하다.
- `이미지 1장만 넣은 .edb`
- `PDF 1페이지만 넣은 .edb`

2. 비교 파서 고도화
- 기존 `edb_make/inspect_edb.py`로 레코드 차이를 계속 비교
- 텍스트형/이미지형/기타 컨트롤 레코드 분류 정확도 높이기

3. 이미지 레코드 writer 만들기
- 이미지 1개를 받아 `원본 + 썸네일` 2개를 넣는 레코드 생성
- 좌표 `x, y, w, h` 필드와 tail 바이트 규칙 정리

4. 최소 edb 생성기 만들기
- outer header 작성
- gzip payload 작성
- 내부 header 작성
- image record N개 작성
- 종료 레코드/마커 규칙 반영

5. ClassIn 실기 테스트
- 생성된 edb가 열리는지
- 페이지 순서가 맞는지
- 썸네일이 정상인지
- 확대/축소/이동 시 깨지지 않는지

## 추천 개발 구조
```text
C:\Projects\Class_project
├─ CLASSIN_EDB_NEXT_STEPS.md
├─ edb_make
│  ├─ inspect_edb.py
│  ├─ samples
│  ├─ out_images_sample4
│  ├─ writer.py
│  ├─ pdf_to_images.py
│  └─ build_edb.py
```

## 모듈별 역할
### `inspect_edb.py`
- 기존 edb 읽기
- 헤더/레코드/텍스트/내장 이미지 분석

### `pdf_to_images.py`
- PDF를 페이지별 PNG/JPG로 렌더링
- 썸네일 생성
- 해상도/품질 옵션 처리

### `writer.py`
- 내부 레코드 바이너리 생성
- float/int/길이 필드 직렬화
- outer header + gzip 패키징

### `build_edb.py`
- 실제 CLI 진입점
- 입력: PDF 또는 이미지 폴더
- 출력: `.edb`

## 필요한 라이브러리
### 필수
- Python 3.11+ 또는 현재 사용 중인 Python
- `Pillow`
- `PyMuPDF` 또는 `pdf2image`

### 권장
- `rich`
- `pytest`
- `hexdump` 또는 바이너리 비교용 유틸

### 선택
- `opencv-python`
- `numpy`
- `pytesseract`

## PDF 처리 선택지
### 추천 1순위: `PyMuPDF`
- 설치가 상대적으로 간단하다.
- 페이지 렌더링이 빠르다.
- 외부 Poppler 의존성이 없다.

### 대안: `pdf2image`
- 결과는 좋지만 Windows에서 Poppler 설치가 추가로 필요하다.

## AI API 필요 여부
### v1 이미지 기반 edb 생성기
- 필요 없음
- PDF 렌더링과 이미지 삽입만으로 구현 가능

### AI API가 있으면 좋은 경우
- 스캔 PDF의 레이아웃 분석
- OCR 후 텍스트 객체 분리 시도
- 판서/문단/도형 자동 분류
- 이미지 자동 보정

### 추천 AI 기능 분리
- `core pipeline`: AI 없이 동작
- `optional AI enhancement`: 별도 플래그로 동작

## 쓸 수 있는 AI/API 후보
### OpenAI API
- 사용 목적:
- OCR 보조
- 문단/도형/문제 영역 분리
- 페이지를 텍스트/도형 객체 후보로 구조화
- 필수 여부: 선택

### Google Vision / Azure Vision / AWS Textract
- 사용 목적:
- OCR 품질 향상
- 표/문단 영역 인식
- 필수 여부: 선택

## 추천 구현 순서
1. `inspect_edb.py` 안정화
2. `pdf_to_images.py` 구현
3. `writer.py`에서 이미지형 레코드 1개 생성
4. `.edb` 1페이지 생성 성공
5. 다중 페이지 확장
6. CLI 정리
7. 필요시 AI 보강

## 성공 기준
- ClassIn에서 파일이 열린다.
- 적어도 한 페이지 이미지가 정상 표시된다.
- 다중 페이지가 순서대로 보인다.
- 앱이 크래시하지 않는다.

## 리스크
- 내부 헤더의 일부 필드 의미가 아직 완전히 확정되지 않음
- tail 바이트/종료 레코드 규칙이 버전별로 다를 수 있음
- ClassIn 버전에 따라 strict validation이 있을 수 있음

## 지금 당장 필요한 것
- 실제 ClassIn에서 만든 `1장 이미지 edb`
- 실제 ClassIn에서 만든 `1페이지 PDF edb`
- 테스트 가능한 샘플 PDF 1개

## AI/API 정리 한 줄 결론
- 첫 버전은 AI API 없이 충분히 가능
- OpenAI API는 나중에 OCR/구조화 자동화가 필요할 때 붙이면 된다
