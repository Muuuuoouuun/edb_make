# 0004. 국어 지문 헤더 정규식 확장

## Context
`passage_detection.py` `_KOREAN_MATERIAL_RE`(27)가 "다음을 읽고", "(가)와 (나)를 읽고" 같은 헤더를 놓쳐 지문이 문항에 붙었다.

## Decision
`(?:다음|아래|\([가-힣]\)(?:\s*(?:와|과)\s*\([가-힣]\))?)\s*(?:을|를)\s*(?:읽|보|살펴)`를 추가한다. 테스트는 `test_passage_detection.py`에 추가했다.

## Consequences
국어 지문 묶음 인식률이 올라갔다. 영어처럼 다른 과목 패턴은 영향받지 않는다.

## Status
Accepted (2026-10-16)
