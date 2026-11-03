#!/usr/bin/env python3
"""
截止提醒 —— 把「快到期的」推到微信。

读 collect.py 产出的 out/activities.json，挑出未来 N 天内到期的，
拼成一条摘要发出去。

两个通道，配了哪个用哪个：
    SERVERCHAN_KEY    Server酱   https://sct.ftqq.com
    PUSHPLUS_TOKEN    PushPlus   https://www.pushplus.plus

两个都没配就静默退出（exit 0）—— 没配提醒不该让采集任务变红。

用法：
    python collector/notify.py              # 未来 3 天内到期的
    python collector/notify.py --days 7     # 放宽到 7 天
    python collector/notify.py --dry        # 只打印不发送，调格式用
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
SRC = BASE / "out" / "activities.json"
CST = timezone(timedelta(hours=8))

TYPE_ICON = {35: "📝", 45: "📢"}


def load_due(days, today):
    if not SRC.exists():
        sys.exit(f"找不到 {SRC}，先跑 collect.py")

    doc = json.loads(SRC.read_text(encoding="utf-8"))
    horizon = today + timedelta(days=days)

    due, overdue = [], []
    for a in doc.get("activities", []):
        raw = a.get("deadline")
        if not raw:
            continue
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue          # 解析器偶尔会写出奇怪的东西，别让它炸掉整个提醒

        item = {
            "date": d,
            "title": (a.get("title") or a.get("typeName") or "（无标题）").strip(),
            "course": a.get("courseName") or "",
            "author": a.get("author") or "",
            "type": a.get("type"),
            "left": (d - today).days,
        }
        if d < today:
            # 只提最近一周内才过期的，翻旧账没意义
            if (today - d).days <= 7:
                overdue.append(item)
        elif d <= horizon:
            due.append(item)

    due.sort(key=lambda x: x["date"])
    overdue.sort(key=lambda x: x["date"])
    return doc, due, overdue


def render(doc, due, overdue, days, today):
    stats = doc.get("stats", {})
    parts = []
    n = len(due)

    if n:
        head = f"{n} 项在 {days} 天内截止"
    elif overdue:
        head = "没有新的截止，但有逾期未处理的"
    else:
        head = f"未来 {days} 天没有截止的"

    parts.append(f"**{head}**\n")

    for it in due:
        icon = TYPE_ICON.get(it["type"], "•")
        left = it["left"]
        when = "今天" if left == 0 else "明天" if left == 1 else f"{left} 天后"
        line = f"{icon} **{when}** · {it['date']:%m-%d}　{it['title']}"
        tail = " / ".join(x for x in (it["course"], it["author"]) if x)
        if tail:
            line += f"\n　　{tail}"
        parts.append(line)

    if overdue:
        parts.append(f"\n---\n\n**已逾期 {len(overdue)} 项**")
        for it in overdue:
            parts.append(f"• {it['date']:%m-%d}　{it['title']}")

    parts.append(
        f"\n---\n\n数据采集于 {doc.get('collectedAt', '?')[:16].replace('T', ' ')}"
        f"（{stats.get('courses', '?')} 门课 / {stats.get('activities', '?')} 条活动）"
    )
    return head, "\n".join(parts)


def send_serverchan(key, title, body):
    r = requests.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": title[:32], "desp": body},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    # code 0 = 成功；其他都算失败，把原始返回带进异常里方便排查
    if j.get("code") != 0:
        raise RuntimeError(f"Server酱返回异常: {j}")
    return "Server酱"


def send_pushplus(token, title, body):
    r = requests.post(
        "https://www.pushplus.plus/api/send",
        json={"token": token, "title": title[:100],
              "content": body, "template": "markdown"},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 200:
        raise RuntimeError(f"PushPlus返回异常: {j}")
    return "PushPlus"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="提前几天开始提醒（默认 3）")
    ap.add_argument("--dry", action="store_true", help="只打印，不发送")
    ap.add_argument("--force", action="store_true",
                    help="没有到期项也发（调通道用）")
    args = ap.parse_args()

    today = datetime.now(CST).date()
    doc, due, overdue = load_due(args.days, today)
    title, body = render(doc, due, overdue, args.days, today)

    print(title)
    print("-" * 40)
    print(body)

    if args.dry:
        print("\n[--dry] 未发送")
        return

    sk = (os.environ.get("SERVERCHAN_KEY") or "").strip()
    pp = (os.environ.get("PUSHPLUS_TOKEN") or "").strip()

    if not sk and not pp:
        print("\n没配 SERVERCHAN_KEY / PUSHPLUS_TOKEN，跳过推送。")
        return

    if not due and not overdue and not args.force:
        print("\n没有要提醒的，跳过推送。")
        return

    try:
        if sk:
            print(f"\n已推送（{send_serverchan(sk, title, body)}）")
        if pp:
            print(f"\n已推送（{send_pushplus(pp, title, body)}）")
    except Exception as e:
        # 推送失败不该让采集任务整体失败 —— 数据已经提交了，提醒是附加的
        print(f"\n推送失败：{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
