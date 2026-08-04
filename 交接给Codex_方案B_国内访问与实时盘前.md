# 交接单：方案 B —— 国内免翻墙访问 + 美股实时盘前

> 交接对象：Codex（有 computer use，操作阿里云控制台更顺手）
> 交接人：Claude（已完成方案 A，卡在阿里云控制台自动化太难，交给你收尾）
> 日期：2026-07-08

---

## 一、这个项目是什么（30秒背景）

- 项目：「烟囱的美股学习笔记」——一个美股数据面板 + 学习笔记网站。
- 仓库：`https://github.com/chenyanchong321/us-stock-notes`（公开仓库）
- 现在托管在 GitHub Pages：`https://chenyanchong321.github.io/us-stock-notes/`
- 数据流水线：GitHub Actions 的 `update-data.yml` 每隔一段时间抓行情写进 `data/quotes.json`，前端 `index.html` 读取并渲染；盘中价格由前端直连腾讯行情 30 秒实时。
- **详细工程规范全部在仓库根目录 `CLAUDE.md`，务必先读它。** 本文件只讲方案 B 的增量。

## 二、已经完成的（方案 A，你不用动）

1. **GitHub token 续期**：新 fine-grained PAT（名 `us-stock-notes-bot`，含 Contents + Actions 读写，2026-10-06 到期）。已写入：
   - 本地镜像 `01_公司投研/us-stock-notes/.git/config` 的 origin
   - 阿里云 ECS 的 `/root/.gh_token`（600 权限）
2. **阿里云 ECS 定时闹钟**：根治 GitHub cron 跳班。ECS 上 `/root/us-stock-trigger.sh` 读 `/root/.gh_token` + `/root/.gh_payload`({"ref":"main"})，通过 GitHub API `workflow_dispatch` 触发流水线。crontab（北京时间 Asia/Shanghai）：
   ```
   */6 16-23 * * 1-5 /root/us-stock-trigger.sh   # 美股盘前+盘中，每6分钟
   */6 0-8  * * 2-6 /root/us-stock-trigger.sh    # 美股盘中+盘后，每6分钟
   7,37 9-15 * * 1-5 /root/us-stock-trigger.sh    # A股/港股白天，30分钟
   ```
   首次验证 dispatch=204 成功。**这套别动，方案 B 在它基础上加东西。**

## 三、现有资产（方案 B 要用的）

| 资产 | 值 | 说明 |
|---|---|---|
| 阿里云 ECS | 公网 IP **118.31.109.150**，杭州(cn-hangzhou)，Ubuntu 22.04，2核2G | 已付费到 2027-05；CPU 用率<2%，基本闲置 |
| ECS 实例ID | `i-bp1927ebi4ojz411rzig` | |
| 已备案域名 | **ziyuanai.top**，浙ICP备2026030169号-1，管局审核通过 | 单位备案（杭州很会读书文化传媒有限公司，负责人陈艳鹏）；接入商=阿里云 |
| 目标子域名 | **stock.ziyuanai.top** | 子域名继承主域名备案，不用重新备案 |
| ECS 云助手 | 阿里云控制台「ECS 云助手 → 创建/执行命令」可免登录跑 Shell | Claude 一直用这个跑命令；也可用「远程连接」开 Workbench 终端 |
| GitHub token | 已在 ECS `/root/.gh_token` | 方案 B 不需要它，但服务器 git pull 公开仓库不需要鉴权 |

## 四、方案 B 目标 & 架构

**目标**：
1. 国内用户免翻墙秒开 `https://stock.ziyuanai.top`（GitHub Pages 国内访问慢/不稳，实测 ECS 连不上 github.io）。
2. 美股盘前/盘后价从「6 分钟档」提升到「~1 分钟」（真·准实时）。

**架构（同一台 ECS 一并搞定）**：
```
GitHub 仓库(main) ──git pull每2分钟──▶ ECS: /var/www/us-stock（网站文件）
                                          │
新浪美股接口 ──每60秒抓盘前价──▶ ECS: /var/www/us-stock/data/live.json
                                          │
                                    nginx 托管(80/443)
                                          │
用户 ──▶ https://stock.ziyuanai.top（国内直连，秒开，前端读 live.json 每60秒刷盘前）
```
- 网站内容仍以 GitHub 为准（流水线更新 quotes.json → ECS git pull 同步），ECS 只做「就近托管 + 盘前实时层」。
- github.io 那个地址保留作海外备用入口，不受影响。

## 五、执行步骤（六步，建议按序）

### 步骤 0：先验证新浪盘前数据质量（重要！别跳过）
Claude 之前的探针在盘中做的，**没有在盘前时段确认过新浪 gb_ 接口返回的到底是「实时盘前价」还是「昨收」**。上你必须在**美股盘前时段（北京时间约 16:00–21:30）**跑一次验证：
```bash
curl -s -m 8 -H 'Referer: https://finance.sina.com.cn' 'https://hq.sinajs.cn/list=gb_dram,gb_mu' | iconv -f gbk -t utf-8
```
新浪 gb_ 字段格式（逗号分隔）：`[0]名称,[1]当前价,[2]涨跌幅%,[3]时间戳,[4]?,[5]今开,[6]最高,[7]最低,[8]52周高,[9]52周低...`
- 拿 `[1]当前价` 和 `[3]时间戳` 对照富途牛牛的盘前价。**如果 [1] 在盘前时段跟着富途跳动 → 新浪可用，用它。**
- **如果 [1] 在盘前不动（只是昨收）→ 新浪不行，改用 Yahoo**：ECS 直连 `https://query1.finance.yahoo.com/v7/finance/quote?symbols=...` 带 `includePrePost`，参考仓库 `scripts/update_quotes.py` 里 `fetch_pe_map` 的 cookie+crumb 方式（ECS 实测能连 Yahoo，之前偶发 429，需带 UA/cookie 缓解）。
- 决定数据源后再写步骤 3 的抓取脚本。

### 步骤 1：ECS 装 nginx + git，克隆网站
云助手执行（一条命令）：
```bash
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get install -y nginx git
rm -rf /var/www/us-stock
git clone --depth 1 https://github.com/chenyanchong321/us-stock-notes.git /var/www/us-stock
echo DONE; ls /var/www/us-stock/index.html
```

### 步骤 2：nginx 配置（先上 80 端口，证书后面加）
```bash
cat > /etc/nginx/sites-available/us-stock <<'NG'
server {
    listen 80;
    server_name stock.ziyuanai.top;
    root /var/www/us-stock;
    index index.html;
    location / { try_files $uri $uri/ =404; }
    location = /data/live.json { add_header Cache-Control "no-store"; }
}
NG
ln -sf /etc/nginx/sites-available/us-stock /etc/nginx/sites-enabled/us-stock
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl restart nginx && echo NGINX_OK
```

### 步骤 3：盘前实时抓取脚本 + 网站同步 cron
`/root/us-live.sh`（数据源按步骤0结论选新浪或Yahoo，下面给新浪版模板）：
```bash
cat > /root/us-live.sh <<'SH'
#!/bin/bash
REPO=/var/www/us-stock
OUT=$REPO/data/live.json
SYMS=$(python3 -c "
import json
w=json.load(open('$REPO/config/watchlist.json'))
o=[]
for s in w['sections']:
  for it in s['items']:
    if str(it.get('market','')).startswith('美股'):
      c=it['code']
      if c.isalpha(): o.append(c.lower())
print(','.join(dict.fromkeys(o)))
")
Q=$(echo $SYMS | sed 's/[^,]*/gb_&/g')
RAW=$(curl -s -m 10 -H 'Referer: https://finance.sina.com.cn' "https://hq.sinajs.cn/list=$Q" | iconv -f gbk -t utf-8)
echo "$RAW" | python3 -c "
import sys,json,time
res={}
for line in sys.stdin:
  line=line.strip()
  if 'hq_str_gb_' not in line: continue
  try:
    key=line.split('hq_str_gb_')[1].split('=')[0].strip().upper()
    v=line.split('\"')[1].split(',')
    price=float(v[1]); pct=float(v[2])
    if price>0: res[key]={'p':round(price,2),'c':round(pct,2)}
  except: pass
json.dump({'t':int(time.time()),'q':res}, open('$OUT','w'))
" 
SH
chmod 700 /root/us-live.sh
```
crontab 追加（保留已有的 us-stock-trigger 行！）：
```bash
(crontab -l 2>/dev/null | grep -v 'us-live\|us-stock-sync'; \
 echo '* * * * * /root/us-live.sh  # us-live'; \
 echo '*/2 * * * * cd /var/www/us-stock && git pull -q origin main >> /root/us-sync.log 2>&1  # us-stock-sync') | crontab -
```
> ⚠️ live.json 会被 git pull 冲突（它在工作区）。解法：把 live.json 加进 `.git/info/exclude` 或让 us-live.sh 写到 `data/live.json` 但该文件不在仓库里（仓库没有它，git pull 不会碰）。确认仓库 main 里**没有** data/live.json 即可，git pull 不会删本地未跟踪文件。

### 步骤 4：前端读 live.json（需改仓库 index.html 并推送）
在 `index.html` 的实时层加：非盘中时段（美股盘前/盘后）每 60 秒 fetch 同源 `data/live.json`，把 `q[TICKER].p/.c` 覆盖到对应美股行的现价/当日涨跌。github.io 上该文件 404 会静默失败（无副作用），只有 ECS 托管版能拿到。参考 CLAUDE.md 里 `applyExt()` / `usSession()` 的写法，新增 `applyLive()`。改完 `git add/commit/push origin main`（token 已在本地镜像 origin）。

### 步骤 5：域名解析（阿里云 DNS 控制台）
给 ziyuanai.top 加一条 A 记录：主机记录 `stock`，记录值 `118.31.109.150`。
路径：阿里云控制台 → 云解析 DNS → ziyuanai.top → 添加记录（A / stock / 118.31.109.150）。

### 步骤 6：开端口 + HTTPS 证书
1. **安全组开 80/443**：ECS 控制台 → 网络与安全组 → 安全组 → 入方向加规则：TCP 80、TCP 443，源 0.0.0.0/0。
2. **备案接入确认**：解析生效后如果 80 端口访问被阿里云拦（提示未备案），因接入商已是阿里云、子域名继承主域名备案，通常直接可用；若被拦，在阿里云备案控制台做「接入备案」（该主体已在阿里云，走「新增网站」加 stock.ziyuanai.top，免费，1-3天）。
3. **HTTPS**：`apt-get install -y certbot python3-certbot-nginx && certbot --nginx -d stock.ziyuanai.top --non-interactive --agree-tos -m chimneycyc@gmail.com`（需 80 端口通 + 解析生效）。certbot 会自动改 nginx 配置上 443 + 自动续期。

## 六、验收标准
- 浏览器（**关掉梯子**）访问 `https://stock.ziyuanai.top` 能秒开面板；
- 美股盘前时段，页面美股现价/涨跌与富途基本一致（≤1-2分钟延迟），且左上"数据更新"时间在跳；
- github.io 老地址仍正常。

## 七、坑与注意
- 阿里云控制台弹窗极多（简捷版推广、新手引导、安全防护弹窗），每步先关弹窗。云助手「创建/执行命令」的命令编辑器里输入时会弹「AI命令助手」，按 Esc 关掉。
- 云助手命令编辑器多行 heredoc 容易被自动补全打乱；建议用「远程连接」开 Workbench 终端粘贴，或把脚本分单行 echo 追加。
- **不要 commit 任何 token 到公开仓库**（GitHub 扫到会自动吊销）。token 只放 .git/config 和 ECS 的 /root/.gh_token。
- 数据源结论（新浪 or Yahoo）务必先在盘前时段实测再定，别拍脑袋。
- 全程零现金成本（用已付费的 ECS + 免费 Let's Encrypt 证书）。

## 八、验证清单（做完逐项打勾）
- [x] 步骤0：**2026-07-09 16:08（盘前窗口内）实测完成 → 结论：新浪不可用，必须换 Yahoo**
- [x] 步骤1：nginx+git 装好，网站克隆到 /var/www/us-stock
- [x] 步骤2：nginx 配置 + 重启 OK
- [x] 步骤3：us-live.sh + 两条 cron（未覆盖 us-stock-trigger；us-stock-sync 改用 codeload tarball 同步 GitHub main，避开 ECS git/TLS 不稳定）
- [x] 步骤4：index.html 加 applyLive() 并推送
- [x] 步骤5：stock.ziyuanai.top A 记录 → 118.31.109.150
- [x] 步骤6：安全组 80/443 + certbot HTTPS
- [ ] 验收：关梯子秒开 https ✅ + 盘前分钟级 ❌（https、DNS、live.json 分钟级链路均已验；但 live.json 内容是昨收，见步骤0）

### 步骤0 实测结论（2026-07-09 16:08，盘前窗口内，DRAM 对照富途）
| 来源 | DRAM 报价 | 判定 |
|---|---|---|
| 富途牛牛（基准） | 62.89（盘前） | — |
| 新浪 `live.json` | 62.04 | ❌ 就是昨收 |
| Yahoo 流水线 `r[17]` | 61.92，`st:"盘前"` | ✅ 真盘前，但快照于 16:00，滞后 8 分钟 |

- **判定证据**：live.json 的 143 只美股里，**137 只的价格与涨跌幅和昨收完全一致**（其余 6 只仅小数取整差异，如 DJI 52348.39 vs 52348）。即新浪 gb_ 的 `[1]当前价` 盘前只返回昨收。
  同一时刻 Yahoo `r[17]` 有 155 只带 `st:"盘前"`，且**全部 ≠ 昨收**（NVDA 盘前 204.985 vs 昨收 204.12）。
- **副作用（已修）**：`applyLive()` 每 60 秒用新浪的昨收覆盖掉 `applyExt()` 已取到的 Yahoo 盘前价，盘前显示反而倒退成昨收。已在前端置 `LIVE_JSON_TRUSTED = false` 关闭该覆盖。

### 待办：把 ECS 的 live.json 数据源换成 Yahoo（换完即恢复分钟档）
1. 改 `/root/us-live.sh`：改调 `https://query1.finance.yahoo.com/v8/finance/chart/<SYM>?range=1d&interval=1m&includePrePost=true`，
   取 `chart.result[0].meta.preMarketPrice` / `postMarketPrice`（及对应 previousClose 算涨跌幅）。带 UA + cookie/crumb 缓解 429，参考 `scripts/update_quotes.py` 的 `fetch_pe_map`。
2. **输出结构保持不变**：`{"t":<unix秒>,"q":{"<CODE>":{"p":<价>,"c":<涨跌幅%>}}}`，前端无需再改。
3. 前端把 `LIVE_JSON_TRUSTED` 改回 `true`，盘前即回到分钟档。
4. 自检：`curl -s https://stock.ziyuanai.top/data/live.json | python3 -c "import json,sys;j=json.load(sys.stdin);print(j['q'].get('NVDA'))"`，
   盘前时段该值应 ≠ 昨收，且随时间跳动。

### Codex 执行记录（2026-07-08）
- `https://stock.ziyuanai.top/` 已启用 Let's Encrypt 证书，HTTP 自动 301 到 HTTPS；证书 SAN 为 `DNS:stock.ziyuanai.top`，到期日 2026-10-06。
- `https://stock.ziyuanai.top/data/live.json` 返回 200，`Cache-Control: no-store`，当前包含 143 个报价，外部验收时数据年龄约 10-40 秒。
- ECS 站点同步已改为 `/root/us-stock-sync.sh` 每 2 分钟从 `https://codeload.github.com/chenyanchong321/us-stock-notes/tar.gz/refs/heads/main` 拉取并 `rsync` 到 `/var/www/us-stock`，同时保留 `data/live.json`。
- GitHub Pages 老入口 `https://chenyanchong321.github.io/us-stock-notes/` 仍返回 200；ECS 与 GitHub Pages 首页大小一致，均包含 `applyLive()`。
