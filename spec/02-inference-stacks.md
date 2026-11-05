# 02 — Inference Stacks

## 1. Stack set

### 1.1 Core set (mandatory for every SUT where supported)

| Stack | Repo | Supported on | Notes |
|---|---|---|---|
| vLLM | vllm-project/vllm | SUT-1 to SUT-7 (Intel: to verify in pilot) | Default serving engine. |
| SGLang | sgl-project/sglang | SUT-1 to SUT-7 (Intel: to verify in pilot) | AMD: ROCm + AITER kernels. |
| TensorRT-LLM | NVIDIA/TensorRT-LLM | SUT-1, SUT-2, SUT-3 only | Peak-performance lane for NVIDIA. |

### 1.2 Extended set (run if time allows)

| Stack | Repo | Supported on | Notes |
|---|---|---|---|
| LMDeploy | InternLM/lmdeploy | SUT-1 to SUT-5 | TurboMind engine. |
| llama.cpp | ggml-org/llama.cpp | SUT-1 to SUT-3, SUT-7 (SYCL, to verify) | GGUF/quantized lane. |
| Ollama | ollama/ollama | SUT-1 to SUT-3 | llama.cpp wrapper. |

### 1.3 Excluded

| Stack | Reason |
|---|---|
| TGI (huggingface/text-generation-inference) | Archived Dec 2025. Maintenance mode only. |
| llmperf (ray-project) | Archived. |
| MLC-LLM | Compiler lane. Out of scope for v1. |
| Dynamo (ai-dynamo) | Multi-GPU orchestration layer. Out of scope for v1. |

## 2. Version pinning

Rules:

1. Record the exact version or container image digest of each stack.
2. Pin the version at the pilot phase (Phase 2). Reuse the pin in all later phases.
3. If a pinned version breaks on a SUT, choose the nearest working version.
   Record the deviation in the environment record with the reason.
4. Never mix two stack versions in one campaign.
5. Update pins only by a recorded change (new campaign version).

Pilot pins (fill during Phase 2):

| Stack | Version / digest | Date |
|---|---|---|
| vLLM | _TBD_ | _TBD_ |
| SGLang | _TBD_ | _TBD_ |
| TensorRT-LLM | _TBD_ | _TBD_ |

## 3. Serving interface

1. Every stack serves an OpenAI-compatible HTTP endpoint.
2. The endpoint exposes at minimum: model load, chat/completion, and server health.
3. The harness talks to this endpoint only. No in-process calls.

## 4. Baseline server configuration

Apply this baseline to every stack on every SUT.
A stack-specific override requires a recorded reason.

| Setting | Value |
|---|---|
| Sampling | Greedy (temperature 0). |
| Tensor parallelism | 1 for Tier 1, 8 for Tier 2. |
| Max concurrent sequences | 256. |
| GPU memory utilization target | 0.90. |
| Chunked prefill | Enabled. |
| Prefix cache | Disabled in the unique-prompt scenario. Enabled in the shared-prefix scenario. |
| Speculative decoding | Disabled by default. One optional lane with the model-native method (MTP/DSpark or EAGLE) enabled, marked SPEC. |
| Engine build (TensorRT-LLM only) | FP8, max batch 256, context per spec 04. |

### 4.1 Configuration lanes

| Lane | Config | Use |
|---|---|---|
| CFG-BASE | The baseline table above. | Headline lane. Every SUT and stack reports it. |
| CFG-TUNED | The best-known configuration for this stack on this SUT: vendor recipes, DP attention, expert parallelism, custom kernels. | Tuned lane. Shows the stack at its best. |

Rules:
1. Record the full CFG-TUNED server settings in the environment record.
2. Flag every CFG-TUNED row TUNED.
3. Never compare CFG-BASE with CFG-TUNED rows in one table.
4. CFG-TUNED runs the same payload profiles, request counts and
   concurrency list as CFG-BASE. Only the server configuration changes.
5. The baseline still runs first. A CFG-TUNED result without the
   matching CFG-BASE result is incomplete.

## 5. Support matrix (expected, verify in pilot)

| Stack | SUT-1 H200 | SUT-2 B300 | SUT-3 RTX6000 | SUT-4 MI300X | SUT-5 MI355X | SUT-6 MI450 | SUT-7 D270 |
|---|---|---|---|---|---|---|---|
| vLLM | Yes | Yes | Yes | Yes | Yes | Expected | To verify |
| SGLang | Yes | Yes | Yes | Yes | Yes | Expected | To verify |
| TensorRT-LLM | Yes | Yes | Yes | No | No | No | No |
| LMDeploy | Yes | Yes | Yes | Yes | Yes | Expected | No |
| llama.cpp | Yes | Yes | Yes | No | No | No | To verify |
| Ollama | Yes | Yes | Yes | No | No | No | No |

## 6. Per-SUT stack requirement

- Every SUT runs vLLM and SGLang if the vendor stack supports them.
- SUT-1, SUT-2, SUT-3 also run TensorRT-LLM.
- A SUT that cannot run two stacks reports only the runs it can.
  Mark the missing rows as N/A with the reason in the report.
