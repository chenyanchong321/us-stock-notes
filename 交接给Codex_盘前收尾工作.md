# 交接单：盘前分钟档 —— 收尾工作（4 项）

> 日期：2026-07-09
> 前置：分钟档主体已由你完成并**验收通过**，本单只是收尾。
> 相关：`交接给Codex_盘前改用Yahoo分钟档.md`（含三层回退方案，动手前先看第七节）

---

## 验收复核结论（我已独立复验，不必重做）

盘前时段实测 189 只美股：

- `live.json` 年龄 23~55 秒，分钟档确实通了 ✅
- **170 只带「盘前」角标的，价格全部 ≠ 昨收**；**19 只无角标的，价格全部 = 昨收** ✅
- 零个「挂着盘前角标却显示昨收」，零个「价格动了却没标角标」 ✅
- 你说的 ECS 直连 Yahoo 被 403 属实：我用「OTC 与指数在 `live.json` 中全部缺失」这一特征反推确认，**当前真正供数的是 Nasdaq 兜底**，不是 Yahoo。

### 但我发现并修了一个你漏掉的 bug（前端，已上线）

**Nasdaq 在个股盘前无成交时，不返回「无数据」，而是退回常规时段：价格给昨收、涨跌幅给昨日的。**

实测 136 条里有 4 条如此。其中 `TLN` 最严重：Yahoo 流水线已取到真盘前价 365.36 / −0.68%，
却被 Nasdaq 的昨收 367.88 / 0% **覆盖**。这是新浪那个 bug 的缩小版（1/136，我原先那道 70% 整批守卫拦不住少数派）。

已在 `index.html` 的 `applyLive()` 里加**逐只防呆**：某只票的 live 价若等于流水线写下的昨收，跳过该只、不覆盖。已验证 TLN 恢复正常。

> 另：`PGJ` / `BRKR` / `NDSN` 这三只盘前无成交的，显示「昨收 + 昨日涨跌 + 无角标」，
> 这与富途「盘前暂无成交」的口径一致，**是正确的，不要改**。

---

## 要做的 4 件事

### 1｜停止每分钟白撞 Yahoo 的 403（优先级最高）

现状：`us-live.py` 每次运行都先试 Yahoo —— `load_auth()` 握手 → 批量请求 → 403 抛 `AuthError`
→ `refresh_auth()` 再握手 → 再请求 → 再 403 → 才降级 Nasdaq。

即**每分钟约 4 次无效 Yahoo 请求，一天约 5700 次**。既拖慢每轮耗时，也可能让这台 ECS 的 IP 被 Yahoo 拉得更死。

脚本里已有 `FORCE_NASDAQ_LIVE` 环境变量可直接跳过 Yahoo。**推荐做法（二选一）**：

- **简单**：在 crontab 那行加上 `FORCE_NASDAQ_LIVE=1`，直接走 Nasdaq。
- **更好（推荐）**：加一个 **Yahoo 冷却文件**（如 `/root/.yahoo_cooldown`，存上次失败的 unix 秒）。
  失败后 1 小时内不再尝试 Yahoo，1 小时后重试一次。这样既不刷 403，又保留「哪天 Yahoo 解封就自动恢复」的能力。

> 注意：Yahoo 一旦恢复可用，`live.json` 会自动覆盖 OTC 和指数（Nasdaq 覆盖不到它们），是净收益。

### 2｜给 `live.json` 加数据源标记

现在文件里只有 `{"t":..., "q":{...}}`，**看不出这批数据是 Yahoo 还是 Nasdaq 给的**，
排查时只能靠「OTC 在不在」间接反推。请加两个字段：

```json
{"t": 1752051234, "src": "nasdaq", "n": 139, "q": { ... }}
```

- `src`：`"yahoo"` / `"nasdaq"`
- `n`：本批报价条数

前端会忽略未知字段，**不需要改前端，也不会破坏兼容**。

### 3｜把流水线从 6 分钟放慢到 15 分钟

价格已经不搭流水线了，它只剩 PE / 52周区间 / 各期涨幅 / 财报日这些慢变数据。
而流水线**单次运行 216~593 秒（平均 355 秒）**，`*/6`（360秒）等于贴着上限硬撑，随时可能上一轮没跑完下一轮又压上来，还会给 Pages 构建队列造成大量无谓 churn。

ECS 上 `crontab -e`，美股时段两行改为 `*/15`：

```
*/15 16-23 * * 1-5 /root/us-stock-trigger.sh
*/15 0-8   * * 2-6 /root/us-stock-trigger.sh
7,37 9-15  * * 1-5 /root/us-stock-trigger.sh    # A股/港股，保持不变
```

**影响面我已量化**：原本有 3 只（IAU / GDX / SLV）的盘前价只能靠流水线。
但我已经修掉了根因 —— 它们本就是 ETF，却在 `watchlist.json` 里被标成 `美股` 而非 `美股 ETF`，
导致 `nasdaq_asset_class()` 按 `stocks` 端点去查、查不到。改标签后它们会自动进入 `live.json` 分钟档。

所以**做完第 3 步后，请确认这 3 只已出现在 `live.json` 里**；若确认，则放慢流水线对盘前价**零影响**。

### 4｜同步两处文案与文档

- `index.html` 的 `usNoteFor()` 里，降级文案 `"(6分钟档)"` 应随第 3 步改成 `"(15分钟档)"`。
  （这是 `applyLive` 未生效时的兜底文案；成功时走上一行的 `"(分钟档)"`，逻辑本身没问题，不用动。）
- 把下面这段**写进 `CLAUDE.md`**，这是花了很大代价才换来的认知，别丢：

  > **美股盘前/盘后数据源的踩坑史**
  > 1. 腾讯：美股非盘中时段是延时冻结口径，不可用。
  > 2. 新浪 `gb_`：盘前只返回昨收（实测 143 只里 137 只与昨收完全一致）。已弃用。
  > 3. Yahoo：数据正确，但**从阿里云 ECS 直连会被 403**；只能由 GitHub Actions 流水线取（6~15 分钟档）。
  > 4. Nasdaq `api.nasdaq.com`：ECS 可直连，是当前 `live.json` 的实际数据源（分钟档）。
  >    **两个已知缺陷**：(a) 不覆盖 OTC 与指数；(b) 个股盘前无成交时会退回常规时段数据（价=昨收、涨跌幅=昨日的）。
  > 5. 前端因此有两道防呆守卫（`applyLive()` 内）：整批守卫（>70% 等于昨收则整批丢弃）+ 逐只守卫（单只等于昨收则跳过）。
  >    **若将来换源后页面价格不动、console 出现 `疑似昨收快照`，是数据源在盘前不给盘前价，去查字段，不要动守卫阈值。**

---

## 验收（盘前时段做，北京时间 16:00–21:30）

```bash
# 1) src 标记与条数
curl -s "https://stock.ziyuanai.top/data/live.json?x=$RANDOM" \
  | python3 -c "import json,sys;j=json.load(sys.stdin);print('src=',j.get('src'),'n=',j.get('n'),'age=',__import__('time').time()-j['t'])"

# 2) 三只金银 ETF 应已进入分钟档
curl -s "https://stock.ziyuanai.top/data/live.json?x=$RANDOM" \
  | python3 -c "import json,sys;q=json.load(sys.stdin)['q'];print({k:q.get(k) for k in ['IAU','GDX','SLV']})"

# 3) 不再有失败的 Yahoo 请求
grep -c "LIVE_WARN yahoo" /root/us-live.log 2>/dev/null || journalctl -u cron --since "-10min" | grep -c "yahoo"
```

- 浏览器打开 `https://stock.ziyuanai.top`，F12 console 不应出现 `疑似昨收快照`。
- 页面美股现价旁应有金色「盘前」角标；`TLN` 应显示 Yahoo 的盘前价而非昨收。

## 铁律（重申）

- 所有 ECS 改动**必须落在 `scripts/deploy-ecs-live.sh`**（改完 commit，再在服务器上重跑该脚本）。
  不要 ssh 上去手改 `/root/` 下的文件，否则服务器与仓库脱节，就真的回不去了。
- 不要 commit 任何 token 到公开仓库。
- 当前 PAT 没有 `workflow` scope，改不了 `.github/workflows/` 下的文件。
- 出问题的三层回退方案见 `交接给Codex_盘前改用Yahoo分钟档.md` 第七节；
  最快的一层是把前端 `LIVE_JSON_TRUSTED` 改回 `false`，2 分钟生效。
