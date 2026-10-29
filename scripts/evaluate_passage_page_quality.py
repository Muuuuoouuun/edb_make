#!/usr/bin/env python3
"""Strict, evidence-bound five-axis aggregation; never creates measurements.

See the separately frozen evaluator-schema.md for the input contract. This
checks supplied measurement evidence, not the semantic truth of an annotation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

POLICY_ID = "shared-fullpage-five-axis-v1"
AXES = {
    "detection": ("precision", "recall"),
    "preservation": ("precision", "recall"),
    "joining": ("unit_uniqueness", "edge_precision", "edge_recall", "seam_retention"),
    "layout": ("assertions",),
}
CELLS = tuple((subject, mode) for subject in ("korean", "english")
              for mode in ("shared-passage", "full-page"))
KINDS = {"real-original", "derived-real-stress", "synthetic"}
SPLITS = {"development", "holdout", "regression"}
BINDINGS = ("case_id", "source_id", "subject", "mode", "kind", "split", "family",
            "source_sha256", "ground_truth_sha256", "git_commit")
ROLES = {"edb", "decoded-render", "comparison", "raw-metrics", "trial-trace"}
HARD_CHECKS = frozenset({"no-missing-instance", "no-duplicate-instance", "no-forbidden-content",
                         "no-meaningful-content-loss", "correct-order", "no-unintended-merge",
                         "valid-edb", "no-overlap-or-cut", "input-unchanged", "no-crash-or-oom",
                         "task-complete"})
HASH = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


class EvaluationError(ValueError):
    """Incomplete, inconsistent, or unverifiable observations."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def number(value: Any, label: str, *, positive: bool = False) -> float:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    require(finite, f"{label}: finite number required")
    require(value > 0 if positive else value >= 0, f"{label}: out of range")
    return value


def identifiers(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    require(isinstance(value, list), f"{label}: list required")
    require(all(isinstance(v, str) and v.strip() for v in value), f"{label}: nonempty IDs required")
    require(len(value) == len(set(value)), f"{label}: duplicate IDs")
    require(bool(value) or not nonempty, f"{label}: empty inventory")
    return value


def load_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise EvaluationError(f"{path.name}: nonfinite JSON number {value}")
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            require(key not in result, f"{path.name}: duplicate JSON key {key}")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                          object_pairs_hook=unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"Cannot read {path}: {exc}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(reference: Any, root: Path, label: str) -> Path:
    require(isinstance(reference, dict), f"{label}: artifact reference required")
    require(isinstance(reference.get("path"), str) and reference["path"].strip(), f"{label}: path required")
    path = (root / reference["path"]).resolve()
    require(path.is_relative_to(root), f"{label}: artifact escapes evidence root")
    require(path.is_file() and path.stat().st_size > 0, f"{label}: missing/empty artifact")
    require(type(reference.get("size_bytes")) is int and reference["size_bytes"] == path.stat().st_size,
            f"{label}: artifact size mismatch")
    require(isinstance(reference.get("sha256"), str) and HASH.fullmatch(reference["sha256"]) is not None,
            f"{label}: invalid SHA256")
    require(sha256(path) == reference["sha256"], f"{label}: artifact SHA256 mismatch")
    return path


def ratio(value: Any, label: str) -> float:
    require(isinstance(value, dict) and set(value) == {"numerator", "denominator", "failed_ids"},
            f"{label}: numerator/denominator/failed_ids required")
    top = number(value["numerator"], label + ".numerator")
    bottom = number(value["denominator"], label + ".denominator", positive=True)
    require(top <= bottom, f"{label}: numerator exceeds denominator")
    failed = identifiers(value["failed_ids"], label + ".failed_ids", nonempty=False)
    require(not failed or top < bottom, f"{label}: failed IDs contradict perfect fraction")
    return top / bottom


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: all trials count, including the slowest at n=3."""
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def budget_ms(mode: str, pages: int) -> float:
    return min(300_000, 10_000 + (5_000 if mode == "shared-passage" else 2_500) * pages)


def _bindings(payload: dict[str, Any], case: dict[str, Any], label: str) -> None:
    for field in BINDINGS:
        require(payload.get(field) == case[field], f"{label}: {field} binding mismatch")


def _ground_truth(path: Path, case: dict[str, Any]) -> int:
    gt = load_json(path)
    require(isinstance(gt, dict), "Ground truth must be an object")
    require(gt.get("policy_id") == POLICY_ID, "Ground truth policy mismatch")
    # A frozen annotation retains its reviewed semantics; accept the explicit
    # frozen label without rewriting its immutable bytes/hash for evaluation.
    require(gt.get("review_status") in ("source-reviewed", "source-reviewed-frozen", "approved"),
            "Ground truth review incomplete")
    require(gt.get("annotator_type") in ("human", "agent") and gt.get("reviewer_type") in ("human", "agent"),
            "Ground truth reviewer types required")
    annotator, reviewer = gt.get("annotator_id"), gt.get("reviewer_id")
    require(isinstance(annotator, str) and annotator.strip() and isinstance(reviewer, str)
            and reviewer.strip() and annotator != reviewer, "Independent ground truth review required")
    documents = gt.get("documents")
    require(isinstance(documents, list), "Ground truth documents required")
    matches = [doc for doc in documents if isinstance(doc, dict) and doc.get("id") == case["source_id"]]
    require(len(matches) == 1, "Ground truth source inventory mismatch")
    doc = matches[0]
    require(isinstance(doc.get("source"), dict) and doc["source"].get("sha256") == case["source_sha256"],
            "Ground truth source SHA mismatch")
    require(doc.get("language") == case["subject"] and doc.get("page_count") == case["page_count"],
            "Ground truth subject/page count mismatch")
    for key in ("split", "family"):
        if key in doc:
            require(doc[key] == case[key], f"Ground truth {key} mismatch")
    if case["mode"] == "full-page":
        return case["page_count"]
    groups = doc.get("groups", doc.get("expected_shared_ranges"))
    require(isinstance(groups, list) and groups, "Positive shared-instance GT inventory required")
    return len(groups)


def _readback(path: Path) -> str | None:
    # Reuse the existing independent binary parser, rather than accepting JSON
    # preflight claims as proof that a nonempty EDB was written.
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from inspect_edb import parse_edb
    try:
        parsed = parse_edb(path)
        require(1 <= parsed.page_count_hint <= 50, "EDB part exceeds 50-page limit")
        require(parsed.record_count_actual > 0 and parsed.record_count_actual == parsed.record_count_hint,
                "EDB empty/truncated record inventory")
    except Exception as exc:
        return f"invalid-edb:{path.name}:{exc}"
    return None


def _evidence(obs: dict[str, Any], case: dict[str, Any], root: Path) -> list[str]:
    bundle = load_json(artifact(obs.get("artifact_manifest"), root, "artifact manifest"))
    require(isinstance(bundle, dict), "Artifact manifest must be an object")
    _bindings(bundle, case, "artifact manifest")
    files = bundle.get("files")
    require(isinstance(files, list) and files, "Nonempty artifact manifest required")
    roles: dict[str, list[Path]] = defaultdict(list)
    seen = set()
    for entry in files:
        require(isinstance(entry, dict) and entry.get("role") in ROLES, "Unknown/missing artifact role")
        path = artifact(entry, root, entry["role"])
        require(path not in seen, "Same artifact reused for multiple roles")
        seen.add(path)
        roles[entry["role"]].append(path)
    require(set(roles) == ROLES, "Missing EDB/decoded-render/comparison/raw-metrics/trial-trace artifacts")
    readback_failures = [failure for path in roles["edb"] if (failure := _readback(path))]
    for role in ("decoded-render", "comparison"):
        for path in roles[role]:
            try:
                from PIL import Image
                with Image.open(path) as image:
                    require(image.width > 0 and image.height > 0, f"{role}: empty image")
                    image.verify()
            except EvaluationError:
                raise
            except Exception as exc:
                raise EvaluationError(f"{role}: invalid image: {exc}") from exc
    for role, keys in (("raw-metrics", ("metrics", "assertions", "hard_checks", "hard_failures")),
                       ("trial-trace", ("trials",))):
        require(len(roles[role]) == 1, f"Exactly one {role} artifact required")
        raw = load_json(roles[role][0])
        require(isinstance(raw, dict), f"{role}: JSON object required")
        _bindings(raw, case, role)
        for key in keys:
            require(key in obs and raw.get(key) == obs[key], f"{role}: {key} differs from bound raw evidence")
    return readback_failures


def _booleans(items: Any, expected_ids: list[str], label: str) -> list[str]:
    require(isinstance(items, list), f"{label}: assertion results required")
    require(all(isinstance(item, dict) and set(item) == {"id", "passed"} for item in items),
            f"{label}: assertion ID/passed required")
    ids = identifiers([item["id"] for item in items], label)
    require(set(ids) == set(expected_ids), f"{label}: missing/extra registered assertions")
    require(all(type(item["passed"]) is bool for item in items), f"{label}: boolean required")
    return [item["id"] for item in items if not item["passed"]]


def score_case(case: dict[str, Any], obs: dict[str, Any], root: Path) -> dict[str, Any]:
    _bindings(obs, case, "observation")
    require(obs.get("status") in ("completed", "failed"), "Observation status required")
    require(obs.get("timing_method") == "browser-end-to-end", "CLI timings cannot establish UI usability")
    for key in ("environment_fingerprint", "pipeline_fingerprint", "backend"):
        require(obs.get(key) == case[key], f"{key} mismatch")
    metrics = obs.get("metrics")
    require(isinstance(metrics, dict) and set(metrics) == set(AXES) | {"speed"}, "Exactly five metric axes required")
    require(metrics["speed"] == {"derived_from": "all-trials"}, "Speed must be derived from all raw trials")
    scores = {}
    for axis, components in AXES.items():
        require(isinstance(metrics[axis], dict) and set(metrics[axis]) == set(components), f"{axis}: missing/extra components")
        scores[axis] = 100 * min(ratio(metrics[axis][key], f"{axis}.{key}") for key in components)
    precision, recall = metrics["detection"]["precision"], metrics["detection"]["recall"]
    require(all(type(value) is int for metric in (precision, recall)
                for value in (metric["numerator"], metric["denominator"])), "Detection instance counts must be integers")
    require(recall["denominator"] == case["expected_instances"] and precision["numerator"] == recall["numerator"],
            "Detection counts disagree with GT inventory/one-to-one matching")
    failed_assertions = _booleans(obs.get("assertions"), case["assertion_ids"], "layout")
    layout = metrics["layout"]["assertions"]
    require(layout["denominator"] == len(case["assertion_ids"]) and
            layout["numerator"] == len(case["assertion_ids"]) - len(failed_assertions) and
            set(layout["failed_ids"]) == set(failed_assertions), "Layout fraction disagrees with registered assertion results")
    hard = list(identifiers(obs.get("hard_failures"), "hard_failures", nonempty=False))
    hard.extend(_booleans(obs.get("hard_checks"), case["hard_check_ids"], "hard checks"))
    if scores["detection"] < 100:
        hard.append("missing-or-extra-selected-instance")
    if obs["status"] != "completed":
        hard.append("observation-failed")
    trials = obs.get("trials")
    require(isinstance(trials, list) and len(trials) >= 3, "At least three timing trials required")
    trial_ids = identifiers([t.get("id") if isinstance(t, dict) else None for t in trials], "trials")
    require(set(trial_ids) == set(case["trial_ids"]), "Missing/extra registered trials; all attempts must count")
    totals, feedbacks, successes, rss = [], [], [], []
    for trial in trials:
        require(trial.get("fresh_process") is True and trial.get("cache_disabled") is True, "Fresh-process/cache-disabled trials required")
        require(type(trial.get("task_success")) is bool and type(trial.get("correctness_approved")) is bool,
                "Trial task success/correctness booleans required")
        require(trial.get("status") in ("completed", "failed", "timeout", "crash", "oom"), "Trial status required")
        total = number(trial.get("total_ms"), "total_ms", positive=True)
        feedback = number(trial.get("feedback_ms"), "feedback_ms", positive=True)
        # Total task timing includes the fresh app process startup; immediate
        # UI feedback starts at the later upload action, not process launch.
        times = [number(trial.get("started_ms"), "started_ms"),
                 number(trial.get("feedback_started_ms", trial.get("started_ms")), "feedback_started_ms")]
        times.extend(number(trial.get(key), key) for key in ("feedback_at_ms", "ready_ms", "completed_ms"))
        require(times == sorted(times) and times[-1] > times[0], "Nonmonotonic trial timestamps")
        require(math.isclose(total, times[-1] - times[0], abs_tol=0.001) and
                math.isclose(feedback, times[2] - times[1], abs_tol=0.001), "Trial elapsed/timestamps disagree")
        peak = number(trial.get("peak_rss_bytes"), "peak_rss_bytes", positive=True)
        success = trial["task_success"] and trial["correctness_approved"] and trial["status"] == "completed"
        if not success:
            hard.append("failed-trial:" + trial["id"])
        totals.append(total); feedbacks.append(feedback); successes.append(success); rss.append(peak)
    total_p95, feedback_p95 = percentile(totals, .95), percentile(feedbacks, .95)
    scores["speed"] = 100 * min(1, case["timing_budget_ms"] / total_p95,
                                 case["feedback_budget_ms"] / feedback_p95) * sum(successes) / len(successes)
    hard.extend(_evidence(obs, case, root))
    mean = sum(scores.values()) / 5
    return {**{field: case[field] for field in BINDINGS}, "page_count": case["page_count"],
            "tags": case["tags"], "scores": scores, "mean": mean,
            "hard_failures": sorted(set(hard)), "status": "passed" if not hard and mean >= 93 else "failed",
            "timing": {"trial_count": len(trials), "total_ms_p50": percentile(totals, .5),
                       "total_ms_p95": total_p95, "total_ms_max": max(totals),
                       "feedback_ms_p95": feedback_p95, "peak_rss_bytes_max": max(rss),
                       "task_success_fraction": sum(successes) / len(successes),
                       "timing_budget_ms": case["timing_budget_ms"], "feedback_budget_ms": 250}}


def cell_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    scores = {axis: sum(c["scores"][axis] for c in cases) / len(cases) for axis in (*AXES, "speed")}
    mean = sum(scores.values()) / 5
    passed = mean >= 95 and min(scores.values()) >= 93 and all(c["status"] == "passed" for c in cases)
    return {"status": "passed" if passed else "failed", "document_count": len(cases),
            "independent_families": len({c["family"] for c in cases}), "scores": scores,
            "mean": mean, "minimum_axis": min(scores.values()), "case_ids": [c["case_id"] for c in cases]}


def evaluate(manifest_path: Path, observations_path: Path) -> dict[str, Any]:
    root = manifest_path.resolve().parent
    manifest, payload = load_json(manifest_path), load_json(observations_path)
    require(isinstance(manifest, dict) and type(manifest.get("schema_version")) is int
            and manifest["schema_version"] == 1, "Manifest schema_version1 required")
    require(manifest.get("policy_id") == POLICY_ID, "Unsupported policy_id")
    policy_path = artifact(manifest.get("policy"), root, "policy")
    require(isinstance(manifest.get("git_commit"), str) and COMMIT.fullmatch(manifest["git_commit"]) is not None, "Full git_commit required")
    for field in ("pipeline_fingerprint", "environment_fingerprint"):
        require(isinstance(manifest.get(field), str) and HASH.fullmatch(manifest[field]) is not None, f"{field} SHA256 required")
    require(manifest.get("backend") in ("local", "cloud"), "Declared backend required")
    require(isinstance(payload, dict) and type(payload.get("schema_version")) is int
            and payload["schema_version"] == 1 and isinstance(payload.get("observations"), list),
            "Observation schema_version1/list required")
    require(payload.get("manifest_sha256") == sha256(manifest_path), "Observation manifest SHA mismatch")
    cases = manifest.get("cases")
    require(isinstance(cases, list) and cases, "Frozen expected case inventory required")
    expected_ids, family_splits, source_bindings = set(), {}, {}
    source_modes: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for case in cases:
        require(isinstance(case, dict), "Case object required")
        for key in BINDINGS[:-3]:
            require(isinstance(case.get(key), str) and case[key].strip(), f"Case {key} required")
        require(case["case_id"] not in expected_ids, "Duplicate expected case")
        expected_ids.add(case["case_id"])
        require((case["subject"], case["mode"]) in CELLS and case["kind"] in KINDS and case["split"] in SPLITS,
                "Invalid subject/mode/kind/split")
        require(type(case.get("page_count")) is int and case["page_count"] > 0, "Positive source page_count required")
        for field in ("source_sha256", "ground_truth_sha256"):
            require(isinstance(case.get(field), str) and HASH.fullmatch(case[field]) is not None, f"{field} required")
        require(case.get("git_commit") == manifest["git_commit"], "Case source commit mismatch")
        for field in ("environment_fingerprint", "pipeline_fingerprint", "backend"):
            require(case.get(field) == manifest[field], f"Case {field} mismatch")
        require(case.get("timing_budget_ms") == budget_ms(case["mode"], case["page_count"]) and case.get("feedback_budget_ms") == 250,
                "Timing budget differs from frozen code-owned policy")
        for key in ("assertion_ids", "hard_check_ids", "trial_ids"):
            identifiers(case.get(key), key)
        require(HARD_CHECKS.issubset(case["hard_check_ids"]), "Missing required hard-loss checks")
        require(len(case["trial_ids"]) >= 3, "Register at least three trials")
        identifiers(case.get("tags"), "tags", nonempty=False)
        source = artifact(case.get("source"), root, "source")
        gt = artifact(case.get("ground_truth"), root, "ground truth")
        require(sha256(source) == case["source_sha256"] and sha256(gt) == case["ground_truth_sha256"], "Case source/GT binding mismatch")
        case["expected_instances"] = _ground_truth(gt, case)
        try:
            import pymupdf
            with pymupdf.open(source) as pdf:
                require(not pdf.needs_pass and pdf.page_count == case["page_count"], "Actual source PDF page count/protection mismatch")
        except EvaluationError:
            raise
        except Exception as exc:
            raise EvaluationError(f"Invalid source PDF: {exc}") from exc
        source_modes[(case["source_sha256"], case["kind"], case["split"])].add(case["mode"])
        require(family_splits.setdefault(case["family"], case["split"]) == case["split"], "Source family leaked across splits")
        identity = (case["source_id"], case["subject"], case["family"], case["kind"], case["split"])
        require(source_bindings.setdefault(case["source_sha256"], identity) == identity, "Original/source identity relabeled")
    require(all(modes == {"shared-passage", "full-page"} for modes in source_modes.values()),
            "Every registered source requires both modes")
    observations = payload["observations"]
    require(all(isinstance(obs, dict) for obs in observations), "Observation object required")
    ids = identifiers([obs.get("case_id") for obs in observations], "observation cases")
    require(set(ids) == expected_ids, "Missing/extra expected case observations")
    observed = {obs["case_id"]: obs for obs in observations}
    scored = [score_case(case, observed[case["case_id"]], root) for case in cases]
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in scored:
        cohorts[case["kind"] + "/" + case["split"]].append(case)
    require(any(key.startswith("real-original/") for key in cohorts), "Real originals required; synthetic cannot qualify real data")
    reports = {}
    for cohort, documents in cohorts.items():
        cells = {}
        for subject, mode in CELLS:
            selected = [c for c in documents if c["subject"] == subject and c["mode"] == mode]
            require(bool(selected), f"Missing required cell {cohort}/{subject}/{mode}")
            require(len({c["source_sha256"] for c in selected}) == len(selected), "Duplicate source/mode inflates macro average")
            cells[subject + "/" + mode] = cell_summary(selected)
        subgroups = {}
        for tag in sorted({tag for c in documents for tag in c["tags"]}):
            for subject, mode in CELLS:
                selected = [c for c in documents if tag in c["tags"] and c["subject"] == subject and c["mode"] == mode]
                if not selected:
                    continue
                key = subject + "/" + mode + "/" + tag
                if len({c["family"] for c in selected}) >= 3:
                    subgroups[key] = cell_summary(selected)
                else:
                    subgroups[key] = {"status": "insufficient subgroup evidence", "case_ids": [c["case_id"] for c in selected]}
        passed = all(c["status"] == "passed" for c in cells.values()) and all(s["status"] != "failed" for s in subgroups.values())
        reports[cohort] = {"status": "passed" if passed else "failed", "cells": cells, "subgroups": subgroups}
    return {"schema_version": 1, "policy_id": POLICY_ID, "status": "passed" if all(c["status"] == "passed" for c in reports.values()) else "failed",
            "scope": "bounded finite-corpus evaluation; not universal qualification",
            "manifest_sha256": sha256(manifest_path), "observations_sha256": sha256(observations_path),
            "policy_sha256": sha256(policy_path), "git_commit": manifest["git_commit"],
            "ground_truth_sha256": sorted({c["ground_truth_sha256"] for c in cases}),
            "cases": scored, "cohorts": reports,
            "failure_ledger": [{"case_id": c["case_id"], "hard_failures": c["hard_failures"], "mean": c["mean"]} for c in scored if c["status"] != "passed"],
            "advisories": ["Corpus-size recommendations are advisory; report measured independent families and absent challenge subgroups.",
                           "Three-trial p95 uses nearest rank; small-n uncertainty remains.",
                           "Source-reviewed annotations/raw measurements remain external evidence; hashes do not prove semantic correctness.",
                           "EDB parser/readback does not establish ClassIn application openability."]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("observations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.manifest, args.observations)
        code = 0 if result["status"] == "passed" else 1
    except (EvaluationError, OSError) as exc:
        result, code = {"status": "incomplete", "errors": [str(exc)], "scores": None}, 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"[passage-page-quality] {result['status']}: {args.output}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
