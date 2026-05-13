from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()
    metrics = json.loads(Path(args.metrics).read_text())
    lines = [
        "# Day 10 Reliability Final Report",
        "",
        "## Metrics Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        if key == "scenarios":
            continue
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Chaos Scenarios", "", "| Scenario | Status |", "|---|---|"]
    for key, value in metrics.get("scenarios", {}).items():
        lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## Analysis TODO(student)",
        "",
        "Explain what failed, why the fallback path worked or did not work, and what you would change before production.",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines))

    csv_path = Path(args.out).with_name("metrics.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        for key, value in metrics.items():
            if key == "scenarios":
                continue
            writer.writerow([key, value])
        for key, value in metrics.get("scenarios", {}).items():
            writer.writerow([f"scenario:{key}", value])

    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
