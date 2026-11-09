# 07 — Reporting Format

## 1. Result store layout

One campaign is one directory.
The runner writes the per-run artifacts.
The assembler writes the aggregate files.
JSON is the canonical format.
CSV and Markdown are generated from the JSON.

```
results/<campaign-id>/
  <run-id-prefix>/                          # one per (SUT, stack, version, model, TP)
    <profile>-<scenario>-<regime>[-OPEN]-r<rep>/
      meta.json                             # environment record (section 4)
      server-<n>.log                        # one per container start
      power-<harness>-C<c>.csv              # telemetry rows of one measured pass
      guidellm/C<c>/                        # raw primary harness output
      crosscheck-genai-perf/C<c>/           # cross-check output (optional)
  runs.jsonl                                # one record per (test point, harness, value, repetition)
  aggregate/
    results.json                            # canonical: records + aggregates + versions
    results.csv                             # generated from results.json
    headlines.md                            # generated from results.json
    validation.json                         # validity levels, V8 drift (section 1.3)
  report.md                                 # final narrative report
```

Rules:
1. `results.json` is the canonical result format.
   The assembler generates `results.csv` and `headlines.md` from it.
   Do not edit the generated files by hand.
2. Keep every individual repetition in the result store.
   Do not aggregate or delete raw records.
3. The `<run-id-prefix>` directory is the per-system, per-stack
   directory. The prefix encodes SUT, stack, version, model, and TP.
4. Multiple run manifests (SUTs, stacks, lanes) append to the same
   `runs.jsonl` of one campaign.
5. The campaign directory does not live in the repository. In
   production it lives in a local staging directory on the load client
   (`BENCH_STAGING_DIR`, default `results`) and is checkpoint-synced
   to the confidential S3 store (`BENCH_S3_URI`) through
   `tools/s3sync.sh`. See section 7.

Run ID format: `SUTx-stack-vvv-mmmm-TPx-PFx-Px-Wx-Cxx-rN`
Example: `SUT3-vllm-0.32-gpt-oss-120b-TP8-PF1-PU-WON-C128-r2`

### 1.1 Result versions

Every result row and every aggregate file records the benchmark
versions (spec 00.3.5):

| Field | Source |
|---|---|
| benchmark_spec | VERSION file, key `benchmark_spec`. |
| runner_version | VERSION file, key `runner_version`. |
| assembler_version | VERSION file, key `assembler_version`. |
| corpus_version | VERSION file, key `corpus_version`. |
| result_schema_version | VERSION file, key `result_schema_version`. |

The runner writes the block to `meta.json` (section 4).
The assembler writes it to `results.json` and `validation.json`.
Two rows with different `benchmark_spec` values are reported but not
ranked against each other (spec 00.3.5).

### 1.2 results.json structure

```
{
  "schema_version": "<result_schema_version>",
  "campaign_id": "<campaign-id>",
  "generated_at": "<UTC timestamp>",
  "generator": { "assembler_version": "...", "benchmark_spec": "..." },
  "versions": { ...the five fields of 1.1... },
  "reference": { "count": 4, "throughputs_tps": [ ... ],
                 "drift_pct": 1.2, "limit_pct": 5.0, "pass": true },
  "records": [ <every runs.jsonl record, unchanged> ],
  "aggregates": [
    { <identity fields of the test point>,
      "repetitions": 5,
      "out_tps": <median>, "out_tps_p10": ..., "out_tps_p90": ...,
      "cov_pct": ..., "ci95_low": ..., "ci95_high": ...,
      "validity": "VALID | QUALIFIED | INVALID",
      "reasons": [ ... ] }
  ]
}
```

The `aggregates` list holds one entry per test point
(SUT, stack, harness, model, precision, lane, profile, scenario,
regime, traffic mode, concurrency), computed from the raw records.
The confidence interval is a 95% percentile bootstrap over the
repetition values with 2 000 resamples, seeded from the campaign ID
and the test point key so a re-assembly gives the same interval.

### 1.3 validation.json structure

```
{
  "schema_version": "<result_schema_version>",
  "campaign_id": "<campaign-id>",
  "generated_at": "<UTC timestamp>",
  "versions": { ...the five fields of 1.1... },
  "reference": { "count": 4, "drift_pct": 1.2, "limit_pct": 5.0,
                 "pass": true },
  "runs": [
    { "run_id": "...", "validity": "VALID | QUALIFIED | INVALID | null",
      "reasons": [ ... ] }
  ],
  "test_points": [
    { "key": "<group key string>", "validity": "...",
      "repetitions": 5, "reasons": [ ... ] }
  ],
  "summary": { "valid": 0, "qualified": 0, "invalid": 0,
               "crosscheck": 0, "reference": 0 }
}
```

`runs` holds the per-run validity (V1, V5, V6, V7 and failure flags).
`test_points` holds the per-test-point validity (V3, V9, and the V8
campaign drift). The levels are defined in spec 05.7.1.
Cross-check rows (CROSSCHECK) and reference rows (REFERENCE) carry
`null` validity.

## 2. results.csv columns

One row per (test point, harness, repetition).
The file is generated from `results.json` (section 1.2).
The per-row statistics (p10, p90, confidence interval) live in the
`aggregates` of `results.json`, not here.

| Column | Example | Source |
|---|---|---|
| run_id | SUT1-vllm-... | Naming rule above |
| campaign_id | 2026Q4 | Campaign tag |
| sut | SUT-1 | spec 01 |
| stack | vllm | spec 02 |
| stack_version | 0.32.0 (digest ...) | Environment record |
| harness | guidellm | Primary harness (spec 03). Cross-check tools record their own name, flagged CROSSCHECK. |
| model | gpt-oss-120b | spec 04 |
| model_revision | hf commit SHA | Environment record |
| precision | MXFP4 | spec 04 |
| comparability | STRICT / ARCH / HWBEST | Manifest field, checked per spec 04.1.6 |
| lane | BASE / TUNED | spec 02.4.1 |
| tp | 8 | Test point |
| payload_profile | PF-1 ... PF-5 | spec 04 |
| prompt_scenario | PU / PS | spec 04 |
| regime | WON / WOFF | spec 04 |
| traffic_mode | closed / open | spec 03.3 |
| concurrency | 128 | Test point |
| value_order | 3 | Position of this value in the randomized sweep order (spec 05.2.1). |
| repetition | 1..5 or 1..7 | spec 05.3, 05.4 |
| ttft_ms_p90 | 412 | M1 |
| tpot_ms_p90 | 28.4 | M2 |
| itl_ms_p99 | 41.0 | M3 |
| e2e_ms_p90 | 38 120 | M4 |
| out_tps | 5 421 | M5 |
| out_tps_per_gpu | 678 | M8 |
| goodput_tps | 4 980 | M10 (secondary) |
| slo_concurrency | 128 | M11 (secondary) |
| hbm_peak_gb | 241 | M12 |
| gpu_util_pct | 99.2 | M14 |
| temp_max_c | 63 | M15 |
| power_mean_w | 7 410 | M16 |
| j_per_1m_out | 682 000 | M18 |
| success_rate_pct | 100.0 | M24 |
| flags | PILOT, DEV, SPEC, TUNED, OPEN, INVALID | spec 01/02/03/05 |
| deviation_note | Text, or blank | spec 05.5 |
| error_rate_pct | 0.0 | V1 |
| validity | VALID / QUALIFIED / INVALID / blank | spec 05.7.1 (per-run level) |

## 3. Headlines tables

Build one table per model, precision, scenario, regime, payload
profile, config lane, traffic mode, and comparability category.
Rows: SUT x stack, at the highest measured concurrency.
Columns: the headline metrics (spec 06.9): M5 (with its 95%
confidence interval), M8, M24, M1 to M4 (p90), M15, M18.
Sort: by M5 (out_tps) descending.
Flags print inline (for example: "678 (PILOT)").
The M5 cell prints the median with the interval: "5 420 (5 310 to 5 510)".

Rules:
1. Never mix CFG-BASE and CFG-TUNED rows in one table (spec 02.4.1).
2. Never mix closed-loop and OPEN rows in one table (spec 03.3).
3. The SLO view (M10, M11) is secondary (spec 06.4). It does not
   select the headline row.
4. Never mix comparability categories in one table (spec 04.1.6).
   The table heading states the category.
5. Cross-check rows (CROSSCHECK) and reference rows (REFERENCE)
   never enter a headline table.
6. An INVALID row is marked inline. A QUALIFIED row prints its
   reasons in a footnote (spec 05.7.1).

Example shape (values are illustrative):

| SUT | Stack | out_tps | out_tps/gpu | success | TTFT p90 | TPOT p90 | E2E p90 | J/1M out |
|---|---|---|---|---|---|---|---|---|
| SUT-1 | vllm | 12 800 | 1 600 | 100.0% | 980 ms | 49 ms | 51 200 ms | 610 000 |
| SUT-4 | vllm | 10 100 | 1 263 | 99.8% | 1 050 ms | 47 ms | 49 400 ms | 730 000 |

## 4. Environment record (meta.json)

Record all of these per run:

- OS image name and hash
- CPU model and count, system RAM
- GPU model, count, firmware, driver version
- GPU power limit in effect (vendor default unless recorded otherwise)
- Runtime version (CUDA / ROCm / oneAPI)
- Container digest or package version per stack
- Harness versions
- Model path, revision, checksum
- Server settings (full CLI or config file, verbatim)
- Power profile / power limit value
- BIOS and firmware versions
- Room temperature band
- Client machine identity and network path
- Campaign and run identifiers
- Benchmark versions (section 1.1)
- Comparability category of the run (spec 04.1.6)
- Order seed and the actual value order of the sweep (spec 05.2.1)
- Thermal soak settings actually applied (spec 05.3 step 6)
- GPU temperature at the start and end of each measured pass
  (from the telemetry, when available)

## 5. Final report (report.md)

Structure:

1. Campaign summary (dates, systems, stacks, versions, SLO).
2. Headline tables (spec 07.3).
3. Per-SUT notes: deviations, pilot flags, failures.
4. Scaling analysis (M9 across SUTs).
5. Efficiency analysis (M18, M19, M21).
6. Known limitations:
   - Pilot-phase hardware results are not comparable to GA results.
   - DEV rows are comparable only within the same deviation.
   - PCIe-fabric systems (SUT-3) differ in communication cost.
   - Price data (M20) is stale at read time.
7. Reproduction: pointer to run IDs and raw data paths.

## 6. Reporting rules

1. Never put cross-check results in a headline table. Report them
   separately, flagged CROSSCHECK (spec 03.1).
2. Never mix prompt scenarios in one table.
3. Never mix precisions in one table.
4. Never mix comparability categories in one table (spec 04.1.6).
5. A missing cell is "N/A" with the reason in a footnote.
6. Every table states the SLO in use and the model revision.
7. Archive raw data for 3 years.
8. `results.json` is canonical. Generate the CSV and Markdown from it
   with the assembler. Do not keep hand-edited copies.
9. State the benchmark versions (section 1.1) in every report.
10. Cite `validation.json` when a table contains QUALIFIED or
    INVALID rows.

## 7. Repository hygiene and the confidential results store

The repository holds the setup only. Run results are confidential and
never enter the repository.

1. The repository may contain: this document set, `tools/`,
   `tools/schemas/`, `infra/`, the CI, the `VERSION` file, the
   example manifests, and the corpus seeds with their checksums and
   attribution.
2. Never commit: `runs.jsonl` or any run record, `meta.json`,
   `node-meta.json`, telemetry CSVs, raw harness output, server logs,
   real manifests with hostnames, the store URI, s3cmd credentials,
   or private addresses. The CI job `hygiene` fails on access-key
   patterns, private-key blocks, private IPv4 ranges, real ssh
   targets, and s3cmd credential lines. Use documentation ranges
   (192.0.2.0/24) in committed examples.
3. Committed example manifests use environment placeholders, for
   example `docker_host: ssh://bench@${SUT1_HOST}`. The runner expands
   them from the environment and aborts when a variable is unset.
   Real values go into the environment or into `runs/local/`
   (gitignored).
4. The results store is a private S3-compatible bucket. The load
   client reaches it with s3cmd. The Ansible `s3-store` role configures
   s3cmd: it prompts for the access key and the secret key at setup
   and writes `~/.s3cmd` with mode 0600. The keys never enter the
   repository, the logs, or any committed file.
5. The role also writes `~/.bench/bench.env` (mode 0600) on the
   client. Source it before the tools:
   `BENCH_S3_URI` (store prefix), `BENCH_STAGING_DIR` (local staging),
   `BENCH_CORPORA_DIR` (generated corpora).
6. `tools/s3sync.sh` syncs one local directory into the store. It
   never deletes remote objects; deletion belongs to the bucket
   lifecycle policy. The runner calls it after every test-point
   repetition and at exit. The assembler calls it once on the whole
   campaign directory. A crash loses at most one test point of
   unsynced data; the staging copy stays complete.
7. Without `BENCH_S3_URI` the tools run local-only (development mode).
   The runner prints a note and skips every sync.
8. Access to the bucket is scoped: the IAM policy of the setup key
   pair covers only the campaign prefix, TLS is required, and the
   provider access log records every read. Rotate a key pair by
   creating a new one, re-running the `s3-store` role, and
   deactivating the old pair.
9. The 3-year archive rule (section 6, rule 7) applies to the store:
   Object Lock or versioning plus a lifecycle transition. The archive
   never lives in the repository.
