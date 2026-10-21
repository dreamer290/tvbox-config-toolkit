import json, sys, urllib.parse, re
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from tvcommon import http_get

BAD = set(" \t\r\n'\"<>\\")

def find_m3u8(text):
    for m in re.finditer(r"https?://[^ \t\r\n'\"<>\\]+", text):
        u = m.group(0)
        if ".m3u8" in u:
            return u
    return ""

cfgs = json.load(open("allcfg.json", encoding="utf-8"))
vid = json.load(open("sample_play.json", encoding="utf-8"))[0]

pool = {}
for n, d in cfgs.items():
    for p in (d.get("parses") or []):
        u = (p.get("url") or "").strip()
        if not u.startswith("http"):
            continue
        rec = pool.setdefault(u, dict(url=u, type=p.get("type"), names=[], src=[]))
        rec["names"].append(p.get("name"))
        rec["src"].append(n)

print("唯一解析(可测):", len(pool))

def test(item):
    u, v = item
    sep = "" if u.endswith(("=", "?", "&")) else ("&" if "?" in u else "?url=")
    tgt = u + sep + urllib.parse.quote(vid, safe="")
    r = http_get(tgt, timeout=14, maxbytes=200000,
                 headers={"Referer": "https://www.360zy.com/"})
    if not r["ok"]:
        return dict(url=u, type=v["type"], ok=False, err=r["err"],
                    names=v["names"][:2], src=v["src"][:3])
    t = r["data"].decode("utf-8", "ignore")
    got = find_m3u8(t) if "m3u8" in t else ("m3u8(相对)" if "m3u8" in t else "")
    return dict(url=u, type=v["type"], ok=bool(got),
                err="" if got else "无m3u8返回", got=got[:120], ms=r["ms"],
                names=v["names"][:2], src=v["src"][:3])

with ThreadPoolExecutor(max_workers=14) as ex:
    res = list(ex.map(test, pool.items()))
res.sort(key=lambda x: (not x["ok"], x["url"]))
json.dump(res, open("parse_test.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for x in res:
    print(("OK  " if x["ok"] else "BAD "), "t%s" % x["type"],
          str(x["names"])[:30].ljust(32), x["url"][:62], "|", (x.get("got") or x["err"])[:40])
print("可用解析:", sum(1 for x in res if x["ok"]), "/", len(res))
