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

**加笔记**：文章 HTML 放 `articles/`（复制现有一篇改正文，白底黑字模板），PDF 放 `notes/`，在 notes.json 加卡片（t/date/tags/d/page/ts）。**发布时必须写 ts=「YYYY-MM-DD HH:MM」发布时刻**，时间线按 ts 倒序，缺 ts 同日排序会乱。

**推送方式**：需要主人提供 fine-grained PAT（仅限本仓库，Contents+Workflows 读写，7天有效期），用
`git push https://x-access-token:TOKEN@github.com/chenyanchong321/us-stock-notes.git main`。
推送前先 `git pull --rebase`（机器人每天会 commit 数据）。

## 已踩过的坑（勿重蹈）

1. **Yahoo range=max&interval=1d 对老股票会悄悄降级/截断近期数据** → 已改为 5y日线+max月线双路，勿改回单路。
2. 必须用 **adjclose 复权价**，否则高分红股涨跌幅和行情软件对不上。
3. YTD 基准 = 当年 1 月 1 日前最近收盘（上年末），不是 12-31 00:00。
4. Pages 偶发 "Deployment failed, try again later" 是 GitHub 服务端故障，空 commit 重推即可。
5. 涨跌幅口径与富途基本一致；历史高点是**收盘价口径**（盘中高点会略高，属正常）。
6. 首页表格行数据是 15 列数组：[名称,代码,市场,市值,高点,现价,回撤,近1月,近3月,近半年,YTD,近1年,分组名,PE(TTM或null),52周位置0-100,52周区间字符串,当日涨跌%或null]。PE 走 Yahoo v7 quote（cookie+crumb），失败自动降级为空。

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

## 买点标注机制（2026-07-06 增）

- `config/buypoints.json`：{代码:"点位文案"}，前端在「现价」下方渲染灰色小字「买点 xx」。没有配置的标的不显示，不占空间。
- 点位来源：主人的《投资机会和价格点位》总文档（桌面 金融投资/00_投资机会和价格点位/）。主人给出新买点时同步更新此文件即可，无需动数据管线。
- 列顺序（显示序）：名称/市值/历史高点/现价(+买点)/高点跌幅（52周位改为悬停现价弹出信息卡，不占列）/近1月.../PE。数据行索引不变（15列见上文），仅前端展示顺序调整。

### 买点书写铁律（2026-07-06 补）

- **买点必须写具体数值**，禁止"当前可分批"这类相对表述——现价会变，相对表述会误导。凡"按当前价分批"型建议，一律换算成「x.x以下分批（MM/DD锚）」并注明锚定日期。
- 有买点的标的前端自动高亮：名称前金色★+名称列金色左边条+买点文字金色（tr.buy 类）。

## 盘中准实时与新增机制（2026-07-06 增）

- **数据更新频率**：亚洲时段（北京8:00-15:37，含韩日开盘）与美股时段（UTC 13:30-20:00）每30分钟（用:07/:37错峰，GitHub cron整点常被跳过）自动跑一次 workflow，收盘后 21:30 UTC 完整跑一次。Public 仓库 Actions 免费不限量。concurrency 防止并发冲突。
- **买点触达**：前端解析 buypoints 文案里第一个数字为触发价，现价 ≤ 触发价×1.02 时买点文字变绿加🎯，并在顶部「已进买点区」聚合条汇总（含风险锚/市值类文案的不参与解析）。
- **相关笔记互链**：浮窗底部自动附相关笔记链接，映射表在 index.html 的 NOTE_CODE（按代码）与 NOTE_GRP（按分组名关键词）。新笔记发布后应视情况补映射。
- **手机端**：≤860px 时 header 紧凑化（logo 缩短、slogan 隐藏），topbar 出现「跳转板块」下拉。

## 前端实时行情层（2026-07-06 增）

- index.html「实时行情」IIFE：页面每30秒经 `qt.gtimg.cn`（腾讯行情，script 标签 JSONP 免 CORS，charset=GBK）直连刷新**现价与当日涨跌**，并重算买点触达聚合条。覆盖 A股(sh/sz)、港股(hk五位码)、美股(usTICKER)、日股(jp)、韩股(kr)、主要指数（映射表 IDX）；台股/OTC/商品/加密不覆盖，保持每日快照值。
- 分层职责：实时层管现价/当日/市值/回撤/52周位（锚点+现价实时推导）；GitHub Actions 管锚点与 PE/各周期涨跌（cron 亚洲8:07-15:37、美股21:37-4:07 每30分钟）（cron 已错峰 :07/:37）。腾讯接口若失效，页面自动退回每日快照，不会坏。

## 视图切换器（2026-07-07 增）

- 顶部统计卡即视图切换：全景（观察标的）/ ⭐重点清单（买点标的，含"距买点"列，触达置顶）/ 今日涨幅榜Top30 / 跌幅榜Top30 / 回撤榜(>30%) / 板块热力（当日中位涨跌·剔除ETF指数·含领涨直达与分化极差列）。
- 跨板块交叉引用标的在榜单里按代码去重；实时行情30秒刷新时自动重建当前榜单；搜索输入自动切回全景。
- 实现在 index.html renderDash 末尾的「视图切换器」段（buildFlat/setView），榜单为跨板块平铺表，带"板块"列。

## 市场全局过滤器（2026-07-07 增）

- 视图条「市场」卡：全部/A股/港股/美股/日股/韩股/台股。选中后全站生效：全景（复用 applyFilter，rowHTML 带 data-mkt）、五个榜单（flatRows 过滤）、板块热力、买点机会条。选择存 localStorage("mkt")。**切市场保持当前视图不变**（市场=滤镜，视图=镜头）；再点当前选中的视图卡=回全景。
- 匹配规则 MKT_RULE 按 market 字段前缀（"日"匹配日股/日本/日股 指数）。

## 术语词典与开盘状态（2026-07-07 增）

- 第三页签「术语词典」：config/glossary.json（{cats[], terms:[{t,f,c,d}]}），renderDict 支持搜索/分类/A-Z（中文开头归"中"）。**新增学习笔记涉及新黑话时应顺手补词条**，风格=白话+类比，与笔记体系一致。
- 市场下拉菜单每项显示 ●盘中/○休市 + 交易时段（北京时间），美股自动判夏令时（mktStatus 函数）；打开菜单时实时刷新状态。

## 超额收益（2026-07-07 增）

- 「超额榜」视图：个股 YTD/近1年 减去**对应市场基准**（A股→沪深300、港股→恒指、美股→标普SPX、日→日经、韩→KOSPI、台→TWII 台湾加权），锚必须匹配市场（丹萍原则）。悬停浮窗自动附「相对大盘 YTD ±pp」一行。benchOf/alphaOf 在 index.html。

## 列顺序与榜单一致性（2026-07-07 定版）
- 全站统一列序：名称/市值/现价/历史高点/高点跌幅/当日/近1月/近3月/近半年/年初至今/近1年。现价紧跟市值，历史高点紧挨高点跌幅（父子字段相邻）。
- 榜单视图（重点清单/涨跌幅榜/超额榜/回撤榜）表头与主页完全一致，不得省列；专属列（距买点/超额pp）紧跟现价之后。回撤榜不再单加"历史高点"extra列（已是标准列）。
- 52周位不占列：鼠标悬停任意视图的现价(td.pxc)弹信息卡（区间+位置条+距高低点），见 posTip()。
- 改列序牵连三处必须同步：rowHTML/fvRow、SORT_MAP、applyQuote 的 tds 下标。

## 双行单元格与港股基准拆分（2026-07-07）
- 市值列双行：上市值下小字 PE（mcapCell()）；PE 不再挂在现价括号里。
- 现价列双行：全景/涨跌幅榜/超额榜/回撤榜第二行=「52周 低–高」；重点清单第二行=买点（priceCell(r,"buy")）。浮窗保留更深信息（当前位%、距高低点）。
- 港股超额基准拆分（丹萍建议）：科技/新经济板块→恒生科技（3033 ETF代理），能源/电力/资源/价值老登/贵金属→恒指，见 benchOf(mkt,sec) 的 HK_TRAD 正则。风格错配与权重股自我对比两点不做工程化修正，只在超额榜说明里注明局限。

## 大事件日历（2026-07-07 上线）
- 页签「📅 大事件」：未来7天置顶（倒计时）+按月时间轴+45天内已过事件可回看；类型色标 财报🟦/宏观🟥/上市🟨/产品🟩；关联标的点击跳数据面板行并闪烁（jumpToCode）。数据面板 #evweek 显示"本周大事 N 件"直达。
- 数据两层：①自动层 data/events.json——update_quotes.py 在 PE 批量请求里顺带取 earningsTimestamp（零额外请求），留未来120天，估算日期标 est；②人工层 config/events.json——宏观官方日程（FOMC/CPI/非农，每年初我更新一次，2026已填到年底）+特殊事件（IPO等，对话提到随手补）。用户不提供数据。
- 铁律：对话中出现新的重要事件（上市/发布会/政策节点），主动补进 config/events.json。

## 四项更新（2026-07-07 傍晚）
- 回撤配色对调（用户：黄牌先于红牌）：距高点跌幅>30% 红色加粗(.ddsev)，≤30% 黄色(.ddmild)，全站统一。
- 新高榜视图 "hi"：回撤<10% 的标的，按距新高由近到远；与回撤榜互为镜像。
- 手机端顶栏两行：--header-h 移动端 94px，第一行 Logo+搜索，第二行四页签横排可滑（.tabs order:3 width:100%）。
- 美股盘前/盘后（最终方案，2026-07-07 实测定案）：腾讯网页源美股为延时冻结口径（qt块标"delay"，f[9]/f[19]是收盘冻结盘口，不是实时，勿再踩坑）。改由流水线 Yahoo v7 quote 顺带取 preMarketPrice/postMarketPrice（零额外请求）写入行数据第18元素 r[17]={px,pct,st}；cron 补 UTC 8-12（北京16-21盘前）与 UTC 21-23（北京05-08盘后深段）。前端 applyExt() 在非盘中时段用 r[17] 接管美股显示（30分钟档），parseBatch 在非盘中直接跳过美股防止昨收覆盖；usSession() 用 America/New_York 时区判定，自动处理夏令时。标签显示"美股盘前(半小时档)"。用户桌面 Documents/Claude/Scheduled/verify-us-premarket-tencent 定时任务已无意义，可让用户删除。

## 区间表现榜（2026-07-07）
- 视图 "perf"（卡片在今日跌幅榜后）：一张卡装 5周期×2方向共10个榜。榜内周期胶囊(近1月/近3月/近半年/年初至今/近1年)+涨跌方向开关；状态存 localStorage(perfW/perfDir)，卡片副标题跟随周期；与市场过滤器联动；"上市后 x%"字符串行不参与排名；手机端胶囊横滑。

## 近1周窗口 + 超额榜两步框架（2026-07-07 晚）
- 行数据第19元素 r[18]=近1周涨跌（流水线 ts_1w）。列序：…当日→近1周→近1月…；SORT_MAP 5列→18；区间表现榜含近1周共6周期。追加字段一律 append 到行尾，禁止中间插位（避免全量下标漂移）。
- 超额榜两步框架（丹萍）：SEC_BENCH 板块正则→赛道指数（半导体系→SOX、软件→IGV、中国互联网→PGJ、加密→BTC、巨头/云→NDX，全是面板已有行）。第一列 vs赛道=个股alpha（排序键，无赛道基准者按vs大盘），第二列 vs大盘=市场超额；双正🏆双强；浮窗同步两步显示。A股半导体对标SOX为全球行业口径，已在说明注明。

## 修复与紧凑模式（2026-07-07 晚二）
- 教训：近1周上线时主表 rowHTML 替换静默未命中→表头11列/行10格错位，"近1年"看似消失。铁律：任何改列操作后必须校验「COLS数==rowHTML/fvRow的td数」（node校验脚本已用），python replace 后要 assert count==1，不许静默跳过。
- "上市后+x%" 不再占列宽：fmtPct 渲染为灰色"／"+title悬停（上市时间不满该窗口·上市至今+x%）。
- 紧凑模式：.tbl-wrap width:fit-content、table width:auto+min-width:100%，表格按内容收缩不再拉伸注水；th/td 水平内边距 9→13px。吸顶表头本就逐板块测量，自动适配。

## 填充列布局（2026-07-07 定版，用户方案）
- 表格布局最终形态：数据列内容自适应紧凑 + 行尾隐形填充列(th.fill/td.fill, width:100%,padding:0)吸收全部剩余宽度 → 卡片满宽与视图条对齐、行线贯通、右侧留白在框内。主表/榜单/热力全套；grp行 colspan=COLS.length+1。列数校验规则更新：rowHTML/fvRow td数 = COLS数+1(填充)。
- 上市不满窗口的"／"悬停改用 #stip 浮窗（.ipoflag[data-tip]，悬停即出+点击可固定），禁用原生 title/cursor:help（有延迟、手机无效、问号光标被用户否决过）。

## 四项增强（2026-07-07 晚三）
- 热力表周期切换：当日/近1周/近1月胶囊（data-hw，HEAT_W 存 localStorage heatW），中位/上涨家数/领涨/分化全随周期。
- 数据健康警示：静态数据工作日>4小时、周末>26小时未更新时，topbar 前插入金色警示条（现价实时层不受影响）。
- 亏损公司估值补位：流水线 v7 quote 顺带取 forwardPE→行尾 r[19]；mcapCell 无 PE 时显示"远期PE x"。行数据现为 20 元素(0-19)，新字段继续 append 行尾。
- 手机吸顶表头修复：topOf() 硬编码 56 改为实测 #header.offsetHeight（手机两行头 94px，浮头曾被压在头条下看不见）。

## 新会话交接指南（换窗口/换模型必读）
1. 让烟囱挂载文件夹「01_公司投研」，读本文件（us-stock-notes/CLAUDE.md）即可无缝接手，不依赖旧对话记忆。
2. 推送凭证：01_公司投研/us-stock-notes 本地克隆的 origin 已内置 token（存在 .git/config，本地文件不会被提交）。直接在该目录 `git add/commit/pull --rebase origin main/push origin main` 即可，无需再要 token。⚠️ 当前 token 2026-07-11 到期。
3. token 到期续期（教烟囱操作）：github.com 右上头像 → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token → Repository access 只勾 us-stock-notes → Permissions 里 Contents 选 Read and write → 有效期建议 90 天 → 生成后把 github_pat_ 开头的字符串发给 Claude，Claude 执行 `git remote set-url origin https://x-access-token:新token@github.com/chenyanchong321/us-stock-notes.git` 写回本地克隆。
4. 铁律：token 只放 .git/config（本地），绝不写进任何会被 commit 的文件（公开仓库，GitHub 扫到会自动吊销）。

## 新增/修改标的后必须立刻出数据（铁律，2026-07-07 补）
烟囱不等下一班 cron——改完 watchlist/profiles 推送后，必须马上手动触发流水线：
1. 沙盒连不上 api.github.com，但可以用 Claude in Chrome 浏览器工具操作网页触发：navigate 到
   https://github.com/chenyanchong321/us-stock-notes/actions/workflows/update-data.yml
   → 点右侧「Run workflow」下拉（坐标约 1304,418，以截图为准）→ 点弹层里绿色「Run workflow」按钮（约 1089,561）→ 截图确认出现新的 Queued run。
2. 若没有浏览器权限，就直接请求加载 Claude in Chrome 工具（ToolSearch: claude-in-chrome）；实在不行才告知烟囱去手动点，并给他上面的链接。
3. run 约 3-5 分钟；结束后 git pull 验证 data/quotes.json 里新标的行已生成（17+元素、近1周 r[18]、远期PE r[19]），再等 Pages 部署 1-2 分钟，用 ?v=随机数 访问验证页面可见。
4. Pages 部署偶发失败/CDN 缓存约10分钟：kick 空提交重试≤2次，然后等缓存。
5. 新增标的时顺带做三件事：profiles.json 简介（记忆卡风格）、已知的重要事件（业绩预告/财报日/上市日）写入 config/events.json、若烟囱给了买点则按"具体数值+锚定日期"写 buypoints.json。

## 锚点跳转防遮挡（2026-07-07）
- tr.grp/.section 的 scroll-margin-top 改为动态 CSS 变量 --anchor-grp/--anchor-sec（floatHead IIFE 的 setAnchors() 实时测算 = 顶栏+吸顶视图条+悬浮表头高度）。以后凡改变顶部吸顶层高度（视图卡增减、顶栏换行）无需手调数字。
