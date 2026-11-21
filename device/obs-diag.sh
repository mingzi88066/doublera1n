#!/var/jb/usr/bin/zsh
# obs-diag - on-device diagnostics for the OS B (2nd Data) pain points:
# activation, keychain, iCloud/Apple ID sign-in (akd/accountsd), Wi-Fi calibration,
# touch (DCP) and app crashes. Run as root (dualboot.py diag pipes it over SSH as `zsh -s`).
# Read-only: nothing on the phone is modified.
export PATH=/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin
emulate -L zsh; setopt no_err_exit
LOGWIN=15m   # window scanned in the unified log

sec() { print -r -- "\n================ $* ================"; }
line() { print -r -- "  $*"; }

# the OS B userland ships no pgrep/awk/ping: use ps ax + grep instead.
is_running() { ps ax 2>/dev/null | grep -Eq "/$1( |$)"; }

sec "device"
line "date       : $(date)"
line "build      : $(sw_vers -productVersion 2>/dev/null) ($(sw_vers -buildVersion 2>/dev/null))"
line "uptime     : $(uptime)"
line "data volume: $(mount 2>/dev/null | grep 'on /private/var ' | head -1)"

sec "activation (hactivation)"
is_running mobileactivationd && line "mobileactivationd: running" || line "mobileactivationd: NOT RUNNING"
log show --last $LOGWIN --style syslog 2>/dev/null | grep -iE "hacthook|should_hactivate|ActivationState|dealwith_activation" | tail -6 | while IFS= read -r l; do line "$l"; done
line "(obkeychain/mkdaemon check: mobileactivationd must answer Activated; see hacthook lines above)"

sec "keychain (securityd + obkeychain/obacl)"
for f in /private/var/Keychains/.obkeychain /private/var/Keychains/obkeychain.master /private/var/Keychains/obkeychain.dylib; do
  [[ -e $f ]] && line "$f : $(ls -ld $f)" || line "$f : MISSING"
done
is_running securityd && line "securityd: running" || line "securityd: NOT RUNNING"
log show --last $LOGWIN --style syslog 2>/dev/null | grep -E "obkeychain|obacl" | tail -8 | while IFS= read -r l; do line "$l"; done
kcq 2>/dev/null | head -12 | while IFS= read -r l; do line "kcq: $l"; done

sec "iCloud / Apple ID (akd + accountsd)"
for d in akd accountsd identityservicesd appleaccountsd idmsdaemon; do
  is_running $d && line "$d: running" || line "$d: NOT RUNNING"
done
line "-- keychain/sign-in errors in the last $LOGWIN (the interesting part for a failed sign-in) --"
log show --last $LOGWIN --style syslog 2>/dev/null \
  | grep -iE "akd|accountsd|idms|AppleAccount|GSA|gsa\.apple\.com|IdentityServices" \
  | grep -iE "error|fail|-25291|-25330|-26275|NoIdentity|denied|AuthNeeded|AKAuthentication|AWDSession|activation" \
  | tail -40 | while IFS= read -r l; do line "$l"; done
line "-- how to read this --"
line "-25291 = keychain class key unavailable (obkeychain must cover the bag/class; needs a provision --apply)"
line "-25330 = ACL/ACM rejection (obacl guard must be active)"
line "NoIdentity / X-Apple-I-MD / AKAuthenticationError 4xxx = GSA rejects the device identity:"
line "  structural on OS B - hactivation only fools the local state, Apple's servers still expect a"
line "  real activation record + device identity that only a SEP-backed activation can produce."

sec "network (Wi-Fi)"
line "-- en0 --"
ifconfig en0 2>/dev/null | grep -E "status|ssid|media|inet " | while IFS= read -r l; do line "$l"; done
line "-- default route + gateway reachability --"
r=$(route -n get default 2>/dev/null | sed -n 's/.*gateway: //p' | head -1); line "gateway: ${r:-none}"
if (( $+commands[ping] )); then
  [[ -n $r ]] && ping -c 3 -t 5 $r 2>&1 | tail -2 | while IFS= read -r l; do line "ping gw: $l"; done
  ping -c 3 -t 5 1.1.1.1 2>&1 | tail -2 | while IFS= read -r l; do line "ping 1.1.1.1: $l"; done
else
  line "(no ping in this userland - use the DNS latency below as the reachability check)"
fi
line "-- DNS latency (dnsq) --"
dnsq gsa.apple.com apple.com setup.icloud.com 2>/dev/null | while IFS= read -r l; do line "$l"; done
line "-- Wi-Fi calibration evidence (obfdr / wlcal / sik) --"
log show --last $LOGWIN --style syslog 2>/dev/null \
  | grep -iE "obfdr|wlcal|calibration|not found in FDR|sik" \
  | grep -viE "CommCenter.*bbcl" | tail -15 | while IFS= read -r l; do line "$l"; done
line "-- which daemon owns Wi-Fi calibration here (this rootfs has no wirelessd)? --"
line "launchd jobs matching wireless/wifi:"
find /System/Library/LaunchDaemons /var/jb/Library/LaunchDaemons -maxdepth 1 -name '*ireless*' -o -maxdepth 1 -name '*ifid*' 2>/dev/null | while IFS= read -r l; do line "  $l"; done
line "binaries containing the wlcal string (the calibration consumer):"
for d in /usr/libexec /usr/sbin /usr/libexec/lowpowerd; do
  grep -a -l -s wlcal $d/* 2>/dev/null
done | head -8 | while IFS= read -r l; do line "  wlcal in: $l"; done
for f in /usr/libexec/wirelessd /usr/sbin/wirelessd /usr/libexec/wifid /var/jb/usr/libexec/wirelessd; do
  [[ -e $f ]] && line "  exists: $f"
done
line "if nothing above matches and Wi-Fi is slow-but-associated, the radio runs on default"
line "calibration (FDR unseal fix has no target on this build); send this section upstream."

sec "touch (DCP / HID)"
line "-- touch/DCP faults in the last $LOGWIN --"
log show --last $LOGWIN --style syslog 2>/dev/null \
  | grep -iE "DCP|Multitouch|AppleMultitouch|TouchEvents|hid.*reset|SPU.*(fault|panic|error)" \
  | grep -iE "error|fail|reset|fault|panic|timeout|dead|disconnect" \
  | tail -25 | while IFS= read -r l; do line "$l"; done
line "repeated DCP resets/timeouts here line up with the reported touch dropouts; capture a few of"
line "these lines when a dropout happens for the report."

sec "crashes (apps)"
CR=/private/var/mobile/Library/Logs/CrashReporter
if [[ -d $CR ]]; then
  line "-- crashes in the last 48 h, by process --"
  find $CR -name "*.ips" -mtime -2 -exec basename {} \; 2>/dev/null | sed -E 's/-[0-9]{4}-[0-9]{2}-[0-9]{2}-.*\.ips//' \
    | sort | uniq -c | sort -rn | head -12 | while IFS= read -r l; do line "$l"; done
  newest=$(ls -t $CR/*.ips 2>/dev/null | head -1)
  if [[ -n $newest ]]; then
    line "-- most recent crash: $newest (header) --"
    head -c 2048 $newest | tr ',' '\n' | grep -E '"(app_name|process|exception|terminationReason|bug_type|faultingThread|exceptionReason)"' \
      | head -12 | while IFS= read -r l; do line "$l"; done
  fi
else
  line "no CrashReporter directory"
fi
line "-- injection kill switch / deny list (a crashing app can be added to /var/jb/etc/obhook.deny) --"
[[ -e /var/jb/.obhook_noauto ]] && line ".obhook_noauto present (auto-injection OFF)" || line ".obhook_noauto absent (auto-injection ON)"
obhookctl status 2>/dev/null | head -4 | while IFS= read -r l; do line "obhookctl: $l"; done
[[ -f /var/jb/etc/obhook.deny ]] && head -20 /var/jb/etc/obhook.deny | while IFS= read -r l; do line "deny: $l"; done

sec "done"
print -r -- "copy this whole output into the issue/report - every section above maps to one fix layer."
