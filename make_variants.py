"""生成三份诊断/修复用配置变体，用于定位「MD5 缓存失效 / jar err」。

A 版：不含任何 spider —— 用来确认「配置本身能不能加载」
B 版：spider 走在线地址且不带 md5 —— 绕开本地文件与校验
C 版：spider 走本地相对路径且不带 md5 —— 绕开校验，保留离线能力
"""
import json, os, copy

OUT = "output"
base = json.load(open(os.path.join(OUT, "tvbox-config.json"), encoding="utf-8"))
online = json.load(open(os.path.join(OUT, "tvbox-online.json"), encoding="utf-8"))

LAN_LIVES = [l for l in online["lives"] if not str(l.get("url", "")).startswith("./")]


def dump(name, cfg):
    p = os.path.join(OUT, name)
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  %-26s %6d bytes" % (name, os.path.getsize(p)))


# A 版：只保留 macCMS 采集源（type 0/1），彻底不碰 jar
a = copy.deepcopy(online)
a.pop("spider", None)
a["sites"] = [s for s in a["sites"] if s.get("type") in (0, 1)]
a["lives"] = LAN_LIVES

# B 版：在线 spider，去掉 md5 校验
b = copy.deepcopy(online)
url = b["spider"].split(";md5;")[0]
b["spider"] = url
b["lives"] = LAN_LIVES

# C 版：本地 spider，去掉 md5 校验
c = copy.deepcopy(base)
c["spider"] = "./spider.jar"

print("生成诊断配置:")
dump("tvbox-A-nospider.json", a)
dump("tvbox-B-online-nomd5.json", b)
dump("tvbox-C-local-nomd5.json", c)
print("\nA 版站点数:", len(a["sites"]), "（全部 type 0/1，无需 jar）")
print("B 版 spider:", b["spider"])
print("C 版 spider:", c["spider"])
