# 0003. 문항 crop은 다음 지문 시작에서 자른다

## Context
지문 묶음 헤더("[4~7] 다음 글을 읽고…")가 앞 문항 crop 안으로 들어가 앞 문항과 다음 지문이 한 덩어리가 됐다.

## Decision
`segment.py` `_clip_problem_blocks_at_passage_starts`(4674): 문항 블록 안에 지문 시작이 있으면 그 위에서 자른다. 같은 행 판정은 `_same_row_band`가 맡는다.

## Consequences
지문 조각과 교차 페이지 지문이 정확해졌다(3월 고1 국어 지문 조각 12→14, 교차 페이지 10→12).

## Status
Accepted (2026-10-16)
