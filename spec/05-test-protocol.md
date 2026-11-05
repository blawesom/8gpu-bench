# 05 — Test Protocol

This document fixes the run procedure.
It exists so that every result comes from the same logic.

## 1. Test point

A test point is one combination of:

- SUT (spec 01)
- Stack and version (spec 02)
- Model and revision (spec 04)
- Payload profile PF-1 to PF-9 (spec 04)
- Prompt scenario P-U, P-S, or P-C (spec 04)
- Traffic regime W-ON or W-OFF
- Concurrency value (for W-ON)

## 2. Order of runs

Run test points in this order (TP definitions in spec 04.4):

1. All TP1 and TP2 points (Tier 1, one GPU).
2. All TP3 to TP5 points (Tier 2 headline, eight GPUs).
3. All TP6 points (shared prefix).
4. All TP7 to TP9 points (secondary lanes).
5. All TP10 to TP12 points (production lanes). Run them last.
6. Cross-checks (spec 03.1) after the primary results.
7. Extended-set stacks after the core set finishes on the SUT.

Rationale: the one-GPU tests are the fastest feedback loop.
They catch setup errors before the long eight-GPU runs.
The production lanes run last because they hold the context
for a long time.

## 3. Procedure per test point

1. Confirm the SUT is idle. No other process holds a GPU.
2. Start the server with the pinned stack version.
3. Wait for the health endpoint to pass.
4. Capture the server startup log and settings.
5. Run the warmup (spec 03.3). One warmup per server start.
6. Start power sampling (spec 06.7).
7. Run the measured load with the primary harness for every
   concurrency value of the test point, in ascending order. Save the
   output of every value.
8. Stop the server. Confirm the GPUs release.
9. Stop power sampling. Save the power trace.
10. Write one result row per concurrency value (spec 07).
11. Repeat the full sequence for two more repetitions.

One server start per test point and repetition. The harness is a
fixed instrument (spec 03.1), so no restart between measurements is
needed. A failed pass restarts the server and retries once
(spec 04.5 rule 4). The sweep then continues with the next value.
Use the median of the three repetitions for the headline result.
Keep all raw outputs.

## 4. Pinned variables (do not change between SUTs)

| Variable | Value |
|---|---|
| Sampling | Greedy, temperature 0. |
| Concurrency list | 1, 2, 4, 8, 16, 32, 64, 128, 256. |
| Requests per run | 1 000 (plus warmup). |
| Payload profiles | PF-1 to PF-9 (spec 04.2). |
| Prompt seed | Fixed per campaign. |
| Server config | CFG-BASE lane (spec 02.4). CFG-TUNED is a separate lane. |
| Client machine | One machine for the whole campaign. |
| Network path | 25 GbE or faster, dedicated. |
| GPU count and TP | Per test point (spec 04.4). |
| Memory utilization target | 0.90. |
| Power profile | Vendor default. |

## 5. Permitted deviations

A deviation is allowed only when a SUT or stack cannot meet a pinned variable.
Rules:

1. Record the deviation in the environment record.
2. State the reason (for example: "stack does not support TP=8 on this interconnect").
3. A deviation must not change the payload profile or the concurrency list.
4. A result with a deviation is marked DEV in the report.
5. Compare DEV rows only against rows with the same deviation.

## 6. Environment rules

1. One OS image per vendor class, recorded as a hash.
2. One driver/runtime version set per SUT, recorded.
3. No background jobs on the SUT or the client during a measured run.
4. Set the GPU power limit to the vendor default. Record the value.
5. Record the BIOS, firmware, and thermal profile.
6. Keep the room temperature in a recorded band (18 to 27 C).

## 7. Validity checks

A result passes only if all checks pass:

| ID | Check | Limit |
|---|---|---|
| V1 | Error + timeout rate per run | Below 2%. |
| V2 | Token count match (spec 04.2) | Within ±1 token. |
| V3 | Coefficient of variation of throughput across the 3 repetitions | Below 5%. |
| V4 | GPU utilization during the measured run | Above 95% at saturation points (W-OFF, highest concurrency). |
| V5 | Power trace coverage | At least 90% of run duration sampled. |
| V6 | Environment record completeness | All fields present. |
| V7 | Thermal events (throttle, trip) in the run log | Zero. |

If a check fails, mark the result INVALID.
Fix the cause. Re-run the test point.
Two consecutive INVALID results stop the SUT campaign. Escalate.

## 8. Comparability logic

State the logic that makes results comparable:

1. Same client measures every stack, so client cost is constant.
2. Same primary harness on every stack (spec 03.1), so the
   measurement instrument never shifts the comparison. Cross-check
   tools never enter the headline tables.
3. Same model revision, precision, and payload profile on every SUT.
4. Same server baseline config, so vendor-tuned defaults do not shift
   the result. The CFG-TUNED lane (spec 02.4.1) is reported apart.
5. Unique-prompt scenario with cache control, so prefix reuse cannot hide or create speed.
6. Median of 3 repetitions with a CoV gate, so noise does not pass as signal.
7. Full environment record, so any row can be re-checked or re-run.

When comparing two rows, check that these fields match:
model revision, precision, payload profile, scenario, regime,
concurrency, TP, config lane, traffic mode, and deviation flags.
If a field does not match, do not compare the two rows.
