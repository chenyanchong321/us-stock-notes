#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ECS 站点同步（2026-08-04 终版）：GitHub tree API + 逐文件增量下载。
背景：仓库带音频/PDF后超500MB，tarball(5分钟超时)与 git clone(单流中途断)都无法在
ECS↔GitHub 的不稳定链路上完成。本方案把传输拆成逐文件粒度：
- 每2分钟：拉 main 的文件清单(1个API调用)，与本地 manifest 比对，只下载变化的文件；
- 每个文件写盘即更新 manifest —— 任何一班被打断，下一班从断点继续（收敛性保证）；
- 首次运行自动用本地现有文件的 git blob sha 引导 manifest，不重复下载已有内容。
依赖 /root/.gh_token（PAT，续期时本脚本随之续命——token 过期站点同步会停）。"""
import json, os, sys, time, hashlib, subprocess, urllib.request

REPO = "chenyanchong321/us-stock-notes"
SITE = "/var/www/us-stock"
MAN  = "/root/us-sync-manifest.json"
TOK  = open("/root/.gh_token").read().strip()

def api(url, raw=False, timeout=120):
    req = urllib.request.Request(url, headers={
        "User-Agent": "us-sync",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "Authorization": "Bearer " + TOK})
    return urllib.request.urlopen(req, timeout=timeout)

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()

def main():
    tree = json.load(api(f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"))
    if tree.get("truncated"):
        print("SYNC_ERR tree truncated"); sys.exit(1)
    files = {e["path"]: e["sha"] for e in tree["tree"] if e["type"] == "blob"}

    if os.path.exists(MAN):
        man = json.load(open(MAN))
    else:  # 首次引导：用磁盘现有文件反推 sha，避免整站重下
        man = {}
        for p in files:
            dst = os.path.join(SITE, p)
            if os.path.isfile(dst):
                man[p] = blob_sha(open(dst, "rb").read())
        json.dump(man, open(MAN, "w"))
        print("BOOTSTRAP manifest from disk:", len(man), "files")

    changed = [p for p, s in files.items() if man.get(p) != s]
    for p in changed:
        data = api(f"https://api.github.com/repos/{REPO}/git/blobs/{files[p]}",
                   raw=True, timeout=600).read()
        dst = os.path.join(SITE, p)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".syncing"
        with open(tmp, "wb") as f: f.write(data)
        os.replace(tmp, dst)
        man[p] = files[p]
        json.dump(man, open(MAN, "w"))   # 每文件记账＝断点续传
        print("GET", p, len(data))

    removed = 0
    for p in list(man):
        if p not in files:
            try: os.remove(os.path.join(SITE, p)); removed += 1
            except FileNotFoundError: pass
            del man[p]
    json.dump(man, open(MAN, "w"))

    if changed or removed:
        subprocess.run(["chown", "-R", "www-data:www-data", SITE], check=False)
        subprocess.run(f'find "{SITE}" -type d -exec chmod 755 {{}} + ; find "{SITE}" -type f -exec chmod 644 {{}} +',
                       shell=True, check=False)
    print("SYNC_OK", len(changed), "changed,", removed, "removed,", time.strftime("%F %T"))

if __name__ == "__main__":
    main()
