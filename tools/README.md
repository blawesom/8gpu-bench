# Tools

Phase 2 tooling for the 8gpu-bench specification. Benchmark spec
1.1.0; the tool versions live in the repository `VERSION` file.

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
with sources, revisions, attribution, the corpus version, the expected
cache hit, and the corpus checksum. The first line of each corpus
file is a `_meta` record. Harness adapters skip it.

Sources (spec 04.2.2): PG-19 (session history), WildChat-1M (chat
and prompt text for every non-session profile). Fetch records the
dataset revisions. FineWeb is a fallback source, fetched only with
`--fineweb-docs N`.

## run-benchmark.py — test-point runner (spec 05.3)

Executes one run manifest. Per repetition of every test point it runs
the spec 05.3 sequence: server start, health wait, one warmup, a
60 s thermal soak, then the primary harness sweeps every concurrency
value in a seeded random order (spec 05.2.1), with a 15 s soak
between passes, then server stop. One server start per test point and
repetition.

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

Manifest requirements (checked at start, spec 04.1.6):
- `comparability`: STRICT, ARCH, or HWBEST. Recorded on every row.
  The assembler re-checks it against the model and precision (V9).
- `repetitions`: 5 for core test points, 7 for the Tier 2 headline
  points (spec 05.4).
- Optional `reference` block (spec 05.2.2): the drift-detection
  point. The runner runs it before the first test point, after every
  `reference_every` test points (default 10), and after the last one.
  Records carry the REFERENCE flag.

See `runs/example-nvidia.yaml`, `runs/example-amd.yaml` and
`runs/example-tuned.yaml` for the manifest schema
(`tools/schemas/manifest.schema.json`). The AMD example shows the
ROCm `docker_args`. The tuned example shows the CFG-TUNED lane
(spec 02.4.1). Test points accept `mode: OPEN` with a `rate`
list for the open-loop lane (spec 03.3, primary harness only).

Each run directory's `meta.json` records the benchmark versions
(spec 00.3.5), the comparability category, the order seed and the
actual value order of the sweep, the soak settings, and the GPU
temperature at the start and end of each measured pass.

Validity handling (spec 05.7):
- Failed server starts and failed warmups produce INVALID records.
  Nothing is silently dropped.
- A failed pass is retried once (spec 04.5 rule 4).
- Two consecutive INVALID results stop the campaign.
- V1 (error rate), V5 (power coverage), V6 (record completeness) and
  V7 (thermal events) are checked where data is available.
  V3 (CoV), V8 (reference drift) and V9 (comparability) are computed
  by build-results.py.

`--telemetry` points at the SUT telemetry directory (the output of
the `gpu-telemetry` service). The runner slices it by run window and
by the manifest `server.gpu_ids` (all GPUs when unset), writes
`power-guidellm-C<c>.csv` per run, and fills the power, temperature,
utilization and memory metrics. `power_mean_w` and `power_peak_w`
are the per-timestamp sum over the GPU set (M16, M17, spec 06.6);
`power_per_gpu_mean_w` keeps the per-GPU view. Idle GPUs never
enter the resource metrics.

Adapter status: the GuideLLM adapter matches the upstream CLI
documentation (Nov 2026). PILOT-VERIFY the custom-dataset schema at
the version pin (spec 03.5, PLAN item 8). Raw harness output is
always kept under `guidellm/` (primary) and `crosscheck-genai-perf/`
(cross-check).

## build-results.py — results assembler (spec 07)

Reads the `runs.jsonl` records of one campaign and writes
`<campaign>/aggregate/`:

- `results.json`: the canonical result file (spec 07.1.2). Every raw
  record, the per-test-point aggregates (median, p10, p90, CV, 95%
  bootstrap confidence interval of M5), the benchmark versions, and
  the V8 reference drift.
- `results.csv`: generated from results.json (spec 07.2). One row per
  (test point, harness, repetition), with the per-run validity level.
- `headlines.md`: generated from results.json (spec 07.3). One table
  per comparability category, model, precision, scenario, regime,
  profile, config lane and traffic mode. M5 prints its confidence
  interval.
- `validation.json`: per-run and per-test-point validity levels
  (VALID / QUALIFIED / INVALID, spec 05.7.1) with reasons, the
  reference drift result, and schema errors (spec 07.1.3).

```
python3 tools/build-results.py --campaign results/2026Q4
```

Checks enforced (spec 05.7):
- V1 error rate, V3 CoV (5% limit), V9 comparability category.
- V8 reference drift (5% limit): above the limit, every row of the
  campaign is QUALIFIED with the reason REFERENCE-DRIFT.
- Schema checks on every record and aggregate; with the `jsonschema`
  package installed, the generated results.json is validated against
  `tools/schemas/results.schema.json`.

```
python3 tools/build-results.py selftest
```

## schemas/

JSON Schema (draft 2020-12) for the four data contracts:

| File | Describes |
|---|---|
| `manifest.schema.json` | Run manifests (`runs/*.yaml`) |
| `run-record.schema.json` | One line of `runs.jsonl` |
| `results.schema.json` | `aggregate/results.json` |
| `validation.schema.json` | `aggregate/validation.json` |

The runner and the assembler run built-in checks without
dependencies and use `jsonschema` when it is installed.
