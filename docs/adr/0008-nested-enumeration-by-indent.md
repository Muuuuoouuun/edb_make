# 0008. 뒤 문항번호보다 들여쓴 번호 목록은 중첩 내용

## Context
3월 고1 국어 2쪽에서 x=989의 "1./2./3." 목록이 그 아래 x=961의 4번 문항보다 들여쓰여 있었다. 그런데도 1,2,3,4 연속 번호로 인정돼 1~3번이 중복으로 생성됐다.

## Decision
`segment.py`(2395, 2630 부근): 같은 단에서 뒤에 오는 더 큰 번호의 마커보다 max(16px, 폭의 0.8%) 이상 들여쓴 마커는 중첩 목록으로 본다.

## Consequences
3월 고1 국어 중복 [1,2,3]이 사라졌다(numbered 48→45). 회귀 테스트는 `test_scan_layout_regressions.py::TestIndentedListIsNotQuestionSequence`.

## Status
Accepted (2026-10-16)
