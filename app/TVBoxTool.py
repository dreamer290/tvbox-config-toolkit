# -*- coding: utf-8 -*-
"""
TVBox 配置工具（Windows 桌面版）
--------------------------------------------------
功能：
  1) 一键开启 / 关闭局域网配置分发服务（HTTP）
  2) 展示可直接填进 TVBox 的服务地址，支持复制、浏览器打开
  3) 复检配置里的采集源 / spider 类 / 直播频道 / 解析器
  4) 按复检结果更新配置文件（剔除失效项，自动备份，可回滚）

仅使用 Python 标准库，打包后不依赖任何运行环境。
"""

import collections
import hashlib
import json
import os
import queue
import random
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "TVBox 配置工具"
APP_VER = "1.0"
DEFAULT_PORT = 9978
UA = "okhttp/3.15"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------- 配色 ----------------
BG = "#f4f6f9"
CARD = "#ffffff"
BORDER = "#e2e6ee"
TEXT = "#1f2430"
MUTED = "#77808f"
ACCENT = "#2563eb"
OK = "#16a34a"
ERR = "#dc2626"
WARN = "#d97706"
HEAD = "#1e2433"

FONT = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 9, "bold")
FONT_BIG = ("Microsoft YaHei UI", 13, "bold")
FONT_H = ("Microsoft YaHei UI", 10, "bold")

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


# =====================================================================
# 基础工具
# =====================================================================
def app_dir():
    """可执行文件所在目录（打包后是 exe 目录，开发时是脚本目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_settings():
    p = os.path.join(app_dir(), "settings.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    p = os.path.join(app_dir(), "settings.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def detect_root():
    """定位项目根目录：需同时存在 output 目录和 build_config.py（或已有配置）。"""
    cfg = load_settings().get("root")
    if cfg and os.path.isdir(os.path.join(cfg, "output")):
        return cfg
    base = app_dir()
    p = base
    for _ in range(5):
        if os.path.isdir(os.path.join(p, "output")):
            try:
                js = [x for x in os.listdir(os.path.join(p, "output")) if x.endswith(".json")]
            except Exception:
                js = []
            if js:
                return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def lan_ips():
    """获取本机局域网 IPv4 列表，第一项优先。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            ips.append(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips and not ip.startswith("169.254."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def http_get(url, timeout=12, maxbytes=600000, headers=None):
    h = {"User-Agent": UA}
    if isinstance(headers, str):
        h["User-Agent"] = headers
    elif headers:
        h.update(headers)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            data = r.read(maxbytes)
            return dict(ok=True, code=r.status, data=data, ct=r.headers.get("Content-Type", ""),
                        ms=int((time.time() - t0) * 1000), err="")
    except urllib.error.HTTPError as e:
        return dict(ok=False, code=e.code, data=b"", ct="", ms=int((time.time() - t0) * 1000),
                    err="HTTP %d" % e.code)
    except Exception as e:
        return dict(ok=False, code=0, data=b"", ct="", ms=int((time.time() - t0) * 1000),
                    err="%s: %s" % (type(e).__name__, str(e)[:70]))


def port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def probe_stream(url, ua=None, timeout=8):
    """判断一个直播流地址是否还能出数据。"""
    headers = {"User-Agent": ua} if ua else {}
    if ".m3u8" in url.lower():
        r = http_get(url, timeout=timeout, maxbytes=20000, headers=headers)
        if not r["ok"]:
            return False
        t = r["data"].decode("utf-8", "ignore")
        return "#EXTM3U" in t or "#EXTINF" in t
    headers["Range"] = "bytes=0-4096"
    r = http_get(url, timeout=timeout, maxbytes=4096, headers=headers)
    if not r["ok"] or not r["data"]:
        return False
    d = r["data"]
    if d[:3] == b"FLV" or d[:3] == b"ID3" or b"#EXTM3U" in d[:4096]:
        return True
    return any(k in r["ct"].lower() for k in ("video", "mpegurl", "octet-stream", "mp2t"))


def parse_channels(txt):
    """解析 m3u / txt 直播清单，返回 [(分组, 名称, 地址)]。"""
    chans = []
    group = ""
    if "#EXTINF" in txt or "#EXTM3U" in txt:
        cur, cur_group = "", ""
        for ln in txt.splitlines():
            ln = ln.strip()
            if ln.startswith("#EXTINF"):
                cur = ln
                m = re.search(r'group-title="([^"]*)"', ln)
                cur_group = m.group(1) if m else ""
            elif ln and "://" in ln and not ln.startswith("#"):
                name = ""
                if "," in cur:
                    name = cur.rsplit(",", 1)[-1].strip()
                chans.append((cur_group, name or ln[:20], ln))
                cur = ""
    else:
        for ln in txt.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if "#genre#" in ln:
                group = ln.split(",")[0].strip()
                continue
            if ln.startswith("#"):
                continue
            if "," in ln:
                name, _, url = ln.partition(",")
                if "://" in url:
                    chans.append((group, name.strip(), url.strip()))
    return chans


# =====================================================================
# 配置体检（对应 TVBox 的 Java 数据模型）
# =====================================================================
def lint_config(path):
    """返回 (错误列表, 警告列表)。错误会导致 TVBox 直接「配置失败」。"""
    errs, warns = [], []
    try:
        raw = open(path, "rb").read()
        d = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        return ["文件不是合法 JSON：%s" % e], []

    if not isinstance(d, dict):
        return ["顶层不是 JSON 对象"], []
    sites = d.get("sites")
    if not isinstance(sites, list) or not sites:
        errs.append("sites 缺失或为空")

    keys = collections.Counter()
    for i, s in enumerate(sites or []):
        if not isinstance(s, dict):
            errs.append("第 %d 个站点不是对象" % (i + 1))
            continue
        k = s.get("key")
        if not k:
            errs.append("第 %d 个站点缺少 key" % (i + 1))
        else:
            keys[k] += 1
        if "ext" in s and not isinstance(s["ext"], str):
            errs.append("站点 %s 的 ext 是 %s（TVBox 要求字符串）"
                        % (k, type(s["ext"]).__name__))
        if not isinstance(s.get("type"), int):
            warns.append("站点 %s 缺少 type" % k)
        if s.get("type") in (0, 1):
            api = s.get("api") or ""
            if not str(api).startswith(("http", "csp_")):
                errs.append("站点 %s（采集型）的 api 非法：%s" % (k, str(api)[:40]))

    dup = [k for k, v in keys.items() if v > 1]
    if dup:
        errs.append("存在重复 key：%s" % ", ".join(dup[:6]))

    for field in ("lives", "parses"):
        if field in d and not isinstance(d[field], list):
            errs.append("%s 不是数组" % field)

    sp = d.get("spider")
    if sp:
        if not isinstance(sp, str):
            errs.append("spider 必须是字符串")
        else:
            p = sp.split(";md5;")[0]
            if p.startswith(("./", "../")):
                warns.append("spider 使用相对路径 %s，需由 HTTP/clan 提供才有效" % p)

    return errs, warns


def jar_classes(jar_path):
    """从 jar 的 dex 中提取 spider 类名集合（只做字节级匹配，够用且快）。"""
    import zipfile
    out = set()
    try:
        with zipfile.ZipFile(jar_path) as z:
            for name in z.namelist():
                if not name.endswith(".dex"):
                    continue
                b = z.read(name)
                for m in re.findall(rb"Lcom/github/catvod/spider/([A-Za-z0-9_]{2,40});", b):
                    out.add(m.decode())
    except Exception:
        pass
    return out


# =====================================================================
# 复检引擎（与界面解耦，便于命令行自测）
# =====================================================================
class Report(object):
    def __init__(self, on_add=None):
        self.on_add = on_add
        self.rows = []          # dict(cat,name,state,ms,detail,key,drop)
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")
        self.live_channels = []  # 本地直播清单的逐条结果

    def add(self, cat, name, state, ms=0, detail="", key=None, drop=False, **extra):
        row = dict(cat=cat, name=name, state=state, ms=ms, detail=detail,
                   key=key if key is not None else name, drop=drop)
        row.update(extra)
        self.rows.append(row)
        if self.on_add:
            try:
                self.on_add(row)
            except Exception:
                pass
        return row

    def stats(self):
        c = collections.Counter(r["state"] for r in self.rows)
        return c


class Checker(object):
    """在后台线程执行复检，通过 emit 回调向界面推送进度。"""

    def __init__(self, root, cfg_name, emit, stop_event=None, quick=False):
        self.root = root
        self.out = os.path.join(root, "output")
        self.cfg_path = os.path.join(self.out, cfg_name)
        self.cfg_name = cfg_name
        self.emit = emit
        self.stop = stop_event or threading.Event()
        self.quick = quick
        self.cfg = None
        self.report = Report(on_add=lambda r: self.emit("row", r))
        self.sample_play = ""
        self.jar_cs = set()
        self.total = 0
        self.done = 0

    # ---------- 进度 ----------
    def tick(self, n=1, note=""):
        self.done += n
        self.emit("progress", self.done, self.total, note)

    def log(self, msg, tag="info"):
        self.emit("log", msg, tag)

    # ---------- 主流程 ----------
    def run(self):
        self.emit("stage", "读取配置")
        self.cfg = json.load(open(self.cfg_path, encoding="utf-8"))
        sites = self.cfg.get("sites") or []
        lives = self.cfg.get("lives") or []
        parses = self.cfg.get("parses") or []

        cms = [s for s in sites if s.get("type") in (0, 1)]
        spiders = [s for s in sites if s.get("type") in (3, 4)]
        # 本地直播清单
        local_live = [l for l in lives if str(l.get("url", "")).startswith(("./", "http://127.", "http://localhost"))]
        remote_live = [l for l in lives if l not in local_live]
        local_chans = []
        if local_live:
            local_chans = self.read_local_live(local_live[0])

        sample = 6 if self.quick else 5
        per_live = 3 if self.quick else 6
        cms_list = cms[:6] if self.quick else cms
        chan_list = local_chans[:10] if self.quick else local_chans

        self.total = (1 + len(cms_list) + 1 + len(spiders) + len(remote_live) * per_live
                      + len(chan_list) + len(parses))
        self.total = max(self.total, 1)
        self.emit("progress", 0, self.total, "准备")

        # ---- 1. 结构体检 ----
        self.emit("stage", "配置体检")
        errs, warns = lint_config(self.cfg_path)
        if errs:
            for e in errs[:8]:
                self.report.add("配置", "结构错误", "dead", 0, e, key="lint")
            self.log("结构体检发现 %d 个错误" % len(errs), "err")
        else:
            self.report.add("配置", os.path.basename(self.cfg_path), "ok", 0,
                            "字段类型与 key 唯一性正常", key="lint")
        for w in warns[:5]:
            self.report.add("配置", "提示", "warn", 0, w, key="lint-warn")
        self.tick(1, "结构体检")

        # ---- 2. spider jar ----
        self.emit("stage", "校验 spider jar")
        sp = self.cfg.get("spider") or ""
        jar_path, jar_ok, jar_detail = self.check_jar(sp)
        self.report.add("jar", os.path.basename(jar_path) if jar_path else "spider",
                        "ok" if jar_ok else "dead", 0, jar_detail, key="jar", drop=False)
        if jar_ok:
            self.jar_cs = jar_classes(jar_path)
        self.tick(1, "jar 校验")

        # ---- 3. 采集源 ----
        self.emit("stage", "复测采集源")
        if cms_list:
            with ThreadPoolExecutor(max_workers=10) as ex:
                futs = {ex.submit(self.test_cms, s): s for s in cms_list}
                for f in futs:
                    if self.stop.is_set():
                        break
                    try:
                        r = f.result()
                    except Exception as e:
                        r = dict(key=futs[f].get("key"), name=futs[f].get("name") or "?",
                                 state="warn", ms=0, detail="检查异常：%s" % e, play_url="")
                    self.report.add("站点", r["name"], r["state"], r["ms"], r["detail"],
                                    key=r["key"], drop=(r["state"] == "dead"))
                    if r.get("play_url") and not self.sample_play:
                        self.sample_play = r["play_url"]
                    self.tick(1, "采集源")

        # ---- 4. spider 类 ----
        self.emit("stage", "校验 spider 站点")
        for s in spiders:
            if self.stop.is_set():
                break
            api = str(s.get("api", ""))
            cls = api.split("&")[0].strip()
            if not cls.startswith("csp_"):
                self.report.add("spider", s.get("name") or cls, "warn", 0,
                                "api 非 csp_ 前缀：%s" % api[:30], key=s.get("key"), drop=True)
            elif self.jar_cs and cls[4:] not in self.jar_cs:
                self.report.add("spider", s.get("name") or cls, "dead", 0,
                                "jar 内不存在类 %s" % cls, key=s.get("key"), drop=True)
            else:
                self.report.add("spider", s.get("name") or cls, "ok", 0,
                                "类存在于 jar（桌面端无法实测内容源）", key=s.get("key"))
            self.tick(1, "spider")

        # ---- 5. 直播 ----
        if local_chans:
            self.emit("stage", "复测精选频道（%d 台）" % len(chan_list))
            self.check_channels(chan_list, local_live[0])

        for l in remote_live:
            if self.stop.is_set():
                break
            self.emit("stage", "复测直播源 %s" % str(l.get("name"))[:16])
            self.check_live_entry(l, per_live)

        # ---- 6. 解析 ----
        if parses:
            self.emit("stage", "复测解析器")
            for p in parses:
                if self.stop.is_set():
                    break
                r = self.test_parse(p)
                self.report.add("解析", r["name"], r["state"], r["ms"], r["detail"],
                                key=r["url"], drop=(r["state"] == "dead"))
                self.tick(1, "解析")

        self.emit("finished", self.report, self.cfg_name, self.stop.is_set())
        return self.report

    # ---------- 各类检查 ----------
    def check_jar(self, sp):
        if not sp:
            return None, False, "配置未声明 spider"
        p = sp.split(";md5;")[0].strip()
        md5 = ""
        if ";md5;" in sp:
            md5 = sp.split(";md5;")[1].strip().lower()
        if p.startswith(("http://", "https://")):
            name = os.path.basename(urllib.parse.urlsplit(p).path)
        else:
            name = os.path.basename(p)
        local = os.path.join(self.out, name)
        if not os.path.exists(local):
            return local, False, "本地缺少 %s（在线地址：%s）" % (name, p[:60])
        h = hashlib.md5(open(local, "rb").read()).hexdigest()
        size = os.path.getsize(local)
        if md5 and h != md5:
            return local, False, "MD5 不匹配：本地 %s / 声明 %s" % (h[:12], md5[:12])
        if not open(local, "rb").read(2) == b"PK":
            return local, False, "不是有效 zip/jar"
        cs = jar_classes(local)
        return local, True, "%s · %.1fMB · MD5 %s · %d 个 spider 类" % (
            name, size / 1048576.0, h[:8], len(cs))

    def test_cms(self, s):
        api = s.get("api", "")
        base = api.split("?")[0]
        name = s.get("name") or s.get("key")
        if not base.startswith("http"):
            return dict(key=s.get("key"), name=name, state="dead", ms=0,
                        detail="api 非 http：%s" % api[:30], play_url="")
        word = urllib.parse.quote("庆余年")
        tries = [base + "?ac=detail&wd=" + word,
                 base + "?ac=videolist&wd=" + word,
                 base + "?ac=detail&pg=1"]
        last = "无响应"
        for i, u in enumerate(tries):
            if self.stop.is_set():
                return dict(key=s.get("key"), name=name, state="warn", ms=0, detail="已中止", play_url="")
            r = http_get(u, timeout=10, maxbytes=600000,
                         headers=UA_BROWSER if i == 2 else None)
            if not r["ok"]:
                last = r["err"]
                continue
            txt = r["data"].decode("utf-8", "ignore")
            if s.get("type") == 1:
                try:
                    j = json.loads(txt, strict=False)
                except Exception:
                    last = "返回非 JSON（多为拦截页）"
                    continue
                lst = j.get("list") or j.get("data") or []
                if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                    it = lst[0]
                    pu = it.get("vod_play_url") or ""
                    eps = [x.split("$")[-1] for x in pu.split("#") if "$" in x]
                    if it.get("vod_id") or it.get("vod_name"):
                        return dict(key=s.get("key"), name=name, state="ok", ms=r["ms"],
                                    detail="搜索到 %d 条，首条：%s" % (len(lst), str(it.get("vod_name"))[:12]),
                                    play_url=(eps[0] if eps else ""))
                last = "list 为空 / code=%s" % j.get("code")
            else:
                n = len(re.findall(r"<video", txt, re.I))
                if n:
                    return dict(key=s.get("key"), name=name, state="ok", ms=r["ms"],
                                detail="XML 返回 %d 个节点" % n, play_url="")
                last = "无 video 节点"
        return dict(key=s.get("key"), name=name, state="dead", ms=0, detail=last[:60], play_url="")

    def read_local_live(self, entry):
        url = str(entry.get("url", ""))
        path = os.path.join(self.out, os.path.basename(urllib.parse.urlsplit(url).path))
        if not os.path.exists(path):
            self.report.add("直播", entry.get("name") or "本地清单", "dead", 0,
                            "缺少文件 %s" % os.path.basename(path), key="live-file", drop=False)
            return []
        txt = open(path, encoding="utf-8", errors="ignore").read()
        chans = parse_channels(txt)
        self.local_live_path = path
        if not chans:
            self.report.add("直播", entry.get("name") or "本地清单", "dead", 0,
                            "清单解析不出频道", key="live-file", drop=False)
        return chans

    def check_channels(self, chans, entry):
        name0 = entry.get("name") or "本地清单"
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(probe_stream, u): (g, n, u) for g, n, u in chans}
            okn = 0
            for f in futs:
                if self.stop.is_set():
                    break
                g, n, u = futs[f]
                try:
                    good = f.result()
                except Exception:
                    good = False
                okn += 1 if good else 0
                self.report.add("频道", n, "ok" if good else "dead", 0,
                                (g + " · " if g else "") + u[:46],
                                key=u, drop=not good)
                self.tick(1, "频道")
        if chans:
            self.report.add("直播", name0, "ok" if okn else "dead", 0,
                            "%d/%d 台可播" % (okn, len(chans)), key="live-summary", drop=False)

    def check_live_entry(self, l, samples):
        url = str(l.get("url", ""))
        name = str(l.get("name") or url[:20])
        if not url.startswith("http"):
            self.report.add("直播", name, "warn", 0, "地址非 http，跳过", key=url, drop=False)
            self.tick(1)
            return
        r = http_get(url, timeout=12, maxbytes=1500000)
        if not r["ok"]:
            self.report.add("直播", name, "dead", 0, r["err"], key=url, drop=True)
            for _ in range(samples - 1):
                self.tick(1)
            return
        txt = r["data"].decode("utf-8", "ignore")
        chans = parse_channels(txt)
        if not chans:
            self.report.add("直播", name, "dead", 0, "清单解析不出频道", key=url, drop=True)
            for _ in range(samples - 1):
                self.tick(1)
            return
        step = max(1, len(chans) // samples)
        picks = [chans[i] for i in range(0, len(chans), step)][:samples]
        alive = 0
        for g, n, u in picks:
            if self.stop.is_set():
                break
            if probe_stream(u, l.get("ua")):
                alive += 1
            self.tick(1, "直播")
        state = "ok" if alive else "dead"
        self.report.add("直播", name, state, r["ms"],
                        "%d 台，抽样 %d 台通 %d" % (len(chans), len(picks), alive),
                        key=url, drop=(state == "dead"))

    def test_parse(self, p):
        url = str(p.get("url") or "")
        name = str(p.get("name") or url[:18])
        if not url.startswith("http"):
            return dict(name=name, url=url, state="warn", ms=0, detail="非 http 地址")
        target = self.sample_play
        if not target:
            return dict(name=name, url=url, state="warn", ms=0, detail="缺少可用视频地址，未测")
        sep = "" if url.endswith(("=", "?", "&")) else ("&" if "?" in url else "?url=")
        r = http_get(url + sep + urllib.parse.quote(target, safe=""), timeout=14,
                     maxbytes=200000, headers={"Referer": "https://www.360zy.com/"})
        if not r["ok"]:
            return dict(name=name, url=url, state="dead", ms=0, detail=r["err"])
        t = r["data"].decode("utf-8", "ignore")
        if "m3u8" in t:
            return dict(name=name, url=url, state="ok", ms=r["ms"], detail="返回播放地址")
        return dict(name=name, url=url, state="dead", ms=r["ms"], detail="未返回 m3u8")


# =====================================================================
# 应用配置（按复检结果剔除失效项）
# =====================================================================
def apply_report(root, cfg_name, report, log):
    out = os.path.join(root, "output")
    path = os.path.join(out, cfg_name)
    cfg = json.load(open(path, encoding="utf-8"))
    drop_sites = {r["key"] for r in report.rows if r["cat"] in ("站点", "spider") and r["drop"]}
    drop_lives = {r["key"] for r in report.rows if r["cat"] == "直播" and r["drop"]}
    drop_parses = {r["key"] for r in report.rows if r["cat"] == "解析" and r["drop"]}
    dead_chans = {r["key"] for r in report.rows if r["cat"] == "频道" and r["drop"]}
    ok_chans = {r["key"] for r in report.rows if r["cat"] == "频道" and r["state"] == "ok"}

    before = (len(cfg.get("sites") or []), len(cfg.get("lives") or []), len(cfg.get("parses") or []))

    new_sites = [s for s in cfg.get("sites") or [] if s.get("key") not in drop_sites]
    new_lives = [l for l in cfg.get("lives") or []
                 if str(l.get("url")) not in drop_lives]
    new_parses = [p for p in cfg.get("parses") or []
                  if str(p.get("url")) not in drop_parses]

    cfg["sites"] = new_sites
    cfg["lives"] = new_lives
    cfg["parses"] = new_parses

    # 备份
    bak_dir = os.path.join(out, "backup")
    os.makedirs(bak_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(bak_dir, "%s.%s.bak" % (os.path.splitext(cfg_name)[0], stamp))
    shutil.copy2(path, bak)

    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 本地直播清单同步裁剪
    pruned = 0
    live_file = os.path.join(out, "live.txt")
    if os.path.exists(live_file) and ok_chans:
        lines, group = [], ""
        for ln in open(live_file, encoding="utf-8", errors="ignore").read().splitlines():
            s = ln.strip()
            if not s:
                continue
            if "#genre#" in s:
                group = s
                lines.append(s)
                continue
            name, _, url = s.partition(",")
            if url.strip() in ok_chans:
                if group and lines and not lines[-1].startswith(("#",)) and lines[-1] != group:
                    pass
                lines.append(s)
            else:
                pruned += 1
        open(live_file, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    after = (len(new_sites), len(new_lives), len(new_parses))
    log("已更新 %s：站点 %d→%d，直播源 %d→%d，解析 %d→%d，频道裁剪 %d 条"
        % (cfg_name, before[0], after[0], before[1], after[1], before[2], after[2], pruned), "ok")
    log("原文件已备份：%s" % bak, "muted")
    return dict(before=before, after=after, backup=bak, pruned=pruned)


# =====================================================================
# HTTP 服务
# =====================================================================
class ConfigServer(object):
    def __init__(self, root_dir, port, on_log=None, on_req=None):
        self.root = root_dir
        self.port = port
        self.httpd = None
        self.thread = None
        self.on_log = on_log or (lambda *a: None)
        self.on_req = on_req or (lambda *a: None)
        self.count = 0

    def make_handler(self):
        srv = self

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=srv.root, **kw)

            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
                super().end_headers()

            def log_message(self, fmt, *args):
                srv.count += 1
                try:
                    srv.on_req(self.path, self.command)
                except Exception:
                    pass

        return H

    def start(self):
        # Windows 下 SO_REUSEADDR 允许重复绑定同一端口，会造成多个服务抢端口，
        # 必须关闭；端口被占时让 start() 直接抛错，由界面提示换端口。
        ThreadingHTTPServer.allow_reuse_address = (os.name != "nt")
        ThreadingHTTPServer.daemon_threads = True
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self.make_handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.3},
                                       daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None
        self.thread = None

    def alive(self):
        return self.httpd is not None and self.thread is not None and self.thread.is_alive()


# =====================================================================
# 界面
# =====================================================================
class App(object):
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.ttk = ttk

        self.root_dir = detect_root()
        self.settings = load_settings()
        self.server = None
        self.msgq = queue.Queue()
        self.checker = None
        self.stop_event = threading.Event()
        self.report = None
        self.cfg_name = self.settings.get("cfg") or ""
        self.rows_by_iid = {}
        self._busy_verify = False
        self.quick_run = False

        self.win = tk.Tk()
        self.win.title("%s v%s" % (APP_NAME, APP_VER))
        self.win.geometry("1040x760")
        self.win.minsize(920, 680)
        self.win.configure(bg=BG)
        try:
            self.win.iconbitmap(default="")
        except Exception:
            pass
        self._dpi()
        self._style()
        self._build()
        self.win.after(120, self._drain)
        self.win.after(400, self._autostart)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 基础 ----------
    def _dpi(self):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    def _style(self):
        ttk = self.ttk
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", font=FONT, background=BG, foreground=TEXT)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=CARD, foreground=TEXT)
        st.configure("Muted.TLabel", background=CARD, foreground=MUTED)
        st.configure("Head.TLabel", background=CARD, foreground=TEXT, font=FONT_H)
        st.configure("TButton", padding=(12, 6), font=FONT, borderwidth=1)
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     font=FONT_B, borderwidth=0, padding=(16, 8))
        st.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#9db8f0")])
        st.configure("Danger.TButton", background="#eee6e6", foreground=ERR, borderwidth=0,
                     padding=(16, 8), font=FONT_B)
        st.map("Danger.TButton", background=[("active", "#f3d6d6")])
        st.configure("Ghost.TButton", background=CARD, foreground=TEXT, borderwidth=1,
                     relief="solid", padding=(10, 5))
        st.configure("TEntry", fieldbackground="#fbfcfe", bordercolor=BORDER, padding=6)
        st.configure("TSpinbox", fieldbackground="#fbfcfe", bordercolor=BORDER, padding=4)
        st.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                     rowheight=24, borderwidth=0, font=("Microsoft YaHei UI", 9))
        st.configure("Treeview.Heading", font=FONT_B, background="#f1f4f9",
                     foreground=MUTED, relief="flat", padding=(6, 6))
        st.map("Treeview", background=[("selected", "#e8f0ff")],
               foreground=[("selected", TEXT)])
        st.configure("TCombobox", padding=4)
        st.configure("TProgressbar", background=ACCENT, troughcolor="#e8ecf3", borderwidth=0)
        st.configure("Bar.Horizontal.TProgressbar", background=ACCENT, troughcolor="#e8ecf3",
                     borderwidth=0, thickness=6)

    def _card(self, parent, title=None, pady=10):
        tk, ttk = self.tk, self.ttk
        outer = tk.Frame(parent, bg=BORDER, bd=0)
        inner = tk.Frame(outer, bg=CARD, bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            tk.Label(inner, text=title, bg=CARD, fg=TEXT, font=FONT_H,
                     anchor="w").pack(fill="x", padx=14, pady=(10, 0))
        body = tk.Frame(inner, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=(8, 12))
        return outer, body

    # ---------- 布局 ----------
    def _build(self):
        tk, ttk = self.tk, self.ttk
        # 顶栏
        head = tk.Frame(self.win, bg=HEAD, height=64)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="TVBox 配置工具", bg=HEAD, fg="#ffffff", font=("Microsoft YaHei UI", 14, "bold")
                 ).place(x=18, y=11)
        self.lbl_root = tk.Label(head, text="", bg=HEAD, fg="#9aa4b8", font=("Microsoft YaHei UI", 8))
        self.lbl_root.place(x=19, y=40)
        self.chip = tk.Label(head, text="● 服务未启动", bg=HEAD, fg="#f08a8a",
                             font=("Microsoft YaHei UI", 9, "bold"))
        self.chip.place(relx=1.0, x=-22, y=22, anchor="ne")
        self.btn_root = tk.Button(head, text="更改目录", command=self.change_root,
                                  bg="#2b3242", fg="#cfd6e4", bd=0, font=("Microsoft YaHei UI", 8),
                                  activebackground="#394155", activeforeground="#ffffff",
                                  cursor="hand2", padx=10, pady=3)
        self.btn_root.place(relx=1.0, x=-130, y=14, anchor="ne")

        body = tk.Frame(self.win, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=12)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=300)
        body.rowconfigure(2, weight=1)

        # --- 服务卡 ---
        card, f = self._card(body, "① 服务控制")
        card.grid(row=0, column=0, sticky="ew", padx=(0, 12))

        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="端口", bg=CARD, fg=MUTED).pack(side="left")
        self.var_port = tk.StringVar(value=str(self.settings.get("port", DEFAULT_PORT)))
        self.sp = ttk.Spinbox(row, from_=1024, to=65535, width=7, textvariable=self.var_port,
                              command=self._port_changed)
        self.sp.pack(side="left", padx=(8, 14))
        self.btn_srv = ttk.Button(row, text="启动服务", style="Accent.TButton", command=self.toggle_server)
        self.btn_srv.pack(side="left")
        ttk.Button(row, text="打开输出目录", style="Ghost.TButton",
                   command=self.open_dir).pack(side="left", padx=8)
        self.var_auto = tk.BooleanVar(value=bool(self.settings.get("autostart", True)))
        tk.Checkbutton(row, text="启动程序时自动开启", variable=self.var_auto, bg=CARD,
                       fg=MUTED, activebackground=CARD, font=FONT, bd=0, highlightthickness=0,
                       selectcolor="#ffffff").pack(side="left", padx=(8, 0))

        # 地址区
        self.addr_box = tk.Frame(f, bg="#f7f9fc")
        self.addr_box.pack(fill="x", pady=(12, 0))
        self.build_addrs()

        # --- 右侧：配置选择 ---
        card2, f2 = self._card(body, "② 当前配置")
        card2.grid(row=0, column=1, sticky="nsew")
        tk.Label(f2, text="复检与更新都作用于这个文件", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8)
                 ).pack(anchor="w")
        self.var_cfg = tk.StringVar()
        self.cb_cfg = ttk.Combobox(f2, textvariable=self.var_cfg, state="readonly", width=26)
        self.cb_cfg.pack(fill="x", pady=6)
        self.cb_cfg.bind("<<ComboboxSelected>>", lambda e: self.on_cfg_pick())
        self.lbl_cfg_info = tk.Label(f2, text="", bg=CARD, fg=MUTED, justify="left",
                                     font=("Microsoft YaHei UI", 8), anchor="w")
        self.lbl_cfg_info.pack(fill="x")
        self.refresh_cfg_list()

        # --- 复检卡 ---
        card3, f3 = self._card(body, "③ 复检 / 更新配置")
        card3.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        bar = tk.Frame(f3, bg=CARD)
        bar.pack(fill="x")
        self.btn_check = ttk.Button(bar, text="开始复检", style="Accent.TButton", command=self.start_check)
        self.btn_check.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", style="Ghost.TButton", command=self.stop_check,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_apply = ttk.Button(bar, text="应用结果并更新配置", style="Ghost.TButton",
                                    command=self.apply_results, state="disabled")
        self.btn_apply.pack(side="left")
        self.btn_report = ttk.Button(bar, text="导出报告", style="Ghost.TButton",
                                     command=self.export_report, state="disabled")
        self.btn_report.pack(side="left", padx=8)
        self.lbl_stage = tk.Label(bar, text="", bg=CARD, fg=ACCENT, font=("Microsoft YaHei UI", 9, "bold"))
        self.lbl_stage.pack(side="right")
        self.pb = ttk.Progressbar(f3, mode="determinate", style="Bar.Horizontal.TProgressbar")
        self.pb.pack(fill="x", pady=(8, 0))
        self.lbl_sum = tk.Label(f3, text="复检会实测采集源搜索、直播流、解析器；spider 站点仅做 jar 类校验。",
                                bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8), anchor="w")
        self.lbl_sum.pack(fill="x", pady=(4, 0))

        # --- 结果 + 日志 ---
        pane = tk.Frame(body, bg=BG)
        pane.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        pane.columnconfigure(0, weight=3)
        pane.columnconfigure(1, weight=2)
        pane.rowconfigure(1, weight=1)

        tk.Label(pane, text="复检结果", bg=BG, fg=TEXT, font=FONT_H).grid(row=0, column=0, sticky="w")
        tk.Label(pane, text="运行日志", bg=BG, fg=TEXT, font=FONT_H).grid(row=0, column=1, sticky="w", padx=(12, 0))

        wrap = tk.Frame(pane, bg=BORDER)
        wrap.grid(row=1, column=0, sticky="nsew", pady=(6, 0), padx=(0, 12))
        tv = ttk.Treeview(wrap, columns=("cat", "name", "state", "ms", "detail"),
                          show="headings", selectmode="browse")
        for c, txt, w, anchor in (("cat", "类别", 58, "center"), ("name", "名称", 150, "w"),
                                  ("state", "状态", 54, "center"), ("ms", "耗时", 52, "center"),
                                  ("detail", "说明", 320, "w")):
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor=anchor, stretch=(c == "detail"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        vs.pack(side="right", fill="y", pady=1, padx=(0, 1))
        tv.tag_configure("ok", foreground=OK)
        tv.tag_configure("dead", foreground=ERR)
        tv.tag_configure("warn", foreground=WARN)
        self.tv = tv

        lw = tk.Frame(pane, bg=BORDER)
        lw.grid(row=1, column=1, sticky="nsew", pady=(6, 0))
        self.log = tk.Text(lw, bg="#12161f", fg="#c8d0de", bd=0, font=("Consolas", 9),
                           wrap="word", padx=10, pady=8, state="disabled")
        ls = ttk.Scrollbar(lw, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=ls.set)
        self.log.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        ls.pack(side="right", fill="y", pady=1, padx=(0, 1))
        self.log.tag_configure("ok", foreground="#5ee08a")
        self.log.tag_configure("err", foreground="#ff7b7b")
        self.log.tag_configure("warn", foreground="#f0c060")
        self.log.tag_configure("muted", foreground="#7b8497")
        self.log.tag_configure("info", foreground="#c8d0de")

        self.set_root_label()
        self.log_line("%s v%s 就绪" % (APP_NAME, APP_VER), "info")

    # ---------- 顶栏/目录 ----------
    def set_root_label(self):
        if self.root_dir:
            self.lbl_root.config(text="工作目录：" + self.root_dir)
        else:
            self.lbl_root.config(text="未找到配置目录，请点右侧「更改目录」")

    def change_root(self):
        from tkinter import filedialog, messagebox
        init = self.root_dir or os.path.expanduser("~")
        p = filedialog.askdirectory(title="选择 TVBox 项目目录（需包含 output 文件夹）", initialdir=init)
        if not p:
            return
        if not os.path.isdir(os.path.join(p, "output")):
            if messagebox.askyesno("确认", "该目录下没有 output 文件夹，仍然使用？"):
                pass
            else:
                return
        self.root_dir = p
        self.settings["root"] = p
        save_settings(self.settings)
        self.set_root_label()
        self.refresh_cfg_list()
        self.build_addrs()
        self.log_line("工作目录切换为 %s" % p, "info")

    def open_dir(self):
        if not self.root_dir:
            return
        p = os.path.join(self.root_dir, "output")
        try:
            os.startfile(p)
        except Exception:
            webbrowser.open("file:///" + p.replace("\\", "/"))

    # ---------- 配置列表 ----------
    def cfg_files(self):
        if not self.root_dir:
            return []
        out = os.path.join(self.root_dir, "output")
        try:
            names = [x for x in os.listdir(out) if x.endswith(".json")]
        except Exception:
            return []
        priority = ["tvbox-osc-fixed.json", "tvbox-config.json", "tvbox-lan.json", "tvbox-online.json"]
        names.sort(key=lambda x: (priority.index(x) if x in priority else 99, x))
        return names

    def refresh_cfg_list(self):
        files = self.cfg_files()
        self.cb_cfg["values"] = files
        if self.cfg_name not in files:
            self.cfg_name = files[0] if files else ""
        self.var_cfg.set(self.cfg_name)
        self.on_cfg_pick()

    def on_cfg_pick(self):
        self.cfg_name = self.var_cfg.get()
        self.settings["cfg"] = self.cfg_name
        save_settings(self.settings)
        info = "—"
        if self.cfg_name and self.root_dir:
            p = os.path.join(self.root_dir, "output", self.cfg_name)
            try:
                d = json.load(open(p, encoding="utf-8"))
                sites = d.get("sites") or []
                c = collections.Counter(s.get("type") for s in sites)
                info = "站点 %d（采集 %d / spider %d）\n直播入口 %d · 解析 %d\n体积 %.0f KB" % (
                    len(sites), c.get(1, 0) + c.get(0, 0), c.get(3, 0) + c.get(4, 0),
                    len(d.get("lives") or []), len(d.get("parses") or []),
                    os.path.getsize(p) / 1024.0)
            except Exception as e:
                info = "读取失败：%s" % e
        self.lbl_cfg_info.config(text=info)
        self.build_addrs()

    # ---------- 地址区 ----------
    def build_addrs(self):
        tk = self.tk
        for w in self.addr_box.winfo_children():
            w.destroy()
        if not self.root_dir:
            tk.Label(self.addr_box, text="未设置工作目录", bg="#f7f9fc", fg=MUTED).pack(pady=10)
            return
        port = self.current_port()
        ips = lan_ips()
        title = tk.Frame(self.addr_box, bg="#f7f9fc")
        title.pack(fill="x", padx=12, pady=(10, 2))
        running = self.server is not None and self.server.alive()
        tk.Label(title, text="电视/盒子填写以下任一地址", bg="#f7f9fc", fg=MUTED,
                 font=("Microsoft YaHei UI", 8, "bold")).pack(side="left")
        tk.Label(title, text="服务运行中" if running else "服务未启动（地址仅作预览）", bg="#f7f9fc",
                 fg=OK if running else WARN, font=("Microsoft YaHei UI", 8)).pack(side="right")

        if not ips:
            ips = ["<本机IP>"]
        for ip in ips:
            line = tk.Frame(self.addr_box, bg="#f7f9fc")
            line.pack(fill="x", padx=12, pady=3)
            url = "http://%s:%d/%s" % (ip, port, self.cfg_name or "tvbox-config.json")
            ent = tk.Entry(line, bg="#ffffff", fg=TEXT, relief="solid", bd=1,
                           font=("Consolas", 10), readonlybackground="#ffffff")
            ent.insert(0, url)
            ent.configure(state="readonly")
            ent.pack(side="left", fill="x", expand=True, ipady=5)
            self._mkbtn(line, "复制", lambda u=url: self.copy(u)).pack(side="left", padx=(6, 0))
            self._mkbtn(line, "打开", lambda u=url: webbrowser.open(u)).pack(side="left", padx=(6, 0))

        tip = tk.Frame(self.addr_box, bg="#f7f9fc")
        tip.pack(fill="x", padx=12, pady=(4, 10))
        tk.Label(tip, text="提示：电脑与盒子需在同一 WiFi；首次启动若弹出防火墙提示，请勾选「专用网络」并允许。",
                 bg="#f7f9fc", fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left")

    def _mkbtn(self, parent, text, cmd):
        tk = self.tk
        b = tk.Label(parent, text=text, bg="#eef2f8", fg=ACCENT, font=("Microsoft YaHei UI", 8, "bold"),
                     padx=10, pady=5, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg="#e0e8f6"))
        b.bind("<Leave>", lambda e: b.configure(bg="#eef2f8"))
        return b

    def copy(self, text):
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.log_line("已复制：%s" % text, "muted")

    # ---------- 服务 ----------
    def current_port(self):
        try:
            return int(self.var_port.get())
        except Exception:
            return DEFAULT_PORT

    def _port_changed(self):
        self.settings["port"] = self.current_port()
        save_settings(self.settings)
        self.build_addrs()

    def toggle_server(self):
        if self.server and self.server.alive():
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        if not self.root_dir:
            self.log_line("尚未设置工作目录", "err")
            return
        port = self.current_port()
        if port_in_use(port):
            self.log_line("端口 %d 已被占用（可能是之前遗留的 serve.py 或本程序已开着一个实例）" % port, "err")
            self.log_line("处理：换一个端口，或在任务管理器里结束旧的 python/TVBoxConfigTool 进程后重试。", "warn")
            return
        out = os.path.join(self.root_dir, "output")
        srv = ConfigServer(out, port,
                           on_req=lambda p, m: self.msgq.put(("req", p, m)))
        try:
            srv.start()
        except OSError as e:
            self.log_line("端口 %d 启动失败：%s" % (port, e), "err")
            self.log_line("可能被占用，换一个端口再试；或先关闭其它 serve.py 进程。", "warn")
            return
        self.server = srv
        self.settings["port"] = port
        save_settings(self.settings)
        ips = lan_ips()
        self.chip.config(text="● 服务运行中 · 端口 %d" % port, fg="#6fe39a")
        self.btn_srv.config(text="停止服务", style="Danger.TButton")
        self.log_line("服务已启动，根目录 %s" % out, "ok")
        for ip in ips or ["127.0.0.1"]:
            self.log_line("  配置地址 http://%s:%d/%s" % (ip, port, self.cfg_name), "ok")
        self.build_addrs()

    def stop_server(self):
        if self.server:
            self.server.stop()
        self.server = None
        self.chip.config(text="● 服务已停止", fg="#f08a8a")
        self.btn_srv.config(text="启动服务", style="Accent.TButton")
        self.log_line("服务已停止", "warn")
        self.build_addrs()

    def _autostart(self):
        if self.var_auto.get():
            self.start_server()

    # ---------- 复检 ----------
    def start_check(self):
        if self._busy_verify:
            return
        if not self.cfg_name:
            self.log_line("没有可复检的配置文件", "err")
            return
        for i in self.tv.get_children():
            self.tv.delete(i)
        self.rows_by_iid.clear()
        self.report = None
        self.btn_apply.config(state="disabled")
        self.btn_report.config(state="disabled")
        self.btn_check.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.pb["value"] = 0
        self.pb["maximum"] = 100
        self._busy_verify = True
        self.stop_event.clear()
        self.log_line("开始复检 %s" % self.cfg_name, "info")

        emit = lambda *a: self.msgq.put(a)
        self.checker = Checker(self.root_dir, self.cfg_name, emit, self.stop_event,
                               quick=bool(self.quick_run))
        self.quick_run = False
        threading.Thread(target=self._run_checker, args=(self.checker,), daemon=True).start()

    def _run_checker(self, chk):
        try:
            chk.run()
        except Exception as e:
            import traceback
            self.msgq.put(("log", "复检异常：%s" % e, "err"))
            self.msgq.put(("log", traceback.format_exc()[-600:], "err"))
            self.msgq.put(("finished", None, chk.cfg_name, True))

    def stop_check(self):
        self.stop_event.set()
        self.log_line("正在停止复检…", "warn")

    def apply_results(self):
        if not self.report:
            return
        from tkinter import messagebox
        dead = [r for r in self.report.rows if r["drop"]]
        if not dead:
            messagebox.showinfo("无需更新", "本次复检没有需要剔除的条目。")
            return
        if not messagebox.askyesno("确认更新",
                                   "将剔除 %d 个失效条目并写回 %s。\n原文件会自动备份，可随时回滚。继续？"
                                   % (len(dead), self.cfg_name)):
            return
        try:
            info = apply_report(self.root_dir, self.cfg_name, self.report, self.log_line)
        except Exception as e:
            self.log_line("写入失败：%s" % e, "err")
            return
        self.btn_apply.config(state="disabled")
        self.refresh_cfg_list()
        messagebox.showinfo("已更新",
                            "配置已更新。\n\n站点 %d → %d\n直播源 %d → %d\n解析 %d → %d\n\n备份：%s\n\n请在 TVBox 中「清空缓存」后重新加载配置。"
                            % (info["before"][0], info["after"][0], info["before"][1], info["after"][1],
                               info["before"][2], info["after"][2], info["backup"]))

    def export_report(self):
        if not self.report:
            return
        from tkinter import filedialog
        p = filedialog.asksaveasfilename(defaultextension=".html", initialfile="复检报告.html",
                                         filetypes=[("HTML", "*.html"), ("JSON", "*.json")])
        if not p:
            return
        if p.lower().endswith(".json"):
            json.dump(dict(started=self.report.started, rows=self.report.rows),
                      open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        else:
            open(p, "w", encoding="utf-8").write(report_html(self.report, self.cfg_name))
        self.log_line("报告已导出：%s" % p, "ok")

    # ---------- 消息泵 ----------
    def _drain(self):
        try:
            while True:
                m = self.msgq.get_nowait()
                self._handle(m)
        except queue.Empty:
            pass
        self.win.after(100, self._drain)

    def _handle(self, m):
        kind = m[0]
        if kind == "log":
            self.log_line(m[1], m[2] if len(m) > 2 else "info")
        elif kind == "stage":
            self.lbl_stage.config(text=m[1] + "…")
        elif kind == "progress":
            done, total, note = m[1], m[2], m[3]
            self.pb["maximum"] = max(total, 1)
            self.pb["value"] = min(done, total)
            if note:
                self.lbl_sum.config(text="进度 %d/%d · %s" % (done, total, note))
        elif kind == "row":
            self.add_row(m[1])
        elif kind == "finished":
            self.on_finished(m[1], m[2], m[3])
        elif kind == "req":
            self.log_line("  [请求] %s %s" % (m[2], m[1]), "muted")

    def add_row(self, r):
        icon = {"ok": "✓ 正常", "dead": "✗ 失效", "warn": "! 提示"}.get(r["state"], r["state"])
        iid = self.tv.insert("", "end", values=(r["cat"], r["name"], icon,
                                                ("%d ms" % r["ms"]) if r["ms"] else "—",
                                                r["detail"]), tags=(r["state"],))
        self.rows_by_iid[iid] = r
        self.tv.see(iid)

    def on_finished(self, rep, cfg_name, stopped):
        self._busy_verify = False
        self.btn_check.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_stage.config(text="已停止" if stopped else "复检完成")
        if rep is None:
            self.log_line("复检中断，未产生结果", "err")
            return
        self.report = rep
        st = rep.stats()
        summary = "正常 %d · 失效 %d · 提示 %d" % (st.get("ok", 0), st.get("dead", 0), st.get("warn", 0))
        self.lbl_sum.config(text=("%s ｜ " % summary) + ("已停止" if stopped else "全部完成"))
        self.log_line("复检结束：%s" % summary, "ok" if st.get("dead", 0) == 0 else "warn")
        self.btn_report.config(state="normal")
        if st.get("dead", 0) and not stopped:
            self.btn_apply.config(state="normal")
            self.log_line("有 %d 个失效条目，可点「应用结果并更新配置」剔除" % st.get("dead", 0), "warn")

    # ---------- 日志 ----------
    def log_line(self, text, tag="info"):
        t = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", "[%s] " % t, "muted")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- 关闭 ----------
    def on_close(self):
        if self._busy_verify:
            self.stop_event.set()
        if self.server:
            self.server.stop()
        try:
            self.win.destroy()
        except Exception:
            pass

    def run(self):
        self.win.mainloop()


# =====================================================================
# 报告
# =====================================================================
def report_html(rep, cfg_name):
    st = rep.stats()
    rows = []
    for r in rep.rows:
        color = {"ok": OK, "dead": ERR, "warn": WARN}.get(r["state"], MUTED)
        label = {"ok": "正常", "dead": "失效", "warn": "提示"}.get(r["state"], r["state"])
        rows.append("<tr><td>%s</td><td>%s</td><td style='color:%s;font-weight:600'>%s</td>"
                    "<td>%s</td><td class='d'>%s</td></tr>"
                    % (r["cat"], r["name"], color, label, ("%d" % r["ms"]) if r["ms"] else "—",
                       r["detail"]))
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>复检报告</title><style>
body{font:14px/1.7 "Microsoft YaHei UI",sans-serif;background:#f4f6f9;color:#1f2430;margin:0;padding:32px}
.wrap{max-width:980px;margin:0 auto;background:#fff;border:1px solid #e2e6ee;border-radius:10px;padding:28px 30px}
h1{font-size:20px;margin:0 0 6px}.sub{color:#77808f;font-size:12px;margin-bottom:20px}
.kpi{display:flex;gap:12px;margin:18px 0 22px}
.kpi div{flex:1;background:#f7f9fc;border:1px solid #e8ecf3;border-radius:8px;padding:12px 14px}
.kpi b{display:block;font-size:22px}.kpi span{color:#77808f;font-size:12px}
table{width:100%%;border-collapse:collapse;font-size:13px}
th{text-align:left;background:#f1f4f9;color:#77808f;font-weight:600;padding:8px 10px;font-size:12px}
td{padding:7px 10px;border-bottom:1px solid #f0f2f6;vertical-align:top}
td.d{color:#5c6675;font-size:12px}.foot{margin-top:18px;color:#9aa3b2;font-size:11px}
</style></head><body><div class="wrap">
<h1>TVBox 配置复检报告</h1>
<div class="sub">配置：%s ｜ 开始时间：%s</div>
<div class="kpi">
<div><b style="color:%s">%d</b><span>正常</span></div>
<div><b style="color:%s">%d</b><span>失效</span></div>
<div><b style="color:%s">%d</b><span>提示</span></div>
<div><b>%d</b><span>总检查项</span></div>
</div>
<table><thead><tr><th>类别</th><th>名称</th><th>状态</th><th>耗时(ms)</th><th>说明</th></tr></thead>
<tbody>%s</tbody></table>
<div class="foot">由 TVBox 配置工具生成 · 复检在本机实测，结果仅代表本次网络环境</div>
</div></body></html>""" % (cfg_name, rep.started, OK, st.get("ok", 0), ERR, st.get("dead", 0),
                         WARN, st.get("warn", 0), len(rep.rows), "".join(rows))


# =====================================================================
# 入口
# =====================================================================
def main():
    args = sys.argv[1:]
    if "--cli" in args:
        # 命令行自检模式：不弹窗，便于脚本或排查使用
        root = detect_root() or "."
        cfgs = [x for x in os.listdir(os.path.join(root, "output")) if x.endswith(".json")]
        quick = "--quick" in args
        name = "tvbox-osc-fixed.json" if "tvbox-osc-fixed.json" in cfgs else (cfgs[0] if cfgs else "")
        name = next((a for a in args if a.endswith(".json")), name)
        print("工作目录:", root, "| 配置:", name, "| quick:", quick)
        chk = Checker(root, name, lambda *a: None, quick=quick)
        rep = chk.run()
        for r in rep.rows:
            print("%-4s %-6s %-22s %s" % ({"ok": "OK", "dead": "BAD", "warn": "WARN"}[r["state"]],
                                          r["cat"], str(r["name"])[:22], r["detail"][:70]))
        print(dict(rep.stats()))
        return 0

    app = App()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
