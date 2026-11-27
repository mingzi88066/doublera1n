"""Kernel platform-error families: keep an AMCC bus error in the log instead of panicking.

The AppleT8030PlatformErrorHandler reports every cache-controller fault through one of several byte-formatted
phrases. The AFERRLOG variant ends in `Ignored %s` / `"%s" @%s:%d`, i.e. the same routine decides whether the fault
is fatal:

    cbz   w25, <fatal>          ; w25 = pending-sticky count, 0 means "panic"
    ...   adrp/add x0, "Ignored %s"; bl log
    ...   clear the status register; retab
  <fatal>:
    bl    <noreturn panic stub>

NOPping that `cbz` turns the fault into the log line it already knows how to print, which is what a mixed-version
boot needs: the firmware/environment mismatch that produces the fault is under investigation, and a panic hides
everything the kernel would do next. This is a diagnostic patch, not something a shipping profile should carry.

The site is located semantically (anchor string -> its unique ADRP+ADD -> nearest preceding conditional branch that
jumps at a BL-only stub), so a build whose shape differs raises instead of patching an unrelated branch.
"""
from __future__ import annotations

from typing import List

try:
    from . import core                     # imported as a package
except ImportError:                        # flat import: the package directory is on sys.path
    import core

PatchfinderError, PatchRecord = core.PatchfinderError, core.PatchRecord
ARM64, MachOLayout = core.ARM64, core.MachOLayout

COMPONENT = 'kernelcache'
PREFIXES = {'amcc_log_only': ('kernel.amcc.',), 'iop_state_timeout': ('kernel.iop.',),
            'sio_protocol_accept': ('kernel.iop.',), 'apfs_root_live_fs': ('kernel.apfs.',),
            'insecure_boot_null': ('kernel.apfs.',)}

ANCHOR = 'Ignored %s'
SEARCH_BACK = 0x30          # the guard sits immediately before the log block

TIMEOUT_ANCHOR = 'No response received in %llds'
MAX_FRAGMENT = 0x200        # a cold panic fragment is far shorter than this

SIO_ANCHOR = 'Unexpected SIO Protocol version %u - %u'
APFS_ANCHOR = 'Failed to find the root snapshot'
INSECURE_ANCHOR = 'insecure boot is enabled'


def _c_string_start(image: core.BinaryImage, offset: int) -> int:
    return image.data.rfind(b'\0', 0, offset) + 1


def _is_bl(word: int) -> bool:
    return word & 0xFC00_0000 == 0x9400_0000


def amcc_log_only(image: core.BinaryImage) -> List[PatchRecord]:
    """NOP the fatal guard of the AFERRLOG branch so an AMCC address error is logged as `Ignored`."""
    layout = MachOLayout.of(image)
    starts = {_c_string_start(image, hit) for hit in image.find_all_utf8(ANCHOR, nul_terminated=False)}
    sites = []
    for start in sorted(starts):
        for reference in layout.adrp_add_references(start):
            for offset in range(reference.adrp_offset - 4, reference.adrp_offset - SEARCH_BACK - 4, -4):
                if offset < 0:
                    continue
                address = layout.virtual_address(offset)
                if address is None:
                    continue
                word = image.read_u32(offset)
                target = ARM64.conditional_target(word, address)
                if target is None:
                    continue
                target_offset = layout.file_offset(target)
                if target_offset is None or not _is_bl(image.read_u32(target_offset)):
                    continue
                sites.append((offset, target_offset))
                break
    sites = sorted(set(sites))
    if len(sites) != 1:
        if not sites:
            raise PatchfinderError.no_candidate(f'AMCC fatal guard for: {ANCHOR}')
        raise PatchfinderError.ambiguous_candidate(f'AMCC fatal guard for: {ANCHOR}', [o for o, _ in sites])
    offset, stub = sites[0]
    return [_word_patch(image, 'kernel.amcc.ignored-address-error', offset, ARM64.nop,
                        'Log AMCC address errors instead of panicking',
                        (f'diagnostic phrase: {ANCHOR}',
                         'unique ADRP+ADD reference to that C string',
                         f'nearest preceding conditional branch jumps at the BL-only panic stub at {stub:#x}'))]


def _word_patch(image: core.BinaryImage, id: str, offset: int, replacement: int,  # noqa: A002
                summary: str, evidence) -> PatchRecord:
    return PatchRecord.from_words(id=id, component=COMPONENT, offset=offset, original=image.read_u32(offset),
                                  replacement=replacement, summary=summary, evidence=evidence)


def _panic_fragment(image: core.BinaryImage, layout: MachOLayout, string_offset: int) -> int:
    """The PACIBSP prologue of the cold function that formats this panic: the ADRP+ADD lives inside it."""
    references = layout.adrp_add_references(string_offset)
    if len(references) != 1:
        raise PatchfinderError.ambiguous_anchor(f'{TIMEOUT_ANCHOR}: {len(references)} xref(s)')
    offset = references[0].adrp_offset
    for start in range(offset, max(0, offset - MAX_FRAGMENT), -4):
        if image.read_u32(start) == ARM64.pacibsp:
            return start
    raise PatchfinderError.no_candidate(f'panic fragment prologue for: {TIMEOUT_ANCHOR}')


def iop_state_timeout(image: core.BinaryImage) -> List[PatchRecord]:
    """Keep a coprocessor state-transition timeout as an IOReturn instead of a panic.

    RTBuddy::_setManagedStateGated compares the wait result with kIOReturnTimeout (0xe00002d6), skips the panic
    when it differs, and otherwise `bl`s a cold fragment that formats "No response received in %llds, %s not
    started? (%%x)". The instruction right after that call already stores kIOReturnTimeout-adjacent status and
    returns, so dropping the call keeps the failure path and only removes the panic.
    """
    layout = MachOLayout.of(image)
    fragments = set()
    for hit in image.find_all_utf8(TIMEOUT_ANCHOR, nul_terminated=False):
        fragments.add(_panic_fragment(image, layout, _c_string_start(image, hit)))
    callers = []
    for fragment in sorted(fragments):
        address = layout.virtual_address(fragment)
        if address is None:
            continue
        for r in layout.executable_file_ranges:
            for offset in range(r.start, r.stop - 4, 4):
                word = image.read_u32(offset)
                if not _is_bl(word):
                    continue
                if ARM64.branch_link_target(word, layout.virtual_address(offset)) != address:
                    continue
                callers.append((offset, fragment))
    callers = sorted(set(callers))
    if len(callers) != 1:
        if not callers:
            raise PatchfinderError.no_candidate(f'timeout panic call for: {TIMEOUT_ANCHOR}')
        raise PatchfinderError.ambiguous_candidate(f'timeout panic call for: {TIMEOUT_ANCHOR}',
                                                   [offset for offset, _ in callers])
    offset, fragment = callers[0]
    # the call must sit on a conditional edge, so falling through is a path the code already has
    previous = image.read_u32(offset - 4)
    condition = ARM64.conditional_target(previous, layout.virtual_address(offset - 4))
    if condition is None or layout.file_offset(condition) == offset:
        raise PatchfinderError.invalid_fixture(f'timeout panic call at {offset:#x} is not branch-guarded')
    return [_word_patch(image, 'kernel.iop.state-timeout-log-only', offset, ARM64.nop,
                        'Return a timeout instead of panicking when a coprocessor does not answer',
                        (f'diagnostic phrase: {TIMEOUT_ANCHOR}',
                         f'single BL to its cold panic fragment at {fragment:#x}',
                         'the BL is the taken edge of a conditional branch and the next instruction '
                         'already produces the error return'))]


def _movz_w(word: int):
    """`mov wN, #imm16` (MOVZ 32-bit) -> (destination register, immediate), else None."""
    if word & 0x7F80_0000 != 0x5280_0000:
        return None
    return word & 0x1f, (word >> 5) & 0xffff


def _cmp_w_imm(word: int):
    """`cmp wN, #imm12` (SUBS/CMP immediate 32-bit) -> (register, immediate), else None."""
    if word & 0x7F00_0000 != 0x7100_0000:
        return None
    return (word >> 5) & 0x1f, (word >> 10) & 0xfff


def sio_protocol_accept(image: core.BinaryImage) -> List[PatchRecord]:
    """Accept any SIO protocol version instead of panicking on a mismatch.

    AppleSmartIO checks the version returned by the coprocessor with `cmp wN, #expected; b.ne <cold block>`;
    the cold block formats "Unexpected SIO Protocol version %u - %u". A mixed boot pairs a 16.1 kernel with a
    27.x SIO coprocessor (v10 instead of v9) precisely because only the newer firmware answers at all, so the
    guard is the remaining gate. The expected version is read from the `mov wN, #imm` in the cold fragment, and
    the guard is the unique `cmp wM, #that-immediate` immediately before a conditional branch whose target is
    inside the call site's cold block.
    """
    layout = MachOLayout.of(image)
    starts = {_c_string_start(image, hit)
              for hit in image.find_all_utf8(SIO_ANCHOR, nul_terminated=False)}
    if len(starts) != 1:
        raise PatchfinderError.ambiguous_anchor(SIO_ANCHOR, len(starts))
    references = layout.adrp_add_references(starts.pop())
    if len(references) != 1:
        raise PatchfinderError.ambiguous_anchor(f'SIO xref: {SIO_ANCHOR}', len(references))
    xref = references[0].adrp_offset

    # the expected version is materialised as `mov wN, #v` just before the format string's ADRP
    expected = None
    for offset in range(xref - 4, max(0, xref - 0x40), -4):
        mov = _movz_w(image.read_u32(offset))
        if mov and mov[1] < 0x100:
            expected = mov[1]
            break
    if expected is None:
        raise PatchfinderError.no_candidate(f'expected SIO protocol version near {SIO_ANCHOR}')

    # locate the one function that formats the panic, then the one BL that calls it
    fragment = _panic_fragment(image, layout, _c_string_start(image, image.find_all_utf8(
        SIO_ANCHOR, nul_terminated=False)[0]))
    fragment_va = layout.virtual_address(fragment)
    callers = []
    for r in layout.executable_file_ranges:
        for offset in range(r.start, r.stop - 4, 4):
            word = image.read_u32(offset)
            if _is_bl(word) and ARM64.branch_link_target(word, layout.virtual_address(offset)) == fragment_va:
                callers.append(offset)
    if len(callers) != 1:
        raise PatchfinderError.ambiguous_candidate(f'SIO version panic call: {SIO_ANCHOR}', callers)
    caller = callers[0]

    # the guard: `cmp wM, #expected` followed by `b.ne` into the cold block that ends at the call
    guards = []
    for offset in range(caller - 4, max(0, caller - 0x800), -4):
        cond = ARM64.conditional_target(image.read_u32(offset), layout.virtual_address(offset))
        if cond is None:
            continue
        destination = layout.file_offset(cond)
        if destination is None or not (offset < destination <= caller):
            continue
        candidate = _cmp_w_imm(image.read_u32(offset - 4))
        if candidate and candidate[1] == expected:
            guards.append(offset)
    if len(guards) != 1:
        if not guards:
            raise PatchfinderError.no_candidate(f'SIO version guard for expected v{expected} at {caller:#x}')
        raise PatchfinderError.ambiguous_candidate(f'SIO version guard for expected v{expected}', guards)
    guard = guards[0]
    return [_word_patch(image, 'kernel.iop.sio-protocol-accept', guard, ARM64.nop,
                        'Do not panic when the SIO coprocessor reports a different protocol version',
                        (f'diagnostic phrase: {SIO_ANCHOR}',
                         f'expected version v{expected} read from the cold fragment at {fragment:#x}',
                         f'unique `cmp wN, #{expected}` + conditional branch before the single caller at '
                         f'{caller:#x}'))]


def apfs_root_live_fs(image: core.BinaryImage) -> List[PatchRecord]:
    """Let APFS return from mountroot instead of panicking when no root snapshot matches.

    `apfs_vfsop_mountroot` formats "Failed to find the root snapshot: ..." in a cold block; the guard is a
    `b.ne` whose fall-through already restores the frame and returns the recorded status. In a mixed boot there
    is no snapshot hash the 16.1 kernel recognises for the 27.x system volume, so NOPping the branch exposes
    whether that status is usable or whether the next failure is the dyld/launchd boundary instead.
    """
    layout = MachOLayout.of(image)
    starts = {_c_string_start(image, hit)
              for hit in image.find_all_utf8(APFS_ANCHOR, nul_terminated=False)}
    if len(starts) != 1:
        raise PatchfinderError.ambiguous_anchor(APFS_ANCHOR, len(starts))
    references = layout.adrp_add_references(starts.pop())
    if len(references) != 1:
        raise PatchfinderError.ambiguous_anchor(f'APFS xref: {APFS_ANCHOR}', len(references))
    xref = references[0].adrp_offset

    # The panic block has two entry points in this XNU: a `b.ne` and a `tbnz` a few instructions later that
    # jumps past the first one's landing pad into the block's error-code formatting. NOPping only the first
    # leaves the second live and the kernel still panics. The block's start is the earliest landing pad within
    # 0x40 bytes before the phrase's ADRP; guards are conditional branches from *before* that start into the
    # block. Branches whose source is already inside the block are error-code switch arms, not entries.
    cold_start = xref
    for offset in range(max(0, xref - 0x800), xref - 4, 4):
        word = image.read_u32(offset)
        if (word & 0xFC00_0000) == 0x9400_0000:  # BL
            continue
        target = core.ARM64.any_conditional_target(word, layout.virtual_address(offset))
        if target is None:
            continue
        destination = layout.file_offset(target)
        if destination is not None and offset < destination < xref and xref - destination <= 0x40:
            cold_start = min(cold_start, destination)
    if cold_start == xref:
        raise PatchfinderError.no_candidate(f'APFS root-snapshot panic block for: {APFS_ANCHOR}')

    guards = []
    for offset in range(max(0, cold_start - 0x800), cold_start, 4):
        word = image.read_u32(offset)
        if (word & 0xFC00_0000) == 0x9400_0000:  # BL: not a guard
            continue
        target = core.ARM64.any_conditional_target(word, layout.virtual_address(offset))
        if target is None:
            continue
        destination = layout.file_offset(target)
        if destination is not None and cold_start <= destination <= xref:
            guards.append(offset)
    guards = sorted(set(guards))
    if not guards:
        raise PatchfinderError.no_candidate(f'APFS root-snapshot panic guards for: {APFS_ANCHOR}')
    return [_word_patch(image, f'kernel.apfs.root-from-live-fs.{index}', guard, ARM64.nop,
                        'Return from APFS mountroot instead of panicking on an unmatched root snapshot',
                        (f'diagnostic phrase: {APFS_ANCHOR}',
                         f'unique ADRP+ADD reference to the phrase at {xref:#x}',
                         f'conditional branch at {guard:#x} enters the panic block at {cold_start:#x}',
                         f'{len(guards)} entry point(s) patched'))
            for index, guard in enumerate(guards)]


def _adrp_imm(word: int):
    """Decode an ADRP: return (page-aligned target when evaluated at `at`,才能真正判断)."""
    return word & 0x9F00_001F == 0x9000_0000


def insecure_boot_null(image: core.BinaryImage) -> List[PatchRecord]:
    """Skip the NULL vtbl dereference in the insecure-boot path when iBoot forwarded no root hash.

    With the APFS root-live-fs patch applied, a mixed boot that has no `system-volume-auth-blob` reaches
    `insecure_boot_enabled()`. That routine reports success but leaves its output object NULL because no root
    hash was forwarded; the caller then does `ldr x0,[sp,#slot]; ldr x16,[x0]; ...; blraa x8,x17` and faults on
    `ldr x16,[x0]` with FAR 0. The function already has a merge point a few instructions later (the failure arm
    reconverges before the frame restore), so turn the dereference into `cbz x0, <merge>`: NULL skips the whole
    vtable call, non-NULL keeps the original path byte-for-byte.

    Located semantically: unique ADRP+ADD xref to the "insecure boot is enabled" phrase, then the nearest
    preceding `ldr xN,[sp,#slot]` whose next instruction is `ldr xM,[xN]` (a PAC vtbl deref, followed within
    0x20 by AUDA + BLRAA). The merge point is the unconditional branch's landing pad after the BLRA.
    """
    layout = MachOLayout.of(image)
    starts = {_c_string_start(image, hit)
              for hit in image.find_all_utf8(INSECURE_ANCHOR, nul_terminated=False)}
    if len(starts) != 1:
        raise PatchfinderError.ambiguous_anchor(INSECURE_ANCHOR, len(starts))
    references = layout.adrp_add_references(starts.pop())
    if len(references) != 1:
        raise PatchfinderError.ambiguous_anchor(f'insecure-boot xref: {INSECURE_ANCHOR}', len(references))
    xref = references[0].adrp_offset

    sites = []
    # The deref sits just AFTER the phrase's ADRP+ADD (the log is emitted before the failing call), so scan a
    # window centred on the xref rather than behind it.
    for slot in range(max(0, xref - 0x80), xref + 0x100, 4):
        word = image.read_u32(slot)
        # ldr xN, [sp, #imm12]  (unsigned offset 64-bit, Rn=31)
        if word & 0xFFC0_0000 != 0xF940_0000 or (word >> 5) & 0x1f != 31:
            continue
        reg = word & 0x1f
        nxt = image.read_u32(slot + 4)
        # ldr xM, [xN]  (unsigned offset imm12=0, Rn must equal reg from the slot load)
        if nxt & 0xFFC0_0000 != 0xF940_0000 or (nxt >> 5) & 0x1f != reg or (nxt >> 10) & 0xfff != 0:
            continue
        # confirm the PAC vtable indirect call (BLRAA/BLRAB: 0xD63Fxxxx / 0xD73Fxxxx) just after
        window = image.bytes_at(slot + 8, 0x30)
        words = [int.from_bytes(window[i:i + 4], 'little') for i in range(0, len(window), 4)]
        if not any((w >> 16) in (0xD63F, 0xD73F) for w in words):
            continue
        sites.append((slot, slot + 4))
    sites = sorted(set(sites))
    if len(sites) != 1:
        if not sites:
            raise PatchfinderError.no_candidate(f'insecure-boot NULL vtbl deref for: {INSECURE_ANCHOR}')
        raise PatchfinderError.ambiguous_candidate(f'insecure-boot NULL vtbl deref for: {INSECURE_ANCHOR}',
                                                   [d for _, d in sites])
    slot, deref = sites[0]
    reg = image.read_u32(slot) & 0x1f

    # merge point: the landing pad of the first unconditional `b` after the BLRA sequence
    merge = None
    for offset in range(deref + 8, deref + 0x40, 4):
        word = image.read_u32(offset)
        if word & 0xFC00_0000 == 0x1400_0000:  # B (imm26)
            delta = word & 0x03FF_FFFF
            if delta & 0x0200_0000:
                delta -= 0x0400_0000
            merge = offset + delta * 4
            break
    if merge is None or not (deref < merge < len(image.data)):
        raise PatchfinderError.no_candidate(f'insecure-boot merge point after {deref:#x}')

    # cbz xN, <merge>  (imm19 = (merge - deref)/4, signed)
    delta = (merge - deref) // 4
    if not 0 < delta < 0x8_0000:
        raise PatchfinderError.invalid_fixture(f'insecure-boot merge point out of cbz range: {merge:#x}')
    cbz = 0xB400_0000 | (delta << 5) | reg
    return [_word_patch(image, 'kernel.apfs.insecure-boot-null', deref, cbz,
                        'Skip the NULL vtbl dereference when insecure boot has no forwarded root hash',
                        (f'diagnostic phrase: {INSECURE_ANCHOR}',
                         f'unique ADRP+ADD reference at {xref:#x}',
                         f'PAC vtbl deref at {deref:#x} reads the object loaded at {slot:#x}',
                         f'merge point at {merge:#x} reconverges the existing failure arm'))]
