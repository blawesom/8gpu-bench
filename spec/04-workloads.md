# 04 — Workloads

## 1. Model set

### 1.0 Design goal

Use recent models that use the hardware fully.
Recent models carry these optimizations:

| Optimization | Effect on hardware | Models that use it |
|---|---|---|
| MoE with low active parameters | More tokens per byte of memory moved. | All Tier 2 and Tier 3 models. |
| MLA (latent attention) | Small KV cache per token. | DeepSeek, Kimi, GLM families. |
| DSA (sparse attention with an indexer) | Fewer bytes read at long context. | DeepSeek-V3.2+, GLM-5.x, Kimi K3.1. |
| GDN / linear attention (recurrent state) | Decode cost stays flat as context grows. | Qwen3.8 (48 of 64 layers), Kimi K3.1 (KDA, 69 of 93 layers). |
| SWA and hybrid attention | Smaller KV cache on most layers. | DeepSeek-V4.1 class. |
| MTP (multi-token prediction) | Speculative decode without a separate draft model. | DeepSeek (DSpark), Qwen3.8 (DSpark/DFlash2), Kimi (DSpark). |
| Native FP8 and FP4 (MXFP4, NVFP4) | Less memory, more compute per byte. | All Tier 2 and Tier 3 models. |

The test must show the effect of these optimizations.
Record which features the server activated for each run (see 1.5).

### 1.1 Tier 1 — optimized dense, single GPU

| Item | Value |
|---|---|
| Model (selected) | Qwen/Qwen3.8-27B (Aug 2026) |
| Fallback | Qwen/Qwen3-8B, if a stack does not support Qwen3.8 |
| Architecture | Dense 27B, hybrid attention: 48 of 64 layers use Gated DeltaNet (linear), 16 use full attention. Built-in MTP draft head. 262K native context. |
| Serving path | Text only. The vision tower stays out of scope. |
| Precision | BF16 for all SUTs. PL-4 lane (NVFP4, 24.6 GiB) optional. |
| Placement | TP=1, one GPU (GPU 0). |
| Purpose | Per-GPU decode speed, linear-attention kernel quality, memory-bandwidth behavior. |

Notes:
- BF16 weights are about 54 GB. This fits one GPU of every SUT,
  including SUT-3 (RTX PRO 6000, 96 GB) and SUT-7 (D270, 160 GB).
- vLLM serves it from version 0.17.0. NVIDIA NIM has FP8 and NVFP4
  profiles for Hopper and Blackwell.
- The AMD path for the 27B is not verified upstream. Verify it in the
  pilot. If it fails, use the fallback model on AMD SUTs and flag the rows.
- Thinking mode: the model ships with adaptive thinking (default effort
  xhigh). This changes the output length and the decode time.
  See rule 1.5.7.

### 1.2 Tier 2 — optimized large MoE, all 8 GPUs (headline)

| Item | Value |
|---|---|
| Model (selected) | deepseek-ai DeepSeek-V4.1-Flash (552B total, 8B/16B active, 890 bytes KV per token, compressed attention plus SWA, MTP/DSpark) |
| Fallback | zai-org GLM-5.3-Flash (321B MoE, DSA, NVFP4 / FP8 / BF16) |
| Cross-vendor denominator | openai/gpt-oss-120b (MXFP4, MLPerf Inference v6.0 workload) |

Notes:
- DeepSeek-V4.1-Flash has SGLang day-0 support on Blackwell, H200, and MI350X. vLLM supports the DeepSeek-V4 family.
- GLM-5.3-Flash runs at TP2 to TP8 in NVFP4, FP8, and BF16.
- Run the cross-vendor denominator model on every SUT. It anchors our
  numbers against published MLPerf Inference v6.0 results.
- DeepSeek-V4.1-Flash at FP8 needs about 600 GB for the weights. It fits SUT-1,
  SUT-2, SUT-4, SUT-5, SUT-6 at FP8. For SUT-3, use the FP4 lane (1.4).

### 1.3 Tier 3 — frontier lane (optional, not comparable)

| Item | Value |
|---|---|
| Model (selected) | moonshotai/Kimi-K3.1-Pro (Nov 2026) |
| Architecture | 2.8T total, 104B active MoE. 93 layers: 69 Kimi Delta Attention (linear) plus 24 full attention with DSA indexer (top-2048) and NoPE. 896 experts, 16 active, MXFP4. 1M context. |
| Spec decode | DSpark draft (Inferact/Kimi-K3.1-Pro-DSpark-preview), 5 draft tokens. |
| Placement | TP=8 on one node. SUT-2, SUT-5, SUT-6 only. |
| Purpose | Frontier lane. Shows the upper bound of the large-memory systems. |

Notes:
- The MXFP4 weights are about 1.57 TB (about 196 GB per GPU at TP8).
  They fit one node of SUT-2, SUT-5, and SUT-6. They do not fit SUT-1
  (H200 needs two nodes), SUT-3, SUT-4, or SUT-7. Mark those N/A.
- Use decode context parallelism (DCP8) where the stack supports it.
  DCP shards the KV cache across the 8 GPUs. Record whether it is on.
- The model is a thinking model. Apply rule 1.5.7.
- Practical concurrency is lower than the Tier 2 models (about 48 in
  published recipes). Use the reduced concurrency list 1, 4, 16, 32, 48
  for Tier 3. Flag these rows T3.
- Day-0 support: vLLM and SGLang on NVIDIA Blackwell and AMD MI355X.

Tier 3 is not part of the headline comparison. Report it in a separate table.

### 1.4 Precision lanes

| Lane | Precision | Use |
|---|---|---|
| PL-8 | FP8 (or BF16 for Tier 1) | Common lane. Runs on every SUT. Headline table. |
| PL-4 | Native FP4: NVFP4 on NVIDIA, MXFP4 on AMD | Hardware-best lane. Separate table. |

Rules:
1. PL-8 is the headline lane. All SUTs report it.
2. PL-4 shows the hardware at its best. Report it in a separate table.
3. Never mix PL-8 and PL-4 rows in one table.
4. If a SUT emulates a format (example: NVFP4 emulation on H200),
   flag the row EMU. An emulated row is not comparable with a native row.
5. Run PL-4 for Tier 2 test points TP3, TP5, and TP6 only. This bounds the time.

### 1.5 Model rules

1. Download the checkpoint once per SUT. Verify the checksum.
2. Record the checkpoint path, revision, and checksum in the environment record.
3. Do not quantize a checkpoint at runtime unless the native format requires it.
   If the stack requires a format conversion, record the conversion command.
4. The same revision runs on every SUT.
5. Record which model features the server activated: DSA, SWA, MLA,
   MTP/speculative decoding, and the KV cache dtype.
   A run where an advertised feature is not active is flagged FEAT.
6. A stack that cannot run a model feature (example: no DSA kernels on a
   platform) runs the model without it. Flag the row DEV and record the
   missing feature. Do not compare this row with rows where the feature runs.
7. Thinking mode control: Qwen3.8 and Kimi K3.1 are thinking models.
   Set the same reasoning mode for every run of these models.
   Default: disable thinking (`enable_thinking: false`) so that the
   model answers directly. Record the mode in the environment record.
   The exact OSL rule (2.3) still applies: `max_tokens` limits the total
   output, thinking tokens included.

## 2. Payload profiles

### 2.1 Registry

A payload profile fixes the request shape: the ISL, the OSL, and the
source of the prompt text. Two profiles are synthetic with exact
lengths. Three profiles model real traffic shapes. All profiles are
versioned artifacts.

| ID | Name | Type | ISL | OSL | Use |
|---|---|---|---|---|---|
| PF-1 | Short chat | Fixed synthetic | 512 | 128 | Headline latency and throughput. |
| PF-2 | Long context | Fixed synthetic | 8 192 | 1 024 | Prefill-heavy, long-context lane. |
| PF-3 | Chat distribution | Dataset | Empirical | Empirical, capped at 1 024 | Real chat traffic shape. |
| PF-4 | Agentic / RAG | Distribution synthetic | Mean 4 096, SD 2 048 | 256 | Long input, short output. Agent and RAG shape. |
| PF-5 | Decode-heavy | Distribution synthetic | 1 024 | 4 096 | Reasoning and long-output shape. |

Rules:
1. PF-1 and PF-2 are headline profiles. Every SUT reports them.
2. PF-3, PF-4, and PF-5 are secondary lanes. Run them after the
   headline lanes pass on the SUT. Report them in separate tables.
3. Never mix profiles in one table.
4. A new profile gets a new ID. Do not change an ID that has results.
5. PF-6 (trace replay) is reserved. Define it when production traces
   exist. Do not use it in this version.

PF-3 data source:
- Default: LMSYS-Chat-1M, a fixed recorded sample of 2 000 conversations.
- Fallback: ShareGPT, processed with a pinned script.
- Record the dataset revision, the sample seed, and the sampling rule.

### 2.2 Corpus rules

Each profile has one corpus. A corpus is a list of requests. Each
request holds the prompt text, the target ISL, and the target OSL.

1. Generate each corpus once with the generator script and a fixed seed.
2. Store the corpus as a JSONL file. Version it in the repository.
3. Record the corpus checksum in the environment record.
4. Both harnesses consume the same corpus file for a measured run.
   The harnesses do not generate their own prompts.
5. The corpus stores text, not tokens. Token counts depend on the model
   tokenizer. The generator emits one request list per model, with the
   ISL and the OSL counted with the tokenizer of that model.
6. A corpus holds at least 2 000 requests. No prompt repeats within
   one measured run of 1 000 requests.
7. PF-4 and PF-5 sample the ISL from the distribution with the fixed
   seed. The corpus stores the sampled value per request.

### 2.3 Token control

1. For PF-1 and PF-2, pad or truncate each prompt to the exact ISL.
   Record the method.
2. For PF-4 and PF-5, pad or truncate each prompt to the sampled ISL.
3. Force the OSL with `max_tokens` on every profile. Disable early stop.
4. For PF-3, do not force the ISL. Cap the OSL at 1 024.
5. Verify the actual token counts against the profile:
   - PF-1, PF-2, PF-4, PF-5: within 1 token of the target.
   - PF-3: within 10% of the recorded corpus statistics.

### 2.4 Harness mapping

| Profile | GuideLLM input | GenAI-Perf input |
|---|---|---|
| PF-1 to PF-5 | The corpus file, through the dataset data kind. | The corpus file, through the custom dataset input. |

Both harnesses have built-in synthetic generation options. Do not use
them for measured runs. The corpus file is the single source of truth.
This keeps the two harness views comparable.
Verify the exact flag names and file schemas at the version pin
(spec 03.5).

## 3. Prompt scenarios

The scenario defines the prefix structure. The profile (section 2)
defines the request shape. They are independent axes.

| ID | Name | Setup |
|---|---|---|
| P-U | Unique | No two requests share a prefix. Disable prefix cache where the stack allows. |
| P-S | Shared prefix | 80% of the prompt is one fixed system prompt. The remaining 20% is unique. Enable prefix cache. |

Run rules:
1. Every measured test point runs P-U.
2. Run P-S for SGLang and vLLM only, at concurrency 32 and 128.
3. Label P-S rows clearly. Never mix P-S rows with P-U rows in one table.

## 4. Traffic regimes

| ID | Name | Description |
|---|---|---|
| W-OFF | Offline | Saturate the server. Send requests as fast as the server accepts them, up to 256 in flight. |
| W-ON | Online | Fixed concurrency sweep. Use the concurrency list in spec 03.3. |

Test point matrix (per model, per stack, per SUT):

| Test point | Model | TP | Profile | Scenario | Regime |
|---|---|---|---|---|---|
| TP1 | Tier 1 | 1 | PF-1 | P-U | W-ON (all concurrency values) |
| TP2 | Tier 1 | 1 | PF-2 | P-U | W-ON (1, 4, 16, 64) |
| TP3 | Tier 2 | 8 | PF-1 | P-U | W-ON (all concurrency values) |
| TP4 | Tier 2 | 8 | PF-2 | P-U | W-ON (1, 4, 16, 64, 128) |
| TP5 | Tier 2 | 8 | PF-1 | P-U | W-OFF (saturate) |
| TP6 | SGLang/vLLM only | 8 | PF-1 | P-S | W-ON (32, 128) |
| TP7 | Tier 2 | 8 | PF-3 | P-U | W-ON (16, 64) |
| TP8 | Tier 2 | 8 | PF-4 | P-U | W-ON (16, 64) |
| TP9 | Tier 2 | 8 | PF-5 | P-U | W-ON (16) |

TP1 to TP6 are headline test points. TP7 to TP9 are secondary lanes.
Run the secondary lanes after the headline lanes pass on the SUT.

## 5. Workload validity

1. The first 100 requests of a run are warmup. Discard them.
2. A run is complete when 1 000 measured requests finish or 1 800 s elapse.
3. If more than 2% of requests time out or error, the run is invalid.
4. Re-run an invalid run once. If it fails again, mark INVALID.
5. A run fails the profile check if the token counts do not meet
   rule 2.3.5. Mark it INVALID with the flag PROFILE.
