# -*- coding: utf-8 -*-
"""第四轮：修正 canon 后补 7 个缺失频道"""
import sys, os, re, json, random, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quality_check import measure
from live_race import load_pool
from tvcommon import http_get

TARGETS = {t["name"]: t for t in json.load(open("targets.json", encoding="utf-8"))}
RACE = json.load(open("live_race.json", encoding="utf-8"))

ALIAS = {
    "南方卫视": ["南方卫视", "大湾区", "南方卫星"],
    "优漫卡通": ["优漫"],
    "金鹰卡通": ["金鹰"],
    "嘉佳卡通": ["嘉佳"],
    "CHC高清电影": ["chc"],
    "CCTV-16 奥林匹克": ["cctv16", "奥林匹克"],
    "CCTV-5+ 体育赛事": ["cctv5+"],
}

def canon(name):
    s = re.sub(r"[\s\-·]+", "", name).lower()
    if "cctv5+" in s or "cctv5plus" in s:
        return "cctv5+"
    m = re.search(r"cctv(\d+)", s)
    if m:
        return "cctv" + m.group(1)
    m = re.search(r"([^\s]{2,6})卫视", s)
    if m:
        return m.group(1) + "卫视"
    for kw in ("优漫", "金鹰", "嘉佳", "卡酷", "chc"):
        if kw in s:
            return kw
    return None

def main():
    # 再抓两个补充池
    for n, u in [("gather", "https://gh-proxy.com/https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u"),
                 ("donnie", "https://gh-proxy.com/https://raw.githubusercontent.com/donnie06/iptv/main/live.txt")]:
        r = http_get(u, timeout=20, maxbytes=4_000_000)
        if r["ok"] and len(r["data"]) > 1000:
            open("livesrc/%s.txt" % n, "wb").write(r["data"])
            print("补充池:", n, len(r["data"]))
        else:
            print("补充池失败:", n, r["code"], r["err"][:40])

    pool = load_pool()
    url_list = list(pool.items())
    url_canon = {}
    for u, names in url_list:
        cs = set()
        for n in names:
            c = canon(n)
            if c:
                cs.add(c)
        url_canon[u] = cs

    # 修正已有 picks 的校验（cctvN 之前 5+ 误判；卡通类 canon 不匹配）
    def consistent(t, url):
        cs = url_canon.get(url, set())
        if not cs:
            return True
        target_c = canon(t["name"])
        if target_c in cs:
            return True
        return False

    fixed = 0
    for r in RACE:
        if not r["ok"] and r["name"] not in ALIAS:
            continue
        t = TARGETS[r["name"]]
        keep = [p for p in r["picks"] if consistent(t, p["url"])]
        if keep and not r["ok"]:
            fixed += 1
        r["picks"] = keep
        r["ok"] = bool(keep)
    print("复活:", fixed)

    MISS = [r["name"] for r in RACE if not r["ok"]]
    print("待补:", MISS)

    def race(name):
        t = TARGETS[name]
        kws = ALIAS.get(name)
        hits = set()
        for u, names in url_list:
            for n in names:
                s = re.sub(r"[\s\-·]+", "", n).lower()
                if any(k.lower() in s for k in kws):
                    hits.add(u)
        hits = sorted(hits)
        random.seed(name + "#4")
        random.shuffle(hits)
        hits = hits[:40]
        print("\n== %s（候选 %d）" % (name, len(hits)), flush=True)
        good = []
        for u in hits:
            r = measure(u)
            tag = "OK " if (r["ok"] and r["kbps"] > 1200) else "-- "
            print("  %s kbps=%-6d %s | %s" % (tag, r["kbps"], u[:66], r["err"][:34]), flush=True)
            if r["ok"] and r["kbps"] > 1200:
                good.append(dict(url=u, kbps=r["kbps"], segs=r["segs"], ttfb=r["ttfb"]))
            if len(good) >= 3:
                break
        return name, good

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=7) as ex:
        res = list(ex.map(race, MISS))
    print("耗时 %.0fs" % (time.time() - t0))
    for name, good in res:
        for r in RACE:
            if r["name"] == name:
                r["ok"] = bool(good)
                r["picks"] = good
    json.dump(RACE, open("live_race.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    still = [n for n, g in res if not g]
    print("补齐:", len(res) - len(still), "| 最终仍缺:", still)

if __name__ == "__main__":
    main()
