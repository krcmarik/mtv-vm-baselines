"""VM configuration capture from vCenter inventories."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from mtv_vm_baselines.models import (
    BaselineMeta,
    SecurityFeatures,
    SharedDiskGroup,
    SharedDiskPosition,
    VMBaseline,
)
from mtv_vm_baselines.power_manager import PowerManager
from mtv_vm_baselines.vsphere_client import VSphereClient

logger = logging.getLogger(__name__)


class BaselineCollector:
    """Orchestrates VM baseline capture from vSphere.

    Uses a VSphereClient to query VM configuration and assemble
    VMBaseline models.
    """

    def __init__(self, client: VSphereClient) -> None:
        """Initialize the collector with a connected vSphere client.

        Args:
            client: A connected VSphereClient instance.
        """
        self.client = client

    def capture(self, vm_name: str, description: str = "") -> VMBaseline:
        """Capture a full baseline for a single VM (hardware only in Phase 1).

        Args:
            vm_name: Name of the VM to capture.
            description: Optional human-readable description for the baseline.

        Returns:
            VMBaseline model with the captured configuration.

        Raises:
            ValueError: If the VM is not found in vCenter.
        """
        logger.info(f"Capturing baseline for VM '{vm_name}'")

        vm = self.client.find_vm(vm_name)
        if vm is None:
            raise ValueError(f"VM '{vm_name}' not found in vCenter '{self.client.host}'")

        guest_os = self.client.get_guest_os(vm)
        hardware = self.client.get_hardware(vm)
        storage = self.client.get_storage(vm)
        network = self.client.get_network(vm)
        advanced = self.client.get_advanced_config(vm)
        security_features = (
            self.client.get_security_features(vm) if guest_os.os_family == "windowsGuest" else SecurityFeatures()
        )
        guest_runtime = self.client.get_guest_network_info(vm)

        meta = BaselineMeta(
            vm_name=vm_name,
            captured_at=datetime.now(tz=UTC).isoformat(),
            description=description,
        )

        baseline = VMBaseline(
            meta=meta,
            guest_os=guest_os,
            hardware=hardware,
            storage=storage,
            network=network,
            advanced=advanced,
            security_features=security_features,
            guest_runtime=guest_runtime,
        )

        logger.info(
            f"Baseline captured for VM '{vm_name}': "
            f"{hardware.num_cpu} vCPU, {hardware.memory_mb} MB RAM, "
            f"{len(storage.disks)} disk(s), {len(network.nics)} NIC(s)"
        )

        return baseline

    def capture_with_power_on(
        self,
        vm_name: str,
        power_manager: PowerManager,
        description: str = "",
    ) -> VMBaseline:
        """Capture a VM baseline, powering on the VM if needed for guest info.

        Powers on the VM via vSphere API (no guest credentials needed),
        waits for VMware Tools, captures hardware + guest runtime data,
        then the PowerManager restores power state on exit.

        Args:
            vm_name: Name of the VM to capture.
            power_manager: PowerManager instance for power state tracking.
            description: Optional description for the baseline metadata.

        Returns:
            VMBaseline with hardware and guest runtime data.

        Raises:
            ValueError: If the VM is not found.
            TimeoutError: If VMware Tools does not become ready.
        """
        vm = self.client.find_vm(vm_name)
        if vm is None:
            raise ValueError(f"VM '{vm_name}' not found on vCenter '{self.client.host}'")

        power_manager.power_on(vm)
        return self.capture(vm_name, description=description)

    def capture_multiple(self, vm_names: list[str]) -> dict[str, VMBaseline]:
        """Capture baselines for multiple VMs.

        Args:
            vm_names: List of VM names to capture.

        Returns:
            Dict mapping VM name to its VMBaseline. VMs that fail
            capture are logged and skipped.
        """
        results: dict[str, VMBaseline] = {}

        for name in vm_names:
            try:
                results[name] = self.capture(name)
            except ValueError as exc:
                logger.error(f"Failed to capture baseline for VM '{name}': {exc}")
            except ConnectionError as exc:
                logger.error(f"Connection lost while capturing VM '{name}': {exc}")
                break

        logger.info(f"Captured {len(results)}/{len(vm_names)} baselines")
        return results

    def detect_shared_disks(self, baselines: dict[str, VMBaseline]) -> dict[str, VMBaseline]:
        """Detect shared disk groups across captured VMs.

        Cross-references VMDK backing file paths across all captured VMs.
        VMs with disks pointing to the same VMDK are in a shared disk group.

        This requires the vSphere client to be connected, as it queries each
        VM's disk backing information from vCenter.

        Args:
            baselines: Dict of vm_name -> VMBaseline from capture_multiple().

        Returns:
            Updated baselines with shared_disk_groups populated.
        """
        if len(baselines) < 2:
            logger.debug("Fewer than 2 VMs captured, skipping shared disk detection")
            return baselines

        # Build a map of VMDK backing file path -> list of (vm_name, bus_number, unit_number)
        vmdk_map: dict[str, list[tuple[str, int, int]]] = {}

        for vm_name in baselines:
            vm = self.client.find_vm(vm_name)
            if vm is None:
                logger.warning(f"VM '{vm_name}' not found during shared disk detection, skipping")
                continue

            disk_infos = self.client.get_disk_backing_info(vm)
            for disk_info in disk_infos:
                backing_file: str = disk_info["backing_file"]
                if not backing_file:
                    continue
                vmdk_map.setdefault(backing_file, []).append(
                    (
                        vm_name,
                        disk_info["bus_number"],
                        disk_info["unit_number"],
                    )
                )

        # Filter to only VMDKs shared by 2+ distinct VMs
        shared_vmdks = {
            path: positions for path, positions in vmdk_map.items() if len({vm_name for vm_name, _, _ in positions}) > 1
        }

        if not shared_vmdks:
            logger.info("No shared disks detected across captured VMs")
            return baselines

        logger.info(f"Detected {len(shared_vmdks)} shared disk group(s) across VMs")

        # Build SharedDiskGroup objects and assign to participating VMs
        for vmdk_path, positions in shared_vmdks.items():
            participating_vms = sorted({vm_name for vm_name, _, _ in positions})
            scsi_positions = [SharedDiskPosition(vm=vm_name, bus=bus, unit=unit) for vm_name, bus, unit in positions]

            group = SharedDiskGroup(
                participating_vms=participating_vms,
                scsi_positions=scsi_positions,
            )

            logger.info(
                f"Shared disk group: VMDK='{vmdk_path}', "
                f"VMs={participating_vms}, "
                f"positions={[(p.vm, p.bus, p.unit) for p in scsi_positions]}"
            )

            # Add the group to each participating VM's baseline
            for vm_name in participating_vms:
                if vm_name in baselines:
                    baselines[vm_name].shared_disk_groups.append(group)

        return baselines

    @staticmethod
    def _are_groups_equivalent(group1: SharedDiskGroup, group2: SharedDiskGroup) -> bool:
        """Check if two SharedDiskGroup objects are equivalent.

        Two groups are equivalent if they have the same participating VMs
        and the same SCSI positions.

        Args:
            group1: First SharedDiskGroup to compare.
            group2: Second SharedDiskGroup to compare.

        Returns:
            True if groups are equivalent, False otherwise.
        """
        # Compare participating VMs (order doesn't matter, both are sorted lists)
        if sorted(group1.participating_vms) != sorted(group2.participating_vms):
            return False

        # Compare SCSI positions (order doesn't matter, so compare as sets)
        # Convert to tuples for set comparison since SharedDiskPosition is not hashable
        positions1 = {(p.vm, p.bus, p.unit) for p in group1.scsi_positions}
        positions2 = {(p.vm, p.bus, p.unit) for p in group2.scsi_positions}

        return positions1 == positions2

    @staticmethod
    def detect_shared_disks_from_baselines(baselines: dict[str, VMBaseline]) -> dict[str, VMBaseline]:
        """Detect shared disk groups from stored baseline data.

        Cross-references VMDK backing file paths stored in each VM's disk
        entries. Unlike detect_shared_disks(), this does not require a
        vSphere connection.

        Args:
            baselines: Dict of vm_name -> VMBaseline loaded from files.

        Returns:
            Updated baselines with shared_disk_groups populated.
        """
        if len(baselines) < 2:
            return baselines

        vmdk_map: dict[str, list[tuple[str, int, int]]] = {}

        for vm_name, baseline in baselines.items():
            for disk in baseline.storage.disks:
                if not disk.backing_file:
                    continue
                vmdk_map.setdefault(disk.backing_file, []).append(
                    (vm_name, disk.controller_bus, disk.unit_number)
                )

        shared_vmdks = {
            path: positions
            for path, positions in vmdk_map.items()
            if len({vm_name for vm_name, _, _ in positions}) > 1
        }

        if not shared_vmdks:
            logger.info("No shared disks detected in stored baselines")
            return baselines

        logger.info(f"Detected {len(shared_vmdks)} shared disk group(s) in stored baselines")

        for vmdk_path, positions in shared_vmdks.items():
            participating_vms = sorted({vm_name for vm_name, _, _ in positions})
            scsi_positions = [
                SharedDiskPosition(vm=vm_name, bus=bus, unit=unit)
                for vm_name, bus, unit in positions
            ]

            group = SharedDiskGroup(
                participating_vms=participating_vms,
                scsi_positions=scsi_positions,
            )

            logger.info(
                f"Shared disk group (from baselines): VMDK='{vmdk_path}', "
                f"VMs={participating_vms}, "
                f"positions={[(p.vm, p.bus, p.unit) for p in scsi_positions]}"
            )

            for vm_name in participating_vms:
                if vm_name in baselines:
                    # Check if this group already exists in the baseline
                    existing_groups = baselines[vm_name].shared_disk_groups
                    if not any(BaselineCollector._are_groups_equivalent(group, existing) for existing in existing_groups):
                        baselines[vm_name].shared_disk_groups.append(group)
                    else:
                        logger.debug(
                            f"Skipping duplicate shared disk group for VM '{vm_name}': "
                            f"group already exists with VMs={participating_vms}"
                        )

        return baselines

    def find_and_capture_shared_partners(
        self,
        baselines: dict[str, VMBaseline],
        power_manager: PowerManager | None = None,
    ) -> dict[str, VMBaseline]:
        """Find and auto-capture VMs that share disks with already-captured VMs.

        Checks each captured VM for shared SCSI buses, then uses
        PropertyCollector to efficiently find partner VMs in vCenter.
        Partners not already captured are captured automatically.

        Args:
            baselines: Dict of vm_name -> VMBaseline already captured.
            power_manager: Optional PowerManager for power-on capture.

        Returns:
            Updated baselines dict with partner VMs added.
        """
        shared_vmdk_paths: set[str] = set()

        for vm_name, baseline in baselines.items():
            has_shared_bus = any(
                ctrl.bus_sharing != "noSharing"
                for ctrl in baseline.storage.controllers
            )
            if not has_shared_bus:
                continue

            for disk in baseline.storage.disks:
                if not disk.backing_file:
                    continue
                for ctrl in baseline.storage.controllers:
                    if ctrl.bus_number == disk.controller_bus and ctrl.bus_sharing != "noSharing":
                        shared_vmdk_paths.add(disk.backing_file)
                        break

        if not shared_vmdk_paths:
            return baselines

        logger.info(f"Checking for shared disk partners ({len(shared_vmdk_paths)} shared VMDK path(s))")

        try:
            vmdk_to_vms = self.client.find_shared_disk_partners(list(shared_vmdk_paths))
        except Exception:
            logger.warning("Failed to query vCenter for shared disk partners, skipping auto-capture")
            return baselines

        partners_to_capture: set[str] = set()
        for vmdk_path, vm_names in vmdk_to_vms.items():
            for vm_name in vm_names:
                if vm_name not in baselines:
                    partners_to_capture.add(vm_name)
                    logger.info(
                        f"Discovered shared disk partner: VM '{vm_name}' "
                        f"shares VMDK '{vmdk_path}'"
                    )

        if not partners_to_capture:
            logger.info("All shared disk partners already captured")
            return baselines

        logger.info(f"Auto-capturing {len(partners_to_capture)} shared disk partner(s): {sorted(partners_to_capture)}")

        for partner_name in sorted(partners_to_capture):
            try:
                if power_manager:
                    baselines[partner_name] = self.capture_with_power_on(partner_name, power_manager)
                else:
                    baselines[partner_name] = self.capture(partner_name)
            except (ValueError, TimeoutError) as exc:
                logger.warning(f"Failed to capture shared disk partner VM '{partner_name}': {exc}")

        return baselines
