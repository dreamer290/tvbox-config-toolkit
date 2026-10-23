# -*- coding: utf-8 -*-
"""第三轮：别名补缺 + 标注共识校验（剔除被多个源标注成不同频道的 URL）"""
import sys, os, re, json, random, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quality_check import measure
from live_race import load_pool

TARGETS = {t["name"]: t for t in json.load(open("targets.json", encoding="utf-8"))}
RACE = json.load(open("live_race.json", encoding="utf-8"))

ALIAS = {
    "南方卫视": ["南方卫视", "大湾区", "tv South", "南方卫星"],
    "优漫卡通": ["优漫"],
    "金鹰卡通": ["金鹰"],
    "CHC高清电影": ["chc"],
    "CCTV-16 奥林匹克": ["cctv16", "奥林匹克"],
}

def canon(name):
    """把候选频道名映射为规范 id，便于冲突检测"""
    s = re.sub(r"[\s\-·]+", "", name).lower()
    m = re.search(r"cctv(\d+)", s)
    if m:
        return "cctv" + m.group(1)
    if "cctv5+" in s or "cctv5plus" in s:
        return "cctv5+"
    m = re.search(r"([^\s]{2,6})卫视", s)
    if m:
        return m.group(1) + "卫视"
    for kw in ("优漫", "金鹰", "嘉佳", "卡酷", "chc"):
        if kw in s:
            return kw
    return None

def main():
    pool = load_pool()
    url_list = list(pool.items())
    # URL -> 规范 id 集合（共识检测）
    url_canon = {}
    for u, names in url_list:
        cs = set()
        for n in names:
            c = canon(n)
            if c:
                cs.add(c)
        url_canon[u] = cs

    # 校验已有 picks
    def consistent(t, url):
        cs = url_canon.get(url, set())
        target_c = canon(t["name"]) if t["match"]["type"] != "contains" or "cctv" in t["name"] else \
                   t["match"]["kw"]
        if not cs:
            return True  # 无标注信息，保留
        if target_c in cs:
            return len(cs) == 1 or len(cs & set(x for x in cs if x != target_c)) == 0
        return False

    conflicts = 0
    for r in RACE:
        t = TARGETS[r["name"]]
        keep = [p for p in r["picks"] if consistent(t, p["url"])]
        if len(keep) != len(r["picks"]):
            conflicts += len(r["picks"]) - len(keep)
        r["picks"] = keep
        r["ok"] = bool(keep)
    print("剔除冲突标注 pick:", conflicts)

    MISS = [r["name"] for r in RACE if not r["ok"]]
    print("仍缺:", MISS)

    def race(name):
        t = TARGETS[name]
        kws = ALIAS.get(name, [t["match"]["kw"]] if t["match"]["type"] == "contains" else None)
        if t["match"]["type"] == "sat":
            kws = [re.sub(r"卫视.*", "", t["name"]).replace("-", ""), name]
        hits = set()
        for u, names in url_list:
            for n in names:
                s = re.sub(r"[\s\-·]+", "", n).lower()
                if any(k.lower().replace(" ", "") in s for k in (kws or [])):
                    hits.add(u)
        hits = sorted(hits)
        random.seed(name + "#3")
        random.shuffle(hits)
        hits = hits[:40]
        print("\n== %s（候选 %d）" % (name, len(hits)), flush=True)
        good = []
        for u in hits:
            if not consistent(t, u):
                print("  跳过(标注冲突):", u[:60], url_canon.get(u), flush=True)
                continue
            r = measure(u)
            tag = "OK " if (r["ok"] and r["kbps"] > 1200) else "-- "
            print("  %s kbps=%-6d %s | %s" % (tag, r["kbps"], u[:64], r["err"][:36]), flush=True)
            if r["ok"] and r["kbps"] > 1200:
                good.append(dict(url=u, kbps=r["kbps"], segs=r["segs"], ttfb=r["ttfb"]))
            if len(good) >= 3:
                break
        return name, good

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as ex:
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
