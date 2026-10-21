import json, sys, re, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from tvcommon import http_get

miss_targets = ["大湾区卫视", "卡酷少儿", "哈哈炫动", "凤凰中文", "凤凰资讯", "凤凰香港",
                "翡翠台", "明珠台", "无线新闻", "TVBPlus", "HOYTV", "澳视澳门", "澳门莲花",
                "香港卫视", "劲爆体育", "快乐垂钓", "茶频道", "CHC动作电影", "CHC高清电影"]

def norm(s):
    s = (s or "").strip()
    s = re.sub(r"[\(\)\[\]【】\s]", "", s)
    s = re.sub(r"(4K|8K|1080P|1080I|720P|FHD|UHD|HD|超清|高清|蓝光|标清|原画|超高清|流畅|IPV6|IPv4)", "", s, flags=re.I)
    s = s.replace("-", "").replace("_", "").replace("|", "").replace("·", "").replace("+", "plus")
    return s.upper()

def channels(txt):
    ch, cur = [], ""
    if "#EXTINF" in txt:
        for ln in txt.splitlines():
            ln = ln.strip()
            if ln.startswith("#EXTINF"):
                cur = ln.split(",", 1)[-1].strip() if "," in ln else ""
            elif ln and "://" in ln and not ln.startswith("#"):
                ch.append((cur, ln)); cur = ""
    else:
        for ln in txt.splitlines():
            ln = ln.strip()
            if not ln or "#genre#" in ln:
                continue
            if "," in ln:
                nm, _, u = ln.partition(",")
                if "://" in u:
                    ch.append((nm.strip(), u.strip()))
    return ch

def seg_ok(u):
    r = http_get(u, timeout=8, maxbytes=60000)
    if not r["ok"]:
        return False
    txt = r["data"].decode("utf-8", "ignore")
    if "#EXTM3U" not in txt:
        return False
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    sub = None
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            sub = lines[i + 1]; break
    base = u
    if sub:
        base = urllib.parse.urljoin(u, sub)
        r2 = http_get(base, timeout=8, maxbytes=60000)
        if not r2["ok"]:
            return False
        txt = r2["data"].decode("utf-8", "ignore")
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
    seg = next((l for l in lines if l and not l.startswith("#")), None)
    if not seg:
        return False
    r3 = http_get(urllib.parse.urljoin(base, seg), timeout=10, maxbytes=200000,
                  headers={"Range": "bytes=0-60000"})
    return r3["ok"] and len(r3["data"]) > 800

live = json.load(open("live_test.json", encoding="utf-8"))
srcs = [x for x in live if x["ok"]]

cand = {}
for s in srcs:
    r = http_get(s["u"], timeout=15, maxbytes=2500000)
    if not r["ok"]:
        continue
    txt = r["data"].decode("utf-8", "ignore")
    if txt.lstrip().startswith(("[", "{")):
        try:
            j = json.loads(txt, strict=False); lines = []
            def walk(o):
                if isinstance(o, dict):
                    nm = o.get("name") or o.get("title"); lk = o.get("url") or o.get("link")
                    if nm and lk: lines.append("%s,%s" % (nm, lk))
                    for x in o.values(): walk(x)
                elif isinstance(o, list):
                    for x in o: walk(x)
            walk(j); txt = "\n".join(lines)
        except Exception: pass
    for nm, u in channels(txt):
        if "://[" in u:
            continue
        cand.setdefault(norm(nm), []).append((s["name"], nm, u))

print("候选名:", len(cand))
def work(t):
    tn = norm(t)
    hits = []
    for cn, lst in cand.items():
        if tn in cn or cn in tn:
            hits.extend(lst)
    uniq, seen = [], set()
    for h in hits:
        if h[2] in seen: continue
        seen.add(h[2]); uniq.append(h)
    # 并发探测
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(uniq)))) as ex:
        res = list(ex.map(lambda h: (seg_ok(h[2]), h), uniq[:40]))
    for good, h in res:
        if good:
            return dict(target=t, name=h[1], url=h[2], src=h[0])
    return None

with ThreadPoolExecutor(max_workers=6) as ex:
    out = [r for r in ex.map(work, miss_targets) if r]
json.dump(out, open("live_extra.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("补齐:", len(out), "/", len(miss_targets))
for r in out:
    print("  %-10s %-14s %s" % (r["target"], r["name"], r["url"][:60]))
print("仍缺:", "、".join(t for t in miss_targets if t not in [r["target"] for r in out]))
