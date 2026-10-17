#!/usr/bin/env python3
"""Fits Talos's hack/modules-arm64.txt to the BSP kernel's module tree.

Talos v1.14.0 lists the modules its rootfs carries by path, written for 6.18.
The Talos build runs `install -D` on every listed path and fails on the first
one that does not exist, then fails again if depmod warns about a missing
dependency. On the 6.1 BSP tree some modules are built in, renamed or absent,
and some have dependencies the 6.18 list never needed.

So: keep every listed module that exists, add the dependency closure of what
is kept from modules.dep, and print what was dropped so the change is visible
in the build log.

Usage: modules-arm64.py <talos modules-arm64.txt> <lib/modules/KVER dir>
Writes the fitted list to stdout.
"""

import os
import sys


def main() -> int:
    listing, moddir = sys.argv[1], sys.argv[2]

    deps: dict[str, list[str]] = {}
    with open(os.path.join(moddir, "modules.dep"), encoding="utf-8") as f:
        for line in f:
            mod, _, rest = line.strip().partition(":")
            deps[mod] = rest.split()

    with open(listing, encoding="utf-8") as f:
        wanted = [line.strip() for line in f if line.strip()]

    kept, dropped = [], []
    for mod in wanted:
        (kept if os.path.exists(os.path.join(moddir, mod)) else dropped).append(mod)

    closure = set(kept)
    queue = list(kept)
    while queue:
        for dep in deps.get(queue.pop(), []):
            if dep not in closure:
                closure.add(dep)
                queue.append(dep)

    added = sorted(closure - set(kept))

    for mod in dropped:
        print(f"dropped (not built as a module on this kernel): {mod}", file=sys.stderr)
    for mod in added:
        print(f"added (dependency of a kept module): {mod}", file=sys.stderr)
    print(f"{len(wanted)} listed, {len(kept)} kept, {len(dropped)} dropped, {len(added)} added",
          file=sys.stderr)

    sys.stdout.write("".join(f"{m}\n" for m in sorted(closure)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
