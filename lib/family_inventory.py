#!/usr/bin/env python3
"""Per-family coverage report for one build: which kernel patch families still resolve, which do not.

`precheck.py` stops at the first family that fails, which is the right behaviour before touching a phone but the
wrong one when porting a new iOS major: it hides how much of the patch set is missing. This runs every family of
the kernel plans against a kernelcache on its own and prints one line per family, so a port can be planned from
the list instead of from a stack trace.

usage: family_inventory.py <kernelcache IM4P | raw payload> [--label TEXT]
Exit status 0 always (the report is the output; the family list is not a check).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PF = os.path.join(HERE, 'patchfinder')
if PF not in sys.path:
    sys.path.insert(0, PF)                     # flat imports, like resolve.py / pf.py
import core
import kernel_cm
import kernel_policy
import kernel_restore

FAMILIES = [  # order = the order KERNEL_BOOT_JIT runs them in (resolve.py)
    (kernel_restore, 'identity'), (kernel_restore, 'panic'), (kernel_restore, 'amfi'),
    (kernel_restore, 'ppl_trust_cache_required'),
    (kernel_policy, 'boot_policy'), (kernel_policy, 'aks'), (kernel_policy, 'sep_silence'),
    (kernel_policy, 'sandbox_compatibility'), (kernel_policy, 'codesigning_required'),
    (kernel_policy, 'exception_port'), (kernel_policy, 'vm_fault_cs_bypass'),
    (kernel_cm, 'credential_manager'), (kernel_cm, 'valeria_composite'),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('kernelcache', help='an IPSW kernelcache IM4P or a raw (decompressed) payload')
    ap.add_argument('--label', default='', help='name to print in the header (default: the file name)')
    a = ap.parse_args()
    artifact = core.FirmwareArtifact.from_bytes(open(a.kernelcache, 'rb').read())
    image = core.BinaryImage(artifact.payload)
    print(f"== {a.label or os.path.basename(a.kernelcache)}  |  {artifact.container_description}  |  "
          f"{len(artifact.payload)} bytes", flush=True)
    ok = 0
    for module, name in FAMILIES:
        try:
            records = getattr(module, name)(image)
        except NotImplementedError as error:
            print(f"   TODO {name:26s} not implemented: {error}")
        except Exception as error:                 # PatchfinderError and anything a resolver raises on a new layout
            print(f"   FAIL {name:26s} {type(error).__name__}: {str(error)[:100]}")
        else:
            ok += 1
            print(f"   OK   {name:26s} {len(records):3d} records")
    print(f"   -- {ok}/{len(FAMILIES)} families resolved", flush=True)


if __name__ == '__main__':
    main()
