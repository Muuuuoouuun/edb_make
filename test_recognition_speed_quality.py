def test_pdf_text_marker_blocks_skip_backend_ocr(monkeypatch, tmp_path):
    from PIL import Image

    import build_structured_page_json as pipeline
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage
    from structured_schema import Subject

    backend_factory_calls = []

    source = tmp_path / "page.png"
    Image.new("RGB", (600, 800), "white").save(source)
    prepared = PreparedPage(
        page_id="pdf-page-001",
        source_path=str(source),
        page_number=1,
        image=Image.open(source).convert("RGB"),
        original_size=(600, 800),
        metadata={
            "source_type": "pdf",
            "pdf_problem_markers": [
                {
                    "number": 1,
                    "text": "1. problem stem",
                    "bbox": {"left": 60, "top": 120, "right": 90, "bottom": 140},
                }
            ],
        },
    )
    def fail_if_backend_is_created(mode):
        backend_factory_calls.append(mode)
        raise AssertionError("trusted PDF marker page should not create OCR backend")

    monkeypatch.setattr(pipeline, "create_ocr_backend", fail_if_backend_is_created)

    page = pipeline.build_page_model(
        prepared,
        subject=Subject.MATH,
        ocr_mode="gemini",
        ai_config=build_ai_fallback_config(mode="off"),
    )

    assert len(page.problems) == 1
    assert page.blocks[0].text == "1."
    assert page.blocks[0].metadata["ocr_skipped_reason"] == "trusted_pdf_text_marker"
    assert page.blocks[0].metadata["ocr_backend"] == "pdf_text_marker"
    assert backend_factory_calls == []
    assert page.metadata["ai_stages"]["ocr"]["status"] == "skipped"
    assert page.metadata["ai_stages"]["ocr"]["skipped_block_count"] == 1


def test_marker_like_block_uses_backend_when_page_segmenter_is_not_pdf_markers(monkeypatch, tmp_path):
    from PIL import Image

    import build_structured_page_json as pipeline
    from ocr_backend import OCRResult
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage
    from structured_schema import BlockType, Box, ContentBlock, PageModel, Subject

    class CountingBackend:
        engine_name = "fake_marker_guardrail"

        def __init__(self):
            self.calls = 0

        def recognize(self, image):
            self.calls += 1
            text = "recognized by backend"
            return OCRResult(
                text=text,
                confidence=0.91,
                backend_name=self.engine_name,
                metadata={
                    "empty_text": False,
                    "line_count": 0,
                    "text_length": len(text),
                },
            )

    source = tmp_path / "page.png"
    image = Image.new("RGB", (600, 800), "white")
    image.save(source)
    prepared = PreparedPage(
        page_id="marker-like-page-001",
        source_path=str(source),
        page_number=1,
        image=image,
        original_size=image.size,
        metadata={},
    )
    marker_like_block = ContentBlock(
        block_id="marker-like-page-001-block-001",
        block_type=BlockType.TITLE,
        bbox=Box.from_points(60, 120, 300, 240),
        reading_order=0,
        text="1.",
        confidence=1.0,
        metadata={
            "segmenter": "pdf-text-markers",
            "force_image_record": True,
            "force_problem_start": True,
            "problem_number": 1,
            "problem_number_source": "pdf_text_marker",
        },
    )
    segmented_page = PageModel(
        page_id=prepared.page_id,
        width_px=image.width,
        height_px=image.height,
        subject=Subject.MATH,
        source_path=str(source),
        blocks=[marker_like_block],
        metadata={"segmenter": "document"},
    )
    backend = CountingBackend()

    monkeypatch.setattr(pipeline, "create_ocr_backend", lambda _mode: backend)
    monkeypatch.setattr(
        pipeline,
        "segment_page",
        lambda prepared_page, page_id, subject: segmented_page,
    )

    page = pipeline.build_page_model(
        prepared,
        subject=Subject.MATH,
        ocr_mode="fake_marker_guardrail",
        ai_config=build_ai_fallback_config(mode="off"),
    )

    assert backend.calls == 1
    assert page.blocks[0].text == "recognized by backend"
    assert page.blocks[0].metadata["ocr_backend"] == "fake_marker_guardrail"


def test_ocr_cache_uses_stable_index_and_can_fallback_to_legacy_image_hash(tmp_path):
    from PIL import Image

    from ocr_backend import OCRResult
    from pipeline_cache import PipelineCache

    cache = PipelineCache(tmp_path / "cache")
    image = Image.new("RGB", (40, 20), "white")
    result = OCRResult(text="cached", confidence=0.9, backend_name="gemini")
    identity = {
        "version": "ocr_block_identity_v1",
        "source": {"fingerprint": "source-a"},
        "block": {"id": "block-1"},
    }

    payload_path = cache.save_ocr_result(
        image,
        result,
        backend_name="gemini",
        cache_identity=identity,
    )

    assert "ocr" in payload_path.parts
    assert "ocr_index" not in payload_path.parts

    changed_crop = Image.new("RGB", (41, 20), "white")
    loaded = cache.load_ocr_result(
        changed_crop,
        backend_name="gemini",
        cache_identity=identity,
    )
    assert loaded is not None
    assert loaded.text == "cached"
    assert loaded.metadata["cache_key_kind"] == "stable_index"

    legacy_cache = PipelineCache(tmp_path / "legacy")
    legacy_cache.save_ocr_result(image, result, backend_name="gemini")
    legacy_loaded = legacy_cache.load_ocr_result(
        image,
        backend_name="gemini",
        cache_identity={"version": "missing"},
    )
    assert legacy_loaded is not None
    assert legacy_loaded.text == "cached"
    assert legacy_loaded.metadata["cache_key_kind"] == "image_hash"


def test_ocr_cache_identity_uses_original_source_normalization_and_bucketed_bbox(tmp_path):
    from PIL import Image

    import build_structured_page_json as pipeline
    from preprocess import PreparedPage
    from structured_schema import BlockType, Box, ContentBlock

    original = tmp_path / "source.pdf"
    original.write_bytes(b"%PDF-1.7\nstable-source\n")
    normalized = tmp_path / "normalized.png"
    image = Image.new("RGB", (200, 100), "white")
    image.save(normalized)

    prepared = PreparedPage(
        page_id="source-page-003",
        source_path=str(normalized),
        page_number=3,
        image=image,
        original_size=image.size,
        metadata={
            "original_source_path": str(original),
            "normalized_path": str(normalized),
            "source_type": "pdf",
            "dpi": 144,
            "deskewed": False,
            "margin_cropped": True,
            "margin_crop_box": {"left": 2, "top": 4, "right": 202, "bottom": 104},
            "backend_latency_ms": 999,
        },
    )
    block = ContentBlock(
        block_id="source-page-003-block-001",
        block_type=BlockType.STEM,
        bbox=Box.from_points(10.2, 20.1, 110.6, 60.7),
        reading_order=0,
        text="do not key from OCR text",
        confidence=0.2,
        metadata={"ocr_backend": "gemini", "ocr_latency_ms": 123},
    )
    shifted_block = ContentBlock(
        block_id=block.block_id,
        block_type=block.block_type,
        bbox=Box.from_points(10.9, 21.1, 111.4, 61.4),
        reading_order=block.reading_order,
        text="changed OCR text",
        confidence=0.99,
        metadata={"ocr_backend": "paddleocr", "ocr_latency_ms": 456},
    )

    identity = pipeline._build_ocr_cache_identity(prepared, block)
    shifted_identity = pipeline._build_ocr_cache_identity(prepared, shifted_block)
    serialized = str(identity)

    assert identity["source"]["path"] == str(original.resolve())
    assert identity["page"]["number"] == 3
    assert identity["page"]["size"] == {"width": 200, "height": 100}
    assert identity["normalization"]["dpi"] == 144
    assert identity["block"]["bbox_bucket_px"] == shifted_identity["block"]["bbox_bucket_px"]
    assert "do not key from OCR text" not in serialized
    assert "backend_latency" not in serialized
    assert "ocr_latency" not in serialized


def test_ocr_cache_identity_ignores_segmentation_labels_when_bbox_bucket_matches(tmp_path):
    from PIL import Image

    import build_structured_page_json as pipeline
    from preprocess import PreparedPage
    from structured_schema import BlockType, Box, ContentBlock

    source = tmp_path / "source.png"
    image = Image.new("RGB", (240, 160), "white")
    image.save(source)
    prepared = PreparedPage(
        page_id="page-001",
        source_path=str(source),
        page_number=1,
        image=image,
        original_size=image.size,
        metadata={"original_source_path": str(source)},
    )
    first = ContentBlock(
        block_id="page-001-block-001",
        block_type=BlockType.STEM,
        bbox=Box.from_points(20.1, 28.1, 120.2, 80.2),
        reading_order=0,
    )
    relabeled = ContentBlock(
        block_id="page-001-block-009",
        block_type=BlockType.TITLE,
        bbox=Box.from_points(20.4, 29.9, 120.1, 80.4),
        reading_order=8,
    )

    assert pipeline._build_ocr_cache_identity(prepared, first) == pipeline._build_ocr_cache_identity(prepared, relabeled)


def test_build_page_model_passes_stable_cache_identity_to_primary_and_escalated_ocr(
    monkeypatch,
    tmp_path,
):
    from PIL import Image

    import build_structured_page_json as pipeline
    from ocr_backend import OCRResult
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage
    from structured_schema import BlockType, Box, ContentBlock, PageModel, Subject

    class PrimaryBackend:
        engine_name = "primary_ocr"

        def recognize(self, image):
            return OCRResult(text="", confidence=0.1, backend_name=self.engine_name)

    class EscalationBackend:
        engine_name = "gemini"

        def recognize(self, image):
            return OCRResult(text="escalated", confidence=0.95, backend_name=self.engine_name)

    class RecordingCache:
        def __init__(self):
            self.root_dir = tmp_path / "cache"
            self.load_calls = []
            self.save_calls = []

        def load_ocr_result(self, image, *, backend_name, cache_identity=None):
            self.load_calls.append((backend_name, cache_identity))
            return None

        def save_ocr_result(self, image, result, *, backend_name, cache_identity=None):
            self.save_calls.append((backend_name, cache_identity))
            return self.root_dir / "ocr" / backend_name / "payload.json"

    source = tmp_path / "page.png"
    image = Image.new("RGB", (180, 120), "white")
    image.save(source)
    prepared = PreparedPage(
        page_id="image-page-001",
        source_path=str(source),
        page_number=1,
        image=image,
        original_size=image.size,
        metadata={"original_source_path": str(source), "deskewed": False},
    )
    block = ContentBlock(
        block_id="image-page-001-block-001",
        block_type=BlockType.STEM,
        bbox=Box.from_points(10, 10, 90, 60),
        reading_order=0,
    )
    segmented_page = PageModel(
        page_id=prepared.page_id,
        width_px=image.width,
        height_px=image.height,
        subject=Subject.MATH,
        source_path=str(source),
        blocks=[block],
        metadata={"segmenter": "document"},
    )
    cache = RecordingCache()

    monkeypatch.setattr(pipeline, "create_ocr_backend", lambda _mode: PrimaryBackend())
    monkeypatch.setattr(
        pipeline,
        "_maybe_build_gemini_escalation",
        lambda **_kwargs: EscalationBackend(),
    )
    monkeypatch.setattr(
        pipeline,
        "segment_page",
        lambda prepared_page, page_id, subject: segmented_page,
    )

    page = pipeline.build_page_model(
        prepared,
        subject=Subject.MATH,
        ocr_mode="primary_ocr",
        ai_config=build_ai_fallback_config(mode="off", threshold=0.72),
        cache=cache,
    )

    assert page.blocks[0].text == "escalated"
    assert page.blocks[0].metadata["ocr_stage"] == "primary_ocr"
    assert page.blocks[0].metadata["ocr_escalation_attempted"] is True
    assert [call[0] for call in cache.load_calls] == ["primary_ocr", "gemini_escalated"]
    assert [call[0] for call in cache.save_calls] == ["primary_ocr", "gemini_escalated"]
    assert all(call[1] is not None for call in cache.load_calls + cache.save_calls)
    assert cache.load_calls[0][1] == cache.save_calls[0][1]
    assert cache.load_calls[1][1] == cache.save_calls[1][1]
    assert page.metadata["ai_stages"]["ocr"]["status"] == "used"
    assert page.metadata["ai_stages"]["ocr"]["api_call_block_count"] == 1
    assert page.metadata["ai_stages"]["ocr_escalation"]["attempted_block_count"] == 1
    assert page.metadata["ai_stages"]["ocr_escalation"]["applied_block_count"] == 1


def test_block_worker_count_defaults_and_env_override(monkeypatch):
    import build_structured_page_json as pipeline

    monkeypatch.delenv("EDB_RECOGNITION_BLOCK_WORKERS", raising=False)
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 16)

    assert (
        pipeline.resolve_block_ocr_worker_count(
            5,
            ocr_mode="none",
            backend_name="none",
        )
        == 1
    )
    assert (
        pipeline.resolve_block_ocr_worker_count(
            10,
            ocr_mode="gemini",
            backend_name="gemini",
        )
        == 3
    )
    assert (
        pipeline.resolve_block_ocr_worker_count(
            10,
            ocr_mode="auto",
            backend_name="gemini",
        )
        == 3
    )
    assert (
        pipeline.resolve_block_ocr_worker_count(
            20,
            ocr_mode="paddleocr",
            backend_name="paddleocr",
        )
        == 8
    )
    assert (
        pipeline.resolve_block_ocr_worker_count(
            4,
            ocr_mode="paddleocr",
            backend_name="paddleocr",
        )
        == 4
    )

    monkeypatch.setenv("EDB_RECOGNITION_BLOCK_WORKERS", "6")
    assert (
        pipeline.resolve_block_ocr_worker_count(
            10,
            ocr_mode="gemini",
            backend_name="gemini",
        )
        == 6
    )

    monkeypatch.setenv("EDB_RECOGNITION_BLOCK_WORKERS", "100000")
    assert (
        pipeline.resolve_block_ocr_worker_count(
            2,
            ocr_mode="gemini",
            backend_name="gemini",
        )
        == 2
    )

    monkeypatch.setenv("EDB_RECOGNITION_BLOCK_WORKERS", "bogus")
    assert (
        pipeline.resolve_block_ocr_worker_count(
            10,
            ocr_mode="gemini",
            backend_name="gemini",
        )
        == 3
    )

    monkeypatch.setenv("EDB_RECOGNITION_BLOCK_WORKERS", "0")
    assert (
        pipeline.resolve_block_ocr_worker_count(
            0,
            ocr_mode="gemini",
            backend_name="gemini",
        )
        == 1
    )


def test_gemini_ocr_model_not_found_falls_back_without_retry_sleep(monkeypatch):
    import json
    from io import BytesIO
    from urllib.error import HTTPError

    from PIL import Image

    import ocr_backend

    urls = []
    sleep_calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            response_text = json.dumps(
                {
                    "text": "recognized",
                    "block_type": "stem",
                    "confidence": 0.96,
                    "lines": ["recognized"],
                }
            )
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": response_text}],
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(req, timeout):
        urls.append(req.full_url)
        if "gemini-3.1-pro-preview" in req.full_url:
            raise HTTPError(
                req.full_url,
                404,
                "model not found",
                hdrs=None,
                fp=BytesIO(b"model not found"),
            )
        return FakeResponse()

    monkeypatch.setattr(ocr_backend.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        ocr_backend.time,
        "sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    monkeypatch.delenv(ocr_backend.GEMINI_OCR_MODEL_ENV, raising=False)
    backend = ocr_backend.GeminiOCRBackend(
        model="gemini-3.1-pro-preview",
        api_key="test-key",
        max_retries=1,
    )
    result = backend.recognize(Image.new("RGB", (240, 120), "white"))

    assert result.text == "recognized"
    assert sleep_calls == []
    assert sum("gemini-3.1-pro-preview" in url for url in urls) == 1
    assert any("gemini-2.5-pro" in url for url in urls)
    assert result.metadata["model"] == "gemini-2.5-pro"


def test_gemini_ocr_quota_error_does_not_retry_or_fallback(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError

    from PIL import Image

    import ocr_backend

    urls = []
    sleep_calls = []
    error_body = (
        b'{"error":{"status":"RESOURCE_EXHAUSTED",'
        b'"message":"Your prepayment credits are depleted."}}'
    )

    def fake_urlopen(req, timeout):
        urls.append(req.full_url)
        raise HTTPError(
            req.full_url,
            429,
            "quota exhausted",
            hdrs=None,
            fp=BytesIO(error_body),
        )

    monkeypatch.setattr(ocr_backend.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        ocr_backend.time,
        "sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    monkeypatch.delenv(ocr_backend.GEMINI_OCR_MODEL_ENV, raising=False)
    backend = ocr_backend.GeminiOCRBackend(api_key="test-key", max_retries=1)
    result = backend.recognize(Image.new("RGB", (240, 120), "white"))

    assert result.text == ""
    assert sleep_calls == []
    assert sum("gemini-3.5-flash" in url for url in urls) == 1
    assert not any("gemini-2.5-pro" in url for url in urls)
    assert "RESOURCE_EXHAUSTED" in result.metadata["error"]
    assert result.metadata["model_attempts"] == [
        {
            "model": "gemini-3.5-flash",
            "status": "error",
            "error": (
                'Gemini request failed with HTTP 429: {"error":{"status":"RESOURCE_EXHAUSTED",'
                '"message":"Your prepayment credits are depleted."}}'
            ),
        }
    ]


def test_gemini_ocr_model_can_be_overridden_with_flash(monkeypatch):
    import json
    from PIL import Image

    import ocr_backend

    urls: list[str] = []
    payloads: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            response_text = json.dumps(
                {
                    "text": "빠른 인식",
                    "block_type": "stem",
                    "confidence": 0.91,
                    "lines": ["빠른 인식"],
                },
                ensure_ascii=False,
            )
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": response_text}],
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(req, timeout):
        urls.append(req.full_url)
        payloads.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setenv(ocr_backend.GEMINI_OCR_MODEL_ENV, "gemini-3.5-flash")
    monkeypatch.setattr(ocr_backend.urllib.request, "urlopen", fake_urlopen)

    backend = ocr_backend.GeminiOCRBackend(api_key="test-key", max_retries=0)
    result = backend.recognize(Image.new("RGB", (240, 120), "white"))

    assert backend.model == "gemini-3.5-flash"
    assert result.text == "빠른 인식"
    assert result.metadata["model"] == "gemini-3.5-flash"
    assert result.metadata["thinking_level"] == "low"
    assert any("gemini-3.5-flash" in url for url in urls)
    assert not any("gemini-2.5-pro" in url for url in urls)
    assert payloads[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_default_gemini_ocr_model_is_flash_with_low_thinking(monkeypatch):
    import ocr_backend

    monkeypatch.delenv(ocr_backend.GEMINI_OCR_MODEL_ENV, raising=False)
    monkeypatch.delenv(ocr_backend.GEMINI_OCR_THINKING_LEVEL_ENV, raising=False)
    backend = ocr_backend.GeminiOCRBackend(api_key="test-key")

    assert backend.model == "gemini-3.5-flash"
    assert backend.thinking_level == "low"


def test_gemini_ocr_flash_fallback_does_not_send_thinking_level_to_25(monkeypatch):
    import json
    from io import BytesIO
    from urllib.error import HTTPError

    from PIL import Image

    import ocr_backend

    urls: list[str] = []
    payloads: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            response_text = json.dumps(
                {
                    "text": "fallback ok",
                    "block_type": "stem",
                    "confidence": 0.88,
                    "lines": ["fallback ok"],
                }
            )
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": response_text}],
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(req, timeout):
        urls.append(req.full_url)
        payloads.append(json.loads(req.data.decode("utf-8")))
        if "gemini-3.5-flash" in req.full_url:
            raise HTTPError(req.full_url, 503, "temporary", hdrs=None, fp=BytesIO(b"temporary"))
        return FakeResponse()

    monkeypatch.delenv(ocr_backend.GEMINI_OCR_MODEL_ENV, raising=False)
    monkeypatch.delenv(ocr_backend.GEMINI_OCR_THINKING_LEVEL_ENV, raising=False)
    monkeypatch.setattr(ocr_backend.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ocr_backend.time, "sleep", lambda seconds: None)

    backend = ocr_backend.GeminiOCRBackend(api_key="test-key", max_retries=0)
    result = backend.recognize(Image.new("RGB", (240, 120), "white"))

    assert result.text == "fallback ok"
    assert result.metadata["model"] == "gemini-2.5-pro"
    assert result.metadata["thinking_level"] == ""
    assert any("gemini-3.5-flash" in url for url in urls)
    assert any("gemini-2.5-pro" in url for url in urls)
    assert payloads[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingConfig" not in payloads[1]["generationConfig"]


def test_explicit_gemini_model_name_builds_matching_backend(monkeypatch):
    import ocr_backend

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    backend = ocr_backend.create_ocr_backend("gemini-3.5-flash")

    assert isinstance(backend, ocr_backend.GeminiOCRBackend)
    assert backend.model == "gemini-3.5-flash"
    assert backend.thinking_level == "low"


def test_page_model_records_recognition_timing_metadata_before_repair(
    monkeypatch,
    tmp_path,
):
    from PIL import Image

    import build_structured_page_json as pipeline
    from ocr_backend import OCRResult
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage
    from structured_schema import BlockType, Box, ContentBlock, PageModel, Subject

    class EmptyBackend:
        engine_name = "none"

        def recognize(self, image):
            return OCRResult(text="", confidence=None, backend_name="none")

    class EmptyCache:
        root_dir = tmp_path / "cache"

        def load_ocr_result(self, image, *, backend_name, cache_identity=None):
            return None

        def save_ocr_result(self, image, result, *, backend_name, cache_identity=None):
            return self.root_dir / "ocr" / backend_name / "payload.json"

    source = tmp_path / "page.png"
    image = Image.new("RGB", (240, 160), "white")
    image.save(source)
    prepared = PreparedPage(
        page_id="image-page-001",
        source_path=str(source),
        page_number=1,
        image=image,
        original_size=image.size,
        metadata={},
    )
    ocr_block = ContentBlock(
        block_id="image-page-001-block-001",
        block_type=BlockType.STEM,
        bbox=Box.from_points(10, 10, 100, 60),
        reading_order=0,
    )
    skipped_block = ContentBlock(
        block_id="image-page-001-block-002",
        block_type=BlockType.IMAGE,
        bbox=Box.from_points(120, 10, 220, 80),
        reading_order=1,
    )
    segmented_page = PageModel(
        page_id=prepared.page_id,
        width_px=image.width,
        height_px=image.height,
        subject=Subject.UNKNOWN,
        source_path=str(source),
        blocks=[ocr_block, skipped_block],
        metadata={"segmenter": "document"},
    )
    captured = {}

    def capture_repair(prepared_page, page, *, ocr_mode, config, cache):
        captured["metadata"] = dict(page.metadata)
        return page

    monkeypatch.setattr(pipeline, "create_ocr_backend", lambda _mode: EmptyBackend())
    monkeypatch.setattr(
        pipeline,
        "segment_page",
        lambda prepared_page, page_id, subject: segmented_page,
    )
    monkeypatch.setattr(pipeline, "repair_page_model", capture_repair)

    page = pipeline.build_page_model(
        prepared,
        subject=Subject.UNKNOWN,
        ocr_mode="none",
        ai_config=build_ai_fallback_config(mode="off"),
        cache=EmptyCache(),
    )

    metadata = captured["metadata"]
    timing = metadata["recognition_timing_ms"]
    assert timing["segmentation"] >= 0
    assert timing["block_ocr"] >= 0
    assert timing["total_before_repair"] >= timing["segmentation"]
    assert page.metadata["ocr_block_worker_count"] == 1
    assert metadata["ocr_eligible_block_count"] == 1
    assert metadata["ocr_skipped_block_count"] == 1
    assert metadata["ocr_cache_hit_count"] == 0
    assert metadata["ocr_cache_miss_count"] == 1
    assert metadata["ocr_escalated_block_count"] == 0
    assert metadata["ai_stages"]["ocr"]["label"] == "1단계 OCR 인식"
    assert metadata["ai_stages"]["ocr_escalation"]["label"] == "2단계 블록 보강"
