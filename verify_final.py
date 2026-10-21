import os
import os
import json, sys, re, urllib.parse, hashlib
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from tvcommon import http_get

CFG = "output/tvbox-config.json"
d = json.load(open(CFG, encoding="utf-8"))
rep = {}

# 1) spider jar（本地版是相对路径，直接校验本地副本；在线版才走网络）
sp = d["spider"]
url, md5 = sp.split(";md5;")
local = hashlib.md5(open("output/spider.jar", "rb").read()).hexdigest()
if url.startswith(("./", "/", "file:")):
    rep["spider"] = dict(url=url, ok=(local == md5), md5=md5, got=local, size=os.path.getsize("output/spider.jar"),
                         local_md5=local, local_match=(local == md5), mode="local")
    print("spider jar: 本地副本 md5", local == md5)
else:
    r = http_get(url, timeout=25, maxbytes=9_000_000)
    h = hashlib.md5(r["data"]).hexdigest() if r["ok"] else ""
    rep["spider"] = dict(url=url, ok=r["ok"] and h == md5, md5=md5, got=h, size=len(r["data"]),
                         local_md5=local, local_match=(local == md5), mode="remote")
    print("spider jar:", "OK" if rep["spider"]["ok"] else "FAIL", "md5", h == md5, "local副本", local == md5)

# 2) 采集源逐站复测
def test_site(s):
    api = s.get("api", "")
    base = api.split("?")[0]
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    tries = [base + "?ac=detail&wd=" + urllib.parse.quote("庆余年"),
             base + "?ac=videolist&wd=" + urllib.parse.quote("庆余年"),
             base + "?ac=detail&pg=1"]
    last = "未知"
    for i, tgt in enumerate(tries):
        r = http_get(tgt, timeout=12, maxbytes=600000, headers=(ua if i else None))
        if not r["ok"]:
            last = "HTTP %d" % r["code"] if r["code"] else r["err"][:30]
            continue
        t = r["data"].decode("utf-8", "ignore")
        if s["type"] == 1:
            try:
                j = json.loads(t, strict=False)
            except Exception:
                last = "返回非JSON"
                continue
            lst = j.get("list") or []
            if lst and isinstance(lst[0], dict) and lst[0].get("vod_play_url"):
                return dict(key=s["key"], name=s.get("name"), type=1, ok=True, n=len(lst),
                            ms=r["ms"], via=i, sample=str(lst[0].get("vod_name"))[:20])
            last = "无播放数据"
        else:
            n = len(re.findall(r"<video", t, re.I))
            if n:
                return dict(key=s["key"], name=s.get("name"), type=0, ok=True, n=n, ms=r["ms"], via=i)
            last = "无video节点"
    return dict(key=s["key"], name=s.get("name"), type=s["type"], ok=False, err=last)

cms = [s for s in d["sites"] if s["type"] in (0, 1)]
with ThreadPoolExecutor(max_workers=12) as ex:
    cres = list(ex.map(test_site, cms))
rep["cms"] = cres
print("采集源复测:", sum(1 for x in cres if x["ok"]), "/", len(cres))

# 3) 直播：本地清单逐台复测
chan = []
for ln in open("output/live.txt", encoding="utf-8"):
    ln = ln.strip()
    if not ln or "#genre#" in ln or "," not in ln:
        continue
    nm, _, u = ln.partition(",")
    chan.append((nm.strip(), u.strip()))

def seg_ok(u):
    r = http_get(u, timeout=8, maxbytes=60000)
    if not r["ok"]:
        return False, r["err"]
    t = r["data"].decode("utf-8", "ignore")
    if "#EXTM3U" not in t:
        return False, "非m3u8"
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    sub = None
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            sub = lines[i + 1]; break
    base = u
    if sub:
        base = urllib.parse.urljoin(u, sub)
        r2 = http_get(base, timeout=8, maxbytes=60000)
        if not r2["ok"]:
            return False, "子列表失败"
        t = r2["data"].decode("utf-8", "ignore")
        lines = [l.strip() for l in t.splitlines() if l.strip()]
    seg = next((l for l in lines if l and not l.startswith("#")), None)
    if not seg:
        return False, "无分片"
    r3 = http_get(urllib.parse.urljoin(base, seg), timeout=10, maxbytes=200000,
                  headers={"Range": "bytes=0-60000"})
    if not r3["ok"]:
        return False, "分片失败"
    return len(r3["data"]) > 800, "%dB" % len(r3["data"])

def one(c):
    ok, info = seg_ok(c[1])
    return dict(name=c[0], url=c[1], ok=ok, info=info)

with ThreadPoolExecutor(max_workers=16) as ex:
    lres = list(ex.map(one, chan))
rep["live_channels"] = lres
bad = [x for x in lres if not x["ok"]]
print("本地直播复测:", len(lres) - len(bad), "/", len(lres))
for b in bad:
    print("   失效:", b["name"], b["info"], b["url"][:50])

# 4) 远程直播清单
def live_src_test(l):
    u = l["url"]
    if u.startswith("./"):
        return dict(name=l["name"], ok=True, info="本地文件", nch=0)
    r = http_get(u, timeout=15, maxbytes=2500000)
    if not r["ok"]:
        return dict(name=l["name"], ok=False, info=r["err"], nch=0)
    t = r["data"].decode("utf-8", "ignore")
    n = t.count("#EXTINF") or sum(1 for x in t.splitlines() if "," in x and "://" in x and "#genre#" not in x)
    return dict(name=l["name"], ok=n > 0, info="%d频道" % n, nch=n)

rep["live_sources"] = [live_src_test(l) for l in d["lives"]]
for x in rep["live_sources"]:
    print("   直播清单:", "OK " if x["ok"] else "BAD", x["name"], x["info"])

# 5) 解析器
def ptest(p):
    tgt = p["url"] + urllib.parse.quote("https://vod2.maowushi.com/20240516/zN7aNzzK/index.m3u8", safe="")
    r = http_get(tgt, timeout=12, maxbytes=150000, headers={"Referer": "https://www.360zy.com/"})
    ok = r["ok"] and "m3u8" in r["data"].decode("utf-8", "ignore")
    return dict(name=p["name"], url=p["url"], ok=ok)

with ThreadPoolExecutor(max_workers=8) as ex:
    pres = list(ex.map(ptest, d["parses"]))
rep["parses"] = pres
print("解析复测:", sum(1 for x in pres if x["ok"]), "/", len(pres))

json.dump(rep, open("report_final.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
