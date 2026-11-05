# 00 — Scope and Definitions

## 1. Scope

This specification defines the test of 8-GPU server systems.
The test measures AI inference performance only.

### 1.1 In scope

- 8-GPU servers from NVIDIA, AMD, and Intel.
- Large language model (LLM) inference.
- Offline (batch) and online (served) traffic.
- Inference stacks listed in spec 02.
- Metrics listed in spec 06.

### 1.2 Out of scope

- Model quality or accuracy tests.
- Training and fine-tuning.
- Multi-node tests. Phase 1 is single-node only.
- Non-LLM models (vision, speech, embeddings).

## 2. Definitions

Use these terms with these meanings in all documents.

| Term | Definition |
|---|---|
| System | One 8-GPU server, with host CPU, memory, and network. |
| System under test (SUT) | The system that runs the model during a test. |
| Stack | The software that runs the model and serves requests. Examples: vLLM, SGLang, TensorRT-LLM. |
| Harness | The client tool that sends load and records times. The primary harness in this project: GuideLLM. Cross-check tools: GenAI-Perf and the native stack benchmarks. |
| Model | A trained LLM checkpoint. A model has a size, an architecture, and a precision. |
| Dense model | A model where every token activates all parameters. |
| MoE model | A model where each token activates only part of the parameters (experts). |
| Precision | The numeric format of weights. Examples: BF16, FP8, FP4. |
| TP | Tensor parallelism. Splitting a model across several GPUs. |
| Concurrency | The number of requests the server processes at the same time. |
| ISL | Input sequence length. The number of prompt tokens in a request. |
| OSL | Output sequence length. The number of tokens the model generates. |
| TTFT | Time to first token. Time from request start to first output token. |
| TPOT | Time per output token. Average time between generated tokens. |
| ITL | Inter-token latency. Time between two consecutive output tokens. |
| Throughput | Number of tokens per second. |
| Goodput | Throughput while the system meets the SLO. |
| SLO | A latency limit. A request meets the SLO if it is faster than the limit. |
| Prefix | The shared first part of several prompts. |
| Prefix cache | A KV cache reuse of a stored prefix. |
| Warmup | Run requests before a measured run. Warmup discards cold-start effects. |
| Repetition | One full measured run of one test point. |
| Environment record | The full list of versions, settings, and hashes for one run. See spec 07. |
| Pilot phase | A system is in pilot phase if its software is not general availability. |
| Comparability category | The level of model match of a result row. STRICT: identical model revision and precision. ARCH: a different model of the same tier (for example a fallback model). HWBEST: the hardware-best precision lane (native FP4). See spec 04.1.6. |
| Validity level | The state of a result row: VALID, QUALIFIED, or INVALID. See spec 05.7. |
| Reference point | A small fixed test point that the campaign re-runs to detect performance drift. See spec 05.2. |
| Thermal soak | A fixed idle wait between load phases so the GPU reaches a stable temperature before a measured pass. See spec 05.3. |
| Confidence interval | The range that holds the measured value for a stated share of resampled repetitions. This project uses a 95% bootstrap interval. See spec 07.1.2. |

## 3. Rules

### 3.1 Language

- Write all documents in the ASD-STE100 standard.
- Use short sentences. Use the active voice.
- Use present tense for facts. Use numbered steps for actions.
- One instruction per sentence.

### 3.2 Measurement

- Measure time on the client. The server clock is not the reference.
- A test point is one (model, stack, workload, concurrency) combination.
- Record every test point as one result row. See spec 07.

### 3.3 Comparability

- Run every stack with the same client and the same primary harness.
- Fix all settings before the run. See spec 05.
- Change one variable per comparison.
- Report the environment record with every result.

### 3.4 Validity

- A result is valid only if it passes the checks in spec 05.7.
- An invalid result is marked INVALID. Do not delete it.
- Every row carries a validity level: VALID, QUALIFIED, or INVALID.
  The levels are defined in spec 05.7. The assembler assigns them.

### 3.5 Versioning

- The repository keeps one `VERSION` file with four versioned artifacts:
  `benchmark_spec`, `runner_version`, `assembler_version`,
  `corpus_version`, and `result_schema_version`.
- Record all versions in every run record and in the aggregate results.
- Compare two result rows only when the `benchmark_spec` version of
  both rows matches. Otherwise report the rows, but state that the
  benchmark versions differ.
- Raise the `benchmark_spec` version when a rule in this document set
  changes in a way that makes new results not comparable with old ones
  (for example: a new validity rule, a changed repetition count).
- Raise the tool versions when the tools change without a spec change.
