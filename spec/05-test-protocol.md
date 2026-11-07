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

### 2.1 Randomized value order

The order of the TP groups above stays fixed.
Within one test point, the concurrency values (or request rates)
run in a random order.
This removes systematic bias from GPU temperature drift, power-limit
state, and cache warm state (see the risks in PLAN section 6).

1. Fix the order seed per campaign.
   The seed is the campaign seed (spec 05.4) and is recorded in the
   environment record of every run.
2. Re-shuffle the value list for every repetition of a test point.
3. Record the actual value order of every sweep in the run record.
4. A re-run of a stopped campaign reuses the recorded order.
   Do not shuffle again.

### 2.2 Reference points (drift detection)

Performance can drift over a campaign: temperature, firmware state,
and background load change the result of the same test point.
A reference point detects this drift.

1. The campaign defines one reference point: a Tier 1 test point
   (PF-1, P-U, W-ON) at one fixed concurrency (32) with 200 requests.
   The manifest field is `reference`.
2. Run the reference point before the first test point, after every
   10 test points (manifest field `reference_every`, default 10),
   and after the last test point.
3. Mark every reference record REFERENCE.
   Reference records never enter the headline tables.
4. The assembler computes the reference drift per reference group:
   one group per SUT, stack, stack version, model, precision and
   config lane. The drift is the relative spread (max minus min,
   over the mean) of the output throughput of the reference records
   of the group (spec 07.1.3). The groups never pool: throughput is
   not comparable between SUTs, so a pooled drift measures the
   difference between the systems, not drift.
5. The drift limit is 5% (check V8, section 7).
   Above the limit, the assembler marks every row of the group
   QUALIFIED with the reason REFERENCE-DRIFT.
   Do not rank rows of a group with failed drift without a re-run.
6. A group with fewer than 2 reference points has an unevaluated
   V8. The assembler records this in validation.json.
   No row is demoted for an unevaluated check.

## 3. Procedure per test point

1. Confirm the SUT is idle. No other process holds a GPU.
2. Start the server with the pinned stack version. The runner runs
   on the client and starts the SUT container through Docker over
   SSH (spec 03.2 rule 6).
3. Wait for the health endpoint to pass, from the client.
4. Capture the server startup log and settings.
5. Run the warmup (spec 03.3). One warmup per server start.
6. Run the thermal soak: wait 60 s with no load after the warmup and
   before the first measured pass. Between two measured passes, wait
   15 s. Record the GPU temperature at the start and the end of
   every pass (spec 07.4).
7. Start power sampling (spec 06.7).
8. Run the measured load with the primary harness for every
   concurrency value of the test point, in the randomized order of
   section 2.1. Save the output of every value.
9. Stop the server. Confirm the GPUs release.
10. Stop power sampling. Save the power trace.
11. Write one result row per concurrency value (spec 07).
12. Repeat the full sequence for the repetition count of section 4.

One server start per test point and repetition. The harness is a
fixed instrument (spec 03.1), so no restart between measurements is
needed. A failed pass restarts the server and retries once
(spec 04.5 rule 4). The sweep then continues with the next value.
The headline result is the median of the repetitions
(section 4). Report the median, p10, p90, the coefficient of
variation, and the 95% bootstrap confidence interval
(spec 07.1.2). Keep every individual repetition in the result store.
Keep all raw outputs.

## 4. Pinned variables (do not change between SUTs)

| Variable | Value |
|---|---|
| Sampling | Greedy, temperature 0. |
| Concurrency list | 1, 2, 4, 8, 16, 32, 64, 128, 256. |
| Value order | Randomized per test point and repetition, fixed campaign seed (section 2.1). |
| Requests per run | 1 000 (plus warmup). |
| Repetitions | 5 for core test points. 7 for the Tier 2 headline points (TP3, TP4, TP5). |
| Payload profiles | PF-1 to PF-9 (spec 04.2). |
| Prompt seed | Fixed per campaign. |
| Order seed | The campaign seed (same value as the prompt seed). |
| Server config | CFG-BASE lane (spec 02.4). CFG-TUNED is a separate lane. |
| Client machine | One machine for the whole campaign. |
| Network path | 25 GbE or faster, dedicated. |
| GPU count and TP | Per test point (spec 04.4). |
| Memory utilization target | 0.90. |
| Power profile | Vendor default. |
| Thermal soak | 60 s after warmup, 15 s between passes (section 3). |
| Reference point | PF-1, P-U, W-ON, concurrency 32, 200 requests (section 2.2). |

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
| V3 | Coefficient of variation of output throughput across the repetitions (5 core, 7 headline) | Below 5%. |
| V4 | GPU utilization during the measured run | Above 95% at saturation points (W-OFF, highest concurrency). |
| V5 | Power trace coverage | At least 90% of run duration sampled. |
| V6 | Environment record completeness | All fields present. |
| V7 | Thermal events (throttle, trip) in the run log | Zero. |
| V8 | Reference point drift over the campaign (section 2.2) | Below 5%. |
| V9 | Comparability category matches the model and precision (spec 04.1.6) | Exact match. |

A failed check marks the result INVALID.
Fix the cause. Re-run the test point.
Two consecutive INVALID results stop the SUT campaign. Escalate.

### 7.1 Validity levels

The assembler assigns one validity level to every result row.
The levels make the quality of a row explicit instead of leaving it
to the reader of the report.

| Level | Meaning | Assignment |
|---|---|---|
| VALID | Fully compliant with this specification. | All checks pass. No limitation flags. |
| QUALIFIED | Usable, with documented limitations. | All checks pass, but the row carries a limitation: flags DEV, PILOT, EMU, or FEAT; missing power data; or the campaign reference drift failed V8 (reason REFERENCE-DRIFT). |
| INVALID | Excluded from the rankings. | A check failed (V1, V3, V5, V6, V7, V9), or a server, warmup, or harness failure. |

Rules:
1. Cross-check rows (flag CROSSCHECK) carry no validity level.
   They are informational only (spec 03.1).
2. Reference rows (flag REFERENCE) carry no validity level.
   They feed the V8 check only.
3. An INVALID row is never deleted. Keep it in the result store.
   Headline tables mark it INVALID (spec 07.3).
4. The assembler writes the level and the reasons for every row to
   `validation.json` (spec 07.2). The report cites this file.

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
6. Median of 5 (core) or 7 (headline) repetitions with a CoV gate
   and a 95% bootstrap confidence interval, so noise does not pass
   as signal (spec 07.2).
7. Full environment record with all benchmark versions
   (spec 00.3.5), so any row can be re-checked or re-run.
8. Randomized value order with a fixed seed (section 2.1), so
   thermal and state drift does not bias one value.
9. Reference points (section 2.2), so slow performance drift over a
   campaign is visible.

When comparing two rows, check that these fields match:
benchmark spec version, model revision, precision, comparability
category, payload profile, scenario, regime, concurrency, TP,
config lane, traffic mode, and deviation flags.
If a field does not match, do not compare the two rows.
