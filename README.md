# mtv-vm-baselines

Source VM baseline verification and test coverage tracking for the Migration Toolkit for Virtualization (MTV).

## Features

- **VM Baseline Capture** -- Snapshot VM configurations (disks, NICs, firmware, guest agent state, MAC addresses, Windows security features) from one or more vCenters and persist them as version-controlled JSON baselines.
- **Shared Disk Detection** -- Automatically detect shared disks and capture partner VMs using efficient bulk vSphere PropertyCollector lookups.
- **Guest Runtime Data** -- Optionally power on VMs to collect guest network info via VMware Tools, then power them off (guest agent, DHCP detection).
- **DHCP Filtering** -- Silently skip DHCP, SLAAC, and link-local IPs during verification to avoid ephemeral address drift.
- **VM Baseline Verification** -- Compare live VM state against stored baselines to detect configuration drift before a migration test run.
- **Multi-vCenter Support** -- Operate across multiple vCenter endpoints in a single invocation.
- **Offline Baseline Diff** -- Compare two baseline files without connecting to vCenter.
- **Colored CLI Output** -- PASS in green, FAIL in red, warnings in yellow.
- **Test Coverage Verification** -- Parse `mtv-api-tests` configuration, generate a coverage manifest of expected checks, and diff it against the actual baseline to surface gaps.
- **Marker-Based Filtering** -- Filter coverage commands by pytest markers with AND logic for precise test selection.

## Installation

```bash
uv sync
```

## Usage

### Capture VM baselines

```bash
# Basic capture
mtv-vm-baselines vm capture \
  --vcenter vcsa.example.com \
  --user admin \
  --password secret \
  --vm rhel8-vm \
  --vm windows-vm

# Capture with guest network info (powers on VMs temporarily)
mtv-vm-baselines vm capture \
  --vcenter vcsa.example.com \
  --user admin \
  --vm rhel8-vm \
  --power-on-for-guest-info

# Options:
#   --vcenter TEXT             vCenter hostname or IP
#   --user TEXT                vCenter username (or VCENTER_USER env)
#   --password TEXT            vCenter password (or VCENTER_PASSWORD env, prompts if missing)
#   --vm TEXT                  VM names to capture (repeatable)
#   --port INTEGER             vCenter HTTPS port (default: 443)
#   --verify-ssl               Verify vCenter TLS certificate (default: false)
#   --output-dir PATH          Output directory for baseline files (default: baselines/vms)
#   --power-on-for-guest-info  Power on VMs to collect guest network info, then power off
#   --verbose, -v              Enable debug logging
#
# Exit codes:
#   0 = success (all VMs captured)
#   2 = partial failure (some VMs could not be captured)
#   3 = connection error
```

### Verify VMs against baselines

```bash
# Single vCenter
mtv-vm-baselines vm verify \
  --vcenter vcsa.example.com \
  --user admin \
  --password secret

# Multiple vCenters
mtv-vm-baselines vm verify \
  --vcenter vcsa1.example.com \
  --vcenter vcsa2.example.com \
  --user admin \
  --output-format junit

# Options:
#   --vcenter TEXT             vCenter hostname(s) or IP(s) (repeatable)
#   --user TEXT                vCenter username (or VCENTER_USER env)
#   --password TEXT            vCenter password (or VCENTER_PASSWORD env, prompts if missing)
#   --port INTEGER             vCenter HTTPS port (default: 443)
#   --verify-ssl               Verify vCenter TLS certificate (default: false)
#   --baselines-dir PATH       Directory with baseline JSON files (default: baselines/vms)
#   --output-format TEXT       Output format: text, json, junit (default: text)
#   --power-on-for-guest-info  Power on VMs to collect guest network info, then power off
#   --verbose, -v              Enable debug logging
#
# Exit codes:
#   0 = pass
#   1 = drift detected
#   2 = VM missing
#   3 = connection error
```

### Offline baseline diff

```bash
# Compare two baseline files
mtv-vm-baselines vm diff \
  baselines/vms/rhel8-vm.json \
  baselines/vms/rhel8-vm-updated.json \
  --output-format text

# Options:
#   --output-format TEXT  Output format: text, json (default: text)
#   --verbose, -v         Enable debug logging
#
# Exit codes:
#   0 = baselines match
#   1 = differences found
```

### Generate and verify test coverage

```bash
# Generate a coverage manifest from mtv-api-tests
mtv-vm-baselines coverage generate \
  --mtv-api-tests /path/to/mtv-api-tests \
  --output baselines/coverage/test-coverage-manifest.json \
  --commit-sha abc123

# Generate with marker filtering (AND logic)
mtv-vm-baselines coverage generate \
  --mtv-api-tests /path/to/mtv-api-tests \
  --marker tier0 \
  --marker warm

# Options:
#   --mtv-api-tests PATH  Path to mtv-api-tests repo root
#   --output PATH         Output manifest path (default: baselines/coverage/test-coverage-manifest.json)
#   --commit-sha TEXT     Git commit SHA to record
#   --marker, -m TEXT     Filter by pytest marker (AND logic, repeatable)
#   --verbose, -v         Enable debug logging

# Verify current coverage against a stored manifest
mtv-vm-baselines coverage verify \
  --mtv-api-tests /path/to/mtv-api-tests \
  --baseline baselines/coverage/test-coverage-manifest.json \
  --output-format text

# Verify with marker filtering
mtv-vm-baselines coverage verify \
  --mtv-api-tests /path/to/mtv-api-tests \
  --baseline baselines/coverage/test-coverage-manifest.json \
  --marker tier0 \
  --marker warm

# Options:
#   --mtv-api-tests PATH  Path to mtv-api-tests repo root
#   --baseline PATH       Path to stored coverage manifest
#   --output-format TEXT  Output format: text, json (default: text)
#   --marker, -m TEXT     Filter by pytest marker (AND logic, repeatable)
#   --verbose, -v         Enable debug logging
#
# Exit codes:
#   0 = no drift
#   1 = coverage lost
#   2 = coverage changed (review needed)
```

## License

Apache-2.0
