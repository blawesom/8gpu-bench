# 03 — Benchmark Harnesses

## 1. Method

Use one primary harness for every measured test point.
The harness is a fixed instrument, like the client machine and the
network path. Every stack, SUT and configuration lane is measured
with the same tool, so the measurement never shifts the comparison.
The harness is an HTTP client. It talks to the OpenAI-compatible
endpoint of the SUT.

| Role | Harness | Source | Why selected |
|---|---|---|---|
| Primary | GuideLLM | vllm-project/guidellm | Custom dataset input, tokenizer control, fixed seed, concurrent and Poisson profiles. Stack-agnostic, pure HTTP client. |

Cross-check tier (not a primary result):

| Tool | Where it lives | Use |
|---|---|---|
| GenAI-Perf | triton-inference-server (perf_analyzer) | Cross-check the primary result on selected points. Mature TTFT/ITL/throughput metrics. |
| vLLM `benchmark_serving` | In the vLLM repo | Sanity-check the vLLM row. |
| SGLang `sgl-bench` | In the SGLang repo | Sanity-check the SGLang row. |
| `llama-bench` | In the llama.cpp repo | Sanity-check the llama.cpp row. Note: micro-benchmark, excludes tokenization. |
| TensorRT-LLM `Benchmark` | In the TRT-LLM repo | Sanity-check the TensorRT-LLM row. |

Rules for cross-checks:
1. Run a cross-check only after the primary result is recorded.
2. Never compare a cross-check result against a primary result in
   the headline table.
3. If a cross-check deviates by more than 15% from the primary,
   re-run the primary once.
4. Cross-check a few points per SUT and stack, not every test point.

## 2. Client placement

1. Put the load client on a dedicated machine.
2. Do not run the harness on the SUT.
3. Use a 25 GbE or faster path between the client and the SUT.
4. No other traffic shares this path during a measured run.
5. Sync the client clock with the same NTP source as the SUT.

## 3. Harness configuration

Fix these settings for the primary harness:

| Setting | Value |
|---|---|
| Requests per run | 1 000 per concurrency point. Production lanes: PF-7 200, PF-8 100, PF-9 30 (spec 04). |
| Payload source | The profile corpus file (spec 04.2). The harness does not generate prompts. |
| Request distribution | Closed-loop: constant concurrency. Open-loop: the OPEN lane below. |
| Concurrency values | 1, 2, 4, 8, 16, 32, 64, 128, 256. |
| Warmup before measure | 100 requests or 60 s, whichever is longer. |
| Warmup corpus | A separate warmup file, disjoint from the measured rows (spec 04.5.1). |
| Warmup output length | Capped at 64 tokens by default (manifest `warmup_max_tokens`). |
| Timeout per request | 300 s. Production lanes PF-8 and PF-9 use 3 600 s. |
| Retry on network error | None. Drop the request, count it as a failure. |
| Random seed | Fixed per campaign, recorded. |

The warmup warms the prefix cache and the engine.
It must not reuse the measured prompts, or a cache state from the
warmup changes the measured result.
The warmup output cap does not change the measured output length:
the cache warm comes from the prefills.

Open-loop lane (OPEN):
1. The OPEN lane sends requests at a fixed rate with Poisson arrivals.
2. Run the OPEN lane on the Tier 2 headline test points (TP3, TP5).
3. The OPEN lane reuses the concurrency value list as request rates
   (1, 2, 4, ... 256 requests per second).
4. The primary harness drives the OPEN lane with its Poisson profile.
5. Mark every OPEN row OPEN. Closed-loop concurrency holds the queue
   inside the client, so latency under load reads low. The OPEN lane
   shows the queueing cost.
6. Never compare closed-loop rows with OPEN rows in one table.

GuideLLM specific:
- Profile: concurrent (constant concurrency, closed loop) or
  poisson (fixed rate, open loop).
- Report percentiles: p50, p90, p99.
- SLO for goodput: see spec 06.4.

## 4. Smoke test

Before a campaign, run the smoke test on one SUT:

1. Start the server with the Tier 1 model, TP=1.
2. Run 10 requests at concurrency 1 with the primary harness.
3. Check that TTFT, TPOT, and throughput are non-zero.
4. Check that the environment record script runs.
5. Save the output as the smoke-test record.

If any check fails, stop the campaign and fix the setup.

## 5. Harness version pinning

| Tool | Version / digest | Date |
|---|---|---|
| GuideLLM (primary) | _TBD at Phase 2_ | _TBD_ |
| GenAI-Perf (cross-check) | _TBD at Phase 2_ | _TBD_ |

Pilot pins are reused for all later phases.
Update a pin only with a recorded change.
