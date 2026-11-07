#!/usr/bin/env python3
"""8gpu-bench results assembler. Implements spec 07 (benchmark spec 1.1.0).

Reads the runs.jsonl records of one campaign and writes to
<campaign>/aggregate/:

  results.json    canonical result file (spec 07.1.2)
  results.csv     generated from results.json (spec 07.2)
  headlines.md    generated from results.json (spec 07.3)
  validation.json validity levels and checks (spec 07.1.3)

Validity (spec 05.7):
  Per run:        V1 error rate, V6 record completeness, plus the
                  failure flags written by the runner (V5, V7).
  Per test point: V3 CoV across repetitions, V9 comparability category.
  Per drift group (SUT, stack, model, precision, lane):
                  V8 reference drift. A failed group demotes only its
                  own rows.
  Levels: VALID / QUALIFIED / INVALID (spec 05.7.1). Cross-check and
  reference rows carry no validity level.

Statistics (spec 07.1.2): for M5 the aggregate reports the median,
p10, p90, the coefficient of variation, and a 95% percentile
bootstrap confidence interval (2 000 resamples, seeded from the
campaign ID and the test point key, so re-assembly is stable).

Record and aggregate schema checks run on every assembly. If the
jsonschema package is installed, the generated results.json is
validated against tools/schemas/results.schema.json as well.

Usage:
  build-results.py --campaign results/2026Q4
  build-results.py --runs results/2026Q4/runs.jsonl --out results/2026Q4/aggregate
  build-results.py selftest
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import random
import statistics
import sys
import time

# ---------------------------------------------------------------------------
# Limits and constants (spec 05.7, spec 04.1.6)
# ---------------------------------------------------------------------------

MAX_COV_PCT = 5.0            # V3: CoV of M5 across repetitions.
MAX_ERROR_RATE_PCT = 2.0     # V1: error + timeout rate per run.
MAX_REFERENCE_DRIFT_PCT = 5.0  # V8: spread of the reference point.
MIN_REPETITIONS = 5          # spec 05.4: core test points.
BOOTSTRAP_RESAMPLES = 2000

# spec 04.1.6: fallback models run as ARCH, never as STRICT.
FALLBACK_MODEL_MARKERS = ("glm-5.3", "qwen3-8b")
FP4_PRECISIONS = ("FP4", "NVFP4", "MXFP4")

# Flags that document a limitation without failing a check
# (spec 05.7.1, QUALIFIED level).
QUALIFIED_FLAGS = ("DEV", "PILOT", "EMU", "FEAT")

# Required identity fields of a run record (V6, spec 05.7).
REQUIRED_FIELDS = [
    "run_id", "campaign_id", "sut", "stack", "stack_version", "harness",
    "model", "precision", "config_lane", "tp", "payload_profile",
    "prompt_scenario", "regime", "traffic_mode", "concurrency",
    "repetition",
]

# results.csv columns (spec 07.2). One row per (test point, harness,
# repetition). Per-row statistics live in the aggregates of
# results.json, not here.
COLUMNS = [
    "run_id", "campaign_id", "sut", "stack", "stack_version", "harness",
    "model", "model_revision", "precision", "comparability",
    "config_lane", "tp", "payload_profile", "prompt_scenario", "regime",
    "traffic_mode", "concurrency", "value_order", "repetition",
    "ttft_ms_p90", "tpot_ms_p90", "itl_ms_p99", "e2e_ms_p90",
    "out_tps", "out_tps_per_gpu", "goodput_tps", "slo_concurrency",
    "hbm_peak_gb", "gpu_util_pct", "temp_max_c", "power_mean_w",
    "j_per_1m_out", "flags", "deviation_note", "error_rate_pct",
    "success_rate_pct", "validity", "raw_dir", "generated_at",
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

# Numeric columns that get a median in the aggregate row.
NUMERIC_COLUMNS = [c for c in COLUMNS
                   if c not in ("run_id", "campaign_id", "sut", "stack",
                                "stack_version", "harness", "model",
                                "model_revision", "precision",
                                "config_lane", "comparability",
                                "payload_profile",
                                "prompt_scenario", "regime",
                                "traffic_mode", "flags", "deviation_note",
                                "validity", "raw_dir", "generated_at")]


# ---------------------------------------------------------------------------
# Benchmark versions (spec 00.3.5, spec 07.1.1)
# ---------------------------------------------------------------------------


def load_versions() -> dict:
    """Read the VERSION file next to the repository root."""
    versions = {
        "benchmark_spec": "1.1.0",
        "runner_version": "1.2.0",
        "assembler_version": "1.2.0",
        "corpus_version": "1.0.0",
        "result_schema_version": "1.0.0",
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "VERSION")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                if key and value:
                    versions[key.strip()] = value.strip()
    except OSError:
        pass
    return versions


# ---------------------------------------------------------------------------
# Statistics (spec 07.1.2)
# ---------------------------------------------------------------------------


def quantile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolated quantile of a sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def bootstrap_ci(values: list[float], seed: int) -> tuple[float, float] | None:
    """95% percentile bootstrap of the mean, seeded for stability."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return (quantile(means, 0.025), quantile(means, 0.975))


def stable_seed(campaign_id: str, key: str) -> int:
    digest = hashlib.sha256(f"{campaign_id}:{key}".encode()).hexdigest()
    return int(digest[:12], 16)


# ---------------------------------------------------------------------------
# Comparability (spec 04.1.6, check V9)
# ---------------------------------------------------------------------------


def expected_comparability(model: str, precision: str) -> str:
    """The category a row must carry, from model and precision."""
    if (precision or "").upper() in FP4_PRECISIONS:
        return "HWBEST"
    low = (model or "").lower()
    if any(marker in low for marker in FALLBACK_MODEL_MARKERS):
        return "ARCH"
    return "STRICT"


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def read_runs(paths: list[str]) -> list[dict]:
    """Read runs.jsonl records. Deduplicates by run_id (last wins), so
    re-assembling an appended file is idempotent and a re-run of a
    stopped campaign overwrites its stale record.

    Two records with the same run_id but different content is a tool
    bug (for example the old runner collision between reference and
    test-point records, review defect 1). Keep the last one, but warn
    loudly so it is never silent again."""
    records: dict[str, dict] = {}
    seen: dict[str, str] = {}
    for pattern in paths:
        for path in sorted(glob.glob(pattern, recursive=True)):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    run_id = rec["run_id"]
                    digest = hashlib.sha256(line.encode()).hexdigest()
                    if run_id in seen and seen[run_id] != digest:
                        print(f"WARNING: run_id {run_id} appears more than "
                              f"once with different content; keeping the "
                              f"last record. Check the runner version.",
                              file=sys.stderr)
                    seen[run_id] = digest
                    records[run_id] = rec
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
    # M18: energy per 1M output tokens = M16 x 1e6 / M5.
    power = row.get("power_mean_w")
    if out_tps and power:
        row["j_per_1m_out"] = round(float(power) * 1e6 / float(out_tps))
    # M24: request success rate (spec 06.2).
    error_rate = row.get("error_rate_pct")
    if isinstance(error_rate, (int, float)):
        row["success_rate_pct"] = round(100.0 - float(error_rate), 2)
    row["flags"] = ",".join(rec.get("flags", []))
    return row


# ---------------------------------------------------------------------------
# Validity (spec 05.7, spec 05.7.1)
# ---------------------------------------------------------------------------


def run_validity(rec: dict, row: dict, drift_fail: bool) -> tuple[str | None, list[str]]:
    """Per-run validity level and reasons (spec 05.7.1).

    Cross-check and reference rows carry no validity level.
    """
    flags = rec.get("flags", [])
    if "CROSSCHECK" in flags or "REFERENCE" in flags:
        return None, []

    invalid = "INVALID" in flags
    reasons = [f for f in flags if f != "INVALID"] if invalid else []
    qualified: list[str] = []

    # V1: error + timeout rate per run.
    error_rate = row.get("error_rate_pct")
    if isinstance(error_rate, (int, float)) and error_rate >= MAX_ERROR_RATE_PCT:
        invalid = True
        if "ERROR-RATE" not in reasons:
            reasons.append("ERROR-RATE")

    # V6: record completeness.
    missing = [f for f in REQUIRED_FIELDS if rec.get(f) in (None, "")]
    if missing:
        invalid = True
        reasons.append("RECORD-INCOMPLETE")

    # Limitation flags and missing power data -> QUALIFIED.
    for flag in QUALIFIED_FLAGS:
        if flag in flags and flag not in qualified:
            qualified.append(flag)
    if row.get("power_mean_w") in ("", None) and "NO-POWER" not in qualified:
        qualified.append("NO-POWER")

    if invalid:
        return "INVALID", reasons + qualified
    if qualified:
        return "QUALIFIED", qualified
    if drift_fail:
        return "QUALIFIED", ["REFERENCE-DRIFT"]
    return "VALID", []


def group_key(row: dict) -> tuple:
    return (row["sut"], row["stack"], row["stack_version"], row["harness"],
            row["model"], row.get("model_revision", ""),
            row["precision"], row.get("comparability", ""),
            row["config_lane"], row["tp"],
            row["payload_profile"], row["prompt_scenario"], row["regime"],
            row["traffic_mode"], row["concurrency"])


def group_key_string(key: tuple) -> str:
    return "/".join(str(v) for v in key)


def aggregate_test_point(campaign_id: str, key: tuple,
                         members: list[dict],
                         run_levels: dict[str, tuple]) -> dict:
    """One aggregate row per test point: median and statistics of the
    repetition set, plus the V3, V9 and V8 checks (spec 05.7)."""
    members.sort(key=lambda r: r["repetition"])
    agg = dict(members[0])
    agg["repetition"] = "median"

    # Medians of the numeric columns over the available values.
    for col in NUMERIC_COLUMNS:
        values = [m[col] for m in members if m[col] not in ("", None)]
        if not values:
            agg[col] = ""
            continue
        numbers = [float(v) for v in values]
        agg[col] = round(statistics.median(numbers), 4)
        if col in ("concurrency", "tp") and agg[col] % 1 == 0:
            agg[col] = int(agg[col])

    # M5 statistics of the repetition set (spec 07.1.2).
    tps = [float(m["out_tps"]) for m in members
           if m["out_tps"] not in ("", None)]
    invalid = False
    reasons: list[str] = []
    if tps:
        tps_sorted = sorted(tps)
        agg["out_tps_p10"] = round(quantile(tps_sorted, 0.10), 3)
        agg["out_tps_p90"] = round(quantile(tps_sorted, 0.90), 3)
        if len(tps) >= 2 and statistics.mean(tps) > 0:
            cov = 100.0 * statistics.stdev(tps) / statistics.mean(tps)
            agg["cov_pct"] = round(cov, 2)
            # V3: coefficient of variation of the repetitions.
            if cov >= MAX_COV_PCT:
                invalid = True
                reasons.append("INVALID-COV")
            ci = bootstrap_ci(tps, stable_seed(campaign_id, group_key_string(key)))
            if ci:
                agg["ci95_low"] = round(ci[0], 3)
                agg["ci95_high"] = round(ci[1], 3)
    agg.setdefault("out_tps_p10", "")
    agg.setdefault("out_tps_p90", "")
    agg.setdefault("cov_pct", "")
    agg.setdefault("ci95_low", "")
    agg.setdefault("ci95_high", "")

    # V9: comparability category matches model and precision.
    expected = expected_comparability(agg.get("model", ""),
                                      agg.get("precision", ""))
    declared = agg.get("comparability", "")
    if declared != expected:
        invalid = True
        reasons.append("COMPARABILITY")

    # Merge the per-run validity levels.
    for m in members:
        level, run_reasons = run_levels.get(m["run_id"], ("VALID", []))
        if level == "INVALID":
            invalid = True
            for r in run_reasons:
                if r not in reasons:
                    reasons.append(r)
        elif level == "QUALIFIED":
            for r in run_reasons:
                if r not in reasons and r not in QUALIFIED_FLAGS:
                    reasons.append(r)

    # Limitation flags of any member, and missing power data.
    qualified: list[str] = []
    member_flags = set()
    for m in members:
        member_flags.update(f for f in m.get("flags", "").split(",") if f)
    for flag in QUALIFIED_FLAGS:
        if flag in member_flags:
            qualified.append(flag)
    # Repetition count below the spec minimum (spec 05.4).
    if len(members) < MIN_REPETITIONS:
        qualified.append("FEW-REPS")
    if any(m.get("power_mean_w") in ("", None) for m in members):
        qualified.append("NO-POWER")

    if invalid:
        level = "INVALID"
        all_reasons = reasons + qualified
    elif qualified:
        level = "QUALIFIED"
        all_reasons = qualified
    else:
        level = "VALID"
        all_reasons = []

    agg["repetitions"] = len(members)
    agg["validity"] = level
    agg["reasons"] = all_reasons
    return agg


def reference_group_key(fields: dict) -> str:
    """The drift group of a record: SUT, stack, stack version, model,
    precision and config lane (spec 05.2.2).

    Drift is computed per group. Output throughput is not comparable
    across groups: pooling the reference points of several SUTs (or
    several lanes) measures the difference between the systems, not
    drift, and demotes every row of the campaign.
    """
    key = (fields.get("sut", ""), fields.get("stack", ""),
           fields.get("stack_version", ""), fields.get("model", ""),
           fields.get("precision", ""), fields.get("config_lane", ""))
    return group_key_string(key)


def _drift_entry(tps: list[float]) -> dict:
    if len(tps) < 2:
        # V8 is unevaluated for this group: no rows are demoted, the
        # gap is recorded in validation.json (spec 05.2.2).
        return dict(count=len(tps), throughputs_tps=sorted(tps),
                    drift_pct="", limit_pct=MAX_REFERENCE_DRIFT_PCT,
                    **{"pass": None},
                    note="fewer than 2 reference points, V8 unevaluated")
    mean = statistics.mean(tps)
    drift = 100.0 * (max(tps) - min(tps)) / mean if mean > 0 else 0.0
    return dict(count=len(tps), throughputs_tps=sorted(tps),
                drift_pct=round(drift, 2),
                limit_pct=MAX_REFERENCE_DRIFT_PCT,
                **{"pass": drift < MAX_REFERENCE_DRIFT_PCT})


def reference_drift_groups(records: list[dict]) -> dict[str, dict]:
    """V8: relative spread of the reference point output throughput,
    per drift group (spec 05.2.2)."""
    groups: dict[tuple, list[float]] = {}
    for rec in records:
        if "REFERENCE" not in rec.get("flags", []):
            continue
        if rec.get("harness") != "guidellm":
            continue
        value = (rec.get("metrics") or {}).get("output_throughput_mean")
        if not isinstance(value, (int, float)):
            continue
        key = (rec.get("sut", ""), rec.get("stack", ""),
               rec.get("stack_version", ""), rec.get("model", ""),
               rec.get("precision", ""), rec.get("config_lane", ""))
        groups.setdefault(key, []).append(float(value))
    out: dict[str, dict] = {}
    for key, tps in groups.items():
        entry = _drift_entry(tps)
        entry["key"] = group_key_string(key)
        out[group_key_string(key)] = entry
    return out


def reference_block(groups: dict[str, dict]) -> dict:
    """The reference section of results.json and validation.json."""
    passes = [g["pass"] for g in groups.values()]
    if not passes:
        ref_pass, note = None, "no reference records, V8 unevaluated"
    elif any(p is False for p in passes):
        ref_pass, note = False, ""
    elif any(p is None for p in passes):
        ref_pass, note = None, "some reference groups are unevaluated"
    else:
        ref_pass, note = True, ""
    block = dict(groups=list(groups.values()), **{"pass": ref_pass})
    if note:
        block["note"] = note
    return block


# ---------------------------------------------------------------------------
# Schema checks (built-in; jsonschema used when installed)
# ---------------------------------------------------------------------------

AGGREGATE_REQUIRED = [
    "sut", "stack", "model", "precision", "comparability", "config_lane",
    "tp", "payload_profile", "prompt_scenario", "regime", "traffic_mode",
    "concurrency", "repetitions", "out_tps", "validity",
]


def check_record(rec: dict) -> list[str]:
    problems = [f"missing field: {f}" for f in REQUIRED_FIELDS
                if rec.get(f) in (None, "")]
    if not isinstance(rec.get("metrics"), dict):
        problems.append("metrics is not an object")
    return problems


def check_aggregate(agg: dict) -> list[str]:
    problems = [f"missing field: {f}" for f in AGGREGATE_REQUIRED
                if agg.get(f) in (None, "")]
    level = agg.get("validity")
    if level not in ("VALID", "QUALIFIED", "INVALID"):
        problems.append(f"invalid validity level: {level!r}")
    return problems


def validate_with_jsonschema(results: dict, repo_root: str) -> list[str]:
    """Optional: full JSON Schema validation when the package exists.

    The run-record schema is registered locally so the $ref resolves
    without network access.
    """
    try:
        import jsonschema
    except ImportError:
        return []
    schema_dir = os.path.join(repo_root, "tools", "schemas")
    results_path = os.path.join(schema_dir, "results.schema.json")
    record_path = os.path.join(schema_dir, "run-record.schema.json")
    if not (os.path.exists(results_path) and os.path.exists(record_path)):
        return []
    with open(results_path, "r", encoding="utf-8") as fh:
        results_schema = json.load(fh)
    with open(record_path, "r", encoding="utf-8") as fh:
        record_schema = json.load(fh)
    record_uri = record_schema.get("$id")
    validator_cls = jsonschema.validators.validator_for(results_schema)
    try:
        try:
            # Modern jsonschema (>= 4.18): referencing registry.
            from referencing import Registry, Resource
            registry = Registry().with_resources(
                [(record_uri, Resource.from_contents(record_schema))])
            validator = validator_cls(results_schema, registry=registry)
        except ImportError:
            # Older jsonschema: RefResolver with preloaded remote refs.
            validator = validator_cls(
                results_schema,
                resolver=jsonschema.RefResolver(
                    base_uri=record_uri.rsplit("/", 1)[0] + "/",
                    ref_schema={},
                    remote_refs={record_uri: record_schema}))
        errors = sorted(validator.iter_errors(results),
                        key=lambda e: list(e.absolute_path))
        return [f"jsonschema: {e.message} at {list(e.absolute_path)}"
                for e in errors]
    except Exception as exc:
        return [f"jsonschema runner error: {exc}"]


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_json(obj: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


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
        valid = [r for r in candidates
                 if r.get("validity") != "INVALID"]
        return dict(valid[0] if valid else candidates[0])
    return None


def write_headlines(aggregates: list[dict], out_dir: str) -> str:
    """Headline tables (spec 07.3): one table per model, precision,
    scenario, regime, profile, config lane, traffic mode, and
    comparability category. Cross-check and reference rows never enter
    (spec 07.3 rules 4 and 5)."""
    tables: dict[tuple, list[dict]] = {}
    for row in aggregates:
        if "CROSSCHECK" in row.get("flags", "") \
                or "REFERENCE" in row.get("flags", ""):
            continue
        tables.setdefault((row.get("comparability", ""), row["model"],
                           row["precision"], row["prompt_scenario"],
                           row["regime"], row["payload_profile"],
                           row["config_lane"], row["traffic_mode"]),
                          []).append(row)

    lines = ["# Headline tables", "",
             "One table per comparability category, model, precision, "
             "scenario, regime, payload profile, config lane and "
             "traffic mode (spec 07.3).",
             "Rows sit at the highest measured concurrency. "
             "Values are the median of the repetitions (spec 05.3).",
             "M5 prints the 95% bootstrap confidence interval "
             "(spec 07.1.2).",
             "INVALID rows are marked; QUALIFIED reasons print as "
             "footnotes (spec 05.7.1).", ""]

    for key in sorted(tables):
        category, model, precision, scenario, regime, profile, lane, \
            mode = key
        by_sut_stack: dict[tuple, list[dict]] = {}
        for row in tables[key]:
            by_sut_stack.setdefault((row["sut"], row["stack"]), []).append(row)
        lanes = [pick_headline_row(v) for v in by_sut_stack.values()]
        lanes = [r for r in lanes if r]
        if not lanes:
            continue
        lanes.sort(key=lambda r: float(r.get("out_tps") or 0), reverse=True)

        lines.append(f"## {category} — {model} / {precision} / {scenario} "
                     f"/ {regime} / {profile} / {lane} / {mode}")
        lines.append("")
        header = (["sut", "stack", "concurrency", "out_tps", "out_tps_ci95",
                   "out_tps_per_gpu", "success_rate_pct",
                   "ttft_ms_p90", "tpot_ms_p90", "e2e_ms_p90",
                   "temp_max_c", "power_mean_w", "j_per_1m_out",
                   "validity", "flags"])
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        footnotes: list[str] = []
        for row in lanes:
            ci_low, ci_high = row.get("ci95_low", ""), row.get("ci95_high", "")
            ci = (f"({ci_low} to {ci_high})"
                  if ci_low not in ("", None) and
                     ci_high not in ("", None) else "")
            tps_cell = str(row.get("out_tps", ""))
            if ci:
                tps_cell += f" {ci}"
            validity = row.get("validity", "")
            if validity == "INVALID":
                tps_cell += " (INVALID)"
            elif validity == "QUALIFIED":
                footnotes.append(
                    f"{row['sut']}/{row['stack']}: "
                    f"{' + '.join(row.get('reasons', []) or [])}")
            cells = [str(row.get(h, "")) if h != "out_tps" else tps_cell
                     for h in header if h != "out_tps_ci95"]
            cells.insert(4, ci)
            lines.append("| " + " | ".join(cells) + " |")
        if footnotes:
            lines.append("")
            for note in footnotes:
                lines.append(f"Footnote — QUALIFIED: {note}")
        lines.append("")

    path = os.path.join(out_dir, "headlines.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_records(campaign_id: str, records: list[dict],
                     out_dir: str, versions: dict) -> dict:
    """Assemble one campaign from its records. Returns the results
    dict (canonical)."""
    rows = [row_from_record(rec) for rec in records]

    # V8 first, per drift group: a failed group demotes only its own
    # rows to QUALIFIED (spec 05.2.2).
    drift_groups = reference_drift_groups(records)
    reference = reference_block(drift_groups)

    def drift_fail(fields: dict) -> bool:
        return drift_groups.get(reference_group_key(fields), {}) \
            .get("pass") is False

    run_levels: dict[str, tuple] = {}
    run_entries: list[dict] = []
    for rec, row in zip(records, rows):
        level, reasons = run_validity(rec, row, drift_fail(row))
        run_levels[rec["run_id"]] = (level, reasons)
        row["validity"] = level or ""
        run_entries.append(dict(run_id=rec["run_id"],
                                validity=level,
                                reasons=reasons))

    # Reference and cross-check rows feed the V8 check and the
    # cross-check report only. They never enter a test point group
    # (spec 05.7.1 rules 1 and 2).
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if "REFERENCE" in row.get("flags", "") \
                or "CROSSCHECK" in row.get("flags", ""):
            continue
        groups.setdefault(group_key(row), []).append(row)
    aggregates: list[dict] = []
    tp_entries: list[dict] = []
    for key in sorted(groups, key=group_key_string):
        members = groups[key]
        agg = aggregate_test_point(campaign_id, key, members, run_levels)
        if drift_fail(members[0]) and agg["validity"] == "VALID":
            agg["validity"] = "QUALIFIED"
            agg["reasons"] = ["REFERENCE-DRIFT"] + agg["reasons"]
        aggregates.append(agg)
        tp_entries.append(dict(
            key=group_key_string(key), validity=agg["validity"],
            repetitions=agg["repetitions"], reasons=agg["reasons"]))

    results = dict(
        schema_version=versions["result_schema_version"],
        campaign_id=campaign_id,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        generator=dict(assembler_version=versions["assembler_version"],
                       benchmark_spec=versions["benchmark_spec"]),
        versions=versions,
        reference=reference,
        records=records,
        aggregates=[
            {k: a.get(k, "") for k in
             ("sut", "stack", "stack_version", "harness", "model",
              "model_revision", "precision", "comparability",
              "config_lane", "tp", "payload_profile", "prompt_scenario",
              "regime", "traffic_mode", "concurrency", "repetitions",
              "out_tps", "out_tps_p10", "out_tps_p90", "cov_pct",
              "ci95_low", "ci95_high", "ttft_ms_p90", "tpot_ms_p90",
              "e2e_ms_p90", "out_tps_per_gpu", "success_rate_pct",
              "hbm_peak_gb", "gpu_util_pct", "temp_max_c",
              "power_mean_w", "j_per_1m_out", "validity", "reasons")}
            for a in aggregates
        ],
    )

    # Schema checks (spec 07, "reject or flag" rule).
    problems = []
    for rec in records:
        for p in check_record(rec):
            problems.append(f"record {rec.get('run_id')}: {p}")
    for agg in results["aggregates"]:
        for p in check_aggregate(agg):
            problems.append(f"aggregate {agg.get('sut')}/{agg.get('model')} "
                            f"C{agg.get('concurrency')}: {p}")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems += validate_with_jsonschema(results, repo_root)

    os.makedirs(out_dir, exist_ok=True)
    write_json(results, os.path.join(out_dir, "results.json"))
    write_csv(rows, out_dir)
    write_headlines(aggregates, out_dir)

    levels = [e["validity"] for e in run_entries]
    validation = dict(
        schema_version=versions["result_schema_version"],
        campaign_id=campaign_id,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        versions=versions,
        reference=reference,
        runs=run_entries,
        test_points=tp_entries,
        summary=dict(
            valid=levels.count("VALID"),
            qualified=levels.count("QUALIFIED"),
            invalid=levels.count("INVALID"),
            crosscheck=sum(1 for r in records
                           if "CROSSCHECK" in r.get("flags", [])),
            reference=sum(1 for r in records
                          if "REFERENCE" in r.get("flags", []))),
        schema_errors=problems,
    )
    write_json(validation, os.path.join(out_dir, "validation.json"))

    if problems:
        print("schema problems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
    return results


def assemble(campaign_dir: str, out_dir: str, versions: dict) -> dict:
    """Assemble one campaign directory (spec 07.1 layout)."""
    campaign_id = os.path.basename(os.path.normpath(campaign_dir))
    patterns = [os.path.join(campaign_dir, "**", "runs.jsonl")]
    records = read_runs(patterns)
    if not records:
        raise SystemExit(f"no run records found under {campaign_dir}")
    return assemble_records(campaign_id, records, out_dir, versions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("selftest", help="run the selftest on synthetic data")

    parser.add_argument("--campaign", default=None,
                        help="campaign directory holding runs.jsonl "
                             "(spec 07.1). Output goes to <campaign>/aggregate.")
    parser.add_argument("--runs", nargs="+", default=None,
                        help="runs.jsonl files or patterns (explicit mode)")
    parser.add_argument("--campaign-id", default=None,
                        help="campaign tag for explicit mode "
                             "(default: derived from the first record)")
    parser.add_argument("--out", default=None,
                        help="output directory for explicit mode")
    args = parser.parse_args()

    if args.cmd == "selftest":
        return selftest()
    if not args.campaign and not args.runs:
        parser.print_help()
        return 1

    versions = load_versions()
    if args.campaign:
        out_dir = args.out or os.path.join(args.campaign, "aggregate")
        results = assemble(args.campaign, out_dir, versions)
    else:
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
        campaign_id = args.campaign_id or records[0].get("campaign_id") \
            or "campaign"
        out_dir = args.out or os.path.join(os.getcwd(), "aggregate")
        results = assemble_records(campaign_id, records, out_dir, versions)
    print(f"wrote {out_dir}/results.json "
          f"({len(results['records'])} records, "
          f"{len(results['aggregates'])} test points)")
    reference = results["reference"]
    if reference["groups"]:
        for group in reference["groups"]:
            print(f"reference drift {group['key']}: "
                  f"{group['drift_pct']}% (pass={group['pass']})")
    else:
        note = reference.get("note", "no reference records")
        print(f"reference drift: {note} (pass={reference['pass']})")
    return 0


# ---------------------------------------------------------------------------
# Selftest (spec 05.7, spec 07.1.2, spec 07.1.3)
# ---------------------------------------------------------------------------


def make_record(campaign, sut, stack, model, precision, comparability,
                profile, concurrency, repetition, tps, flags=None,
                harness="guidellm", error_rate=0.0, power=300.0) -> dict:
    return dict(
        run_id=f"{sut}-{stack}-test-{model}-{profile}-{concurrency}"
               f"-r{repetition}-{harness}",
        campaign_id=campaign, sut=sut, stack=stack,
        stack_version="0.32.0", harness=harness, model=model,
        model_revision="rev1", precision=precision,
        comparability=comparability, config_lane="BASE", tp=1,
        payload_profile=profile, prompt_scenario="P-U", regime="W-ON",
        traffic_mode="closed", concurrency=concurrency,
        repetition=repetition, value_order=repetition,
        run_dir=f"/tmp/{campaign}/{sut}", raw_dir="/tmp/raw",
        order_seed=42,
        metrics=dict(
            ttft_p90=100.0, tpot_p90=30.0, itl_p99=40.0,
            request_latency_p90=5000.0,
            output_throughput_mean=tps, goodput_mean=tps * 0.9,
            gpu_util_pct=99.0, temp_max_c=60.0, hbm_peak_gb=55.0,
            power_mean_w=power, error_rate_pct=error_rate,
            power_coverage_pct=100.0),
        flags=flags or [],
        run_started_at="2026-11-05T00:00:00Z",
        generated_at="2026-11-05T00:00:00Z",
    )


def write_campaign(path: str, records: list[dict]) -> None:
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "runs.jsonl"), "w",
              encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def selftest() -> int:
    import tempfile
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)
            print(f"  FAIL: {name}", flush=True)
        else:
            print(f"  ok: {name}", flush=True)

    versions = load_versions()
    with tempfile.TemporaryDirectory() as tmp:
        # Campaign A: clean rows, a QUALIFIED (PILOT) group, an
        # INVALID-COV group, an ARCH group, an HWBEST group, a
        # comparability mismatch, cross-check and reference rows.
        camp_a = os.path.join(tmp, "2026Q4")
        records_a = []
        for r in range(1, 6):
            records_a.append(make_record(
                "2026Q4", "SUT-1", "vllm", "Qwen/Qwen3.8-27B", "BF16",
                "STRICT", "PF-1", 32, r, 5400 + 30 * r))
        for r in range(1, 6):
            records_a.append(make_record(
                "2026Q4", "SUT-4", "vllm", "Qwen/Qwen3.8-27B", "BF16",
                "STRICT", "PF-1", 32, r, 5100 + 10 * r, flags=["PILOT"]))
        # High CoV group: 6.25% spread.
        for r, tps in enumerate([1000, 1080, 1010, 930, 1070, 940], 1):
            records_a.append(make_record(
                "2026Q4", "SUT-1", "vllm", "Qwen/Qwen3.8-27B", "BF16",
                "STRICT", "PF-1", 64, r, tps))
        # Fallback model: ARCH.
        for r in range(1, 6):
            records_a.append(make_record(
                "2026Q4", "SUT-4", "vllm", "zai-org/GLM-5.3-Flash", "FP8",
                "ARCH", "PF-1", 32, r, 4800 + 5 * r))
        # Hardware-best lane: HWBEST (native FP4).
        for r in range(1, 6):
            records_a.append(make_record(
                "2026Q4", "SUT-2", "vllm", "deepseek-ai/DeepSeek-V4.1-Flash",
                "NVFP4", "HWBEST", "PF-1", 32, r, 6100 + 8 * r))
        # Comparability mismatch: FP4 declared as STRICT.
        for r in range(1, 6):
            records_a.append(make_record(
                "2026Q4", "SUT-2", "vllm", "deepseek-ai/DeepSeek-V4.1-Flash",
                "NVFP4", "STRICT", "PF-1", 64, r, 6000 + r))
        # Cross-check row: no validity level.
        records_a.append(make_record(
            "2026Q4", "SUT-1", "vllm", "Qwen/Qwen3.8-27B", "BF16",
            "STRICT", "PF-1", 32, 1, 5400, flags=["CROSSCHECK"],
            harness="genai-perf"))
        # Reference rows of SUT-1: small drift (about 1.5%).
        for i, tps in enumerate([1000.0, 1010.0, 995.0, 1005.0], 1):
            rec = make_record("2026Q4", "SUT-1", "vllm",
                              "Qwen/Qwen3.8-27B", "BF16", "STRICT", "PF-1",
                              32, 1, tps, flags=["REFERENCE"])
            rec["run_id"] = f"2026Q4-ref-{i}"
            records_a.append(rec)
        # Reference rows of SUT-4: stable. The two SUTs must be
        # evaluated as separate drift groups (review defect 2).
        for i, tps in enumerate([5100.0, 5110.0, 5090.0], 1):
            rec = make_record("2026Q4", "SUT-4", "vllm",
                              "Qwen/Qwen3.8-27B", "BF16", "STRICT", "PF-1",
                              32, 1, tps, flags=["REFERENCE"])
            rec["run_id"] = f"2026Q4-ref-sut4-{i}"
            records_a.append(rec)
        write_campaign(camp_a, records_a)

        out_a = os.path.join(camp_a, "aggregate")
        results = assemble(camp_a, out_a, versions)
        with open(os.path.join(out_a, "results.json"), encoding="utf-8") as fh:
            disk = json.load(fh)
        with open(os.path.join(out_a, "validation.json"),
                  encoding="utf-8") as fh:
            validation = json.load(fh)

        aggs = {(a["sut"], a["concurrency"], a.get("comparability", ""),
                 a.get("model", "")): a
                for a in results["aggregates"]}
        sut1 = aggs[("SUT-1", 32, "STRICT", "Qwen/Qwen3.8-27B")]
        sut4 = aggs[("SUT-4", 32, "STRICT", "Qwen/Qwen3.8-27B")]
        cov_bad = aggs[("SUT-1", 64, "STRICT", "Qwen/Qwen3.8-27B")]
        arch = aggs[("SUT-4", 32, "ARCH", "zai-org/GLM-5.3-Flash")]
        hwb = aggs[("SUT-2", 32, "HWBEST", "deepseek-ai/DeepSeek-V4.1-Flash")]
        mismatch = aggs[("SUT-2", 64, "STRICT",
                         "deepseek-ai/DeepSeek-V4.1-Flash")]

        check("A: results.json on disk has schema_version",
              disk["schema_version"] == versions["result_schema_version"])
        check("A: versions block present",
              disk["versions"]["benchmark_spec"] ==
              versions["benchmark_spec"])
        check("A: clean group is VALID", sut1["validity"] == "VALID")
        check("A: PILOT group is QUALIFIED",
              sut4["validity"] == "QUALIFIED" and "PILOT" in sut4["reasons"])
        check("A: high-CoV group is INVALID",
              cov_bad["validity"] == "INVALID"
              and "INVALID-COV" in cov_bad["reasons"])
        check("A: ARCH row kept separate", arch["comparability"] == "ARCH")
        check("A: HWBEST row kept separate",
              hwb["comparability"] == "HWBEST")
        check("A: mismatched category is INVALID",
              mismatch["validity"] == "INVALID"
              and "COMPARABILITY" in mismatch["reasons"])
        check("A: median computed",
              sut1["out_tps"] == round(statistics.median(
                  [5430, 5460, 5490, 5520, 5550]), 3))
        check("A: p10 below median below p90",
              sut1["out_tps_p10"] < sut1["out_tps"] < sut1["out_tps_p90"])
        check("A: CI contains the median",
              sut1["ci95_low"] <= sut1["out_tps"] <= sut1["ci95_high"])
        check("A: cov under the V3 limit", sut1["cov_pct"] < MAX_COV_PCT)
        ref_groups = results["reference"]["groups"]
        check("A: per-group reference drift computed",
              len(ref_groups) == 2
              and all(g["pass"] is True for g in ref_groups))
        check("A: cross-check row has no validity",
              validation["summary"]["crosscheck"] == 1)
        check("A: reference rows counted",
              validation["summary"]["reference"] == 7)

        # The assembler deduplicates by run_id, so a reference record
        # and a test-point record must never share one (review defect
        # 1). Build both with the runner's own record builder.
        import importlib.util as _ilu
        import tempfile as _tempfile
        spec_mod = _ilu.spec_from_file_location(
            "run_benchmark",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "run-benchmark.py"))
        runner = _ilu.module_from_spec(spec_mod)
        spec_mod.loader.exec_module(runner)
        cfg = dict(run_id_prefix="sut1-vllm", campaign_id="2026Q4",
                   sut="SUT-1", stack="vllm", stack_version="0.32.0",
                   model="Qwen/Qwen3.8-27B", precision="BF16",
                   comparability="STRICT",
                   server=dict(image="vllm/vllm-openai:v0.32.0"),
                   client=dict(target="http://sut1:8000",
                               guidellm="guidellm"),
                   test_points=[])
        point = dict(profile="PF-1", scenario="P-U", regime="W-ON",
                     mode="closed")
        with _tempfile.TemporaryDirectory() as tmp:
            tp_rec = runner.base_record(cfg, point, "guidellm", 32, 1, tmp)
            ref_rec = runner.base_record(
                cfg, point, "guidellm", 32, 1, tmp,
                extra_flags=["REFERENCE"], run_suffix="REF-start")
        check("A: runner reference and test-point run_ids differ",
              tp_rec["run_id"] != ref_rec["run_id"])
        check("A: validation has summary",
              all(k in validation["summary"]
                  for k in ("valid", "qualified", "invalid",
                            "crosscheck", "reference")))
        check("A: no schema errors", validation["schema_errors"] == [])

        headlines = open(os.path.join(out_a, "headlines.md"),
                         encoding="utf-8").read()
        check("A: headline splits by category",
              "## STRICT —" in headlines and "## ARCH —" in headlines
              and "## HWBEST —" in headlines)
        check("A: CI printed in the headline", "(5" in headlines)
        check("A: reference row not in the headline",
              "ref-" not in headlines)
        csv_text = open(os.path.join(out_a, "results.csv"),
                        encoding="utf-8").read().splitlines()
        check("A: csv has the new columns",
              "comparability" in csv_text[0]
              and "value_order" in csv_text[0]
              and "validity" in csv_text[0])

        # Campaign B: the SUT-1 drift group fails. The SUT-2 group is
        # stable. A failed group demotes only its own rows (review
        # defect 2): the old pooled drift would have demoted SUT-2.
        camp_b = os.path.join(tmp, "2026Q5")
        records_b = []
        for r in range(1, 6):
            records_b.append(make_record(
                "2026Q5", "SUT-1", "vllm", "Qwen/Qwen3.8-27B", "BF16",
                "STRICT", "PF-1", 32, r, 5400 + 10 * r))
        for i, tps in enumerate([1000.0, 1200.0, 800.0], 1):
            rec = make_record("2026Q5", "SUT-1", "vllm",
                              "Qwen/Qwen3.8-27B", "BF16", "STRICT", "PF-1",
                              32, 1, tps, flags=["REFERENCE"])
            rec["run_id"] = f"2026Q5-ref-{i}"
            records_b.append(rec)
        for r in range(1, 6):
            records_b.append(make_record(
                "2026Q5", "SUT-2", "vllm", "Qwen/Qwen3.8-27B", "BF16",
                "STRICT", "PF-1", 32, r, 6000 + 5 * r))
        for i, tps in enumerate([6100.0, 6110.0, 6090.0], 1):
            rec = make_record("2026Q5", "SUT-2", "vllm",
                              "Qwen/Qwen3.8-27B", "BF16", "STRICT", "PF-1",
                              32, 1, tps, flags=["REFERENCE"])
            rec["run_id"] = f"2026Q5-ref-sut2-{i}"
            records_b.append(rec)
        write_campaign(camp_b, records_b)
        out_b = os.path.join(camp_b, "aggregate")
        results_b = assemble(camp_b, out_b, versions)
        aggs_b = {(a["sut"], a["concurrency"]): a
                  for a in results_b["aggregates"]}
        check("B: drift fails (SUT-1 group)",
              results_b["reference"]["pass"] is False)
        check("B: SUT-1 rows demoted to QUALIFIED",
              aggs_b[("SUT-1", 32)]["validity"] == "QUALIFIED"
              and "REFERENCE-DRIFT" in aggs_b[("SUT-1", 32)]["reasons"])
        check("B: SUT-2 rows stay VALID (per-group drift)",
              aggs_b[("SUT-2", 32)]["validity"] == "VALID")

    if failures:
        print(f"selftest FAILED: {len(failures)} checks")
        return 1
    print("selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
