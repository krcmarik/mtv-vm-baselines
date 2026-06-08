"""Safe VM power state management with automatic restore."""

from __future__ import annotations

import logging
import time
from typing import Any

from pyVmomi import vim

logger = logging.getLogger(__name__)


class PowerManager:
    """Tracks VMs powered on during capture and restores their original state.

    Use as a context manager to ensure VMs are powered off on exit.
    """

    def __init__(self) -> None:
        self._powered_on: list[vim.VirtualMachine] = []

    def __enter__(self) -> PowerManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.restore_all()

    def power_on(self, vm: vim.VirtualMachine, timeout: int = 120) -> None:
        """Power on a VM if it is not already running.

        Waits for VMware Tools to report as running before returning.

        Args:
            vm: The vSphere VM managed object.
            timeout: Seconds to wait for VMware Tools readiness.

        Raises:
            TimeoutError: If VMware Tools does not become ready within timeout.
        """
        power_state = str(vm.runtime.powerState)
        if power_state == "poweredOn":
            logger.debug(f"VM '{vm.name}' is already powered on")
            return

        logger.info(f"Powering on VM '{vm.name}'")
        task = vm.PowerOnVM_Task()
        self._wait_for_task(task, f"PowerOn '{vm.name}'")
        self._powered_on.append(vm)

        self._wait_for_tools(vm, timeout)

    def power_off(self, vm: vim.VirtualMachine, timeout: int = 60) -> None:
        """Power off a VM using graceful shutdown with hard power-off fallback.

        Attempts guest shutdown first (requires VMware Tools). Falls back
        to hard power-off if graceful shutdown fails or times out.

        Args:
            vm: The vSphere VM managed object.
            timeout: Seconds to wait for graceful shutdown.
        """
        power_state = str(vm.runtime.powerState)
        if power_state != "poweredOn":
            logger.debug(f"VM '{vm.name}' is already powered off")
            return

        try:
            logger.info(f"Shutting down VM '{vm.name}' (graceful)")
            vm.ShutdownGuest()

            deadline = time.time() + timeout
            while time.time() < deadline:
                if str(vm.runtime.powerState) != "poweredOn":
                    logger.info(f"VM '{vm.name}' shut down gracefully")
                    return
                time.sleep(5)

            logger.warning(
                f"Graceful shutdown timed out for VM '{vm.name}', using hard power-off",
                exc_info=True,
            )
        except Exception:
            logger.warning(
                f"Graceful shutdown failed for VM '{vm.name}', using hard power-off",
                exc_info=True,
            )

        task = vm.PowerOffVM_Task()
        self._wait_for_task(task, f"PowerOff '{vm.name}'")
        logger.info(f"VM '{vm.name}' powered off (hard)")

    def restore_all(self) -> None:
        """Power off all VMs that were powered on by this manager."""
        for vm in reversed(self._powered_on):
            try:
                self.power_off(vm)
            except Exception:
                logger.error(f"Failed to restore power state for VM '{vm.name}'", exc_info=True)
        self._powered_on.clear()

    def _wait_for_tools(self, vm: vim.VirtualMachine, timeout: int) -> None:
        """Wait for VMware Tools and guest networking to initialize.

        Waits for VMware Tools to report as running, then waits for guest
        networking to populate with at least one NIC that has IP addresses.
        The guest networking wait is best-effort and will not raise on timeout.

        Args:
            vm: The vSphere VM managed object.
            timeout: Seconds to wait.

        Raises:
            TimeoutError: If Tools do not become ready within timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            tools_status = str(vm.guest.toolsRunningStatus)
            if tools_status == "guestToolsRunning":
                logger.debug(f"VMware Tools running on VM '{vm.name}'")
                break
            time.sleep(5)
        else:
            raise TimeoutError(f"VMware Tools did not become ready on VM '{vm.name}' within {timeout}s")

        # Tools reports running before guest networking is fully initialized
        while time.time() < deadline:
            if self._has_guest_net_ips(vm):
                nics_with_ips = sum(1 for nic in vm.guest.net if nic.ipAddress)
                logger.debug(f"Guest networking ready on VM '{vm.name}': {nics_with_ips} NIC(s) with IPs")
                return
            time.sleep(5)

        logger.warning(f"Guest networking did not populate on VM '{vm.name}' within remaining timeout")

    @staticmethod
    def _has_guest_net_ips(vm: vim.VirtualMachine) -> bool:
        """Check if the VM has at least one NIC with IP addresses in guest info.

        Args:
            vm: The vSphere VM managed object.

        Returns:
            bool: True if at least one NIC has a non-empty ipAddress list.
        """
        if not vm.guest.net:
            return False
        return any(nic.ipAddress for nic in vm.guest.net)

    @staticmethod
    def _wait_for_task(task: vim.Task, description: str, timeout: int = 120) -> None:
        """Wait for a vSphere task to complete.

        Args:
            task: The vSphere task object.
            description: Human-readable task description for logging.
            timeout: Seconds to wait for completion.

        Raises:
            RuntimeError: If the task fails or times out.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = str(task.info.state)
            if state == "success":
                return
            if state == "error":
                error_msg = str(task.info.error) if task.info.error else "Unknown error"
                raise RuntimeError(f"Task '{description}' failed: {error_msg}")
            time.sleep(2)

        raise RuntimeError(f"Task '{description}' timed out after {timeout}s")
