#!/usr/bin/env python3
"""Build and execute the Upscayl decision-analysis notebook."""

from __future__ import annotations

from pathlib import Path

import nbformat
from nbclient import NotebookClient


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "upscayl_benchmark" / "upscayl_analysis.ipynb"


def markdown(source: str):
    return nbformat.v4.new_markdown_cell(source.strip())


def code(source: str):
    return nbformat.v4.new_code_cell(source.strip())


cells = [
    markdown(
        """
## tl;dr

- Apple M4에서 기존 2단계/3단계의 이미지 변환 커널은 문항당 각각 약 0.07초/0.15초였다.
- 동일한 실제 crop 3건에서 Lite 중앙값은 1.00초, Standard는 11.32초로 Lite가 약 11.3배 빨랐다.
- 정답 원본이 있는 합성 열화 세트에서 Upscayl Standard의 기술 충실도는 92.36점으로 2단계 76.62점보다 높았다. 3단계 점수는 페이지 장식 제거와 획 강화까지 변화로 계산되어 주관적 가독성 점수로 해석하면 안 된다.
- 권장안은 **3단계를 기본값으로 유지하고, 저해상도 crop에만 Upscayl Lite를 선택 적용**하는 것이다.
"""
    ),
    markdown(
        """
## Context & Methods

이 노트북은 EDB의 2단계, 3단계, 로컬 Upscayl을 품질·시간·비용 관점에서 비교한다. 대상 장치는 Apple M4 10-core GPU이며 측정일은 2026-07-14이다.

### Key Assumptions

- 최종 비교 폭은 1600px이다.
- 기술 충실도 점수는 허용 오차 edge F1 40%, 잉크 IoU 35%, alpha 유사도 25%의 합성 지표다.
- 합성 8건만 고해상도 정답 원본을 갖는다.
- 실제 국어·수학·과학 crop 6건은 원본 대비 구조 변화와 시간만 평가한다.
- 클라우드 API 호출은 하지 않았으며 가격은 공식 가격표의 출력 비용을 사용한다.
- 원화 환산은 계획 환율 1 USD = 1,400 KRW를 사용한다.
"""
    ),
    code(
        """
from pathlib import Path
import csv, json, statistics
from collections import defaultdict
from IPython.display import Markdown, display

ROOT = Path.cwd()
DATA = ROOT / "docs" / "upscayl_benchmark"

def load_csv(name):
    with (DATA / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))

def md_table(rows, fields):
    header = "| " + " | ".join(fields) + " |"
    divider = "|" + "|".join(["---"] * len(fields)) + "|"
    body = ["| " + " | ".join(str(row.get(field, "")) for field in fields) + " |" for row in rows]
    return "\\n".join([header, divider, *body])

synthetic = load_csv("synthetic_results.csv")
real = load_csv("real_results.csv")
models = load_csv("model_pilot_results.csv")
summary = json.loads((DATA / "summary.json").read_text(encoding="utf-8"))

print({
    "synthetic_rows": len(synthetic),
    "real_rows": len(real),
    "model_rows": len(models),
    "device": summary["device"],
})
"""
    ),
    markdown("## Data"),
    code(
        """
display(Markdown("### 합성 정답 세트 요약\\n\\n" + md_table(
    summary["synthetic_summary"],
    ["method", "samples", "median_seconds", "mean_technical_fidelity_score", "mean_edge_f1", "mean_ink_iou"],
)))

display(Markdown("### 실제 crop 구조 보존 요약\\n\\n" + md_table(
    summary["real_summary"],
    ["method", "samples", "median_seconds", "mean_technical_fidelity_score", "mean_edge_f1", "mean_ink_iou"],
)))

display(Markdown("### Upscayl 모델 파일럿\\n\\n" + md_table(
    summary["model_summary"],
    ["method", "samples", "median_seconds", "mean_technical_fidelity_score", "mean_edge_f1", "mean_ink_iou"],
)))
"""
    ),
    markdown("## Results"),
    code(
        """
def group_mean(rows, field):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["method"]].append(float(row[field]))
    return {method: statistics.mean(values) for method, values in grouped.items()}

sharpness_synthetic = group_mean(synthetic, "sharpness")
sharpness_real = group_mean(real, "sharpness")
display(Markdown(md_table([
    {
        "method": method,
        "synthetic_sharpness": round(sharpness_synthetic[method], 1),
        "real_sharpness": round(sharpness_real[method], 1),
    }
    for method in sorted(sharpness_real)
], ["method", "synthetic_sharpness", "real_sharpness"])))
"""
    ),
    code(
        """
# Local sequential throughput uses measured median kernel/runtime latency.
latency_seconds = {
    "2단계": 0.0673,
    "3단계": 0.1494,
    "Upscayl Lite": 1.0016,
    "Upscayl Standard": 11.0899,
    "Upscayl Ultrasharp": 11.3840,
}

volumes = [100, 1000, 10000]
throughput_rows = []
for method, seconds in latency_seconds.items():
    for volume in volumes:
        throughput_rows.append({
            "method": method,
            "images": volume,
            "sequential_minutes": round(seconds * volume / 60, 2),
            "sequential_hours": round(seconds * volume / 3600, 2),
        })

display(Markdown(md_table(
    throughput_rows,
    ["method", "images", "sequential_minutes", "sequential_hours"],
)))
"""
    ),
    code(
        """
USD_KRW = 1400
api_prices = {
    "Gemini 3.1 Flash Image 1K": 0.067,
    "Gemini 3.1 Flash Image 2K": 0.101,
    "GPT Image 2 high landscape": 0.165,
    "GPT Image 2 high square": 0.211,
}
cost_rows = []
for method, usd_per_image in api_prices.items():
    for volume in volumes:
        usd = usd_per_image * volume
        cost_rows.append({
            "method": method,
            "images": volume,
            "usd_output_cost": round(usd, 2),
            "krw_output_cost": round(usd * USD_KRW),
        })

display(Markdown(md_table(
    cost_rows,
    ["method", "images", "usd_output_cost", "krw_output_cost"],
)))
"""
    ),
    code(
        """
# Electricity-only estimate. Hardware depreciation and labor are excluded.
POWER_WATTS = 30
ELECTRICITY_KRW_PER_KWH = 200
local_cost_rows = []
for method, seconds in latency_seconds.items():
    if not method.startswith("Upscayl"):
        continue
    for volume in volumes:
        kwh = POWER_WATTS * seconds * volume / 3_600_000
        local_cost_rows.append({
            "method": method,
            "images": volume,
            "estimated_kwh": round(kwh, 5),
            "electricity_krw": round(kwh * ELECTRICITY_KRW_PER_KWH, 2),
        })

display(Markdown(md_table(
    local_cost_rows,
    ["method", "images", "estimated_kwh", "electricity_krw"],
)))
"""
    ),
    markdown(
        """
## Takeaways

1. **2단계는 가장 안전하고 빠른 기본 변환이다.** 이미 선명한 원본은 추가 초해상도 없이도 충분하다.
2. **3단계는 가독성과 페이지 장식 제거를 위한 제품 처리다.** 기술 충실도 점수가 낮은 것은 실패라기보다 의도적인 crop·획 변화가 포함되기 때문이다.
3. **Upscayl은 저해상도 복원 효과가 확실하지만 Standard는 운영 시간이 크다.** Lite는 동일한 표본 3건의 중앙값 비교에서 Standard보다 약 11.3배 빨랐고 구조 보존도 동등 이상이었다.
4. **API 재구성은 예외 처리로 남기는 편이 경제적이다.** 1,000문항을 2K Gemini로 처리하면 출력만 약 $101이며, GPT Image 2 high는 약 $165~$211에 입력 비용이 추가된다.
5. **권장 라우팅은 3단계 기본 + 조건부 Upscayl Lite + API 최후 fallback이다.**
"""
    ),
]


notebook = nbformat.v4.new_notebook(
    cells=cells,
    metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.14"},
    },
)
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
nbformat.write(notebook, OUTPUT)

client = NotebookClient(notebook, timeout=120, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}})
client.execute()
nbformat.write(notebook, OUTPUT)
print(OUTPUT)
