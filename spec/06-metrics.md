# 06 — Metrics

## 1. Latency metrics (online regime)

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M1 | TTFT | ms | Time from client send to first output token. |
| M2 | TPOT | ms | (Total request time minus TTFT) divided by (output tokens minus 1). |
| M3 | ITL | ms | Time between two consecutive output tokens. |
| M4 | E2E | ms | Time from client send to final output token. |

Report M1 to M4 as: mean, p50, p90, p99.
A request meets the SLO (spec 06.4) if both M1 and M2 meet their limits.

## 2. Throughput metrics

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M5 | Output throughput | tokens/s | Total output tokens of the run divided by run time. |
| M6 | Total throughput | tokens/s | (Input tokens + output tokens) of the run divided by run time. |
| M7 | Per-request rate | req/s | Completed requests divided by run time. |
| M8 | Per-GPU output throughput | tokens/s/GPU | M5 divided by the number of active GPUs. |
| M24 | Request success rate | % | Requests completed without error or timeout, divided by total requests. Equals 100 minus the V1 error rate (spec 05.7). |

M5 and M8 are the headline throughput metrics.
Report M5 and M8 for every test point.
Report M6 and M7 for the W-OFF test points.

## 3. Scaling metrics

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M9 | Scaling efficiency | % | (Per-GPU throughput at TP=8) divided by (per-GPU throughput at TP=1), same stack and model. |

Compute M9 for Tier 2 models where both runs exist.
M9 shows the cost of splitting the model across GPUs.

## 4. SLO and goodput (secondary)

The campaign headline is the delivery view: success rate (M24),
latency (M1 to M4) and throughput (M5 to M8). The SLO view below is
secondary. It condenses the delivery metrics into one pass/fail
statement for readers who need it.

SLO (reference values, November 2026):

| Limit | Value |
|---|---|
| TTFT (M1) p90 | Below 1 000 ms |
| TPOT (M2) p90 | Below 50 ms |

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M10 | Goodput | tokens/s | Output throughput at the highest concurrency where the SLO holds. |
| M11 | SLO concurrency | requests | The concurrency value where the SLO last held. |

Report M10 and M11 only where they are computed (Harness A).
They are secondary. Never use them as the only comparison basis.

## 5. Resource metrics

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M12 | HBM usage peak | GB | Max GPU memory used during the run. |
| M13 | HBM usage mean | GB | Mean over the measured run. |
| M14 | GPU utilization | % | Mean compute utilization over the measured run. |
| M15 | GPU temperature max | C | Peak over the run. |

## 6. Power metrics

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M16 | System power mean | W | Mean over the measured run. |
| M17 | System power peak | W | Peak over the measured run. |
| M18 | Energy per 1M output tokens | J | (M16 x run time x 1 000 000) divided by total output tokens. |
| M19 | Output tokens per joule | tokens/J | 1 000 000 divided by M18. |

Power rules:
1. Sample at 1 Hz or faster over the whole measured run.
2. Use per-GPU package power where the tool gives it.
   Otherwise use the power-meter value at the system inlet.
3. Record which source (GPU sensors or meter) in the environment record.
4. If no power data is available (for example: D270 early tooling),
   report M16 to M19 as N/A with the reason.

## 7. Cache metrics

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M22 | Prefix cache hit rate | % | Token-level cache hits divided by input tokens, from server metrics. |
| M23 | Session capacity | sessions | The highest concurrency where the PF-9 lane runs the full request count without an out-of-memory error. |

M22 records the real cache behavior under the P-C scenario.
M23 shows the long-context capacity of the SUT. KV cache size, not
compute, sets this limit. Report M23 for every SUT that runs PF-9.

## 8. Cost metric (informational)

| ID | Metric | Unit | Definition |
|---|---|---|---|
| M20 | List price | USD | System list price at the campaign start. |
| M21 | Output throughput per 1 000 USD | (tokens/s)/1 000 USD | M8 divided by (M20 / 1 000). |

M20 and M21 are informational only.
Price changes. Do not rank systems on M21 alone.

## 9. Headline table metrics

The headline comparison table (spec 07.3) carries, at the highest
measured concurrency:
M5, M8, M24, M1, M2, M3, M4 (p90), M15, M18.
The production-lane table carries: M5, M8, M24, M1, M2, M22, M23.
M10 and M11 (spec 06.4) stay in the full result file only.
All other metrics live in the full result file.
