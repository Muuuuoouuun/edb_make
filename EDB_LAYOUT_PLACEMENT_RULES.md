# EDB Layout Placement Rules

## Goal

Define a concrete placement policy for arranging extracted problems on the ClassIn board.

This document turns the UX rule into an implementation-ready layout rule.

## Core Constraints

- The board is treated as a vertical sequence of about `50 pages`
- The visible problem area is the fixed left content zone
- The right side remains open for live teaching and handwriting
- The base vertical rhythm is `1.2 page`

## Placement Model

Each problem should track at least these values:

- `problem_id`
- `subject`
- `start_y_pages`
- `nominal_slot_height_pages`
- `actual_content_height_pages`
- `actual_bottom_y_pages`
- `snapped_next_start_y_pages`
- `overflow_allowed`

## Default Rule

- Use `nominal_slot_height_pages = 1.2`
- Start each problem on a snapped `1.2` boundary
- Keep the problem inside the fixed left zone

## Overflow Rule

Overflow is allowed for tall content such as:

- Korean reading passages
- long multi-line stems
- tall composite problem crops

Overflow means:

- the content may visually extend below the base `1.2` slot
- the content still belongs to the same problem block
- the next problem must wait for the next snapped boundary above the occupied bottom

## Snap Rule

Use this formula:

```text
snapped_next_start_y_pages = ceil(actual_bottom_y_pages / 1.2) * 1.2
```

Where:

- `actual_bottom_y_pages = start_y_pages + actual_content_height_pages`

## Stair-Step Examples

### Example 1. Normal Problem

- start = `0.0`
- actual height = `0.92`
- actual bottom = `0.92`
- next start = `1.2`

### Example 2. Long Korean Passage

- start = `1.2`
- actual height = `1.43`
- actual bottom = `2.63`
- next start = `3.6`

### Example 3. Slight Overflow

- start = `3.6`
- actual height = `1.24`
- actual bottom = `4.84`
- next start = `6.0`

## Recommended Subject Defaults

- `korean`: overflow allowed by default
- `english`: overflow allowed for reading sets
- `math`: prefer fit-first, overflow only if readability would degrade
- `science`: prefer fit-first, allow overflow for figure-heavy problems

## Preview Requirements

The layout preview should display:

- fixed left content zone
- `1.2` grid lines
- actual content box
- snapped next-start guide
- warning when the remaining board height is insufficient

## QA Requirements

The QA layer should flag:

- overlap with the next problem
- missing snap after overflow
- content leaving the fixed left zone
- too much scale reduction to force fit
- total board length exceeding configured board capacity

## Suggested Engine Output

The placement engine should output a list like:

```json
[
  {
    "problem_id": "p1",
    "start_y_pages": 0.0,
    "actual_content_height_pages": 0.92,
    "actual_bottom_y_pages": 0.92,
    "snapped_next_start_y_pages": 1.2,
    "overflow_allowed": false
  },
  {
    "problem_id": "p2",
    "start_y_pages": 1.2,
    "actual_content_height_pages": 1.43,
    "actual_bottom_y_pages": 2.63,
    "snapped_next_start_y_pages": 3.6,
    "overflow_allowed": true
  }
]
```

## Engineering Follow-Up

Recommended next implementation steps:

1. Add a layout template schema with `base_slot_height_pages = 1.2`
2. Add a placement engine that computes snap positions
3. Store both nominal height and actual occupied height per problem
4. Surface the stair-step guides in the review UI
