# Redivivus

> **Universal Windows Bootloader Recovery Tool from Linux without WinPE**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8%2B-green.svg)](pyproject.toml)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero%20Python%20Wheels-brightgreen.svg)](redivivus)

---

## Overview

When Windows fails to boot in a dual-boot or rescue scenario—due to a formatted or corrupted EFI System Partition (ESP), damaged BCD registry hive, lost motherboard NVRAM boot entries, or broken Master Boot Record—the standard recommendation is to find a secondary Windows PC, download an official multi-gigabyte ISO, create a bootable USB drive, boot into Windows PE / Recovery Environment, and run `bcdboot.exe`.

**Redivivus** solves this natively from Linux. It rebuilds and restores complete Windows bootloaders (both **UEFI / GPT** and **Legacy BIOS / MBR**) directly from any running Linux system or live rescue USB (Ubuntu, Debian, Fedora, Arch, Alpine, SystemRescue), **without requiring Windows installation media or WinPE**.

---

## How It Works

### The Architectural Insight
Windows already maintains a complete, pristine library of bootloader binaries, font rendering engines, and localized MUI resources on the Windows system partition itself:
- **UEFI Boot Assets**: Located at `C:\Windows\Boot\EFI\` (`bootmgfw.efi`, `bootmgr.efi`, `boot.sdi`, `memtest.efi`, `Fonts/`, `Resources/`).
- **Legacy BIOS Assets**: Located at `C:\Windows\Boot\PCAT\` (`bootmgr`, `boot.sdi`, `memtest.exe`, `Fonts/`).
- **Canonical BCD Skeleton**: Located at `C:\Windows\System32\config\BCD-Template`.

This internal directory is the exact source repository that Windows' own `bcdboot.exe` utility copies from. **Redivivus** orchestrates this process natively in Linux, deploying files, synthesizing BCD hives, and registering UEFI firmware NVRAM boot entries.

```
+-----------------------------------------------------------------------------------+
|                            TARGET MACHINE STORAGE                                 |
|                                                                                   |
|  +-------------------------------+             +-------------------------------+  |
|  | Windows Partition (NTFS)      |             | EFI System Partition (FAT32)  |  |
|  |                               |             |                               |  |
|  | \Windows\Boot\EFI\            |   DEPLOY    | \EFI\Microsoft\Boot\          |  |
|  |   - bootmgfw.efi  ------------+------------>|   - bootmgfw.efi              |  |
|  |   - bootmgr.efi   ------------+------------>|   - bootmgr.efi               |  |
|  |   - boot.sdi      ------------+------------>|   - boot.sdi                  |  |
|  |   - Fonts\        ------------+------------>|   - Fonts\                    |  |
|  |   - en-US\        ------------+------------>|   - en-US\                    |  |
|  |                               |             |                               |  |
|  | \Windows\System32\config\     |   SYNTH     |                               |  |
|  |   - BCD-Template  ------------+------------>|   - BCD (Registry Hive)       |  |
|  +-------------------------------+             |                               |  |
|                                                | \EFI\Boot\                    |  |
|                                                |   - bootx64.efi (Fallback)    |  |
|                                                +---------------+---------------+  |
|                                                                |                  |
|                                                                v REGISTER         |
|                                                +-------------------------------+  |
|                                                | Motherboard UEFI NVRAM        |  |
|                                                |   - Boot000X: Windows Boot Mgr|  |
|                                                +-------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## Key Features

1. **Zero External Python Dependencies**: Built entirely using the Python 3 standard library. Runs out-of-the-box on live rescue USBs without installing third-party pip wheels.
2. **Universal Boot Support**:
   - **UEFI / GPT**: Deploys `bootmgfw.efi`, sets up localized fonts/resources, creates valid BCD, and installs standard `\EFI\Boot\bootx64.efi` fallback loader.
   - **Legacy BIOS / MBR**: Deploys `bootmgr` to partition root, supporting assets to `\Boot\`, and creates legacy PCAT BCD hive.
3. **Automated Partition & OS Discovery**: Scans all block devices, identifies Windows installations (including product version), discovers candidate ESPs, and inspects NVRAM entries.
4. **Resilient BCD Synthesis**: Reads and patches `BCD-Template` from the Windows installation. If the disk template is damaged or missing, automatically deploys bundled canonical BCD templates.
5. **Firmware NVRAM Integration**: Inspects existing entries via `efibootmgr`, revives inactive entries, registers missing entries, and configures boot priority.
6. **Hardware Firmware Fallback Strategy**: Buggy UEFI firmware on many laptops ignores or wipes NVRAM entries; Redivivus automatically installs the standard fallback loader (`\EFI\Boot\bootx64.efi`) ensuring guaranteed bootability.
7. **Pre-flight Safety Backups**: Automatically creates timestamped backups of existing `EFI/Microsoft` directories before modifying any files.
8. **Dry-Run Simulation**: `--dry-run` previews every planned action, target file, and shell command without altering disk state.

---

## Installation & Requirements

### System Requirements
- Linux kernel with block device access (`lsblk`, `blkid`).
- Python 3.8 or higher.
- `efibootmgr` (for UEFI NVRAM operations; standard on almost all distributions).
- NTFS read/write support (`ntfs-3g` or kernel `ntfs3`).
- FAT32 support (`vfat` kernel module).

### Running Directly (No Installation)
```bash
git clone https://github.com/khoipm06/redivivus.git
cd redivivus

# Run directly via python module
sudo python3 -m redivivus scan
```

### Installation via pip
```bash
sudo pip install .
sudo redivivus scan
```

---

## Command-Line Usage

### 1. Scan System Storage
Discovers all block devices, Windows installations, EFI partitions, and firmware boot entries:

```bash
sudo redivivus scan
```

Output format (clean Markdown table):
```markdown
# Redivivus Hardware & Boot Diagnostic Report (v0.1.0)

- **Firmware Environment**: `UEFI`
- **Detected Boot Mode**: `UEFI`
- **Total Partitions Discovered**: 14

## 1. Detected Windows Installations
| Device         | Product      | UEFI Capable | Legacy Capable | BCD-Template | Mount          |
| -------------- | ------------ | ------------ | -------------- | ------------ | -------------- |
| /dev/nvme0n1p2 | Windows (NT) | Yes          | Yes            | Found        | /media/windows |

## 2. Detected EFI System Partitions (ESP)
| Device         | Disk         | Part # | FSType | Label | Size   | Mount     |
| -------------- | ------------ | ------ | ------ | ----- | ------ | --------- |
| /dev/nvme0n1p1 | /dev/nvme0n1 | 1      | vfat   | ESP   | 275.0M | /boot/efi |

## 3. UEFI NVRAM Boot Entries
| Boot #   | Active | Label                | File Path | Is Windows |
| -------- | ------ | -------------------- | --------- | ---------- |
| Boot0005 | *      | Windows Boot Manager | (N/A)     | Yes        |
```

For scripting or automated pipelines, output JSON:
```bash
sudo redivivus scan --json
```

---

### 2. Diagnose Boot Health
Checks partition layouts and identifies common failure modes (missing ESP, inactive NVRAM entry, missing BCD):

```bash
sudo redivivus diagnose
```

---

### 3. Recover Windows Bootloader

#### Automated Recovery (Interactive Confirmation)
```bash
sudo redivivus recover
```

#### Non-Interactive Recovery (for scripts / headless rescue)
```bash
sudo redivivus recover --yes
```

#### Safe Dry-Run Simulation (No Changes Made)
```bash
sudo redivivus recover --dry-run
```

#### Explicit Target Selection
When multiple Windows or ESP partitions exist:
```bash
sudo redivivus recover --win /dev/nvme0n1p2 --esp /dev/nvme0n1p1 --mode UEFI -y
```

#### Available Flags
| Flag | Description |
| ---- | ----------- |
| `--win <dev>` | Specify Windows OS partition (e.g. `/dev/nvme0n1p2`) |
| `--esp <dev>` | Specify target EFI System Partition (e.g. `/dev/nvme0n1p1`) |
| `--mode <AUTO\|UEFI\|BIOS>` | Force boot mode (defaults to `AUTO`) |
| `--dry-run` | Simulate and preview all actions without writing to disk |
| `--no-fallback` | Skip deploying `\EFI\Boot\bootx64.efi` |
| `--no-nvram` | Skip modifying motherboard UEFI NVRAM entries |
| `--no-backup` | Skip pre-flight safety backup of existing ESP files |
| `-y`, `--yes` | Confirm all prompts automatically |
| `--json` | Format scan output as JSON |

---

## Verification & Testing

Redivivus includes an exhaustive unit and integration test suite covering:
- Parsing of diverse `lsblk` and `blkid` formats.
- Real-world `efibootmgr -v` multi-boot firmwares.
- Binary BCD registry hive validation and synthesis.
- Filesystem asset deployment and case-insensitive matching.
- Pre-flight backup and post-recovery verification pipelines.

Run the test suite:
```bash
python3 -m unittest discover tests
```

---

## Safety & Ethics

Redivivus is built with defense-in-depth safety:
- **Non-destructive Scanning**: `scan` and `diagnose` never modify block devices.
- **Read-Only Probing**: Inspects Windows partitions via read-only mounts.
- **Automatic Backups**: Preserves prior boot structures prior to modification.
- **Fail-Safe Checks**: Verifies binary headers and PE32+ structures before marking any operation complete.

---

## License

This project is licensed under the [MIT License](LICENSE).
