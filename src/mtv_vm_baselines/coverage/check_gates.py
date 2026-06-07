"""Compute expected verification checks from test configurations."""

from __future__ import annotations

from typing import Any

# Provider marker sets used to determine which checks apply
VSPHERE_MARKERS = frozenset({"vsphere", "esxi"})
RHV_MARKERS = frozenset({"rhv"})
OPENSTACK_MARKERS = frozenset({"openstack"})
OVA_MARKERS = frozenset({"ova"})
OCP_MARKERS = frozenset({"openshift"})

# All "real" source provider markers (have source VM stats)
_NON_OVA_PROVIDER_MARKERS = VSPHERE_MARKERS | RHV_MARKERS | OPENSTACK_MARKERS | OCP_MARKERS


def _has_guest_agent(config: dict[str, Any]) -> bool:
    """Check if any VM in the config has guest agent enabled.

    Args:
        config: The ``tests_params`` entry for a test.

    Returns:
        True if at least one VM has ``guest_agent: True``.
    """
    vms: list[dict[str, Any]] = config.get("virtual_machines", [])
    return any(vm.get("guest_agent") for vm in vms)


def _effective_power_state(config: dict[str, Any]) -> str:
    """Determine the effective VM power state after migration.

    If ``target_power_state`` is explicitly set, use it. Otherwise, infer from
    the first VM's ``source_vm_power`` (default ``"off"``).

    Args:
        config: The ``tests_params`` entry for a test.

    Returns:
        ``"on"`` or ``"off"``.
    """
    target = config.get("target_power_state")
    if target is not None:
        return str(target)

    vms: list[dict[str, Any]] = config.get("virtual_machines", [])
    if vms:
        return str(vms[0].get("source_vm_power", "off"))

    return "off"


def _marker_set(markers: list[str]) -> frozenset[str]:
    """Convert a markers list to a frozenset for set operations.

    Args:
        markers: List of pytest marker strings.

    Returns:
        Frozenset of marker strings.
    """
    return frozenset(markers)


def compute_expected_checks(
    config: dict[str, Any],
    markers: list[str],
) -> list[str]:
    """Compute which post-migration checks should run for a test.

    Encodes the gate logic from ``utilities/post_migration.py:check_vms()``
    and additional verification steps that run as separate test methods
    (e.g. ``shared_disk``).
    This is deterministic: same inputs always produce the same expected checks.

    The logic mirrors the check groups in ``check_vms()``:

    - **Group 1** (all providers): power_state, guest_agent, ssh_connectivity,
      node_selector, labels, affinity
    - **Group 2** (non-OVA): cpu, memory, network, storage, pvc_names
    - **Group 3** (vSphere-specific): serial
    - **Group 4** (RHV-specific): rhv_power_off_event
    - **Cross-provider**: ssl_config (vSphere, RHV, OpenStack)
    - **Separate step**: shared_disk (when ``migrate_shared_disks`` is set)

    Args:
        config: The ``tests_params`` entry for the test.
        markers: Pytest markers on the test class.

    Returns:
        Sorted list of expected check names.
    """
    checks: set[str] = set()
    mset = _marker_set(markers)
    has_non_ova = bool(mset & _NON_OVA_PROVIDER_MARKERS)
    has_vsphere = bool(mset & VSPHERE_MARKERS)
    has_rhv = bool(mset & RHV_MARKERS)
    has_openstack = bool(mset & OPENSTACK_MARKERS)
    power_on = _effective_power_state(config) == "on"
    guest_agent = _has_guest_agent(config)

    # Group 1: All providers — destination checks
    # power_state always runs
    checks.add("power_state")

    # guest_agent check runs when any VM has guest_agent: True
    if guest_agent:
        checks.add("guest_agent")

    # SSH connectivity: requires guest agent AND destination VM powered on
    if guest_agent and power_on:
        checks.add("ssh_connectivity")

        # Static IP and NIC name preservation: vSphere + preserve_static_ips + SSH
        if config.get("preserve_static_ips") and has_vsphere:
            checks.add("static_ip")
            checks.add("nic_name")

    # node_selector check
    if config.get("target_node_selector"):
        checks.add("node_selector")

    # labels check
    if config.get("target_labels"):
        checks.add("labels")

    # affinity check
    if config.get("target_affinity"):
        checks.add("affinity")

    # SSL configuration check: vSphere, RHV, OpenStack
    if has_vsphere or has_rhv or has_openstack:
        checks.add("ssl_config")

    # Group 2: Source-comparison checks — non-OVA providers only
    if has_non_ova:
        checks.add("cpu")
        checks.add("memory")
        checks.add("storage")

        # network check: non-OVA AND non-OCP providers.
        # In check_vms(), the guard is:
        #   source_provider.type != Provider.ProviderType.OPENSHIFT
        # So network runs for all non-OVA, non-OCP providers.
        # If test has both OCP and non-OCP markers, network still runs
        # because the non-OCP providers need it.
        non_ocp_non_ova = mset & (_NON_OVA_PROVIDER_MARKERS - OCP_MARKERS)
        if non_ocp_non_ova:
            checks.add("network")

        # pvc_names check
        if config.get("pvc_name_template"):
            checks.add("pvc_names")

    # OVA-only tests: only Group 1 checks apply (power_state, labels, affinity, etc.)
    # No cpu/memory/network/storage checks

    # Group 3: vSphere-specific checks
    if has_vsphere:
        checks.add("serial")

    # Group 4: RHV-specific checks
    if has_rhv:
        checks.add("rhv_power_off_event")

    # Separate step: shared disk verification
    if config.get("migrate_shared_disks"):
        checks.add("shared_disk")

    return sorted(checks)
