# 0002. 단 분리: 중앙 세로선 우선, 없으면 가장 넓은 투영 골짜기

## Context
기존 단 분리는 가운데에 가장 가까운 좁은 골짜기를 골라, 단 사이 경계 근처 본문을 잘라내거나 좌/우 단 문항을 섞었다(N/Z 읽기 순서 오류).

## Decision
- `segment.py` `_document_split_ink_gutters`(571): 세로 rule 판정 기준을 높이의 20%로 둔다.
- `_widest_projection_valley_offset`(463): 중앙 세로선이 없으면 세로 잉크 투영에서 가장 넓은 골짜기를 고르고, 인접한 세로선이 있으면 그 선에 snap한다.
- `_visual_marker_column_boxes`: 각 단의 왼쪽 경계를 피크 잉크의 5% 이상인 첫 x로 좁히고, 오른쪽 단은 맨 앞의 중앙 구분선을 건너뛴다.

## Consequences
경계 근처 본문이 잘리지 않고 N 순서가 유지된다. 단 경계가 base보다 넓다.

## Status
Accepted (2026-10-16)
