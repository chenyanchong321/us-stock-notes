#!/usr/bin/env python3
"""生成财多多与刻度点位的私有校对报告；不发布、不改两套事实源。"""

import argparse
from collections import Counter
from pathlib import Path

import server


RELATION_TEXT = {
    "overlap": "重合",
    "near": "接近",
    "different": "有差异",
    "divergent": "明显分歧",
    "uncomparable": "暂不可比",
}


def band_text(bands):
    if not bands:
        return "—"
    values = []
    for band in bands:
        if len(band) < 2:
            continue
        lo, hi = band[0], band[-1]
        values.append(str(lo) if lo == hi else f"{lo}–{hi}")
    return " / ".join(values) or "—"


def kedu_band_text(item):
    bands = (item or {}).get("bands") or {}
    return " / ".join(
        f"{key.upper()} {band_text([value])}" for key, value in bands.items() if value
    ) or "—"


def build_report(items, caido_path, kedu_path):
    relations = Counter(item.get("relation") or "uncomparable" for item in items)
    both = sum(bool(item["sources"].get("caiduoduo") and item["sources"].get("kedu")) for item in items)
    caido_only = sum(bool(item["sources"].get("caiduoduo") and not item["sources"].get("kedu")) for item in items)
    kedu_only = sum(bool(item["sources"].get("kedu") and not item["sources"].get("caiduoduo")) for item in items)
    lines = [
        "# 刻度点位双来源校对（Codex）",
        "",
        "> 私有审计文件。只比较两套判断，不把它们平均成一个数，也不自动发布任何候选。",
        "",
        f"- 财多多源：`{caido_path}`",
        f"- 刻度源：`{kedu_path}`",
        f"- 并集：{len(items)} 只；两边都有 {both} 只；仅财多多 {caido_only} 只；仅刻度 {kedu_only} 只。",
        "- 关系：" + "；".join(f"{RELATION_TEXT.get(key, key)} {value} 只" for key, value in sorted(relations.items())),
        "",
        "## 需要优先人工复核",
        "",
    ]
    review = [item for item in items if item.get("relation") in {"divergent", "different"}]
    if not review:
        lines.append("- 当前自动比较未发现明显分歧；`暂不可比` 仍可能只是单来源或原文无法可靠解析，不代表已经一致。")
    for item in review:
        caido = item["sources"].get("caiduoduo") or {}
        kedu = item["sources"].get("kedu") or {}
        lines.append(
            f"- {item['company']}（{item['code']}）：{RELATION_TEXT.get(item['relation'], item['relation'])}；"
            f"财多多 `{caido.get('text') or '—'}`；刻度 `{kedu_band_text(kedu)}`。"
        )
    lines.extend(["", "## 全量对照", "", "| 标的 | 市场 | 财多多 | 刻度 | 关系 |", "|---|---|---|---|---|"])
    for item in items:
        caido = item["sources"].get("caiduoduo") or {}
        kedu = item["sources"].get("kedu") or {}
        raw = str(caido.get("text") or "—").replace("|", "／")
        lines.append(
            f"| {item['company']}（{item['code']}） | {item.get('market') or '—'} | {raw} | "
            f"{kedu_band_text(kedu)} | {RELATION_TEXT.get(item.get('relation'), item.get('relation'))} |"
        )
    lines.extend([
        "",
        "## 口径说明",
        "",
        "- `重合`：两套第一观察位有交集；`接近`：中点差不超过 10%；`明显分歧`：中点差超过 20%。",
        "- 财多多原文无法可靠解析时只保留原文，标为 `暂不可比`，不猜测数字。",
        "- 本报告只用于发现需要人工复核的条目；线上仍只读取状态为 `current` 的刻度记录。",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--caiduoduo", required=True)
    parser.add_argument("--kedu", required=True)
    parser.add_argument("--quotes", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    server.POINTS = args.caiduoduo
    server.KEDU_POINTS = args.kedu
    server.STATIC_QUOTES = args.quotes
    server.LIVE_QUOTES = ""
    items = server.aggregate_kedu_points()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(items, args.caiduoduo, args.kedu) + "\n", encoding="utf-8")
    print(f"双来源校对 {len(items)} 只 → {output}")


if __name__ == "__main__":
    main()
