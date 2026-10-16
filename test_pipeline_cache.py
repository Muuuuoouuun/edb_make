from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from ocr_backend import OCRLine, OCRResult
from pipeline_cache import PipelineCache
from structured_schema import Box, PageModel, Subject


@pytest.mark.parametrize("stable_identity", [False, True])
@pytest.mark.parametrize("damage", ["root", "confidence", "bbox", "metadata", "lines", "empty_lines", "empty_metadata", "nonfinite", "encoding"])
def test_damaged_ocr_cache_is_a_miss_and_can_be_replaced(tmp_path: Path, stable_identity: bool, damage: str) -> None:
    cache = PipelineCache(tmp_path)
    image = Image.new("RGB", (20, 10), "white")
    identity = {"version": "regression-test", "source": "sample"} if stable_identity else None
    result = OCRResult(
        text="1. preserved text",
        confidence=0.9,
        backend_name="noop",
        lines=[OCRLine(text="1. preserved text", confidence=0.9, bbox=Box(0, 0, 20, 10))],
    )
    path = cache.save_ocr_result(image, result, backend_name="noop", cache_identity=identity)
    payload = json.loads(path.read_text())
    if damage == "root":
        payload = []
    elif damage == "confidence":
        payload["confidence"] = "invalid"
    elif damage == "bbox":
        payload["lines"][0]["bbox"] = [1, 2, 3]
    elif damage == "metadata":
        payload["metadata"] = 17
    elif damage == "lines":
        payload["lines"] = "invalid"
    elif damage == "empty_lines":
        payload["lines"] = {}
    elif damage == "empty_metadata":
        payload["metadata"] = []
    elif damage == "nonfinite":
        payload["confidence"] = float("nan")
    path.write_bytes(b"\xff" if damage == "encoding" else json.dumps(payload).encode())

    assert cache.load_ocr_result(image, backend_name="noop", cache_identity=identity) is None
    cache.save_ocr_result(image, result, backend_name="noop", cache_identity=identity)
    loaded = cache.load_ocr_result(image, backend_name="noop", cache_identity=identity)
    assert loaded is not None
    assert loaded.text == result.text
    assert loaded.lines[0].bbox == result.lines[0].bbox


@pytest.mark.parametrize("payload", [b"[]", b"42", b"\xff"])
def test_damaged_ai_repair_cache_is_a_miss(tmp_path: Path, payload: bytes) -> None:
    cache = PipelineCache(tmp_path)
    kwargs = {"page": PageModel(page_id="page-1", width_px=20, height_px=10, subject=Subject.UNKNOWN),
              "provider": "gemini", "model": "test-model", "trigger_reasons": ["test"]}
    path = cache.save_ai_repair(**kwargs, repair_payload={}, response_id=None)
    path.write_bytes(payload)

    assert cache.load_ai_repair(**kwargs) is None
