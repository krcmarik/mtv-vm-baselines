"""Diff a coverage manifest against a stored baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Severity ordering for sorting (most severe first)
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True, slots=True)
class CoverageDiff:
    """A single difference detected between baseline and current manifest.

    Attributes:
        test_name: Name of the affected test.
        change_type: One of ``"removed"``, ``"added"``, ``"modified"``.
        severity: One of ``"critical"``, ``"warning"``, ``"info"``.
        description: Human-readable description of the change.
        details: Optional dict with structured change details.
    """

    test_name: str
    change_type: str
    severity: str
    description: str
    details: dict[str, Any] | None = field(default=None)


class CoverageComparator:
    """Compares a generated manifest against a stored baseline.

    Detects coverage regressions, additions, and modifications with
    appropriate severity levels:

    - **critical**: Test removed, feature flag removed, expected check lost
    - **warning**: VM name changed, marker removed
    - **info**: New test added, feature flag added, new marker added
    """

    def compare(self, baseline: dict[str, Any], current: dict[str, Any]) -> list[CoverageDiff]:
        """Compare two manifests and report differences.

        Args:
            baseline: The stored baseline manifest.
            current: The freshly generated manifest.

        Returns:
            List of :class:`CoverageDiff` entries sorted by severity
            (critical first), then by test name.
        """
        diffs: list[CoverageDiff] = []
        baseline_tests: dict[str, Any] = baseline.get("tests", {})
        current_tests: dict[str, Any] = current.get("tests", {})

        # Detect removed tests
        for test_name in sorted(baseline_tests):
            if test_name not in current_tests:
                diffs.append(
                    CoverageDiff(
                        test_name=test_name,
                        change_type="removed",
                        severity="critical",
                        description=f"Test '{test_name}' was removed from the test suite",
                        details={
                            "baseline_class": baseline_tests[test_name].get("test_class"),
                            "baseline_file": baseline_tests[test_name].get("test_file"),
                        },
                    )
                )

        # Detect added tests
        for test_name in sorted(current_tests):
            if test_name not in baseline_tests:
                diffs.append(
                    CoverageDiff(
                        test_name=test_name,
                        change_type="added",
                        severity="info",
                        description=f"New test '{test_name}' added to the test suite",
                        details={
                            "current_class": current_tests[test_name].get("test_class"),
                            "current_file": current_tests[test_name].get("test_file"),
                        },
                    )
                )

        # Detect modifications in tests that exist in both
        for test_name in sorted(baseline_tests):
            if test_name not in current_tests:
                continue

            base = baseline_tests[test_name]
            curr = current_tests[test_name]

            self._compare_markers(test_name, base, curr, diffs)
            self._compare_vms(test_name, base, curr, diffs)
            self._compare_features(test_name, base, curr, diffs)
            self._compare_expected_checks(test_name, base, curr, diffs)
            self._compare_power_state(test_name, base, curr, diffs)

        # Sort: severity first, then test name
        diffs.sort(key=lambda d: (_SEVERITY_ORDER.get(d.severity, 99), d.test_name))
        return diffs

    def _compare_markers(
        self,
        test_name: str,
        base: dict[str, Any],
        curr: dict[str, Any],
        diffs: list[CoverageDiff],
    ) -> None:
        """Compare markers between baseline and current.

        Args:
            test_name: Test name for diff attribution.
            base: Baseline test entry.
            curr: Current test entry.
            diffs: Accumulator list for diffs.
        """
        base_markers = set(base.get("markers", []))
        curr_markers = set(curr.get("markers", []))

        removed = base_markers - curr_markers
        added = curr_markers - base_markers

        if removed:
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="warning",
                    description=f"Marker(s) removed: {', '.join(sorted(removed))}",
                    details={"removed_markers": sorted(removed)},
                )
            )

        if added:
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="info",
                    description=f"Marker(s) added: {', '.join(sorted(added))}",
                    details={"added_markers": sorted(added)},
                )
            )

    def _compare_vms(
        self,
        test_name: str,
        base: dict[str, Any],
        curr: dict[str, Any],
        diffs: list[CoverageDiff],
    ) -> None:
        """Compare VM lists between baseline and current.

        Args:
            test_name: Test name for diff attribution.
            base: Baseline test entry.
            curr: Current test entry.
            diffs: Accumulator list for diffs.
        """
        base_vms = base.get("vms", [])
        curr_vms = curr.get("vms", [])

        if base_vms != curr_vms:
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="warning",
                    description=f"VM list changed: {base_vms} -> {curr_vms}",
                    details={"baseline_vms": base_vms, "current_vms": curr_vms},
                )
            )

    def _compare_features(
        self,
        test_name: str,
        base: dict[str, Any],
        curr: dict[str, Any],
        diffs: list[CoverageDiff],
    ) -> None:
        """Compare feature flags between baseline and current.

        Args:
            test_name: Test name for diff attribution.
            base: Baseline test entry.
            curr: Current test entry.
            diffs: Accumulator list for diffs.
        """
        base_features: dict[str, Any] = base.get("features", {})
        curr_features: dict[str, Any] = curr.get("features", {})

        removed_keys = set(base_features) - set(curr_features)
        added_keys = set(curr_features) - set(base_features)
        common_keys = set(base_features) & set(curr_features)

        for key in sorted(removed_keys):
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="critical",
                    description=f"Feature flag '{key}' removed (was: {base_features[key]!r})",
                    details={"removed_feature": key, "baseline_value": base_features[key]},
                )
            )

        for key in sorted(added_keys):
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="info",
                    description=f"Feature flag '{key}' added (value: {curr_features[key]!r})",
                    details={"added_feature": key, "current_value": curr_features[key]},
                )
            )

        for key in sorted(common_keys):
            if base_features[key] != curr_features[key]:
                diffs.append(
                    CoverageDiff(
                        test_name=test_name,
                        change_type="modified",
                        severity="warning",
                        description=(f"Feature flag '{key}' changed: {base_features[key]!r} -> {curr_features[key]!r}"),
                        details={
                            "feature": key,
                            "baseline_value": base_features[key],
                            "current_value": curr_features[key],
                        },
                    )
                )

    def _compare_expected_checks(
        self,
        test_name: str,
        base: dict[str, Any],
        curr: dict[str, Any],
        diffs: list[CoverageDiff],
    ) -> None:
        """Compare expected post-migration checks between baseline and current.

        Args:
            test_name: Test name for diff attribution.
            base: Baseline test entry.
            curr: Current test entry.
            diffs: Accumulator list for diffs.
        """
        base_checks = set(base.get("expected_checks", []))
        curr_checks = set(curr.get("expected_checks", []))

        lost = base_checks - curr_checks
        gained = curr_checks - base_checks

        if lost:
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="critical",
                    description=f"Expected check(s) lost: {', '.join(sorted(lost))}",
                    details={"lost_checks": sorted(lost)},
                )
            )

        if gained:
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="info",
                    description=f"Expected check(s) gained: {', '.join(sorted(gained))}",
                    details={"gained_checks": sorted(gained)},
                )
            )

    def _compare_power_state(
        self,
        test_name: str,
        base: dict[str, Any],
        curr: dict[str, Any],
        diffs: list[CoverageDiff],
    ) -> None:
        """Compare VM power state between baseline and current.

        Args:
            test_name: Test name for diff attribution.
            base: Baseline test entry.
            curr: Current test entry.
            diffs: Accumulator list for diffs.
        """
        base_power = base.get("vm_power_state")
        curr_power = curr.get("vm_power_state")

        if base_power != curr_power:
            diffs.append(
                CoverageDiff(
                    test_name=test_name,
                    change_type="modified",
                    severity="warning",
                    description=f"VM power state changed: '{base_power}' -> '{curr_power}'",
                    details={"baseline_power": base_power, "current_power": curr_power},
                )
            )

    def format_report(self, diffs: list[CoverageDiff]) -> str:
        """Format diffs as a human-readable report with severity sections.

        Args:
            diffs: List of coverage diffs, typically from :meth:`compare`.

        Returns:
            Multi-line string report grouped by severity.
        """
        if not diffs:
            return "No coverage differences detected."

        sections: dict[str, list[CoverageDiff]] = {
            "critical": [],
            "warning": [],
            "info": [],
        }
        for diff in diffs:
            sections.setdefault(diff.severity, []).append(diff)

        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Test Coverage Diff Report")
        lines.append("=" * 60)

        severity_headers = {
            "critical": "CRITICAL (coverage lost)",
            "warning": "WARNING (review needed)",
            "info": "INFO (coverage gained)",
        }

        for severity in ("critical", "warning", "info"):
            items = sections.get(severity, [])
            if not items:
                continue

            lines.append("")
            lines.append(f"--- {severity_headers[severity]} ---")
            for diff in items:
                lines.append(f"  [{diff.change_type.upper()}] {diff.test_name}")
                lines.append(f"    {diff.description}")

        lines.append("")
        lines.append("-" * 60)

        total = len(diffs)
        critical_count = len(sections.get("critical", []))
        warning_count = len(sections.get("warning", []))
        info_count = len(sections.get("info", []))
        lines.append(
            f"Total: {total} difference(s) ({critical_count} critical, {warning_count} warning, {info_count} info)"
        )

        return "\n".join(lines)
