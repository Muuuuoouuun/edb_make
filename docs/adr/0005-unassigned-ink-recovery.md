# 0005. 어느 crop에도 속하지 않은 잉크 복구

## Context
마커 기반 분할은 번호가 있는 곳에서만 crop을 만든다. 그래서 단 상단에 이어지는 지문, 표 꼬리 같은 잉크가 조용히 사라졌다.

## Decision
`segment.py` `_recover_uncovered_document_regions`(4817):
- 남은 잉크를 단별 영역으로 복구하고 review flag를 단다.
- 복구 영역은 바로 앞 블록 뒤에 삽입한다.
- 텍스트 PDF에서는 단 상단 이어짐만 복구한다. 머리말 절반(header_band), 머리말 구분선 위 영역(above_header_rule)은 제외한다.
- 묶음 지시문(`PDF_GROUP_INSTRUCTION_RE`)은 다음 문항 bbox에 합친다.
- 스캔에서 전폭 띠는 하나의 영역으로 묶어 가장 먼저 읽는다.
- `build_problem_board_edb.py` `_drop_recovered_regions_inside`(3834): 이후 단계가 덮는 복구 영역은 중복이므로 제거한다.
- `assemble_page.py`: 정렬 키에 recovered 여부를 넣는다.
- 2026-10-16 추가: `_merge_consecutive_column_regions`(4729)는 같은 단에서 사이에 블록이 없는 복구 영역들을 하나로 합친다. 문단 간격마다 조각나는 것을 막기 위해서다.

## Consequences
미배정 잉크 비율이 크게 줄었다(스캔 4~8% → 0.2~0.6%). 복구 영역은 검토 대상으로 표시된다.

## Status
Accepted (2026-10-16)
