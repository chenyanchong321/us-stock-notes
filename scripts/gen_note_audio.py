#!/usr/bin/env python3
# 学习笔记音频生成器（2026-07-19，在ECS上运行——沙箱连不上微软TTS）
# 用法: python3 gen_note_audio.py <slug> [voice]
#   拉取 articles/<slug>.html → 提取正文纯文本 → edge-tts 生成MP3 → PUT回 notes/audio/<slug>.mp3
# 依赖: pip3 install edge-tts ；token 读 /root/.gh_token（Contents RW）
import sys, json, base64, re, html as H, asyncio, urllib.request

REPO = "chenyanchong321/us-stock-notes"
TOKEN = open("/root/.gh_token").read().strip()

def gh(path, raw=False, method="GET", body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method)
    req.add_header("Authorization", "token " + TOKEN)
    req.add_header("Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json")
    if body is not None:
        req.data = json.dumps(body).encode()
    return urllib.request.urlopen(req, timeout=120).read()

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
    if i > 0: text = text[:i]          # 免责声明不朗读（冗长）
    return text.strip()

async def tts(text, out, voice):
    import edge_tts
    c = edge_tts.Communicate(text, voice, rate="+8%")
    await c.save(out)

def main():
    slug = sys.argv[1]
    voice = sys.argv[2] if len(sys.argv) > 2 else "zh-CN-YunxiNeural"
    outslug = sys.argv[3] if len(sys.argv) > 3 else slug   # 可选：输出变体名（音色A/B对比用）
    page = gh(f"contents/articles/{slug}.html", raw=True).decode()
    text = extract(page)
    print("正文字数:", len(text))
    out = f"/root/{outslug}.mp3"
    asyncio.run(tts(text, out, voice))
    data = open(out, "rb").read()
    print("MP3大小:", len(data)//1024, "KB")
    path = f"contents/notes/audio/{outslug}.mp3"
    sha = None
    try:
        sha = json.loads(gh(path)).get("sha")
    except Exception:
        pass
    body = {"message": f"audio: {slug}", "content": base64.b64encode(data).decode()}
    if sha: body["sha"] = sha
    gh(path, method="PUT", body=body)
    print("已上传 notes/audio/%s.mp3" % slug)

if __name__ == "__main__":
    main()
