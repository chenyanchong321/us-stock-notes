/* 文章自动目录（2026-07-18 主人需求：所有长文可点击跳转章节）。
   用法：文章 </body> 前加 <script defer src="toc.js"></script>，零配置。
   行为：扫描 article 内 h1/h2 → 生成目录卡（插在 .head 之后）；
   标题文本为「目录」的手写目录块整段隐藏（避免与自动目录重复）；
   点击平滑滚动（吸顶栏补偿）；滚过目录后右下角出现 ☰ 浮钮一键回目录；
   章节标题少于3个的短文不生成（没必要）。 */
(function(){
  function init(){
    var art = document.querySelector("article") || document.querySelector("main");
    if(!art) return;
    var hs = [].slice.call(art.querySelectorAll("h1,h2"));

    /* 隐藏手写目录块：标题=「目录」→ 该标题与其后兄弟节点直到下一个 h1/h2 全部隐藏 */
    var hidden = [];
    hs.forEach(function(h){
      if(h.textContent.trim().replace(/^[📑📖\s]+/,"") === "目录"){
        hidden.push(h);
        var n = h.nextElementSibling;
        h.style.display = "none";
        while(n && !/^H[12]$/.test(n.tagName)){ n.style.display = "none"; n = n.nextElementSibling; }
      }
    });

    var items = hs.filter(function(h){ return hidden.indexOf(h) < 0; });
    if(items.length < 3) return;

    /* 样式（脚本自带，文章文件零改动） */
    var st = document.createElement("style");
    st.textContent =
      "html{scroll-behavior:smooth}article h1,article h2,article h3{scroll-margin-top:74px}" +
      "#autotoc{background:var(--panel,#f6f8fa);border:1px solid var(--border,#e2e5e9);border-radius:10px;padding:14px 18px;margin:0 0 28px}" +
      "#autotoc .t{font-weight:700;font-size:15px;margin-bottom:8px}" +
      "#autotoc .ls{}" +
      "@media(min-width:640px){#autotoc .ls{column-count:2;column-gap:30px}}" +
      "#autotoc a{display:block;color:var(--accent,#2563eb);text-decoration:none;font-size:14px;line-height:1.55;padding:2.5px 0;break-inside:avoid}" +
      "#autotoc a:hover{text-decoration:underline}" +
      "#autotoc a[data-lv='2']{padding-left:1.1em;color:var(--text2,#6b7280)}" +
      "#autotoc a[data-lv='2']:hover{color:var(--accent,#2563eb)}" +
      "#tocfab{position:fixed;right:16px;bottom:18px;z-index:60;width:42px;height:42px;border-radius:50%;" +
      "background:var(--panel,#f6f8fa);border:1px solid var(--border,#e2e5e9);color:var(--text2,#6b7280);" +
      "font-size:17px;cursor:pointer;display:none;align-items:center;justify-content:center;box-shadow:0 3px 10px rgba(0,0,0,.12)}" +
      "#tocfab:hover{color:var(--accent,#2563eb);border-color:var(--accent,#2563eb)}";
    document.head.appendChild(st);

    /* 目录卡：一级=文内h1（多章大部头），二级=h2；纯h2文章全部按一级排 */
    var hasH1 = items.some(function(h){ return h.tagName === "H1"; });
    var links = items.map(function(h, i){
      if(!h.id) h.id = "sec-toc-" + i;
      var lv = (hasH1 && h.tagName === "H2") ? 2 : 1;
      return '<a href="#' + h.id + '" data-lv="' + lv + '">' + h.textContent.trim() + "</a>";
    }).join("");
    var box = document.createElement("nav");
    box.id = "autotoc";
    box.innerHTML = '<div class="t">📑 目录 · 点击跳转</div><div class="ls">' + links + "</div>";
    var head = document.querySelector(".head");
    if(head && head.parentElement) head.parentElement.insertBefore(box, head.nextElementSibling);
    else art.insertBefore(box, art.firstChild);

    /* ☰ 浮钮：滚过目录卡后出现，点击回目录 */
    var fab = document.createElement("button");
    fab.id = "tocfab"; fab.title = "回到目录"; fab.textContent = "☰";
    fab.addEventListener("click", function(){ box.scrollIntoView({behavior:"smooth", block:"start"}); });
    document.body.appendChild(fab);
    var tick = false;
    window.addEventListener("scroll", function(){
      if(tick) return; tick = true;
      requestAnimationFrame(function(){
        tick = false;
        fab.style.display = (window.scrollY > box.offsetTop + box.offsetHeight + 100) ? "flex" : "none";
      });
    }, {passive:true});
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
