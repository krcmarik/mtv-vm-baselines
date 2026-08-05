"""Tests for DNS server comparison in baselines."""

from mtv_vm_baselines.comparator import BaselineComparator
from mtv_vm_baselines.models import (
    AdvancedConfig,
    BaselineMeta,
    GuestOS,
    GuestRuntime,
    Hardware,
    IPAddress,
    Network,
    NICIPConfig,
    Storage,
    VMBaseline,
)


def _make_baseline(ip_config: list[NICIPConfig]) -> VMBaseline:
    return VMBaseline(
        meta=BaselineMeta(vm_name="test-vm"),
        guest_os=GuestOS(guest_id="rhel8_64Guest", guest_full_name="RHEL 8", os_family="linuxGuest"),
        hardware=Hardware(hw_version="vmx-17", firmware="bios", num_cpu=2, memory_mb=4096),
        storage=Storage(),
        network=Network(),
        advanced=AdvancedConfig(),
        guest_runtime=GuestRuntime(ip_config=ip_config),
    )


class TestDnsServerComparison:
    def test_identical_dns_servers_no_diff(self) -> None:
        nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1", "8.8.8.8"],
        )
        baseline = _make_baseline([nic])
        live = _make_baseline([nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert not dns_diffs

    def test_dns_change_with_static_ip_is_error(self) -> None:
        baseline_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1"],
        )
        live_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.2"],
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert len(dns_diffs) == 1
        assert dns_diffs[0].severity == "error"

    def test_dns_change_with_dhcp_ip_is_warning(self) -> None:
        baseline_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="dhcp")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1"],
        )
        live_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="dhcp")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.2"],
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert len(dns_diffs) == 1
        assert dns_diffs[0].severity == "warning"

    def test_dns_empty_in_baseline_populated_in_live(self) -> None:
        baseline_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=[],
        )
        live_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1"],
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert len(dns_diffs) == 1
        assert dns_diffs[0].severity == "error"

    def test_dns_order_independent(self) -> None:
        baseline_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["8.8.8.8", "10.0.0.1"],
        )
        live_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1", "8.8.8.8"],
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert not dns_diffs

    def test_multi_nic_mixed_static_dhcp_dns(self) -> None:
        baseline_nics = [
            NICIPConfig(
                nic_label="Network adapter 1",
                ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
                gateway="10.0.0.1",
                dns_servers=["10.0.0.1"],
            ),
            NICIPConfig(
                nic_label="Network adapter 2",
                ip_addresses=[IPAddress(address="192.168.1.5", prefix_length=24, origin="dhcp")],
                gateway="",
                dns_servers=["192.168.1.1"],
            ),
        ]
        live_nics = [
            NICIPConfig(
                nic_label="Network adapter 1",
                ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
                gateway="10.0.0.1",
                dns_servers=["10.0.0.2"],
            ),
            NICIPConfig(
                nic_label="Network adapter 2",
                ip_addresses=[IPAddress(address="192.168.1.5", prefix_length=24, origin="dhcp")],
                gateway="",
                dns_servers=["192.168.1.2"],
            ),
        ]
        baseline = _make_baseline(baseline_nics)
        live = _make_baseline(live_nics)
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert len(dns_diffs) == 2
        static_diff = [d for d in dns_diffs if "Network adapter 1" in d.path]
        dhcp_diff = [d for d in dns_diffs if "Network adapter 2" in d.path]
        assert static_diff[0].severity == "error"
        assert dhcp_diff[0].severity == "warning"

    def test_dns_change_with_empty_origin_is_warning(self) -> None:
        """Empty origin (Linux can't distinguish static vs DHCP) should be treated as warning."""
        baseline_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1"],
        )
        live_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.2"],
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert len(dns_diffs) == 1
        assert dns_diffs[0].severity == "warning"

    def test_dns_populated_in_baseline_empty_in_live(self) -> None:
        baseline_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=["10.0.0.1", "8.8.8.8"],
        )
        live_nic = NICIPConfig(
            nic_label="Network adapter 1",
            ip_addresses=[IPAddress(address="10.0.0.5", prefix_length=24, origin="manual")],
            gateway="10.0.0.1",
            dns_servers=[],
        )
        baseline = _make_baseline([baseline_nic])
        live = _make_baseline([live_nic])
        diffs = BaselineComparator().compare(baseline, live)
        dns_diffs = [d for d in diffs if "dns_servers" in d.path]
        assert len(dns_diffs) == 1
        assert dns_diffs[0].severity == "error"
