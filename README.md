# doublera1n: jailbreak your iPhone 11/SE2 by using a new data volume

> [!CAUTION]
> CURRENTLY SUPPORTS iOS: 27.0-27.2 ON MAIN OS

> [!NOTE]
> **iOS 16 fork status (dualboot-iOS16):** the tool now accepts iOS 16.x builds, and iOS 16 firmware needs no
> TXM/SPTM handling (that component only exists since iOS 17 - the profile omits the TXM plans automatically).
> BUT booting iOS 16 is NOT possible yet: the whole SETUP chain (kernel restore + ramdisk set) resolves fully
> there, but the 2nd-Data boot-jit plan still misses the aks / sep_silence / credential-manager / valeria
> families on xnu-8796, and iOS 16's iBSS/iBEC are KBAG-encrypted (LZFSE_ENCRYPTED, GID key)
> while iOS 27's are plain LZFSE - the host cannot patch the iOS 16 bootloaders without their decrypted images.
> See [iOS 16 status](#ios-16-status-what-works-what-is-missing) below. `./dualboot.py install` refuses builds
> whose profile still has `pending_families`.

doublera1n runs the phone's own stock System volume (sealed, untouched) with a separated data volume, as `/private/var`. It is booted from pwned DFU (usbliter8) with a patched kernel/iBoot and comes with a rootless jailbreak.

Stock iOS still here. A normal reboot on jailbreak data volume send you to stock iOS, to boot into the jailbreak state again, put the phone into pwned DFU and run `boot.sh`.

This resolve 2 problems I faced while using CFW: storage (which dualboot eats a lot) and cfw (which reboot turns your device into recovery mode).

But this is not a great solution, since SEP is disable for 2nd data partition, you can't login into iCloud on this, but luckily, FairPlay still works, which means application works well.

<p align="center">
  <img src="demo.jpg" width="360" alt="iPhone 11 Pro on iOS 27.2 booted into 2nd Data: App Store signed in, Sileo and Filza on the home screen, windowed apps">
</p>

## Quick start
```
git clone https://github.com/34306/doublera1n.git
cd doublera1n
./boot.sh
```
That's it. Plug the iPhone in booted into iOS (its version is read automatically), then put it into pwned DFU when it asks. The first time `boot.sh` installs 2nd Data (it asks for pwned DFU once more for the first boot), every time after it just boots 2nd Data.

Updated stock iOS? Plug the iPhone in booted into the new iOS and run `./boot.sh`: 2nd Data is rebuilt for the new build and DataB (apps, data, jailbreak) is kept. *(developing feature, not device-tested yet)*

On first use it runs `./setup.sh` by itself, which installs the Homebrew tools (libimobiledevice, libirecovery, ldid, libusb, ipsw) and creates `.venv` from `requirements.txt`. `.venv/` is machine-local, run `./setup.sh` again any time to repair it (if needed).

Prefer a menu? `./dualboot.sh` (↑/↓ select, Enter run):

| Menu | What it does |
|---|---|
| Start | same as `./boot.sh`: installs 2nd Data the first time, boots it after |
| SSH into 2nd Data | `ssh root@` through iproxy (pinned to the phone's UDID) |
| Re-apply fixes | pushes every userland fix again over SSH (2nd Data running) |
| Remove 2nd Data | deletes DataB and our files in Preboot, back to a single stock OS |
| Dualboot: 2nd iOS | **developing feature**: another iOS 27.x on its own volumes, see below |
| More | pick the iOS version by hand, status / check the Mac, run `setup.sh` again |

> [!WARNING]
> When setting up 2nd Data, if the iPhone asks **iPhone Partially Set Up**, choose **Continue with Partial Setup**. Do NOT choose **Erase and Start Over**.

I also fixed a lot of problems such as Wifi did not auto connect when reboot, deep sleep panic the device, SEP-less panic stuff, etc. You can check patches i did in the repo as well.

## Troubleshooting

Run `./dualboot.py diag` (or the menu: Diagnostics) while 2nd Data is running: it collects on-device
evidence for the usual complaints — iCloud/Apple ID sign-in failures, slow Wi-Fi, touch dropouts and
app crashes — and prints how to read it.

- **iCloud sign-in**: the SEP-less data volume cannot store Apple ID session tokens in a stock
  keychain: obkeychain now serves the item-secret classes of the device AND the user keybag
  (WhenUnlocked-protected IDMS tokens land in the user keybag), and obacl stores ACL-constrained
  items as class-wrapped keys. What still cannot work without a SEP is the server-side part: GSA
  expects a real activation record and a device identity that only a SEP-backed activation produces
  (`NoIdentity` / `X-Apple-I-MD` errors in the diag output). Everything short of that is fixable
  client-side; check the diag's iCloud section.
- **Slow Wi-Fi while connected**: the Wi-Fi/BT calibration blobs ("wlcal") are sealed in FDR with
  the AP identity, and wirelessd unseals them the same way CommCenter does for the baseband. obfdr
  now loads into wirelessd too (needs a rebuild on the Mac: `./src/build.sh`, then
  `./dualboot.py provision --apply`). Without it the radio runs on the firmware's default
  calibration: associated but very slow.
- **App crashes**: check the crash section of the diag; an app that dies from tweak injection can be
  added to `/var/jb/etc/obhook.deny` (user-editable), then respring.

## Dualboot: a 2nd iOS (developing feature)
> [!NOTE]
> **Developing feature.** The code is in, but it has not been booted on a device yet. Expect bugs.

Besides 2nd Data (stock System + DataB), you can add a second iOS 27.x build on its own volumes:
- `OS2System`: the system files of that build, `OS2Data`: its `/private/var`
- its cryptex content (dyld shared cache) in `Preboot:/Cryptexes/ob/os2/<build>`

Stock iOS and DataB are not touched, and a normal reboot still goes to stock iOS. 2nd Data has to be installed and running (the files are copied over SSH). You need about 22 GB free on the phone and about 30 GB on the Mac (IPSW + decrypted images).

```
./dualboot.py os2 install --build <build>   # 2nd Data running
./dualboot.py os2 boot                      # pwned DFU
./dualboot.py os2 remove                    # 2nd Data running
```
"Remove 2nd Data" refuses while a 2nd iOS is installed (both use the same folder in Preboot): remove the 2nd iOS first.

## iOS 16 status: what works, what is missing

Measured on iOS 16.6.1 / 20G81, iPhone 11 Pro (d421ap), kernel xnu-8796.142.1~1:

| Resolver family | iOS 16 |
|---|---|
| kernel identity / panic / sandbox-compatibility / codesigning / vm-fault-cs-bypass / ppl | ✅ resolves |
| kernel amfi (trust-cache, launch-constraints, debugger, developer-mode, post-validation, dyld-policy) | ✅ resolves - new pre-17 `PE_i_can_has_debugger` resolver (BTI c + paired-load shape, 206 direct callers) |
| kernel boot_policy: USB restore-mode stub | ✅ resolves |
| kernel boot_policy: persona uid/gid guards | n/a - `spawn_validate_persona` does not exist before xnu-10002 (skipped by version gate) |
| kernel exception_port | n/a - the EXC_GUARD enforcement does not exist before xnu-10002 (skipped by version gate) |
| kpatchfind ramdisk set: rd/jb renames | ✅ resolves (2 records) |
| kpatchfind ramdisk set: mount union/rootfs | n/a - iOS 16's `__mac_mount` is built with CONFIG_UNION_MOUNTS enabled, there is no MNT_UNION -> EPERM path to patch |
| kpatchfind osb set (SEP no-panic stubs, sep-booted false, sleep sep-not-alive) | ✅ resolves - same 7 records as 27.x |
| kernel aks (`AKSUserClient::start` SEP call, externalMethod selector-7 shim, log call) | ❌ needs new masked patterns for the iOS 16 AppleKeyStore kext |
| kernel sep_silence (`sepPanicCheck`, `_didTimeout`, set-power/notify-active gates, PRNG reseed) | ❌ iOS 16 restructured the retry logic; only the paging-off anchor resolves today |
| kernel credential-manager (ACM) / tfp0 | ❌ needs per-method masked signatures recorded for xnu-8796 |
| kernel valeria (inactive-owner check + registration shim cave) | ❌ needs its own iOS 16 research |
| TXM plans | n/a - iOS 16 has no TXM (omitted automatically) |
| iBSS / iBEC patching | ❌ LZFSE_ENCRYPTED (KBAG, A13 GID key unknown) - hardest blocker |

Run `./dualboot.py select --build 20G81 && ./dualboot.py precheck --build 20G81` to see the live list
(profiles/<board>-<build>/profile.json `pending_families`). Every item that turns ✅ brings an iOS 16 dualboot
one step closer; the iBSS/iBEC encryption needs on-device research (e.g. bootrom-side decryption via pwned DFU).

## Requirements
- A Mac (support both Apple silicon and Intel) with Homebrew. `setup.sh` installs libimobiledevice, libirecovery, ldid, libusb, [ipsw](https://github.com/blacktop/ipsw) and creates `.venv`.
- A usbliter8 board (RP2350) for pwned DFU. I'd recommend using the board i put the image below.
- A13: iPhone SE 2 (iPhone12,8, d79ap), iPad 9 Wi-Fi / Cellular (iPad12,1 j181ap / iPad12,2 j182ap), for iPad 9: added support but untested.
- Any iOS 27.x build of that iPhone. 2nd Data always runs the build installed on the phone (i might be extend to iOS 26 soon?).

## Untested iOS version

Every patch site is found again in the IPSW of the build you pick. If Apple changes a patched function too much, wait for me until I update and support it. Only iPhone 11 Pro on 27.2 (24B119) has booted so far, Issues, PR are welcome if it broken for you.

**Pre-check** (`lib/precheck.py`) runs before anything touches the phone: it pulls only BuildManifest, kernelcache, TXM, iBSS, iBEC and DeviceTree (~70 MB) with `ipsw extract --remote`, builds the profile, applies every patch and checks TSS. Release builds come from ipsw.me, betas and RCs from AppleDB. If your build is on neither, give the IPSW yourself (file or URL).

```
./dualboot.py versions                                   # local profiles + Apple's builds
./dualboot.py select --auto                              # read the build from the iPhone (USB)
./dualboot.py select --build 24B5110a                    # any build, betas included
./dualboot.py precheck --build <build> --ipsw <file|URL> # an IPSW you fetched yourself
```

TSS: if Apple doesn't sign the build anymore (old builds, betas), the newest signed build's ticket is used for the setup chain, the patched iBoot doesn't check AP images. Once installed, 2nd Data uses the ticket installed on the phone (read over UART), this UART require you to have this kind of board:

![B4 PICO DFU board (RP2350, Lightning plug + USB-C): pwned DFU and UART over the Lightning port](pico_uart.jpg)

## How to PWN iPad 9th?

Open the patch.c file in the usbliter source code of t8030_shellcode and add the patches, then recompile

   ```
    SET32(ROM_NEW_PA + 0x763C, NOP);
    SET32(ROM_NEW_PA + 0x7644, 0x52800008); // mov w8, #0
    SET32(ROM_NEW_PA + 0x766C, NOP);
    SET32(ROM_NEW_PA + 0x795C, NOP);
   ```



## Credits
- [usbliter8-fun by wh1te4ever](https://github.com/wh1te4ever/usbliter8-fun)
- [usbliter8]() (got deleted)
- [Liter8 by Xplo8E](https://github.com/Xplo8E/Liter8)
- [lycorine by hrtowii](https://github.com/hrtowii/lycorine-public)

## License
doublera1n is MIT, see [LICENSE](LICENSE). Some parts keep their own license.
