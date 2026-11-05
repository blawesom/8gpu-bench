# 03 — Benchmark Harnesses

## 1. Method

Use two harnesses for every measured test point.
Both harnesses are HTTP clients.
Both talk to the OpenAI-compatible endpoint of the SUT.
A test point is complete when both harnesses finish it.

| Role | Harness | Source | Why selected |
|---|---|---|---|
| Harness A | GuideLLM | vllm-project/guidellm | SLO-aware. Sweep and Poisson profiles. Reports goodput. Stack-agnostic. |
| Harness B | GenAI-Perf | triton-inference-server (perf_analyzer) | Mature TTFT/ITL/throughput metrics. Pure client, so vendor-neutral in practice. |

Cross-check tier (not a primary result):

| Tool | Where it lives | Use |
|---|---|---|
| vLLM `benchmark_serving` | In the vLLM repo | Sanity-check the vLLM row. |
| SGLang `sgl-bench` | In the SGLang repo | Sanity-check the SGLang row. |
| `llama-bench` | In the llama.cpp repo | Sanity-check the llama.cpp row. Note: micro-benchmark, excludes tokenization. |
| TensorRT-LLM `Benchmark` | In the TRT-LLM repo | Sanity-check the TRT-LLM row. |

Rules for cross-checks:
1. Run a cross-check only after the primary result is recorded.
2. Never compare a cross-check result against a primary result in the headline table.
3. If a cross-check deviates by more than 15% from the primary, re-run the primary once.

## 2. Client placement

1. Put the load client on a dedicated machine.
2. Do not run the harness on the SUT.
3. Use a 25 GbE or faster path between the client and the SUT.
4. No other traffic shares this path during a measured run.
5. Sync the client clock with the same NTP source as the SUT.

## 3. Harness configuration

Fix these settings for both harnesses:

| Setting | Value |
|---|---|
| Requests per run | 1 000 per concurrency point. Production lanes: PF-7 200, PF-8 100, PF-9 30 (spec 04). |
| Payload source | The profile corpus file (spec 04.2). The harness does not generate prompts. |
| Request distribution | Constant concurrency (open-loop optional lane, marked OPEN). |
| Concurrency values | 1, 2, 4, 8, 16, 32, 64, 128, 256. |
| Warmup before measure | 100 requests or 60 s, whichever is longer. |
| Timeout per request | 300 s. Production lanes PF-8 and PF-9 use 3 600 s. |
| Retry on network error | None. Drop the request, count it as a failure. |
| Random seed | Fixed per campaign, recorded. |

GuideLLM specific:
- Profile: sweep for the throughput point, constant concurrency for the latency points.
- SLO for goodput: see spec 06.4.

GenAI-Perf specific:
- Mode: profile at each concurrency value.
- Streaming: on.
- Report percentiles: p50, p90, p99.

## 4. Smoke test

Before a campaign, run the smoke test on one SUT:

1. Start the server with the Tier 1 model, TP=1.
2. Run 10 requests at concurrency 1 with Harness A.
3. Run 10 requests at concurrency 1 with Harness B.
4. Check that TTFT, TPOT, and throughput are non-zero.
5. Check that the environment record script runs.
6. Save the output as the smoke-test record.

If any check fails, stop the campaign and fix the setup.

## 5. Harness version pinning

| Harness | Version / digest | Date |
|---|---|---|
| GuideLLM | _TBD at Phase 2_ | _TBD_ |
| GenAI-Perf | _TBD at Phase 2_ | _TBD_ |

Pilot pins are reused for all later phases.
Update a pin only with a recorded change.
