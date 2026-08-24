import tempfile
import unittest
from pathlib import Path

from PIL import Image

from assemble_page import group_problem_units
from build_problem_board_edb import build_problem_entries
from layout_template_schema import LayoutTemplate
from preprocess import PreparedPage
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject


def _block(block_id: str, block_type: BlockType, top: float, text: str | None, *, height: float = 80.0) -> ContentBlock:
    return ContentBlock(
        block_id=block_id,
        block_type=block_type,
        bbox=Box(left=40.0, top=top, width=760.0, height=height),
        reading_order=int(top),
        text=text,
    )


def _problem_block_ids(problem: ProblemUnit) -> list[str]:
    return (
        list(problem.stem_block_ids)
        + list(problem.choice_block_ids)
        + list(problem.explanation_block_ids)
        + list(problem.figure_block_ids)
    )


def _problem_by_number(page: PageModel, number: int) -> ProblemUnit:
    return next(problem for problem in page.problems if problem.metadata.get("problem_number") == number)


class TestPassageGroupQualityScore(unittest.TestCase):
    def test_korean_english_large_passage_quality_score_is_at_least_90(self):
        score = 0
        failures: list[str] = []

        if self._korean_range_passage_is_independent():
            score += 25
        else:
            failures.append("국어 범위 지문 독립 인식 실패")

        if self._english_range_image_passage_is_independent():
            score += 25
        else:
            failures.append("영어 범위 이미지 지문 독립 인식 실패")

        if self._single_problem_internal_passage_stays_inside_problem():
            score += 25
        else:
            failures.append("문제 하위 지문/보기 오분리")

        if self._cross_page_passage_continuation_links_without_mixing():
            score += 25
        else:
            failures.append("페이지 넘김 지문 연결/비혼합 실패")

        self.assertGreaterEqual(score, 90, f"passage quality score={score}; failures={failures}")

    def _korean_range_passage_is_independent(self) -> bool:
        page = PageModel(
            page_id="korean-page-001",
            width_px=900,
            height_px=1200,
            subject=Subject.KOREAN,
            blocks=[
                _block("range-18-21", BlockType.STEM, 40, "[18~21] 다음 글을 읽고 물음에 답하시오."),
                _block("shared-passage", BlockType.STEM, 140, "이 글은 여러 문항이 함께 참조하는 긴 지문이다.", height=320),
                _block("q18", BlockType.STEM, 520, "18. 윗글의 내용으로 적절한 것은?"),
                _block("q19", BlockType.STEM, 660, "19. 윗글의 서술 방식으로 적절한 것은?"),
                _block("q20", BlockType.STEM, 800, "20. 윗글을 바탕으로 추론한 내용은?"),
                _block("q21", BlockType.STEM, 940, "21. 윗글의 핵심 내용은?"),
            ],
        )
        grouped = group_problem_units(page)
        fragments = [problem for problem in grouped.problems if problem.metadata.get("passage_role") == "passage_fragment"]
        if len(fragments) != 1:
            return False
        fragment = fragments[0]
        expected_shared = {"range-18-21", "shared-passage"}
        if set(_problem_block_ids(fragment)) != expected_shared:
            return False
        for number in range(18, 22):
            problem = _problem_by_number(grouped, number)
            if problem.metadata.get("passage_role") != "child_question":
                return False
            if expected_shared.intersection(_problem_block_ids(problem)):
                return False
        return True

    def _english_range_image_passage_is_independent(self) -> bool:
        page = PageModel(
            page_id="english-page-001",
            width_px=900,
            height_px=1400,
            subject=Subject.ENGLISH,
            blocks=[
                _block("range-31-34", BlockType.STEM, 40, "[31-34] Read the following passage and answer the questions."),
                _block("passage-image", BlockType.IMAGE, 130, None, height=520),
                _block("q31", BlockType.STEM, 720, "31. What is the main idea of the passage?"),
                _block("q32", BlockType.STEM, 860, "32. Which is closest in meaning to the underlined word?"),
                _block("q33", BlockType.STEM, 1000, "33. What can be inferred from the passage?"),
                _block("q34", BlockType.STEM, 1140, "34. Which sentence best completes the paragraph?"),
            ],
        )
        grouped = group_problem_units(page)
        fragments = [problem for problem in grouped.problems if problem.metadata.get("passage_role") == "passage_fragment"]
        if len(fragments) != 1:
            return False
        fragment = fragments[0]
        if "passage-image" not in fragment.figure_block_ids:
            return False
        for number in range(31, 35):
            problem = _problem_by_number(grouped, number)
            if problem.metadata.get("passage_group_id") != "english-page-001-passage-31-34":
                return False
            if "passage-image" in _problem_block_ids(problem):
                return False
        return True

    def _single_problem_internal_passage_stays_inside_problem(self) -> bool:
        page = PageModel(
            page_id="english-page-002",
            width_px=900,
            height_px=1000,
            subject=Subject.ENGLISH,
            blocks=[
                _block("q35", BlockType.STEM, 60, "35. Read the following excerpt in <보기> and choose the correct answer."),
                _block("q35-internal-passage", BlockType.STEM, 170, "<보기> The speaker hesitates before answering.", height=180),
                _block("q35-choice-a", BlockType.CHOICE, 400, "A. The speaker is confident."),
                _block("q35-choice-b", BlockType.CHOICE, 480, "B. The speaker is uncertain."),
            ],
        )
        grouped = group_problem_units(page)
        if any(problem.metadata.get("passage_role") == "passage_fragment" for problem in grouped.problems):
            return False
        if len(grouped.problems) != 1:
            return False
        problem = grouped.problems[0]
        return "q35-internal-passage" in _problem_block_ids(problem)

    def _cross_page_passage_continuation_links_without_mixing(self) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_1_path = root / "page-1.png"
            page_2_path = root / "page-2.png"
            for path in (page_1_path, page_2_path):
                Image.new("RGB", (900, 1400), "white").save(path)

            prepared_pages = [
                PreparedPage(
                    page_id="korean-cross-001",
                    source_path=str(page_1_path),
                    page_number=1,
                    image=Image.open(page_1_path).convert("RGB"),
                    original_size=(900, 1400),
                ),
                PreparedPage(
                    page_id="korean-cross-002",
                    source_path=str(page_2_path),
                    page_number=2,
                    image=Image.open(page_2_path).convert("RGB"),
                    original_size=(900, 1400),
                ),
            ]

            page_1 = PageModel(
                page_id="korean-cross-001",
                width_px=900,
                height_px=1400,
                subject=Subject.KOREAN,
                source_path=str(page_1_path),
                blocks=[
                    _block("range-18-21", BlockType.STEM, 40, "[18~21] 다음 글을 읽고 물음에 답하시오."),
                    _block("shared-passage-a", BlockType.STEM, 140, "긴 지문의 첫 페이지 내용이다.", height=520),
                ],
                problems=[
                    ProblemUnit(
                        unit_id="korean-cross-001-passage-fragment",
                        subject=Subject.KOREAN,
                        title="지문 18~21",
                        stem_block_ids=["range-18-21", "shared-passage-a"],
                        metadata={
                            "passage_group_id": "korean-cross-001-passage-18-21",
                            "passage_range": {"start": 18, "end": 21},
                            "passage_role": "passage_fragment",
                            "passage_child_problem_numbers": [18, 19, 20, 21],
                            "supplemental_item": True,
                        },
                    )
                ],
            )
            page_2 = group_problem_units(
                PageModel(
                    page_id="korean-cross-002",
                    width_px=900,
                    height_px=1400,
                    subject=Subject.KOREAN,
                    source_path=str(page_2_path),
                    blocks=[
                        _block("shared-passage-b", BlockType.STEM, 40, "앞 페이지에서 이어지는 긴 지문 내용이다.", height=420),
                        _block("q18", BlockType.STEM, 520, "18. 윗글의 내용으로 적절한 것은?"),
                        _block("q19", BlockType.STEM, 680, "19. 윗글의 서술 방식으로 적절한 것은?"),
                        _block("q20", BlockType.STEM, 840, "20. 윗글을 바탕으로 추론한 내용은?"),
                        _block("q21", BlockType.STEM, 1000, "21. 윗글의 핵심 내용은?"),
                    ],
                )
            )

            entries = build_problem_entries(
                prepared_pages,
                [page_1, page_2],
                root / "out",
                LayoutTemplate(name="academy-default"),
            )
            entry_blocks = {
                entry.problem_id: [block.block_id for block in entry.blocks]
                for entry in entries
            }

            q20 = _problem_by_number(page_2, 20)
            q21 = _problem_by_number(page_2, 21)
            fragments = [
                problem
                for problem in page_2.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            ]
            primary_fragments = [
                problem
                for problem in page_1.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            ]
            if len(fragments) != 1:
                return False
            if len(primary_fragments) != 1:
                return False
            if fragments[0].metadata.get("passage_group_id") != "korean-cross-001-passage-18-21":
                return False
            if q20.metadata.get("passage_group_id") != "korean-cross-001-passage-18-21":
                return False
            if q21.metadata.get("passage_group_id") != "korean-cross-001-passage-18-21":
                return False
            if "shared-passage-b" in entry_blocks.get(q20.unit_id, []):
                return False
            primary_fragment = primary_fragments[0]
            if primary_fragment.unit_id not in entry_blocks:
                return False
            if fragments[0].unit_id in entry_blocks:
                return False
            if entry_blocks.get(primary_fragment.unit_id) != []:
                return False
            if not primary_fragment.metadata.get("passage_fragments_merged"):
                return False
            if fragments[0].metadata.get("passage_merged_into_problem_id") != primary_fragment.unit_id:
                return False
            return True


if __name__ == "__main__":
    unittest.main()
