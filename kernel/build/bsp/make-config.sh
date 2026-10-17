#!/usr/bin/env bash
# Regenerates kernel/build/config-arm64 for the Rockchip BSP tree.
#
# Run inside the Talos kernel toolchain (tools + llvm images at the pkgs
# TOOLS_REV) from the root of an unpacked BSP source tree:
#   make-config.sh /path/to/talos-pkgs/kernel/build
#
# Talos's own arm64 config is the base, so every option Talos relies on is kept
# where the 6.1 tree has it; olddefconfig drops what 6.1 does not know, and the
# fragment adds the BSP's RK3588 hardware and media drivers on top.
set -euo pipefail

build="${1:?kernel/build directory}"

cp "${build}/bsp/talos-1.14.0-config-arm64" .config
ARCH=arm64 LLVM=1 ./scripts/kconfig/merge_config.sh -m .config "${build}/bsp/rk3588-bsp.fragment"
make ARCH=arm64 LLVM=1 olddefconfig
cp .config "${build}/config-arm64"
