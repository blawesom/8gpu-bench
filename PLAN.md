# PLAN — 8-GPU Box Inference Benchmark

## 1. Purpose

This project defines a test specification for 8-GPU server systems.
The specification measures AI inference performance only.
It does not measure model quality or accuracy.

The goal is a fair and comparable test result for every system.
A buyer can use the result to compare systems.

## 2. Scope

- Systems: 8-GPU servers from NVIDIA, AMD, and Intel.
- Workloads: LLM inference, offline and online traffic.
- Software: inference stacks only. See spec 02.
- Excluded: model quality tests, fine-tuning, training.

## 3. Research findings (Nov 2026)

### 3.1 Top inference stacks by GitHub stars

| Rank | Repository | Stars (Nov 2026) | Status | Test role |
|---|---|---|---|---|
| 1 | ollama/ollama | 186 010 | Active | Extended set only |
| 2 | ggml-org/llama.cpp | 133 461 | Active | Extended set only |
| 3 | vllm-project/vllm | 96 400 | Active | Core set |
| 4 | sgl-project/sglang | 38 500 | Active | Core set |
| 5 | mlc-ai/mlc-llm | 23 307 | Active | Not in scope |
| 6 | NVIDIA/TensorRT-LLM | 15 021 | Active | Core set, NVIDIA only |
| 7 | triton-inference-server/server | 11 128 | Active | Not in scope (layer, not engine) |
| 8 | huggingface/text-generation-inference | 10 882 | Archived Dec 2025 | Excluded |
| 9 | ai-dynamo/dynamo | 8 447 | Active | Not in scope v1 |
| 10 | InternLM/lmdeploy | 8 134 | Active | Extended set |

### 3.2 Top benchmarking harnesses (technical)

| Rank | Repository | Stars | Role in this project |
|---|---|---|---|
| 1 | modelscope/evalscope | 3 655 | Not selected |
| 2 | vllm-project/guidellm | 1 752 | Harness A (primary) |
| 3 | mlcommons/inference (MLPerf) | 1 657 | Reference only (fixed model list, heavy setup) |
| 4 | ray-project/llmperf | 1 127 | Excluded (archived) |
| 5 | huggingface/optimum-benchmark | 341 | Not selected |
| 6 | huggingface/inference-benchmarker | 176 | Not selected |
| 7 | NVIDIA genai-perf (perf_analyzer) | 164 | Harness B (primary) |

Key decision: both primary harnesses are HTTP clients.
They measure any OpenAI-compatible endpoint the same way.
Native stack benchmarks (vLLM, SGLang, llama.cpp) are cross-checks only.

### 3.3 Model landscape (Nov 2026)

Recent open models carry hardware-level optimizations:
MLA, DSA sparse attention, SWA/hybrid attention, MTP speculative decoding,
and native FP8/FP4 quantization.

| Model | Size | Key optimizations | Stack support |
|---|---|---|---|
| DeepSeek-V4.1-Flash | 552B MoE, 8B/16B active | Compressed attention + SWA, 890 B KV/token, MTP (DSpark) | SGLang day-0 (Blackwell, H200, MI350X); vLLM DeepSeek-V4 path |
| GLM-5.3-Flash | 321B MoE | DSA, NVFP4/FP8/BF16 | SGLang/vLLM; NVIDIA NIM profiles TP2-TP8 |
| Kimi K2.6 / K3.1 Pro | 1T / 2.8T MoE | MLA, DSA, NVFP4/MXFP4 | SGLang/vLLM day-0; K3.1 too large for one node |
| GPT-OSS-120B | ~117B MoE | MXFP4 native | All stacks; MLPerf Inference v6.0 workload |
| DeepSeek-V3.2 | 671B MoE | DSA, MLA, FP8; AMD MXFP4 build (amd/DeepSeek-V3.2-mxfp4) | SGLang/vLLM on NVIDIA and MI350/MI355 |

Spec 04 selects: Tier 1 optimized dense (Qwen3.8-27B,
hybrid attention, 48 of 64 layers linear, MTP head),
Tier 2 headline MoE (DeepSeek-V4.1-Flash, GLM-5.3-Flash fallback,
GPT-OSS-120B cross-vendor denominator), Tier 3 frontier (Kimi K3.1 Pro,
2.8T MXFP4, single node on B300/MI355X/MI450 class only).
FP8 is the common lane. Native FP4 is a separate hardware-best lane.

## 4. Document map

| File | Content |
|---|---|
| spec/00-scope-and-definitions.md | Scope, definitions, rules |
| spec/01-systems-under-test.md | Hardware matrix, entry criteria |
| spec/02-inference-stacks.md | Stack set, version pinning |
| spec/03-benchmark-harnesses.md | Dual-harness method |
| spec/04-workloads.md | Models, token lengths, traffic |
| spec/05-test-protocol.md | Run procedure, comparability rules |
| spec/06-metrics.md | Metric definitions, units, SLOs |
| spec/07-reporting-format.md | Result tables, environment records |

All documents use the ASD-STE100 standard (Simplified Technical English).

## 5. Phases

| Phase | Name | Exit criteria |
|---|---|---|
| 0 | Research | Repo tables verified. Done (Nov 2026). |
| 1 | Specification | This document set is complete and approved. |
| 2 | Pilot | One system per vendor runs all core tests end-to-end. The corpus generator emits all profile corpora with checksums. The Ansible bring-up passes `verify.yml` on every SUT. |
| 2a | Hardware pilot | Intel D270 and AMD MI450 support verified. Fail = mark system pilot-phase in results. |
| 3 | Baseline run | H200 + RTX PRO 6000 + MI300X complete all tests. |
| 4 | Full campaign | All systems, all core stacks. |
| 5 | Reporting | Final report per spec 07. |

## 6. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Intel D270 in customer sampling | No stable drivers | Mark pilot-phase. Report with flag. |
| AMD MI450 early availability | Limited units | Use MI350/MI355X as AMD current-gen reference. |
| Stack version drift between vendors | Different software per vendor | Pin versions per vendor at pilot. Record digest. |
| TensorRT-LLM on non-NVIDIA | Not supported | NVIDIA-only lane. State this in every result row. |
| Power data not available on D270 | No tokens/J for Intel | Report power where the tooling allows. Flag missing data. |
| Vendor-tuned defaults differ | Unequal starting point | spec 05 fixes one baseline config per stack. |

## 7. Open items (decide before Phase 3)

1. ~~Tier 1 model~~ Decided: Qwen3.8-27B (fallback Qwen3-8B on stacks
   without Qwen3.8 support).
2. ~~Tier 2 headline MoE~~ Decided: DeepSeek-V4.1-Flash (fallback
   GLM-5.3-Flash). Kimi K3.1 Pro set as Tier 3 frontier lane.
3. Whether the GPT-OSS-120B denominator model runs as a full lane
   or as the Phase 3 MLPerf anchor only.
4. SLO values for goodput (defaults in spec 06).
5. System list per Phase 4 (availability, cost).
6. Whether LMDeploy and llama.cpp enter the core set after the pilot.
7. PL-4 lane scope: which SUTs run native FP4 (see spec 04.4).
8. ~~Payload profile registry~~ Done: PF-1 to PF-5 in spec 04.
   Decide the PF-3 data source (LMSYS-Chat-1M default, ShareGPT
   fallback) and confirm the corpus file schema of both harnesses
   at the version pin.
9. Verify Qwen3.8-27B on AMD (ROCm) during the pilot. The upstream recipe
   does not list a tested AMD path for the 27B.
10. Verify Kimi K3.1 Pro concurrency ceiling per SUT in the pilot
    (published recipes use about 48).
11. Production lanes (PF-7 to PF-9, from observed traffic):
    ~~confirm the DeepSeek-V4.1-Flash context window~~ Confirmed:
    1M (max output 384k). Context cap set to 256k (spec 04.2.1) so
    every selected model runs every production lane natively.
    Remaining: select the public text source for the long-prompt
    corpora (rule 2.2.12).
12. Infra pins at Phase 2 (infra/ansible): exact driver versions
    per vendor, jointly with the container digests (the driver must
    match what the pinned images expect). Verify the AMD GPU count
    assertion against real rocm-smi JSON output. Verify the D270
    xpu-smi output format.
