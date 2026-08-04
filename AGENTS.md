# AGENTS.md — Codex 接手入口

> 给任何新窗口里的 Codex：先读本文件，再读 `CLAUDE.md`。`CLAUDE.md` 是项目事实源，本文件只补 Codex 的操作路径和防踩坑规则。

## 必读顺序

1. 先读 `CLAUDE.md`，了解项目架构、数据口径、发布规则和历史坑。
2. 如果任务涉及盘前/盘后分钟档，再读对应交接单，尤其是验收命令和回退方案。
3. 动手前先看 `git status` 和最新提交。自动行情任务会频繁提交数据，推送前必须先拉最新主线。

## 工作副本规则

- 本地挂载目录 `01_公司投研/us-stock-notes` 只当烟囱本地镜像，不在里面 commit/rebase/reset。
- 正确做法：复制或 clone 到 `/tmp` 等临时工作目录里修改、提交、推送。
- 推送后再回本地镜像执行 `git pull --ff-only` 同步；如果遇到 `.git/index.lock`，不要强删，告知烟囱或换有权限的窗口处理。

## ECS 改动铁律

- ECS（Elastic Compute Service，阿里云云服务器）上的 `/root` 文件禁止手工改。
- 所有服务器脚本改动必须先落到仓库里的 `scripts/deploy-ecs-live.sh`，提交并推送后，再让服务器运行这个部署脚本。
- 不把 token、cookie、密码、验证码写入仓库；遇到敏感输入让烟囱本人在浏览器或服务器终端完成。

## 阿里云控制台部署 SOP

如果 SSH（Secure Shell，远程命令通道）直连失败，不要反复绕路；直接用阿里云 ECS 控制台的「全部操作」→「发送命令」。

推荐路径：

1. 在本地临时工作副本完成代码修改、校验、提交、推送。
2. 打开阿里云 ECS 实例 `118.31.109.150`，进入「发送命令」。
3. 命令编辑器里不要用 Computer Use 逐字输入长命令；它可能把 `_`、`|`、`>` 等符号打坏。用剪贴板粘贴，粘贴后先肉眼核对，再点「立即执行」。
4. 不要贴长 here-doc。控制台编辑器滚动和选区不稳定，容易执行残缺内容。用一条短命令启动后台部署，把详细输出写到日志。

当前实时行情部署可用这条短命令：

```bash
nohup bash -lc '/root/us-stock-sync.sh && bash /var/www/us-stock/scripts/deploy-ecs-live.sh && /root/us-live.sh' >/root/us-stock-deploy.log 2>&1 &
echo DEPLOY_STARTED
```

如果需要防止服务器同步到旧代码，可在中间加一个与本次改动相关的 `grep -q` 断言，例如检查新常量、新函数名或新文案；断言必须来自仓库脚本，不能用它手工改服务器文件。

## 部署后验收

先跑服务器只读检查，确认脚本和定时任务已落地：

```bash
echo CHECK
grep -q YAHOO_COOLDOWN_SECONDS /root/us-live.py && echo cooldown_ok || true
grep -q '"src": src' /root/us-live.py && echo src_writer_ok || true
echo CRON
crontab -l | grep -E 'us-live|us-stock-sync|us-stock-trigger' || true
echo LIVE
python3 -c 'import json,time;j=json.load(open("/var/www/us-stock/data/live.json"));print(j.get("src"),j.get("n"),len(j.get("q",{})),int(time.time())-j.get("t",0),sorted(j.keys()))'
echo LOG
tail -n 20 /root/us-live.log 2>/dev/null | grep -E 'LIVE_WARN yahoo|LIVE_INFO yahoo cooldown|LIVE_OK' || true
```

再从公网域名验收：

```bash
python3 - <<'PY'
import json, time, urllib.request
base = 'https://stock.ziyuanai.top'
now = str(int(time.time()))
j = json.load(urllib.request.urlopen(base + '/data/live.json?x=' + now, timeout=20))
html = urllib.request.urlopen(base + '/?x=' + now, timeout=20).read().decode('utf-8', 'ignore')
print('live', {'src': j.get('src'), 'n': j.get('n'), 'q_len': len(j.get('q', {})), 'age': int(time.time()) - j.get('t', 0), 'keys': sorted(j.keys())})
print('sample', {k: j.get('q', {}).get(k) for k in ['IAU', 'GDX', 'SLV', 'NVDA', 'TLN', 'DRAM', 'SPY']})
print('html', {'fallback15': '美股盘前(15分钟档)' in html and '美股盘后(15分钟档)' in html, 'fallback6': '美股盘前(6分钟档)' in html or '美股盘后(6分钟档)' in html, 'trusted': 'LIVE_JSON_TRUSTED = true' in html})
PY
```

验收要点：

- `live.json` 必须有 `t/src/n/q`。
- `q_len` 应接近当前 watchlist 的美股覆盖数量；2026-07-09 收尾时是 139。
- IAU/GDX/SLV 必须存在，这是 ETF 标签和 Nasdaq ETF 端点是否正确的烟雾测试。
- 如果日志里出现一次 Yahoo 403 后进入 `yahoo cooldown`，是预期；如果每分钟反复 `LIVE_WARN yahoo`，说明冷却没有生效。

## 不要改的已知现象

- `PGJ/BRKR/NDSN`：看起来像昨收或涨跌幅不动时，先按交接单判断是否属于 Nasdaq 无盘前成交退回常规口径，不要直接改前端守卫阈值。
- 前端 `applyLive()` 的整批守卫和逐只守卫是数据质量防线。若触发 `疑似昨收快照`，优先查数据源字段和 `marketState/preMarketPrice`，不要调阈值掩盖问题。
