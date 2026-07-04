# 烟囱的美股学习笔记

个人投资数据面板 + 学习笔记分享站。

## 目录结构

```
├── index.html                    首页（读 JSON 渲染，一般不用改）
├── config/
│   ├── watchlist.json            ★ 关注池配置：加股票/加板块改这里
│   └── notes.json                ★ 笔记目录：加笔记改这里
├── data/
│   └── quotes.json               行情数据（自动生成，勿手改）
├── articles/                     笔记文章页（网页版，含图片）
├── notes/                        笔记 PDF 存档（文章页内可下载）
├── scripts/
│   └── update_quotes.py          行情更新脚本
└── .github/workflows/
    └── update-data.yml           GitHub Actions：每天北京时间 5:30 自动更新
```

## 日常维护

**加一只股票**：在 `config/watchlist.json` 对应板块的 `items` 里加一条：

```json
{
 "name": "铠侠", "code": "285A", "market": "日股",
 "yahoo": "285A.T", "currency": "¥",
 "mcap_base": {"prefix": "¥", "yi": 20000, "approx": false},
 "mcap_base_price": 3000,
 "ath_floor": 0
}
```

字段说明：`yahoo` 是 Yahoo Finance 代码（A股加 `.SS`/`.SZ`，港股加 `.HK`，日股加 `.T`）；
`mcap_base` + `mcap_base_price` 是"某时点的市值（单位：亿）和当时股价"，用于按最新价滚动估算市值；
`ath_floor` 是历史高点兜底值（行情源历史不全时用），可填 0。

**加一篇笔记**：文章 HTML 放进 `articles/`，PDF 放进 `notes/`，然后在 `config/notes.json` 加一条卡片记录。

**手动刷新数据**：GitHub 仓库页 → Actions → 每日更新行情数据 → Run workflow；
或本地运行 `python3 scripts/update_quotes.py`。

## 免责声明

本站内容为个人学习笔记与数据整理，仅作知识分享，不构成任何投资建议。
