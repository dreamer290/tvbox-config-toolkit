# 「md5缓存失效 / jar load err」根因与修复

## 结论

**不是配置问题，也不是 jar 文件损坏，而是 TVBoxOSC（构建 `20260821-1729`）与 xiaosa/shenmi 系 spider jar 的兼容性冲突。**

xiaosa 的 jar 被 TVBoxOSC 误判为「受保护 jar」，强制走 `DexNative` 加载路径，而该 jar 里没有 `DexNative` 类，加载必然返回 false —— 客户端把这两步失败合并显示成 `md5缓存失效 jar load err`。

## 证据链（全部来自源码与 jar 内字节码）

### 1. 提示文案的真实含义

`ApiConfig.java` 第 534–555 行：cache 存在且 md5 匹配时走 `loadJarAsync`，**加载失败也会回调 `callback.error("md5缓存失效")`**。
也就是说这句提示并不专指 md5 不匹配，`load()` 返回 false 同样触发 —— 文案具有误导性。

### 2. load() 的第一道关

`JarLoader.java` 第 105–127 行：

```java
Class<?> clz = loader.loadClass("com.github.catvod.spider.Init");
protectedJar = protectedInitJar.check(jar);
if (protectedJar) {
    return protectedInitJar.init(clz);   // ← 受保护分支
} else {
    Method method = clz.getMethod("init", Context.class);
    method.invoke(null, App.getInstance());
}
return !protectedJar;
```

### 3. 什么情况算「受保护」

`ProtectedInitJar.java` 的 `isProtected()`：jar 内存在 `com.github.catvod.spider.Init` **且** 调用了 `Process.killProcess`，并且 `Init.init()` 方法里没有当前包名的白名单字符串 → 判为受保护。

实测本机 11 个 jar，判定结果：

| jar | Init | killProcess | 判定 |
| --- | --- | --- | --- |
| xiaosa / shenmi / xiaosa_gh / 当前 spider.jar | ✓ | ✓ | **受保护** |
| builtin_spider | ✓ | ✓ | 受保护 |
| myinterface_starlink | ✓ | ✓ | 受保护 |
| shiguang_svip | ✓ | — | 普通 |
| xiaohezi_Yoursmile | ✓ | — | 普通 |
| junge_top98_1 | ✓ | — | 普通 |
| ok1_liucn_fty | ✓ | — | 普通 |

### 4. 受保护分支为什么必死

```java
private boolean bindDexLoader(Class<?> clz, Object init) {
    Class<?> nativeClass = clz.getClassLoader().loadClass("com.github.catvod.spider.DexNative");
    ...
    if (!(loader instanceof DexClassLoader)) return false;
}
```

实测 `spider.jar` 内：

- 条目只有 `META-INF/ANDROID.RSA`、`ANDROID.SF`、`MANIFEST.MF`、`classes.dex`
- **没有 `Lcom/github/catvod/spider/DexNative;`**
- **没有任何 `.so`**

→ `loadClass` 抛 `ClassNotFoundException` → catch → `return false` → 加载失败。

### 5. jar 其实适配你的播放器，但校验方式对不上

解密 jar 内 AES 白名单（key 为 `1234123412341234`，与 TVBoxOSC 用的是同一个）：

- `Init.a(Context)` 里的**包名白名单**明确包含 `com.github.tvbox.osc.jun` —— 也就是你当前 TVBoxOSC 的 `applicationId`
- 但 TVBoxOSC 只扫 `Init.init()` 方法里的字符串，白名单在 `a()` 里，扫不到
- 于是仍然判成受保护 → 走死路

**这是一个「双向兼容但恰好错位」的 bug**，任何配置写法都绕不过去。

## 修复方案

### 方案 A：换 jar（保持 TVBoxOSC，推荐先试）

已生成 `tvbox-osc-fixed.json`，改用 **shiguang_svip.jar**（普通 jar，`init` 只检查 INTERNET 权限与网络连通性，无包名校验），并配上该 jar 真实存在的站点类：

- 172 个站点：39 个实测采集源 + 133 个 spider 站（3 个因类缺失已剔除）
- 直播 7 个入口（含 53 台精选）、解析 8 个
- 已通过模型体检，jar 在局域网可达（1.94MB）

配置地址填：

```
http://192.168.1.100:9978/tvbox-osc-fixed.json
```

> 上面的 `192.168.1.100` 是占位地址，请换成你自己电脑的局域网 IP（工具启动后会直接显示），
> 或者改用 `tvbox-osc-relative.json`，它走 `./` 相对路径，不用改 IP。

**换之前先点「清空缓存」**，否则会继续读到旧 jar 的缓存文件。

需要 `spider-shiguang.jar` 与配置放在同一目录（已放在 `output/`，服务已托管）。

### 方案 B：换播放器（保留全量 146 站点）

包名白名单里包含 `com.fongmi.android.tv`（影视 TV / FongMi 系）等 50 多个 App。
装一个白名单内的播放器，用原来的 `tvbox-config.json` 即可，xiaosa 的全量 spider 站点都能用。

代价是要重装 App；好处是站点比方案 A 多（146 vs 172，但 spider 类覆盖更全）。

## 为什么之前几轮没查出来

前两轮的判断（`ext` 类型、服务未启动、外网可达性）都是真实存在的问题，但都不是这条链路的原因。
这次能定位，是因为直接读了 TVBoxOSC 的 `JarLoader` / `ProtectedInitJar` / `ApiConfig` 源码，
再用 dex 解析器在 jar 字节码里验证了 `DexNative` 缺失、`killProcess` 存在、白名单内容——
这三条都是静态可证的，不依赖客户端日志。

## 复现与自检脚本

| 脚本 | 作用 |
| --- | --- |
| `jar_audit.py` | 检查 jar 的 Init 类、init 方法签名、宿主依赖方法 |
| `protected_audit.py` | 复刻 TVBoxOSC 的受保护判定，给出每个 jar 的结论 |
| `jar_coverage.py` | 站点 `csp_*` 与 jar 内类的覆盖率 |
| `make_osc_fixed.py` | 用普通 jar 重建 TVBoxOSC 可用配置 |
