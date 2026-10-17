# 0001. 스캔 PDF는 시각적 문항번호 검출 + 밀도 게이트로 분할

## Context
텍스트 레이어가 없는 스캔 PDF 페이지는 `pdf_text_lines`/`pdf_problem_markers`가 비어 있어 곧장 row-band(document-bands)로 떨어졌고, 2단 시험지를 격자처럼 잘랐다.

## Decision
- `segment.py` `_is_scanned_pdf_page_metadata`: 텍스트 메타데이터가 비어 있는 PDF 페이지는 이미지 업로드처럼 취급해 `visual-problem-markers`(숫자+점 컴포넌트)를 먼저 시도한다.
- `_visual_marker_density_plausible` / `VISUAL_MARKER_MAX_PER_COLUMN = 8`: 실제 시험지 한 단에는 문항이 6~7개 이하이므로, 스캔 PDF에서 한 단에 8개를 넘게 검출하면 본문 숫자를 오검출한 것으로 보고 document-bands로 되돌린다.

## Consequences
- 스캔에서도 문항 단위 crop이 가능해졌다. 다만 숫자 오검출이 많으면 과분할이 생길 수 있다(0007에서 보완).
- 밀도 8 초과 페이지는 여전히 row-band 품질이다.

## Status
Accepted (2026-10-16). 0007로 보완.
