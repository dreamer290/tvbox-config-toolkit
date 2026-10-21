"""最小 dex 解析：列出指定类的方法签名。

TVBoxOSC 的 JarLoader.invokeInit 会反射调用
  com.github.catvod.spider.Init.init(android.content.Context)
方法签名对不上就直接判 load error，表现为「md5缓存失效 / jar load err」。
"""
import zipfile, struct, sys


def uleb128(buf, p):
    result = 0
    shift = 0
    while True:
        b = buf[p]
        p += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, p
        shift += 7


class Dex:
    def __init__(self, data):
        self.d = data
        self.string_ids_size, self.string_ids_off = struct.unpack_from("<II", data, 56)
        self.type_ids_size, self.type_ids_off = struct.unpack_from("<II", data, 64)
        self.proto_ids_size, self.proto_ids_off = struct.unpack_from("<II", data, 72)
        self.method_ids_size, self.method_ids_off = struct.unpack_from("<II", data, 88)
        self.class_defs_size, self.class_defs_off = struct.unpack_from("<II", data, 96)

    def string(self, idx):
        off = struct.unpack_from("<I", self.d, self.string_ids_off + idx * 4)[0]
        n, p = uleb128(self.d, off)
        return self.d[p:p + n].decode("utf-8", "replace")

    def type_name(self, idx):
        return self.string(struct.unpack_from("<I", self.d, self.type_ids_off + idx * 4)[0])

    def proto(self, idx):
        shorty_idx, ret_idx, params_off = struct.unpack_from(
            "<III", self.d, self.proto_ids_off + idx * 12)
        params = []
        if params_off:
            size = struct.unpack_from("<I", self.d, params_off)[0]
            for i in range(size):
                t = struct.unpack_from("<H", self.d, params_off + 4 + i * 2)[0]
                params.append(self.type_name(t))
        return self.type_name(ret_idx), params

    def method(self, idx):
        class_idx, proto_idx, name_idx = struct.unpack_from(
            "<HHI", self.d, self.method_ids_off + idx * 8)
        ret, params = self.proto(proto_idx)
        return self.type_name(class_idx), self.string(name_idx), ret, params

    def classes(self):
        for i in range(self.class_defs_size):
            base = self.class_defs_off + i * 32
            class_idx = struct.unpack_from("<I", self.d, base)[0]
            class_data_off = struct.unpack_from("<I", self.d, base + 24)[0]
            yield self.type_name(class_idx), class_data_off

    def methods_of(self, class_data_off):
        p = class_data_off
        sf, p = uleb128(self.d, p)
        inf, p = uleb128(self.d, p)
        dm, p = uleb128(self.d, p)
        vm, p = uleb128(self.d, p)
        for _ in range(sf + inf):  # 跳过字段
            _, p = uleb128(self.d, p)
            _, p = uleb128(self.d, p)
        out = []
        midx = 0
        for _ in range(dm + vm):
            diff, p = uleb128(self.d, p)
            _, p = uleb128(self.d, p)   # access_flags
            _, p = uleb128(self.d, p)   # code_off
            midx += diff
            out.append(self.method(midx))
        return out


if __name__ == "__main__":
    jar = sys.argv[1] if len(sys.argv) > 1 else "../output/spider.jar"
    with zipfile.ZipFile(jar) as z:
        dex = Dex(z.read("classes.dex"))
    print("类数:", dex.class_defs_size, "| 方法数:", dex.method_ids_size)

    targets = ["Lcom/github/catvod/spider/Init;", "Lcom/github/catvod/crawler/Spider;"]
    for desc, off in dex.classes():
        if desc not in targets:
            continue
        print("\n=== %s" % desc)
        if not off:
            print("  (无 class_data)")
            continue
        for cls, name, ret, params in dex.methods_of(off):
            ps = ",".join(params)
            print("  %s %s(%s)" % (ret, name, ps))
