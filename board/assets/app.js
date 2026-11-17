/* neuro_12 / app.js — логика веб-интерфейса «Мозг-12» (App Lab, порт 7000)
 * v2.19: БУКВЫ И СЛОВА ГЕНЕРАТОРА СКРЫТЫ — лента букв и чипы «буква/слово»
 *   убраны (человек не видит «мысли» буквогенератора); буквы фраз уходят
 *   на пин D6 (RPC d6seq); видны только готовые фразы. Тихие навыки:
 *   мозг создаёт функции сам из заготовок (v2.24: LLM код не пишет) —
 *   без автоответов об этом.
 * v2.17: ГЕНЕРАТОР СЛУШААТ НЕЙРОНЫ — панель показывает, какие нейроны сейчас
 *   «говорят» и какими словами; чип сети показывает горячих нейронов.
 * v2.10: ЕДИНЫЙ МОЗГ (мозг-почта: письма устройств через облако), кнопка 📎
 *   файлов в локальном чате (фото/PDF/TXT/Excel/презентации), длинные
 *   ответы мозга без LLM (LLM — только учитель).
 * Опрос /api/state и /api/messages раз в секунду; без библиотек, только fetch. */
"use strict";

const $ = (id) => document.getElementById(id);
const chatEl = $("chat"), inp = $("inp"), typing = $("typing");

let lastSeq = 0;
let lastTtsId = 0;
let whisper = localStorage.getItem("mozg12_whisper") === "1";
let llmInfo = { via: "", ok: false, err: "", ts: 0 };
let lastState = {};

/* ---------- утилиты ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function timeStr(ts) {
  const d = new Date((ts || 0) * 1000);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function fmtErr(e) { return String(e && e.message ? e.message : e); }

async function apiGet(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status + " " + path);
  return r.json();
}
async function apiPost(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error("HTTP " + r.status + " " + path);
  return r.json();
}

/* ---------- чат ---------- */
const ZONE_ICON = {
  "Шёпот·LLM": " 🧠", "Шёпот·зоны": " 🤫", "Учитель": " 🧑‍🏫",
  "Диктант": " 🎯", "Код": " ⌨", "Поиск": " 🌐", "Память": " ☁",
  "Облако": " ☁", "Буквы": " 🗣", "Единый мозг": " 🧠", "Файл": " 📄",
};
const ZONE_COLORS = { "Код": "#4ade80", "Поиск": "#22d3ee", "Память": "#f472b6", "Облако": "#94a3b8",
  "Учитель": "#c084fc", "Диктант": "#fbbf24", "Единый мозг": "#2dd4bf", "Файл": "#60a5fa", "Буквы": "#facc15" };
function zoneColor(z) { return ZONE_COLORS[z] || "#8b93a3"; }
let deviceName = "плата";          /* v2.14: кто отвечает — из /api/state */
/* v2.37: ```блоки кода``` и `inline` в сообщениях (остальное — как раньше, с esc) */
function richText(src) {
  const parts = String(src ?? "").split("```");
  if (parts.length < 3) return esc(src).replace(/`([^`\n]+)`/g, '<code class="inl">$1</code>');
  let out = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      out += esc(parts[i]).replace(/`([^`\n]+)`/g, '<code class="inl">$1</code>');
    } else if (i < parts.length - 1) {          /* закрытый блок */
      const lm = parts[i].match(/^([\w+#-]*)\n/);
      out += '<pre data-lang="' + esc(lm ? lm[1] : "") + '"><code>' +
        esc(parts[i].replace(/^[\w+#-]*\n/, "").replace(/\n$/, "")) + "</code></pre>";
    } else {                                     /* незакрытый — как обычный текст */
      out += esc("```" + parts[i]);
    }
  }
  return out;
}
function addMsg(m) {
  const div = document.createElement("div");
  div.className = "msg " + (m.role || "bot");
  if (/```/.test(String(m.text || ""))) div.style.whiteSpace = "normal";
  let html = richText(m.text);
  if (m.role !== "system") {
    html += '<div class="meta">' + timeStr(m.ts) +
      (m.zone ? ' <span class="zone" style="--zc:' + zoneColor(m.zone) + '">' + esc(m.zone) + "</span>" : "") + "</div>";
    /* v2.14: бейдж автора ответа — как в чате облака: отвечает мозг платы */
    if (m.role === "bot" && m.zone) {
      html += '<div class="brainbadge">🧠 ответил мозг <b>' + esc(deviceName) +
        "</b>" + (m.zone ? " · зона: " + esc(m.zone) : "") + "</div>";
    }
  } else if (m.zone && ZONE_ICON[m.zone]) {
    html += '<div class="meta"><span class="zone">' + esc(m.zone) + "</span>" +
      ZONE_ICON[m.zone] + "</div>";
  }
  div.innerHTML = html;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

async function pollMessages() {
  try {
    const j = await apiGet("/api/messages?after=" + lastSeq);
    (j.items || []).forEach((m) => { addMsg(m); lastSeq = Math.max(lastSeq, m.seq); });
  } catch (e) { /* тише — статус покажет чип моста */ }
}

/* ---------- статус-чипы и лента букв ---------- */
function setChip(id, text, cls, title) {
  const el = $(id);
  el.textContent = text;
  el.className = "chip" + (cls ? " " + cls : "");
  if (title) el.title = title;
}

// v2.47: иконка «отрицательная частота на D6»: горит, когда опция включена в
// облаке, мост жив и знаковый кадр уже отдан скетчу (недавно).
function nfChip(st, nf, fresh) {
  const el = $("c-nf");
  if (!el) return;
  if (!nf.on && !nf.on4) { el.className = "chip hidden"; return; }
  const pins = (nf.on4 ? "D4" : "") + (nf.on4 && nf.on ? "+" : "") + (nf.on ? "D6" : "");
  const cam = st.d6_cam || {};
  const by = nf.by ? (" Знак решил: " + nf.by + ".") : "";
  const cnt = " Кадров знака: «−» " + (nf.neg || 0) + ", «+» " + (nf.pos || 0) +
    " (LLM " + (nf.llm || 0) + ", по Δf " + (nf.delta || 0) + ")." +
    (nf.on4 ? " Полярность D4 сейчас: " + (nf.pol4 < 0 ? "− (2/3 периода)" : nf.pol4 > 0 ? "+ (1/3 периода)" : "обычная") + "." : "") +
    (cam.on4 ? (function () {
      const b = st.d4_breath || {};
      return " Кадр камеры на D4 (знаковая частота «дыхания» ±2000 Гц задаёт тензорная сеть — не LLM и не нейроны): отправлено " +
        (cam.d4_sent || 0) + (b.src ? ", источник: " + b.src : "") + (b.period ? ", период вдоха-выдоха " + (+b.period).toFixed(1) + " с, глубина " +
        Math.round((b.depth || 0) * 100) + "%, пауза между кадрами " + (+b.interval).toFixed(1) + " с, последний размах " + (b.lo || 0) + "…" + (b.hi || 0) + " Гц" : "") + ", строк со словами из таблицы «сигнал↔слова»: " + (b.words_rows || 0) + "/24, дыхание отдано обучению облака: " + (b.learned || 0) + " раз.";
    })() : "") +
    (cam.on ? " Кадр камеры на D6 (" + (cam.mode === "pwm" ? "PWM-растр" : "чистый кадр") + "): отправлено " + (cam.sent || 0) +
      (cam.sent ? (", последний с отставанием " + (cam.neg ? "−π/2" : "π/2") + " (" + (cam.src || "?") + ")") : "") +
      (cam.err ? ", ошибка: " + cam.err : "") + "." : "");
  if (nf.err) {
    setChip("c-nf", "−f " + pins + ": ошибка", "bad", nf.err + cnt);
  } else if (fresh && st.bridge_alive) {
    setChip("c-nf", "−f " + pins + ": " + (nf.last < 0 ? "−" : "+") + (nf.hz || 0) + " Гц", "on",
      "Отрицательная частота на " + pins + " готова: знак отдан скетчу." + by + cnt +
      " Знак — код/полярность, на пинах всё равно 0…3,3 В; нужен скетч v2.48 и внешний приёмник.");
  } else {
    setChip("c-nf", "−f " + pins + ": ждёт", "warn",
      "Опция включена в облаке. Иконка загорится, когда мост жив и после очередной " +
      "мысли уйдёт знак (на D6 — кадром, на D4 — полярностью меандра)." + cnt);
  }
}

function cloudChip(st, cfg) {
  const c = st && typeof st === "object" ? st : {};
  if (!cfg.has_cloud) {
    setChip("c-cloud", "облако: —", "",
      "Облако не задано (адрес платформы + токен — в «Настройках»)." +
      " Без облака не будет поиска, кода, речи и LLM-учителя.");
    return;
  }
  const when = c.ts ? " (проверено " + timeStr(c.ts) + ")" : "";
  if (c.checking) {
    setChip("c-cloud", "облако: …", "warn", "Проверяю доступность облака…");
  } else if (c.ok) {
    setChip("c-cloud", "облако: да" + (c.ms ? " · " + c.ms + " мс" : ""), "good",
      "Облако отвечает" + (c.ms ? " за " + c.ms + " мс" : "") + when +
      ". Через него ходят: учитель LLM, речь TTS, поиск и код.");
  } else {
    setChip("c-cloud", "облако: нет", "bad",
      ("Облако не отвечает: " + (c.err || "нет связи") + when +
       ". Проверь адрес/токен в «Настройках» и что платформа запущена.").slice(0, 160));
  }
}

function memChip(st, cfg) {
  const m = st && typeof st === "object" ? st : {};
  const s = m.sync || {};
  const n = (m.mem_count || 0) + (m.chat_count || 0);
  const when = s.ts ? " (в " + timeStr(s.ts) + ")" : "";
  const base = "Память: реплик диалога " + (m.mem_count || 0) +
    ", сообщений чата " + (m.chat_count || 0) + ". Плата «" +
    (m.device || "?") + "». Выгрузок в облако: " + (s.pushed || 0) +
    ", восстановлений: " + (s.pulled || 0) + when + ".";
  if (s.err) {
    setChip("c-mem", "память: " + n + " · ошибка", "bad",
      (base + " " + s.err).slice(0, 160));
  } else if (s.mode === "cloud" && s.ok) {
    setChip("c-mem", "память: " + n + " · облако ✓", "good",
      base + " Облако хранит копию: перезапуск и даже смена платы не страшны.");
  } else {
    setChip("c-mem", "память: " + n + " · локально", cfg && cfg.has_cloud ? "warn" : "",
      base + " Копия пока только на плате" +
      (cfg && cfg.has_cloud ? " — облако задано, ждёт первой синхронизации."
        : ". Задай облако+токен в «Настройках» — мозг начнёт дублировать память в облако."));
  }
}

/* ---------- чип словаря и учёбы (v2.8) ---------- */
function dictChip(d) {
  const dd = d || {};
  if (!dd.ready) {
    setChip("c-dict", "словарь: …", "warn", "Словари русского языка загружаются…");
    return;
  }
  setChip("c-dict", "словарь: " + (dd.count || 0),
    (dd.count || 0) > 1000 ? "good" : "warn",
    "Словари русского языка: " + (dd.count || 0) + " слов (частотный топ) + " +
    "существительных " + (dd.pos && dd.pos.nouns || 0) + ", глаголов " +
    (dd.pos && dd.pos.verbs || 0) + ", прилагательных " + (dd.pos && dd.pos.adjs || 0) +
    ". Выучено из облака: " + (dd.extra_count || 0) + ".");
}

function learnChip(st) {
  const l = st.learn || {};
  const dt = st.dictation || {};
  const tot = (l.hits || 0) + (l.miss || 0);
  const acc = tot ? Math.round(100 * (l.hits || 0) / tot) : 0;
  const target = dt.target ? "цель «" + dt.target + "»" : "цели нет";
  setChip("c-learn", "учёба: " + acc + "%" + (dt.wins ? " · 🎯" + dt.wins : ""),
    tot ? (acc >= 60 ? "good" : "warn") : "",
    "Новая система обучения (вместо размытых оценок ±1):\n" +
    "• словарная награда: слов верных " + (l.hits || 0) + ", выдумок " +
    (l.miss || 0) + " (" + acc + "%) — кредит получает та буква, что заслужила;\n" +
    "• диктант: " + target + ", попыток " + (l.dict_tries || 0) +
    ", побед " + (l.dict_wins || 0) + ", в очереди " + (dt.queue_len || 0) + ";\n" +
    "• букв с мгновенной оценкой: " + (l.letters || 0) +
    ", словарных оценок фраз: " + (l.rewards || 0) + ".");
}

/* ---------- чип мозг-почты (v2.10): единый мозг ---------- */
function mailChip(bm, cfg, reg) {
  const m = bm || {};
  const when = m.last_ts ? " (" + timeStr(m.last_ts) + ")" : "";
  const r = reg || {};
  const regLine = (r.ts && r.ok !== null && r.ok !== undefined)
    ? (r.ok
       ? " Регистрация в облаке: ок" + (r.url ? " (" + r.url + ")" : "") +
         " — плата видна в Studio с адресом."
       : " Регистрация в облаке не прошла: " + (r.err || "?") +
         " (без неё плата видна в облаке без адреса).")
    : " Регистрация с адресом ещё не выполнялась (первый цикл мозг-почты).";
  const base = "Единый мозг: писем получено " + (m.letters || 0) +
    ", отвечено " + (m.answered || 0) + ", отправлено " + (m.sent || 0) +
    ", последнее от «" + (m.last_from || "?") + "»" + when +
    ". Письма других устройств приходят через облако; пока мозг-почта «в сети» — " +
    "чат VPS и Макс-бот получают ответы от этого устройства." + regLine +
    " Письма думают в отдельных потоках — опрос inbox (heartbeat) не прерывается," +
    " плата не «выпадает» из свободных мозгов облака (v2.21).";
  const q = (m.mail_q ? " · ⏳" + m.mail_q : "");
  if (!cfg || !cfg.has_cloud) {
    setChip("c-mail", "почта: —", "",
      "Единый мозг: задай облако + токен в «Настройках» — устройства начнут " +
      "переписываться между собой, как один распределённый мозг.");
  } else if (!(cfg.brain_on !== false)) {
    setChip("c-mail", "почта: выкл", "warn",
      "Мозг-почта выключена в «Настройках»: письма плата не берёт, но сама " +
      "видна в облаке (ping, v2.21) — включи мозг, и письма дойдут.");
  } else if (m.err) {
    setChip("c-mail", "почта: ошибка", "bad", ("Канал писем: " + m.err).slice(0, 160));
  } else {
    setChip("c-mail", "почта: вкл" + ((m.answered || m.letters) ?
      " · 📨" + (m.answered || 0) : "") + q, "good", base);
  }
}

function llmChip(st, cfg) {
  llmInfo = st || llmInfo;
  if (!cfg) return;
  const err = (llmInfo.err || "").slice(0, 120);
  const tried = (llmInfo.ts || 0) > 0;
  if (llmInfo.ok) {
    const name = { cloud: "LLM: облако", direct: "LLM: прямо" }[llmInfo.via] || "LLM";
    setChip("c-llm", name + " · наставник", "good",
      "LLM — только учитель и наставник: цели диктанта, вердикты, советы " +
      "нейросети (как отвечать) и размер зонной сети («НЕЙРОНЫ: N», v2.15). " +
      "Отвечать не может: /api/llm/ask закрыт (410), ответы строит " +
      "нейросеть платы.");
  } else if (!cfg.has_cloud && !cfg.has_direct) {
    setChip("c-llm", "LLM: не настроена", "bad",
      "Задай облако/токен либо прямой URL ядра в «Настройках». " +
      "Без LLM мозг продолжают учить словарь и диктант, а советует он сам себе.");
  } else if (!tried) {
    setChip("c-llm", "LLM: готова", "on",
      (cfg.has_cloud ? "Облако и токен заданы" : "Прямой URL ядра задан") +
      ". Наставник подключится при первом сообщении (совет, цель, вердикт).");
  } else if (llmInfo.via) {
    setChip("c-llm", "LLM: ошибка", "bad",
      "Канал «" + llmInfo.via + "» не ответил: " + (err || "?") +
      ". Словарная награда и диктант работают без LLM.");
  } else {
    setChip("c-llm", "LLM: ждёт", "warn", err || "Настройки ещё не применены");
  }
}

function renderTeacher(st) {
  const note = $("t-note"), di = $("t-dict");
  if (note) note.textContent = st.teacher_note
    ? "💬 " + st.teacher_note
    : "Мысли учителя появятся здесь (LLM пишет в панель, а не в чат).";
  if (di) {
    const dt = st.dictation || {};
    di.textContent = dt.target
      ? ("Цель: «" + dt.target + "» (" + (dt.src || "словарь") + ") · попыток " +
         (dt.tries || 0) + " · побед " + (dt.wins || 0) +
         " · в очереди " + (dt.queue_len || 0) +
         (cfgDictate() ? "" : " · диктант выключен"))
      : (cfgDictate() ? "Цель пока не выбрана — придёт из словаря." : "Диктант выключен.");
  }
}
function cfgDictate() { return !!(lastState.cfg && lastState.cfg.dictate_on); }

/* ---------- v2.36: строка АКТИВНЫХ ЗОН над чатом ---------- */
function renderZones(st) {
  const el = $("zstrip");
  if (!el) return;
  const cfg = st.cfg || {};
  const bm = st.brainmail || {};
  const chips = [];
  const add = (name, on, title) => {
    chips.push('<span class="zchip ' + (on ? "on" : "off") + '" title="' +
      esc(title || name) + '">' + esc(name) + "</span>");
  };
  add("нейроны " + (st.neurons_online || 0) + "/12",
    (st.neurons_online || 0) > 0,
    "Сколько плат-нейронов сейчас отвечают на опрос шины");
  add("слова", !!st.word_pump,
    "Насос слов: мозг посылает плате слова каждые 0,5 с" +
    ((st.word_pump && st.word_pump.mem_unique) ?
      (" · в памяти " + st.word_pump.mem_unique + " слов") : ""));
  add("мысли на D6", (bm.d6 || 0) > 0,
    "Мысли зон облака играются на пине D6: всего " + (bm.d6 || 0) +
    (bm.last_d6 ? (" · последняя: " + String(bm.last_d6).slice(0, 60)) : ""));
  add("единый мозг", !!cfg.brain_on,
    "Мозг-почта: письма устройств через облако" +
    (bm.answered ? (" · отвечено " + bm.answered) : ""));
  add("диктант", !!cfg.dictate_on,
    cfg.dictate_on && st.dictation && st.dictation.target ?
      ("Цель диктанта: «" + st.dictation.target + "»") : "Диктант выключен");
  add("любопытство", !!cfg.curious_on,
    "Плата сама просит у облака новые темы для чтения");
  add("длинно", !!cfg.long_answers, "Писать ответы как можно длиннее");
  add("TTS", !!cfg.tts_on, "Озвучка фраз через облако");
  /* какие зоны мозга отвечали в чате (счётчики из /api/state.zones_hist) */
  const zh = st.zones_hist || {};
  const zkeys = Object.keys(zh).sort((a, b) => (zh[b] || 0) - (zh[a] || 0))
    .slice(0, 6);
  const zline = '<span class="zchip zlist" title="Активные зоны мозга в чате: ' +
    'какая зона сколько раз отвечала в последних сообщениях">зоны: ' +
    (zkeys.length ?
      zkeys.map((z) => esc(z) + " " + esc(String(zh[z]))).join(" · ") :
      "—") + "</span>";
  el.innerHTML = zline + chips.join("");
}

async function pollState() {
  let st;
  try { st = await apiGet("/api/state"); } catch (e) {
    setChip("c-bridge", "мост: нет", "bad", "Приложение/скетч не отвечает: " + fmtErr(e));
    return;
  }
  lastState = st;
  renderZones(st);   /* v2.36: активные зоны над чатом */
  const cfg = st.cfg || {};
  const cloud = st.cloud || {};
  deviceName = (st.mem && st.mem.device) || deviceName;   /* v2.14: бейдж */
  if (st.bridge_alive) {
    setChip("c-bridge", "мост: ок", "good",
      "RouterBridge жив: живость — по ЛЮБОМУ пакету скетча + пинг с паузой " +
      "(4/8/12 с). Нейронов в сети: " + (st.neurons_online || 0) + "/12. " +
      "Сброшено устаревших вызовов в тишину: " + (st.bridge_dropped || 0) +
      " (v2.18: очередь не копит старые команды — мост не тонет при 12 нейронах).");
  } else if (st.sketch && st.sketch.diag) {
    /* v2.68: скетч ответил на diag из потока моста — видно, где он стоит */
    const d = st.sketch.diag;
    setChip("c-bridge", d.stuck ? "скетч завис: " + d.loop_name : "мост: скетч жив, сводок нет", "warn",
      "Скетч отвечает из потока моста (diag): запуск — «" + d.boot_name + "», " +
      (d.boot < 9 ? "setup() НЕ ЗАВЕРШЁН — застрял на этом шаге. " :
        "сейчас в loop(): «" + d.loop_name + "», проходов loop: " + d.count + (d.stuck ? " (не растёт — ЗАВИС здесь). " : ". ")) +
      "Работает " + d.up + " с. Последние строки журнала скетча: " + ((st.sketch.log || []).slice(-3).join(" | ") || "—"));
  } else if (!st.bridge_seen) {
    setChip("c-bridge", "мост: ждёт скетч", "warn",
      "Скетч ещё НИ РАЗУ не отвечал (ни сводок, ни ping). Проверь в App Lab " +
      "вкладку Sketch: sketch.ino собирается без ошибок; затем нажми Stop, " +
      "потом Run. Чат, память и мозг работают и без скетча — просто нет " +
      "спайков/речи на D8 и букв на D6." +
      (st.sketch_build ? " Последний вывод сборки/заливки скетча (neuro12_start.log): " + st.sketch_build : ""));
  } else {
    const age = Math.round(st.bridge_age || 0);
    setChip("c-bridge", "мост: молчит " + age + " с", "warn",
      "Скетч отвечал, но уже " + age + " сек тишины. До ~10 с — норма: " +
      "скетч занят опросом нейронов (в сети " + (st.neurons_online || 0) +
      "/12). В тишину устаревшие команды сбрасываются (сброшено: " +
      (st.bridge_dropped || 0) + "), пинг — с паузой, чтобы не добивать MCU. " +
      "Дольше 30 с — разбуди: Stop, затем Run в App Lab.");
  }
  const nf = st.d6_negfreq || {};
  const nfFresh = (nf.on || nf.on4) && nf.ts && (Date.now() / 1000 - nf.ts) < 120 && nf.last !== null && nf.last !== undefined;
  // v2.47: при опции «отрицательная частота на D6» знак показывается вместе с числом
  const genTxt = nfFresh && nf.last < 0 && st.gen_hz > 0 ? "−" + st.gen_hz : String(st.gen_hz || 0);
  setChip("c-gen", genTxt + " Гц", st.gen_hz > 0 ? "on" : "",
    "Частота генератора скетча на шине D4→A0 всех нейронов" +
    (nfFresh ? ". Знак — код в кадре на D6 (физически частота не бывает отрицательной)" : ""));
  nfChip(st, nf, nfFresh);
  netChip(st);                              // v2.11: связь с нейронами (пассивно)
  nnChip(st);                               // v2.15: адаптивная зонная сеть
  tnChip(st);                               // v2.39: тензорное ядро
  // v2.19: чипы «буква»/«слово» и лента букв УБРАНЫ — человек не видит
  // «мысли» буквогенератора; буквы фраз уходят на пин D6, видны только фразы
  cloudChip(st.cloud, cfg);
  llmChip(st.llm, cfg);
  mailChip(st.brainmail, cfg, st.cloud_reg);
  memChip(st.mem, cfg);
  dictChip(st.dict);
  learnChip(st);
  renderMemInfo(st.mem);
  renderTeacher(st);
  const ttsB = st.tts || {};
  const ttsTitle = ttsB.err
    ? ("Облако TTS: " + ttsB.err + " (мелодия D8 работает всегда)")
    : (ttsB.via === "sketch"
        ? "Речь идёт В СКЕТЧ (say_wave) — «нейроголос» играет динамик D8"
        : (ttsB.via === "board"
            ? "Речь через облако — играет динамик платы (aplay)"
            : (ttsB.via === "browser"
                ? "Речь через облако — играет этот браузер"
                : "Речь через облако (POST /api/tts -> WAV -> скетч D8 / aplay / браузер); мелодия слов на D8 работает независимо")));
  setChip("c-tts", "TTS " + (cfg.tts_on ? "вкл" : "выкл"),
    !cfg.tts_on ? "" : (ttsB.err ? "warn" : (ttsB.via || cloud.ok) ? "good" : "warn"),
    ttsTitle);
  // v2.19: лента букв убрана — буквы/слова генератора человек не видит
  $("p-last-say").textContent = st.last_say ? "«" + st.last_say + "»" : "фраза ещё не собрана…";
  const gen = st.gen || {};   /* v2.16: речь мозга из словаря */
  const pbs = $("p-brain-say");
  if (pbs) {
    pbs.textContent = gen.last ? ("💬 " + gen.last)
      : (gen.ready ? "мысль созревает…" : "мысль ещё не родилась — пообщайся, «/поиск» или 📎 файл");
    pbs.title = gen.last
      ? ("Фразу собрал сам (v2.18): сеть знаний " + (gen.links || 0) + " связей, " +
         (gen.vocab || 0) + " словарных слов. Она выходит СООБЩЕНИЕМ в чат платы, " +
         "а в облако уходят СОБРАННЫЕ МЫСЛИ (v2.20): копятся в очереди (сейчас " +
         (gen.queued || 0) + "), раз в 45 с склеиваются в один текст и пишутся " +
         "в чат облака «Мысли плат» + ленту «Мысли мозга» Studio + хронику (всего " +
         (gen.cloud_says || 0) + " публикаций). Нейронам — дубликат STIM. Фраз: " +
         (gen.phrases || 0) + ", слов: " + (gen.words || 0) + ".")
      : "Мозг генерирует фразы словами из словаря по тому, что знает: память " +
        "диалогов, файлы, заметки наставника. Пока знаний мало — молчит.";
  }
  const bz = gen.breath || {};    /* v2.74: общее дыхание */
  const pb = $("p-breath");
  if (pb && bz.period) {
    const sdd = gen.sdict || {};
    pb.textContent = "🫁 дыхание: " + bz.state + ", период " + bz.period + " с, глубина " + (bz.depth || "—") +
      " (" + bz.src + ") · 📖 общий словарь: " + (sdd.words || 0) + " слов, с частотой " + (sdd.with_hz || 0) +
      (sdd.err ? " — " + sdd.err : ", сверок с облаком " + (sdd.syncs || 0)) +
      (gen.reads ? " · 📚 прочитала сама: " + (gen.reads.read || 0) + " статей" + (gen.reads.last ? " (последняя «" + gen.reads.last + "»)" : "") +
        ((gen.reads.miss || []).length ? ", в очереди «не знаю»: " + gen.reads.miss.join(", ") : "") : "") +
      (bz.lead && bz.lead.zone ? " · 🧠 ведёт зона «" + bz.lead.zone + "» (" + bz.lead.a + ")" : "") +
      (bz.cloud_ctl ? " · облако: работают " + (bz.cloud_ctl.run || []).length + ", мозг остановил " + (bz.cloud_ctl.stop || []).length : "");
    pb.title = "Воля мозга Роуз (зонная сеть) задаёт силу каждого алгоритма, тензорная сеть — дыхание:\n" +
      (bz.algos || []).map(a => "• " + a.name + " — зона «" + a.zone + "», сила ×" + a.strength + "; " + a.how +
      " (" + (a.uses || 0) + ")").join("\n") +
      (bz.cloud_ctl && (bz.cloud_ctl.stop || []).length ? "\n\nМозг остановил в облаке:\n• " + bz.cloud_ctl.stop.join("\n• ") : "");
  }
  const cm = gen.cam || {};       /* v2.73: камера */
  const pc = $("p-cam");
  if (pc) {
    const ago = cm.sent_ts ? Math.round(Date.now() / 1000 - cm.sent_ts) : null;
    pc.textContent = "📷 камера: " + (cm.state || "?") +
      (cm.err ? " — " + cm.err : "") + (cm.up_err ? " · " + cm.up_err : "") +
      (ago != null ? " · последний кадр в облаке " + ago + " с назад (" + (cm.kb || 0) + " КБ)" : " · в облако кадры ещё не уходили");
    const ai = cm.ai || {};         /* v2.88/v2.89: камера с нейросетью */
    if (ai.on) {
      pc.textContent += ai.ok
        ? " · 🧠 нейросеть камеры: человек " + Math.round((ai.p || 0) * 100) + "%, участвует в выборе слов — опросов " +
          (ai.wvote_ok || 0) + "/" + (ai.wvotes || 0) + ", фраз выучила " + (ai.said || 0) + " (словарь " + (ai.lex || 0) + ")"
        : " · 🧠 нейросеть камеры: " + (ai.err || "молчит");
      if (ai.last_wvote) pc.title = "Последний голос камеры за слова:\n" + ai.last_wvote.v.map(x => x[1] + " — " + x[0]).join("\n");
    }
  }
  const mc = gen.mic || {};       /* v2.70: уши мозга */
  const pm = $("p-mic");
  if (pm) {
    pm.textContent = !mc.on ? "🎤 микрофон выключен"
      : "🎤 микрофон: " + (mc.state || "?") + (mc.err ? " — " + mc.err : "") +
        (mc.last ? " · последнее: " + mc.last : "") +
        " · фраз " + (mc.phrases || 0) + ", с речью " + (mc.heard || 0);
    pm.title = "Устройство " + (mc.dev || "default") + ", громкость " + (mc.rms || 0) +
      " (шум " + (mc.floor || 0) + "). Фразы режутся по громкости и уходят в облако; " +
      "речь распознаёт облако (Vosk). Скажи «мозг, …» — мозг ответит голосом.";
  }
  const gm = gen.game || {};      /* v2.69: игра «РОК-12» */
  const pg = $("p-game");
  if (pg) {
    const d = (gm.desire != null) ? Number(gm.desire).toFixed(2) : "—";
    pg.textContent = !gm.cloud
      ? "🎮 игра: в облаке нет «РОК-12» (нужно облако v3.127+)" + (gm.err ? " — " + gm.err : "")
      : (gm.playing
        ? "🎮 играю в «РОК-12»: уровень " + (gm.level || "?") + ", убил " + (gm.kills || 0) +
          ", погиб " + (gm.deaths || 0) + " · желание " + d + " (" + (gm.why || "") + ")"
        : "🎮 не играю: желание " + d + " < 0.55 (" + (gm.why || "") + ")");
    pg.title = "Мозг сам решает, играть ли: скука + напряжение сети + частота генератора + " +
      "приглашение − разговор − усталость − досада. Сеть игры " + (gm.net || "") +
      ": шагов " + (gm.steps || 0) + ", обновлений весов " + (gm.updates || 0) +
      ", средняя награда " + (gm.avg_r != null ? gm.avg_r : "—") + ", ответ облака " +
      (gm.ms || 0) + " мс.";
  }
  const lis = gen.listen || {};   /* v2.17: генератор слушает нейроны */
  const pl = $("p-listen");
  if (pl) {
    const hot = lis.hot || [];
    pl.textContent = hot.length
      ? "👂 " + hot.map((h) => "#" + h.id + " " + h.zone + " → " +
          ((h.words && h.words.length) ? h.words.slice(0, 3).join(", ") : "...")).join(" · ")
      : "👂 нейроны тихи — слова придут из памяти и словаря";
    pl.title = "Генератор СЛУШААТ нейроны (v2.17): сеть возбуждается от всего, " +
      "что слышит — твои сообщения, буквы, фразы, спайки 12 плат, вердикты — " +
      "и самые активные нейроны подсказывают генератору слова из своих связей.";
  }
  $("p-info").textContent =
    "v" + st.version + " · аптайм " + Math.round((st.up_sec || 0) / 60) + " мин · " +
    "мыслей " + ((st.gen || {}).phrases || 0) +
    ", словарных оценок " + ((st.learn || {}).rewards || 0) +
    ", LLM-оценок " + (st.teach_count || 0) + ", спайков " + (st.spk || 0);
  typing.classList.toggle("hidden", !llmWait());
}

function llmWait() {
  return false; /* индикация «шепчу» включается на время запроса send */
}

/* ---------- v2.15: адаптивная зонная сеть ---------- */
function nnChip(st) {
  const nn = st.nn || {};
  if (!nn.n) {
    setChip("c-nnet", "сеть —", "", "Адаптивная зонная сеть ещё не загружена");
    return;
  }
  const hist = (nn.hist || []);
  const last = hist.length ? hist[hist.length - 1] : null;
  const why = last ? (" Последнее изменение: " + last.from + "→" + last.to +
    " — " + (last.why || "наставник")) : "";
  const hot = (nn.hot || []);
  const hotTxt = hot.length
    ? (" Сейчас говорят: " + hot.map((h) =>
        "#" + h.id + " " + h.zone + " (a=" + h.a + ")").join(", "))
    : " Сейчас нейроны тихи (активность затухает, порог 0.12).";
  setChip("c-nnet", "сеть: " + nn.n + " нейр." + (hot.length ? " 🔥" : ""),
    hot.length ? "on" : ((last && Date.now() / 1000 - (last.ts || 0) < 300) ? "on" : ""),
    "Адаптивная зонная сеть (v2.15/v2.17): " + nn.n + " нейронов (границы " +
    nn.min + ".." + nn.max + "). Размер задаёт LLM-наставник советом " +
    "«НЕЙРОНЫ: N»: болтовня — сеть сжимается, сложная тема — растёт. " +
    "Рост — деление опытного нейрона, сокращение — отмирание слабого. " +
    "Нейроны голосуют за зону ответа, учатся у наставника и вердиктов, а их " +
    "АКТИВАЦИЯ подсказывает генератору слова." + hotTxt + why + " Файл: neuro12_net.json");
}

/* ---------- v2.39: тензорное ядро — ЭТОТ КОМПЬЮТЕР даёт ресурс ----------
 * Плата ставит задачу обучения (если сервер не смог), браузер забирает её
 * GET /api/tensor/job, считает в Web Worker (из Blob — отдельного файла нет)
 * и отдаёт веса POST /api/tensor/result. Алгоритм — тот же, что у ядра
 * платы/сервера: MLP вход -> ReLU -> softmax зон, SGD. */
function tnTrainJS(job) {
  const H = job.H, IN = job.IN, Z = job.Z;
  let st = (job.seed % 2147483647) || 1;
  const rnd = () => { st = (st * 48271) % 2147483647; return st / 2147483647; };
  let W1, b1, W2, b2;
  if (job.init) {
    W1 = job.init.W1; b1 = job.init.b1; W2 = job.init.W2; b2 = job.init.b2;
  } else {
    const a2 = Math.sqrt(6 / (H + Z));
    W1 = Array.from({ length: H }, () => Array.from({ length: IN }, () => (rnd() * 2 - 1) * 0.3));
    b1 = new Array(H).fill(0.01);
    W2 = Array.from({ length: Z }, () => Array.from({ length: H }, () => (rnd() * 2 - 1) * a2));
    b2 = new Array(Z).fill(0);
  }
  const S = job.samples || [];
  const h = new Float64Array(H), p = new Float64Array(Z), gh = new Float64Array(H);
  const fwd = (x) => {
    for (let j = 0; j < H; j++) {
      const row = W1[j]; let s = b1[j];
      for (let q = 0; q < x.length; q++) s += row[x[q][0]] * x[q][1];
      h[j] = s > 0 ? s : 0;
    }
    let m = -Infinity;
    for (let k = 0; k < Z; k++) {
      const row = W2[k]; let s = b2[k];
      for (let j = 0; j < H; j++) if (h[j]) s += row[j] * h[j];
      p[k] = s; if (s > m) m = s;
    }
    let t = 0;
    for (let k = 0; k < Z; k++) { p[k] = Math.exp(p[k] - m); t += p[k]; }
    for (let k = 0; k < Z; k++) p[k] /= t;
  };
  const order = S.map((_, i) => i);
  let loss = 0, ops = 0;
  for (let ep = 0; ep < job.epochs; ep++) {
    for (let i = order.length - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1)); const tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    }
    const rate = job.lr / (1 + 0.15 * ep);
    loss = 0;
    for (const n of order) {
      const x = S[n][0], z = S[n][1];
      fwd(x);
      loss -= Math.log(Math.max(p[z], 1e-9));
      gh.fill(0);
      for (let k = 0; k < Z; k++) {
        const gk = p[k] - (k === z ? 1 : 0);
        if (!gk) continue;
        const row = W2[k];
        for (let j = 0; j < H; j++) {
          if (h[j]) { gh[j] += row[j] * gk; row[j] -= rate * (gk * h[j] + 0.0001 * row[j]); }
        }
        b2[k] -= rate * gk;
      }
      for (let j = 0; j < H; j++) {
        const gj = gh[j];
        if (!gj) continue;
        const row = W1[j];
        for (let q = 0; q < x.length; q++) row[x[q][0]] -= rate * gj * x[q][1];
        b1[j] -= rate * gj;
      }
      ops += H * x.length * 2 + Z * H * 2;
    }
    loss /= Math.max(1, S.length);
  }
  let ok = 0;
  for (const s of S) {
    fwd(s[0]);
    let best = 0;
    for (let k = 1; k < Z; k++) if (p[k] > p[best]) best = k;
    if (best === s[1]) ok++;
  }
  return { W1, b1, W2, b2, loss, acc: S.length ? ok / S.length : 0, ops };
}

let tnWorker = null, tnBusy = false, tnBench = 0, tnDone = 0;
function tnBenchmark() {               /* скорость компьютера: операций/с */
  const a = new Float64Array(4096);
  for (let i = 0; i < a.length; i++) a[i] = Math.random();
  const t0 = performance.now(); let s = 0, n = 0;
  while (performance.now() - t0 < 40) {
    for (let i = 0; i < a.length; i++) s += a[i] * a[(i * 7) & 4095];
    n += a.length;
  }
  tnBench = Math.round(n / ((performance.now() - t0) / 1000) * 0.25 + (s > 0 ? 0 : 1));
}
function tnMakeWorker() {
  try {
    const src = tnTrainJS.toString() +
      "\nself.onmessage = (e) => { try { self.postMessage(tnTrainJS(e.data)); }" +
      " catch (err) { self.postMessage({ error: String(err) }); } };";
    return new Worker(URL.createObjectURL(new Blob([src], { type: "text/javascript" })));
  } catch (e) { return null; }
}
async function pollTensor() {
  if (tnBusy) return;
  if (!tnBench) tnBenchmark();
  let r;
  try { r = await apiGet("/api/tensor/job?bench=" + tnBench); } catch (e) { return; }
  const job = r && r.job;
  if (!job) return;
  tnBusy = true;
  const t0 = performance.now();
  const done = async (res) => {
    res = res || {};
    res.id = job.id; res.ms = Math.round(performance.now() - t0);
    if (!res.error) { try { await apiPost("/api/tensor/result", res); tnDone++; } catch (e) { /* плата посчитает сама */ } }
    tnBusy = false;
  };
  if (!tnWorker) tnWorker = tnMakeWorker();
  if (tnWorker) {
    tnWorker.onmessage = (ev) => done(ev.data);
    tnWorker.onerror = () => { tnWorker = null; done({ error: "worker" }); };
    tnWorker.postMessage(job);
  } else {
    setTimeout(() => { try { done(tnTrainJS(job)); } catch (e) { done({ error: String(e) }); } }, 0);
  }
}

function tnChip(st) {
  const t = st.tensor || {};
  if (!t.H) { setChip("c-tnet", "тензор —", "", "Тензорное ядро ещё не загружено"); return; }
  const lt = t.task || {};
  const WHERE = { server: "сервер", browser: "этот компьютер", local: "плата" };
  const srv = t.srv || {}, br = t.browser || {}, mod = t.mod || {};
  setChip("c-tnet", "тензор " + t.H + "×" + t.IN + (t.busy ? " ⏳" : ""),
    t.ready ? "on" : "",
    "Тензор мысли (логика зон, v2.84): " + t.H + " нейронов — объём по ОЗУ платы (~" +
    (t.ram_mb || "?") + " МБ → цель " + t.h_target + ", " + t.h_min + ".." + t.h_max +
    "), не от дыхания. Поведение модулируется частотой (~" + (t.hz_ema || 0) +
    " Гц), волей и дыханием мозга" +
    (mod.lr != null ? " (lr×" + mod.lr + " conf×" + mod.conf + " voice×" + mod.voice + ")" : "") +
    ". Вход " + t.IN + " = 64 признака текста + 12 аппаратных нейронов. Параметров " + t.params +
    ". Примеров " + t.samples + " (новых " + t.new + "), обучений " + t.trains +
    (t.acc != null ? ", точность " + Math.round(t.acc * 100) + "%" : "") +
    (t.ready ? ". Ведёт логику мыслей (зона, выбор фразы)." : ". Пока копит примеры.") +
    " Учится по материалам базы знаний (кнопка «Обучить все сети» в облаке)." +
    " Последняя задача: " + (lt.kind || "—") + " → " + (WHERE[lt.where] || "—") +
    ". Сервер: " + (srv.ok ? "ок" : (srv.err || "ещё не звали")) +
    ". Этот компьютер: " + (br.alive ? "даёт ресурс" : "не на связи") +
    " (задач " + tnDone + ").");
}

/* ---------- v2.11: связь с нейронами ---------- */
function netChip(st) {
  const net = st.net || "";
  if (net) {
    setChip("c-net", "нейроны " + (st.neurons_online ?? net.split("").filter(c => c === "1").length) + "/12",
      st.neurons_online === 12 ? "good" : (st.neurons_online ? "warn" : "bad"),
      "Карта связи из скетча: кто отвечал на опрос шины. Точки — в панели ⚙ → «Связь с нейронами»");
    renderNet(net);
  } else {
    setChip("c-net", "нейроны —", "",
      "Скетч ещё не прислал карту связи (NET=): запусти приложение и скетч");
  }
}

function renderNet(net) {
  const row = $("net-row");
  if (!row) return;
  if (row.children.length !== net.length) {
    row.innerHTML = "";
    for (let i = 0; i < net.length; i++) {
      const d = document.createElement("div");
      d.className = "nd"; d.id = "nd" + i; d.textContent = i;
      d.title = "Нейрон №" + i + " (адрес I2C 0x" + (0x10 + i).toString(16) + ")";
      row.appendChild(d);
    }
  }
  for (let i = 0; i < net.length; i++) {
    const d = $("nd" + i);
    if (!d) continue;
    d.classList.toggle("on", net[i] === "1");
    d.classList.toggle("off", net[i] !== "1");
  }
}

async function netTest() {
  const b = $("btn-net"), out = $("net-out");
  b.disabled = true; const old = b.textContent;
  b.textContent = "⏳ пингую 12 плат (2.5 с)…";
  try {
    const j = await apiPost("/api/neurons/test", {});
    if (!j.ok) {
      out.textContent = "Ошибка: " + (j.error || "неизвестно");
    } else {
      if (j.net) renderNet(j.net);
      (j.neurons || []).forEach(n => {
        const d = $("nd" + n.id);
        if (d && n.reacted) { d.classList.add("rct"); setTimeout(() => d.classList.remove("rct"), 4000); }
      });
      out.innerHTML =
        "Опрос шины: <b>" + j.online + "/12</b> · отозвались на пинг: <b>" +
        j.replied + "/12</b> · спайков: <b>" + j.spikes_total + "</b>" +
        (j.ping_sent ? "" : " · <b style='color:var(--warn)'>пинг не ушёл!</b>") +
        "<br>Зелёные — на связи, пульсируют — ответили спайками, оранжевые — молчат" +
        " (проверь питание, SDA/SCL, адреса 0x10…0x1B и NEURON_ID).";
    }
  } catch (e) { out.textContent = "Ошибка запроса: " + fmtErr(e); }
  b.disabled = false; b.textContent = old;
}
$("btn-net").onclick = netTest;

/* ---------- отправка ---------- */
async function send() {
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  typing.textContent = whisper ? "🤫 учитель слушает…" : "➤ передаю нейронам…";
  typing.classList.remove("hidden");
  try {
    await apiPost("/api/send", { text: text, whisper: whisper });
  } catch (e) {
    addMsg({ role: "system", text: "Ошибка отправки: " + fmtErr(e), ts: Date.now() / 1000 });
  }
  typing.classList.add("hidden");
  pollMessages();
}

/* ---------- настройки ---------- */
async function loadSettings() {
  try {
    const s = await apiGet("/api/settings");
    $("s-cloud").value = s.cloud_url || "";
    $("s-token").value = "";           // маска: пусто = не менять
    $("s-token").placeholder = s.token || "оставь пустым — не менять";
    $("s-direct").value = s.direct_url || "";
    $("s-dkey").value = "";
    $("s-dkey").placeholder = s.direct_key || "оставь пустым — не менять";
    $("s-dmodel").value = s.direct_model || "";
    $("s-tvoice").value = s.tts_voice || "";
    $("s-tts").checked = !!s.tts_on;
    $("s-teach").checked = !!s.teach_on;
    $("s-dictate").checked = !!s.dictate_on;
    $("s-curious").checked = !!s.curious_on;
    $("s-memsync").checked = !!s.mem_sync;
    $("s-brain").checked = s.brain_on !== false;
    $("s-long").checked = s.long_answers !== false;
    $("s-advice").checked = s.advice_on !== false;
    $("s-gen").checked = s.gen_on !== false;    /* v2.16 */
    if ($("s-grav")) $("s-grav").checked = s.grav_on !== false;   /* v2.66 */
    if ($("s-gravg")) $("s-gravg").value = (s.grav_g != null ? s.grav_g : 1);
    if ($("s-sync")) $("s-sync").checked = s.sync_on !== false;   /* v2.67 */
    if ($("s-game")) $("s-game").checked = s.game_on !== false;   /* v2.69 */
    if ($("s-mic")) $("s-mic").checked = s.mic_on !== false;      /* v2.70 */
    if ($("s-cam")) $("s-cam").checked = s.cam_on !== false;      /* v2.73 */
    if ($("s-camurl")) $("s-camurl").value = s.cam_url || "";
    if ($("s-camai")) $("s-camai").checked = s.cam_ai !== false;       /* v2.88 */
    if ($("s-camaiw")) $("s-camaiw").value = s.cam_ai_w != null ? s.cam_ai_w : 0.35;
    if ($("s-micdev")) $("s-micdev").value = s.mic_dev || "default";
  } catch (e) { /* панель просто останется пустой */ }
}

async function saveSettings() {
  const body = {
    cloud_url: $("s-cloud").value.trim(),
    token: $("s-token").value.trim(),
    direct_url: $("s-direct").value.trim(),
    direct_key: $("s-dkey").value.trim(),
    direct_model: $("s-dmodel").value.trim(),
    tts_voice: $("s-tvoice").value.trim(),
    tts_on: $("s-tts").checked,
    teach_on: $("s-teach").checked,
    dictate_on: $("s-dictate").checked,
    curious_on: $("s-curious").checked,
    mem_sync: $("s-memsync").checked,
    brain_on: $("s-brain").checked,
    long_answers: $("s-long").checked,
    advice_on: $("s-advice").checked,
    gen_on: $("s-gen").checked,
    grav_on: $("s-grav") ? $("s-grav").checked : true,
    sync_on: $("s-sync") ? $("s-sync").checked : true,
    game_on: $("s-game") ? $("s-game").checked : true,
    mic_on: $("s-mic") ? $("s-mic").checked : true,
    cam_on: $("s-cam") ? $("s-cam").checked : true,
    cam_url: $("s-camurl") ? $("s-camurl").value.trim() : "",
    cam_ai: $("s-camai") ? $("s-camai").checked : true,                /* v2.88 */
    cam_ai_w: $("s-camaiw") ? parseFloat($("s-camaiw").value || "0.35") : 0.35,
    mic_dev: $("s-micdev") ? ($("s-micdev").value.trim() || "default") : "default",
    grav_g: $("s-gravg") ? Math.max(0, Math.min(3, parseFloat($("s-gravg").value) || 0)) : 1,
  };
  try {
    await apiPost("/api/settings", body);
    addMsg({ role: "system", text: "⚙ Настройки сохранены.", ts: Date.now() / 1000 });
  } catch (e) {
    addMsg({ role: "system", text: "Не удалось сохранить: " + fmtErr(e), ts: Date.now() / 1000 });
  }
  pollState();
}

/* ---------- память ---------- */
function renderMemInfo(m) {
  const el = $("mem-info");
  if (!el) return;
  const d = m || {};
  const s = d.sync || {};
  el.textContent = "Реплик диалога: " + (d.mem_count || 0) +
    " · чат: " + (d.chat_count || 0) + " · плата «" + (d.device || "?") +
    "» · копия в облаке: " + (s.mode === "cloud" && s.ok ? "есть" : "нет") +
    (s.err ? " («" + s.err + "»)" : "");
}

async function memSync() {
  const b = $("btn-mem-sync");
  b.disabled = true;
  try {
    const r = await apiPost("/api/memory/sync", {});
    addMsg({ role: "system", ts: Date.now() / 1000,
      text: (r.ok ? "☁ " : "☁⚠ ") + (r.pulled || "") +
        (r.pushed ? "; копия в облаке обновлена." :
                   "; в облако не ушло — см. чип памяти.") });
  } catch (e) {
    addMsg({ role: "system", ts: Date.now() / 1000,
      text: "Синхронизация не удалась: " + fmtErr(e) });
  }
  b.disabled = false;
  pollState();
}

async function memForget() {
  if (!confirm("Забыть ВСЮ память? Чат, реплики, счётчики, выученные слова " +
               "и цели диктанта будут стёрты и локально, и в облаке.")) return;
  const b = $("btn-mem-forget");
  b.disabled = true;
  try {
    const r = await apiPost("/api/memory/forget", {});
    lastSeq = 0;
    chatEl.innerHTML = "";
    addMsg({ role: "system", ts: Date.now() / 1000,
      text: "🧹 Память очищена (записей: " + (r.wiped || 0) +
        (r.cloud ? ", облако тоже" : "") + "). Начинаем с чистого листа." });
  } catch (e) {
    addMsg({ role: "system", ts: Date.now() / 1000,
      text: "Не удалось забыть: " + fmtErr(e) });
  }
  b.disabled = false;
  pollMessages();
  pollState();
}

/* ---------- учитель: ±1, цель диктанта, другое слово ---------- */
async function teach(score) {
  try {
    await apiPost("/api/teach", { score: score });
  } catch (e) {
    addMsg({ role: "system", text: "Ошибка оценки: " + fmtErr(e), ts: Date.now() / 1000 });
  }
}

async function setDictate() {
  const w = $("t-word").value.trim();
  if (!w) return;
  $("t-word").value = "";
  try {
    const r = await apiPost("/api/dictate", { word: w });
    if (!r.ok && r.error) {
      addMsg({ role: "system", text: "🎯 " + r.error, ts: Date.now() / 1000 });
    } else {
      addMsg({ role: "system", text: "🎯 Цель поставлена: «" + w + "»", ts: Date.now() / 1000 });
    }
  } catch (e) {
    addMsg({ role: "system", text: "Не вышло: " + fmtErr(e), ts: Date.now() / 1000 });
  }
  pollState();
}

async function nextWord() {
  try { await apiPost("/api/dictate", {}); } catch (e) { /* молча */ }
  pollState();
}

/* ---------- ☁ код в облаке ---------- */
async function loadCode() {
  try {
    const r = await apiGet("/api/cloud/code");
    if (r.ok && $("code-ed").value.trim() === "") $("code-ed").value = r.code || "";
  } catch (e) { /* пустой редактор */ }
}

async function runCode() {
  const out = $("code-out");
  out.hidden = false;
  out.textContent = "▶ выполняю в облаке…";
  try {
    const r = await apiPost("/api/cloud/code",
      { code: $("code-ed").value, text: $("code-in").value });
    out.textContent = r.ok
      ? ("✔ " + (r.ms || 0) + " мс\n" + (r.result || "выполнено (пусто)"))
      : ("✖ " + (r.error || "не выполнился"));
  } catch (e) { out.textContent = "✖ " + fmtErr(e); }
  pollMessages();
}

async function writeCode() {
  const task = $("code-task").value.trim();
  if (!task) { $("code-task").focus(); return; }
  const out = $("code-out");
  out.hidden = false;
  out.textContent = "🧠 мозг собирает функцию сам (заготовки → проверка → песочница)…";
  try {
    const r = await apiPost("/api/cloud/write", { task: task });
    if (r.ok) {
      $("code-ed").value = r.code || "";
      out.textContent = "✔ Написано «" + (r.name || r.id || "функция") +
        "», проверка песочницей: " + (r.verified ? "пройдена" : "НЕ пройдена") +
        ". Можно запускать ▶";
    } else {
      out.textContent = "✖ " + (r.error || "облако не смогло");
    }
  } catch (e) { out.textContent = "✖ " + fmtErr(e); }
  pollMessages();
}

/* ---------- 🌐 поиск в облаке ---------- */
async function cloudSearch() {
  const q = $("search-q").value.trim();
  if (!q) return;
  const out = $("search-out");
  out.textContent = "🌐 ищу «" + q + "» через облако…";
  try {
    const r = await apiPost("/api/cloud/search", { q: q });
    if (!r.ok) {
      out.textContent = "✖ " + (r.error || "поиск не удался");
    } else {
      const items = (r.results || []).map((i) =>
        "• " + (i.title || "") + " — " + String(i.snippet || "").slice(0, 120));
      out.textContent = items.join("\n") || "ничего не нашлось";
      if ((r.learned || []).length) {
        out.textContent += "\n📚 выучил: " + r.learned.join(", ");
      }
    }
  } catch (e) { out.textContent = "✖ " + fmtErr(e); }
  pollMessages();
  pollState();
}

/* ---------- 📚 словари ---------- */
async function renderDictInfo() {
  const el = $("dict-info");
  try {
    const d = await apiGet("/api/dicts");
    if (d.ok) {
      el.textContent = d.ready
        ? ("Частотный: " + d.count + " слов · существительных " +
           (d.pos.nouns || 0) + ", глаголов " + (d.pos.verbs || 0) +
           ", прилагательных " + (d.pos.adjs || 0) +
           " · выучено из облака: " + d.extra_count +
           (d.extra && d.extra.length ? " (например: " + d.extra.slice(-5).join(", ") + ")" : ""))
        : "словари ещё грузятся…";
    }
  } catch (e) { el.textContent = "словари недоступны"; }
}

async function dictLookup() {
  const w = $("dict-q").value.trim();
  if (!w) return;
  const out = $("dict-out");
  try {
    const r = await apiPost("/api/dicts/lookup", { w: w });
    out.textContent = r.ok
      ? ("«" + r.word + "»: " + (r.found
          ? "есть в словаре" + (r.in_extra ? " (выучено из облака)" : "")
          : "нет в словаре — буквогенератор за такое слово получит −"))
      : ("✖ " + (r.error || "не вышло"));
  } catch (e) { out.textContent = "✖ " + fmtErr(e); }
}

/* ---------- речь ---------- */
function playB64(b64, mime) {
  try {
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([arr], { type: mime || "audio/wav" }));
    const a = new Audio(url);
    a.onended = () => URL.revokeObjectURL(url);
    a.play().catch(() => { /* автоплей заблокирован до первого клика */ });
  } catch (e) { /* аудио не критично */ }
}

async function pollTts() {
  try {
    const d = await apiGet("/api/tts_audio?after=" + lastTtsId);
    const id = d.id || 0;
    if (d.b64 && id && lastTtsId > 0) playB64(d.b64, d.mime);
    lastTtsId = Math.max(lastTtsId, id);
  } catch (e) { /* тише — статус покажет чип TTS */ }
}

async function speak() {
  const text = $("s-speak").value.trim();
  if (!text) return;
  try {
    const r = await apiPost("/api/speak", { text: text });
    if (!r.ok && r.error) {
      addMsg({ role: "system", text: "Речь через облако не удалась: " + r.error, ts: Date.now() / 1000 });
    } else {
      $("s-speak").value = "";
      addMsg({ role: "system", text: "🔊 Озвучил через облако: " +
        (r.played === "sketch" ? "скетч (динамик D8)"
          : r.played === "board" ? "динамик платы" : "этот браузер"), ts: Date.now() / 1000 });
    }
  } catch (e) {
    addMsg({ role: "system", text: "Речь недоступна: " + fmtErr(e), ts: Date.now() / 1000 });
  }
  pollTts();
}

/* ---------- файлы в локальном чате (v2.10): 📎 -> /api/upload ---------- */
function fileB64(file) {
  return new Promise((resolve, reject) => {
    const rd = new FileReader();
    rd.onload = () => resolve(String(rd.result).split(",")[1] || "");
    rd.onerror = () => reject(new Error("не прочитать файл"));
    rd.readAsDataURL(file);
  });
}

async function uploadFiles() {
  const files = Array.from($("file-in").files || []);
  $("file-in").value = "";
  if (!files.length) return;
  for (const f of files) {
    if (f.size > 16 * 1024 * 1024) {
      addMsg({ role: "system", text: "📄 «" + f.name + "» больше 16 МБ — пропустил.",
        ts: Date.now() / 1000 });
      continue;
    }
    typing.textContent = "📄 читаю файл «" + f.name + "»…";
    typing.classList.remove("hidden");
    try {
      const data = await fileB64(f);
      const r = await apiPost("/api/upload",
        { name: f.name, data: data, src: "chat" });
      if (!r.ok) {
        addMsg({ role: "system", text: "📄 «" + f.name + "»: " + (r.error || "не принят"),
          ts: Date.now() / 1000 });
      }
    } catch (e) {
      addMsg({ role: "system", text: "Файл «" + f.name + "» не ушёл: " + fmtErr(e),
        ts: Date.now() / 1000 });
    }
  }
  typing.classList.add("hidden");
  pollMessages();
}

/* ---------- события UI ---------- */
$("btn-send").addEventListener("click", send);
$("inp").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$("btn-save").addEventListener("click", saveSettings);
$("btn-plus").addEventListener("click", () => teach(1));
$("btn-minus").addEventListener("click", () => teach(-1));
$("btn-dictate").addEventListener("click", setDictate);
$("t-word").addEventListener("keydown", (e) => { if (e.key === "Enter") setDictate(); });
$("btn-next-word").addEventListener("click", nextWord);
$("btn-code-run").addEventListener("click", runCode);
$("btn-code-write").addEventListener("click", writeCode);
$("btn-search").addEventListener("click", cloudSearch);
$("search-q").addEventListener("keydown", (e) => { if (e.key === "Enter") cloudSearch(); });
$("btn-dict-lookup").addEventListener("click", dictLookup);
$("dict-q").addEventListener("keydown", (e) => { if (e.key === "Enter") dictLookup(); });
$("btn-speak").addEventListener("click", speak);
$("btn-mem-sync").addEventListener("click", memSync);
$("btn-mem-forget").addEventListener("click", memForget);
$("btn-panel").addEventListener("click", () => {
  const p = $("panel");
  p.classList.toggle("hidden");
  if (!p.classList.contains("hidden")) {
    loadSettings();
    loadCode();
    renderDictInfo();
  }
});
$("btn-file").addEventListener("click", () => $("file-in").click());
$("file-in").addEventListener("change", uploadFiles);
function renderWhisper() {
  $("btn-whisper").classList.toggle("active", whisper);
  $("btn-whisper").title = whisper
    ? "Режим учителя ВКЛ: твоё сообщение получает LLM — он молча выберет слово-цель и поставит диктант. В чат он не пишет. Нажми, чтобы выключить."
    : "Режим учителя ВЫКЛ: текст пойдёт нейронам стимулом и ответят зоны. Нажми, чтобы учитель LLM получил сообщение.";
}
$("btn-whisper").addEventListener("click", () => {
  whisper = !whisper;
  localStorage.setItem("mozg12_whisper", whisper ? "1" : "0");
  renderWhisper();
  inp.placeholder = whisper ? "Чему учит мозг? (LLM молча возьмёт цель)"
                            : "Напиши мозгу… (/поиск запрос, /код задача, «цель слово»)";
});
renderWhisper();

/* ---------- старт ---------- */
pollState();                            /* сначала статус: имя платы для бейджа */
pollMessages();
pollTts();
setInterval(pollMessages, 1000);
setInterval(pollState, 1000);
setInterval(pollTts, 1500);
setInterval(renderDictInfo, 30000);
setInterval(pollTensor, 2000);          /* v2.39: компьютер даёт ресурс тензору */
