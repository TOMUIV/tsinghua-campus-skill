"""library.py — 图书馆综合查询子 SKILL 统一入口

整合清华大学图书馆多个子系统：
- seat.lib（座位）：实时余量（公开）+ 座位分布（公开）+ 我的预约记录（登录）
- cab.lib（研读间/研讨间）：空间占用状态（登录）

用法:
  library.py seat [--area 北馆]      # 座位余量（公开，无需登录）
  library.py areas [--area 北馆]     # 座位分布：馆→楼层→区域→总/不可用/剩余（公开）
  library.py my-bookings            # 我的座位预约记录（需登录）
  library.py rooms [--space 北馆单人研读间]  # 研读间占用状态（需登录）
"""
import sys
import os
import json
import time
import re
import argparse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "base-cas", "scripts"))
import common
import browser
import login

SEAT_URL = "https://seat.lib.tsinghua.edu.cn"
CAB_URL = "https://cab.lib.tsinghua.edu.cn"

# 馆区 id 映射
SEAT_AREAS = {
    "35": "北馆(李文正馆)", "64": "西馆(逸夫馆)", "89": "文科图书馆",
    "6": "法律图书馆", "19": "美术图书馆", "29": "金融图书馆",
}

# 研读间空间列表（cab）
CAB_SPACES = [
    "北馆单人研读间（三层）",
    "北馆团体研讨间（二层）",
    "西馆高山音乐研讨间（中208）",
    "西馆流水音乐研讨间（中210）",
    "文科馆单人研读间（三层）",
    "文科馆团体研讨间（二层）",
    "法律馆单人研读间（四层）",
    "法律馆研讨舱（四层、五层)",
    "法律馆双人舱（五层)",
]


def _iframe_cas_login(page, user, pwd, target_domain):
    """处理 iframe 内嵌 CAS 登录（seat/cab 都是 iframe CAS）。

    返回 True 若登录成功。
    """
    for i in range(15):
        time.sleep(3)
        for fr in page.frames:
            try:
                if "id.tsinghua" in fr.url:
                    for k in range(8):
                        try:
                            if fr.evaluate("() => typeof window.doLogin === 'function'"):
                                break
                        except Exception:
                            pass
                        time.sleep(2)
                    fr.fill("#i_user", user)
                    fr.fill("#i_pass", pwd)
                    fr.evaluate("doLogin()")
                    common.log(f"[library] CAS 已填表（frame）")
                    break
            except Exception:
                pass
        try:
            body = page.inner_text("body")
            if target_domain in page.url and "用户密码登录" not in body:
                return True
        except Exception:
            pass
    return False


# ---------- seat 余量 ----------
def _parse_seat_areas(page):
    return page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('div.rooms').forEach(r => {
            const b = [...r.querySelectorAll('div.ceng.nowap.zh b')].map(x => (x.innerText||'').trim());
            const img = r.querySelector('img[src*="area/"]');
            let area_id = '';
            if (img) {
                const m = (img.getAttribute('src')||'').match(/area\\/(\\d+)/);
                if (m) area_id = m[1];
            }
            if (b.length >= 2) {
                const m = b[1].match(/今日剩余(\\d+)，总量(\\d+)/);
                out.push({name: b[0], area_id, remaining: m ? parseInt(m[1]) : null, total: m ? parseInt(m[2]) : null});
            }
        });
        return out;
    }""")


def cmd_seat(area_filter=""):
    browser.start_cdp(headed=False)
    pw, b, ctx, page = browser.connect_cdp()
    page.on("dialog", lambda d: d.accept())
    try:
        page.goto(SEAT_URL + "/home/web/f_second", wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)
        areas = _parse_seat_areas(page)
        if not areas:
            common.output_json({"status": "error", "message": "未解析到座位数据"})
            sys.exit(1)
        if area_filter:
            areas = [a for a in areas if area_filter in a["name"]]
        common.output_json({"status": "ok", "type": "seat", "areas": areas})
    finally:
        try:
            browser.stop_cdp()
        except Exception:
            pass


def _fetch_v3areas(area_id):
    """公开 API 查馆区楼层/区域分布。"""
    try:
        r = urllib.request.urlopen(f"{SEAT_URL}/api.php/v3areas/{area_id}", timeout=15)
        d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    if d.get("status") != 1:
        return None
    data = d["data"]["list"]
    ai = data["areaInfo"]
    floors = []
    for f in data.get("childArea", []):
        total = f.get("TotalCount", 0) or 0
        un = f.get("UnavailableSpace", 0) or 0
        regions = []
        # 区域子节点（若有）
        if f.get("id") and f.get("id") != ai.get("id"):
            pass
        item = {"floor": f.get("name"), "id": f.get("id"),
                "total": total, "unavailable": un, "remaining": total - un}
        if total == 0:
            item["note"] = "0 为接口占位，不代表无座，请以 seat 实时余量为准"
        floors.append(item)
    return {"name": ai.get("name"), "id": ai.get("id"), "floors": floors}


def cmd_areas(area_filter=""):
    """座位分布：馆→楼层→区域→总/不可用/剩余（公开 API，无需登录）。"""
    ids = list(SEAT_AREAS.keys())
    if area_filter:
        ids = [aid for aid, name in SEAT_AREAS.items() if area_filter in name]
        if not ids:
            common.output_json({"status": "ok", "message": f"未找到馆区 {area_filter}", "areas": []})
            sys.exit(0)
    result = []
    for aid in ids:
        info = _fetch_v3areas(aid)
        if info:
            result.append(info)
    common.output_json({"status": "ok", "type": "areas", "areas": result})


# ---------- seat 我的预约 ----------
def _parse_my_bookings(page):
    return page.evaluate("""() => {
        const out = {bookings: []};
        document.querySelectorAll('table').forEach(t => {
            const rows = [...t.querySelectorAll('tr')].map(tr =>
                [...tr.querySelectorAll('td,th')].map(td => (td.innerText||'').trim()));
            if (rows.length > 1 && rows[0].join('|').includes('预约号')) {
                rows.slice(1).forEach(r => {
                    if (r.length >= 6 && r[0]) out.bookings.push({
                        no: r[0], space: r[1], start: r[2], end: r[3], status: r[4]
                    });
                });
            }
        });
        return out;
    }""")


def cmd_my_bookings():
    user = login._get_cred("cas_username")
    pwd = login._get_cred("cas_password")
    if not user or not pwd:
        common.output_json({"status": "error", "message": "CAS 凭据未配置"})
        sys.exit(1)
    browser.start_cdp(headed=False)
    pw, b, ctx, page = browser.connect_cdp()
    page.on("dialog", lambda d: d.accept())
    try:
        page.goto(SEAT_URL + "/home/web/f_second", wait_until="load", timeout=45000)
        time.sleep(5)
        # 点登录
        page.evaluate("() => { const a = document.querySelector('a.login_click'); if (a) a.click(); }")
        time.sleep(5)
        if not _iframe_cas_login(page, user, pwd, "seat.lib"):
            common.output_json({"status": "error", "message": "座位系统登录失败"})
            sys.exit(1)
        common.log("[library] seat 登录成功")
        page.goto(SEAT_URL + "/user/index/book", wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)
        data = _parse_my_bookings(page)
        common.output_json({"status": "ok", "type": "my_bookings", **data})
    finally:
        try:
            browser.stop_cdp()
        except Exception:
            pass


# ---------- cab 研读间 ----------
def _cab_login(page, user, pwd):
    """cab 登录（Vue SPA，iframe CAS）。

    注意：cab 是 Vue SPA，body 在 Vue 挂载前为空。不能复用 _iframe_cas_login
    （它只检查"无 CAS 文案"会误判登录成功）。需等待 Vue ready：
      - 已登录（信任浏览器）→ body 出现"个人中心"
      - 未登录 → Vue 注入 CAS iframe → 填表
    """
    page.goto(CAB_URL, wait_until="domcontentloaded", timeout=45000)
    filled = False
    for i in range(20):
        time.sleep(3)
        try:
            body = page.inner_text("body")
        except Exception:
            body = ""
        has_login_ui = "个人中心" in body
        # 处理 CAS frame（未登录时 Vue 注入）
        for fr in page.frames:
            try:
                if "id.tsinghua" in fr.url and not filled:
                    for k in range(8):
                        try:
                            if fr.evaluate("() => typeof window.doLogin === 'function'"):
                                break
                        except Exception:
                            pass
                        time.sleep(2)
                    try:
                        fr.fill("#i_user", user)
                        fr.fill("#i_pass", pwd)
                        fr.evaluate("doLogin()")
                        filled = True
                        common.log("[library] cab CAS 已填表")
                    except Exception:
                        pass
                    break
            except Exception:
                pass
        # 已登录判断：Vue ready 且出现"个人中心"
        if has_login_ui and "用户密码登录" not in body:
            return True
    return False


def _click_space(page, space_name):
    """真实点击空间（Vue SPA 路由跳转）。"""
    try:
        page.click(f"text={space_name}", timeout=8000)
        return True
    except Exception:
        pass
    try:
        page.click(f"text={space_name}（三层）", timeout=5000)
        return True
    except Exception:
        return False


def _parse_rooms(page):
    """解析研读间占用状态（从"预约状态"标记后开始，提取房间+占用者）。"""
    body = page.inner_text("body")
    idx = body.find("预约状态")
    if idx >= 0:
        body = body[idx:]
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    rooms = []
    cur_room = None
    for l in lines:
        # 房间行：如 "北馆3F-01  (北馆单人间(三层))"
        if re.match(r"^[\u5317\u897f\u6587\u6cd5]\u9986?3?F?\d", l) or re.match(r"^[\u5317\u897f\u6587\u6cd5][\u4e2d\u5317\u897f\u6587\u6cd5]*3F", l):
            if cur_room:
                rooms.append(cur_room)
            cur_room = {"room": l.split("(")[0].strip(), "occupied": []}
        elif cur_room and l and len(l) <= 5 and not l.isdigit() and ":" not in l and not l.startswith("20") and l not in (
                "周一", "周二", "周三", "周四", "周五", "周六", "周日", "今日", "上周", "下周", "日期", "名称",
                "预约状态", "预约须知", "实景展示", "楼层筛选", "名称筛选", "已预约", "非开放预约时段"):
            # 占用者姓名（如 程*）
            if any('\u4e00' <= ch <= '\u9fff' for ch in l):
                cur_room["occupied"].append(l)
    if cur_room:
        rooms.append(cur_room)
    return rooms


def cmd_rooms(space_filter=""):
    user = login._get_cred("cas_username")
    pwd = login._get_cred("cas_password")
    if not user or not pwd:
        common.output_json({"status": "error", "message": "CAS 凭据未配置"})
        sys.exit(1)
    browser.start_cdp(headed=False)
    pw, b, ctx, page = browser.connect_cdp()
    page.on("dialog", lambda d: d.accept())
    try:
        if not _cab_login(page, user, pwd):
            common.output_json({"status": "error", "message": "研读间系统登录失败"})
            sys.exit(1)
        common.log("[library] cab 登录成功")
        # 默认用北馆单人研读间；space_filter 模糊匹配
        space = space_filter or "北馆单人研读间"
        clicked = _click_space(page, space)
        if not clicked:
            common.output_json({"status": "error", "message": f"未找到空间 {space}"})
            sys.exit(1)
        time.sleep(15)
        rooms = _parse_rooms(page)
        common.output_json({"status": "ok", "type": "rooms", "space": space, "rooms": rooms})
    finally:
        try:
            browser.stop_cdp()
        except Exception:
            pass


# ---------- 座位预约（book/cancel）----------
def _seat_login(page, user, pwd):
    """座位系统登录（iframe CAS）。返回 True 若成功。"""
    page.goto(SEAT_URL + "/home/web/f_second", wait_until="load", timeout=45000)
    time.sleep(5)
    page.evaluate("() => { const a = document.querySelector('a.login_click'); if (a) a.click(); }")
    time.sleep(5)
    return _iframe_cas_login(page, user, pwd, "seat.lib")


def _seat_lookup(page, area_id, day="today"):
    """查馆区楼层/区域树。返回楼层/区域列表。"""
    d = page.evaluate("""async (aid) => {
        const r = await fetch('/api.php/v3areas/' + aid, {credentials: 'include'});
        return await r.json();
    }""", area_id)
    if not d or d.get("status") != 1:
        return []
    data = d["data"]["list"]
    floors = []
    for f in data.get("childArea", []):
        total = f.get("TotalCount", 0) or 0
        un = f.get("UnavailableSpace", 0) or 0
        item = {"id": f.get("id"), "name": f.get("name"), "total": total,
                "unavailable": un, "remaining": total - un}
        if total == 0:
            item["note"] = "0 为接口占位，不代表无座，请以 seat 实时余量为准"
        floors.append(item)
    return floors


def _seat_time_buckets(page, area_id, day):
    """查某区域可预约时间段。返回时段列表（segment id + start/end）。"""
    d = page.evaluate("""async (args) => {
        const r = await fetch('/api.php/space_time_buckets?area=' + args.area + '&day=' + args.day, {credentials: 'include'});
        return await r.json();
    }""", {"area": area_id, "day": day})
    if not d or d.get("status") != 1:
        return []
    lst = d["data"]["list"] or []
    out = []
    for item in lst:
        seg = item.get("id") or item.get("bookTimeId")
        if seg:
            out.append({"segment": seg, "spaceName": item.get("spaceName"),
                        "startTime": item.get("startTime"), "endTime": item.get("endTime"),
                        "status": item.get("status")})
    return out


def _seat_spaces(page, area_id, segment, day, start, end):
    """查某时段可选座位列表（spaces_old）。"""
    d = page.evaluate("""async (args) => {
        const q = 'area=' + args.area + '&segment=' + args.segment + '&day=' + args.day
                + '&startTime=' + args.start + '&endTime=' + args.end;
        const r = await fetch('/api.php/spaces_old?' + q, {credentials: 'include'});
        return await r.json();
    }""", {"area": area_id, "segment": segment, "day": day, "start": start, "end": end})
    if not d or d.get("status") != 1:
        return []
    lst = d["data"]["list"] or []
    out = []
    for item in lst:
        out.append({"id": item.get("id"), "no": item.get("no"),
                    "status": item.get("status"),
                    "x": item.get("point_x"), "y": item.get("point_y")})
    return out


SEAT_STATUS = {1: "可选", 2: "已预约", 6: "使用中", 7: "暂离", 3: "关闭", 4: "关闭", 5: "关闭"}

# 座位系统开放窗口：每日 6:00-23:00（可预约当日/次日）
OPEN_START_HOUR = 6
OPEN_END_HOUR = 23


def _check_open_hours(now=None):
    """校验当前是否在座位系统开放窗口（6:00-23:00）。

    窗口外 → 返回说明文字（调用方应直接报错退出，不启动浏览器/不登录）；
    窗口内 → 返回 None。
    now 可注入用于单测。
    """
    import datetime
    now = now or datetime.datetime.now()
    hour = now.hour
    if hour < OPEN_START_HOUR or hour >= OPEN_END_HOUR:
        return (f"座位系统开放时间为每日 6:00-23:00（可预约当日/次日座位），"
                f"当前 {now.strftime('%H:%M')} 不在开放窗口内，无法预约。"
                f"请 {OPEN_START_HOUR}:00 后再试（届时可预约当日或次日座位）。")
    return None


def cmd_book(area_filter="", floor_filter="", region_filter="", seat_no="", confirm=False):
    """座位查找 + 预约。

    流程：
      1. 登录 seat
      2. 查馆区楼层/区域树（v3areas）
      3. 若指定区域 → 查时间段（space_time_buckets）→ 查可选座位（spaces_old）
      4. 若指定座位 → 预约（需 --confirm 才真正执行；否则仅预览）

    ⚠️ 写操作保护：预约是真实写操作，必须带 --confirm 才执行。AI 应先在对话里
    向用户 double check（座位号/时间段/规则提醒），用户确认后再带 --confirm 执行。
    """
    # 开放窗口校验：窗口外直接报错退出，不启动浏览器/不登录
    early = _check_open_hours()
    if early:
        common.output_json({"status": "error", "type": "book_out_of_window", "message": early})
        sys.exit(1)
    user = login._get_cred("cas_username")
    pwd = login._get_cred("cas_password")
    if not user or not pwd:
        common.output_json({"status": "error", "message": "CAS 凭据未配置"})
        sys.exit(1)
    browser.start_cdp(headed=False)
    pw, b, ctx, page = browser.connect_cdp()
    page.on("dialog", lambda d: d.accept())
    page.set_viewport_size({"width": 1280, "height": 900})
    try:
        if not _seat_login(page, user, pwd):
            common.output_json({"status": "error", "message": "座位系统登录失败"})
            sys.exit(1)
        common.log("[library] seat 登录成功")
        import datetime
        day = datetime.date.today().strftime("%Y-%m-%d")
        # 1. 查馆区
        area_id = None
        for aid, name in SEAT_AREAS.items():
            if not area_filter or area_filter in name:
                area_id = aid
                break
        if not area_id:
            common.output_json({"status": "error", "message": f"未找到馆区 {area_filter}"})
            sys.exit(1)
        floors = _seat_lookup(page, area_id)
        if not floors:
            common.output_json({"status": "error", "message": "馆区无楼层数据"})
            sys.exit(1)
        # 2. 选楼层 → 区域
        floor_id = None
        if floor_filter:
            for f in floors:
                if floor_filter in f["name"]:
                    floor_id = f["id"]
                    break
        # 若未指定楼层，输出分布并结束
        if not floor_id:
            common.output_json({"status": "ok", "type": "book_floors",
                                "area": SEAT_AREAS[area_id], "area_id": area_id,
                                "floors": floors,
                                "message": "请指定 --floor 楼层继续（如 --floor 二层）"})
            sys.exit(0)
        # 查楼层的区域（v3areas/<floor_id>）
        fd = page.evaluate("""async (fid) => {
            const r = await fetch('/api.php/v3areas/' + fid, {credentials: 'include'});
            return await r.json();
        }""", floor_id)
        regions = []
        if fd and fd.get("status") == 1 and fd["data"]["list"].get("childArea"):
            for r in fd["data"]["list"]["childArea"]:
                total = r.get("TotalCount", 0) or 0
                un = r.get("UnavailableSpace", 0) or 0
                regions.append({"id": r.get("id"), "name": r.get("name"),
                                "total": total, "unavailable": un, "remaining": total - un})
        # 选区域
        region_id = None
        if region_filter:
            for r in regions:
                if region_filter in r["name"]:
                    region_id = r["id"]
                    break
        if not region_id:
            common.output_json({"status": "ok", "type": "book_regions",
                                "area": SEAT_AREAS[area_id], "floor": floor_filter,
                                "regions": regions,
                                "message": "请指定 --region 区域（如 --region A）"})
            sys.exit(0)
        # 3. 查时间段
        segments = _seat_time_buckets(page, region_id, day)
        if not segments:
            common.output_json({"status": "ok", "type": "book_segments",
                                "region": region_filter, "segments": [],
                                "message": "该区域当前无可用时间段（当日可约时段已过或该区域今日不可约）。可改选其他区域/楼层（用 library.py areas 查分布），或次日 6:00 后预约当日座位。"})
            sys.exit(0)
        seg = segments[0]
        # 4. 查可选座位
        spaces = _seat_spaces(page, region_id, seg["segment"], day, seg["startTime"], seg["endTime"])
        avail = [s for s in spaces if s["status"] == 1]
        if not seat_no:
            common.output_json({"status": "ok", "type": "book_seats",
                                "region": region_filter, "segment": seg,
                                "total": len(spaces), "available": avail,
                                "message": "可选座位如上，请指定 --seat NF2A001 预约"})
            sys.exit(0)
        # 5. 预约指定座位
        target = None
        for s in spaces:
            if s["no"] == seat_no or str(s["id"]) == seat_no:
                target = s
                break
        if not target:
            common.output_json({"status": "error", "message": f"未找到座位 {seat_no}"})
            sys.exit(1)
        if target["status"] != 1:
            common.output_json({"status": "error", "message": f"座位 {seat_no} 不可预约（状态 {SEAT_STATUS.get(target['status'])}）"})
            sys.exit(1)
        # ⚠️ 写操作确认门禁：必须带 --confirm 才真正预约
        if not confirm:
            common.output_json({
                "status": "ok", "type": "book_preview",
                "action": "book",
                "seat": seat_no, "spaceId": target["id"],
                "segment": seg["segment"],
                "day": day, "startTime": seg["startTime"], "endTime": seg["endTime"],
                "remind": "预约是写操作。请先向用户 double check：确认预约该座位和时间段？并提醒：预约后 30 分钟内必须到场签到（未签到释放座位+记违规1次）；取消预约每日限1次；违规满5次暂停3天。用户确认后，带 --confirm 重新执行。",
                "confirm_required": True,
            })
            sys.exit(0)
        # 直接调 book API（比 DOM 点击更稳）
        d = page.evaluate("""async (args) => {
            const form = new URLSearchParams();
            form.append('access_token', ska.access_token);
            form.append('userid', ska.userid);
            form.append('segment', args.segment);
            form.append('type', '1');
            form.append('operateChannel', '2');
            const r = await fetch('/api.php/spaces/' + args.spaceId + '/book', {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: form.toString()
            });
            return await r.json();
        }""", {"spaceId": target["id"], "segment": seg["segment"]})
        common.output_json({"status": "ok", "type": "book_result",
                            "seat": seat_no, "spaceId": target["id"],
                            "segment": seg["segment"],
                            "day": day, "startTime": seg["startTime"], "endTime": seg["endTime"],
                            "book_response": d,
                            "remind": "预约成功后 30 分钟内必须到场签到（未签到释放座位+记违规1次）；取消预约每日限1次；违规满5次暂停3天。请提醒用户确认时间安排。"})
    finally:
        try:
            browser.stop_cdp()
        except Exception:
            pass


def cmd_cancel(booking_id="", userid="", access_token="", confirm=False):
    """取消座位预约。

    参数：--id 预约 id（我的预约列表里的预约号或内部 id）。
    若不指定 id → 列出当前有效预约。
    ⚠️ 写操作保护：取消是真实写操作（且取消每日限 1 次），必须带 --confirm 才执行；
    否则仅预览将取消的预约。AI 应先在对话里向用户 double check。
    """
    user = login._get_cred("cas_username")
    pwd = login._get_cred("cas_password")
    if not user or not pwd:
        common.output_json({"status": "error", "message": "CAS 凭据未配置"})
        sys.exit(1)
    browser.start_cdp(headed=False)
    pw, b, ctx, page = browser.connect_cdp()
    page.on("dialog", lambda d: d.accept())
    page.set_viewport_size({"width": 1280, "height": 900})
    try:
        if not _seat_login(page, user, pwd):
            common.output_json({"status": "error", "message": "座位系统登录失败"})
            sys.exit(1)
        common.log("[library] seat 登录成功")
        # 我的预约列表
        page.goto(SEAT_URL + "/user/index/book", wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)
        # 找可取消的预约（含"取消"按钮，状态为预约成功/提醒）
        rows = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('tr').forEach(tr => {
                const tds = [...tr.querySelectorAll('td')].map(td => (td.innerText||'').trim());
                if (tds.length >= 5 && /^\\d{12}$/.test(tds[0])) {
                    const del = tr.querySelector('a[onclick*="menuDel"]');
                    out.push({
                        no: tds[0], space: tds[1], start: tds[2], end: tds[3], status: tds[4],
                        cancelId: del ? (del.getAttribute('onclick').match(/\\d+/g)||[]).pop() : null
                    });
                }
            });
            return out;
        }""")
        cancellable = [r for r in rows if r["cancelId"]]
        if not booking_id:
            common.output_json({"status": "ok", "type": "cancel_list",
                                "bookings": rows, "cancellable": cancellable,
                                "message": "请指定 --id <预约内部id> 取消（cancellable 里的 cancelId）"})
            sys.exit(0)
        # 取消指定预约
        # 参数可能是预约号或 cancelId
        cancel_id = booking_id
        for r in rows:
            if r["no"] == booking_id:
                cancel_id = r["cancelId"]
                break
        if not cancel_id:
            common.output_json({"status": "error", "message": f"未找到可取消的预约 {booking_id}"})
            sys.exit(1)
        # 从页面 JS 提取 access_token + userid（我的预约页无 ska，token 在 menuDel 硬编码）
        token = page.evaluate("""() => {
            const scripts = [...document.querySelectorAll('script')].map(s => s.textContent || '').join('');
            const m = scripts.match(/access_token['":\\s]+([a-f0-9]{32})/i);
            const u = scripts.match(/userid['":\\s]+([0-9]{8,10})/i);
            return {token: m ? m[1] : '', userid: u ? u[1] : ''};
        }""")
        if not token.get("token"):
            common.output_json({"status": "error", "message": "无法获取 access_token（我的预约页无 token）"})
            sys.exit(1)
        uid = token["userid"] or "2025013187"
        # ⚠️ 写操作确认门禁：必须带 --confirm 才真正取消
        if not confirm:
            common.output_json({
                "status": "ok", "type": "cancel_preview",
                "action": "cancel",
                "booking": booking_id, "cancel_id": cancel_id,
                "remind": "取消是写操作，且【取消预约每日限1次】。请先向用户 double check：确认取消该预约？用户确认后，带 --confirm 重新执行。",
                "confirm_required": True,
            })
            sys.exit(0)
        d = page.evaluate("""async (args) => {
            const form = new URLSearchParams();
            form.append('_method', 'delete');
            form.append('id', args.id);
            form.append('userid', args.userid);
            form.append('access_token', args.token);
            form.append('operateChannel', '2');
            const r = await fetch('/api.php/profile/books/' + args.id, {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: form.toString()
            });
            return await r.json();
        }""", {"id": cancel_id, "userid": uid, "token": token["token"]})
        common.output_json({"status": "ok", "type": "cancel_result",
                            "booking": booking_id, "cancel_id": cancel_id,
                            "cancel_response": d})
    finally:
        try:
            browser.stop_cdp()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="图书馆综合查询")
    ap.add_argument("cmd", choices=["seat", "areas", "my-bookings", "rooms", "book", "cancel"])
    ap.add_argument("--area", default="", help="seat/areas/book: 馆区筛选")
    ap.add_argument("--floor", default="", help="book: 楼层筛选")
    ap.add_argument("--region", default="", help="book: 区域筛选")
    ap.add_argument("--seat", default="", help="book: 座位号（如 NF2A001）")
    ap.add_argument("--id", default="", help="cancel: 预约内部id")
    ap.add_argument("--space", default="", help="rooms: 研读间空间名")
    ap.add_argument("--confirm", action="store_true", help="book/cancel: 确认执行写操作（预约/取消）。不带则仅预览。")
    args = ap.parse_args()
    if args.cmd == "seat":
        cmd_seat(args.area)
    elif args.cmd == "areas":
        cmd_areas(args.area)
    elif args.cmd == "my-bookings":
        cmd_my_bookings()
    elif args.cmd == "rooms":
        cmd_rooms(args.space)
    elif args.cmd == "book":
        cmd_book(args.area, args.floor, args.region, args.seat, args.confirm)
    elif args.cmd == "cancel":
        cmd_cancel(args.id, confirm=args.confirm)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        common.log(f"[library] 未捕获异常: {e}")
        common.output_json({"status": "error", "error": "unexpected", "message": f"脚本异常: {str(e)[:200]}"})
        sys.exit(1)
