#!/usr/bin/env python3
"""8gpu-bench test-point runner. Implements the spec 05.3 procedure
(benchmark spec 1.1.0).

One run manifest declares one stack on one SUT. The runner executes
every test point in it. Per repetition of every test point it runs
the spec 05.3 sequence: server start, health wait, one warmup, a
60 s thermal soak, then the primary harness (GuideLLM) sweeps every
concurrency value in a seeded random order (spec 05.2.1), with a
15 s soak between passes. One server start per test point and
repetition.

The harness is a fixed instrument (spec 03.1): every stack, SUT and
configuration lane is measured with the same tool. Cross-check tools
(genai-perf) run only with --cross-check, after the primary results,
and their records carry the CROSSCHECK flag.

Reference points (spec 05.2.2): when the manifest carries a
`reference` block, the runner executes that point before the first
test point, after every `reference_every` test points, and after the
last one. Reference records carry the REFERENCE flag and feed the V8
drift check of build-results.py.

The manifest declares the comparability category of every row
(spec 04.1.6): STRICT, ARCH, or HWBEST. The runner records the
benchmark versions from the repository VERSION file in every
meta.json (spec 00.3.5).

Usage:
  run-benchmark.py --config runs/example-nvidia.yaml --corpora corpora \\
      --out results/2026Q4
  run-benchmark.py --config ... --telemetry /mnt/sut1/gpu-telemetry
  run-benchmark.py --config ... --cross-check   # after primary results
  run-benchmark.py selftest

Output layout per test point and repetition (spec 07.1):
  <out>/<run_id_prefix>/<profile>-<scenario>-<regime>-r<r>/
    meta.json                 per-run environment record (spec 07.4)
    server-<n>.log            server console log, one per container start
    power-guidellm-C<c>.csv   telemetry rows of one measured pass
    guidellm/C<c>/            primary harness raw output, one dir per pass
    crosscheck-genai-perf/C<c>/  cross-check output (--cross-check only)
  <out>/runs.jsonl     one record per (test point, harness, value,
                       repetition). Multiple manifests of one
                       campaign append to the same runs.jsonl.

Validity handling (spec 05.7, spec 04.5):
  - Failed server starts, failed warmups and failed harness runs
    produce INVALID records. No result is silently dropped.
  - A failed pass restarts the server and retries once
    (spec 04.5 rule 4). The sweep then continues with the next value.
  - Two consecutive INVALID results stop the campaign (spec 05.7).
  - V1 (error rate), V5 (power coverage), V6 (record completeness)
    and V7 (thermal events) are checked where data is available.
    V3 (CoV), V8 (drift) and V9 (comparability) are computed by
    build-results.py.

Adapter status (verified against the upstream docs, Nov 2026):
  GuideLLM adapter: matches the documented 0.7.x CLI
    (guidellm run --backend/--data/--profile/--constraint/--output).
    PILOT-VERIFY the json_file dataset schema against the corpus
    format at the version pin (spec 03.5, PLAN item 8).
  GenAI-Perf adapter: uses -m, --tokenizer, --streaming, --input-file.
    PILOT-VERIFY the input-file payload schema at the version pin.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import random
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOOL_VERSION = "1.2.0"

# Thermal soak (spec 05.3 step 6): 60 s after the warmup, 15 s
# between measured passes.
SOAK_AFTER_WARMUP_S = 60
SOAK_BETWEEN_PASSES_S = 15

# Repetition defaults (spec 05.4): 5 for core test points, 7 for the
# Tier 2 headline points. The manifest sets the per-manifest count.
DEFAULT_REPETITIONS = 5

# Reference point schedule (spec 05.2.2).
DEFAULT_REFERENCE_EVERY = 10
DEFAULT_REFERENCE = dict(profile="PF-1", scenario="P-U", regime="W-ON",
                         concurrency=[32], requests=200,
                         warmup_requests=50)

COMPARABILITY_CATEGORIES = ("STRICT", "ARCH", "HWBEST")


def load_versions() -> dict:
    """Read the VERSION file next to the repository root (spec 00.3.5)."""
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

# Request timeout per payload profile (spec 03.3). Production lanes
# PF-8 and PF-9 use 3600 s because one request can hold a 50 000-token
# output stream.
REQUEST_TIMEOUTS = {"PF-8": 3600, "PF-9": 3600}
DEFAULT_REQUEST_TIMEOUT = 300

# Warmup: 100 requests or 60 s, whichever is longer (spec 03.3).
WARMUP_MIN_SECONDS = 60
WARMUP_CONSECUTIVE_FAILURES = 3

# Consecutive INVALID results that stop the campaign (spec 05.7).
MAX_CONSECUTIVE_INVALID = 2

# V1 limit (spec 05.7): error + timeout rate per run.
MAX_ERROR_RATE_PCT = 2.0

# Core metrics of a primary-harness record. If extraction does not
# produce them, the pass is INVALID, never a usable result (review
# defect 3). error_rate_pct comes from the request counts, so its
# absence also means V1 cannot run.
REQUIRED_PRIMARY_METRICS = ("output_throughput_mean", "error_rate_pct")

# V5 limit (spec 05.7): power trace coverage of the run window.
MIN_POWER_COVERAGE_PCT = 90.0

# V7 keywords scanned in the server log (spec 05.7).
THERMAL_KEYWORDS = ("throttle", "thermal", "overheat", "power cap")

HARNESS_DIRS = {"guidellm": "guidellm", "genai-perf": "crosscheck-genai-perf"}

# The primary harness (spec 03.1). The harness is a fixed instrument.
PRIMARY_HARNESS = "guidellm"
# Cross-check tools (spec 03.1). Run with --cross-check after the
# primary results. Their records carry the CROSSCHECK flag and never
# enter the headline tables.
CROSS_CHECK_HARNESSES = ["genai-perf"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, capture_output=capture, text=True)


def wait_health(url: str, timeout_s: int = 1800, interval_s: int = 5) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(interval_s)
    return False


def post_completion(url: str, model: str, prompt: str, max_tokens: int,
                     timeout_s: int) -> bool:
    body = json.dumps(
        {"model": model, "prompt": prompt, "max_tokens": max_tokens,
         "temperature": 0}
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/completions", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def read_corpus_requests(corpus: str) -> list[dict]:
    rows = []
    bad = 0
    with open(corpus, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"corpus is not valid JSONL ({corpus}): {exc}")
            if rec.get("_meta"):
                continue
            if "prompt" not in rec or "max_tokens" not in rec:
                bad += 1
                continue
            rows.append(rec)
    if bad:
        raise SystemExit(
            f"corpus rows without 'prompt' or 'max_tokens': {bad} in {corpus}. "
            "Regenerate the corpus with gen-corpus.py.")
    return rows


def corpus_sha256(corpus: str) -> str:
    """Corpus checksum. Prefer the sidecar written by gen-corpus.py."""
    import hashlib
    sidecar = corpus[: -len(".jsonl")] + ".meta.json" \
        if corpus.endswith(".jsonl") else corpus + ".meta.json"
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            return json.load(fh).get("corpus_sha256", "")
    except (OSError, json.JSONDecodeError):
        pass
    digest = hashlib.sha256()
    with open(corpus, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_iso8601(value: str) -> float | None:
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Value order and campaign schedule (spec 05.2)
# ---------------------------------------------------------------------------


def shuffled_values(seed: int, key: str, values: list) -> list:
    """Seeded random order of the sweep values (spec 05.2.1).

    Deterministic: the same (seed, key) always gives the same order,
    so a re-run of a stopped campaign reuses the recorded order.
    """
    rng = random.Random(f"8gpu-bench:{seed}:{key}")
    order = list(values)
    rng.shuffle(order)
    return order


def sweep_key(cfg: dict, point: dict, mode: str, repetition: int) -> str:
    return (f"{cfg.get('run_id_prefix', '')}/{point['profile']}/"
            f"{point['scenario']}/{point['regime']}/{mode}/r{repetition}")


def plan_schedule(test_points: list, has_reference: bool,
                  every: int) -> list[tuple]:
    """The campaign step list (spec 05.2.2): test points in manifest
    order with reference points before the first, after every
    `every` points (except after the last), and after the last."""
    steps: list[tuple] = []
    if has_reference:
        steps.append(("reference", "start"))
    count = len(test_points)
    for i in range(1, count + 1):
        steps.append(("test-point", i))
        if has_reference and i < count and i % every == 0:
            steps.append(("reference", f"mid{i}"))
    if has_reference:
        steps.append(("reference", "end"))
    return steps


# ---------------------------------------------------------------------------
# Server lifecycle (cross-vendor, spec 05.3 steps 2 to 4 and 8)
# ---------------------------------------------------------------------------


class Server:
    """Container lifecycle for the stack under test.

    One server instance serves the whole concurrency sweep of one
    harness (spec 05.3): start once, warm up once, run every value,
    stop. The server restarts once per harness, not once per
    concurrency value.

    The manifest controls vendor passthrough:
      - NVIDIA: set ``gpus`` (for example ``all``). Emits ``--gpus``.
      - AMD: omit ``gpus`` and set ``docker_args`` to
        ``--device /dev/kfd --device /dev/dri --group-add video --ipc=host``.
      - Intel: set ``docker_args`` per the stack documentation.
    """

    def __init__(self, cfg: dict, log_dir: str):
        self.cfg = cfg
        self.name = "bench-server"
        self.port = cfg["port"]
        self.log_dir = log_dir
        self.start_command: list[str] = []
        self.start_count = 0
        self.alive = False
        self._log_offset = 0

    def start(self) -> bool:
        self.start_count += 1
        subprocess.run(["docker", "rm", "-f", self.name],
                       capture_output=True)
        cmd = ["docker", "run", "-d", "--name", self.name]
        if self.cfg.get("gpus"):
            cmd += ["--gpus", self.cfg["gpus"]]
        cmd += ["--shm-size", self.cfg.get("shm_size", "64g"),
                "-p", f"{self.port}:{self.cfg.get('container_port', 8000)}"]
        for mount in self.cfg.get("mounts", []):
            cmd += ["-v", mount]
        extra = self.cfg.get("docker_args", "")
        if isinstance(extra, list):
            extra = " ".join(extra)
        cmd += shlex.split(extra)
        cmd.append(self.cfg["image"])
        if self.cfg.get("args"):
            cmd += shlex.split(self.cfg["args"])
        self.start_command = cmd
        result = run(cmd)
        if result.returncode != 0:
            print(result.stderr)
            return False
        self.alive = wait_health(f"http://localhost:{self.port}/health")
        self._log_offset = 0
        return self.alive

    def read_log(self) -> str:
        """New server log content since the last read. Each measured
        pass scans only the content it generated (V7)."""
        if not self.alive:
            return ""
        result = subprocess.run(["docker", "logs", self.name],
                                capture_output=True, text=True,
                                errors="replace")
        text = (result.stdout or "") + (result.stderr or "")
        new = text[self._log_offset:]
        self._log_offset = len(text)
        return new

    def stop(self) -> str:
        """Remove the container and keep its console log (spec 05.3.4).
        One log file per container start: server-<n>.log."""
        log_path = os.path.join(self.log_dir, f"server-{self.start_count}.log")
        result = subprocess.run(["docker", "logs", self.name],
                                capture_output=True, text=True,
                                errors="replace")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(result.stdout or "")
            fh.write(result.stderr or "")
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        self.alive = False
        time.sleep(5)
        return log_path

    def image_digest(self) -> str:
        """Resolved image digest (spec 07.4). Falls back to the local
        image ID when the image has no RepoDigest (local build)."""
        result = subprocess.run(
            ["docker", "image", "inspect", self.cfg["image"],
             "--format", "{{json .RepoDigests}}"],
            capture_output=True, text=True)
        try:
            digests = json.loads(result.stdout)
            if digests:
                return digests[0]
        except (json.JSONDecodeError, TypeError):
            pass
        result = subprocess.run(
            ["docker", "image", "inspect", self.cfg["image"],
             "--format", "{{.Id}}"],
            capture_output=True, text=True)
        return (result.stdout or "").strip() or "unknown"


# ---------------------------------------------------------------------------
# Harness adapters (verified against upstream docs, Nov 2026)
# ---------------------------------------------------------------------------


def guidellm_command(cfg: dict, corpus: str, concurrency: int,
                     requests: int, out_prefix: str,
                     mode: str = "closed") -> list[str]:
    """Primary harness. GuideLLM 0.7.x CLI shape.

    PILOT-VERIFY: the json_file dataset schema at the version pin.
    GuideLLM reports goodput directly (spec 06.4).
    Closed-loop: concurrent profile with N streams.
    Open-loop (OPEN lane, spec 03.3): poisson profile at N req/s.
    """
    if mode == "open":
        profile = f"kind=poisson,rate={concurrency}"
    else:
        profile = f"kind=concurrent,streams={concurrency}"
    return [
        cfg["client"]["guidellm"], "run",
        "--backend", f"kind=openai_http,target={cfg['client']['target']}",
        "--data", f"kind=json_file,path={corpus}",
        "--tokenizer", f"kind=huggingface_auto,model={cfg['model']}",
        "--profile", profile,
        "--constraint", f"kind=max_requests,count={requests}",
        "--seed", f"kind=static,value={cfg.get('seed', 42)}",
        "--output", f"kind=json,path={out_prefix}benchmarks.json",
        "--output", f"kind=csv,path={out_prefix}benchmarks.csv",
    ]


def genai_perf_command(cfg: dict, corpus: str, concurrency: int,
                       requests: int, out_prefix: str,
                       mode: str = "closed") -> list[str]:
    """Cross-check harness. GenAI-Perf CLI shape.

    PILOT-VERIFY: the --input-file payload schema at the version pin.
    -m and --tokenizer are required for token-based metrics.
    --streaming is a flag; --endpoint-type selects the API shape.
    The OPEN lane does not run on the cross-check harness (spec 03.3).
    """
    assert mode == "closed", "the cross-check harness runs closed-loop only (spec 03.3)"
    return [
        cfg["client"]["genai_perf"], "profile",
        "--service-kind", "openai",
        "--endpoint-type", "completions",
        "--streaming",
        "-m", cfg.get("served_model_name", cfg["model"]),
        "--tokenizer", cfg["model"],
        "--url", cfg["client"]["target"],
        "--concurrency", str(concurrency),
        "--num-prompts", str(requests),
        "--input-file", corpus,
        "--random-seed", str(cfg.get("seed", 42)),
        "--output-file-prefix", out_prefix,
    ]


HARNESS_ADAPTERS = {"guidellm": guidellm_command, "genai-perf": genai_perf_command}


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------


def _stats_from_node(node: dict, key: str) -> dict:
    out = {}
    mean = node.get("mean")
    p90 = node.get("percentiles", {}).get("90")
    p99 = node.get("percentiles", {}).get("99")
    if mean is not None:
        out[f"{key}_mean"] = round(float(mean), 4)
    if p90 is not None:
        out[f"{key}_p90"] = round(float(p90), 4)
    if p99 is not None:
        out[f"{key}_p99"] = round(float(p99), 4)
    return out


def _error_rate_from_node(node: dict) -> float | None:
    """Best-effort V1 error rate. Tolerant to schema drift; the raw
    files are always kept for re-extraction."""
    total = node.get("request_count") or node.get("total_request_count")
    ok = node.get("successful_request_count")
    if isinstance(total, (int, float)) and isinstance(ok, (int, float)) \
            and total > 0:
        return round(100.0 * (total - ok) / total, 2)
    return None


def extract_guidellm_metrics(raw_output_dir: str) -> dict:
    """Best-effort metric extraction from GuideLLM benchmarks.json.

    The exact schema is pinned at Phase 2. Missing fields stay empty.
    """
    metrics: dict = {}
    bench = os.path.join(raw_output_dir, "benchmarks.json")
    if not os.path.exists(bench):
        return metrics
    try:
        with open(bench, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return metrics
    results = data if isinstance(data, list) else data.get("benchmarks", [])
    if not results:
        return metrics
    benchmark = results[0] if isinstance(results[0], dict) else {}
    first = benchmark.get("metrics", benchmark)
    if isinstance(first, dict):
        for key in ("ttft", "tpot", "itl", "request_latency", "output_throughput",
                    "total_throughput", "request_throughput", "goodput"):
            node = first.get(key)
            if isinstance(node, dict):
                metrics.update(_stats_from_node(node, key))
    # V1: request counts sit on the benchmark node, not the metrics node.
    rate = _error_rate_from_node(benchmark)
    if rate is not None:
        metrics["error_rate_pct"] = rate
    return metrics


# GenAI-Perf CSV export: long format (one row per metric) with the unit
# in the metric name, for example "Time to First Token (us)".
_GENAI_NAME_MAP = [
    (("time to first token", "ttft"), "ttft"),
    (("time per output token", "tpot"), "tpot"),
    (("inter token latency", "itl"), "itl"),
    (("request latency", "e2e"), "request_latency"),
    (("output token throughput",), "output_throughput"),
    (("request throughput",), "request_throughput"),
]
_GENAI_UNIT_FACTORS = [("(ns)", 1e-6), ("(us)", 1e-3), ("(µs)", 1e-3),
                       ("(ms)", 1.0), ("(s)", 1000.0)]


def _genai_map_name(name: str) -> str | None:
    low = name.lower()
    for needles, key in _GENAI_NAME_MAP:
        if any(n in low for n in needles):
            return key
    return None


def _genai_unit_factor(name: str, latency: bool) -> float:
    if not latency:
        return 1.0
    for marker, factor in _GENAI_UNIT_FACTORS:
        if marker in name.lower():
            return factor
    return 1.0


def extract_genai_perf_metrics(raw_output_dir: str) -> dict:
    """Best-effort metric extraction from the GenAI-Perf CSV export.

    PILOT-VERIFY: the export layout depends on the genai-perf version.
    Handles the long format (Metric / avg / p50 / p90 / p99 rows).
    Missing fields stay empty; raw output is always kept.
    """
    import glob as _glob
    metrics: dict = {}
    for path in sorted(_glob.glob(os.path.join(raw_output_dir, "*.csv"))):
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, csv.Error):
            continue
        for row in rows:
            name = next(iter(row.values()), "")
            key = _genai_map_name(name)
            if not key:
                continue
            latency = key in ("ttft", "tpot", "itl", "request_latency")
            factor = _genai_unit_factor(name, latency)
            for stat, col in (("mean", "avg"), ("mean", "mean"),
                              ("p90", "p90"), ("p99", "p99")):
                value = row.get(col)
                if value in (None, "", "N/A"):
                    continue
                try:
                    metrics[f"{key}_{stat}"] = round(float(value) * factor, 4)
                except ValueError:
                    continue
    return metrics


HARNESS_EXTRACTORS = {
    "guidellm": extract_guidellm_metrics,
    "genai-perf": extract_genai_perf_metrics,
}


# ---------------------------------------------------------------------------
# Telemetry join (spec 06.6, 06.7; columns of gpu-telemetry.sh)
# ---------------------------------------------------------------------------


def slice_telemetry(telemetry_dir: str, t0: float, t1: float,
                    power_csv: str | None = None) -> dict | None:
    """Slice the SUT telemetry CSV by the run window.

    The sampler writes one file per UTC day with columns:
    timestamp, gpu_index, power_w, temp_c, memory_used_mb, util_pct.
    """
    import glob as _glob
    stamps: set[int] = set()
    powers: list[float] = []
    temps: list[float] = []
    temp_order: list[float] = []
    mems: list[float] = []
    utils: list[float] = []
    out_rows: list[list[str]] = []
    for path in sorted(_glob.glob(os.path.join(telemetry_dir, "*.csv"))):
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                for row in csv.reader(fh):
                    if len(row) < 6:
                        continue
                    ts = parse_iso8601(row[0])
                    if ts is None or not (t0 - 1.0 <= ts <= t1 + 1.0):
                        continue
                    try:
                        powers.append(float(row[2]))
                        temp = float(row[3])
                        temps.append(temp)
                        mems.append(float(row[4]))
                        utils.append(float(row[5]))
                    except ValueError:
                        continue
                    stamps.add(int(ts))
                    temp_order.append(temp)
                    out_rows.append(row)
        except OSError:
            continue
    if not stamps:
        return None
    if power_csv and out_rows:
        with open(power_csv, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(out_rows)
    duration = max(t1 - t0 + 1.0, 1.0)
    return dict(
        power_mean_w=round(sum(powers) / len(powers), 1),
        temp_max_c=round(max(temps), 1),
        temp_first_c=round(temp_order[0], 1),
        temp_last_c=round(temp_order[-1], 1),
        hbm_peak_gb=round(max(mems) / 1024.0, 1),
        gpu_util_pct=round(sum(utils) / len(utils), 1),
        power_coverage_pct=round(100.0 * len(stamps) / duration, 1),
    )


def count_thermal_events(log_text: str) -> int:
    """V7: count thermal keywords in new server log content."""
    if not log_text:
        return 0
    low = log_text.lower()
    return sum(low.count(keyword) for keyword in THERMAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Warmup (spec 03.3: 100 requests or 60 s, whichever is longer)
# ---------------------------------------------------------------------------


def warmup(url: str, model: str, rows: list[dict], target: int,
           timeout_s: int) -> tuple[bool, int]:
    """Sequential warmup. Aborts the test point on repeated failures.

    Runs until both the request target and the 60 s minimum are met
    (spec 03.3: 100 requests or 60 s, whichever is longer). The corpus
    cycles: warmup prompts may repeat, only measured runs need unique
    prompts.
    """
    started = time.time()
    done = 0
    consecutive_failures = 0
    index = 0
    while True:
        if done >= target and (time.time() - started) >= WARMUP_MIN_SECONDS:
            break
        if not rows:
            return False, 0
        row = rows[index % len(rows)]
        index += 1
        if post_completion(url, model, row["prompt"], row["max_tokens"],
                           timeout_s):
            done += 1
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= WARMUP_CONSECUTIVE_FAILURES:
                print(f"WARMUP FAILED: {consecutive_failures} requests "
                      f"in a row ({done} completed)", flush=True)
                return False, done
    return done > 0, done


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def base_record(cfg: dict, point: dict, harness: str, c: int,
                repetition: int, run_dir: str,
                extra_flags: list | None = None,
                value_order: int | None = None,
                run_suffix: str = "") -> dict:
    lane = str(cfg.get("lane", "BASE")).upper()
    mode = str(point.get("mode", "closed")).lower()
    tag = f"R{c}" if mode == "open" else f"C{c}"
    raw_dir = os.path.join(run_dir, HARNESS_DIRS[harness], tag)
    primary = harness == PRIMARY_HARNESS
    # The run suffix (for example REF-start) is part of the run_id, not
    # just the directory name. Without it, reference records collide
    # with test-point records that share profile, scenario, regime,
    # value and repetition, and the assembler drops one silently.
    suffix = f"-{run_suffix}" if run_suffix else ""
    record = dict(
        run_id=f"{cfg['run_id_prefix']}-{point['profile']}-{point['scenario']}"
               f"-{point['regime']}-{tag}-r{repetition}-{harness}{suffix}",
        campaign_id=cfg.get("campaign_id"),
        sut=cfg["sut"], stack=cfg["stack"],
        stack_version=cfg["stack_version"], harness=harness,
        model=cfg["model"], model_revision=cfg.get("model_revision"),
        precision=cfg.get("precision"),
        comparability=cfg.get("comparability", ""),
        config_lane=lane,
        tp=cfg.get("tp"),
        payload_profile=point["profile"],
        prompt_scenario=point["scenario"], regime=point["regime"],
        traffic_mode=mode, concurrency=c, repetition=repetition,
        value_order=value_order if value_order is not None else "",
        order_seed=cfg.get("seed", 42),
        run_dir=run_dir, raw_dir=raw_dir, metrics={},
        flags=list(cfg.get("flags", [])) + (["TUNED"] if lane == "TUNED" else [])
        + (["OPEN"] if mode == "open" else [])
        + ([] if primary else ["CROSSCHECK"])
        + list(extra_flags or []),
        run_started_at=now_utc(), generated_at=now_utc(),
    )
    os.makedirs(raw_dir, exist_ok=True)
    return record


def append_record(out_root: str, record: dict) -> None:
    """Append exactly one record per call (no rewrite of the file)."""
    os.makedirs(out_root, exist_ok=True)
    path = os.path.join(out_root, "runs.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


# ---------------------------------------------------------------------------
# Procedure (spec 05.3)
# ---------------------------------------------------------------------------


def run_measured(cfg: dict, point: dict, corpus: str, run_dir: str,
                 harness: str, c: int, repetition: int,
                 telemetry_dir: str | None, mode: str,
                 server: Server, value_order: int | None = None,
                 extra_flags: list | None = None,
                 run_suffix: str = "",
                 gpu_ids: list | None = None) -> dict:
    """One measured pass: harness run, extraction, validity checks.

    The server is already running and warmed up (sweep_harness).
    Returns a record. A failure returns an INVALID record so that no
    result is silently dropped (spec 00, spec 05.7).
    """
    tag = f"R{c}" if mode == "open" else f"C{c}"
    record = base_record(cfg, point, harness, c, repetition, run_dir,
                         extra_flags=extra_flags, value_order=value_order,
                         run_suffix=run_suffix)
    raw_dir = record["raw_dir"]

    # Step 7 / step 10: measured run with the harness.
    t0 = time.time()
    cmd = HARNESS_ADAPTERS[harness](
        cfg, corpus, c, point.get("requests", 1000),
        os.path.join(raw_dir, ""), mode)
    record["command"] = " ".join(cmd)
    result = run(cmd, capture=False)
    t1 = time.time()
    record["exit_code"] = result.returncode
    with open(os.path.join(raw_dir, "exit_code.txt"), "w") as fh:
        fh.write(str(result.returncode))
    record["run_finished_at"] = now_utc()
    record["run_window_s"] = round(t1 - t0, 1)
    metrics = HARNESS_EXTRACTORS[harness](raw_dir)

    # V7: thermal events in the server log content of this pass.
    thermal = count_thermal_events(server.read_log())
    if thermal:
        record["flags"] += ["INVALID", "THERMAL"]
        record["thermal_events"] = thermal

    # Non-zero harness exit: INVALID.
    if result.returncode != 0:
        record["flags"] += ["INVALID", "HARNESS-EXIT"]
        record["metrics"] = metrics
        return record

    # Extraction failed (for example: the harness output schema
    # changed and nothing parsed). A measured pass without core
    # metrics is INVALID, never a usable result (review defect 3).
    # Cross-check rows carry no validity level, so they are exempt.
    if harness == PRIMARY_HARNESS \
            and any(m not in metrics for m in REQUIRED_PRIMARY_METRICS):
        record["flags"] += ["INVALID", "MISSING-METRICS"]

    # Telemetry join (M12 to M16) and V5 coverage check. The GPU
    # temperature at the start and end of the pass is recorded for
    # the thermal-soak audit (spec 05.3 step 6).
    if telemetry_dir:
        tele = slice_telemetry(
            telemetry_dir, t0, t1,
            os.path.join(run_dir, f"power-{HARNESS_DIRS[harness]}-{tag}.csv"),
            gpu_ids=gpu_ids)
        if tele:
            metrics.update(tele)
            record["temp_start_c"] = tele["temp_first_c"]
            record["temp_end_c"] = tele["temp_last_c"]
            if tele["power_coverage_pct"] < MIN_POWER_COVERAGE_PCT:
                record["flags"] += ["INVALID", "POWER-COVERAGE"]

    # V1: error + timeout rate.
    error_rate = metrics.get("error_rate_pct")
    if error_rate is not None and error_rate >= MAX_ERROR_RATE_PCT:
        record["flags"] += ["INVALID", "ERROR-RATE"]

    record["metrics"] = metrics
    return record


def sweep_harness(cfg: dict, point: dict, corpus: str, run_dir: str,
                  harness: str, values: list, repetition: int,
                  telemetry_dir: str | None, mode: str,
                  server: Server, order: list | None = None,
                  extra_flags: list | None = None,
                  run_suffix: str = "",
                  gpu_ids: list | None = None) -> list[dict]:
    """One server lifecycle for the whole sweep of one harness
    (spec 05.3): start, health wait, one warmup, a 60 s thermal soak,
    then every concurrency value in the given order (randomized per
    spec 05.2.1), with a 15 s soak between passes.

    On a failed pass, restart the server and retry the value once
    (spec 04.5 rule 4). A failure that persists produces one INVALID
    record; the sweep continues with the next value.
    """
    timeout_s = REQUEST_TIMEOUTS.get(point["profile"], DEFAULT_REQUEST_TIMEOUT)
    warm_target = point.get("warmup_requests", 100)
    corpus_rows = read_corpus_requests(corpus)
    records: list[dict] = []

    for idx, c in enumerate(order if order is not None else values):
        # Soak between measured passes (spec 05.3 step 6).
        if idx > 0:
            print(f"soak {SOAK_BETWEEN_PASSES_S} s before {c}", flush=True)
            time.sleep(SOAK_BETWEEN_PASSES_S)
        record: dict | None = None
        for attempt in (1, 2):
            # Start and warm up when the server is not running:
            # first value of the sweep, or after a failure.
            if not server.alive:
                if not server.start():
                    if attempt == 1:
                        print("server start failed, retrying (spec 04.5.4)",
                              flush=True)
                        continue
                    record = base_record(cfg, point, harness, c,
                                         repetition, run_dir,
                                         extra_flags=extra_flags,
                                         value_order=idx + 1,
                                         run_suffix=run_suffix)
                    record["flags"] += ["INVALID", "SERVER-START"]
                    record["run_finished_at"] = now_utc()
                    break
                ok, done = warmup(f"http://localhost:{server.port}",
                                  cfg.get("served_model_name", cfg["model"]),
                                  corpus_rows, warm_target, timeout_s)
                print(f"warmup: {done} requests (target {warm_target})",
                      flush=True)
                if not ok:
                    server.stop()
                    if attempt == 1:
                        print("warmup failed, retrying (spec 04.5.4)",
                              flush=True)
                        continue
                    record = base_record(cfg, point, harness, c,
                                         repetition, run_dir,
                                         extra_flags=extra_flags,
                                         value_order=idx + 1,
                                         run_suffix=run_suffix)
                    record["flags"] += ["INVALID", "WARMUP"]
                    record["run_finished_at"] = now_utc()
                    break
                # Thermal soak after the warmup, before the first
                # measured pass (spec 05.3 step 6).
                print(f"soak {SOAK_AFTER_WARMUP_S} s after warmup",
                      flush=True)
                time.sleep(SOAK_AFTER_WARMUP_S)
            record = run_measured(cfg, point, corpus, run_dir, harness, c,
                                  repetition, telemetry_dir, mode, server,
                                  value_order=idx + 1,
                                  extra_flags=extra_flags,
                                  run_suffix=run_suffix,
                                  gpu_ids=gpu_ids)
            if "INVALID" not in record["flags"] or attempt == 2:
                break
            # The server may have died mid-pass. Restart for the retry.
            print(f"retrying {record['run_id']} once (spec 04.5.4)", flush=True)
            server.stop()
        records.append(record)
    if server.alive:
        server.stop()
    return records


def write_run_meta(run_dir: str, cfg: dict, point: dict, corpus: str,
                   digest: str, passes: list[dict], mode: str = "closed",
                   value_order: list | None = None,
                   run_suffix: str = "") -> None:
    """Per-run environment record (spec 07.4).

    The node-level fields (OS, driver, BIOS) come from the Ansible
    env-record role. This file adds the run-level fields, including
    the benchmark versions (spec 00.3.5), the comparability category
    (spec 04.1.6), the sweep order (spec 05.2.1) and the thermal
    soak settings (spec 05.3 step 6).
    """
    harnesses: dict[str, list[dict]] = {}
    for p in passes:
        harnesses.setdefault(p["harness"], []).append(dict(
            concurrency=p.get("concurrency"),
            value_order=p.get("value_order", ""),
            exit_code=p.get("exit_code"),
            run_window_s=p.get("run_window_s"),
            temp_start_c=p.get("temp_start_c", ""),
            temp_end_c=p.get("temp_end_c", ""),
            command=p.get("command", ""),
        ))
    suffix = f"-{run_suffix}" if run_suffix else ""
    meta = dict(
        run_id=f"{cfg['run_id_prefix']}-{point['profile']}-{point['scenario']}"
               f"-{point['regime']}{suffix}",
        campaign_id=cfg.get("campaign_id"),
        sut=cfg["sut"], stack=cfg["stack"],
        stack_version=cfg["stack_version"],
        config_lane=str(cfg.get("lane", "BASE")).upper(),
        comparability=cfg.get("comparability", ""),
        traffic_mode=str(mode).lower(),
        image=cfg["server"]["image"],
        image_digest=digest,
        model=cfg["model"],
        model_revision=cfg.get("model_revision", ""),
        precision=cfg.get("precision"),
        tp=cfg.get("tp"),
        server_settings=cfg["server"].get("args", ""),
        corpus_path=os.path.abspath(corpus),
        corpus_sha256=corpus_sha256(corpus),
        order_seed=cfg.get("seed", 42),
        value_order=list(value_order) if value_order is not None else [],
        soak_s=dict(after_warmup=SOAK_AFTER_WARMUP_S,
                    between_passes=SOAK_BETWEEN_PASSES_S),
        harnesses=harnesses,
        versions=load_versions(),
        tool_version=TOOL_VERSION,
        generated_at=now_utc(),
    )
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    # V6: environment record completeness.
    required = ["campaign_id", "sut", "stack", "stack_version", "image_digest",
                "model", "corpus_sha256"]
    if any(not meta.get(field) for field in required):
        missing = [f for f in required if not meta.get(f)]
        print(f"V6 WARNING: meta.json incomplete: {missing}", flush=True)


def run_test_point(cfg: dict, point: dict, corpora_root: str, out_root: str,
                   harness_filter: str | None, repetition: int,
                   telemetry_dir: str | None,
                   cross_check: bool = False,
                   extra_flags: list | None = None,
                   run_suffix: str = "") -> list[dict]:
    profile = point["profile"]
    model_dir = cfg["model"].replace("/", "_")
    corpus = os.path.join(corpora_root, model_dir, f"{profile}.jsonl")
    if not os.path.exists(corpus):
        print(f"SKIP: corpus missing: {corpus}")
        return []

    mode = str(point.get("mode", "closed")).lower()
    # Closed-loop: concurrency values. Open-loop: request rates.
    values = point.get("rate" if mode == "open" else "concurrency", [1])
    if not isinstance(values, list):
        values = [values]

    # One primary harness (spec 03.1), fixed for every stack and lane.
    # Cross-check tools run only on request, only closed-loop, and
    # only after the primary results (spec 03.1).
    harnesses = [PRIMARY_HARNESS]
    if cross_check and mode == "closed":
        harnesses += CROSS_CHECK_HARNESSES
    if harness_filter:
        harnesses = [harness_filter]

    # One run directory per test point and repetition. Every harness
    # sweep and every concurrency value lives inside it (spec 07.1).
    # The traffic mode is part of the name: the OPEN lane is a
    # separate test point and must not share the directory. Reference
    # runs (spec 05.2.2) carry their label in the name.
    run_dir = os.path.join(
        out_root, cfg["run_id_prefix"],
        f"{profile}-{point['scenario']}-{point['regime']}"
        + ("-OPEN" if mode == "open" else "")
        + (f"-{run_suffix}" if run_suffix else "")
        + f"-r{repetition}")
    os.makedirs(run_dir, exist_ok=True)
    server = Server(cfg["server"], run_dir)
    digest = server.image_digest()

    # Seeded random value order, re-shuffled per repetition
    # (spec 05.2.1). The same (seed, key) re-shuffles identically,
    # so a re-run of a stopped campaign reuses the recorded order.
    order = shuffled_values(cfg.get("seed", 42),
                            sweep_key(cfg, point, mode, repetition),
                            values)
    print(f"value order: {order}", flush=True)

    records: list[dict] = []
    # One server lifecycle per harness sweep: the primary harness
    # sweeps all values, then the server stops (spec 05.3).
    gpu_ids = cfg["server"].get("gpu_ids")
    for harness in harnesses:
        records += sweep_harness(cfg, point, corpus, run_dir, harness,
                                 values, repetition, telemetry_dir,
                                 mode, server, order=order,
                                 extra_flags=extra_flags,
                                 run_suffix=run_suffix,
                                 gpu_ids=gpu_ids)
    write_run_meta(run_dir, cfg, point, corpus, digest, records, mode,
                   value_order=order, run_suffix=run_suffix)
    return records


def load_manifest(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("pyyaml is required: pip install pyyaml") from exc
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_manifest(cfg: dict) -> list[str]:
    """Built-in manifest checks (tools/schemas/manifest.schema.json is
    the full schema; jsonschema is used when installed)."""
    problems = []
    for field in ("run_id_prefix", "campaign_id", "sut", "stack",
                  "stack_version", "model", "precision", "server",
                  "client", "test_points"):
        if not cfg.get(field):
            problems.append(f"missing field: {field}")
    comparability = cfg.get("comparability")
    if comparability not in COMPARABILITY_CATEGORIES:
        problems.append("comparability must be one of "
                        f"{', '.join(COMPARABILITY_CATEGORIES)} "
                        f"(spec 04.1.6); got {comparability!r}")
    try:
        import jsonschema
    except ImportError:
        return problems
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "schemas", "manifest.schema.json")
    if not os.path.exists(schema_path):
        return problems
    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError as exc:
        problems.append(f"jsonschema: {exc.message}")
    return problems


def run_reference(cfg: dict, corpora_root: str, out_root: str,
                  telemetry_dir: str | None, label: str) -> list[dict]:
    """One reference point run (spec 05.2.2). One repetition, no
    cross-check. Records carry the REFERENCE flag."""
    ref = {**DEFAULT_REFERENCE, **cfg.get("reference", {})}
    point = dict(
        profile=ref["profile"],
        scenario=ref.get("scenario", "P-U"),
        regime=ref.get("regime", "W-ON"),
        concurrency=ref.get("concurrency", [32]),
        requests=ref.get("requests", 200),
        warmup_requests=ref.get("warmup_requests", 50),
    )
    print(f"=== reference point ({label}) ===", flush=True)
    return run_test_point(cfg, point, corpora_root, out_root, None, 1,
                          telemetry_dir, cross_check=False,
                          extra_flags=["REFERENCE"], run_suffix=f"REF-{label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("selftest", help="validate the scheduling and order "
                     "logic without a server")
    parser.add_argument("--config", default=None, help="run manifest YAML")
    parser.add_argument("--corpora", default="corpora")
    parser.add_argument("--out", default="results")
    parser.add_argument("--telemetry", default=None,
                        help="SUT telemetry directory (gpu-telemetry CSVs). "
                             "Copies the run window to power.csv and "
                             "computes M12 to M16 and V5.")
    parser.add_argument("--harness", choices=sorted(HARNESS_ADAPTERS),
                        default=None,
                        help="run one tool only (debug). Default: the "
                             "primary harness only (spec 03.1).")
    parser.add_argument("--cross-check", action="store_true",
                        help="also run the cross-check tools after the "
                             "primary harness (spec 03.1). Records carry "
                             "the CROSSCHECK flag.")
    parser.add_argument("--repetitions", type=int, default=None,
                        help="override the manifest repetition count")
    args = parser.parse_args()

    if args.cmd == "selftest":
        return selftest()
    if not args.config:
        parser.print_help()
        return 1

    cfg = load_manifest(args.config)
    problems = validate_manifest(cfg)
    if problems:
        print("manifest problems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    repetitions = args.repetitions or cfg.get(
        "repetitions", DEFAULT_REPETITIONS)

    steps = plan_schedule(cfg.get("test_points", []),
                          "reference" in cfg,
                          cfg.get("reference_every", DEFAULT_REFERENCE_EVERY))
    consecutive_invalid = 0
    total = 0

    def emit(records: list[dict]) -> int:
        nonlocal consecutive_invalid, total
        for record in records:
            append_record(args.out, record)
            total += 1
            if "INVALID" in record["flags"]:
                consecutive_invalid += 1
                print(f"INVALID ({consecutive_invalid} consecutive): "
                      f"{record['run_id']} flags={record['flags']}",
                      flush=True)
                if consecutive_invalid >= MAX_CONSECUTIVE_INVALID:
                    print("STOP: two consecutive INVALID results. "
                          "The SUT campaign stops (spec 05.7). "
                          "Fix the cause, then restart the campaign.",
                          flush=True)
                    return 1
            else:
                consecutive_invalid = 0
        return 0

    for step, index in steps:
        if step == "reference":
            if emit(run_reference(cfg, args.corpora, args.out,
                                  args.telemetry, index)) == 1:
                return 2
            continue
        point = cfg["test_points"][index - 1]
        for repetition in range(1, repetitions + 1):
            records = run_test_point(cfg, point, args.corpora, args.out,
                                     args.harness, repetition,
                                     args.telemetry, args.cross_check)
            if emit(records) == 1:
                return 2

    print(f"wrote {total} run records to {args.out}/runs.jsonl")
    return 0


def selftest() -> int:
    """Validate the order and schedule logic. No server, no docker."""
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"  {'ok' if cond else 'FAIL'}: {name}", flush=True)
        if not cond:
            failures.append(name)

    values = [1, 2, 4, 8, 16, 32, 64, 128, 256]
    key = "sut1-vllm/PF-1/P-U/W-ON/closed/r1"
    o1 = shuffled_values(42, key, values)
    o2 = shuffled_values(42, key, values)
    o3 = shuffled_values(42, "sut1-vllm/PF-1/P-U/W-ON/closed/r2", values)
    check("order is deterministic for the same key", o1 == o2)
    check("order covers every value exactly once",
          sorted(o1) == sorted(values))
    check("order differs across repetitions", o1 != o3)

    pts = [dict(profile="PF-1"), dict(profile="PF-2"), dict(profile="PF-3")]
    s1 = plan_schedule(pts, True, 10)
    check("schedule without mid points",
          s1 == [("reference", "start"), ("test-point", 1),
                 ("test-point", 2), ("test-point", 3),
                 ("reference", "end")])
    s2 = plan_schedule([dict() for _ in range(4)], True, 2)
    check("schedule with a mid point after every 2",
          s2 == [("reference", "start"), ("test-point", 1),
                 ("test-point", 2), ("reference", "mid2"),
                 ("test-point", 3), ("test-point", 4),
                 ("reference", "end")])
    s3 = plan_schedule(pts, False, 10)
    check("schedule without reference",
          s3 == [("test-point", 1), ("test-point", 2),
                 ("test-point", 3)])

    good = dict(
        run_id_prefix="sut1-vllm", campaign_id="2026Q4", sut="SUT-1",
        stack="vllm", stack_version="0.32.0", model="Qwen/Qwen3.8-27B",
        precision="BF16", comparability="STRICT",
        server=dict(image="vllm/vllm-openai:v0.32.0"),
        client=dict(target="http://sut1:8000",
                    guidellm="/opt/8gpu-bench/harness/venv/bin/guidellm"),
        test_points=[dict(profile="PF-1", scenario="P-U", regime="W-ON",
                          concurrency=[1, 32])],
    )
    # Run IDs: a reference record must never collide with a test-point
    # record that shares profile, scenario, regime, value and
    # repetition. The assembler deduplicates by run_id (review defect 1).
    import tempfile as _tempfile
    point = dict(profile="PF-1", scenario="P-U", regime="W-ON",
                 mode="closed")
    with _tempfile.TemporaryDirectory() as tmp:
        tp_rec = base_record(good, point, "guidellm", 32, 1, tmp)
        ref_rec = base_record(good, point, "guidellm", 32, 1, tmp,
                              extra_flags=["REFERENCE"],
                              run_suffix="REF-start")
    check("reference run_id does not collide with the test point",
          tp_rec["run_id"] != ref_rec["run_id"]
          and ref_rec["run_id"].endswith("-REF-start"))

    # A failed extraction (for example: harness output schema drift)
    # must lack the core metrics, which makes the pass INVALID
    # (review defect 3).
    with _tempfile.TemporaryDirectory() as tmp:
        empty_metrics = extract_guidellm_metrics(tmp)
    check("empty extraction lacks the core metrics (INVALID path)",
          any(m not in empty_metrics
              for m in REQUIRED_PRIMARY_METRICS))

    check("valid manifest passes", validate_manifest(good) == [])
    bad = {k: v for k, v in good.items()}
    bad.pop("comparability")
    check("missing comparability fails",
          any("comparability" in p for p in validate_manifest(bad)))
    bad2 = dict(good, comparability="WRONG")
    check("bad comparability fails",
          any("comparability" in p for p in validate_manifest(bad2)))

    if failures:
        print(f"selftest FAILED: {len(failures)} checks")
        return 1
    print("selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
