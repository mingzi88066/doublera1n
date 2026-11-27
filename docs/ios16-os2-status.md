# iOS 16.1 作为第二系统（OS2）的可行性结论与适配进度

设备：iPhone 11 Pro Max（`iPhone12,5` / `d431ap` / CPID `0x8030` / ECID `0x001d45a02683802e`）
主系统：iOS 27.0.1（`24A446`），2nd Data 已安装并成功启动过（`2026-11-24 21:57`）
目标：第二系统（`os2`）换成 iOS 16.1（`20B82`）

## 结论

**这条路在"改配置"的层面走不通，不是 IPSW 换一个就完事。** 卡点是硬的：

| # | 阻塞点 | 性质 | 能否在本仓库内修好 |
|---|---|---|---|
| 1 | iOS 16.1 的 iBSS/iBEC 是 **GID 加密**（IM4P 带 KBAG），27.x 是明文 | **绝对阻塞** | 不能。需要先拿到解密后的 iBoot |
| 2 | 16.1 没有 `Ap,TrustedExecutionMonitor`（TXM 是 iOS 17 起才有） | 结构性 | 已修（见下） |
| 3 | 内核补丁集按 27.x 的 XNU 写的，16.1 上 13 个族里 7 个解析不出来 | 工程量 | 部分可修，需逐个逆向 |
| 4 | 用户态 payload（jbinit / obhook / 4 个 daemon 补丁）按 27.x 写的 | 工程量 | 未开始 |
| 5 | `os2` 本身在 README 里标注"developing feature，未在设备上启动过" | 未验证 | 27.x 都还没验证 |

### 1. 为什么 iBSS/iBEC 加密是死结

`doublera1n` 的整条链路依赖 `lib/patch_iboot.py` 去改 iBoot：关掉 image4 校验、写 boot-args 槽（`rd=md0` / `jb=md0`）、
关 SEP。没有打过补丁的 iBoot，就既不能加载改过的 kernelcache/DeviceTree，也进不了 SEP-less 的启动路径。

实测两个版本的同一位置：

```
iOS 27.0.1   iBSS.stock.raw  @0x300:  "mBoot-20457.2.37"       ← 明文 ARM64，可 patch
iOS 16.1     iBSS.d431.RELEASE.im4p  @0x300: 09 3a 67 cd ...  ← 密文，KBAG 存在，payload 高熵
iOS 16.1     kernelcache.release.iphone12                     ← 明文（krnl payload 无 KBAG），patchfinder 能读
```

也就是说：**内核能分析、iBoot 不能改**。GID 密钥只在芯片内部，离线无解；要拿到 16.1 的明文 iBoot，只有
在 pwned DFU 下让 bootrom 自己解密再把 SRAM dump 出来（checkm8 时代 `decrypt iBoot` 的做法）。
`tools/usbliter8ctl` 目前只有 `boot`（上传 raw iBoot）一条路径，没有内存 dump 能力，所以这一步在本机做不了。

### 2. 内核补丁族实测（同一套解析器，两个 build）

`lib/patchfinder` 的语义解析器在 16.1 上跑出来的结果：

| 补丁族 | iOS 16.1 (20B82) | iOS 27.0.1 (24A446) |
|---|---|---|
| identity | OK 2 | OK 2 |
| panic | OK 5（4 个锚点，seal-broken 有 3 处文案） | OK 4 |
| amfi | **FAIL** `no candidate: AMFI trust-cache function` | OK 14 |
| ppl_trust_cache_required | OK 0 | OK 0 |
| boot_policy | **FAIL** `no candidate: spawn_validate_persona sibling guards` | OK 4 |
| aks | **FAIL** `no candidate: AKSUserClient::start SEP call` | OK 22 |
| sep_silence | **FAIL** `no candidate: sepPanicCheck` | OK 10 |
| sandbox_compatibility | OK 11 | OK 11 |
| codesigning_required | OK 0 | OK 0 |
| exception_port | **FAIL** `missing anchor: com.apple.security.only-one-exception-port` | OK 1 |
| vm_fault_cs_bypass | OK 1 | OK 1 |
| credential_manager | **FAIL** `does not support firmware profile unidentified` | OK 52 |
| valeria_composite | **FAIL** `no candidate: IOUSBDeviceInterface class-command callback owner check` | OK 15 |

注意 `credential_manager`（52 条）和 `valeria_composite`（15 条）在 16.1 的 XNU 里对应的子系统/结构体
形态不同甚至不存在 —— 这不是"偏移量对不上"，是**安全模型换代**，得按 16.1 的 XNU 重写补丁语义。

## 本次已完成的适配（可复用的部分）

1. `lib/mkprofile.py`：TXM 组件改成"这版固件有才解析"。iOS 16 及更早没有 `Ap,TrustedExecutionMonitor`，
   原来是硬 `KeyError` 直接崩；现在写入空 TXM 记录并在 `profile.json` 里标记 `"txm": false`。
2. `lib/precheck.py`：
   - 组件按 BuildManifest 求交集，缺 TXM 只提示不崩；
   - `records` 阶段跳过本版不存在的 TXM 链；
   - **iBoot 阶段新增密文检测**：payload 里找不到 `iBoot-`/`mBoot-` 头就明确报
     `payload is encrypted (no iBoot/mBoot header) - must be decrypted before patch_iboot.py can patch it`，
     而不是原来含糊的 `not a raw iBoot`。
3. `lib/mk_kernel.py` / `lib/mk_osb.py` / `lib/boot_chain.py`：TXM 相关产物与上传项改为"有才做"，
   没有就不生成、不上传（不再因为缺文件在启动链里断掉）。
4. `lib/patchfinder/kernel_restore.py`（`panic` 族）：原来要求诊断字符串在整颗核心里**只出现一次**，
   iOS 16.1 的 `root volume seal is broken` 有 3 处文案，直接报 ambiguous。改成按 **xref + 冷块结构**
   判定：每处文案各自取唯一 ADRP+ADD 引用，再把"最近的唯一条件分支目标"作为一个 panic 块全部 NOP 掉
   （少打一处就照样 panic）；单块时 id 不带下标，**27.0.1 的输出字节级不变**（已回归验证）。

验证方式：

```
# 27.0.1 三个计划，改前/改后 JSON 逐字节相同
resolve.py kernel {restore,boot-jit,vm-fault-cs-bypass} kernelcache(24A446) --json   # 20 / 135 / 1 条
# 16.1
precheck.py --build 20B82 --board d431ap --ipsw iPhone12,3,iPhone12,5_16.1_20B82_Restore.ipsw
  → components: TXM 不存在（已跳过）
  → profile:    FAIL  patchfinder kernel boot-jit: no candidate: AMFI trust-cache function
```

## 若一定要 16.1，剩下的两条路

**路线 A：先解决 iBoot 解密（唯一能解锁其它工作的前置）**
在 pwned DFU 下让 bootrom 解密 16.1 iBSS/iBEC 并 dump SRAM —— 需要给 `usbliter8` 的 t8030 shellcode 加
"加载 iBSS 后不跳转、把 SRAM 通过 USB 吐出来"的能力（RP2350 板子要重新烧），或者在板子侧加 UART dump。
拿到明文 iBSS/iBEC 之后，本仓库的 `patch_iboot.py` 才有输入可言。

**路线 B：不碰 iBoot，改用 27.x 的 iBoot 引导 16.1 内核（研究性，成功率低）**
27.x 的 iBSS/iBEC 是明文可直接 patch，理论上可以让它 `bootx` 一个 16.1 的 kernelcache + DeviceTree。
但 iBoot 与内核之间的 `boot_args` 版本、内存布局、TXM/SPTM 握手都是跨代的，需要在 16.1 内核里改
"boot args revision" 校验，且 16.1 没有 TXM 而 27.x iBoot 期待它 —— 属于要先证明可行再谈工程量的实验。

两条路都绕不开第 3 项（内核补丁族 7 个 FAIL）；即使 iBoot 解决，也还要把 `boot-jit` 这套按 16.1 的
XNU 重写一遍，并把 jbinit/obhook 与 4 个 daemon 补丁移植过去。

## 现状

- 主系统 + 2nd Data（27.0.1 越狱态）**未被改动**，仍然可用；
- 未对设备写入任何 16.1 相关数据（无法构造合法启动链，写进去只会留下垃圾分区）；
- 本文件与上面的代码改动已提交到分支 `ios16-os2-port`。
