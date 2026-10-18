# 0006. 기울기 보정: minAreaRect → 투영 프로파일

## Context
`cv2.minAreaRect`는 전체 잉크의 외곽을 재기 때문에, 전폭 표나 박스가 있으면 기울지 않은 페이지에도 회전을 넣었다.

## Decision
`preprocess.py` `_estimate_skew_angle_by_projection`(3732): ±5° 범위에서 행 투영의 분산이 가장 큰 각도를 고른다.

## Consequences
불필요한 회전이 사라져 단 경계와 박스 판정이 안정됐다. 계산량은 소폭 늘었다.

## Status
Accepted (2026-10-16)
