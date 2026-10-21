import json, sys, re, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from tvcommon import http_get

targets = [
    "CCTV1", "CCTV2", "CCTV3", "CCTV4", "CCTV5", "CCTV5+", "CCTV6", "CCTV7", "CCTV8",
    "CCTV9", "CCTV10", "CCTV11", "CCTV12", "CCTV13", "CCTV14", "CCTV15", "CCTV16", "CCTV17",
    "湖南卫视", "浙江卫视", "江苏卫视", "东方卫视", "北京卫视", "深圳卫视", "广东卫视",
    "安徽卫视", "山东卫视", "天津卫视", "四川卫视", "湖北卫视", "河南卫视", "江西卫视",
    "辽宁卫视", "黑龙江卫视", "贵州卫视", "云南卫视", "重庆卫视", "东南卫视",
    "吉林卫视", "河北卫视", "山西卫视", "陕西卫视", "甘肃卫视", "青海卫视",
    "宁夏卫视", "内蒙古卫视", "新疆卫视", "西藏卫视", "海南卫视", "厦门卫视", "三沙卫视",
    "大湾区卫视", "南方卫视", "金鹰卡通", "卡酷少儿", "优漫卡通", "嘉佳卡通", "哈哈炫动",
    "凤凰中文", "凤凰资讯", "凤凰香港", "翡翠台", "明珠台", "无线新闻", "TVBPlus",
    "HOYTV", "澳视澳门", "澳门莲花", "香港卫视",
    "五星体育", "广东体育", "劲爆体育", "快乐垂钓", "茶频道",
    "CHC动作电影", "CHC高清电影", "CCTV第一剧场", "CCTV风云剧场",
]

def norm(s):
    s = (s or "").strip()
    s = re.sub(r"[\(\)\[\]【】\s]", "", s)
    s = re.sub(r"(4K|8K|1080P|1080I|720P|FHD|UHD|HD|超清|高清|蓝光|标清|原画|超高清|流畅|IPV6|IPv4)", "", s, flags=re.I)
    s = s.replace("-", "").replace("_", "").replace("|", "").replace("·", "").replace("+", "plus")
    return s.upper()

OK_SUFFIX = ("", "综合", "频道", "综合频道", "高清", "TV", "套")

def match(cn, tn):
    if cn == tn:
        return 3
    if cn.startswith(tn):
        rest = cn[len(tn):]
        if rest and not rest[0].isdigit() and rest in OK_SUFFIX:
            return 2
        if not rest:
            return 3
    return 0

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

def probe(u):
    t0 = time.time()
    if ".m3u8" in u.lower() or ".m3u" in u.lower():
        r = http_get(u, timeout=6, maxbytes=20000)
        if r["ok"]:
            t = r["data"].decode("utf-8", "ignore")
            if "#EXTM3U" in t:
                return int((time.time() - t0) * 1000)
        return -1
    r = http_get(u, timeout=6, maxbytes=4096, headers={"Range": "bytes=0-4096"})
    if not r["ok"] or not r["data"]:
        return -1
    d = r["data"]
    if d[:3] == b"FLV" or d[:3] == b"ID3" or b"#EXTM3U" in d[:4096]:
        return int((time.time() - t0) * 1000)
    if any(k in r["ct"].lower() for k in ("video", "mpegurl", "octet-stream", "mp2t")):
        return int((time.time() - t0) * 1000)
    return -1

live = json.load(open("live_test.json", encoding="utf-8"))
good = [x for x in live if x["ok"]]

cand = {}
stats = []
for src in good:
    r = http_get(src["u"], timeout=15, maxbytes=2500000)
    if not r["ok"]:
        stats.append((src["name"], 0, "拉取失败:" + r["err"][:24])); continue
    txt = r["data"].decode("utf-8", "ignore")
    if txt.lstrip().startswith(("[", "{")):
        try:
            j = json.loads(txt, strict=False)
            lines = []
            def walk(o):
                if isinstance(o, dict):
                    nm = o.get("name") or o.get("title"); lk = o.get("url") or o.get("link")
                    if nm and lk:
                        lines.append("%s,%s" % (nm, lk))
                    for x in o.values():
                        walk(x)
                elif isinstance(o, list):
                    for x in o:
                        walk(x)
            walk(j); txt = "\n".join(lines)
        except Exception:
            pass
    n = 0
    for nm, u in channels(txt):
        if "://[" in u:      # IPv6 直连，本机无 v6 出口
            continue
        cn = norm(nm)
        if not cn:
            continue
        cand.setdefault(cn, []).append((src["name"], nm, u)); n += 1
    stats.append((src["name"], len(channels(txt)), "ok"))

print("候选频道名:", len(cand))
pairs = []
for t in targets:
    tn = norm(t)
    hits = []
    for cn, lst in cand.items():
        sc = match(cn, tn)
        if sc:
            for h in lst:
                hits.append((sc, h))
    hits.sort(key=lambda x: -x[0])
    uniq, seen = [], set()
    for sc, h in hits:
        if h[2] in seen:
            continue
        seen.add(h[2]); uniq.append(h)
    pairs.append((t, uniq[:24]))
print("有候选目标:", sum(1 for t, h in pairs if h), "/", len(pairs))

def work(pair):
    t, hits = pair
    if not hits:
        return None
    with ThreadPoolExecutor(max_workers=min(10, len(hits))) as ex:
        res = list(ex.map(lambda h: (probe(h[2]), h), hits))
    okr = [(ms, h) for ms, h in res if ms >= 0]
    if not okr:
        return None
    okr.sort(key=lambda x: x[0])
    ms, h = okr[0]
    return dict(target=t, name=h[1], url=h[2], src=h[0], ms=ms, alternatives=len(okr) - 1)

with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(work, pairs))

ok = [r for r in results if r]
json.dump(dict(ok=ok, all=results), open("live_curated.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
miss = [t for t, r in zip([p[0] for p in pairs], results) if not r]
print("可用频道:", len(ok), "/", len(pairs))
for r in ok:
    print("  %-14s %-16s %5dms  备用%d  %s" % (r["target"], r["name"], r["ms"], r["alternatives"], r["url"][:56]))
print("未找到:", "、".join(miss))
print("\n各源贡献:", end=" ")
from collections import Counter
print(Counter(r["src"] for r in ok).most_common())
