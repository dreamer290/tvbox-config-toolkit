# -*- coding: utf-8 -*-
"""
自动发现更新模块
--------------------------------------------------
每次运行在 GitHub 上搜索公开的 TVBox 配置与 IPTV 直播清单，
下载解析 → 实测验证 → 去重合并 → 写回本地配置文件。

流程：
  1) 发现：种子仓库 + GitHub 仓库搜索，列出候选文件
  2) 下载：raw.githubusercontent.com → jsDelivr → 镜像，逐级回退
  3) 解析：点播抽 sites(type 0/1)，直播抽频道 URL 池
  4) 验证：点播三连 URL 真实搜索；直播分片测速（低并发）
  5) 合并：按 key/域名、频道名归一化去重；备份 → 写回 → 体检（失败自动回滚）
  6) 报告：output/update_report.html

仅使用 Python 标准库；命令行：
  python updater.py --preview   # 只发现验证不写回
  python updater.py --run       # 验证后写回（自动备份）
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TVBOX_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TVBOX_ROOT not in sys.path:
    sys.path.insert(0, _TVBOX_ROOT)
import tvcommon as tvc  # http_get / lenient_json（忽略证书、永不抛异常）
import TVBoxTool as tbt  # 复用 parse_channels / probe_stream / lint_config / UA_BROWSER

http_get = tvc.http_get
lenient_json = tvc.lenient_json


def safe_json(raw):
    """宽容 JSON 解析，失败返回 None 而不抛异常（网上的配置文件经常是残缺的）。"""
    try:
        return lenient_json(raw)
    except Exception:
        return None
UA_BROWSER = tbt.UA_BROWSER

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = ""  # 可选：填入 PAT 提升 API 限流额度

SEED_LIVE_REPOS = [
    "iptv-org/iptv",
    "YanG-1989/m3u",
    "joevess/IPTV",
    "YueChan/Live",
]
SEARCH_QUERIES = ["tvbox config", "tvbox 配置", "iptv 直播源 m3u"]

# 新增站点 / 新增频道 / 单频道备用源 的上限（防垃圾源污染）
MAX_NEW_SITES = 15
MAX_VALIDATE_SITES = 40
MAX_NEW_CHANNELS = 24
MAX_BACKUP_URLS = 2
MAX_LIVE_URLS_MEASURE = 160

KBPS_PASS = 1200  # 直播通过线（kbps），与 live_race 保持一致


def canon(name):
    """频道名归一化：小写、去分隔符、统一 CCTV 写法。"""
    s = name.lower().strip()
    s = re.sub(r"cctv[\s\-‐–—丨|/＋+.]*", "cctv", s)
    s = re.sub(r"[\s\-‐–—丨|/＋+.:：·•,，()（）\[\]【】]+", "", s)
    s = s.replace("中央电视台", "cctv").replace("央视", "cctv")
    return s


# =====================================================================
# GitHub 发现与下载
# =====================================================================
def gh_api(path, params=""):
    url = GITHUB_API + path + params
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA_BROWSER}
    if GITHUB_TOKEN:
        headers["Authorization"] = "Bearer " + GITHUB_TOKEN
    r = http_get(url, timeout=12, maxbytes=4000000, headers=headers)
    if not r["ok"]:
        return None
    return safe_json(r["data"])


def fetch_gh_file(user, repo, branch, path):
    """按 直连 raw → jsDelivr → 加速镜像 顺序下载仓库文件，返回文本或 ''。"""
    urls = [
        "https://raw.githubusercontent.com/%s/%s/%s/%s" % (user, repo, branch, path),
        "https://cdn.jsdelivr.net/gh/%s/%s@%s/%s" % (user, repo, branch, urllib.parse.quote(path)),
        "https://ghfast.top/https://raw.githubusercontent.com/%s/%s/%s/%s" % (user, repo, branch, path),
    ]
    for u in urls:
        r = http_get(u, timeout=15, maxbytes=5000000)
        if r["ok"] and r["data"]:
            return r["data"].decode("utf-8", "ignore")
    return ""


def repo_default_branch(full):
    info = gh_api("/repos/" + full)
    if not info or not isinstance(info, dict):
        return None
    return info.get("default_branch") or "main"


def list_repo_files(full, emit):
    """列出仓库文件 [(path, size)]，失败返回 []。"""
    br = repo_default_branch(full)
    if not br:
        emit("log", "  跳过 %s（无法获取分支信息，可能被限流）" % full, "muted")
        return []
    tree = gh_api("/repos/%s/git/trees/%s" % (full, br), "?recursive=1")
    if not tree or not isinstance(tree, dict):
        return []
    out = []
    for it in tree.get("tree", []):
        if it.get("type") != "blob":
            continue
        out.append((it.get("path", ""), it.get("size", 0)))
    return out


def classify_files(files):
    """把仓库文件分为直播清单与点播配置候选。"""
    live, vod = [], []
    for path, size in files:
        low = path.lower()
        if size and size > 4000000:
            continue
        if low.endswith((".m3u", ".m3u8")):
            live.append((path, size))
        elif low.endswith(".txt") and any(k in low for k in ("iptv", "live", "tv", "channel", "stream", "直播")):
            live.append((path, size))
        elif low.endswith(".json") and size and 20000 <= size <= 6000000:
            vod.append((path, size))
    # 候选优先级：路径含 cn/china/cctv 的直播文件；大 JSON 优先
    live.sort(key=lambda x: (0 if any(k in x[0].lower() for k in ("cn", "china", "cctv")) else 1, -x[1]))
    vod.sort(key=lambda x: -x[1])
    return live[:4], vod[:3]


def discover(emit, stop):
    """返回 (live_texts: [(来源, 文本)], vod_configs: [(来源, dict)])"""
    repos = []
    seen = set()
    for q in SEARCH_QUERIES:
        if stop.is_set():
            break
        emit("log", "GitHub 仓库搜索：%s" % q, "muted")
        res = gh_api("/search/repositories", "?q=" + urllib.parse.quote(q) + "&sort=updated&per_page=8")
        for it in (res or {}).get("items", []):
            full = it.get("full_name", "")
            if full and not it.get("fork") and full not in seen:
                seen.add(full)
                repos.append(full)
    for r in SEED_LIVE_REPOS:
        if r not in seen:
            seen.add(r)
            repos.append(r)
    repos = repos[:12]
    emit("log", "候选仓库 %d 个：%s" % (len(repos), ", ".join(repos[:6]) + ("…" if len(repos) > 6 else "")), "muted")

    live_texts, vod_configs = [], []
    for i, full in enumerate(repos):
        if stop.is_set():
            break
        emit("progress", i, len(repos), "扫描仓库 %s" % full)
        files = list_repo_files(full, emit)
        if not files:
            continue
        live_files, vod_files = classify_files(files)
        user, repo = full.split("/", 1)
        br = repo_default_branch(full) or "main"
        for path, _ in live_files:
            txt = fetch_gh_file(user, repo, br, path)
            if txt and ("#EXTM3U" in txt or "#genre#" in txt):
                live_texts.append(("%s/%s" % (full, path), txt))
            if len(live_texts) >= 12:
                break
        for path, _ in vod_files:
            txt = fetch_gh_file(user, repo, br, path)
            j = safe_json(txt.encode("utf-8", "ignore")) if txt else None
            if isinstance(j, dict) and isinstance(j.get("sites"), list) and j["sites"]:
                vod_configs.append(("%s/%s" % (full, path), j))
            if len(vod_configs) >= 10:
                break
        emit("log", "  %s：直播清单 %d · 点播配置 %d" % (full, len(live_texts), len(vod_configs)), "muted")
    return live_texts, vod_configs


# =====================================================================
# 解析
# =====================================================================
def norm_ext(ext):
    """ext 必须是字符串（TVBox Gson 模型的硬要求）。"""
    if ext is None:
        return None
    if isinstance(ext, str):
        return ext
    if isinstance(ext, (dict, list)):
        return json.dumps(ext, ensure_ascii=False, separators=(",", ":"))
    return None


def extract_vod_sites(configs, emit):
    """从配置里抽取 type 0/1 采集站候选（spider 站无法脱离 jar 验证，自动跳过）。"""
    by_api = {}
    skipped = 0
    for src, cfg in configs:
        for s in cfg.get("sites", []):
            if not isinstance(s, dict):
                continue
            if s.get("type") not in (0, 1):
                skipped += 1
                continue
            api = str(s.get("api", "")).strip()
            if not api.lower().startswith("http"):
                skipped += 1
                continue
            key = api.split("?")[0].rstrip("/").lower()
            if key in by_api:
                continue
            by_api[key] = {
                "name": str(s.get("name") or key)[:40],
                "type": int(s["type"]),
                "api": api,
                "ext": norm_ext(s.get("ext")),
                "src": src,
            }
    emit("log", "点播候选 %d 个（去重后），跳过 spider/无效条目 %d 个" % (len(by_api), skipped), "muted")
    return list(by_api.values())[:MAX_VALIDATE_SITES]


def extract_live_urls(live_texts, emit, keep_canons):
    """抽取直播 URL，按频道归一化名归组；只保留与现有频道或常见央视频道匹配的 URL。"""
    pool = {}  # canon -> {"name": 展示名, "urls": {url: None}}
    total = 0
    for src, txt in live_texts:
        for _grp, name, url in tbt.parse_channels(txt):
            url = url.split("$")[0].split("#")[0].strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            c = canon(name)
            if not c:
                continue
            if keep_canons is not None and c not in keep_canons:
                continue
            d = pool.setdefault(c, {"name": name, "urls": {}})
            if len(d["name"]) < len(name):
                d["name"] = name
            if url not in d["urls"] and total < MAX_LIVE_URLS_MEASURE * 3:
                d["urls"][url] = None
                total += 1
    emit("log", "直播候选：频道名 %d 个、URL %d 条" % (len(pool), total), "muted")
    return pool


# =====================================================================
# 验证
# =====================================================================
def test_cms(s):
    """三连 URL 真实搜索测试（ac=detail → ac=videolist → pg=1）。返回 (state, ms, detail)。"""
    base = s["api"].split("?")[0]
    q = urllib.parse.quote("庆余年")
    tries = [
        (base + "?ac=detail&wd=" + q, {}),
        (base + "?ac=videolist&wd=" + q, {}),
        (base + "?ac=detail&pg=1", {"User-Agent": UA_BROWSER}),
    ]
    for u, hdr in tries:
        r = http_get(u, timeout=10, maxbytes=600000, headers=hdr)
        if not r["ok"]:
            continue
        if s["type"] == 1:
            j = safe_json(r["data"])
            lst = []
            if isinstance(j, dict):
                lst = j.get("list") or j.get("data") or []
            if lst and isinstance(lst[0], dict) and "vod_play_url" in str(lst[0]):
                return "ok", r["ms"], "搜索返回 %d 条" % (len(lst) if isinstance(lst, list) else 0)
        else:
            t = r["data"].decode("utf-8", "ignore")
            if "<video" in t or "<list" in t:
                return "ok", r["ms"], "XML 响应正常"
    return "dead", 0, "三种查询均失败"


def measure(url):
    """测一个直播流：m3u8 下载前 4 个分片估算码率；其他流用 Range 抽样。返回 kbps 或 0。"""
    try:
        if ".m3u8" in url.lower():
            r = http_get(url, timeout=10, maxbytes=30000)
            if not r["ok"]:
                return 0
            t = r["data"].decode("utf-8", "ignore")
            if "#EXTM3U" not in t:
                return 0
            segs = [l.strip() for l in t.splitlines() if l.strip() and not l.strip().startswith("#")][:4]
            base = url.rsplit("/", 1)[0]
            n_bytes, t0 = 0, time.time()
            for sg in segs:
                u2 = sg if "://" in sg else base + "/" + sg
                rr = http_get(u2, timeout=10, maxbytes=1500000)
                if rr["ok"]:
                    n_bytes += len(rr["data"])
            dt = time.time() - t0
            return int(n_bytes * 8 / 1024 / dt) if (dt > 0 and n_bytes) else 0
        r = http_get(url, timeout=10, maxbytes=400000, headers={"Range": "bytes=0-399999"})
        if not r["ok"] or len(r["data"]) < 10000:
            return 0
        d = r["data"]
        magic = d[:3] == b"FLV" or d[:3] == b"ID3" or b"#EXTM3U" in d[:4096]
        ct_ok = any(k in r["ct"].lower() for k in ("video", "mpegurl", "octet-stream", "mp2t"))
        if not (magic or ct_ok):
            return 0
        ms = max(r["ms"], 1)
        return int(len(d) * 8 / 1024 / (ms / 1000.0))
    except Exception:
        return 0


def validate_live(pool, emit, stop):
    """低并发测速。返回 {canon: {"name":…, "urls": [(url, kbps) 降序]}}"""
    todo = []
    for c, d in pool.items():
        for u in list(d["urls"])[:6]:
            todo.append((c, u))
    todo = todo[:MAX_LIVE_URLS_MEASURE]
    results = {}
    done = [0]
    emit("stage", "实测直播流 %d 条" % len(todo))

    def work(item):
        c, u = item
        if stop.is_set():
            return
        kbps = measure(u)
        done[0] += 1
        if done[0] % 10 == 0:
            emit("progress", done[0], len(todo), "测速 %s" % pool[c]["name"][:12])
        if kbps >= KBPS_PASS:
            results.setdefault(c, []).append((u, kbps))

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, todo))
    for c in results:
        results[c].sort(key=lambda x: -x[1])
    good = {c: {"name": pool[c]["name"], "urls": results[c]} for c in results if results[c]}
    emit("log", "直播测速完成：%d/%d 条通过（>%d kbps）" % (
        sum(len(v["urls"]) for v in good.values()), len(todo), KBPS_PASS), "muted")
    return good


# =====================================================================
# 合并与写回
# =====================================================================
def load_live_txt(path):
    """解析现有 live.txt，保留行结构：[("group", 文本) | ("chan", 名称, [urls])]"""
    items = []
    try:
        raw = open(path, "r", encoding="utf-8-sig").read()
    except Exception:
        return items
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if "#genre#" in ln:
            items.append(("group", ln.split(",")[0].strip()))
        elif "," in ln and "://" in ln:
            name, _, urls = ln.partition(",")
            ul = [u.split("$")[0].strip() for u in urls.split("#") if u.split("$")[0].strip()]
            items.append(("chan", name.strip(), ul))
    return items


def save_live_txt(path, items):
    lines = []
    for it in items:
        if it[0] == "group":
            lines.append("%s,#genre#" % it[1])
        else:
            lines.append("%s,%s" % (it[1], "#".join(it[2])))
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def merge_live(existing_items, good, emit):
    """把实测通过的新源并入：已有频道追加备用源，新频道进「🆕 自动发现」分组。"""
    idx = {}
    for i, it in enumerate(existing_items):
        if it[0] == "chan":
            idx[canon(it[1])] = i
    updated, appended = 0, 0
    for c, info in good.items():
        if c in idx:
            it = existing_items[idx[c]]
            have = set(it[2])
            extra = [u for u, _ in info["urls"] if u not in have][:MAX_BACKUP_URLS]
            if extra:
                it[2].extend(extra)
                updated += 1
                appended += len(extra)
        else:
            continue
    new_chans = [(c, info) for c, info in good.items() if c not in idx]
    new_chans.sort(key=lambda x: -sum(k for _, k in x[1]["urls"]))
    added_names = []
    if new_chans:
        existing_items.append(("group", "🆕 自动发现"))
        for c, info in new_chans[:MAX_NEW_CHANNELS]:
            urls = [u for u, _ in info["urls"][:MAX_BACKUP_URLS + 1]]
            existing_items.append(("chan", info["name"], urls))
            added_names.append(info["name"])
    emit("log", "直播合并：现有频道补充备用源 %d 处，新增频道 %d 个" % (updated, len(added_names)), "muted")
    return updated, appended, added_names


def merge_sites(cfg, candidates, emit, stop):
    """验证通过的采集站追加进 sites；key 自动生成且不冲突。"""
    exist_keys = {s.get("key") for s in cfg.get("sites", [])}
    exist_apis = {str(s.get("api", "")).split("?")[0].rstrip("/").lower() for s in cfg.get("sites", [])}
    added = []
    for s in candidates:
        if stop.is_set():
            break
        state, ms, detail = test_cms(s)
        if state != "ok":
            emit("row", {"cat": "点播", "name": s["name"], "state": "dead", "ms": ms,
                         "detail": "新源实测失败：" + detail, "key": None})
            continue
        api_key = s["api"].split("?")[0].rstrip("/").lower()
        if api_key in exist_apis:
            continue
        key = "auto" + hashlib.md5(api_key.encode()).hexdigest()[:6]
        while key in exist_keys:
            key += "x"
        exist_keys.add(key)
        site = {"key": key, "name": s["name"], "type": s["type"], "api": s["api"]}
        if s.get("ext"):
            site["ext"] = s["ext"]
        cfg["sites"].append(site)
        exist_apis.add(api_key)
        added.append((s["name"], api_key, ms))
        emit("row", {"cat": "点播", "name": s["name"], "state": "ok", "ms": ms,
                     "detail": "新增采集站（来自 %s）" % s.get("src", "?"), "key": None})
    return added


def write_report(out, summary):
    rows = "".join(
        "<tr><td>%s</td><td style='color:%s'>%s</td><td class='d'>%s</td></tr>" % (
            h, ("#16a34a" if s == "ok" else "#dc2626" if s == "dead" else "#d97706"), n, d)
        for s, n, d in summary.get("report_rows", []))
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>自动更新报告</title>
<style>body{font-family:"Microsoft YaHei UI",sans-serif;margin:24px;color:#1f2430}
h1{font-size:20px}table{border-collapse:collapse;width:100%%;margin-top:12px}
td,th{border:1px solid #e2e6ee;padding:6px 10px;text-align:left;font-size:13px}
th{background:#f4f6f9}.d{color:#556;font-size:12px}</style></head><body>
<h1>TVBox 自动更新报告 · %s</h1>
<p>%s</p>
<table><tr><th>类别</th><th>结果</th><th>名称 / 说明</th></tr>%s</table>
</body></html>""" % (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        summary.get("headline", ""), rows or "<tr><td colspan=3>无</td></tr>")
    p = os.path.join(out, "update_report.html")
    open(p, "w", encoding="utf-8").write(html)
    return p


# =====================================================================
# 主流程
# =====================================================================
class Updater:
    def __init__(self, root_dir, cfg_name, emit, stop_event, preview=False, log=None):
        self.root = root_dir
        self.out = os.path.join(root_dir, "output")
        self.cfg_name = cfg_name or "tvbox-osc-relative.json"
        self.cfg_path = os.path.join(self.out, self.cfg_name)
        self.live_path = os.path.join(self.out, "live.txt")
        self.emit = emit or (lambda *a: None)
        self.stop = stop_event
        self.preview = preview
        self._log = log

    def log(self, msg, tag="info"):
        self.emit("log", msg, tag)
        if self._log:
            self._log(msg, tag)

    def run(self):
        summary = {"preview": self.preview, "report_rows": [], "written": False, "err": None}
        try:
            self._run(summary)
        except Exception as e:
            import traceback
            summary["err"] = str(e)
            self.log("自动更新异常：%s" % e, "err")
            self.emit("log", traceback.format_exc()[-600:], "err")
        self.emit("ufinished", summary)
        return summary

    def _run(self, summary):
        st = summary
        self.emit("stage", "搜索 GitHub 资源")
        self.log("=== 自动发现更新开始（%s）===" % ("预览模式" if self.preview else "写回模式"))
        live_texts, vod_configs = discover(self.emit, self.stop)
        if not live_texts and not vod_configs:
            st["err"] = "未能从 GitHub 获取任何资源（网络受限或被限流），请稍后再试或配置 PAT"
            self.log(st["err"], "err")
            return
        self.log("发现：直播清单 %d 份，点播配置 %d 份" % (len(live_texts), len(vod_configs)))

        # ---- 点播 ----
        self.emit("stage", "解析与验证点播站点")
        cands = extract_vod_sites(vod_configs, self.emit)
        cfg = None
        added = []
        if cands:
            try:
                cfg = json.loads(open(self.cfg_path, "rb").read().decode("utf-8-sig"))
            except Exception as e:
                self.log("读取配置失败：%s" % e, "err")
                cfg = None
            if cfg:
                added = merge_sites(cfg, cands, self.emit, self.stop)
        st["added_sites"] = added
        for n, api, ms in added:
            st["report_rows"].append(("ok", "点播新增", "%s · %s" % (n, api)))
        self.emit("progress", 1, 1, "点播验证完成，新增 %d" % len(added))
        self.log("点播：实测通过并新增 %d 个采集站" % len(added), "ok" if added else "muted")

        # ---- 直播 ----
        self.emit("stage", "解析与实测直播源")
        exist_items = load_live_txt(self.live_path)
        keep = set()
        for it in exist_items:
            if it[0] == "chan":
                keep.add(canon(it[1]))
        pool = extract_live_urls(live_texts, self.emit, keep)
        good = validate_live(pool, self.emit, self.stop)
        updated, appended, new_names = merge_live(exist_items, good, self.emit)
        st["updated_chans"], st["appended_urls"], st["new_chans"] = updated, appended, new_names
        for n in new_names:
            st["report_rows"].append(("ok", "直播新增", n))
        for c, info in good.items():
            for u, k in info["urls"][:1]:
                st["report_rows"].append(("ok", "直播补充", "%s · %d kbps" % (info["name"], k)))
                break
        self.log("直播：补充备用源 %d 处，新增频道 %d 个" % (updated, len(new_names)),
                 "ok" if (updated or new_names) else "muted")

        # ---- 写回 ----
        if self.preview:
            st["headline"] = "预览完成：可新增站点 %d 个、直播补充 %d 处、新频道 %d 个（未写入）" % (
                len(added), updated, len(new_names))
            self.log(st["headline"], "warn")
            return

        import shutil
        bak_dir = os.path.join(self.out, "backup")
        os.makedirs(bak_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backups = []
        if cfg is not None and added:
            bak = os.path.join(bak_dir, "%s.%s.bak" % (os.path.splitext(self.cfg_name)[0], stamp))
            shutil.copy2(self.cfg_path, bak)
            backups.append(bak)
        bak2 = os.path.join(bak_dir, "live.txt.%s.updbak" % stamp)
        shutil.copy2(self.live_path, bak2)
        backups.append(bak2)

        self.emit("stage", "写回配置并体检")
        wrote_any = False
        if cfg is not None and added:
            json.dump(cfg, open(self.cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            wrote_any = True
        save_live_txt(self.live_path, exist_items)
        wrote_any = True

        # 体检，失败自动回滚
        errs, warns = tbt.lint_config(self.cfg_path)
        st["lint_errs"], st["lint_warns"] = errs, warns
        if errs:
            for b in backups:
                try:
                    shutil.copy2(b, self.cfg_path if b.endswith(".bak") else self.live_path)
                except Exception:
                    pass
            st["err"] = "写回后体检失败，已回滚：%s" % "; ".join(errs[:3])
            self.log(st["err"], "err")
            return
        st["written"] = True
        st["backup"] = backups
        st["headline"] = "更新完成：新增站点 %d · 直播补充 %d 处 · 新频道 %d 个%s" % (
            len(added), updated, len(new_names), "（预览）" if self.preview else "")
        self.log(st["headline"], "ok")
        rp = write_report(self.out, st)
        self.log("报告：%s" % rp, "muted")


def main():
    argv = sys.argv[1:]
    preview = "--preview" in argv
    if "--preview" not in argv and "--run" not in argv:
        print("用法：python updater.py --run | --preview")
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    msgs = {"info": "\033[0m", "ok": "\033[32m", "err": "\033[31m", "warn": "\033[33m", "muted": "\033[90m"}

    def emit(kind, *a):
        if kind == "log":
            tag = a[1] if len(a) > 1 else "info"
            print("%s%s\033[0m" % (msgs.get(tag, ""), a[0]))
        elif kind == "stage":
            print("\n--- %s ---" % a[0])
        elif kind == "progress":
            print("  [%d/%d] %s" % (a[0], a[1], a[2] if len(a) > 2 else ""))

    import threading
    up = Updater(root, "tvbox-osc-relative.json", emit, threading.Event(), preview=preview)
    s = up.run()
    print("\n===> %s" % (s.get("headline") or s.get("err") or "完成"))


if __name__ == "__main__":
    main()
