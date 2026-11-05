#!/usr/bin/env python3
"""8gpu-bench results assembler. Implements spec 07.

Reads runs.jsonl from one or more campaign result directories,
writes results.csv (one row per run, spec 07.2) and headlines.md
(the headline tables, spec 07.3).

Validity (spec 05.7):
  - V3: the assembler computes the coefficient of variation of output
    throughput across the repetitions of each test point. CoV of 5%
    or more marks the row INVALID.
  - Headlines use the median of the repetitions (spec 05.3).

Usage:
  build-results.py --runs results/2026Q4 --out results/2026Q4
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics
import sys

# V3 (spec 05.7): CoV of throughput across repetitions.
MAX_COV_PCT = 5.0

# results.csv columns (spec 07.2). Metrics come from the runner's
# normalized run records. Missing values stay empty.
COLUMNS = [
    "run_id", "campaign_id", "sut", "stack", "stack_version", "harness",
    "model", "model_revision", "precision", "config_lane", "tp",
    "payload_profile", "prompt_scenario", "regime", "traffic_mode",
    "concurrency", "repetition",
    "ttft_ms_p90", "tpot_ms_p90", "itl_ms_p99", "e2e_ms_p90",
    "out_tps", "out_tps_per_gpu", "goodput_tps", "slo_concurrency",
    "hbm_peak_gb", "gpu_util_pct", "temp_max_c", "power_mean_w",
    "j_per_1m_out", "flags", "deviation_note", "error_rate_pct",
    "success_rate_pct", "cov_pct", "raw_dir", "generated_at",
]

# Metric name mapping from runner records to CSV columns.
METRIC_MAP = {
    "ttft_ms_p90": ("ttft", "p90"),
    "tpot_ms_p90": ("tpot", "p90"),
    "itl_ms_p99": ("itl", "p99"),
    "e2e_ms_p90": ("request_latency", "p90"),
    "out_tps": ("output_throughput", "mean"),
    "goodput_tps": ("goodput", "mean"),
    "gpu_util_pct": ("gpu_util_pct", None),
    "temp_max_c": ("temp_max_c", None),
    "hbm_peak_gb": ("hbm_peak_gb", None),
    "power_mean_w": ("power_mean_w", None),
    "error_rate_pct": ("error_rate_pct", None),
}

HEADLINE_METRICS = ["out_tps", "out_tps_per_gpu", "success_rate_pct",
                    "ttft_ms_p90", "tpot_ms_p90", "e2e_ms_p90"]

NUMERIC_COLUMNS = [c for c in COLUMNS
                   if c not in ("run_id", "campaign_id", "sut", "stack",
                                "stack_version", "harness", "model",
                                "model_revision", "precision",
                                "config_lane", "payload_profile",
                                "prompt_scenario", "regime",
                                "traffic_mode", "flags", "deviation_note",
                                "raw_dir", "generated_at")]


def read_runs(paths: list[str]) -> list[dict]:
    """Read runs.jsonl records. Deduplicates by run_id (last wins) and
    drops exact duplicate lines, so re-assembling an appended file is
    idempotent."""
    records: dict[str, dict] = {}
    for pattern in paths:
        for path in sorted(glob.glob(pattern, recursive=True)):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        records[rec["run_id"]] = rec
    return list(records.values())


def row_from_record(rec: dict) -> dict:
    row = {c: rec.get(c, "") for c in COLUMNS}
    metrics = rec.get("metrics", {})
    for col, (key, stat) in METRIC_MAP.items():
        value = metrics.get(f"{key}_{stat}") if stat else metrics.get(key)
        if value is not None:
            row[col] = value
    tp = rec.get("tp")
    out_tps = row.get("out_tps")
    if out_tps and tp:
        row["out_tps_per_gpu"] = round(float(out_tps) / tp, 3)
    # M18: energy per 1M output tokens. With M5 = out_tps and a mean
    # power over the same window, M18 = M16 * 1e6 / M5.
    power = row.get("power_mean_w")
    if out_tps and power:
        row["j_per_1m_out"] = round(float(power) * 1e6 / float(out_tps))
    # M24: request success rate, the delivery view (spec 06.2, 06.9).
    error_rate = row.get("error_rate_pct")
    if isinstance(error_rate, (int, float)):
        row["success_rate_pct"] = round(100.0 - float(error_rate), 2)
    row["flags"] = ",".join(rec.get("flags", []))
    return row


# ---------------------------------------------------------------------------
# Aggregation across repetitions (spec 05.3 median, spec 05.7 V3)
# ---------------------------------------------------------------------------


def group_key(row: dict) -> tuple:
    return (row["sut"], row["stack"], row["stack_version"], row["harness"],
            row["model"], row["model_revision"], row["precision"],
            row["config_lane"], row["tp"],
            row["payload_profile"], row["prompt_scenario"], row["regime"],
            row["traffic_mode"], row["concurrency"])


def aggregate(rows: list[dict]) -> list[dict]:
    """Median of the repetitions per test point, plus the V3 CoV gate.

    Rows that do not compare (different harnesses, models, precisions,
    scenarios, regimes, concurrencies, TPs) are never merged
    (spec 05.8, 07.6).
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault(group_key(row), []).append(row)

    out: list[dict] = []
    for key, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        members.sort(key=lambda r: r["repetition"])
        agg = dict(members[0])
        agg["repetition"] = "median"
        for col in NUMERIC_COLUMNS:
            values = [m[col] for m in members
                      if m[col] not in ("", None)]
            if not values:
                agg[col] = ""
                continue
            numbers = [float(v) for v in values]
            agg[col] = round(statistics.median(numbers), 4)
            # Identity columns keep their integer form.
            if col in ("concurrency", "tp") and agg[col] % 1 == 0:
                agg[col] = int(agg[col])

        # V3: CoV of output throughput across repetitions.
        flags = [f for f in agg["flags"].split(",") if f and f != "INVALID"]
        invalid = any("INVALID" in m["flags"] for m in members)
        tps_values = [float(m["out_tps"]) for m in members
                      if m["out_tps"] not in ("", None)]
        if len(tps_values) == len(members) and len(tps_values) >= 2 \
                and statistics.mean(tps_values) > 0:
            cov = 100.0 * statistics.stdev(tps_values) \
                / statistics.mean(tps_values)
            agg["cov_pct"] = round(cov, 2)
            if cov >= MAX_COV_PCT:
                invalid = True
                flags.append("INVALID-COV")
        agg["flags"] = ",".join(flags + (["INVALID"] if invalid else []))
        out.append(agg)
    return out


def write_csv(rows: list[dict], out_dir: str) -> str:
    path = os.path.join(out_dir, "results.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def pick_headline_row(rows: list[dict]) -> dict | None:
    """The headline row: the highest measured concurrency (spec 07.3).
    Prefers a valid row over an INVALID one at the same concurrency."""
    by_concurrency: dict[int, list[dict]] = {}
    for row in rows:
        try:
            concurrency = int(row["concurrency"])
        except (TypeError, ValueError):
            continue
        by_concurrency.setdefault(concurrency, []).append(row)
    if not by_concurrency:
        return None
    for concurrency in sorted(by_concurrency, reverse=True):
        candidates = by_concurrency[concurrency]
        valid = [r for r in candidates if "INVALID" not in r.get("flags", "")]
        row = dict(valid[0] if valid else candidates[0])
        row["slo_concurrency"] = ""
        return row
    return None


def write_headlines(rows: list[dict], out_dir: str) -> str:
    """Headline tables (spec 07.3): one table per model and regime.

    Rows: one per SUT x stack x harness, at the highest measured
    concurrency. Never mixes prompt scenarios, precisions, config
    lanes or traffic modes (spec 07.6).
    """
    # One table per (model, precision, scenario, regime, profile,
    # lane, traffic mode): the spec 07.6 rules forbid mixing these,
    # so they must split the tables. Different payload profiles are
    # different workloads.
    tables: dict[tuple, list[dict]] = {}
    for row in rows:
        tables.setdefault((row["model"], row["precision"],
                            row["prompt_scenario"], row["regime"],
                            row["payload_profile"], row["config_lane"],
                            row["traffic_mode"]),
                           []).append(row)

    lines = ["# Headline tables", "",
             "One table per model, precision, scenario, regime, payload "
             "profile, config lane and traffic mode (spec 07.3, 07.6).",
             "Rows sit at the highest measured concurrency. "
             "Values are the median of the repetitions (spec 05.3).",
             "INVALID rows are marked (spec 05.7).", ""]

    for key in sorted(tables):
        model, precision, scenario, regime, profile, lane, mode = key
        by_lane: dict[tuple, list[dict]] = {}
        for row in tables[key]:
            by_lane.setdefault((row["sut"], row["stack"], row["harness"]),
                               []).append(row)
        lanes = [pick_headline_row(v) for v in by_lane.values()]
        lanes = [r for r in lanes if r]
        if not lanes:
            continue
        lanes.sort(key=lambda r: float(r.get("out_tps") or 0), reverse=True)

        lines.append(f"## {model} / {precision} / {scenario} / {regime} "
                     f"/ {profile} / {lane} / {mode}")
        lines.append("")
        header = (["sut", "stack", "harness", "concurrency"]
                  + HEADLINE_METRICS
                  + ["temp_max_c", "power_mean_w", "j_per_1m_out",
                     "flags"])
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        for row in lanes:
            cells = []
            for h in header:
                cell = str(row.get(h, ""))
                if h in ("out_tps", "out_tps_per_gpu") and cell \
                        and "INVALID" in row.get("flags", ""):
                    cell += " (INVALID)"
                cells.append(cell)
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

    rows = [row_from_record(rec) for rec in records]
    aggregated = aggregate(rows)
    os.makedirs(args.out, exist_ok=True)
    csv_path = write_csv(rows, args.out)
    md_path = write_headlines(aggregated, args.out)
    print(f"wrote {csv_path} ({len(rows)} rows)")
    print(f"wrote {md_path} ({len(aggregated)} aggregated test points)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
