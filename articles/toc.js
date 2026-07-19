/* 文章自动目录 v2（2026-07-18 主人定稿，参照 deepread.to 文档阅读器布局）。
   用法：文章 </body> 前加 <script defer src="toc.js"></script>，零配置。
   桌面(≥1280px)：目录常驻左侧栏，随滚动高亮当前章节，点击瞬时跳转（文首不再放目录卡）。
   窄屏/手机：文首目录卡（开篇总览）+ 右下 ☰ 弹出目录抽屉（就地选章，不用回顶部）。
   手写「目录」块自动隐藏；章节<3不生成。跳转一律瞬时（smooth 长文动画慢且易被吞，铁律）。 */
(function(){
  var WIDE = "(min-width:1280px)";
  function init(){
    var art = document.querySelector("article") || document.querySelector("main");
    if(!art) return;
    var hs = [].slice.call(art.querySelectorAll("h1,h2"));

    /* 隐藏手写目录块 */
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

    var hasH1 = items.some(function(h){ return h.tagName === "H1"; });
    items.forEach(function(h,i){ if(!h.id) h.id = "sec-toc-" + i; });
    function linksHTML(cls){
      return items.map(function(h){
        var lv = (hasH1 && h.tagName === "H2") ? 2 : 1;
        return '<a class="' + cls + '" href="#' + h.id + '" data-tid="' + h.id + '" data-lv="' + lv + '">' + h.textContent.trim() + "</a>";
      }).join("");
    }

    var st = document.createElement("style");
    st.textContent =
      /* 全站铁律注入（2026-07-18 主人定）：①禁 iOS 字体膨胀（宽表格文字被系统放大的元凶）；
         ②表格单元格直接换行、永不横向滚动（用户不会也不该左右滑） */
      "html{-webkit-text-size-adjust:100%;text-size-adjust:100%}" +
      "article table{table-layout:auto;word-break:break-word;max-width:100%}" +
      "article th,article td{white-space:normal}" +
      "@media(max-width:640px){article table{font-size:12.5px}article th,article td{padding:6px 7px}}" +
      "article h1,article h2,article h3{scroll-margin-top:74px}" +
      /* 文首目录卡（窄屏用） */
      "#autotoc{background:var(--panel,#f6f8fa);border:1px solid var(--border,#e2e5e9);border-radius:10px;padding:14px 18px;margin:0 0 28px}" +
      "#autotoc .t{font-weight:700;font-size:15px;margin-bottom:8px}" +
      "@media(min-width:640px){#autotoc .ls{column-count:2;column-gap:30px}}" +
      "@media" + WIDE + "{#autotoc{display:none}}" +
      "#autotoc a{display:block;color:var(--accent,#2563eb);text-decoration:none;font-size:14px;line-height:1.55;padding:2.5px 0;break-inside:avoid}" +
      "#autotoc a:hover{text-decoration:underline}" +
      "#autotoc a[data-lv='2']{padding-left:1.1em;color:var(--text2,#6b7280)}" +
      /* 左侧常驻目录（桌面） */
      "#tocside{display:none}" +
      "@media" + WIDE + "{#tocside{display:block;position:fixed;z-index:5;top:86px;bottom:30px;width:224px;" +
      "left:max(14px,calc(50% - 380px - 250px));overflow-y:auto;overscroll-behavior:contain;" +
      "font-size:13px;padding-right:6px}}" +
      "#tocside .t{font-weight:700;font-size:13px;color:var(--text2,#6b7280);margin:0 0 8px;letter-spacing:.5px}" +
      "#tocside a{display:block;color:var(--text2,#6b7280);text-decoration:none;line-height:1.5;padding:4px 8px;" +
      "border-left:2px solid var(--border,#e2e5e9);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
      "#tocside a:hover{color:var(--accent,#2563eb)}" +
      "#tocside a[data-lv='2']{padding-left:20px}" +
      "#tocside a.cur{color:var(--accent,#2563eb);border-left-color:var(--accent,#2563eb);font-weight:600;background:rgba(37,99,235,.06)}" +
      /* ☰ 抽屉（窄屏） */
      "#tocfab{position:fixed;right:16px;bottom:18px;z-index:60;width:42px;height:42px;border-radius:50%;" +
      "background:var(--panel,#f6f8fa);border:1px solid var(--border,#e2e5e9);color:var(--text2,#6b7280);" +
      "font-size:17px;cursor:pointer;display:none;align-items:center;justify-content:center;box-shadow:0 3px 10px rgba(0,0,0,.12)}" +
      "#tocfab:hover{color:var(--accent,#2563eb);border-color:var(--accent,#2563eb)}" +
      "@media" + WIDE + "{#tocfab{display:none !important}}" +
      /* ↑ 回到顶部（2026-07-18 主人定：☰管换章、↑管回顶，两个动作都要有）：窄屏叠在☰上方，宽屏（☰隐藏）落回下角 */
      "#toctop{position:fixed;right:16px;bottom:70px;z-index:60;width:42px;height:42px;border-radius:50%;" +
      "background:var(--panel,#f6f8fa);border:1px solid var(--border,#e2e5e9);color:var(--text2,#6b7280);" +
      "font-size:17px;cursor:pointer;display:none;align-items:center;justify-content:center;box-shadow:0 3px 10px rgba(0,0,0,.12)}" +
      "#toctop:hover{color:var(--accent,#2563eb);border-color:var(--accent,#2563eb)}" +
      "@media" + WIDE + "{#toctop{bottom:18px}}" +
      "#tocdrw{position:fixed;inset:0;z-index:120;display:none}" +
      "#tocdrw.show{display:block}" +
      "#tocdrw .msk{position:absolute;inset:0;background:rgba(0,0,0,.45)}" +
      "#tocdrw .pan{position:absolute;left:0;top:0;bottom:0;width:min(78vw,320px);background:var(--bg,#fff);" +
      "border-right:1px solid var(--border,#e2e5e9);padding:18px 14px calc(18px + env(safe-area-inset-bottom));overflow-y:auto;overscroll-behavior:contain}" +
      "#tocdrw .t{font-weight:700;font-size:15px;margin-bottom:10px}" +
      "#tocdrw a{display:block;color:var(--text,#1f2328);text-decoration:none;font-size:14.5px;line-height:1.5;" +
      "padding:9px 8px;border-radius:6px;border-left:2px solid transparent}" +
      "#tocdrw a[data-lv='2']{padding-left:24px;color:var(--text2,#6b7280)}" +
      "#tocdrw a.cur{color:var(--accent,#2563eb);background:rgba(37,99,235,.07);border-left-color:var(--accent,#2563eb);font-weight:600}";
    document.head.appendChild(st);

    /* 文首目录卡 */
    var box = document.createElement("nav");
    box.id = "autotoc";
    box.innerHTML = '<div class="t">📑 目录 · 点击跳转</div><div class="ls">' + linksHTML("") + "</div>";
    var head = document.querySelector(".head");
    if(head && head.parentElement) head.parentElement.insertBefore(box, head.nextElementSibling);
    else art.insertBefore(box, art.firstChild);

    /* 左侧栏 */
    var side = document.createElement("nav");
    side.id = "tocside";
    side.innerHTML = '<div class="t">目录</div>' + linksHTML("");
    document.body.appendChild(side);

    /* ☰ + 抽屉 */
    var fab = document.createElement("button");
    fab.id = "tocfab"; fab.title = "目录"; fab.textContent = "☰";
    document.body.appendChild(fab);
    var topBtn = document.createElement("button");
    topBtn.id = "toctop"; topBtn.title = "回到顶部"; topBtn.textContent = "↑";
    topBtn.addEventListener("click", function(){ window.scrollTo(0,0); });
    document.body.appendChild(topBtn);
    var drw = document.createElement("div");
    drw.id = "tocdrw";
    drw.innerHTML = '<div class="msk"></div><div class="pan"><div class="t">📑 目录</div>' + linksHTML("") + "</div>";
    document.body.appendChild(drw);
    fab.addEventListener("click", function(){ drw.classList.add("show"); syncCur(); });
    drw.addEventListener("click", function(e){
      if(e.target.closest("a")){ drw.classList.remove("show"); return; }   /* 原生锚点跳转后关抽屉 */
      if(!e.target.closest(".pan")) drw.classList.remove("show");
    });

    /* 当前章节高亮（滚动驱动，三处列表同步） */
    var curId = "";
    function syncCur(){
      document.querySelectorAll("#tocside a,#tocdrw a").forEach(function(a){
        a.classList.toggle("cur", a.dataset.tid === curId);
      });
      var act = side.querySelector("a.cur");
      if(act) act.scrollIntoView({block:"nearest"});
    }
    /* 节流用 setTimeout 而非 rAF：rAF 在窗口遮挡/后台时暂停，闸门标志会卡死、监听永久失效
       （2026-07-18 实测踩坑）。setTimeout 不受渲染暂停影响。 */
    var last = 0, pend = false;
    function run(){
      last = Date.now();
      var y = 100, id = items[0].id;
      for(var i=0;i<items.length;i++){
        if(items[i].getBoundingClientRect().top <= y) id = items[i].id; else break;
      }
      if(id !== curId){ curId = id; syncCur(); }
      var on = window.scrollY > 300;
      fab.style.display = on ? "flex" : "none";
      topBtn.style.display = on ? "flex" : "none";
    }
    function onScroll(){
      var n = Date.now();
      if(n - last >= 120) run();
      else if(!pend){ pend = true; setTimeout(function(){ pend = false; run(); }, 140); }
    }
    window.addEventListener("scroll", onScroll, {passive:true});
    run();
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

/* ===== 🎧 文章音频 v2（2026-07-19）=====
   自绘播放器（iOS 原生 audio 控件窄容器会缩成只剩播放键、进度条被藏——主人真机抓包，自绘保证可拖）。
   倍速 0.8/1/1.25/1.5/2（0.8=听众点播）；<audio>本体隐藏保留=锁屏后台续播。 */
(function(){
  var m = location.pathname.match(/articles\/([^\/]+)\.html/);
  if(!m) return;
  var src = "../notes/audio/" + m[1] + ".mp3";
  function fmt(t){ if(!isFinite(t)) return "--:--"; var mn=Math.floor(t/60), sc=Math.floor(t%60); return mn+":"+(sc<10?"0":"")+sc; }
  function inject(){
    var h1 = document.querySelector("article h1") || document.querySelector("h1");
    if(!h1) return;
    var box = document.createElement("div");
    box.id = "audiobar";
    box.style.cssText = "margin:14px 0 18px;padding:12px 14px;border:1px solid #ddd;border-radius:12px;background:#fafafa";
    box.innerHTML = '<div style="display:flex;align-items:center;gap:10px">'
      + '<button id="audplay" style="flex:0 0 auto;width:40px;height:40px;border-radius:50%;border:none;background:#333;color:#fff;font-size:15px;cursor:pointer">\u25B6</button>'
      + '<input id="audseek" type="range" min="0" max="1000" value="0" style="flex:1;min-width:0;accent-color:#333;height:26px">'
      + '<span id="audtime" style="flex:0 0 auto;font-size:11.5px;color:#666;font-variant-numeric:tabular-nums">--:-- / --:--</span></div>'
      + '<div style="display:flex;align-items:center;gap:4px;margin-top:8px;flex-wrap:wrap">'
      + '<span style="font-size:12px;color:#555;margin-right:4px">\uD83C\uDFA7 \u6536\u542C\u672C\u6587</span><span id="audspd"></span>'
      + '<span style="font-size:11px;color:#999;margin-left:auto">AI\u6717\u8BFB \u00B7 \u8868\u683C\u56FE\u793A\u8BF7\u770B\u539F\u6587 \u00B7 \u9501\u5C4F\u53EF\u7EED\u64AD</span></div>';
    h1.insertAdjacentElement("afterend", box);
    var au = new Audio(src);
    au.preload = "metadata";
    var play = box.querySelector("#audplay"), seek = box.querySelector("#audseek"), time = box.querySelector("#audtime");
    var rates = [0.8, 1, 1.25, 1.5, 2];
    var cur = 1; try{ cur = parseFloat(localStorage.getItem("audRate")) || 1; }catch(e){}
    if(rates.indexOf(cur)<0) cur = 1;
    var sp = box.querySelector("#audspd");
    function paintSpd(){
      sp.innerHTML = rates.map(function(r){
        return '<button data-r="'+r+'" style="border:1px solid #ccc;background:'+(r===cur?"#333":"#fff")+';color:'+(r===cur?"#fff":"#555")+';border-radius:8px;padding:2px 8px;font-size:11px;cursor:pointer;margin-left:4px">'+r+'x</button>';
      }).join("");
    }
    paintSpd();
    au.playbackRate = cur;
    sp.addEventListener("click", function(e){
      var b = e.target.closest("button"); if(!b) return;
      cur = parseFloat(b.dataset.r); au.playbackRate = cur;
      try{ localStorage.setItem("audRate", cur); }catch(x){}
      paintSpd();
    });
    play.addEventListener("click", function(){
      if(au.paused){ au.play(); } else { au.pause(); }
    });
    au.addEventListener("play", function(){ play.textContent = "\u2759\u2759"; play.style.fontSize="11px"; au.playbackRate = cur; });
    au.addEventListener("pause", function(){ play.textContent = "\u25B6"; play.style.fontSize="15px"; });
    au.addEventListener("ended", function(){ play.textContent = "\u25B6"; });
    function paintTime(){ time.textContent = fmt(au.currentTime) + " / " + fmt(au.duration); }
    au.addEventListener("loadedmetadata", paintTime);
    var dragging = false;
    au.addEventListener("timeupdate", function(){
      if(!dragging && isFinite(au.duration)) seek.value = Math.round(au.currentTime / au.duration * 1000);
      paintTime();
    });
    seek.addEventListener("input", function(){
      dragging = true;
      if(isFinite(au.duration)){ time.textContent = fmt(seek.value/1000*au.duration) + " / " + fmt(au.duration); }
    });
    seek.addEventListener("change", function(){
      if(isFinite(au.duration)) au.currentTime = seek.value/1000*au.duration;
      dragging = false;
    });
  }
  fetch(src, {method:"HEAD"}).then(function(r){ if(r.ok) inject(); }).catch(function(){});
})();
