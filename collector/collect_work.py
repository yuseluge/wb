#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学习通作业采集器
================

从 mooc1 的作业列表页抓取每门课的作业（标题/状态/剩余时间/截止时间），
输出 works.json。

与 collect.py 的关系：
    collect.py      抓「活动」（讨论/签到/问卷）—— activelist 接口，不含作业
    collect_work.py 抓「作业」—— 需要走课程页 → work/list，多两步取 enc

链路（每门课）：
    1. GET  mooc1/mooc-ans/visit/stucoursemiddle?courseid=&clazzid=&cpi=
       302 跳转，Location 里带 enc（记为 stuenc）
    2. GET  mooc2-ans/mooc2-ans/mycourse/stu?...&enc=stuenc
       HTML 隐藏域里有 workEnc —— 这就是作业列表要的 enc
    3. GET  mooc1/mooc2/work/list?courseId=&classId=&cpi=&ut=s&stuenc=&enc=workEnc
       返回作业列表 HTML

关键点：enc 是动态的，每次访问都变，所以每轮都要现取，不能缓存。
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
COOKIE_FILE = BASE / "cookies.json"
OUT_DIR = BASE / "out"
CST = timezone(timedelta(hours=8))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

STATUS_MAP = {"未交": "unsubmitted", "已完成": "finished", "已交": "submitted"}


def load_cookies() -> dict:
    if not COOKIE_FILE.exists():
        sys.exit(f"找不到 {COOKIE_FILE}，请先导出 Cookie。")
    return json.loads(COOKIE_FILE.read_text(encoding="utf-8"))


def cookie_header(cookies: dict, host: str) -> str:
    jar = {}
    for domain, kv in cookies.items():
        if domain == ".chaoxing.com" or host.endswith(domain):
            jar.update(kv)
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def headers_for(cookies: dict, host: str, **extra) -> dict:
    h = {
        "User-Agent": UA,
        "Cookie": cookie_header(cookies, host),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    h.update(extra)
    return h


# ---------------------------------------------------------------- 课程

def fetch_courses(session: requests.Session, cookies: dict) -> list:
    """课程列表，顺带取 personId（后面拼 cpi 用）"""
    url = "https://mooc1.chaoxing.com/mooc-ans/visit/courselistdata"
    body = "courseType=1&courseFolderId=0&baseEducation=0&superstarClass=&courseFolderSize=0"
    r = session.post(
        url, data=body,
        headers=headers_for(cookies, "mooc1.chaoxing.com", **{
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mooc1.chaoxing.com/visit/interaction",
        }), timeout=30)
    r.raise_for_status()

    courses, seen = [], set()
    for chunk in r.text.split('<li class="course')[1:]:
        cid = re.search(r'courseId="(\d+)"', chunk)
        clz = re.search(r'clazzId="(\d+)"', chunk)
        pid = re.search(r'personId="(\d+)"', chunk)
        if not (cid and clz):
            continue
        key = (cid.group(1), clz.group(1))
        if key in seen:
            continue
        seen.add(key)
        name = re.search(r'class="course-name[^"]*"\s+title="([^"]*)"', chunk)
        courses.append({
            "courseId": cid.group(1),
            "classId": clz.group(1),
            "personId": pid.group(1) if pid else "",
            "name": name.group(1) if name else "",
        })
    return courses


def course_enc(session, cookies, course) -> dict:
    """走 stucoursemiddle 拿 302 的 enc，再进课程页取 workEnc"""
    cid, clz, pid = course["courseId"], course["classId"], course["personId"]
    mid = (f"https://mooc1.chaoxing.com/mooc-ans/visit/stucoursemiddle"
           f"?courseid={cid}&clazzid={clz}&vc=1&cpi={pid}&ismooc2=1&v=2")
    r = session.get(mid, headers=headers_for(cookies, "mooc1.chaoxing.com"),
                    timeout=30, allow_redirects=False)
    loc = r.headers.get("Location", "")
    m = re.search(r'enc=([0-9a-f]+)', loc)
    if not m:
        raise RuntimeError(f"没有拿到 enc（status={r.status_code}）")
    stuenc = m.group(1)

    page = session.get(loc, headers=headers_for(cookies, "mooc2-ans.chaoxing.com"),
                       timeout=30).text
    work = re.search(r'id="workEnc"[^>]*value="([0-9a-f]+)"', page)
    # 兼容属性顺序相反的情况
    if not work:
        work = re.search(r'value="([0-9a-f]+)"[^>]*id="workEnc"', page)
    if not work:
        raise RuntimeError("课程页里没找到 workEnc")
    return {"stuenc": stuenc, "workEnc": work.group(1)}


# ---------------------------------------------------------------- 作业

def parse_remaining(text: str):
    """'剩余430小时42分钟' -> timedelta"""
    h = re.search(r'(\d+)\s*小时', text)
    m = re.search(r'(\d+)\s*分钟', text)
    d = re.search(r'(\d+)\s*天', text)
    if not (h or m or d):
        return None
    return timedelta(hours=int(h.group(1)) if h else 0,
                     minutes=int(m.group(1)) if m else 0,
                     days=int(d.group(1)) if d else 0)


def fetch_works(session, cookies, course, encs) -> list:
    cid, clz, pid = course["courseId"], course["classId"], course["personId"]
    url = (f"https://mooc1.chaoxing.com/mooc2/work/list?courseId={cid}"
           f"&classId={clz}&cpi={pid}&ut=s&stuenc={encs['stuenc']}"
           f"&enc={encs['workEnc']}")
    r = session.get(url, headers=headers_for(
        cookies, "mooc1.chaoxing.com",
        Referer="https://mooc1.chaoxing.com/mooc2/work/list"), timeout=30)
    html = r.text
    if "无权限" in html:
        raise RuntimeError("作业页返回「无权限」")
    if "作业列表" not in html:
        raise RuntimeError(f"不是作业列表页（len={len(html)}）")

    now = datetime.now(CST)
    works = []
    for li in html.split("<li onclick=")[1:]:
        wm = re.search(r'workId=(\d+)', li)
        if not wm:
            continue
        am = re.search(r'answerId=(\d+)', li)
        tm = re.search(r'<p class="overHidden2 fl">(.*?)</p>', li, re.S)
        sm = re.search(r'<p class="status fl">(.*?)</p>', li, re.S)
        rm = re.search(r'(剩余[^<]*)', li)
        title = re.sub(r'<[^>]+>', '', tm.group(1)).strip() if tm else ""
        status = re.sub(r'<[^>]+>', '', sm.group(1)).strip() if sm else ""
        remain = rm.group(1).strip() if rm else ""
        delta = parse_remaining(remain)
        works.append({
            "courseId": cid,
            "courseName": course["name"],
            "workId": wm.group(1),
            "answerId": am.group(1) if am else "0",
            "title": title,
            "status": STATUS_MAP.get(status, status),
            "statusText": status,
            "remaining": remain,
            "deadline": (now + delta).isoformat() if delta else None,
        })
    return works


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-unfinished", action="store_true", help="只保留未交作业")
    args = ap.parse_args()

    cookies = load_cookies()
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    print("[1/3] 拉取课程列表…")
    courses = fetch_courses(session, cookies)
    print(f"      {len(courses)} 门课程")

    all_works, failed = [], []
    print("[2/3] 逐门课取 enc → 拉作业…")
    for i, c in enumerate(courses, 1):
        tag = f"({i}/{len(courses)}) {c['name'][:14]}"
        try:
            encs = course_enc(session, cookies, c)
            works = fetch_works(session, cookies, c, encs)
            all_works.extend(works)
            todo = [w for w in works if w["status"] != "finished"]
            print(f"   {tag}: {len(works)} 条（未交 {len(todo)}）")
        except Exception as e:
            failed.append({"course": c["name"], "error": str(e)})
            print(f"   {tag}: ⚠ {e}")
        time.sleep(0.4)

    if args.only_unfinished:
        all_works = [w for w in all_works if w["status"] != "finished"]

    result = {
        "collectedAt": datetime.now(CST).isoformat(),
        "stats": {
            "courses": len(courses),
            "works": len(all_works),
            "unfinished": len([w for w in all_works if w["status"] != "finished"]),
            "failedCourses": len(failed),
        },
        "works": all_works,
        "failed": failed,
    }
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "works.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n[3/3] 完成 —— {out}")
    print(f"  作业 {result['stats']['works']} 条，未交 {result['stats']['unfinished']} 条")
    for w in sorted(all_works, key=lambda x: x["deadline"] or "9"):
        if w["status"] != "finished":
            print(f"    · {w['title']} | {w['statusText']} | {w['remaining']} "
                  f"| 截止约 {w['deadline'][:16] if w['deadline'] else '未知'}")


if __name__ == "__main__":
    main()
