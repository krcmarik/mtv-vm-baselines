# MTV API Tests -- Tier0 and Upgrade Test Coverage

Internal QE reference document for the MTV (Migration Toolkit for Virtualization) API test suite.
Covers all tier0 test classes and the upgrade test class.

---

## 1. Executive Summary

The tier0 and upgrade test suites comprise **7 tier0 test classes** and **1 upgrade test class**, exercising the following MTV features:

- Cold migration (single VM, multi-provider)
- Warm migration with CBT-based incremental snapshots
- Comprehensive cold migration (static IPs, PVC naming, node selector, labels, affinity)
- Comprehensive warm migration (static IPs, PVC naming with generateName, labels, affinity, custom VM namespace)
- OVA cold migration (destination-only validation, labels, affinity)
- Pre/Post hook execution with VM retention on hook failure
- Shared disk migration with VM-level `migrateSharedDisks` overrides
- MTV operator upgrade with pre-created plan migration

**Source VMs used across all tests:** `mtv-tests-rhel8`, `mtv-win2022-ip-3disks`, `mtv-win2019-3disks`, `mtv-feature-shared-rhel1`, `mtv-feature-shared-rhel2`, `mtv-rhel8-warm-2disks2nics`

---

## 2. Source VM Inventory

### 2.1 VM Hardware Summary

| VM Name | OS | Firmware | vCPU | RAM | Disks | NICs | Special Features |
|---------|-----|----------|------|-----|-------|------|------------------|
| mtv-tests-rhel8 | RHEL 8.8 (kernel 4.18.0-477.10.1) | BIOS | 4 | 2 GB | 1x 16 GB thin (PVSCSI) | 3x VMXNET3 | CBT enabled, static IPs (ifcfg + NM keyfile), open-vm-tools 12.1.5 |
| mtv-win2022-ip-3disks | Windows Server 2019 (64-bit) [^1] | EFI | 2 | 2 GB | 3x thin on SATA/AHCI (16 + 2 + 1 GB) | 3x E1000e | CBT enabled, static IPs on cnv-test NIC |
| mtv-win2019-3disks | Windows Server 2019 | BIOS | 2 | 2 GB | 3 disks (LSI Logic SAS: 13 + 4 + 1 GB) | 2x E1000e | Static IP on VM Test Network NIC |
| mtv-feature-shared-rhel1 | RHEL 9.6 | BIOS | 1 | 2 GB | 3 disks (2x PVSCSI controllers, shared VMDK on controller 1) | 1x VMXNET3 | Shared disk pair -- owner (migrateSharedDisks=true) |
| mtv-feature-shared-rhel2 | RHEL 9.6 | BIOS | 1 | 2 GB | 3 disks (2x PVSCSI controllers, same shared VMDK) | 1x VMXNET3 | Shared disk pair -- consumer (migrateSharedDisks=false) |
| mtv-rhel8-warm-2disks2nics | RHEL 8.8 | BIOS | 1 | 2 GB | 2 disks (PVSCSI: 16 + 4 GB) | 2x VMXNET3 | CBT enabled, warm migration |

[^1]: The vSphere `guestId` is `windows2019srv_64Guest` but the VM name references "win2022". The guest OS reported by VMware Tools is "Microsoft Windows Server 2019 (64-bit)".

### 2.2 Network Configuration Detail

**mtv-tests-rhel8** (3 NICs on VM Network, VM Test Network, cnv-test):

| NIC | vSphere Network | Type | IP Configuration |
|-----|-----------------|------|------------------|
| eth0 | VM Network | VMXNET3 | DHCP |
| eth1 | VM Test Network | VMXNET3 | Static via ifcfg files (192.168.1.100/24, 192.168.1.101/24) |
| eth2 | cnv-test | VMXNET3 | Static via NetworkManager keyfile (192.168.2.100/24) |

**mtv-win2022-ip-3disks** (3 NICs on VM Network, VM Test Network, cnv-test):

| NIC | vSphere Network | Type | IP Configuration |
|-----|-----------------|------|------------------|
| NIC 1 | VM Network | E1000e | DHCP |
| NIC 2 | VM Test Network | E1000e | DHCP |
| NIC 3 | cnv-test | E1000e | Static Windows (10.2.122.67/23, 10.2.122.68/23) |

**mtv-win2019-3disks** (2 NICs on VM Network, VM Test Network):

| NIC | vSphere Network | Type | IP Configuration |
|-----|-----------------|------|------------------|
| NIC 1 | VM Network | E1000e | DHCP |
| NIC 2 | VM Test Network | E1000e | Static (192.168.190.101/24) |

---

## 3. Per-Test Coverage

### 3.1 TestSanityColdMtvMigration

| Field | Value |
|-------|-------|
| **Test file** | `tests/cold/test_mtv_cold_migration.py` |
| **Class name** | `TestSanityColdMtvMigration` |
| **Feature tested** | Basic cold migration -- smoke test for all providers |
| **Config entry** | `tests_params["test_sanity_cold_mtv_migration"]` |
| **Source VMs** | `mtv-tests-rhel8` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `vsphere`, `rhv`, `openstack`, `openshift`, `esxi`, `incremental` |
| **Provider coverage** | vSphere, RHV, OpenStack, OpenShift, ESXi |
| **Test flow** | Standard 5-step: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `False` |
| `guest_agent` | `True` |
| `source_vm_power` | not set (VM powered off before migration) |

**Notes:** The sanity cold test does not set `preserve_static_ips`, `target_labels`, `target_affinity`, `pvc_name_template`, or `target_power_state`. The destination VM power state defaults to matching the source power state before migration.

---

### 3.2 TestSanityWarmMtvMigration

| Field | Value |
|-------|-------|
| **Test file** | `tests/warm/test_mtv_warm_migration.py` |
| **Class name** | `TestSanityWarmMtvMigration` |
| **Feature tested** | Basic warm (incremental snapshot) migration with static IP preservation |
| **Config entry** | `tests_params["test_sanity_warm_mtv_migration"]` |
| **Source VMs** | `mtv-tests-rhel8` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `warm`, `upgrade`, `vsphere`, `rhv`, `incremental` |
| **Provider coverage** | vSphere, RHV |
| **Test flow** | Standard 5-step: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `True` |
| `preserve_static_ips` | `True` |
| `source_vm_power` | `"on"` |
| `guest_agent` | `True` |

**Additional fixtures:** `precopy_interval_forkliftcontroller` (sets snapshot interval on the ForkliftController CR).

**Notes:** Migration uses `cut_over=get_cutover_value()` for automatic cutover after precopy snapshots. The `preserve_static_ips` flag triggers the static IP preservation logic in the Plan CR.

---

### 3.3 TestColdMigrationComprehensive

| Field | Value |
|-------|-------|
| **Test file** | `tests/cold/test_cold_migration_comprehensive.py` |
| **Class name** | `TestColdMigrationComprehensive` |
| **Feature tested** | Cold migration with all Plan CR features: static IPs, PVC naming, node selector, labels, affinity, custom VM namespace |
| **Config entry** | `tests_params["test_cold_migration_comprehensive"]` |
| **Source VMs** | `mtv-win2019-3disks` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `vsphere`, `rhv`, `openstack`, `openshift`, `incremental` |
| **Provider coverage** | vSphere, RHV, OpenStack, OpenShift |
| **Test flow** | Standard 5-step: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `False` |
| `target_power_state` | `"on"` |
| `preserve_static_ips` | `True` |
| `pvc_name_template` | `"{{.VmName}}-disk-{{.DiskIndex}}"` |
| `pvc_name_template_use_generate_name` | `False` |
| `target_node_selector` | `{"mtv-comprehensive-node": <auto-uuid>}` |
| `target_labels` | `{"mtv-comprehensive-label": <auto-uuid>, "test-type": "comprehensive"}` |
| `target_affinity` | `podAffinity.preferredDuringSchedulingIgnoredDuringExecution` (weight 50, topologyKey `kubernetes.io/hostname`) |
| `vm_target_namespace` | `"mtv-vms-cold-comprehensive-<random>"` |
| `multus_namespace` | `"default"` (cross-namespace NAD access) |
| `guest_agent_timeout` | `600` |
| `source_vm_power` | `"on"` (required for VMware Tools to report static IP info) |
| `guest_agent` | `True` |

**Notes:** This test validates the largest combination of Plan CR features in a single test. The `labeled_worker_node` and `target_vm_labels` fixtures are consumed by both `test_create_plan` and `test_check_vms`. PVC name validation uses exact match (`generateName=False`).

---

### 3.4 TestWarmMigrationComprehensive

| Field | Value |
|-------|-------|
| **Test file** | `tests/warm/test_warm_migration_comprehensive.py` |
| **Class name** | `TestWarmMigrationComprehensive` |
| **Feature tested** | Warm migration with all Plan CR features: static IPs, PVC naming with generateName, labels, affinity, custom VM namespace |
| **Config entry** | `tests_params["test_warm_migration_comprehensive"]` |
| **Source VMs** | `mtv-win2022-ip-3disks` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `warm`, `vsphere`, `rhv`, `incremental` |
| **Provider coverage** | vSphere, RHV |
| **Test flow** | Standard 5-step: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `True` |
| `target_power_state` | `"on"` |
| `preserve_static_ips` | `True` |
| `pvc_name_template` | `'{{ .FileName \| trimSuffix ".vmdk" \| replace "_" "-" }}-{{.DiskIndex}}'` |
| `pvc_name_template_use_generate_name` | `True` |
| `target_labels` | `{"mtv-comprehensive-test": <auto-uuid>, "static-label": "static-value"}` |
| `target_affinity` | `podAffinity.preferredDuringSchedulingIgnoredDuringExecution` (weight 75, topologyKey `kubernetes.io/hostname`) |
| `vm_target_namespace` | `"mtv-vms-warm-comprehensive-<random>"` |
| `multus_namespace` | `"default"` (cross-namespace NAD access) |
| `guest_agent_timeout` | `600` |
| `source_vm_power` | `"on"` |
| `guest_agent` | `True` |

**Additional fixtures:** `precopy_interval_forkliftcontroller`.

**Notes:** This test uses `pvc_name_template_use_generate_name=True`, so PVC name validation uses prefix matching (Kubernetes appends a random suffix). The template uses Go/Sprig functions (`trimSuffix`, `replace`) to transform the VMDK filename. This is the only test exercising `{{.FileName}}` in the PVC name template.

---

### 3.5 TestOvaColdMigration

| Field | Value |
|-------|-------|
| **Test file** | `tests/cold/test_ova_cold_migration.py` |
| **Class name** | `TestOvaColdMigration` |
| **Feature tested** | OVA cold migration with destination-only validation, labels, and affinity |
| **Config entry** | `tests_params["test_ova_cold_migration"]` |
| **Source VMs** | `mtv-win2019-3disks` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `ova`, `incremental` |
| **Provider coverage** | OVA only |
| **Test flow** | Standard 5-step: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `False` |
| `target_power_state` | `"on"` |
| `target_labels` | `{"mtv-comprehensive-label": <auto-uuid>, "test-type": "comprehensive"}` |
| `target_affinity` | `podAffinity.preferredDuringSchedulingIgnoredDuringExecution` (weight 50, topologyKey `kubernetes.io/hostname`) |
| `guest_agent` | `True` |

**Notes:** OVA provider is a special case. The `check_vms()` function skips all source-comparison checks (cpu, memory, network, storage, PVC names) because OVA has no live source to compare against. Only destination-side checks run: power state, guest agent, labels, and affinity.

---

### 3.6 TestPostHookRetainFailedVm

| Field | Value |
|-------|-------|
| **Test file** | `tests/hooks/test_post_hook_retain_failed_vm.py` |
| **Class name** | `TestPostHookRetainFailedVm` |
| **Feature tested** | Pre/Post hook execution: PreHook succeeds, PostHook fails, VMs are retained |
| **Config entry** | `tests_params["test_post_hook_retain_failed_vm"]` |
| **Source VMs** | `mtv-tests-rhel8` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `vsphere`, `rhv`, `openstack`, `openshift`, `incremental` |
| **Provider coverage** | vSphere, RHV, OpenStack, OpenShift |
| **Test flow** | 5-step with expected failure: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` (expects `MigrationPlanExecError`) -> `check_vms` (conditional) |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `False` |
| `target_power_state` | `"off"` |
| `pre_hook` | `{"expected_result": "succeed"}` |
| `post_hook` | `{"expected_result": "fail"}` |
| `expected_migration_result` | `"fail"` |
| `source_vm_power` | `"on"` |
| `guest_agent` | `True` |

**Notes:** This test validates MTV's hook execution lifecycle. The Plan is created with both a PreHook and PostHook. Migration is expected to fail because the PostHook is designed to fail. The test validates that (a) the migration error is `MigrationPlanExecError`, (b) `validate_hook_failure_and_check_vms()` determines whether VMs should be checked, and (c) if VMs were migrated before the PostHook failed, they are retained and pass standard `check_vms()` validation. The `test_check_vms` step uses a runtime skip (`pytest.skip()`) if the hook failed before VM migration completed.

---

### 3.7 TestSharedDiskRhelMigration

| Field | Value |
|-------|-------|
| **Test file** | `tests/shared_disk/test_shared_disk_rhel_migration.py` |
| **Class name** | `TestSharedDiskRhelMigration` |
| **Feature tested** | Shared disk migration with VM-level `migrateSharedDisks` overrides (MTV-676) |
| **Config entry** | `tests_params["test_shared_disk_rhel_migration"]` |
| **Source VMs** | `mtv-feature-shared-rhel1` (owner), `mtv-feature-shared-rhel2` (consumer) (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `tier0`, `vsphere`, `shared_disk`, `incremental` |
| **Provider coverage** | vSphere only |
| **Test flow** | 6-step shared disk pattern: `create_storagemap` -> `create_networkmap` -> `create_plan` -> `migrate_vms` -> `verify_shared_disk_data` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `False` |
| `migrate_shared_disks` | `True` (plan-level) |
| `target_power_state` | `"on"` |
| `shared_disk_device` | `"/dev/vdc"` |
| VM 1 (`mtv-feature-shared-rhel1`) `migrate_shared_disks` | `True` (owner -- migrates the shared disk PVC) |
| VM 2 (`mtv-feature-shared-rhel2`) `migrate_shared_disks` | `False` (consumer -- skips shared disk) |
| `source_vm_power` (both VMs) | `"off"` |
| `guest_agent` (both VMs) | `True` |

**Notes:** This is the only test using the 6-step shared disk pattern. Both VMs are migrated in a single Plan. The `verify_shared_disk_data` step uses `verify_shared_disk_data()` from `utilities/shared_disk.py` to mount, write, and read a shared disk from both VMs, confirming bidirectional access after migration. The owner VM creates the shared PVC; the consumer VM attaches to it. Both VMs are powered off before migration to avoid conflicts.

---

### 3.8 TestUpgradeColdMigration

| Field | Value |
|-------|-------|
| **Test file** | `tests/upgrade/test_upgrade_migration.py` |
| **Class name** | `TestUpgradeColdMigration` |
| **Feature tested** | Cold migration with MTV operator upgrade between plan creation and execution |
| **Config entry** | `tests_params["test_upgrade_cold_migration"]` |
| **Source VMs** | `mtv-tests-rhel8` (see [VM Inventory](#2-source-vm-inventory)) |
| **Pytest markers** | `upgrade`, `vsphere`, `incremental` |
| **Provider coverage** | vSphere only |
| **Test flow** | Upgrade pattern: `upgrade_mtv` -> `verify_post_upgrade` -> `migrate_vms` -> `check_vms` |

**Key config flags:**

| Flag | Value |
|------|-------|
| `warm_migration` | `False` |
| `guest_agent` | `True` |

**Additional fixtures (from `tests/upgrade/conftest.py`):**

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `upgrade_script_path` | session | Clones `mtv-autodeploy` repo and returns path to upgrade script |
| `pre_upgrade_storage_map` | class | Creates StorageMap before upgrade |
| `pre_upgrade_network_map` | class | Creates NetworkMap before upgrade |
| `pre_upgrade_plan_resource` | class | Creates Plan CR before upgrade (via `create_plan_resource`) |

**Test flow detail:**

1. **Pre-upgrade setup** (via fixtures): StorageMap, NetworkMap, and Plan CR are created on the **current (pre-upgrade)** MTV version.
2. `test_upgrade_mtv`: Runs the upgrade script from the `mtv-autodeploy` repo. Requires `mtv_upgrade_to_version`, `mtv_upgrade_to_source`, and `mtv_upgrade_image_index` config values.
3. `test_verify_post_upgrade`: Verifies the MTV operator version matches the expected target version (major.minor comparison). Waits for the pre-created Plan CR to reach `Ready` condition after the upgrade (timeout: 300s).
4. `test_migrate_vms`: Executes migration using the pre-created Plan CR on the **upgraded** MTV operator.
5. `test_check_vms`: Standard post-migration validation.

**Notes:** This test does NOT have a `tier0` marker. It uses the `upgrade` marker only. The Plan is created **before** the upgrade and executed **after**, validating that MTV upgrades preserve existing Plan CRs.

---

## 4. Post-Migration Check Matrix

The `check_vms()` function in `utilities/post_migration.py` conditionally runs checks based on plan flags, provider type, and VM state. The table below shows which checks execute for each test.

### 4.1 Check Legend

| Check | Gate Condition |
|-------|----------------|
| `power_state` | Always runs |
| `guest_agent` | Runs when `guest_agent=True` in VM config |
| `ssh_connectivity` | Runs when `vm_ssh_connections` is provided AND destination VM `power_state == "on"` |
| `static_ip` | Runs when `preserve_static_ips=True` AND provider is vSphere AND destination VM is powered on |
| `nic_name` | Runs when `preserve_static_ips=True` AND provider is vSphere AND destination VM is powered on |
| `cpu` | Runs for non-OVA providers |
| `memory` | Runs for non-OVA providers |
| `network` | Runs for non-OVA, non-OpenShift providers |
| `storage` | Runs for non-OVA providers |
| `pvc_names` | Runs when `pvc_name_template` is set AND provider is non-OVA |
| `serial` | Runs for vSphere provider |
| `node_selector` | Runs when `target_node_selector` is set AND `labeled_worker_node` fixture provided |
| `labels` | Runs when `target_labels` is set AND `target_vm_labels` fixture provided |
| `affinity` | Runs when `target_affinity` is set in plan config |
| `ssl_config` | Runs for vSphere, RHV, OpenStack providers |
| `shared_disk` | Runs via separate `verify_shared_disk_data()` call, NOT inside `check_vms()` |
| `rhv_power_off_event` | Runs for RHV provider only |
| `data_integrity` | Runs via separate `verify_data_integrity()` call when data integrity marker was created pre-migration |

### 4.2 Check Matrix (vSphere provider)

The matrix below assumes vSphere as the source provider, which is the primary provider for all tests.

| Check | Cold Sanity | Warm Sanity | Cold Comp. | Warm Comp. | OVA | Hooks | Shared Disk | Upgrade |
|-------|:-----------:|:-----------:|:----------:|:----------:|:---:|:-----:|:-----------:|:-------:|
| `power_state` | Y | Y | Y | Y | Y | Y | Y | Y |
| `guest_agent` | Y | Y | Y | Y | Y | Y | Y | Y |
| `ssh_connectivity` | -- [^2] | Y | Y | Y | Y [^5] | -- [^3] | Y [^4] | -- [^2] |
| `static_ip` | -- | Y | Y | Y | -- | -- | -- | -- |
| `nic_name` | -- | Y | Y | Y | -- | -- | -- | -- |
| `cpu` | Y | Y | Y | Y | -- | Y | Y | Y |
| `memory` | Y | Y | Y | Y | -- | Y | Y | Y |
| `network` | Y | Y | Y | Y | -- | Y | Y | Y |
| `storage` | Y | Y | Y | Y | -- | Y | Y | Y |
| `pvc_names` | -- | -- | Y | Y | -- | -- | -- | -- |
| `serial` | Y | Y | Y | Y | -- | Y | Y | Y |
| `node_selector` | -- | -- | Y | -- | -- | -- | -- | -- |
| `labels` | -- | -- | Y | Y | Y | -- | -- | -- |
| `affinity` | -- | -- | Y | Y | Y | -- | -- | -- |
| `ssl_config` | Y | Y | Y | Y | -- | Y | Y | Y |
| `shared_disk` | -- | -- | -- | -- | -- | -- | Y | -- |
| `rhv_power_off_event` | -- | -- | -- | -- | -- | -- | -- | -- |

**Y** = check runs, **--** = check does not run.

[^2]: Cold sanity and upgrade tests do not set `source_vm_power`, so the VM is powered off before migration. The destination VM also powers off, and SSH connectivity requires `power_state == "on"`.

[^3]: Hooks test sets `target_power_state="off"`. The destination VM is powered off, so SSH connectivity is skipped.

[^4]: Shared disk test sets `target_power_state="on"` and `source_vm_power="off"`. After migration, VMs are powered on. SSH is used for both `check_vms()` connectivity check AND the separate `verify_shared_disk_data()` step.

[^5]: OVA test sets `target_power_state="on"` but SSH connectivity depends on `vm_ssh_connections` fixture availability and whether credentials are configured.

### 4.3 Check Matrix (RHV provider)

When running with RHV as the source provider (where applicable based on markers):

| Check | Cold Sanity | Warm Sanity | Cold Comp. | Hooks |
|-------|:-----------:|:-----------:|:----------:|:-----:|
| `power_state` | Y | Y | Y | Y |
| `guest_agent` | Y | Y | Y | Y |
| `ssh_connectivity` | -- | Y | Y | -- |
| `static_ip` | -- | -- | -- | -- |
| `nic_name` | -- | -- | -- | -- |
| `cpu` | Y | Y | Y | Y |
| `memory` | Y | Y | Y | Y |
| `network` | Y | Y | Y | Y |
| `storage` | Y | Y | Y | Y |
| `serial` | -- | -- | -- | -- |
| `ssl_config` | Y | Y | Y | Y |
| `rhv_power_off_event` | Y | Y | Y | Y |

**Notes on RHV:** Static IP and NIC name preservation checks are vSphere-only (gate: `source_provider.type == Provider.ProviderType.VSPHERE`). Serial preservation is also vSphere-only. RHV adds the `rhv_power_off_event` check to verify `USER_STOP_VM` (event code 33) was not triggered.

---

## 5. Test Execution Reference

### 5.1 Running Tests

```bash
# Run all tier0 tests
pytest -m tier0

# Run upgrade tests
pytest -m upgrade

# Run tier0 for a specific provider
pytest -m "tier0 and vsphere"
pytest -m "tier0 and rhv"

# Run warm tier0 tests only
pytest -m "tier0 and warm"

# Run shared disk tests
pytest -m shared_disk

# Run a specific test class
pytest tests/cold/test_mtv_cold_migration.py::TestSanityColdMtvMigration
pytest tests/upgrade/test_upgrade_migration.py::TestUpgradeColdMigration
```

### 5.2 Required Configuration

**For all tests:**

| Requirement | Details |
|-------------|---------|
| `.providers.json` | Provider connection details, credentials, VM inventories |
| OpenShift cluster | Live cluster with MTV operator installed |
| vCenter credentials | For vSphere tests: URL, username, password |
| Guest VM credentials | `guest_vm_linux_user`, `guest_vm_linux_password`, `guest_vm_win_user`, `guest_vm_win_password` in provider config |
| Multus/NAD | Network Attachment Definition configured on the cluster |

**For upgrade tests (additional):**

| Config Key | Purpose |
|------------|---------|
| `mtv_upgrade_to_version` | Target MTV version (e.g., `"2.10.0"`) |
| `mtv_upgrade_to_source` | CatalogSource for the target version |
| `mtv_upgrade_image_index` | Image index for the upgrade |
| `upgrade_repo_url` | URL of the mtv-autodeploy repository |
| `upgrade_repo_ref` | Git ref (branch/tag) to clone |
| `upgrade_script_path` | Relative path to the upgrade script within the repo |

### 5.3 Pytest Markers Reference

| Marker | Description | Used in Tier0/Upgrade |
|--------|-------------|----------------------|
| `tier0` | Core functionality / smoke tests | Yes -- 7 test classes |
| `upgrade` | MTV operator upgrade tests | Yes -- 1 test class; also on warm sanity |
| `warm` | Warm migration (CBT/incremental) | Warm sanity, warm comprehensive |
| `vsphere` | vSphere provider tests | All except OVA |
| `rhv` | RHV provider tests | Cold/warm sanity, comprehensive, hooks |
| `openstack` | OpenStack provider tests | Cold sanity, cold comprehensive, hooks |
| `openshift` | OpenShift provider tests | Cold sanity, cold comprehensive, hooks |
| `esxi` | ESXi provider tests | Cold sanity only |
| `ova` | OVA provider tests | OVA cold migration only |
| `shared_disk` | Shared disk tests | Shared disk migration only |
| `incremental` | Sequential test dependency within class | All test classes |
