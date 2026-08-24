from __future__ import annotations

import unittest

from scripts.evaluate_quality_corpus import Observation, ProblemSignature
from scripts.bootstrap_windows_exam_corpus import (
    ExamAsset,
    extract_horaeng_post_links,
    extract_horaeng_problem_pdfs,
    extract_kice_assets,
    extract_printed_ranges,
    expected_question_numbers,
)
from scripts.run_windows_exam_benchmark import score_observation


class TestWindowsExamCorpus(unittest.TestCase):
    def test_extracts_requested_horaeng_posts(self) -> None:
        page = """
        <article><a href="https://horaeng.com/431">
          2025년 3월 고1 모의고사 문제, 답, 해설 – 국어/수학/영어
        </a></article>
        <article><a href="https://horaeng.com/436">
          <strong>2025년 6월 고1 모의고사 문제</strong>, 답, 해설
        </a></article>
        <a href="https://horaeng.com/999">2025년 3월 고2 모의고사 문제</a>
        """

        posts = extract_horaeng_post_links(
            page,
            grade=1,
            year=2025,
            months={3, 6},
        )

        self.assertEqual(
            {
                3: "https://horaeng.com/431",
                6: "https://horaeng.com/436",
            },
            posts,
        )

    def test_extracts_only_problem_pdfs_and_normalizes_hangul(self) -> None:
        page = """
        <a href="/wp-content/uploads/korean.pdf">2025년 고1 국어 문제</a>
        <a href="/wp-content/uploads/korean-answer.pdf">2025년 고1 국어 해설</a>
        <a href="/wp-content/uploads/math.pdf">2025년 고1 수학 문제</a>
        <a href="/wp-content/uploads/english.pdf">2025년 고1 영어 문제</a>
        """

        result = extract_horaeng_problem_pdfs(
            page,
            subjects={"korean", "math", "english"},
        )

        self.assertEqual(
            "https://horaeng.com/wp-content/uploads/korean.pdf",
            result["korean"],
        )
        self.assertEqual(
            "https://horaeng.com/wp-content/uploads/math.pdf",
            result["math"],
        )
        self.assertEqual(
            "https://horaeng.com/wp-content/uploads/english.pdf",
            result["english"],
        )

    def test_extracts_kice_problem_pdf_and_prefers_odd_form(self) -> None:
        page = """
        <table><tr>
          <td>5089364</td><td>2025</td><td>국어</td><td>문제 및 정답</td>
          <td><a onclick="fn_fileDown('odd-seq');" title='국어영역_문제지_홀수형.pdf'></a></td>
          <td><a onclick="fn_fileDown('even-seq');" title='국어영역_문제지_짝수형.pdf'></a></td>
          <td><a onclick="fn_fileDown('answer-seq');" title='국어영역_정답표.pdf'></a></td>
        </tr></table>
        """

        assets = extract_kice_assets(
            page,
            years={2025},
            subjects={"korean"},
        )

        self.assertEqual(1, len(assets))
        self.assertEqual("kice-2025-korean", assets[0].case_id)
        self.assertTrue(assets[0].download_url.endswith("fileSeq=odd-seq"))

    def test_extracts_unique_printed_ranges_with_bounds(self) -> None:
        text = """
        [1 ~ 3] 다음 글을 읽고 물음에 답하시오.
        (18∼21) 다음 글을 읽고 물음에 답하시오.
        [18～21] duplicate typography
        [22-22] single question is not a passage range
        [44~48] outside the document
        """

        ranges = extract_printed_ranges(text, question_max=45)

        self.assertEqual([[1, 3], [18, 21]], ranges)

    def test_printed_ranges_exclude_independent_group_instructions(self) -> None:
        text = """
        [16 ~ 17] \ub2e4\uc74c\uc744 \ub4e3\uace0, \ubb3c\uc74c\uc5d0 \ub2f5\ud558\uc2dc\uc624.
        [31 ~ 34] \ub2e4\uc74c \ube48\uce78\uc5d0 \ub4e4\uc5b4\uac08 \ub9d0\ub85c \uac00\uc7a5 \uc801\uc808\ud55c \uac83\uc744 \uace0\ub974\uc2dc\uc624.
        [41 ~ 42] \ub2e4\uc74c \uae00\uc744 \uc77d\uace0, \ubb3c\uc74c\uc5d0 \ub2f5\ud558\uc2dc\uc624.
        """

        self.assertEqual(
            [[16, 17], [41, 42]],
            extract_printed_ranges(text, question_max=45),
        )

    def test_kice_expected_questions_include_forms_and_electives(self) -> None:
        def asset(subject: str, question_max: int) -> ExamAsset:
            return ExamAsset(
                case_id=f"kice-2026-{subject}",
                provider="kice",
                source_page_url="https://example.test",
                download_url="https://example.test/file.pdf",
                subject=subject,
                level="csat",
                year=2026,
                month=None,
                expected_question_max=question_max,
            )

        korean, korean_forms = expected_question_numbers(
            asset("korean", 45),
            page_count=40,
        )
        math, math_forms = expected_question_numbers(
            asset("math", 30),
            page_count=40,
        )
        english, english_forms = expected_question_numbers(
            asset("english", 45),
            page_count=16,
        )

        self.assertEqual((112, 2, 4), (len(korean), korean_forms, korean.count(45)))
        self.assertEqual((92, 2, 6), (len(math), math_forms, math.count(30)))
        self.assertEqual((90, 2, 2), (len(english), english_forms, english.count(45)))

    def test_scores_complete_observation_at_100(self) -> None:
        signatures = tuple(
            ProblemSignature(
                number=number,
                source_page_id="a" * 64,
                bbox_sha256="b" * 64,
                crop_sha256="c" * 64,
                render_sha256="d" * 64,
                visual_sha256="e" * 64,
                content_sha256="f" * 64,
                choice_count=5,
                choice_order=(1, 2, 3, 4, 5),
                artifact_valid=True,
                artifact_size_bytes=1024,
            )
            for number in (1, 2)
        )
        observation = Observation(
            question_numbers=(1, 2),
            passage_ranges=((1, 2),),
            preflight_issue_count=0,
            manual_review_count=0,
            review_population=2,
            processing_ms=100,
            problem_signatures=signatures,
        )

        score = score_observation(
            case_id="case-1",
            subject="korean",
            level="high1",
            observation=observation,
            expected_questions=[1, 2],
            expected_passages=[[1, 2]],
            processing_ms=100,
            output_dir=__import__("pathlib").Path("."),
        )

        self.assertEqual(100.0, score.score)
        self.assertEqual((), score.failures)

    def test_score_penalizes_missing_question_and_artifact(self) -> None:
        observation = Observation(
            question_numbers=(1,),
            passage_ranges=((1, 2),),
            preflight_issue_count=0,
            manual_review_count=0,
            review_population=1,
            processing_ms=100,
            problem_signatures=(),
        )

        score = score_observation(
            case_id="case-1",
            subject="korean",
            level="high1",
            observation=observation,
            expected_questions=[1, 2],
            expected_passages=[[1, 2]],
            processing_ms=100,
            output_dir=__import__("pathlib").Path("."),
        )

        self.assertEqual(72.5, score.score)
        self.assertIn("missing_questions", score.failures)
        self.assertIn("missing_or_invalid_artifacts", score.failures)


if __name__ == "__main__":
    unittest.main()
