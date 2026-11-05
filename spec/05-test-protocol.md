# 05 — Test Protocol

This document fixes the run procedure.
It exists so that every result comes from the same logic.

## 1. Test point

A test point is one combination of:

- SUT (spec 01)
- Stack and version (spec 02)
- Model and revision (spec 04)
- Token profile T-S or T-L
- Prompt scenario P-U or P-S
- Traffic regime W-ON or W-OFF
- Concurrency value (for W-ON)

## 2. Order of runs

Run test points in this order:

1. All TP1 points on a SUT (Tier 1, one GPU).
2. All TP2 to TP5 points (Tier 2, eight GPUs).
3. All TP6 points (shared prefix).
4. Cross-checks (spec 03.1) after the primary results.
5. Extended-set stacks after the core set finishes on the SUT.

Rationale: the one-GPU tests are the fastest feedback loop.
They catch setup errors before the long eight-GPU runs.

## 3. Procedure per test point

1. Confirm the SUT is idle. No other process holds a GPU.
2. Start the server with the pinned stack version.
3. Wait for the health endpoint to pass.
4. Capture the server startup log and settings.
5. Run the warmup (spec 03.3).
6. Start power sampling (spec 06.7).
7. Run the measured load with Harness A. Save the output.
8. Stop the server. Confirm the GPUs release.
9. Start the server again with the same settings.
10. Repeat steps 5 to 8 with Harness B.
11. Stop power sampling. Save the power trace.
12. Write the result row (spec 07).
13. Repeat the full sequence for two more repetitions.

Use the median of the three repetitions for the headline result.
Keep all three raw outputs.

## 4. Pinned variables (do not change between SUTs)

| Variable | Value |
|---|---|
| Sampling | Greedy, temperature 0. |
| Concurrency list | 1, 2, 4, 8, 16, 32, 64, 128, 256. |
| Requests per run | 1 000 (plus warmup). |
| Token profiles | T-S, T-L (spec 04.2). |
| Prompt seed | Fixed per campaign. |
| Server config | spec 02.4 baseline. |
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
3. A deviation must not change the token profile or the concurrency list.
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
2. Same harness pair on every stack, so the two results are independent views of one run.
3. Same model revision, precision, and token profile on every SUT.
4. Same server baseline config, so vendor-tuned defaults do not shift the result.
5. Unique-prompt scenario with cache control, so prefix reuse cannot hide or create speed.
6. Median of 3 repetitions with a CoV gate, so noise does not pass as signal.
7. Full environment record, so any row can be re-checked or re-run.

When comparing two rows, check that these fields match:
model revision, precision, token profile, scenario, regime, concurrency, TP, and deviation flags.
If a field does not match, do not compare the two rows.
