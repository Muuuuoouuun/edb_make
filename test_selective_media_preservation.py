from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import io

from PIL import Image, ImageDraw

from build_problem_board_edb import (
    ProblemEntry,
    _ProblemAssetTask,
    _apply_selective_media_preservation,
    _build_image_only_record_image,
    _problem_allows_selective_media_preservation,
    _render_problem_asset,
)
from edb_builder import CROP_FORMAT_V1
from preprocess import _extract_pdf_media_regions, _transform_pdf_text_geometry
from structured_schema import Box, ProblemUnit, Subject


class _FakePdfPage:
    rect = SimpleNamespace(width=1000.0, height=1000.0)

    def get_text(self, mode: str):
        assert mode == "dict"
        return {
            "blocks": [
                {"type": 1, "bbox": (100.0, 100.0, 300.0, 260.0)},
                {"type": 1, "bbox": (0.0, 0.0, 1000.0, 1000.0)},
                {"type": 1, "bbox": (10.0, 10.0, 18.0, 18.0)},
            ]
        }

    def find_tables(self):
        valid = SimpleNamespace(
            bbox=(400.0, 400.0, 700.0, 600.0),
            row_count=3,
            col_count=3,
        )
        too_small = SimpleNamespace(
            bbox=(20.0, 20.0, 40.0, 40.0),
            row_count=2,
            col_count=2,
        )
        passage_frame = SimpleNamespace(
            bbox=(50.0, 50.0, 950.0, 950.0),
            row_count=10,
            col_count=3,
        )
        return SimpleNamespace(tables=[valid, too_small, passage_frame])


def test_pdf_media_extraction_keeps_only_high_confidence_regions() -> None:
    regions = _extract_pdf_media_regions(_FakePdfPage(), 2.0)

    assert [region["kind"] for region in regions] == ["image", "table"]
    assert regions[0]["bbox"] == {
        "left": 200.0,
        "top": 200.0,
        "right": 600.0,
        "bottom": 520.0,
        "width": 400.0,
        "height": 320.0,
    }
    assert regions[1]["row_count"] == 3
    assert regions[1]["column_count"] == 3


def test_pdf_media_regions_follow_normalized_page_geometry() -> None:
    metadata = {
        "pdf_media_regions": [
            {
                "kind": "image",
                "bbox": {"left": 30.0, "top": 50.0, "right": 130.0, "bottom": 250.0},
            }
        ]
    }

    _transform_pdf_text_geometry(metadata, offset_x=10.0, offset_y=20.0, scale=0.5)

    assert metadata["pdf_media_regions"][0]["bbox"] == {
        "left": 10.0,
        "top": 15.0,
        "right": 60.0,
        "bottom": 115.0,
        "width": 50.0,
        "height": 100.0,
    }


def test_shared_child_questions_keep_the_legacy_stage2_treatment() -> None:
    child = ProblemUnit(
        unit_id="child-1",
        subject=Subject.KOREAN,
        title="1번",
        metadata={"passage_role": "child_question"},
    )
    passage = ProblemUnit(
        unit_id="passage-1",
        subject=Subject.KOREAN,
        title="공통 지문",
        metadata={"passage_role": "passage_fragment"},
    )

    assert not _problem_allows_selective_media_preservation(child)
    assert _problem_allows_selective_media_preservation(passage)


def test_selective_overlay_changes_only_the_media_rectangle() -> None:
    source = Image.new("RGB", (120, 80), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((30, 20, 89, 59), fill=(20, 130, 220))
    rendered = Image.new("RGBA", source.size, (238, 238, 226, 0))
    region = {
        "kind": "image",
        "bbox": {"left": 30.0, "top": 20.0, "right": 90.0, "bottom": 60.0},
    }

    selective = _apply_selective_media_preservation(rendered, source, [region])

    assert selective.getpixel((50, 40)) == (20, 130, 220, 255)
    assert selective.getpixel((10, 10)) == rendered.getpixel((10, 10))
    assert selective.getpixel((100, 70)) == rendered.getpixel((100, 70))


def test_default_stage2_asset_restores_pdf_media(tmp_path: Path) -> None:
    source = Image.new("RGB", (1100, 500), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((300, 120, 699, 379), fill=(188, 52, 74))
    draw.line((80, 60, 220, 60), fill="black", width=5)
    task = _ProblemAssetTask(
        source_image=source,
        bounds=Box(left=0.0, top=0.0, width=1100.0, height=500.0),
        crop_path=tmp_path / "crop.png",
        board_render_path=tmp_path / "stage2.png",
        chalk_color=(238, 238, 226),
        trim_edge_guides=False,
        pad_edges=False,
        source_media_regions=(
            {
                "kind": "image",
                "source": "pdf_image_block",
                "confidence": 0.99,
                "bbox": {
                    "left": 300.0,
                    "top": 120.0,
                    "right": 700.0,
                    "bottom": 380.0,
                    "width": 400.0,
                    "height": 260.0,
                },
            },
        ),
    )

    assert _render_problem_asset(task) == (1100, 500)

    with Image.open(task.board_render_path) as rendered:
        assert rendered.convert("RGBA").getpixel((500, 250)) == (188, 52, 74, 255)
        assert rendered.convert("RGBA").getpixel((150, 60))[:3] == (238, 238, 226)
    assert len(task.rendered_media_regions) == 1


def test_text_priority_edb_export_restores_media_after_final_normalization(tmp_path: Path) -> None:
    crop_path = tmp_path / "crop.png"
    board_path = tmp_path / "board.png"
    source = Image.new("RGB", (240, 140), "white")
    draw = ImageDraw.Draw(source)
    draw.line((10, 20, 80, 20), fill="black", width=4)
    draw.rectangle((110, 30, 209, 109), fill=(40, 150, 210))
    source.save(crop_path)
    Image.new("RGBA", source.size, (238, 238, 226, 0)).save(board_path)
    regions = [
        {
            "kind": "image",
            "confidence": 0.99,
            "bbox": {"left": 110.0, "top": 30.0, "right": 210.0, "bottom": 110.0},
        }
    ]
    entry = ProblemEntry(
        problem_id="korean-1",
        title="국어 1번",
        problem_number=1,
        subject=Subject.KOREAN,
        source_page_id="page-1",
        source_path="korean.pdf",
        prepared_page=None,
        bounds=Box(left=0.0, top=0.0, width=240.0, height=140.0),
        crop_path=crop_path,
        board_render_path=board_path,
        blocks=[],
        actual_height_pages=1.0,
        overflow_allowed=True,
        reading_heavy=True,
        risk_flags=[],
        processing_step="s2",
        preserve_media_regions=regions,
    )
    placement = SimpleNamespace(
        metadata={
            "crop_path": str(crop_path),
            "board_render_path": str(board_path),
            "processing_step": "s2",
        }
    )

    payload = _build_image_only_record_image(
        placement,
        entry,
        dark_board=True,
        board_theme="charcoal",
        crop_format=CROP_FORMAT_V1,
        target_image_width_px=0.0,
        continuous_flow=False,
    )

    with Image.open(io.BytesIO(payload.image_bytes)) as exported:
        assert exported.convert("RGBA").getpixel((160, 70)) == (40, 150, 210, 255)
