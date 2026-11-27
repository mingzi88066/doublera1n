#!/usr/bin/env python3
"""Build a *mixed-version* boot set: the current iBoot boots another iOS build's kernel.

`doublera1n` normally boots one build end to end. This builds the artifact set for the other direction: the
device's own (clear-text) iBSS/iBEC/iBoot stay as they are, while the kernel, the DeviceTree and every
coprocessor firmware come from a second, older IPSW. Only builds whose iBoot is in the clear can be booted this
way - iOS 16.x iBoot is GID-encrypted, so on an A13 the *newer* iBoot has to do the booting.

Why the firmware has to come from the second build: each coprocessor driver in the kernel checks the protocol
version its firmware reports. Booting an iOS 16.1 kernel with the 27.x SIO firmware panics in
AppleSmartIO ("Unexpected SIO Protocol version 10 - 9"); AOP/ANE/ISP/AVE/GFX are the same shape of check.

  MIX_IPSW     the second build's .ipsw (kernel, DeviceTree, coprocessor firmware, SEP)
  MIX_KERNEL   Kernelcache.img4 already built for that build (mk_kernel.py + its records)
  OSB_WORK     output root  -> $OSB_WORK/osb
  OSB_PRIMARY  the current build's built osb/ dir (iBSS.raw, iBEC.img4, DeviceTree.img4, RestoreTrustCache.img4)
  OSB_OUT      tickets: apticket.im4m is what iBoot reads back as /chosen/boot-manifest-hash
  OSB_BOARD    device class

usage: mk_mixfw.py [--dt primary|secondary] [--sep primary|secondary] [--skip SIO,WCH] [--out DIR]
"""
import argparse, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
JB = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import mkprofile, osbcfg, dt_patch
from pyimg4 import IM4P, IM4M, IMG4

# boot_chain.py's upload order; the fourcc is what iBoot's `firmware` handler expects for that name
ORDER = ['ANE', 'WCH', 'PMP', 'AOP', 'AVE', 'GFX', 'ISP', 'SIO']
FOURCC = {'ANE': 'anef', 'WCH': 'wchf', 'PMP': 'pmpf', 'AOP': 'aopf', 'AVE': 'avef',
          'GFX': 'gfxf', 'ISP': 'ispf', 'SIO': 'siof', 'SEP': 'rsep'}
KEY = {'WCH': 'WCHFirmwareUpdater'}
FROM_PRIMARY = ['iBSS.raw', 'iBEC.img4', 'RestoreTrustCache.img4']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dt', choices=['primary', 'secondary'], default='primary')
    ap.add_argument('--sep', choices=['primary', 'secondary'], default='secondary')
    ap.add_argument('--skip', default='', help='comma separated img4 names to leave out (SIO,WCH,...)')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    out = a.out or osbcfg.work('osb')
    primary = os.environ.get('OSB_PRIMARY') or osbcfg.work('osb')
    mix_ipsw = os.environ.get('MIX_IPSW')
    kernel = os.environ.get('MIX_KERNEL')
    if not mix_ipsw or not kernel:
        sys.exit('!! set MIX_IPSW (second build .ipsw) and MIX_KERNEL (Kernelcache.img4 for it)')
    if os.path.abspath(primary) == os.path.abspath(out):
        sys.exit('!! --out must differ from OSB_PRIMARY (that set is the source of iBSS/iBEC)')
    os.makedirs(out, exist_ok=True)

    ticket = IM4M(open(os.path.join(osbcfg.OUT, 'apticket.im4m'), 'rb').read())
    fw = mkprofile.Firmware(mix_ipsw, osbcfg.BOARD)
    print(f'second build : iOS {fw.version} ({fw.build}) board {osbcfg.BOARD}')
    skip = {x.strip() for x in a.skip.split(',') if x.strip()}

    for name in FROM_PRIMARY:
        shutil.copy2(os.path.join(primary, name), os.path.join(out, name))
    shutil.copy2(kernel, os.path.join(out, 'Kernelcache.img4'))
    print('kept from main build :', ', '.join(FROM_PRIMARY))

    def shipped(key):
        return key in fw.ident['Manifest'] and bool(fw.ident['Manifest'][key]['Info'].get('Path'))

    for name in ORDER:
        if name in skip or not shipped(KEY.get(name, name)):
            print(f'  {name:4s} skipped')
            continue
        p = IM4P(open(fw.comp(KEY.get(name, name)), 'rb').read())
        p.fourcc = FOURCC[name]
        open(os.path.join(out, name + '.img4'), 'wb').write(IMG4(im4p=p, im4m=ticket).output())
        print(f'  {name:4s} {p.description:8s} {fw.comp(KEY.get(name, name))}')

    if a.sep == 'secondary':
        p = IM4P(open(fw.comp('SEP'), 'rb').read())
        p.fourcc = 'rsep'                     # the IPSW ships 'sepi'; `rsepfirmware` takes 'rsep'
        open(os.path.join(out, 'SEP.img4'), 'wb').write(IMG4(im4p=p, im4m=ticket).output())
        print('  SEP  from second build')
    else:
        shutil.copy2(os.path.join(osbcfg.OUT, 'rsep.img4'), os.path.join(out, 'SEP.img4'))
        print('  SEP  from main build')

    if a.dt == 'secondary':
        p = IM4P(open(fw.comp('DeviceTree'), 'rb').read())
        p.payload.decompress()
        open('/tmp/mix-dt.raw', 'wb').write(bytes(p.payload.output().data))
        ops = list(dt_patch.OS2_OPS if osbcfg.OS2 else dt_patch.OSB_OPS)
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, 'dt_patch.py'), '/tmp/mix-dt.raw',
                            '/tmp/mix-dt.patched.raw'] + ops, capture_output=True, text=True)
        if r.returncode:
            sys.exit('!! patching the second build DeviceTree failed:\n' + r.stdout + r.stderr)
        raw = open('/tmp/mix-dt.patched.raw', 'rb').read()
        open(os.path.join(out, 'DeviceTree.img4'), 'wb').write(IMG4(im4p=IM4P(fourcc='rdtr', payload=raw),
                                                                   im4m=ticket).output())
        print('  DT   from second build, patched with', 'OS2_OPS' if osbcfg.OS2 else 'OSB_OPS')
    else:
        shutil.copy2(os.path.join(primary, 'DeviceTree.img4'), os.path.join(out, 'DeviceTree.img4'))
        print('  DT   from main build (already patched)')
    print(f'wrote {out}: {len(os.listdir(out))} files')


if __name__ == '__main__':
    main()
