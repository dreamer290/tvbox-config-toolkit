import json, sys, re, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from tvcommon import http_get

def seg_check(m3u8_url):
    """取 master/媒体播放列表里的首个分片，确认真的能拉到数据"""
    r = http_get(m3u8_url, timeout=8, maxbytes=60000)
    if not r["ok"]:
        return False, 0
    txt = r["data"].decode("utf-8", "ignore")
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    sub = None
    for l in lines:
        if l.startswith("#EXT-X-STREAM-INF"):
            idx = lines.index(l)
            if idx + 1 < len(lines):
                sub = lines[idx + 1]
                break
    if sub:
        u2 = urllib.parse.urljoin(m3u8_url, sub)
        r2 = http_get(u2, timeout=8, maxbytes=60000)
        if not r2["ok"]:
            return False, 0
        txt = r2["data"].decode("utf-8", "ignore")
        base = u2
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
    else:
        base = m3u8_url
    seg = None
    for l in lines:
        if l and not l.startswith("#"):
            seg = l
            break
    if not seg:
        return False, 0
    su = urllib.parse.urljoin(base, seg)
    r3 = http_get(su, timeout=10, maxbytes=200000, headers={"Range": "bytes=0-60000"})
    if not r3["ok"]:
        return False, 0
    return len(r3["data"]) > 800, len(r3["data"])

cur = json.load(open("live_curated.json", encoding="utf-8"))
ok = cur["ok"]

def work(c):
    good, size = seg_check(c["url"])
    c["seg_ok"] = good
    c["seg_bytes"] = size
    return c

with ThreadPoolExecutor(max_workers=12) as ex:
    ok = list(ex.map(work, ok))

full = [c for c in ok if c["seg_ok"]]
part = [c for c in ok if not c["seg_ok"]]
print("播放列表可播:", len(ok), " | 分片验证通过:", len(full), " | 仅播放列表:", len(part))
if part:
    print("仅播放列表通过的:", [(c["target"], c["url"][:40]) for c in part])
cur["ok"] = full
json.dump(cur, open("live_curated.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
