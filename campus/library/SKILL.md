---
name: campus-library
description: 清华大学图书馆综合查询。座位余量/座位分布（公开）、我的座位预约记录、研读间/研讨间占用状态。当用户需要"图书馆还有座位吗、座位分布、哪个馆有空位、研读间、自习位置、我的预约"时使用。
metadata:
  openclaw:
    requires:
      env:
        - CAS_PASSWORD
    os:
      - windows
      - macos
      - linux
---

# 图书馆

清华大学图书馆综合查询，整合多个子系统：
- **座位系统**（seat.lib）：实时余量（公开）+ 座位分布（公开）+ 我的预约记录（登录）
- **研读间/研讨间**（cab.lib）：空间占用状态（登录）

## 如果你是 AI，请阅读以下内容

### 铁律

- **铁律 1：AI 运行所有脚本**。禁止让用户敲命令。
- **铁律 2：脚本面向 AI**。stdout 输出 JSON，进度写 `runtime/logs/campus.log`，不写 stderr。
- **铁律 3：验证码两阶段**。seat/cab 登录走 iframe 内嵌 CAS（信任浏览器免 2FA，通常无验证码）。
- **铁律 4：全程无头 + 即用即退**。base-cas 一律 headless 运行。浏览器用完即关，保留 session cookie 文件 + profile 指纹；仅 2FA 登录流程内保持打开。
- **铁律 5：隐私红线**。研读间占用者姓名含个人信息，面向用户输出时脱敏（系统默认 `程*` 格式，保留）。

### 使用

```
library.py seat [--area 北馆]         # 座位余量（公开，无需登录）
library.py areas [--area 北馆]        # 座位分布：馆→楼层→区域→总/不可用/剩余（公开）
library.py my-bookings               # 我的座位预约记录（需登录）
library.py rooms [--space 空间名]      # 研读间占用状态（需登录）
library.py book --area 北馆 [--floor 二层] [--region A] [--seat NF2A001]  # 选座+预约（需登录；需 CAS 登录，耗时约 1-3 分钟，shell 超时设 ≥180s）
library.py cancel [--id <预约id>]     # 取消预约（需登录，无 id 列出可取消）
```

输出 JSON：`seat`（各馆余量/总量/馆区id）、`areas`（馆区→楼层座位分布）、`my_bookings`（预约号/空间/起止时间/状态）、`rooms`（房间/占用者）、`book_floors/book_regions/book_seats/book_result`（选座流程）、`cancel_list/cancel_result`（取消）。

### 工作流

```
用户: 图书馆还有座位吗 / 哪个馆有空位
AI:
  1. library.py seat → 各馆实时余量
  2. 汇报（推荐余量多的馆）

用户: 北馆哪个楼层空位多 / 座位分布
AI:
  1. library.py areas --area 北馆 → 楼层/区域分布
  2. 汇报各楼层剩余

用户: 研读间还有空吗 / 哪个研读间可用
AI:
  1. library.py rooms → 空间占用
  2. 列出空房间和被占房间

用户: 帮我预约个北馆二层的座位
AI:
  1. library.py book --area 北馆 --floor 二层 → 看区域分布
  2. library.py book --area 北馆 --floor 二层 --region A → 看可选座位
  3. 选好座位后【必须 double check】向用户确认：
     "确认预约 NF2A001（15:07-17:00）吗？提醒：预约后30分钟内必须到场签到，当日取消仅限1次"
  4. 用户确认 → library.py book ... --seat NF2A001 --confirm → 预约
  5. 汇报预约号 + 提醒签到（30分钟内）
```

### 预约失败排查（AI 必须先判断再回复）

- **`book` 直接返回 `book_out_of_window`（"不在开放窗口内"）** → 脚本内置了 **6:00-23:00 开放窗口校验**（在启动浏览器前拦截）。说明当前是深夜/清晨（23:00-6:00），座位系统不开放预约。此时**不要**再尝试其他参数，直接告知用户下次可约时间（当日 6:00 起，可约当日/次日），或让用户明早再约。
- **`space_time_buckets` 无时段 / 提示"该区域当前无可用时间段"** → 说明**当天该时段已过**或该区域当日不可约（后台只允许预约**当天开放时段**，如 09:00-17:00 过点即空）。**不要**尝试给 book 命令传日期/其他参数——后端次日预约须等**次日当天 06:00 后**才开放，脚本不支持也不应绕过。此时应：
  1. 告知用户"该区域今日可约时段已过，座位系统 6:00 起开放当日预约，需明日再试"
  2. 询问是否改约其他馆区/楼层/区域（用 seat/areas 查实时余量），或明早再来约
- **预约 API 返回"系统开始预约时间为：06:00"** → 说明**不在开放时段**（6:00-23:00 之外，或次日座位尚未到 6:00 开放）。应告知用户当前不可约、明确下一次可约时间点，而不是视为 bug。
- 其他失败（如"座位已约"）→ 换其他可选座位（book_seats 的 available 列表）重试。

### 图书馆基本情况

- 清华大学图书馆（lib.tsinghua.edu.cn）总馆藏 1579 万册图书 + 17 万种电子期刊 + 1525 万篇学位论文 + 949 个数据库
- 6 个分馆：北馆(李文正馆)/西馆(逸夫馆)/文科图书馆/法律图书馆/美术图书馆/金融图书馆
- 座位系统：每日开放 6:00-23:00，可预约当日/次日座位；预约成功后 30 分钟内签到
- 研读间/研讨间系统（cab.lib）：北馆单人研读间/团体研讨间/西馆音乐研讨间/文科馆/法律馆研讨舱等 9 类空间

### 座位分布（实测快照）

| 馆区 | 楼层 | 总座位 | 剩余 |
|------|------|--------|------|
| 北馆(李文正馆) | 二层/三层/四层/五层 | 280/257/240/100 | ~191/199/240/100 |
| 西馆(逸夫馆) | 一层/二层/三层 | 140/492/244 | ~133/439/244 |
| 文科图书馆 | 一层/二层/三层/四层 | 150/138/172/76 | ~124/84/131/66 |
| 法律图书馆 | 二层/三层/四层/五层 | 90/279/260/14 | ~80/262/158/13 |

（具体以 `library.py areas` 实时查询为准）

> ⚠️ `areas` 返回的楼层 `total/remaining` 若为 `0`，是接口占位字段（文科/西馆等馆区顶层 childArea 不汇总），**不代表该楼层无座**，请以 `seat` 实时余量为准。`book` 流程内部会逐区域取真实数。

### 技术链路

- **座位余量**：`seat.lib.tsinghua.edu.cn/home/web/f_second`（**公开，无需登录**），解析 `.rooms` 块（馆名/余量/总量/馆区id）
- **座位分布**：**公开 API** `seat.lib.tsinghua.edu.cn/api.php/v3areas/<馆区id>`（馆→楼层→区域树，含 TotalCount/UnavailableSpace），无需登录。馆区 id：35北馆/64西馆/89文科/6法律/19美术/29金融
- **座位登录**：点"登录"→ **iframe 内嵌 CAS**，遍历 `page.frames` 找 `id.tsinghua` frame → `fr.fill("#i_user")` + `fr.evaluate("doLogin()")`（信任浏览器免 2FA）
- **我的预约**：`seat.lib.tsinghua.edu.cn/user/index/book`（预约号/空间/起止时间/状态）
- **研读间**：`cab.lib.tsinghua.edu.cn`（Vue SPA），iframe CAS 登录 → 首页**真实点击**空间（`page.click("text=空间名")` 触发 Vue 路由）→ `#/ic/researchSpace/...` 占用视图（房间 + 占用者）

> 登录要点：seat/cab 都是 iframe 内嵌 CAS（非整页跳转），必须遍历 `page.frames` 填表。研读间空间点击用真实 `page.click`（Vue SPA 需真实交互）。

### ⚠️ 座位预约铁律（AI 必须遵守，否则用户会吃亏）

- **铁律 A：预约前必须 double check**。执行 `library.py book` 预约前，**必须**向用户确认：
  1. 选定的座位号（如 NF2A001）
  2. 预约时间段（如 15:07-17:00）
  3. 用户确认"确定预约"后才执行 book API。
- **铁律 B：预约后 30 分钟内必须到场**。预约成功后，用户**必须在预约开始后 30 分钟内签到**（刷卡入馆自动签到）。未及时签到 → 座位被释放 + 记违规 1 次。
- **铁律 C：取消预约每日限 1 次**（官方规则第 6 条）。取消前必须告知用户"**当日内只能取消 1 次预约**"，确认后再执行 cancel。
- **铁律 D：违规后果**。违规累计满 5 次 → 暂停座位使用 3 天。**务必在预约前提醒用户"想清楚再约"**，避免爽约。

### 预约规则（官方原文，ska.bookRule）

1. 可预约当日或次日座位；系统开放 6:00-23:00
2. 开馆时段预约当日座位，须预约成功后 **30 分钟内签到**（门禁刷卡自动签到）
3. 未开馆时段预约（含次日），须生效日开馆后 30 分钟内签到
4. 临时离开：选位机刷"临时离开"保留 60 分钟（美术/法律馆就餐时段 90 分钟）
5. 完全离开：选位机刷"完全离开"或出馆刷卡 → 座位释放
6. **取消预约：每日限 1 次**（预约开始前或开始后 30 分钟内可取消）
7. 违规计次满 5 次暂停 3 天；每学期初（3/1、9/1）清零

### 座位预约（已实现 book/cancel）

- **选座流程**（library.py book）：
  1. 查馆区楼层（v3areas/馆区id）→ 指定 --floor
  2. 查楼层区域（v3areas/楼层id）→ 指定 --region
  3. 查时间段（space_time_buckets）→ 可选座位（spaces_old）
  4. 指定 --seat → POST /api.php/spaces/<spaceId>/book 预约
- **预约 API**：`POST /api.php/spaces/<spaceId>/book` + `{access_token, userid, segment, type:1, operateChannel:2}`
- **取消 API**：`POST /api.php/profile/books/<预约id>` + `{_method:delete, id, userid, access_token, operateChannel:2}`
- **关键**：
  - 座位是 **DOM `li.seat`**（非纯 Canvas），`data-no` 定位，status=1 可选
  - `spaces_old`（非 spaces）返回座位列表：坐标+状态（1可选/2已约/6使用中/7暂离/3-5关闭）
  - `access_token` 从 ska 全局（座位页）或页面 JS（我的预约页）提取
  - 区域点击跳转 `/web/seat2/area/<区域id>/day/<日期>`；选时间段跳转 `/web/seat3?area=&segment=&day=&startTime=&endTime=`

### 座位预约 API（逆向笔记，选座待完善）

- 区域树：`/api.php/v3areas/<id>`（公开）
- 日期：`/api.php/v3areadays/<馆区id>`
- 时间段：`/api.php/space_time_buckets?area=<区域id>&day=<日期>` → `spaceName`(如 NF2A001)/`bookTimeId`/`beginTime`/`endTime`/`status`
- 座位列表：`/api.php/spaces?area=<区域>&segment=<时间段>&type=2`（参数待解，返回 null 可能因时段无座）
- 座位图是 **zrender Canvas** 渲染（非 DOM），选座需 Canvas 坐标点击，headless 驱动复杂
- 预约提交：页面 `books(url, query, callback)` POST 函数

### 空间列表（cab 研读间）

北馆单人研读间（三层）/ 北馆团体研讨间（二层）/ 西馆高山音乐研讨间（中208）/ 西馆流水音乐研讨间（中210）/ 文科馆单人研读间（三层）/ 文科馆团体研讨间（二层）/ 法律馆单人研读间（四层）/ 法律馆研讨舱 / 法律馆双人舱

### 边界

- 座位余量/分布公开；预约记录/研读间占用/选座预约需登录（CAS 凭据）。
- **座位预约 book/cancel 已实现**：查楼层/区域/时间段/可选座位 → 预约 → 取消。
- ⚠️ **写操作红线**：book/cancel 是写操作，AI 必须遵守上方"座位预约铁律"（预约前 double check、30 分钟签到、取消每日 1 次、违规暂停 3 天）。
- 取消当日超 1 次 → 系统返回"当日取消次数已达上限"。
- **研读间预约（写操作）未实现**——cab 时间条拖拽选时间（Vue 交互），headless 驱动不稳定；占用查询（rooms）已可用。逆向笔记见 `../docs/library-reverse-notes.md`（预约 API 已定位为 `/ic-web/reserve/bulkAdd`）。
- "我的图书馆"（discover.lib 借阅记录）登录走第三方认证（metaauth/yuntaigo 裸 http，被 CDP Chrome 拦截 `ERR_BLOCKED_BY_CLIENT`），且 CAS casservice 入口 login/check 页在 CDP 下加载失败（chrome-error）。完整攻破笔记见 `../docs/library-reverse-notes.md`。

---

## 如果你是用户，请阅读以下内容

对 AI 说：
- **"图书馆还有座位吗"** / "哪个馆有空位" — 座位余量
- **"座位分布"** / "北馆几层有空位" — 座位分布查询
- **"我的座位预约"** — 预约记录
- **"帮我预约个座位"** — 选座预约（AI 会先跟你确认，并提醒：预约后 30 分钟内到场、当日取消仅 1 次）
- **"取消预约"** — 取消（当日限 1 次，AI 会先提醒）
- **"研读间有空吗"** / "哪个研读间可用" — 研读间占用
