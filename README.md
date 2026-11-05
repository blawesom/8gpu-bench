# 8gpu-bench

A test specification for 8-GPU server systems.
The goal: measure AI inference performance, and compare systems fairly.

This repository holds the specification. It holds no results yet.
The project is in Phase 1 (specification). See [PLAN.md](PLAN.md) for
the phase list.

## What it measures

- Large language model (LLM) inference on one 8-GPU node.
- Latency, throughput, success rate, memory, and power.
- Inference performance only. Model quality is out of scope.

## Systems under test

| ID | System | Accelerators | Phase |
|---|---|---|---|
| SUT-1 | NVIDIA HGX H200 | 8x H200 (141 GB) | Core |
| SUT-2 | NVIDIA HGX B300 / GB300 | 8x Blackwell Ultra (288 GB) | Core |
| SUT-3 | NVIDIA RTX PRO 6000 Blackwell Server | 8x RTX PRO 6000 (96 GB) | Core |
| SUT-4 | AMD Instinct MI300X | 8x MI300X (192 GB) | Core |
| SUT-5 | AMD Instinct MI350/MI355X | 8x MI355X (288 GB) | Core |
| SUT-6 | AMD Instinct MI450 (Helios node) | 8x MI455X (432 GB) | Pilot |
| SUT-7 | Intel GPU D270 (Crescent Island) | 8x D270 (160 GB LPDDR5X) | Pilot |

## Inference stacks

- Core set: vLLM, SGLang. TensorRT-LLM on NVIDIA systems.
- Extended set: LMDeploy, llama.cpp, Ollama.
- Excluded: TGI (archived December 2025), llmperf (archived).

## Benchmark harnesses

Every stack serves an OpenAI-compatible endpoint. Two client harnesses
measure every test point:

| Role | Harness |
|---|---|
| Harness A | GuideLLM (vllm-project) |
| Harness B | GenAI-Perf (NVIDIA, pure HTTP client) |

Native stack benchmarks are cross-checks only.

## Models

| Tier | Model | Placement | Purpose |
|---|---|---|---|
| 1 | Qwen3.8-27B (dense, hybrid attention) | TP=1, one GPU | Per-GPU decode speed. |
| 2 | DeepSeek-V4.1-Flash (552B MoE, fallback GLM-5.3-Flash) | TP=8 | Headline node throughput. |
| 2 | GPT-OSS-120B (denominator) | TP=8 | Cross-vendor anchor, MLPerf v6.0 workload. |
| 3 | Kimi K3.1-Pro (2.8T MoE, optional) | TP=8, large-memory nodes | Frontier lane. |

FP8 (or BF16 for Tier 1) is the common comparable lane.
Native FP4 is a separate hardware-best lane.

Every stack runs two configuration lanes (spec 02.4.1): CFG-BASE, the
fixed baseline, and CFG-TUNED, the vendor best-known configuration.
The lanes are reported separately and never compared with each other.
Load runs closed-loop (constant concurrency) and, on the Tier 2
headline points, an open-loop lane (OPEN) with Poisson arrivals
(spec 03.3).

## Workload profiles

| ID | Type | ISL / OSL | Role |
|---|---|---|---|
| PF-1 | Fixed synthetic | 512 / 128 | Headline. |
| PF-2 | Fixed synthetic | 8,192 / 1,024 | Headline, long context. |
| PF-3 | Dataset (WildChat-1M) | Empirical | Real chat shape. |
| PF-4 | Distribution synthetic | 4,096 +/- 2,048 / 256 | Agentic / RAG shape. |
| PF-5 | Distribution synthetic | 1,024 / 4,096 | Decode-heavy shape. |
| PF-7 | Session model | 30,000 / 500 | Production p50. |
| PF-8 | Session model | 100,000 / 5,000 | Production p90. |
| PF-9 | Session model | 200,000 / 50,000 | Production p99. |

Production lanes run the P-C scenario: 10-turn sessions with a target
prefix-cache hit rate of 80%. ISL plus OSL stays at or below 256k
tokens, so every selected model runs every lane natively.

## Document map

| File | Content |
|---|---|
| [PLAN.md](PLAN.md) | Phases, research summary, risks, open items. |
| [infra/ansible/README.md](infra/ansible/README.md) | Node environment bring-up (Ansible). |
| [tools/README.md](tools/README.md) | Corpus generator, test-point runner, results assembler. |
| [spec/00-scope-and-definitions.md](spec/00-scope-and-definitions.md) | Scope, terms, rules. |
| [spec/01-systems-under-test.md](spec/01-systems-under-test.md) | Hardware matrix, entry criteria. |
| [spec/02-inference-stacks.md](spec/02-inference-stacks.md) | Stack set, version pinning, baseline config. |
| [spec/03-benchmark-harnesses.md](spec/03-benchmark-harnesses.md) | Dual-harness method, client setup. |
| [spec/04-workloads.md](spec/04-workloads.md) | Models, payload profiles, scenarios, test matrix. |
| [spec/05-test-protocol.md](spec/05-test-protocol.md) | Run procedure, comparability, validity checks. |
| [spec/06-metrics.md](spec/06-metrics.md) | Metrics M1 to M24. |
| [spec/07-reporting-format.md](spec/07-reporting-format.md) | Result store, tables, environment records. |

## Status and next steps

- Phase 0 (research): done.
- Phase 1 (specification): this document set.
- Phase 2 (pilot): in progress. The corpus generator, the test-point
  runner and the results assembler are in `tools/`. Example run
  manifests are in `runs/`. Next: pin versions, verify the harness
  dataset schemas, run one system per vendor end to end.

Open decisions are listed in [PLAN.md](PLAN.md), section 7.

## Writing standard

All documents use the ASD-STE100 standard (Simplified Technical
English): approved words, short sentences, active voice, one
instruction per sentence.

## License

Apache-2.0. See [LICENSE](LICENSE).
Corpus files carry ODC-BY attribution for their sources (spec 04.2.2).
