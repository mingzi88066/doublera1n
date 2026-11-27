# 路线 B 实测：用 27.x 的 iBoot 启动 iOS 16.1 内核

设备：iPhone 11 Pro Max（`d431ap` / CPID `0x8030` / ECID `0x001d45a02683802e`）
主系统：iOS 27.0.1（`24A446`，iBoot 明文，可 patch）
实验对象：iOS 16.1（`20B82`）的 kernelcache + DeviceTree + 协处理器固件
日期：2026-11-26，4 次 pwned DFU 启动

## 结论

**27.x 的 iBoot 确实能 bootx 一颗 16.1 的内核**，而且跑得比预期远得多：全部 kext 加载完成、SEP 管理器
启动、显示管线初始化、一路走到 NVMe（ANS2）提交队列创建才死。死因是一个硬件总线错误，不是版本拒绝：

```
panic(cpu 0): "AMCC PLANE3 Unimplemented Address error:
  INTSTS 0x10000000 AFERRLOG0/1/2/3 0/0x70800/0x14c040/0x1004
  ADDR 0 CMD/SIZE/TYPE 0x8()/0x7/0 AID/TID 0x40/0xc0" @AppleT8030PlatformErrorHandler.cpp:1094
```

即某条总线主设备向 **物理地址 0** 发了一次 128 字节的 DMA。这一条在三种不同配置下**字节级相同**，
只有触发的时机在变——说明它是一个始终存在的错误源，而不是某个配置的偶发问题。

到这一步为止，"能不能启动"这个问题已经有答案了：**能，但过不了存储初始化**。而即使过了，
第二系统里现在装的是 27.x 的用户态，16.1 内核也不可能把它跑起来（见"下一步"）。

## 四轮实测

| # | iBSS/iBEC | DeviceTree | 协处理器固件 | SEP | 结果 | 日志 |
|---|---|---|---|---|---|---|
| 1 | 27.x | 27.x | 27.x | 27.x | 跳过 SPTM/TXM 后全部 kext 加载完 → `Unexpected SIO Protocol version 10 - 9`（`AppleSmartIO.cpp:84`） | [run1](logs/route-b-run1-27dt-27fw.log) |
| 2 | 27.x | 27.x | **16.1** | 16.1 | 过 SIO 这关；显示管线中断 → AMCC PLANE3 | [run2](logs/route-b-run2-27dt-16fw.log) |
| 3 | 27.x | **16.1** | 16.1 | 16.1 | 显示这关也过了，NVMe 提交队列创建时 → AMCC PLANE3 | [run3](logs/route-b-run3-16dt-16fw.log) |
| 4 | 27.x | 16.1 | 16.1（**去掉 SIO**） | 16.1 | 与 run3 相同位置、相同错误 → 与 SIO 无关 | [run4](logs/route-b-run4-16dt-16fw-nosio.log) |

每一轮只动一个变量。run1 的 iBSS 里 `mBoot-20457.2.37` 明确是 27.x，四轮内核都是
`Darwin Kernel Version 22.1.0 ... xnu-8792.42.7~1/RELEASE_ARM64_T8030`（16.1），这点从 panic 头能核对。

### 这一轮学到的四件事

1. **image4 校验关掉之后，iBoot 不在乎内核是哪个版本的。** 16.1 的 `rkrn` 用 27.x 的 APTicket 签的
   IM4M 包起来，iBoot 照收、照 bootx，没有 manifest 比对失败。
2. **16.1 内核不需要 SPTM/TXM。** 27.x 的启动流程会把 `SPTM.img4` / `TXM.img4` 一起下发，但 16.1 的
   XNU 里没有这两个概念，不下发反而是对的：run1 就是不下发跑起来的。27.x iBoot 也没因为缺 SPTM
   而拒绝 bootx。
3. **协处理器固件的协议版本跟着内核走，不跟着 iBoot 走。** 27.x 的 SIO 固件上报 v10，16.1 的
   `AppleSmartIO` 只认 v9，直接 panic。把 ANE/AOP/AVE/GFX/ISP/PMP/SIO/WCH 整套换成 16.1 之后，
   同类检查全部通过——这是这条路线最关键的一条经验：**固件集必须和内核配对，不是和 iBoot 配对**。
4. **DeviceTree 也该跟内核配对。** 换成 16.1 自己的 DT（只补 OSB 需要的 fstab `vol.fs_role` 等三条编辑）
   之后，显示管线的 panic 消失，内核多跑了整整一个阶段，到存储初始化才停。同一份 16.1 DT 里同样缺
   `sioaesdisable-*` 这几个属性，所以那四条 warning 与版本无关，可以忽略。

### run4 排除了 SIO

run3 崩溃点前几行正好是 `RTBuddy(SIO): Resuming...`，看着很像 SIO 固件在 DMA。run4 直接不发
`SIO.img4`，panic 位置和 AID/TID 一个字都没变，所以 SIO 不是那个往地址 0 写数据的主设备。

## 复现方法

工具已经并进仓库：`lib/mk_mixfw.py`，产出与本次实测**逐字节一致**（用同一套输入重跑过 md5 比对）。

```sh
cd /path/to/doublera1n
export OSB_WORK=~/dualboot-work/mix-dt16          # 输出根目录，产物落在 $OSB_WORK/osb
export OSB_PRIMARY=~/dualboot-work/<ecid>-24A446/build/osb   # 主系统那套（iBSS/iBEC/DT/rtsc 从这取）
export OSB_IPSW=~/dualboot-work/<ecid>-24A446/build/ipsw
export OSB_OUT=~/dualboot-work/<ecid>-24A446/out
export OSB_BOARD=d431ap
export MIX_IPSW="/path/iPhone12,3,iPhone12,5_16.1_20B82_Restore.ipsw"
export MIX_KERNEL=~/dualboot-work/mix-16.1/osb/Kernelcache.img4   # 16.1 内核（mk_kernel + 16.1 记录）

.venv/bin/python lib/mk_mixfw.py --dt secondary --sep secondary
.venv/bin/python lib/boot_chain.py --osb        # 设备需在 pwned DFU
```

`--dt secondary` 用 16.1 的 DT 并自动打上 `dt_patch.OSB_OPS`；`--skip SIO,WCH` 可以复现 run4。

串口是唯一能看到内核死在哪的通道（`serial=3` 已经从 iBEC 的 boot-args 里带上）。注意两件事：

- 崩溃后内核停在 debugger 里（`wdt=-1` 关掉了看门狗），**不会自己重启**；这一次 usbliter8 板子在
  前两轮之后自动重新 pwn 过，第三轮就没再回来，需要手动 音量+ / 音量- / 长按电源，再进 pwned DFU。
- 抓串口要用常驻的后台进程（`lib/common.py` 的 `Serial`），随手 `&` 起的读进程会被会话清理掉，
  run1 就这么丢过一次日志。

## run 5：AMCC 错误不是必然的，真正的墙是 SIO

run2 和 run4 的 panic 里 `AFERRLOG0/1/2/3 0/0x70800/0x14c040/0x1004 ADDR 0 AID/TID 0x40/0xc0` 完全一致，
说明那是**同一个** DMA 错误，只是错误处理器在 display / NVMe 两个不同的时间点抓到它。于是把
`AppleT8030PlatformErrorHandler` 的致命判定改成只打印（`kernel.amcc.ignored-address-error`），
重跑同一套链（其它 13 个产物 md5 与 run3/run4 完全一致，只换了内核）：

- AMCC 错误**一次都没出现**，连 `Ignored` 都没打印 —— 那一轮的 DMA 到 0 与本次配置无关；
- NVMe/CoastGuard 顺利初始化（`Successfully initialized NVMe CoastGuard`、8 个 namespace）；
- 内核继续跑到 `AppleM2ScalerCSCHalMSR::detectHardwareVersion`，然后死在
  `RTBuddy(SIO)::_setManagedStateGated "No response received in 20s, SIO not started? (4)"@RTBuddy.cpp:1005`。

也就是说：**16.1 的 SIO 协处理器固件在 27.x iBoot 下根本没跑起来**。对比 run1（用 27 的 SIO 固件）报的是
`Unexpected SIO Protocol version 10 - 9` —— 27 的固件是活的，只是协议版本比 16.1 内核期望的新一代。
两版 DT 的 `sio` 节点（`reg`、`map-range`、`iop-version`、`ascwrap-v2`）逐字段相同，所以不是 DT 描述的差异。

据此加了第二个诊断补丁 `kernel.iop.state-timeout-log-only`：`_setManagedStateGated` 在
`cmp w2, #0xe00002d6`（kIOReturnTimeout）命中时 `bl` 进冷块 panic，而紧接着的指令本身就把错误码写回并
返回 —— 把这个 `bl` NOP 掉，超时变成本来就存在的错误返回路径。

两条补丁都是**诊断用**，不写进默认 profile：`lib/patchfinder/kernel_amcc.py` 里按语义定位（锚字符串 →
唯一 xref → 守卫分支），在 16.1 和 27.0.1 上都能各自命中，定位不到就报错而不是乱打。

## run 6：把 SIO 换成 27.x 固件 + 接受 v10 —— 内核第一次走到 APFS mountroot

run5 里 16.1 的 SIO 固件从不应答（NOP 超时只是把它静音，display/传感器一系起不来）。反过来用**能应答的
27.x SIO 固件**，`Unexpected SIO Protocol version 10 - 9` 就成了唯一的门。新增第三个补丁
`kernel.iop.sio-protocol-accept`：找到 `Unexpected SIO Protocol version %u - %u` 冷块，冷块里
`mov w9, #9` 给出内核期望的 v9，正常路径上 `cmp w8, #9 / b.ne <冷块>` 是唯一的版本守卫，NOP 掉即可。

这一轮（日志 `docs/logs/route-b-run6-3patch-27sio-apfs.log`）是全系列里走得最远的：

- `RTBuddy(SIO/GFX/ANS2/AOP/PMP): Resuming...` 全部完成，SIO 协议检查通过；
- APFS 模块加载成功（`apfs-2142.40.13.0.1`），NVMe/CoastGuard 初始化通过；
- `apfs_vfsop_mountroot:2653: apfs: mountroot called!`，`BSD root: disk0s1`；
- 唯一剩下的 panic：
  `Failed to find the root snapshot: No error. (0). Rooting from the live fs of a sealed volume is not allowed on a RELEASE build @apfs_vfsops.c:2401`

根因很明确：`import_iboot_forwarded_roothash: could not retrieve system-volume-auth-blob from device tree`
—— 16.1 DT 里没有 `system-volume-auth-blob`，iBoot 也没有转发 root hash。这是**用户态墙**：第二系统卷里
装的是按 27.x 生成的系统，16.1 内核找不到它认识的 APFS 快照哈希。

## run 7/8：APFS 冷块有两个入口，堵一个不够

第四个补丁 `kernel.apfs.root-from-live-fs.*` 尝试让 mountroot 在找不到快照时直接返回而不 panic。第一次
只 NOP 了 `0x307bba4` 的 `b.ne`，panic 依旧 —— 因为同一个冷块还有第二个入口 `0x307bfa0` 的
`tbnz w8,#5`，它跳过第一个入口的落点、直接进冷块内部格式化错误码。补丁族已改为：在短语 ADRP 前 0x40
内收集所有近距离落点、取最小者为冷块起点，再把冷块起点之前、所有跳入该冷块的条件分支全部 NOP
（16.1 上命中 2 个：`0x307bba4`、`0x307bfa0`）。两个入口都已确认写成 NOP。

run8 的日志因两个串口捕获进程同时写同一文件而交错损坏，无法确认这一轮内核的最终落点；设备随后 panic
卡在 debugger（`wdt=-1`），板子也没自动 pwn 回来，需要物理重启才能继续。但即使 APFS panic 被绕过，
结论不变：**第二系统卷里没有 16.1 的用户态**（dyld shared cache、launchd、所有系统二进制都是 27.x 的），
16.1 内核越过 mountroot 也只会在exec/launchd 处失败。这就是路线 B 的天花板 —— 不是配置问题，是必须
把 16.1 的 System 卷真正写进 OS2System 才能解决，而那条路（路线 A）卡在加密的 16.1 iBoot。

## 诊断补丁族小结（都在 `lib/patchfinder/kernel_amcc.py`，语义定位，不进默认 profile）

| id | 关掉的 panic | 16.1 落点 |
|---|---|---|
| `kernel.amcc.ignored-address-error` | AMCC 地址错误 → 只打印 | 0x23c5ab8 |
| `kernel.iop.state-timeout-log-only` | 协处理器 20s 超时 → 错误返回 | 0x2fa7858 |
| `kernel.iop.sio-protocol-accept` | SIO 协议版本 v10≠v9 | 0x237cd68 |
| `kernel.apfs.root-from-live-fs.{0,1}` | APFS 找不到 root snapshot | 0x307bba4 / 0x307bfa0 |

## 下一步

现在的位置很明确：**内核能启动，但第二系统里没有能给 16.1 内核用的用户态**。`OS2System`/`OS2Data`
里装的是按 27.x 生成的系统，16.1 内核即使越过 NVMe 这一关，也只会在 mount/launchd 处失败。所以：

1. **先让 SIO 活过来。** 两条互斥的路：把 SIO 固件换回 27.x 并改掉 16.1 内核里的协议版本校验（SIO 是活的，
   但 v10 固件是否肯服务 v9 客户端未知），或者保持 16.1 固件、只把超时降级成错误（SIO 是死的，display /
   传感器一系全部起不来）。前者更接近可用，后者只用来确认剩下的链路（存储 / root mount / launchd）。
   两个补丁族已经把这两条都铺好了，缺的只是设备时间。
2. **再解决用户态。** 需要把一个 16.1 的 System 卷放上设备（16.1 IPSW 里有完整的 OS 镜像），并且
   处理 SSV 封印与 APFS 加密——这一步和 16.1 的 SEP/AKS 协议绑死，也是 `family_inventory.py` 里
   `aks`/`boot_policy`/`amfi` 三个族在 16.1 上还没解析出来的原因。
3. 上面两条都通了，才谈得上 `os2` 的 jbinit/obhook 移植（现在那套 payload 是 27.x 的二进制）。

换句话说：路线 B 的前半段（启动协议）已经验证可行，后半段（用户态 + 存储）是新的工程量，不是配置问题。
