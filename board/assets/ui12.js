/* ui12.js — neuro_12 v2.37: тема/акцент, блоки кода с «Копировать», горячие клавиши.
   Общий для index.html и cloud.html; без зависимостей; безопасен при отсутствии элементов. */
"use strict";
(function () {
  var LS_T = "n12_theme", LS_A = "n12_accent";
  var ACC = [["teal", "#2dd4bf", "Бирюза"], ["violet", "#a78bfa", "Фиолет"], ["blue", "#60a5fa", "Лазурь"],
    ["amber", "#fbbf24", "Янтарь"], ["rose", "#fb7185", "Роза"]];
  var TH = [["dark", "Тёмная", "☾"], ["light", "Светлая", "☀"], ["auto", "Авто", "◐"]];
  function get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function isLight(m) { return m === "light" || (m === "auto" && window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches); }
  function apply() {
    var m = get(LS_T, "dark"), a = get(LS_A, "teal"), r = document.documentElement;
    r.setAttribute("data-theme", isLight(m) ? "light" : "dark");
    if (a === "teal") r.removeAttribute("data-accent"); else r.setAttribute("data-accent", a);
    var b = document.getElementById("btn-theme");
    if (b) { var t = TH.filter(function (x) { return x[0] === m; })[0] || TH[0]; b.textContent = t[2]; b.title = "Тема: " + t[1].toLowerCase() + " (Alt+T)"; }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", isLight(m) ? "#f6f7fb" : "#0f1115");
  }
  function cycle() {
    var m = get(LS_T, "dark"), i = 0;
    TH.forEach(function (x, n) { if (x[0] === m) i = n; });
    set(LS_T, TH[(i + 1) % TH.length][0]); apply();
  }
  apply();   /* сразу: без «вспышки» темы */
  if (window.matchMedia) {
    var mq = matchMedia("(prefers-color-scheme: light)");
    (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(function () { if (get(LS_T, "dark") === "auto") apply(); });
  }

  /* ---- блоки кода: язык + копировать + скачать ---- */
  var EXT = { python: "py", py: "py", javascript: "js", js: "js", cpp: "ino", ino: "ino", arduino: "ino", c: "c",
    html: "html", bash: "sh", sh: "sh", sql: "sql", json: "json" };
  var NAME = { python: "Python", py: "Python", javascript: "JavaScript", js: "JavaScript", cpp: "Arduino / C++",
    ino: "Arduino", html: "HTML", bash: "Bash", sh: "Bash", sql: "SQL", json: "JSON", c: "C" };
  function decoratePre(pre) {
    if (pre.querySelector(".pre12-bar")) return;
    var lang = (pre.getAttribute("data-lang") || "").toLowerCase();
    var code = pre.querySelector("code");
    var bar = document.createElement("div"); bar.className = "pre12-bar";
    var lab = document.createElement("span"); lab.className = "pre12-lang"; lab.textContent = NAME[lang] || lang || "код";
    bar.appendChild(lab);
    function text() { return (code || pre).textContent.replace(/\n$/, ""); }
    function btn(label, fn) {
      var b = document.createElement("button"); b.type = "button"; b.className = "pre12-btn"; b.textContent = label;
      b.onclick = function (e) { e.stopPropagation(); fn(b); }; bar.appendChild(b); return b;
    }
    btn("⭳ файл", function () {
      var ext = EXT[lang] || "txt", blob = new Blob([text() + "\n"], { type: "text/plain;charset=utf-8" });
      var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
      a.download = (ext === "ino" ? "sketch" : "code") + "." + ext; document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    });
    btn("Копировать", function (b) {
      var done = function () { b.textContent = "✓ Готово"; setTimeout(function () { b.textContent = "Копировать"; }, 1400); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text()).then(done, fallback);
      else fallback();
      function fallback() {
        var ta = document.createElement("textarea"); ta.value = text(); document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (e) {} ta.remove();
      }
    });
    pre.insertBefore(bar, pre.firstChild);
  }
  function scan(root) { (root || document).querySelectorAll("pre").forEach(decoratePre); }
  function watch(id) {
    var el = document.getElementById(id); if (!el || !window.MutationObserver) return;
    new MutationObserver(function () { scan(el); }).observe(el, { childList: true, subtree: true });
    scan(el);
  }

  /* ---- блок «Оформление» в панели настроек (index.html) ---- */
  function appearance() {
    var panel = document.getElementById("panel");
    if (!panel || document.getElementById("appearance12")) return;
    var box = document.createElement("div"); box.id = "appearance12"; box.className = "appearance12";
    box.innerHTML = '<div class="lab">Тема</div><div class="seg12" id="seg12"></div>' +
      '<div class="lab">Акцентный цвет</div><div class="sw12" id="sw12"></div>';
    var h = panel.querySelector("h3"); if (h) h.insertAdjacentElement("afterend", box); else panel.prepend(box);
    function draw() {
      var m = get(LS_T, "dark"), a = get(LS_A, "teal"), seg = box.querySelector("#seg12"), sw = box.querySelector("#sw12");
      seg.innerHTML = ""; sw.innerHTML = "";
      TH.forEach(function (t) {
        var b = document.createElement("button"); b.type = "button"; b.textContent = t[2] + " " + t[1];
        b.className = m === t[0] ? "on" : ""; b.onclick = function () { set(LS_T, t[0]); apply(); draw(); }; seg.appendChild(b);
      });
      ACC.forEach(function (c) {
        var b = document.createElement("button"); b.type = "button"; b.title = c[2]; b.style.setProperty("--c", c[1]);
        b.className = a === c[0] ? "on" : ""; b.onclick = function () { set(LS_A, c[0]); apply(); draw(); }; sw.appendChild(b);
      });
    }
    draw();
  }

  function init() {
    apply();
    var b = document.getElementById("btn-theme"); if (b) b.onclick = cycle;
    watch("chat"); watch("msgs");
    appearance();
    document.addEventListener("keydown", function (e) {
      var tag = (e.target && e.target.tagName) || "", typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === "KeyT") { e.preventDefault(); cycle(); return; }
      if (e.key === "Escape") {
        var p = document.getElementById("panel");
        if (p && !p.classList.contains("hidden")) { var bp = document.getElementById("btn-panel"); if (bp) bp.click(); }
      }
      if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.key === "/") {
        var i = document.getElementById("inp") || document.getElementById("c-inp");
        if (i) { e.preventDefault(); i.focus(); }
      }
    });
  }
  window.N12UI = { cycle: cycle, apply: apply, scan: scan };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
