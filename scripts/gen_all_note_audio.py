#!/usr/bin/env python3
# 批量音频生成（在ECS上运行）：读 config/notes.json 全部文章 → 逐篇调 gen_note_audio.py
# 用法: nohup python3 gen_all_note_audio.py > /root/audio_batch.log 2>&1 &
import json, re, subprocess, urllib.request

REPO = "chenyanchong321/us-stock-notes"
TOKEN = open("/root/.gh_token").read().strip()
VOICE = "zh-CN-YunyangNeural"   # 全站标准台声（2026-07-19 主人定）

def gh_raw(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}")
    req.add_header("Authorization", "token " + TOKEN)
    req.add_header("Accept", "application/vnd.github.raw")
    return urllib.request.urlopen(req, timeout=60).read()

notes = json.loads(gh_raw("contents/config/notes.json"))
slugs = []
for c in notes["cats"]:
    for it in c["items"]:
        m = re.match(r"articles/([^/]+)\.html", it.get("page", ""))
        if m and m.group(1) not in slugs:
            slugs.append(m.group(1))
print("共", len(slugs), "篇", flush=True)
fail = []
for i, s in enumerate(slugs):
    print(f"[{i+1}/{len(slugs)}] {s}", flush=True)
    r = subprocess.run(["python3", "/root/gna.py", s, VOICE], capture_output=True, text=True)
    print(r.stdout.strip(), flush=True)
    if r.returncode != 0:
        fail.append(s)
        print("FAIL:", r.stderr[-300:], flush=True)
print("完成。失败:", fail if fail else "无", flush=True)
