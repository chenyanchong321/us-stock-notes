# 交接单：把盘前/盘后价从「6分钟档」提到「1分钟档」

> 交接对象：Codex（有阿里云控制台操作能力）
> 交接人：Claude
> 日期：2026-07-09
> 前置：请先读仓库根目录 `CLAUDE.md`，以及 `交接给Codex_方案B_国内访问与实时盘前.md`（方案B 的基础设施都还在，本单只换数据源）

---

## 一、一句话说明要做什么

**ECS 上抓行情的是 `/root/us-live.py`，它目前抓新浪，而新浪在盘前只返回昨收。把它改成抓 Yahoo，`live.json` 的输出格式一个字不变。**

> 注意文件关系：`/root/us-live.sh` 只是个三行包装器（`python3 /root/us-live.py`），
> **真正的抓取逻辑在 `/root/us-live.py`**，而它由仓库的 `scripts/deploy-ecs-live.sh`
> 第 53–115 行的 heredoc 生成。你要改的是那段 heredoc。

改完之后，美股盘前/盘后的现价与当日涨跌幅从 6 分钟档变成 1 分钟档。

## 二、为什么要改（结论已实测坐实，不必重新验证）

2026-07-09 16:08（美股盘前时段）对照富途牛牛实测：

| 来源 | DRAM 报价 | 判定 |
|---|---|---|
| 富途牛牛（基准） | 62.89（盘前） | — |
| 新浪 `live.json` | 62.04 | ❌ 就是昨收 |
| Yahoo 流水线 `r[17]` | 62.94，`st:"盘前"` | ✅ 真盘前价 |

判定证据：`live.json` 的 143 只美股中，**137 只的价格与涨跌幅和昨收完全一致**（其余 6 只仅小数取整差异，如 DJI 52348.39 vs 52348）。即新浪 `gb_` 的 `[1]当前价` 字段在盘前不更新。

同一时刻 Yahoo 有 155 只带 `st:"盘前"` 标记，且**全部 ≠ 昨收**（NVDA 盘前 204.985 vs 昨收 204.12）。

**副作用（已在前端临时关闭）**：`applyLive()` 每 60 秒用新浪的昨收覆盖掉 `applyExt()` 已取到的 Yahoo 正确盘前价，导致盘前显示反而倒退。目前前端 `LIVE_JSON_TRUSTED = false`。

## 三、为什么不能靠流水线提速（别走这条路）

实测流水线单次运行 **216~593 秒（平均 355 秒）**，因为它要下载 386 个标的的全年 K 线（每个 `sleep 0.4`）、重算 52 周/各期涨幅/PE，再 git commit + 触发 Pages 构建。6 分钟已经是它的物理下限。

而取盘前价只需要 Yahoo 的**批量报价接口**，190 只美股分 5 批、几秒返回。所以必须走 ECS 这条轻量快车道，不要动流水线频率。

---

## 四、要改的三处

> ### ⚠️ 铁律：改动必须落在 `scripts/deploy-ecs-live.sh` 里，不要手改服务器上的文件
>
> ECS 上的 `/root/us-live.py`、`/root/us-live.sh`、nginx 配置和 crontab，
> **全都是仓库里的 `scripts/deploy-ecs-live.sh` 生成的**
> （第 53 行 `cat > /root/us-live.py`、第 116 行 `cat > /root/us-live.sh`、第 125 行写 crontab）。
>
> 该脚本是**幂等**的（`set -euo pipefail`，全用 `cat >` 覆盖写、`ln -sf`、`mkdir -p`；
> 重建 crontab 时先 `grep -v` 剔除自己那两行再追加，**不会误删方案A 的 `us-stock-trigger` 闹钟**），
> 可以放心重复执行。
>
> 所以：**改 `scripts/deploy-ecs-live.sh` → commit → 在 ECS 上重跑它**。
> 这样整台机器的状态永远能从 git 重建，出问题 `git revert` 后重跑即可复原。
>
> 如果你直接 ssh 上去手改 `/root/us-live.py`，服务器就和仓库脱节了，那才是真正回不去的状态。

### 改动 1（主要）：改 `scripts/deploy-ecs-live.sh` 第 53–115 行那段生成 `/root/us-live.py` 的 heredoc，数据源 新浪 → Yahoo

**关键约束：`live.json` 输出结构必须保持不变**，前端已按此结构消费：

```json
{"t": 1752048000, "q": {"NVDA": {"p": 204.99, "c": 0.42}, "DRAM": {"p": 62.94, "c": 1.61}}}
```

- `t` = unix 秒（生成时刻）
- `q.<代码>.p` = 盘前/盘后价（数字）
- `q.<代码>.c` = 盘前/盘后涨跌幅百分数（数字，如 `0.42` 表示 +0.42%）
- 代码用**大写美股 ticker**（与 `watchlist.json` 的 `code` 一致，非 Yahoo 符号）

**Yahoo 鉴权与取数写法，直接抄仓库里的 `scripts/update_quotes.py` 的 `fetch_pe_map()`（第 82–126 行），它已经在生产跑通：**

```python
opener = ur.build_opener(ur.HTTPCookieProcessor())
opener.addheaders = list(UA.items())            # 必须带 User-Agent
opener.open("https://fc.yahoo.com", timeout=15).read(0)   # 种 cookie（返回404无妨）
crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).read().decode()
# 每批 40 个符号
url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
       + urllib.parse.quote(",".join(chunk)) + "&crumb=" + urllib.parse.quote(crumb))
j = json.load(opener.open(url, timeout=20))
for q in j["quoteResponse"]["result"]:
    st = q.get("marketState", "")
    if st.startswith("PRE") and q.get("preMarketPrice"):
        out[q["symbol"]] = {"p": round(q["preMarketPrice"], 4),
                            "c": round(q.get("preMarketChangePercent") or 0, 2)}
    elif st.startswith("POST") and q.get("postMarketPrice"):
        out[q["symbol"]] = {"p": round(q["postMarketPrice"], 4),
                            "c": round(q.get("postMarketChangePercent") or 0, 2)}
```

实现要点：
1. **符号来源**：从 `/var/www/us-stock/config/watchlist.json` 取 `market` 以「美股」开头的条目。注意用 `yahoo` 字段作请求符号，用 `code` 字段作 `live.json` 的键（多数相同，但 ADR/OTC 可能不同）。
2. **cookie/crumb 缓存**：不要每分钟重新种 cookie。把 crumb 缓存到 `/root/.yahoo_crumb`，过期（收到 401/403/Unauthorized）时再重取，否则容易触发 429。
3. **批量**：40 个/批，批间 `sleep 0.5`。约 190 只美股 → 5 批，总耗时数秒。
4. **非盘前/盘后时段直接退出**：`marketState` 不是 PRE*/POST* 时不必写文件（省配额）。
5. **原子写**：先写 `live.json.tmp` 再 `mv` 覆盖，避免 nginx 读到半个文件。
6. **失败时不要写空文件**：宁可保留上一版（前端有 240 秒新鲜度检查，过期会自动忽略）。

### 改动 2：前端把开关打开

`index.html` 里：

```js
const LIVE_JSON_TRUSTED = false;   // ← 改成 true
```

改完 `git add index.html && git commit && git push origin main`（token 已在本地镜像的 `.git/config` 的 origin 里）。

### 改动 3（可选，推荐）：把流水线频率降下来

价格不再搭流水线之后，流水线只剩 PE / 52周区间 / 各期涨幅 / 财报日这些慢变数据，**6 分钟纯属浪费且已在硬撑**（单次跑 355 秒）。

ECS 上 `crontab -e`，把美股时段两行从 `*/6` 改成 `*/15`：

```
*/15 16-23 * * 1-5 /root/us-stock-trigger.sh
*/15 0-8   * * 2-6 /root/us-stock-trigger.sh
7,37 9-15  * * 1-5 /root/us-stock-trigger.sh    # A股/港股，保持不变
```

**注意：这一步必须在改动 1 和 2 都验收通过之后再做**，否则盘前价会退化到 15 分钟档。

---

## 五、前端已有的两道保护（你不用写，但要知道它们会拦你）

1. **新鲜度检查**：`live.json` 的 `t` 若距今 > 240 秒，前端整批忽略。所以 cron 必须真的每分钟跑。
2. **昨收防呆守卫**（2026-07-09 新增）：若 `live.json` 里 ≥20 个报价中有 >70% 与流水线写下的收盘价一模一样，前端判定该数据源在盘前不返回盘前价，**整批丢弃并在 console 打 warning**，自动退回 `applyExt()` 的 Yahoo 值。

   实测：新浪那批 152/152 相同（100%）→ 被拒；Yahoo 真盘前 0/136 相同（0%）→ 通过。

   **所以如果你改完 live.json 页面价格没变、控制台出现 `[applyLive] live.json 疑似昨收快照`，说明你取到的还是收盘价，不是盘前价。** 别去调守卫阈值，去查 `marketState` 和 `preMarketPrice` 字段。

---

## 六、验收标准（盘前时段做，北京时间 16:00–21:30）

1. ECS 上直接看文件：
   ```bash
   cat /var/www/us-stock/data/live.json | python3 -m json.tool | head -20
   date +%s   # 与文件里的 t 相差应 < 60
   ```
2. 外部取数，确认 NVDA 的盘前价 ≠ 昨收，且随时间跳动：
   ```bash
   for i in 1 2 3; do
     curl -s "https://stock.ziyuanai.top/data/live.json?x=$RANDOM" \
       | python3 -c "import json,sys;j=json.load(sys.stdin);print(j['t'], j['q'].get('NVDA'))"
     sleep 70
   done
   ```
   三次的 `t` 应各差约 60 秒；`NVDA` 的 `p` 应有变化（盘前有成交时）。
3. 浏览器打开 `https://stock.ziyuanai.top`（关梯子），F12 console **不应**出现 `疑似昨收快照` 警告。
4. 页面上美股现价旁应显示金色「盘前」角标，价格与富途牛牛的盘前价一致（差几分钱正常，不同券商盘前成交回报有差异）。
5. 左上角「数据更新」行末尾应显示 `· 美股盘前(分钟档)`。

   > 注：前端 `usNoteFor()` 目前硬编码为「6分钟档」。改动 2 之后请一并把它改回按 `lastLiveAt` 判断，显示「分钟档」。

## 七、出问题怎么回退（三层，从快到慢）

**第 0 层｜什么都不做，前端自动兜底。**
如果新的 `live.json` 取到的还是收盘价，前端的防呆守卫会整批丢弃它，自动退回 `applyExt()` 的 Yahoo 值；
如果 `live.json` 挂了或过期 >240 秒，前端同样忽略。**页面不会显示错价，最多是退回 6 分钟档。**

**第 1 层｜一行开关，2 分钟生效（首选）。**
把 `index.html` 里的 `LIVE_JSON_TRUSTED` 改回 `false` 并推送。前端立刻不再读 `live.json`，
盘前完全走 Yahoo 流水线（即今天这个已知正确的状态）。ECS 上的脚本原封不动、继续空转，无副作用。

```bash
# 或者直接回滚那个提交
git revert <改动2的commit>
git push origin main
```

**第 2 层｜回滚 ECS 脚本。**
```bash
git revert <改动1的commit>     # 恢复 deploy-ecs-live.sh 到新浪版
git push origin main
# 在 ECS 上重跑，即可把 /root/us-live.sh 和 crontab 一并复原
bash /var/www/us-stock/scripts/deploy-ecs-live.sh
```
（前提是你遵守了上面的铁律：改动落在 `deploy-ecs-live.sh` 里。）

**第 3 层｜crontab 频率回退。**
若改动 3 做了但想撤，`crontab -e` 把 `*/15` 改回 `*/6` 即可。

**不需要回退的东西**：`quotes.json` 和 `live.json` 都是自动生成的，下一轮流水线/定时器会覆盖，不用管。

## 八、坑与注意

- **不要 commit 任何 token 到公开仓库**（GitHub 扫到会自动吊销）。token 只放 `.git/config` 和 ECS 的 `/root/.gh_token`。
- 当前 PAT **没有 `workflow` scope**，无法修改 `.github/workflows/` 下的文件。如需改流水线 YAML，得先给 token 补权限。
- Yahoo 对高频请求会返回 **429**。务必缓存 crumb + 批量 40 个 + 批间 sleep。若持续 429，把频率降到 90 秒也可接受（仍远好于 6 分钟）。
- ECS 站点同步脚本是 `/root/us-stock-sync.sh`（每 2 分钟从 codeload tarball 拉取 + rsync），它**会保留 `data/live.json`**。别把 live.json 提交进 git 仓库。
- 美股「夜盘」（北京白天那段场外交易）三个免费源都没有，是富途付费买的。这是已知盲区，不在本单范围内。
