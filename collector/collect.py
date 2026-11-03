#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学习通（超星）数据采集器
=========================

从学习通抓取课程列表和老师发布的通知/作业，输出成一份 JSON。

用法:
    python collect.py                # 采集全部课程
    python collect.py --only-new     # 只保留 30 天内的条目

依赖: requests  (pip install requests)

数据来源（两个接口都是实测验证过的）:
    1. 课程列表  POST https://mooc1.chaoxing.com/mooc-ans/visit/courselistdata
       - 必须带 Content-Type: application/x-www-form-urlencoded; charset=UTF-8
         否则服务端返回"暂无课程"
    2. 活动列表  GET  https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist
       - 纯 JSON，只需要 Cookie，无签名参数
       - 一次返回该课程的全部历史活动（实测某门课返回 62 条，不分页）

注意:
    cookies.json 里是登录凭据，等于账号密码，绝不要提交到 git。
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

FID = "1475"  # 用户学校
CST = timezone(timedelta(hours=8))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 活动类型码 -> 中文名（实测解码，未知的会显示为 typeN）
TYPE_NAMES = {
    2: "签到", 4: "报名", 5: "讨论", 11: "课堂互动", 14: "问卷",
    35: "作业", 42: "讨论", 44: "测验", 45: "通知", 51: "专题",
    74: "签退",
}
# 这些类型对工作台没用，直接丢弃
IGNORED_TYPES = {2, 74}

# 实测发现：通知(type 45)的 endTime 恒等于 startTime + 24h，那是"通知有效期"，
# 不是作业截止时间。只有间隔明显超过 24h 的 endTime 才算真截止。
EXPIRY_HOURS = 26


# ---------------------------------------------------------------- Cookie

def load_cookies() -> dict:
    if not COOKIE_FILE.exists():
        sys.exit(f"找不到 {COOKIE_FILE}，请先导出 Cookie。")
    return json.loads(COOKIE_FILE.read_text(encoding="utf-8"))


def cookie_header(cookies: dict, host: str) -> str:
    """
    给指定域名拼 Cookie 头。

    不用 requests 的 cookie jar —— 因为超星的 cookie 值里已经有 %2B 这类
    百分号编码，交给 requests 处理有可能被二次编码。手工拼最保险。
    """
    jar = {}
    for domain, kv in cookies.items():
        if domain == ".chaoxing.com" or host.endswith(domain):
            jar.update(kv)
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def make_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def headers_for(cookies: dict, host: str, **extra) -> dict:
    h = {
        "User-Agent": UA,
        "Cookie": cookie_header(cookies, host),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    h.update(extra)
    return h


# ---------------------------------------------------------------- 课程列表

def fetch_courses(session: requests.Session, cookies: dict) -> list:
    """拉取全部课程。返回 [{courseId, classId, name}]"""
    url = "https://mooc1.chaoxing.com/mooc-ans/visit/courselistdata"
    body = "courseType=1&courseFolderId=0&baseEducation=0&superstarClass=&courseFolderSize=0"
    r = session.post(
        url,
        data=body,
        headers=headers_for(
            cookies, "mooc1.chaoxing.com",
            **{
                # 这个 header 是必须的，少了会返回空列表
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mooc1.chaoxing.com/visit/interaction",
            },
        ),
        timeout=30,
    )
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="replace")

    # 每个课程是一个 <li class="course" courseId=... clazzId=...>
    # 按它切开逐块解析，比整体正则稳（块内有嵌套的 <li>）
    courses, seen = [], set()
    for chunk in html.split('<li class="course')[1:]:
        cid = re.search(r'courseId="(\d+)"', chunk)
        clz = re.search(r'clazzId="(\d+)"', chunk)
        if not (cid and clz):
            continue
        name = re.search(r'class="course-name[^"]*"\s+title="([^"]*)"', chunk)
        key = (cid.group(1), clz.group(1))
        if key in seen:
            continue
        seen.add(key)
        courses.append({
            "courseId": cid.group(1),
            "classId": clz.group(1),
            "name": name.group(1) if name else "",
        })
    return courses


# ---------------------------------------------------------------- 活动列表

def fetch_activities(session: requests.Session, cookies: dict,
                     course_id: str, class_id: str) -> list:
    """拉取单门课程的全部活动（通知/作业/签到…）"""
    url = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
    params = {
        "fid": FID,
        "courseId": course_id,
        "classId": class_id,
        "showNotStartedActive": 0,   # 0 = 不含未开始的
        "_": int(time.time() * 1000),  # 防缓存
    }
    r = session.get(
        url,
        params=params,
        headers=headers_for(
            cookies, "mobilelearn.chaoxing.com",
            Referer=f"https://mobilelearn.chaoxing.com/page/active/stuActiveList"
                    f"?courseid={course_id}&clazzid={class_id}&fid={FID}",
        ),
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    return data.get("activeList") or []


def fetch_notice_body(session: requests.Session, cookies: dict,
                      id_code: str, limit: int = 3000) -> str:
    """
    取通知正文。

    真正的正文不在 detail 那个 HTML 页面里（那是个 SPA 外壳，任何 idCode 都返回
    同一份 109KB 的空壳），而在 getNoticeDetail 这个 JSON 接口里。
    """
    if not id_code or not re.fullmatch(r"[0-9A-Fa-f]{16,}", id_code):
        return ""
    url = f"https://notice.chaoxing.com/pc/course/notice/{id_code}/getNoticeDetail"
    try:
        r = session.get(
            url,
            params={"sendTag": 0, "_": int(time.time() * 1000)},
            headers=headers_for(
                cookies, "notice.chaoxing.com",
                Referer="https://notice.chaoxing.com/",
                **{"X-Requested-With": "XMLHttpRequest"},
            ),
            timeout=25,
        )
        msg = (r.json() or {}).get("msg") or {}
        if not isinstance(msg, dict):
            return ""
        return (msg.get("content") or "").strip()[:limit]
    except Exception:
        return ""      # 正文拿不到不影响主流程，退化成只有标题的通知


# ---------------------------------------------------------------- 截止时间解析

# 常见写法：2026年1月12日 / 1月12日 / 11/30 / 11-30 / 11.30
RE_FULL_DATE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")
RE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?(?!\d)")

# 强截止信号：出现就基本可以确定这句在讲截止时间
RE_STRONG = re.compile(r"截止|到期|最后期限|过期|ddl", re.I)
# "…之前 提交" / "…前 完成" 这类"日期 + 动作"组合。
# 单独一个"之前"不算数 —— "在观看视频任务点之前可以先自学"不是截止时间。
RE_VERB_BEFORE = re.compile(
    r"(?:之前|以前|前)\s{0,4}(?:提交|上传|上交|完成|发送|发至|作答|填写|填报|报名|打卡)"
    r"|(?:提交|上传|上交|完成|发送|作答|填写|填报|报名|打卡)[^。；;\n]{0,10}(?:之前|以前)")
# 排课特征词：用来排除"4月23日…上实验课""地点教三二"这类课表信息
RE_CLASS_TALK = re.compile(r"上课|授课|开课|下课|地点|教室|实验室|开始|课程时间")
# 只有紧挨着这些词，"今天/今晚"才算截止信号
RE_TODAY_BOUND = re.compile(r"(今日|今天|今晚|本日)\s*(截止|结束|到期|前|24点|24:00)")


def _extract_date(sent: str, ref: datetime):
    """从一句话里抠出日期。ref 用来推断没写年份的日期属于哪一年。"""
    m = RE_FULL_DATE.search(sent)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=CST).strftime("%Y-%m-%d")
        except ValueError:
            return None

    if RE_TODAY_BOUND.search(sent):
        return ref.strftime("%Y-%m-%d")

    m = RE_MD.search(sent)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        # 年份推断：先试发布那年；若该日期落在发布日一个月以前，
        # 说明讲的是下一年（例：12月发的通知说"1月5日截止"）
        for year in (ref.year, ref.year + 1):
            try:
                cand = datetime(year, mo, d, tzinfo=CST)
            except ValueError:
                return None
            if cand >= ref - timedelta(days=30):
                return cand.strftime("%Y-%m-%d")
    return None


def parse_deadline(text: str, ref: datetime):
    """
    从文本里找出真正在讲截止时间的那句话，抽出日期。

    精度优先 —— 宁可漏判，也不要把"4月23日上实验课"这种排课信息当成作业截止。
    漏判的后果是那条通知没有截止时间；误判的后果是日历上多一个假 DDL。

    ref 用活动发布时间而不是"今天"：通知是过去发的，用发布时间推年份准得多。
    返回 (date_str, "text") 或 (None, None)
    """
    if not text:
        return None, None

    for sent in re.split(r"[。；;\n]", text):
        sent = sent.strip()
        if not sent:
            continue
        # 这句话里得先有个日期（或"今晚"这种相对说法）
        if not (RE_FULL_DATE.search(sent) or RE_MD.search(sent)
                or RE_TODAY_BOUND.search(sent)):
            continue

        strong = bool(RE_STRONG.search(sent))
        if not (strong or RE_VERB_BEFORE.search(sent)
                or RE_TODAY_BOUND.search(sent)):
            continue
        # 明显是在讲排课、又没有"截止"字样 → 不是截止时间
        if RE_CLASS_TALK.search(sent) and not strong:
            continue

        got = _extract_date(sent, ref)
        if got:
            return got, "text"

    return None, None


# ---------------------------------------------------------------- 归一化

def ts_to_iso(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, CST).isoformat()
    except (ValueError, OSError, TypeError):
        return None


def normalize(raw: dict, course: dict) -> dict:
    """把接口返回的一条活动整理成干净结构（此时还没有截止时间）"""
    try:
        meta = json.loads(raw.get("content") or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    extra = raw.get("extraInfo") or {}

    return {
        "id": raw.get("id"),
        "courseId": course["courseId"],
        "classId": course["classId"],
        "courseName": course["name"],
        "type": raw.get("activeType"),
        "typeName": TYPE_NAMES.get(raw.get("activeType"), f'type{raw.get("activeType")}'),
        "title": (raw.get("nameOne") or "").strip(),
        "author": meta.get("creatorRealName", ""),
        "startAt": ts_to_iso(raw.get("startTime")),
        "endAt": ts_to_iso(raw.get("endTime")),
        "read": raw.get("isLook") == 1,
        "noticeId": extra.get("noticeId"),
        # idCode 在 content 里，不在 extraInfo 里
        "idCode": meta.get("idCode") or extra.get("idCode"),
        "body": "",
        "deadline": None,
        "deadlineSource": None,
    }


def resolve_deadline(rec: dict) -> None:
    """
    就地填 rec 的 deadline / deadlineSource。

    顺序：
      1. 从标题+正文里抽 —— 老师真正写的截止时间在这里，最可信
      2. 退回到接口的 endTime，但只有它不是"24小时通知有效期"时才算数
    """
    start = (datetime.fromisoformat(rec["startAt"]) if rec["startAt"]
             else datetime.now(CST))

    found, src = parse_deadline(f'{rec["title"]}\n{rec.get("body") or ""}', start)
    if found:
        rec["deadline"], rec["deadlineSource"] = found, src
        return

    if rec["endAt"]:
        end = datetime.fromisoformat(rec["endAt"])
        gap_h = (end - start).total_seconds() / 3600
        # 作业的 endTime 就是交作业窗口，直接信；其它类型要排除掉 24h 有效期
        if rec["type"] == 35 or gap_h > EXPIRY_HOURS:
            rec["deadline"], rec["deadlineSource"] = rec["endAt"][:10], "endTime"


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-new", action="store_true",
                    help="只保留最近 30 天的条目")
    ap.add_argument("--delay", type=float, default=0.35,
                    help="每次请求之间的间隔秒数（默认 0.35，别调太小）")
    ap.add_argument("--no-body", action="store_true",
                    help="跳过通知正文抓取（快很多，但截止时间会漏）")
    args = ap.parse_args()

    cookies = load_cookies()
    session = make_session(cookies)

    print("[1/2] 拉取课程列表…")
    courses = fetch_courses(session, cookies)
    print(f"      {len(courses)} 门课程")

    print(f"[2/2] 逐门课拉取活动 + 通知正文（间隔 {args.delay}s）…")
    activities, failed = [], []
    for i, c in enumerate(courses, 1):
        try:
            raw_list = fetch_activities(session, cookies, c["courseId"], c["classId"])
        except Exception as e:                       # 单门课失败不影响整体
            failed.append({"course": c["name"], "courseId": c["courseId"], "error": str(e)[:120]})
            continue
        for raw in raw_list:
            if raw.get("activeType") in IGNORED_TYPES:
                continue
            rec = normalize(raw, c)
            if rec["idCode"] and not args.no_body:
                rec["body"] = fetch_notice_body(session, cookies, rec["idCode"])
                time.sleep(args.delay)
            resolve_deadline(rec)
            activities.append(rec)
        if i % 10 == 0:
            print(f"      {i}/{len(courses)} …")
        time.sleep(args.delay)

    # 去重（同一活动可能出现在多门课里）+ 按发布时间倒序
    dedup = {}
    for a in activities:
        dedup[a["id"]] = a
    activities = sorted(dedup.values(), key=lambda x: x["startAt"] or "", reverse=True)

    if args.only_new:
        # 保留：最近 30 天发出来的，或者截止时间还没过的。
        # 后半句不能少 —— 否则一个半月前发的通知，如果截止日期在将来，会被误删。
        now = datetime.now(CST)
        cutoff = (now - timedelta(days=30)).isoformat()
        today = now.strftime("%Y-%m-%d")
        activities = [a for a in activities
                      if (a["startAt"] or "") >= cutoff
                      or (a["deadline"] or "") >= today]

    notices = [a for a in activities if a["type"] == 45]
    with_deadline = [a for a in activities if a["deadline"]]
    by_source = {}
    for a in activities:
        by_source[a["deadlineSource"] or "none"] = \
            by_source.get(a["deadlineSource"] or "none", 0) + 1

    result = {
        "collectedAt": datetime.now(CST).isoformat(),
        "fid": FID,
        "stats": {
            "courses": len(courses),
            "activities": len(activities),
            "notices": len(notices),
            "withDeadline": len(with_deadline),
            "withBody": len([a for a in activities if a["body"]]),
            "deadlineSource": by_source,
            "unread": len([a for a in activities if not a["read"]]),
            "failedCourses": len(failed),
        },
        "courses": courses,
        "activities": activities,
    }

    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / "activities.json"
    out_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print("\n完成 ——", out_file)
    print(f"  活动 {len(activities)} 条（通知 {len(notices)} 条，"
          f"有正文 {result['stats']['withBody']} 条，未读 "
          f"{result['stats']['unread']} 条）")
    print(f"  截止时间 {len(with_deadline)} 条，来源：{by_source}")
    if failed:
        print(f"  ⚠ {len(failed)} 门课失败：")
        for f in failed[:5]:
            print(f"    - {f['course']}: {f['error']}")


if __name__ == "__main__":
    main()
