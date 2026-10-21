"""生成 TVBoxOSC 可加载的配置。

原因：TVBoxOSC 20260821 的 ProtectedInitJar 会把含 Process.killProcess 的 jar 判为
「受保护」，强制走 DexNative 路径；xiaosa/shenmi 系 jar 没有该类，必然加载失败。
shiguang_svip.jar 是普通 jar，init 即使抛异常也会被判成功，因此可在 TVBoxOSC 运行。
"""
import json, os, shutil, hashlib, re, zipfile
from dex_methods import Dex

OUT = "output"
LAN = "http://192.168.1.100:9978/"
JAR_SRC = "jar/shiguang_svip.jar"
JAR_NAME = "spider-shiguang.jar"

cfgs = json.load(open("allcfg.json", encoding="utf-8"))
src = cfgs["shiguang"]

# 1) 落盘 jar 并算 md5
shutil.copy(JAR_SRC, os.path.join(OUT, JAR_NAME))
md5 = hashlib.md5(open(os.path.join(OUT, JAR_NAME), "rb").read()).hexdigest()
print("jar:", JAR_NAME, os.path.getsize(os.path.join(OUT, JAR_NAME)), "bytes  md5:", md5)

# 2) jar 内真实存在的 spider 类（用于过滤站点）
with zipfile.ZipFile(JAR_SRC) as z:
    d = Dex(z.read("classes.dex"))
    classes = {desc[len("Lcom/github/catvod/spider/"):-1] for desc, _ in d.classes()
               if desc.startswith("Lcom/github/catvod/spider/") and "$" not in desc}
print("jar 内 spider 类:", len(classes))

# 3) 站点 = 已验证采集源 + shiguang 的 spider 站（类必须存在）
base = json.load(open(os.path.join(OUT, "tvbox-config.json"), encoding="utf-8"))
sites = [s for s in base["sites"] if s.get("type") in (0, 1)]
print("保留已验证采集源:", len(sites))

used = {s["key"] for s in sites}
added = skipped = 0
for s in src.get("sites") or []:
    if s.get("type") not in (3, 4):
        continue
    api = str(s.get("api", "")).strip()
    if "csp_" not in api:
        continue
    cls = api.split("csp_")[1].split("&")[0].strip()
    if cls not in classes:
        skipped += 1
        continue
    item = dict(s)
    for k in list(item.keys()):
        if item[k] in (None, "", [], {}):
            del item[k]
    ext = item.get("ext")
    if isinstance(ext, (dict, list)):
        item["ext"] = json.dumps(ext, ensure_ascii=False, separators=(",", ":"))
    # 原配置里有字符串形式的数值字段，TVBox 反序列化会失败，统一纠正
    for f in ("type", "playerType", "searchable", "filterable", "quickSearch", "changeable", "timeout"):
        if f in item and not isinstance(item[f], int):
            try:
                item[f] = int(item[f])
            except (TypeError, ValueError):
                item.pop(f)
    key = re.sub(r"\s+", "", item.get("key") or cls)[:32]
    k, i = key, 2
    while k in used:
        k = "%s%d" % (key, i)
        i += 1
    used.add(k)
    item["key"] = k
    sites.append(item)
    added += 1

print("新增 shiguang spider 站:", added, "| 因类缺失跳过:", skipped)

# 4) 直播：本地清单 + 网络源
lives = [dict(l) for l in base["lives"]]
for l in lives:
    if str(l.get("url", "")).startswith("./"):
        l["url"] = LAN + l["url"][2:]

cfg = dict(base)
cfg["spider"] = LAN + JAR_NAME + ";md5;" + md5
cfg["sites"] = sites
cfg["lives"] = lives

path = os.path.join(OUT, "tvbox-osc-fixed.json")
json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n输出:", path, os.path.getsize(path), "bytes")
print("站点总数:", len(sites), "| 直播:", len(lives), "| 解析:", len(cfg["parses"]))
print("spider:", cfg["spider"])
