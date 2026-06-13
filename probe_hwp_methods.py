#!/usr/bin/env python3
"""Compare HWP/HWPX text extraction methods on local samples."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import preprocess


HANGUL_EXTENSIONS = {".hwp", ".hwpx"}


@dataclass(frozen=True)
class ProbeMethod:
    name: str
    extractor: Callable[[Path, int], str]


def normalized_text(value: str | Path) -> str:
    return unicodedata.normalize("NFC", str(value))


def iter_hangul_files(source: Path) -> list[Path]:
    if source.is_file() and source.suffix.lower() in HANGUL_EXTENSIONS:
        return [source]
    if not source.is_dir():
        return []
    return sorted(
        [path for path in source.iterdir() if path.is_file() and path.suffix.lower() in HANGUL_EXTENSIONS],
        key=lambda path: normalized_text(path.name),
    )


def summarize_text(
    method: str,
    text: str,
    *,
    elapsed_s: float = 0.0,
    error: str = "",
) -> dict[str, Any]:
    clean_text = str(text or "").replace("\x00", "").strip()
    signals = preprocess._summarize_hwp_text_problem_signals(clean_text)
    numbered = int(signals.get("hwp_text_numbered_problem_count") or 0)
    stem = int(signals.get("hwp_text_stem_problem_count") or 0)
    score = preprocess._hwp_text_problem_signal_score(clean_text)
    return {
        "method": method,
        "ok": bool(clean_text) and not error,
        "chars": len(clean_text),
        "lines": len([line for line in clean_text.splitlines() if line.strip()]),
        "numbered": numbered,
        "stem": stem,
        "score": score,
        "elapsed_s": round(float(elapsed_s), 3),
        "error": error,
    }


def best_method_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok_results = [result for result in results if result.get("ok")]
    if not ok_results:
        return {}
    non_rhwp_text_results = [result for result in ok_results if result.get("method") != "rhwp-text"]
    rhwp_text_results = [result for result in ok_results if result.get("method") == "rhwp-text"]
    if non_rhwp_text_results and rhwp_text_results:
        non_rhwp_best = max(
            non_rhwp_text_results,
            key=lambda result: (int(result.get("score") or 0), int(result.get("chars") or 0)),
        )
        rhwp_best = max(
            rhwp_text_results,
            key=lambda result: (int(result.get("score") or 0), int(result.get("chars") or 0)),
        )
        non_rhwp_score = int(non_rhwp_best.get("score") or 0)
        rhwp_score = int(rhwp_best.get("score") or 0)
        if non_rhwp_score >= 10 and rhwp_score > int(non_rhwp_score * 1.25):
            ok_results = non_rhwp_text_results
    return max(
        ok_results,
        key=lambda result: (
            int(result.get("score") or 0),
            int(result.get("chars") or 0),
            -float(result.get("elapsed_s") or 0.0),
        ),
    )


def _extract_with_command(command_prefix: list[str], source_path: Path, timeout_seconds: int) -> str:
    result = subprocess.run(
        [*command_prefix, str(source_path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:300]
        raise RuntimeError(f"exit {result.returncode}: {detail}")
    return (result.stdout or "").replace("\x00", "").strip()


def _wrap_preprocess_extractor(func: Callable[..., str]) -> Callable[[Path, int], str]:
    def extract(source_path: Path, timeout_seconds: int) -> str:
        return func(source_path, timeout_seconds=timeout_seconds)

    return extract


def _iter_kordoc_npx_commands(include_kordoc_npx: bool) -> list[list[str]]:
    if not include_kordoc_npx:
        return []
    npx = shutil.which("npx")
    if not npx:
        return []
    return [[npx, "-y", "kordoc"]]


def build_methods(*, include_kordoc_npx: bool = False) -> list[ProbeMethod]:
    methods: list[ProbeMethod] = []
    if preprocess._iter_pyhwp_text_converter_commands():
        methods.append(ProbeMethod("hwp5txt", _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_pyhwp)))
    if preprocess._iter_unhwp_text_converter_commands():
        methods.append(ProbeMethod("unhwp", _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_unhwp)))
    if preprocess._iter_hwp_hwpx_parser_text_converter_commands():
        methods.append(
            ProbeMethod(
                "hwp-hwpx-parser",
                _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_hwp_hwpx_parser),
            )
        )
    if preprocess._iter_rhwp_python_text_converter_commands():
        methods.append(ProbeMethod("rhwp-python", _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_rhwp_python)))
    if preprocess._iter_rhwp_converter_commands():
        methods.extend(
            [
                ProbeMethod("rhwp-text", _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_rhwp)),
                ProbeMethod("rhwp-markdown", _wrap_preprocess_extractor(preprocess._extract_hwp_markdown_with_rhwp)),
            ]
        )
    if preprocess._iter_hwpilot_text_converter_commands():
        methods.append(ProbeMethod("hwpilot", _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_hwpilot)))
    if preprocess._iter_kordoc_text_converter_commands():
        methods.append(ProbeMethod("kordoc", _wrap_preprocess_extractor(preprocess._extract_hwp_text_with_kordoc)))
    for index, command in enumerate(_iter_kordoc_npx_commands(include_kordoc_npx), start=1):
        name = "kordoc-npx" if index == 1 else f"kordoc-npx-{index}"
        methods.append(ProbeMethod(name, lambda source, timeout, cmd=command: _extract_with_command(cmd, source, timeout)))
    return methods


def probe_file(source_path: Path, methods: list[ProbeMethod], *, timeout_seconds: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for method in methods:
        start = time.monotonic()
        try:
            text = method.extractor(source_path, timeout_seconds)
            result = summarize_text(method.name, text, elapsed_s=time.monotonic() - start)
        except Exception as exc:
            result = summarize_text(method.name, "", elapsed_s=time.monotonic() - start, error=f"{type(exc).__name__}: {exc}")
        results.append(result)
    best = best_method_result(results)
    return {
        "file": normalized_text(source_path.name),
        "path": str(source_path),
        "best_method": best.get("method") or "",
        "best_score": int(best.get("score") or 0),
        "best_numbered": int(best.get("numbered") or 0),
        "best_stem": int(best.get("stem") or 0),
        "best_chars": int(best.get("chars") or 0),
        "methods": results,
    }


def format_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| file | best | score | numbered | stem | chars |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {file} | {best} | {score} | {numbered} | {stem} | {chars} |".format(
                file=str(row.get("file") or "").replace("|", "\\|"),
                best=str(row.get("best_method") or "-").replace("|", "\\|"),
                score=int(row.get("best_score") or 0),
                numbered=int(row.get("best_numbered") or 0),
                stem=int(row.get("best_stem") or 0),
                chars=int(row.get("best_chars") or 0),
            )
        )
    return "\n".join(lines)


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins: dict[str, int] = {}
    for row in rows:
        method = str(row.get("best_method") or "")
        if method:
            wins[method] = wins.get(method, 0) + 1
    return {
        "sample_count": len(rows),
        "method_wins": dict(sorted(wins.items())),
        "max_score": max((int(row.get("best_score") or 0) for row in rows), default=0),
        "min_score": min((int(row.get("best_score") or 0) for row in rows), default=0),
    }


def write_outputs(rows: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summarize_rows(rows),
        "files": rows,
    }
    (output_dir / "probe_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "probe_summary.md").write_text(format_markdown(rows) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare available HWP text extraction methods.")
    parser.add_argument("source", type=Path, help="A .hwp/.hwpx file or a folder of samples")
    parser.add_argument("--output-dir", type=Path, default=Path(".app_runtime/hwp_method_probe/latest"))
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--include-kordoc-npx", action="store_true", help="Also try `npx -y kordoc` as a probe-only method")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = iter_hangul_files(args.source)
    methods = build_methods(include_kordoc_npx=args.include_kordoc_npx)
    if not files:
        print("No HWP/HWPX files found.")
        return 1
    if not methods:
        print("No HWP/HWPX text extraction methods found.")
        return 2

    rows = [probe_file(path, methods, timeout_seconds=args.timeout_seconds) for path in files]
    write_outputs(rows, args.output_dir)
    summary = summarize_rows(rows)
    wins = ", ".join(f"{name}:{count}" for name, count in summary["method_wins"].items()) or "-"
    print(f"Probe summary: samples {summary['sample_count']} · wins {wins} · score {summary['min_score']}..{summary['max_score']}")
    print(args.output_dir / "probe_summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
