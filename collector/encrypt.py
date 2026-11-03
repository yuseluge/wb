#!/usr/bin/env python3
"""
把 activities.json 加密成 activities.enc。

为什么要这一步：仓库得公开（Free 计划的 Pages 只能用公开仓库），
而 activities.json 里是你 63 门课、146 条通知的全文 —— 课程名、老师名、
作业要求。GitHub 会索引公开仓库内容，搜一下就可能撞到。

加密之后公开仓库里只有密文，密钥只在你手机和 GitHub Secret 里。

格式（就是给浏览器 WebCrypto 用的，没有自定义二进制）：
    {
      "v": 1,
      "kdf": "PBKDF2-SHA256", "iter": 310000,
      "cipher": "AES-256-GCM",
      "salt": "<base64>", "iv": "<base64>", "ct": "<base64>"
    }

密码从环境变量 WORKBENCH_PASSWORD 读 —— 不落命令行参数，
因为命令行参数会进 ps 和 shell history。

用法：
    WORKBENCH_PASSWORD='你的密码' python collector/encrypt.py
    WORKBENCH_PASSWORD='...' python collector/encrypt.py --check   # 验一下能不能解开
"""

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAIN = BASE / "out" / "activities.json"
ENC = BASE / "out" / "activities.enc"

ITER = 310_000          # OWASP 对 PBKDF2-HMAC-SHA256 的推荐值；浏览器里约 200ms
SALT_LEN = 16
IV_LEN = 12


def b64e(b):
    return base64.b64encode(b).decode("ascii")


def b64d(s):
    return base64.b64decode(s)


def derive(password, salt, iterations=ITER):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                               iterations, dklen=32)


def get_password(required=True):
    pw = os.environ.get("WORKBENCH_PASSWORD", "")
    if not pw:
        if required:
            sys.exit(
                "没有设置 WORKBENCH_PASSWORD。\n"
                "加密是刻意做成「没密码就失败」的 —— 悄悄退化成明文上传，\n"
                "正好是这一步要防的事。\n\n"
                "  本地：  WORKBENCH_PASSWORD='你的密码' python collector/encrypt.py\n"
                "  云端：  仓库 Settings → Secrets → Actions → 新建 WORKBENCH_PASSWORD"
            )
        return None
    if len(pw) < 8:
        sys.exit("密码太短了，至少 8 位。这是个公开仓库，别用 123456。")
    return pw


def encrypt(plaintext: bytes, password: str) -> dict:
    # 每次采集都换新 salt/iv —— 同一个密码两天的密文不该长得一样
    salt = os.urandom(SALT_LEN)
    iv = os.urandom(IV_LEN)
    key = derive(password, salt)

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ct = AESGCM(key).encrypt(iv, plaintext, None)

    return {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "iter": ITER,
        "cipher": "AES-256-GCM",
        "salt": b64e(salt),
        "iv": b64e(iv),
        "ct": b64e(ct),
    }


def decrypt(env: dict, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = derive(password, b64d(env["salt"]), env.get("iter", ITER))
    return AESGCM(key).decrypt(b64d(env["iv"]), b64d(env["ct"]), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="解开已有的 .enc 验证密码对不对，不重新加密")
    ap.add_argument("--plain", type=Path, default=PLAIN)
    ap.add_argument("--out", type=Path, default=ENC)
    args = ap.parse_args()

    pw = get_password()

    if args.check:
        if not args.out.exists():
            sys.exit(f"{args.out} 不存在，没什么可验的")
        env = json.loads(args.out.read_text(encoding="utf-8"))
        try:
            pt = decrypt(env, pw)
        except Exception:
            sys.exit("解不开 —— 密码不对，或者文件被改过")
        j = json.loads(pt)
        print(f"解开成功：{j['stats']['activities']} 条活动、"
              f"{j['stats']['courses']} 门课、{j['stats']['withDeadline']} 个截止时间")
        return

    if not args.plain.exists():
        sys.exit(f"找不到 {args.plain}，先跑 collect.py")

    raw = args.plain.read_bytes()
    env = encrypt(raw, pw)
    args.out.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")

    # 立刻解一遍确认能还原 —— 加密写错比不加密更糟，那是永久丢数据
    assert decrypt(env, pw) == raw, "自检失败：加密后解不回原文"

    print(f"{args.plain.name}  {len(raw):,} 字节 → {args.out.name}  {args.out.stat().st_size:,} 字节")
    print(f"PBKDF2-SHA256 × {ITER:,} / AES-256-GCM，密码长度 {len(pw)}")


if __name__ == "__main__":
    main()
