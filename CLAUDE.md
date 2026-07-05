# CLAUDE.md — 项目交接手册

> 给任何接手本项目的 Claude（新会话 / Claude Code）：读完本文件即可开始维护，无需之前的对话记录。

## 项目是什么

「烟囱的美股学习笔记」：主人 yancong 的个人投资数据面板 + 学习笔记分享站。

- 线上地址：https://chenyanchong321.github.io/us-stock-notes/
- 仓库：github.com/chenyanchong321/us-stock-notes（Public，GitHub Pages 从 main 分支根目录发布）
- 两个栏目：📊 数据面板（19 个板块 ~270 个标的行情）+ 📚 学习笔记（网页文章 + PDF 下载）

## 架构（三层分离）

| 文件 | 作用 | 谁改 |
|---|---|---|
| `config/watchlist.json` | 关注池配置：板块、标的、Yahoo代码、市值基准 | 加/删股票改这里 |
| `config/notes.json` | 笔记卡片目录 | 加笔记改这里 |
| `data/quotes.json` | 行情数据（**自动生成，勿手改**；除非重排板块需同步顺序） |
| `scripts/update_quotes.py` | 数据抓取：Yahoo chart API，每标的两路（5y日线算涨跌 + max月线补历史高点），复权价口径 |
| `.github/workflows/update-data.yml` | 每天 UTC 21:30（北京 5:30）自动跑脚本、commit、触发 Pages 重发布；支持手动 Run workflow |
| `index.html` | 首页（读 JSON 渲染），一般不动 |
| `articles/*.html` | 文章页，白底黑字阅读版；新文章参考现有文件结构 |
| `notes/*.pdf` | 文章对应的 PDF 存档 |

## 高频操作 SOP

**加股票**：在 watchlist.json 对应板块 items 里加一条：
```json
{"name":"公司名","code":"显示代码","market":"美股/A股/港股/日股/韩国",
 "yahoo":"Yahoo代码","currency":"$",
 "mcap_base":{"prefix":"$","yi":市值亿数,"approx":true},"mcap_base_price":当时股价,"ath_floor":0}
```
Yahoo 代码规则：A股 `.SS`/`.SZ`，港股去前导0加 `.HK`（02476→2476.HK），日股 `.T`，韩股 `.KS`。
mcap_base 的本质是"股本数"：yi/mcap_base_price 应等于总股本（亿股），估算即可（页面已声明估算口径）。
改完 push，然后到 Actions 手动 Run「每日更新行情数据」让新标的立刻有数据。

**加板块**：sections 里加 `{"name":"NN 板块名","items":[...]}`，如需重排全站顺序，记得把 data/quotes.json 的 sections 同步重排（否则要等下次数据刷新才生效）。同一标的可跨板块重复（用同一配置对象即可）。A/H 两地上市相邻排列。

**加笔记**：文章 HTML 放 `articles/`（复制现有一篇改正文，白底黑字模板），PDF 放 `notes/`，在 notes.json 加卡片（t/date/tags/d/page）。

**推送方式**：需要主人提供 fine-grained PAT（仅限本仓库，Contents+Workflows 读写，7天有效期），用
`git push https://x-access-token:TOKEN@github.com/chenyanchong321/us-stock-notes.git main`。
推送前先 `git pull --rebase`（机器人每天会 commit 数据）。

## 已踩过的坑（勿重蹈）

1. **Yahoo range=max&interval=1d 对老股票会悄悄降级/截断近期数据** → 已改为 5y日线+max月线双路，勿改回单路。
2. 必须用 **adjclose 复权价**，否则高分红股涨跌幅和行情软件对不上。
3. YTD 基准 = 当年 1 月 1 日前最近收盘（上年末），不是 12-31 00:00。
4. Pages 偶发 "Deployment failed, try again later" 是 GitHub 服务端故障，空 commit 重推即可。
5. 涨跌幅口径与富途基本一致；历史高点是**收盘价口径**（盘中高点会略高，属正常）。
6. 首页表格行数据是 12 列数组：[名称,代码,市场,市值,高点,现价,回撤,近1月,近3月,近半年,YTD,近1年]。

## 风格约定

- 数据面板深色主题，**绿涨红跌**；文章页白底黑字。
- 板块顺序 = 主人的重点动线：指数→七巨头→芯片→存储→光连接→云算力→供应链→其他资产，调整顺序需主人确认。
- 免责声明保留在页脚和文章尾部。
- 主人偏好：回复简洁、先讨论方案再执行大改动、数据准确性问题必须追根因。

## 数据面板二级分组机制（2026-07-06 增）

- 板块内二级分组：在 watchlist.json 的 section 加 `"groups":[{"name":"组名","codes":[代码...]}]`，items 顺序须与 groups 展开顺序一致（同一标的在一个板块内只能属一个组；跨板块重复不受影响）。
- update_quotes.py 会把组名写进每行第 13 列（r[12]）；本地手改分组后要同步重排 data/quotes.json 各行顺序并补 r[12]，否则要等下次数据刷新才生效。
- 前端自动渲染：表内蓝色分组标题行（锚点 secN-gM）+ 侧边栏缩进二级链接；搜索时分组行自动隐藏，排序时切平铺。
- 表头为 JS 悬浮实现（#floatHead，任意窗口宽度吸顶、随表格横滚同步），勿改回 CSS position:sticky（与横向滚动容器互斥）。
- 当前 18 个板块中 12 个已分组（光连接8组、800VDC5组、设备5组、材料5组、软件5组、能源4组、存储4组、芯片4组、云4组、贵金属4组、封测3组、光纤3组、中国互联网3组）。
- 板块合并史：碳化硅+800VDC→「800VDC 功率半导体（SiC·GaN）」；光芯片激光器并入光连接。

## 标的简介浮窗（2026-07-06 增，固定规则）

- `config/profiles.json`：每个标的代码对应一段 HTML 简介，鼠标悬停（手机点按）名称时弹出浮窗。
- **固定规则：以后每新增一个标的，必须同步在 profiles.json 手写一条简介**，缺了算任务没完成。
- 文案风格（C 记忆卡式）：`<b>粗体第一句</b>`一句话讲清它是干嘛的（能用类比就用类比，如「收费站」「全科医生」），后接 2-3 句自由发挥：地位数据、叙事逻辑、与同组邻居的区分。60-120字。人设绰号尽量沿用学习笔记体系（EML之王、泵浦之王、悲情先驱等）。
- A/H 两地上市共用同一条文案（两个代码各写一个 key，值相同）。
- 浮窗实现在 index.html 的「标的简介浮窗」IIFE：#stip 固定定位、跟随鼠标、自动避让屏幕边缘；名称上不加下划线不改光标（主人明确要求）。
