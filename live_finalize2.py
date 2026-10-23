# -*- coding: utf-8 -*-
"""最终生成 v2：低并发稳定性确认 + 冲突审计 + 生成 live.txt"""
import sys, os, re, json, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quality_check import measure

RACE = json.load(open("live_race.json", encoding="utf-8"))
TARGETS = {t["name"]: t for t in json.load(open("targets.json", encoding="utf-8"))}
ORDER = [t["name"] for t in json.load(open("targets.json", encoding="utf-8"))]

def main():
    # 冲突审计
    claim = defaultdict(set)
    for r in RACE:
        for p in r["picks"]:
            claim[p["url"]].add(r["name"])
    conflict = {u for u, ts in claim.items() if len(ts) > 1}
    for u in sorted(conflict):
        print("冲突剔除:", u[:64], "<-", sorted(claim[u]))
    for r in RACE:
        r["picks"] = [p for p in r["picks"] if p["url"] not in conflict]

    # 低并发逐频道确认（2 次机会）
    final = []
    for name in ORDER:
        r = next(x for x in RACE if x["name"] == name)
        picks = r["picks"]
        good = []
        for p in picks:
            m1 = measure(p["url"])
            time.sleep(0.8)
            if not (m1["ok"] and m1["kbps"] > 1000):
                m2 = measure(p["url"])
                m1 = m2 if (m2["ok"] and m2["kbps"] > 1000) else m1
            if m1["ok"] and m1["kbps"] > 1000:
                p["kbps"], p["segs"] = m1["kbps"], m1["segs"]
                good.append(p)
            if len(good) >= 3:
                break
        r["picks"] = good
        r["ok"] = bool(good)
        final.append(r)
        print("%-18s %s" % (name, ("OK kbps=%d 备选%d" % (good[0]["kbps"], len(good))) if good else "-- 无可用流"), flush=True)

    # 生成
    lines, group, stat = [], "", []
    for r in final:
        g = TARGETS[r["name"]]["group"]
        if g != group:
            lines.append("%s,#genre#" % g)
            group = g
        if r["ok"]:
            lines.append("%s,%s" % (r["name"], "#".join(p["url"] for p in r["picks"])))
            stat.append((r["name"], r["picks"][0]["kbps"], len(r["picks"])))
        else:
            stat.append((r["name"], 0, 0))
    open("output/live.txt", "w", encoding="utf-8").write("\n".join(lines) + "\n")

    ok = [s for s in stat if s[1]]
    print("\n可用 %d/%d | ≥8Mbps %d | 3-8Mbps %d | <3Mbps %d" % (
        len(ok), len(stat), sum(1 for s in ok if s[1] >= 8000),
        sum(1 for s in ok if 3000 <= s[1] < 8000), sum(1 for s in ok if s[1] < 3000)))
    miss = [n for n, k, c in stat if not k]
    print("仍缺:", miss)
    json.dump(stat, open("live_final_stat.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump([{ "name": r["name"], "ok": r["ok"], "picks": r["picks"]} for r in final],
              open("live_final.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
