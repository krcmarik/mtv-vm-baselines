"""pyvmomi wrapper for vCenter connectivity and VM introspection."""

from __future__ import annotations

import ipaddress
import logging
import ssl
from typing import Any

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim

from mtv_vm_baselines.models import (
    NIC,
    AdvancedConfig,
    Disk,
    GuestOS,
    GuestRuntime,
    Hardware,
    IPAddress,
    Network,
    NICIPConfig,
    Storage,
    StorageController,
)

logger = logging.getLogger(__name__)


class VSphereClient:
    """Manages connection to a vCenter and provides VM query methods.

    Supports use as a context manager for automatic disconnect on exit.
    """

    def __init__(self, host: str, user: str, password: str, port: int = 443, verify_ssl: bool = False) -> None:
        """Connect to vCenter using SmartConnect.

        Args:
            host: vCenter hostname or IP address.
            user: vCenter username.
            password: vCenter password.
            port: vCenter HTTPS port (default 443).
            verify_ssl: If True, verify the vCenter TLS certificate.
                If False (default), skip verification.

        Raises:
            ConnectionError: If connection to vCenter fails.
        """
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.verify_ssl = verify_ssl
        self._si: Any = None
        self._content_cache: vim.ServiceInstanceContent | None = None
        self._connect()

    def _connect(self) -> None:
        """Establish the vCenter connection.

        Raises:
            ConnectionError: If SmartConnect fails.
        """
        if self.verify_ssl:
            ssl_context = ssl.create_default_context()
        else:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        try:
            self._si = SmartConnect(
                host=self.host,
                user=self.user,
                pwd=self.password,
                port=self.port,
                sslContext=ssl_context,
            )
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect to vCenter '{self.host}:{self.port}' as user '{self.user}': {exc}"
            ) from exc
        logger.info(f"Connected to vCenter '{self.host}'")

    def disconnect(self) -> None:
        """Disconnect from vCenter."""
        if self._si:
            Disconnect(self._si)
            logger.info(f"Disconnected from vCenter '{self.host}'")
            self._si = None
            self._content_cache = None

    def __enter__(self) -> VSphereClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.disconnect()

    @property
    def _content(self) -> vim.ServiceInstanceContent:
        """Retrieve the vCenter service content (cached after first call).

        Returns:
            vim.ServiceInstanceContent: The vCenter service content object.

        Raises:
            ConnectionError: If not connected.
        """
        if not self._si:
            raise ConnectionError("Not connected to vCenter")
        if self._content_cache is None:
            self._content_cache = self._si.RetrieveContent()
        return self._content_cache

    def find_vm(self, name: str) -> vim.VirtualMachine | None:
        """Find a VM by name using a container view.

        Args:
            name: The VM name to search for.

        Returns:
            The VM managed object, or None if not found.
        """
        content = self._content
        container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        try:
            for obj in container.view:
                if obj.name == name:
                    return obj
        finally:
            container.Destroy()
        return None

    def get_hardware(self, vm: vim.VirtualMachine) -> Hardware:
        """Extract hardware configuration (CPU, memory, firmware, HW version).

        Args:
            vm: The vSphere VM managed object.

        Returns:
            Hardware model with the VM's hardware settings.

        Raises:
            ValueError: If VM config is not accessible.
        """
        config = vm.config
        if not config:
            raise ValueError(f"No config found for VM '{vm.name}'")

        firmware = config.firmware if config.firmware else "bios"
        hw_version = config.version if config.version else "unknown"

        return Hardware(
            hw_version=hw_version,
            firmware=firmware,
            num_cpu=config.hardware.numCPU,
            memory_mb=config.hardware.memoryMB,
        )

    def get_guest_os(self, vm: vim.VirtualMachine) -> GuestOS:
        """Extract guest OS info from VM config.

        Args:
            vm: The vSphere VM managed object.

        Returns:
            GuestOS model with OS identification.

        Raises:
            ValueError: If VM config is not accessible.
        """
        config = vm.config
        if not config:
            raise ValueError(f"No config found for VM '{vm.name}'")

        return GuestOS(
            guest_id=config.guestId or "",
            guest_full_name=config.guestFullName or "",
            os_family=_infer_os_family(config.guestId or ""),
        )

    def get_storage(self, vm: vim.VirtualMachine) -> Storage:
        """Extract storage controllers and disks from VM hardware.

        Iterates vm.config.hardware.device to find:
        - Controllers: VirtualSCSIController subclasses, VirtualAHCIController, VirtualNVMEController
        - Disks: VirtualDisk with label, capacity, controller mapping, provisioning, disk mode

        Args:
            vm: The vSphere VM managed object.

        Returns:
            Storage model with controllers and disks.

        Raises:
            ValueError: If VM config is not accessible.
        """
        config = vm.config
        if not config:
            raise ValueError(f"No config found for VM '{vm.name}'")

        controllers: list[StorageController] = []
        disks: list[Disk] = []

        # Build a map of controller key -> (type_name, bus_number) for disk lookups
        controller_map: dict[int, tuple[str, int]] = {}

        for device in config.hardware.device:
            # Detect storage controllers
            if _is_storage_controller(device):
                type_name = type(device).__name__
                bus_number: int = getattr(device, "busNumber", 0)
                bus_sharing = _get_bus_sharing(device)
                controller_map[device.key] = (type_name, bus_number)
                controllers.append(
                    StorageController(
                        type=type_name,
                        bus_number=bus_number,
                        bus_sharing=bus_sharing,
                    )
                )

        # Extract disks
        for device in config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualDisk):
                label = device.deviceInfo.label if device.deviceInfo else "Unknown"
                capacity_gb = device.capacityInKB / (1024 * 1024)  # KB -> GB
                unit_number: int = device.unitNumber

                # Find controller bus number
                ctrl_info = controller_map.get(device.controllerKey)
                controller_bus = ctrl_info[1] if ctrl_info else 0

                # Determine provisioning type from backing info
                provisioning = _get_provisioning_type(device)

                # Determine disk mode
                disk_mode = _get_disk_mode(device)

                disks.append(
                    Disk(
                        label=label,
                        capacity_gb=round(capacity_gb, 2),
                        controller_bus=controller_bus,
                        unit_number=unit_number,
                        provisioning=provisioning,
                        disk_mode=disk_mode,
                    )
                )

        return Storage(controllers=controllers, disks=disks)

    def get_network(self, vm: vim.VirtualMachine) -> Network:
        """Extract NICs (type, label, network name) from VM hardware.

        Iterates vm.config.hardware.device for VirtualEthernetCard subclasses.
        Handles both standard vSwitch and Distributed Virtual Switch port groups.

        Args:
            vm: The vSphere VM managed object.

        Returns:
            Network model with NIC configurations.

        Raises:
            ValueError: If VM config is not accessible.
        """
        config = vm.config
        if not config:
            raise ValueError(f"No config found for VM '{vm.name}'")

        nics: list[NIC] = []

        for device in config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualEthernetCard):
                label = device.deviceInfo.label if device.deviceInfo else "Unknown"
                adapter_type = type(device).__name__
                network_name = self._get_network_name(device)

                nics.append(
                    NIC(
                        label=label,
                        adapter_type=adapter_type,
                        network_name=network_name,
                        mac_address=device.macAddress or "",
                        address_type=device.addressType or "",
                    )
                )

        return Network(nics=nics)

    def _get_network_name(self, device: vim.vm.device.VirtualEthernetCard) -> str:
        """Extract the network name from a virtual ethernet device backing.

        Handles standard network backing (vSwitch) and distributed virtual port
        backing (DVS) types.

        Args:
            device: Virtual ethernet card device.

        Returns:
            The network name, or "Unknown" if unable to determine.
        """
        if not device.backing:
            return "Unknown"

        # Standard network backing (vSwitch)
        if hasattr(device.backing, "network") and device.backing.network:
            return device.backing.network.name

        # Distributed virtual port backing (DVS)
        if hasattr(device.backing, "port") and device.backing.port:
            port = device.backing.port
            if hasattr(port, "portgroupKey"):
                try:
                    content = self._content
                    container = content.viewManager.CreateContainerView(
                        content.rootFolder,
                        [vim.dvs.DistributedVirtualPortgroup],
                        True,
                    )
                    try:
                        for pg in container.view:
                            if pg.key == port.portgroupKey:
                                return pg.name
                    finally:
                        container.Destroy()
                except Exception as exc:
                    logger.debug(f"Could not resolve DVS portgroup key '{port.portgroupKey}': {exc}")

                return f"DVS-{port.portgroupKey}"
            return "Distributed Virtual Switch"

        # Fallback: try deviceInfo summary
        if device.deviceInfo and device.deviceInfo.summary:
            return device.deviceInfo.summary

        return "Unknown"

    def get_advanced_config(self, vm: vim.VirtualMachine) -> AdvancedConfig:
        """Extract advanced configuration (CBT, etc.) from VM config.

        Args:
            vm: The vSphere VM managed object.

        Returns:
            AdvancedConfig model with advanced settings.

        Raises:
            ValueError: If VM config is not accessible.
        """
        config = vm.config
        if not config:
            raise ValueError(f"No config found for VM '{vm.name}'")

        cbt_enabled = bool(config.changeTrackingEnabled)

        return AdvancedConfig(cbt_enabled=cbt_enabled)

    def get_guest_network_info(self, vm: vim.VirtualMachine) -> GuestRuntime:
        """Extract guest network info from VMware Tools runtime data.

        Uses vm.guest.net and vm.guest.ipStack to collect NIC IP
        configurations without requiring guest credentials or Guest
        Operations API. Works when VMware Tools is running; returns
        empty GuestRuntime when the VM is off or Tools are unavailable.

        Args:
            vm: The vSphere VM managed object.

        Returns:
            GuestRuntime model with IP configuration for each NIC.
        """
        if not vm.guest or not vm.guest.net:
            logger.debug(f"No guest network data available for VM '{vm.name}' (VM off or Tools not running)")
            return GuestRuntime()

        # Build device key -> label map from hardware devices
        device_label_map: dict[int, str] = {}
        config = vm.config
        if config and config.hardware:
            for device in config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualEthernetCard):
                    device_label_map[device.key] = device.deviceInfo.label if device.deviceInfo else f"NIC-{device.key}"

        # Extract default gateways from ipStack
        default_gateways: list[str] = []
        try:
            ip_stacks = vm.guest.ipStack
            if ip_stacks:
                ip_route_config = ip_stacks[0].ipRouteConfig
                if ip_route_config and ip_route_config.ipRoute:
                    for route in ip_route_config.ipRoute:
                        if (route.network == "0.0.0.0" or route.network == "::") and route.prefixLength == 0:
                            gateway_spec = getattr(route, "gateway", None)
                            if gateway_spec:
                                gw_addr = getattr(gateway_spec, "ipAddress", None)
                                if gw_addr:
                                    default_gateways.append(gw_addr)
        except (AttributeError, IndexError):
            logger.debug(f"Could not extract default gateway from ipStack for VM '{vm.name}'")

        # Iterate vm.guest.net to build NIC IP configs
        nic_configs: list[NICIPConfig] = []
        gateway_assigned = False

        for guest_nic in vm.guest.net:
            device_config_id = getattr(guest_nic, "deviceConfigId", -1)
            nic_label = device_label_map.get(device_config_id, f"NIC-{device_config_id}")

            # Collect IP addresses, filtering out link-local
            ip_addresses: list[IPAddress] = []
            ip_config = getattr(guest_nic, "ipConfig", None)
            if ip_config and ip_config.ipAddress:
                for ip_entry in ip_config.ipAddress:
                    addr = getattr(ip_entry, "ipAddress", "")
                    prefix = getattr(ip_entry, "prefixLength", 0)
                    if not addr:
                        continue
                    # Filter link-local addresses
                    try:
                        if ipaddress.ip_address(addr).is_link_local:
                            continue
                    except ValueError:
                        continue
                    origin = getattr(ip_entry, "origin", "") or ""
                    ip_addresses.append(IPAddress(address=addr, prefix_length=prefix, origin=origin))

            # Assign gateway to the appropriate NIC
            nic_gateway = ""
            if default_gateways and ip_addresses and not gateway_assigned:
                for gw in default_gateways:
                    if _is_gateway_in_subnet(gw, ip_addresses):
                        nic_gateway = gw
                        gateway_assigned = True
                        break

            nic_configs.append(
                NICIPConfig(
                    nic_label=nic_label,
                    ip_addresses=ip_addresses,
                    gateway=nic_gateway,
                )
            )

        # Fallback: if gateway was not assigned to any NIC by subnet match,
        # assign to the first NIC that has IP addresses
        if default_gateways and not gateway_assigned:
            for nic_config in nic_configs:
                if nic_config.ip_addresses:
                    nic_config.gateway = default_gateways[0]
                    break

        return GuestRuntime(ip_config=nic_configs)

    def get_disk_backing_info(self, vm: vim.VirtualMachine) -> list[dict[str, Any]]:
        """Extract disk backing file paths for shared disk detection.

        Iterates VM hardware devices to find VirtualDisk objects and extracts
        their backing file paths, controller keys, unit numbers, and the bus
        sharing mode of the owning SCSI controller.

        Args:
            vm: The vSphere VM managed object.

        Returns:
            List of dicts, each containing:
                - label: Disk device label (e.g., "Hard disk 2")
                - backing_file: VMDK backing file path (e.g., "[datastore] path/to/disk.vmdk")
                - controller_key: Device key of the owning controller
                - unit_number: SCSI unit number on the controller
                - bus_number: SCSI bus number of the owning controller
                - bus_sharing: Bus sharing mode of the controller ("noSharing",
                  "physicalSharing", or "virtualSharing")

        Raises:
            ValueError: If VM config is not accessible.
        """
        config = vm.config
        if not config:
            raise ValueError(f"No config found for VM '{vm.name}'")

        # Build controller key -> (bus_number, bus_sharing) map.
        # Only SCSI controllers are relevant: shared disks on vSphere require
        # SCSI bus sharing (physicalSharing or virtualSharing).  NVMe and AHCI
        # controllers do not support bus sharing, so disks on those controllers
        # will receive the fallback (bus=0, sharing="noSharing") which is correct.
        controller_map: dict[int, tuple[int, str]] = {}
        for device in config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualSCSIController):
                bus_sharing = str(device.sharedBus) if device.sharedBus else "noSharing"
                controller_map[device.key] = (device.busNumber, bus_sharing)

        result: list[dict[str, Any]] = []
        for device in config.hardware.device:
            if not isinstance(device, vim.vm.device.VirtualDisk):
                continue

            backing_file = ""
            if hasattr(device.backing, "fileName") and device.backing.fileName:
                backing_file = device.backing.fileName

            ctrl_info = controller_map.get(device.controllerKey, (0, "noSharing"))
            bus_number, bus_sharing = ctrl_info

            result.append(
                {
                    "label": device.deviceInfo.label if device.deviceInfo else "Unknown",
                    "backing_file": backing_file,
                    "controller_key": device.controllerKey,
                    "unit_number": device.unitNumber,
                    "bus_number": bus_number,
                    "bus_sharing": bus_sharing,
                }
            )

        return result


def _is_storage_controller(device: Any) -> bool:
    """Check if a device is a storage controller (SCSI, AHCI, NVMe).

    Args:
        device: A vSphere virtual device.

    Returns:
        True if the device is a storage controller.
    """
    return isinstance(
        device,
        (
            vim.vm.device.VirtualSCSIController,
            vim.vm.device.VirtualAHCIController,
            vim.vm.device.VirtualNVMEController,
        ),
    )


def _get_bus_sharing(device: Any) -> str:
    """Extract bus sharing mode from a storage controller.

    Args:
        device: A vSphere storage controller device.

    Returns:
        Bus sharing mode string, or "noSharing" for non-SCSI controllers.
    """
    if isinstance(device, vim.vm.device.VirtualSCSIController):
        return str(device.sharedBus) if device.sharedBus else "noSharing"
    return "noSharing"


def _get_provisioning_type(disk: vim.vm.device.VirtualDisk) -> str:
    """Determine disk provisioning type from its backing info.

    Detection logic mirrors the mtv-api-tests VMWareProvider pattern:
    - thinProvisioned == True -> "thin"
    - eagerlyScrub == True -> "thick-eager"
    - else -> "thick-lazy"

    Args:
        disk: A VirtualDisk device.

    Returns:
        Provisioning type: "thin", "thick-eager", or "thick-lazy".
    """
    backing = disk.backing
    if not backing:
        return "unknown"

    if isinstance(backing, vim.vm.device.VirtualDisk.FlatVer2BackingInfo):
        if getattr(backing, "thinProvisioned", False):
            return "thin"
        if getattr(backing, "eagerlyScrub", False):
            return "thick-eager"
        return "thick-lazy"

    if isinstance(backing, vim.vm.device.VirtualDisk.RawDiskMappingVer1BackingInfo):
        return "rdm"

    # Other backing types (sparse, sesparse, etc.)
    return type(backing).__name__


def _get_disk_mode(disk: vim.vm.device.VirtualDisk) -> str:
    """Extract disk mode from a VirtualDisk's backing.

    Args:
        disk: A VirtualDisk device.

    Returns:
        Disk mode string (e.g., "persistent", "independent_persistent"),
        or "unknown" if not available.
    """
    backing = disk.backing
    if backing and hasattr(backing, "diskMode"):
        return str(backing.diskMode)
    return "unknown"


def _is_gateway_in_subnet(gateway: str, ip_addresses: list[IPAddress]) -> bool:
    """Check if a gateway IP is in the same subnet as any of the given addresses.

    Uses simple prefix-based matching for IPv4 addresses. For each IP address,
    computes the network prefix from the address and prefix length, then checks
    if the gateway falls in the same network.

    Args:
        gateway: Gateway IP address string.
        ip_addresses: List of IPAddress objects to check against.

    Returns:
        True if the gateway is in the same subnet as any address.
    """
    try:
        gw = ipaddress.ip_address(gateway)
        for ip_addr in ip_addresses:
            try:
                network = ipaddress.ip_network(f"{ip_addr.address}/{ip_addr.prefix_length}", strict=False)
                if gw in network:
                    return True
            except ValueError:
                continue
    except ValueError:
        return False
    return False


def _infer_os_family(guest_id: str) -> str:
    """Infer the OS family from the vSphere guest ID string.

    Args:
        guest_id: The vSphere guest ID (e.g., "rhel8_64Guest").

    Returns:
        OS family string (e.g., "linuxGuest", "windowsGuest").
    """
    guest_id_lower = guest_id.lower()
    if any(tag in guest_id_lower for tag in ("win", "windows")):
        return "windowsGuest"
    if any(
        tag in guest_id_lower for tag in ("rhel", "centos", "ubuntu", "debian", "sles", "linux", "fedora", "oracle")
    ):
        return "linuxGuest"
    return "otherGuest"
