# -*- coding: utf-8 -*-
"""最终审计 + 生成新 live.txt"""
import sys, os, re, json, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quality_check import measure

RACE = json.load(open("live_race.json", encoding="utf-8"))
TARGETS = {t["name"]: t for t in json.load(open("targets.json", encoding="utf-8"))}

def main():
    # 1) URL 冲突审计：同一 URL 被多个频道认领 -> 全部剔除
    claim = defaultdict(set)
    for r in RACE:
        for p in r["picks"]:
            claim[p["url"]].add(r["name"])
    conflict_urls = {u for u, ts in claim.items() if len(ts) > 1}
    if conflict_urls:
        print("冲突 URL（多频道认领，全部剔除）:", len(conflict_urls))
        for u in sorted(conflict_urls):
            print("   ", u[:70], "<-", sorted(claim[u]))
    for r in RACE:
        r["picks"] = [p for p in r["picks"] if p["url"] not in conflict_urls]
        r["ok"] = bool(r["picks"])

    miss = [r["name"] for r in RACE if not r["ok"]]
    print("\n审计后缺失:", miss, "| 可用:", len(RACE) - len(miss))

    # 2) 主选流二次稳定性复测（每频道第 1 选择，间隔重测）
    def retest(r):
        if not r["ok"]:
            return None
        time.sleep(0.3)
        m1 = measure(r["picks"][0]["url"])
        return r["name"], m1
    with ThreadPoolExecutor(max_workers=12) as ex:
        retests = [x for x in ex.map(retest, RACE) if x]
    unstable = set()
    for name, m in retests:
        if not m["ok"] or m["kbps"] < 1000:
            unstable.add(name)
    print("复测不稳:", sorted(unstable))
    for r in RACE:
        if r["name"] in unstable:
            r["picks"] = r["picks"][1:]  # 降级到备选
            r["ok"] = bool(r["picks"])

    # 3) 生成 live.txt（主选 + 备选 # 分隔，TVBox 支持故障切换）
    lines = []
    group = ""
    stat = []
    for tname in [t["name"] for t in json.load(open("targets.json", encoding="utf-8"))]:
        r = next(x for x in RACE if x["name"] == tname)
        g = TARGETS[tname]["group"]
        if g != group:
            lines.append("%s,#genre#" % g)
            group = g
        if r["ok"]:
            urls = [p["url"] for p in r["picks"]]
            lines.append("%s,%s" % (tname, "#".join(urls)))
            stat.append((tname, r["picks"][0]["kbps"], len(urls)))
        else:
            stat.append((tname, 0, 0))
    open("output/live.txt", "w", encoding="utf-8").write("\n".join(lines) + "\n")

    ok = [s for s in stat if s[1] > 0]
    fast = [s for s in ok if s[1] >= 8000]
    print("\n=== 最终结果 ===")
    print("可用 %d/%d | ≥8Mbps %d | 3-8Mbps %d | <3Mbps %d" % (
        len(ok), len(stat), len(fast),
        len([s for s in ok if 3000 <= s[1] < 8000]),
        len([s for s in ok if s[1] < 3000])))
    for n, k, c in stat:
        print("  %-18s kbps=%-6d 备选%d" % (n, k, c))
    json.dump(stat, open("live_final_stat.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
