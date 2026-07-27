#!/usr/bin/env python3
"""Evaluate the product's AI capability controls against a fixed 0-10 rubric.

The scorecard intentionally mixes executable evidence (the live OCR benchmark)
with code-and-regression-test controls. It does not claim that static controls
prove production accuracy; missing real-corpus or semantic-comparison evidence
remains visible as a zero-scored metric and a development backlog item.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUBRIC_VERSION = "ai-capability-v1"
DEFAULT_TARGET_AVERAGE = 8.0
DEFAULT_TARGET_MINIMUM = 6.8
DIMENSION_WEIGHTS = {
    "recognition": 0.30,
    "output_quality": 0.25,
    "efficiency": 0.15,
    "economics": 0.15,
    "user_control": 0.15,
}
DIMENSION_LABELS = {
    "recognition": "인식",
    "output_quality": "결과 품질",
    "efficiency": "효율·신뢰성",
    "economics": "경제성·비용",
    "user_control": "사용자 통제",
}


@dataclass(frozen=True)
class Metric:
    metric_id: str
    label: str
    weight: float
    ratio: float
    evidence: str
    evidence_type: str = "code_and_test"
    backlog: str | None = None

    @property
    def points(self) -> float:
        return round(self.weight * max(0.0, min(1.0, self.ratio)), 4)

    @property
    def passed(self) -> bool:
        return self.ratio >= 0.999

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["ratio"] = round(max(0.0, min(1.0, self.ratio)), 4)
        row["points"] = self.points
        row["passed"] = self.passed
        return row


class Evidence:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, str] = {}

    def text(self, relative_path: str) -> str:
        if relative_path not in self._cache:
            path = self.root / relative_path
            self._cache[relative_path] = (
                path.read_text(encoding="utf-8") if path.exists() else ""
            )
        return self._cache[relative_path]

    def has(self, relative_path: str, *needles: str) -> bool:
        source = self.text(relative_path)
        return bool(source) and all(needle in source for needle in needles)

    def has_any(self, relative_path: str, *needles: str) -> bool:
        source = self.text(relative_path)
        return bool(source) and any(needle in source for needle in needles)


def _binary(
    metric_id: str,
    label: str,
    weight: float,
    condition: bool,
    evidence: str,
    *,
    backlog: str | None = None,
) -> Metric:
    return Metric(
        metric_id=metric_id,
        label=label,
        weight=weight,
        ratio=1.0 if condition else 0.0,
        evidence=evidence,
        backlog=None if condition else backlog,
    )


def _load_benchmark(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _model_summary(benchmark: dict[str, Any], model: str) -> dict[str, Any]:
    summary = benchmark.get("summary")
    if not isinstance(summary, dict):
        return {}
    row = summary.get(model)
    return row if isinstance(row, dict) else {}


def _float_ratio(value: Any, target: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, number / target))


def build_metrics(root: Path, benchmark_path: Path) -> dict[str, list[Metric]]:
    evidence = Evidence(root)
    benchmark = _load_benchmark(benchmark_path)
    balanced = _model_summary(benchmark, "gemini-3.5-flash")
    economy = _model_summary(benchmark, "gemini-3.5-flash-lite")
    routed_economy = benchmark.get("economy_routing_summary")
    if not isinstance(routed_economy, dict):
        routed_economy = economy
    total_errors = sum(
        int(row.get("error_count") or 0)
        for row in (balanced, economy)
        if isinstance(row, dict)
    )
    live_call_count = sum(
        int(row.get("call_count") or 0)
        for row in (balanced, economy)
        if isinstance(row, dict)
    )
    source_count = int(benchmark.get("source_count") or 0)

    recognition = [
        _binary(
            "rec.exact_schema",
            "정확 전사 프롬프트와 구조화 응답 스키마",
            1.4,
            evidence.has(
                "ocr_backend.py",
                "_OCR_RESPONSE_SCHEMA",
                "Transcribe this Korean exam crop exactly",
                '"temperature": 0.0',
            ),
            "ocr_backend.py: structured Gemini OCR contract",
            backlog="OCR 응답 스키마와 exact-copy 프롬프트를 함께 고정한다.",
        ),
        _binary(
            "rec.offline_route",
            "로컬 OCR이 클라우드로 우회하지 않음",
            1.2,
            evidence.has(
                "test_ai_global_settings.py",
                "test_local_ocr_mode_never_falls_through_to_cloud",
                "NoOcrBackend",
            ),
            "test_ai_global_settings.py: hard offline route regression",
            backlog="local 모드의 provider fallback을 차단하고 회귀 테스트를 추가한다.",
        ),
        _binary(
            "rec.risk_escalation",
            "경제형 OCR의 고신뢰도 표기 변형을 품질 모델로 재검증",
            1.6,
            evidence.has(
                "build_structured_page_json.py",
                "_ocr_exactness_risk",
                'reason == "exactness_risk"',
                "DEFAULT_GEMINI_OCR_MODEL",
            )
            and evidence.has(
                "test_recognition_speed_quality.py",
                "test_economy_ocr_latex_normalization_triggers_exactness_escalation",
            ),
            "build_structured_page_json.py + test_recognition_speed_quality.py",
            backlog="신뢰도 외 exactness-risk 신호와 품질 모델 재검증을 구현한다.",
        ),
        _binary(
            "rec.cache_identity",
            "소스 해시·모델 동작 버전을 포함한 OCR 캐시 식별",
            1.0,
            evidence.has(
                "build_structured_page_json.py",
                "_build_ocr_cache_identity",
                "backend_fingerprint",
                "sha256",
            ),
            "build_structured_page_json.py: stable OCR cache identity",
            backlog="파일 stat 외 내용 해시와 모델 fingerprint를 캐시 키에 포함한다.",
        ),
        Metric(
            "rec.live_balanced_min",
            "균형형 OCR 실측 최저 유사도 ≥ 0.98",
            2.2,
            _float_ratio(balanced.get("minimum_similarity_to_expected"), 0.98),
            (
                f"{benchmark_path}: min_similarity="
                f"{balanced.get('minimum_similarity_to_expected', 'missing')}"
            ),
            evidence_type="live_benchmark",
            backlog="균형형 OCR 최저 유사도를 0.98 이상으로 올린다.",
        ),
        Metric(
            "rec.live_economy_min",
            "경제형+선택 재검증 경로 실측 최저 유사도 ≥ 0.98",
            1.2,
            _float_ratio(routed_economy.get("minimum_similarity_to_expected"), 0.98),
            (
                f"{benchmark_path}: routed_min_similarity="
                f"{routed_economy.get('minimum_similarity_to_expected', 'missing')}, "
                f"escalations={routed_economy.get('escalation_count', 'unavailable')}"
            ),
            evidence_type="live_benchmark",
            backlog="경제형+선택 재검증 경로의 최저 유사도를 0.98 이상으로 올린다.",
        ),
        _binary(
            "rec.live_errors",
            "균형형·경제형 실측 API 오류 0건",
            0.7,
            live_call_count > 0 and total_errors == 0,
            f"{benchmark_path}: calls={live_call_count}, errors={total_errors}",
            backlog="실측 호출 오류를 제거하고 실패 원인을 보고서에 남긴다.",
        ),
        _binary(
            "rec.real_corpus",
            "실제 문서 OCR 코퍼스가 벤치마크에 포함됨",
            0.7,
            source_count > 0,
            f"{benchmark_path}: real_source_count={source_count}",
            backlog="개인정보를 제거한 실제 시험지 표본을 정답 전사와 함께 추가한다.",
        ),
    ]

    output_quality = [
        _binary(
            "quality.safe_auto",
            "자동 이미지 개선은 비생성 보존 모드",
            1.4,
            evidence.has("app_server.py", "def _resolved_image_enhance_mode", 'return "preserve"'),
            "app_server.py: auto -> deterministic preserve",
        ),
        _binary(
            "quality.original_source",
            "반복 개선도 최초 원본에서 재시작",
            1.0,
            evidence.has("app_server.py", "def _original_problem_image_path", "originalImagePath"),
            "app_server.py: original image provenance",
        ),
        _binary(
            "quality.pixel_gate",
            "생성 결과의 누락·구조 변화 콘텐츠 게이트",
            1.3,
            evidence.has(
                "image_reconstruction_backend.py",
                "def analyze_reconstruction_content_preservation",
                "formula_row_loss",
                "glyph_structure_changed",
            ),
            "image_reconstruction_backend.py: content-preservation analysis",
        ),
        _binary(
            "quality.retry_fallback",
            "게이트 실패 시 1회 재시도 후 결정론적 폴백",
            1.5,
            evidence.has(
                "app_server.py",
                "ai_content_retry",
                "content_safe_fallback",
                "_result_passes_content_gate",
            ),
            "app_server.py: bounded recovery path",
        ),
        _binary(
            "quality.semantic_review",
            "생성형 결과는 의미 동일성 미검증 상태를 사용자 검토로 노출",
            1.2,
            evidence.has(
                "app_server.py",
                "def _semantic_text_preservation_gate",
                "semantic_ocr_comparison_unavailable",
                "review_required",
            ),
            "app_server.py: semantic review guardrail",
        ),
        _binary(
            "quality.formula_guard",
            "수학·과학 수식 누락 전용 위험 플래그",
            0.9,
            evidence.has(
                "app_server.py",
                "ai_image_formula_loss_suspected",
                'subject in {"math", "science"}',
            ),
            "app_server.py: subject-aware formula-loss review",
        ),
        _binary(
            "quality.exact_copy_prompt",
            "문자·수식 불변 exact-copy 생성 프롬프트",
            0.9,
            evidence.has(
                "image_reconstruction_backend.py",
                "TEXT_PRIORITY_RECONSTRUCTION_PROMPT",
                "glyph count as immutable evidence",
            ),
            "image_reconstruction_backend.py: exact-copy prompt",
        ),
        _binary(
            "quality.regression_tests",
            "누락·문자 구조·폴백·의미 검토 회귀 테스트",
            0.8,
            evidence.has(
                "test_openai_image_reconstruction.py",
                "test_content_preservation_catches_missing_formula_row",
                "test_enhance_image_uses_content_safe_fallback_after_two_rejected_generations",
                "test_explicit_ai_for_korean_requires_semantic_text_review",
            ),
            "test_openai_image_reconstruction.py",
        ),
        _binary(
            "quality.automatic_semantic_diff",
            "원본/결과 OCR 의미 비교 자동 판정",
            1.0,
            evidence.has_any(
                "app_server.py",
                "semantic_ocr_comparison_verified",
                "semantic_text_diff_pass",
            ),
            "not implemented; current code correctly marks it unverified",
            backlog="원본과 생성 결과를 독립 OCR로 비교하되 오판 시 원본을 유지하는 게이트를 추가한다.",
        ),
    ]

    efficiency = [
        _binary(
            "eff.trusted_pdf_skip",
            "신뢰 가능한 PDF 텍스트 블록 OCR 생략",
            1.3,
            evidence.has(
                "build_structured_page_json.py",
                "_should_skip_ocr_for_trusted_block",
                "trusted_pdf_text_marker",
            ),
            "build_structured_page_json.py",
        ),
        _binary(
            "eff.cache",
            "1차·재검증·페이지 보정 캐시",
            1.3,
            evidence.has("build_structured_page_json.py", "load_ocr_result", "gemini_escalated")
            and evidence.has("page_repair.py", "load_ai_repair", "save_ai_repair"),
            "pipeline cache at OCR and repair stages",
        ),
        _binary(
            "eff.concurrency",
            "페이지/블록 호출 동시성 상한",
            1.2,
            evidence.has(
                "build_structured_page_json.py",
                "BoundedSemaphore",
                "resolve_global_ocr_worker_limit",
            ),
            "build_structured_page_json.py",
        ),
        _binary(
            "eff.lazy_backend",
            "캐시 적중 시 AI 백엔드 지연 생성",
            1.0,
            evidence.has(
                "build_structured_page_json.py",
                "_lazy_cache_backend_name",
                "def _get_backend",
            ),
            "build_structured_page_json.py",
        ),
        _binary(
            "eff.circuit_breaker",
            "연속 provider 실패 회로 차단",
            1.2,
            evidence.has(
                "ocr_backend.py",
                "_GEMINI_FAILURES",
                "retry_at",
                "circuit_open",
            ),
            "ocr_backend.py",
        ),
        _binary(
            "eff.negative_capability",
            "로컬 OCR 미설치 탐지 결과 단기 캐시",
            1.0,
            evidence.has(
                "ocr_backend.py",
                "DEFAULT_OCR_NEGATIVE_CAPABILITY_TTL_SECONDS",
                "_TESSERACT_NEGATIVE_PROBE_AT",
            ),
            "ocr_backend.py",
        ),
        _binary(
            "eff.payload_compression",
            "큰 OCR 이미지는 JPEG로 전송량 절감",
            1.0,
            evidence.has(
                "ocr_backend.py",
                "def _encode_image",
                'format="JPEG"',
                "quality=92",
            ),
            "ocr_backend.py",
        ),
        _binary(
            "eff.telemetry",
            "단계별 지연·캐시·호출량 메타데이터",
            1.0,
            evidence.has(
                "build_structured_page_json.py",
                "ocr_latency_ms",
                "api_call_block_count",
                "cache_hit_count",
            ),
            "build_structured_page_json.py",
        ),
        _binary(
            "eff.regression_tests",
            "속도 경로·캐시·동시성 회귀 테스트",
            1.0,
            evidence.has(
                "test_recognition_speed_quality.py",
                "test_explicit_backend_cache_hit_defers_backend_creation",
                "test_build_page_model_passes_stable_cache_identity",
            ),
            "test_recognition_speed_quality.py",
        ),
    ]

    economics = [
        _binary(
            "cost.versioned_prices",
            "모델별 단가표 버전 고정",
            1.2,
            evidence.has(
                "ai_usage.py",
                "AI_PRICING_VERSION",
                "MODEL_TOKEN_PRICING_USD_PER_MILLION",
            ),
            "ai_usage.py",
        ),
        _binary(
            "cost.cached_tokens",
            "캐시 입력·추론 토큰을 구분한 비용 계산",
            0.8,
            evidence.has(
                "ai_usage.py",
                "cached_content_token_count",
                "uncached_tokens",
                'token_pricing["cached_input"]',
            ),
            "ai_usage.py",
        ),
        _binary(
            "cost.breakdown",
            "모델·단계별 USD/KRW 비용 집계",
            1.2,
            evidence.has(
                "ai_usage.py",
                "by_model",
                "by_stage",
                "estimated_krw",
            ),
            "ai_usage.py",
        ),
        _binary(
            "cost.ui_visibility",
            "사용자 화면에 실행 비용 표시",
            0.8,
            evidence.has("ui_prototype/app.jsx", "ai_cost_summary", "estimated_krw"),
            "ui_prototype/app.jsx",
        ),
        _binary(
            "cost.economy_profile",
            "저비용 OCR 프로필과 선택적 품질 승격",
            1.0,
            evidence.has(
                "ocr_backend.py",
                "ECONOMY_GEMINI_OCR_MODEL",
                'profile in {"economy"',
            )
            and evidence.has("build_structured_page_json.py", "_ocr_exactness_risk"),
            "ocr_backend.py + build_structured_page_json.py",
        ),
        _binary(
            "cost.non_gen_auto",
            "자동 이미지 개선의 생성 비용 0원 경로",
            1.2,
            evidence.has(
                "app_server.py",
                "Auto mode must be both economical",
                'return "preserve"',
            ),
            "app_server.py",
        ),
        _binary(
            "cost.image_cap",
            "생성 이미지 해상도 비용 상한",
            0.8,
            evidence.has(
                "app_server.py",
                "def _active_image_enhance_size",
                "ACTIVE_IMAGE_ENHANCE_SIZE",
            ),
            "app_server.py",
        ),
        _binary(
            "cost.global_off",
            "전체 AI 비용을 즉시 차단하는 전역 OFF",
            1.0,
            evidence.has("app_server.py", "def _global_ai_enabled", "if not ai_enabled"),
            "app_server.py",
        ),
        _binary(
            "cost.unpriced_visibility",
            "가격 미등록 요청을 별도 집계",
            0.8,
            evidence.has("ai_usage.py", "unpriced_request_count", "estimate_only"),
            "ai_usage.py",
        ),
        _binary(
            "cost.hard_budget",
            "실행 전 사용자 지정 비용 예산 상한",
            1.2,
            evidence.has_any(
                "app_server.py",
                "ai_budget_limit_krw",
                "max_ai_cost_krw",
            ),
            "not implemented; costs are estimated after usage",
            backlog="세션별 예상 비용 상한과 초과 전 확인/중단 정책을 추가한다.",
        ),
    ]

    user_control = [
        _binary(
            "control.persisted_toggle",
            "AI ON/OFF 설정 영속 저장",
            1.5,
            evidence.has(
                "user_settings.py",
                "_AI_ENABLED_KEY",
                "def update_ai_enabled",
            ),
            "user_settings.py",
        ),
        _binary(
            "control.server_enforcement",
            "서버가 재시도·생성·인식에서 OFF 강제",
            2.0,
            evidence.has("app_server.py", "def _global_ai_enabled", "if not _global_ai_enabled()")
            and evidence.has("app_server.py", "if not ai_enabled and requested_mode == \"ai\""),
            "app_server.py",
        ),
        _binary(
            "control.ui_toggle",
            "설정 화면 ON/OFF 버튼과 상태 표시",
            1.2,
            evidence.has(
                "ui_prototype/app.jsx",
                "onToggleAi",
                "aiToggleBusy",
                "{aiEnabled ? '켜짐' : '꺼짐'}",
            ),
            "ui_prototype/app.jsx",
        ),
        _binary(
            "control.ui_disable",
            "OFF 상태에서 AI 작업 버튼 비활성화",
            1.0,
            evidence.has(
                "ui_prototype/app.jsx",
                "disabled={!canEnhanceCurrent || !aiEnabled",
                "AI 기능을 켜면",
            ),
            "ui_prototype/app.jsx",
        ),
        _binary(
            "control.keys_preserved",
            "OFF 전환 시 저장 API 키 유지",
            1.0,
            evidence.has(
                "test_ai_global_settings.py",
                "test_ai_toggle_persists_without_clearing_saved_keys",
            ),
            "test_ai_global_settings.py",
        ),
        _binary(
            "control.no_secret_echo",
            "설정 응답에 API 키 원문 미노출",
            1.0,
            evidence.has(
                "test_ai_global_settings.py",
                'summarize_for_response(tmp_path)["geminiApiKey"] == ""',
            ),
            "test_ai_global_settings.py",
        ),
        _binary(
            "control.explicit_generation",
            "생성형 이미지 개선은 명시적 선택만 허용",
            1.0,
            evidence.has(
                "app_server.py",
                "Generative",
                "explicit opt-in",
                'return "preserve"',
            ),
            "app_server.py",
        ),
        _binary(
            "control.session_state",
            "세션 응답에 실제 적용 AI 상태 기록",
            0.5,
            evidence.has("app_server.py", 'session["ai_enabled"]', 'session["aiEnabled"]'),
            "app_server.py",
        ),
        _binary(
            "control.regression_tests",
            "전역 OFF·로컬 강제·키 보존 회귀 테스트",
            0.8,
            evidence.has(
                "test_ai_global_settings.py",
                "test_server_blocks_ai_mutations_when_global_switch_is_off",
                "test_local_ocr_mode_never_falls_through_to_cloud",
            ),
            "test_ai_global_settings.py",
        ),
    ]

    return {
        "recognition": recognition,
        "output_quality": output_quality,
        "efficiency": efficiency,
        "economics": economics,
        "user_control": user_control,
    }


def evaluate(
    root: Path,
    benchmark_path: Path,
    *,
    target_average: float = DEFAULT_TARGET_AVERAGE,
    target_minimum: float = DEFAULT_TARGET_MINIMUM,
) -> dict[str, Any]:
    metrics_by_dimension = build_metrics(root, benchmark_path)
    dimensions: dict[str, Any] = {}
    backlog: list[dict[str, Any]] = []

    for dimension, metrics in metrics_by_dimension.items():
        possible = round(sum(metric.weight for metric in metrics), 4)
        if abs(possible - 10.0) > 0.001:
            raise RuntimeError(f"{dimension} rubric weights must total 10.0, got {possible}")
        score = round(sum(metric.points for metric in metrics), 2)
        rows = [metric.to_dict() for metric in metrics]
        dimensions[dimension] = {
            "label": DIMENSION_LABELS[dimension],
            "weight": DIMENSION_WEIGHTS[dimension],
            "score": score,
            "possible": possible,
            "metrics": rows,
        }
        backlog.extend(
            {
                "dimension": dimension,
                "metric_id": metric.metric_id,
                "priority_loss": round(metric.weight - metric.points, 2),
                "action": metric.backlog,
            }
            for metric in metrics
            if metric.backlog and not metric.passed
        )

    overall = round(
        sum(
            dimensions[name]["score"] * DIMENSION_WEIGHTS[name]
            for name in DIMENSION_WEIGHTS
        ),
        2,
    )
    simple_average = round(
        sum(row["score"] for row in dimensions.values()) / len(dimensions),
        2,
    )
    minimum_dimension = min(
        dimensions,
        key=lambda name: dimensions[name]["score"],
    )
    minimum_score = dimensions[minimum_dimension]["score"]
    backlog.sort(key=lambda row: (-float(row["priority_loss"]), str(row["metric_id"])))

    return {
        "rubric_version": RUBRIC_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "AI OCR, page repair, image reconstruction, runtime efficiency, "
            "cost observability, and user AI controls"
        ),
        "targets": {
            "weighted_average": target_average,
            "minimum_dimension": target_minimum,
        },
        "overall_score": overall,
        "simple_average": simple_average,
        "minimum_dimension": minimum_dimension,
        "minimum_score": minimum_score,
        "passed": overall >= target_average and minimum_score >= target_minimum,
        "dimensions": dimensions,
        "priority_backlog": backlog,
        "guardrails": [
            "정적 코드·테스트 존재는 실데이터 정확도를 증명하지 않는다.",
            "인식 정확도 점수에는 제공된 live OCR benchmark만 사용한다.",
            "실문서 source_count=0이면 real-corpus 항목은 반드시 0점이다.",
            "생성 이미지의 자동 의미 비교가 없으면 해당 항목은 반드시 0점이다.",
            "비용은 provider 공개 단가 기반 추정치이며 청구액과 다를 수 있다.",
        ],
    }


def _markdown(report: dict[str, Any], benchmark_path: Path) -> str:
    lines = [
        "# AI 기능 평가표",
        "",
        f"- 평가 버전: `{report['rubric_version']}`",
        f"- 가중 평균: **{report['overall_score']:.2f}/10** "
        f"(목표 {report['targets']['weighted_average']:.1f})",
        f"- 단순 평균: **{report['simple_average']:.2f}/10**",
        f"- 최저 영역: **{report['dimensions'][report['minimum_dimension']]['label']} "
        f"{report['minimum_score']:.2f}/10** "
        f"(목표 {report['targets']['minimum_dimension']:.1f})",
        f"- 판정: **{'통과' if report['passed'] else '미달'}**",
        f"- 실측 OCR 근거: `{benchmark_path}`",
        "",
        "## 영역별 점수",
        "",
        "| 영역 | 비중 | 점수 |",
        "|---|---:|---:|",
    ]
    for name, row in report["dimensions"].items():
        lines.append(f"| {row['label']} | {row['weight'] * 100:.0f}% | {row['score']:.2f} |")

    for name, row in report["dimensions"].items():
        lines.extend(
            [
                "",
                f"## {row['label']}",
                "",
                "| 지표 | 배점 | 획득 | 근거 |",
                "|---|---:|---:|---|",
            ]
        )
        for metric in row["metrics"]:
            evidence = str(metric["evidence"]).replace("|", "\\|")
            lines.append(
                f"| {metric['label']} | {metric['weight']:.1f} | "
                f"{metric['points']:.2f} | {evidence} |"
            )

    lines.extend(["", "## 우선 개발 항목", ""])
    if report["priority_backlog"]:
        for index, item in enumerate(report["priority_backlog"], start=1):
            lines.append(
                f"{index}. `{item['metric_id']}` (+{item['priority_loss']:.2f}점): "
                f"{item['action']}"
            )
    else:
        lines.append("- 현재 평가표에서 미달 항목이 없습니다.")

    lines.extend(["", "## 해석 가드레일", ""])
    lines.extend(f"- {item}" for item in report["guardrails"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    script_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=script_root)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=script_root / ".audit" / "ai-ocr-benchmark-current.json",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=script_root / ".audit" / "ai-capability-scorecard.json",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=script_root / "docs" / "ai-capability-scorecard.md",
    )
    parser.add_argument("--target-average", type=float, default=DEFAULT_TARGET_AVERAGE)
    parser.add_argument("--target-minimum", type=float, default=DEFAULT_TARGET_MINIMUM)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    benchmark_path = args.benchmark.resolve()
    report = evaluate(
        root,
        benchmark_path,
        target_average=args.target_average,
        target_minimum=args.target_minimum,
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown_out.write_text(_markdown(report, benchmark_path), encoding="utf-8")

    print(
        json.dumps(
            {
                "overall_score": report["overall_score"],
                "simple_average": report["simple_average"],
                "minimum_dimension": report["minimum_dimension"],
                "minimum_score": report["minimum_score"],
                "passed": report["passed"],
                "json_out": str(args.json_out.resolve()),
                "markdown_out": str(args.markdown_out.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 1 if args.strict and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
