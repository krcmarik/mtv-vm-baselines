"""Tests for IP address comparison in BaselineComparator, focused on the
same-subnet tolerance applied to IPs with an empty ``origin``.
"""

from mtv_vm_baselines.comparator import BaselineComparator


class TestSameSubnetIpToleranceForEmptyOrigin:
    """Empty-origin IPs that move within the same subnet should pass silently."""

    def test_same_subnet_change_with_empty_origin_passes_silently(self) -> None:
        """Baseline IP changing to another IP in the same /24 with empty origin produces no diff."""
        expected = [{"address": "192.168.1.10", "prefix_length": 24, "origin": ""}]
        actual = [{"address": "192.168.1.20", "prefix_length": 24, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert not diffs

    def test_different_subnet_change_with_empty_origin_produces_two_errors(self) -> None:
        """Baseline IP changing to an IP in a different subnet still errors."""
        expected = [{"address": "192.168.1.10", "prefix_length": 24, "origin": ""}]
        actual = [{"address": "10.0.0.5", "prefix_length": 24, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert len(diffs) == 2
        assert all(d.severity == "error" for d in diffs)

    def test_same_subnet_change_with_manual_origin_still_errors(self) -> None:
        """Manual-origin IPs do not get the same-subnet tolerance."""
        expected = [{"address": "192.168.1.10", "prefix_length": 24, "origin": "manual"}]
        actual = [{"address": "192.168.1.20", "prefix_length": 24, "origin": "manual"}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert len(diffs) == 2
        assert all(d.severity == "error" for d in diffs)

    def test_exact_match_unaffected(self) -> None:
        """Identical IP addresses produce no diff, regardless of origin."""
        expected = [{"address": "192.168.1.10", "prefix_length": 24, "origin": ""}]
        actual = [{"address": "192.168.1.10", "prefix_length": 24, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert not diffs

    def test_multiple_missing_and_extra_pairs_matched_independently(self) -> None:
        """Each extra IP matches at most one missing IP within the same subnet."""
        expected = [
            {"address": "192.168.1.10", "prefix_length": 24, "origin": ""},
            {"address": "10.0.0.10", "prefix_length": 24, "origin": ""},
        ]
        actual = [
            {"address": "192.168.1.99", "prefix_length": 24, "origin": ""},
            {"address": "10.0.0.99", "prefix_length": 24, "origin": ""},
        ]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert not diffs

    def test_extra_ip_matches_only_one_missing_ip(self) -> None:
        """When two missing IPs share a subnet with one extra IP, only one pair is tolerated."""
        expected = [
            {"address": "192.168.1.10", "prefix_length": 24, "origin": ""},
            {"address": "192.168.1.11", "prefix_length": 24, "origin": ""},
        ]
        actual = [{"address": "192.168.1.99", "prefix_length": 24, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert len(diffs) == 1
        assert diffs[0].severity == "error"

    def test_same_subnet_change_with_prefix_length_divergence_still_errors(self) -> None:
        """A same-subnet match still errors on prefix_length if it changed."""
        expected = [{"address": "192.168.1.10", "prefix_length": 24, "origin": ""}]
        actual = [{"address": "192.168.1.20", "prefix_length": 16, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert len(diffs) == 1
        assert diffs[0].severity == "error"
        assert diffs[0].path == "guest_runtime.ip_config[nic_label=eth0].ip_addresses[address=192.168.1.10].prefix_length"
        assert diffs[0].expected == 24
        assert diffs[0].actual == 16

    def test_missing_prefix_length_does_not_crash(self) -> None:
        """A baseline IP without prefix_length falls back to a plain missing/extra error pair."""
        expected = [{"address": "192.168.1.10", "origin": ""}]
        actual = [{"address": "192.168.1.20", "prefix_length": 24, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert len(diffs) == 2
        assert all(d.severity == "error" for d in diffs)

    def test_ipv6_same_subnet_change_with_empty_origin_passes_silently(self) -> None:
        """IPv6 addresses within the same /64 with empty origin also get the tolerance."""
        expected = [{"address": "2001:db8::10", "prefix_length": 64, "origin": ""}]
        actual = [{"address": "2001:db8::20", "prefix_length": 64, "origin": ""}]

        diffs = BaselineComparator()._compare_ip_addresses(
            "guest_runtime.ip_config[nic_label=eth0]", expected, actual, "linuxGuest"
        )

        assert not diffs
