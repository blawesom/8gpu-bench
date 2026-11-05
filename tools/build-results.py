#!/usr/bin/env python3
"""8gpu-bench results assembler. Implements spec 07.

Reads runs.jsonl from one or more campaign result directories,
writes results.csv (one row per run) and headlines.md (the
headline table, sorted by output throughput).

Usage:
  build-results.py --runs results/2026Q4 --out results/2026Q4
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

# results.csv columns (spec 07.2). Metrics come from the runner's
# normalized run records. Missing values stay empty.
COLUMNS = [
    "run_id", "campaign_id", "sut", "stack", "stack_version", "harness",
    "model", "precision", "tp", "payload_profile", "prompt_scenario",
    "regime", "concurrency", "repetition", "flags",
    "ttft_ms_p90", "tpot_ms_p90", "itl_ms_p99",
    "out_tps", "out_tps_per_gpu", "goodput", "hbm_peak_gb",
    "gpu_util_pct", "temp_max_c", "power_mean_w", "j_per_1m_out",
    "raw_dir", "generated_at",
]

# Metric name mapping from runner records to CSV columns.
METRIC_MAP = {
    "ttft_ms_p90": ("ttft", "p90"),
    "tpot_ms_p90": ("tpot", "p90"),
    "itl_ms_p99": ("itl", "p99"),
    "out_tps": ("output_throughput", "mean"),
    "goodput": ("goodput", "mean"),
    "gpu_util_pct": ("gpu_utilization", "mean"),
}

HEADLINE_METRICS = ["out_tps", "out_tps_per_gpu", "goodput",
                    "ttft_ms_p90", "tpot_ms_p90"]


def read_runs(paths: list[str]) -> list[dict]:
    records = []
    for pattern in paths:
        for path in sorted(glob.glob(pattern)):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    return records


def row_from_record(rec: dict, tp_default: int | None) -> dict:
    row = {c: rec.get(c, "") for c in COLUMNS}
    metrics = rec.get("metrics", {})
    for col, (key, stat) in METRIC_MAP.items():
        value = metrics.get(f"{key}_{stat}")
        if value is not None:
            row[col] = value
    tp = rec.get("tp") or tp_default
    out_tps = row.get("out_tps")
    if out_tps and tp:
        row["out_tps_per_gpu"] = round(float(out_tps) / tp, 3)
    return row


def write_csv(rows: list[dict], out_dir: str) -> str:
    path = os.path.join(out_dir, "results.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_headlines(rows: list[dict], out_dir: str) -> str:
    """Headline table (spec 07.3): one table per payload profile."""
    by_profile: dict[str, list[dict]] = {}
    for row in rows:
        by_profile.setdefault(row["payload_profile"], []).append(row)

    lines = ["# Headline tables", "",
             "One table per payload profile. Sorted by output throughput.",
             "Median of repetitions is not applied here. The campaign",
             "report applies it (spec 07.5).", ""]
    for profile in sorted(by_profile):
        lines.append(f"## {profile}")
        lines.append("")
        header = ["sut", "stack", "harness", "concurrency"] + HEADLINE_METRICS + ["flags"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        table = sorted(
            by_profile[profile],
            key=lambda r: float(r.get("out_tps") or 0), reverse=True)
        for row in table:
            cells = [str(row.get(h, "")) for h in header]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    path = os.path.join(out_dir, "headlines.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True,
                        help="runs.jsonl files or directories")
    parser.add_argument("--out", required=True, help="output directory")
    args = parser.parse_args()

    patterns = []
    for entry in args.runs:
        if os.path.isdir(entry):
            patterns.append(os.path.join(entry, "**", "runs.jsonl"))
        else:
            patterns.append(entry)
    records = read_runs(patterns)
    if not records:
        print("no run records found")
        return 1

    rows = [row_from_record(rec, None) for rec in records]
    os.makedirs(args.out, exist_ok=True)
    csv_path = write_csv(rows, args.out)
    md_path = write_headlines(rows, args.out)
    print(f"wrote {csv_path} ({len(rows)} rows)")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
