#!/usr/bin/env python3
# 在 GitHub Actions runner 上运行：本地 checkout 里找"有文章没音频"的笔记，批量生成（云扬+ffmpeg补头）。
# 幂等：已有音频的跳过。本地生成到 notes/audio/，提交由 workflow 负责。
import json, re, os, subprocess, asyncio, html as H

VOICE = "zh-CN-YunyangNeural"

def extract(page):
    page = re.sub(r"<script[\s\S]*?</script>", "", page)
    page = re.sub(r"<style[\s\S]*?</style>", "", page)
    m = re.search(r"<article[\s\S]*?</article>", page)
    if m: page = m.group(0)
    page = re.sub(r"<table[\s\S]*?</table>", "\n此处有一张数据表格，请参见原文。\n", page)
    page = re.sub(r"<(h1|h2|h3)[^>]*>", "\n\n", page)
    page = re.sub(r"</(h1|h2|h3|p|li|div|blockquote)>", "\n", page)
    page = re.sub(r"<br\s*/?>", "\n", page)
    text = re.sub(r"<[^>]+>", "", page)
    text = H.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    i = text.find("免责声明")
    if i > 0: text = text[:i]
    return text.strip()

async def tts(text, out):
    import edge_tts
    c = edge_tts.Communicate(text, VOICE, rate="+8%")
    await c.save(out)

def main():
    notes = json.load(open("config/notes.json"))
    slugs = []
    for c in notes["cats"]:
        for it in c["items"]:
            m = re.match(r"articles/([^/]+)\.html", it.get("page", ""))
            if m and m.group(1) not in slugs:
                slugs.append(m.group(1))
    os.makedirs("notes/audio", exist_ok=True)
    missing = [s for s in slugs if not os.path.exists(f"notes/audio/{s}.mp3")]
    print(f"文章{len(slugs)}篇，缺音频{len(missing)}篇: {missing}")
    for s in missing:
        try:
            text = extract(open(f"articles/{s}.html").read())
            out = f"notes/audio/{s}.mp3"
            asyncio.run(tts(text, out))
            subprocess.run(["ffmpeg","-y","-loglevel","error","-i",out,"-c","copy",out+".fix"], check=True)
            os.replace(out+".fix", out)
            print(f"✓ {s} ({len(text)}字, {os.path.getsize(out)//1024}KB)")
        except Exception as e:
            print(f"✗ {s}: {e}")

if __name__ == "__main__":
    main()
