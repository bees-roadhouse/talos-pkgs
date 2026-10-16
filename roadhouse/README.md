# Talos on the Rockchip BSP kernel (RK3588)

> **Dead end, kept on purpose (2026-10-16).** This branch builds Rockchip's BSP
> 6.1 kernel for Talos v1.14.0, and the kernel builds, but Talos cannot run on
> it: machined's `/etc` overlay needs `FSCONFIG_SET_FD` with `lowerdir+`
> (Linux 6.8), and 6.1's overlayfs is not even on the new mount API (6.5).
> Booted in QEMU it stops with `operation not supported: key="lowerdir+"`.
> The work continues on BSP 6.12 in the default branch's PR (#1); see
> brh-infra#442. Do not build on this branch.

This fork of [siderolabs/pkgs](https://github.com/siderolabs/pkgs) builds Talos
Linux v1.14.0 with Rockchip's BSP 6.1 kernel instead of mainline 6.18, for the
Bee's Roadhouse Orange Pi 5 Plus nodes (talos-4, talos-5). The reason is
hardware video: Rockchip's MPP service (`/dev/mpp_service`) and the RGA
blitter (`/dev/rga`) exist only in the BSP kernels, mainline has no RK3588
encoder, and nectar's transcoder (jellyfin-ffmpeg with `--enable-rkmpp
--enable-rkrga`) needs both. Decided by Nate on 2026-10-16.

The default branch, `talos-v1.14`, is pkgs at `2f03590` (the commit Talos
v1.14.0 was built from) plus the change below; it plays the part `main` plays
in our other repos. `main` is upstream's, left as forked: the org's
default-branch ruleset refuses rewriting it, and a branch per Talos minor
release is what an upgrade to 1.15 will need anyway. Upstream's own workflows
run on Sidero's self-hosted runners and were removed.

## What changed against pkgs

- **Kernel source** (`Pkgfile`, `kernel/prepare/pkg.yaml`): Armbian's
  `linux-rockchip` at a pinned commit of branch `rk-6.1-rkr7.2` (Linux 6.1.172),
  which is the Rockchip BSP plus the Orange Pi 5 Plus device tree. Armbian's
  `vendor` kernel uses the same branch. Its `vendor-next` branch
  (`rk-6.12-rkr1`) also carries MPP and RGA, and is the fallback if 6.1 hits a
  wall with Talos's userspace.
- **Kernel config** (`kernel/build/config-arm64`): generated, never edited.
  `kernel/build/bsp/make-config.sh` takes Talos's own v1.14.0 arm64 config
  (`bsp/talos-1.14.0-config-arm64`), lets `olddefconfig` drop what 6.1 does
  not know, and merges `bsp/rk3588-bsp.fragment` on top: the BSP's names for
  the RK3588 SoC glue, PMIC, PCIe, MPP and RGA, with DRM and every non-Rockchip
  SoC family switched off. The fragment says why for each block.
- **Hardening check** (`kernel/build/scripts/filter-hardened-check.py`): the
  same check Talos runs, with seven arm64 options ignored because they do not
  exist in 6.1 (each named, with the version that added it).
  `CONFIG_DEBUG_CREDENTIALS`, which 6.1 still has, is turned on to satisfy it.
- **Patches** (`kernel/build/patches`): upstream's 6.18 patches are gone.
  `0001` gives the board a plain serial console on UART2 in place of the
  vendor FIQ debugger, so `console=ttyS2,1500000` keeps working. `0002` drops
  four device-table entries that duplicate earlier ones (no behaviour change),
  because Talos fails its rootfs build on any `depmod` warning.

## What CI builds

`.github/workflows/rk3588-bsp.yaml`, natively on `ubuntu-24.04-arm`, pushes to
`ghcr.io/bees-roadhouse/talos-rk3588-bsp/*`:

| Image | What it is |
|---|---|
| `kernel` | this tree's `kernel` target |
| `sbc-rockchip` | upstream's sbc-rockchip v0.2.1 overlay with the BSP dtb swapped in (`overlay/Containerfile`) |
| `imager` | siderolabs/talos v1.14.0 built against that kernel; `hack/modules-arm64.txt` fitted to the 6.1 module tree by `talos/modules-arm64.py` |
| `installer` | the imager's `installer` output with the overlay (`spi_boot: true`), the nodes' two extra kernel args and their three extensions |

The talos binaries keep version `v1.14.0` and upstream's compiled-in image
references; only the image names and tags are ours.

`verify-installer.sh <ref>` pulls an installer and runs
`inspect_installer.py` on it: kernel banner and release, the config recovered
from IKCONFIG, the dtb's MPP and RGA nodes, the boot cmdlines and the
extension manifest. Run against the upstream installer it must fail the BSP
checks, which is how to tell it actually looked.

## Regenerating the config

From an unpacked BSP tree, inside the Talos toolchain (`ghcr.io/siderolabs/tools`
with `ghcr.io/siderolabs/llvm` copied over it, both at the Pkgfile's
`TOOLS_REV`):

```sh
kernel/build/bsp/make-config.sh /path/to/talos-pkgs/kernel/build
```
