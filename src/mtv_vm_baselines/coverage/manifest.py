"""Generate and load test coverage manifests."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mtv_vm_baselines.coverage.check_gates import _effective_power_state, compute_expected_checks
from mtv_vm_baselines.coverage.parser import MtvTestParser

logger = logging.getLogger(__name__)

# Known feature flags that are tracked in the manifest
_FEATURE_FLAGS = (
    "warm_migration",
    "preserve_static_ips",
    "migrate_shared_disks",
    "target_power_state",
    "pvc_name_template",
    "pvc_name_template_use_generate_name",
    "target_labels",
    "target_affinity",
    "target_node_selector",
    "copyoffload",
    "pre_hook",
    "post_hook",
    "expected_migration_result",
    "vm_target_namespace",
    "multus_namespace",
    "guest_agent_timeout",
    "shared_disk_device",
)


def _extract_vm_names(config: dict[str, Any]) -> list[str]:
    """Extract VM names from a test configuration.

    Args:
        config: A ``tests_params`` entry.

    Returns:
        List of VM name strings.
    """
    vms: list[dict[str, Any]] = config.get("virtual_machines", [])
    return [vm.get("name", "<unknown>") for vm in vms]


def _extract_features(config: dict[str, Any]) -> dict[str, Any]:
    """Extract tracked feature flags from a test configuration.

    Only includes flags that are explicitly present in the config dict.

    Args:
        config: A ``tests_params`` entry.

    Returns:
        Dict of feature flag names to their values.
    """
    features: dict[str, Any] = {}
    for flag in _FEATURE_FLAGS:
        if flag in config:
            value = config[flag]
            # Convert complex objects to a stable representation
            if isinstance(value, dict):
                features[flag] = value
            elif isinstance(value, (list, tuple)):
                features[flag] = list(value)
            else:
                features[flag] = value
    return features


def _extract_vm_power_state(config: dict[str, Any]) -> str:
    """Determine the primary VM power state from config.

    Delegates to :func:`check_gates._effective_power_state` to keep the
    logic in a single place.

    Args:
        config: A ``tests_params`` entry.

    Returns:
        Power state string (``"on"`` or ``"off"``).
    """
    return _effective_power_state(config)


class CoverageManifest:
    """Generates and loads test coverage manifests.

    A coverage manifest is a JSON snapshot of the test coverage state at a
    point in time. It records which tests exist, what VMs they test, which
    pytest markers they carry, what features they exercise, and which
    post-migration checks are expected to run.
    """

    def generate(self, mtv_api_tests_path: Path, commit_sha: str = "") -> dict[str, Any]:
        """Generate a coverage manifest from the current mtv-api-tests state.

        Args:
            mtv_api_tests_path: Path to the mtv-api-tests repo root.
            commit_sha: Optional git commit SHA to record in the manifest.

        Returns:
            Manifest dict with the following structure::

                {
                    "manifest_version": "1.0",
                    "generated_from": "mtv-api-tests@<commit_sha>",
                    "tests": {
                        "test_name": {
                            "test_class": "ClassName",
                            "test_file": "tests/cold/test_cold.py",
                            "vms": ["vm-name-1"],
                            "markers": ["tier0", "vsphere"],
                            "vm_power_state": "on",
                            "features": {
                                "warm_migration": false,
                                ...
                            },
                            "expected_checks": ["cpu", "power_state", ...]
                        }
                    }
                }
        """
        parser = MtvTestParser(mtv_api_tests_path)
        inventory = parser.build_test_inventory()

        tests: dict[str, Any] = {}
        for test_name, entry in sorted(inventory.items()):
            config: dict[str, Any] = entry["config"]
            markers: list[str] = entry.get("markers", [])

            tests[test_name] = {
                "test_class": entry.get("class_name"),
                "test_file": entry.get("test_file"),
                "vms": _extract_vm_names(config),
                "markers": markers,
                "vm_power_state": _extract_vm_power_state(config),
                "features": _extract_features(config),
                "expected_checks": compute_expected_checks(config, markers),
            }

        return {
            "manifest_version": "1.0",
            "generated_from": f"mtv-api-tests@{commit_sha}" if commit_sha else "mtv-api-tests",
            "tests": tests,
        }

    def load(self, path: Path) -> dict[str, Any]:
        """Load a manifest from a JSON file.

        Args:
            path: Path to the manifest JSON file.

        Returns:
            The parsed manifest dict.

        Raises:
            FileNotFoundError: If the file does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        result: dict[str, Any] = json.loads(path.read_text())
        return result

    def save(self, manifest: dict[str, Any], path: Path) -> None:
        """Save a manifest to a JSON file.

        Creates parent directories if they do not exist.

        Args:
            manifest: The manifest dict to save.
            path: Destination file path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=False, default=str) + "\n")
