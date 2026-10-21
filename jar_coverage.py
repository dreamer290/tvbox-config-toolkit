"""验证「站点 api 与 jar 内类」是否配套。

TVBoxOSC 的 JarLoader 把 api 里的 csp_XXX 映射成
  com.github.catvod.spider.XXX
类不存在则 newInstance 失败，站点表现为打不开。
"""
import zipfile, glob, os, json
from dex_methods import Dex

cfg = json.load(open("output/tvbox-config.json", encoding="utf-8"))
apis = []
for s in cfg["sites"]:
    if s.get("type") not in (3, 4):
        continue
    api = str(s.get("api", ""))
    if "csp_" in api:
        apis.append(api.split("csp_")[1].split("&")[0].strip())
uniq = sorted(set(apis))
print("配置中 spider 站点: %d 个，唯一 csp_ 类: %d 个" % (len(apis), len(uniq)))


def spider_classes(path):
    with zipfile.ZipFile(path) as z:
        dexs = [n for n in z.namelist() if n.endswith(".dex")]
        d = Dex(z.read(sorted(dexs)[0]))
        out = set()
        hooks = set()
        for desc, off in d.classes():
            if desc.startswith("Lcom/github/catvod/spider/"):
                name = desc[len("Lcom/github/catvod/spider/"):-1]
                if "$" not in name:
                    out.add(name)
            if desc == "Lcom/github/catvod/spider/Init;" and off:
                for cls, name, ret, params in d.methods_of(off):
                    hooks.add(name)
        return out, hooks


print("\n%-30s %8s %6s %6s  %s" % ("jar", "spider类", "命中", "覆盖率", "缺失示例"))
print("-" * 100)
results = []
for path in sorted(glob.glob("jar/*.jar")) + ["output/spider.jar"]:
    try:
        cls, hooks = spider_classes(path)
    except Exception as e:
        print("%-30s  解析失败 %s" % (os.path.basename(path), e))
        continue
    hit = [a for a in uniq if a in cls]
    miss = [a for a in uniq if a not in cls]
    cov = len(hit) / len(uniq) * 100
    host = [h for h in ("startFloatBall", "startGoProxy", "interceptActivityStart") if h in hooks]
    results.append((os.path.basename(path), len(cls), len(hit), cov, miss, host))
    print("%-30s %8d %6d %5.1f%%  %s%s" % (
        os.path.basename(path), len(cls), len(hit), cov,
        ",".join(miss[:4]) + ("..." if len(miss) > 4 else ""),
        ("  [宿主依赖]" if host else "")))

print("\n=== 覆盖率 100% 且无宿主依赖的候选 ===")
cand = [r for r in results if r[3] == 100.0 and not r[5]]
for r in cand:
    print("  ", r[0], "类数", r[1])
if not cand:
    print("  无（最优覆盖 %.1f%%）" % max(r[3] for r in results))
