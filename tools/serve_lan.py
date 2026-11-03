#!/usr/bin/env python3
"""
局域网预览 —— 让手机在同一个 WiFi 下打开这个工作台。

为什么不用 `python -m http.server`：
    它会把这整个目录挂出去，包括 collector/cookies.json ——
    那玩意儿等同于你的学习通账号密码，拿到就能冒充你登录。
    校园网/宿舍网谁都能扫，等于把账号摊在桌上。

所以这里用了白名单：只有下面 SAFE 里的路径能被访问，其余一律 404。

用法：
    python tools/serve_lan.py              # 监听 0.0.0.0:8765
    python tools/serve_lan.py --port 9000
    python tools/serve_lan.py --host 127.0.0.1    # 只本机，不给手机看

跑起来后它会打印手机该输的地址。Ctrl+C 停。
"""

import argparse
import http.server
import socket
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 只有这些能被访问。加文件必须手动写在这里 —— 白名单的意义就在于
# "忘了加" 的后果是 404，而不是把凭据漏出去。
SAFE = {
    "index.html",
    "sw.js",
    "manifest.webmanifest",
    "icon.svg",
    "icon-192.png",
    "icon-512.png",
    "collector/out/activities.enc",
}

# 明文数据只有「只听本机」时才放行 —— 本地开发要看数据，
# 但一旦挂到局域网/校园网上，明文就等于没加密。
LOCAL_ONLY = {"collector/out/activities.json"}


class Handler(http.server.SimpleHTTPRequestHandler):
    allow_plaintext = False          # main() 里按监听地址决定

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _resolve(self):
        """把请求路径映射成相对 ROOT 的 posix 路径；不在白名单就返回 None"""
        p = self.path.split("?")[0].split("#")[0].lstrip("/")
        if p in ("", "/"):
            p = "index.html"
        # 防 ../ 穿越
        try:
            real = (ROOT / p).resolve()
            rel = real.relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            return None
        allowed = SAFE | (LOCAL_ONLY if self.allow_plaintext else set())
        return rel if rel in allowed else None

    def do_GET(self):
        if self._resolve() is None:
            self.send_error(404, "Not Found")
            return
        super().do_GET()

    def do_HEAD(self):
        if self._resolve() is None:
            self.send_error(404, "Not Found")
            return
        super().do_HEAD()

    def list_directory(self, path):
        self.send_error(404, "Not Found")     # 永远不给目录列表

    def log_message(self, fmt, *args):
        # 默认会把请求打到 stderr，手机上刷一下刷屏太吵，只留错误
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")


def lan_ip():
    """拿到本机在局域网里的地址。连一个外网地址但不真发包，问内核选了哪块网卡。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    Handler.allow_plaintext = loopback

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), Handler) as httpd:
        print("白名单模式 —— 只有这些能访问：")
        for f in sorted(SAFE):
            print("   ", f)
        if loopback:
            print("    collector/out/activities.json   （只听本机才放行明文）")
        else:
            print("\n明文 activities.json 已屏蔽 —— 局域网能看到的只有密文。")
        print()
        if args.host == "0.0.0.0":
            ip = lan_ip()
            print(f"本机：   http://127.0.0.1:{args.port}/")
            if ip:
                print(f"手机：   http://{ip}:{args.port}/    ← 手机连同一个 WiFi 后输这个")
            print()
            print("注意：校园网常开「AP 隔离」，手机可能连不上；连不上就换 GitHub Pages。")
        else:
            print(f"http://{args.host}:{args.port}/")
        print("\nCtrl+C 停止\n")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
