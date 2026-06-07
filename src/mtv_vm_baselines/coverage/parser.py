"""Parse mtv-api-tests configuration to extract test parameters."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Markers that are not test-classification markers (filtered out during parsing)
_IGNORED_MARKERS = frozenset(
    {
        "parametrize",
        "usefixtures",
        "skipif",
        "incremental",
        "filterwarnings",
    }
)


def _extract_marker_name(decorator: ast.expr) -> str | None:
    """Extract the marker name from a ``pytest.mark.<name>`` decorator node.

    Handles both bare attributes (``@pytest.mark.tier0``) and calls
    (``@pytest.mark.skipif(...)``).

    Args:
        decorator: An AST expression node from a class decorator list.

    Returns:
        The marker name (e.g. ``"tier0"``) or ``None`` if not a pytest marker.
    """
    node = decorator
    if isinstance(node, ast.Call):
        node = node.func

    if not isinstance(node, ast.Attribute):
        return None

    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)

    parts.reverse()
    dotted = ".".join(parts)

    if not dotted.startswith("pytest.mark."):
        return None

    marker = dotted.removeprefix("pytest.mark.")
    if marker in _IGNORED_MARKERS:
        return None

    return marker


def _extract_config_key_from_source(source: str, decorator: ast.Call) -> str | None:
    """Extract the ``tests_params`` key from a ``@pytest.mark.parametrize`` call.

    Searches the source text span of the decorator for the pattern
    ``py_config["tests_params"]["<key>"]``.

    Args:
        source: Full source text of the file.
        decorator: The AST Call node for the parametrize decorator.

    Returns:
        The config key string, or ``None`` if not found.
    """
    # Pattern matches: tests_params"]["key"] or tests_params']['key']
    # The closing quote+bracket after tests_params, then opening bracket+quote for key
    config_key_pattern = re.compile(r'tests_params["\']?\]\s*\[\s*["\']([^"\']+)["\']')

    segment = ast.get_source_segment(source, decorator)
    if segment:
        match = config_key_pattern.search(segment)
        if match:
            return match.group(1)

    # Fallback: use line range to extract the source
    if hasattr(decorator, "lineno") and hasattr(decorator, "end_lineno"):
        lines = source.splitlines()
        start = decorator.lineno - 1
        end = decorator.end_lineno
        block = "\n".join(lines[start:end])
        match = config_key_pattern.search(block)
        if match:
            return match.group(1)

    return None


def _is_parametrize_call(decorator: ast.expr) -> bool:
    """Check if decorator is ``pytest.mark.parametrize(...)``.

    Args:
        decorator: An AST expression node.

    Returns:
        True if this is a parametrize call.
    """
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    return isinstance(func, ast.Attribute) and func.attr == "parametrize"


class MtvTestParser:
    """Parses the mtv-api-tests codebase to extract test configuration and metadata.

    This parser uses AST-based analysis to safely extract test parameters and
    class metadata without importing the mtv-api-tests modules (which would
    require live cluster dependencies).

    Args:
        mtv_api_tests_path: Path to the root of the mtv-api-tests repository.
    """

    def __init__(self, mtv_api_tests_path: Path) -> None:
        self.root = mtv_api_tests_path

    def parse_tests_params(self) -> dict[str, dict[str, Any]]:
        """Parse ``tests/tests_config/config.py`` to extract the ``tests_params`` dict.

        Uses AST parsing to safely extract the dict literal without importing the
        config module. The ``tests_params`` value is expected to be a plain dict
        literal (no function calls or variable references in values, except
        ``uuid.uuid4()`` calls in string f-strings which are replaced with
        placeholders).

        Returns:
            Dict mapping test name to its configuration dict.

        Raises:
            FileNotFoundError: If config.py does not exist.
            ValueError: If ``tests_params`` assignment cannot be found or parsed.
        """
        config_path = self.root / "tests" / "tests_config" / "config.py"
        if not config_path.is_file():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        source = config_path.read_text()
        tree = ast.parse(source, filename=str(config_path))

        # Find the tests_params assignment
        tests_params_node: ast.expr | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "tests_params" and node.value is not None:
                    tests_params_node = node.value
                    break
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "tests_params":
                        tests_params_node = node.value
                        break
                if tests_params_node is not None:
                    break

        if tests_params_node is None:
            raise ValueError("Could not find 'tests_params' assignment in config.py")

        # Extract the source text of the dict literal
        segment = ast.get_source_segment(source, tests_params_node)
        if segment is None:
            raise ValueError("Could not extract source segment for tests_params value")

        # Replace f-string expressions with placeholders so ast.literal_eval works.
        # Common pattern: f"mtv-vms-warm-comprehensive-{uuid.uuid4().hex[:4]}"
        # Replace the whole f-string with a regular string placeholder.
        segment = re.sub(r'f"([^"]*)\{[^}]+\}([^"]*)"', r'"\1<dynamic>\2"', segment)
        segment = re.sub(r"f'([^']*)\{[^}]+\}([^']*)'", r"'\1<dynamic>\2'", segment)

        try:
            result = ast.literal_eval(segment)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"Could not parse tests_params dict literal: {exc}") from exc

        if not isinstance(result, dict):
            raise ValueError(f"Expected tests_params to be a dict, got {type(result).__name__}")

        return result

    def parse_test_classes(self) -> dict[str, dict[str, Any]]:
        """Scan test files for test classes and extract metadata.

        For each test class decorated with ``@pytest.mark.parametrize`` using
        ``class_plan_config``, extracts:

        - ``class_name``: The Python class name.
        - ``test_file``: Relative path to the test file from the repo root.
        - ``markers``: List of pytest markers (tier0, warm, vsphere, etc.).
        - ``config_key``: The ``tests_params`` key referenced in the
          ``@pytest.mark.parametrize`` decorator.

        Returns:
            Dict mapping ``config_key`` to class metadata. If multiple classes
            reference the same config key, the last one wins (uncommon).
        """
        results: dict[str, dict[str, Any]] = {}
        tests_dir = self.root / "tests"

        if not tests_dir.is_dir():
            logger.warning("Tests directory not found: %s", tests_dir)
            return results

        for test_file in sorted(tests_dir.rglob("test_*.py")):
            try:
                source = test_file.read_text()
                tree = ast.parse(source, filename=str(test_file))
            except SyntaxError:
                logger.warning("Skipping file with syntax error: %s", test_file)
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not node.name.startswith("Test"):
                    continue

                markers: list[str] = []
                config_key: str | None = None

                for dec in node.decorator_list:
                    marker = _extract_marker_name(dec)
                    if marker is not None:
                        markers.append(marker)

                    if _is_parametrize_call(dec) and isinstance(dec, ast.Call):
                        key = _extract_config_key_from_source(source, dec)
                        if key is not None:
                            config_key = key

                if config_key is None:
                    logger.debug(
                        "Skipping class %s in %s: no config_key found",
                        node.name,
                        test_file,
                    )
                    continue

                relative_path = str(test_file.relative_to(self.root))

                results[config_key] = {
                    "class_name": node.name,
                    "test_file": relative_path,
                    "markers": sorted(markers),
                    "config_key": config_key,
                }

        return results

    def build_test_inventory(self) -> dict[str, dict[str, Any]]:
        """Combine ``tests_params`` and test class metadata into a unified inventory.

        Merges the configuration from ``tests/tests_config/config.py`` with the
        class-level metadata (markers, file path, class name) extracted from test
        files.

        Returns:
            Dict mapping test name to merged config and class metadata. Tests
            that exist in ``tests_params`` but have no corresponding test class
            are still included (with empty class metadata).
        """
        tests_params = self.parse_tests_params()
        test_classes = self.parse_test_classes()
        inventory: dict[str, dict[str, Any]] = {}

        # Start with all tests_params entries
        for test_name, config in tests_params.items():
            entry: dict[str, Any] = {"config": config}

            class_meta = test_classes.get(test_name)
            if class_meta is not None:
                entry["class_name"] = class_meta["class_name"]
                entry["test_file"] = class_meta["test_file"]
                entry["markers"] = class_meta["markers"]
            else:
                entry["class_name"] = None
                entry["test_file"] = None
                entry["markers"] = []

            inventory[test_name] = entry

        # Log any test classes that reference config keys not in tests_params
        for config_key, class_meta in test_classes.items():
            if config_key not in tests_params:
                logger.warning(
                    "Test class %s references config key '%s' not found in tests_params",
                    class_meta["class_name"],
                    config_key,
                )

        return inventory
