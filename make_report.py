import json, html, collections, datetime

cfg = json.load(open("output/tvbox-config.json", encoding="utf-8"))
rep = json.load(open("report_final.json", encoding="utf-8"))
cms_all = json.load(open("cms_test.json", encoding="utf-8"))
live_all = json.load(open("live_test.json", encoding="utf-8"))
par_all = json.load(open("parse_test.json", encoding="utf-8"))
jar_all = json.load(open("jar_test.json", encoding="utf-8"))
fm = json.load(open("fetch_meta.json", encoding="utf-8"))

cms_ok = [x for x in rep["cms"] if x["ok"]]
live_ch = rep["live_channels"]
live_src = rep["live_sources"]

NAMES = {
    "wangerxiao": "王二小", "junge": "俊哥", "xiaohezi": "小盒子", "xiaohezi4k": "小盒子4K",
    "eryuehong": "二月红", "fantaiying": "饭太硬", "xiaosa": "潇洒", "niuer2": "牛二",
    "ok1_liucn": "ok线路1/liu673cn", "jianpian": "荐片", "myinterface": "my interface",
    "vip1": "1号VIP源", "builtin": "软件内置源", "ok2_gist": "ok线路2", "hao11_jsdelivr": "11号托管源",
    "niuer": "牛儿", "shiguang": "拾光", "shenmi": "神秘大佬", "xiaosa_gh": "潇洒GitHub备用",
    "jinying": "金鹰", "feimao": "肥猫", "szyyds": "小马", "qist_api_jsdelivr": "qist/api.json",
    "fanqie": "番茄", "test": "测试",
}

def esc(s):
    return html.escape(str(s or ""))

rows_src = []
for m in fm:
    if m["name"] in ("test",):
        continue
    ok = m["ok"]
    rows_src.append(
        "<tr><td>%s</td><td class='mono'>%s</td><td>%s</td><td class='%s'>%s</td></tr>" % (
            esc(NAMES.get(m["name"], m["name"])), esc(m["url"][:78]),
            ("%.1f KB" % (m["size"] / 1024)) if ok else "—",
            "ok" if ok else "bad", "成功" if ok else (esc(m["err"][:30]) or "失败")))

rows_cms = []
for x in sorted(cms_ok, key=lambda v: v.get("ms", 9999)):
    rows_cms.append(
        "<tr><td>%s</td><td class='mono'>%s</td><td>%s ms</td><td>%s</td></tr>" % (
            esc(x["name"]), esc(x["key"][:34]), x.get("ms", "-"),
            esc(x.get("sample") or ("XML 源 %s 条" % x.get("n", "")))))

rows_ch = []
group = ""
for c in live_ch:
    rows_ch.append(
        "<tr><td>%s</td><td class='mono'>%s</td><td class='ok'>%s</td></tr>" % (
            esc(c["name"]), esc(c["url"][:72]), esc(c["info"])))

rows_ls = []
for x in live_src:
    rows_ls.append("<tr><td>%s</td><td class='%s'>%s</td></tr>" % (
        esc(x["name"]), "ok" if x["ok"] else "bad", esc(x["info"])))

def bar(items, total, color="#2f6fed"):
    out = []
    for k, v in items:
        pct = v / total * 100
        out.append(
            "<div class='bar'><span class='bl'>%s</span>"
            "<span class='bt'><i style='width:%.0f%%;background:%s'></i></span>"
            "<span class='bv'>%d</span></div>" % (esc(k), max(pct, 2), color, v))
    return "".join(out)

cms_fail = collections.Counter()
for x in cms_all:
    if x["ok"]:
        continue
    e = x.get("err", "")
    if "Errno 11001" in e:
        cms_fail["DNS 解析失败/污染"] += 1
    elif "timed out" in e:
        cms_fail["连接超时"] += 1
    elif "HTTP 4" in e or "HTTP 5" in e:
        cms_fail["HTTP 4xx/5xx"] += 1
    elif "非JSON" in e:
        cms_fail["返回 HTML 拦截页"] += 1
    elif "WinError" in e:
        cms_fail["连接被重置"] += 1
    else:
        cms_fail["其他"] += 1

live_fail = collections.Counter()
for x in live_all:
    if x["ok"]:
        continue
    e = x.get("err", "")
    if "HTTP 404" in e:
        live_fail["源地址 404 已下线"] += 1
    elif "URLError" in e:
        live_fail["域名失效/无法解析"] += 1
    elif "无频道" in e:
        live_fail["格式不识别"] += 1
    elif "抽样" in e:
        live_fail["频道全部失效"] += 1
    elif "HTTP 4" in e or "HTTP 5" in e:
        live_fail["HTTP 4xx/5xx"] += 1
    else:
        live_fail["其他"] += 1

par_fail = collections.Counter()
for x in par_all:
    if x["ok"]:
        continue
    e = x.get("err", "")
    if "timed out" in e:
        par_fail["接口超时"] += 1
    elif "Errno 11001" in e:
        par_fail["域名失效"] += 1
    elif "WinError" in e:
        par_fail["端口已关闭"] += 1
    elif "HTTP 4" in e or "HTTP 5" in e:
        par_fail["HTTP 4xx/5xx"] += 1
    elif "无m3u8" in e:
        par_fail["需 WebView 未返回直链"] += 1
    else:
        par_fail["其他"] += 1

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

html_doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TVBox 资源验证报告</title>
<style>
:root{--bg:#f7f8fa;--card:#fff;--line:#e6e8ec;--tx:#1c1f23;--tx2:#5b626c;--tx3:#8a919c;
--ok:#0f8a52;--okbg:#e8f6ee;--bad:#c0362c;--badbg:#fdecea;--acc:#2f6fed;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:1060px;margin:0 auto;padding:40px 24px 72px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.3px}
.sub{color:var(--tx2);font-size:13.5px;margin-bottom:26px}
h2{font-size:17.5px;margin:38px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:22px 0 10px;color:var(--tx)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:8px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi b{display:block;font-size:22px;letter-spacing:-.5px}
.kpi span{color:var(--tx2);font-size:12.5px}
.kpi em{font-style:normal;color:var(--ok);font-size:12.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 18px 18px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--tx3);font-weight:500;padding:9px 8px;border-bottom:1px solid var(--line);font-size:12.5px}
td{padding:7px 8px;border-bottom:1px solid #f1f2f4;vertical-align:top}
tr:last-child td{border-bottom:0}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:var(--tx2);word-break:break-all}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11.5px;margin-right:4px}
.t-ok{background:var(--okbg);color:var(--ok)}.t-bad{background:var(--badbg);color:var(--bad)}
.bar{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.bl{width:190px;color:var(--tx2);flex:none}
.bt{flex:1;height:8px;background:#eef0f3;border-radius:4px;overflow:hidden}
.bt i{display:block;height:100%;border-radius:4px}
.bv{width:28px;text-align:right;color:var(--tx2);flex:none}
.note{background:#fffdf5;border:1px solid #f2e6c7;border-radius:9px;padding:12px 16px;font-size:13.5px;color:#6b5620}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
ul{margin:8px 0;padding-left:20px}
li{margin:4px 0}
code{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:12.5px;font-family:ui-monospace,Consolas,monospace}
@media(max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}.bl{width:120px}}
</style></head><body><div class="wrap">

<h1>TVBox 点播 / 直播资源验证报告</h1>
<div class="sub">验证时间 {{NOW}} ｜ 全部结论来自本机实测（Windows / 家庭宽带），非第三方播报 ｜ 共检查 21 个配置、1888 个站点、49 个直播源、78 个解析；成品配置 146 个站点、7 个直播入口、8 个解析</div>

<div class="kpis">
  <div class="kpi"><span>配置文件</span><b>21 / 24</b><em>87.5% 可下载解析</em></div>
  <div class="kpi"><span>点播采集源</span><b>39 / 73</b><em>真实搜索返回片源</em></div>
  <div class="kpi"><span>直播源</span><b>18 / 49</b><em>频道解析 + 抽样可播</em></div>
  <div class="kpi"><span>精选频道</span><b>53 台</b><em>逐台验证到分片</em></div>
</div>

<h2>零、安装「配置失败」的根因与修复</h2>
<div class="card">
<p style="color:#5b626c;font-size:13.5px;margin:6px 0 12px">初版配置在客户端加载时报「配置失败」，逐项复现后定位到两个自身缺陷和一个操作误区，均已修复。</p>
<table>
<tr><th style="width:22%">问题</th><th style="width:34%">为什么会导致失败</th><th>修复方式</th></tr>
<tr><td><b>42 个站点的 <code>ext</code> 写成了 JSON 对象</b><br><span style="color:#d93025">致命</span></td>
<td>TVBox 的 <code>Site.ext</code> 是 <b>String</b> 字段。Gson 反序列化 <code>{"host":"..."}</code> 到 String 时抛
<code>Expected a string but was BEGIN_OBJECT</code>，解析在第一个这样的站点处中断，整个配置被判为无效。</td>
<td>全部压成紧凑 JSON 字符串（<code>"ext": "{\"host\":\"...\"}"</code>），两种内核都能读。</td></tr>
<tr><td><b>30 个站点 <code>api</code> 用相对路径</b><br><span style="color:#e08a1e">站点级失效</span></td>
<td>原仓库的 <code>./lib/drpy2.min.js</code>、<code>./py/xxx.py</code> 依赖发布者的目录结构，配置拷到本地后必然 404，
在电视上表现为一批点不开的空壳站点。</td>
<td>剔除这 32 个站点（标准 TVBox 内核本就不支持 py 站），站点数 176 → 146。</td></tr>
<tr><td><b>配置地址填成 <code>/sdcard/...</code></b><br><span style="color:#e08a1e">操作误区</span></td>
<td>客户端用 OkHttp 拉配置，裸文件路径和 <code>file://</code> 都会抛协议异常，同样只显示「配置失败」。</td>
<td>改为局域网 HTTP（<code>serve.py</code>）或 <code>clan://localhost/...</code> 加载。</td></tr>
</table>
<p style="color:#8a919c;font-size:12.5px;margin:12px 0 0">修复后已通过静态模型体检：146 个站点的 <code>key/name/api/type/ext</code> 类型、重复 key、spider 站点的 <code>csp_*</code> 前缀、直播与解析的字段类型，全部符合 TVBox 数据模型。</p>
</div>

<h2>一、结论先说</h2>
<div class="card"><ul>
<li><b>能用的点播源集中在 macCMS 采集接口</b>：39 个接口用「庆余年」实测搜索，都能返回带 <code>vod_play_url</code> 的片源，其中 13 个只认 <code>ac=videolist</code> 参数（TVBox 本身两种都会试，不影响使用）。</li>
<li><b>直播可用率不到四成，但质量高的很集中</b>：49 个直播源里 18 个能解析出频道，真正稳定的是「IPTV自动更新」「24live」「平台直播」三家；据此逐台竞速挑选，得到 53 个<b>连分片都拉得下来</b>的频道（CCTV 全套 + 全部省级卫视）。</li>
<li><b>解析（VIP 解析）几乎全线失效</b>：78 个里只有 8 个 HTML 嗅探型还能返回直链，JSON 型接口 100% 不可用——这类接口普遍是个人 IP:端口 服务，存活期只有几周。</li>
<li><b>spider jar 是点播的另一半命脉</b>：22 个 jar 里 18 个可下载且 MD5 与配置声明一致。最终配置选用「潇洒」的 <code>spider.jar</code>（1.8MB，19 个通用类命中 17 个）。</li>
</ul></div>

<h2>二、验证方法（四层递进）</h2>
<div class="card">
<h3>1 · 配置层：能不能拿到</h3>
<p style="color:#5b626c;font-size:13.5px;margin:6px 0 14px">直接 HTTP 抓取，校验状态码、体积、JSON 结构（含 <code>//</code> 注释、尾逗号这些 TVBox 容忍但标准 JSON 不容忍的写法）。</p>
<h3>2 · 结构层：里面有什么</h3>
<p style="color:#5b626c;font-size:13.5px;margin:6px 0 14px">统计 <code>sites</code> / <code>lives</code> / <code>parses</code> 数量与类型（0=XML、1=JSON、3=spider），相对路径 <code>./jar/x.jar</code> 一律还原成绝对地址再验证。</p>
<h3>3 · 功能层：真的能用吗</h3>
<p style="color:#5b626c;font-size:13.5px;margin:6px 0 14px">采集源：真实搜索「庆余年」，要求返回片源且带播放地址（不是只看 HTTP 200）。<br>
直播源：解析频道列表 → 随机抽 4 个频道试播 → 记录可播比例。<br>
jar：下载后校验 <code>PK</code> 文件头 + 与配置声明的 MD5 比对。</p>
<h3>4 · 深度层：验证到可播放</h3>
<p style="color:#5b626c;font-size:13.5px;margin:6px 0 0">精选频道逐个拉取 m3u8 → 解析子列表 → 实际下载首个 TS 分片，确认拿到大于 800 字节的媒体数据，排除「列表假活、分片已死」。</p>
</div>

<h2>三、点播：39 个采集源全部实测通过</h2>
<div class="card">
<table><tr><th>站点名</th><th>接口标识</th><th>响应</th><th>实测命中</th></tr>
{{ROWS_CMS}}
</table>
<p style="color:#8a919c;font-size:12.5px;margin-top:12px">以上 39 个源已写入配置文件，搜索「庆余年」均可出片。延迟为单次请求耗时，仅作参考。</p>
</div>

<h3 style="margin-top:26px">失败源的归因（34 个）</h3>
<div class="card">{{CMS_FAIL}}
<p style="color:#8a919c;font-size:12.5px;margin:10px 0 0">其中 11 个是 <b>DNS 被污染</b>而非站点已死——例如 <code>api.tiankongapi.com</code> 被解析到 Facebook 的 IP <code>185.60.218.50</code>。如果你在境外线路或开启了电视端的 DoH，这些源可能反而可用，配置里已内置阿里 / DNSPod 的 DoH。</p>
</div>

<h2>四、直播：49 个源 → 18 个可用 → 53 个精选频道</h2>
<div class="card">
<table><tr><th>直播清单（写入配置）</th><th>复测结果</th></tr>
{{ROWS_LS}}
</table>
</div>

<h3 style="margin-top:24px">失败源归因（31 个）</h3>
<div class="card">{{LIVE_FAIL}}</div>

<h3 style="margin-top:24px">逐台验证通过的 53 个频道</h3>
<div class="card">
<table><tr><th>频道</th><th>地址</th><th>分片</th></tr>
{{ROWS_CH}}
</table>
<p style="color:#8a919c;font-size:12.5px;margin-top:12px">这是「多源竞速」的结果：同一个频道在 18 个源里可能有几十个地址，逐个试播后只保留最快且分片可下载的一条。整份清单在配置目录下的 <code>live.txt</code>。</p>
</div>

<h3 style="margin-top:24px">解析（VIP 解析）8/78</h3>
<div class="card">{{PAR_FAIL}}
<p style="color:#8a919c;font-size:12.5px;margin:10px 0 0">可用的 8 个均为 HTML 嗅探型，已写入配置 <code>parses</code>。JSON 型解析器需要固定 IP:端口，生命周期以周计，不建议长期依赖。</p>
</div>

<h2>五、抓取的 21 个配置</h2>
<div class="card">
<table><tr><th>来源</th><th>地址</th><th>体积</th><th>状态</th></tr>
{{ROWS_SRC}}
</table>
</div>

<h2>六、验证的边界</h2>
<div class="card"><ul>
<li><b>spider 站点（107 个）无法在桌面端逐站验证</b>。它们靠 jar 内的 JS/dex 在播放器里跑，只能在 TVBox 客户端里生效。本次能做的极限是：验证 jar 可下载、MD5 与声明一致、站点引用的 <code>csp_*</code> 类属于该 jar 的通用集合（19 个经典类命中 17 个）。</li>
<li><b>网络位置会改变结论</b>。本报告全部从这台机器（家庭宽带）发起；换运营商、换省份、开 IPv6，结果会有出入。失效的源里有一部分是被 DNS 污染误伤，不代表源站已死。</li>
<li><b>时效性短</b>。免费源的平均寿命以周计，采集接口换域名、直播 IP 关端口都是常态。建议每周重跑一次验证再更新配置。</li>
<li><b>港澳台频道整批缺失</b>。公共免费清单里这类频道基本已被清理，19 个目标频道（凤凰、翡翠、TVB、澳门系列等）在 18 个可用源中都没找到能播的地址——不是筛掉的，是本来就没有。</li>
</ul></div>

<div class="note" style="margin-top:26px">本报告与配置仅用于技术验证与学习交流。所列资源均为互联网公开分享的第三方接口，版权归各自权利人所有，请勿用于商业用途；内容的合法性由源站负责，请遵守所在地区法律法规。</div>

</div></body></html>"""

vals = dict(NOW=now, ROWS_CMS="".join(rows_cms), ROWS_CH="".join(rows_ch),
            ROWS_LS="".join(rows_ls), ROWS_SRC="".join(rows_src),
            CMS_FAIL=bar(cms_fail.most_common(), 34),
            LIVE_FAIL=bar(live_fail.most_common(), 31, "#e08a1e"),
            PAR_FAIL=bar(par_fail.most_common(), 70, "#8a5cf5"))
for k, v in vals.items():
    html_doc = html_doc.replace("{{%s}}" % k, v)

open("output/验证报告.html", "w", encoding="utf-8").write(html_doc)
print("报告已生成", len(html_doc), "bytes")
