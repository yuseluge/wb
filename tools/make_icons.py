# -*- coding: utf-8 -*-
"""生成 PWA 图标（icon-192.png / icon-512.png）。改配色或图形后重跑一次即可。"""
from pathlib import Path
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent.parent
S = 1024                      # 先画大的再缩小，边缘更干净
C1 = (99, 102, 241)           # #6366f1
C2 = (67, 56, 202)            # #4338ca


def gradient(size):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)          # 左上 → 右下
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(C1, C2))
    return img


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1],
                                        radius=radius, fill=255)
    return m


def build(size=S):
    img = gradient(size).convert("RGBA")
    img.putalpha(rounded_mask(size, round(size * 0.21875)))   # 112/512

    d = ImageDraw.Draw(img)
    w = round(size * 0.074)                                   # 38/512
    # 对勾
    d.line([(size * .293, size * .488), (size * .395, size * .590),
            (size * .605, size * .380)],
           fill=(255, 255, 255, 255), width=w, joint="curve")
    # 下面一条横线
    d.line([(size * .344, size * .727), (size * .656, size * .727)],
           fill=(255, 255, 255, 140), width=round(w * .89))
    return img


if __name__ == "__main__":
    icon = build()
    for out in (192, 512):
        icon.resize((out, out), Image.LANCZOS).save(BASE / f"icon-{out}.png")
        print(f"icon-{out}.png")
