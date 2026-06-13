import sys
import unittest
from structured_schema import PageModel, ContentBlock, BlockType, Subject, Box, ProblemUnit
from assemble_page import group_problem_units

class TestAssemblePageKoreanEnhancements(unittest.TestCase):
    def test_choice_gap_filling(self):
        # Create a page containing choices with gaps (e.g., ①, then a block that contains ② text but wasn't classified, then ③, ④, ⑤)
        blocks = [
            ContentBlock(
                block_id="p1-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 10, 500, 50),
                reading_order=0,
                text="1. 다음 문제를 푸시오."
            ),
            ContentBlock(
                block_id="choice-1",
                block_type=BlockType.CHOICE,
                bbox=Box(10, 70, 100, 30),
                reading_order=1,
                text="① x = 1"
            ),
            ContentBlock(
                block_id="choice-2-gap",
                block_type=BlockType.STEM, # Starts as STEM
                bbox=Box(110, 70, 100, 30),
                reading_order=2,
                text="② x = 2" # Contains the circled number ② but got misclassified
            ),
            ContentBlock(
                block_id="choice-3",
                block_type=BlockType.CHOICE,
                bbox=Box(210, 70, 100, 30),
                reading_order=3,
                text="③ x = 3"
            ),
        ]
        page = PageModel(
            page_id="page-choice-test",
            width_px=1000,
            height_px=1000,
            subject=Subject.MATH,
            blocks=blocks
        )
        
        grouped = group_problem_units(page)
        # Verify choice-2-gap was relabeled as BlockType.CHOICE
        block_types = {b.block_id: b.block_type for b in grouped.blocks}
        self.assertEqual(block_types["choice-2-gap"], BlockType.CHOICE)

    def test_set_problem_range_parsing(self):
        # Create a page with:
        # 1. Range header: [13~14] 다음 글을 읽고 물음에 답하시오.
        # 2. Shared passage: <보기> box contents...
        # 3. Question 13: 13. 위 글의 성격으로 알맞은 것은?
        # 4. Question 14: 14. 다음 밑줄 친 부분의 뜻은?
        blocks = [
            ContentBlock(
                block_id="range-header",
                block_type=BlockType.STEM,
                bbox=Box(10, 10, 500, 40),
                reading_order=0,
                text="[13~14] 다음 글을 읽고 물음에 답하시오."
            ),
            ContentBlock(
                block_id="shared-passage",
                block_type=BlockType.STEM,
                bbox=Box(10, 60, 500, 150),
                reading_order=1,
                text="어떤 나라의 언어는 역사적 흐름에 따라 변화한다. (가) 조선 시대에는..."
            ),
            ContentBlock(
                block_id="q13-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 220, 500, 50),
                reading_order=2,
                text="13. 위 글의 성격으로 알맞은 것은?"
            ),
            ContentBlock(
                block_id="q14-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 280, 500, 50),
                reading_order=3,
                text="14. 다음 밑줄 친 부분의 뜻은?"
            ),
        ]
        
        page = PageModel(
            page_id="page-range-test",
            width_px=1000,
            height_px=1000,
            subject=Subject.KOREAN,
            blocks=blocks
        )
        
        grouped = group_problem_units(page)
        
        # We expect 2 problem units (for 13 and 14)
        problems = grouped.problems
        self.assertEqual(len(problems), 2)
        
        p13 = next((p for p in problems if p.metadata.get("problem_number") == 13), None)
        p14 = next((p for p in problems if p.metadata.get("problem_number") == 14), None)
        
        self.assertIsNotNone(p13)
        self.assertIsNotNone(p14)
        
        # Verify that both p13 and p14 have range-header and shared-passage in their stem_block_ids
        self.assertIn("range-header", p13.stem_block_ids)
        self.assertIn("shared-passage", p13.stem_block_ids)
        self.assertIn("q13-stem", p13.stem_block_ids)
        
        self.assertIn("range-header", p14.stem_block_ids)
        self.assertIn("shared-passage", p14.stem_block_ids)
        self.assertIn("q14-stem", p14.stem_block_ids)
        
        # Also, range-header and shared-passage should not be in q14-stem's normal sequencing
        self.assertNotIn("q13-stem", p14.stem_block_ids)

    def test_set_problem_range_marks_child_question_metadata(self):
        blocks = [
            ContentBlock(
                block_id="range-header",
                block_type=BlockType.STEM,
                bbox=Box(10, 10, 500, 40),
                reading_order=0,
                text="[13~14] 다음 글을 읽고 물음에 답하시오."
            ),
            ContentBlock(
                block_id="shared-passage",
                block_type=BlockType.STEM,
                bbox=Box(10, 60, 500, 150),
                reading_order=1,
                text="Long passage text that both child questions depend on."
            ),
            ContentBlock(
                block_id="q13-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 220, 500, 50),
                reading_order=2,
                text="13. 위 글의 내용으로 알맞은 것은?"
            ),
            ContentBlock(
                block_id="q14-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 280, 500, 50),
                reading_order=3,
                text="14. 윗글을 바탕으로 추론한 것은?"
            ),
        ]
        page = PageModel(
            page_id="page-range-metadata-test",
            width_px=1000,
            height_px=1000,
            subject=Subject.KOREAN,
            blocks=blocks
        )

        grouped = group_problem_units(page)
        p13 = next(problem for problem in grouped.problems if problem.metadata.get("problem_number") == 13)
        p14 = next(problem for problem in grouped.problems if problem.metadata.get("problem_number") == 14)

        for problem in (p13, p14):
            self.assertEqual("page-range-metadata-test-passage-13-14", problem.metadata.get("passage_group_id"))
            self.assertEqual({"start": 13, "end": 14}, problem.metadata.get("passage_range"))
            self.assertEqual("child_question", problem.metadata.get("passage_role"))
            self.assertEqual(["range-header", "shared-passage"], problem.metadata.get("shared_passage_block_ids"))
            self.assertEqual([13, 14], problem.metadata.get("passage_child_problem_numbers"))

if __name__ == "__main__":
    unittest.main()
