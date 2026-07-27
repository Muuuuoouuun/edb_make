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

    def test_accepts_shared_listening_but_rejects_grouped_independent_tasks(self) -> None:
        self.assertEqual(
            (16, 17),
            extract_shared_passage_range(
                "[16 ~ 17] \ub2e4\uc74c\uc744 \ub4e3\uace0, \ubb3c\uc74c\uc5d0 \ub2f5\ud558\uc2dc\uc624."
            ),
        )
        for text in (
            "[31 ~ 34] \ub2e4\uc74c \ube48\uce78\uc5d0 \ub4e4\uc5b4\uac08 \ub9d0\ub85c \uac00\uc7a5 \uc801\uc808\ud55c \uac83\uc744 \uace0\ub974\uc2dc\uc624.",
            "[36 ~ 37] \uc8fc\uc5b4\uc9c4 \uae00 \ub2e4\uc74c\uc5d0 \uc774\uc5b4\uc9c8 \uae00\uc758 \uc21c\uc11c\ub85c \uac00\uc7a5 \uc801\uc808\ud55c \uac83\uc744 \uace0\ub974\uc2dc\uc624.",
            "[38 ~ 39] \uae00\uc758 \ud750\ub984\uc73c\ub85c \ubcf4\uc544, \uc8fc\uc5b4\uc9c4 \ubb38\uc7a5\uc774 \ub4e4\uc5b4\uac00\uae30\uc5d0 \uac00\uc7a5 \uc801\uc808\ud55c \uacf3\uc744 \uace0\ub974\uc2dc\uc624.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_shared_passage_range(text))


if __name__ == "__main__":
    unittest.main()
