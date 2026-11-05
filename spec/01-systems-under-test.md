# 01 — Systems Under Test

## 1. System matrix

| ID | System | Accelerators | Memory per GPU | Memory total | Interconnect | Cooling | Phase |
|---|---|---|---|---|---|---|---|
| SUT-1 | NVIDIA HGX H200 | 8x H200 SXM5 | 141 GB HBM3e | 1 128 GB | NVLink 4 | Liquid / air (OEM) | Core |
| SUT-2 | NVIDIA HGX B300 / GB300 NVL8 | 8x Blackwell Ultra | 288 GB HBM3e | 2 304 GB | NVLink 5 | Liquid | Core |
| SUT-3 | NVIDIA RTX PRO 6000 Blackwell Server Edition | 8x RTX PRO 6000 | 96 GB GDDR7 | 768 GB | PCIe Gen5 (no NVLink) | Air | Core |
| SUT-4 | AMD Instinct MI300X platform | 8x MI300X OAM | 192 GB HBM3 | 1 536 GB | Infinity Fabric (UBB 1.0) | Air / liquid | Core |
| SUT-5 | AMD Instinct MI350/MI355X platform | 8x MI355X OAM | 288 GB HBM3e | 2 304 GB | Infinity Fabric (UBB 2.0) | Air / liquid | Core |
| SUT-6 | AMD Instinct MI450 (Helios node) | 8x MI455X | 432 GB HBM4 | 3 456 GB | UALink | Liquid | Pilot |
| SUT-7 | Intel GPU D270 (Crescent Island) | 8x D270 PCIe5 | 160 GB LPDDR5X (up to 480 GB ODM) | 1 280 GB (up to 3 840 GB) | PCIe Gen5 | Air | Pilot |

Notes:
- SUT-2: pick one physical form (HGX B300 or GB300 NVL8). Record which.
- SUT-3: no intra-node GPU fabric. This is a test condition, not a defect. State it in the report.
- SUT-6: early availability. Entry requires a working node and ROCm release.
- SUT-7: customer sampling. Entry requires a stable driver and a serving path (see spec 02).

## 2. Host requirements

The host must meet these minimums for every SUT:

- Two host CPUs with at least 64 cores total.
- At least 2 TB of system RAM.
- One network port of 25 GbE or faster for the load client path.
- One dedicated management port.
- A power meter or per-GPU power sensors (see spec 06, power metrics).

If a SUT cannot meet a minimum, record the exception in the environment record.

## 3. Entry criteria

A SUT enters the core test campaign when all of these pass:

1. The vendor software (driver + runtime) is at a recorded version.
2. One core stack serves one Tier 1 model on one GPU.
3. The same stack serves the Tier 1 model across 8 GPUs (TP=8).
4. The primary harness runs one smoke test end-to-end (see spec 03.4).
5. The environment record script outputs a full record (see spec 07).
6. The system runs 30 minutes idle with no error and no thermal trip.

A SUT that fails item 1, 2, or 3 stays in pilot phase.
Report pilot-phase results with the flag PILOT.

## 4. Availability matrix (planned)

| SUT | Core campaign | Notes |
|---|---|---|
| SUT-1 H200 | Yes | Reference system. |
| SUT-2 B300 | Yes | Confirm liquid-cooling logistics. |
| SUT-3 RTX PRO 6000 | Yes | PCIe fabric changes results. Interpret with care. |
| SUT-4 MI300X | Yes | |
| SUT-5 MI355X | Yes | |
| SUT-6 MI450 | If available | Else skip and note in report. |
| SUT-7 D270 | If available | Else skip and note in report. |

## 5. System preparation

Before each campaign on a SUT:

1. Install the recorded OS image. Reuse the image across all SUTs of the same vendor class where possible.
2. Install the recorded driver and runtime versions.
3. Set the power profile to the vendor default for serving. Do not overclock. Do not undervolt.
4. Record the BIOS and firmware versions in the environment record.
5. Run the entry criteria checks. Save the output.
