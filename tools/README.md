# Tools

Phase 2 tooling for the 8gpu-bench specification.

## gen-corpus.py — corpus generator (spec 04)

Builds the payload profile corpora. One corpus per profile, per model
tokenizer. Both harnesses consume the same corpus file.

```
# 1. Fetch and pin the source records (one time).
python3 tools/gen-corpus.py fetch-sources --out-dir sources

# 2. Generate a corpus for a model tokenizer.
python3 tools/gen-corpus.py generate --profile PF-1 \
    --model Qwen/Qwen3.8-27B --sources-dir sources --out-dir corpora

# 3. Validate the generation logic without any dependency.
python3 tools/gen-corpus.py selftest
```

Output: `corpora/<model>/<profile>.jsonl` plus a `.meta.json` sidecar
with sources, revisions, attribution, expected cache hit, and the
corpus checksum. The first line of each corpus file is a `_meta`
record. Harness adapters skip it.

Sources (spec 04.2.2): PG-19 (session history), WildChat-1M (chat
and prompt text for every non-session profile). Fetch records the
dataset revisions. FineWeb is a fallback source, fetched only with
`--fineweb-docs N`.

## run-benchmark.py — test-point runner (spec 05.3)

Executes one run manifest. Per repetition of every test point, it
runs the spec 05.3 sequence: server start, health wait, one warmup,
the primary harness sweeps all concurrency values in ascending
order, server stop. One server start per test point and repetition.
Writes `runs.jsonl` run records and a per-run `meta.json`.

```
python3 tools/run-benchmark.py --config runs/example-nvidia.yaml \
    --corpora corpora --out results/2026Q4 \
    --telemetry /mnt/sut1/gpu-telemetry
```

The harness is a fixed instrument (spec 03.1): every stack and lane
is measured with GuideLLM. Cross-check tools run after the primary
results and never enter the headline tables:

```
python3 tools/run-benchmark.py --config runs/example-nvidia.yaml \
    --corpora corpora --out results/2026Q4 --cross-check
```

See `runs/example-nvidia.yaml`, `runs/example-amd.yaml` and
`runs/example-tuned.yaml` for the manifest schema. The AMD example
shows the ROCm `docker_args`. The tuned example shows the CFG-TUNED
lane (spec 02.4.1). Test points accept `mode: OPEN` with a `rate`
list for the open-loop lane (spec 03.3, primary harness only).

Validity handling (spec 05.7):
- Failed server starts and failed warmups produce INVALID records.
  Nothing is silently dropped.
- A failed pass is retried once (spec 04.5 rule 4).
- Two consecutive INVALID results stop the campaign.
- V1 (error rate), V5 (power coverage), V6 (record completeness) and
  V7 (thermal events) are checked where data is available.
  V3 (CoV) is computed by build-results.py.

`--telemetry` points at the SUT telemetry directory (the output of
the `gpu-telemetry` service). The runner slices it by run window,
writes `power-harness-A.csv` / `power-harness-B.csv` per run, and
fills the power, temperature, utilization and memory metrics.

Adapter status: both adapters match the upstream CLI documentation
(Nov 2026). PILOT-VERIFY the custom-dataset schema of each harness
at the version pin (spec 03.5, PLAN item 8). Raw harness output is
always kept under `harness-A/` and `harness-B/`.

## build-results.py — results assembler (spec 07)

Merges `runs.jsonl` records into `results.csv` and `headlines.md`.

```
python3 tools/build-results.py --runs results/2026Q4 --out results/2026Q4
```

- `results.csv`: one row per (test point, harness, repetition),
  columns per spec 07.2, including the config lane, traffic mode and
  success rate.
- `headlines.md`: one table per model, precision, scenario, regime,
  profile, config lane and traffic mode. Rows sit at the highest
  measured concurrency, with the delivery metrics (throughput,
  success rate, TTFT, TPOT, E2E). Values are the median of the
  repetitions. A coefficient of variation of 5% or more across
  repetitions marks the row INVALID (V3).
