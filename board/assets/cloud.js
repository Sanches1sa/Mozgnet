/* neuro_12 v2.10 — логика страницы «Облако» (ChatGPT-стиль).
   ОТВЕЧАЕТ МОЗГ (LLM остался только учителем): режимы «мой мозг» (зоны,
   память, файлы, базы знаний) и «другой мозг» (свободное устройство через
   облако, /api/brains/ask). Разговоры хранятся в localStorage браузера;
   сообщения уходят на плату POST /api/cloud/chat, файлы — POST /api/upload. */
"use strict";

/* ---------- константы ---------- */
var LS_CHATS = "neuro12_cloud_chats_v1";
var LS_ROUTE = "neuro12_cloud_route_v2";
var KIND_ICON = { image: "🖼", pdf: "📕", xlsx: "📊", pptx: "📽", docx: "📄",
                  text: "📝", bin: "📦" };

/* ---------- утилиты ---------- */
function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
async function apiGet(path) {
  const r = await fetch(path, { cache: "no-store" });
  try { return await r.json(); } catch (e) { return { ok: false, error: "HTTP " + r.status }; }
}
async function apiPost(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  try { return await r.json(); } catch (e) { return { ok: false, error: "HTTP " + r.status }; }
}
function kb(n) { return (n / 1024).toFixed(1) + " КБ"; }

/* мини-разметка: ```блоки```, `код`, **жирный**, списки, заголовки, ссылки */
function fmt(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|\s)\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function mdLine(src) {
  const lines = esc(src).split(/\n/);
  let html = "", ul = false, buf = [];
  const flush = () => { if (buf.length) { html += "<p>" + buf.join("<br>") + "</p>"; buf = []; } };
  const closeUl = () => { if (ul) { html += "</ul>"; ul = false; } };
  for (const ln of lines) {
    const li = ln.match(/^\s*[-*•]\s+(.*)$/);
    if (li) { flush(); closeUl(); if (!ul) { html += "<ul>"; ul = true; } html += "<li>" + fmt(li[1]) + "</li>"; continue; }
    if (/^\s*#{1,4}\s/.test(ln)) { flush(); closeUl(); html += "<h3>" + fmt(ln.replace(/^\s*#{1,4}\s/, "")) + "</h3>"; continue; }
    if (!ln.trim()) { flush(); closeUl(); continue; }
    buf.push(fmt(ln));
  }
  flush(); closeUl();
  return html;
}
function md(src) {
  const parts = String(src).split(/```/);
  let out = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) out += mdLine(parts[i]);
    else {
      const lm = parts[i].match(/^([a-zA-Z0-9_+.#-]*)\n/);      // v2.37: язык блока для панели кода
      out += '<pre data-lang="' + esc(lm ? lm[1] : "") + '"><code>' +
        esc(parts[i].replace(/^[a-zA-Z0-9_+.#-]*\n/, "")) + "</code></pre>";
    }
  }
  return out;
}

/* ---------- хранилище разговоров ---------- */
let store = { cur: null, list: [] };
function loadStore() {
  try { store = JSON.parse(localStorage.getItem(LS_CHATS)) || store; } catch (e) {}
  if (!store.list) store.list = [];
}
function saveStore() { localStorage.setItem(LS_CHATS, JSON.stringify(store)); }
function curChat() { return store.list.find(c => c.id === store.cur) || null; }
function newChat() {
  const c = { id: "c" + Date.now(), title: "Новый разговор", ts: Date.now(), messages: [] };
  store.list.unshift(c); store.cur = c.id; saveStore(); renderChats(); renderMsgs();
}

/* ---------- рендер ---------- */
function renderChats() {
  const box = el("chat-list"); box.innerHTML = "";
  for (const c of store.list) {
    const it = document.createElement("div");
    it.className = "chat-item" + (c.id === store.cur ? " cur" : "");
    const t = document.createElement("span"); t.className = "t"; t.textContent = c.title;
    const x = document.createElement("button"); x.className = "x"; x.textContent = "✕";
    x.title = "Удалить разговор";
    x.onclick = (ev) => {
      ev.stopPropagation();
      store.list = store.list.filter(v => v.id !== c.id);
      if (store.cur === c.id) store.cur = store.list[0] ? store.list[0].id : null;
      saveStore(); if (!curChat()) newChat(); renderChats(); renderMsgs();
    };
    it.appendChild(t); it.appendChild(x);
    it.onclick = () => { store.cur = c.id; saveStore(); renderChats(); renderMsgs(); };
    box.appendChild(it);
  }
}
function addNote(text) {          // локальная служебная заметка в разговоре
  const ch = curChat(); if (!ch) return;
  ch.messages.push({ role: "note", content: text, ts: Date.now() });
  ch.ts = Date.now(); saveStore(); renderMsgs();
}
function renderMsgs() {
  const box = el("msgs"); box.innerHTML = "";
  const col = document.createElement("div"); col.className = "col";
  const ch = curChat();
  const msgs = ch ? ch.messages : [];
  if (!msgs.length) {
    col.appendChild(helloClone());
  }
  for (const m of msgs) {
    const row = document.createElement("div");
    if (m.role === "user") {
      row.className = "mrow user";
      const b = document.createElement("div"); b.className = "bubble";
      b.textContent = m.content;
      row.appendChild(b);
    } else if (m.role === "note") {
      row.className = "mrow note";
      const b = document.createElement("div"); b.className = "bubble";
      b.innerHTML = fmt(m.content);
      row.appendChild(b);
    } else {
      row.className = "mrow bot";
      const av = document.createElement("div"); av.className = "avatar"; av.textContent = "🧠";
      const b = document.createElement("div"); b.className = "bubble";
      b.innerHTML = md(m.content);
      if (m.via || m.err) {
        const v = document.createElement("div"); v.className = "via";
        v.textContent = (m.err ? "ошибка" :
          (m.via === "brain" ? "ответил: мой мозг" :
           m.via === "cloud-brain" ? "ответил: мозг " + (m.who || "?") :
           "канал: " + m.via));
        b.appendChild(v);
      }
      row.appendChild(av); row.appendChild(b);
    }
    col.appendChild(row);
  }
  if (busy) {
    const t = document.createElement("div"); t.className = "mrow bot";
    t.innerHTML = '<div class="avatar">🧠</div><div class="bubble"><span id="typing">🧠 мозг думает… это может занять до минуты (ответ будет длинным)</span></div>';
    col.appendChild(t);
  }
  box.appendChild(col);
  box.scrollTop = box.scrollHeight;
}
function helloClone() {
  const d = document.createElement("div");
  d.className = "hello";
  d.innerHTML = '<div class="hello-logo">🧠</div><h1>Разговор с мозгом в облаке</h1>' +
    "<p>Спрашивай что угодно — мозг ответит развёрнуто. Прикрепляй <b>фото, PDF, " +
    "текстовые файлы, Excel и презентации</b> (📎): их текст извлечётся на плате " +
    "и попадёт в контекст, а новые слова мозг выучит для буквогенератора.</p>";
  return d;
}
function setVia(via, ok, err, who) {
  const c = el("via");
  c.className = "chip " + (ok ? "good" : "bad");
  if (via === "brain") c.textContent = "ответил: мой мозг";
  else if (via === "cloud-brain") c.textContent = "ответил: мозг " + (who || "?");
  else c.textContent = "канал: " + (err ? "ошибка" : "нет");
  c.title = err || "";
}

/* ---------- отправка ---------- */
let busy = false;
function routeVal() {
  const r = el("route");
  return r ? r.value : "local";
}
async function send() {
  if (busy) return;
  const inp = el("c-inp");
  const text = inp.value.trim();
  if (!text) return;
  if (!curChat()) newChat();
  const ch = curChat();
  ch.messages.push({ role: "user", content: text, ts: Date.now() });
  if (ch.title === "Новый разговор") ch.title = text.slice(0, 42);
  inp.value = ""; autogrow(); saveStore(); renderMsgs(); renderChats();
  busy = true; el("btn-send").disabled = true; renderMsgs();
  try {
    const r = await apiPost("/api/cloud/chat", {
      message: text, route: routeVal(),
      use_doc: true, use_image: el("opt-img").checked
    });
    if (r && r.ok) {
      ch.messages.push({ role: "assistant", content: r.text, ts: Date.now(),
        via: r.via, who: r.brain || "" });
      setVia(r.via, true, "", r.brain);
    } else {
      const err = (r && r.error) || "мозг не ответил";
      ch.messages.push({ role: "assistant", content: "⚠️ " + err, ts: Date.now(), err: true });
      setVia("", false, err);
    }
  } catch (e) {
    ch.messages.push({ role: "assistant", content: "⚠️ Плата недоступна: " + e, ts: Date.now(), err: true });
    setVia("", false, String(e));
  }
  busy = false; el("btn-send").disabled = false;
  ch.ts = Date.now(); saveStore(); renderMsgs(); renderChats();
}

/* ---------- вложения ---------- */
function addChip(text, busyChip) {
  const bar = el("attach-bar"); bar.classList.remove("hidden");
  const c = document.createElement("span");
  c.className = "att" + (busyChip ? " busy" : "");
  c.innerHTML = '<span class="m"></span>';
  c.querySelector(".m").textContent = text;
  bar.appendChild(c);
  return c;
}
function attSummary(r) {
  if (r.kind === "image") return (r.w && r.h ? r.w + "×" + r.h + " пикс, " : "") + kb(r.bytes || 0);
  let s = kb(r.bytes || 0);
  if (r.chars) s += ", знаков текста: " + r.chars;
  if (r.note) s += " (" + r.note + ")";
  return s;
}
async function uploadOne(f) {
  if (f.size > 16 * 1024 * 1024) { addNote("⚠️ «" + f.name + "» больше 16 МБ — не приму."); return; }
  const chip = addChip("⏳ " + f.name + " — разбираю на плате…", true);
  try {
    const b64 = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result).split(",").pop() || "");
      r.onerror = () => rej("не прочитан");
      r.readAsDataURL(f);
    });
    const r = await apiPost("/api/upload", { name: f.name, data: b64 });
    chip.remove();
    if (r && r.ok) {
      addNote("📎 **" + f.name + "** принят: " + attSummary(r) +
        (r.learned && r.learned.length ? " · 🧠 выучил слова: " + r.learned.join(", ") : "") +
        (r.excerpt ? "\n> " + r.excerpt.slice(0, 260).replace(/\n+/g, " ") + "…" : ""));
      if (r.kind === "image") {
        el("img-opt-wrap").classList.remove("hidden");
        el("opt-img").checked = true; el("img-opt-wrap").classList.add("on");
      }
    } else {
      addNote("⚠️ **" + f.name + "»: " + ((r && r.error) || "не принят"));
    }
  } catch (e) {
    chip.remove();
    addNote("⚠️ **" + f.name + "»: " + e);
  }
}
async function refreshDocs() {
  const box = el("doc-list"); box.innerHTML = "";
  let docs = [], img = { has: false };
  try { const r = await apiGet("/api/docs"); docs = r.docs || []; img = r.image || { has: false }; }
  catch (e) { box.innerHTML = '<div class="hint">плата недоступна</div>'; return; }
  if (img.has) {
    const it = document.createElement("div"); it.className = "doc-item";
    it.innerHTML = '<span class="k">🖼</span> <span></span><button class="x" title="убрать фото">✕</button>';
    it.children[1].textContent = img.name || "фото" + (img.w ? " (" + img.w + "×" + img.h + ")" : "");
    it.querySelector(".x").onclick = async () => { await apiPost("/api/docs/clear", { image: true }); refreshDocs(); };
    box.appendChild(it);
  }
  for (const d of docs) {
    const it = document.createElement("div"); it.className = "doc-item";
    it.innerHTML = '<span class="k">' + (KIND_ICON[d.kind] || "📄") + '</span> <span></span><button class="x" title="убрать из контекста">✕</button>';
    it.children[1].textContent = d.name + " · " + kb(d.bytes || 0) + (d.chars ? " · " + d.chars + " зн." : "");
    it.title = d.note || "";
    it.querySelector(".x").onclick = async () => {
      await apiPost("/api/docs/clear", { name: d.name }); refreshDocs();
    };
    box.appendChild(it);
  }
  if (!docs.length && !img.has) box.innerHTML = '<div class="hint">пока пусто — прикрепи файл через 📎</div>';
  if (!img.has) { el("img-opt-wrap").classList.add("hidden"); el("opt-img").checked = false; el("img-opt-wrap").classList.remove("on"); }
}

/* ---------- мелочи ---------- */
function autogrow() {
  const t = el("c-inp");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 180) + "px";
}

/* ---------- инициализация ---------- */
loadStore();
if (!store.list.length) newChat();
else if (!curChat()) { store.cur = store.list[0].id; }
el("route").value = localStorage.getItem(LS_ROUTE) || "local";
el("route").addEventListener("change", () =>
  localStorage.setItem(LS_ROUTE, el("route").value));
el("btn-new").onclick = newChat;
el("btn-send").onclick = send;
el("btn-side").onclick = () => el("side").classList.toggle("closed");
el("btn-attach").onclick = () => el("file-inp").click();
el("file-inp").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []).slice(0, 3);
  e.target.value = "";
  for (const f of files) await uploadOne(f);
  refreshDocs();
});
el("c-inp").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
el("c-inp").addEventListener("input", autogrow);
el("opt-img").addEventListener("change", () =>
  el("img-opt-wrap").classList.toggle("on", el("opt-img").checked));
renderChats(); renderMsgs(); refreshDocs();

(async () => {          // версия и статус единого мозга в шапке
  try {
    const st = await apiGet("/api/state");
    el("side-ver").textContent = "v" + (st.version || "?") +
      (st.cfg && st.cfg.has_cloud ? " · облако ✓ · единый мозг" :
        " · облако не настроено");
  } catch (e) { el("side-ver").textContent = "плата недоступна"; }
})();
