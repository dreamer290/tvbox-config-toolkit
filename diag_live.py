import json, sys, re
sys.path.insert(0, ".")
from tvcommon import http_get

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

live = json.load(open("live_test.json", encoding="utf-8"))
rows = []
for src in [x for x in live if x["ok"]][:10]:
    r = http_get(src["u"], timeout=12, maxbytes=2000000)
    if not r["ok"]:
        print("源拉取失败", src["name"], r["err"]); continue
    txt = r["data"].decode("utf-8", "ignore")
    n = 0
    for nm, u in channels(txt):
        if norm(nm).startswith("CCTV1"):
            rows.append((src["name"], nm, u)); n += 1
            if n >= 2:
                break
print("CCTV1 候选", len(rows))
for s, nm, u in rows:
    r = http_get(u, timeout=9, maxbytes=600)
    head = r["data"][:40].decode("utf-8", "ignore").replace("\n", " ") if r["ok"] else ""
    print(str(s)[:10].ljust(12), str(nm)[:14].ljust(16), str(r["code"]).rjust(4),
          str(r["ms"]).rjust(5), r["ct"][:20].ljust(22), (head or r["err"])[:40], u[:50])
