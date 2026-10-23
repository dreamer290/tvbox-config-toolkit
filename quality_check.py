# -*- coding: utf-8 -*-
"""实测 live.txt 每个频道的播放质量：可达性、分片延迟、持续下载速率"""
import sys, os, time, ssl, re, json, random, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tvcommon import _ctx

UA = "okhttp/3.15"

def get(url, timeout=8, maxbytes=200000, headers=None):
    h = {"User-Agent": UA}
    if headers: h.update(headers)
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            return True, r.read(maxbytes), time.time() - t0, ""
    except Exception as e:
        return False, b"", time.time() - t0, "%s: %s" % (type(e).__name__, str(e)[:60])

def measure(url):
    """返回 dict(ok, ttfb, seg_ms列表, kbps)"""
    ok, data, ttfb, err = get(url, timeout=10, maxbytes=30000)
    if not ok:
        return dict(ok=False, ttfb=0, segs=[], kbps=0, err=err)
    txt = data.decode("utf-8", "ignore")
    if "#EXTM3U" not in txt:
        return dict(ok=False, ttfb=ttfb, segs=[], kbps=0, err="非 m3u8")
    segs = [l.strip() for l in txt.splitlines() if l.strip() and not l.startswith("#")]
    if not segs:
        return dict(ok=False, ttfb=ttfb, segs=[], kbps=0, err="无分片")
    base = url.rsplit("/", 1)[0]
    seg_ms, total_bytes, total_t = [], 0, 0.0
    for s in segs[:4]:
        su = s if s.startswith("http") else base + "/" + s
        ok2, d2, t2, e2 = get(su, timeout=10, maxbytes=3000000)
        if not ok2:
            return dict(ok=False, ttfb=ttfb, segs=seg_ms, kbps=0, err="分片失败 " + e2)
        seg_ms.append(int(t2 * 1000))
        total_bytes += len(d2); total_t += t2
    kbps = int(total_bytes * 8 / 1024 / total_t) if total_t else 0
    return dict(ok=True, ttfb=int(ttfb * 1000), segs=seg_ms, kbps=kbps, err="")

def main():
    lines = open("output/live.txt", encoding="utf-8").read().splitlines()
    items = []
    for ln in lines:
        ln = ln.strip()
        if not ln or "#genre#" in ln:
            continue
        name, _, url = ln.partition(",")
        items.append((name.strip(), url.strip()))
    print("频道数:", len(items))
    random.seed(3)

    def work(it):
        name, url = it
        r = measure(url)
        r["name"] = name; r["url"] = url
        return r

    with ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(work, items))

    res.sort(key=lambda x: x["name"])
    bad = [r for r in res if not r["ok"]]
    slow = [r for r in res if r["ok"] and (r["kbps"] < 1500 or max(r["segs"], default=0) > 2500)]
    print("\n== 不可用 %d ==" % len(bad))
    for r in bad:
        print("  ❌ %-18s %s" % (r["name"], r["err"]))
    print("\n== 可用但慢(卡顿嫌疑) %d ==" % len(slow))
    for r in slow:
        print("  ⚠️ %-18s kbps=%-6d segs=%s" % (r["name"], r["kbps"], r["segs"]))
    print("\n== 正常 %d ==" % (len(res) - len(bad) - len(slow)))
    for r in res:
        if r["ok"] and r not in slow:
            print("  ✅ %-18s kbps=%-6d segs=%s" % (r["name"], r["kbps"], r["segs"]))
    json.dump([dict(name=r["name"], url=r["url"], ok=r["ok"], kbps=r["kbps"],
                    segs=r["segs"], ttfb=r["ttfb"], err=r["err"]) for r in res],
              open("live_quality.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
