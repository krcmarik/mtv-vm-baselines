"""Diff engine for comparing VM baselines."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from mtv_vm_baselines.models import VMBaseline

logger = logging.getLogger(__name__)

# Fields that should produce warnings instead of errors
_WARNING_FIELDS: set[str] = {
    "advanced.cbt_enabled",
    "network_name",
}

# Sections to skip during comparison (always different between captures)
_SKIP_SECTIONS: set[str] = {"meta"}

# Key fields used for matching list items by canonical path.
# Canonical path = dotted path with bracket-notation segments removed.
# E.g., "guest_runtime.ip_config[nic_label=eth0].ip_addresses" becomes
# "guest_runtime.ip_config.ip_addresses".
_LIST_KEY_FIELDS: dict[str, str] = {
    "storage.controllers": "bus_number",
    "storage.disks": "label",
    "network.nics": "label",
    "guest_runtime.ip_config": "nic_label",
    "guest_runtime.ip_config.ip_addresses": "address",
}


@dataclass
class DiffEntry:
    """A single difference between baseline and live VM state."""

    path: str  # e.g., "hardware.num_cpu"
    expected: Any  # baseline value
    actual: Any  # live value
    severity: str  # "error" or "warning"


_BRACKET_RE: re.Pattern[str] = re.compile(r"\[[^\]]*\]")


def _canonical_path(path: str) -> str:
    """Strip bracket-notation segments from a dotted path.

    Converts paths like "guest_runtime.ip_config[nic_label=eth0].ip_addresses"
    to "guest_runtime.ip_config.ip_addresses" so they match ``_LIST_KEY_FIELDS``
    entries regardless of which list item is currently being compared.

    Args:
        path: Dotted path possibly containing [key=value] segments.

    Returns:
        Path with all bracket segments removed and duplicate dots collapsed.
    """
    stripped = _BRACKET_RE.sub("", path)
    # Collapsing runs of dots that appear after bracket removal (e.g., "a..b" -> "a.b")
    while ".." in stripped:
        stripped = stripped.replace("..", ".")
    return stripped.strip(".")


class BaselineComparator:
    """Compares a live VM state against a stored baseline.

    Compares sections: guest_os, hardware, storage, network, advanced,
    guest_runtime, shared_disk_groups.
    Skips: meta (always different between captures).
    """

    def compare(self, baseline: VMBaseline, live: VMBaseline) -> list[DiffEntry]:
        """Compare all fields between a baseline and a live snapshot.

        Compares sections: guest_os, hardware, storage, network, advanced,
        guest_runtime, shared_disk_groups.
        Skips: meta (always different between captures).

        Args:
            baseline: The stored baseline to compare against.
            live: The live VM snapshot captured from vCenter.

        Returns:
            List of DiffEntry objects for each detected difference.
        """
        baseline_dict = baseline.model_dump()
        live_dict = live.model_dump()

        diffs: list[DiffEntry] = []

        for section_name, expected_val in baseline_dict.items():
            if section_name in _SKIP_SECTIONS:
                continue

            actual_val = live_dict.get(section_name)

            if section_name == "network":
                diffs.extend(self._compare_network(expected_val or {}, actual_val or {}))
                continue

            if section_name == "guest_runtime":
                diffs.extend(
                    self._compare_guest_runtime(
                        expected_val or {},
                        actual_val or {},
                        baseline.guest_os.os_family,
                    )
                )
                continue

            # Shared disk groups need special matching by participating_vms
            if section_name == "shared_disk_groups":
                diffs.extend(
                    self._compare_shared_disk_groups(
                        expected_val or [],
                        actual_val or [],
                    )
                )
                continue

            if isinstance(expected_val, dict):
                diffs.extend(self._compare_dicts(section_name, expected_val, actual_val or {}))
            elif isinstance(expected_val, list):
                key_field = _LIST_KEY_FIELDS.get(_canonical_path(section_name), "")
                diffs.extend(self._compare_lists(section_name, expected_val, actual_val or [], key_field))
            else:
                if expected_val != actual_val:
                    diffs.append(
                        DiffEntry(
                            path=section_name,
                            expected=expected_val,
                            actual=actual_val,
                            severity=_severity_for(section_name),
                        )
                    )

        return diffs

    def _compare_dicts(self, path: str, expected: dict[str, Any], actual: dict[str, Any]) -> list[DiffEntry]:
        """Recursively compare two dicts, producing diffs for mismatched values.

        Args:
            path: Dotted path prefix for diff entries.
            expected: Expected (baseline) dict.
            actual: Actual (live) dict.

        Returns:
            List of DiffEntry objects.
        """
        diffs: list[DiffEntry] = []

        all_keys = set(expected.keys()) | set(actual.keys())

        for key in sorted(all_keys):
            full_path = f"{path}.{key}"
            exp_val = expected.get(key)
            act_val = actual.get(key)

            if exp_val == act_val:
                continue

            if isinstance(exp_val, dict) and isinstance(act_val, dict):
                diffs.extend(self._compare_dicts(full_path, exp_val, act_val))
            elif isinstance(exp_val, list) and isinstance(act_val, list):
                key_field = _LIST_KEY_FIELDS.get(_canonical_path(full_path), "")
                diffs.extend(self._compare_lists(full_path, exp_val, act_val, key_field))
            else:
                diffs.append(
                    DiffEntry(
                        path=full_path,
                        expected=exp_val,
                        actual=act_val,
                        severity=_severity_for(full_path),
                    )
                )

        return diffs

    def _compare_network(self, expected: dict[str, Any], actual: dict[str, Any]) -> list[DiffEntry]:
        """Compare network section with MAC-address-type-aware logic.

        Only compares mac_address when the baseline address_type is "manual".
        For auto-assigned MACs (assigned, generated, or empty), the value can
        change freely between captures.

        Args:
            expected: Baseline network dict.
            actual: Live network dict.

        Returns:
            List of DiffEntry objects for detected differences.
        """
        diffs: list[DiffEntry] = []
        path = "network"

        exp_nics = expected.get("nics", [])
        act_nics = actual.get("nics", [])

        exp_by_label: dict[str, dict[str, Any]] = {nic["label"]: nic for nic in exp_nics}
        act_by_label: dict[str, dict[str, Any]] = {nic["label"]: nic for nic in act_nics}

        # Report missing NICs (in baseline but not live)
        for label in sorted(set(exp_by_label) - set(act_by_label)):
            diffs.append(
                DiffEntry(
                    path=f"{path}.nics[label={label}]",
                    expected=exp_by_label[label],
                    actual=None,
                    severity="error",
                )
            )

        # Report extra NICs (in live but not baseline)
        for label in sorted(set(act_by_label) - set(exp_by_label)):
            diffs.append(
                DiffEntry(
                    path=f"{path}.nics[label={label}]",
                    expected=None,
                    actual=act_by_label[label],
                    severity="error",
                )
            )

        # Compare matched NICs
        for label in sorted(set(exp_by_label) & set(act_by_label)):
            exp_nic = exp_by_label[label]
            act_nic = act_by_label[label]
            nic_path = f"{path}.nics[label={label}]"

            # Skip mac_address comparison when baseline address_type is not manual
            skip_fields: set[str] = set()
            if exp_nic.get("address_type") != "manual":
                skip_fields.add("mac_address")

            for key in sorted(set(exp_nic.keys()) | set(act_nic.keys())):
                if key == "label":  # key field, already matched
                    continue
                if key in skip_fields:
                    continue

                exp_val = exp_nic.get(key)
                act_val = act_nic.get(key)

                if exp_val != act_val:
                    diffs.append(
                        DiffEntry(
                            path=f"{nic_path}.{key}",
                            expected=exp_val,
                            actual=act_val,
                            severity=_severity_for(f"{nic_path}.{key}"),
                        )
                    )

        # Compare any non-NIC keys in the network section via the generic path
        non_nic_keys = (set(expected.keys()) | set(actual.keys())) - {"nics"}
        for key in sorted(non_nic_keys):
            full_path = f"{path}.{key}"
            exp_val = expected.get(key)
            act_val = actual.get(key)
            if exp_val != act_val:
                if isinstance(exp_val, dict) and isinstance(act_val, dict):
                    diffs.extend(self._compare_dicts(full_path, exp_val, act_val))
                else:
                    diffs.append(
                        DiffEntry(
                            path=full_path,
                            expected=exp_val,
                            actual=act_val,
                            severity=_severity_for(full_path),
                        )
                    )

        return diffs

    def _compare_shared_disk_groups(
        self,
        expected: list[dict[str, Any]],
        actual: list[dict[str, Any]],
    ) -> list[DiffEntry]:
        """Compare shared disk groups by matching on a composite key.

        Groups are matched by (sorted participating_vms, sorted scsi_positions)
        so that two VMs sharing multiple different disks are tracked as distinct
        groups.  The key combines the VM names with the SCSI bus:unit positions.

        Args:
            expected: Baseline shared disk groups (serialized dicts).
            actual: Live shared disk groups (serialized dicts).

        Returns:
            List of DiffEntry objects for mismatched groups.
        """
        diffs: list[DiffEntry] = []

        def _group_key(group: dict[str, Any]) -> str:
            """Create a stable key from sorted participating VMs and SCSI positions.

            Two groups with the same VMs but different SCSI positions (i.e.,
            different shared disks) produce different keys.
            """
            vms = sorted(group.get("participating_vms", []))
            positions = sorted(
                (p.get("vm", ""), p.get("bus", 0), p.get("unit", 0)) for p in group.get("scsi_positions", [])
            )
            pos_str = ";".join(f"{vm}:{bus}:{unit}" for vm, bus, unit in positions)
            return f"{','.join(vms)}|{pos_str}"

        def _display_key(group: dict[str, Any]) -> str:
            """Create a human-readable key for diff path display."""
            vms = sorted(group.get("participating_vms", []))
            return ",".join(vms)

        expected_by_key = {_group_key(g): g for g in expected}
        actual_by_key = {_group_key(g): g for g in actual}

        # Groups in baseline but not live
        for key in sorted(set(expected_by_key.keys()) - set(actual_by_key.keys())):
            group = expected_by_key[key]
            diffs.append(
                DiffEntry(
                    path=f"shared_disk_groups[vms={_display_key(group)}]",
                    expected=group,
                    actual=None,
                    severity="error",
                )
            )

        # Groups in live but not baseline
        for key in sorted(set(actual_by_key.keys()) - set(expected_by_key.keys())):
            group = actual_by_key[key]
            diffs.append(
                DiffEntry(
                    path=f"shared_disk_groups[vms={_display_key(group)}]",
                    expected=None,
                    actual=group,
                    severity="error",
                )
            )

        # Compare matched groups (SCSI positions within each group)
        for key in sorted(set(expected_by_key.keys()) & set(actual_by_key.keys())):
            exp_group = expected_by_key[key]
            act_group = actual_by_key[key]

            exp_positions = exp_group.get("scsi_positions", [])
            act_positions = act_group.get("scsi_positions", [])

            # Sort positions by (vm, bus, unit) for stable comparison
            exp_sorted = sorted(exp_positions, key=lambda p: (p.get("vm", ""), p.get("bus", 0), p.get("unit", 0)))
            act_sorted = sorted(act_positions, key=lambda p: (p.get("vm", ""), p.get("bus", 0), p.get("unit", 0)))

            if exp_sorted != act_sorted:
                diffs.append(
                    DiffEntry(
                        path=f"shared_disk_groups[vms={_display_key(exp_group)}].scsi_positions",
                        expected=exp_sorted,
                        actual=act_sorted,
                        severity="error",
                    )
                )

        return diffs

    def _compare_guest_runtime(
        self,
        expected: dict[str, Any],
        actual: dict[str, Any],
        os_family: str,
    ) -> list[DiffEntry]:
        """Compare guest_runtime with OS-family-aware IP severity.

        Args:
            expected: Baseline guest_runtime dict.
            actual: Live guest_runtime dict.
            os_family: OS family string (e.g., "windowsGuest", "linuxGuest").

        Returns:
            List of DiffEntry objects with appropriate severity.
        """
        diffs: list[DiffEntry] = []

        exp_nics = expected.get("ip_config", [])
        act_nics = actual.get("ip_config", [])

        exp_by_label = {nic["nic_label"]: nic for nic in exp_nics}
        act_by_label = {nic["nic_label"]: nic for nic in act_nics}

        for label in sorted(set(exp_by_label) - set(act_by_label)):
            diffs.append(
                DiffEntry(
                    path=f"guest_runtime.ip_config[nic_label={label}]",
                    expected=exp_by_label[label],
                    actual=None,
                    severity="error",
                )
            )

        for label in sorted(set(act_by_label) - set(exp_by_label)):
            diffs.append(
                DiffEntry(
                    path=f"guest_runtime.ip_config[nic_label={label}]",
                    expected=None,
                    actual=act_by_label[label],
                    severity="error",
                )
            )

        for label in sorted(set(exp_by_label) & set(act_by_label)):
            exp_nic = exp_by_label[label]
            act_nic = act_by_label[label]
            nic_path = f"guest_runtime.ip_config[nic_label={label}]"

            if exp_nic.get("gateway") != act_nic.get("gateway"):
                diffs.append(
                    DiffEntry(
                        path=f"{nic_path}.gateway",
                        expected=exp_nic.get("gateway"),
                        actual=act_nic.get("gateway"),
                        severity="warning",
                    )
                )

            diffs.extend(
                self._compare_ip_addresses(
                    nic_path,
                    exp_nic.get("ip_addresses", []),
                    act_nic.get("ip_addresses", []),
                    os_family,
                )
            )

        return diffs

    def _compare_ip_addresses(
        self,
        nic_path: str,
        expected: list[dict[str, Any]],
        actual: list[dict[str, Any]],
        os_family: str,
    ) -> list[DiffEntry]:
        """Compare IP addresses with OS-family-aware severity.

        Args:
            nic_path: Path prefix for diff entries.
            expected: Baseline IP address dicts.
            actual: Live IP address dicts.
            os_family: OS family string.

        Returns:
            List of DiffEntry objects.
        """
        diffs: list[DiffEntry] = []
        ip_path = f"{nic_path}.ip_addresses"

        exp_by_addr = {ip["address"]: ip for ip in expected}
        act_by_addr = {ip["address"]: ip for ip in actual}

        missing = sorted(set(exp_by_addr) - set(act_by_addr))
        extra = sorted(set(act_by_addr) - set(exp_by_addr))

        is_windows = os_family == "windowsGuest"

        if is_windows:
            for addr in missing:
                origin = exp_by_addr[addr].get("origin", "")
                severity = "warning" if origin in ("dhcp", "linklayer", "random") else "error"
                diffs.append(
                    DiffEntry(
                        path=f"{ip_path}[address={addr}]",
                        expected=exp_by_addr[addr],
                        actual=None,
                        severity=severity,
                    )
                )
            for addr in extra:
                origin = act_by_addr[addr].get("origin", "")
                severity = "warning" if origin in ("dhcp", "linklayer", "random") else "error"
                diffs.append(
                    DiffEntry(
                        path=f"{ip_path}[address={addr}]",
                        expected=None,
                        actual=act_by_addr[addr],
                        severity=severity,
                    )
                )
        else:
            if len(expected) != len(actual):
                diffs.append(
                    DiffEntry(
                        path=ip_path,
                        expected=f"{len(expected)} address(es)",
                        actual=f"{len(actual)} address(es)",
                        severity="error",
                    )
                )
            for addr in missing:
                diffs.append(
                    DiffEntry(
                        path=f"{ip_path}[address={addr}]",
                        expected=exp_by_addr[addr],
                        actual=None,
                        severity="warning",
                    )
                )
            for addr in extra:
                diffs.append(
                    DiffEntry(
                        path=f"{ip_path}[address={addr}]",
                        expected=None,
                        actual=act_by_addr[addr],
                        severity="warning",
                    )
                )

        for addr in sorted(set(exp_by_addr) & set(act_by_addr)):
            exp_ip = exp_by_addr[addr]
            act_ip = act_by_addr[addr]
            if exp_ip.get("prefix_length") != act_ip.get("prefix_length"):
                diffs.append(
                    DiffEntry(
                        path=f"{ip_path}[address={addr}].prefix_length",
                        expected=exp_ip.get("prefix_length"),
                        actual=act_ip.get("prefix_length"),
                        severity="error",
                    )
                )

        return diffs

    def _compare_lists(
        self,
        path: str,
        expected: list[Any],
        actual: list[Any],
        key_field: str,
    ) -> list[DiffEntry]:
        """Compare two lists, matching items by key_field when available.

        If key_field is empty or items are not dicts, falls back to
        index-based comparison.

        Args:
            path: Dotted path prefix for diff entries.
            expected: Expected (baseline) list.
            actual: Actual (live) list.
            key_field: Field name to use as a stable key for matching items.

        Returns:
            List of DiffEntry objects.
        """
        diffs: list[DiffEntry] = []

        # If items are dicts and we have a key field, match by key
        if key_field and expected and isinstance(expected[0], dict):
            expected_by_key = {item[key_field]: item for item in expected if key_field in item}
            actual_by_key = {item[key_field]: item for item in actual if key_field in item}

            # Report missing items (in baseline but not live)
            for key_val in sorted(set(expected_by_key.keys()) - set(actual_by_key.keys()), key=str):
                diffs.append(
                    DiffEntry(
                        path=f"{path}[{key_field}={key_val}]",
                        expected=expected_by_key[key_val],
                        actual=None,
                        severity="error",
                    )
                )

            # Report extra items (in live but not baseline)
            for key_val in sorted(set(actual_by_key.keys()) - set(expected_by_key.keys()), key=str):
                diffs.append(
                    DiffEntry(
                        path=f"{path}[{key_field}={key_val}]",
                        expected=None,
                        actual=actual_by_key[key_val],
                        severity="error",
                    )
                )

            # Compare matched items
            for key_val in sorted(set(expected_by_key.keys()) & set(actual_by_key.keys()), key=str):
                item_path = f"{path}[{key_field}={key_val}]"
                diffs.extend(self._compare_dicts(item_path, expected_by_key[key_val], actual_by_key[key_val]))
        else:
            # Fall back to index-based comparison
            max_len = max(len(expected), len(actual))
            for i in range(max_len):
                item_path = f"{path}[{i}]"
                if i >= len(expected):
                    diffs.append(
                        DiffEntry(
                            path=item_path,
                            expected=None,
                            actual=actual[i],
                            severity="error",
                        )
                    )
                elif i >= len(actual):
                    diffs.append(
                        DiffEntry(
                            path=item_path,
                            expected=expected[i],
                            actual=None,
                            severity="error",
                        )
                    )
                elif isinstance(expected[i], dict) and isinstance(actual[i], dict):
                    diffs.extend(self._compare_dicts(item_path, expected[i], actual[i]))
                elif expected[i] != actual[i]:
                    diffs.append(
                        DiffEntry(
                            path=item_path,
                            expected=expected[i],
                            actual=actual[i],
                            severity=_severity_for(item_path),
                        )
                    )

        return diffs


def _severity_for(path: str) -> str:
    """Determine the severity level for a given field path.

    Args:
        path: Dotted path to the field.

    Returns:
        "warning" for known warning-level fields, "error" otherwise.
    """
    for warning_path in _WARNING_FIELDS:
        if path == warning_path or path.endswith(f".{warning_path}"):
            return "warning"
    return "error"
