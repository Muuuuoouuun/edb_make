from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import app_server
from app_server import _copy_publish_problem_layout_metadata


class TestPublishControlPreservation(unittest.TestCase):
    def test_editor_controls_survive_publish_without_overwriting_measured_record_layout(self) -> None:
        source = {
            "placementScaleRatio": 3,
            "placementAutoFitDisabled": True,
            "placementXEdited": True,
            "placementMagnetColumnIndex": 0,
            "boardColumns": 1,
            "boardColumnIndex": 0,
            "startYPages": 34.3,
            "actualHeightPages": 34.3,
            "renderedBottomYPages": 68.6,
            "imageRecordGeometry": {"baseHeightPages": 34.3},
        }
        target = {
            "placementScaleRatio": 1142 / 540,
            "startYPages": 24.228814,
            "actualHeightPages": 0.8,
            "renderedBottomYPages": 25.920666,
            "snappedNextStartYPages": 26.4,
            "imageRecordGeometry": {"baseHeightPages": 0.8},
        }
        measured = copy.deepcopy(target)
        original = copy.deepcopy(source)
        _copy_publish_problem_layout_metadata(target, source)
        self.assertEqual(target["placementScaleRatio"], 3)
        self.assertTrue(target["placement_auto_fit_disabled"])
        self.assertTrue(target["placement_x_edited"])
        self.assertEqual(target["placement_magnet_column_index"], 0)
        self.assertEqual(target["boardColumnCount"], 1)
        self.assertEqual(target["board_column_count"], 1)
        self.assertEqual(target["board_columns"], 1)
        self.assertEqual(target["board_column_index"], 0)
        for field in ("startYPages", "actualHeightPages", "renderedBottomYPages", "snappedNextStartYPages", "imageRecordGeometry"):
            self.assertEqual(target[field], measured[field], field)
        self.assertEqual(source, original, "metadata preservation must not rewrite the editor draft")

    def test_false_zero_and_cleared_magnet_do_not_revive_prior_controls(self) -> None:
        target = {
            "placementAutoFitDisabled": True, "placementXEdited": True,
            "placementMagnetColumnIndex": 2, "boardColumnIndex": 2,
        }
        _copy_publish_problem_layout_metadata(target, {"metadata": {
            "placement_auto_fit_disabled": "false", "placement_x_edited": 0,
            "force_full_page_bounds": "false", "preserve_legacy_placement_scale": "true",
            "placement_magnet_column_index": None, "board_columns": 1, "board_column_index": 0,
        }})
        self.assertIs(target["placementAutoFitDisabled"], False)
        self.assertIs(target["placement_auto_fit_disabled"], False)
        self.assertIs(target["placementXEdited"], False)
        self.assertIs(target["forceFullPageBounds"], False)
        self.assertIs(target["preserveLegacyPlacementScale"], True)
        self.assertIsNone(target["placementMagnetColumnIndex"])
        self.assertIsNone(target["placement_magnet_column_index"])
        self.assertEqual(target["boardColumnIndex"], 0)
        self.assertEqual(target["boardColumns"], 1)

    def test_manual_crop_undo_context_including_empty_resets_is_retained_and_cloned(self) -> None:
        source = {
            "manual_crop": {"top": 0.1689, "bottom": 1},
            "crop_base_bbox": [0, 0, 480, 6000],
            "crop_base_image_path": "/tmp/synthetic-source.png",
            "crop_base_board_render_path": None,
            "crop_base_preserve_media_regions": [],
        }
        target = {"cropBaseBoardRenderPath": "/tmp/stale.png", "cropBasePreserveMediaRegions": [{"top": 0.1}]}
        _copy_publish_problem_layout_metadata(target, source)
        self.assertEqual(target["manualCrop"], source["manual_crop"])
        self.assertEqual(target["cropBaseBbox"], source["crop_base_bbox"])
        self.assertEqual(target["cropBaseImagePath"], source["crop_base_image_path"])
        self.assertIsNone(target["cropBaseBoardRenderPath"])
        self.assertEqual(target["cropBasePreserveMediaRegions"], [])
        self.assertIsNot(target["manualCrop"], source["manual_crop"])
        self.assertIsNot(target["cropBaseBbox"], source["crop_base_bbox"])
        target["manualCrop"]["top"] = 0.4
        target["cropBaseBbox"][0] = 10
        self.assertEqual(source["manual_crop"]["top"], 0.1689)
        self.assertEqual(source["crop_base_bbox"][0], 0)
        _copy_publish_problem_layout_metadata(target, {"manualCrop": None, "cropBaseBbox": []})
        self.assertIsNone(target["manualCrop"])
        self.assertEqual(target["cropBaseBbox"], [])

    def test_crop_base_assets_and_aliases_are_protected_from_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            output = runtime / app_server.DEFAULT_OUTPUT_ROOT_NAME
            output.mkdir()
            paths = [output / f"asset-{index}.png" for index in range(6)]
            for path in paths:
                Image.new("RGB", (12, 16), "white").save(path)
            session = {"problems": [{
                "id": "cropped", "imagePath": paths[0].as_uri(),
                "cropBaseImagePath": paths[1].as_uri(),
                "crop_base_image_path": app_server.path_to_api_url(paths[2]),
                "cropBaseBoardRenderPath": str(paths[3]),
                "crop_base_board_render_path": paths[4].as_uri(),
            }]}
            original = copy.deepcopy(session)
            expected = {str(path.resolve()) for path in paths[:5]}
            self.assertEqual(expected, app_server.collect_session_file_paths(session))
            result = app_server.cleanup_runtime_artifacts(
                runtime_dir=runtime, active_session=session, history=[],
                dry_run=False, min_age_seconds=0, now=10**12,
            )
            self.assertEqual([str(paths[5].resolve())], result["deletedPaths"])
            self.assertTrue(all(path.is_file() for path in paths[:5]))
            self.assertEqual(session, original, "collecting/protecting assets must not rewrite session paths")
            history = [{"session": json.loads(json.dumps(session))}]
            self.assertEqual(expected, app_server.collect_session_history_file_paths(history))
            archived_cleanup = app_server.cleanup_runtime_artifacts(
                runtime_dir=runtime, active_session={}, history=history,
                dry_run=False, min_age_seconds=0, now=10**12,
            )
            self.assertEqual([], archived_cleanup["deletedPaths"])

    def test_published_crop_context_survives_json_restore_and_can_return_to_base_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            output = runtime / app_server.DEFAULT_OUTPUT_ROOT_NAME
            output.mkdir()
            base_path = output / "upscaled-base.png"
            base_board = output / "upscaled-board.png"
            current_path = output / "current-crop.png"
            Image.new("RGB", (80, 100), "white").save(base_path)
            Image.new("RGBA", (80, 100), (255, 255, 255, 255)).save(base_board)
            Image.new("RGB", (60, 80), "white").save(current_path)
            base_bbox = {"left": 10, "top": 20, "width": 80, "height": 100}
            prior = {
                "manualCrop": {"leftRatio": 0.125, "rightRatio": 0.125, "topRatio": 0.1, "bottomRatio": 0.1},
                "cropBaseBbox": base_bbox,
                "cropBaseImagePath": app_server.path_to_api_url(base_path),
                "cropBaseBoardRenderPath": base_board.as_uri(),
                "cropBasePreserveMediaRegions": [],
            }
            published_problem = {
                "id": "cropped", "imagePath": current_path.as_uri(), "boardRenderPath": current_path.as_uri(),
                "bbox": {"left": 20, "top": 30, "width": 60, "height": 80}, "step": "s1",
                "startYPages": 0, "actualHeightPages": 0.72,
            }
            _copy_publish_problem_layout_metadata(published_problem, prior)
            session = {"problems": [published_problem], "output_dir": str(output / "work"), "pages": []}
            snapshot = json.loads(json.dumps(app_server.rewrite_session_for_http(session)))
            restored = app_server._denormalize_session_paths(snapshot)
            collected = app_server.collect_session_file_paths(restored)
            self.assertIn(str(base_path.resolve()), collected)
            self.assertIn(str(base_board.resolve()), collected)
            app_server.cleanup_runtime_artifacts(
                runtime_dir=runtime, active_session=restored, history=[],
                dry_run=False, min_age_seconds=0, now=10**12,
            )
            undo = app_server._mutate_crop(restored, "cropped", {})["problems"][0]
            self.assertEqual(base_bbox, undo["bbox"])
            self.assertEqual(base_path.resolve().as_uri(), undo["imagePath"])
            self.assertEqual(base_board.resolve().as_uri(), undo["boardRenderPath"])
            self.assertTrue(all(value == 0 for value in undo["manualCrop"].values()))
            with Image.open(app_server.decode_file_reference(undo["imagePath"])) as recovered:
                self.assertEqual((80, 100), recovered.size)


if __name__ == "__main__":
    unittest.main()
