import unittest
from structured_schema import PageModel, ContentBlock, BlockType, Subject, Box, OcrLine
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
        
        # We expect 1 passage fragment plus 2 child question units.
        problems = grouped.problems
        self.assertEqual(len(problems), 3)
        
        passage = next((p for p in problems if p.metadata.get("passage_role") == "passage_fragment"), None)
        p13 = next((p for p in problems if p.metadata.get("problem_number") == 13), None)
        p14 = next((p for p in problems if p.metadata.get("problem_number") == 14), None)
        
        self.assertIsNotNone(passage)
        self.assertIsNotNone(p13)
        self.assertIsNotNone(p14)
        
        self.assertEqual(["range-header", "shared-passage"], passage.stem_block_ids)
        self.assertEqual("page-range-test-passage-13-14", passage.metadata.get("passage_group_id"))
        self.assertEqual({"start": 13, "end": 14}, passage.metadata.get("passage_range"))
        self.assertTrue(passage.metadata.get("supplemental_item"))
        
        self.assertEqual(["q13-stem"], p13.stem_block_ids)
        self.assertEqual(["q14-stem"], p14.stem_block_ids)
        
        self.assertEqual("child_question", p13.metadata.get("passage_role"))
        self.assertEqual("child_question", p14.metadata.get("passage_role"))

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
        passage = next(problem for problem in grouped.problems if problem.metadata.get("passage_role") == "passage_fragment")
        p13 = next(problem for problem in grouped.problems if problem.metadata.get("problem_number") == 13)
        p14 = next(problem for problem in grouped.problems if problem.metadata.get("problem_number") == 14)

        self.assertEqual("page-range-metadata-test-passage-13-14", passage.unit_id)
        self.assertEqual("passage_fragment", passage.metadata.get("passage_role"))
        self.assertEqual(["range-header", "shared-passage"], passage.stem_block_ids)
        self.assertTrue(passage.metadata.get("supplemental_item"))

        for problem in (p13, p14):
            self.assertEqual("page-range-metadata-test-passage-13-14", problem.metadata.get("passage_group_id"))
            self.assertEqual({"start": 13, "end": 14}, problem.metadata.get("passage_range"))
            self.assertEqual("child_question", problem.metadata.get("passage_role"))
            self.assertEqual(["range-header", "shared-passage"], problem.metadata.get("shared_passage_block_ids"))
            self.assertEqual([13, 14], problem.metadata.get("passage_child_problem_numbers"))
        self.assertEqual(["q13-stem"], p13.stem_block_ids)
        self.assertEqual(["q14-stem"], p14.stem_block_ids)

    def test_set_problem_range_parses_korean_number_suffix_header(self):
        blocks = [
            ContentBlock(
                block_id="range-header",
                block_type=BlockType.STEM,
                bbox=Box(10, 10, 500, 40),
                reading_order=0,
                text="13~14번 다음 글을 읽고 물음에 답하시오."
            ),
            ContentBlock(
                block_id="shared-passage",
                block_type=BlockType.STEM,
                bbox=Box(10, 60, 500, 150),
                reading_order=1,
                text="이 글은 두 문항이 함께 참조하는 긴 지문이다."
            ),
            ContentBlock(
                block_id="q13-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 220, 500, 50),
                reading_order=2,
                text="13. 위 글의 전개 방식으로 적절한 것은?"
            ),
            ContentBlock(
                block_id="q14-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 280, 500, 50),
                reading_order=3,
                text="14. 윗글의 내용과 일치하는 것은?"
            ),
        ]
        page = PageModel(
            page_id="page-korean-suffix-range-test",
            width_px=1000,
            height_px=1000,
            subject=Subject.KOREAN,
            blocks=blocks
        )

        grouped = group_problem_units(page)
        problems = grouped.problems
        p13 = next(problem for problem in problems if problem.metadata.get("problem_number") == 13)
        p14 = next(problem for problem in problems if problem.metadata.get("problem_number") == 14)

        passage = next(problem for problem in problems if problem.metadata.get("passage_role") == "passage_fragment")
        self.assertEqual(3, len(problems))
        self.assertEqual(["range-header", "shared-passage"], passage.stem_block_ids)
        for problem in (p13, p14):
            self.assertEqual("page-korean-suffix-range-test-passage-13-14", problem.metadata.get("passage_group_id"))
            self.assertEqual({"start": 13, "end": 14}, problem.metadata.get("passage_range"))
            self.assertEqual(["range-header", "shared-passage"], problem.metadata.get("shared_passage_block_ids"))
        self.assertEqual(["q13-stem"], p13.stem_block_ids)
        self.assertEqual(["q14-stem"], p14.stem_block_ids)

    def test_set_problem_range_parses_bracketed_korean_suffix_header(self):
        blocks = [
            ContentBlock(
                block_id="range-header",
                block_type=BlockType.STEM,
                bbox=Box(10, 10, 500, 40),
                reading_order=0,
                text="[15~16번] 다음 자료를 보고 물음에 답하시오."
            ),
            ContentBlock(
                block_id="shared-material",
                block_type=BlockType.STEM,
                bbox=Box(10, 60, 500, 150),
                reading_order=1,
                text="두 문항이 공유하는 탐구 자료이다."
            ),
            ContentBlock(
                block_id="q15-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 220, 500, 50),
                reading_order=2,
                text="15. 자료에 대한 설명으로 옳은 것은?"
            ),
            ContentBlock(
                block_id="q16-stem",
                block_type=BlockType.STEM,
                bbox=Box(10, 280, 500, 50),
                reading_order=3,
                text="16. 자료를 바탕으로 추론한 것은?"
            ),
        ]
        page = PageModel(
            page_id="page-bracketed-korean-suffix-range-test",
            width_px=1000,
            height_px=1000,
            subject=Subject.SCIENCE,
            blocks=blocks
        )

        grouped = group_problem_units(page)
        problems = grouped.problems
        p15 = next(problem for problem in problems if problem.metadata.get("problem_number") == 15)
        p16 = next(problem for problem in problems if problem.metadata.get("problem_number") == 16)

        passage = next(problem for problem in problems if problem.metadata.get("passage_role") == "passage_fragment")
        self.assertEqual(3, len(problems))
        self.assertEqual(["range-header", "shared-material"], passage.stem_block_ids)
        for problem in (p15, p16):
            self.assertEqual(
                "page-bracketed-korean-suffix-range-test-passage-15-16",
                problem.metadata.get("passage_group_id"),
            )
            self.assertEqual(["range-header", "shared-material"], problem.metadata.get("shared_passage_block_ids"))
        self.assertEqual(["q15-stem"], p15.stem_block_ids)
        self.assertEqual(["q16-stem"], p16.stem_block_ids)

    def test_document_band_skips_unit_header_and_reads_bare_problem_number(self):
        blocks = [
            ContentBlock(
                block_id="unit-header",
                block_type=BlockType.TITLE,
                bbox=Box(0, 0, 500, 120),
                reading_order=0,
                text="중등 1학년\n1. 기본도형",
                ocr_lines=[
                    OcrLine("중등 1학년", Box(0, 0, 500, 60)),
                    OcrLine("1. 기본도형", Box(0, 60, 500, 60)),
                ],
                metadata={"segmenter": "document-bands", "column_index": 1, "question_band_index": 1},
            ),
            ContentBlock(
                block_id="q21",
                block_type=BlockType.STEM,
                bbox=Box(0, 180, 500, 180),
                reading_order=1,
                text="다음 그림과 같은 직육면체에서 평행한 면의 개수를 구하여라.",
                metadata={"segmenter": "document-bands", "column_index": 1, "question_band_index": 2},
            ),
            ContentBlock(
                block_id="q22",
                block_type=BlockType.STEM,
                bbox=Box(0, 460, 500, 180),
                reading_order=2,
                text="22\n다음 그림과 같은 직육면체에서 꼬인 위치에 있는 모서리의 개수를 구하여라.",
                ocr_lines=[
                    OcrLine("22", Box(0, 0, 500, 60)),
                    OcrLine("다음 그림과 같은 직육면체에서 꼬인 위치에 있는 모서리의 개수를 구하여라.", Box(0, 60, 500, 120)),
                ],
                metadata={"segmenter": "document-bands", "column_index": 1, "question_band_index": 3},
            ),
        ]
        page = PageModel("page-document-band-bare-number", 1000, 1000, Subject.MATH, blocks=blocks)

        grouped = group_problem_units(page)

        self.assertEqual(2, len(grouped.problems))
        self.assertEqual("q21", grouped.problems[0].stem_block_ids[0])
        self.assertEqual(22, grouped.problems[1].metadata.get("problem_number"))
        self.assertEqual("ocr_top_left", grouped.problems[1].metadata.get("problem_number_source"))

    def test_document_band_prompt_starts_unnumbered_math_questions(self):
        blocks = [
            ContentBlock(
                block_id="q23",
                block_type=BlockType.FORMULA,
                bbox=Box(0, 0, 500, 160),
                reading_order=0,
                text="다음 그림에서 점 B, C는 선분 AD의 삼등분점이다. AD의 길이는 몇 cm인지 구하여라.",
                metadata={"segmenter": "document-bands", "column_index": 1, "question_band_index": 1},
            ),
            ContentBlock(
                block_id="q24",
                block_type=BlockType.FORMULA,
                bbox=Box(0, 320, 500, 160),
                reading_order=1,
                text="다음 그림과 같이 각 BOC = 50도일 때 각 AOD의 크기는 몇 도인지 구하여라.",
                metadata={"segmenter": "document-bands", "column_index": 1, "question_band_index": 2},
            ),
            ContentBlock(
                block_id="footer",
                block_type=BlockType.TITLE,
                bbox=Box(0, 900, 500, 80),
                reading_order=2,
                text="윤자매 놀이학습(fillthevoid82.com)",
                metadata={"segmenter": "document-bands", "column_index": 1, "question_band_index": 3},
            ),
        ]
        page = PageModel("page-document-band-prompts", 1000, 1000, Subject.MATH, blocks=blocks)

        grouped = group_problem_units(page)

        self.assertEqual(2, len(grouped.problems))
        self.assertEqual(["q23"], grouped.problems[0].stem_block_ids)
        self.assertEqual(["q24"], grouped.problems[1].stem_block_ids)

    def test_shared_table_before_choices_infers_missing_problem_number(self):
        blocks = [
            ContentBlock(
                block_id="shared-table",
                block_type=BlockType.IMAGE,
                bbox=Box(500, 0, 300, 160),
                reading_order=0,
                text="※ 다음 표를 보고 물음에 답하시오.\n| 이름 | Ted | Eric |",
                metadata={"segmenter": "document-bands", "column_index": 2, "question_band_index": 1},
            ),
            ContentBlock(
                block_id="q4-choices",
                block_type=BlockType.CHOICE,
                bbox=Box(500, 180, 300, 120),
                reading_order=1,
                text="① He's from Canada.\n② He isn't shy.\n③ He is tall.",
                metadata={"segmenter": "document-bands", "column_index": 2, "question_band_index": 2},
            ),
            ContentBlock(
                block_id="q5",
                block_type=BlockType.TITLE,
                bbox=Box(500, 340, 300, 160),
                reading_order=2,
                text="5. 다음은 Ted가 자신과 친구 Eric에 대해 쓴 글이다.",
                metadata={"segmenter": "document-bands", "column_index": 2, "question_band_index": 3},
            ),
        ]
        page = PageModel("page-shared-table-missing-number", 1000, 1000, Subject.ENGLISH, blocks=blocks)

        grouped = group_problem_units(page)

        self.assertEqual(2, len(grouped.problems))
        self.assertEqual(4, grouped.problems[0].metadata.get("problem_number"))
        self.assertEqual("inferred_before_next_number", grouped.problems[0].metadata.get("problem_number_source"))
        self.assertEqual(["shared-table"], grouped.problems[0].figure_block_ids)
        self.assertEqual(["q4-choices"], grouped.problems[0].choice_block_ids)
        self.assertEqual(5, grouped.problems[1].metadata.get("problem_number"))

    def test_unit_header_block_with_embedded_problem_number_is_preserved(self):
        blocks = [
            ContentBlock(
                block_id="header-with-q24",
                block_type=BlockType.STEM,
                bbox=Box(500, 0, 300, 180),
                reading_order=0,
                text="기 단원평가\n위치관계\n윤자매놀이학습(fillthevoid82.com)\n24\n다음 그림에서 l//m일 때, 각 x의 크기는 몇 도인지 구하여라.",
                metadata={"segmenter": "document-bands", "column_index": 2, "question_band_index": 1},
            ),
            ContentBlock(
                block_id="q25",
                block_type=BlockType.STEM,
                bbox=Box(500, 360, 300, 180),
                reading_order=1,
                text="다음 그림과 같이 직사각형 모양의 종이를 접었다. 각 QPR의 크기는 몇 도인지 구하여라.",
                metadata={"segmenter": "document-bands", "column_index": 2, "question_band_index": 2},
            ),
        ]
        page = PageModel("page-header-with-embedded-number", 1000, 1000, Subject.MATH, blocks=blocks)

        grouped = group_problem_units(page)

        self.assertEqual(2, len(grouped.problems))
        self.assertEqual(24, grouped.problems[0].metadata.get("problem_number"))
        self.assertEqual("ocr_internal_line", grouped.problems[0].metadata.get("problem_number_source"))
        self.assertEqual(["header-with-q24"], grouped.problems[0].stem_block_ids)

if __name__ == "__main__":
    unittest.main()
