from __future__ import annotations

import unittest

from passage_detection import (
    extract_shared_passage_range,
    parse_shared_passage_range_header,
    shared_passage_cue_language,
)


class TestPassageDetection(unittest.TestCase):
    def test_accepts_korean_and_english_shared_passage_headers(self) -> None:
        cases = {
            "[18~21] 다음 글을 읽고 물음에 답하시오.": (18, 21),
            "[15~16번] 다음 자료를 보고 물음에 답하시오.": (15, 16),
            "40번부터 41번까지는 다음 글을 읽고 물음에 답하시오.": (40, 41),
            "문항 24~26은 다음 자료를 보고 물음에 답하시오.": (24, 26),
            "[1~3] 다음은 학생의 발표이다. 물음에 답하시오.": (1, 3),
            "[4~6] 다음은 학생회 토의 중 일부이다. 물음에 답하시오.": (4, 6),
            "[28~30] 다음은 작문 상황과 이를 바탕으로 작성한 초고이다. 물음에 답하시오.": (28, 30),
            "[31-34] Read the following passage and answer the questions.": (31, 34),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, extract_shared_passage_range(text))

    def test_rejects_ranges_without_a_shared_material_and_task_cue(self) -> None:
        cases = [
            "[1~3]에서 x의 범위를 구하시오.",
            "[18~21] 다음 중 옳은 것은?",
            "[18~21] 지문에 포함된 숫자의 범위이다.",
            "문항 24~26의 정답 개수를 더하시오.",
            "[31-34] passage index values",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(extract_shared_passage_range(text))

    def test_header_exposes_language_and_detection_confidence(self) -> None:
        header = parse_shared_passage_range_header(
            "[4~6] 다음 대화를 읽고 물음에 답하시오."
        )
        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual("ko", header.cue_language)
        self.assertGreaterEqual(header.confidence, 0.95)
        self.assertEqual("en", shared_passage_cue_language(
            "[4-6] Read the following text and answer the questions."
        ))


if __name__ == "__main__":
    unittest.main()
