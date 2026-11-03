#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并器：把作业（works.json）并进工作台的活动数据（activities.json）
====================================================================

为啥要合并：
    collect.py     抓「活动」→ activities.json（讨论/签到/问卷）
    collect_work.py 抓「作业」→ works.json（作业是另一个接口，拿不到活动里）
    而 index.html 只认 activities.json 的 activities 数组，所以这里接一手。

字段映射（作业 → 活动）：
    type      35（超星里作业的类型码）
    typeName  "作业"
    deadline  只取日期 YYYY-MM-DD —— 前端会自己拼 "T23:59:59"，
              传完整 ISO 反而会解析失败
    read      未交 = false（这样角标统计才有意义）

幂等：每次先剔除旧的 work:* 条目再重建，重复跑不会翻倍。
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "out"
ACTS = OUT_DIR / "activities.json"
WORKS = OUT_DIR / "works.json"
CST = timezone(timedelta(hours=8))

WORK_PREFIX = "work:"


def work_to_activity(w: dict) -> dict:
    deadline = None
    if w.get("deadline"):
        # 2026-11-21T22:11:00+08:00 -> 2026-11-21
        deadline = w["deadline"][:10]
    return {
        "id": WORK_PREFIX + str(w["workId"]),
        "courseId": w.get("courseId", ""),
        "classId": w.get("classId", ""),
        "courseName": w.get("courseName", ""),
        "type": 35,
        "typeName": "作业",
        "title": w.get("title", ""),
        "author": "",
        "startAt": None,
        "endAt": None,
        "read": w.get("status") == "finished",
        "noticeId": None,
        "idCode": None,
        "body": "",
        "deadline": deadline,
        "deadlineSource": "remaining" if deadline else None,
        # 额外字段，前端会忽略，但方便自己排查
        "workStatus": w.get("statusText", ""),
        "workRemaining": w.get("remaining", ""),
    }


def main():
    if not ACTS.exists():
        sys.exit(f"找不到 {ACTS}，先跑 collect.py")

    data = json.loads(ACTS.read_text(encoding="utf-8"))
    # 作业采集失败时不要让整个流程挂掉：没有 works.json 就当 0 条作业，
    # 活动数据照常可用（作业是锦上添花，不是地基）。
    if WORKS.exists():
        works = json.loads(WORKS.read_text(encoding="utf-8"))
    else:
        print(f"⚠ 没找到 {WORKS}，本轮跳过作业合并")
        works = {"works": []}

    # 剔除上一轮合并进来的作业，避免重复叠加
    base = [a for a in data["activities"]
            if not str(a.get("id", "")).startswith(WORK_PREFIX)]
    merged = [work_to_activity(w) for w in works.get("works", [])]
    data["activities"] = base + merged

    st = data.setdefault("stats", {})
    st["activities"] = len(data["activities"])
    st["works"] = len(merged)
    st["worksUnfinished"] = len([w for w in works.get("works", [])
                                 if w.get("status") != "finished"])
    data["mergedAt"] = datetime.now(CST).isoformat()

    ACTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"合并完成：活动 {len(base)} 条 + 作业 {len(merged)} 条 "
          f"= {len(data['activities'])} 条")
    print(f"  其中有截止时间 "
          f"{len([a for a in data['activities'] if a.get('deadline')])} 条")
    for a in sorted([x for x in merged if x.get("deadline")], key=lambda x: x["deadline"]):
        print(f"  · {a['deadline']} | {a['courseName'][:12]} | {a['title'][:26]} "
              f"| {a['workStatus']}")


if __name__ == "__main__":
    main()
