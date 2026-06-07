"""Pydantic v2 models for VM baselines and configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BaselineMeta(BaseModel):
    """Metadata about when and how a baseline was captured."""

    vm_name: str
    baseline_version: str = "1.1"
    captured_at: str = ""  # ISO 8601 timestamp
    description: str = ""


class GuestOS(BaseModel):
    """Guest operating system identification."""

    guest_id: str  # e.g., "rhel8_64Guest"
    guest_full_name: str  # e.g., "Red Hat Enterprise Linux 8 (64-bit)"
    os_family: str  # e.g., "linuxGuest"


class Hardware(BaseModel):
    """Virtual hardware configuration."""

    hw_version: str  # e.g., "vmx-17"
    firmware: str  # "bios" or "efi"
    num_cpu: int
    memory_mb: int


class StorageController(BaseModel):
    """SCSI/NVMe/AHCI storage controller configuration."""

    type: str  # e.g., "ParaVirtualSCSIController"
    bus_number: int
    bus_sharing: str  # e.g., "noSharing", "physicalSharing", "virtualSharing"


class Disk(BaseModel):
    """Virtual disk configuration."""

    label: str
    capacity_gb: float
    controller_bus: int
    unit_number: int
    provisioning: str  # "thin", "thick-lazy", "thick-eager"
    disk_mode: str  # "persistent", "independent_persistent", etc.


class Storage(BaseModel):
    """Aggregated storage configuration (controllers + disks)."""

    controllers: list[StorageController] = Field(default_factory=list)
    disks: list[Disk] = Field(default_factory=list)


class NIC(BaseModel):
    """Virtual network interface card configuration."""

    label: str
    adapter_type: str  # e.g., "VirtualVmxnet3", "VirtualE1000e"
    network_name: str


class Network(BaseModel):
    """Aggregated network configuration."""

    nics: list[NIC] = Field(default_factory=list)


class AdvancedConfig(BaseModel):
    """Advanced VM configuration flags."""

    cbt_enabled: bool = False


class IPAddress(BaseModel):
    """Single IP address assignment on a NIC."""

    address: str
    prefix_length: int
    origin: str = ""


class NICIPConfig(BaseModel):
    """IP configuration for a single NIC (guest runtime data)."""

    nic_label: str
    ip_addresses: list[IPAddress] = Field(default_factory=list)
    gateway: str = ""


class GuestRuntime(BaseModel):
    """Runtime guest OS state (populated from VMware Tools guest info)."""

    ip_config: list[NICIPConfig] = Field(default_factory=list)


class SharedDiskPosition(BaseModel):
    """SCSI position of a shared disk on a specific VM."""

    vm: str
    bus: int
    unit: int


class SharedDiskGroup(BaseModel):
    """Group of VMs sharing the same physical disk."""

    participating_vms: list[str] = Field(default_factory=list)
    scsi_positions: list[SharedDiskPosition] = Field(default_factory=list)


class VMBaseline(BaseModel):
    """Complete VM configuration baseline."""

    meta: BaselineMeta
    guest_os: GuestOS
    hardware: Hardware
    storage: Storage
    network: Network
    advanced: AdvancedConfig = AdvancedConfig()
    guest_runtime: GuestRuntime = GuestRuntime()
    shared_disk_groups: list[SharedDiskGroup] = Field(default_factory=list)
