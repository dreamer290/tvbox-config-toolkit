"""批量体检 spider jar：能否被 TVBoxOSC 加载。

TVBoxOSC 的加载门槛（JarLoader.invokeInit）：
  1. 必须能 loadClass("com.github.catvod.spider.Init")
  2. 必须存在 init(android.content.Context) 且返回 void
另外 Init 里若含 startFloatBall / startGoProxy 等宿主专属逻辑，
init 执行时容易因缺少对应权限或组件抛异常，进而被判 load error。
"""
import zipfile, glob, os, sys
from dex_methods import Dex

HOST_HOOKS = ["startFloatBall", "startGoProxy", "interceptActivityStart", "checkPermission"]

rows = []
for path in sorted(glob.glob("jar/*.jar")) + ["output/spider.jar"]:
    rec = dict(file=os.path.basename(path), size=os.path.getsize(path),
               has_init_cls=False, has_init_method=False, hooks=[], classes=0, err="")
    try:
        with zipfile.ZipFile(path) as z:
            dexs = [n for n in z.namelist() if n.endswith(".dex")]
            if not dexs:
                rec["err"] = "无 dex"
                rows.append(rec)
                continue
            d = Dex(z.read(sorted(dexs)[0]))
            rec["classes"] = d.class_defs_size
            for desc, off in d.classes():
                if desc == "Lcom/github/catvod/spider/Init;":
                    rec["has_init_cls"] = True
                    if off:
                        for cls, name, ret, params in d.methods_of(off):
                            if name == "init" and params == ["Landroid/content/Context;"] and ret == "V":
                                rec["has_init_method"] = True
                            if name in HOST_HOOKS and name not in rec["hooks"]:
                                rec["hooks"].append(name)
                    break
    except Exception as e:
        rec["err"] = "%s: %s" % (type(e).__name__, str(e)[:40])
    rows.append(rec)

print("%-30s %8s %5s %5s %4s  %s" % ("jar", "size", "Init", "init()", "类数", "宿主依赖"))
print("-" * 96)
for r in rows:
    print("%-30s %8d %5s %5s %4d  %s%s" % (
        r["file"], r["size"],
        "✓" if r["has_init_cls"] else "—",
        "✓" if r["has_init_method"] else "—",
        r["classes"],
        ",".join(r["hooks"]) or "无",
        ("  [" + r["err"] + "]") if r["err"] else ""))

ok = [r for r in rows if r["has_init_cls"] and r["has_init_method"] and not r["err"]]
print("\n可被 TVBoxOSC 加载: %d/%d" % (len(ok), len(rows)))
clean = [r for r in ok if not r["hooks"]]
print("其中最纯净（无宿主依赖）: %s" % ([r["file"] for r in clean] or "无"))
