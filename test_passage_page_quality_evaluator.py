"""Adversarial validator tests. Fixture fractions are NOT product measurements."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from edb_builder import ImageRecordSpec, build_edb, build_image_record
from scripts.evaluate_passage_page_quality import (
    AXES, BINDINGS, CELLS, HARD_CHECKS, EvaluationError, budget_ms, evaluate, main,
)


class PassagePageEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest_path = self.root / "manifest.json"
        self.observations_path = self.root / "observations.json"
        self.manifest = {
            "schema_version": 1, "policy_id": "shared-fullpage-five-axis-v1",
            "git_commit": "a" * 40, "pipeline_fingerprint": "b" * 64,
            "environment_fingerprint": "c" * 64, "backend": "local", "cases": [],
            "policy": self.write("policy.md", b"Unit-test policy fixture; no measured product scores.\n"),
        }
        self.observations = []
        self.png = io.BytesIO()
        Image.new("RGB", (10, 10), "black").save(self.png, format="PNG")
        image = self.png.getvalue()
        record = build_image_record(ImageRecordSpec(1, image, image, 0, 0, .2, .2))
        self.edb = build_edb([record], header_flag=0, page_count_hint=50)
        for subject, mode in CELLS:
            self.add_case(subject, mode)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(content, bytes):
            content = json.dumps(content, sort_keys=True, allow_nan=True).encode()
        path.write_bytes(content)
        return {"path": name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}

    def add_case(self, subject, mode, *, suffix="", kind="real-original", split="development", source_suffix=""):
        source_id = subject + source_suffix
        case_id = source_id + "-" + mode + suffix
        import pymupdf
        with pymupdf.open() as document:
            document.new_page().insert_text((20, 20), "Unit fixture " + source_id)
            source = self.write(source_id + ".pdf", document.tobytes(no_new_id=True))
        gt = {"policy_id": self.manifest["policy_id"], "review_status": "source-reviewed",
              "annotator_id": "source-annotator", "annotator_type": "agent",
              "reviewer_id": "independent-source-reviewer", "reviewer_type": "agent",
              "documents": [{"id": source_id, "language": subject, "page_count": 1,
                             "source": {"sha256": source["sha256"]}, "groups": [{"id": "shared-1-2"}]}]}
        truth = self.write(source_id + "-gt.json", gt)
        case = {"case_id": case_id, "source_id": source_id, "subject": subject, "mode": mode,
                "kind": kind, "split": split, "family": source_id, "source": source,
                "ground_truth": truth, "source_sha256": source["sha256"],
                "ground_truth_sha256": truth["sha256"], "page_count": 1,
                "assertion_ids": ["record-1", "page-1"], "hard_check_ids": sorted(HARD_CHECKS),
                "trial_ids": ["cold-1", "cold-2", "cold-3"], "tags": ["native-text"],
                "timing_budget_ms": budget_ms(mode, 1), "feedback_budget_ms": 250,
                **{key: self.manifest[key] for key in ("git_commit", "pipeline_fingerprint", "environment_fingerprint", "backend")}}
        metrics = {axis: {key: {"numerator": 100, "denominator": 100, "failed_ids": []}
                          for key in components} for axis, components in AXES.items()}
        metrics["layout"]["assertions"] = {"numerator": 2, "denominator": 2, "failed_ids": []}
        metrics["detection"] = {key: {"numerator": 1, "denominator": 1, "failed_ids": []}
                                for key in ("precision", "recall")}
        metrics["speed"] = {"derived_from": "all-trials"}
        obs = {**{key: case[key] for key in BINDINGS}, "status": "completed",
               "timing_method": "browser-end-to-end", "metrics": metrics,
               **{key: case[key] for key in ("environment_fingerprint", "pipeline_fingerprint", "backend")},
               "hard_failures": [], "assertions": [{"id": key, "passed": True} for key in case["assertion_ids"]],
               "hard_checks": [{"id": key, "passed": True} for key in case["hard_check_ids"]],
               "trials": [{"id": key, "total_ms": 1000, "feedback_ms": 100,
                           "started_ms": i * 2000, "feedback_at_ms": i * 2000 + 100,
                           "ready_ms": i * 2000 + 900, "completed_ms": i * 2000 + 1000,
                           "peak_rss_bytes": 100000, "fresh_process": True, "cache_disabled": True,
                           "task_success": True, "correctness_approved": True, "status": "completed"}
                          for i, key in enumerate(case["trial_ids"])]}
        self.manifest["cases"].append(case)
        self.observations.append(obs)

    def sync_evidence(self):
        for obs in self.observations:
            cid = obs["case_id"]
            bindings = {key: obs[key] for key in BINDINGS}
            files = []
            for role, content, extension in (("edb", self.edb, "edb"),
                    ("decoded-render", self.png.getvalue(), "png"), ("comparison", self.png.getvalue(), "png"),
                    ("raw-metrics", {**bindings, **{key: obs[key] for key in ("metrics", "assertions", "hard_checks", "hard_failures")}}, "json"),
                    ("trial-trace", {**bindings, "trials": obs["trials"]}, "json")):
                files.append({"role": role, **self.write(f"{cid}/{role}.{extension}", content)})
            obs["artifact_manifest"] = self.write(f"{cid}/artifacts.json", {**bindings, "files": files})

    def save(self, *, sync=True):
        if sync:
            self.sync_evidence()
        self.write("manifest.json", self.manifest)
        self.write("observations.json", {"schema_version": 1,
                   "manifest_sha256": hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
                   "observations": self.observations})

    def run_eval(self, *, sync=True):
        self.save(sync=sync)
        return evaluate(self.manifest_path, self.observations_path)

    def test_complete_four_cells_and_raw_evidence_pass_without_universal_claim(self):
        report = self.run_eval()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(report["cohorts"]["real-original/development"]["cells"]), 4)
        self.assertIn("not universal", report["scope"])
        self.assertTrue(report["advisories"])

    def test_five_axis_minimum_and_mean_are_independent_unrounded_gates(self):
        for key in self.observations[0]["metrics"]["joining"]:
            self.observations[0]["metrics"]["joining"][key]["numerator"] = 92.999
        report = self.run_eval()
        cell = report["cohorts"]["real-original/development"]["cells"]["korean/shared-passage"]
        self.assertGreater(cell["mean"], 95)
        self.assertEqual(cell["status"], "failed")
        self.assertLess(cell["minimum_axis"], 93)

    def test_cell_mean_95_is_required_even_when_every_axis_reaches_93(self):
        case, obs = self.manifest["cases"][0], self.observations[0]
        for axis in ("preservation", "joining"):
            for metric in obs["metrics"][axis].values():
                metric["numerator"] = 93
        case["assertion_ids"] = [f"layout-{i}" for i in range(100)]
        obs["assertions"] = [{"id": key, "passed": i < 93} for i, key in enumerate(case["assertion_ids"])]
        obs["metrics"]["layout"]["assertions"] = {"numerator": 93, "denominator": 100,
                                                   "failed_ids": case["assertion_ids"][93:]}
        for trial in obs["trials"]:
            trial.update(feedback_ms=25000 / 93, feedback_at_ms=trial["started_ms"] + 25000 / 93)
        report = self.run_eval()
        cell = report["cohorts"]["real-original/development"]["cells"]["korean/shared-passage"]
        self.assertAlmostEqual(cell["minimum_axis"], 93)
        self.assertAlmostEqual(cell["mean"], 94.4)
        self.assertEqual(report["cases"][0]["status"], "passed")
        self.assertEqual(cell["status"], "failed")

    def test_bad_document_mean_cannot_hide_behind_other_documents(self):
        self.observations[0]["metrics"]["joining"]["unit_uniqueness"]["numerator"] = 60
        for i in range(5):
            for mode in ("shared-passage", "full-page"):
                self.add_case("korean", mode, source_suffix=f"-extra-{i}")
        report = self.run_eval()
        cell = report["cohorts"]["real-original/development"]["cells"]["korean/shared-passage"]
        self.assertGreaterEqual(cell["minimum_axis"], 93)
        self.assertGreaterEqual(cell["mean"], 95)
        self.assertEqual(report["cases"][0]["mean"], 92)
        self.assertEqual(cell["status"], "failed")

    def test_case_hard_loss_fails_even_with_perfect_macro_scores(self):
        self.observations[0]["hard_checks"][0]["passed"] = False
        report = self.run_eval()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cases"][0]["mean"], 100)
        self.assertTrue(report["failure_ledger"][0]["hard_failures"])

    def test_tiny_missing_instance_cannot_hide_in_high_average(self):
        self.observations[0]["metrics"]["detection"]["recall"]["numerator"] = 0
        self.observations[0]["metrics"]["detection"]["precision"]["numerator"] = 0
        report = self.run_eval()
        self.assertEqual(report["status"], "failed")
        self.assertIn("missing-or-extra-selected-instance", report["cases"][0]["hard_failures"])

    def test_missing_case_cell_and_duplicate_case_rejected(self):
        original = copy.deepcopy(self.observations)
        self.observations.pop()
        with self.assertRaisesRegex(EvaluationError, "Missing/extra expected"):
            self.run_eval()
        self.observations = original
        removed_case = self.manifest["cases"].pop()
        removed_observation = self.observations.pop()
        with self.assertRaisesRegex(EvaluationError, "requires both modes"):
            self.run_eval()
        self.manifest["cases"].append(removed_case)
        self.observations.append(removed_observation)
        self.observations.append(copy.deepcopy(self.observations[0]))
        with self.assertRaisesRegex(EvaluationError, "duplicate IDs"):
            self.run_eval()

    def test_an_entire_language_cell_cannot_disappear_from_registered_corpus(self):
        self.manifest["cases"] = [c for c in self.manifest["cases"] if c["subject"] == "korean"]
        self.observations = [o for o in self.observations if o["subject"] == "korean"]
        with self.assertRaisesRegex(EvaluationError, "Missing required cell.*english"):
            self.run_eval()

    def test_missing_axis_zero_denominator_nan_and_impossible_fractions_rejected(self):
        original = copy.deepcopy(self.observations[0]["metrics"])
        corruptions = [lambda m: m.pop("joining"),
                       lambda m: m["preservation"]["recall"].update(denominator=0),
                       lambda m: m["preservation"]["recall"].update(numerator=float("nan")),
                       lambda m: m["preservation"]["recall"].update(numerator=101),
                       lambda m: m["preservation"]["recall"].update(numerator=10**500),
                       lambda m: m["preservation"]["recall"].update(numerator=True)]
        for corrupt in corruptions:
            with self.subTest(corruption=corrupt):
                self.observations[0]["metrics"] = copy.deepcopy(original)
                corrupt(self.observations[0]["metrics"])
                with self.assertRaises(EvaluationError):
                    self.run_eval()

    def test_registered_trials_cannot_drop_slow_or_failed_repeat(self):
        self.observations[0]["trials"].pop()
        with self.assertRaisesRegex(EvaluationError, "three timing"):
            self.run_eval()

    def test_failed_trial_is_hard_failure_and_remains_speed_denominator(self):
        trial = self.observations[0]["trials"][1]
        trial.update(status="crash", task_success=False)
        report = self.run_eval()
        self.assertEqual(report["status"], "failed")
        self.assertAlmostEqual(report["cases"][0]["scores"]["speed"], 100 * 2 / 3)
        self.assertIn("failed-trial:cold-2", report["cases"][0]["hard_failures"])

    def test_p95_uses_slowest_trial_and_feedback_not_hand_entered_score(self):
        trial = self.observations[0]["trials"][-1]
        trial.update(feedback_ms=500, feedback_at_ms=trial["started_ms"] + 500)
        report = self.run_eval()
        self.assertEqual(report["cases"][0]["scores"]["speed"], 50)
        self.assertEqual(report["cases"][0]["timing"]["feedback_ms_p95"], 500)

    def test_feedback_action_start_is_separate_from_process_startup(self):
        trial = self.observations[0]["trials"][0]
        trial.update(feedback_started_ms=400, feedback_at_ms=500)
        report = self.run_eval()
        self.assertEqual(report["cases"][0]["scores"]["speed"], 100)
        self.assertEqual(report["cases"][0]["timing"]["total_ms_p95"], 1000)
        self.assertEqual(report["cases"][0]["timing"]["feedback_ms_p95"], 100)
        # Counting startup as feedback, or putting the action after feedback,
        # is inconsistent with the actual observed timestamps.
        trial["feedback_ms"] = 500
        with self.assertRaisesRegex(EvaluationError, "elapsed/timestamps disagree"):
            self.run_eval()
        trial.update(feedback_ms=100, feedback_started_ms=600)
        with self.assertRaisesRegex(EvaluationError, "Nonmonotonic"):
            self.run_eval()

    def test_budget_increase_and_nonmonotonic_timing_rejected(self):
        self.manifest["cases"][0]["timing_budget_ms"] += 1
        with self.assertRaisesRegex(EvaluationError, "Timing budget"):
            self.run_eval()
        self.manifest["cases"][0]["timing_budget_ms"] -= 1
        self.observations[0]["trials"][0]["ready_ms"] = 1100
        with self.assertRaisesRegex(EvaluationError, "Nonmonotonic"):
            self.run_eval()

    def test_bound_source_commit_gt_and_raw_metrics_cannot_be_swapped(self):
        self.save()
        self.observations[0]["git_commit"] = "d" * 40
        with self.assertRaisesRegex(EvaluationError, "git_commit binding"):
            self.run_eval(sync=False)
        self.observations[0]["git_commit"] = "a" * 40
        self.observations[0]["metrics"]["joining"]["unit_uniqueness"]["numerator"] = 99
        with self.assertRaisesRegex(EvaluationError, "differs from bound raw"):
            self.run_eval(sync=False)

    def test_empty_tampered_corrupt_outputs_and_path_escape_rejected(self):
        self.save()
        path = self.root / self.observations[0]["artifact_manifest"]["path"]
        bundle = json.loads(path.read_text())
        edb = next(f for f in bundle["files"] if f["role"] == "edb")
        (self.root / edb["path"]).write_bytes(b"")
        with self.assertRaisesRegex(EvaluationError, "missing/empty"):
            self.run_eval(sync=False)
        self.edb = b"nonempty but not EDB"
        report = self.run_eval()
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any(f.startswith("invalid-edb:") for f in report["cases"][0]["hard_failures"]))
        self.manifest["cases"][0]["source"]["path"] = "../outside.pdf"
        with self.assertRaisesRegex(EvaluationError, "escapes evidence"):
            self.run_eval()

    def test_ground_truth_requires_independent_review_and_source_binding(self):
        case = self.manifest["cases"][0]
        path = self.root / case["ground_truth"]["path"]
        gt = json.loads(path.read_text()); gt["reviewer_id"] = gt["annotator_id"]
        reference = self.write(case["ground_truth"]["path"], gt)
        for c, o in zip(self.manifest["cases"], self.observations):
            if c["source_id"] == case["source_id"]:
                c["ground_truth"] = reference; c["ground_truth_sha256"] = reference["sha256"]
                o["ground_truth_sha256"] = reference["sha256"]
        with self.assertRaisesRegex(EvaluationError, "Independent ground truth"):
            self.run_eval()

    def test_frozen_source_review_status_keeps_independent_review_and_exact_hash(self):
        for subject in ("korean", "english"):
            selected = [c for c in self.manifest["cases"] if c["subject"] == subject]
            path = self.root / selected[0]["ground_truth"]["path"]
            gt = json.loads(path.read_text()); gt["review_status"] = "source-reviewed-frozen"
            reference = self.write(selected[0]["ground_truth"]["path"], gt)
            for c, obs in zip(self.manifest["cases"], self.observations):
                if c["subject"] == subject:
                    c["ground_truth"] = reference; c["ground_truth_sha256"] = reference["sha256"]
                    obs["ground_truth_sha256"] = reference["sha256"]
        self.assertEqual(self.run_eval()["status"], "passed")

    def test_gt_source_forgery_and_wrong_detection_denominators_rejected(self):
        self.observations[0]["metrics"]["detection"]["recall"]["denominator"] = 2
        with self.assertRaisesRegex(EvaluationError, "GT inventory"):
            self.run_eval()
        self.observations[0]["metrics"]["detection"]["recall"]["denominator"] = 1
        case = self.manifest["cases"][0]
        path = self.root / case["ground_truth"]["path"]
        gt = json.loads(path.read_text()); gt["documents"][0]["source"]["sha256"] = "f" * 64
        ref = self.write(case["ground_truth"]["path"], gt)
        for c, obs in zip(self.manifest["cases"], self.observations):
            if c["source_id"] == case["source_id"]:
                c["ground_truth"] = ref; c["ground_truth_sha256"] = ref["sha256"]
                obs["ground_truth_sha256"] = ref["sha256"]
        with self.assertRaisesRegex(EvaluationError, "Ground truth source SHA"):
            self.run_eval()

    def test_declared_page_count_cannot_inflate_time_budget(self):
        for c in self.manifest["cases"]:
            if c["subject"] == "korean":
                c["page_count"] = 2
                c["timing_budget_ms"] = budget_ms(c["mode"], 2)
        case = self.manifest["cases"][0]
        gt = json.loads((self.root / case["ground_truth"]["path"]).read_text())
        gt["documents"][0]["page_count"] = 2
        ref = self.write(case["ground_truth"]["path"], gt)
        for c, obs in zip(self.manifest["cases"], self.observations):
            if c["subject"] == "korean":
                c["ground_truth"] = ref; c["ground_truth_sha256"] = ref["sha256"]
                obs["ground_truth_sha256"] = ref["sha256"]
        with self.assertRaisesRegex(EvaluationError, "Actual source PDF page"):
            self.run_eval()

    def test_source_bytes_and_cross_case_artifact_manifests_cannot_be_swapped(self):
        self.save()
        self.observations[0]["artifact_manifest"] = self.observations[1]["artifact_manifest"]
        with self.assertRaisesRegex(EvaluationError, "artifact manifest: case_id binding"):
            self.run_eval(sync=False)
        (self.root / self.manifest["cases"][0]["source"]["path"]).write_bytes(b"changed-input")
        with self.assertRaisesRegex(EvaluationError, "artifact size mismatch"):
            self.run_eval(sync=False)

    def test_synthetic_perfect_scores_cannot_raise_real_cell(self):
        self.observations[0]["metrics"]["joining"]["unit_uniqueness"]["numerator"] = 90
        for subject, mode in CELLS:
            self.add_case(subject, mode, kind="synthetic", split="regression", source_suffix="-synthetic")
        report = self.run_eval()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cohorts"]["synthetic/regression"]["status"], "passed")
        self.assertEqual(report["cohorts"]["real-original/development"]["status"], "failed")

    def test_real_stress_and_holdout_remain_separate_from_development(self):
        self.observations[0]["metrics"]["joining"]["unit_uniqueness"]["numerator"] = 90
        for kind, split, suffix in (("derived-real-stress", "development", "-stress"),
                                    ("real-original", "holdout", "-holdout")):
            for subject, mode in CELLS:
                self.add_case(subject, mode, kind=kind, split=split, source_suffix=suffix)
        report = self.run_eval()
        self.assertEqual(set(report["cohorts"]), {"real-original/development", "real-original/holdout", "derived-real-stress/development"})
        self.assertEqual(report["cohorts"]["real-original/holdout"]["status"], "passed")
        self.assertEqual(report["cohorts"]["derived-real-stress/development"]["status"], "passed")
        self.assertEqual(report["cohorts"]["real-original/development"]["status"], "failed")

    def test_exposed_family_cannot_be_reclassified_as_holdout(self):
        self.manifest["cases"][1]["split"] = "holdout"
        self.observations[1]["split"] = "holdout"
        with self.assertRaisesRegex(EvaluationError, "family leaked"):
            self.run_eval()

    def test_cli_incomplete_never_reports_a_score(self):
        self.save(); self.observations_path.write_text('{"schema_version": 1, "observations": []}')
        output = self.root / "result.json"
        self.assertEqual(main([str(self.manifest_path), str(self.observations_path), "--output", str(output)]), 2)
        report = json.loads(output.read_text())
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["scores"])


if __name__ == "__main__":
    unittest.main()
