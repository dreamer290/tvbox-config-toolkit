# -*- coding: utf-8 -*-
"""第二轮：对第一轮缺失的频道加大候选上限，并打印每次尝试的结果"""
import sys, os, re, json, random, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quality_check import measure
from live_race import load_pool, name_hits

TARGETS = {t["name"]: t for t in json.load(open("targets.json", encoding="utf-8"))}
RACE = json.load(open("live_race.json", encoding="utf-8"))
MISS = [r["name"] for r in RACE if not r["ok"]]

# 旧清单中曾可用的 URL 作为补充候选
OLD_FALLBACK = {
    "山东卫视": "http://l.cztvcloud.com/channels/lantian/SXshengzhou1/720p.m3u8",
    "黑龙江卫视": "https://idclive.hljtv.com:4430/live/hljws_own.m3u8",
    "南方卫视": "http://110.72.56.100:808/hls/81/index.m3u8",
    "优漫卡通": "http://110.72.56.100:808/hls/31/index.m3u8",
    "CHC高清电影": "http://183.237.95.108:9901/tsfile/live/1008_1.m3u8?key=txiptv&playlive=0&authid=0",
    "内蒙古卫视": "http://222.71.55.147:9901/tsfile/live/0109_1.m3u8?key=txiptv&playlive=0&authid=0",
}

def main():
    pool = load_pool()
    url_list = list(pool.items())
    print("候选:", len(url_list))

    def race(name):
        t = TARGETS[name]
        hits = [u for u, names in url_list if name_hits(t, names)]
        random.seed(name + "#2")
        random.shuffle(hits)
        hits = hits[:30]
        fb = OLD_FALLBACK.get(name)
        if fb and fb not in hits:
            hits.insert(0, fb)
        print("\n== %s（候选 %d）" % (name, len(hits)), flush=True)
        good = []
        for u in hits:
            r = measure(u)
            tag = "OK " if (r["ok"] and r["kbps"] > 1200) else "-- "
            print("  %s kbps=%-6d segs=%-24s %s | %s" % (
                tag, r["kbps"], str(r["segs"][:2]), u[:64], r["err"][:40]), flush=True)
            if r["ok"] and r["kbps"] > 1200:
                good.append(dict(url=u, kbps=r["kbps"], segs=r["segs"], ttfb=r["ttfb"]))
            if len(good) >= 3:
                break
        return name, good

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(race, MISS))
    print("\n耗时 %.0fs" % (time.time() - t0))
    # 合并结果
    for name, good in res:
        for r in RACE:
            if r["name"] == name:
                r["ok"] = bool(good)
                r["picks"] = good
    json.dump(RACE, open("live_race.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    still = [n for n, g in res if not g]
    print("本轮补齐:", len(res) - len(still), "| 仍缺:", still)

if __name__ == "__main__":
    main()
