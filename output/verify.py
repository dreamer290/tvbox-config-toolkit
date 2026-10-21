#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 配置自检：只用标准库，直接对当前目录的 tvbox-config.json / live.txt 复验。

用法：python verify.py
输出：每个点播源、直播频道、解析的当前可用状态。
"""
import json
import re
import ssl
import sys
import time
import hashlib
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = "okhttp/3.15"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, timeout=12, limit=600000, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return True, r.read(limit), r.headers.get("Content-Type", ""), int((time.time() - t0) * 1000), ""
    except urllib.error.HTTPError as e:
        return False, b"", "", int((time.time() - t0) * 1000), "HTTP %d" % e.code
    except Exception as e:
        return False, b"", "", int((time.time() - t0) * 1000), type(e).__name__


def check_site(s):
    """三段回退：ac=detail&wd → ac=videolist&wd（后两次带浏览器 UA，绕过部分 WAF）"""
    base = s.get("api", "").split("?")[0]
    word = urllib.parse.quote("庆余年")
    brow = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    tries = [base + "?ac=detail&wd=" + word,
             base + "?ac=videolist&wd=" + word,
             base + "?ac=detail&pg=1"]
    last = "未知"
    for i, u in enumerate(tries):
        ok, data, _, ms, err = get(u, 12, headers=(brow if i else None))
        if not ok:
            last = err
            continue
        t = data.decode("utf-8", "ignore")
        if s.get("type") == 1:
            try:
                j = json.loads(t, strict=False)
            except Exception:
                last = "非 JSON"
                continue
            lst = j.get("list") or []
            if lst and lst[0].get("vod_play_url"):
                return True, ms, ""
            last = "无片源"
        else:
            n = len(re.findall(r"<video", t, re.I))
            if n:
                return True, ms, ""
            last = "无 video"
    return False, 0, last


def check_chan(url):
    ok, data, _, _, err = get(url, 8, 60000)
    if not ok:
        return False, err
    txt = data.decode("utf-8", "ignore")
    if "#EXTM3U" not in txt:
        return False, "非 m3u8"
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    base = url
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            base = urllib.parse.urljoin(url, lines[i + 1])
            ok, data, _, _, err = get(base, 8, 60000)
            if not ok:
                return False, "子列表失败"
            txt = data.decode("utf-8", "ignore")
            lines = [l.strip() for l in txt.splitlines() if l.strip()]
            break
    seg = next((l for l in lines if l and not l.startswith("#")), None)
    if not seg:
        return False, "无分片"
    ok, data, _, _, err = get(urllib.parse.urljoin(base, seg), 10, 200000,
                              {"Range": "bytes=0-60000"})
    return (ok and len(data) > 800), ("%d B" % len(data) if ok else err)


def lint(cfg):
    """静态体检：TVBox 的 Site.ext 是字符串字段，对象形式会让客户端直接「配置失败」。"""
    probs = []
    for i, s in enumerate(cfg.get("sites", [])):
        nm = str(s.get("name", ""))[:14]
        if not isinstance(s.get("ext"), (str, type(None))):
            probs.append("站点「%s」的 ext 是 %s，必须是字符串" % (nm, type(s.get("ext")).__name__))
        if not isinstance(s.get("type"), int):
            probs.append("站点「%s」的 type 不是数字" % nm)
        if s.get("type") in (3, 4) and not str(s.get("api", "")).startswith("csp_"):
            probs.append("站点「%s」是 spider 类型但 api 不是 csp_*" % nm)
        if isinstance(s.get("api"), str) and s["api"].startswith(("./", "../")):
            probs.append("站点「%s」的 api 是相对路径，拷到本地会 404" % nm)
    for l in cfg.get("lives", []):
        if not isinstance(l.get("type"), int):
            probs.append("直播「%s」的 type 不是数字" % str(l.get("name"))[:14])
    if probs:
        print("结构体检: ❌ 发现 %d 处会导致「配置失败」的问题" % len(probs))
        for p in probs[:10]:
            print("   -", p)
    else:
        print("结构体检: ✅ 通过（字段类型符合 TVBox 模型，不会在解析阶段失败）")
    return not probs


def main():
    cfg = json.load(open("tvbox-config.json", encoding="utf-8"))
    print("=" * 62)
    print("配置自检  %s" % time.strftime("%Y-%m-%d %H:%M"))
    lint(cfg)

    sp = cfg.get("spider", "")
    if ";md5;" in sp:
        u, md5 = sp.split(";md5;")
        if u.startswith("./"):
            print("spider  : 本地文件 %s（跳过下载校验）" % u)
        else:
            ok, data, _, ms, err = get(u, 30, 12000000)
            got = hashlib.md5(data).hexdigest() if ok else ""
            print("spider  : %s  md5 %s  %dms" % ("OK " if got == md5 else "FAIL",
                                                 "匹配" if got == md5 else "不匹配", ms))

    sites = [s for s in cfg.get("sites", []) if s.get("type") in (0, 1)]
    spider_n = sum(1 for s in cfg.get("sites", []) if s.get("type") in (3, 4))
    print("-" * 62)
    print("点播采集源 %d 个（另有 %d 个 spider 站点需在播放器内运行，无法在此验证）" % (len(sites), spider_n))
    with ThreadPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(check_site, sites))
    good = sum(1 for r in res if r[0])
    for s, r in zip(sites, res):
        print("  %s %-22s %s" % ("✅" if r[0] else "❌", s.get("name", "")[:22],
                                 ("%dms" % r[1]) if r[0] else r[2]))
    print("  → 可用 %d / %d" % (good, len(sites)))

    print("-" * 62)
    chans = []
    try:
        for ln in open("live.txt", encoding="utf-8"):
            ln = ln.strip()
            if ln and "#genre#" not in ln and "," in ln:
                nm, _, u = ln.partition(",")
                chans.append((nm.strip(), u.strip()))
    except FileNotFoundError:
        print("未找到 live.txt")
    if chans:
        with ThreadPoolExecutor(max_workers=16) as ex:
            lr = list(ex.map(lambda c: check_chan(c[1]), chans))
        lg = sum(1 for r in lr if r[0])
        for (nm, u), r in zip(chans, lr):
            if not r[0]:
                print("  ❌ %-16s %s" % (nm, r[1]))
        print("直播频道 %d 个 → 分片可下载 %d 个" % (len(chans), lg))

    print("-" * 62)
    vid = "https://vod2.maowushi.com/20240516/zN7aNzzK/index.m3u8"
    pg = 0
    for p in cfg.get("parses", []):
        ok, data, _, ms, err = get(p["url"] + urllib.parse.quote(vid, safe=""), 12, 150000)
        hit = ok and "m3u8" in data.decode("utf-8", "ignore")
        pg += 1 if hit else 0
        print("  %s %-16s %s" % ("✅" if hit else "❌", p.get("name", "")[:16], "" if hit else "未返回直链"))
    print("解析 %d 个 → 可用 %d 个" % (len(cfg.get("parses", [])), pg))
    print("=" * 62)
    print("提示：接口时效性很短，失效属正常现象，建议隔一周重跑本脚本再更新配置。")


if __name__ == "__main__":
    sys.exit(main())
