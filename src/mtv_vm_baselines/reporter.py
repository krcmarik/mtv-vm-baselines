"""Output formatters for baseline diffs and coverage reports."""

from __future__ import annotations

import json
import logging
from xml.etree.ElementTree import Element, SubElement, tostring

from mtv_vm_baselines.comparator import DiffEntry

logger = logging.getLogger(__name__)


class TextReporter:
    """Human-readable text output for baseline comparison results."""

    def report(self, vm_name: str, diffs: list[DiffEntry]) -> str:
        """Format diffs as readable text with PASS/FAIL header.

        Args:
            vm_name: Name of the VM that was compared.
            diffs: List of detected differences.

        Returns:
            Formatted text report string.
        """
        lines: list[str] = []

        if not diffs:
            lines.append(f"PASS: VM '{vm_name}' matches baseline")
            return "\n".join(lines)

        error_count = sum(1 for d in diffs if d.severity == "error")
        warning_count = sum(1 for d in diffs if d.severity == "warning")

        lines.append(f"FAIL: VM '{vm_name}' has {error_count} error(s), {warning_count} warning(s)")
        lines.append("")

        for diff in diffs:
            severity_tag = "ERROR" if diff.severity == "error" else "WARN "
            lines.append(f"  [{severity_tag}] {diff.path}")
            lines.append(f"    expected: {_format_value(diff.expected)}")
            lines.append(f"    actual:   {_format_value(diff.actual)}")
            lines.append("")

        return "\n".join(lines)


class JSONReporter:
    """Machine-readable JSON output for baseline comparison results."""

    def report(self, vm_name: str, diffs: list[DiffEntry]) -> str:
        """Format diffs as a JSON object.

        Args:
            vm_name: Name of the VM that was compared.
            diffs: List of detected differences.

        Returns:
            JSON string with comparison results.
        """
        result = {
            "vm_name": vm_name,
            "status": "pass" if not diffs else "fail",
            "error_count": sum(1 for d in diffs if d.severity == "error"),
            "warning_count": sum(1 for d in diffs if d.severity == "warning"),
            "diffs": [
                {
                    "path": d.path,
                    "expected": d.expected,
                    "actual": d.actual,
                    "severity": d.severity,
                }
                for d in diffs
            ],
        }
        return json.dumps(result, indent=2, default=str)


class JUnitReporter:
    """JUnit XML output for CI integration."""

    def report(self, results: dict[str, list[DiffEntry]]) -> str:
        """Format all VM results as a JUnit XML testsuite.

        Each VM becomes a testcase. VMs with diffs get failure elements.

        Args:
            results: Dict mapping VM name to its list of diffs.

        Returns:
            JUnit XML string.
        """
        total_tests = len(results)
        total_failures = sum(1 for diffs in results.values() if diffs)

        testsuite = Element("testsuite")
        testsuite.set("name", "vm-baseline-verification")
        testsuite.set("tests", str(total_tests))
        testsuite.set("failures", str(total_failures))

        for vm_name, diffs in sorted(results.items()):
            testcase = SubElement(testsuite, "testcase")
            testcase.set("name", vm_name)
            testcase.set("classname", "vm-baseline")

            if diffs:
                error_diffs = [d for d in diffs if d.severity == "error"]
                warning_diffs = [d for d in diffs if d.severity == "warning"]

                failure_lines: list[str] = []
                for diff in error_diffs:
                    failure_lines.append(
                        f"[ERROR] {diff.path}: expected={_format_value(diff.expected)}, "
                        f"actual={_format_value(diff.actual)}"
                    )
                for diff in warning_diffs:
                    failure_lines.append(
                        f"[WARN] {diff.path}: expected={_format_value(diff.expected)}, "
                        f"actual={_format_value(diff.actual)}"
                    )

                failure = SubElement(testcase, "failure")
                failure.set("message", f"{len(error_diffs)} error(s), {len(warning_diffs)} warning(s)")
                failure.text = "\n".join(failure_lines)

        return tostring(testsuite, encoding="unicode")


def _format_value(value: object) -> str:
    """Format a value for human-readable display.

    Args:
        value: Any value to format.

    Returns:
        String representation.
    """
    if value is None:
        return "<missing>"
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return str(value)
