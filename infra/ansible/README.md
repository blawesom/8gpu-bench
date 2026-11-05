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
roles/telemetry/            1 Hz power/thermal sampler
roles/env-record/           Emits node-meta.json (spec 07.4)
roles/smoke/                Smoke test (spec 03.4)
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

## Pilot-phase notes

- SUT-6 (MI450) and SUT-7 (D270) carry `pilot: true`. Their results
  carry the PILOT flag (spec 01.3).
- SUT-7 driver install is manual until GA tooling exists. Record the
  driver source and version by hand.
- If `xpu-smi` is absent on SUT-7, metrics M16 to M19 report N/A
  (spec 06.6.4).
