"""
Core domain models for partition inspection, Windows detection, and boot recovery.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any


class BootMode(str, Enum):
    UEFI = "UEFI"
    BIOS = "BIOS"
    HYBRID = "HYBRID"
    UNKNOWN = "UNKNOWN"


class PartitionRole(str, Enum):
    ESP = "ESP"
    WINDOWS_OS = "WINDOWS_OS"
    SYSTEM_RESERVED = "SYSTEM_RESERVED"
    LINUX = "LINUX"
    DATA = "DATA"
    UNKNOWN = "UNKNOWN"


@dataclass
class PartitionInfo:
    device: str
    disk: str
    part_num: int
    fstype: str
    uuid: Optional[str] = None
    part_uuid: Optional[str] = None
    label: Optional[str] = None
    size_bytes: int = 0
    size_human: str = ""
    mount_point: Optional[str] = None
    is_mounted: bool = False
    is_esp: bool = False
    is_boot_flag: bool = False
    pttype: str = "gpt"
    role: PartitionRole = PartitionRole.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device": self.device,
            "disk": self.disk,
            "part_num": self.part_num,
            "fstype": self.fstype,
            "uuid": self.uuid,
            "part_uuid": self.part_uuid,
            "label": self.label,
            "size_bytes": self.size_bytes,
            "size_human": self.size_human,
            "mount_point": self.mount_point,
            "is_mounted": self.is_mounted,
            "is_esp": self.is_esp,
            "is_boot_flag": self.is_boot_flag,
            "pttype": self.pttype,
            "role": self.role.value,
        }


@dataclass
class WindowsInstallation:
    partition: PartitionInfo
    windows_dir: str = "/Windows"
    system32_dir: str = "/Windows/System32"
    product_name: Optional[str] = None
    version_build: Optional[str] = None
    has_efi_boot_files: bool = False
    has_pcat_boot_files: bool = False
    has_bcd_template: bool = False
    winload_efi_present: bool = False
    winload_exe_present: bool = False

    def is_uefi_compatible(self) -> bool:
        return self.winload_efi_present or self.has_efi_boot_files

    def is_legacy_compatible(self) -> bool:
        return self.winload_exe_present or self.has_pcat_boot_files

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partition": self.partition.to_dict(),
            "windows_dir": self.windows_dir,
            "product_name": self.product_name,
            "version_build": self.version_build,
            "has_efi_boot_files": self.has_efi_boot_files,
            "has_pcat_boot_files": self.has_pcat_boot_files,
            "has_bcd_template": self.has_bcd_template,
            "winload_efi_present": self.winload_efi_present,
            "winload_exe_present": self.winload_exe_present,
            "is_uefi_compatible": self.is_uefi_compatible(),
            "is_legacy_compatible": self.is_legacy_compatible(),
        }


@dataclass
class EfiBootEntry:
    boot_num: str
    label: str
    active: bool
    file_path: Optional[str] = None
    disk_device: Optional[str] = None
    partition_num: Optional[int] = None

    def is_windows_manager(self) -> bool:
        label_lower = self.label.lower()
        path_lower = (self.file_path or "").lower()
        return "windows" in label_lower or "bootmgfw.efi" in path_lower

    def to_dict(self) -> Dict[str, Any]:
        return {
            "boot_num": self.boot_num,
            "label": self.label,
            "active": self.active,
            "file_path": self.file_path,
            "disk_device": self.disk_device,
            "partition_num": self.partition_num,
            "is_windows": self.is_windows_manager(),
        }


@dataclass
class ScanReport:
    firmware_uefi: bool
    system_boot_mode: BootMode
    partitions: List[PartitionInfo] = field(default_factory=list)
    windows_installations: List[WindowsInstallation] = field(default_factory=list)
    esp_partitions: List[PartitionInfo] = field(default_factory=list)
    nvram_entries: List[EfiBootEntry] = field(default_factory=list)
    current_boot_order: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "firmware_uefi": self.firmware_uefi,
            "system_boot_mode": self.system_boot_mode.value,
            "partitions": [p.to_dict() for p in self.partitions],
            "windows_installations": [w.to_dict() for w in self.windows_installations],
            "esp_partitions": [e.to_dict() for e in self.esp_partitions],
            "nvram_entries": [n.to_dict() for n in self.nvram_entries],
            "current_boot_order": self.current_boot_order,
        }


@dataclass
class ActionItem:
    category: str
    description: str
    command: Optional[str] = None
    target_path: Optional[str] = None


@dataclass
class RecoveryPlan:
    windows_target: WindowsInstallation
    esp_target: Optional[PartitionInfo]
    boot_mode: BootMode
    actions: List[ActionItem] = field(default_factory=list)
    backup_esp: bool = True
    install_fallback_bootx64: bool = True
    update_nvram: bool = True
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "windows_target": self.windows_target.to_dict(),
            "esp_target": self.esp_target.to_dict() if self.esp_target else None,
            "boot_mode": self.boot_mode.value,
            "actions": [
                {
                    "category": a.category,
                    "description": a.description,
                    "command": a.command,
                    "target_path": a.target_path,
                }
                for a in self.actions
            ],
            "backup_esp": self.backup_esp,
            "install_fallback_bootx64": self.install_fallback_bootx64,
            "update_nvram": self.update_nvram,
            "dry_run": self.dry_run,
        }


@dataclass
class VerificationCheck:
    name: str
    passed: bool
    details: str
    severity: str = "ERROR"  # "ERROR", "WARNING", "INFO"


@dataclass
class RecoveryResult:
    success: bool
    plan: Optional[RecoveryPlan] = None
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    backup_dir: Optional[str] = None
    verification_checks: List[VerificationCheck] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "messages": self.messages,
            "errors": self.errors,
            "backup_dir": self.backup_dir,
            "verification_checks": [
                {
                    "name": v.name,
                    "passed": v.passed,
                    "details": v.details,
                    "severity": v.severity,
                }
                for v in self.verification_checks
            ],
        }
