import http.server, socketserver, socket, os, sys, threading, webbrowser

# 把 output 目录作为网站根，电视盒子用 http://电脑IP:9978/tvbox-config.json 加载。
# 这样配置里的 ./spider.jar、./live.txt 会自动解析成同一台机器上的地址，不需要改任何路径。

PORT = 9978
_HERE = os.path.dirname(os.path.abspath(__file__))
# 脚本既可以在 output 的上一级，也可以和配置文件放在一起
ROOT = os.path.join(_HERE, "output") if os.path.isdir(os.path.join(_HERE, "output")) else _HERE


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stdout.write("  [请求] %s\n" % (fmt % args))


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


os.chdir(ROOT)
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler)

print("=" * 62)
print(" TVBox 配置分发服务已启动   目录: %s" % ROOT)
print("=" * 62)
ips = lan_ips() or ["<本机IP>"]
for ip in ips:
    print("  配置地址（本地版）: http://%s:%d/tvbox-config.json" % (ip, PORT))
for ip in ips:
    print("  配置地址（在线版）: http://%s:%d/tvbox-online.json" % (ip, PORT))
print()
print("  备用写法（部分版本用）: clan://localhost/TVBox/tvbox-config.json")
print("  使用前请确保手机/电视盒子与本机在同一 WiFi，且放行防火墙端口 %d" % PORT)
print("  按 Ctrl+C 停止")
print("=" * 62)

threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d/tvbox-config.json" % PORT)).start()
try:
    httpd.serve_forever()
except KeyboardInterrupt:
    print("\n已停止")
