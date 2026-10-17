#!/usr/bin/env python3
"""Proves a Talos installer image carries what it claims, without booting it.

Reads an installer image exported to a tar (`crane export --platform
linux/arm64 <ref> installer.tar`) and checks, from the bytes alone:

  - the kernel banner inside the UKI's zboot payload: release 6.12, rockchip;
  - the kernel config, recovered from the same payload's IKCONFIG blob: the
    MPP and RGA drivers, the RK3588 SoC glue, and a sample of the options
    Talos needs (the full hardening set is enforced by the pkgs build itself);
  - that the dtb the overlay installs on the EFI partition is mainline's, so
    an A/B rollback still boots, and that the UKI's .dtb section carries the
    BSP dtb, describing the MPP service, its cores and the RGA cores;
  - the UKI's .cmdline, and the three system extensions in its initrd.

The upstream installer for the nodes' current schematic is the negative
control: run against it, the BSP checks must fail.

Needs `zstd` on PATH (and `xz` if the kernel payload is xz). Exits non-zero
if any check fails, and prints one line per check either way.

Usage: inspect_installer.py installer.tar [--expect-release-prefix 6.12.]
"""

import argparse
import gzip
import re
import struct
import subprocess
import sys
import tarfile
import zlib

UKI = "usr/install/arm64/vmlinuz.efi"
DTB = "overlay/artifacts/arm64/dtb/rockchip/rk3588-orangepi-5-plus.dtb"

# Options behind nectar's four device paths (/dev/mpp_service, /dev/rga,
# /dev/dri, /dev/dma_heap), plus the SoC glue the board cannot boot without.
BSP_OPTIONS = {
    "CONFIG_ROCKCHIP_MPP_SERVICE": "y",
    "CONFIG_ROCKCHIP_MPP_RKVDEC2": "y",
    "CONFIG_ROCKCHIP_MPP_RKVENC2": "y",
    "CONFIG_ROCKCHIP_MPP_VDPU2": "y",
    "CONFIG_ROCKCHIP_MPP_VEPU2": "y",
    "CONFIG_ROCKCHIP_MPP_JPGDEC": "y",
    "CONFIG_ROCKCHIP_MPP_AV1DEC": "y",
    "CONFIG_ROCKCHIP_MULTI_RGA": "y",
    "CONFIG_CPU_RK3588": "y",
    "CONFIG_CLK_RK3588": "y",
    "CONFIG_PCIE_DW_ROCKCHIP": "y",
    "CONFIG_MFD_RK806_SPI": "y",
    "CONFIG_REGULATOR_RK860X": "y",
    "CONFIG_ROCKCHIP_IOMMU": "y",
    "CONFIG_DMABUF_HEAPS_SYSTEM": "y",
    "CONFIG_DMABUF_HEAPS_CMA": "y",
    # /dev/dri: the BSP Rockchip display driver registers the render node.
    "CONFIG_DRM": "y",
    "CONFIG_DRM_ROCKCHIP": "y",
    "CONFIG_ROCKCHIP_VOP2": "y",
    # The HDMI PHYs: without them HDMI defers for ever and the DRM device,
    # /dev/dri included, never binds (see rk3588-bsp.fragment).
    "CONFIG_PHY_ROCKCHIP_SAMSUNG_HDPTX": "y",
}

# A sample of what Talos needs at runtime and enforces at build time.
TALOS_OPTIONS = {
    "CONFIG_EFI_STUB": "y",
    "CONFIG_EFI_ZBOOT": "y",
    "CONFIG_MODULE_SIG": "y",
    "CONFIG_MODULE_SIG_ALL": "y",
    "CONFIG_SECURITY_SELINUX": "y",
    "CONFIG_SQUASHFS": "y",
    "CONFIG_OVERLAY_FS": "y",
    "CONFIG_CGROUPS": "y",
    "CONFIG_MEMCG": "y",
    "CONFIG_BPF_SYSCALL": "y",
    "CONFIG_USER_NS": "y",
    "CONFIG_STACKPROTECTOR_STRONG": "y",
    "CONFIG_STRICT_KERNEL_RWX": "y",
    "CONFIG_RANDOMIZE_BASE": "y",
    "CONFIG_IKCONFIG": "y",
}

# Compatible strings the BSP dtb uses for the hardware nectar needs.
DTB_COMPATIBLES = [
    b"rockchip,mpp-service",
    b"rockchip,rkv-encoder-v2-core",
    b"rockchip,rkv-decoder-v2",
    b"rockchip,vpu-decoder-v2",
    b"rockchip,vpu-encoder-v2",
    b"rockchip,rga3_core0",
    b"rockchip,rga2_core0",
    b"rockchip,display-subsystem",
]

EXTENSIONS = ["iscsi-tools", "util-linux-tools", "spin"]

ZSTD_MAGIC = bytes.fromhex("28b52ffd")

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(f"{'PASS' if ok else 'FAIL'}  {what}")
    if not ok:
        failures += 1


def pe_sections(data: bytes) -> list[tuple[str, bytes]]:
    # A multi-profile UKI repeats section names (.cmdline, .profile), so keep
    # them in order rather than in a dict.
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    off = pe + 24 + opt
    out = []
    for _ in range(count):
        name = data[off:off + 8].rstrip(b"\0").decode()
        vsize, _va, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        out.append((name, data[rptr:rptr + min(vsize, rsize)]))
        off += 40
    return out


def zboot_payload(image: bytes) -> bytes:
    # arm64 EFI zboot: "MZ", then "zimg", payload offset and size, compression.
    if image[4:8] != b"zimg":
        return image
    off, size = struct.unpack_from("<II", image, 8)
    comp = image[24:32].rstrip(b"\0").decode()
    payload = image[off:off + size]
    if comp == "gzip":
        return gzip.decompress(payload)
    tool = {"zstd": ["zstd", "-dc"], "xzkern": ["xz", "-dc"], "xz": ["xz", "-dc"]}.get(comp)
    if tool is None:
        raise SystemExit(f"unknown zboot compression {comp!r}")
    return subprocess.run(tool, input=payload, capture_output=True, check=True).stdout


def ikconfig(image: bytes) -> dict[str, str]:
    start = image.find(b"IKCFG_ST")
    end = image.find(b"IKCFG_ED", start)
    if start < 0 or end < 0:
        return {}
    text = zlib.decompress(image[start + 8:end], 16 + zlib.MAX_WBITS).decode()
    return dict(re.findall(r"^(CONFIG_\w+)=(.*)$", text, re.M))


def newc_files(buf: bytes) -> dict[str, bytes]:
    files, pos = {}, 0
    while buf[pos:pos + 6] == b"070701":
        fields = [int(buf[pos + 6 + 8 * i:pos + 14 + 8 * i], 16) for i in range(13)]
        filesize, namesize = fields[6], fields[11]
        name = buf[pos + 110:pos + 110 + namesize - 1].decode(errors="replace")
        if name == "TRAILER!!!":
            break
        data = pos + ((110 + namesize + 3) & ~3)
        files[name] = buf[data:data + filesize]
        pos = data + ((filesize + 3) & ~3)
    return files


def extensions_yaml(initrd: bytes) -> str:
    # Talos's imager appends the system extensions to the initrd as one
    # zstd-compressed newc cpio holding N.sqsh images and extensions.yaml
    # (pkg/imager/extensions/rebuild.go). Find that frame from the end.
    cands, i = [], initrd.find(ZSTD_MAGIC)
    while i >= 0:
        cands.append(i)
        i = initrd.find(ZSTD_MAGIC, i + 1)
    for start in reversed(cands):
        proc = subprocess.run(["zstd", "-dcq"], input=initrd[start:], capture_output=True)
        if proc.returncode == 0 and proc.stdout.startswith(b"070701"):
            files = newc_files(proc.stdout)
            if "extensions.yaml" in files:
                return files["extensions.yaml"].decode()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tar")
    ap.add_argument("--expect-release-prefix", default="6.12.")
    args = ap.parse_args()

    files = {}
    with tarfile.open(args.tar) as t:
        for m in t.getmembers():
            name = m.name.replace(chr(92), "/").lstrip("./")
            if name in (UKI, DTB) and m.isfile():
                files[name] = t.extractfile(m).read()

    check(UKI in files, f"installer carries {UKI}")
    check(DTB in files, f"installer carries {DTB}")
    if UKI not in files:
        return 1

    sections = pe_sections(files[UKI])
    first = {}
    for name, blob in sections:
        first.setdefault(name, blob)

    cmdlines = [blob.rstrip(b"\0").decode() for name, blob in sections if name == ".cmdline"]
    for c in cmdlines:
        print(f"      cmdline: {c}")
    for arg in ("console=ttyS2,1500000", "nvme_core.default_ps_max_latency_us=0", "pcie_aspm=off",
                "earlycon=uart8250,mmio32,0xfeb50000"):
        check(bool(cmdlines) and all(arg in c.split() for c in cmdlines), f"every boot profile's cmdline has {arg}")

    image = zboot_payload(first[".linux"])
    banner = re.search(rb"Linux version (\S+) [^\n\0]*", image)
    release = banner[1].decode() if banner else ""
    print(f"      banner:  {banner[0].decode() if banner else '(none)'}")
    check(release.startswith(args.expect_release_prefix) and "rockchip" in release,
          f"kernel release is {args.expect_release_prefix}x and names rockchip (is {release or 'unknown'})")

    cfg = ikconfig(image)
    check(bool(cfg), "kernel config recovered from IKCONFIG")
    for opt, want in {**BSP_OPTIONS, **TALOS_OPTIONS}.items():
        check(cfg.get(opt) == want, f"{opt}={want} (is {cfg.get(opt, 'unset')})")

    # Two dtbs, two jobs (brh-infra#442). The overlay's dtb is the one file
    # u-boot hands every boot entry from the EFI partition, so it must stay
    # mainline's: talos-5 crash-looped on 2026-10-17 because the BSP dtb sat
    # there and the A/B rollback's mainline kernel booted against it too. The
    # BSP dtb rides in the UKI's .dtb section, for this entry only.
    if DTB in files:
        dtb = files[DTB]
        check(dtb[:4] == b"\xd0\x0d\xfe\xed", "EFI-partition dtb has the FDT magic")
        check(b"xunlong,orangepi-5-plus" in dtb and b"Orange Pi 5 Plus\0" in dtb,
              "EFI-partition dtb is mainline's Orange Pi 5 Plus (xunlong,orangepi-5-plus)")
        check(b"rockchip,mpp-service" not in dtb, "EFI-partition dtb is not the BSP's (no rockchip,mpp-service)")

    uki_dtb = first.get(".dtb", b"")
    check(uki_dtb[:4] == b"\xd0\x0d\xfe\xed", "UKI has a .dtb section with the FDT magic")
    if uki_dtb[:4] == b"\xd0\x0d\xfe\xed":
        # Mainline spells the board xunlong,orangepi-5-plus; the BSP dts
        # spells it rockchip,rk3588-orangepi-5-plus.
        check(b"rockchip,rk3588-orangepi-5-plus" in uki_dtb and b"Orange Pi 5 Plus\0" in uki_dtb,
              "UKI .dtb is the BSP's Orange Pi 5 Plus (rockchip,rk3588-orangepi-5-plus)")
        for compat in DTB_COMPATIBLES:
            check(compat in uki_dtb, f"UKI .dtb describes {compat.decode()}")

    if ".initrd" in first:
        manifest = extensions_yaml(first[".initrd"])
        names = re.findall(r"^\s+name: (\S+)$", manifest, re.M)
        print(f"      extensions: {', '.join(names) or '(none)'}")
        for ext in EXTENSIONS:
            check(ext in names, f"initrd carries the {ext} extension")
    else:
        check(False, "UKI has an .initrd section")

    print(f"{failures} check(s) failed" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
