"""Tests for MAC address capture and comparison in baselines."""

from mtv_vm_baselines.comparator import BaselineComparator
from mtv_vm_baselines.models import (
    NIC,
    AdvancedConfig,
    BaselineMeta,
    GuestOS,
    Hardware,
    Network,
    Storage,
    VMBaseline,
)


def _make_baseline(nics: list[NIC]) -> VMBaseline:
    """Create a minimal VMBaseline with the given NICs.

    Args:
        nics: List of NIC models to include in the baseline.

    Returns:
        VMBaseline: A minimal baseline suitable for comparison tests.
    """
    return VMBaseline(
        meta=BaselineMeta(vm_name="test-vm"),
        guest_os=GuestOS(guest_id="rhel8_64Guest", guest_full_name="RHEL 8", os_family="linuxGuest"),
        hardware=Hardware(hw_version="vmx-17", firmware="bios", num_cpu=2, memory_mb=4096),
        storage=Storage(),
        network=Network(nics=nics),
        advanced=AdvancedConfig(),
    )


class TestMacAddressComparison:
    """Tests for MAC address field comparison in baselines."""

    def test_identical_mac_addresses_no_diff(self) -> None:
        """Identical MAC addresses produce no diff."""
        nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="manual",
        )
        baseline = _make_baseline([nic])
        live = _make_baseline([nic])

        diffs = BaselineComparator().compare(baseline, live)
        assert not diffs

    def test_mac_address_case_change_detected(self) -> None:
        """Case change in MAC address is detected as error."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="manual",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:aa:bb:cc",
            address_type="manual",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert len(mac_diffs) == 1
        assert mac_diffs[0].severity == "error"
        assert mac_diffs[0].expected == "00:50:56:AA:BB:CC"
        assert mac_diffs[0].actual == "00:50:56:aa:bb:cc"

    def test_mac_address_value_change_detected(self) -> None:
        """Different MAC address value is detected as error."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="manual",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:11:22:33",
            address_type="manual",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert len(mac_diffs) == 1
        assert mac_diffs[0].severity == "error"

    def test_address_type_change_detected(self) -> None:
        """Change in address_type (manual -> generated) is detected as error."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="manual",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="generated",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        type_diffs = [d for d in diffs if "address_type" in d.path]
        assert len(type_diffs) == 1
        assert type_diffs[0].severity == "error"

    def test_empty_mac_fields_backward_compatible(self) -> None:
        """Baselines without MAC fields (empty defaults) compare without error when both empty."""
        nic = NIC(label="Network adapter 1", adapter_type="VirtualVmxnet3", network_name="VM Network")
        baseline = _make_baseline([nic])
        live = _make_baseline([nic])

        diffs = BaselineComparator().compare(baseline, live)
        assert not diffs

    def test_old_baseline_vs_new_capture_detects_new_mac(self) -> None:
        """Old baseline (no MAC fields) vs new capture (with MAC) produces address_type diff only.

        When baseline address_type is empty (not "manual"), mac_address comparison
        is skipped. Only the address_type change is reported.
        """
        old_nic = NIC(label="Network adapter 1", adapter_type="VirtualVmxnet3", network_name="VM Network")
        new_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="manual",
        )
        baseline = _make_baseline([old_nic])
        live = _make_baseline([new_nic])

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path or "address_type" in d.path]
        assert len(mac_diffs) == 1  # Only address_type differs; mac_address skipped (baseline not manual)
        assert mac_diffs[0].path == "network.nics[label=Network adapter 1].address_type"

    def test_assigned_mac_change_ignored(self) -> None:
        """MAC address change is ignored when baseline address_type is 'assigned'."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="assigned",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:11:22:33",
            address_type="assigned",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert not mac_diffs

    def test_manual_mac_change_detected(self) -> None:
        """MAC address change is detected as error when baseline address_type is 'manual'."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="manual",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:11:22:33",
            address_type="manual",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert len(mac_diffs) == 1
        assert mac_diffs[0].severity == "error"
        assert mac_diffs[0].expected == "00:50:56:AA:BB:CC"
        assert mac_diffs[0].actual == "00:50:56:11:22:33"

    def test_generated_mac_change_ignored(self) -> None:
        """MAC address change is ignored when baseline address_type is 'generated'."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="generated",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:99:88:77",
            address_type="generated",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert not mac_diffs

    def test_network_name_change_produces_warning(self) -> None:
        """network_name change through _compare_network still uses warning severity."""
        baseline_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="VM Network",
            mac_address="00:50:56:AA:BB:CC",
            address_type="assigned",
        )
        live_nic = NIC(
            label="Network adapter 1",
            adapter_type="VirtualVmxnet3",
            network_name="Different Network",
            mac_address="00:50:56:11:22:33",
            address_type="assigned",
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])

        diffs = BaselineComparator().compare(baseline, live)
        name_diffs = [d for d in diffs if "network_name" in d.path]
        assert len(name_diffs) == 1
        assert name_diffs[0].severity == "warning"
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert not mac_diffs

    def test_mixed_address_types_multi_nic(self) -> None:
        """Manual NIC MAC change detected while assigned NIC MAC change ignored."""
        baseline_nics = [
            NIC(
                label="Network adapter 1",
                adapter_type="VirtualVmxnet3",
                network_name="VM Network",
                mac_address="00:50:56:AA:BB:CC",
                address_type="assigned",
            ),
            NIC(
                label="Network adapter 2",
                adapter_type="VirtualVmxnet3",
                network_name="cnv-test",
                mac_address="00:50:56:A6:C0:09",
                address_type="manual",
            ),
        ]
        live_nics = [
            NIC(
                label="Network adapter 1",
                adapter_type="VirtualVmxnet3",
                network_name="VM Network",
                mac_address="00:50:56:99:88:77",
                address_type="assigned",
            ),
            NIC(
                label="Network adapter 2",
                adapter_type="VirtualVmxnet3",
                network_name="cnv-test",
                mac_address="00:50:56:A6:D1:10",
                address_type="manual",
            ),
        ]
        baseline = _make_baseline(baseline_nics)
        live = _make_baseline(live_nics)

        diffs = BaselineComparator().compare(baseline, live)
        mac_diffs = [d for d in diffs if "mac_address" in d.path]
        assert len(mac_diffs) == 1
        assert "Network adapter 2" in mac_diffs[0].path
