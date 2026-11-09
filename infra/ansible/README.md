# Ansible environment bring-up

This directory configures the SUTs (spec 01) and the load client
(spec 03.2) from a fresh Ubuntu Server 24.04 LTS install.

Ansible configures the nodes. It does not install the OS image.
Install the OS first (PXE, BMC, or vendor tools), then run the
playbooks over SSH.

## Layout

```
ansible.cfg                 Settings
inventory/hosts.yml         One host per SUT plus the client
inventory/group_vars/      Vendor settings and common pins
roles/base/                 OS prep: NTP, sysctl, limits, services
roles/gpu-driver/           Vendor driver, GPU count check
roles/container-runtime/    Docker plus GPU passthrough
roles/telemetry/            1 Hz power/thermal sampler, NFS export
roles/env-record/           Emits node-meta.json (spec 07.4)
roles/smoke/                Smoke test (spec 03.4)
roles/client-harness/       GuideLLM + GenAI-Perf, telemetry mount
roles/s3-store/             s3cmd + store credentials (spec 07.7)
playbooks/site.yml          Full bring-up
playbooks/verify.yml        Entry criteria (spec 01.3)
playbooks/record.yml        Refresh the environment record
```

## Use

1. Edit `inventory/hosts.yml`. Set `ansible_host` for each node.
2. Set `smoke_model_path`. Download the Tier 1 model to each SUT.
3. Run the full bring-up:

   ```
   ansible-playbook playbooks/site.yml
   ```

   Bring up one node at a time with `--limit sut1-h200`.

4. Verify the entry criteria:

   ```
   ansible-playbook playbooks/verify.yml
   ```

5. Refresh the record before each campaign day:

   ```
   ansible-playbook playbooks/record.yml
   ```

## Placement and telemetry share (spec 03.2)

The runner runs on the client. It controls the SUT container through
Docker over SSH (`server.docker_host` in the run manifest) and reads
the SUT telemetry from a read-only NFS share:

1. Set `telemetry_export_net` in `group_vars/all.yml` to the client
   network CIDR. The telemetry role exports
   `telemetry_output_dir` read-only to that network.
2. Set `telemetry_mount_from` in `group_vars/client.yml` to
   `<sut>:/var/log/gpu-telemetry`. The client-harness role mounts it
   read-only at `telemetry_mount_to`.
3. Point the runner at the mount:
   `--telemetry /mnt/sut1/gpu-telemetry`.

To move the campaign to another SUT, change `telemetry_mount_from`
and re-run the client role.

## Pinning rules

- Pin the driver version per vendor at Phase 2. Freeze it for the
  campaign. Same rule as the stack pins (spec 02.2).
- Pin `docker_apt_version` in `group_vars/all.yml` at Phase 2,
  jointly with the driver pin. The role installs Docker from the
  upstream apt repository (download.docker.com).
- Driver branch and container digests are one joint decision. The
  driver must match what the pinned containers expect.
- Stacks run from containers only. Ansible does not install vLLM,
  SGLang, or TensorRT-LLM by hand.
- Write the OS image hash to `/etc/8gpu-bench-image.sha256` after
  image install. The env-record role reads it into `node-meta.json`.

## Confidential results store (s3-store role, spec 07.7)

Run results never enter the git repository. They go to a private
S3-compatible bucket. The load client is the only machine that talks
to the store.

1. The endpoint (`s3_host_base`, `s3_host_bucket`,
   `s3_signature_v2`) and the paths (`bench_staging_dir`,
   `bench_corpora_dir`) are set in `group_vars/client.yml`. These are
   not secrets.
2. `site.yml` prompts for the access key, the secret key, and the
   store prefix (`bench_s3_uri`) on the client play. Input is private:
   never echoed, never logged, never committed.
3. The `s3-store` role installs pinned s3cmd and writes, for the
   `bench` user only: `~/.s3cmd` (mode 0600) and
   `~/.bench/bench.env` (mode 0600) with `BENCH_S3_URI`,
   `BENCH_STAGING_DIR`, and `BENCH_CORPORA_DIR`.
4. `verify.yml` fails unless `s3cmd ls "$BENCH_S3_URI"` succeeds on
   the client with those credentials. A setup without a working key
   pair therefore never enters a campaign.
5. Before a campaign on the client:

   ```
   sudo -u bench bash -c 'source ~/.bench/bench.env; \
       export SUT1_HOST=sut1-h200; \
       python3 tools/run-benchmark.py --config runs/example-nvidia.yaml'
   ```

   With `BENCH_S3_URI` set, the runner writes the campaign to
   `$BENCH_STAGING_DIR/<campaign_id>` and checkpoint-syncs it to
   `$BENCH_S3_URI/<campaign_id>/` through `tools/s3sync.sh` after
   every test-point repetition and at exit.

6. Key rotation: create a new key pair in the provider console, re-run
   `site.yml` on the client, confirm `verify.yml` passes, then
   deactivate the old pair. The IAM policy of the pair covers only the
   campaign prefix and requires TLS.
7. The bucket keeps the 3-year archive (spec 07.5.7) via Object Lock or
   versioning plus a lifecycle transition. Deletion never comes from
   the runner: `s3sync.sh` never passes a delete flag.

## Pilot-phase notes

- SUT-6 (MI450) and SUT-7 (D270) carry `pilot: true`. Their results
  carry the PILOT flag (spec 01.3).
- SUT-7 driver install is manual until GA tooling exists. Record the
  driver source and version by hand.
- If `xpu-smi` is absent on SUT-7, metrics M16 to M19 report N/A
  (spec 06.6.4).
