# 行情源延时探针（判断某个数据源是不是"实时"）

> 用途：**在市场交易时段内**测量数据源落后真实行情多少分钟。
> 背景：2026-07-10 实测发现腾讯免费港股行情延时 15 分钟——13:56 的价，14:11 才从实时接口返回。
> 教训：**任何新数据源上线前必须先用本探针测过**，不要因为"时间戳很新"就以为数据是新的（新浪 `gb_` 就是时间戳新、内容是昨收）。

## 原理

腾讯有两套接口：

- **实时快照** `qt.gtimg.cn/q=<code>` —— 免费源可能被延时
- **当日分时** `web.ifzq.gtimg.cn/appstock/app/...Minute/query?code=<code>` —— 逐分钟记录真实成交价

在交易时段内，如果快照返回的值等于分时里 **N 分钟前** 的那条记录，那么该源的延时就是 N 分钟。

## 用法

**必须在对应市场的交易时段内运行**（收盘后所有源都等于收盘价，测不出任何东西）：

| 市场 | 交易时段（北京时间） | 分时接口路径 |
|---|---|---|
| A股 | 09:30–15:00 | `app/minute/query` |
| 港股 | 09:30–16:00 | `app/hkMinute/query` |
| 美股 | 21:30–04:00（夏令时） | `app/usMinute/query` |

在页面（`https://stock.ziyuanai.top`）的浏览器控制台粘贴运行：

```js
(async (code, minutePath) => {
  // 1) 取实时快照
  await new Promise(res=>{const s=document.createElement('script');s.charset='GBK';
    s.src='https://qt.gtimg.cn/q='+code+'&_='+Date.now(); s.onload=res;s.onerror=res;document.head.appendChild(s);});
  await new Promise(r=>setTimeout(r,1200));
  const live = parseFloat((window['v_'+code]||'').split('~')[3]);

  // 2) 取当日分时
  await new Promise(res=>{const s=document.createElement('script');
    s.src=`https://web.ifzq.gtimg.cn/appstock/app/${minutePath}/query?_var=MP&code=${code}&r=`+Math.random();
    s.onload=res;s.onerror=res;document.head.appendChild(s);});
  await new Promise(r=>setTimeout(r,1500));
  const d = window.MP.data[code].data;
  const day = Object.keys(d).find(k=>/^\d{4}-\d{2}-\d{2}$/.test(k)) || Object.keys(d)[0];
  const arr = (d[day] && d[day].data) || d.data || [];

  // 3) 分时里最后一条 = 真实最新价；快照值出现在分时的哪一分钟？
  const last = arr[arr.length-1].split(' ');
  const hit  = [...arr].reverse().find(x => Math.abs(parseFloat(x.split(' ')[1]) - live) < 1e-6);
  const toMin = t => (+t.slice(0,2))*60 + (+t.slice(2));
  const lag = hit ? toMin(last[0]) - toMin(hit.split(' ')[0]) : null;

  console.log({
    代码: code,
    实时快照返回: live,
    分时最新一条: last[0] + ' → ' + last[1],
    快照值对应分时时刻: hit ? hit.split(' ')[0] : '未找到',
    延时分钟: lag,
    判定: lag === null ? '无法判定' : (lag <= 1 ? '✅ 实时' : `❌ 延时约 ${lag} 分钟`)
  });
})('hk03750', 'hkMinute');   // ← 改这两个参数测别的市场
```

调用示例：

```js
('sz300750', 'minute')    // A股
('hk03750',  'hkMinute')  // 港股
('usNVDA',   'usMinute')  // 美股
```

## 待测清单（2026-07-10 起）

| 市场 | 行数 | 腾讯是否延时 | 状态 |
|---|---|---|---|
| 港股 | 41 | **延时 15 分钟** | ✅ 已实测确认 |
| 美股（盘中） | 189 | 未知（疑似延时） | ⏳ 待今晚 21:30 后测 |
| A股 | 107 | 未知（疑似实时） | ⏳ 待次日 09:30 后测 |
| 日股 / 韩股 / 台股 | 43 | 未知 | ⏳ 低优先级，分时端点待找 |

> 注：美股**盘前/盘后**已不走腾讯，改由 ECS 抓 Nasdaq 写入 `live.json`（分钟档），不受此问题影响。

## 候选替代源（同样要先用探针测过再上线）

- **腾讯分时接口本身**：分时数据里记录的是真实价格。若在盘中拉取时它的末条就是当前价，那它没被延时——这将是最省事的修法（同一家、同样免费、无需新依赖）。**优先测这个。**
- **Nasdaq** `api.nasdaq.com`：美股盘前已在用，ECS 可直连。需确认常规时段是否实时。
- **东方财富** `push2.eastmoney.com`：浏览器 CORS 不通，须走 ECS。免费港股/美股很可能同样延时 15 分钟，**别想当然**。
- **富途 OpenAPI（OpenD）**：真实时，但需本地常驻富途客户端，不适合服务器端自动化。
