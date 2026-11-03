# 我的工作台

一个自己用的个人工作台，核心是**把学习通的通知和作业截止时间自动捞出来**，
自动变成待办，**在截止之前提醒你**。

```
workbench/
├── index.html                  工作台本体（单文件，含全部样式和逻辑）
├── sw.js / manifest.webmanifest / icon-*.png     PWA，可"添加到主屏幕"当 App
├── collector/
│   ├── collect.py              采集器：学习通 → out/activities.json
│   ├── encrypt.py              加密：activities.json → activities.enc
│   ├── notify.py               提醒：读 JSON → 推微信
│   ├── cookies.json            登录凭据（**已 gitignore，绝不提交**）
│   └── out/
│       ├── activities.json     明文（**已 gitignore**，只在本地/CI 存在）
│       └── activities.enc      密文，跟着仓库走，页面读这个
├── tools/serve_lan.py          局域网预览（白名单，不会漏 cookies.json）
└── .github/workflows/collect.yml   云端每天采集 → 加密 → 提交 → 发提醒
```

---

## 一、它怎么拿到学习通的

不用浏览器自动化，直接调两个接口（都是实测验证过的）：

| 用途 | 接口 | 认证 |
|---|---|---|
| 课程列表 | `POST mooc1.chaoxing.com/mooc-ans/visit/courselistdata` | Cookie |
| 通知 / 作业 / 活动 | `GET mobilelearn.chaoxing.com/v2/apis/active/student/activelist` | Cookie |
| 通知正文 | `GET notice.chaoxing.com/pc/course/notice/{idCode}/getNoticeDetail` | Cookie |

几个踩过的坑，改代码前先看一眼：

- **`courselistdata` 必须带 `Content-Type: application/x-www-form-urlencoded; charset=UTF-8`**。
  少了这个头，服务端静默返回"暂无课程"，不报错。
- **通知的 `endTime` 不是截止时间**。实测它恒等于 `startTime + 24h`，是通知自身的
  有效窗口。把它当 DDL 会凭空造出上百条假数据。真正的截止时间写在**正文**里。
- **`idCode` 在 `content` 字段的 JSON 里**，不在 `extraInfo` 里。
- 通知详情那个 HTML 页面是个 SPA 空壳，任何 idCode 都返回同一份 109KB 的页面。
  正文得走 `getNoticeDetail` 这个 JSON 接口。

### 截止时间怎么抽的

`collect.py` 里的 `parse_deadline()` **精度优先**：

1. 逐句扫描，只认同时含「日期」和「强信号」的句子；
2. 强信号 = `截止 / 到期 / ddl`，或 `之前|前 + 提交|上传|完成|发送…` 这类动作组合；
3. 含「上课 / 地点 / 教室 / 实验室 / 开始」且没有「截止」字样的句子直接排除；
4. 抽不出来就**留空**，退化成一条普通通知，绝不猜。

实测：放宽规则能抽出 21 条但错 8 条（把「4月23日上实验课」当成截止时间）；
收紧后抽出 12 条，**全对**。所以别再放宽了。

`endTime` 只在两种情况采信：作业（type 35）的 endTime 就是交作业窗口；
或 endTime 与发布时间相差超过 26 小时。

---

## 二、跑起来

### 本地看看

```bash
cd workbench
python -m http.server 8765
# 打开 http://127.0.0.1:8765
```

必须走 HTTP，`file://` 打开的话 Service Worker 和 fetch 都会被浏览器拦掉。

### 采集一次

```bash
pip install requests
python collector/collect.py
```

### 手机上用

浏览器打开部署好的地址 → 分享 → **添加到主屏幕**。之后就是个全屏 App，
断网也能打开（显示上次缓存的数据）。

---

## 三、截止提醒

两条腿走路，建议都开：

### 1. 网页开着的时候 —— 浏览器通知（不用配，点一下就行）

设置页 → **开启通知** → 允许。之后网页（或装到主屏幕的那个 App）开着时，
快到期的会弹系统通知。触发时机：打开页面、每 15 分钟、从后台切回来。

提醒档位 `REMIND_AHEAD`（默认 3 天）：到期前 3 天、2 天、1 天、当天各提醒**一次**，
已提醒过的记在 `wb.notified` 里，不会重复轰炸。已逾期的**不再提醒** ——
不然积压一多天天弹，很快就麻木了。

> 局限：网页彻底关掉（iOS 划掉后台）就收不到。要真正随时收到，看下一条。

### 2. 网页关着也能收到 —— 微信推送（需配一次）

1. 打开 <https://sct.ftqq.com>，微信扫码登录，复制 **SendKey**
2. GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**
   名称 `SERVERCHAN_KEY`，值粘贴 SendKey
3. 完事。每天采集完自动把「未来 3 天内到期」+「最近一周逾期的」推到你微信

想换 PushPlus 也行：secret 名换成 `PUSHPLUS_TOKEN`，值填
<https://www.pushplus.plus> 的 token。两个都配会都发。

**没配也不会报错** —— `notify.py` 检测不到 key 就静默跳过，采集任务照常成功。

本地调试格式：

```bash
python collector/notify.py --dry          # 只打印不发送
python collector/notify.py --force        # 没到期项也发，用来试通道
```

---

## 四、数据加密

**为什么必须加密**：GitHub 免费版的 Pages 只能从**公开仓库**发布，而
`activities.json` 里是 63 门课、146 条通知的全文 —— 课程名、老师名、作业要求。
GitHub 会索引公开仓库内容，搜「天津医科大学 通知」这类词有可能撞到。

所以采集完立刻加密，跟着仓库走的是 `activities.enc`，明文只活在
本地和 CI 的临时目录里（已 gitignore）。

| | |
|---|---|
| 算法 | PBKDF2-SHA256 × 310,000 → AES-256-GCM |
| 密码在哪 | GitHub Secret `WORKBENCH_PASSWORD` + 你手机的 localStorage |
| 每次采集 | 换新 salt 和 iv，同一个密码两天的密文长得不一样 |
| 浏览器解密 | WebCrypto（`crypto.subtle`），需要 https —— Pages 满足 |

**没设密码就报错退出**，不会悄悄退化成明文上传 —— 那正是这一步要防的事。

### 密码怎么设

自己编一个，至少 8 位，**别用学习通密码**。两个地方填同一个：

1. 仓库 Secrets 里新建 `WORKBENCH_PASSWORD`
2. 手机第一次打开页面时输进去

我（或者任何帮你弄的人）都不需要知道它。忘了的话：重新设一个、
更新 Secret、在设置页点「忘记密码」，再跑一次采集即可。

本地验证：

```bash
WORKBENCH_PASSWORD='你的密码' python collector/encrypt.py          # 加密
WORKBENCH_PASSWORD='你的密码' python collector/encrypt.py --check  # 验密码对不对
```

---

## 五、部署到云端（每天自动更新）

1. **建仓库**，把 `workbench/` 推上去。
2. **导出 Cookie**：本地 `collector/cookies.json` 的内容就是 Secret 的值。
   整个文件内容原样复制。
3. 仓库 **Settings → Secrets and variables → Actions → New repository secret**
   建两个：
   - `CHAOXING_COOKIES` — 粘贴 cookie JSON
   - `WORKBENCH_PASSWORD` — 你自己编的密码
4. **Settings → Pages** → Source 选 `Deploy from a branch`，分支选 `main`，目录选 `/ (root)`。
5. **Actions → 采集学习通数据 → Run workflow** 手动跑一次，确认成功。
6. 手机浏览器打开 Pages 地址 → 输密码解锁 → 分享 → **添加到主屏幕**。

之后**每天早上 6:17** 自动跑一次（cron 故意避开整点，整点 GitHub 会延迟甚至丢弃）。
跑完把 `collector/out/activities.json` 提交回仓库，Pages 自动更新，页面下次打开就是新的；
同时发一条微信提醒。

> 每天跑不是为了数据新鲜，是为了**提醒及时**。一周一次的话，
> 周三老师发的作业，你要到下周一才收到通知 —— 那还不如没有。

### Cookie 过期怎么办

Cookie 一般能撑几周到几个月。过期时采集会返回 0 条，workflow 会**主动失败**，
GitHub 给你发邮件。这时重新导出 cookie、更新 Secret 即可，其他不用动。

> 没做账号密码自动登录：超星登录页有验证码，自动化登录不稳定，
> 失败模式还很隐蔽（可能只是悄悄返回空数据）。宁可手动更新，也不要一个会静默失效的东西。

---

## 六、页面里有什么

- **待办** — 学习通的 DDL + 手动待办，按今天/三天内/一周内/更远分组。
  **点左边的方框就能划掉**，会掉进「已完成」，再点一下可以撤销。
  逾期超过 14 天的归到「积压」，带一个**全部清掉**按钮，一次清干净。
  手动添加支持 `@12-25` 直接指定日期。回车即建。
- **通知** — 按时间倒序，可搜索课程/老师/正文，可按课程分组，点开看正文，一键跳学习通。
- **项目** — 三列看板（想法/进行中/已完成），放社团和副业的事。
- **设置** — 提醒开关、学习通数据状态、导出/导入全部数据、清空。

底部角标 = 待处理数量（已逾期 + 7 天内到期）。清完了角标就归零。

数据存在浏览器 localStorage 里，**换设备不会同步**。用「导出 JSON」搬家。

---

## 七、还没做

- 课表（得手动录入，还没想好怎么录最省事）
- 云端同步（现在是 `Store` 这一个对象封装的，换 Supabase / CloudBase 只改它）
- 考试安排、成绩查询
- 把通知里的截止时间做成 `.ics`，订阅到手机自带日历（不依赖任何第三方服务）

### 为什么待办不自动折叠

一开始逾期超 14 天是自动折叠的，结果上学期 14 条 DDL 全被藏起来，
整个待办页看起来是空的，用户以为「没法完成待办」。
**积压就该摊开让人一条条处理掉**，藏起来只会让它一直是积压。
所以现在默认展开，另配一个「全部清掉」。

---

## 八、安全

两个东西等同于账号密码，都不能外泄：

**1. `collector/cookies.json`** —— 拿到它就能以你的身份登录学习通。
已 gitignore，云端那份存在 GitHub Secrets 里（加密，日志里也会打码）。

**2. `WORKBENCH_PASSWORD`** —— 解开你所有通知全文的钥匙。
只在 Secret 和你手机里。别用学习通密码，别发微信。

### 别用 `python -m http.server` 预览

它会把这整个目录挂出去，**包括 `cookies.json`**。校园网/宿舍网谁都能扫，
等于把学习通账号摊在桌上。用白名单版的：

```bash
python tools/serve_lan.py --host 127.0.0.1    # 只本机
python tools/serve_lan.py                     # 给手机看，会打印局域网地址
```

它只放行 `index.html` / `sw.js` / 图标 / `activities.enc`，
其余一律 404，也不会给目录列表。

如果怀疑泄露：在学习通里退出所有设备重新登录，旧 cookie 立刻失效；
再换个 `WORKBENCH_PASSWORD` 重跑一次采集，旧密文就废了。
