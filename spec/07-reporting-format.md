# 07 — Reporting Format

## 1. Result store layout

```
results/
  <campaign-id>/
    runs/
      <run-id>/
        meta.json          # environment record (below)
        server.log
        harness-A/         # raw harness output (csv/json)
        harness-B/         # raw harness output (csv/json)
        power.csv          # 1 Hz power trace
    results.csv            # all rows, one per test point + harness
    headlines.md           # comparison tables (below)
    report.md              # final narrative report
```

Run ID format: `SUTx-stack-vvv-mmmm-TPx-PFx-Px-Wx-Cxx-rN`
Example: `SUT3-vllm-0.32-gpt-oss-120b-TP8-PF1-PU-WON-C128-r2`

## 2. results.csv columns

One row per (test point, harness, repetition).

| Column | Example | Source |
|---|---|---|
| run_id | SUT1-vllm-... | Naming rule above |
| campaign_id | 2026Q4 | Campaign tag |
| sut | SUT-1 | spec 01 |
| stack | vllm | spec 02 |
| stack_version | 0.32.0 (digest ...) | Environment record |
| harness | guidellm / genai-perf | spec 03 |
| model | gpt-oss-120b | spec 04 |
| model_revision | hf commit SHA | Environment record |
| precision | MXFP4 | spec 04 |
| lane | BASE / TUNED | spec 02.4.1 |
| tp | 8 | Test point |
| payload_profile | PF-1 ... PF-5 | spec 04 |
| prompt_scenario | PU / PS | spec 04 |
| regime | WON / WOFF | spec 04 |
| traffic_mode | closed / open | spec 03.3 |
| concurrency | 128 | Test point |
| repetition | 1..3 | spec 05.3 |
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
| cov_pct | 1.8 | V3 |

## 3. Headlines tables

Build one table per model and per regime.
Rows: SUT x stack, at the highest measured concurrency.
Columns: the headline metrics (spec 06.9): M5, M8, M24, M1 to M4
(p90), M15, M18.
Sort: by M5 (out_tps) descending.
Flags print inline (for example: "678 (PILOT)").

Rules:
1. Never mix CFG-BASE and CFG-TUNED rows in one table (spec 02.4.1).
2. Never mix closed-loop and OPEN rows in one table (spec 03.3).
3. The SLO view (M10, M11) is secondary (spec 06.4). It does not
   select the headline row.

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

1. Never average across harnesses. Report each harness row.
2. Never mix prompt scenarios in one table.
3. Never mix precisions in one table.
4. A missing cell is "N/A" with the reason in a footnote.
5. Every table states the SLO in use and the model revision.
6. Archive raw data for 3 years.
