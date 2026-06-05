"""CLI entry point for mtv-vm-baselines."""

from __future__ import annotations

import typer

app = typer.Typer(help="MTV source VM baseline verification and test coverage tracking.")

vm_app = typer.Typer(help="VM baseline operations.")
coverage_app = typer.Typer(help="Test coverage verification.")

app.add_typer(vm_app, name="vm")
app.add_typer(coverage_app, name="coverage")


@vm_app.command()
def capture() -> None:
    """Capture VM baselines from a vCenter."""
    raise NotImplementedError


@vm_app.command()
def verify() -> None:
    """Verify live VMs against stored baselines."""
    raise NotImplementedError


@vm_app.command()
def diff() -> None:
    """Offline diff of two baseline files."""
    raise NotImplementedError


@coverage_app.command()
def generate() -> None:
    """Generate test coverage manifest from mtv-api-tests."""
    raise NotImplementedError


@coverage_app.command("verify")
def coverage_verify() -> None:
    """Verify mtv-api-tests against stored coverage manifest."""
    raise NotImplementedError
