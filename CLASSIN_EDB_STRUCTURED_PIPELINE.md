# ClassIn EDB Structured Pipeline

## 현재 이해
- `.edb` 안에 PDF 원본이 직접 들어가지는 않는다.
- 사용자가 PDF를 칠판에 올리는 흐름도 실제 저장 결과는 `캡처/렌더링된 이미지 객체`에 가깝다.
- 따라서 입력이 PDF여도 `.edb writer` 관점에서는 결국 `페이지 이미지 + 텍스트 객체 + 보조 이미지 객체` 조합으로 생각해야 한다.

## 새 controlled sample 결과
### `text-only.edb`
- 경로: `C:\Users\aaaha\Downloads\text-only.edb`
- 분석 결과:
- outer header + gzip payload 구조 동일
- 내부에 텍스트 레코드 2개만 존재
- text preview:
- `test -text`
- `text only`
- 최소 text record writer 실험용 기준 샘플로 적합

### `image1.edb`
- 경로: `C:\Users\aaaha\Downloads\image1.edb`
- 분석 결과:
- 이미지 레코드 1개만 존재
- 동일한 크기의 JPEG 2개 슬롯이 들어 있음
- 즉 두 번째 슬롯은 항상 작은 thumbnail이라고 단정할 수 없음
- 최소 image record writer 실험용 기준 샘플로 적합

## 설계상 바뀌는 점
- `PDF parser -> edb`가 아니다.
- `PDF/image source -> page understanding -> structured blocks -> edb writer`다.
- PDF는 upstream input일 뿐이고, `.edb` 내부 포맷은 결국 칠판 객체 저장 포맷이다.

## 최우선 개발 축
1. `text-only.edb` 기준으로 최소 text record writer 고정
2. `image1.edb` 기준으로 최소 image record writer 고정
3. page understanding 결과를 중간 JSON으로 저장
4. JSON을 text/image 혼합 `.edb`로 변환

## writer v1 목표
- 텍스트 블록은 text record
- 복잡한 수식은 image record fallback
- 도형/실험 그림/삽화는 image record
- 문제지 한 페이지를 혼합 객체로 재배치

## 구현 단계
### 단계 1
- `text-only.edb`를 기준으로 text record binary builder 작성

### 단계 2
- `image1.edb`를 기준으로 image record binary builder 작성

### 단계 3
- 하나의 페이지에서
- 제목
- 본문
- 선택지
- 수식 이미지
- 도해 이미지
- 를 섞어서 mixed page writer 작성

### 단계 4
- OCR/레이아웃 추출 결과를 자동으로 mixed writer에 연결

## 확정된 기술 판단
- `.edb` 내부에 PDF를 넣으려는 시도는 불필요
- OCR은 구조화를 위한 도구이고, 저장 포맷은 객체 배치
- v1에서 복잡한 수식은 이미지 fallback이 더 안전
- controlled sample을 더 늘릴수록 writer 정확도가 빠르게 올라감

## 지금 가장 가치 높은 추가 샘플
- 텍스트 1개 + 이미지 1개가 같이 있는 `.edb`
- 텍스트 여러 줄 정렬이 들어간 `.edb`
- 이미지 2개 이상 배치된 `.edb`
- 같은 이미지를 크기만 바꿔 붙여넣은 `.edb`
