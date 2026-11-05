#!/usr/bin/env python3
"""8gpu-bench test-point runner. Implements the spec 05.3 procedure.

One run manifest declares one stack on one SUT. The runner executes
every test point in it: server start, health wait, warmup, Harness A
(GuideLLM), server restart, Harness B (GenAI-Perf), three repetitions.

Usage:
  run-benchmark.py --config runs/sut1-vllm.yaml --corpora corpora \\
      --out results/2026Q4

Corpus files come from gen-corpus.py: corpora/<model>/<profile>.jsonl

Adapter status:
  GuideLLM adapter: implemented from the documented CLI.
  GenAI-Perf adapter: PILOT-VERIFY. Confirm the exact CLI flags and
  the custom-dataset schema at the version pin (spec 03.5), then
  adjust the command in genai_perf_command().
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOOL_VERSION = "1.0.0"
WARMUP_TAG = "warmup"


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


def post_completion(url: str, model: str, prompt: str, max_tokens: int) -> bool:
    body = json.dumps(
        {"model": model, "prompt": prompt, "max_tokens": max_tokens,
         "temperature": 0}
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/completions", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


class Server:
    """Container lifecycle for the stack under test."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = "bench-server"
        self.port = cfg["port"]

    def start(self) -> bool:
        subprocess.run(["docker", "rm", "-f", self.name],
                       capture_output=True)
        cmd = ["docker", "run", "-d", "--name", self.name,
               "--gpus", self.cfg.get("gpus", "all"),
               "--shm-size", self.cfg.get("shm_size", "64g"),
               "-p", f"{self.port}:{self.cfg.get('container_port', 8000)}"]
        for mount in self.cfg.get("mounts", []):
            cmd += ["-v", mount]
        cmd.append(self.cfg["image"])
        if self.cfg.get("args"):
            cmd += shlex.split(self.cfg["args"])
        result = run(cmd)
        if result.returncode != 0:
            print(result.stderr)
            return False
        return wait_health(f"http://localhost:{self.port}/health")

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        time.sleep(5)


# ---------------------------------------------------------------------------
# Harness adapters
# ---------------------------------------------------------------------------


def guidellm_command(cfg: dict, corpus: str, concurrency: int,
                     requests: int, out_prefix: str) -> list[str]:
    """Harness A. CLI shape from the GuideLLM docs. PILOT-VERIFY flags
    and file schema at the version pin."""
    return [
        cfg["client"]["guidellm"], "run",
        "--backend", f"kind=openai_http,target={cfg['client']['target']}",
        "--data", f"kind=custom,path={corpus}",
        "--profile", f"kind=constant,concurrency={concurrency}",
        "--constraint", f"kind=max_requests,requests={requests}",
        "--output", f"prefix={out_prefix}",
    ]


def genai_perf_command(cfg: dict, corpus: str, concurrency: int,
                       requests: int, out_prefix: str) -> list[str]:
    """Harness B. PILOT-VERIFY: confirm flags and the custom dataset
    schema at the version pin (spec 03.5)."""
    return [
        cfg["client"]["genai_perf"], "profile",
        "--service-kind", "openai",
        "--endpoint-type", "streaming",
        "--url", cfg["client"]["target"],
        "--concurrency", str(concurrency),
        "--num-prompts", str(requests),
        "--input-dataset", corpus,
        "--output-file-prefix", out_prefix,
    ]


HARNESS_ADAPTERS = {"guidellm": guidellm_command, "genai-perf": genai_perf_command}


def extract_metrics(raw_output_dir: str) -> dict:
    """Best-effort metric extraction from GuideLLM benchmarks.json.

    The exact schema is pinned at Phase 2. Missing fields stay empty.
    The raw files are always kept for re-extraction.
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
    first = results[0].get("metrics", results[0])
    for key in ("ttft", "tpot", "itl", "request_latency", "output_throughput",
                "total_throughput", "request_throughput", "goodput"):
        node = first.get(key) if isinstance(first, dict) else None
        if not isinstance(node, dict):
            continue
        mean = node.get("mean")
        p90 = node.get("percentiles", {}).get("90")
        p99 = node.get("percentiles", {}).get("99")
        if mean is not None:
            metrics[f"{key}_mean"] = round(float(mean), 4)
        if p90 is not None:
            metrics[f"{key}_p90"] = round(float(p90), 4)
        if p99 is not None:
            metrics[f"{key}_p99"] = round(float(p99), 4)
    return metrics


# ---------------------------------------------------------------------------
# Procedure (spec 05.3)
# ---------------------------------------------------------------------------


def run_test_point(cfg: dict, point: dict, corpora_root: str, out_root: str,
                   harness: str, repetition: int) -> dict | None:
    profile = point["profile"]
    model_dir = cfg["model"].replace("/", "_")
    corpus = os.path.join(corpora_root, model_dir, f"{profile}.jsonl")
    if not os.path.exists(corpus):
        print(f"SKIP: corpus missing: {corpus}")
        return None

    concurrency = point.get("concurrency", [1])
    if not isinstance(concurrency, list):
        concurrency = [concurrency]

    server = Server(cfg["server"])
    adapter = HARNESS_ADAPTERS[harness]
    records = []

    for c in concurrency:
        run_dir = os.path.join(
            out_root, cfg["run_id_prefix"], harness,
            f"{profile}-{point['scenario']}-{point['regime']}-C{c}-r{repetition}")
        raw_dir = os.path.join(run_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)

        # Step 1 to 4: start the server and wait for health.
        if not server.start():
            print(f"INVALID: server did not become healthy (C{c})")
            continue
        try:
            # Step 5: warmup. Simple sequential requests.
            warm = point.get("warmup_requests", 100)
            done = 0
            for row in read_corpus_requests(corpus):
                if done >= warm:
                    break
                if post_completion(f"http://localhost:{server.port}",
                                   cfg.get("served_model_name", cfg["model"]),
                                   row["prompt"], row["max_tokens"]):
                    done += 1
            print(f"warmup: {done} requests")

            # Step 7: measured run with the harness.
            prefix = os.path.join(raw_dir, "")
            cmd = adapter(cfg, corpus, c, point.get("requests", 1000), prefix)
            result = run(cmd, capture=False)
            with open(os.path.join(raw_dir, "exit_code.txt"), "w") as fh:
                fh.write(str(result.returncode))
            metrics = extract_metrics(raw_dir) if harness == "guidellm" else {}
        finally:
            server.stop()

        records.append(dict(
            run_id=f"{cfg['run_id_prefix']}-{profile}-{point['scenario']}"
                   f"-{point['regime']}-C{c}-r{repetition}-{harness}",
            campaign_id=cfg.get("campaign_id"),
            sut=cfg["sut"], stack=cfg["stack"],
            stack_version=cfg["stack_version"], harness=harness,
            model=cfg["model"], precision=cfg.get("precision"),
            tp=cfg.get("tp"), payload_profile=profile,
            prompt_scenario=point["scenario"], regime=point["regime"],
            concurrency=c, repetition=repetition,
            raw_dir=raw_dir, metrics=metrics,
            flags=[], generated_at=now_utc(),
        ))
        write_run_records(out_root, records)
    return records


def read_corpus_requests(corpus: str) -> list[dict]:
    rows = []
    with open(corpus, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("_meta"):
                continue
            rows.append(rec)
    return rows


def write_run_records(out_root: str, records: list[dict]) -> None:
    path = os.path.join(out_root, "runs.jsonl")
    os.makedirs(out_root, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_manifest(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("pyyaml is required: pip install pyyaml") from exc
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="run manifest YAML")
    parser.add_argument("--corpora", default="corpora")
    parser.add_argument("--out", default="results")
    parser.add_argument("--harness", choices=sorted(HARNESS_ADAPTERS),
                        default="guidellm")
    parser.add_argument("--repetitions", type=int, default=None,
                        help="override the manifest repetition count")
    args = parser.parse_args()

    cfg = load_manifest(args.config)
    repetitions = args.repetitions or cfg.get("repetitions", 3)

    for point in cfg.get("test_points", []):
        for repetition in range(1, repetitions + 1):
            run_test_point(cfg, point, args.corpora, args.out,
                           args.harness, repetition)
    return 0


if __name__ == "__main__":
    sys.exit(main())
