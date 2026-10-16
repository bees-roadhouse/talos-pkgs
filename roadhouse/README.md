# Talos on the Rockchip BSP kernel (RK3588)

This fork of [siderolabs/pkgs](https://github.com/siderolabs/pkgs) builds Talos
Linux v1.14.0 with a Rockchip BSP kernel instead of mainline 6.18, for the
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

## Status: Talos v1.14 reaches maintenance mode on BSP 6.12 in QEMU

Talos v1.14's machined builds its writable `/etc` from detached mounts handed
to overlayfs as file descriptors, then bind-mounts the overlay with
`open_tree()`. Those are mount-API features from Linux 6.8 to 6.15, and both
BSP trees are older. Booting each in QEMU (`-M virt`, the imager's initramfs):

| Kernel | Result |
|---|---|
| BSP 6.1 (`rk-6.1-rkr7.2`), branch `dead-end/bsp-6.1` | builds; machined stops at `FSCONFIG_SET_FD ... operation not supported: key="lowerdir+"` (6.8). 6.1's overlayfs is not on the new mount API at all (6.5), so this route was dropped. |
| BSP 6.12 (`rk-6.12-rkr1`) + patches `0003`–`0007` | builds; Talos passes all its boot phases up to the config phase and enters **maintenance mode**: udevd, machined, containerd, auditd and syslogd are healthy and apid waits for a config. |

What QEMU cannot show: the RK3588 hardware itself (MPP, RGA, VOP2, PCIe,
the PMIC) and the SPI u-boot booting this kernel. Under QEMU the BSP's
Rockchip DRM driver must be skipped
(`initcall_blacklist=__initstub__kmod_rockchipdrm__869_3458_rockchip_drm_init6`):
it asks the firmware for the DRAM map with an SMC at init, and QEMU's virt
machine has no EL3 to answer it, so the instruction traps. On the board,
TF-A answers unknown SiP calls with an error, and the driver carries on
without the map (`rockchip_gem_get_ddr_info()` checks for NULL).

## What changed against pkgs

- **Kernel source** (`Pkgfile`, `kernel/prepare/pkg.yaml`): Armbian's
  `linux-rockchip` at a pinned commit of `rk-6.12-rkr1` (Linux 6.12.69,
  Armbian's `vendor-next`), which is the Rockchip BSP plus the Orange Pi 5
  Plus device tree, with MPP and RGA. `rk-6.1-rkr7.2` (Armbian's `vendor`)
  was tried first and is kept on branch `dead-end/bsp-6.1` so nobody walks
  it again: it builds, but Talos v1.14 cannot run on 6.1 at all.
- **Kernel config** (`kernel/build/config-arm64`): generated, never edited.
  `kernel/build/bsp/make-config.sh` takes Talos's own v1.14.0 arm64 config
  (`bsp/talos-1.14.0-config-arm64`), lets `olddefconfig` drop what the BSP
  tree does not know, and merges `bsp/rk3588-bsp.fragment` on top: the BSP's
  names for the RK3588 SoC glue, PMIC, PCIe, MPP, RGA and the Rockchip display
  driver (whose render node is nectar's `/dev/dri`), with the discrete-GPU
  drivers and every non-Rockchip SoC family switched off. The fragment says
  why for each block.
- **Hardening check** (`kernel/build/scripts/filter-hardened-check.py`): the
  same check Talos runs, with five arm64 options ignored because 6.12 does not
  have them (each named).
- **Patches** (`kernel/build/patches`), upstream's 6.18 patches removed:
  - `0001`: a plain serial console on UART2 in place of the vendor FIQ
    debugger, so `console=ttyS2,1500000` keeps working.
  - `0002`: drops five device-table entries that duplicate earlier ones (no
    behaviour change), because Talos fails its rootfs build on any `depmod`
    warning.
  - `0003`â€“`0005`: upstream `c2f8fde86892`, `a08557d19ef4` (6.13) and
    `0ff053b98a0f` (6.15), unmodified: mount options as path or fd,
    overlayfs layers by fd, O_PATH fds in `FSCONFIG_SET_FD`.
  - `0006`: upstream `db04662e2f4f` (6.15), adapted to 6.12: detached mounts
    in `clone_private_mount()`.
  - `0007`: upstream's 6.15 detached-mounts series (nine commits, listed in
    the patch with each adaptation), so `open_tree(OPEN_TREE_CLONE)` works on
    a detached mount.

## What CI builds

`.github/workflows/rk3588-bsp.yaml`, natively on `ubuntu-24.04-arm` (the
kernel takes about 45 minutes), pushes to
`ghcr.io/bees-roadhouse/talos-rk3588-bsp/*`:

| Image | What it is |
|---|---|
| `kernel` | this tree's `kernel` target |
| `sbc-rockchip` | upstream's sbc-rockchip v0.2.1 overlay with the BSP dtb swapped in (`overlay/Containerfile`) |
| `imager` | siderolabs/talos v1.14.0 built against that kernel; `hack/modules-arm64.txt` fitted to the BSP module tree by `talos/modules-arm64.py` |
| `installer` | built when the repository variable `RK3588_BSP_INSTALLER` is `true`: the imager's `installer` output with the overlay (`spi_boot: true`), the nodes' two extra kernel args and their three extensions |

The talos binaries keep version `v1.14.0` and upstream's compiled-in image
references; only the image names and tags are ours.

`verify-installer.sh <ref>` pulls an installer and runs
`inspect_installer.py` on it: kernel banner and release, the config recovered
from IKCONFIG, the dtb's MPP and RGA nodes, the boot cmdlines and the
extension manifest. Run against the upstream installer it must fail the BSP
checks, which is how to tell it actually looked.

## Booting it in QEMU

The boot test, with `vmlinuz` and `initramfs.xz` taken from
the imager image's `/usr/install/arm64/`:

```sh
qemu-system-aarch64 -M virt -cpu cortex-a76 -smp 4 -m 4096 -nographic \
  -drive if=pflash,format=raw,readonly=on,file=AAVMF_CODE.fd \
  -drive if=pflash,format=raw,file=vars.fd \
  -kernel vmlinuz -initrd initramfs.xz \
  -append "talos.platform=metal console=ttyAMA0 selinux=1 module.sig_enforce=1"
```

Add Talos's own arguments (`slab_nomerge pti=on`; without them its KSPP
check fails phase 1) and the `initcall_blacklist` above. Success looks like
`entering maintenance service` about 30 seconds in; a kernel that cannot run
Talos shows `controller runtime goroutine error` instead.

## Regenerating the config

From an unpacked BSP tree, inside the Talos toolchain (`ghcr.io/siderolabs/tools`
with `ghcr.io/siderolabs/llvm` copied over it, both at the Pkgfile's
`TOOLS_REV`):

```sh
kernel/build/bsp/make-config.sh /path/to/talos-pkgs/kernel/build
```
