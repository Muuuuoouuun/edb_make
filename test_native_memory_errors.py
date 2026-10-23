from pathlib import Path
from unittest.mock import patch

import cv2
import pytest
from PIL import Image

import app_server
import build_problem_board_edb as board
import preprocess
from structured_schema import Subject


def native_cv_error(code=-4):
    error = cv2.error(
        "OpenCV(4.13.0) error: (-4:Insufficient memory) "
        "Failed to allocate 268435456 bytes in function 'OutOfMemoryError'"
    )
    error.code = code
    return error


def prepared_pages(tmp_path, count=3, first_number=1):
    source = tmp_path / "page.png"
    Image.new("RGB", (32, 48), "white").save(source)
    return [preprocess.PreparedPage(
        f"page-{number}", str(source), number, None, (32, 48),
    ) for number in range(first_number, first_number + count)]


def build_pages(pages, segment):
    with (
        patch.object(board, "prepare_source_pages", return_value=pages),
        patch.object(board, "build_page_models_for_prepared_pages", side_effect=segment),
    ):
        return board.build_pages(
            Path("수업.pdf"), subject=Subject.UNKNOWN, ocr_mode="none",
            ai_fallback_config=None, pdf_dpi=72, detect_perspective=False,
            deskew=False, crop_margins=False, max_dimension=None,
        )


@pytest.mark.parametrize("retain_native_code", [True, False])
def test_native_opencv_oom_is_classified_even_without_python_memoryerror(retain_native_code):
    error = native_cv_error()
    if not retain_native_code:
        error.code = None
    assert not isinstance(error, (MemoryError, RuntimeError))
    assert preprocess._is_pdf_resource_error(error)
    response = app_server._export_error_payload(error)
    assert response["code"] == "processing_memory_exhausted"
    assert response["errorKind"] == "memory_exhausted"
    assert app_server._export_error_status(error, response) == app_server.HTTPStatus.SERVICE_UNAVAILABLE


def test_wrapped_native_oom_retains_file_and_page_context():
    error = preprocess.PdfPageProcessingError("큰 수업.pdf", 21, 23, native_cv_error())
    response = app_server._export_error_payload(error)
    assert response["code"] == "processing_memory_exhausted"
    assert response["sourceFile"] == "큰 수업.pdf"
    assert (response["pageStart"], response["pageEnd"]) == (21, 23)


def test_non_memory_opencv_error_does_not_become_capacity_error_from_its_text():
    # The native status wins over wording that can include a source filename
    # or diagnostics about an unrelated allocation. Assertion/argument errors
    # require debugging, not a smaller upload.
    error = native_cv_error(code=-215)
    assert not preprocess._is_pdf_resource_error(error)
    response = app_server._export_error_payload(error)
    assert response["code"] == "app_internal_error"
    assert response["errorKind"] == "app_internal_error"


def test_native_oom_retries_segmentation_as_smaller_batches_without_page_loss(tmp_path):
    pages = prepared_pages(tmp_path)
    attempts = []

    def segment(batch, **_kwargs):
        # A failed batch must release decoded rasters before the smaller retry.
        assert all(page._image is None for page in pages)
        attempts.append(len(batch))
        for page in batch:
            page.image.load()
        if len(batch) > 1:
            raise native_cv_error()
        return board._build_page_as_is_models(batch, subject=Subject.UNKNOWN)

    actual, models = build_pages(pages, segment)
    assert attempts == [3, 1, 2, 1, 1]
    assert [model.page_id for model in models] == [page.page_id for page in pages]
    assert all(page._image is None for page in actual)


def test_single_page_native_oom_reports_resource_limit_and_exact_page(tmp_path):
    pages = prepared_pages(tmp_path, count=1, first_number=7)
    with pytest.raises(preprocess.PdfPageProcessingError) as caught:
        build_pages(pages, lambda *_args, **_kwargs: (_ for _ in ()).throw(native_cv_error()))
    error = caught.value
    assert error.code == "pdf_resource_exhausted"
    assert (error.source_file, error.page_start, error.page_end) == ("수업.pdf", 7, 7)
    assert isinstance(error.cause, cv2.error)
    assert pages[0]._image is None


def test_non_memory_opencv_failure_is_not_retried_as_a_smaller_pdf(tmp_path):
    pages = prepared_pages(tmp_path)
    error = native_cv_error(code=-215)
    attempts = []

    def segment(batch, **_kwargs):
        attempts.append(len(batch))
        raise error

    with pytest.raises(cv2.error) as caught:
        build_pages(pages, segment)
    assert caught.value is error
    assert attempts == [3]
    assert all(page._image is None for page in pages)
