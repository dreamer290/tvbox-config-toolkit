# -*- coding: utf-8 -*-
"""从候选直播源池中为每个目标频道竞速选优，生成新的 live.txt"""
import sys, os, time, re, json, random
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quality_check import measure

POOL_DIR = "livesrc"
TARGETS = json.load(open("targets.json", encoding="utf-8"))  # [{name, group, match}]

def parse_m3u(txt):
    out, cur = [], None
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln.startswith("#EXTINF"):
            m = re.search(r",\s*(.+)$", ln)
            cur = m.group(1).strip() if m else None
        elif ln and not ln.startswith("#"):
            if cur:
                out.append((cur, ln))
            cur = None
    return out

def parse_txt(txt):
    out = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln or "#genre#" in ln:
            continue
        if "," in ln:
            name, _, url = ln.partition(",")
            if "://" in url:
                out.append((name.strip(), url.strip()))
    return out

def load_pool():
    pool = {}  # url -> set(names)
    for f in os.listdir(POOL_DIR):
        raw = open(os.path.join(POOL_DIR, f), encoding="utf-8", errors="ignore").read()
        items = parse_m3u(raw) if "#EXTINF" in raw else parse_txt(raw)
        for name, url in items:
            url = url.split("$")[0].strip()
            if not url.startswith("http"):
                continue
            pool.setdefault(url, set()).add(name)
    return pool

def name_hits(target, names):
    """目标频道名与候选频道名是否匹配"""
    t = target["match"]
    for n in names:
        s = re.sub(r"[\s\-·]+", "", n).lower()
        if t["type"] == "cctv":
            m = re.search(r"cctv(\d+)", s)
            if m and int(m.group(1)) == t["num"]:
                return True
        elif t["type"] == "contains":
            if t["kw"] in s:
                return True
        elif t["type"] == "sat":
            # 卫视：关键字 + "卫视"（关键字不能是其他卫视前缀）
            if t["kw"] in s and "卫视" in s:
                return True
    return False

def main():
    pool = load_pool()
    print("候选 URL 总数:", len(pool))
    # 预索引
    url_list = list(pool.items())
    results = {}
    def find(t):
        hits = [u for u, names in url_list if name_hits(t, names)]
        # 去 URL 权重排序：优先 https、之前测过的快源域名
        random.seed(t["name"])
        random.shuffle(hits)
        hits.sort(key=lambda u: (not u.startswith("https"),))
        return t["name"], hits[:10]
    with ThreadPoolExecutor(max_workers=1) as ex:
        found = list(ex.map(find, TARGETS))
    total_cand = sum(len(h) for _, h in found)
    print("目标候选总数:", total_cand)

    def race(item):
        name, hits = item
        good = []
        for u in hits:
            if len(good) >= 3:
                break
            r = measure(u)
            if r["ok"] and r["kbps"] > 1200:
                good.append(dict(url=u, kbps=r["kbps"], segs=r["segs"], ttfb=r["ttfb"]))
        return name, good

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=16) as ex:
        raced = list(ex.map(race, found))
    print("竞速完成 %.0fs" % (time.time() - t0))

    summary = []
    for name, good in raced:
        summary.append(dict(name=name, ok=bool(good), picks=good))
    json.dump(summary, open("live_race.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    miss = [s["name"] for s in summary if not s["ok"]]
    print("成功 %d / %d" % (len(summary) - len(miss), len(summary)))
    if miss:
        print("缺失:", miss)
    for s in summary:
        if s["ok"]:
            p = s["picks"][0]
            print("  ✅ %-16s kbps=%-6d 备选%d个 %s" % (s["name"], p["kbps"], len(s["picks"]), p["url"][:56]))

if __name__ == "__main__":
    main()
