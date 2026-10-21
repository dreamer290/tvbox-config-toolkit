import json, sys, collections, re

# 按 TVBox / FongMi 的 Java 模型做静态体检：
# 任何一项类型不符，Gson 都会抛 JsonSyntaxException，客户端只会显示「配置失败」。

SITE_STR = {"key", "name", "api", "ext", "playUrl", "playurl", "jar", "click", "detail",
            "playerType", "categories", "indexs", "timeout", "type"}
SITE_INT = {"type", "searchable", "filterable", "quickSearch", "changeable", "playerType", "timeout"}
SITE_OBJ = {"style", "header"}

errs = []


def chk_str(obj, f, where):
    if f in obj and not isinstance(obj[f], str):
        errs.append("%s 的 %s 应为字符串，实际 %s" % (where, f, type(obj[f]).__name__))


def chk_int(obj, f, where):
    if f in obj and not isinstance(obj[f], int):
        errs.append("%s 的 %s 应为数字，实际 %s" % (where, f, type(obj[f]).__name__))


def check(path):
    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        errs.append("文件带 UTF-8 BOM，部分机型解析失败")
    if b"\x00" in raw:
        errs.append("文件含 NUL 字节")
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception as e:
        errs.append("JSON 解析失败: %s" % e)
        return

    sp = d.get("spider")
    if sp is not None:
        if not isinstance(sp, str):
            errs.append("spider 必须是字符串")
        elif ";md5;" in sp:
            u, _, m = sp.partition(";md5;")
            if not re.fullmatch(r"[0-9a-fA-F]{32}", m.strip()):
                errs.append("spider 的 md5 段不是 32 位十六进制: %r" % m[:40])

    sites = d.get("sites")
    if not isinstance(sites, list) or not sites:
        errs.append("sites 缺失或为空")
        return
    keys = []
    for i, s in enumerate(sites):
        w = "站点[%d]%s" % (i, s.get("name", "")[:12])
        for f in ("key", "name", "api"):
            if f not in s:
                errs.append("%s 缺少必需字段 %s" % (w, f))
            elif not isinstance(s[f], str):
                errs.append("%s 的 %s 应为字符串，实际 %s" % (w, f, type(s[f]).__name__))
        if "type" not in s:
            errs.append("%s 缺少 type" % w)
        elif not isinstance(s["type"], int):
            errs.append("%s 的 type 应为数字，实际 %r" % (w, s["type"]))
        elif s["type"] not in (0, 1, 3, 4):
            errs.append("%s 的 type=%r 不在 0/1/3/4 范围" % (w, s["type"]))
        for f in ("ext", "jar", "playUrl", "playurl"):
            chk_str(s, f, w)
        for f in ("searchable", "filterable", "quickSearch", "changeable", "timeout", "playerType"):
            chk_int(s, f, w)
        for f in ("style", "header"):
            if f in s and not isinstance(s[f], dict):
                errs.append("%s 的 %s 应为对象" % (w, f))
        if s.get("type") in (3, 4):
            api = str(s.get("api", ""))
            if not api.startswith("csp_"):
                errs.append("%s 是 spider 站但 api 不是 csp_*: %s" % (w, api[:40]))
        keys.append(s.get("key"))

    dup = [k for k, c in collections.Counter(keys).items() if c > 1]
    if dup:
        errs.append("重复的站点 key: %s" % dup[:6])
    if any(not k for k in keys):
        errs.append("存在空 key 站点")

    for i, l in enumerate(d.get("lives") or []):
        w = "直播[%d]" % i
        for f in ("name", "url"):
            if f not in l:
                errs.append("%s 缺少 %s" % (w, f))
            else:
                chk_str(l, f, w)
        chk_int(l, "type", w)

    for i, p in enumerate(d.get("parses") or []):
        w = "解析[%d]" % i
        for f in ("name", "url"):
            if f not in p:
                errs.append("%s 缺少 %s" % (w, f))
            else:
                chk_str(p, f, w)
        chk_int(p, "type", w)

    # 相对路径引用检查：本地文件是否真的存在
    base = path.rsplit("\\", 1)[0].rsplit("/", 1)[0]
    import os
    for f in (sp or "").split(";")[0], *[l.get("url") for l in (d.get("lives") or [])]:
        if isinstance(f, str) and f.startswith("./"):
            fn = os.path.join(base, f[2:])
            if not os.path.exists(fn):
                errs.append("相对路径指向的文件不存在: %s" % f)


for p in sys.argv[1:]:
    print("=== 体检:", p)
    errs.clear()
    check(p)
    if not errs:
        print("  ✅ 通过：结构、类型、引用全部符合 TVBox 模型")
    else:
        print("  ❌ 发现 %d 处问题" % len(errs))
        for e in errs[:20]:
            print("   -", e)
