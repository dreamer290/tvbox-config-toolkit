"""复刻 TVBoxOSC 的 ProtectedInitJar.isProtected() 判定。

判定为「受保护 jar」后，TVBoxOSC 不再调用 Init.init(Context)，
而是走 DexNative.getLoader() 的路径；jar 里没有 DexNative 就直接加载失败，
客户端表现为「md5缓存失效 / jar load err」。
"""
import zipfile, struct, glob, os
from dex_methods import Dex, uleb128

PKG = "com.github.tvbox.osc.jun"


class Scanner:
    def __init__(self, dex):
        self.d = dex
        self.data = dex.d

    def uleb(self, p):
        return uleb128(self.data, p)

    def code_strings(self, code_off):
        """扫 code_item 的 const-string / const-string-jumbo"""
        out = []
        if code_off <= 0:
            return out
        insns_size = struct.unpack_from("<I", self.data, code_off + 12)[0]
        base = code_off + 16
        end = base + insns_size * 2
        off = base
        while off + 1 < end and off + 1 < len(self.data):
            op = self.data[off]
            if op == 0x1A and off + 3 < end:
                idx = struct.unpack_from("<H", self.data, off + 2)[0]
                out.append(self.d.string(idx))
            elif op == 0x1B and off + 5 < end:
                idx = struct.unpack_from("<I", self.data, off + 2)[0]
                out.append(self.d.string(idx))
            off += 2
        return out

    def calls_kill(self, code_off):
        if code_off <= 0:
            return False
        insns_size = struct.unpack_from("<I", self.data, code_off + 12)[0]
        base = code_off + 16
        end = base + insns_size * 2
        off = base
        while off + 3 < end and off + 3 < len(self.data):
            op = self.data[off]
            if 0x6E <= op <= 0x72:
                midx = struct.unpack_from("<H", self.data, off + 2)[0]
                if midx < self.d.method_ids_size:
                    cls, name, _, _ = self.d.method(midx)
                    if cls == "Landroid/os/Process;" and name == "killProcess":
                        return True
            off += 2
        return False


def fmt(b):
    return b if isinstance(b, str) else b.decode("utf-8", "replace")


def inspect(path):
    with zipfile.ZipFile(path) as z:
        d = Dex(z.read("classes.dex"))
        sc = Scanner(d)
        has_init = False
        kill = False
        init_strings = []
        for desc, off in d.classes():
            if not desc.startswith("Lcom/github/catvod/spider/Init"):
                continue
            if desc == "Lcom/github/catvod/spider/Init;":
                has_init = True
            if not off:
                continue
            p = off
            sf, p = uleb128(d.d, p)
            inf, p = uleb128(d.d, p)
            dm, p = uleb128(d.d, p)
            vm, p = uleb128(d.d, p)
            for _ in range(sf + inf):
                _, p = uleb128(d.d, p)
                _, p = uleb128(d.d, p)
            midx = 0
            for _ in range(dm + vm):
                diff, p = uleb128(d.d, p)
                _, p = uleb128(d.d, p)
                code_off, p = uleb128(d.d, p)
                midx += diff
                if code_off <= 0:
                    continue
                cls, name, ret, params = d.method(midx)
                if sc.calls_kill(code_off):
                    kill = True
                if name == "init" and desc == "Lcom/github/catvod/spider/Init;":
                    init_strings = [fmt(s) for s in sc.code_strings(code_off)]
        return has_init, kill, init_strings


print("TVBoxOSC 包名: %s\n" % PKG)
print("%-30s %5s %6s  %-8s %s" % ("jar", "Init", "kill", "判定", "Init.init 内字符串数 / 是否含包子串"))
print("-" * 108)
for path in sorted(glob.glob("jar/*.jar")) + ["output/spider.jar"]:
    try:
        has_init, kill, strs = inspect(path)
    except Exception as e:
        print("%-30s  解析失败: %s" % (os.path.basename(path), str(e)[:40]))
        continue
    protected = has_init and kill
    hit = [s for s in strs if PKG in s]
    print("%-30s %5s %6s  %-8s %d 个%s" % (
        os.path.basename(path),
        "✓" if has_init else "—",
        "✓" if kill else "—",
        "受保护" if protected else "普通",
        len(strs),
        ("，含包名: " + hit[0][:40]) if hit else ""))
