import json, os, shutil, hashlib, urllib.parse, collections, re

cfgs = json.load(open("allcfg.json", encoding="utf-8"))
jars = {r["cfg"]: r for r in json.load(open("jar_test.json", encoding="utf-8"))}
cms_ok = [x for x in json.load(open("cms_test.json", encoding="utf-8")) if x["ok"]]
parses_ok = [x for x in json.load(open("parse_test.json", encoding="utf-8")) if x["ok"]]
curated = json.load(open("live_curated.json", encoding="utf-8"))["ok"]
extra = json.load(open("live_extra.json", encoding="utf-8"))
live_src = {x["u"]: x for x in json.load(open("live_test.json", encoding="utf-8"))}

OUT = "output"
os.makedirs(OUT, exist_ok=True)

# ---------- 1. 点播：已验证采集源 ----------
sites = []
used_keys = set()

def clean_site(src_site, api_norm=None):
    s = dict(src_site)
    for k in list(s.keys()):
        if s[k] in (None, "", [], {}):
            del s[k]
    # TVBox 的 Site.ext 是 String 字段：对象形式会让 Gson 反序列化直接抛异常，
    # 进而导致「配置加载失败」。统一压成紧凑 JSON 字符串，兼容所有分支版本。
    ext = s.get("ext")
    if isinstance(ext, (dict, list)):
        s["ext"] = json.dumps(ext, ensure_ascii=False, separators=(",", ":"))
    return s

# 为每个已验证 api 找“信息最全”的原始站点定义
for rec in sorted(cms_ok, key=lambda x: x["ms"]):
    api = rec["api"]
    best, best_score = None, -1
    for cn, d in cfgs.items():
        for s in d.get("sites") or []:
            if (s.get("api") or "").strip().split("?")[0].rstrip("/").lower() != rec["k"]:
                continue
            score = len(s) + (3 if s.get("playurl") or s.get("playUrl") else 0) + (2 if s.get("categories") else 0)
            if score > best_score:
                best, best_score, best_cfg = s, score, cn
    s = clean_site(best)
    key = (s.get("key") or rec["k"].split("//")[-1].split("/")[0]).strip()
    base = re.sub(r"\s+", "", key)[:32]
    k, i = base, 2
    while k in used_keys:
        k = "%s%d" % (base, i); i += 1
    used_keys.add(k)
    s["key"] = k
    s.pop("jar", None)
    sites.append(s)

cms_count = len(sites)

# ---------- 2. 点播：spider 层（xiaosa + shenmi 同 jar，md5 一致）----------
SPIDER_CFGS = ["xiaosa", "shenmi"]
JAR_CFG = "xiaosa"
jar = jars[JAR_CFG]
SPIDER_URL = jar["url"]
SPIDER_MD5 = jar["declared"] or jar["md5"]

spider_seen = set()
spider_count = 0
skipped_rel = []
for cn in SPIDER_CFGS:
    d = cfgs[cn]
    base = json.load(open("fetch_meta.json", encoding="utf-8"))
    baseurl = [m["url"] for m in base if m["name"] == cn][0]
    for s in d.get("sites") or []:
        if s.get("type") not in (3, 4):
            continue
        api = (s.get("api") or "").strip()
        if not api:
            continue
        # api 为 ./lib/drpy2.min.js、./py/xxx.py 这类相对路径的站点，依赖发布者的
        # 仓库目录结构；配置拷到本地后该路径必然 404，TVBox 里表现为一堆空壳站点。
        if api.startswith(("./", "../", "/")) or api.endswith((".js", ".py")):
            skipped_rel.append((s.get("name") or api, api))
            continue
        if not api.startswith("csp_"):
            skipped_rel.append((s.get("name") or api, api))
            continue
        sig = api + "|" + str(s.get("ext"))[:120]
        if sig in spider_seen:
            continue
        spider_seen.add(sig)
        item = clean_site(s)
        # 相对路径 ext / jar 转绝对（手工拼接，避免 urljoin 压掉代理地址里的 "//"）
        base_dir = baseurl.rsplit("/", 1)[0] + "/"

        def absolutize(p):
            return base_dir + p.lstrip("./")

        ext = item.get("ext")
        if isinstance(ext, str) and not ext.startswith(("http", "{", "[")) and ("/" in ext or "." in ext):
            item["ext"] = absolutize(ext)
        if "jar" in item and not item["jar"].startswith("http"):
            item["jar"] = absolutize(item["jar"])
        key = re.sub(r"\s+", "", (item.get("key") or api))[:32]
        k, i = key, 2
        while k in used_keys:
            k = "%s%d" % (key, i); i += 1
        used_keys.add(k)
        item["key"] = k
        sites.append(item)
        spider_count += 1

# ---------- 3. 直播 ----------
def genre_of(t):
    if t.startswith("CCTV"):
        return "央视频道"
    if "体育" in t:
        return "体育频道"
    if "剧场" in t or "电影" in t:
        return "数字剧场"
    if "卡通" in t:
        return "少儿频道"
    return "卫视频道"

ORDER = {"央视频道": 0, "卫视频道": 1, "体育频道": 2, "少儿频道": 3, "数字剧场": 4}

def sort_key(c):
    g = genre_of(c["target"])
    t = c["target"]
    m = re.match(r"CCTV(\d+)(\+?)", t)
    num = (int(m.group(1)), m.group(2)) if m else (999, t)
    return (ORDER[g], num, t)

chans = sorted(curated + extra, key=sort_key)
replaced = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺", "CCTV4": "CCTV-4 中文国际",
    "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事", "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事",
    "CCTV8": "CCTV-8 电视剧", "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿", "CCTV15": "CCTV-15 音乐",
    "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村",
}
lines = []
last_g = None
for c in chans:
    g = genre_of(c["target"])
    if g != last_g:
        lines.append("%s,#genre#" % g)
        last_g = g
    lines.append("%s,%s" % (replaced.get(c["target"], c["target"]), c["url"]))
open(os.path.join(OUT, "live.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

REMOTE_LIVES = [
    ("IPTV自动更新(233台,已验)", "https://z.szyyds.cn/iptv"),
    ("24live(1031台,已验)", "https://urlzf.zone.id/iptv-api/output/user_result.m3u"),
    ("平台直播(188台,已验)", "http://47.120.41.246:8025/vip/jar/zb.php"),
    ("虎牙一起看(已验)", "https://sub.ottiptv.cc/huyayqk.m3u"),
    ("斗鱼一起看(已验)", "https://sub.ottiptv.cc/douyuyqk.m3u"),
    ("YY轮播(已验)", "https://sub.ottiptv.cc/yylunbo.m3u"),
]
lives = [{"group": "本地精选", "name": "★ 精选%d台·本地验证" % len(chans),
          "type": 0, "url": "./live.txt", "playerType": 0}]
for nm, u in REMOTE_LIVES:
    lives.append({"group": "网络源", "name": nm, "type": 0, "url": u, "playerType": 0})

# ---------- 4. 解析 ----------
parses = []
pn = set()
for p in parses_ok:
    nm = (p.get("names") or ["解析"])[0] or "解析"
    base = re.sub(r"\s+", "", nm)[:20]
    k, i = base, 2
    while k in pn:
        k = "%s%d" % (base, i); i += 1
    pn.add(k)
    parses.append({"name": nm, "type": p["type"] if p["type"] is not None else 0, "url": p["url"]})

# ---------- 5. 组装 ----------
def assemble(spider_value, lives_value):
    c = collections.OrderedDict()
    c["spider"] = spider_value
    c["wallpaper"] = "https://bing.img.run/1920x1080.php"
    c["sites"] = sites
    c["lives"] = lives_value
    c["parses"] = parses
    c["flags"] = ["youku", "qq", "iqiyi", "qiyi", "letv", "sohu", "tudou", "pptv", "mgtv", "wasu", "bilibili"]
    c["doh"] = [{"name": "AliDNS", "url": "https://dns.alidns.com/dns-query", "ips": ["223.5.5.5", "223.6.6.6"]},
                {"name": "DNSPod", "url": "https://doh.pub/dns-query", "ips": ["1.12.12.12", "120.53.53.53"]}]
    c["warningText"] = "本配置由本地实测可用资源汇总生成，仅供学习交流，请于下载后24小时内删除。"
    return c


# 本地版：spider / live.txt 走「配置同目录」相对路径，配合局域网 HTTP 或 clan:// 使用
cfg_local = assemble("./spider.jar;md5;" + SPIDER_MD5, lives)
# 在线版：不依赖任何本地文件，适合直接填 URL
cfg_online = assemble("%s;md5;%s" % (SPIDER_URL, SPIDER_MD5),
                      [x for x in lives if not str(x.get("url", "")).startswith("./")])


def lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# 局域网版：把相对路径展开成写死的 http 地址，兼容不解析 ./ 的分支
LAN = "http://%s:9978/" % lan_ip()
cfg_lan = assemble(LAN + "spider.jar;md5;" + SPIDER_MD5,
                   [dict(x, url=(LAN + x["url"][2:] if str(x.get("url", "")).startswith("./") else x["url"]))
                    for x in lives])

path = os.path.join(OUT, "tvbox-config.json")
json.dump(cfg_local, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
path_on = os.path.join(OUT, "tvbox-online.json")
json.dump(cfg_online, open(path_on, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
path_lan = os.path.join(OUT, "tvbox-lan.json")
json.dump(cfg_lan, open(path_lan, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 附带 spider jar 本地副本
shutil.copy(jar["file"], os.path.join(OUT, "spider.jar"))

print("配置生成:", path, os.path.getsize(path), "bytes")
print("在线版:", path_on, os.path.getsize(path_on), "bytes")
print("点播站点:", len(sites), "(采集源 %d + spider %d)" % (cms_count, spider_count))
print("剔除相对路径/api 非 csp_ 的站点:", len(skipped_rel), [x[0] for x in skipped_rel][:8])
print("直播源:", len(lives), "条；本地精选频道", len(chans))
print("解析:", len(parses))
print("jar:", SPIDER_URL, SPIDER_MD5)
json.dump(dict(cms=cms_count, spider=spider_count, live=len(lives), chans=len(chans),
               parses=len(parses), spider_url=SPIDER_URL, md5=SPIDER_MD5, jar_file=jar["file"]),
          open("build_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
