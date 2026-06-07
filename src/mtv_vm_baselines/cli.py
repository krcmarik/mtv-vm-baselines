"""CLI entry point for mtv-vm-baselines."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from mtv_vm_baselines.collector import BaselineCollector
from mtv_vm_baselines.comparator import BaselineComparator, DiffEntry
from mtv_vm_baselines.coverage.comparator import CoverageComparator
from mtv_vm_baselines.coverage.manifest import CoverageManifest
from mtv_vm_baselines.models import VMBaseline
from mtv_vm_baselines.power_manager import PowerManager
from mtv_vm_baselines.reporter import JSONReporter, JUnitReporter, TextReporter
from mtv_vm_baselines.vsphere_client import VSphereClient

logger = logging.getLogger(__name__)

app = typer.Typer(help="MTV source VM baseline verification and test coverage tracking.")

vm_app = typer.Typer(help="VM baseline operations.")
coverage_app = typer.Typer(help="Test coverage verification.")

app.add_typer(vm_app, name="vm")
app.add_typer(coverage_app, name="coverage")


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging level based on verbosity flag.

    Args:
        verbose: If True, set DEBUG level. Otherwise, INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@vm_app.command()
def capture(
    vcenter: Annotated[str, typer.Option(help="vCenter hostname or IP")],
    user: Annotated[str, typer.Option(envvar="VCENTER_USER", help="vCenter username")],
    password: Annotated[
        str, typer.Option(envvar="VCENTER_PASSWORD", prompt=True, hide_input=True, help="vCenter password")
    ],
    vm_names: Annotated[list[str], typer.Option("--vm", help="VM names to capture")] = (),  # type: ignore[assignment]
    port: Annotated[int, typer.Option(help="vCenter HTTPS port")] = 443,
    verify_ssl: Annotated[bool, typer.Option(help="Verify vCenter TLS certificate")] = False,
    output_dir: Annotated[Path, typer.Option(help="Output directory for baseline files")] = Path("baselines/vms"),
    power_on_for_guest_info: Annotated[
        bool,
        typer.Option(
            "--power-on-for-guest-info",
            help="Power on VMs to collect guest network info via VMware Tools, then power off.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Capture VM configuration baselines from a vCenter."""
    _setup_logging(verbose)

    if not vm_names:
        typer.echo("Error: No VM names provided. Use --vm to specify VMs.", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with VSphereClient(host=vcenter, user=user, password=password, port=port, verify_ssl=verify_ssl) as client:
            collector = BaselineCollector(client=client)

            if power_on_for_guest_info:
                baselines: dict[str, VMBaseline] = {}
                with PowerManager() as pm:
                    for vm_name in vm_names:
                        try:
                            baselines[vm_name] = collector.capture_with_power_on(vm_name, pm)
                        except (ValueError, TimeoutError) as exc:
                            logger.warning(f"Skipping VM '{vm_name}': {exc}")

                # Detect shared disks when multiple VMs are captured
                if len(baselines) > 1:
                    baselines = collector.detect_shared_disks(baselines)
            else:
                baselines = collector.capture_multiple(vm_names)

                # Detect shared disks when multiple VMs are captured
                if len(baselines) > 1:
                    baselines = collector.detect_shared_disks(baselines)
    except ConnectionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=3) from None

    for name, baseline in baselines.items():
        output_file = output_dir / f"{name}.json"
        output_file.write_text(baseline.model_dump_json(indent=2))
        typer.echo(f"Saved baseline: {output_file}")

    failed = set(vm_names) - set(baselines.keys())
    if failed:
        typer.echo(f"Warning: Failed to capture {len(failed)} VM(s): {', '.join(sorted(failed))}", err=True)
        raise typer.Exit(code=2)


@vm_app.command()
def verify(
    vcenter: Annotated[list[str], typer.Option(help="vCenter hostname(s) or IP(s)")],
    user: Annotated[str, typer.Option(envvar="VCENTER_USER", help="vCenter username")],
    password: Annotated[
        str, typer.Option(envvar="VCENTER_PASSWORD", prompt=True, hide_input=True, help="vCenter password")
    ],
    port: Annotated[int, typer.Option(help="vCenter HTTPS port")] = 443,
    verify_ssl: Annotated[bool, typer.Option(help="Verify vCenter TLS certificate")] = False,
    baselines_dir: Annotated[Path, typer.Option(help="Directory with baseline JSON files")] = Path("baselines/vms"),
    output_format: Annotated[str, typer.Option(help="Output format: text, json, junit")] = "text",
    power_on_for_guest_info: Annotated[
        bool,
        typer.Option(
            "--power-on-for-guest-info",
            help="Power on VMs to collect guest network info via VMware Tools, then power off.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Verify live VM state against stored baselines.

    Exit codes: 0=pass, 1=drift detected, 2=VM missing, 3=connection error.
    """
    _setup_logging(verbose)

    if not baselines_dir.is_dir():
        typer.echo(f"Error: Baselines directory not found: {baselines_dir}", err=True)
        raise typer.Exit(code=1)

    # Load all baseline files
    baseline_files = sorted(baselines_dir.glob("*.json"))
    if not baseline_files:
        typer.echo(f"Error: No baseline files found in {baselines_dir}", err=True)
        raise typer.Exit(code=1)

    baselines: dict[str, VMBaseline] = {}
    for bf in baseline_files:
        baseline = VMBaseline.model_validate_json(bf.read_text())
        baselines[baseline.meta.vm_name] = baseline

    # Connect to vCenter(s) and capture live state.
    # When multiple vCenters are provided, verify each VM on ALL vCenters
    # where it exists. This detects per-vCenter drift (e.g., a VM that was
    # modified on one vCenter but not another).
    all_results: dict[str, list[DiffEntry]] = {}
    comparator = BaselineComparator()
    vms_found_on_any: set[str] = set()
    has_connection_error = False

    for vc_host in vcenter:
        try:
            with VSphereClient(host=vc_host, user=user, password=password, port=port, verify_ssl=verify_ssl) as client:
                collector = BaselineCollector(client=client)

                # Capture live state for all VMs on this vCenter first,
                # then run shared disk detection across them before comparing.
                live_baselines: dict[str, VMBaseline] = {}

                if power_on_for_guest_info:
                    with PowerManager() as pm:
                        for vm_name in baselines:
                            try:
                                live_baselines[vm_name] = collector.capture_with_power_on(vm_name, pm)
                                vms_found_on_any.add(vm_name)
                            except ValueError:
                                logger.debug(f"VM '{vm_name}' not found on vCenter '{vc_host}'")
                            except TimeoutError as exc:
                                logger.warning(f"Skipping VM '{vm_name}' on vCenter '{vc_host}': {exc}")
                else:
                    for vm_name in baselines:
                        try:
                            live_baselines[vm_name] = collector.capture(vm_name)
                            vms_found_on_any.add(vm_name)
                        except ValueError:
                            logger.debug(f"VM '{vm_name}' not found on vCenter '{vc_host}'")

                # Detect shared disks across all captured VMs on this vCenter
                if len(live_baselines) > 1:
                    live_baselines = collector.detect_shared_disks(live_baselines)

                # Compare each live baseline against the stored baseline
                for vm_name, live in live_baselines.items():
                    diffs = comparator.compare(baselines[vm_name], live)
                    # Use per-vCenter key when multiple vCenters are provided
                    result_key = f"{vm_name} ({vc_host})" if len(vcenter) > 1 else vm_name
                    all_results[result_key] = diffs
        except ConnectionError as exc:
            logger.error(f"Connection error for vCenter '{vc_host}': {exc}")
            has_connection_error = True

    # Check for VMs not found on any vCenter
    has_missing = False
    for vm_name in baselines:
        if vm_name not in vms_found_on_any:
            logger.error(f"VM '{vm_name}' not found on any vCenter")
            has_missing = True

    # Format and output results
    _output_results(all_results, output_format)

    # Determine exit code
    if has_connection_error and not all_results:
        raise typer.Exit(code=3)
    if has_missing:
        raise typer.Exit(code=2)
    if any(diffs for diffs in all_results.values()):
        raise typer.Exit(code=1)


@vm_app.command()
def diff(
    file_a: Annotated[Path, typer.Argument(help="First baseline file")],
    file_b: Annotated[Path, typer.Argument(help="Second baseline file")],
    output_format: Annotated[str, typer.Option(help="Output format: text, json")] = "text",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Offline diff of two baseline files."""
    _setup_logging(verbose)

    if not file_a.is_file():
        typer.echo(f"Error: File not found: {file_a}", err=True)
        raise typer.Exit(code=1)
    if not file_b.is_file():
        typer.echo(f"Error: File not found: {file_b}", err=True)
        raise typer.Exit(code=1)

    baseline_a = VMBaseline.model_validate_json(file_a.read_text())
    baseline_b = VMBaseline.model_validate_json(file_b.read_text())

    comparator = BaselineComparator()
    diffs = comparator.compare(baseline_a, baseline_b)

    vm_name = f"{baseline_a.meta.vm_name} vs {baseline_b.meta.vm_name}"

    reporter = JSONReporter() if output_format == "json" else TextReporter()
    typer.echo(reporter.report(vm_name, diffs))

    if diffs:
        raise typer.Exit(code=1)


@coverage_app.command()
def generate(
    mtv_api_tests: Annotated[Path, typer.Option(help="Path to mtv-api-tests repo root")],
    output: Annotated[Path, typer.Option(help="Output manifest path")] = Path(
        "baselines/coverage/test-coverage-manifest.json"
    ),
    commit_sha: Annotated[str, typer.Option(help="Git commit SHA to record")] = "",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Generate test coverage manifest from mtv-api-tests."""
    _setup_logging(verbose)

    if not mtv_api_tests.is_dir():
        typer.echo(f"Error: mtv-api-tests directory not found: {mtv_api_tests}", err=True)
        raise typer.Exit(code=1)

    manifest_mgr = CoverageManifest()

    try:
        manifest = manifest_mgr.generate(mtv_api_tests_path=mtv_api_tests, commit_sha=commit_sha)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error generating manifest: {exc}", err=True)
        raise typer.Exit(code=1) from None

    manifest_mgr.save(manifest, output)

    test_count = len(manifest.get("tests", {}))
    typer.echo(f"Generated manifest with {test_count} test(s): {output}")


@coverage_app.command("verify")
def coverage_verify(
    mtv_api_tests: Annotated[Path, typer.Option(help="Path to mtv-api-tests repo root")],
    baseline: Annotated[Path, typer.Option(help="Path to stored coverage manifest")],
    output_format: Annotated[str, typer.Option(help="Output format: text, json")] = "text",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Verify mtv-api-tests against stored coverage manifest.

    Exit codes: 0=no drift, 1=coverage lost, 2=coverage changed (review needed).
    """
    _setup_logging(verbose)

    if not mtv_api_tests.is_dir():
        typer.echo(f"Error: mtv-api-tests directory not found: {mtv_api_tests}", err=True)
        raise typer.Exit(code=1)
    if not baseline.is_file():
        typer.echo(f"Error: Baseline manifest not found: {baseline}", err=True)
        raise typer.Exit(code=1)

    manifest_mgr = CoverageManifest()

    try:
        baseline_manifest = manifest_mgr.load(baseline)
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        typer.echo(f"Error loading baseline: {exc}", err=True)
        raise typer.Exit(code=1) from None

    try:
        current_manifest = manifest_mgr.generate(mtv_api_tests_path=mtv_api_tests)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error generating current manifest: {exc}", err=True)
        raise typer.Exit(code=1) from None

    comparator = CoverageComparator()
    diffs = comparator.compare(baseline_manifest, current_manifest)

    if output_format == "json":
        diff_data = [
            {
                "test_name": d.test_name,
                "change_type": d.change_type,
                "severity": d.severity,
                "description": d.description,
                "details": d.details,
            }
            for d in diffs
        ]
        typer.echo(json.dumps(diff_data, indent=2, default=str))
    else:
        typer.echo(comparator.format_report(diffs))

    if not diffs:
        raise typer.Exit(code=0)

    has_critical = any(d.severity == "critical" for d in diffs)
    if has_critical:
        raise typer.Exit(code=1)

    raise typer.Exit(code=2)


def _output_results(results: dict[str, list[DiffEntry]], output_format: str) -> None:
    """Format and print verification results.

    Args:
        results: Dict mapping VM name to list of DiffEntry objects.
        output_format: One of "text", "json", "junit".
    """
    if output_format == "junit":
        reporter = JUnitReporter()
        typer.echo(reporter.report(results))
    elif output_format == "json":
        reporter_json = JSONReporter()
        all_reports = []
        for vm_name, diffs in sorted(results.items()):
            all_reports.append(json.loads(reporter_json.report(vm_name, diffs)))
        typer.echo(json.dumps(all_reports, indent=2, default=str))
    else:
        reporter_text = TextReporter()
        for vm_name, diffs in sorted(results.items()):
            typer.echo(reporter_text.report(vm_name, diffs))
            typer.echo("")  # blank line between VMs
