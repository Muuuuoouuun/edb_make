from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.bootstrap_windows_csat_corpus import (
    AREA_LABEL_TO_KEY,
    CSAT_SUBJECT_SPECS,
    CorpusBootstrapError,
    KiceAreaAsset,
    _archive_subject_payloads,
    _case_record,
    build_catalog,
    expected_questions,
    extract_kice_area_assets,
)


def _spec(subject: str):
    return next(item for item in CSAT_SUBJECT_SPECS if item.subject == subject)


def _zip_payload(files: dict[str, bytes]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def test_all_current_csat_subjects_are_unique_and_cover_eight_areas():
    subjects = [spec.subject for spec in CSAT_SUBJECT_SPECS]
    assert len(subjects) == 36
    assert len(set(subjects)) == 36
    assert len(AREA_LABEL_TO_KEY) == 8
    assert {spec.area_label for spec in CSAT_SUBJECT_SPECS} == {
        "국어",
        "수학",
        "영어",
        "한국사",
        "사회탐구",
        "과학탐구",
        "직업탐구",
        "제2외국어/한문",
    }


def test_kice_row_parser_selects_pdf_and_zip_problem_files():
    page = """
    <table>
      <tr><td>1234567 2026 국어 문제</td>
        <td><a onclick="fn_fileDown('pdf-problem')" title='2026_국어_문제지.pdf'>문제</a></td>
        <td><a onclick="fn_fileDown('pdf-answer')" title='2026_국어_정답.pdf'>정답</a></td>
      </tr>
      <tr><td>2345678 2026 사회탐구 문제</td>
        <td><a onclick="fn_fileDown('zip-problem')" title='2026_사회탐구_문제지.zip'>문제</a></td>
        <td><a onclick="fn_fileDown('zip-answer')" title='2026_사회탐구_정답.zip'>정답</a></td>
      </tr>
    </table>
    """
    assets = extract_kice_area_assets(page, years={2026})
    assert [(asset.area, asset.file_seq) for asset in assets] == [
        ("korean", "pdf-problem"),
        ("social", "zip-problem"),
    ]


def test_archive_member_matching_is_complete_and_ignores_unrelated_pdfs():
    specs = (
        _spec("science-physics-1"),
        _spec("science-chemistry-1"),
    )
    payload = _zip_payload(
        {
            "nested/01 물리학Ⅰ 문제지.pdf": b"%PDF-physics",
            "02_화학Ⅰ_문제.pdf": b"%PDF-chemistry",
            "정답.pdf": b"%PDF-answer",
            "../outside.pdf": b"%PDF-unrelated",
        }
    )
    found = _archive_subject_payloads(payload, specs)
    assert set(found) == {"science-physics-1", "science-chemistry-1"}
    assert found["science-physics-1"][1] == b"%PDF-physics"
    assert found["science-chemistry-1"][1] == b"%PDF-chemistry"


def test_archive_member_matching_rejects_missing_subject():
    with pytest.raises(CorpusBootstrapError, match="missing subjects"):
        _archive_subject_payloads(
            _zip_payload({"01 물리학Ⅰ 문제지.pdf": b"%PDF-physics"}),
            (_spec("science-physics-1"), _spec("science-chemistry-1")),
        )


@pytest.mark.parametrize(
    ("subject", "page_count", "expected_count", "form_count"),
    [
        ("korean-history", 8, 40, 2),
        ("social-life-ethics", 4, 20, 1),
        ("foreign-german-1", 4, 30, 1),
        ("english", 16, 90, 2),
    ],
)
def test_expected_question_multisets_follow_official_forms(
    subject: str,
    page_count: int,
    expected_count: int,
    form_count: int,
):
    questions, actual_forms = expected_questions(_spec(subject), page_count=page_count)
    assert len(questions) == expected_count
    assert actual_forms == form_count


def test_build_catalog_requires_all_36_subjects_for_each_year(tmp_path: Path):
    cases = [
        {"subject": spec.subject, "year": 2026}
        for spec in CSAT_SUBJECT_SPECS
    ]
    path = build_catalog(output_root=tmp_path, cases=cases, years={2026})
    text = path.read_text(encoding="utf-8")
    assert '"subject_count_per_year": 36' in text
    assert '"average_min": 93.0' in text
    assert '"case_min": 89.0' in text
    assert '"whole_resolution_valid_rate_min": 1.0' in text
    assert '"within_time_budget_required": true' in text

    with pytest.raises(CorpusBootstrapError, match="coverage mismatch"):
        build_catalog(output_root=tmp_path / "missing", cases=cases[:-1], years={2026})


def test_case_record_includes_problem_and_whole_time_budgets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        "scripts.bootstrap_windows_csat_corpus._pdf_info",
        lambda _payload, _spec: (4, []),
    )
    asset = KiceAreaAsset(
        year=2026,
        area="social",
        area_label="사회탐구",
        board_seq="1234567",
        source_page_url="https://example.test/page",
        file_seq="file",
        file_name="문제지.zip",
    )
    record = _case_record(
        area_asset=asset,
        spec=_spec("social-life-ethics"),
        payload=b"%PDF-test",
        local_path=tmp_path / "source.pdf",
        archive_member_name="생활과 윤리.pdf",
        container_sha256="a" * 64,
    )
    assert record["processing_ms_max"] == 18_000
    assert record["whole_processing_ms_max"] == 19_000
    assert record["resolution_gate"]["problem_crop_min_height"] == 64
    assert record["resolution_gate"]["problem_bbox_scale_min"] == 0.80
    assert record["expected"]["whole_page_count"] == 4
