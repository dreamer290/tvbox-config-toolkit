# TVBox Config Toolkit

在 GitHub 上收集 TVBox 的点播/直播资源，**逐个实测**后生成可用的本地配置，并提供一个 Windows 桌面工具来开关分发服务、复检资源和更新配置。

不是"网上抄一份可用清单"——仓库里每一条站点、每一个频道、每一个 jar，都是在本机跑出来的结果，失败项连同失败原因一起记录在案。

```
172 个点播站点（39 个实测采集源 + 133 个 spider 站点）· 53 台精选直播 · 7 个解析
```

---

## 它解决什么问题

TVBox 配置的痛点从来不是"找不到配置"，而是：

- 抄来的配置里一半源已经死了，且不知道哪个死了
- 配置能加载，但 spider jar 报错，看不出是 jar 坏了还是网络问题
- 电视端和电脑端网络环境不同，电脑能开不代表电视能开

本项目的做法是：**先实测，再生成；生成之后还能随时复检**。

---

## 桌面工具（推荐入口）

`TVBox配置工具.exe` —— 免安装，双击即用，约 12 MB。

| 分区 | 能力 |
|---|---|
| ① 服务控制 | 开/关局域网 HTTP 分发服务（默认 9978），自动列出本机所有网卡的配置地址，一键复制 / 浏览器打开，附端口占用检测 |
| ② 当前配置 | 下拉切换要用的配置文件，实时显示站点/直播/解析数量 |
| ③ 复检 / 更新 | 一键复检全部资源，结果红绿分明；对失效项可一键剔除并写回（自动备份到 `output/backup/`），支持导出 HTML 报告 |

复检做什么：

- **配置体检**：按 TVBox 的 Java 数据模型检查字段类型、重复 key —— 这类错误会直接导致"配置失败"
- **spider jar**：本地副本 MD5 与声明比对、dex 内 `csp_*` 类清单提取
- **采集源**：逐站真实搜索「庆余年」，三级参数回退（`ac=detail` / `ac=videolist` / `pg=1`）
- **spider 站点**：校验配置里的 `csp_类名` 是否真的存在于 jar 的 dex 中
- **直播**：精选频道逐台实测，其余源抓清单后抽样
- **解析器**：用复检时拿到的真实播放地址逐个测试

边界写在明面上：spider 站点的**内容源**必须在电视端才能最终确认，桌面端做到"类存在 + jar 配套"为止，不假装测过。

---

## 快速开始

### 方式一：直接用桌面工具（Windows）

1. 下载 `TVBox配置工具.exe` 与整个仓库（仓库里已含 `output/` 成品配置）
2. 双击运行，首次会弹防火墙提示 —— 勾选「专用网络」并允许
3. 复制界面上显示的地址，填进 TVBox 的配置地址
4. 电视端如提示加载失败，先在 TVBox 设置里「清空缓存」再重试

> 电脑需保持开机、程序保持运行。想彻底摆脱电脑见下方「方式二」。

### 方式二：拷进设备，不依赖电脑

把 `output/` 里的三个文件放进电视 `/sdcard/TVBox/`：

```
tvbox-config.json    # 相对路径版配置
spider.jar           # 相对路径版配套 jar
live.txt             # 精选 53 台
```

配置地址填：

```
clan://localhost/TVBox/tvbox-config.json
```

`clan://` 是 TVBox 内置的本地 HTTP 服务，能让配置里的 `./spider.jar`、`./live.txt` 正确解析，原理与局域网方案一致，只是服务跑在设备自己身上。

### 方式三：从源码跑

```bash
python app/TVBoxTool.py          # 桌面工具
python app/TVBoxTool.py --cli    # 命令行复检，不弹窗（加 --quick 抽样）
```

打包 exe：双击 `app/build_exe.bat`（需 Python 3.12，自带 tkinter）。

---

## 配置文件怎么选

| 文件 | 站点 | 适用场景 | 说明 |
|---|---|---|---|
| `tvbox-osc-fixed.json` | 172 | TVBoxOSC 等分支（推荐） | 配 `spider-shiguang.jar`，普通 jar 不会被误判为受保护；地址绑定 `192.168.31.25`，换机器需改成本机 IP |
| `tvbox-osc-relative.json` | 172 | 同上，但走相对路径 | jar 与直播清单用 `./`，配合 `clan://` 或任意 HTTP 根目录即可，**换机器不用改配置** |
| `tvbox-config.json` | 146 | 支持 `clan://` 的设备本地加载 | `./spider.jar` 相对路径，站点数少于上一份 |
| `tvbox-lan.json` | — | 不解析 `./` 的老分支 | 局域网地址写死，换网络后失效 |
| `tvbox-online.json` | — | 单文件、零本地依赖 | jar 走公网，需电视能直连该域名 |

> 仓库里的配置默认带着打包时的局域网 IP（`192.168.31.25`）。换到自己机器上，用桌面工具开服务后把地址换成界面显示的那个即可；或者直接用 `tvbox-osc-relative.json` + 设备本地 `clan://` 方案，完全不涉及 IP。

`tvbox-A/B/C-*.json` 是排障用的诊断变体（无 jar / 无 md5 / 本地 jar），平时用不到。

---

## 实测数据

采集阶段覆盖 24 个 GitHub 配置仓库，21 个可下载并解析，累计 1888 个站点。

| 类别 | 检查量 | 可用 | 判定口径 |
|---|---:|---:|---|
| 点播采集源 | 73 | **39** | 真实搜索返回片源，含三级参数回退 |
| 直播源 | 49 | 18 | 能解析出频道 + 抽样流可出数据 |
| 精选频道 | 86 目标 | **53** | 逐台验到 TS 分片可下载 |
| spider jar | 21 | 18 | 下载成功 + ZIP 完整 + MD5 与声明一致（12 个） |
| 解析（VIP） | 78 | 8 | 用真实播放地址换到 m3u8 |

失败归因（采集源）：DNS 解析失败/污染 > 连接超时 > 返回 HTML 拦截页 > HTTP 4xx/5xx。

---

## 三个值得记下来的发现

**1. 一部分"失效"其实是 DNS 污染**

`api.tiankongapi.com` 被解析到 Facebook 的 `185.60.218.50`。这类源换网络环境可能反而可用，配置里已内置阿里 / DNSPod 的 DoH。

**2. 直播初筛差点全盘误判**

`CCTV1` 用 `startswith` 匹配会把 `CCTV10` 算进去；排名靠前的候选又集中在移动内网与 IPv6 源。改成前缀边界校验 + 候选并行竞速后，可用频道从 17 台跳到 53 台。

**3. 「md5 缓存失效 / jar load err」可能跟 md5 毫无关系**

读 TVBoxOSC（`q215613905/TVBoxOS`，包名 `com.github.tvbox.osc.jun`）源码后定位：

- `ApiConfig.loadJar` 里 **cache 有效但加载失败时，回调的也是 `error("md5缓存失效")`**，文案具误导性
- `JarLoader.invokeInit` 有一条 `ProtectedInitJar` 分支：jar 若被判定为"受保护"，必须提供 `DexNative.getLoader()`，否则直接 false
- 判定条件 = 有 `Init` 类 + 调用 `Process.killProcess` + `Init.init()` 内没有当前包名白名单
- 某些流行 jar 三条全中，却又**不含 `DexNative`、不含 `.so`** → 必然加载失败
- 更微妙的是：该 jar 的包名白名单（AES key `1234123412341234`）**确实包含 `com.github.tvbox.osc.jun`**，但它写在 `Init.a()` 里，而客户端只扫 `Init.init()` —— 双向适配却恰好错位，配置层面无解

结论：这是客户端与 jar 的兼容性冲突，唯一的解法是换一个不被误判的普通 jar。`output/tvbox-osc-fixed.json` 就是这么来的。

分析工具一并留在仓库：`dex_methods.py`（手写 dex 解析）、`protected_audit.py`（复刻受保护判定）、`jar_coverage.py`（类覆盖率）。

---

## 目录结构

```
.
├── TVBox配置工具.exe        # Windows 桌面工具
├── app/                     # 桌面工具源码
│   ├── TVBoxTool.py         # 主程序（tkinter，纯标准库）
│   ├── build_exe.bat        # 一键打包
│   └── 使用说明-桌面工具.md
├── output/                  # 成品配置与文档
│   ├── tvbox-*.json         # 各版本配置
│   ├── spider*.jar          # 配套 jar
│   ├── live.txt             # 精选 53 台
│   ├── 使用说明.md
│   ├── 验证报告.html
│   └── 排查-jar报错.md
├── 采集 / 构建流水线
│   ├── tvcommon.py          # HTTP 与宽松 JSON 解析
│   ├── build_config.py      # 汇总生成配置
│   ├── make_osc_fixed.py    # 生成 TVBoxOSC 可用配置
│   ├── curate_live.py       # 频道级筛选
│   ├── extra_live.py        # 补齐缺失频道
│   ├── segcheck.py          # 分片级深度验证
│   ├── verify_final.py      # 全量回归验证
│   └── make_variants.py     # 派生诊断变体
├── 校验 / 分析工具
│   ├── lint_config.py       # 按 Java 模型做静态体检
│   ├── dex_methods.py       # dex 解析
│   ├── jar_audit.py         # jar 加载兼容性
│   ├── protected_audit.py   # 受保护 jar 判定
│   ├── jar_coverage.py      # 站点类覆盖率
│   ├── verify_parse.py      # 解析器实测
│   └── diag_live.py         # 直播流诊断
└── 数据
    ├── allcfg.json          # 21 个配置的聚合
    ├── cms_test.json / live_test.json / parse_test.json / jar_test.json
    └── fetch_meta.json / live_curated.json / report_final.json
```

`cfg/`、`jar/`、`*.java` 等第三方下载缓存不入库（见 `.gitignore`），需要时按脚本里的 URL 重新拉取。

---

## 常见问题

**视频源过几天就失效了？**
正常。公共免费源的生命周期通常以周计。打开桌面工具点「开始复检」，再点「应用结果并更新配置」即可，原文件自动备份。

**电视连不上，但电脑浏览器能打开？**
按顺序查：是否同一 WiFi / 防火墙是否放行 9978 / 地址里的 IP 是否与电视同网段（路由器后台可看）/ TVBox 是否清了缓存。

**配置报「配置失败」？**
九成是字段类型问题。跑 `python lint_config.py output/xxx.json`，重点看 `ext` 是否为字符串（TVBox 的 `Site.ext` 是 String，写成对象会让 Gson 直接抛异常）。

**港澳台频道？**
真没有。19 个目标频道在 18 个可用直播源里都找不到能播的地址，公共免费清单基本已被清理。不编造。

---

## 免责声明

本项目仅用于技术学习与接口格式研究，所有资源均来自互联网公开信息，版权归各自权利人所有。

请勿用于商业用途，下载后请于 24 小时内删除。使用本配置产生的任何后果由使用者自行承担，作者不对内容的合法性、可用性、安全性作任何担保。如有侵权，请联系删除。

## License

代码部分采用 [MIT](LICENSE)。第三方 jar、接口源、播放器源码等归各自作者所有，本仓库仅为研究与互操作性目的引用。
