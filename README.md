# mtv-vm-baselines

Source VM baseline verification and test coverage tracking for the Migration Toolkit for Virtualization (MTV).

## Features

- **VM Baseline Capture** -- Snapshot VM configurations (disks, NICs, firmware, guest agent state) from one or more vCenters and persist them as version-controlled JSON baselines.
- **VM Baseline Verification** -- Compare live VM state against stored baselines to detect configuration drift before a migration test run.
- **Multi-vCenter Support** -- Operate across multiple vCenter endpoints in a single invocation.
- **Test Coverage Verification** -- Parse `mtv-api-tests` configuration, generate a coverage manifest of expected checks, and diff it against the actual baseline to surface gaps.

## Installation

```bash
uv sync
```

## Usage

### Capture VM baselines

```bash
mtv-vm-baselines vm capture --vcenter vcsa.example.com --username admin --password secret --output baselines/vms/
```

### Verify VMs against baselines

```bash
mtv-vm-baselines vm verify --vcenter vcsa.example.com --username admin --password secret --baseline baselines/vms/baseline.json
```

### Generate and verify test coverage

```bash
# Generate a coverage manifest from mtv-api-tests config
mtv-vm-baselines coverage generate --config-path ../mtv-api-tests/tests/tests_config/config.py

# Verify current coverage against a stored manifest
mtv-vm-baselines coverage verify --manifest baselines/coverage/manifest.json
```

## License

Apache-2.0
