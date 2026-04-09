#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert board_run_summary.json into ui_prototype/prototype_data.js")
    parser.add_argument("summary_path", help="Path to board_run_summary.json")
    parser.add_argument("--output", default="ui_prototype\\prototype_data.js", help="Output JS file path")
    args = parser.parse_args()

    summary = json.loads(Path(args.summary_path).read_text(encoding="utf-8"))
    placements = summary.get("placements", [])
    payload = {
        "problems": [
            {
                "id": item["problem_id"],
                "title": item["title"],
                "subject": item["subject"],
                "imagePath": Path(item["crop_path"]).resolve().as_uri(),
                "actualHeightPages": item["actual_content_height_pages"],
                "overflowAllowed": item["overflow_allowed"],
                "readingHeavy": item["overflow_allowed"],
            }
            for item in placements
        ]
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "window.PROTOTYPE_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
