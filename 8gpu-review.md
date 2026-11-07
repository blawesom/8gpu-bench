# Review — methodology and tooling (November 2026)

Scope: the whole repository (spec chapters 00 to 07, `tools/`, `runs/`,
`infra/ansible/`, CI). All three tool selftests pass. The main suspected
bugs were reproduced with small probe scripts.

**Verdict:** the spec is unusually careful for a project that hasn't run
its pilot yet. It has separate configuration lanes, comparability
categories, validity levels, randomized sweep order, reference points and
versioned artifacts. The tooling does not yet carry that out. Three
defects silently corrupt or hide results, much of what the spec promises
is never enforced, and the runner can't be deployed the way the spec
describes. Until those are fixed, the results would look rigorous without
being trustworthy.

## A. Technical defects, most serious first

**1. Run IDs collide, so records are silently lost (confirmed).** The run
ID (`tools/run-benchmark.py:713`) leaves out the reference label, and
every reference run uses repetition 1 (`:1077`). The assembler removes
duplicate run IDs and keeps the last one (`tools/build-results.py:218`).
- Probe: 3 reference records written, **1 kept**.
- In `runs/example-nvidia.yaml`, the reference point (PF-1, C=32, rep 1)
  has the same ID as the test point at C=32, rep 1. One silently replaces
  the other, and that test point drops to 4 repetitions.
- CI missed this because the assembler selftest builds records with
  hand-made IDs instead of using the runner's record builder.

**2. The drift check (V8) can never pass or fail correctly.**
- Because of #1, it sees at most one reference per manifest. With a real
  20% drift it reports `pass=None` (not evaluated).
- When several manifests share one `runs.jsonl` (spec 07.1 rule 4), it
  pools references from all SUTs and stacks together
  (`build-results.py:402`). Probe: two perfectly stable SUTs give
  **66.7% drift**, and every row in the campaign is demoted (`:661`).
- Drift needs to be computed per SUT, stack and model, and demotion
  applied only to that group.

**3. A failed metric extraction counts as a usable result (confirmed).**
If the harness output can't be parsed, metrics are empty. That test point
still comes out **QUALIFIED** with `out_tps=''`, and the only sign is a
schema warning. The V1 error-rate check is also skipped silently when the
request counts are missing (`run-benchmark.py:479`). Missing core metrics
should make the row INVALID.

**4. The runner can't be deployed as the spec describes.** It starts the
server with a local `docker run`, then checks health and runs warmup
against `localhost` (`:346`, `:860`). It also runs GuideLLM from a local
path. But spec 03.2 and the Ansible client role put the harness on a
separate client machine. So either the harness runs on the SUT (breaking
spec 03.2), or the container starts on the client.

**5. Power and energy figures are wrong by about the GPU count.** The
telemetry writes one row per GPU, and `slice_telemetry` averages all of
them (`:641`). So `power_mean_w` is the average for one GPU, and energy
per 1M tokens (M18, `build-results.py:234`) is about **8× too low at
TP=8**. At TP=1, the container gets `--gpus all`, so 7 idle GPUs pull the
averages down (power, and utilization for V4). `hbm_peak_gb` is the
maximum of one GPU. On AMD, a temperature reading of `N/A` (MI300X does
this for the edge sensor) throws away the whole row, including power
(`:622`).

**6. Spec rules that nothing enforces.** None of these appear in the
harness command or the example manifests:
- `ignore_eos` / forced output length
- the per-request timeout (300 s / 3 600 s)
- the 1 800 s run cap
- `enable_thinking: false`
- prefix cache off in the unique-prompt scenario (vLLM turns it on by
  default)
- max sequences 256 and memory utilization 0.90

Checks that are never implemented: V2 (token count), V4 (GPU
utilization), the CACHE check and M22 (the server's `/metrics` is never
read), and FEAT. The production-lane cache validation, which the spec
calls "the purpose of the lane", can't run at all.

**7. Warmup contaminates measurements and costs a lot of time.**
- Warmup is sequential at concurrency 1 (`:665`). It replays the same
  first corpus rows that the measured run uses, and every concurrency
  value reuses the same rows on the same server. With any prefix cache
  active, later passes get cache hits.
- For PF-9, 100 sequential warmup requests at 50k output tokens each is
  roughly a day per repetition (rough estimate). It also uses up the
  whole 100-row corpus.

**8. The example manifests contradict the spec, and V9 doesn't catch it.**
- Tier 1 is declared FP8 + STRICT, but the spec says Tier 1 is BF16. V9
  accepts FP8 as STRICT (`build-results.py:191`).
- `--dtype float8` is probably not a valid vLLM dtype (FP8 is normally
  `--quantization fp8`; check this at the version pin).
- `--enable-dp-attention` is an SGLang flag, not a vLLM one.

**9. Smaller issues:**
- `--cross-check` reruns the full primary sweep too (`:981`).
- The corpus checksum is read from the sidecar file, not computed from
  the file actually used (`:226`).
- V7 looks for keywords like "thermal" in the server log instead of
  reading hardware throttle reasons, so it will miss real throttling.
- Two INVALID results at C=256, which is expected saturation behavior,
  stop the whole campaign (`:1138`).
- The grouping key leaves out the SPEC and DEV flags, so those lanes
  merge with the base rows (`build-results.py:292`).
- `fetch-sources` doesn't save the dataset revision, and it stores
  assistant turns even though its comment says user turns only.
- The GuideLLM CLI and output schema are unverified (already marked
  PILOT-VERIFY). Treat extraction as best-effort until then.

## B. Methodology

- **Statistics.** The headline value is the median, but the bootstrap CI
  is computed for the mean. At n=5, a simulation of the "95%" interval
  gives **84% coverage**. The interval also only measures run-to-run
  noise on one physical unit, so it overstates confidence in claims like
  "system A beats system B". The rule "a difference smaller than the CI
  width is not significant" is not a valid test. Use t-intervals or BCa,
  or permutation tests, and state the n=1-hardware limitation.
- **V3 (variation above 5% means INVALID)** throws away real behavior: an
  unstable system is a finding, not a bad measurement. **V8 (max−min
  spread above 5% over 3–4 points)** will trip on ordinary 2–3% noise
  even after the bugs are fixed.
- **Headline at the highest concurrency** ranks systems on throughput
  alone, with no latency limit. Systems can also land in the same table
  at different concurrencies. A throughput-versus-interactivity curve, or
  the SLO goodput view, would make a fairer headline.
- **Forced output length with real text and greedy decoding.** With early
  stop disabled, the model keeps generating after EOS, often in
  repetitive text. That inflates speculative-decoding acceptance, which
  undercuts the reason for using WildChat text. PF-3 also claims an
  empirical output length but forces 1 024 tokens.
- **The session model doesn't produce sessions.** GuideLLM's `json_file`
  mode sends rows as independent requests. Turns of the same session can
  be in flight at the same time, and the next turn's history doesn't
  include the model's previous output. The expected ~80% cache hit then
  depends on scheduling, not on the stack.
- **OPEN lane at 1–256 requests/s with 1 000 requests** is a short burst
  above saturation, not a steady state. PLAN §8 already accepts that it
  should use rates relative to saturation.
- **Campaign length.** PF-2 at C=1 is several hours per value (rough
  estimate), uncapped, times 5–7 repetitions. A full budget estimate
  should come before Phase 3.
- **Naming.** "TP2" means test point 2 and tensor parallelism 2 in
  different places (spec 04 §1.1 notes, PLAN item 13). Rename the test
  points.

## Pilot blockers

1. #1 run-ID collision
2. #2 per-group drift
3. #3 missing metrics must mean INVALID
4. #4 split runner placement (client vs SUT)
5. #5 sum power per GPU set
6. #6 pass `ignore_eos`, timeouts and run caps, and pin server flags such
   as prefix cache off and the memory and sequence limits

Statistics, V3/V8 rules, the OPEN lane, sessions and the headline choice
can wait for a spec 1.2 revision.
