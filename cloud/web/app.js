/* ==========================================================================
   app.js — neuro12-cloud · фронтенд в стиле ChatGPT
   Чат с облаком (мозг-12 без LLM), маршруты авто/облако/плата, файлы,
   TTS, хроника, зрение, «Мысли плат», настройки (память, KB, функции,
   учитель-LM только для обучения, голос, журнал).
   ========================================================================== */
'use strict';

/* ------------------------------- утилиты --------------------------------- */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const S = {                       // состояние интерфейса
  chats: [], chat: null,          // текущий чат {id,title,messages[]}
  route: localStorage.getItem('n12_route') || 'auto',
  long: localStorage.getItem('n12_long') === '1',
  tts: localStorage.getItem('n12_tts') === '1',
  attach: null,                   // {name, b64}
  pending: null,                  // {mid, until} — ждём плату
  statusTimer: null, pollTimer: null,
};

function toast(msg, kind) {
  const t = document.createElement('div');
  t.className = 'toast ' + (kind || '');
  t.textContent = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), 4200);
}

async function api(path, opts) {
  opts = opts || {};
  const init = { method: opts.method || (opts.body ? 'POST' : 'GET') };
  if (opts.body !== undefined && !(opts.body instanceof FormData)) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.body);
  } else if (opts.body instanceof FormData) {
    init.body = opts.body;
  }
  let r;
  try {
    r = await fetch(path, init);
    // v3.175.1: облако ещё запускается (порт открыт раньше посевов) — ждём
    for (let i = 0; r.status === 503 && i < 40 &&
         (r.headers.get('Retry-After') || '') !== ''; i++) {
      await new Promise(res => setTimeout(res, 1500));
      r = await fetch(path, init);
    }
  } catch (e) {
    // v3.8.1: сеть недоступна — честная причина вместо тишины
    throw new Error('сеть недоступна (' + (e && e.message ?
      e.message : 'нет ответа сервера') + ')');
  }
  if (r.status === 401) {
    // сессия истекла — на страницу входа
    const ct = r.headers.get('content-type') || '';
    if (ct.includes('json')) {
      try { await r.json(); } catch (e) {}
    }
    location.href = '/login';
    throw new Error('401');
  }
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('json')) return r.json();
  return r;
}

/* ------------------------- markdown-lite рендер --------------------------- */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function md(src) {
  // v3.16: блоки формул и картинок — SVG от движков latexlite/imagine.
  // Вынимаем ДО экранирования (SVG — наш собственный вывод, текст внутри
  // него уже экранирован на сервере); вставляем в конце как готовые узлы.
  const rich = [];
  let s = String(src == null ? '' : String(src));
  s = s.replace(/\[(ФОРМУЛА|КАРТИНКА)\]([\s\S]*?)\[\/\1\]/g, (_, tag, body) => {
    const cls = tag === 'ФОРМУЛА' ? 'mathsvg' : 'imagsvg';
    const safe = body.indexOf('<svg') === 0 ? body :
      esc(body); // не наш SVG — показываем текстом, ничего не исполняем
    if (tag === 'ФОРМУЛА') {
      // v3.108: формулу можно скопировать — кнопкой (LaTeX) и выделением (текст)
      const mt = body.match(/data-tex="([^"]*)"/), mp = body.match(/data-plain="([^"]*)"/);
      const tex = mt ? mt[1] : '', plain = mp ? mp[1] : tex;
      rich.push('<div class="' + cls + '" data-tex="' + tex + '" data-plain="' + plain + '">' + safe +
        (tex ? '<button class="mcopy" type="button" title="Скопировать формулу (LaTeX)">⧉</button>' : '') + '</div>');
    } else {
      rich.push('<div class="' + cls + '">' + safe + '</div>');
    }
    return '\x00G' + (rich.length - 1) + '\x00';
  });
  // v3.124: иллюстрация статьи базы знаний (картинка со страницы-источника)
  s = s.replace(/\[ИЛЛЮСТРАЦИЯ src=(\S+?)(?: url=(\S+?))?\]([\s\S]*?)\[\/ИЛЛЮСТРАЦИЯ\]/g, (_, src, url, alt) => {
    const u = src.indexOf('kbimg:') === 0 ? '/api/kbimg?f=' + encodeURIComponent(src.slice(6)) :
      src.indexOf('desk:') === 0 ? '/api/desktop?shot=' + encodeURIComponent(src.slice(5)) :
      (/^https?:\/\//.test(src) ? src : '');
    if (!u) return esc(alt || '');
    const orig = url || (/^https?:/.test(src) ? src : '');
    rich.push('<figure class="kbimg"><img src="' + esc(u) + '" alt="' + esc(alt || '') + '" loading="lazy" style="max-width:100%;border-radius:6px">' +
      ((alt || orig) ? '<figcaption class="mini">' + esc(alt || '') +
        (orig ? ' · <a href="' + esc(orig) + '" target="_blank" rel="noopener">источник</a>' : '') + '</figcaption>' : '') + '</figure>');
    return '\x00G' + (rich.length - 1) + '\x00';
  });
  s = esc(s);
  const blocks = [];
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => {
    const lm = code.match(/^([\w+#-]*)\n/);          // v3.22: язык блока
    blocks.push('<pre data-lang="' + (lm ? lm[1] : '') + '"><code>' +
      code.replace(/^[\w+#-]*\n/, '') + '</code></pre>');
    return '\x00B' + (blocks.length - 1) + '\x00';
  });
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
  s = s.replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,!?])/g, '$1<i>$2</i>');
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // v3.149.6: документы, созданные ИИ — карточка файла с кнопкой скачивания
  s = s.replace(/\[([^\]]+)\]\((\/api\/files\/get\/[^)\s"'<>]+)\)/g,
    function (_, label, url) {
      var name = decodeURIComponent(url.split('/').pop() || label);
      var ext = (name.split('.').pop() || '').toLowerCase();
      var icon = '📄';
      if (ext === 'docx' || ext === 'doc') icon = '📘';
      else if (ext === 'pptx' || ext === 'ppt') icon = '📊';
      else if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') icon = '📗';
      else if (ext === 'pdf') icon = '📕';
      else if (ext === 'html' || ext === 'htm') icon = '🌐';
      else if (ext === 'txt' || ext === 'md') icon = '📝';
      return '<div class="file-card" style="display:flex;align-items:center;gap:10px;' +
        'margin:8px 0;padding:8px 12px;border:1px solid var(--line);border-radius:10px;' +
        'background:var(--side);max-width:420px">' +
        '<span style="font-size:22px">' + icon + '</span>' +
        '<div style="flex:1;min-width:0">' +
          '<div style="font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' +
            esc(name) + '</div>' +
          '<div class="mini">' + ext.toUpperCase() + ' · файл нейросети</div>' +
        '</div>' +
        '<a class="btn" href="' + esc(url) + '" download="' + esc(name) + '" ' +
          'style="flex-shrink:0;text-decoration:none;padding:6px 10px;font-size:13px">' +
          '⬇ Скачать</a>' +
        '</div>';
    });
  s = s.replace(/(^|\s)(https?:\/\/[^\s<]+[^\s<.,)])()/g,
    '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  s = s.replace(/\x00B(\d+)\x00/g, (_, i) => blocks[+i]);
  s = s.replace(/\x00G(\d+)\x00/g, (_, i) => rich[+i]);
  return s.replace(/\n/g, '<br>');
}

/* ------------------------------ рендер чата ------------------------------- */
function whoLabel(m) {
  if (m.role === 'user') return 'Вы';
  if (m.role === 'system') return 'Облако';
  if (m.brain) return 'мозг-12 · ' + m.brain;
  return 'мозг-12 · облако';
}

function renderMsg(m) {
  const row = document.createElement('div');
  row.className = 'row ' + (m.role === 'user' ? 'user' : m.role);
  const ava = document.createElement('div');
  ava.className = 'ava ' + (m.role === 'user' ? 'user' : m.role);
  ava.textContent = m.role === 'user' ? 'Вы' : (m.role === 'system' ? '⚙' : '🧠');
  const bub = document.createElement('div');
  bub.className = 'bubble';
  if (m.role !== 'user') {
    const who = document.createElement('div');
    who.className = 'who';
    const zoneCls = (m.zone === 'Навык' || m.zone === 'База знаний' ||
      m.zone === 'Плата-12') ? 'acc' : '';
    who.innerHTML = '<span>' + esc(whoLabel(m)) + '</span>' +
      (m.zone ? '<span class="zone ' + zoneCls + '"' + (m.understood ? ' title="Понял вопрос так: ' +
        esc(m.understood + (m.ctx_did ? '. По понятому: ' + m.ctx_did : '')).replace(/"/g, '&quot;') + '"' : '') +
        '>' + esc(m.zone) + (m.understood ? ' ⓘ' : '') + '</span>' : '') +
      (m.ms != null ? '<span class="mini">' + m.ms + ' мс</span>' : '');
    bub.appendChild(who);
  }
  const body = document.createElement('div');
  body.innerHTML = md(m.text || '');
  bub.appendChild(body);
  if (m.net && m.role !== 'user') {
    const net = document.createElement('div');
    net.className = 'mini';
    net.style.marginTop = '6px';
    net.style.opacity = '.75';
    net.textContent = '◈ сеть выросла: +' + (m.net.nodes || 0) + ' узлов, +' +
      (m.net.links || 0) + ' связей (всего ' + (m.net.total_nodes || 0) +
      ' узлов)';
    bub.appendChild(net);
  }
  if (m.role === 'bot') {
    const acts = document.createElement('div');
    acts.className = 'msg-acts';
    const bCopy = document.createElement('button');
    bCopy.textContent = '⧉ Копировать';
    bCopy.onclick = async () => {        // v3.108: формулы — текстом, без SVG-разметки
      const ok = await window.n12CopyText(window.n12PlainText(m.text || ''));
      toast(ok ? 'Скопировано' : 'Не удалось скопировать', ok ? 'ok' : 'err');
    };
    acts.appendChild(bCopy);
    if (S.tts || true) {
      const bPlay = document.createElement('button');
      bPlay.textContent = '🔊 Озвучить';
      bPlay.onclick = () => playTTS(m.text || '', bPlay);
      acts.appendChild(bPlay);
    }
    if (m.pending) {
      const note = document.createElement('button');
      note.textContent = '⏳ ждём плату…';
      note.disabled = true;
      acts.appendChild(note);
    }
    /* v3.28: обучение на ответах — честная оценка человека */
    if (m.ts && S.chat && S.chat.id) {
      [[1, '👍 Верно'], [-1, '👎 Неверно']].forEach(([v, label]) => {
        const b = document.createElement('button');
        b.textContent = label;
        if (m.rated === v) b.classList.add('rated');
        b.title = v > 0 ? 'Ответ верный — мозг запомнит его как подтверждённый'
          : 'Ответ неверный — мозг не будет его повторять и дочитает тему';
        b.onclick = async () => {
          try {
            const rr = await api('/api/chat/rate', { body: { id: S.chat.id, ts: m.ts, v } });
            if (!rr.ok) { toast('Не вышло: ' + (rr.error || '?'), 'err'); return; }
            m.rated = v;
            $$('button', acts).forEach(x => x.classList.remove('rated'));
            b.classList.add('rated');
            toast(v > 0 ? 'Запомнила: ответ подтверждён — на такой же вопрос отвечу так же'
              : 'Принято: этот ответ больше не повторю. Знаешь верный — напиши «правильный ответ: …»',
              'ok');
          } catch (e) { toast('Ошибка: ' + e.message, 'err'); }
        };
        acts.appendChild(b);
      });
    }
    bub.appendChild(acts);
  }
  row.appendChild(ava);
  row.appendChild(bub);
  return row;
}

function renderChat() {
  const box = $('#msgsInner');
  box.innerHTML = '';
  const msgs = (S.chat && S.chat.messages) || [];
  if (!msgs.length) {
    const d = document.createElement('div');
    d.className = 'empty';
    d.innerHTML = '<div class="big">🧠</div>' +
      '<h2>Облако «мозг-12»</h2>' +
      '<p>Спроси о чём угодно или запусти приложение App Lab — это мои навыки на Python. ' +
      'Своё приложение можно дописать в App Lab (например, для покупок в интернете).</p>' +
      '<div class="ex al-chips"><span class="mini">загружаю приложения App Lab…</span></div>';
    box.appendChild(d);
    loadChatApps(d);
    return;
  }
  msgs.forEach(m => box.appendChild(renderMsg(m)));
  $('#msgs').scrollTop = $('#msgs').scrollHeight;
}

/* v3.170: в окне чата — приложения App Lab вместо типичных вопросов */
async function loadChatApps(d) {
  const ex = $('.ex', d);
  let apps = [];
  try { apps = (await api('/api/applab/chat')).apps || []; } catch (e) { apps = []; }
  if (!ex.isConnected) return;
  const esc = t => String(t || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  ex.innerHTML = apps.map(a => {
    const q = (a.examples || [])[0] || '';
    return '<button data-app="' + esc(a.id) + '" data-q="' + esc(q) + '" title="' + esc(a.desc) + (q ? ' — пример: ' + esc(q) : '') + '">' +
      esc(a.icon || '🧩') + ' ' + esc(a.name) + '</button>';
  }).join('') + '<button data-newapp="1" title="Дописать свой навык — приложение App Lab на Python">＋ навык в App Lab</button>';
  $$('button[data-app]', ex).forEach(b => b.onclick = () => {
    if (b.dataset.q) { $('#input').value = b.dataset.q; send(); }
    else { AL.next = () => openApp(b.dataset.app); showView('applab'); }
  });
  $('button[data-newapp]', ex).onclick = () => {
    AL.next = () => { AL.cur = { id: '', name: '', icon: '🧩', desc: '', triggers: [], examples: [], chat: true, enabled: true, code: AL_TEMPLATE }; drawEditor(); };
    showView('applab');
  };
}

function typingOn(status) {
  const box = $('#msgsInner');
  let row = $('#typingRow');
  if (!row) {
    row = document.createElement('div');
    row.className = 'row bot';
    row.id = 'typingRow';
    row.innerHTML = '<div class="bubble">' +
      '<div class="typing-status" style="display:flex;align-items:center;gap:8px;color:var(--muted)">' +
      triangleMarkup() +
      '<span class="status-text" role="status">Думаю…</span></div></div>';
    box.appendChild(row);
  }
  const st = row.querySelector('.status-text');
  if (st) st.textContent = status || 'Думаю…';
  $('#msgs').scrollTop = $('#msgs').scrollHeight;
}
function typingOff() { const r = $('#typingRow'); if (r) r.remove(); }
let _typingTimer = null;
let _typingRequest = null;
function typingStart(requestId) {
  typingStop();
  _typingRequest = requestId;
  typingOn('Запрос отправляется на сервер…');
  const poll = async () => {
    try {
      const r = await api('/api/chat/activity?id=' + encodeURIComponent(requestId));
      if (_typingRequest !== requestId) return;
      typingOn(r.ok ? r.status : 'Ожидаю подтверждения от сервера…');
    } catch (e) {
      if (_typingRequest === requestId) typingOn('Нет связи со статусом сервера. Ожидаю ответ…');
    }
    if (_typingRequest === requestId) _typingTimer = setTimeout(poll, 1000);
  };
  _typingTimer = setTimeout(poll, 300);
}
function typingStop() {
  _typingRequest = null;
  clearTimeout(_typingTimer);
  _typingTimer = null;
  typingOff();
}

/* -------------------------------- TTS ------------------------------------- */
async function playTTS(text, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await fetch('/api/tts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: (text || '').slice(0, 500) })
    });
    if (!r.ok) throw new Error('TTS недоступен');
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch (e) {
    toast('Голос недоступен: ' + e.message, 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔊 Озвучить'; }
  }
}

/* ------------------------------ список чатов ------------------------------ */
function chatTitleFrom(m) {
  return m.title === 'Мысли плат' ? '💭 ' + m.title : m.title;
}

function renderChatList() {
  const q = ($('#chatSearch').value || '').toLowerCase();
  const box = $('#chatlist');
  box.innerHTML = '';
  const items = S.chats.filter(c => !q ||
    (c.title || '').toLowerCase().includes(q));
  items.forEach(c => {
    const el = document.createElement('div');
    el.className = 'citem' + (S.chat && S.chat.id === c.id ? ' on' : '');
    el.innerHTML = '<span>' + esc(chatTitleFrom(c)) + '</span>' +
      '<small>' + new Date((c.ts || 0) * 1000).toLocaleString('ru',
        { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) +
      ' · ' + (c.count || 0) + ' сообщ.</small>' +
      (c.kind === 'thoughts' ? '<span class="badge">лента мыслей</span>' : '') +
      '<span class="acts">' +
      (c.kind !== 'thoughts' ? '<button data-act="ren" title="Переименовать">✎</button>' : '') +
      (c.kind !== 'thoughts' ? '<button class="del" data-act="del" title="Удалить">🗑</button>' : '') +
      '</span>';
    el.onclick = (ev) => {
      const act = ev.target.dataset && ev.target.dataset.act;
      if (act === 'del') { ev.stopPropagation(); return delChat(c); }
      if (act === 'ren') { ev.stopPropagation(); return renChat(c); }
      openChat(c.id);
    };
    box.appendChild(el);
  });
}

async function refreshChats() {
  const r = await api('/api/chat/list');
  S.chats = (r && r.chats) || [];
  renderChatList();
}

async function openChat(id) {
  const r = await api('/api/chat/get?id=' + encodeURIComponent(id));
  if (!r || !r.ok) { toast('Чат не найден', 'err'); return; }
  S.chat = r.chat;
  showView('chat');
  $('#chatTitle').textContent = chatTitleFrom(S.chat);
  renderChat();
  renderChatList();
}

async function newChat() {
  S.chat = { id: null, title: 'Новый чат', messages: [] };
  showView('chat');
  $('#chatTitle').textContent = 'Новый чат';
  renderChat();
  renderChatList();
  $('#input').focus();
  closeSidebar();
}

async function delChat(c) {
  if (!confirm('Удалить чат «' + c.title + '»?')) return;
  await api('/api/chat/delete', { body: { id: c.id } });
  if (S.chat && S.chat.id === c.id) newChat();
  refreshChats();
  toast('Чат удалён', 'ok');
}

async function renChat(c) {
  const t = prompt('Новое имя чата:', c.title);
  if (!t) return;
  await api('/api/chat/rename', { body: { id: c.id, title: t.slice(0, 80) } });
  refreshChats();
  if (S.chat && S.chat.id === c.id) $('#chatTitle').textContent = t;
}

/* ------------------------------- отправка -------------------------------- */
async function send() {
  const ta = $('#input');
  const text = ta.value.trim();
  if (!text || $('#btnSend').disabled) return;
  if (S.route === 'code' && PROJECT.project) { await runProjectTask(text); return; }
  ta.value = ''; ta.style.height = 'auto';
  if (!S.chat || !S.chat.id) {
    // пустой чат — просто локально рисуем, id придёт из ответа
    S.chat = S.chat || { title: 'Новый чат', messages: [] };
  }
  S.chat.messages.push({ role: 'user', text });
  renderChat();
  const requestId = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2);
  typingStart(requestId);
  $('#btnSend').disabled = true;
  try {
    const body = { text, route: S.route, long: S.long, request_id: requestId };
    if (S.route === 'code') body.lang = localStorage.getItem('n12_codelang') || '';
    if (S.chat.id) body.id = S.chat.id;
    if (S.attach) body.attach = S.attach;
    const r = await api('/api/chat/send', { body });
    typingStop();
    if (!r || !r.ok) throw new Error((r && r.error) || 'сервер не ответил');
    if (!S.chat.id) { S.chat.id = r.id; }
    S.chat.title = r.title || S.chat.title;
    $('#chatTitle').textContent = chatTitleFrom({ title: S.chat.title });
    if (r.parts && r.parts.length > 1) {
      // v3.28: шаблон «приветствие + ответ» — длинный ответ двумя сообщениями
      r.parts.forEach((pp, i) => S.chat.messages.push({ role: 'bot', text: pp.text,
        zone: r.zone, ms: r.ms, ts: pp.ts,
        pending: !!r.pending && i === r.parts.length - 1, mid: r.mid }));
    } else {
      S.chat.messages.push({ role: 'bot', text: r.reply, zone: r.zone,
        ms: r.ms, ts: r.ts, pending: !!r.pending, mid: r.mid });
    }
    renderChat();
    if (r.pending && r.mid) waitBoard(r.mid);
    if (S.tts) playTTS(r.reply);
    refreshChats();
  } catch (e) {
    typingStop();
    S.chat.messages.push({ role: 'system',
      text: 'Ошибка: ' + (e.message || 'нет связи') });
    renderChat();
  } finally {
    $('#btnSend').disabled = false;
    S.attach = null; renderAttach();
  }
}

/* ожидание ответа живой платы (поллинг чата) */
function waitBoard(mid) {
  S.pending = { mid, until: Date.now() + 120000 };
  clearTimeout(S.pollTimer);
  S.pollTimer = setTimeout(pollPending, 3000);
}

async function pollPending() {
  if (!S.pending || !S.chat || !S.chat.id) return;
  try {
    const r = await api('/api/chat/poll?id=' + encodeURIComponent(S.chat.id) +
      '&after=' + ((S.chat.messages.length &&
        S.chat.messages[S.chat.messages.length - 1].ts) || 0));
    if (r && r.ok && r.messages && r.messages.length) {
      r.messages.forEach(m => S.chat.messages.push(m));
      renderChat();
      S.pending = null;
      if (S.tts) { const last = r.messages[r.messages.length - 1];
        if (last.role === 'bot') playTTS(last.text); }
      return;
    }
  } catch (e) { /* сеть мигнула — не страшно */ }
  if (Date.now() > S.pending.until) { S.pending = null; return; }
  S.pollTimer = setTimeout(pollPending, 3000);
}

/* ------------------------------ файлы ------------------------------------ */
function renderAttach() {
  const box = $('#attachPreview');
  box.innerHTML = '';
  if (S.attach) {
    const fp = document.createElement('span');
    fp.className = 'fp';
    fp.innerHTML = '📄 ' + esc(S.attach.name) +
      ' <button title="убрать">✕</button>';
    fp.querySelector('button').onclick = () => { S.attach = null; renderAttach(); };
    box.appendChild(fp);
  }
}

$('#btnAttach').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = async (ev) => {
  const f = ev.target.files[0];
  ev.target.value = '';
  if (!f) return;
  if (f.size > 60 * 1024 * 1024) { toast('Файл больше 60 МБ', 'err'); return; }
  if (/\.(zip|rar)$/i.test(f.name)) {
    setRoute('code');
    await uploadProject(f);
    return;
  }
  const fd = new FormData();
  fd.append('device', 'владелец');
  fd.append('text', 'Файл от владельца: ' + f.name);
  fd.append('file', f, f.name);
  const r = await api('/api/files/upload', { body: fd });
  if (r && r.ok) {
    S.attach = { name: f.name };
    renderAttach();
    const ta = $('#input');
    if (r.kind === 'image') {
      // v3.30: картинку облако разобрало сразу — показываем и спрашиваем
      toast('Картинка разобрана и запомнена', 'ok');
      if (!S.chat) S.chat = { title: 'Новый чат', messages: [] };
      S.chat.messages.push({ role: 'user', text: '🖼 ' + f.name });
      S.chat.messages.push({ role: 'bot', text: r.description || '', zone: 'Зрение' });
      renderChat();
      ta.value = 'Что на картинке «' + f.name + '»?';
    } else {
      toast('Файл в базе: ' + (r.chars ?
        (r.chars + ' зн. текста, KB обновлена') : 'текст не извлечён'), 'ok');
      ta.value = 'Прочитай файл «' + f.name + '» и расскажи, что в нём главное.';
    }
    ta.focus();
  } else toast('Файл не принят: ' + ((r && r.error) || '?'), 'err');
};

/* ------------------------------- маршруты -------------------------------- */
function setRoute(r) {
  S.route = r;
  renderProjectPanel();
  localStorage.setItem('n12_route', r);
  $$('#modeBox button').forEach(b =>
    b.classList.toggle('on', b.dataset.route === r));
  $('#headSub').textContent =
    r === 'auto' ? 'авто: тот же ИИ, что в боте МАКС' :
    r === 'board' ? 'отвечает живая плата (мозг-почта)' :
    r === 'talk' ? 'разговор: пишет сам мозг, своя LLM дополняет (без фактов)' :
    r === 'code' ? 'режим «Код»: ИИ-агент — задача, план, код, проверка' :
    'облако: тот же ИИ, что в боте МАКС';
}


/* ---------------- Контур: агент проекта ---------------- */
const PROJECT = { project: null, jobId: null, timer: null, busy: false, uploading: false, lastEvent: '' };
function triangleMarkup() {
  return '<svg class="activity-triangle" data-missing="' + _trianglePhase + '" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M12.8 4.5L21.2 19.5"/><path d="M20 21H4"/>' +
    '<path d="M2.8 19.5L11.2 4.5"/></svg>';
}
let _triangleTimer = null;
let _trianglePhase = 0;
function advanceActivityTriangles() {
  _trianglePhase = (_trianglePhase + 1) % 3;
  const phase = _trianglePhase;
  $$('.activity-triangle').forEach(svg => svg.setAttribute('data-missing', String(phase)));
}
function startActivityTriangles() {
  if (_triangleTimer !== null) return;
  _triangleTimer = setInterval(advanceActivityTriangles, 600);
}
function renderProjectPanel() {
  const panel = $('#projectAgent');
  if (!panel) return;
  panel.hidden = S.route !== 'code';
  if (panel.dataset.ready) { if (S.route === 'code') pollBrainWill(); return; }
  panel.dataset.ready = '1';
  panel.innerHTML = '<div class="project-toolbar"><b>Контур · агент проекта</b>' +
    '<button type="button" class="btn ghost" id="projectNew">Новый проект</button>' +
    '<button type="button" class="btn ghost" id="projectUpload">Открыть ZIP / RAR</button>' +
    '<input id="projectFile" type="file" accept=".zip,.rar" hidden>' +
    '<button type="button" class="btn ghost" id="projectClear" hidden>Убрать проект</button></div>' +
    '<details class="brain-will"><summary id="willHeadline">Воля мозга-12: проверяю связь…</summary>' +
    '<pre id="willDetails" class="mini"></pre></details>' +
    '<div id="projectName" class="mini">Загрузите проект и опишите задачу в поле сообщения ниже.</div>' +
    '<div class="project-options"><label><input type="checkbox" id="projectInternet" checked> Интернет</label>' +
    '<label>Модель <select id="projectModel"><option value="auto">Своя LLM + настроенная модель</option>' +
    '<option value="own">Только своя LLM</option></select></label></div>' +
    '<details class="agent-status" id="projectStatus" hidden><summary>' + triangleMarkup() +
    '<span id="projectStatusText" role="status">Ожидаю задачу</span><span class="mini">Действия</span></summary>' +
    '<ol id="projectEvents" class="agent-events"></ol></details>' +
    '<div id="projectResult" class="project-result" hidden></div>' +
    '<details id="codeCourse" class="code-course"><summary>Короткое обучение своей LLM коду</summary>' +
    '<p class="mini">Токены кода — это алфавит. Два потока готовят код и тексты; общие веса обновляются по очереди. Можно выбрать равное чередование или приоритет кода. Перед началом сохраняется копия. Лимит времени относится к обучению; ожидание текущего шага, копирование весов и проверки идут отдельно.</p>' +
    '<label class="course-mode">Режим <select id="courseMode"><option value="dual">Два источника: код + тексты (1:1)</option>' +
    '<option value="code">Приоритет кода (4:1)</option></select></label>' +
    '<button type="button" class="btn ghost" id="courseStart">До 200 шагов / 2 мин обучения</button> ' +
    '<button type="button" class="btn ghost" id="courseStop" hidden>Остановить курс</button>' +
    '<div class="course-state" id="courseState" hidden>' + triangleMarkup() +
    '<span id="courseStatus" role="status"></span></div><pre id="courseReport" class="mini"></pre></details>';
  $('#projectNew').onclick = async () => {
    if (PROJECT.busy || PROJECT.uploading) return;
    projectBusy(true);
    try {
      const r = await api('/api/code/project/new', { body: {} });
      if (!r.ok) throw new Error(r.error || 'Не удалось создать проект');
      PROJECT.project = r.project;
      PROJECT.jobId = null; clearTimeout(PROJECT.timer);
      localStorage.removeItem('n12_code_job');
      localStorage.setItem('n12_code_project', JSON.stringify(r.project));
      $('#projectStatus').hidden = true; $('#projectResult').hidden = true;
      showProjectName(); $('#input').focus();
    } catch (e) { toast(e.message, 'err'); }
    finally { projectBusy(false); }
  };
  $('#projectUpload').onclick = () => $('#projectFile').click();
  $('#projectFile').onchange = async e => {
    const file = e.target.files[0]; e.target.value = '';
    if (file) await uploadProject(file);
  };
  $('#projectClear').onclick = () => {
    if (PROJECT.busy) return;
    PROJECT.project = null;
    localStorage.removeItem('n12_code_project');
    $('#projectName').textContent = 'Загрузите проект и опишите задачу в поле сообщения ниже.';
    $('#projectClear').hidden = true;
  };
  try { PROJECT.project = JSON.parse(localStorage.getItem('n12_code_project') || 'null'); } catch (e) {}
  if (PROJECT.project) showProjectName();
  $('#courseStart').onclick = async () => {
    if (PROJECT.busy) return toast('Сначала дождитесь завершения задачи агента', 'err');
    $('#courseStart').disabled = true;
    $('#courseState').hidden = false;
    $('#courseStatus').textContent = 'Запускаю короткий курс…';
    try {
      const r = await api('/api/code/course', { body: { steps: 200, seconds: 120, mode: $('#courseMode').value } });
      if (!r.ok) throw new Error(r.error || 'Курс не запущен');
      drawCodeCourse(r.course); pollCodeCourse();
    } catch (e) { $('#courseStatus').textContent = e.message; $('#courseState').classList.add('finished'); $('#courseStart').disabled = false; }
  };
  $('#courseStop').onclick = async () => {
    try { const r = await api('/api/code/course', { body: { stop: true } });
      if (!r.ok) throw new Error(r.error || 'Ошибка остановки');
      $('#courseStatus').textContent = 'Остановлю после текущего шага…';
    } catch (e) { toast(e.message, 'err'); }
  };
  pollCodeCourse();
  if (S.route === 'code') pollBrainWill();
  const saved = localStorage.getItem('n12_code_job');
  if (saved && !PROJECT.jobId) { PROJECT.jobId = saved; pollProject(); }
}
let _willTimer = null;
let _willBusy = false;
async function pollBrainWill() {
  clearTimeout(_willTimer);
  if (S.route !== 'code' || _willBusy) return;
  _willBusy = true;
  try {
    const r = await api('/api/code/will');
    if (!r.ok || !r.will) throw new Error(r.error || 'нет сигнала');
    const w = r.will;
    $('#willHeadline').textContent = w.connected ? 'Воля мозга-12: свежий сигнал' : 'Воля мозга-12: нейтральный режим';
    const zones = ['Кирпичики', 'Помощь', 'Обучение', 'Буквы'].map(z =>
      z + ': ' + (w.zones[z] == null ? 'нет значения' : Math.round(w.zones[z] * 100) + '%'));
    const boards = (w.sources || []).map(b => b.board + ' · ' + b.age_seconds + ' с назад');
    $('#willDetails').textContent = [w.source, ...boards, ...zones,
      'Сила агента ×' + w.agent_strength + '; кода ×' + w.code_strength,
      'Шаг курса ×' + Number(w.learning_rate_multiplier).toFixed(2),
      'Сигнал старше 90 с не используется. Задача и разрешения пользователя сохраняются.'].join('\n');
  } catch (e) {
    $('#willHeadline').textContent = 'Воля мозга-12: статус недоступен';
    $('#willDetails').textContent = e.message;
  } finally {
    _willBusy = false;
    if (S.route === 'code') _willTimer = setTimeout(pollBrainWill, 5000);
  }
}
let _courseTimer = null;
function drawCodeCourse(course) {
  if (!course || !course.id) return;
  $('#courseState').hidden = false;
  $('#courseState').classList.toggle('finished', !course.running);
  $('#courseStatus').textContent = (course.message || course.phase) +
    (course.running ? ' · ' + course.done + '/' + course.limit_steps : '');
  $('#courseStart').disabled = !!course.running;
  $('#courseMode').disabled = !!course.running;
  $('#courseStop').hidden = !course.running;
  const measure = (name, data) => {
    if (!data) return '';
    const probes = data.probes || [];
    return name + ': loss кода ' + data.loss + (data.text_loss != null ? '; loss текстов ' + data.text_loss : '') +
      '; синтаксис ' + probes.filter(p => p.syntax).length + '/' + probes.length +
      '; простые проверки ' + probes.filter(p => p.cases === true).length + '/' + probes.length;
  };
  $('#courseReport').textContent = ['Шаги: код ' + (course.code_steps || 0) + ', тексты ' + (course.text_steps || 0) + (course.active_stream ? '. Сейчас: ' + course.active_stream : ''), measure('До', course.before), measure('После', course.after),
    course.will ? 'Воля мозга-12: ' + course.will.source + '; шаг ×' + Number(course.will.learning_rate_multiplier).toFixed(2) : '',
    course.backup ? 'Резервная копия: ' + course.backup : '',
    course.rollback ? 'Восстановлены исходные веса.' : '',
    course.phase === 'done' ? 'Низкий loss не доказывает умение решать новые задачи.' : ''].filter(Boolean).join('\n');
}
async function pollCodeCourse() {
  clearTimeout(_courseTimer);
  try {
    const r = await api('/api/code/course');
    if (!r.ok) throw new Error(r.error || 'Нет статуса курса');
    drawCodeCourse(r.course);
    if (!r.course || !r.course.running) return;
  } catch (e) {
    const label = $('#courseStatus');
    if (label) { $('#courseState').hidden = false; label.textContent = 'Статус курса недоступен: ' + e.message; }
  }
  _courseTimer = setTimeout(pollCodeCourse, 1500);
}
function showProjectName() {
  const p = PROJECT.project;
  $('#projectName').textContent = p.name + ' · ' + p.files + ' файлов · ' +
    Math.round(p.bytes / 1024) + ' КБ. Отправьте задачу ниже — агент возьмёт проект в работу.';
  $('#projectClear').hidden = false;
}
async function uploadProject(file) {
  renderProjectPanel();
  if (PROJECT.busy || PROJECT.uploading) return toast('Дождитесь текущей операции', 'err');
  if (file.size > 60 * 1024 * 1024) return toast('Архив больше 60 МБ', 'err');
  PROJECT.uploading = true;
  $('#projectUpload').disabled = true;
  $('#projectNew').disabled = true;
  $('#projectClear').disabled = true;
  $('#btnSend').disabled = true;
  $('#projectName').textContent = 'Загружаю и распаковываю ' + file.name + '…';
  const fd = new FormData(); fd.append('file', file, file.name);
  try {
    const r = await api('/api/code/project/upload', { body: fd });
    if (!r.ok) throw new Error(r.error || 'Архив не принят');
    PROJECT.project = r.project;
    PROJECT.jobId = null; clearTimeout(PROJECT.timer);
    localStorage.removeItem('n12_code_job');
    $('#projectStatus').hidden = true; $('#projectResult').hidden = true;
    localStorage.setItem('n12_code_project', JSON.stringify(r.project));
    showProjectName();
    $('#input').focus();
    toast('Проект распакован. Опишите задачу и отправьте.', 'ok');
  } catch (e) {
    if (PROJECT.project) showProjectName();
    else $('#projectName').textContent = 'Проект не загружен';
    toast(e.message, 'err');
  } finally {
    PROJECT.uploading = false;
    $('#projectUpload').disabled = false;
    $('#projectNew').disabled = false;
    $('#projectClear').disabled = false;
    $('#btnSend').disabled = PROJECT.busy;
  }
}
function projectBusy(busy) {
  PROJECT.busy = busy;
  ['#projectNew', '#projectUpload', '#projectClear', '#projectInternet', '#projectModel'].forEach(q => {
    const el = $(q); if (el) el.disabled = busy;
  });
  $('#btnSend').disabled = busy;
}
async function runProjectTask(task) {
  if (PROJECT.busy || PROJECT.uploading) return;
  projectBusy(true);
  $('#projectStatus').hidden = false;
  $('#projectStatus').classList.remove('finished');
  $('#projectStatusText').textContent = 'Передаю задачу агенту…';
  $('#projectResult').hidden = true;
  $('#projectEvents').textContent = '';
  try {
    const r = await api('/api/code/project/run', { body: {
      project_id: PROJECT.project.id, task, base_job: PROJECT.project.last_job || '',
      internet: $('#projectInternet').checked, model: $('#projectModel').value,
    } });
    if (!r.ok) throw new Error(r.error || 'Задача не принята');
    PROJECT.jobId = r.id;
    PROJECT.project.last_job = r.id;
    localStorage.setItem('n12_code_project', JSON.stringify(PROJECT.project));
    localStorage.setItem('n12_code_job', r.id);
    $('#input').value = ''; $('#input').style.height = 'auto';
    await pollProject();
  } catch (e) {
    $('#projectStatusText').textContent = 'Не удалось запустить: ' + e.message;
    $('#projectStatus').classList.add('finished');
    projectBusy(false);
  }
}
async function pollProject() {
  const id = PROJECT.jobId;
  if (!id) return;
  clearTimeout(PROJECT.timer);
  try {
    const r = await api('/api/code/project/status?id=' + encodeURIComponent(id));
    if (PROJECT.jobId !== id) return;
    if (!r.ok) {
      localStorage.removeItem('n12_code_job');
      PROJECT.jobId = null;
      projectBusy(false);
      $('#projectStatus').hidden = false;
      $('#projectStatus').classList.add('finished');
      $('#projectStatusText').textContent = r.error || 'Нет сохранённой задачи';
      return;
    }
    const job = r.job;
    $('#projectStatus').hidden = false;
    $('#projectStatus').classList.toggle('finished', job.done);
    $('#projectStatusText').textContent = job.status;
    $('#projectEvents').textContent = '';
    (job.events || []).forEach(event => {
      const li = document.createElement('li');
      const title = document.createElement('div');
      title.textContent = new Date(event.ts * 1000).toLocaleTimeString() + ' · ' + event.status;
      li.appendChild(title);
      if (event.detail) { const detail = document.createElement('pre'); detail.textContent = event.detail; li.appendChild(detail); }
      $('#projectEvents').appendChild(li);
    });
    projectBusy(!job.done);
    if (job.done) {
      const result = $('#projectResult'); result.hidden = false; result.textContent = '';
      const summary = document.createElement('p'); summary.textContent = job.summary; result.appendChild(summary);
      const changed = document.createElement('p'); changed.textContent = 'Изменены: ' + ((job.changed || []).join(', ') || 'нет изменений'); result.appendChild(changed);
      const check = document.createElement('p');
      check.textContent = 'Синтаксис: успешно ' + (job.checks || []).filter(x => x.ok === true).length +
        ', ошибок ' + (job.checks || []).filter(x => x.ok === false).length +
        ', пропущено ' + (job.checks || []).filter(x => x.ok === null).length + '. Тесты и приложение не запускались.';
      result.appendChild(check);
      if (job.download) {
        const link = document.createElement('a'); link.className = 'btn'; link.textContent = 'Скачать проект, изменения и отчёт';
        link.href = job.download; result.appendChild(link);
      }
      if (job.diff) {
        const details = document.createElement('details'); const label = document.createElement('summary');
        label.textContent = 'Посмотреть изменения'; details.appendChild(label);
        const diff = document.createElement('pre'); diff.textContent = job.diff; details.appendChild(diff); result.appendChild(details);
      }
      return;
    }
  } catch (e) {
    $('#projectStatus').hidden = false;
    $('#projectStatusText').textContent = 'Статус недоступен: ' + e.message + '. Повторяю запрос…';
  }
  PROJECT.timer = setTimeout(pollProject, 1500);
}

/* ------------------- v3.150: рабочий стол компьютера ------------------- */
const DESK = { on: false, timer: null, auto: false, busy: false };
async function renderDesktop() {
  const box = $('#desktopBox');
  DESK.on = true;
  const r = await api('/api/desktop');
  if (!r || !r.ok) {
    box.innerHTML = '<div class="empty"><div class="big">🖥</div><h2>Рабочий стол выключен</h2>' +
      '<p>' + esc((r && r.error) || 'нет доступа') + '</p>' +
      '<p><button class="btn" id="deskOpenSet">Открыть настройки</button></p></div>';
    const b = $('#deskOpenSet'); if (b) b.onclick = () => $('#btnSettings').click();
    return;
  }
  const kb = ['Enter', 'Esc', 'Tab', 'Backspace', 'Win', 'Alt+Tab', 'Ctrl+C', 'Ctrl+V', 'Ctrl+Z', 'Ctrl+A', 'Win+D', 'Alt+F4'];
  box.innerHTML =
    '<div class="desk-bar">' +
      '<button class="btn" id="deskShot">⟳ Обновить</button>' +
      '<label class="desk-auto"><input type="checkbox" id="deskAuto"' + (DESK.auto ? ' checked' : '') + '> обновлять каждые 2 с</label>' +
      '<span class="mini" id="deskInfo">экран ' + r.screen.w + '×' + r.screen.h + ' · клик — левая кнопка, двойной клик, правая — контекстное меню, колесо — прокрутка</span>' +
    '</div>' +
    '<div class="desk-screen"><img id="deskImg" alt="рабочий стол" draggable="false"><div class="desk-dot" id="deskDot"></div></div>' +
    '<div class="desk-row"><input id="deskText" placeholder="Текст — напечатать в активное окно" style="flex:1">' +
      '<button class="btn" id="deskType">Напечатать</button></div>' +
    '<div class="desk-row desk-keys">' + kb.map(k => '<button class="btn ghost sm" data-k="' + k + '">' + k + '</button>').join('') +
      '<input id="deskCombo" placeholder="своя: ctrl+shift+esc" style="width:170px"><button class="btn ghost sm" id="deskComboGo">Нажать</button></div>' +
    '<div class="desk-row"><input id="deskOpen" placeholder="Открыть: notepad, calc, C:\\\\Папка, https://ya.ru" style="flex:1">' +
      '<button class="btn" id="deskOpenGo">Открыть</button></div>' +
    '<h3 style="margin:16px 0 6px">Окна <button class="btn ghost sm" id="deskWinRef">обновить</button></h3>' +
    '<div class="desk-wins" id="deskWins"></div>';
  const img = $('#deskImg');
  const shot = () => new Promise(res => {
    const n = new Image();
    n.onload = () => { img.src = n.src; res(true); };
    n.onerror = () => { $('#deskInfo').textContent = 'снимок не получился — проверь журнал облака'; res(false); };
    n.src = '/api/desktop?shot=1&w=' + Math.min(2560, Math.round(img.clientWidth * (devicePixelRatio || 1)) || 1600) + '&t=' + Date.now();
  });
  const act = async (b, then = true) => {
    const x = await api('/api/desktop', { body: b });
    if (x && !x.ok) toast(x.error || 'не получилось', 'err');
    if (then) setTimeout(shot, 350);
    return x;
  };
  const frac = (e) => {
    const rc = img.getBoundingClientRect();
    return { fx: (e.clientX - rc.left) / rc.width, fy: (e.clientY - rc.top) / rc.height };
  };
  const dot = (e) => {
    const d = $('#deskDot'), rc = img.getBoundingClientRect(), pr = img.parentElement.getBoundingClientRect();
    d.style.left = (e.clientX - pr.left) + 'px'; d.style.top = (e.clientY - pr.top) + 'px';
    d.classList.remove('go'); void d.offsetWidth; d.classList.add('go');
  };
  let clickT = null;
  img.onclick = (e) => {             // одиночный клик ждёт, не двойной ли это
    const f = frac(e); dot(e);
    clearTimeout(clickT);
    clickT = setTimeout(() => act(Object.assign({ act: 'click' }, f)), 240);
  };
  img.ondblclick = (e) => { clearTimeout(clickT); dot(e); act(Object.assign({ act: 'click', double: true }, frac(e))); };
  img.oncontextmenu = (e) => { e.preventDefault(); dot(e); act(Object.assign({ act: 'click', button: 'right' }, frac(e))); };
  let wheelT = null, wheelSum = 0;
  img.onwheel = (e) => {
    e.preventDefault(); wheelSum += e.deltaY > 0 ? -1 : 1;
    clearTimeout(wheelT);
    wheelT = setTimeout(async () => {
      const f = frac(e); await act(Object.assign({ act: 'move' }, f), false);
      act({ act: 'scroll', dy: wheelSum * 3 }); wheelSum = 0;
    }, 160);
  };
  $('#deskShot').onclick = shot;
  $('#deskAuto').onchange = (e) => { DESK.auto = e.target.checked; };
  $('#deskType').onclick = () => { const t = $('#deskText').value; if (t) { act({ act: 'type', text: t }); $('#deskText').value = ''; } };
  $('#deskText').onkeydown = (e) => { if (e.key === 'Enter') $('#deskType').click(); };
  $$('#desktopBox [data-k]').forEach(b => b.onclick = () => act({ act: 'key', keys: b.dataset.k.toLowerCase() }));
  $('#deskComboGo').onclick = () => { const k = $('#deskCombo').value.trim(); if (k) act({ act: 'key', keys: k }); };
  $('#deskOpenGo').onclick = () => { const t = $('#deskOpen').value.trim(); if (t) act({ act: 'open', target: t }).then(() => setTimeout(shot, 1500)); };
  $('#deskOpen').onkeydown = (e) => { if (e.key === 'Enter') $('#deskOpenGo').click(); };
  const wins = async () => {
    const x = await api('/api/desktop', { body: { act: 'windows' } });
    const ws = (x && x.windows) || [];
    $('#deskWins').innerHTML = ws.length ? '' : '<span class="mini">окон не нашёл</span>';
    ws.forEach(w => {
      const b = document.createElement('button');
      b.className = 'btn ghost sm'; b.textContent = w.title.slice(0, 70); b.title = w.title;
      b.onclick = () => act({ act: 'focus', id: w.id });
      $('#deskWins').appendChild(b);
    });
  };
  $('#deskWinRef').onclick = wins;
  wins();
  await shot();
  clearInterval(DESK.timer);
  DESK.timer = setInterval(() => {
    if (!DESK.on || !$('#viewDesktop').classList.contains('on')) { clearInterval(DESK.timer); return; }
    if (DESK.auto && !document.hidden && !DESK.busy) { DESK.busy = true; shot().then(() => { DESK.busy = false; }); }
  }, 2000);
}

/* v3.178: ОКНО «СЕРВЕРЫ» — серверы и платы, включение по сети, консоль,
 * агент-администратор (с разрешениями), код на сервере, рабочий стол. */
const SRV = { tg: '', job: '', timer: null };
async function renderServers() {
  const box = $('#serversBox');
  let r;
  try { r = await api('/api/servers'); } catch (e) { box.textContent = 'нет связи с облаком: ' + e.message; return; }
  const ts = r.targets || [];
  if (!SRV.tg && ts.length) SRV.tg = ts[0].target;
  const opt = ts.map(t => '<option value="' + esc(t.target) + '"' + (t.target === SRV.tg ? ' selected' : '') + '>' +
    esc((t.via === 'board' ? '📟 ' : '🗄 ') + t.name + (t.host ? ' (' + t.host + ')' : '')) + '</option>').join('');
  const key = r.key || {};
  let h = '<h2 style="margin-top:0">🗄 Серверы</h2>' +
    '<div class="mini">Серверы по SSH (ключ облака) и платы на связи. Читающие команды выполняются сразу, изменяющие — после подтверждения, опасные — всегда с подтверждением.</div>' +
    '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">Цели</h3>';
  if (!ts.length) h += '<div class="muted">Пока пусто. Добавь сервер в ⚙ Настройки → SSH-серверы, или подключи плату v2.93+.</div>';
  ts.forEach(t => {
    h += '<div style="border-top:1px solid var(--line,#333);padding:6px 0" data-t="' + esc(t.target) + '">' +
      '<b>' + (t.via === 'board' ? '📟 ' : '🗄 ') + esc(t.name) + '</b> <span class="muted">' + esc((t.user ? t.user + '@' : '') + (t.host || '')) +
      (t.via === 'board' ? ' · плата v' + esc(t.ver || '?') + ', связь ' + t.age + ' с назад' + (t.admin ? '' : ' · команды запрещены на плате') : ' · SSH') +
      (t.mac ? ' · MAC ' + esc(t.mac) : '') + '</span> <span class="srvst" data-st="' + esc(t.target) + '"></span><div style="margin-top:4px">' +
      '<button class="btn" data-sa="check">🔍 Проверить</button> ' +
      (t.via === 'ssh' ? '<button class="btn" data-sa="wake">⚡ Включить</button> <button class="btn" data-sa="mac">MAC</button> ' : '') +
      (t.via === 'board' && t.host ? '<button class="btn" data-sa="addb" data-host="' + esc(t.host) + '" data-name="' + esc(t.name) + '">➕ Linux платы как SSH-сервер</button> ' : '') +
      '<button class="btn" data-sa="use">⌨ Выбрать</button></div></div>';
  });
  h += '</div>';
  h += '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">🔑 Ключ облака</h3>' +
    (key.ok ? '<div class="mini">Положи эту строку в <code>~/.ssh/authorized_keys</code> на сервере (на плате — пользователь <code>arduino</code>):</div>' +
      '<textarea readonly style="width:100%;height:58px;font-size:12px">' + esc(key.pub) + '</textarea>' +
      '<div class="mini">Одной командой на сервере:</div><textarea readonly style="width:100%;height:58px;font-size:12px">' + esc(key.hint) + '</textarea>'
      : '<div class="mini">Ключа ещё нет. ' + esc(key.missing ? '' : (key.error || '')) + '</div><button class="btn" id="srvKey">Создать ключ облака</button>') + '</div>';
  h += '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">🤖 Агент-администратор</h3>' +
    '<div class="frow"><div class="fgroup" style="flex:1"><label>Цель</label><select id="srvTg"><option value="">— агент выберет сам —</option>' + opt + '</select></div></div>' +
    '<textarea id="srvTask" rows="2" style="width:100%" placeholder="Например: покажи состояние сервера · почему не открывается сайт · перезапусти nginx · установи htop на плату · включи backup · напиши скрипт, который пишет свободную память в файл"></textarea>' +
    '<div style="margin:4px 0"><label class="switch-inline"><input type="checkbox" id="srvAuto"' + (r.auto_ok ? ' checked' : '') + '> разрешать изменения без вопросов (опасные — всё равно спросит)</label></div>' +
    '<button class="btn" id="srvGo">▶ Выполнить задачу</button> <button class="btn" id="srvSeed">📚 Уроки админа в базу знаний</button>' +
    '<div id="srvJob" class="mini" style="margin-top:8px"></div></div>';
  h += '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">⌨ Консоль</h3>' +
    '<div class="frow"><div class="fgroup" style="flex:1"><select id="srvTg2">' + opt + '</select></div></div>' +
    '<div style="display:flex;gap:6px"><input type="text" id="srvCmd" style="flex:1" placeholder="uptime; df -h"><button class="btn" id="srvRun">Выполнить</button></div>' +
    '<pre id="srvOut" style="max-height:340px;overflow:auto;white-space:pre-wrap;font-size:12px;margin-top:6px"></pre></div>';
  h += '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">🧩 Код на сервере</h3>' +
    '<div class="mini">Пиши сам или попроси мозг (раздел «Код»: своя LLM, модель, изученные примеры). Файл кладётся через base64 и проверяется (py_compile / bash -n).</div>' +
    '<div class="frow"><div class="fgroup" style="flex:1"><select id="srvTg3">' + opt + '</select></div><div class="fgroup" style="flex:2"><input type="text" id="srvPath" value="~/neuro12-agent/task.py"></div></div>' +
    '<div style="display:flex;gap:6px;margin-bottom:4px"><input type="text" id="srvAsk" style="flex:1" placeholder="что должен делать код"><button class="btn" id="srvGen">✨ Написать</button></div>' +
    '<textarea id="srvCode" rows="10" style="width:100%;font-family:monospace;font-size:12px"></textarea>' +
    '<button class="btn" id="srvWrite">💾 Записать</button> <button class="btn" id="srvWriteRun">▶ Записать и запустить</button>' +
    '<pre id="srvCodeOut" style="max-height:260px;overflow:auto;white-space:pre-wrap;font-size:12px"></pre></div>';
  h += '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">🖥 Рабочий стол</h3>' +
    '<div class="mini">Удалённое управление рабочим столом компьютера, где работает облако: снимок экрана, клики, ввод, окна.</div>' +
    '<button class="btn" id="srvDesk">Открыть рабочий стол</button></div>';
  h += '<div class="card" style="margin:10px 0"><h3 style="margin-top:0">📜 Журнал команд</h3>' +
    ((r.history || []).map(x => '<div class="mini">' + new Date((x.ts || 0) * 1000).toLocaleString('ru') + ' · ' + esc(x.server || '') + ' · <code>' + esc(x.cmd || '') + '</code> → ' + esc(String(x.code)) + '</div>').join('') || '<div class="muted">пусто</div>') +
    ((r.board_jobs || []).length ? '<div class="mini" style="margin-top:6px"><b>Задания плат:</b></div>' + r.board_jobs.map(j => '<div class="mini">' + esc(j.board) + ' · ' + esc(j.mode) + ' · ' + esc(j.status) + ' · <code>' + esc((j.code || '').slice(0, 80)) + '</code></div>').join('') : '') + '</div>';
  box.innerHTML = h;

  box.querySelectorAll('[data-sa]').forEach(b => b.onclick = async () => {
    const tg = b.closest('[data-t]').dataset.t, a = b.dataset.sa, st = box.querySelector('[data-st="' + CSS.escape(tg) + '"]');
    if (a === 'use') { SRV.tg = tg; ['#srvTg', '#srvTg2', '#srvTg3'].forEach(s => { if ($(s)) $(s).value = tg; }); $('#srvCmd').focus(); return; }
    if (a === 'mac') {
      const mac = prompt('MAC-адрес сетевой карты сервера (для включения по сети), например 3c:7c:3f:12:34:56');
      if (mac === null) return;
      await api('/api/servers', { body: { action: 'mac', target: tg, mac } }); return renderServers();
    }
    if (a === 'addb') {
      const x = await api('/api/servers', { body: { action: 'add_board', name: b.dataset.name + '-linux', host: b.dataset.host } });
      toast(x.ok ? 'Добавлено: arduino@' + b.dataset.host + ' — положи ключ облака на плату' : (x.error || 'ошибка'), x.ok ? 'ok' : 'err');
      return renderServers();
    }
    st.textContent = '⏳';
    const x = await api('/api/servers', { body: { action: a, target: tg, wait: a === 'wake' ? 90 : 0 } });
    st.innerHTML = (x.ok ? '🟢 ' : '🔴 ') + esc(x.out || x.error || '');
  });
  if ($('#srvKey')) $('#srvKey').onclick = async () => { const x = await api('/api/servers', { body: { action: 'key' } });
    if (!x.ok) toast(x.error, 'err'); renderServers(); };
  $('#srvAuto').onchange = () => api('/api/servers', { body: { action: 'auto_ok', on: $('#srvAuto').checked } });
  $('#srvSeed').onclick = async () => { const x = await api('/api/ssh/agent', { body: { action: 'seed' } });
    toast(x.ok ? 'Уроков в базе знаний: ' + x.added : x.error, x.ok ? 'ok' : 'err'); };
  $('#srvDesk').onclick = () => showView('desktop');
  $('#srvGo').onclick = async () => {
    const task = $('#srvTask').value.trim(); if (!task) return;
    const x = await api('/api/ssh/agent', { body: { action: 'start', task, target: $('#srvTg').value, auto_ok: $('#srvAuto').checked } });
    if (!x.ok) { toast(x.error || 'ошибка', 'err'); return; }
    SRV.job = x.job_id; srvPoll();
  };
  const run = async (confirmed) => {
    const cmd = $('#srvCmd').value.trim(); if (!cmd) return;
    $('#srvOut').textContent = '⏳ ' + cmd;
    const x = await api('/api/servers', { body: { action: 'exec', target: $('#srvTg2').value, command: cmd, confirmed } });
    if (x.need_confirm) {
      if (confirm((x.kind === 'danger' ? '⚠ ОПАСНАЯ команда' : 'Команда меняет систему') + ':\n' + cmd + '\n\nВыполнить?')) return run(true);
      $('#srvOut').textContent = 'отменено'; return;
    }
    $('#srvOut').textContent = '$ ' + cmd + '\n' + (x.out || x.error || '') + (x.code != null && x.code !== 0 ? '\n[код выхода ' + x.code + ']' : '');
  };
  $('#srvRun').onclick = () => run(false);
  $('#srvCmd').onkeydown = e => { if (e.key === 'Enter') run(false); };
  $('#srvGen').onclick = async () => {
    const ask = $('#srvAsk').value.trim(); if (!ask) return;
    $('#srvCodeOut').textContent = '⏳ мозг пишет код…';
    const lang = /\.sh$/.test($('#srvPath').value) ? 'bash' : 'python';
    const x = await api('/api/code/write', { body: { task: ask, lang } }).catch(e => ({ ok: false, error: e.message }));
    const m = /```[\w+#.-]*\s*\n([\s\S]*?)```/.exec(x.reply || x.text || '');
    if (m) { $('#srvCode').value = m[1]; $('#srvCodeOut').textContent = (x.reply || '').replace(m[0], '').trim().slice(0, 600); }
    else $('#srvCodeOut').textContent = x.reply || x.error || 'кода нет';
  };
  const wr = async (runIt) => {
    $('#srvCodeOut').textContent = '⏳';
    const x = await api('/api/servers', { body: { action: 'write', target: $('#srvTg3').value, path: $('#srvPath').value.trim(), content: $('#srvCode').value, run: runIt } });
    $('#srvCodeOut').textContent = (x.written ? 'записано: ' + x.written + '\n' : '') + (x.out || x.error || '') + (x.code ? '\n[код выхода ' + x.code + ']' : '');
  };
  $('#srvWrite').onclick = () => { if (confirm('Записать файл на сервер?')) wr(false); };
  $('#srvWriteRun').onclick = () => { if (confirm('Записать и запустить код на сервере?')) wr(true); };
  if (SRV.job) srvPoll();
}
async function srvPoll() {
  clearTimeout(SRV.timer);
  const box = $('#srvJob');
  if (!box || !SRV.job || !$('#viewServers').classList.contains('on')) return;
  const r = await api('/api/ssh/agent?id=' + encodeURIComponent(SRV.job)).catch(() => null);
  const j = r && r.job;
  if (!j) { box.textContent = 'задание не найдено'; return; }
  let h = '<div><b>' + esc(j.task) + '</b> — ' + esc(j.status) + ' <span class="muted">(' + esc(j.planner || '') + ')</span>' +
    (j.status === 'работает' || j.status === 'ждёт разрешения' ? ' <button class="btn" id="srvStop">⏹</button>' : '') + '</div>';
  (j.steps || []).forEach(s => {
    const a = s.act || {}, res = s.result || {};
    h += '<div style="border-top:1px solid var(--line,#333);padding:4px 0">' + (res.ok ? '✓' : '✗') + ' <b>' + esc(a.tool) + '</b> ' +
      esc(a.server || '') + ' <code>' + esc(a.command || a.path || '') + '</code>' +
      '<pre style="white-space:pre-wrap;max-height:200px;overflow:auto;font-size:12px;margin:2px 0">' + esc(String(res.out || res.error || '').slice(-3000)) + '</pre></div>';
  });
  if (j.pending) {
    const a = j.pending.act || {};
    h += '<div class="card" style="border:1px solid ' + (j.pending.kind === 'danger' ? '#e55' : '#ca4') + ';padding:8px;margin:6px 0">' +
      (j.pending.kind === 'danger' ? '⚠ ' : '❓ ') + esc(j.pending.why) + ':<pre style="white-space:pre-wrap">' +
      esc(a.command || (a.tool === 'write' ? 'запись ' + a.path + '\n' + String(a.content || '').slice(0, 1500) : a.tool)) + '</pre>' +
      '<button class="btn" id="srvYes">✅ Разрешить</button> <button class="btn" id="srvNo">❌ Отклонить</button></div>';
  }
  if (j.result) h += '<div style="margin-top:6px;white-space:pre-wrap"><b>Итог:</b> ' + esc(j.result) + '</div>';
  if (j.own_note) h += '<div class="muted" style="margin-top:4px">Своя LLM (учится в потоке «Администрирование серверов»): ' + esc(j.own_note) + '</div>';
  box.innerHTML = h;
  const dec = yes => api('/api/ssh/agent', { body: { action: 'approve', id: j.id, yes } }).then(srvPoll);
  if ($('#srvYes')) { $('#srvYes').onclick = () => dec(true); $('#srvNo').onclick = () => dec(false); }
  if ($('#srvStop')) $('#srvStop').onclick = () => api('/api/ssh/agent', { body: { action: 'stop', id: j.id } }).then(srvPoll);
  if (j.status === 'работает' || j.status === 'ждёт разрешения') SRV.timer = setTimeout(srvPoll, 1500);
}

/* ------------------------------- виды ------------------------------------ */
function showView(name) {
  const vEl = $('#view' + name.charAt(0).toUpperCase() + name.slice(1));
  if (!vEl) { if (name === 'thoughts') openChat('thoughts'); return; }
  $$('.view').forEach(v => v.classList.remove('on'));
  vEl.classList.add('on');
  if (name === 'chronicle') renderChronicle();
  if (name === 'vision') renderVision();
  if (name === 'applab') renderAppLab();
  if (name === 'desktop') renderDesktop(); else DESK.on = false;
  if (name === 'servers') renderServers();
  if (name === 'pulse' && window.N12UI) window.N12UI.renderPulse();
  if (name === 'chat') { $('#chatTitle').textContent = chatTitleFrom(S.chat || { title: 'Новый чат' }); }
  else $('#chatTitle').textContent =
    name === 'chronicle' ? 'Хроника плат' :
    name === 'vision' ? 'Зрение плат' :
    name === 'applab' ? 'App Lab · приложения на Python' :
    name === 'desktop' ? 'Рабочий стол компьютера' :
    name === 'servers' ? 'Серверы · администрирование' :
    name === 'pulse' ? 'Пульс мозга' : 'Новый чат';
  if (window.N12UI) window.N12UI.onView(name);
  // v3.149.15: из любого раздела — назад в чат (кнопка «← Чат», пункт «💬 Чат», Esc)
  const back = $('#btnBackChat'); if (back) back.hidden = name === 'chat';
  $$('.sidenav button[data-view="chat"]').forEach(b => b.classList.toggle('on', name === 'chat'));
  closeSidebar();
}

async function renderChronicle() {
  const box = $('#chronicleBox');
  box.innerHTML = '<div class="typing"><i></i><i></i><i></i></div>';
  const r = await api('/api/chronicle');
  box.innerHTML = '';
  if (!r.ok || !r.items.length) {
    box.innerHTML = '<div class="empty"><div class="big">📜</div><h2>Хроника пуста</h2>' +
      '<p>Сюда попадают сообщения плат: реплики чата, сводки, мысли. ' +
      'Подключи плату neuro_12 — и лента оживёт.</p></div>';
    return;
  }
  r.items.forEach(it => {
    const d = document.createElement('div');
    d.className = 'feed-item';
    d.innerHTML = '<div class="meta"><b>' + esc(it.board || '?') + '</b>' +
      '<span>' + esc(it.zone || '') + '</span><span>' +
      new Date((it.ts || 0) * 1000).toLocaleString('ru') + '</span></div>' +
      '<div class="txt">' + md(it.text || '') + '</div>';
    box.appendChild(d);
  });
}

let VIS_TIMER = null;
async function renderVision(quiet) {
  const box = $('#visionBox');
  if (!quiet) box.innerHTML = '<div class="typing"><i></i><i></i><i></i></div>';
  const [rc, rb] = await Promise.all([api('/api/vision/cams'),
    api('/api/vision/buffer?limit=8')]);
  const cams = (rc && rc.cams) || [];
  const buf = (rb && rb.items) || [];
  /* v3.130: пока раздел открыт — кадр обновляется сам каждые 4 с */
  clearTimeout(VIS_TIMER);
  VIS_TIMER = setTimeout(() => {
    const v = $('#viewVision');
    if (v && v.classList.contains('on')) renderVision(true);
  }, 4000);
  const ago = (t) => { const s = Math.round(Date.now() / 1000 - t);
    return s < 90 ? s + ' с назад' : s < 5400 ? Math.round(s / 60) + ' мин назад' : Math.round(s / 3600) + ' ч назад'; };
  if (!cams.length) {
    box.innerHTML = '<div class="empty"><div class="big">📷</div>' +
      '<h2>Кадров пока нет</h2><p>Ни одна плата ещё не прислала кадр и не сообщила о камере. ' +
      'На плате neuro_12 (v2.73+): ⚙ Настройки → «Камера» — включи и впиши адрес ESP32-CAM ' +
      '(например 192.168.1.50) или «uart», если камера подключена проводом. ' +
      'Строка «📷 камера» в панели платы покажет, идут ли кадры, а если нет — почему.</p></div>';
    return;
  }
  const grid = document.createElement('div');
  grid.className = 'grid2';
  cams.forEach(c => {
    const card = document.createElement('div');
    card.className = 'card';
    const live = c.ts && (Date.now() / 1000 - c.ts) < 120;
    const bd = c.board || null;
    let why = '';
    if (bd && bd.state && bd.state !== 'кадры идут') why = '📷 Плата: <b>' + esc(bd.state) + '</b>' + (bd.err ? ' — ' + esc(bd.err) : '');
    else if (bd && bd.up_err) why = '📷 Плата снимает, но ' + esc(bd.up_err);
    if (c.bad && c.bad_ts && (!c.ts || c.bad_ts > c.ts))
      why += (why ? '<br>' : '') + '⚠ Облако отвергло кадр ' + ago(c.bad_ts) + ': ' + esc(c.bad_why) + ' (показан последний целый кадр)';
    card.innerHTML = '<h3>📷 ' + esc(c.cam) +
      ' <span class="pill ' + (live ? 'ok' : 'bad') + '">' + (live ? 'в сети' : (c.ts ? 'молчит' : 'нет кадров')) + '</span></h3>' +
      (c.nofile ? '<div class="mini">Ни одного кадра от этой платы ещё не пришло.</div>' :
        '<img class="camimg" src="/api/vision/last.jpg?cam=' + encodeURIComponent(c.cam) + '&t=' + Date.now() + '" alt="кадр" ' +
        'onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{className:\'mini bad\',textContent:\'Кадр не открылся в браузере: файл повреждён или не JPEG.\'}))">') +
      '<div class="kv"><span>кадр:</span><b>' + (c.ts ? ago(c.ts) + (c.bytes ? ', ' + Math.round(c.bytes / 1024) + ' КБ' : '') : '—') + '</b></div>' +
      '<div class="kv"><span>видит (YOLO):</span><b>' + esc((c.names || []).join(', ') || '—') + '</b></div>' +
      (why ? '<div class="mini" style="margin-top:6px">' + why + '</div>' : '');
    grid.appendChild(card);
  });
  box.innerHTML = '';
  box.appendChild(grid);
  if (buf.length) {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<h3>Буфер детекций</h3>';
    buf.forEach(b => {
      card.innerHTML += '<div class="kv"><span>' + esc(b.cam) + ' · ' +
        esc(b.src || '') + ' · ' +
        new Date((b.ts || 0) * 1000).toLocaleTimeString('ru') +
        '</span><b>' + esc((b.names || []).join(', ') || '—') + '</b></div>';
    });
    box.appendChild(card);
  }
}

/* ============ APP LAB на Python (v3.170: навыки упразднены) ============= */
const AL = { apps: [], cur: null, guide: null };
const AL_TEMPLATE = "# Приложение App Lab: python/main.py\n# run(text, ctx) получает фразу из чата и возвращает ответ строкой.\n# ctx — настоящие функции облака (справка — кнопка «Инструкция и API»).\n\ndef run(text, ctx):\n    hits = ctx.web.search(text, 5)\n    if not hits:\n        return \"Ничего не нашла по «%s».\" % text\n    return \"\\n\".join(\"• %s — %s\" % (h[\"title\"], h[\"url\"]) for h in hits)\n";

function alEsc(t) { return String(t == null ? '' : t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

async function renderAppLab(openId) {
  const box = $('#applabBox');
  box.innerHTML = '<div class="typing"><i></i><i></i><i></i></div>';
  try {
    const r = await api('/api/applab/apps');
    AL.apps = r.apps || [];
  } catch (e) {
    box.innerHTML = '<div class="empty"><div class="big">🧩</div><h2>App Lab не загрузился</h2><p>' +
      alEsc(e && e.message || e) + '</p><button class="btn" id="alRetry">Повторить</button></div>';
    $('#alRetry').onclick = () => renderAppLab();
    return;
  }
  if (AL.next) { const f = AL.next; AL.next = null; return f(); }
  if (openId) return openApp(openId);
  drawAppLabList();
}

function drawAppLabList() {
  const box = $('#applabBox');
  let html = '<h2>🧩 App Lab — приложения на Python</h2>' +
    '<div class="hint">Навыки облака теперь — приложения App Lab, как в Arduino App Lab: папка с описанием и <code>python/main.py</code>, ' +
    'где <code>run(text, ctx)</code> возвращает ответ. Чат зовёт приложение по его словам-триггерам, а примеры включённых приложений видны кнопками в окне чата.</div>' +
    '<div class="frow" style="gap:8px;flex-wrap:wrap;margin:10px 0">' +
    '<button class="btn" id="alNew">＋ Новое приложение</button>' +
    '<button class="btn ghost" id="alGuide">📘 Инструкция и API</button></div>' +
    '<div class="fgroup"><label for="alAiDesc"><b>🤖 ИИ, напиши приложение</b> — опиши, что оно должно делать (например: «ищет билеты на поезд и показывает ссылки»). Первой пишет своя LLM (если училась коду), потом локальная модель, потом заготовки; каждый код проверяется запуском.</label>' +
    '<div class="frow" style="gap:6px"><input id="alAiDesc" style="flex:1" placeholder="что должно делать приложение"><button class="btn" id="alAiGo">Написать</button></div>' +
    '<div class="mini" id="alAiState"></div></div>';
  if (!AL.apps.length) {
    html += '<div class="empty"><div class="big">🧩</div><h2>Приложений пока нет</h2><p>Нажми «＋ Новое приложение».</p></div>';
  } else {
    html += '<div class="al-list">' + AL.apps.map(m =>
      '<div class="fgroup al-app" style="display:flex;gap:10px;align-items:flex-start' + (m.enabled ? '' : ';opacity:.6') + '">' +
      '<div style="font-size:22px">' + alEsc(m.icon || '🧩') + '</div><div style="flex:1;min-width:0">' +
      '<b>' + alEsc(m.name) + '</b> <span class="mini">' + alEsc(m.id) + ' · v' + (m.ver || 1) + ' · ' + alEsc(m.origin || '') +
      ' · запусков ' + (m.runs || 0) + (m.last_ok === false ? ' · ⚠ ' + alEsc(m.last_err || 'ошибка') : '') + '</span>' +
      '<div class="mini">' + alEsc(m.desc || '') + '</div>' +
      ((m.triggers || []).length ? '<div class="mini">триггеры: ' + alEsc(m.triggers.join(', ')) + '</div>' : '<div class="mini">без триггеров — запускается только отсюда</div>') +
      '<div class="frow" style="gap:6px;margin-top:6px;flex-wrap:wrap">' +
      '<button class="btn ghost" data-open="' + alEsc(m.id) + '">Открыть код</button>' +
      '<label class="mini"><input type="checkbox" data-en="' + alEsc(m.id) + '"' + (m.enabled ? ' checked' : '') + '> включено</label>' +
      '<label class="mini"><input type="checkbox" data-chat="' + alEsc(m.id) + '"' + (m.chat ? ' checked' : '') + '> в окне чата</label>' +
      '</div></div></div>').join('') + '</div>';
  }
  box.innerHTML = html;
  $('#alNew').onclick = () => { AL.cur = { id: '', name: '', icon: '🧩', desc: '', triggers: [], examples: [], chat: true, enabled: true, code: AL_TEMPLATE }; drawEditor(); };
  $('#alGuide').onclick = showGuide;
  $('#alAiGo').onclick = async () => {
    const d = $('#alAiDesc').value.trim(); if (!d) return;
    $('#alAiState').textContent = 'Пишу приложение и проверяю запуском… это может занять до пары минут.';
    try {
      const r = await api('/api/applab/ai', { body: { desc: d } });
      $('#alAiState').textContent = r.ok ? (r.result || ('Готово: ' + (r.app && r.app.name || r.id))) : ('Не вышло: ' + (r.error || '?'));
      if (r.ok) setTimeout(() => renderAppLab(r.id), 600);
    } catch (e) { $('#alAiState').textContent = 'Ошибка: ' + (e && e.message || e); }
  };
  $$('[data-open]', box).forEach(b => b.onclick = () => openApp(b.dataset.open));
  $$('[data-en]', box).forEach(c => c.onchange = async () => { await api('/api/applab/flags', { body: { id: c.dataset.en, enabled: c.checked } }); renderAppLab(); });
  $$('[data-chat]', box).forEach(c => c.onchange = async () => { await api('/api/applab/flags', { body: { id: c.dataset.chat, chat: c.checked } }); });
}

async function showGuide() {
  const box = $('#applabBox');
  if (!AL.guide) AL.guide = await api('/api/applab/guide');
  const g = AL.guide;
  box.innerHTML = '<div class="frow" style="gap:8px;margin-bottom:8px"><button class="btn ghost" id="alBack">← к приложениям</button></div>' +
    '<h2>📘 Как ИИ пишет приложения App Lab</h2><pre class="al-pre">' + alEsc(g.guide) + '</pre>' +
    '<h3>API ctx</h3><pre class="al-pre">' + alEsc(g.api) + '</pre>' +
    '<h3>🧠 Инструкция для своей LLM</h3><div class="mini">' + (g.llm_ready ? 'Своя LLM уже училась коду — в «ИИ, напиши приложение» она пишет первой.' : 'Своя LLM ещё не училась коду: она учится на коде включённых приложений App Lab при дообучении; пока за неё пишет локальная модель или заготовки.') + '</div><pre class="al-pre">' + alEsc(g.llm_guide || '') + '</pre>' +
    '<h3>Кирпичики старого App Lab (ctx.brick)</h3><div class="mini">' +
    (g.bricks || []).map(b => '<div><code>ctx.brick("' + alEsc(b.id) + '"' + (b.params.length ? ', ' + b.params.map(p => alEsc(p) + '=…').join(', ') : '') + ')</code> — ' + alEsc(b.desc) + '</div>').join('') + '</div>' +
    '<h3>Инструкция ИИ облака (собирается сама)</h3><pre class="al-pre">' + alEsc(g.instructions || '') + '</pre>';
  $('#alBack').onclick = () => drawAppLabList();
}

async function openApp(id, runNow) {
  const r = await api('/api/applab/get?id=' + encodeURIComponent(id));
  if (!r.ok) { toast(r.error || 'не открылось'); return; }
  AL.cur = r.app;
  drawEditor();
  if (runNow) runApp();
}

function drawEditor() {
  const box = $('#applabBox'), m = AL.cur;
  box.innerHTML = '<div class="frow" style="gap:8px;margin-bottom:8px;flex-wrap:wrap"><button class="btn ghost" id="alBack">← к приложениям</button>' +
    '<button class="btn" id="alSave">💾 Сохранить</button><button class="btn ghost" id="alRun">▶ Запустить</button>' +
    (m.id ? '<button class="btn ghost" id="alDel">🗑 Удалить</button>' : '') + '<button class="btn ghost" id="alGuide2">📘 API</button></div>' +
    '<div class="frow" style="gap:6px;flex-wrap:wrap">' +
    '<input id="alIcon" style="width:56px" maxlength="4" value="' + alEsc(m.icon || '🧩') + '" title="значок">' +
    '<input id="alName" style="flex:1" placeholder="Название приложения" value="' + alEsc(m.name) + '">' +
    '<input id="alId" style="width:180px" placeholder="id (латиница)" value="' + alEsc(m.id) + '"' + (m.id ? ' readonly' : '') + '></div>' +
    '<input id="alDesc" style="width:100%;margin-top:6px" placeholder="Что делает (это читают ИИ и владелец)" value="' + alEsc(m.desc) + '">' +
    '<input id="alTrig" style="width:100%;margin-top:6px" placeholder="Слова-триггеры через запятую (например: купить, сколько стоит; или re:^курс\\s+\\w+)" value="' + alEsc((m.triggers || []).join(', ')) + '">' +
    '<input id="alEx" style="width:100%;margin-top:6px" placeholder="Примеры фраз через ; — кнопки в окне чата" value="' + alEsc((m.examples || []).join('; ')) + '">' +
    '<div class="frow" style="gap:12px;margin-top:6px"><label class="mini"><input type="checkbox" id="alEn"' + (m.enabled !== false ? ' checked' : '') + '> включено</label>' +
    '<label class="mini"><input type="checkbox" id="alChat"' + (m.chat !== false ? ' checked' : '') + '> показывать в окне чата</label></div>' +
    '<div class="mini" style="margin-top:8px">python/main.py</div>' +
    '<textarea id="alCode" spellcheck="false" style="width:100%;min-height:320px;font-family:ui-monospace,Consolas,monospace;font-size:13px;tab-size:4">' + alEsc(m.code || AL_TEMPLATE) + '</textarea>' +
    '<div class="frow" style="gap:6px;margin-top:6px"><input id="alInput" style="flex:1" placeholder="Фраза для проверки (text)" value="' + alEsc((m.examples || [])[0] || '') + '"></div>' +
    '<pre class="al-pre" id="alOut" style="min-height:60px">Результат запуска появится здесь.</pre>';
  const ta = $('#alCode');
  ta.addEventListener('keydown', e => { if (e.key === 'Tab') { e.preventDefault(); const s0 = ta.selectionStart; ta.setRangeText('    ', s0, ta.selectionEnd, 'end'); } });
  $('#alBack').onclick = () => renderAppLab();
  $('#alSave').onclick = saveApp;
  $('#alRun').onclick = runApp;
  $('#alGuide2').onclick = showGuide;
  if ($('#alDel')) $('#alDel').onclick = async () => {
    if (!confirm('Удалить приложение «' + m.name + '»?')) return;
    await api('/api/applab/delete', { body: { id: m.id } }); renderAppLab();
  };
}

function alForm() {
  return { id: $('#alId').value.trim(), name: $('#alName').value.trim(), icon: $('#alIcon').value.trim(),
    desc: $('#alDesc').value.trim(), triggers: $('#alTrig').value, examples: $('#alEx').value,
    enabled: $('#alEn').checked, chat: $('#alChat').checked, code: $('#alCode').value };
}

async function saveApp() {
  const f = alForm();
  if (!f.name) { toast('Назови приложение'); return; }
  try {
    const r = await api('/api/applab/save', { body: f });
    if (!r.ok) { $('#alOut').textContent = '⚠ ' + (r.error || 'не сохранилось'); return; }
    AL.cur = Object.assign({}, r.app, { code: f.code });
    toast('Сохранено: ' + r.app.name + ' (v' + r.app.ver + ')');
    drawEditor();
  } catch (e) { $('#alOut').textContent = '⚠ ' + (e && e.message || e); }
}

async function runApp() {
  const f = alForm();
  $('#alOut').textContent = 'Запускаю…';
  try {
    const r = await api('/api/applab/run', { body: { id: f.id || 'draft', code: f.code, text: $('#alInput').value } });
    $('#alOut').textContent = (r.ok ? '✅ ' : '⚠ ' + (r.error || '') + '\n') + (r.result || '') +
      ((r.log || []).length ? '\n\nжурнал:\n' + r.log.join('\n') : '') + (r.ms != null ? '\n\n(' + r.ms + ' мс)' : '');
  } catch (e) { $('#alOut').textContent = '⚠ ' + (e && e.message || e); }
}

async function refreshStatus() {
  try {
    const r = await api('/api/status');
    if (!r || !r.ok) return;
    $('#dotCloud').className = 'dot ok';
    $('#boardsOnline').textContent =
      r.stats.boards_online + '/' + r.stats.boards_total;
    $('#dotBoards').className =
      'dot ' + (r.stats.boards_online > 0 ? 'ok' : 'bad');
    $('#netNodes').textContent =
      (r.cloud_readings && r.cloud_readings.nodes) || 0;
    const ver = $('#verline');
    if (ver) ver.textContent = 'neuro12-cloud v' + r.version +
      (r.load && r.load.cores ? ' · ЦП ' + r.load.cpu_pct + '% · ОЗУ ' +
        Math.round(r.load.rss_mb) + ' МБ' : '');
  } catch (e) {
    $('#dotCloud').className = 'dot bad';
  }
}

/* ======================= НАСТРОЙКИ (модал) ================================ */
let SETTINGS = null;

async function openSettings() {
  $('#settingsVeil').classList.add('on');
  const [rs, rset] = await Promise.all([api('/api/status'),
    api('/api/settings')]);
  S.status = rs; SETTINGS = rset;
  renderTab('common');
}

function closeSettings() { $('#settingsVeil').classList.remove('on'); }

const TAB_BUSY = {};   // v3.155: вкладка уже рисуется — второй раз не рисовать
function renderTab(name) {
  $$('.mtabs button[data-tab]').forEach(b =>
    b.classList.toggle('on', b.dataset.tab === name));
  $$('.mpane').forEach(p => p.classList.toggle('on',
    p.dataset.pane === name));
  if (TAB_BUSY[name]) return;   // двойной клик: раньше две отрисовки шли наперегонки
  TAB_BUSY[name] = true;        // и писали в блоки, которые уже заменены
  let r;
  try {
    r = ({ common: tabCommon, devices: tabDevices, ssh: tabSsh,
     memory: tabMemory, kb: tabKB, zones: tabZones,
     teacher: tabTeacher, instruct: tabInstruct, voice: tabVoice, security: tabSecurity,
     maxbot: tabMaxbot, tgbot: tabTgbot, aiapi: tabAiApi, update: tabUpdate,
     log: tabLog }[name] || tabCommon)();
  } catch (e) { TAB_BUSY[name] = false; throw e; }
  Promise.resolve(r).catch(() => {}).finally(() => { TAB_BUSY[name] = false; });
}

async function tabInstruct() {
  const pane = $('[data-pane="instruct"]');
  pane.textContent = 'Загружаю инструкции ИИ…';
  try {
    const r = await api('/api/agent/rules');
    if (!r.ok || !r.rules) throw new Error(r.error || 'Сервер не вернул инструкции');
    const list = a => '<ol>' + (a || []).map(x => '<li>' + esc(x) + '</li>').join('') + '</ol>';
    pane.innerHTML = '<h2>Инструкции ИИ</h2>' +
      '<h3>Программирование</h3>' + list(r.rules.coding) +
      '<h3>Разговор</h3>' + list(r.rules.talk) +
      (r.rules.zai && r.rules.zai.length ? '<h3>Стиль z.ai / GLM</h3>' + list(r.rules.zai) : '');
  } catch (e) {
    pane.textContent = 'Не удалось загрузить инструкции: ' + e.message;
    const retry = document.createElement('button');
    retry.className = 'btn'; retry.textContent = 'Повторить'; retry.onclick = tabInstruct;
    pane.appendChild(retry);
  }
}

/* ------------------- v3.28: API для приложений и ответы ------------------- */
async function tabAiApi() {
  const p = $('[data-pane="aiapi"]');
  const s = (SETTINGS && SETTINGS.settings) || {};
  const base = location.origin + '/api/v1';
  p.innerHTML = '<h2>API и ответы</h2>' +
    '<h3>Шаблонный ответ: приветствие + ответ</h3>' +
    '<div class="hint">Ответ начинается с приветствия по времени суток и имени ' +
    'собеседника. Не влез в одно сообщение — уходит несколькими (по абзацам, ' +
    'не посреди фразы и не посреди кода). Если мозг уже поздоровался сам — ' +
    'второй раз не здоровается.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tplBot"' +
    (s.tpl_bot !== false ? ' checked' : '') + '><label for="tplBot">В боте МАКС</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tplChat"' +
    (s.tpl_chat ? ' checked' : '') + '><label for="tplChat">В чате облака</label></div>' +
    '<div class="frow"><div class="fgroup" style="flex:2"><label>Приветствие ' +
    '({приветствие} — «Добрый день», {имя} — имя собеседника)</label>' +
    '<input type="text" id="tplGreet" value="' + esc(s.tpl_greet || '{приветствие}, {имя}!') + '"></div>' +
    '<div class="fgroup"><label>Сообщений на ответ (1–5)</label>' +
    '<input type="number" id="tplParts" min="1" max="5" value="' + (s.tpl_parts || 2) + '"></div>' +
    '<div class="fgroup"><label>Здороваться только в начале диалога: новый диалог — ' +
    'после паузы, ч</label>' +
    '<input type="number" id="tplHours" min="0" max="72" value="' +
    (s.tpl_greet_gap_h != null ? s.tpl_greet_gap_h : 4) + '"></div></div>' +
    '<button class="btn" id="tplSave">Сохранить шаблон</button>' +
    '<h3 style="margin-top:18px">API для других приложений</h3>' +
    '<div class="hint">Приложения подключаются к мозгу по ключу. Адрес: <code>' +
    esc(base) + '</code>. Заголовок: <code>Authorization: Bearer &lt;ключ&gt;</code>. ' +
    'Есть совместимость с форматом OpenAI Chat Completions — подойдут готовые ' +
    'клиенты (base_url = адрес выше, model = mozg-12). Ключ показывается ОДИН раз ' +
    '— на сервере хранится только его хеш.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="aiOn"><label for="aiOn">API включено</label></div>' +
    '<div class="frow"><div class="fgroup" style="flex:2"><label>Название приложения</label>' +
    '<input type="text" id="aiName" placeholder="например: сайт поддержки"></div>' +
    '<div class="fgroup"><label>Запросов в минуту на ключ</label>' +
    '<input type="number" id="aiRate" min="1" max="1000"></div>' +
    '<button class="btn" id="aiCreate">Выдать ключ</button></div>' +
    '<div id="aiNew"></div><div id="aiKeys" style="margin-top:8px"></div>' +
    '<details style="margin-top:10px"><summary>Примеры запросов</summary>' +
    '<pre style="white-space:pre-wrap;font-size:12px">' + esc(
      'curl -X POST ' + base + '/ask \\\n' +
      '  -H "Authorization: Bearer n12-…" -H "Content-Type: application/json" \\\n' +
      '  -d \'{"question": "как обновить РЕД ОС?", "session": "user42"}\'\n\n' +
      '# ответ: {"ok":true,"answer":"…","zone":"База знаний","ms":120,...}\n' +
      '# mode: "auto" (весь мозг) | "talk" (разговор + мысли облака) | "code"\n' +
      '# template: true — вернёт parts: приветствие + ответ по сообщениям\n\n' +
      '# Python (пакет openai):\n' +
      'from openai import OpenAI\n' +
      'c = OpenAI(base_url="' + base + '", api_key="n12-…")\n' +
      'r = c.chat.completions.create(model="mozg-12",\n' +
      '    messages=[{"role": "user", "content": "что такое нейросеть?"}])\n' +
      'print(r.choices[0].message.content)\n\n' +
      '# оценка последнего ответа сессии (обучение): POST ' + base + '/rate {"session":"user42","v":1}') +
      '</pre></details>' +
    '<h3 style="margin-top:18px">Обучение на вопросах и ответах</h3>' +
    '<div class="hint">Вопросы учат всегда. Ответы запоминаются, только если опираются ' +
    'на источник (база знаний, интернет, расчёт) — свои догадки мозг знанием не ' +
    'считает. Подтверждённым ответ становится после 👍 в чате или исправления ' +
    '«правильный ответ: …» (от владельца). 👎 — ответ больше не повторяется.</div>' +
    '<div id="qaBox" class="mini"><div class="typing"><i></i><i></i><i></i></div></div>' +
    '<h3 style="margin-top:18px">Локальная LLM, зрение и видео</h3>' +
    '<div class="hint">Облако модели НЕ скачивает. Поставь локальную модель с ' +
    'OpenAI-совместимым API (например, Ollama: <code>ollama run qwen2.5:1.5b</code>, ' +
    'для картинок — <code>llava</code>) и впиши адрес. Тогда ответы знаний модель ' +
    'пишет связно и по делу ТОЛЬКО по фрагментам базы и интернета, помня диалог; ' +
    'плата получает это через весь мозг облака. Без модели всё работает как раньше.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="llmOn"><label for="llmOn">' +
    'Локальная LLM пишет ответы по материалам</label></div>' +
    '<div class="frow"><div class="fgroup" style="flex:2"><label>Адрес (OpenAI-совместимый)</label>' +
    '<input type="text" id="llmUrl" placeholder="http://127.0.0.1:11434/v1"></div>' +
    '<div class="fgroup"><label>Модель</label><input type="text" id="llmModel" ' +
    'placeholder="qwen2.5:1.5b"></div></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="visOn"><label for="visOn">' +
    'Модель со зрением описывает картинки (llava и т.п.)</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="vidOn"><label for="vidOn">' +
    'Любопытство смотрит видео (субтитры YouTube) — не только Википедию</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="wkbOn"><label for="wkbOn">' +
    'Ответы из интернета сохранять в базу знаний (обучение контексту вопроса)</label></div>' +
    '<button class="btn" id="llmSave">Сохранить</button> ' +
    '<button class="btn ghost" id="llmTest">Проверить модель</button>' +
    '<div id="llmBox" class="mini" style="margin-top:6px"></div>' +
    '<h3 style="margin-top:18px">Своя LLM облака (tinyllm)</h3>' +
    '<div class="hint">Настоящий декодер-трансформер (эмбеддинги, N блоков внимания с ' +
    'FFN, LayerNorm, tied-голова, Adam), учится на статьях базы в фоне. Потолков у размеров нет: ' +
    'ставь любые значения — они сохраняются. Оценка нагрузки учитывает ваш компьютер (ОЗУ всего и ' +
    'свободную сейчас, скорость ЦП). Правки размеров НЕ сбрасываются, пока не нажато «Применить» ' +
    '(поля с оранжевой рамкой); галочки и параметры связи сохраняются сразу по клику. ' +
    'Смена размеров = обучение заново. Размеры работают только с numpy.</div>' +
    '<div id="tlNp" class="warnbox" style="display:none"></div>' +
    '<div id="tlGh" class="hint"></div>' +
    '<div id="tlHear" class="hint"></div>' +
    '<div id="tlSys" class="mini"></div>' +
    '<div class="frow">' +
    '<div class="fgroup"><label>d_model</label><input type="number" id="tlD" min="1" step="1"></div>' +
    '<div class="fgroup"><label>слоёв</label><input type="number" id="tlL" min="1"></div>' +
    '<div class="fgroup"><label>голов</label><input type="number" id="tlH" min="1"></div>' +
    '<div class="fgroup"><label>контекст, токенов</label><input type="number" id="tlC" min="4"></div>' +
    '<div class="fgroup"><label>словарь</label><input type="number" id="tlV" min="64" step="1"></div>' +
    '<div class="fgroup"><label>доля ЦП (≥0.01; 1 и больше — без пауз)</label><input type="number" id="tlCpu" min="0.01" step="0.05"></div>' +
    '<div class="fgroup"><label>дообучение: не больше шагов за запуск (0 — без лимита)</label><input type="number" id="tlIncMax" min="0" step="50"></div>' +
    '<div class="fgroup"><label>дообучение: не дольше минут за запуск (0 — без лимита)</label><input type="number" id="tlIncMin" min="0" step="5"></div></div>' +
    '<div class="frow">' +
    '<div class="fgroup"><label>эпох</label><input type="number" id="tlE" min="1"></div>' +
    '<div class="fgroup"><label>батч</label><input type="number" id="tlB" min="1"></div>' +
    '<div class="fgroup"><label>токенов корпуса</label><input type="number" id="tlCT" min="100" step="1000"></div>' +
    '<div class="fgroup"><label>скорость обучения</label><input type="number" id="tlLR" min="0" step="0.0005"></div></div>' +
    '<div class="frow">' +
    '<div class="fgroup"><label>температура</label><input type="number" id="tlT" min="0.01" step="0.05"></div>' +
    '<div class="fgroup"><label>top-k (0 — выкл.)</label><input type="number" id="tlK" min="0"></div>' +
    '<div class="fgroup"><label>top-p (0.01–1)</label><input type="number" id="tlP" min="0.01" max="1" step="0.01"></div>' +
    '<div class="fgroup"><label>штраф повторов (≥1)</label><input type="number" id="tlR" min="1" step="0.05"></div>' +
    '<div class="fgroup"><label>новых токенов</label><input type="number" id="tlN" min="1"></div></div>' +
    '<div id="tlProbe" class="mini"></div>' +
    '<div id="tlDirty" class="mini" style="color:#d19a1e;margin:4px 0"></div>' +
    '<button class="btn" id="tlApply">Применить размеры и переобучить</button> ' +
    '<button class="btn ghost" id="tlRetrain">Переобучить на текущей базе</button> ' +
    '<button class="btn ghost" id="tlRec">Взять рекомендуемые размеры</button> ' +
    '<button class="btn danger" id="tlDelete" style="background:#b8443a;color:#fff">🗑 Удалить нейросеть</button>' +
    '<div class="fgroup switch" style="margin-top:10px"><input type="checkbox" id="tlDisk"><label for="tlDisk">' +
    'Состояние Adam на диске — «виртуальный размер» (v3.78): можно обучать модель больше, чем помещается в ОЗУ вместе с оптимизатором; шаг медленнее (обмен с диском). Бюджет диска — поле ниже, 0 = половина свободного места (до 20 ГБ). Применяется при следующем обучении.</label></div>' +
    '<div class="fgroup switch" style="margin-top:10px"><input type="checkbox" id="tlSwap"><label for="tlSwap">' +
    'Учитывать файл подкачки (v3.80): если модель не помещается в свободную ОЗУ, но помещается вместе с подкачкой, обучение разрешается (вердикт «уйдёт в подкачку», заметно медленнее), а рекомендуемые размеры считаются с учётом свободной подкачки (она учитывается вдвое дешевле ОЗУ).</label></div>' +
    '<div class="fgroup"><label>Бюджет диска под модель, МБ (0 — авто)</label><input type="number" id="tlDiskMb" min="0"></div>' +
    '<div class="fgroup"><label>Секунд на один шаг обучения (v3.100; 10 — по умолчанию, 0 — без подстройки): облако само подбирает, сколько примеров брать в шаг (до «батча»), а если и одного много — укорачивает окно; применяется сразу, без переобучения</label><input type="number" id="tlStepS" min="0" step="1"></div>' +
    '<div class="fgroup"><label>Шагов в плане обучения не больше (v3.101; 3000 ≈ 8 ч при 10 с на шаг; 0 — без потолка): план идёт по всему корпусу по разу, на потолке останавливается, а статьи, что не успели, доучивает дообучение по частям; применяется сразу</label><input type="number" id="tlPlanN" min="0" step="500"></div>' +
    '<div class="fgroup"><label>Потолок времени обучения, часов (0 — без; 20 = не дольше 20 ч: число шагов подгоняется под скорость компьютера, потерь будет больше, если модель большая)</label><input type="number" id="tlTimeH" min="0" step="0.5"></div>' +
    '<div class="fgroup"><label>Предложений в ответе (мозг + LLM), 2–30 (10 ≈ объём чат-бота)</label><input type="number" id="tlAnsN" min="2" max="30"></div>' +
    '<div id="tlBrainTr" class="mini" style="margin:8px 0"></div>' +
    '<button class="btn ghost" id="tlBrainBtn">Обучить мозг на всей базе</button>' +
    '<div class="fgroup switch" style="margin-top:10px"><input type="checkbox" id="tlWordOnly"><label for="tlWordOnly">' +
    'Только слова из базы (v3.71): слова вне словаря при обучении НЕ разбираются по буквам, поэтому модель не учится «собирать» выдуманные слова. Чтобы старая модель это «забыла», нажми «Переобучить на текущей базе».</label></div>' +
    '<div class="fgroup switch" style="margin-top:10px"><input type="checkbox" id="tlForce"><label for="tlForce">' +
    'Обучать всё равно, даже если памяти не хватает</label></div>' +
    '<h4 style="margin:14px 0 4px">Связь с мозгом облака и платами</h4>' +
    '<div class="hint">LLM — ещё один думающий участник: её мысли идут в готовый модуль мыслей (журнал и ' +
    'лента «Мысли плат»), оттуда их видит мозг облака (зона «Мысли плат», «мысль облака») и платы. ' +
    'Мусор не пускается: порог качества мысли. Своё LLM в обучение не берёт — учится на ' +
    'подтверждённых ответах мозга и мыслях плат. Все функции платформы доступны LLM через ' +
    '<code>POST /api/llm/chat</code>.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tlLinkNet"><label for="tlLinkNet">' +
    'Мысли LLM попадают в сеть материалов мозга (мозг их «видит»)</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tlLinkBoards"><label for="tlLinkBoards">' +
    'Мысли LLM — на платы (пин D6; D6 включён всегда)</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tlLinkLearn"><label for="tlLinkLearn">' +
    'Дообучение: новые статьи и материалы мозга LLM учит сама, без переобучения с нуля (старое повторяет, словарь растёт)</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tlLinkRead"><label for="tlLinkRead">' +
    'Опыт чтения: LLM «читает» статьи базы знаний и передаёт мозгу выжимку (в сеть материалов и мысли)</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tlLinkExt"><label for="tlLinkExt">' +
    'LLM развивает мысли плат по фактам базы знаний (не заменяет их: мозг думает лучше)</label></div>' +
    '<div class="frow">' +
    '<div class="fgroup"><label>думать сама раз в, сек (0 — выкл.)</label><input type="number" id="tlEvery" min="0" step="30"></div>' +
    '<div class="fgroup"><label>порог качества мысли (0–1; 0 — пускать всё)</label><input type="number" id="tlQ" min="0" max="1" step="0.05"></div>' +
    '<div class="fgroup"><label>порог опоры на факты в ответах (0,3–1; было 0,7, теперь 0,5)</label><input type="number" id="tlGQ" min="0.3" max="1" step="0.05"></div></div>' +
    '<button class="btn ghost" id="tlThinkBtn">💭 Подумать сейчас</button>' +
    '<div id="tlThink" class="mini" style="margin-top:6px"></div>' +
    '<div id="tlBox" class="mini" style="margin-top:6px"></div>';
  const drawLlm = (r) => {
    const l = (r && r.llm) || {};
    $('#llmOn').checked = !!l.llm_answer_on; $('#visOn').checked = !!l.vision_llm_on;
    $('#vidOn').checked = l.curiosity_video !== false; $('#wkbOn').checked = l.web_learn_kb !== false;
    $('#llmUrl').value = l.llm_answer_url || ''; $('#llmModel').value = l.llm_answer_model || '';
  };
  const llmBody = () => ({ llm_answer_on: $('#llmOn').checked, vision_llm_on: $('#visOn').checked,
    curiosity_video: $('#vidOn').checked, web_learn_kb: $('#wkbOn').checked,
    llm_answer_url: $('#llmUrl').value.trim(), llm_answer_model: $('#llmModel').value.trim() });
  drawLlm(await api('/api/llm'));
  $('#llmSave').onclick = async () => { drawLlm(await api('/api/llm', { body: llmBody() }));
    toast('Сохранено', 'ok'); };
  $('#llmTest').onclick = async () => {
    $('#llmBox').textContent = 'Спрашиваю модель…';
    const rr = await api('/api/llm', { body: Object.assign(llmBody(), { test: true }) });
    $('#llmBox').textContent = rr.ok ? ('Модель ответила: «' + (rr.test || '') + '»') :
      ('Нет связи: ' + (rr.error || '?'));
  };
  /* v3.33: своя LLM — статус, нагрузка компьютера, размеры, связь с мозгом.
     Форма НЕ перезаписывается таймером: поле с правкой (оранжевая рамка) и поле в
     фокусе остаются как есть до «Применить»; галочки и параметры связи — по клику. */
  const fmtEta = (s) => !s ? '' : (s > 7200 ? Math.round(s / 3600) + ' ч' : s > 120 ?
    Math.round(s / 60) + ' мин' : s + ' с');
  const TL_NUM = { tlD: 'llm_d', tlL: 'llm_layers', tlH: 'llm_heads', tlC: 'llm_ctx', tlV: 'llm_vocab',
    tlCpu: 'llm_cpu', tlTimeH: 'llm_time_h', tlIncMax: 'llm_inc_max_steps', tlIncMin: 'llm_inc_max_min', tlE: 'llm_epochs', tlB: 'llm_batch', tlCT: 'llm_corpus_tokens', tlLR: 'llm_lr',
    tlT: 'llm_temp', tlK: 'llm_topk', tlP: 'llm_topp', tlR: 'llm_rep', tlN: 'llm_max_new' };
  // v3.99: связь LLM с мозгом, ответы по фактам и «без строки 🤖» — часть режима «мозг + своя LLM»
  const TL_SW = { tlForce: 'llm_force', tlLinkNet: 'llm_link_net',
    tlLinkBoards: 'llm_link_boards', tlLinkExt: 'llm_link_ext', tlLinkRead: 'llm_link_read', tlLinkLearn: 'llm_link_learn' };
  const TL_AUTO = { tlEvery: 'llm_link_every', tlQ: 'llm_link_minq', tlGQ: 'llm_link_gen_minq', tlStepS: 'llm_step_s', tlPlanN: 'llm_plan_steps' };
  const tlDirty = new Set();
  let tlBusy = 0, tlLastProbe = null;
  const tlSet = (id, v) => { const e = $('#' + id);
    if (!e || v == null || tlDirty.has(id) || document.activeElement === e) return; e.value = v; };
  const tlChk = (id, v) => { const e = $('#' + id); if (e && !tlBusy && v != null) e.checked = !!v; };
  const tlMark = () => {
    Object.keys(TL_NUM).forEach(id => { const e = $('#' + id); if (e) e.style.outline = tlDirty.has(id) ? '2px solid #d19a1e' : ''; });
    $('#tlDirty').textContent = tlDirty.size ? 'Есть неприменённые правки размеров (' + tlDirty.size +
      ') — нажми «Применить размеры и переобучить». Пока не нажато, панель их не сбрасывает.' : '';
  };
  let tlLastR = null;
  window.tabAiApiRefreshGh = () => setTimeout(async () => { try { drawTl(await api('/api/tinyllm')); } catch (e) {} }, 400);
  const drawTl = (r) => {
    tlLastR = r;
    const t = (r && r.llm) || {}, c = (r && r.config) || {}, lk = (r && r.link) || {};
    if (!$('#tlD')) return;
    Object.keys(TL_NUM).forEach(id => tlSet(id, c[TL_NUM[id]]));
    tlSet('tlEvery', lk.every); tlSet('tlQ', lk.minq); tlSet('tlGQ', lk.gen_minq); tlSet('tlStepS', c.llm_step_s); tlSet('tlPlanN', c.llm_plan_steps);
    const bt = (r && r.brain_train) || null;
    if (bt && $('#tlBrainTr')) $('#tlBrainTr').innerHTML = '<b>Мозг (сеть материалов):</b> ' +
      (bt.running ? 'учится на базе знаний — ' + bt.done + ' из ' + bt.total + ' статей, новых в сети ' + bt.new :
       (bt.total ? 'обучен: просмотрено ' + bt.total + ' статей, новых в сети ' + bt.new : 'после запуска начнёт учиться сам')) +
      (bt.auto ? ' (автозапуск после старта включён)' : ' (автозапуск выключен)') + (bt.error ? ' · ошибка: ' + esc(bt.error) : '');
    tlChk('tlWordOnly', c.llm_wordonly); tlChk('tlDisk', c.llm_disk_state); tlChk('tlSwap', c.llm_use_swap);
    tlSet('tlDiskMb', c.llm_disk_mb); tlSet('tlAnsN', c.llm_answer_sents); tlChk('tlForce', c.llm_force); tlChk('tlLink', lk.on); tlChk('tlLinkNet', lk.net);
    tlChk('tlLinkBoards', lk.boards); tlChk('tlLinkReply', lk.reply); tlChk('tlLinkGen', lk.gen); tlChk('tlLinkExt', lk.ext); tlChk('tlLinkRead', lk.read); tlChk('tlLinkLearn', lk.learn);
    // numpy: без него размеры из настроек не применяются (работает старая мини-модель)
    const np = (r && r.numpy) || {}, nb = $('#tlNp');
    if (np.have === false) {
      nb.style.display = '';
      nb.innerHTML = '<b>numpy не установлен</b> — поэтому работает только старая мини-модель на чистом Python, ' +
        'а размеры ниже НЕ применяются. Установка: кнопка ниже (команда <code>' + esc(np.cmd || 'pip install numpy') +
        '</code>, нужен интернет). ' + (np.state === 'running' ? '<i>Ставлю… это 1–3 минуты.</i> ' : '') +
        (np.state === 'error' ? '<span class="bad">Не вышло: ' + esc((np.log || '').slice(-300)) + '</span> ' : '') +
        '<br><button class="btn" id="tlNpBtn"' + (np.state === 'running' ? ' disabled' : '') + '>Установить numpy</button>';
      const nbtn = $('#tlNpBtn');
      if (nbtn) nbtn.onclick = async () => { await api('/api/tinyllm/numpy', { body: {} });
        toast('Ставлю numpy — несколько минут', 'ok'); };
    } else if (np.have && np.state === 'done') {
      nb.style.display = ''; nb.innerHTML = '✓ numpy установлен — настоящая LLM с вашими размерами включена.';
    } else { nb.style.display = 'none'; }
    api('/api/github').then(g => {
      const gh = (g && g.github) || {}, el = $('#tlGh');
      if (!el) return;
      const st = gh.stats || {}; let wireGh = false;
      if (!$('#tlGhUrl')) {
        el.innerHTML = '<b>Код с GitHub:</b> вставь ссылку (репозиторий, папка, файл, аккаунт или просто github.com; площадки GitLab/GitVerse/GitFlic/Codeberg/Gitee тоже) — облако читает файлы в фоне по любопытству, функции идут в мозг, а своя LLM учится на них писать код. ' +
          '<br><input type="text" id="tlGhUrl" placeholder="https://github.com/владелец/репозиторий" style="width:60%"> <button class="btn" id="tlGhGo">Читать</button>' +
          '<br>Площадка закрыта? Площадки: GitLab, Codeberg, GitVerse, GitFlic, Gitee, Bitbucket, SourceCraft — или скачай ZIP репозитория в браузере и загрузи: <input type="file" id="tlGhZip" accept=".zip"> <button class="btn ghost" id="tlGhZipB">Загрузить ZIP</button>' +
          '<div id="tlGhLinks" style="margin:6px 0"></div>' +
          '<div id="tlGhStat"></div>' +
          '<div id="tlGhStream" class="okbox" style="margin:8px 0"></div>' +
          'Токен GitHub (необязательно): <input type="password" id="tlGhTok" placeholder="ghp_…" style="width:30%"> <button class="btn ghost" id="tlGhTokB">Сохранить токен</button>' +
          '<br>Проба «Код»: <input type="text" id="tlCodeTask" placeholder="напиши функцию суммы списка" style="width:50%"> <button class="btn ghost" id="tlCodeGo">Написать</button><div id="tlCodeOut"></div>';
        wireGh = true;
      }
      $('#tlGhStat').innerHTML = 'Прочитано файлов: <b>' + (st.files || 0) + '</b>, функций: ' + (st.funcs || 0) + ', документов: ' + (st.docs || 0) +
        ', ошибок: ' + (st.errors || 0) + ' · репозиториев: ' + ((gh.repos || []).length) +
        ' · своя LLM кодит: <b>' + (gh.own_code ? 'да (знает токены кода)' : 'пока нет — нужно дообучение на прочитанном коде') + '</b>' +
        (gh.last_file ? '<br>Последний файл: ' + esc(gh.last_file) : '') +
        (gh.backoff_s ? '<br><span class="bad">Лимит GitHub API — пауза ещё ' + gh.backoff_s + ' с (токен поднимет лимит)</span>' : '') +
        (gh.last_error ? '<br><span class="mini">Последняя ошибка: ' + esc(gh.last_error) + '</span>' : '');
      // v3.176: ссылки на репозитории — списком, с ходом чтения; поле не пустеет
      const links = gh.links || [];
      $('#tlGhLinks').innerHTML = links.length ? '<b>Читаю репозитории:</b>' + links.map(l =>
        '<div class="mini" style="margin:3px 0">' + (l.err ? '⚠️ ' : (l.reading ? '⏳ ' : '✅ ')) +
        '<a href="' + esc(l.url) + '" target="_blank" rel="noopener">' + esc(l.url) + '</a> — ' +
        (l.err ? '<span class="bad">' + esc(l.err) + '</span>' +
          (l.retry_s ? ' <i>повторю сама через ' + Math.ceil(l.retry_s / 60) + ' мин</i>' : '') +
          (/архив не скачался/.test(l.err) ? '<br><span class="mini">Если сайт с этого компьютера не открывается: скачай ZIP репозитория в браузере и загрузи кнопкой «Загрузить ZIP» выше.</span>' : '') :
          (l.total ? 'прочитано ' + l.done + ' из ' + l.total + ' файлов' + (l.repos > 1 ? ', репозиториев: ' + l.repos : '') :
            esc(l.note || 'составляю план файлов…'))) +
        ' <button class="btn ghost sm" data-ghre="' + esc(l.url) + '" title="Прочитать заново (только изменившиеся файлы)">↻</button>' +
        ' <button class="btn ghost sm" data-ghdel="' + esc(l.url) + '" title="Убрать ссылку и её статьи">✕</button></div>').join('')
        : '<span class="mini">Ссылок на репозитории пока нет.</span>';
      $('#tlGhLinks').querySelectorAll('[data-ghre]').forEach(b => b.onclick = async () => {
        await api('/api/github', { body: { url: b.dataset.ghre } }); toast('Перечитываю', 'ok'); tabAiApiRefreshGh();
      });
      $('#tlGhLinks').querySelectorAll('[data-ghdel]').forEach(b => b.onclick = async () => {
        if (!confirm('Убрать ссылку и прочитанные из неё статьи?')) return;
        await api('/api/github', { body: { forget: b.dataset.ghdel } }); tabAiApiRefreshGh();
      });
      const inp = $('#tlGhUrl');
      if (inp && !inp.value && document.activeElement !== inp && links.length) inp.value = links[links.length - 1].url;
      // v3.177: ПОТОКИ своей LLM — код, чаты МАКС, разговор; доли — по воле мозга-12
      const cs = gh.stream || {}, ls = gh.streams || {};
      if (!$('#tlGhStream').contains(document.activeElement)) {
      const rowsS = (ls.streams || []).map(x =>
        '<tr><td><label class="switch-inline"><input type="checkbox" data-stream="' + esc(x.key) + '"' + (x.on ? ' checked' : '') + '> ' + esc(x.title) + '</label></td>' +
        '<td><b>' + x.share + '%</b></td>' +
        '<td title="воля зон на платах: 0,5…1,5; 1 — мозг не на связи">' + esc(x.zones.join(', ')) + ' ×' + x.brain + '</td>' +
        '<td>' + x.steps + '</td><td>' + (x.loss == null ? '—' : esc(String(x.loss))) + '</td>' +
        '<td>' + x.learned + ' / осталось ' + x.left + '</td>' +
        '<td>' + (x.off ? '<span class="bad">⏸ ' + esc(x.off) + '</span>' : (x.on && ls.on ? '▶' : '—')) + '</td></tr>').join('');
      $('#tlGhStream').innerHTML = '<label class="switch-inline"><input type="checkbox" id="tlCsOn"' + (ls.on !== false ? ' checked' : '') +
        '> <b>Потоки своей LLM</b> — модель учится несколькими потоками сразу</label>' +
        '<div class="mini">Сейчас: <b>' + esc(ls.mode || cs.mode || '—') + '</b>. Каждый поток в своей нити готовит материал, его шаги вплетаются между шагами ' +
        'обучения статьям базы. Доля потока — его база × воля зон мозга-12 на платах' + (ls.brain ? ' (мозг на связи)' : ' (мозг не на связи — доли нейтральные)') + '.</div>' +
        '<table class="mini" style="margin:6px 0;border-collapse:collapse"><tr style="text-align:left"><th>поток</th><th>доля</th><th>зоны мозга</th><th>шагов</th><th>loss</th><th>выучено</th><th></th></tr>' + rowsS + '</table>' +
        '<div class="mini">Код: курсов ' + (cs.runs || 0) + (cs.rollbacks ? ', откатов ' + cs.rollbacks : '') + (cs.running ? ' · идёт курс: ' + esc(cs.course_msg || '') : '') +
        (cs.last_msg ? ' · ' + esc(cs.last_msg) : '') + '</div>' +
        (cs.diverged ? '<div class="errbox" style="margin-top:6px">⚠️ ' + esc(cs.diverged) + '</div>' : '') +
        '<div class="mini" style="margin-top:6px">Шаг потока на каждый <input type="number" id="tlCsMix" min="1" max="100" style="width:56px" value="' + (ls.mix || cs.mix || 2) + '">-й шаг текста · ' +
        'курс кода, когда основное обучение отдыхает, — не чаще раза в <input type="number" id="tlCsEvery" min="1" max="1440" style="width:64px" value="' + (cs.every || 10) + '"> мин' +
        ' · <button class="btn ghost sm" id="tlCsRestore">Вернуть веса из резервной копии</button></div>';
      const csSave = async () => {
        const body = { llm_code_stream: $('#tlCsOn').checked,
          llm_code_mix: +$('#tlCsMix').value || 2, llm_code_every_min: +$('#tlCsEvery').value || 10 };
        $('#tlGhStream').querySelectorAll('[data-stream]').forEach(c => { body['llm_stream_' + c.dataset.stream] = c.checked; });
        await api('/api/github', { body });
        toast('Потоки своей LLM: сохранено', 'ok');
        tabAiApiRefreshGh();
      };
      $('#tlCsOn').onchange = csSave; $('#tlCsMix').onchange = csSave; $('#tlCsEvery').onchange = csSave;
      $('#tlGhStream').querySelectorAll('[data-stream]').forEach(c => c.onchange = csSave);
      $('#tlCsRestore').onclick = async () => {
        if (!confirm('Вернуть веса своей LLM из последней исправной резервной копии? Обучение после неё будет потеряно.')) return;
        toast('Останавливаю обучение и возвращаю веса…', 'ok');
        const rr = await api('/api/github', { body: { restore_weights: true } });
        const x = rr.restore || {};
        toast(x.ok ? 'Веса возвращены (loss кода был ' + x.loss + ')' : (x.error || 'не вышло'), x.ok ? 'ok' : 'err');
        tabAiApiRefreshGh();
      };
      }
      if (!wireGh) return;
      const ghAdd = async (quiet) => {
        const u = $('#tlGhUrl').value.trim();
        if (!u) { if (!quiet) toast('Вставь ссылку GitHub', 'err'); return; }
        if (quiet && (gh.links || []).some(l => l.url.replace(/^https?:\/\//, '') === u.replace(/^https?:\/\//, ''))) return;
        const rr = await api('/api/github', { body: { url: u } });
        toast(rr.add && rr.add.ok === false ? (rr.add.err || 'не принято') : 'Ссылка сохранена — читаю в фоне', rr.add && rr.add.ok === false ? 'err' : 'ok');
        tabAiApiRefreshGh();
      };
      $('#tlGhGo').onclick = () => ghAdd(false);
      // v3.176: ссылку вставили и ушли из поля (или из настроек) — сохраняем сами
      $('#tlGhUrl').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); ghAdd(false); } };
      $('#tlGhUrl').onchange = () => { if (/^(https?:\/\/)?[\w.-]+\.[a-z]{2,}\/?/i.test($('#tlGhUrl').value.trim())) ghAdd(true); };
      $('#tlGhZipB').onclick = async () => {
        const f = $('#tlGhZip').files[0];
        if (!f) { toast('Выбери zip-файл', 'err'); return; }
        const fd = new FormData(); fd.append('file', f, f.name);
        const rr = await api('/api/github/zip', { body: fd });
        toast(rr.ok ? 'Принято: файлов ' + rr.files + ' — читаю по одному' : (rr.err || 'не принято'), rr.ok ? 'ok' : 'err');
      };
      $('#tlGhTokB').onclick = async () => {
        await api('/api/github', { body: { gh_token: $('#tlGhTok').value.trim() } });
        toast('Токен сохранён', 'ok');
      };
      $('#tlCodeGo').onclick = async () => {
        const t = $('#tlCodeTask').value.trim(), out = $('#tlCodeOut');
        if (!t) return;
        out.textContent = 'Пишу…';
        const rr = await api('/api/code/try', { body: { task: t } });
        out.innerHTML = '<pre style="white-space:pre-wrap">' + esc(rr.reply || rr.error || '—') + '</pre>';
        const cm = /```(?:python|py)?\s*\n([\s\S]*?)```/.exec(rr.reply || '');
        if (cm) {                                   // v3.178: проверка кода на плате
          const b = document.createElement('button'); b.className = 'btn'; b.textContent = '🧪 Запустить на плате';
          const o = document.createElement('pre'); o.style.whiteSpace = 'pre-wrap';
          b.onclick = async () => {
            o.textContent = '⏳ отправляю плате (связь раз в 5 с)…';
            const x = await api('/api/code/board', { body: { code: cm[1], mode: 'py_run', timeout: 20 } });
            o.textContent = x.error ? '✗ ' + x.error : ((x.ok ? '✓' : '✗') + ' плата ' + (x.board || '') + ' · Python ' + (x.py || '?') + ' · ' + (x.machine || '') +
              ' · ' + (x.ms || 0) + ' мс · код выхода ' + x.rc + '\n' + (x.out || ''));
          };
          out.appendChild(b); out.appendChild(o);
        }
      };
    }).catch(() => {});
    api('/api/hearing').then(h => {
      const hh = (h && h.hearing) || {}, el = $('#tlHear');
      if (!el) return;
      const ok = hh.asr_ready; let wireHear = false;
      if (!$('#tlHearFile')) {
        el.innerHTML = '<div id="tlHearStat"></div>Послушать файл (mp3, wav, ogg, mp4…): <input type="file" id="tlHearFile" accept="audio/*,video/*,.ogg,.opus,.m4a,.mp3,.wav,.mp4,.webm"> <button class="btn" id="tlHearGo">Послушать</button><div id="tlHearOut" class="mini"></div>';
        wireHear = true;
      }
      $('#tlHearStat').innerHTML = '<b>Слух (звук видео):</b> ffmpeg ' + (hh.ffmpeg ? '✓' : '✗ (Windows: <code>winget install ffmpeg</code>)') +
        ' · распознавание речи Vosk ' + (hh.vosk ? '✓' : '✗') + ' · русская модель ' + (hh.model ? '✓' : '✗') +
        (ok ? ' — мозг слышит и понимает речь в видео.' : ' — без этого мозг слышит только свойства звука (речь/музыка/тишина), слов нет.') +
        ((hh.install && hh.install.state === 'running') ? ' <i>Ставлю… ' + esc(hh.install.log || '') + '</i>' : '') +
        ((hh.install && hh.install.state === 'error') ? ' <span class="bad">Не вышло: ' + esc((hh.install.log || '').slice(-200)) + '</span>' : '') +
        ((!hh.vosk || !hh.model) ? '<br><button class="btn ghost" id="tlHearBtn">Поставить распознавание речи (Vosk, ~45 МБ)</button>' : '');
      const hg = wireHear ? $('#tlHearGo') : null;
      if (hg) hg.onclick = async () => {
        const f = $('#tlHearFile').files[0], out = $('#tlHearOut');
        if (!f) { toast('Выбери звуковой или видео файл', 'err'); return; }
        out.textContent = 'Слушаю «' + f.name + '»…';
        const fd = new FormData(); fd.append('file', f, f.name);
        try {
          const rr = await api('/api/hear', { body: fd });
          out.innerHTML = rr.ok ? '<div class="okbox">' + esc(rr.reply || '').replace(/\n/g, '<br>') + '</div>'
            : '<div class="errbox">' + esc(rr.error || rr.reply || 'не вышло') + '</div>';
        } catch (e) { out.innerHTML = '<div class="errbox">' + esc(e.message) + '</div>'; }
      };
      const b2 = $('#tlHearBtn');
      if (b2) b2.onclick = async () => { await api('/api/hearing', { body: { install: true } });
        toast('Ставлю Vosk и русскую модель — несколько минут', 'ok'); };
    }).catch(() => {});
    const sy = (r && r.sys) || {};
    $('#tlSys').innerHTML = sy.cores ? ('<b>Компьютер:</b> ядер ' + sy.cores + ' · ОЗУ свободно ' + fmtN(sy.mem_free_mb) +
      ' из ' + fmtN(sy.mem_total_mb) + ' МБ · облако занимает ' + sy.rss_mb + ' МБ, ЦП облака ' + sy.cpu_pct +
      '% ядра · скорость вычислений ~' + sy.gflops + ' ГФЛОПС') : '';
    if (!(r && r.config)) {
      $('#tlBox').innerHTML = '<span class="bad">Сервер не вернул настройки LLM' +
        (r && r.config_error ? ': ' + esc(r.config_error) : '') + '</span>';
      return;
    }
    $('#tlBox').innerHTML = '<b>Сейчас:</b> ' + esc(t.backend || '') + ' · d_model ' + t.d_model +
      ', слоёв ' + t.layers + ', голов ' + t.heads + ', контекст ' + t.ctx + ', словарь ' + fmtN(t.vocab) +
      ' · параметров <b>' + fmtN(t.params) + '</b> · память ~' + t.mem_mb + ' МБ' +
      ' · операций на токен ' + fmtN(t.flops_per_token) + '<br><b>Обучение:</b> ' +
      (t.training ? 'идёт' : (t.ready ? 'готова' : 'не обучена')) +
      (t.inc_active
        ? ' — <b>дообучение на новых статьях</b>: шаг ' + fmtN(t.inc_done) + ' из ' + fmtN(t.inc_total) +
          ' (' + Math.min(100, Math.round(100 * t.inc_done / Math.max(1, t.inc_total))) + '%), новых документов ' +
          fmtN(t.inc_new_docs) + '; всего шагов за всё время ' + fmtN(t.steps) +
          ' (основной план обучения выполнен — проценты выше 100 означают именно дообучение)'
        : ' — шаг ' + fmtN(t.steps) + ' из ' + fmtN(t.total_steps) + ' (' + Math.min(100, t.pct || 0) + '%)') +
      (t.loss != null ? ', потеря ' + t.loss : '') +
      (t.tok_per_s ? ', скорость ' + fmtN(t.tok_per_s) + ' токенов/с' : '') +
      (t.step_s != null ? ', шаг ' + t.step_s + ' с' + (t.step_target ? ' (цель ' + t.step_target + ' с)' : '') +
        (t.step_batch ? ', примеров в шаге ' + t.step_batch + (t.step_ctx ? ' × ' + t.step_ctx + ' токенов' : '') : '') : '') +
      (t.step_note ? '<br><span class="mini">⏱ ' + esc(t.step_note) + '</span>' : '') +
      (t.plan_note ? '<br><span class="mini">📋 ' + esc(t.plan_note) + '</span>' : '') +
      (t.tok_ver >= 2 ? '<br><span class="mini">🧩 Речь словами (v3.117): словарь кусков BPE, слов для речи ' + fmtN(t.speech_words || 0) +
        (t.net_share != null ? ', доля сети в речи ' + Math.round(100 * t.net_share) + '% (остальное — статистика троек/четвёрок из базы, уходит по мере обучения)' : '') +
        (t.net_nll != null ? ', замер сети ' + t.net_nll + ' нат/кусок' : '') + '</span>' : '') +
      ((t.board_led && t.board_led.on) ? '<br><span class="mini">🔗 Порядок обучения ведёт плата (её мысли и частота генератора); первыми: ' +
        esc((t.board_led.top || []).join(' · ')) + '</span>' : '') +
      (t.board_steps ? '<br><span class="mini">🔗 Шагов с мыслями плат: ' + fmtN(t.board_steps) + '</span>' : '') +
      (t.training && t.eta_s ? ', осталось ~' + fmtEta(t.eta_s) : '') +
      ' · доля ЦП ' + t.cpu_share + ' · корпус: статей ' + fmtN(t.kb_articles) + ', токенов ' +
      fmtN(t.corpus_tokens) + (t.error ? '<br><span class="bad">' + esc(t.error) + '</span>' : '') +
      (t.deleted ? '<br><span class="bad">Нейросеть удалена — сама обучаться не начнёт. Нажми «Переобучить на текущей базе», чтобы обучить новую.</span>' : '') +
      (t.note ? '<br><b>Нагрузка при сохранённых размерах:</b> ' + esc(t.note) : '');
    const ll = $('#llmLoad'); if (ll) ll.textContent = 'Своя LLM: ' + fmtN(t.params) +
      ' параметров, ~' + t.mem_mb + ' МБ' + (t.training ? ', обучается (' + (t.pct || 0) + '%)' : '');
    const last = lk.last || {};
    $('#tlThink').innerHTML = (last.text ? ('<b>Последняя мысль LLM:</b> «' + esc(last.text) + '» · качество ' +
      last.quality + ' · ' + (last.ok ? 'отправлена: ' + esc((last.via || []).join(', ')) :
      '<span class="bad">не отправлена: ' + esc(last.reason || '') + '</span>') + '<br>') : '') +
      'Отправлено мыслей: ' + (lk.published || 0) + ', отклонено по качеству/повтору: ' + (lk.rejected || 0) +
      (lk.every ? ' · следующая сама через ~' + (lk.next_in || 0) + ' с' : ' · сама не думает') +
      ' · примеров из мозгов для обучения: ' + (lk.corpus_extra || 0) +
      (lk.err ? ' · <span class="mini">' + esc(lk.err) + '</span>' : '') +
      (function () {
        const on = (r && r.onenet) || null, gm = (r && r.genmod) || null;
        let h = '';
        if (gm) h += '<br><b>Генератор скетча → веса LLM:</b> ' + (gm.fresh_devices ?
          ('сила ' + gm.strength + ', z ' + gm.z + ', шагов с шумом ' + gm.noise_steps +
           (gm.last && gm.last.lr_factor ? ', множитель lr ' + gm.last.lr_factor : '') +
           (gm.meaning ? (' · смысл: пар «сигнал↔текст» ' + gm.meaning.pairs + ', уровень сейчас ' +
             (gm.meaning.band === null ? '—' : gm.meaning.band + '/4') + ', слов с подсказкой ' + gm.meaning.bias_words +
             (gm.meaning.frames ? ', пар от дыхания кадра D4: ' + gm.meaning.frames : '') + (gm.meaning.finetune ? ', дообучение: ' + esc(gm.meaning.finetune) : '') +
             (gm.meaning.ranges ? '<br>Уровни (Гц со знаком, слов в таблице): ' + gm.meaning.ranges.map(function (r, i) {
               return esc(gm.meaning.names[i]) + ' ' + r[0] + '…' + r[1] + ' — ' + (gm.meaning.band_words[i] || 0);
             }).join('; ') : '')) : '')) :
          'нет свежих данных генератора от платы (нужна neuro_12 v2.43) — влияния нет');
        if (gm && gm.prior && gm.prior.strength > 0) h += '<br><b>Опора сетей на частоту:</b> уровень сигнала ' +
          (gm.prior.z === null ? 'нет свежих данных плат (опора не действует)' : gm.prior.z) +
          ' · оценено фраз ' + gm.prior.used + ', без данных ' + gm.prior.no_data;
        const nf = (r && r.negfreq) || null;
        if (nf && nf.on) h += '<br><b>Знак частоты на D6:</b> решала LLM ' + nf.by_llm + ' раз, по Δf ' + nf.by_delta +
          ' (LLM занята/не обучена/сомневалась: ' + (nf.busy + nf.cold) + ') · пар «знак↔текст» ' + nf.pairs +
          ' (−' + nf.neg + '/+' + nf.pos + ', решает при ≥20 и ≥5 с каждой стороны)' +
          (nf.ft ? ', дообучение: ' + esc(nf.ft) : '');
        if (on && on.docs) {
          const parts = Object.keys(on.origins || {}).map(function (k) { return esc(k) + ' ' + on.origins[k]; });
          h += '<br><b>Единая сеть:</b> материалов ' + on.docs + ', узлов ' + fmtN(on.nodes) + ', связей ' + fmtN(on.links) +
            ' · по происхождению: ' + parts.join(', ') + ' · принято из памяти плат: ' + (on.from_boards || 0);
        }
        return h;
      })();
  };
  const probe = async () => {
    const g = (id, d) => (+$('#' + id).value || d);
    const q = '?probe_d=' + g('tlD', 128) + '&probe_layers=' + g('tlL', 2) + '&probe_heads=' + g('tlH', 4) +
      '&probe_ctx=' + g('tlC', 64) + '&probe_vocab=' + g('tlV', 4000) + '&probe_batch=' + g('tlB', 16) +
      '&probe_epochs=' + g('tlE', 3) + '&probe_corpus=' + g('tlCT', 120000) + '&probe_cpu=' + g('tlCpu', 0.5);
    const r = await api('/api/tinyllm' + q);
    const p = (r && r.probe) || null; tlLastProbe = p;
    const col = !p ? '' : p.verdict === 'ok' ? '#3aa76d' : p.verdict === 'no' ? '#d9534f' : '#d19a1e';
    $('#tlProbe').innerHTML = p ? ('<b>Для этих размеров:</b> параметров ' + fmtN(p.params) + ', виртуальный размер (веса+Adam+градиенты) ~' + (p.virtual_mb || p.mem_mb) + ' МБ, в ОЗУ ~' + p.mem_mb + (p.disk_state ? ', на диске ~' + p.disk_mb : '') + ', файл подкачки: ' + (p.swap_total_mb ? (p.swap_free_mb + ' из ' + p.swap_total_mb + ' МБ свободно') : 'нет/не определён') + ' МБ; веса+Adam ~' + p.mem_mb +
      ' МБ + активации ~' + p.act_mb + ' МБ = <b>~' + p.need_mb + ' МБ</b>. <span style="color:' + col + '">' +
      esc(p.note) + '</span>') : '<span class="bad">Сервер не вернул оценку нагрузки' +
      (r && r.probe_error ? ': ' + esc(r.probe_error) : '') + '</span>';
    return r;
  };
  const tlSave = async (body, msg) => {          // сохранить сразу, без сброса формы
    tlBusy++;
    let r = null;
    try { r = await api('/api/tinyllm', { body }); } finally { tlBusy--; }
    if (r) drawTl(r);
    if (msg) toast(msg, 'ok');
    return r;
  };
  drawTl(await probe());
  Object.keys(TL_NUM).forEach(id => { const e = $('#' + id); if (!e) return;
    e.addEventListener('input', () => { tlDirty.add(id); tlMark(); });
    e.addEventListener('change', probe); });
  Object.keys(TL_SW).forEach(id => { const e = $('#' + id); if (!e) return;
    e.addEventListener('change', async () => {
      const on = e.checked;
      if (id === 'tlForce' && on && tlLastProbe && tlLastProbe.verdict && tlLastProbe.verdict !== 'ok') {
        if (!confirm('Памяти может не хватить: ' + tlLastProbe.note + '\n\nВсё равно обучать?')) { e.checked = false; return; }
      }
      const b = {}; b[TL_SW[id]] = on;
      await tlSave(b, 'Сохранено'); }); });
  Object.keys(TL_AUTO).forEach(id => { const e = $('#' + id); if (!e) return;
    e.addEventListener('change', async () => { const b = {}; b[TL_AUTO[id]] = +e.value || 0;
      await tlSave(b, 'Сохранено'); }); });
  $('#tlApply').onclick = async () => {
    if (!confirm('Применить размеры и обучить модель заново? Прежняя модель будет заменена.')) return;
    const b = {};
    Object.keys(TL_NUM).forEach(id => { b[TL_NUM[id]] = +$('#' + id).value; });
    b.llm_force = $('#tlForce').checked;
    const r = await tlSave(b);
    if (r) { tlDirty.clear(); tlMark(); drawTl(r); }
    toast(r && r.llm && r.llm.error ? 'Размеры сохранены. ' + r.llm.error : 'Размеры сохранены, обучение запущено',
      r && r.llm && r.llm.error ? 'err' : 'ok');
  };
  $('#tlDisk').addEventListener('change', async (ev) => {
    await tlSave({ llm_disk_state: ev.target.checked }, 'Сохранено — применится при следующем обучении'); });
  $('#tlSwap').addEventListener('change', async (ev) => { await tlSave({ llm_use_swap: ev.target.checked }, 'Сохранено'); });
  $('#tlDiskMb').addEventListener('change', async (ev) => { await tlSave({ llm_disk_mb: +ev.target.value || 0 }, 'Сохранено'); });
  $('#tlAnsN').addEventListener('change', async (ev) => { await tlSave({ llm_answer_sents: +ev.target.value || 10 }, 'Сохранено'); });
  $('#tlWordOnly').addEventListener('change', async (ev) => {
    await tlSave({ llm_wordonly: ev.target.checked }, 'Сохранено — переобучи модель, чтобы применить'); });
  $('#tlBrainBtn').onclick = async () => { await api('/api/brain/train', { body: { start: true } });
    toast('Мозг начал учиться на базе знаний', 'ok'); };
  $('#tlRec').onclick = () => {
    const rc = (tlLastR && tlLastR.recommend) || null;
    if (!rc) { toast('Нет данных о памяти компьютера', 'err'); return; }
    Object.keys(TL_NUM).forEach(id => { const k = TL_NUM[id];
      if (rc[k] != null && ['llm_d', 'llm_layers', 'llm_heads', 'llm_ctx', 'llm_batch'].includes(k)) {
        $('#' + id).value = rc[k]; tlDirty.add(id); } });
    tlMark(); probe();
    toast('Подставлено под ~' + rc.free_mb + ' МБ свободной памяти — проверь и нажми «Применить»', 'ok');
  };
  $('#tlRetrain').onclick = async () => { await tlSave({ retrain: true }, 'Переобучение запущено: старые веса удалены, обучение с нуля'); };
  $('#tlDelete').onclick = async () => {
    if (!confirm('Удалить свою LLM полностью (веса, состояние Adam, план обучения)? Память и база мозга НЕ трогаются.')) return;
    const again = confirm('Сразу начать обучение новой нейросети с нуля?\nОК — начать, Отмена — оставить без LLM (ответит мозг).');
    const r = await tlSave({ delete: true, restart: again });
    if (r && r.ok) toast(again ? 'Нейросеть удалена, обучение новой начато' : 'Нейросеть удалена', 'ok');
    else toast('Не удалось удалить: ' + ((r && r.error) || 'нет ответа'), 'err');
  };
  $('#tlThinkBtn').onclick = async () => {
    $('#tlThink').textContent = 'Думаю…';
    const r = await api('/api/llm/think', { body: {} });
    $('#tlThink').innerHTML = r && r.text ? ('<b>Тема:</b> ' + esc(r.topic || '—') + '<br>«' + esc(r.text) + '»<br>качество ' +
      r.quality + ' · ' + (r.ok ? '<span style="color:#3aa76d">отправлена: ' + esc((r.via || []).join(', ')) + '</span>' :
      '<span class="bad">не отправлена: ' + esc(r.reason || '') + '</span>')) : '<span class="bad">' + esc((r && r.reason) || 'нет ответа') + '</span>';
    setTimeout(async () => { try { drawTl(await api('/api/tinyllm')); } catch (e) {} }, 800);
  };
  if (window.__tlTimer) clearInterval(window.__tlTimer);
  window.__tlTimer = setInterval(async () => { if (!$('#tlBox')) { clearInterval(window.__tlTimer); return; }
    if (tlBusy) return;
    try { drawTl(await api('/api/tinyllm')); } catch (e) {} }, 5000);
  $('#tplSave').onclick = async () => {
    SETTINGS = await api('/api/settings', { body: {
      tpl_bot: $('#tplBot').checked, tpl_chat: $('#tplChat').checked,
      tpl_greet: $('#tplGreet').value.trim() || '{приветствие}, {имя}!',
      tpl_parts: Math.max(1, Math.min(5, parseInt($('#tplParts').value, 10) || 2)),
      tpl_greet_gap_h: Math.max(0, Math.min(72, parseFloat($('#tplHours').value) || 0)) } });
    SETTINGS = await api('/api/settings');
    toast('Шаблон ответа сохранён', 'ok');
  };
  // v3.113: галочки сохраняются сразу по щелчку (раньше — только кнопкой
  // «Сохранить», и снятая галочка после обновления страницы «возвращалась»)
  ['tplBot', 'tplChat'].forEach(id => { const e = $('#' + id);
    if (e) e.addEventListener('change', () => $('#tplSave').click()); });
  ['llmOn', 'visOn', 'vidOn', 'wkbOn'].forEach(id => { const e = $('#' + id);
    if (e) e.addEventListener('change', () => $('#llmSave').click()); });
  const drawKeys = (r) => {
    $('#aiOn').checked = !!r.ai_api_on;
    $('#aiRate').value = r.ai_rate_per_min || 60;
    const ks = r.keys || [];
    $('#aiKeys').innerHTML = !ks.length ? '<span class="mini">Ключей пока нет.</span>' :
      '<table class="tbl"><tr><th>Приложение</th><th>Ключ</th><th>Запросов</th>' +
      '<th>Последний раз</th><th></th></tr>' + ks.map(k => '<tr><td><b>' + esc(k.name) +
        '</b>' + (k.on === false ? ' <span class="bad">выключен</span>' : '') + '</td><td><code>' +
        esc(k.prefix) + '</code></td><td>' + (k.calls || 0) + '</td><td><span class="mini">' +
        (k.last_used ? new Date(k.last_used * 1000).toLocaleString('ru') : '—') +
        '</span></td><td><button class="btn ghost" data-kon="' + esc(k.id) + '" data-v="' +
        (k.on === false ? 'on' : 'off') + '">' + (k.on === false ? 'включить' : 'выключить') +
        '</button> <button class="btn danger" data-krm="' + esc(k.id) + '">отозвать</button></td></tr>'
      ).join('') + '</table>';
    $$('#aiKeys [data-kon]').forEach(b => b.onclick = async () =>
      drawKeys(await api('/api/ai/keys', { body: { action: b.dataset.v, id: b.dataset.kon } })));
    $$('#aiKeys [data-krm]').forEach(b => b.onclick = async () => {
      if (!confirm('Отозвать ключ? Приложение с ним перестанет работать.')) return;
      drawKeys(await api('/api/ai/keys', { body: { action: 'revoke', id: b.dataset.krm } }));
    });
  };
  drawKeys(await api('/api/ai/keys'));
  $('#aiOn').onchange = async (e) =>
    drawKeys(await api('/api/ai/keys', { body: { ai_api_on: e.target.checked } }));
  $('#aiRate').onchange = async (e) =>
    drawKeys(await api('/api/ai/keys', { body: { ai_rate_per_min: +e.target.value || 60 } }));
  $('#aiCreate').onclick = async () => {
    const r = await api('/api/ai/keys', { body: { action: 'create',
      name: $('#aiName').value.trim() } });
    if (r.key) {
      $('#aiNew').innerHTML = '<div class="okbox">Ключ для «' + esc(r.created.name) +
        '» — скопируй сейчас, больше он не покажется:<div class="ai-key">' + esc(r.key) +
        '</div><button class="btn ghost" id="aiCopy">⧉ Копировать</button></div>';
      $('#aiCopy').onclick = () => navigator.clipboard.writeText(r.key)
        .then(() => toast('Ключ скопирован', 'ok'));
    }
    $('#aiName').value = '';
    drawKeys(r);
  };
  try {
    const _qr = await Promise.race([api('/api/qa/stats'),
      new Promise((_r, rej) => setTimeout(() => rej(new Error('timeout')), 20000))]);
    const q = _qr.qa;
    const c = q.counters || {};
    $('#qaBox').innerHTML = '<b>Вопросов выучено:</b> ' + fmtN(q.questions) +
      ' · <b>пар вопрос/ответ:</b> ' + fmtN(q.pairs) + ' (подтверждено ' + fmtN(q.verified) +
      ') · исправлений: ' + fmtN(c.corrected || 0) + ' · отклонено 👎: ' + fmtN(c.rejected || 0) +
      ' · повторено из памяти: ' + fmtN(c.recalled || 0) +
      ' · не взято в знание (догадки сети): ' + fmtN(c.skipped || 0) +
      (q.suggest ? ' · <span class="warnbox">предложений из МАКС/API ждут проверки: ' + q.suggest + '</span>' : '') +
      (q.top && q.top.length ? '<br><b>Чаще всего спрашивают:</b> ' +
        q.top.map(x => esc(x.q.slice(0, 50)) + ' (' + x.n + ')').join('; ') : '') +
      (q.unanswered && q.unanswered.length ? '<br><b>Без ответа (дочитает любопытство):</b> ' +
        q.unanswered.map(x => esc(x.q.slice(0, 50))).join('; ') : '') +
      (q.rep_top && q.rep_top.length ? '<br><b>Опыт по источникам (👍−👎):</b> ' +
        q.rep_top.map(x => esc(x[0]) + ' ' + (x[1] > 0 ? '+' : '') + x[1]).join(', ') : '');
    const sg = q.suggest_list || [];
    if (sg.length) {
      $('#qaBox').insertAdjacentHTML('beforeend', '<div style="margin-top:8px"><b>Исправления ' +
        'от собеседников (МАКС/API) — станут знанием только после твоей проверки:</b>' +
        '<ul style="margin:4px 0 0 18px">' + sg.map(x => '<li><b>' + esc(x.q.slice(0, 80)) +
          '</b> → ' + esc(x.a.slice(0, 160)) + ' <span class="mini">(' + esc(x.by || '?') +
          ')</span> <button class="btn ghost" data-sg="' + esc(x.k) + '" data-ok="1">принять</button>' +
          ' <button class="btn danger" data-sg="' + esc(x.k) + '" data-ok="0">отклонить</button></li>')
          .join('') + '</ul></div>');
      $$('#qaBox [data-sg]').forEach(b => b.onclick = async () => {
        const rr = await api('/api/qa/suggest', { body: { k: b.dataset.sg, ok: b.dataset.ok === '1' } });
        toast(rr.ok ? (b.dataset.ok === '1' ? 'Принято — теперь это знание' : 'Отклонено') :
          ('Ошибка: ' + (rr.error || '?')), rr.ok ? 'ok' : 'err');
        tabAiApi();
      });
    }
  } catch (e) { $('#qaBox').textContent = (e && e.message === 'timeout') ? 'Сервер занят (идёт обучение?) и не ответил за 20 с — обнови страницу позже. Медленные запросы смотри в журнале («МЕДЛЕННО»).' : 'Сводка обучения недоступна.'; }
}

/* ------------------------------- Общие ----------------------------------- */
/* v3.57: «Общие» — группы-аккордеоны, длинные пояснения под «подробнее» */
function groupCommon(p) {
  const GR = [
    ['💬 Чат и ответы', true, ['setMode', 'setHonest', 'setLlmFree', 'setOffline', 'setImagine']],
    ['🕸 Сеть материалов — сколько помнит мозг', true, ['setNetBox', 'setNetNodes', 'setNetDocs', 'setNetSents']],
    ['🧠 Связь с мозгом neuro-12', false, ['setBrainStrict', 'setBrainBox', 'setBrainLlm']],
    ['🌐 Интернет и знания', false, ['setWeb', 'setWebSrc', 'setSrcBox', 'setLlmWebBox', 'setReadFirst', 'setPassage', 'setEngBox', 'setWebPages']],
    ['📡 Сигналы платы: D4 / D6, кадр камеры', false,
      ['setD6Neg', 'setD4Neg', 'setD6Cam', 'setD6CamMode', 'setD4Cam']],
    ['🤖 Боты', false, ['setBotStatus']],
    ['🖥 Компьютер облака', false, ['setTerminal']],
    ['🔐 Доступ и чаты', false, ['setChatsIp', 'setToken']]];
  const save = $('#saveCommon');
  GR.forEach(g => {
    const d = document.createElement('details');
    d.className = 'sgrp'; if (g[1]) d.open = true;
    d.innerHTML = '<summary>' + g[0] + '<small></small></summary><div class="sbody"></div>';
    const body = d.querySelector('.sbody');
    g[2].forEach(id => {
      const el = p.querySelector('#' + id);
      const fg = el && el.closest('.fgroup');
      if (fg) body.appendChild(fg);
    });
    d.querySelector('small').textContent = body.children.length + ' парам.';
    p.insertBefore(d, save);
  });
  p.querySelectorAll('.sgrp .fgroup > label').forEach(lb => {
    const t = lb.textContent;
    if (t.length < 170 || lb.children.length) return;
    let i = t.search(/[.:] /);
    if (i < 40 || i > 140) i = t.lastIndexOf(' ', 110);
    const head = t.slice(0, i + 1).replace(/[:]$/, ''), rest = t.slice(i + 1).trim();
    lb.textContent = head;
    const m = document.createElement('details'); m.className = 'more';
    m.innerHTML = '<summary>подробнее</summary><div class="mini"></div>';
    m.querySelector('.mini').textContent = rest;
    lb.closest('.fgroup').appendChild(m);
  });
}

/* v3.177.2: КВАНТЫ НА D6 — галочки для скетча платы и шум плат */
async function partWire() {
  const box = $('#partForm');
  if (!box) return;
  let r;
  try { r = await api('/api/particles'); } catch (e) { box.textContent = 'нет связи с облаком'; return; }
  const s = r.settings || {};
  const ck = (id, k, t) => '<label class="switch-inline" style="margin-right:14px"><input type="checkbox" id="' + id + '"' + (s[k] ? ' checked' : '') + '> ' + t + '</label>';
  box.innerHTML =
    '<div style="margin:4px 0">' + ck('partGrav', 'part_grav', '«гравитоны» — низкий фон') + ck('partMuon', 'part_muon', '«мюоны» — пакеты') +
    ck('partPhot', 'part_phot', '«фотоны» — вспышки') + '</div>' +
    '<div style="margin:4px 0">Частота генератора: <input type="number" id="partGen" min="0" max="20000" style="width:84px" value="' + (s.part_gen_hz || 0) + '"> Гц (0 — сам) · ' +
    ck('partCpu', 'part_cpu_link', 'привязать к частоте процессора') + '</div>' +
    '<div style="margin:4px 0">Полоса: от <input type="number" id="partMin" step="1" style="width:76px" value="' + (s.part_min_mhz ?? 100) + '"> МГц до ' +
    '<input type="number" id="partMax" step="0.1" style="width:64px" value="' + (s.part_max_ghz ?? 4) + '"> ГГц</div>' +
    '<div style="margin:4px 0">Скетчу уходит: <code>' + esc(r.cfg || '') + '</code></div>' +
    ((r.boards || []).length ? (r.boards || []).map(b => '<div>📟 <b>' + esc(b.board) + '</b>: шум A4 <b>' + (b.cap ?? '—') + '</b>/255 · активность D6 ' + (b.d6 ?? '—') +
      ' · шаг весов ×' + (b.scale ?? 1) + (b.cfg ? '' : ' · <i>конфиг ещё не дошёл</i>') + ' <span class="muted">(' + b.age + ' с назад)</span></div>').join('')
      : '<div class="muted">Платы v2.92 пока не прислали шум (они шлют его каждые 5 с в канале связи).</div>');
  const save = async () => {
    await api('/api/particles', { body: { part_grav: $('#partGrav').checked, part_muon: $('#partMuon').checked,
      part_phot: $('#partPhot').checked, part_cpu_link: $('#partCpu').checked, part_gen_hz: +$('#partGen').value || 0,
      part_min_mhz: +$('#partMin').value || 0, part_max_ghz: +$('#partMax').value || 0 } });
    toast('Кванты: сохранено — платы получат за 5 с', 'ok');
    partWire();
  };
  box.querySelectorAll('input').forEach(i => i.onchange = save);
}

/* v3.178: 🎤 Микрофон и 📷 камера плат — проверка ушей и глаз из облака */
let MIC_T = null, MIC_PLAY = null;
async function micWire() {
  const box = $('#micForm');
  if (!box) { clearTimeout(MIC_T); MIC_T = null; clearInterval(MIC_PLAY); MIC_PLAY = null; return; }
  let r;
  try { r = await api('/api/hearing/live'); } catch (e) { box.textContent = 'нет связи с облаком'; MIC_T = setTimeout(micWire, 4000); return; }
  const bs = r.boards || [];
  const bar = (v, max) => { const w = Math.max(0, Math.min(100, Math.round(100 * (+v || 0) / max)));
    return '<span style="display:inline-block;width:120px;height:8px;background:var(--line,#333);border-radius:4px;vertical-align:middle">' +
      '<span style="display:block;width:' + w + '%;height:100%;background:' + (w > 70 ? '#e55' : w > 25 ? '#4c8' : '#888') + ';border-radius:4px"></span></span>'; };
  let h = '';
  if (!bs.length) h += '<div class="muted">Платы ещё не прислали состояние микрофона и камеры (нужна плата v2.93+, связь мозгов каждые ~5 с).</div>';
  bs.forEach(b => {
    const m = b.mic, c = b.cam, lv = b.live;
    h += '<div style="border-top:1px solid var(--line,#333);padding:6px 0"><b>📟 ' + esc(b.board) + '</b>';
    if (m) {
      h += '<div>🎤 ' + esc(m.state || '?') + (m.on === false ? ' (выключен в настройках платы)' : '') + ' · уровень ' + bar(m.rms, 3000) + ' ' + (m.rms ?? '—') +
        ' (фон ' + (m.floor ?? '—') + ') · фраз ' + (m.phrases || 0) + ', отправлено ' + (m.sent || 0) + ', понято ' + (m.heard || 0) +
        ' · устройство <code>' + esc(m.dev || 'default') + '</code> <span class="muted">(' + m.age + ' с назад)</span></div>';
      if (m.err) h += '<div style="color:#e88">⚠ ' + esc(m.err) + '</div>';
      if (m.last) h += '<div class="muted">последнее: «' + esc(m.last) + '»</div>';
      if (m.live_err) h += '<div style="color:#e88">живой звук: ' + esc(m.live_err) + '</div>';
    } else h += '<div class="muted">🎤 плата не прислала состояние микрофона (обнови плату до v2.93)</div>';
    if (lv) h += '<div>🔊 живой звук: ' + lv.sec + ' с в буфере, ' + lv.kb + ' КБ, кусков ' + lv.chunks + ', пик ' + bar(lv.peak, 32767) +
      ' <span class="muted">(' + (lv.age ?? '—') + ' с назад)</span></div>';
    h += '<div style="margin:4px 0">' + (b.want ? '<button class="btn" data-mic-stop="' + esc(b.board) + '">⏹ Стоп</button> <span class="muted">слушаю… звук играет кусками по 3 с</span>'
      : '<button class="btn" data-mic-go="' + esc(b.board) + '">🎧 Слушать 60 с</button>') +
      ' <button class="btn" data-mic-clip="' + esc(b.board) + '">▶ Последние 10 с</button>' +
      ' <button class="btn" data-mic-phrase="' + esc(b.board) + '">▶ Последняя фраза</button></div>';
    if (c) {
      h += '<div>📷 ' + esc(c.state || '?') + (c.on ? '' : ' (камера выключена: cam_on)') + ' · адрес <code>' + esc(c.url || '—') + '</code>' +
        (c.ok_age != null ? ' · последний кадр ' + c.ok_age + ' с назад' : '') + (c.sent_age != null ? ' · в облако ' + c.sent_age + ' с назад' : '') + '</div>';
      if (c.err) h += '<div style="color:#e88">⚠ ' + esc(c.err) + '</div>';
      if (c.up_err) h += '<div style="color:#e88">⚠ отправка: ' + esc(c.up_err) + '</div>';
      const u = c.uart;
      if (u) h += '<div>UART: байт <b>' + (u.bytes ?? '?') + '</b> · целых кадров <b>' + (u.ok ?? '?') + '</b> · битых ' + (u.bad ?? '?') +
        ' · оборванных ' + (u.lost ?? '?') + ' · ' + (u.baud ?? '?') + ' бод' + (u.why ? '<div style="color:#e88">→ ' + esc(u.why) + '</div>' : '') + '</div>';
    } else h += '<div class="muted">📷 плата не прислала состояние камеры</div>';
    h += '</div>';
  });
  h += '<div style="margin-top:6px"><img id="micCamImg" alt="" style="max-width:320px;max-height:240px;border-radius:6px;display:none"> ' +
    '<button class="btn" id="micCamBtn">📷 Последний кадр в облаке</button> <button class="btn" id="micPick">🎙 Микрофон для голосового ввода в чате</button></div>' +
    '<audio id="micAudio" controls style="width:100%;margin-top:6px"></audio>';
  const heard = r.heard || [];
  if (heard.length) h += '<div class="mini" style="margin-top:6px"><b>Что облако услышало:</b>' + heard.map(x =>
    '<div>' + new Date((x.ts || 0) * 1000).toLocaleTimeString() + ' · ' + esc(x.board || '') + ': ' +
    (x.text ? '«' + esc(x.text) + '»' : x.reply ? '↩ ' + esc(x.reply) : '<span class="muted">' + esc((x.kind || 'звук') + '; ' + (x.why || '')) + '</span>') + '</div>').join('') + '</div>';
  const keepSrc = $('#micAudio') && $('#micAudio').src, keepImg = $('#micCamImg') && $('#micCamImg').src;
  box.innerHTML = h;
  const au = $('#micAudio');
  if (keepSrc) au.src = keepSrc;
  if (keepImg) { $('#micCamImg').src = keepImg; $('#micCamImg').style.display = ''; }
  const play = (url) => { au.src = url + (url.includes('?') ? '&' : '?') + 't=' + Date.now(); au.play().catch(() => toast('Звука ещё нет', 'warn')); };
  box.querySelectorAll('[data-mic-go]').forEach(x => x.onclick = async () => {
    const bd = x.dataset.micGo;
    await api('/api/hearing/live', { body: { board: bd, on: true, sec: 60 } });
    toast('Плата начнёт слать звук за ~5 с', 'ok');
    clearInterval(MIC_PLAY);
    MIC_PLAY = setInterval(() => { if (!$('#micAudio')) { clearInterval(MIC_PLAY); return; }
      play('/api/hearing/live.wav?board=' + encodeURIComponent(bd) + '&sec=3'); }, 3000);
    micWire();
  });
  box.querySelectorAll('[data-mic-stop]').forEach(x => x.onclick = async () => {
    clearInterval(MIC_PLAY); MIC_PLAY = null;
    await api('/api/hearing/live', { body: { board: x.dataset.micStop, on: false } });
    micWire();
  });
  box.querySelectorAll('[data-mic-clip]').forEach(x => x.onclick = () => { clearInterval(MIC_PLAY); MIC_PLAY = null;
    play('/api/hearing/live.wav?board=' + encodeURIComponent(x.dataset.micClip) + '&sec=10'); });
  box.querySelectorAll('[data-mic-phrase]').forEach(x => x.onclick = () => { clearInterval(MIC_PLAY); MIC_PLAY = null;
    play('/api/hearing/phrase.wav?board=' + encodeURIComponent(x.dataset.micPhrase)); });
  $('#micPick').onclick = voicePick;
  $('#micCamBtn').onclick = () => { const im = $('#micCamImg'); im.style.display = '';
    im.onerror = () => { im.style.display = 'none'; toast('Кадров в облаке нет', 'warn'); };
    im.src = '/api/vision/last.jpg?t=' + Date.now(); };
  clearTimeout(MIC_T);
  MIC_T = setTimeout(micWire, 2500);
}

/* ------------------------- Общие (вкладка) ------------------------------- */
function tabCommon() {
  const p = $('[data-pane="common"]');
  const st = S.status || {};
  const rset = SETTINGS || {};
  const L = st.load || {};
  setTimeout(partWire, 60);            // v3.177.2: кванты на D6 — свой блок
  clearTimeout(MIC_T); MIC_T = setTimeout(micWire, 80);   // v3.178: уши и глаза плат
  p.innerHTML = '<h2>Общие</h2>' +
    /* v3.173.1: блок Pillow был два раза (одинаковые id — второй вечно «Проверка…») */
    '<div class="fgroup" style="margin-bottom:12px"><label><b>Зрение — Pillow</b></label>' +
    '<div id="pillowStatus" class="mini" style="margin:6px 0">Проверка…</div>' +
    '<button class="btn" id="btnInstallPillow">Установить Pillow</button>' +
    '<div class="hint">Нужен для анализа картинок и кадров. Установка 1–2 минуты.</div></div>' +

    '<div class="hint">Платформа «мозг-12» v' + (st.version || '3.0.0') +
    '. Время работы: ' + Math.round((st.uptime || 0) / 60) + ' мин. ' +
    'Каталог данных: <code>' + esc(rset.data_dir || '') + '</code></div>' +
    (L.cores ? '<div class="card" style="margin:8px 0 12px"><h3 style="margin-top:0">' +
      'Нагрузка на компьютер</h3><div class="mini">ЦП облака: <b>' + L.cpu_pct +
      '%</b> одного ядра (' + L.cpu_pct_total + '% из ' + L.cores + ' ядер' +
      (L.loadavg ? ', средняя нагрузка системы ' + L.loadavg.join(' / ') : '') + ')' +
      ' · память облака: <b>' + fmtN(L.rss_mb) + ' МБ</b>' +
      (L.mem_total_mb ? ' из ' + fmtN(L.mem_total_mb) + ' МБ (свободно ' + fmtN(L.mem_free_mb) + ')' : '') +
      ' · потоков: ' + L.threads + ' · данные облака: ' + fmtN(L.data_mb) + ' МБ, файлов ' +
      fmtN(L.data_files) + ' · Python ' + esc(L.python || '') + ' (' + esc(L.os || '') + ')' +
      '</div><div class="mini" id="llmLoad"></div></div>' : '') +
    '<div class="card" id="partBox" style="margin:8px 0 12px"><h3 style="margin-top:0">⚛ Кванты на D6 (плата v2.92+)</h3>' +
    '<div class="mini">Скетч платы выдаёт на пин D6 дискретные уровни ШИМ: короткие вспышки, пакеты и низкий фон. ' +
    'Шум конденсатора на A4 становится стимулом нейронам и ускоряет подбор весов. Это имитация квантов: настоящие частицы конденсатор не рождает.</div>' +
    '<div id="partForm" class="mini" style="margin-top:6px">загрузка…</div></div>' +
    '<div class="card" id="micBox" style="margin:8px 0 12px"><h3 style="margin-top:0">🎤 Микрофон и 📷 камера плат (плата v2.93+)</h3>' +
    '<div class="mini">Уровень микрофона, распознанные фразы и живой звук с платы — нажми «Слушать», и плата начнёт слать звук в облако на ПК; ' +
    'для камеры по проводу (UART) видны счётчики приёмника: сколько байт и целых кадров дошло и почему нет кадра.</div>' +
    '<div id="micForm" class="mini" style="margin-top:6px">загрузка…</div></div>' +
    '<div class="fgroup" id="setMode"><label><b>Как мозг отвечает на вопрос</b> (v3.129)</label>' +
    '<div class="mini">1) Команды и точные вещи: расчёт, время, заметки, камера и микрофон, код. ' +
    '2) Подтверждённый ответ из памяти диалогов (👍 или «правильный ответ: …»). 3) Статья базы знаний — по контексту вопроса (эпоха, что спрашивают). ' +
    '4) Интернет — с источниками. 5) Прочитанные материалы и похожая статья — только если в них есть ВСЕ слова темы вопроса. 6) Иначе — честное «не знаю» и почему.</div></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setHonest"' +
    (rset.settings && rset.settings.answer_honest !== false ? ' checked' : '') + '>' +
    '<label for="setHonest"><b>Честный ответ без бреда</b> (рекомендуется; объединяет «Честный ответ» и «Не показывать бред»): ответ только из проверяемого — база знаний, интернет, расчёт, код, камера, ' +
    'подтверждённая память. Мысли плат (генератор слов), «память платы» и фантазии своей LLM ответом не выдаются, плату по ходу ответа не ждём ' +
    '(раньше это давало бред вроде «мозг роуз второго плана» и ожидание до 24 с). Факты из источника — дословно, если не включено «Своя LLM пишет свободно».</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setLlmFree"' +
    (rset.settings && rset.settings.llm_free !== false ? ' checked' : '') + '>' +
    '<label for="setLlmFree"><b>Своя LLM пишет свободно</b> (v3.175): переписывает предложения ответа как хочет и пишет свой код — ' +
    'отсеивается только пустое, «каша из букв» и дословный повтор. Внизу ответа — сколько предложений её и какая у них опора на факты; источники остаются ссылками. ' +
    'Код, не прошедший проверку, показывается как её черновик, а ниже — проверенный вариант. Выключи — будет как раньше: дословные факты и строгие фильтры.</label></div>' +
    
    '<div class="fgroup switch"><input type="checkbox" id="setOffline"' +
    (rset.settings && rset.settings.answer_offline !== false ? ' checked' : '') + '>' +
    '<label for="setOffline"><b>Отвечать без сети</b> (v3.122): когда у облака нет интернета, бот всё равно отвечает — базой, памятью, мыслями мозга и своей LLM. ' +
    'Выключено — без интернета отвечает только базой знаний, подтверждёнными ответами и навыками, остальное — сообщение «облако без интернета».</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setWeb"' +
    (rset.settings && rset.settings.web_on ? ' checked' : '') + '>' +
    '<label for="setWeb"><b>Интернет</b>: облако и платы (через облако) выходят в сеть — без него не работают поиск, чтение ссылок и любопытство</label></div>' +
    
    
    '<div class="fgroup"><label for="setWebSrc"><b>Ответы из интернета</b> (v3.155; объединяет «Интернет-источники для вопросов» и «Любые сайты»): ' +
    'страницы выдачи читаются и собираются в ответ с подписью источника</label><select id="setWebSrc">' +
    '<option value="any"' + (rset.settings && rset.settings.web_first && rset.settings.web_any !== false ? ' selected' : '') + '>Поисковики (Mail.ru, Mojeek) + Википедия/Рувики</option>' +
    '<option value="wiki"' + (rset.settings && rset.settings.web_first && rset.settings.web_any === false ? ' selected' : '') + '>Только Википедия</option>' +
    '<option value="off"' + (!rset.settings || !rset.settings.web_first ? ' selected' : '') + '>Не искать в интернете</option></select></div>' +
    (function () {           /* v3.155: поисковики Google и Яндекс + ключи API */
      const ss = rset.settings || {}, en = String(ss.web_engines || 'mailru,mojeek');
      const esc = v => String(v || '').replace(/[&"<>]/g, c => ({ '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;' }[c]));
      return '<div class="fgroup" id="setEngBox"><label for="setEngines"><b>Поисковики</b>: Поиск Mail.ru и Mojeek — без ключей, доступны в России. Google и Яндекс — только с ключами API (без ключа они отдают капчу). Ключи хранятся только в облаке, в панель приходит маска.</label>' +
        '<select id="setEngines">' +
        '<option value="mailru,mojeek"' + (!/google|yandex/.test(en) || /mailru/.test(en) && /mojeek/.test(en) ? ' selected' : '') + '>Поиск Mail.ru и Mojeek (доступны в России, без ключа)</option>' +
        '<option value="mailru"' + (en === 'mailru' ? ' selected' : '') + '>Только Поиск Mail.ru</option>' +
        '<option value="mojeek"' + (en === 'mojeek' ? ' selected' : '') + '>Только Mojeek</option></select>' +
        '<div class="mini">v3.167: Google и Яндекс без ключа отдают капчу — теперь они подключаются только по ключу API (поля ниже) и добавляются к выдаче сами. Энциклопедия: Википедия, при недоступности — Рувики.</div>' +
        '<div class="frow" style="margin-top:6px;gap:6px;flex-wrap:wrap">' +
        '<input id="setGKey" placeholder="Google API key (необязательно)" autocomplete="off" value="' + esc(ss.google_api_key) + '">' +
        '<input id="setGCx" placeholder="Google cx (ID поисковой системы)" autocomplete="off" value="' + esc(ss.google_cx) + '"></div>' +
        '<div class="frow" style="margin-top:6px;gap:6px;flex-wrap:wrap">' +
        '<input id="setYKey" placeholder="Яндекс Search API key (необязательно)" autocomplete="off" value="' + esc(ss.yandex_api_key) + '">' +
        '<input id="setYFolder" placeholder="Яндекс folderid (каталог Yandex Cloud)" autocomplete="off" value="' + esc(ss.yandex_folder) + '"></div>' +
        '<div class="frow" style="margin-top:6px"><button class="btn ghost" id="engTest" type="button">Проверить поисковики</button></div>' +
        '<div class="mini" id="engState">Ключ Google: programmablesearchengine.google.com (cx) + console.cloud.google.com (Custom Search API). Яндекс: Yandex Cloud → Search API (ключ сервисного аккаунта и ID каталога).</div></div>';
    })() +
    /* v3.174: источники для поиска по ссылкам */
    '<div class="fgroup" id="setSrcBox"><label class="switch-inline"><input type="checkbox" id="setSrcOn"' +
    (rset.settings && rset.settings.sources_on === false ? '' : ' checked') + '> <b>Источники для поиска по ссылкам</b> (v3.174)</label>' +
    '<div class="hint">ИИ ищет ответ на ВСЕХ площадках из этого списка: Confluence, вики (MediaWiki), любой сайт (облако обходит его страницы в пределах ссылки и строит свой индекс), документ по прямой ссылке. Нашёл — отвечает связным куском страницы со ссылкой; в базу знаний не сохраняет. Ссылки «Учёбы по ссылкам» попадают сюда сами.</div>' +
    '<div id="srcList" class="mini" style="margin:8px 0">загружаю…</div>' +
    '<div class="frow" style="gap:6px;flex-wrap:wrap"><input type="text" id="srcUrl" style="flex:2;min-width:220px" placeholder="Ссылка: сайт, Confluence, вики или файл" autocomplete="off">' +
    '<input type="text" id="srcName" style="flex:1;min-width:120px" placeholder="Название (необязательно)" autocomplete="off"></div>' +
    '<div class="frow" style="gap:6px;flex-wrap:wrap;margin-top:6px"><input type="text" id="srcUser" placeholder="Логин (если закрыто)" autocomplete="off">' +
    '<input id="srcPw" type="password" style="flex:1;min-width:120px" placeholder="Пароль" autocomplete="new-password"><input type="text" id="srcTok" placeholder="или токен" autocomplete="off">' +
    '<button class="btn" id="srcAdd" type="button">＋ Добавить</button></div>' +
    '<div class="frow" style="gap:6px;margin-top:8px"><input type="text" id="srcQ" style="flex:1" placeholder="Проверить поиск: вопрос…"><button class="btn ghost" id="srcTest" type="button">Искать</button></div>' +
    '<div class="mini" id="srcOut"></div></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setImagine"' +
    (rset.settings && rset.settings.imagine_on ? ' checked' : '') + '>' +
    '<label for="setImagine">Зона Воображения: рисование, перерисовка, графики, LaTeX-формулы, фантазии мини-LLM (v3.16)</label></div>' +
    
    '<div class="fgroup switch"><input type="checkbox" id="setTerminal"' +
    (rset.settings && rset.settings.terminal_on ? ' checked' : '') + '>' +
    '<label for="setTerminal"><b>Терминал и рабочий стол</b> (v3.150): облако выполняет команды («выполни dir»), делает снимок экрана и управляет мышью и клавиатурой компьютера — раздел «🖥 Рабочий стол» в правом меню и команды в чате («клик 500 300», «напечатай …», «нажми ctrl+c», «запусти notepad», «окна»). Только владельцу в панели — боты МАКС и Telegram и платы этого не могут. Выключено по умолчанию, действия пишутся в журнал.</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setBotStatus"' +
    (!rset.settings || rset.settings.bot_status_msg !== false ? ' checked' : '') + '>' +
    '<label for="setBotStatus">Боты (МАКС, Telegram): пока готовится ответ, писать «🔍 Смотрю базу данных…» и убирать это сообщение, когда ответ пришёл (v3.50)</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setD6Neg"' +
    (rset.settings && rset.settings.d6_negfreq ? ' checked' : '') + '>' +
    '<label for="setD6Neg">Отрицательная частота на D6 (v3.51, опция): знак «−» появляется в генераторе платы вместе с числом («−440 Гц») и уходит на D6 кадром вместе с мыслью. Знак выбирает своя LLM (после какой затравки мысль вероятнее: «частота минус.» или «частота плюс.»; решает, когда накопилось ≥20 примеров с обеих сторон; фоном, обучению и ответам не мешает — занята, значит решает Δf: частота ниже сглаженной → «−»). В чате платы загорается иконка «−f D6». ' +
    'Честно: физически отрицательной частоты и напряжения на пине нет (D6 даёт 0…3,3 В на несущей 500 Гц) — знак передаётся кодом: маркерный байт 0xFF («−») или 0xFE («+») и два байта частоты в Гц. ' +
    'Нужны приложение платы v2.47 и скетч v2.46+; читать кадр должен внешний приёмник (вольтметр/A1 другой платы), нейроны шины D4 его не слышат.</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setD4Neg"' +
    (rset.settings && rset.settings.d4_negfreq ? ' checked' : '') + '>' +
    '<label for="setD4Neg">Отрицательная частота и на D4 (v3.52, опция): знак решается так же (своя LLM, иначе Δf). Честно: частота на D4 остаётся той же, меняется ПОЛЯРНОСТЬ меандра — «+»: высокий уровень 1/3 периода, «−»: 2/3 периода (инверсия). Нейроны считают фронты, поэтому темп для них не меняется; знак виден по среднему напряжению на A0 (≈1,1 В против ≈2,2 В) или по длительности высокого уровня. Нужны приложение платы v2.48 и пересобранный скетч.</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setD6Cam"' +
    (rset.settings && rset.settings.d6_camframe ? ' checked' : '') + '>' +
    '<label for="setD6Cam">Кадр ESP32-CAM на D6 (v3.52): кадр уходит САМИМ КАДРОМ — растром 16×12 серых точек, каждая точка уровнем PWM (1…254), строка за строкой (255 — начало/конец кадра, 0 — конец строки), а не байтами JPEG. Идёт по тому же пину циклом из 4 слотов: слот 0 — мысль, слот 1 — точка кадра (отставание кадра от мысли π/2), при галочке «отрицательная частота» точка идёт в слоте 3 (−π/2), темп тот же. Один кадр ≈ 40 с. Уменьшает кадр облако (нужен Pillow), камера ESP32-CAM должна быть подключена. Выкл — D6 несёт только мысль.</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setD4Cam"' +
    (rset.settings && rset.settings.d4_camframe ? ' checked' : '') + '>' +
    '<label for="setD4Cam">Кадр камеры и на D4 (v3.55, опция): тот же чистый кадр (Манчестер 32×24, без PWM и байтов), но частота кадра — «глубокое дыхание» в пределах −2000…+2000 Гц: частоту по строкам задаёт ТОЛЬКО внутренняя тензорная сеть платы (не LLM и не нейроны): по неуверенности сети в зоне последней мысли она выбирает период вдоха-выдоха (4…12 с), глубину размаха и паузу между кадрами (3…10 с вместо прежних 30 с). Знак частоты — код строки: минус = длинный низкий уровень после синхро и обратный код точек (уровни не инвертируются). Частота ведётся не быстрее ±25 % за строку, по модулю не ниже 200 Гц (перед сменой знака «пауза между вдохом и выдохом»). Честно: пока идёт кадр (~2,5 с), меандр генератора на D4 не выводится — при частых кадрах нейроны заметную часть времени видят искажённый темп; снимите галочку, если это мешает. Частота совпадает со словами (v3.56): строки кадра делятся между словами мысли, и если слово есть в таблице «сигнал ↔ слова» (слова с низким сигналом — к минусу, с высоким — к плюсу), его уровень на 60 % задаёт частоту строки, остальное — дыхание сети; неизвестное слово — чистое дыхание. Дыхание (без слов) вместе с фразой уходит в облако и учит смыслу (пара «уровень ↔ слова», дообучение LLM). Нужны плата v2.52 и пересобранный скетч.</label></div>' +
    '<div class="fgroup"><label>Как слать кадр на D6 (v3.53)</label><select id="setD6CamMode">' +
    '<option value="pure"' + ((rset.settings && rset.settings.d6_cammode) !== 'pwm' ? ' selected' : '') + '>Чистый кадр: без PWM и байтов (цифровой уровень, ~1,7 с на кадр)</option>' +
    '<option value="pwm"' + ((rset.settings && rset.settings.d6_cammode) === 'pwm' ? ' selected' : '') + '>Растр уровнями PWM (v3.52, ~41 с на кадр)</option></select>' +
    '<div class="mini">Чистый кадр: пин D6 — обычный цифровой выход, кадр 32×24 чёрно-белых точек (серое — дизеринг) рисуется самим уровнем сигнала, как факс: точка = Манчестер-слот (белая ВЫСОКИЙ→НИЗКИЙ, чёрная НИЗКИЙ→ВЫСОКИЙ), начало кадра — высокий уровень 6 половинок, начало строки — 3 (в данных больше 2 подряд не бывает). «+»: кадр идёт после мысли с паузой π/2 (мысль, пришедшая во время кадра, кадр прерывает); «−» (отрицательная частота): кадр опережает мысль на π/2, мысль ждёт его конца. Пока не проверено на железе UNO Q; приёмник — осциллограф/логический анализатор/A1 другой платы.</div></div>' +
    '<div class="fgroup"><label>Сколько первых страниц выдачи читать для ответа (v3.14, до 10)</label>' +
    '<input type="number" id="setWebPages" min="1" max="10" step="1" value="' +
    (rset.settings && rset.settings.web_pages ? rset.settings.web_pages : 10) + '"></div>' +
    (function () {          /* v3.166: строгая связь с мозгом neuro-12 */
      const ss = rset.settings || {};
      return '<div class="fgroup switch"><input type="checkbox" id="setBrainStrict"' + (ss.brain_strict !== false ? ' checked' : '') + '>' +
        '<label for="setBrainStrict"><b>Строгая связь с мозгом</b> (v3.166): каждый фоновый процесс облака работает, только пока его зона мозга neuro-12 активна; сила — 2×воля зоны. Зона молчит — процесс стоит. Ответы в чате и задачи, которые вы запустили сами, мозг не останавливает.</label></div>' +
        '<div class="fgroup" id="setBrainBox"><label for="setBrainMin">Порог воли зоны: ниже — процесс стоит (0…0,9)</label>' +
        '<input type="number" id="setBrainMin" min="0" max="0.9" step="0.05" value="' + (ss.brain_strict_min != null ? ss.brain_strict_min : 0.1) + '">' +
        '<label for="setBrainNo" style="margin-top:6px">Мозга нет на связи (плат нет 90 с)</label><select id="setBrainNo">' +
        '<option value="pause"' + (ss.brain_strict_nobrain !== 'neutral' ? ' selected' : '') + '>Фоновые процессы ждут мозг</option>' +
        '<option value="neutral"' + (ss.brain_strict_nobrain === 'neutral' ? ' selected' : '') + '>Работать как обычно</option></select>' +
        '<div class="mini" id="brainCtl">Состояние связи загружается…</div></div>' +
        '<div class="fgroup switch"><input type="checkbox" id="setBrainLlm"' + (ss.brain_llm_thought !== false ? ' checked' : '') + '>' +
        '<label for="setBrainLlm"><b>Своя LLM пишет от мысли мозга</b>: в ответе раздел «Мысль мозга → своя LLM» — мысль neuro-12 по теме вопроса (или ведущая) и продолжение своей LLM по фактам ответа.</label></div>';
    })() +
    '<div class="fgroup switch" id="setLlmWebBox"><input type="checkbox" id="setLlmWeb"' +
    (rset.settings && rset.settings.llm_web_on !== false ? ' checked' : '') + '>' +
    '<label for="setLlmWeb"><b>Своя LLM сама ходит в интернет</b> (v3.169): раз в 5 мин читает Википедию/Рувики и Confluence по темам, где промолчала в ответе, по мыслям мозга и вопросам чата — в свой корпус (не в базу знаний) и дообучается. <button class="btn ghost" id="llmWebNow" type="button">Прочитать сейчас</button></label>' +
    '<div class="mini" id="llmWebState"></div></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setReadFirst"' +
    (rset.settings && rset.settings.answer_read_first !== false ? ' checked' : '') + '>' +
    '<label for="setReadFirst"><b>Читать статью перед ответом, как «Любопытство»</b> (v3.165): статьи по теме в базе нет — облако сначала читает её в Википедии целиком, сохраняет в базу и отвечает по ней. Прочитанное получают мозг облака (сеть материалов), мозг neuro-12 на платах (выжимка) и своя LLM (чтение статьи). Практические и свежие вопросы («как установить…», «погода», «курс») — по страницам Google/Яндекса, как раньше.</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setPassage"' +
    (rset.settings && rset.settings.answer_passage !== false ? ' checked' : '') + '>' +
    '<label for="setPassage"><b>Отвечать связным куском подряд</b> (v3.165): от первого предложения по теме и дальше по тексту источника, а не отдельными предложениями со словом вопроса вразброс.</label></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="setChatsIp"' +
    (rset.settings && rset.settings.chats_by_ip ? ' checked' : '') + '>' +
    '<label for="setChatsIp">У разных адресов (IP) — разные чаты (v3.24.2; старые чаты без владельца и «Мысли плат» — общие)</label></div>' +
    '<div class="fgroup" id="setNetBox"><div id="netUse" class="mini">Показания сети загружаются…</div>' +
    '<div class="frow" style="margin-top:6px">' +
    '<button class="btn ghost" id="netX2" type="button">Расширить ×2</button> ' +
    '<button class="btn ghost" id="netKb" type="button">Под всю базу знаний</button> ' +
    '<button class="btn ghost" id="netLoad" type="button">Догрузить базу в сеть</button></div>' +
    '<div class="mini" id="netEst" style="margin-top:4px"></div></div>' +
    '<div class="fgroup"><label>Предел узлов сети материалов (по умолчанию 150000; дошла до предела — поднимите, редкие узлы при превышении вытесняются)</label>' +
    '<input type="number" id="setNetNodes" min="1000" max="20000000" step="1000" value="' +
    ((rset.settings && rset.settings.net_nodes_max) || 150000) + '"></div>' +
    '<div class="fgroup"><label>Предел материалов сети (статей/мыслей; по умолчанию 3000 — при его достижении старые вытесняются, сеть «стоит»)</label>' +
    '<input type="number" id="setNetDocs" min="100" max="2000000" step="500" value="' +
    ((rset.settings && rset.settings.net_docs_max) || 3000) + '"></div>' +
    '<div class="fgroup"><label>Предел предложений сети (по умолчанию 40000)</label>' +
    '<input type="number" id="setNetSents" min="1000" max="50000000" step="5000" value="' +
    ((rset.settings && rset.settings.net_sents_max) || 40000) + '"></div>' +
    '<div class="fgroup"><label>Токен платы (X-Mozg-Token) — впиши его в ' +
    'настройках платы: облако + токен</label>' +
    '<input type="text" id="setToken" value="' + esc(rset.token || '') + '"></div>' +
    '<button class="btn" id="saveCommon">Сохранить</button>';
  groupCommon(p);
  /* v3.105: сеть материалов — заполненность, оценка памяти, расширение */
  const rdN = (st.cloud_readings || {}), kbN = ((st.stats || {}).kb_articles) || 0;
  const netEst = () => {
    const n = +$('#setNetNodes').value || 0, d = +$('#setNetDocs').value || 0, sn = +$('#setNetSents').value || 0;
    const mb = Math.round((n * 0.6 + d * 2 + sn * 1.3) / 1024);
    $('#netEst').innerHTML = 'Оценка памяти сети при этих пределах: ~<b>' + fmtN(mb) + ' МБ</b> ОЗУ и столько же на диске' +
      (kbN ? ' · статей в базе знаний: <b>' + fmtN(kbN) + '</b>' + (d < kbN ? ' — <span class="bad">материалов в сети меньше, чем статей: часть базы мозг не помнит</span>' : '') : '') +
      '. Чем больше сеть, тем дольше её обход в ответах и сохранение.';
  };
  const pct = (a, b) => b ? Math.min(100, Math.round(100 * a / b)) : 0;
  const bar = (name, a, b) => {
    const pc = pct(a, b);
    return '<div style="margin:3px 0"><b>' + name + ':</b> ' + fmtN(a) + ' из ' + fmtN(b) + ' (' + pc + '%)' +
      '<div style="height:6px;border-radius:3px;background:var(--line,#333);margin-top:2px"><div style="height:6px;border-radius:3px;width:' +
      pc + '%;background:' + (pc >= 95 ? '#e5484d' : pc >= 80 ? '#d19a1e' : 'var(--acc,#2dd4bf)') + '"></div></div></div>';
  };
  if (rdN.nodes != null) {
    const full = pct(rdN.docs, rdN.docs_max) >= 95 || pct(rdN.sents, rdN.sents_max) >= 95 || pct(rdN.nodes, rdN.nodes_max) >= 95;
    $('#netUse').innerHTML = bar('Узлы (основы слов)', rdN.nodes, rdN.nodes_max || (+$('#setNetNodes').value)) +
      bar('Материалы (статьи, файлы, диалоги, мысли)', rdN.docs, rdN.docs_max || (+$('#setNetDocs').value)) +
      bar('Предложения', rdN.sents, rdN.sents_max || (+$('#setNetSents').value)) +
      '<div>Связей: ' + fmtN(rdN.links || 0) + (rdN.busy ? ' · сеть сейчас занята — показания последние известные' : '') + '</div>' +
      (full ? '<div class="bad" style="margin-top:4px">Сеть заполнена: новое вытесняет старое, поэтому кажется, что облако перестало загружать информацию. Расширь пределы и нажми «Сохранить», затем «Догрузить базу в сеть».</div>' : '');
  } else {
    $('#netUse').textContent = 'Сеть ещё загружается после старта — показания появятся через минуту.';
  }
  ['setNetNodes', 'setNetDocs', 'setNetSents'].forEach(id => $('#' + id).addEventListener('input', netEst));
  netEst();
  $('#netX2').onclick = () => {
    ['setNetNodes', 'setNetDocs', 'setNetSents'].forEach(id => { $('#' + id).value = (+$('#' + id).value || 0) * 2; });
    netEst(); toast('Пределы увеличены вдвое — нажми «Сохранить»', 'ok');
  };
  $('#netKb').onclick = () => {
    const d = Math.max(+$('#setNetDocs').value || 0, Math.ceil(kbN * 1.2) + 2000);
    $('#setNetDocs').value = d;
    $('#setNetSents').value = Math.max(+$('#setNetSents').value || 0, d * 40);
    $('#setNetNodes').value = Math.max(+$('#setNetNodes').value || 0, 150000, Math.round(d * 30));
    netEst(); toast('Пределы подобраны под базу знаний (' + fmtN(kbN) + ' статей) — нажми «Сохранить»', 'ok');
  };
  $('#netLoad').onclick = async () => {
    await api('/api/brain/train', { body: { start: true } });
    toast('Мозг перечитывает базу знаний в сеть материалов (идёт в фоне)', 'ok');
  };
  (async () => {               /* v3.166: что мозг разрешил и что остановил */
    const box = $('#brainCtl'); if (!box) return;
    try {
      const r = await api('/api/brainctl');
      const esc = t => String(t || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
      const link = (r.will_boards || []).length ? '🟢 Мозг на связи: воля зон приходит от ' + r.will_boards.map(esc).join(', ') + '<br>'
        : (r.boards_online ? '🟡 Плата на связи (' + r.boards_online + '), но не присылает активность зон — обновите приложение платы<br>'
          : '🔴 Плат на связи нет<br>');
      const ld = link + (r.lead ? 'Ведущая зона: <b>' + esc(r.lead.zone) + '</b> (' + Number(r.lead.a).toFixed(2) + (r.lead.derived ? ', по воле зон' : ', плата ' + esc(r.lead.board)) + ')' : 'Ведущей зоны нет');
      const th = r.thought ? '<br>Мысль мозга: «' + esc(r.thought.text) + '»' : '';
      const st = (r.gates || []).filter(g => !g.ok).map(g => esc(g.name) + ' — ' + esc(g.why));
      box.innerHTML = ld + th + '<br>Работают: ' + r.running + ', стоят: ' + r.stopped + (st.length ? '<br>⏸ ' + st.join('<br>⏸ ') : '');
    } catch (e) { box.textContent = 'Состояние не загрузилось: ' + (e && e.message || e); }
  })();
  const llmWebShow = (r) => {
    const box = $('#llmWebState'); if (!box || !r) return;
    box.textContent = 'Своя LLM прочитала сама: ' + (r.reads || 0) + ' (в корпусе ' + (r.kept || 0) + ')' +
      (r.last ? ', последнее «' + r.last + '»' : '') + ((r.want || []).length ? '; хочет прочитать: ' + r.want.join(', ') : '') +
      (r.result && !r.result.ok ? ' — ' + (r.result.why || '') : '') + (r.err ? ' · ' + r.err : '');
  };
  api('/api/llm/web').then(llmWebShow).catch(() => {});
  if ($('#llmWebNow')) $('#llmWebNow').onclick = async (ev) => {
    ev.preventDefault(); $('#llmWebState').textContent = 'Читаю…';
    try { llmWebShow(await api('/api/llm/web', { body: {} })); } catch (e) { $('#llmWebState').textContent = String(e && e.message || e); }
  };
  /* v3.174: источники по ссылкам */
  const srcEsc = t => String(t == null ? '' : t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const KIND = { confluence: 'Confluence', mediawiki: 'вики', site: 'сайт', file: 'документ' };
  const srcLoad = async () => {
    const box = $('#srcList'); if (!box) return;
    try {
      const r = await api('/api/sources');
      const xs = r.sources || [];
      box.innerHTML = xs.length ? xs.map(x =>
        '<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line)">' +
        '<input type="checkbox" data-src-on="' + x.id + '"' + (x.on ? ' checked' : '') + ' title="искать здесь">' +
        '<div style="flex:1;min-width:0"><b>' + srcEsc(x.name || x.url) + '</b> · ' + (KIND[x.kind] || x.kind) +
        (x.from && x.from !== 'владелец' ? ' · из «' + srcEsc(x.from) + '»' : '') +
        (x.pages != null ? ' · страниц в индексе: ' + x.pages : '') + (x.indexing ? ' · ⏳ обход сайта…' : '') +
        (x.has_password || x.has_token ? ' · 🔑' : '') +
        (x.state_ok === false ? ' · <span style="color:#f87171">⚠ ' + srcEsc(x.state_err) + '</span>' : (x.state_ok ? ' · ✅' : '')) +
        '<div style="opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + srcEsc(x.url) + '</div></div>' +
        '<button class="btn ghost" data-src-probe="' + x.id + '" type="button" title="проверить">⟳</button>' +
        ((x.kind === 'site' || x.kind === 'file') ? '<button class="btn ghost" data-src-re="' + x.id + '" type="button" title="перестроить индекс">⤓</button>' : '') +
        '<button class="btn ghost" data-src-del="' + x.id + '" type="button" title="убрать">✕</button></div>').join('')
        : 'Источников пока нет — добавьте ссылку ниже.';
      $$('[data-src-on]', box).forEach(c => c.onchange = () => api('/api/sources', { body: { act: 'on', id: c.dataset.srcOn, on: c.checked } }));
      $$('[data-src-del]', box).forEach(b => b.onclick = async () => { await api('/api/sources', { body: { act: 'remove', id: b.dataset.srcDel } }); srcLoad(); });
      $$('[data-src-probe]', box).forEach(b => b.onclick = async () => { b.textContent = '…'; await api('/api/sources', { body: { act: 'probe', id: b.dataset.srcProbe } }); srcLoad(); });
      $$('[data-src-re]', box).forEach(b => b.onclick = async () => { await api('/api/sources', { body: { act: 'reindex', id: b.dataset.srcRe } }); toast('Обход сайта запущен — это займёт несколько минут'); setTimeout(srcLoad, 1500); });
    } catch (e) { box.textContent = 'Список не загрузился: ' + (e && e.message || e); }
  };
  srcLoad();
  if ($('#srcAdd')) $('#srcAdd').onclick = async () => {
    const url = $('#srcUrl').value.trim(); if (!url) return;
    const r = await api('/api/sources', { body: { act: 'add', url, name: $('#srcName').value.trim(), user: $('#srcUser').value.trim(), password: $('#srcPw').value, token: $('#srcTok').value.trim() } });
    if (!r.ok) { toast(r.error || 'не добавилось', 'err'); return; }
    ['#srcUrl', '#srcName', '#srcUser', '#srcPw', '#srcTok'].forEach(k => $(k).value = '');
    toast('Источник добавлен — проверяю'); setTimeout(srcLoad, 2500);
  };
  if ($('#srcTest')) $('#srcTest').onclick = async () => {
    const q = $('#srcQ').value.trim(); if (!q) return;
    $('#srcOut').textContent = 'Ищу во всех источниках…';
    try {
      const r = await api('/api/sources', { body: { act: 'search', q } });
      const a = r.answer || {};
      $('#srcOut').innerHTML = (a.ok ? '<b>Ответ</b> (' + srcEsc(a.src) + ' «' + srcEsc(a.title) + '»): ' + srcEsc(a.text).slice(0, 600) + '<br>' : '<b>Ответа нет</b>: ' + srcEsc(a.why || '') + '<br>') +
        (r.results || []).map(x => '• ' + srcEsc(x.src) + ': <a href="' + srcEsc(x.url) + '" target="_blank" rel="noopener">' + srcEsc(x.title) + '</a> (совпадений ' + x.score + ')').join('<br>') +
        ((r.errors || []).length ? '<br>⚠ ' + r.errors.map(srcEsc).join('<br>⚠ ') : '');
      srcLoad();
    } catch (e) { $('#srcOut').textContent = 'Ошибка: ' + (e && e.message || e); }
  };
  if ($('#engTest')) $('#engTest').onclick = async () => {
    const box = $('#engState'); box.textContent = 'Ищу «фотосинтез» во всех поисковиках…';
    try {
      const r = await api('/api/web/engines', { body: { q: 'фотосинтез' } });
      box.textContent = Object.entries(r.engines || {}).map(([k, v]) =>
        k + ': ' + (v.found ? '✅ ' + v.found + ' ссылок' : '❌ ' + (v.err || 'пусто')) + (v.via ? ' (' + v.via + ')' : '')).join(' · ') || 'нет ответа';
    } catch (e) { box.textContent = 'Проверка не удалась: ' + (e && e.message || e); }
  };
  $('#saveCommon').onclick = async () => {
    const token = $('#setToken').value.trim();
    await api('/api/settings', { body: {
      web_on: $('#setWeb').checked,
      web_any: $('#setWebSrc').value === 'any',
      answer_strict: $('#setHonest').checked,   // v3.155: одна галочка «честный ответ без бреда»
      answer_honest: $('#setHonest').checked, llm_free: ($('#setLlmFree') ? $('#setLlmFree').checked : true),
      answer_offline: $('#setOffline').checked,
      web_first: $('#setWebSrc').value !== 'off',
      web_pages: Math.max(1, Math.min(10, parseInt($('#setWebPages').value, 10) || 10)),
      answer_read_first: $('#setReadFirst').checked, llm_web_on: $('#setLlmWeb').checked,
      brain_strict: $('#setBrainStrict').checked, brain_llm_thought: $('#setBrainLlm').checked,
      brain_strict_min: parseFloat($('#setBrainMin').value) || 0, brain_strict_nobrain: $('#setBrainNo').value,
      answer_passage: $('#setPassage').checked,
      web_engines: $('#setEngines').value,
      google_api_key: $('#setGKey').value.trim(), google_cx: $('#setGCx').value.trim(),
      yandex_api_key: $('#setYKey').value.trim(), yandex_folder: $('#setYFolder').value.trim(),
      sources_on: $('#setSrcOn').checked,
      chats_by_ip: $('#setChatsIp').checked,
      net_nodes_max: Math.max(1000, parseInt($('#setNetNodes').value, 10) || 150000),
      net_docs_max: Math.max(100, parseInt($('#setNetDocs').value, 10) || 3000),
      net_sents_max: Math.max(1000, parseInt($('#setNetSents').value, 10) || 40000),
      imagine_on: $('#setImagine').checked,
      terminal_on: $('#setTerminal') ? $('#setTerminal').checked : false,
      bot_status_msg: $('#setBotStatus').checked,
      d6_negfreq: $('#setD6Neg').checked,
      d4_negfreq: $('#setD4Neg').checked,
      d6_camframe: $('#setD6Cam').checked,
      d6_cammode: $('#setD6CamMode').value,
      d4_camframe: $('#setD4Cam').checked,
      d6_on: true } });
    if (token && token !== (rset.token || '')) {
      const fd = new FormData(); fd.append('token', token);
      await fetch('/api/settings/token', { method: 'POST', body: fd });
    }
    toast('Настройки сохранены', 'ok');
    SETTINGS = (await api('/api/settings'));
  };

  // Pillow install
  (async () => {
    const st = $('#pillowStatus');
    if (!st) return;
    try {
      const r = await api('/api/vision');
      if (r.status && r.status.pillow) {
        st.innerHTML = '✓ Pillow установлен — зрение доступно';
        const btn = $('#btnInstallPillow');
        if (btn) btn.style.display = 'none';
      } else {
        st.innerHTML = 'Pillow не установлен';
      }
    } catch (e) { st.textContent = 'не удалось проверить'; }
  })();
  const pbtn = $('#btnInstallPillow');
  if (pbtn) pbtn.onclick = async () => {
    pbtn.disabled = true;
    const st = $('#pillowStatus');
    st.textContent = 'Устанавливаю Pillow…';
    try {
      const r = await api('/api/install/pillow', { body: {} });
      if (r.ok && (r.status === 'installed' || r.status === 'already')) {
        st.innerHTML = '✓ ' + (r.message || 'Pillow установлен');
        pbtn.style.display = 'none';
        toast('Pillow установлен', 'ok');
      } else {
        st.innerHTML = 'Ошибка: ' + (r.message || r.error || 'неизвестно');
        pbtn.disabled = false;
      }
    } catch (e) {
      st.textContent = 'Ошибка: ' + e.message;
      pbtn.disabled = false;
    }
  };
}


/* ----------------------------- Устройства -------------------------------- */
async function tabDevices() {
  const p = $('[data-pane="devices"]');
  p.innerHTML = '<h2>Устройства</h2>' +
    '<div class="hint">Облако САМО стучится к плате (мост, по умолчанию ' +
    '127.0.0.1:7000) и принимает регистрации плат (мозг-почта). Если панель ' +
    'платформы была «недоступна» — её поднимет страж Windows или двойной ' +
    'клик по neuro12-cloud.exe.</div>' +
    '<div class="card" id="lnBox" style="margin:10px 0 14px"><div class="typing">' +
    '<i></i><i></i><i></i></div></div>' +
    '<div class="card" id="unBox" style="margin:10px 0 14px"><div class="typing">' +
    '<i></i><i></i><i></i></div></div>' +
    '<div id="brBox"><div class="typing"><i></i><i></i><i></i></div></div>' +
    '<h3 style="margin-top:18px">Платы, зарегистрированные сами</h3>' +
    '<div id="devBox"><div class="typing"><i></i><i></i><i></i></div></div>';
  renderLink();
  renderUnify();
  await renderBridge();
  renderBoards();
}

/* v3.102: СВЯЗЬ МОЗГОВ — частота генератора и мысли, в реальном времени */
async function renderLink() {
  const box = $('#lnBox');
  if (!box) return;
  let r;
  try { r = await api('/api/link/status'); } catch (e) { box.textContent = 'Связь мозгов недоступна.'; return; }
  const l = (r && r.link) || {}, n = l.n || {}, bs = l.boards || [];
  const fmtEta = (x) => !x ? '' : (x > 7200 ? Math.round(x / 3600) + ' ч' : x > 120 ? Math.round(x / 60) + ' мин' : x + ' с');
  // v3.120: «0 Гц» — с причиной: скетч не шлёт сводку (мост к скетчу молчит) или генератор стоит
  const hzWhy = (b) => { const br = b.bridge; if (!br) return ' (плата старше v2.64 — причину не сообщает)';
    if (!br.seen) return ' — скетч ни разу не ответил приложению (не прошит/не запущен?)';
    if (!br.alive) return ' — мост к скетчу молчит ' + br.age + ' с (частота не приходит)';
    return ' — скетч на связи, генератор стоит'; };
  const hz = (b) => { const v = b.shz != null ? Math.round(b.shz) : (b.hz || 0);
    if (!v) return '0 Гц' + hzWhy(b);
    return (v > 0 && b.shz != null ? '+' : '') + v + ' Гц'; };
  const brs = r && r.breath;      /* v3.133: общее дыхание — от тензорной сети платы */
  let wd = null;
  try { wd = await api('/api/worddict?top=24'); } catch (e) { wd = null; }
  const brHtml = (!brs ? '' : '<div class="card" style="margin:8px 0;padding:10px 12px">' +
    '<b>🫁 Общее дыхание всех ИИ-алгоритмов</b>: сейчас <b>' + esc(brs.state) + '</b>, период ' + brs.period +
    ' с, глубина ' + brs.depth + ' — ' + esc(brs.src) + '.<div class="mini">Источник один — тензорная сеть платы (период и глубина по её уверенности). ' +
    'Вдох — учёба и чтение, выдох — выдача. Алгоритмов всего: ' + brs.count.all + ' (облако ' + brs.count.cloud_total +
    ', плата ' + brs.count.board_total + '), дышат все.</div>' +
    '<div class="mini" style="margin-top:4px">🧠 <b>Подчиняются мозгу Роуз</b>: ' + esc(brs.will_src || '') +
      '. Зона мозга активна — её алгоритмы сильнее (до ×1,5), молчит — тише (до ×0,5); на честность ответов это не влияет.</div>' +
    '<div class="mini">' + brs.algos.map(a => (a.uses ? '🟢 ' : '⚪ ') + '<b>' + esc(a.name) + '</b>' + (a.board ? ' (на плате)' : '') +
      ' — зона «' + esc(a.zone || '?') + '», сила ×' + (a.strength != null ? a.strength : 1) + '; ' + esc(a.how) +
      (a.uses ? ' (вдохов/модуляций: ' + a.uses + ')' : ' (ещё не работал)')).join('<br>') + '</div></div>') +
    (!wd || !wd.ok ? '' : '<div class="card" style="margin:8px 0;padding:10px 12px"><b>📖 Общий словарь мозга (плата + облако)</b>: слов ' +
      wd.words + ', с частотой генератора ' + wd.with_hz + ', связей ' + wd.links + ', версия ' + wd.rev + '.' +
      '<div class="mini">Подбор слов — та же формула, что у генератора платы (+ частота и дыхание). Частые слова: ' +
      (wd.top || []).map(t => esc(t.w) + ' ×' + t.n + (t.hz != null ? ' (' + (t.hz > 0 ? '+' : '') + t.hz + ' Гц)' : '')).join(', ') + '</div></div>');
  const tm = r && r.tmod;          /* v3.146: тензор модуляции сетей */
  const tmHtml = (!tm || !tm.ok) ? '' : '<div class="card" style="margin:8px 0;padding:10px 12px">' +
    '<b>🧮 Тензорные сети: веса от частот плат и дыхания мозга</b>' +
    '<div class="mini">Вход тензора сейчас: ' + tm.x_names.slice(0, 7).map((n, i) => esc(n) + ' ' + tm.x[i]).join(' · ') +
    '. Множители весов каждой сети = 1 ± ' + Math.round(tm.amp * 100) + '% × сила. Своя LLM не модулируется.</div>' +
    tm.nets.map(n => '<div class="frow" style="gap:10px;align-items:center;margin-top:6px;flex-wrap:wrap">' +
      '<b style="min-width:210px">' + esc(n.name) + '</b>' +
      '<input type="range" min="0" max="100" step="5" value="' + n.k + '" data-tmod="' + esc(n.name) + '" style="width:160px">' +
      '<span class="mini" style="min-width:44px">' + n.k + '%</span>' +
      '<span class="mini">' + esc(n.how) + (n.min != null ? ' — множители сейчас ' + n.min + '…' + n.max : ' — ещё не работала') + '</span></div>' +
      (n.last && n.last.of ? '<div class="mini" style="margin-left:220px">последний ответ: контекст ' + n.last.ctx + ' из ' + n.last.of.ctx +
        ', головы ' + n.last.heads + ' из ' + n.last.of.heads + ', слои ' + n.last.layers + ' из ' + n.last.of.layers +
        ', температура ' + n.last.temp + ', top-k ' + n.last.top_k + ', top-p ' + n.last.top_p + ', повторы ' + n.last.rep +
        ', длина ' + n.last.max_new + ' (вопрос ' + n.last.q_words + ' слов → размах ×' + n.last.q_mul + ')</div>' : '')).join('') +
    '</div>';
  box.innerHTML = '<h3 style="margin-top:0">🔗 Связь мозгов: частота генератора и мысли</h3>' + brHtml + tmHtml +
    '<div class="hint">Плата (v2.60) раз в ~10 с присылает частоту генератора скетча и новые мысли. Частота сразу ведёт ' +
    'все нейросети облака (обучение и слова своей LLM, выбор предложений в ответах); мысль учит таблицу «частота ↔ слова», ' +
    'а мозг облака отвечает на неё своей мыслью (своя LLM дополняет) — ответ уходит на плату: нейроны, сеть материалов, ' +
    'пин D6 и текст-канал скетча, от которого меняется частота генератора. Обучение своей LLM идёт в порядке, который ' +
    'задают мысли и частота плат.</div>' +
    '<div class="mini"><b>Платы на связи:</b> ' + (bs.length ? bs.map(b => esc(b.id) + ' — ' + hz(b) +
      ' (' + b.age + ' с назад' + (b.ip ? ', ' + esc(b.ip) : '') + (b.ver ? ', v' + esc(b.ver) : '') +
      (b.up != null ? ', работает ' + fmtEta(Math.round(b.up)) : '') +
      (b.rss ? ', память ' + b.rss + ' МБ' + (b.rss_first && b.rss > b.rss_first * 1.5 ? ' (растёт: было ' + b.rss_first + ')' : '') : '') +
      (b.restarts ? ', <span class="bad">перезапусков: ' + b.restarts + '</span>' : '') + ')' +
      '<br><span class="mini">🔌 Нейроны (12 плат на шине): ' + (b.net ? (b.neurons_online + '/12 отвечают — ' +
        b.net.split('').map((c, i) => (c === '1' ? '●' : '○')).join('')) :
        'карта связи не пришла' + (b.bridge && !b.bridge.seen ? ' — скетч ещё ни разу не ответил (мост ждёт скетч)' :
          ' (нужна плата v2.72+ и пересобранный скетч)')) + '</span>' +
      (b.grav && b.grav.strain != null ? '<br><span class="mini">🪐 Гравитация мыслей (модель, не физика): напряжение сети ' +
        Math.round(100 * b.grav.strain) + '%, тяга G=' + b.grav.g +
        ((b.grav.masses || []).length ? ', самые «тяжёлые» понятия: ' + b.grav.masses.map(m => esc(m[0]) + '… (' + m[1] + ')').join(', ') : '') +
        (b.grav.pulled ? '; последняя мысль «упала» к слову «' + esc(b.grav.pulled) + '»' : '') + '</span>' : '') +
      (b.crash ? '<br><span class="bad">Прошлое падение приложения платы: ' + esc(b.crash.slice(0, 300)) + '</span>' : '')
      ).join('<br>') : 'пока нет — нужна плата neuro_12 v2.60') + '<br>' +
    '<b>Обменов:</b> ' + fmtN(n.links) + ' · вопросов мозгу по каналу (мост недоступен): ' + fmtN(n.asks || 0) +
    ' · мыслей плат: ' + fmtN(n.phrases) + ' · ответов облака: ' + fmtN(n.replies) +
    ' (из них со своей LLM: ' + fmtN(n.llm) + ') · уроков «частота ↔ слова»: ' + fmtN(n.frames) + '</div>' +
    ((l.log || []).length ? '<div class="mini" style="margin-top:6px">' + l.log.slice(0, 5).map(x =>
      '🧠 «' + esc(x.phrase) + '» <span class="pill">' + (x.shz != null ? Math.round(x.shz) + ' Гц' : '?') + '</span> → ☁ «' +
      esc(x.cloud || '') + '»' + (x.llm ? ' + 🤖 «' + esc(x.llm) + '»' : '')).join('<br>') + '</div>' : '') +
    (r.coherence ? '<div class="mini" style="margin-top:8px"><b>🧩 Связность мыслей мозга (v3.121):</b> мысль мозга идёт ответом в чат, только если ' +
      'её пары соседних слов встречаются в настоящих текстах (база знаний, подтверждённые ответы) не реже порога; каждая мысль платы получает ' +
      'вердикт, и плата ослабляет «чужие» пары слов — учится не писать ахинею. ' +
      (r.coherence.ready ? 'Судья: ' + fmtN(r.coherence.pairs) + ' пар слов' : 'судья строится…') +
      ' · проверено ' + fmtN(r.coherence.judged) + ', связных ' + fmtN(r.coherence.good) + ', несвязных ' + fmtN(r.coherence.bad) +
      ', не показано ответом ' + fmtN(r.coherence.blocked) +
      '<br>Порог связности (0–1): <input type="number" id="cohMin" min="0" max="1" step="0.05" style="width:70px" value="' + r.coherence.threshold + '"> ' +
      '<button class="btn ghost" id="cohSave">Сохранить</button></div>' : '');
  const sy = l.sync;
  if (sy) {
    const sp = (sy.spread || []).map(x => x[1]);
    box.insertAdjacentHTML('beforeend', '<div class="mini" style="margin-top:8px"><b>🔄 Синхронизация мозгов по частоте (v3.125):</b> ' +
      'каждая плата получает общий ритм — среднюю частоту остальных плат и ритма платформы (активность облака) — и подтягивает к нему генератор скетча ' +
      'на долю силы связи (как связанные маятники). Ритм платформы сейчас ' + sy.platform + ' Гц' +
      (sp.length ? '; разброс частот (меньше — синхроннее): ' + sp.join(' → ') + ' Гц' : '') +
      '<br><label><input type="checkbox" id="syncOn"' + (sy.on ? ' checked' : '') + '> синхронизировать</label> · сила связи, % (0–60): ' +
      '<input type="number" id="syncK" min="0" max="60" step="5" style="width:60px" value="' + sy.k + '"> ' +
      '<button class="btn ghost" id="syncSave">Сохранить</button> <span class="mini">нужны плата v2.67 и пересобранный скетч</span></div>');
    const ss = $('#syncSave');
    if (ss) ss.onclick = async () => { await api('/api/link/status', { body: { sync_on: $('#syncOn').checked,
      sync_k: parseInt($('#syncK').value, 10) || 0 } }); toast('Синхронизация сохранена', 'ok'); };
  }
  const cs = $('#cohSave');
  if (cs) cs.onclick = async () => { await api('/api/link/status', { body: { brain_coh_min: parseFloat($('#cohMin').value) } });
    toast('Порог связности сохранён', 'ok'); };
}

/* v3.29: ЕДИНЫЙ МОЗГ — облако и плата дополняют друг друга */
async function renderUnify(rr) {
  const box = $('#unBox');
  if (!box) return;
  let r = rr;
  try { if (!r) r = await api('/api/unify'); } catch (e) { box.textContent = 'Единый мозг недоступен.'; return; }
  const u = (r && r.unify) || {};
  const c = u.counters || {};
  const bs = u.boards || [];
  box.innerHTML = '<h3 style="margin-top:0">🧠 Единый мозг: облако + плата</h3>' +
    '<div class="hint">Слабый ответ облака (догадка, пересказ своей сети, «не знаю») ' +
    'дополняет плата — в веб-чате, боте МАКС и API. Плата отвечает с честной пометкой: ' +
    'есть ли у ответа опора (подтверждённый ответ, её файл, память кода, живое ' +
    'состояние) — в МАКС уходят только ответы с опорой. Плата спрашивает весь мозг ' +
    'облака, и подтверждённое в одном месте знают оба. Нужна плата v2.41+.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="unOn"' + (u.on ? ' checked' : '') +
    '><label for="unOn">Единый мозг включён</label></div>' +
    '<div class="fgroup" style="max-width:320px"><label>Ждать плату в МАКС/API, сек ' +
    '(0 — не ждать, дописать позже)</label><input type="number" id="unWait" min="0" max="60" value="' +
    (u.wait != null ? u.wait : 12) + '"></div>' +
    '<div class="mini"><b>Плат в сети:</b> ' + (bs.length ? bs.map(b => esc(b.id) +
      ' (' + b.age + ' с назад)').join(', ') : 'нет — облако отвечает само') + '<br>' +
    '<b>Облако спросило плату:</b> ' + fmtN(c.asked) + ' · ответила: ' + fmtN(c.answered) +
    ' · с опорой: ' + fmtN(c.grounded) + ' · влито в ответ: ' + fmtN(c.merged) +
    ' · дописано позже: ' + fmtN(c.late) + ' · без опоры (не отправлено в МАКС): ' +
    fmtN(c.dropped) + '<br><b>Обмен знанием:</b> от плат подтверждённых ответов ' +
    fmtN(c.pairs_in) + ', кода ' + fmtN(c.code_in) + ' · платам ушло ответов ' +
    fmtN(c.pairs_out) + ' · мыслей облака плате: ' + fmtN(c.thoughts_out) +
    (u.last && u.last.q ? '<br><b>Последний вопрос плате:</b> «' + esc(u.last.q) + '» → ' +
      esc(u.last.board || '?') : '') + '</div>';
  $('#unOn').onchange = async (e) => renderUnify(await api('/api/unify', { body: { unify_on: e.target.checked } }));
  $('#unWait').onchange = async (e) => renderUnify(await api('/api/unify', { body: { unify_wait: +e.target.value || 0 } }));
}

async function renderBridge() {
  const box = $('#brBox');
  const r = await api('/api/bridge/status');
  const b = (r && r.bridge) || {};
  const st = b.state || {};
  // v3.27: сервер в другой сети/за NAT — моста нет, но плата работает
  // мозг-почтой: это не авария, а топология
  const topo = ((b.diagnosis || {}).topology || {}).verdict || '';
  const lnk = (b.link_boards || []);
  const pill = !b.on ? '<span class="pill">выключен</span>' +
      (lnk.length ? ' <span class="pill ok">плата на связи по каналу связи</span>' : '')
    : (b.online ? '<span class="pill ok">плата в сети</span>'
      : (lnk.length
        ? '<span class="pill ok">плата на связи по каналу связи (' + lnk.map(x => x.id).join(', ') + ')</span> ' +
          '<span class="pill">прямого моста нет</span>'
        : (topo === 'nat' || topo === 'other-lan'
          ? '<span class="pill ok">плата на связи по мозг-почте</span> ' +
            '<span class="pill">прямого моста нет: другая сеть</span>'
          : '<span class="pill bad">недоступна</span>')));
  let html = '<h3>Мост к плате</h3>' +
    '<div class="frow"><div class="fgroup" style="flex:2">' +
    '<label>Адрес платы (её веб-хост)</label>' +
    '<input type="text" id="brUrl" value="' + esc(b.url || '') + '" ' +
    'placeholder="http://127.0.0.1:7000"></div>' +
    '<div class="fgroup"><label>&nbsp;</label>' +
    '<button class="btn" id="brSave">Сохранить и проверить</button> ' +
    '<button class="btn ghost" id="brProbe">Проверить сейчас</button> ' +
    '<button class="btn ghost" id="brToggle">' + (b.on ? 'Выключить мост'
      : 'Включить мост') + '</button></div></div>' +
    '<div style="margin:6px 0 10px">' + pill +
    (b.last_err && !b.online ? ' <span class="mini">плата не отвечает: '
      + esc(b.last_err) + '</span>' : '') +
    (b.online && b.active_url && b.active_url !== b.url
      ? ' <span class="mini">мост работает через ' + esc(b.active_url)
        + ' — адрес из настроек не отвечает, плата найдена по мозг-почте'
        + '</span>' : '') + '</div>';
  if (b.online && st.version) {
    html += '<table class="tbl"><tr><th>Версия мозга</th><th>Последняя мысль'
      + '</th><th>Нейроны</th><th>Память</th><th>Работает</th></tr>' +
      '<tr><td><b>' + esc(st.version || '?') + '</b>' +
      (st.device ? '<br><span class="mini">' + esc(st.device) + '</span>' : '') +
      // v3.27: тензорная сеть платы v2.39 — чип «тензор H×76»
      (st.tensor ? '<br><span class="pill" title="тензорная сеть платы: ' +
        esc(String(st.tensor.params || '')) + ' параметров, точность ' +
        (st.tensor.acc != null ? Math.round(st.tensor.acc * 100) + '%' : '—') +
        '">тензор ' + esc(String(st.tensor.H)) + '×' + esc(String(st.tensor.IN)) +
        '</span>' : '')
      + '</td>' +
      '<td style="max-width:320px">' + esc(st.say || 'пока молчит') + '</td>' +
      '<td>' + (st.neurons_online != null ? st.neurons_online + ' / 12'
        : '?') + '</td>' +
      '<td>' + (st.mem_count != null ? st.mem_count + ' заметок' : '?')
      + '</td>' +
      '<td>' + (st.up_sec != null ? fmtUp(st.up_sec) : '?') + '</td></tr>'
      + '</table>' +
      (st.tensor && b.tensor_help ? '<div class="mini" style="margin-top:6px">' +
        'Тензор: облако считает задачи платы — ' +
        (b.tensor_help.on ? (b.tensor_help.alive ? 'на связи' : 'ждёт плату') +
          ', посчитано ' + (b.tensor_help.done || 0) + ', сбоев ' +
          (b.tensor_help.fails || 0) + ', ~' +
          ((b.tensor_help.bench || 0) / 1e6).toFixed(1) + 'M оп/с' +
          (b.tensor_help.last_err ? ' · ' + esc(b.tensor_help.last_err) : '')
          : 'выключено (tensor_help)') + '. В чате: «тензор», «тензор обучить».' +
        '</div>' : '') +
      '<div class="frow" style="margin-top:10px"><div class="fgroup" ' +
      'style="flex:3"><label>Написать плате напрямую (она думает сама, ' +
      'без LLM)</label><input type="text" id="brMsg" ' +
      'placeholder="например: что ты помнишь?"></div>' +
      '<div class="fgroup"><label>&nbsp;</label>' +
      '<button class="btn" id="brSend">Отправить</button></div></div>' +
      '<div id="brRep" class="mini" style="margin-top:6px"></div>' +
      '<div id="brLog" style="margin-top:10px"></div>';
  } else if (b.on) {
    const d = b.diagnosis || {};
    html += '<div class="warnbox"><b>Почему не подключается:</b> '
      + esc(d.cause || ('плата не отвечает на ' + (b.url || ''))) + '<br>'
      + (d.why ? '<span class="mini">Причина: ' + esc(d.why) + '</span><br>' : '')
      + (d.fix ? '<span class="mini">Что делать: ' + esc(d.fix)
        + '</span><br>' : '')
      + '<br>Чек-лист: 1) мозг neuro_12 запущен (python main.py); '
      + '2) его веб-хост живёт на 7000 — открой http://127.0.0.1:7000 в '
      + 'браузере; 3) если плата на другом устройстве — впиши её адрес '
      + '(http://IP:7000) выше.</div>'
      + '<div class="frow" style="margin-bottom:10px"><div class="fgroup">'
      + '<label>&nbsp;</label>'
      + '<button class="btn" id="brFind">Найти плату в сети</button>'
      + '<span id="brFindRep" class="mini" style="margin-left:8px"></span>'
      + '</div></div>';
  }
  box.innerHTML = html;
  const save = $('#brSave');
  if (save) save.onclick = async () => {
    save.disabled = true; save.textContent = '…';
    await api('/api/bridge/config', { body: { url: $('#brUrl').value.trim() } });
    toast('Мост проверен', 'ok');
    renderBridge();
  };
  const prb = $('#brProbe');
  if (prb) prb.onclick = async () => {
    prb.disabled = true; prb.textContent = '…';
    await api('/api/bridge/probe', { body: {} });
    renderBridge();
  };
  const find = $('#brFind');
  if (find) find.onclick = async () => {
    find.disabled = true; find.textContent = 'ищу…';
    const rep = $('#brFindRep');
    if (rep) rep.textContent = 'сканирую 127.0.0.1 и локальную сеть…';
    try {
      const r = await api('/api/bridge/find',
        { body: { apply: true }, timeout: 60000 });
      const f = (r && r.found) || [];
      if (r && r.applied) {
        toast('Плата найдена: ' + r.applied, 'ok');
        renderBridge();
        return;
      }
      if (rep) rep.textContent = f.length
        ? ('нашёл: ' + f.map(x => x.url).join(', '))
        : ('в сети плату не нашёл (просканировано ' + (r.hosts || 0)
          + ' адресов) — мозг запущен? плата в той же сети?');
    } catch (e) {
      if (rep) rep.textContent = 'ошибка поиска: ' + e;
    }
    find.disabled = false; find.textContent = 'Найти плату в сети';
  };
  const tog = $('#brToggle');
  if (tog) tog.onclick = async () => {
    await api('/api/bridge/config', { body: { on: !b.on } });
    renderBridge();
  };
  const send = $('#brSend');
  if (send) send.onclick = async () => {
    const t = $('#brMsg').value.trim();
    if (!t) return;
    send.disabled = true; send.textContent = 'ждём мысль платы…';
    const rep = $('#brRep');
    rep.textContent = 'Плата думает зонной сетью — это до минуты…';
    const rr = await api('/api/bridge/send', { body: { text: t }, timeout: 90000 });
    send.disabled = false; send.textContent = 'Отправить';
    rep.textContent = rr.ok ? ('Плата: ' + (rr.answer || rr.note || 'молчит'))
      : ('Ошибка: ' + rr.error);
    loadBrLog();
  };
  loadBrLog();
}

async function loadBrLog() {
  const el = $('#brLog');
  if (!el) return;
  const r = await api('/api/bridge/messages?after=0');
  const items = (r && r.items) || [];
  if (!items.length) { el.innerHTML = ''; return; }
  let html = '<div class="mini" style="margin-bottom:4px">Лента платы ' +
    '(последние реплики):</div><table class="tbl">' +
    '<tr><th>Кто</th><th>Сообщение</th></tr>';
  items.slice(-10).forEach(m => {
    html += '<tr><td>' + esc(m.role === 'bot' ? 'плата' : m.role) + '</td>' +
      '<td>' + esc((m.text || '').slice(0, 160)) + '</td></tr>';
  });
  el.innerHTML = html + '</table>';
}

function fmtUp(s) {
  s = parseInt(s || 0, 10);
  if (s < 60) return s + ' с';
  if (s < 3600) return Math.floor(s / 60) + ' мин';
  return Math.floor(s / 3600) + ' ч ' + Math.floor((s % 3600) / 60) + ' мин';
}

async function renderBoards() {
  const r = await api('/api/status');
  const box = $('#devBox');
  if (!r.boards || !r.boards.length) {
    box.innerHTML = '<div class="hint">Пока ни одна плата не '
      + 'зарегистрировалась по мозг-почте — это не страшно: мост уже следит '
      + 'за платой выше. Чтобы плата подключалась сама: на ней открой '
      + '«⚙ Настройки» → впиши адрес облака (http://IP:8077) и токен.</div>';
    return;
  }
  let html = '<table class="tbl"><tr><th>Плата</th><th>Адрес</th>' +
    '<th>Статус</th><th></th></tr>';
  r.boards.forEach(b => {
    html += '<tr><td><b>' + esc(b.name || b.id) + '</b><br>' +
      '<span class="mini">' + esc(b.id) + '</span></td>' +
      '<td><span class="mini">' + esc(b.url || 'без адреса') + '</span></td>' +
      '<td><span class="pill ' + (b.online ? 'ok' : 'bad') + '">' +
      (b.online ? 'в сети' : 'молчит') + '</span></td>' +
      '<td><button class="btn ghost" data-ping="' + esc(b.id) + '">проверить' +
      '</button> <button class="btn danger" data-del="' + esc(b.id) +
      '">убрать</button></td></tr>';
  });
  html += '</table>';
  box.innerHTML = html;
  $$('#devBox [data-ping]').forEach(btn => btn.onclick = async () => {
    btn.disabled = true; btn.textContent = '…';
    const rp = await api('/api/boards/ping', { body: { id: btn.dataset.ping } });
    btn.disabled = false; btn.textContent = 'проверить';
    toast(rp.ok ? ('Связь есть: ' + rp.ms + ' мс')
      : ('Не отвечает: ' + rp.error), rp.ok ? 'ok' : 'err');
  });
  $$('#devBox [data-del]').forEach(btn => btn.onclick = async () => {
    if (!confirm('Убрать плату из реестра?')) return;
    await api('/api/brains/delete', { body: { id: btn.dataset.del } });
    renderBoards();
  });
}

/* -------------------------- Облачная память ------------------------------ */
async function tabMemory() {
  const p = $('[data-pane="memory"]');
  p.innerHTML = '<h2>Облачная память</h2><div class="hint">Каждая плата ' +
    'выгружает сюда бандл памяти (POST /api/memory/save: реплики, чат, состояние и ' +
    'память наблюдений тензорной сети) и забирает на ' +
    'чистую плату (GET /api/memory/load). «Забыть всё» удаляет бандл ' +
    'устройства.</div><div id="memBox"><div class="typing"><i></i><i></i>' +
    '<i></i></div></div>';
  const r = await api('/api/memory/list');
  const box = $('#memBox');
  if (!r.devices || !r.devices.length) {
    box.innerHTML = '<div class="warnbox">Память пуста: платы ещё не ' +
      'синхронизировались.</div>';
    return;
  }
  let html = '<table class="tbl"><tr><th>Устройство</th><th>Реплик</th>' +
    '<th>Сообщений</th><th>Наблюдений сети</th><th>Размер</th><th>Обновлено</th><th></th></tr>';
  r.devices.forEach(d => {
    html += '<tr><td><b>' + esc(d.device) + '</b></td>' +
      '<td>' + d.memory + '</td><td>' + d.chat + '</td>' +
      '<td>' + (d.tn_samples || 0) + (d.tn_trains ? ' <span class="mini">(обучений ' + d.tn_trains +
        (d.tn_acc != null ? ', точность ' + Math.round(d.tn_acc * 100) + '%' : '') + ')</span>' : '') + '</td>' +
      '<td>' + Math.round(d.bytes / 1024) + ' КБ</td>' +
      '<td><span class="mini">' + new Date((d.ts || 0) * 1000)
        .toLocaleString('ru') + '</span></td>' +
      '<td><button class="btn danger" data-wipe="' + esc(d.device) +
      '">забыть всё</button></td></tr>';
  });
  html += '</table>';
  box.innerHTML = html;
  $$('#memBox [data-wipe]').forEach(btn => btn.onclick = async () => {
    if (!confirm('Стереть память «' + btn.dataset.wipe + '» в облаке?')) return;
    await api('/api/memory/delete', { body: { device: btn.dataset.wipe } });
    toast('Память стёрта', 'ok');
    tabMemory();
  });
}

/* --------------------------- База знаний --------------------------------- */
/* v3.28: загрузка с прогрессом — fetch не сообщает, сколько уже отправлено */
function uploadWithProgress(path, fd, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', path);
    x.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    x.upload.onload = () => onProgress(1);
    x.onload = () => {
      if (x.status === 401) { location.href = '/login'; reject(new Error('401')); return; }
      try { resolve(JSON.parse(x.responseText)); }
      catch (e) { reject(new Error('ответ сервера не JSON (HTTP ' + x.status + ')')); }
    };
    x.onerror = () => reject(new Error('сеть недоступна'));
    x.send(fd);
  });
}

const fmtN = (n) => Number(n || 0).toLocaleString('ru');
function fmtSize(b) {
  if (b >= 1048576) return (b / 1048576).toFixed(1) + ' МБ';
  return Math.max(1, Math.round(b / 1024)) + ' КБ';
}

/* v3.28: сводка «сколько прочитано» + полосы ссылок, что читаются сейчас */
async function drawUplStats() {
  const box = $('#uplStats');
  if (!box) return false;
  let s;
  try { s = await api('/api/kb/stats'); } catch (e) { return false; }
  if (!s || !s.ok) { box.textContent = 'Сводка недоступна.'; return false; }
  const tile = (cls, lbl, n, unit, chars, sub) =>
    '<div class="upl-tile ' + cls + '"><div class="lbl">' + lbl + '</div>' +
    '<div class="num">' + fmtN(n) + '</div><div class="sub">' + unit +
    ' · <b>' + fmtN(chars) + '</b> знаков' + (sub ? '<br>' + sub : '') + '</div></div>';
  box.innerHTML =
    tile('acc', '🔗 Страниц прочитано по ссылкам', s.pages.n, 'страниц', s.pages.chars) +
    tile('acc', '📄 Документов прочитано', s.docs.n, 'документов', s.docs.chars,
      '<span class="mini">файлы из панели, МАКС, плат, со страниц и вложения</span>') +
    tile('', '📚 Всего в базе', s.total.n, 'статей', s.total.chars,
      '<span class="mini">ещё ' + fmtN(s.other.n) + ' — Википедия, сочинения, заметки</span>');
  const rd = $('#uplReading');
  const reading = s.reading || [];
  if (rd) {
    rd.innerHTML = reading.length ? reading.map(x => {
      const pct = x.total ? Math.min(100, Math.round(100 * x.done / x.total)) : 0;
      return '<div class="upl-item"><div class="row1"><span>🔗 ' + esc(x.title) +
        '</span><span class="st">' + esc(x.what) + ': ' + fmtN(x.done) + ' / ' +
        (x.total ? fmtN(x.total) : '…') + ' · ' + fmtN(x.chars) + ' зн.</span></div>' +
        '<div class="pbar' + (x.total ? '' : ' busy') + '"><i style="width:' + pct +
        '%"></i></div></div>';
    }).join('') : '';
  }
  if (!s.index.ready) {
    box.insertAdjacentHTML('beforeend', '<div class="mini" style="grid-column:1/-1">' +
      'Индекс базы ещё строится: в индексе ' + fmtN(s.index.alive) + ', ждут разбора ' +
      fmtN(s.index.backlog) + ' — цифры дорастут сами.</div>');
  }
  return reading.length > 0 || !s.index.ready;
}

/* сводка обновляется сама: идёт чтение — раз в 3 с, тишина — раз в 15 с */
function uplLoop() {
  const gen = (window.__uplGen = (window.__uplGen || 0) + 1);
  const tick = async () => {
    if (gen !== window.__uplGen || !$('#uplStats')) return;   // вкладку закрыли
    const busy = await drawUplStats();
    if (gen === window.__uplGen) setTimeout(tick, busy ? 3000 : 15000);
  };
  setTimeout(tick, 3000);
}

/* v3.28: очередь файлов: по одному, у каждого — полоса отправки и стадия
   «читаю текст», в конце — сколько знаков легло в базу */
async function uploadFiles(files) {
  const list = $('#uplList'), total = $('#uplTotal');
  if (!list || !files.length) return;
  const items = Array.from(files).map(f => {
    const el = document.createElement('div');
    el.className = 'upl-item';
    el.innerHTML = '<div class="row1"><span>📄 ' + esc(f.name) + ' <span class="mini">' +
      fmtSize(f.size) + '</span></span><span class="st">в очереди</span></div>' +
      '<div class="pbar"><i></i></div>';
    list.prepend(el);
    return { f, el };
  });
  const allBytes = items.reduce((a, x) => a + x.f.size, 0) || 1;
  let doneBytes = 0, okN = 0, badN = 0, chars = 0;
  const drawTotal = (cur, frac) => {
    const pct = Math.round(100 * (doneBytes + cur * frac) / allBytes);
    total.innerHTML = '<b>Загрузка: ' + (okN + badN) + ' из ' + items.length +
      ' файлов</b> · прочитано ' + fmtN(chars) + ' знаков' +
      (badN ? ' · <span class="bad">без текста/ошибок: ' + badN + '</span>' : '') +
      '<div class="pbar"><i style="width:' + Math.min(100, pct) + '%"></i></div>';
  };
  for (const it of items) {
    const st = $('.st', it.el), bar = $('.pbar', it.el), fill = $('.pbar>i', it.el);
    if (it.f.size > 64 * 1048576) {
      it.el.classList.add('bad'); st.textContent = 'больше 64 МБ — не принят';
      badN++; doneBytes += it.f.size; drawTotal(0, 0); continue;
    }
    const fd = new FormData();
    fd.append('device', 'владелец');
    fd.append('text', it.f.name);
    fd.append('file', it.f, it.f.name);
    try {
      const rr = await uploadWithProgress('/api/files/upload', fd, (fr) => {
        fill.style.width = Math.round(fr * 100) + '%';
        st.textContent = fr < 1 ? 'отправка ' + Math.round(fr * 100) + '%' : 'читаю текст…';
        if (fr >= 1) bar.classList.add('busy');
        drawTotal(it.f.size, fr);
      });
      bar.classList.remove('busy');
      fill.style.width = '100%';
      if (rr.ok && rr.kb) {
        okN++; chars += rr.chars || 0;
        it.el.classList.add('ok');
        st.textContent = '✓ в базе: ' + fmtN(rr.chars) + ' зн. (' + (rr.kind || '?') + ')' +
          (rr.chars_file > rr.chars ? ' из ' + fmtN(rr.chars_file) : '');
      } else {
        badN++;
        it.el.classList.add('bad');
        st.textContent = rr.ok ? 'текста нет (' + (rr.kind || '?') + ' — скан-картинки?)'
          : 'ошибка: ' + (rr.error || '?');
      }
    } catch (e) {
      bar.classList.remove('busy');
      badN++; it.el.classList.add('bad'); st.textContent = 'ошибка: ' + e.message;
    }
    doneBytes += it.f.size;
    drawTotal(0, 0);
  }
  toast('Загружено: ' + okN + ' из ' + items.length + ', ' + fmtN(chars) + ' знаков — сеть учится',
    badN ? 'err' : 'ok');
  drawUplStats();
}

async function tabKB() {
  const p = $('[data-pane="kb"]');
  p.innerHTML = '<h2>База знаний</h2><div class="hint">Статьи читают все ' +
    'платы (GET /api/kb/list), сюда же попадают файлы. Мозг пишет свои ' +
    'статьи через /api/kb/import — облако учится у мозга.</div>' +
    '<div class="card" style="margin:10px 0 16px">' +
    '<h3 style="margin-top:0">🎓 Обучение всех нейросетей на материалах</h3>' +
    '<div class="hint">Одна кнопка для всех сетей, которые учатся на текстах: сеть материалов облака, своя LLM, ' +
    'сети плат (сеть материалов и зонная сеть), память инструкций из чатов. Как у LLM: проверка до, чистка шума ' +
    '(вопросы и просьбы из чатов, повторы), обучение по плану шагами, проверка после. LLM не сбрасывается.</div>' +
    '<div class="frow" style="gap:10px;flex-wrap:wrap;margin-top:8px">' +
    '<button class="btn" id="learnAllBtn">Обучить все сети на материалах</button>' +
    '<button class="btn" id="curriculumBtn" title="Загрузить 12 базовых статей и запустить учёбу">Программа обучения</button>' +
    '<button class="btn" id="teacherBtn" title="Принять статьи от внешнего учителя (Grok)">Учитель извне</button>' +
    '<label class="hint" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="learnAllFull"> сеть материалов — переучить все статьи заново</label>' +
    '<button class="btn danger" id="delAllMatBtn" style="background:#b8443a;color:#fff">🗑 Удалить все материалы</button>' +
    '</div>' +
    '<div class="hint" id="learnAllSt" style="margin-top:8px"></div>' +
    '</div>' +
    '<div class="card" style="margin:10px 0 16px"><h3 style="margin-top:0">' +
    '📥 Загрузка в базу</h3>' +
    '<div class="upl-stats" id="uplStats"><div class="typing"><i></i><i></i><i></i></div></div>' +
    '<div id="uplReading" class="upl-list" style="margin:0 0 10px"></div>' +
    '<label class="upl-drop" id="uplDrop"><b>Перетащи документы сюда</b> или нажми, ' +
    'чтобы выбрать<br><span class="mini">PDF, DOC, DOCX, RTF, TXT, MD, CSV, XLSX, ' +
    'PPTX, HTML, XML… можно сразу несколько, до 64 МБ каждый. Ссылки на страницы — ' +
    'в блоке «Ссылки с инструкциями» ниже.</span>' +
    '<input type="file" id="uplFiles" multiple hidden accept=".pdf,.doc,.docx,.rtf,' +
    '.txt,.md,.csv,.tsv,.json,.xlsx,.pptx,.html,.htm,.xml,.log,.py,.ino"></label>' +
    '<div class="upl-total" id="uplTotal"></div><div class="upl-list" id="uplList"></div>' +
    '</div>' +
    '<h3 style="margin-top:4px">Ссылки с инструкциями (v3.24.5)</h3>' +
    '<div class="hint">Вставь https-ссылку на инструкцию — облако САМО ' +
    'скачает страницу, вытянет текст и сохранит его в базу знаний (статья ' +
    '«Инструкции: …») и в сеть материалов. v3.24.3: качаются и ФАЙЛЫ с ' +
    'сайта-инструкций (pdf, doc, docx, rtf, txt, csv, xlsx… того же домена, ' +
    'до 20 МБ на файл) — каждый файл отдельной статьёй; прямая ссылка на ' +
    'файл тоже читается. v3.24.5: Confluence-ссылки читаются ЦЕЛИКОМ — ' +
    'ссылка на пространство (…pages.action?key=KEY) читает ВСЕ его страницы ' +
    'в фоне (прогресс ниже), ссылка на страницу (…viewpage.action?pageId=N) ' +
    '— полное тело страницы (вместо ~2000 знаков скриптовой шапки). ' +
    'ВЛОЖЕНИЯ тоже качаются: текстовые файлы (PDF/DOC/DOCX/TXT…) каждой ' +
    'страницы становятся статьями — десятки тысяч вложений читаются за ' +
    'несколько кругов (предел за круг ниже), перечитка качает только ' +
    'обновившиеся файлы. ' +
    'Перечитывается само раз в N часов или кнопкой; дублей нет — статья ' +
    'обновляется. Убрать ссылку — из списка ниже (статьи страницы и её ' +
    'файлов тоже уберутся).</div>' +
    '<div class="frow"><div class="fgroup" style="flex:2"><label>Ссылка (https — страница или файл)</label>' +
    '<input type="text" id="studyUrl" placeholder="https://example.ru/instrukciya или https://example.ru/manual.pdf"></div>' +
    '<button class="btn" id="studyAdd">Добавить и прочитать</button>' +
    '<select id="studyRefresh" title="как часто перечитывать ссылки само">' +
    '<option value="0">без автоперечитки</option>' +
    '<option value="6">каждые 6 ч</option>' +
    '<option value="12">каждые 12 ч</option>' +
    '<option value="24">раз в сутки</option></select></div>' +
    '<div class="frow"><label style="display:flex;gap:6px;align-items:center">' +
    '<input type="checkbox" id="studyFiles"> качать файлы со страниц (PDF/DOC/DOCX/TXT…)</label>' +
    '<div class="fgroup" style="max-width:180px"><label>файлов со страницы (0–50)</label>' +
    '<input type="number" id="studyFilesMax" min="0" max="50" step="1"></div>' +
    '<div class="fgroup" style="max-width:200px"><label style="display:flex;gap:6px;align-items:center">' +
    '<input type="checkbox" id="studyCrawl"> обходить весь раздел сайта</label>' +
    '<input type="number" id="studyCrawlMax" min="0" max="5000" step="50" title="страниц раздела на ссылку (0 — только сама страница)"></div>' +
    '<div class="fgroup" style="max-width:190px"><label>страниц Confluence на ссылку (1–5000)</label>' +
    '<input type="number" id="studyPagesMax" min="1" max="5000" step="10"></div></div>' +
    '<div class="frow"><label style="display:flex;gap:6px;align-items:center">' +
    '<input type="checkbox" id="studyAttach"> качать вложения Confluence (PDF/DOC/DOCX/TXT…)</label>' +
    '<div class="fgroup" style="max-width:220px"><label>вложений за круг (0–20000)</label>' +
    '<input type="number" id="studyAttachMax" min="0" max="20000" step="50"></div></div>' +
    '<div id="studyBox" class="mini" style="margin-top:8px">' +
    '<div class="typing"><i></i><i></i><i></i></div></div>' +
    '<details style="margin-top:8px"><summary><b>Рекомендованные источники</b> — ' +
    'добавить одной кнопкой</summary><div class="hint">Читается сама страница и ' +
    'файлы-инструкции с неё, а затем и ВЕСЬ РАЗДЕЛ сайта (страницы того же адреса ниже по пути, ' +
    'в фоне, до N страниц; Confluence-пространство — целиком). Сайты с защитой от роботов ' +
    '(Anubis, капча — например ALT Linux, ArchWiki, КиберЛенинка) прочитать нельзя: облако скажет об ' +
    'этом в списке. Кнопка «Проверить» покажет, что реально грузится ИЗ ЭТОГО облака. Для ' +
    'вопросов про «Бештау» и ваши системы лучше всего добавить страницу поддержки ' +
    'производителя и свой Confluence — их адреса знаете вы.</div>' +
    '<ul id="recLinks" style="margin:4px 0 0 18px">' + [
      ['https://api.openalex.org/works?filter=language:ru,has_abstract:true', '🔬 Научные статьи на русском — открытая база OpenAlex (рефераты, авторы, журнал, DOI; ~500 за круг)'],
      ['https://export.arxiv.org/api/query?search_query=cat:cs.LG&sortBy=submittedDate&sortOrder=descending', '📐 arXiv: машинное обучение (рефераты с формулами TeX — отсюда мозг учит LaTeX)'],
      ['https://export.arxiv.org/api/query?search_query=cat:math.NA&sortBy=submittedDate&sortOrder=descending', '📐 arXiv: вычислительная математика (формулы)'],
      ['https://export.arxiv.org/api/query?search_query=cat:physics.class-ph&sortBy=submittedDate&sortOrder=descending', '📐 arXiv: классическая физика (формулы)'],
      ['https://export.arxiv.org/api/query?search_query=cat:quant-ph&sortBy=submittedDate&sortOrder=descending', '📐 arXiv: квантовая физика (квантовая механика, информация, формулы)'],
      ['https://export.arxiv.org/api/query?search_query=cat:hep-th&sortBy=submittedDate&sortOrder=descending', '📐 arXiv: теоретическая физика высоких энергий (квантовая теория поля, формулы)'],
      ['https://export.arxiv.org/api/query?search_query=cat:cond-mat.mes-hall&sortBy=submittedDate&sortOrder=descending', '📐 arXiv: мезоскопика и наноструктуры (квантовые точки, транспорт)'],
      ['https://api.openalex.org/works?filter=language:ru,has_abstract:true&search=%D0%BA%D0%B2%D0%B0%D0%BD%D1%82%D0%BE%D0%B2%D0%B0%D1%8F%20%D1%84%D0%B8%D0%B7%D0%B8%D0%BA%D0%B0', '🔬 Квантовая физика на русском — OpenAlex (рефераты)'],
      ['https://ru.wikipedia.org/wiki/Квантовая_механика', '⚛ Википедия (рус.): квантовая механика — со статьёй обходит связанные (формулы TeX → мозг учит LaTeX)'],
      ['https://ru.wikipedia.org/wiki/Классическая_механика', '⚛ Википедия (рус.): классическая механика (формулы)'],
      ['https://ru.wikipedia.org/wiki/Классическая_электродинамика', '⚛ Википедия (рус.): электродинамика (уравнения Максвелла и др.)'],
      ['https://redos.red-soft.ru/base/', 'РЕД ОС — база знаний (весь раздел)'],
      ['https://wiki.astralinux.ru/', 'Astra Linux — справочный центр'],
      ['https://help.ubuntu.ru/wiki/', 'Ubuntu — русская вики'],
      ['https://docs.arduino.cc/', 'Arduino — документация (для платы)'],
      ['https://docs.python.org/3/', 'Python 3 — документация'],
      ['https://elementy.ru/', 'Элементы — популярная наука на русском'],
    ].map(([u, t]) => '<li>' + esc(t) + ' <span class="mini">' + esc(u) + '</span> ' +
      '<button class="btn ghost" data-rec="' + esc(u) + '">добавить</button></li>').join('') +
    '</ul><button class="btn ghost" id="recCheck" style="margin-top:6px">Проверить, что грузятся</button>' +
    '<div id="recCheckOut" class="mini" style="margin-top:4px"></div></details>' +
    '<div class="frow"><div class="fgroup"><label>Заголовок</label>' +
    '<input type="text" id="kbTitle" placeholder="О чём статья"></div>' +
    '<div class="fgroup" style="flex:2"><label>Текст</label>' +
    '<textarea id="kbText" rows="3" placeholder="Содержание…"></textarea>' +
    '</div></div>' +
    '<button class="btn" id="kbAdd">Добавить статью</button> ' +
    '<button class="btn ghost" id="kbFileBtn">📎 Загрузить файлы</button>' +
    '<h3 style="margin-top:18px">Изучить программный код</h3>' +
    '<div class="hint">Вставь код (main.py платы, sketch.ino, свой скрипт) ' +
    'или выбери файл .py/.ino/.c — мозг разберёт его на функции, классы и ' +
    'константы, выучит в сеть материалов и ответит в чате на вопросы ' +
    '«что делает функция …?» / «как работает say_wave?».</div>' +
    '<div class="frow" style="margin-top:8px">' +
    '<div class="fgroup" style="flex:2"><label>Код</label>' +
    '<textarea id="codeText" rows="4" placeholder="# вставь код сюда или выбери файл…"></textarea></div>' +
    '</div>' +
    '<div class="frow"><div class="fgroup"><label>Имя файла</label>' +
    '<input type="text" id="codeName" placeholder="main.py"></div>' +
    '<label class="btn ghost" style="display:inline-block">Выбрать файл кода' +
    '<input type="file" id="codeFile" hidden accept=".py,.ino,.c,.cpp,.h,.js,.ts,.txt"></label>' +
    '<button class="btn" id="codeRead">Изучить код</button></div>' +
    '<div id="codeBox" class="mini" style="margin-top:8px"></div>' +
    '<h3 style="margin-top:18px">Любопытство нейронов</h3>' +
    '<div class="hint">Нейроны сети сами ХОТЯТ ЗНАТЬ: раз в две минуты ' +
    'берут тему (вопросы, на которые я не знал ответа, горячие узлы сети, ' +
    'темы-семена), читают статью Википедии, сохраняют её в базу знаний и ' +
    'выучивают в сеть материалов. Прочитанное уходит коротким дайджестом ' +
    'онлайн-плате. Всё видно в журнале ниже.</div>' +
    '<div class="frow" style="margin-top:8px">' +
    '<div class="fgroup" style="flex:2"><label>Узнать про…</label>' +
    '<input type="text" id="curTopic" placeholder="например: Солнечная система"></div>' +
    '<button class="btn" id="curNow">Узнать сейчас</button>' +
    '<button class="btn ghost" id="curToggle">…</button></div>' +
    '<div id="curBox" class="mini" style="margin-top:8px"></div>' +
    '<h3 style="margin-top:18px">Предположения (гипотезы)</h3>' +
    '<div class="hint">Мозг не боится догадываться: когда точных знаний нет, '
    'он собирает слабые улики (похожие статьи, ассоциации сети, заметки) и '
    'честно предполагает — помечая это как гипотезу, с источником и '
    'уверенностью. Каждая догадка уходит на проверку нейронам-любопыткам. '
    'Ответь «верно» или «неверно» в чате — подтверждённая догадка станет '
    'знанием сети.</div>' +
    '<div id="hypoBox" class="mini" style="margin-top:8px"></div>' +
    '<h3 style="margin-top:18px">Речь из словаря</h3>' +
    '<div class="hint">Заявка владельца: нейросеть СОБИРАЕТ ответы из слов ' +
    'в словаре — только честно, без заготовок. Сеть речи строится по всем ' +
    'выученным материалам, но узлы у неё — ТОЛЬКО словарные слова ' +
    '(ru_top, ru_pos, extra + словари плат): выдуманное слово она сказать ' +
    'физически не может. Нет знаний по теме — честное «не знаю». ' +
    'Автоматических ответов в чате больше нет.</div>' +
    '<div class="frow" style="margin-top:8px">' +
    '<div class="fgroup" style="flex:2"><label>Тема</label>' +
    '<input type="text" id="spTopic" placeholder="например: нейросеть"></div>' +
    '<button class="btn" id="spNow">Собрать ответ</button>' +
    '<button class="btn ghost" id="spRebuild">Пересобрать сеть</button></div>' +
    '<div id="spBox" class="mini" style="margin-top:8px"></div>' +
    '<div id="kbBox" style="margin-top:16px"><div class="typing"><i></i><i>' +
    '</i><i></i></div></div>';
  /* v3.28: панель «Загрузка в базу»: перетаскивание, очередь, сводка */
  const drop = $('#uplDrop'), finp = $('#uplFiles');
  finp.onchange = (ev) => {
    const fs = Array.from(ev.target.files || []); ev.target.value = '';
    uploadFiles(fs);
  };
  ['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, (e) => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(n => drop.addEventListener(n, (e) => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', (e) => {
    const fs = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    if (fs.length) uploadFiles(fs);
  });
  drawUplStats().then(() => uplLoop());
  /* v3.143: обучение всех сетей + удаление материалов */
  const learnSt = $('#learnAllSt');
  const paintLearn = async () => {
    try {
      const s = await api('/api/learn/all');
      const st = (s && s.status) || {};
      if (!learnSt) return;
      const L = st.llm || {};
      const llmLine = L.text ? ('\nСвоя LLM: ' + L.text + (L.why ? '\nПочему долго: ' + L.why : '')) : '';
      learnSt.style.whiteSpace = 'pre-line';
      if (st.running) {
        learnSt.textContent = 'Идёт: ' + (st.phase || '…') +
          (st.total ? ' — ' + (st.done || 0) + ' из ' + st.total : '') + llmLine;
        setTimeout(paintLearn, 1500);
      } else if (st.finished) {
        const res = st.result || {};
        const mn = res.mn || {};
        const acc = t => (t && t.acc != null) ? (Math.round(100 * t.acc) + '% (' + t.hit + ' из ' + t.n + ')') : '—';
        const fullMark = (res.full || mn.full)
          ? ('\nРежим: переучить всё заново (снято из сети ' + (mn.relearn || 0) + ' материалов базы)')
          : '\nРежим: только новые статьи';
        learnSt.textContent = st.ok
          ? ('Готово. Статей в базе: ' + (res.articles || 0) + fullMark +
             '\nПамять ответов: убрано пар «вопрос вместо ответа» — ' + (res.purged_pairs || 0) +
             '\nСеть материалов: забыто вопросов ' + (mn.forgot || 0) + ', в плане ' + (mn.corpus || 0) +
             ', выучено ' + (mn.learned || 0) +
             (mn.skipped ? ', пропущено ' + mn.skipped : '') +
             ', всего материалов ' + (mn.docs || 0) +
             '\nПроверка сети (находит статью по её заголовку): до ' + acc(mn.before) + ' → после ' + acc(mn.after) +
             '\nИнструкции из чатов: новых ' + (res.instr_new || 0) + ', всего ' + (res.instr_total || 0) +
             '\nСвоя LLM: ' + ((res.llm && res.llm.what) || '—') +
             '\nПлатам: новых материалов ' + (res.boards_new || 0) + ' (годных ' + (res.boards_total || 0) + ')' +
             llmLine)
          : ('Ошибка: ' + (st.error || 'неизвестно'));
      } else {
        learnSt.textContent = llmLine.trim();
      }
    } catch (e) { /* ignore */ }
  };
  if ($('#learnAllBtn')) $('#learnAllBtn').onclick = async () => {
    const full = !!($('#learnAllFull') && $('#learnAllFull').checked);
    const msg = full
      ? 'Обучить все сети на материалах базы знаний?\n\n✓ Галочка «переучить все статьи заново» ВКЛЮЧЕНА.\nСеть материалов: старые копии статей из базы будут сняты и выучены снова.\nСвоя LLM НЕ сбрасывается — только дообучение на новом.\nПлатам материалы уйдут для учёбы (не как готовые ответы).'
      : 'Обучить все сети на материалах базы знаний?\n\nСеть материалов: только новые статьи (уже выученные пропускаются).\nЧтобы переучить всё заново — поставьте галочку «переучить все статьи заново».\nСвоя LLM НЕ сбрасывается.';
    if (!confirm(msg)) return;
    learnSt.textContent = full ? 'Запуск (переучить всё заново)…' : 'Запуск…';
    const r0 = await api('/api/learn/all', { body: { full: full } });
    if (r0 && r0.ok === false) {
      toast((r0.error || 'не удалось запустить'), 'err');
      learnSt.textContent = r0.error || '';
      return;
    }
    toast(full ? 'Обучение запущено: переучить всё' : 'Обучение запущено', 'ok');
    paintLearn();
  };
  if ($('#curriculumBtn')) $('#curriculumBtn').onclick = async () => {
    if (!confirm('Загрузить программу обучения (12 базовых статей) и запустить учёбу?\n\nСтатьи попадут в базу знаний, сеть материалов и корпус своей LLM. Платы тоже получат материалы. Уже выученное не сбрасывается.')) return;
    learnSt.textContent = 'Загружаю программу…';
    const r = await api('/api/learn/curriculum', { body: {} });
    if (r && r.ok) {
      toast('Программа загружена: ' + (r.loaded || 0) + ' статей, учёба запущена', 'ok');
      learnSt.textContent = 'Программа: ' + (r.loaded || 0) + ' статей загружено, учёба идёт';
      paintLearn();
    } else {
      toast((r && r.error) || 'не удалось', 'err');
      learnSt.textContent = (r && r.error) || 'ошибка';
    }
  };
  if ($('#teacherBtn')) $('#teacherBtn').onclick = async () => {
    const raw = prompt('Вставь JSON от учителя:\n{"articles":[{"title":"...","text":"..."}],"train":true}');
    if (!raw) return;
    let body;
    try { body = JSON.parse(raw); } catch (e) {
      toast('Не JSON', 'err'); return;
    }
    learnSt.textContent = 'Принимаю от учителя…';
    const r = await api('/api/learn/teacher', { body });
    if (r && r.ok) {
      toast('Принято статей: ' + (r.loaded || 0), 'ok');
      learnSt.textContent = 'Учитель: ' + (r.loaded || 0) + ' статей, учёба ' +
        (r.train && r.train.ok !== false ? 'запущена' : 'не запущена');
      paintLearn();
    } else {
      toast((r && r.error) || 'не удалось', 'err');
    }
  };
  if ($('#delAllMatBtn')) $('#delAllMatBtn').onclick = async () => {
    if (!confirm('УДАЛИТЬ ВСЕ МАТЕРИАЛЫ?\n\nБудут удалены все статьи базы знаний и очищена сеть материалов облака.\nВеса LLM и настройки не трогаются.\n\nЭто нельзя отменить.')) return;
    if (!confirm('Точно удалить все материалы?')) return;
    learnSt.textContent = 'Удаляю материалы…';
    const r0 = await api('/api/materials/delete_all', { body: { confirm: true } });
    if (r0 && r0.ok) {
      toast('Удалено статей: ' + (r0.deleted || 0) +
        (r0.mozgnet_cleared ? ', сеть материалов очищена' : ''), 'ok');
      learnSt.textContent = 'Удалено статей: ' + (r0.deleted || 0);
      tabKB();
    } else {
      toast((r0 && r0.error) || 'не удалось удалить', 'err');
      learnSt.textContent = (r0 && r0.error) || 'ошибка';
    }
  };
  paintLearn();
  const r = await api('/api/kb/list?limit=200');
  /* v3.24.2: ссылки-инструкции — список, добавление, перечитка, удаление */
  const studyBox = $('#studyBox');
  const drawStudy = (rr) => {
    if (!rr || !rr.ok) {
      studyBox.textContent = 'Панель ссылок недоступна.';
      return;
    }
    $('#studyRefresh').value = String(rr.refresh_hours || 0);
    const sf = $('#studyFiles'), sfm = $('#studyFilesMax');
    if (sf) sf.checked = rr.study_files !== false;
    if (sfm) sfm.value = String(rr.study_files_max != null ? rr.study_files_max : 20);
    const scc = $('#studyCrawl'), scm = $('#studyCrawlMax');
    if (scc) scc.checked = rr.study_crawl !== false;
    if (scm) scm.value = String(rr.study_crawl_max != null ? rr.study_crawl_max : 300);
    const spm = $('#studyPagesMax');
    if (spm) spm.value = String(rr.study_pages_max != null ? rr.study_pages_max : 1000);
    const sa = $('#studyAttach'), sam = $('#studyAttachMax');
    if (sa) sa.checked = rr.study_attach !== false;
    if (sam) sam.value = String(rr.study_attach_max != null ? rr.study_attach_max : 300);
    const ls = rr.links || [];
    if (!ls.length) {
      studyBox.innerHTML = '<span class="mini">Ссылок пока нет: добавь ' +
        'инструкцию выше — она появится здесь и в базе знаний.</span>';
      return;
    }
    studyBox.innerHTML = '<b>Ссылок читаем: ' + ls.length + '</b><ul ' +
      'style="margin:4px 0 0 18px">' + ls.map((x, i) => {
        const st = x.ok
          ? '<span class="ok">' + (x.chars || 0) + ' зн. в KB</span>'
          : (x.reading && !x.err ? '<span class="mini">' + esc(x.note || 'читается…') + '</span>'
            : '<span class="bad">' + esc((x.err || 'не прочитана').slice(0, 80)) + '</span>');
        let files = '';
        if (x.kind === 'file') files = '<span class="mini">[файл]</span>';
        if (x.kind === 'confluence') files = '<span class="mini">[Confluence' +
          (x.space ? ' ' + esc(x.space) : '') + ']</span>';
        if (x.reading) {
          if (x.attach_pages) {
            files += ' <span class="ok">читается… вложения: стр. ' +
              (x.attach_page || 0) + '/' + x.attach_pages + ', прочитано ' +
              (x.attach_read || 0) + ' (' + (x.attach_chars || 0) + ' зн.)</span>';
          } else {
            files += ' <span class="ok">читается… ' + (x.pages_read || 0) +
              ' из ' + (x.pages_total || '…') + ' стр. (' + (x.chars || 0) + ' зн.)</span>';
          }
        } else if (x.kind === 'confluence' && (x.pages_total || x.note)) {
          files += ' <span class="mini">страниц: ' + (x.pages_read || 0) +
            '/' + (x.pages_total || 0) +
            (x.pages_bad ? ', <span class="bad">не прочитано ' + x.pages_bad + '</span>' : '') +
            '</span>';
          if (x.attach_read != null) {
            files += ' <span class="mini">вложений прочитано: ' +
              (x.attach_read || 0) +
              (x.attach_chars ? ' (' + x.attach_chars + ' зн.)' : '') +
              (x.attach_bad ? ', <span class="bad">ошибок ' + x.attach_bad + '</span>' : '') +
              '</span>';
          }
        }
        if (x.files && x.files.length) {
          const fok = x.files.filter(f => f.ok).length;
          const fbad = x.files.length - fok;
          files += ' <span class="mini">файлов: ' + x.files.length +
            (fok ? ' (прочитано ' + fok + ')' : '') +
            (fbad ? ', <span class="bad">не прочитано ' + fbad + '</span>' : '') + '</span>';
        }
        if (x.note && !x.reading) files += ' <span class="mini">' + esc(x.note.slice(0, 120)) + '</span>';
        return '<li><a href="' + esc(x.url) + '" target="_blank" rel="noopener">' +
          esc((x.title || x.url).slice(0, 60)) + '</a> — ' + st + files +
          ' <button class="btn ghost" data-sread="' + i + '">перечитать</button>' +
          ' <button class="btn danger" data-sdel="' + i + '">убрать</button></li>';
      }).join('') + '</ul>';
    /* v3.24.5: пока хоть одна Confluence-ссылка читается в фоне —
       панель сама обновляет прогресс раз в 3 с */
    if (window.__studyTimer) { clearTimeout(window.__studyTimer); window.__studyTimer = null; }
    if (ls.some(x => x.reading)) {
      window.__studyTimer = setTimeout(async () => {
        if (!$('#studyBox')) return;          // вкладку перерисовали
        try { drawStudy(await api('/api/study/links')); } catch (e) {}
      }, 3000);
    }
    $$('#studyBox [data-sread]').forEach(btn => btn.onclick = async () => {
      const x = ls[+btn.dataset.sread];
      toast('Перечитываю «' + (x.title || x.url) + '»…');
      const rz = await api('/api/study/links', { body: { url: x.url, read: true } });
      if (rz.result && rz.result.reading) toast('Читаю в фоне — прогресс в списке и в «Загрузке в базу»', 'ok');
      else if (rz.result && rz.result.ok) toast('Прочитано: ' + rz.result.chars + ' знаков', 'ok');
      else toast('Не прочитал: ' + ((rz.result && rz.result.err) || '?'), 'err');
      drawStudy(rz);
    });
    $$('#studyBox [data-sdel]').forEach(btn => btn.onclick = async () => {
      const x = ls[+btn.dataset.sdel];
      if (!confirm('Убрать ссылку «' + (x.title || x.url) + '» и её статью из базы?')) return;
      const rz = await api('/api/study/links', { body: { url: x.url, remove: true } });
      toast('Ссылка убрана', 'ok');
      drawStudy(rz);
    });
  };
  drawStudy(await api('/api/study/links'));
  $$('#recLinks [data-rec]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const rz = await api('/api/study/links', { body: { url: b.dataset.rec } });
    toast('Добавлено — читаю в фоне', 'ok');
    drawStudy(rz);
  });
  $('#studyAdd').onclick = async () => {
    const url = $('#studyUrl').value.trim();
    if (!url) { toast('Вставь ссылку (https://…)', 'err'); return; }
    toast('Читаю инструкцию…');
    const rz = await api('/api/study/links', { body: { url } });
    $('#studyUrl').value = '';
    if (rz.result && rz.result.reading) {
      toast('Ссылка добавлена — читаю в фоне, прогресс в списке ниже', 'ok');
    } else if (rz.result && rz.result.ok) {
      const fl = (rz.result.files || []).filter(f => f.ok).length;
      toast('Инструкция в базе: ' + rz.result.chars + ' знаков' +
        (fl ? ', файлов прочитано: ' + fl : '') + ', сеть учится', 'ok');
    } else {
      toast('Не прочитал: ' + ((rz.result && rz.result.err) || 'не вышло'), 'err');
    }
    drawStudy(rz);
  };
  $('#studyRefresh').onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { refresh: +ev.target.value || 0 } });
    toast('Автоперечитка: ' + (rz.refresh_hours > 0 ?
      ('раз в ' + rz.refresh_hours + ' ч') : 'выключена'), 'ok');
  };
  const rcb = $('#recCheck');
  if (rcb) rcb.onclick = async () => {
    const urls = $$('#recLinks [data-rec]').map(b => b.dataset.rec);
    rcb.disabled = true; $('#recCheckOut').textContent = 'проверяю ' + urls.length + ' ссылок…';
    try {
      const rz = await api('/api/study/check', { body: { urls } });
      $('#recCheckOut').innerHTML = (rz.results || []).map(x =>
        (x.ok ? '✅' : '❌') + ' ' + esc(x.url.slice(0, 70)) + ' — ' +
        (x.ok ? ('грузится' + (x.links ? ', ссылок раздела: ' + x.links : '')) : esc(x.err || 'не грузится'))).join('<br>');
    } catch (e) { $('#recCheckOut').textContent = 'проверка не удалась'; }
    rcb.disabled = false;
  };
  const scCb = $('#studyCrawl'), scNum = $('#studyCrawlMax');
  if (scCb) scCb.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_crawl: ev.target.checked ? 1 : 0 } });
    toast('Обход раздела сайта: ' + (rz.study_crawl ? 'включён' : 'выключен'), 'ok');
  };
  if (scNum) scNum.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_crawl_max: +ev.target.value || 0 } });
    toast('Страниц раздела на ссылку: до ' + rz.study_crawl_max, 'ok');
  };
  const sfCb = $('#studyFiles'), sfNum = $('#studyFilesMax');
  if (sfCb) sfCb.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_files: ev.target.checked ? 1 : 0 } });
    toast('Файлы со страниц: ' + (rz.study_files ? 'качаю' : 'не качаю'), 'ok');
  };
  if (sfNum) sfNum.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_files_max: +ev.target.value || 0 } });
    toast('Файлов со страницы: до ' + rz.study_files_max, 'ok');
  };
  const spNum = $('#studyPagesMax');
  if (spNum) spNum.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_pages_max: +ev.target.value || 1 } });
    toast('Страниц Confluence на ссылку: до ' + rz.study_pages_max, 'ok');
  };
  const saCb = $('#studyAttach'), samNum = $('#studyAttachMax');
  if (saCb) saCb.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_attach: ev.target.checked ? 1 : 0 } });
    toast('Вложения Confluence: ' + (rz.study_attach ? 'качаю' : 'не качаю'), 'ok');
  };
  if (samNum) samNum.onchange = async (ev) => {
    const rz = await api('/api/study/links', { body: { study_attach_max: +ev.target.value || 0 } });
    toast('Вложений за круг: до ' + rz.study_attach_max, 'ok');
  };
  const codeR = await api('/api/code/list');
  const codeBox = $('#codeBox');
  if (codeR.files && codeR.files.length) {
    codeBox.innerHTML = 'Изучено файлов кода: ' + codeR.files.length + ' — ' +
      codeR.files.map(f => esc(f.name) + ' (' + f.lang + ', ' + f.functions +
        ' фн.)').join('; ');
  } else {
    codeBox.textContent = 'Код пока не изучался: вставь выше main.py или ' +
      'sketch.ino — и спроси в чате, как работает любая функция.';
  }
  $('#codeFile').onchange = (ev) => {
    const f = ev.target.files[0]; ev.target.value = '';
    if (!f) return;
    $('#codeName').value = f.name;
    const rd = new FileReader();
    rd.onload = () => { $('#codeText').value = String(rd.result || ''); };
    rd.readAsText(f);
  };
  $('#codeRead').onclick = async () => {
    const text = $('#codeText').value;
    const name = $('#codeName').value.trim() || 'код.py';
    if (!text.trim()) { toast('Вставь код или выбери файл', 'err'); return; }
    toast('Разбираю код…');
    const rr = await api('/api/code/read', { body: { name, text } });
    if (rr.ok) {
      toast('Изучено: ' + rr.lang + ', функций ' + rr.functions +
        ', классов ' + rr.classes + ', констант ' + rr.defines, 'ok');
      codeBox.textContent = 'Изучен файл ' + esc(rr.name) + ' (' + esc(rr.lang) +
        '): функций ' + rr.functions + ', классов ' + rr.classes +
        ', констант ' + rr.defines + '. Спроси в чате: «что делает функция …?»';
    } else {
      toast('Ошибка: ' + (rr.error || 'не разобрал'), 'err');
    }
  };
  /* v3.10: любопытство нейронов — статус, выключатель, «узнать сейчас» */
  const curBox = $('#curBox');
  const curToggle = $('#curToggle');
  const drawCuriosity = (c) => {
    if (!c || typeof c.on === 'undefined') {
      curBox.textContent = 'Любопытство недоступно.';
      return;
    }
    const state = c.on
      ? 'включено' + (c.web_on ? '' : ', НО интернет выключен (web_on)')
      : 'выключено';
    let html = '<b>Состояние:</b> ' + esc(state) + ' · прочитано статей: ' +
      (c.read || 0) + ' · неудач: ' + (c.failed || 0) +
      ' · раз в ' + (c.every || 120) + ' c' +
      (c.backoff ? ' · <span class="warnbox">пауза ' + c.backoff +
        ' c после сбоя</span>' : '');
    if (c.boards && c.boards.read) {
      html += '<br><b>Мысли плат:</b> прочитано ' + c.boards.read + ', связок «мысль ↔ материал» ' +
        (c.boards.links || 0) + (c.boards.interest && c.boards.interest.length ?
        ' · сейчас платы думают о: ' + c.boards.interest.slice(0, 8).map(q => esc(q)).join(', ') : '');
    }
    if (c.queue && c.queue.length) {
      html += '<br><b>Хочу знать:</b> ' +
        c.queue.slice(0, 6).map(q => esc(q)).join(', ');
    }
    if (c.journal && c.journal.length) {
      html += '<br><b>Журнал чтений:</b><ul style="margin:4px 0 0 18px">' +
        c.journal.slice(0, 8).map(j => {
          const what = j.ok
            ? '<b>' + esc((j.title || j.topic || '?').slice(0, 50)) + '</b> — ' +
              (j.chars || 0) + ' зн., в KB и сеть'
            : esc((j.title || j.topic || '?').slice(0, 50)) + ' — ' +
              esc((j.note || 'не прочитано').slice(0, 90));
          return '<li>' + what + '</li>';
        }).join('') + '</ul>';
    } else {
      html += '<br>Журнал пуст: нейроны ещё ни одной статьи не читали ' +
        '(первая — в течение пары минут после включения).';
    }
    curBox.innerHTML = html;
    curToggle.textContent = c.on ? 'Выключить любопытство'
                                 : 'Включить любопытство';
  };
  drawCuriosity((await api('/api/curiosity')).curiosity);
  curToggle.onclick = async () => {
    const c = (await api('/api/curiosity')).curiosity;
    const rr = await api('/api/curiosity', { body: { action: c.on ? 'off' : 'on' } });
    toast(rr.curiosity && rr.curiosity.on ? 'Любопытство включено' :
      'Любопытство выключено', 'ok');
    drawCuriosity(rr.curiosity);
  };
  $('#curNow').onclick = async () => {
    const q = $('#curTopic').value.trim();
    if (!q) { toast('Напиши тему: «узнать про …»', 'err'); return; }
    toast('Читаю статью про ' + q + '…');
    const rr = await api('/api/curiosity', { body: { action: 'now', q } });
    if (rr.ok) {
      toast('Прочитал «' + rr.title + '» (' + rr.chars + ' знаков) — в KB и сети', 'ok');
    } else {
      toast('Не прочитал: ' + (rr.note || 'не нашлось'), 'err');
    }
    drawCuriosity(rr.curiosity);
  };
  /* v3.11: смелость предположений — счётчики, последние гипотезы, вердикты */
  const hypoBox = $('#hypoBox');
  const drawHypo = (h) => {
    if (!h || typeof h.on === 'undefined') {
      hypoBox.textContent = 'Панель предположений недоступна.';
      return;
    }
    let html = '<b>Состояние:</b> ' +
      (h.on ? 'смелость включена' : 'выключена (hypo_on в Общих)') +
      ' · предположений сделано: ' + (h.made || 0) +
      ' · подтверждено: ' + (h.confirmed || 0) +
      ' · опровергнуто: ' + (h.denied || 0) +
      ' · ждёт проверки: ' + (h.waiting || 0);
    const lst = h.list || [];
    if (lst.length) {
      html += '<ul style="margin:4px 0 0 18px">' + lst.slice(0, 8).map(x => {
        const st = x.status || 'ждёт проверки';
        const cls = st.indexOf('подтвержд') === 0 ? 'ok' :
          (st.indexOf('опроверг') === 0 ? 'bad' : '');
        return '<li><b>' + esc((x.topic || '?').slice(0, 60)) + '</b> — ' +
          'уверенность ' + esc(x.conf >= 0.62 ? 'средняя' : 'низкая') +
          ', <span class="' + cls + '">' + esc(st) + '</span>' +
          '<br><span class="mini">' + esc((x.text || '').slice(0, 110)) +
          '</span></li>';
      }).join('') + '</ul>';
      const wait = lst.find(x => (x.status || '') === 'ждёт проверки');
      if (wait) {
        html += '<div class="frow" style="margin-top:6px">' +
          '<span class="mini">Последняя догадка по «' +
          esc(wait.topic.slice(0, 40)) + '» верна?</span>' +
          '<button class="btn" id="hypoYes">Верно</button>' +
          '<button class="btn ghost" id="hypoNo">Неверно</button></div>';
      }
    } else {
      html += '<br>Гипотез пока не было: спроси в чате о чём-нибудь ' +
        'незнакомом — и я предположу, а не промолчу.';
    }
    if (!hypoBox || !hypoBox.isConnected) return;   // вкладку уже перерисовали
    hypoBox.innerHTML = html;
    const yb = $('#hypoYes'), nb = $('#hypoNo');
    if (yb) yb.onclick = async () => {
      const rr = await api('/api/hypo', { body: { action: 'verdict', ok: true } });
      toast(rr.applied ? 'Принял: догадка стала знанием сети' : 'Ждущих гипотез нет',
            rr.applied ? 'ok' : 'err');
      drawHypo(rr.hypo);
    };
    if (nb) nb.onclick = async () => {
      const rr = await api('/api/hypo', { body: { action: 'verdict', ok: false } });
      toast(rr.applied ? 'Принял: тема ушла любопытству дочитать' : 'Ждущих гипотез нет',
            rr.applied ? 'ok' : 'err');
      drawHypo(rr.hypo);
    };
  };
  drawHypo((await api('/api/hypo')).hypo);
  /* v3.12: речь из словаря — состояние сети, сборка фразы по теме */
  const spBox = $('#spBox');
  const drawSpeech = (s) => {
    if (!s || typeof s.words === 'undefined') {
      spBox.textContent = 'Панель речи недоступна.';
      return;
    }
    const state = s.ready ? 'готова говорить' : (s.busy ? 'собирается…' : 'не собрана');
    spBox.innerHTML = '<b>Сеть речи:</b> ' + esc(state) +
      ' · словарных слов в сети: ' + (s.words || 0) +
      ' · связей: ' + (s.links || 0) +
      ' · словарь: ' + (s.vocab || 0) + ' слов' +
      (s.age ? ' · обновлена ' + s.age + ' c назад' : '') +
      (s.errs ? ' · <span class="warnbox">' + esc(s.errs) + '</span>' : '');
  };
  drawSpeech((await api('/api/speech')).speech);
  $('#spRebuild').onclick = async () => {
    toast('Пересобираю сеть речи…');
    const rr = await api('/api/speech', { body: { action: 'rebuild' } });
    if (rr.ok) toast('Сеть речи пересобрана: связей ' + rr.links, 'ok');
    else toast('Ошибка: ' + (rr.error || 'не вышло'), 'err');
    drawSpeech(rr.speech);
  };
  $('#spNow').onclick = async () => {
    const q = $('#spTopic').value.trim();
    if (!q) { toast('Напиши тему для сборки', 'err'); return; }
    toast('Собираю фразу из словаря…');
    const rr = await api('/api/speech', { body: { text: q } });
    if (rr.ok) {
      spBox.innerHTML = '<b>Собрано из слов словаря:</b> «' + esc(rr.phrase) +
        '»<br><span class="mini">слов: ' + rr.info.words +
        ' · связей в сети: ' + rr.info.links + '</span>';
      toast('Фраза собрана', 'ok');
    } else {
      toast('Честно не собралось: ' + (rr.error || 'нет связей'), 'err');
    }
    drawSpeech(rr.speech);
  };
  const box = $('#kbBox');
  if (!r.articles || !r.articles.length) {
    box.innerHTML = '<div class="warnbox">База пуста. Добавь статью или ' +
      'загрузи файл — мозг прочитает её сам (команда платформы «чтение»).</div>';
  } else {
    let html = '<table class="tbl"><tr><th>Статья</th><th>Источник</th>' +
      '<th>Знаков</th><th></th></tr>';
    r.articles.forEach(a => {
      html += '<tr><td><b>' + esc(a.title) + '</b>' +
        (a.device ? ' <span class="mini">· ' + esc(a.device) + '</span>' : '') +
        '</td><td><span class="mini">' + esc(a.source || '') + '</span></td>' +
        '<td>' + (a.chars || 0) + '</td>' +
        '<td><button class="btn danger" data-kdel="' + esc(a.id) +
        '">удалить</button></td></tr>';
    });
    box.innerHTML = html + '</table>';
  }
  $('#kbAdd').onclick = async () => {
    const title = $('#kbTitle').value.trim();
    const text = $('#kbText').value.trim();
    if (!title || !text) { toast('Нужны заголовок и текст', 'err'); return; }
    const rr = await api('/api/kb/import', { body: { title, text,
      source: 'владелец' } });
    if (rr.ok) { toast('Статья добавлена, сеть учится', 'ok'); tabKB(); }
  };
  $('#kbFileBtn').onclick = () => {
    $('#uplFiles').click();
    $('#uplDrop').scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  $$('#kbBox [data-kdel]').forEach(btn => btn.onclick = async () => {
    if (!confirm('Удалить статью?')) return;
    await api('/api/kb/delete', { body: { id: btn.dataset.kdel } });
    tabKB();
  });
}

/* --------------------------- Функции мозга ------------------------------- */
async function tabSkills() {
  const p = $('[data-pane="skills"]');
  p.innerHTML = '<h2>Функции мозга</h2><div class="hint">Реестр функций: ' +
    'платы сами скачивают новые навыки и учатся им. builtin — исполняет ' +
    'облако, knowledge — шаблон-ответ, code — Python run(text, ctx) в ' +
    'песочнице.</div><div id="skBox"><div class="typing"><i></i><i></i>' +
    '<i></i></div></div>';
  const r = await api('/api/skills/full');
  const box = $('#skBox');
  // v3.27: РЕДАКТОР НАВЫКА — создать/изменить, проверить тестами, «мозг,
  // напиши сам»; код — функция run(text, ctx) -> str (песочница облака)
  const SKEL = 'def run(text, ctx=None):\n    # text — фраза пользователя, вернуть строку-ответ\n' +
    '    import re\n    m = re.search(r"-?\\d+", str(text or ""))\n' +
    '    if not m:\n        return "Назови число."\n    n = int(m.group(0))\n' +
    '    return "Квадрат числа %d = %d" % (n, n * n)\n';
  let ed = '<div class="card" style="margin-bottom:14px"><h3 style="margin-top:0">' +
    'Редактор навыка</h3>' +
    '<div class="frow"><div class="fgroup"><label>id (латиница)</label>' +
    '<input type="text" id="skId" placeholder="square"></div>' +
    '<div class="fgroup" style="flex:2"><label>Название</label>' +
    '<input type="text" id="skName" placeholder="Квадрат числа"></div>' +
    '<div class="fgroup"><label>Вид</label><select id="skKind">' +
    '<option value="code">code — Python run(text)</option>' +
    '<option value="knowledge">knowledge — ответ-шаблон</option></select></div></div>' +
    '<div class="frow"><div class="fgroup" style="flex:2"><label>Ключевые слова ' +
    '(через запятую)</label><input type="text" id="skKw" placeholder="квадрат, в квадрате"></div>' +
    '<div class="fgroup" style="flex:3"><label>Описание</label>' +
    '<input type="text" id="skDesc" placeholder="что делает навык"></div></div>' +
    '<div class="fgroup" id="skReplyG" style="display:none"><label>Ответ ({text} — ' +
    'фраза, {name} — название)</label><textarea id="skReply" rows="3"></textarea></div>' +
    '<div class="fgroup" id="skCodeG"><label>Код (Python): def run(text, ctx=None) -> str</label>' +
    '<textarea id="skCode" rows="9" style="font-family:monospace"></textarea></div>' +
    '<div class="fgroup"><label>Тесты: по строке «вход =&gt; ожидаемый кусок ответа»</label>' +
    '<textarea id="skTests" rows="3" placeholder="квадрат 7 => 49"></textarea></div>' +
    '<div class="frow"><div class="fgroup"><button class="btn ghost" id="skSkel">Шаблон кода</button> ' +
    '<button class="btn ghost" id="skTry">Проверить</button> ' +
    '<button class="btn" id="skSave">Сохранить</button> ' +
    '<button class="btn ghost" id="skNew">Очистить</button></div></div>' +
    '<div class="frow"><div class="fgroup" style="flex:3"><label>Мозг, напиши навык сам ' +
    '(самокод из проверенных заготовок + тесты)</label><input type="text" id="skAuto" ' +
    'placeholder="например: конвертер км в мили"></div><div class="fgroup"><label>&nbsp;</label>' +
    '<button class="btn" id="skAutoBtn">Написать</button></div></div>' +
    '<div id="skOut" class="mini" style="white-space:pre-wrap"></div></div>';
  let html = ed + '<table class="tbl"><tr><th>Функция</th><th>Вид</th>' +
    '<th>Ключевые слова</th><th>Изучили</th><th></th></tr>';
  (r.skills || []).forEach(s => {
    html += '<tr><td><b>' + esc(s.name) + '</b> <span class="mini">' +
      esc(s.id) + '</span>' + (s.desc ? '<br><span class="mini">' +
      esc(s.desc) + '</span>' : '') +
      (s.kind === 'knowledge' ? '<br><span class="mini">«' +
        esc((s.reply || '').slice(0, 80)) + '…</span>' : '') +
      (s.kind === 'code' ? '<pre style="margin:6px 0 0"><code>' +
        esc((s.code || '').slice(0, 160)) + '</code></pre>' : '') +
      '</td><td><span class="pill">' + esc(s.kind) + '</span></td>' +
      '<td><span class="mini">' + esc((s.keywords || []).join(', ')) +
      '</span></td>' +
      '<td>' + Object.keys(s.learned_by || {}).length + '</td>' +
      '<td>' + (s.kind !== 'builtin' ? '<button class="btn ghost" data-sedit="' +
        esc(s.id) + '">изменить</button> ' : '') +
      '<button class="btn ghost" data-run="' + esc(s.id) + '">тест</button>' +
      ' <button class="btn danger" data-sdel="' + esc(s.id) + '">удалить</button></td></tr>';
  });
  box.innerHTML = html + '</table><div class="card" style="margin-top:14px">' +
    '<h3 style="margin-top:0">Инструкции ИИ-агента</h3><div id="agRules" class="mini">' +
    'загружаю…</div></div>';
  const byId = {};
  (r.skills || []).forEach(s => { byId[s.id] = s; });
  const kindUI = () => {
    const k = $('#skKind').value;
    $('#skReplyG').style.display = k === 'knowledge' ? '' : 'none';
    $('#skCodeG').style.display = k === 'code' ? '' : 'none';
  };
  $('#skKind').onchange = kindUI;
  const tests = () => ($('#skTests').value || '').split('\n').map(l => l.trim())
    .filter(Boolean).map(l => { const p = l.split('=>'); return [p[0].trim(), (p[1] || '').trim()]; });
  const form = () => ({ id: $('#skId').value.trim(), name: $('#skName').value.trim(),
    kind: $('#skKind').value, keywords: $('#skKw').value, desc: $('#skDesc').value.trim(),
    reply: $('#skReply').value, code: $('#skCode').value, tests: tests() });
  const out = (t, ok) => { $('#skOut').textContent = t; $('#skOut').style.color = ok === false ? '#f87171' : ''; };
  const showTests = rr => (rr.tests || []).map(t => (t.ok ? '✓ ' : '✗ ') + t.in +
    ' → ' + t.got + (t.ok ? '' : '  (ждали: ' + t.want + ')')).join('\n');
  $('#skSkel').onclick = () => { $('#skKind').value = 'code'; kindUI(); $('#skCode').value = SKEL;
    if (!$('#skTests').value) $('#skTests').value = 'квадрат 7 => 49'; };
  $('#skNew').onclick = () => { ['#skId', '#skName', '#skKw', '#skDesc', '#skReply', '#skCode',
    '#skTests'].forEach(q => { $(q).value = ''; }); out(''); };
  $('#skTry').onclick = async () => {
    const f = form();
    if (f.kind !== 'code') { out('Проверка кода — для вида code. Ответ-шаблон проверь кнопкой «тест» после сохранения.'); return; }
    const rr = await api('/api/skills/try', { body: f.tests.length ? { code: f.code, tests: f.tests }
      : { code: f.code, text: prompt('Ввод для проверки:', '') || '' } });
    out((rr.result || rr.error || '') + (rr.tests ? '\n' + showTests(rr) : ''), rr.ok);
  };
  $('#skSave').onclick = async () => {
    const rr = await api('/api/skills/save', { body: form() });
    out(rr.ok ? 'Сохранено: ' + rr.id + ' (версия ' + rr.ver + ')' : 'Не сохранено: ' + rr.error +
      (rr.tests ? '\n' + showTests(rr) : ''), rr.ok);
    if (rr.ok) { toast('Навык сохранён', 'ok'); setTimeout(tabSkills, 600); }
  };
  $('#skAutoBtn').onclick = async () => {
    out('Мозг пишет функцию и прогоняет тесты в песочнице…');
    const rr = await api('/api/skills/autocode', { body: { desc: $('#skAuto').value } });
    out(rr.result || rr.error || '', rr.ok);
    if (rr.ok) setTimeout(tabSkills, 1200);
  };
  $$('#skBox [data-sedit]').forEach(btn => btn.onclick = () => {
    const s = byId[btn.dataset.sedit] || {};
    $('#skId').value = s.id || ''; $('#skName').value = s.name || '';
    $('#skKind').value = s.kind === 'knowledge' ? 'knowledge' : 'code'; kindUI();
    $('#skKw').value = (s.keywords || []).join(', '); $('#skDesc').value = s.desc || '';
    $('#skReply').value = s.reply || ''; $('#skCode').value = s.code || '';
    $('#skTests').value = ''; out('Редактирую «' + (s.name || s.id) + '». Измени и нажми «Сохранить».');
    $('#skId').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
  api('/api/agent/rules').then(ar => {
    const rl = (ar && ar.rules) || {};
    const li = a => '<ol style="margin:4px 0 8px 18px;padding:0">' +
      (a || []).map(x => '<li>' + esc(x) + '</li>').join('') + '</ol>';
    $('#agRules').innerHTML = '<b>Кодинг (режим «⌨ Код»)</b>' + li(rl.coding) +
      '<b>Общение (режим «💬 Разговор»)</b>' + li(rl.talk);
  });
  $$('#skBox [data-run]').forEach(btn => btn.onclick = async () => {
    const q = prompt('Тест функции «' + btn.dataset.run + '», ввод:', '');
    if (q == null) return;
    const rr = await api('/api/skills/' + encodeURIComponent(btn.dataset.run) +
      '/run', { body: { text: q } });
    toast(rr.ok ? (rr.result || 'пусто').slice(0, 160)
      : ('Ошибка: ' + rr.error), rr.ok ? 'ok' : 'err');
  });
  $$('#skBox [data-sdel]').forEach(btn => btn.onclick = async () => {
    if (!confirm('Удалить функцию? Платы её «разучат».'))
      return;
    // v3.27: защищённый маршрут панели (DELETE теперь только с токеном)
    const rr = await api('/api/skills/remove', { body: { id: btn.dataset.sdel } });
    if (!rr.ok) toast('Не удалено: ' + (rr.error || '?'), 'err');
    tabSkills();
  });
}

/* ----------------------------- Зоны мысли -------------------------------- */
async function tabZones() {
  const p = $('[data-pane="zones"]');
  p.innerHTML = '<h2>Зоны мысли</h2><div class="hint">Все зоны мозга ' +
    'облака — каждая зона это отдельный «орган» мышления, бейдж у ответа ' +
    'в чате показывает, кто отвечал. «Проверить все» запускает щупы: ' +
    'зоны получают пробные задания, чат и база знаний при этом ' +
    'не засоряются.</div>' +
    '<button class="btn" id="btnZonesCheck">Проверить все зоны</button>' +
    '<div id="zonesCheck"></div>' +
    '<div id="zonesBox"><div class="typing"><i></i><i></i><i></i></div></div>';
  const r = await api('/api/zones');
  const box = $('#zonesBox');
  let html = '<table class="tbl"><tr><th>Зона</th><th>Состояние</th>' +
    '<th>Ответов</th><th>Как вызвать</th></tr>';
  (r.zones || []).forEach(z => {
    html += '<tr><td><b>' + esc(z.name) + '</b><br><span class="mini">' +
      esc(z.desc) + '</span></td>' +
      '<td><span class="pill ok">' + esc(z.state || 'подключена') + '</span></td>' +
      '<td>' + (z.answers || 0) + '</td>' +
      '<td><span class="mini"><code>' + esc(z.howto) + '</code></span></td></tr>';
  });
  box.innerHTML = html + '</table>';
  $('#btnZonesCheck').onclick = async () => {
    const btn = $('#btnZonesCheck');
    btn.disabled = true; btn.textContent = 'Проверяю…';
    const rc = await api('/api/zones/check', { body: {} });
    btn.disabled = false; btn.textContent = 'Проверить все зоны';
    const ic = x => x === true ? '✅' : (x === false ? '❌' : '⏳');
    let oh = '<table class="tbl" style="margin-top:12px"><tr><th>Щуп</th>' +
      '<th>Итог</th><th>Время</th></tr>';
    (rc.results || []).forEach(x => {
      oh += '<tr><td><b>' + esc(x.name) + '</b></td>' +
        '<td>' + ic(x.ok) + ' <span class="mini">' + esc(x.note) + '</span></td>' +
        '<td><span class="mini">' + x.ms + ' мс</span></td></tr>';
    });
    $('#zonesCheck').innerHTML = oh + '</table>';
  };
}

/* ---------------------------- Учитель LLM -------------------------------- */
async function tabTeacher() {
  const p = $('[data-pane="teacher"]');
  const t = (SETTINGS.settings && SETTINGS.settings.teacher) || {};
  const ins = (SETTINGS.settings && SETTINGS.settings.teacher_instruction) || '';
  p.innerHTML = '<h2>Учитель (LLM-надстройка)</h2>' +
    '<div class="card" style="margin:8px 0 14px"><h3 style="margin-top:0">📝 Инструкция учителя</h3>' +
    '<div class="hint">Напиши, как мозгу отвечать и о чём помнить: тон, темы, правила. ' +
    'Это и есть «мысли учителя»: мозг обращается к инструкции как к совету наставника — ' +
    'плата получает её вместо совета LLM (панель «Учитель» в чате платы), сеть материалов ' +
    'и своя LLM учатся на ней, а в блоке «Мысли» ответа LLM опирается на подходящую часть. ' +
    'Пусто — работает прежний наставник. Плата подтягивает совет по теме раз в несколько минут.</div>' +
    '<div class="fgroup"><textarea id="tInstr" rows="6" maxlength="4000" ' +
    'placeholder="Например: Отвечай коротко и по делу. Про платы говори только то, что есть в базе знаний. Не выдумывай числа.">' +
    esc(ins) + '</textarea></div>' +
    '<button class="btn" id="tInstrSave">Сохранить инструкцию</button> ' +
    '<span class="mini" id="tInstrInfo">' + (ins ? ins.length + ' симв.' : 'не задана') + '</span></div>' +
    '<div class="warnbox">LLM <b>никогда не отвечает в чате</b>. Если ' +
    'включить внешний учитель, он используется ТОЛЬКО для обучения мозга: ' +
    'вердикты фразам буквогенератора, выбор слова-цели диктанта и ' +
    'советы-наставления (purpose: verdict / word_goal / advice), а также ' +
    'тексты уроков экскурсии. Без внешнего LLM работает встроенный ' +
    'учитель — словари и правила.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tOn"' +
    (t.llm_on ? ' checked' : '') + '><label for="tOn">Включить внешний ' +
    'учитель (OpenAI-совместимый API)</label></div>' +
    '<div class="fgroup"><label>URL ядра (…/v1/chat/completions)</label>' +
    '<input type="text" id="tUrl" value="' + esc(t.llm_url || '') +
    '" placeholder="http://127.0.0.1:8000/v1/chat/completions"></div>' +
    '<div class="frow"><div class="fgroup"><label>Модель</label>' +
    '<input type="text" id="tModel" value="' + esc(t.llm_model || '') +
    '" placeholder="qwen2.5:7b"></div>' +
    '<div class="fgroup"><label>Ключ (не обязателен)</label>' +
    '<input type="password" id="tKey" value="' + esc(t.llm_key || '') +
    '" placeholder="sk-…"></div></div>' +
    '<button class="btn" id="tSave">Сохранить</button> ' +
    '<button class="btn ghost" id="tTest">Проверить учителя</button>' +
    '<h2 style="margin-top:18px">Мысли учителя</h2><div class="hint">Мысли своей ' +
    'LLM (🤖), плат (🧩) и облака (☁) — они же уходят на пин D6 плат.</div>' +
    '<div id="tThoughts" class="hint"></div>' +
    '<div id="tourBox" style="margin-top:18px"><div class="typing">' +
    '<i></i><i></i><i></i></div></div>';
  $('#tInstrSave').onclick = async () => {
    const rr = await api('/api/teacher/instruction', { body: { text: $('#tInstr').value } });
    SETTINGS = await api('/api/settings');
    $('#tInstrInfo').textContent = rr && rr.ok ? (rr.len ? rr.len + ' симв. — сохранено' : 'очищена') : 'ошибка';
    toast(rr && rr.ok ? 'Инструкция учителя сохранена' : 'Не удалось сохранить', rr && rr.ok ? 'ok' : 'err');
  };
  $('#tSave').onclick = async () => {
    await api('/api/settings', { body: { teacher: {
      llm_on: $('#tOn').checked,
      llm_url: $('#tUrl').value.trim(),
      llm_model: $('#tModel').value.trim(),
      llm_key: $('#tKey').value.trim() } } });
    SETTINGS = await api('/api/settings');
    toast('Учитель сохранён. LLM по-прежнему не отвечает в чате.', 'ok');
  };
  $('#tTest').onclick = async () => {
    const rr = await api('/api/llm/test', { body: {} });
    toast(rr.ok ? ('Учитель: ' + (rr.reply || '').slice(0, 120))
      : ('Ошибка: ' + rr.error), rr.ok ? 'ok' : 'err');
  };
  api('/api/teacher/thoughts').then(rt => {
    const el = $('#tThoughts');
    if (!el) return;
    const ic = { llm: '🤖', board: '🧩', cloud: '☁' };
    const it = (rt && rt.items) || [];
    el.innerHTML = it.length ? it.slice(0, 25).map(x =>
      '<div>' + (ic[x.kind] || '') + ' <b>' + esc(x.src) + '</b> · ' +
      new Date((x.ts || 0) * 1000).toLocaleTimeString() + ' — ' +
      esc(x.text) + '</div>').join('') : 'Мыслей пока нет.';
  }).catch(() => {});
  /* -------- Экскурсия платы по функциям платформы (12 уроков) -------- */
  const r = await api('/api/teacher/tour', { body: { action: 'status' } });
  const w = r.tour || {};
  const box = $('#tourBox');
  const sent = Object.keys(w.sent || {}).length;
  box.innerHTML =
    '<h2 style="margin-top:8px">Экскурсия платы по платформе</h2>' +
    '<div class="hint">Учитель сам проведёт плату (App Lab) по всем ' +
    'функциям: уроки идут через мозг-почту, а если у платы не настроено ' +
    'облако — через мост напрямую (127.0.0.1:7000): память, письма, база ' +
    'знаний, функции, словари, сеть материалов, Википедия, генератор, ' +
    'зрение, голос, учитель. Слабый ответ платы — урок повторится. ' +
    'Работает и без LLM; с включённым внешним учителем уроки живее.</div>' +
    '<div class="' + (w.on ? 'okbox' : 'warnbox') + '">' +
    (w.on
      ? ('Идёт: урок <b>' + (w.lesson || 0) + ' / ' + (w.total || 12) +
         '</b>' + (w.lesson_title ? ' — «' + esc(w.lesson_title) + '»' : '') +
         (w.board ? ' · плата «' + esc(w.board) + '»' +
          (w.via_bridge ? ' (через мост)' : '') +
          (w.board_online ? ' <b>в сети</b>' : ' (ждём выхода на связь…)')
          : ' · ждём плату') +
         (sent ? ' · отправлено ' + sent + ', ответов ' + (w.answered || 0)
          : ''))
      : (sent
         ? ('Пауза: отправлено ' + sent + ' из ' + (w.total || 12) +
            ', ответов ' + (w.answered || 0) +
            (w.done ? ' — все уроки разошлись' : ''))
         : 'Экскурсия ещё не запускалась')) + '</div>' +
    (w.hint ? '<div class="warnbox">' + esc(w.hint) + '</div>' : '') +
    (w.last_sent
      ? ('<div class="hint">Последний урок: №' + w.last_sent.lesson +
         ' «' + esc(w.last_sent.title || '') + '» → ' +
         esc(w.last_sent.to || '') + ' (' +
         (w.last_sent.src === 'llm' ? 'внешний LLM' : 'встроенный учитель') +
         ')</div>') : '') +
    ((w.log || []).length
      ? ('<details style="margin:8px 0"><summary style="cursor:pointer">' +
         'Журнал экскурсии</summary><div class="hint">' +
         w.log.slice().reverse().map(l =>
           '<div>' + new Date((l.ts || 0) * 1000).toLocaleTimeString() +
           ' — ' + esc(l.event || '') + '</div>').join('') + '</div></details>')
      : '') +
    '<div class="fgroup"><label>Интервал между уроками</label>' +
    '<select id="wIv">' +
    [60, 90, 120, 180, 300].map(s =>
      '<option value="' + s + '"' +
      ((w.interval || 90) === s ? ' selected' : '') + '>' + s + ' сек</option>'
    ).join('') + '</select></div>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">' +
    '<button class="btn" id="wStart">▶ Начать экскурс</button>' +
    (w.on ? '<button class="btn ghost" id="wStop">⏸ Пауза</button>' : '') +
    (sent ? '<button class="btn ghost" id="wReset">↺ Сброс</button>' : '') +
    '</div>';
  const tourApi = async (body) => {
    const rr = await api('/api/teacher/tour', { body });
    toast(rr.tour && rr.tour.on ? 'Экскурсия запущена' : 'Готово', 'ok');
    tabTeacher();
  };
  $('#wStart').onclick = () => tourApi({
    action: 'start', interval: +$('#wIv').value,
    force: sent > 0 });                      // при повторе — начать заново
  const wStop = $('#wStop');
  if (wStop) wStop.onclick = () => tourApi({ action: 'stop' });
  const wReset = $('#wReset');
  if (wReset) wReset.onclick = () => tourApi({ action: 'reset' });
}

/* ------------------------------- Голос ----------------------------------- */
async function tabVoice() {
  const p = $('[data-pane="voice"]');
  const s = (SETTINGS.settings || {});
  p.innerHTML = '<h2>Голос облака</h2><div class="hint">POST /api/tts ' +
    'отдаёт WAV: плате — на динамик D8, браузеру — кнопка «озвучить» под ' +
    'ответами. Если в системе есть espeak — звучит живая речь, иначе ' +
    'встроенный «нейроголос» (работает всегда).</div>' +
    '<div class="fgroup"><label>Голос</label><select id="vSel">' +
    ['neuro|Нейроголос (по умолчанию)', 'male|Нейроголос низкий',
     'female|Нейроголос высокий', 'robot|Робот'].map(v => {
      const [k, n] = v.split('|');
      return '<option value="' + k + '"' +
        (s.tts_voice === k ? ' selected' : '') + '>' + n + '</option>';
    }).join('') + '</select></div>' +
    '<button class="btn" id="vSave">Сохранить</button> ' +
    '<button class="btn ghost" id="vTest">Прослушать</button>';
  $('#vSave').onclick = async () => {
    await api('/api/settings', { body: { tts_voice: $('#vSel').value } });
    SETTINGS = await api('/api/settings');
    toast('Голос сохранён', 'ok');
  };
  $('#vTest').onclick = () => playTTS('Привет! Я облако нейросети мозг-двенадцать. ' +
    'Умею говорить без больших языковых моделей.');
}

/* ---------------------------- Безопасность -------------------------------- */
function tabSecurity() {
  const p = $('[data-pane="security"]');
  p.innerHTML = '<h2>Безопасность</h2>' +
    '<div class="okbox">Веб-панель закрыта паролем. Платы подключаются ' +
    'отдельно — по токену платы (вкладка «Общие»). Пароль хранится ' +
    'только на этой машине (PBKDF2, 240 000 итераций).</div>' +
    '<div class="fgroup"><label>Старый пароль</label>' +
    '<input id="secOld" type="password" autocomplete="current-password"></div>' +
    '<div class="fgroup"><label>Новый пароль (от 4 знаков)</label>' +
    '<input id="secNew" type="password" autocomplete="new-password"></div>' +
    '<div class="fgroup"><label>Повтори новый пароль</label>' +
    '<input id="secNew2" type="password" autocomplete="new-password"></div>' +
    '<button class="btn" id="secSave">Сменить пароль</button>' +
    '<h2 style="margin-top:26px">Сессии</h2>' +
    '<div class="hint">Сессия живёт 30 дней и продлевается при входе. ' +
    '«Выйти везде» сбрасывает вход на всех устройствах, включая это ' +
    'окно.</div>' +
    '<button class="btn danger" id="secLogoutAll">⎋ Выйти на всех ' +
    'устройствах</button>';
  $('#secSave').onclick = async () => {
    const o = $('#secOld').value, n1 = $('#secNew').value,
          n2 = $('#secNew2').value;
    if (n1.length < 4) { toast('Новый пароль слишком короткий', 'err');
      return; }
    if (n1 !== n2) { toast('Новые пароли не совпадают', 'err'); return; }
    const rr = await api('/api/auth/password',
                         { body: { old: o, new: n1 } });
    if (rr.ok) { toast('Пароль изменён', 'ok');
      $('#secOld').value = $('#secNew').value = $('#secNew2').value = ''; }
    else toast(rr.error || 'Не удалось', 'err');
  };
  $('#secLogoutAll').onclick = async () => {
    if (!confirm('Выйти на всех устройствах? Придётся войти заново.'))
      return;
    await api('/api/auth/logout-all', { body: {} });
    location.href = '/login';
  };
}

/* ---------------------------- Макс-бот (v3.23) ---------------------------- */
async function tabMaxbot() {
  if (window.MX_SCAN_TIMER) { clearTimeout(window.MX_SCAN_TIMER); window.MX_SCAN_TIMER = null; }
  const p = $('[data-pane="maxbot"]');
  p.innerHTML = '<h2>Бот в мессенджере МАКС</h2>' +
    '<div id="mxBox" class="hint">Смотрю…</div>' +
    '<div class="okbox">Бот отвечает мозгом облака (без внешнего LLM) и ' +
    'учится по статьям из групп: сообщения длиннее минимума (по умолчанию ' +
    '10 знаков), ссылки на Википедию и присланные документы (docx, doc, ' +
    'rtf, pdf, txt…) он складывает в базу знаний, файлы сохраняет себе. ' +
    'Он ПОМНИТ недавний диалог каждого чата: продолжения («а почему?», ' +
    '«а когда он основан?», «о чём мы говорили?») понимает по контексту. ' +
    'ВАЖНО: чтобы бот читал группу, добавь его в неё и назначь ' +
    '<b>администратором</b> (иначе МАКС не присылает события). В личке ' +
    'отвечает на всё; в группе — только на обращение («бот …», «мозг …», ' +
    '@username, ответ на его сообщение) и команды.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="mxOn"' +
    (SETTINGS.settings && SETTINGS.settings.maxbot_on ? ' checked' : '') +
    '><label for="mxOn">Бот включён (long polling Bot API)</label></div>' +
    '<div class="fgroup"><label>Токен бота МАКС (Bot API)</label>' +
    '<input type="password" id="mxToken" placeholder="вставь токен из ' +
    'платформы МАКС (Чат-боты → ⋮ → Настройки)"></div>' +
    '<div class="fgroup"><label>Минимальная длина статьи из групп ' +
    '(знаков)</label><input type="number" id="mxMin" min="1" ' +
    'max="100000" step="1" value="10"></div>' +
    '<div class="fgroup"><label>Пауза между статьями из одного чата ' +
    '(сек)</label><input type="number" id="mxGap" min="0" max="3600" ' +
    'step="1" value="20"></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="mxMem"' +
    (SETTINGS.settings && SETTINGS.settings.maxbot_memory !== false ?
    ' checked' : '') + '><label for="mxMem">Память диалога: бот помнит ' +
    'последние реплики каждого чата и понимает продолжения («а почему?», ' +
    '«а когда он основан?», «о чём мы говорили?»); «забудь» в чате ' +
    'стирает память диалога</label></div>' +
    '<h3 style="margin-top:14px">Кто отвечает</h3>' +
    '<div class="hint">Отвечают мозг neuro-12 + своя LLM — тот же движок, что в веб-чате (v3.99): база знаний и интернет, ' +
    'а если статьи нет — мозг сам, с опорой на память (сеть материалов, память диалогов, память платы). ' +
    'Приветствие — только в начале диалога; длинный ответ уходит следующим сообщением.</div>' +
    '<h3 style="margin-top:18px">Скан истории чата</h3>' +
    '<div class="hint">Кнопка ниже читает историю чата через официальный ' +
    'метод GET /messages (новейшие первыми, дальше — вглубь) и загружает ' +
    'её в память нейросети: тексты сообщений (от минимума знаков) — ' +
    'статьями в базу знаний, документы (docx, doc, rtf, pdf, txt…) — ' +
    'файлами в data/files/МАКС + статьями. Уже выученное повторно не ' +
    'заучивается (дедуп + водяной знак), свои сообщения бот пропускает. ' +
    'История длинная? Запусти кнопку ещё раз после окончания — скан ' +
    'продолжится глубже. Свои обращения к боту и ответы бота в историю ' +
    'памяти не попадают.</div>' +
    '<div class="fgroup"><label>Чат для скана</label>' +
    '<select id="mxScanChat"></select></div>' +
    '<div class="fgroup"><label>…или ссылка/ID чата (v3.31: GET /chats в МАКС убран — ' +
    'вставь ссылку вида https://web.max.ru/-68414748216062; бот должен быть в чате ' +
    'администратором)</label><input type="text" id="mxScanLink" placeholder="https://web.max.ru/-68414748216062"></div>' +
    '<button class="btn" id="mxScanStart">Скан истории чата</button> ' +
    '<button class="btn danger" id="mxWipe">Стереть выученное из МАКС</button> ' +
    '<button class="btn ghost" id="mxScanStop" style="display:none">' +
    'Остановить скан</button>' +
    '<div id="mxScanBox" class="hint" style="margin-top:8px"></div>' +
    '<button class="btn" id="mxSave">Сохранить и подключить</button> ' +
    '<button class="btn ghost" id="mxProbe">Обновить статус и чаты</button>' +
    '<div id="mxBot" class="hint"></div>' +
    '<div id="mxChats"></div>';
  let r;
  try { r = await api('/api/maxbot/status'); }
  catch (e) { $('#mxBox').textContent = 'не получил статус: ' + e.message; return; }
  const m = (r && r.maxbot) || {};
  const box = $('#mxBox');
  const st = m.stats || {};
  const lastOk = m.last_ok ? new Date(m.last_ok * 1000).toLocaleTimeString() : '—';
  box.innerHTML = 'Состояние: <b>' + (m.enabled ? 'включён' : 'выключен') +
    '</b>, токен: <b>' + (m.token_set ? 'задан' : 'нет') + '</b>, поток: <b>' +
    (m.thread ? 'жив' : 'не запущен') + '</b><br>' +
    'Сообщений: ' + (st.recv || 0) + ', ответов: ' + (st.ans || 0) +
    ', выучено статей: <b>' + (st.learned || 0) + '</b>' +
    ' (повторов пропущено: ' + (st.skipped || 0) + '), документов: <b>' +
    (st.docs || 0) + '</b>, ошибок: ' +
    (st.errors || 0) + '<br>Память диалога: <b>' +
    (m.memory ? 'вкл' : 'выкл') + '</b> (чатов в памяти: ' +
    (m.mem_chats || 0) + ')<br>Последний контакт с API: ' + lastOk +
    ' · хост: <code>' + esc(m.base || '?') + '</code><br>Опрос МАКС: ' +
    (m.leader ? '<b>веду я</b> (процесс ' + m.pid + ')' :
      (m.enabled && m.token_set ? '<b style="color:#d19a1e">этот процесс в резерве</b> — ' +
        esc(m.leader_note || 'опрос ведёт другая копия облака') +
        '. Это защита от двойных ответов: закрой лишние копии облака (Диспетчер задач → python/pythonw)'
        : 'не идёт (бот выключен или нет токена)')) +
    ' · пропущено дублей: ' + (st.skipped || 0);
  if (m.last_error) {
    box.className = 'errbox';
    box.innerHTML += '<br>Последняя ошибка: ' + esc(m.last_error);
  } else box.className = 'okbox';
  if (m.bot && m.bot.name)
    $('#mxBot').innerHTML = 'Бот: <b>' + esc(m.bot.name) + '</b>' +
      (m.bot.username ? ' (@' + esc(m.bot.username) + ')' : '') +
      ' · id ' + esc(String(m.bot.user_id || '?'));
  else $('#mxBot').textContent = 'Бот ещё не подключался к API — ' +
    'сохрани токен и нажми «Обновить статус».';
  const ch = m.chats || [];
  const docs = m.docs_recent || [];
  $('#mxChats').innerHTML =
    ((m.chats_note ? '<div class="hint">' + esc(m.chats_note) +
      ' (МАКС с июня 2026 метод GET /chats убрал).</div>' : '') +
    (docs.length ? ('<h3 style="margin-top:14px">Последние документы ' +
    'из чатов</h3><table class="tbl"><tr><th>Файл</th><th>Вид</th>' +
    '<th>Знаков</th><th>Когда</th></tr>' + docs.map(d => '<tr><td>' +
    esc(d.name || '?') + '</td><td class="mini">' + esc(d.kind || '?') +
    '</td><td class="mini">' + (d.chars || 0) + '</td><td class="mini">' +
    (d.ts ? new Date(d.ts * 1000).toLocaleString() : '—') +
    '</td></tr>').join('') + '</table>') : '') +
    (ch.length ? ('<h3 style="margin-top:14px">Чаты, где меня видели ' +
    '(по событиям)</h3><table class="tbl"><tr><th>Чат</th><th>Тип</th>' +
    '<th>Активность</th></tr>' + ch.map(c => '<tr><td>' +
    esc(c.title || ('чат ' + c.chat_id)) +
    '</td><td class="mini">' + esc(c.type || '?') + '</td><td class="mini">' +
    (c.seen ? new Date(c.seen * 1000).toLocaleString() : '—') + '</td></tr>')
    .join('') + '</table><div class="hint">В группах бот отвечает и ' +
    'учится, только если он администратор чата.</div>') : ''));
  const tok = $('#mxToken');
  const mn = $('#mxMin');
  const gp = $('#mxGap');
  mn.value = m.min_learn || 10;
  gp.value = (m.learn_gap != null ? m.learn_gap : 20);
  if (m.token_set) tok.placeholder = 'токен уже задан — впиши новый, ' +
    'чтобы заменить';
  $('#mxSave').onclick = async () => {
    const body = { maxbot_on: $('#mxOn').checked };
    if (tok.value.trim()) body.maxbot_token = tok.value.trim();
    const mnV = parseInt(mn.value, 10);
    const gpV = parseInt(gp.value, 10);
    if (!isNaN(mnV)) body.maxbot_min_learn = mnV;
    if (!isNaN(gpV)) body.maxbot_learn_gap = gpV;
    if ($('#mxMem')) body.maxbot_memory = $('#mxMem').checked;
    box.className = 'hint'; box.textContent = 'Сохраняю и проверяю токен…';
    const rr = await api('/api/maxbot/config', { body });
    const pr = rr.probe || {};
    if (pr.ok === false) {
      box.className = 'errbox';
      box.textContent = 'МАКС не принял токен: ' + (pr.error || '?');
    } else if (pr.ok === true) {
      box.className = 'okbox';
      box.textContent = 'Подключено! Бот: ' + ((pr.bot || {}).name || '?') +
        ' — ответы и учёба пошли.';
    } else {
      box.className = 'hint';
      box.textContent = 'Настройки сохранены (проверка токена не ' +
        'запускалась — бот выключен).';
    }
    renderTab('maxbot');
  };
  $('#mxProbe').onclick = async () => {
    const rr = await api('/api/maxbot/probe', { body: {} });
    if (!rr.ok) {
      $('#mxBot').className = 'errbox';
      $('#mxBot').textContent = 'Ошибка: ' + (rr.error || '?');
    } else {
      renderTab('maxbot');
    }
  };
  /* ---------- v3.24: скан истории чата ---------- */
  const selScan = $('#mxScanChat');
  selScan.innerHTML = '<option value="">— все известные чаты (' +
    (ch.length || 0) + ') —</option>' +
    ch.map(c => '<option value="' + esc(String(c.chat_id || '')) + '">' +
      esc(c.title || ('чат ' + c.chat_id)) +
      (c.type ? ' · ' + esc(c.type) : '') + '</option>').join('');
  if (!ch.length)
    $('#mxScanBox').textContent = 'Скан ещё не запускался. Чатов пока нет — ' +
      'они появятся, когда бот получит первое сообщение (напиши ему в ' +
      'личку или добавь в группу), либо выбери «все известные чаты», ' +
      'когда они появятся.';
  function fmtT(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  }
  function renderScanBox(s) {
    const box = $('#mxScanBox');
    if (!box) return;
    const bStart = $('#mxScanStart'), bStop = $('#mxScanStop');
    if (s.running) {
      box.className = 'okbox';
      box.innerHTML = '<b>' + (s.auto ? 'Автоскан (фоновый, раз в 15 мин, только свежие страницы) идёт…' : 'Скан идёт…') + '</b> чат: <b>' +
        esc(s.chat_title || s.chat_id || '?') + '</b>' +
        (s.chats_total > 1 ? ' (' + ((s.chats_done || 0) + 1) + ' из ' +
          s.chats_total + ')' : '') + '<br>Страниц новых: ' + (s.page || 0) +
        ' · запросов всего: ' + (s.walk || 0) +
        ' · сообщений: <b>' + (s.msgs || 0) + '</b>' +
        ' (уже выученных пропущено: ' + (s.old || 0) + ')' +
        '<br>Выучено статей: <b>' + (s.learned || 0) + '</b>, повторов: ' +
        (s.dups || 0) + ', документов: <b>' + (s.docs || 0) + '</b>' +
        (s.doc_errs ? ' (не скачалось: ' + s.doc_errs + ')' : '') +
        (s.errors ? ', ошибок: ' + s.errors : '') +
        ' · время: ' + fmtT(s.elapsed);
      bStop.style.display = '';
      /* v3.136: автоскан не мешает ручному — кнопка доступна (он уступит) */
      bStart.disabled = !s.auto;
    } else if (s.finished) {
      box.className = s.errors ? 'errbox' : 'okbox';
      box.innerHTML = '<b>Скан завершён' + (s.stopped ? ' (остановлен)' : '') +
        '</b>' + (s.elapsed ? ' за ' + fmtT(s.elapsed) : '') + ': чатов ' +
        (s.chats_total || 0) + ', страниц новых ' + (s.page || 0) +
        ', сообщений ' + (s.msgs || 0) +
        (s.old ? ' (пропущено уже выученных: ' + s.old + ')' : '') +
        '<br>Выучено статей: <b>' + (s.learned || 0) + '</b>, повторов: ' +
        (s.dups || 0) + ', документов: <b>' + (s.docs || 0) + '</b>' +
        (s.doc_errs ? ' (не скачалось: ' + s.doc_errs + ')' : '') +
        (s.errors ? ', ошибок: ' + s.errors : '') + '.';
      if (s.note) box.innerHTML += '<br>' + esc(s.note);
      if (s.last_error) box.innerHTML += '<br>Последняя ошибка: ' +
        esc(s.last_error);
      bStop.style.display = 'none';
      bStart.disabled = false;
    } else if (!box.textContent) {
      box.className = 'hint';
      box.textContent = 'Скан ещё не запускался. Выбери чат (или все ' +
        'известные) и нажми «Скан истории чата».';
      bStop.style.display = 'none';
      bStart.disabled = false;
    } else {
      bStop.style.display = 'none';
      bStart.disabled = false;
    }
  }
  async function pollScan() {
    try {
      const rr = await api('/api/maxbot/scan');
      const s = (rr && rr.scan) || {};
      if (s.running || s.finished) renderScanBox(s);
      if (window.MX_SCAN_TIMER) clearTimeout(window.MX_SCAN_TIMER);
      if (s.running) window.MX_SCAN_TIMER = setTimeout(pollScan, 2000);
    } catch (e) {
      const b2 = $('#mxScanBox');
      if (b2) { b2.className = 'errbox'; b2.textContent =
        'не получил статус скана: ' + e.message; }
    }
  }
  $('#mxScanStart').onclick = async () => {
    const b2 = $('#mxScanBox');
    b2.className = 'hint';
    b2.textContent = 'Запускаю скан…';
    // v3.31: ссылка на чат -> id (web.max.ru/-68414748216062 -> -68414748216062)
    let cid = selScan.value;
    const lk = $('#mxScanLink').value.trim();
    if (lk) {
      const m = lk.match(/(-?\d{6,})\s*$/);
      if (!m) { b2.className = 'errbox'; b2.textContent = 'В ссылке не вижу ID чата (число в конце адреса)'; return; }
      cid = m[1];
    }
    const rr = await api('/api/maxbot/scan', { body: { chat_id: cid, pages: 4000 } });
    if (!rr || !rr.ok) {
      b2.className = 'errbox';
      b2.textContent = (rr && rr.error) || 'скан не запустился';
      if (rr && rr.scan) renderScanBox(rr.scan);
      return;
    }
    renderScanBox(rr.scan || {});
    if (window.MX_SCAN_TIMER) clearTimeout(window.MX_SCAN_TIMER);
    window.MX_SCAN_TIMER = setTimeout(pollScan, 1500);
  };
  $('#mxWipe').onclick = async () => {
    if (!confirm('Стереть из базы знаний всё, что бот выучил из чатов МАКС (статьи из групп, ' +
      'история, документы)? Файлы в data/files/МАКС останутся. Сканы можно будет повторить.')) return;
    const rr = await api('/api/maxbot/wipe', { body: { what: 'all' } });
    toast(rr.ok ? ('Стёрто статей: ' + rr.removed) : ('Не вышло: ' + (rr.error || '?')),
      rr.ok ? 'ok' : 'err');
    tabMaxbot();
  };
  $('#mxScanStop').onclick = async () => {
    const rr = await api('/api/maxbot/scan', { body: { stop: true } });
    if (rr && rr.scan) renderScanBox(rr.scan);
    if (rr && rr.ok === false && rr.error) {
      const b2 = $('#mxScanBox');
      b2.className = 'hint';
      b2.textContent = rr.error;
    }
  };
  pollScan();     // возобновить показ, если скан уже шёл (или показать итог)
}

/* ---------------------------- Обновление (v3.19) -------------------------- */
function pollVersion(want, box, requireDown) {
  // Ждём, пока облако перезапустится и /api/health отдаст версию.
  // requireDown — версия та же (ручной перезапуск): ждём хотя бы одного
  // «сервер молчит», иначе перезапуск не считаем случившимся.
  let n = 0, sawDown = false;
  const timer = setInterval(async () => {
    n++;
    let v = '';
    try {
      const j = await (await fetch('/api/health')).json();
      v = j.version || '';
    } catch (e) { v = ''; }
    if (!v) sawDown = true;
    box.innerHTML = 'Жду перезапуск… попытка ' + n +
      '. Сейчас отвечает: <b>' + (v ? ('v' + esc(v)) : 'сервер перезапускается') +
      '</b>';
    if (v === want && v && (!requireDown || sawDown)) {
      clearInterval(timer);
      box.className = 'okbox';
      box.innerHTML = 'Готово: облако <b>v' + esc(v) + '</b> работает. ' +
        'Страница обновится сама…';
      setTimeout(() => location.reload(), 1600);
    } else if (n > 90) {
      clearInterval(timer);
      box.className = 'errbox';
      box.textContent = (requireDown && !sawDown) ?
        'Похоже, сервер так и не перезапустился сам. Перезапусти вручную ' +
        '(start.sh или neuro12-cloud.exe).' :
        'Версия не изменилась за ~75 секунд — сервер не перезапустился ' +
        'сам. Данные в порядке, перезапусти вручную (start.sh или ' +
        'neuro12-cloud.exe) — обновление уже записано.';
    }
  }, 800);
}

async function tabUpdate() {
  const p = $('[data-pane="update"]');
  p.innerHTML = '<h2>Обновление облака</h2>' +
    '<div id="upCur" class="hint">Смотрю…</div>' +
    '<div class="okbox">Обновление через zip: скачай пакет новой версии ' +
    'neuro12-cloud и загрузи его здесь. Облако проверит пакет (структура + ' +
    'компиляция кода), сохранит резервную копию в data/backups, заменит код ' +
    'и перезапустится само (~10 секунд). Чаты, база знаний, настройки и ' +
    'платы (каталог data) пакетом не заменяются никогда.</div>' +
    '<div id="upBox"></div>' +
    '<div class="fgroup"><label>zip-пакет новой версии</label>' +
    '<input type="file" id="upFile" accept=".zip"></div>' +
    '<button class="btn" id="upGo">Загрузить и обновить</button> ' +
    '<button class="btn ghost" id="upRestart">Перезапустить сервер</button>' +
    '<h2 style="margin-top:26px">⬇ Скачать облако целиком</h2>' +
    '<div class="hint">Один zip — всё с диска облака: код платформы + база знаний + файлы + чаты + ' +
    'сеть материалов + своя LLM (веса и файлы подкачки Adam) + память ответов + настройки. ' +
    'Установка на Windows: распакуй и запусти <code>install.exe</code> — он сам смотрит, что в архиве и что ' +
    'уже установлено: в пустую установку переносит всё; поверх существующей спросит — добавить новые и более ' +
    'свежие данные (по умолчанию), заменить целиком (с копией) или обновить только код; архивы ' +
    '<code>neuro12-cloud-v*-update.zip</code>, положенные рядом, ставит поверх, если они новее. ' +
    'Запасной путь — <code>install.bat</code>. Архив содержит токены и всю память — поэтому он под ' +
    'ОТДЕЛЬНЫМ паролем выгрузки.</div>' +
    '<div class="frow"><div class="fgroup" style="max-width:260px"><label>Пароль выгрузки</label>' +
    '<input type="password" id="exPw" autocomplete="off"></div>' +
    '<button class="btn" id="exGo">Собрать и скачать</button></div>' +
    '<div id="exBox" class="mini"></div>' +
    '<details style="margin-top:6px"><summary>Сменить пароль выгрузки</summary>' +
    '<div class="frow"><div class="fgroup"><label>Текущий</label><input type="password" id="exOld"></div>' +
    '<div class="fgroup"><label>Новый (от 6 знаков)</label><input type="password" id="exNew"></div>' +
    '<button class="btn ghost" id="exSet">Сменить</button></div></details>' +
    '<h2 style="margin-top:26px">Резервные копии</h2>' +
    '<div class="hint">Копия кода делается перед каждым обновлением. ' +
    'Если новая версия не нравится — верни любую из списка (данные тоже ' +
    'останутся на месте).</div>' +
    '<div id="upBackups"></div>' +
    '<h2 style="margin-top:26px">Терминал</h2>' +
    '<div class="hint">Команды выполняются на машине облака. Вывод появляется ниже.</div>' +
    '<div class="frow"><input id="termCmd" placeholder="команда (ls, dir, pwd…)" style="flex:1">' +
    '<button class="btn" id="termGo">Выполнить</button></div>' +
    '<pre id="termOut" style="background:#111;color:#eee;padding:10px;border-radius:8px;' +
    'max-height:240px;overflow:auto;font-size:12.5px;white-space:pre-wrap"></pre>';
  try {
    const ex = await api('/api/export');
    if (ex.default) $('#exBox').innerHTML = '<span class="warnbox">Пароль выгрузки ещё ' +
      'заводской (PW12354) — смени его ниже.</span>';
  } catch (e) {}
  $('#exGo').onclick = async () => {
    const pw = $('#exPw').value;
    if (!pw) { toast('Введи пароль выгрузки', 'err'); return; }
    $('#exBox').innerHTML = '<div class="pbar busy"><i></i></div> собираю архив (база ' +
      'и сеть могут занимать сотни МБ — подожди)…';
    try {
      const rr = await api('/api/export', { body: { password: pw } });
      if (!rr.ok) { $('#exBox').textContent = 'Не вышло: ' + (rr.error || '?'); return; }
      $('#exBox').innerHTML = '✓ Архив собран: <b>' + esc(rr.name) + '</b> (' +
        fmtSize(rr.size) + ', файлов ' + fmtN(rr.files) + ') — скачивание началось.';
      $('#exPw').value = '';
      location.href = rr.url;
    } catch (e) { $('#exBox').textContent = 'Ошибка: ' + e.message; }
  };
  $('#exSet').onclick = async () => {
    const rr = await api('/api/export', { body: { password: $('#exOld').value,
      new_password: $('#exNew').value } });
    toast(rr.ok ? 'Пароль выгрузки изменён' : ('Не вышло: ' + rr.error), rr.ok ? 'ok' : 'err');
    if (rr.ok) { $('#exOld').value = ''; $('#exNew').value = ''; $('#exBox').textContent = ''; }
  };
  let r;
  try { r = await api('/api/update/status'); }
  catch (e) { $('#upCur').textContent = 'не получил статус: ' + e.message; return; }
  const u = (r && r.update) || {};
  $('#upCur').innerHTML = 'Сейчас установлена <b>v' + esc(u.version || '?') +
    '</b>. Код платформы: <code>' + esc(u.root || '') + '</code>';
  const box = $('#upBox');
  if (u.state) {
    const s = u.state;
    const stat = { applied: 'применён — жду перезапуска',
      'applied-norestart': 'применён (нужен ручной перезапуск)',
      confirmed: 'успешно установлен и подтверждён' }[s.status] || s.status;
    box.className = 'okbox';
    box.innerHTML = 'Последний пакет: <b>' +
      (s.kind === 'rollback' ? 'откат' : 'обновление') + ' v' +
      esc(s.from || '?') + ' → v' + esc(s.to || '?') + '</b>, ' + esc(stat) +
      ', файлов: ' + (s.files || 0) +
      ', копия: <code>' + esc(s.backup || '—') + '</code>';
  }
  const bl = u.backups || [];
  $('#upBackups').innerHTML = bl.length ? ('<table class="tbl"><tr>' +
    '<th>Копия</th><th>Версия</th><th>Когда</th><th></th></tr>' +
    bl.map(b => '<tr><td><code>' + esc(b.name) + '</code></td>' +
      '<td>' + esc(b.ver || '?') + '</td>' +
      '<td class="mini">' + new Date(b.ts * 1000).toLocaleString() + '</td>' +
      '<td><button class="btn ghost" data-bak="' + esc(b.name) +
      '">Вернуть</button></td></tr>').join('') + '</table>')
    : 'Копий пока нет — они появятся после первого обновления.';
  $$('#upBackups button[data-bak]').forEach(btn => {
    btn.onclick = async () => {
      const name = btn.dataset.bak;
      if (!confirm('Вернуть код из копии ' + name + '? Сервер ' +
        'перезапустится, данные останутся как есть.')) return;
      box.className = 'hint';
      box.textContent = 'Возвращаю код из копии…';
      const rr = await api('/api/update/rollback',
                           { body: { backup: name } });
      if (!rr.ok) {
        box.className = 'errbox';
        box.textContent = 'Откат не удался: ' + (rr.error || '?');
        return;
      }
      box.className = 'okbox';
      box.innerHTML = 'Код из копии записан (v' + esc(rr.update.to) +
        '). Жду перезапуска…';
      pollVersion(rr.update.to, box);
    };
  });
  $('#upGo').onclick = async () => {
    const f = $('#upFile').files[0];
    if (!f) { toast('Сначала выбери zip-файл', 'err'); return; }
    if (!confirm('Обновить облако из файла «' + f.name + '»? Данные ' +
      'сохранятся, сервер перезапустится сам.')) return;
    box.className = 'hint';
    box.textContent = 'Загружаю и проверяю пакет…';
    const fd = new FormData();
    fd.append('zip', f, f.name);
    let rr;
    const goBtn = $('#upGo');               /* v3.174.1: без двойной отправки */
    if (goBtn.disabled) return;
    goBtn.disabled = true;
    try { rr = await api('/api/update/upload', { body: fd }); }
    catch (e) {
      box.className = 'errbox';
      box.textContent = 'Ошибка загрузки: ' + e.message;
      goBtn.disabled = false;
      return;
    } finally { if (!rr || !rr.ok) goBtn.disabled = false; }
    if (!rr.ok) {
      box.className = 'errbox';
      box.textContent = 'Пакет отклонён: ' + (rr.error || 'неизвестная ' +
        'ошибка');
      return;
    }
    box.className = 'okbox';
    box.innerHTML = 'Пакет применён: v' + esc(rr.update.from) + ' → <b>v' +
      esc(rr.update.to) + '</b>, файлов: ' + rr.update.files +
      ', копия: <code>' + esc(rr.update.backup) + '</code>. Жду ' +
      'перезапуска…';
    pollVersion(rr.update.to, box);
  };
  $('#upRestart').onclick = async () => {
    if (!confirm('Перезапустить облако сейчас?')) return;
    box.className = 'hint';
    box.textContent = 'Планирую перезапуск…';
    const rr = await api('/api/update/restart', { body: {} });
    if (!rr.ok) {
      box.className = 'errbox';
      box.textContent = 'Автоперезапуск не удался: ' +
        ((rr.restart && rr.restart.how) || 'перезапусти вручную');
      return;
    }
    pollVersion($('#verline').textContent.replace('neuro12-cloud v', ''),
                box, true);
  };
  if ($('#termGo')) $('#termGo').onclick = async () => {
    const cmd = ($('#termCmd').value || '').trim();
    if (!cmd) return;
    const out = $('#termOut');
    out.textContent = 'Выполняю: ' + cmd + '…\n';
    try {
      const r = await api('/api/terminal', { body: { cmd } });
      out.textContent = 'Код: ' + (r && r.code != null ? r.code : '?') + '\n' +
        ((r && r.out) || (r && r.error) || 'нет вывода');
    } catch (e) {
      out.textContent = 'Ошибка: ' + e.message;
    }
  };
}

/* ------------------------------ Журнал ----------------------------------- */
async function tabLog() {
  // v3.176.2: ПОЛНЫЙ журнал (файл data/log.txt), а не 60 строк из памяти:
  // разделы (КОД, LLM-КОД, МАКС, GITHUB, СТАРТ…), поиск, HTTP по галочке,
  // обновление каждые 5 с
  const p = $('[data-pane="log"]');
  p.innerHTML = '<h2>Журнал облака</h2><div class="hint">Все события платформы из файла журнала ' +
    '(data/log.txt): как облако само кодит (КОД, LLM-КОД), чтение репозиториев (GITHUB), МАКС, старт и сторож.</div>' +
    '<div class="frow" style="align-items:center;gap:8px;flex-wrap:wrap">' +
    '<select id="logCat"><option value="">все разделы</option></select>' +
    '<input type="text" id="logQ" placeholder="поиск" style="width:200px">' +
    '<select id="logN"><option>200</option><option selected>400</option><option>1000</option><option>3000</option></select>' +
    '<label class="switch-inline"><input type="checkbox" id="logHttp"> строки HTTP</label>' +
    '<label class="switch-inline"><input type="checkbox" id="logAuto" checked> обновлять</label></div>' +
    '<div id="logBox"><div class="typing"><i></i><i></i><i></i></div></div>';
  let cats = '';
  const load = async () => {
    const qs = new URLSearchParams({ n: $('#logN').value, q: $('#logQ').value.trim(),
      cat: $('#logCat').value, http: $('#logHttp').checked ? '1' : '' });
    const r = await api('/api/brain/log?' + qs.toString());
    const sel = $('#logCat'), list = (r.cats || []).map(c => c.name + ':' + c.n).join('|');
    if (list !== cats) {
      cats = list;
      const cur = sel.value;
      sel.innerHTML = '<option value="">все разделы</option>' + (r.cats || []).map(c =>
        '<option value="' + esc(c.name) + '"' + (c.name === cur ? ' selected' : '') + '>' + esc(c.name) + ' (' + c.n + ')</option>').join('');
    }
    const box = $('#logBox'), pre = box.querySelector('pre');
    const atEnd = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    box.innerHTML = '<pre style="background:var(--side);border:1px solid var(--line);border-radius:12px;padding:14px;' +
      'font-size:11.5px;line-height:1.6;overflow:auto;max-height:62vh;white-space:pre-wrap">' +
      esc((r.log || []).join('\n') || 'пусто') + '</pre>';
    const np = box.querySelector('pre');
    if (atEnd) np.scrollTop = np.scrollHeight;
  };
  ['#logCat', '#logN', '#logHttp'].forEach(id => $(id).onchange = load);
  let qt; $('#logQ').oninput = () => { clearTimeout(qt); qt = setTimeout(load, 350); };
  await load();
  if (window.__logTimer) clearInterval(window.__logTimer);
  window.__logTimer = setInterval(() => {
    if (!$('#logBox') || !document.body.contains($('#logBox'))) { clearInterval(window.__logTimer); return; }
    if ($('#logAuto').checked && !$('#logBox').closest('[hidden]')) load().catch(() => {});
  }, 5000);
}

/* ============================== ЗАПУСК ==================================== */
function closeSidebar() {
  $('#sidebar').classList.remove('open');
  const rm = $('#rightmenu'); if (rm) rm.classList.remove('open');
  $('#sideVeil').classList.remove('on');
}

/* v3.178: ГОЛОСОВОЙ ВВОД В ЧАТЕ. Источник: микрофон этого устройства
 * (встроенный или подключённый — выбирается из списка браузера) или
 * микрофон платы. Звук пишется в WAV 16 кГц прямо в браузере, облако
 * распознаёт его Vosk-ом (/api/hearing/voice) и ставит текст в поле ввода. */
const VOICE = { rec: null, busy: false };
function voiceSrc() { try { return localStorage.getItem('n12_mic_src') || ''; } catch (e) { return ''; } }
function voiceSetSrc(v) { try { localStorage.setItem('n12_mic_src', v || ''); } catch (e) {} }
async function voiceSources() {
  const out = [{ v: '', t: '🎙 Микрофон этого устройства (по умолчанию)' }];
  try {
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      const ds = await navigator.mediaDevices.enumerateDevices();
      ds.filter(d => d.kind === 'audioinput' && d.deviceId && d.deviceId !== 'default').forEach((d, i) =>
        out.push({ v: 'dev:' + d.deviceId, t: '🎧 ' + (d.label || ('Микрофон ' + (i + 1) + ' (разреши доступ, чтобы увидеть имя)')) }));
    }
  } catch (e) {}
  try {
    const r = await api('/api/hearing/live');
    (r.boards || []).filter(b => b.mic).forEach(b => out.push({ v: 'board:' + b.board, t: '📟 Микрофон платы ' + b.board }));
  } catch (e) {}
  return out;
}
function wavEncode(chunks, inRate) {
  let n = 0; chunks.forEach(c => n += c.length);
  const all = new Float32Array(n); let o = 0; chunks.forEach(c => { all.set(c, o); o += c.length; });
  const ratio = inRate / 16000, m = Math.floor(all.length / ratio);
  const buf = new ArrayBuffer(44 + m * 2), dv = new DataView(buf);
  const w = (p, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(p + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); dv.setUint32(4, 36 + m * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, 16000, true); dv.setUint32(28, 32000, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  w(36, 'data'); dv.setUint32(40, m * 2, true);
  for (let i = 0; i < m; i++) {            // среднее по окну — простой антиалиасинг
    const a = Math.floor(i * ratio), b = Math.max(a + 1, Math.floor((i + 1) * ratio));
    let sum = 0; for (let k = a; k < b && k < all.length; k++) sum += all[k];
    const v = Math.max(-1, Math.min(1, sum / (b - a)));
    dv.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  let bin = ''; const u8 = new Uint8Array(buf);
  for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(bin);
}
function voicePut(text) {
  const inp = $('#input');
  inp.value = (inp.value ? inp.value.replace(/\s*$/, ' ') : '') + text;
  inp.dispatchEvent(new Event('input'));
  inp.focus();
}
async function voiceSend(body, btn) {
  VOICE.busy = true; btn.textContent = '⏳';
  try {
    const r = await api('/api/hearing/voice', { body });
    if (r.ok) { voicePut(r.text); toast('🎙 Распознано', 'ok'); }
    else toast('🎙 ' + (r.error || 'не распознано'), 'warn');
  } catch (e) { toast('🎙 ' + e.message, 'err'); }
  VOICE.busy = false; btn.textContent = '🎙'; btn.classList.remove('on');
}
async function voiceClick() {
  const btn = $('#btnVoice');
  if (VOICE.busy) return;
  const src = voiceSrc();
  if (src.indexOf('board:') === 0) {               // микрофон платы: 5 с с её микрофона
    btn.classList.add('on'); toast('🎙 Говори в микрофон платы — пишу 5 с (плата начнёт слать звук за ~5 с)');
    return voiceSend({ board: src.slice(6), sec: 5 }, btn);
  }
  if (VOICE.rec) {                                  // второе нажатие — стоп
    const R = VOICE.rec; VOICE.rec = null; clearTimeout(R.t);
    try { R.proc.disconnect(); R.srcN.disconnect(); } catch (e) {}
    R.stream.getTracks().forEach(t => t.stop());
    try { R.ctx.close(); } catch (e) {}
    const sec = R.chunks.reduce((n, c) => n + c.length, 0) / R.rate;
    if (sec < 0.4) { btn.textContent = '🎙'; btn.classList.remove('on'); toast('🎙 Слишком коротко', 'warn'); return; }
    return voiceSend({ wav: wavEncode(R.chunks, R.rate) }, btn);
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast('🎙 Браузер не даёт микрофон по http с другого адреса. Открой облако через лаунчер/localhost или выбери микрофон платы (правый клик по 🎙)', 'warn');
    return;
  }
  let stream;
  try {
    const dev = src.indexOf('dev:') === 0 ? src.slice(4) : '';
    stream = await navigator.mediaDevices.getUserMedia({ audio: dev ? { deviceId: { exact: dev } } : { echoCancellation: true, noiseSuppression: true } });
  } catch (e) { toast('🎙 Нет доступа к микрофону: ' + e.message, 'err'); return; }
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const srcN = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const R = { stream, ctx, srcN, proc, chunks: [], rate: ctx.sampleRate };
  proc.onaudioprocess = e => { R.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
  srcN.connect(proc); proc.connect(ctx.destination);
  R.t = setTimeout(() => { if (VOICE.rec === R) voiceClick(); }, 30000);   // потолок 30 с
  VOICE.rec = R;
  btn.textContent = '⏺'; btn.classList.add('on');
  toast('🎙 Говори… нажми ⏺ ещё раз, чтобы закончить');
}
async function voicePick(ev) {
  if (ev) ev.preventDefault();
  document.querySelectorAll('.voicepick').forEach(x => x.remove());
  const list = await voiceSources(), cur = voiceSrc();
  const d = document.createElement('div');
  d.className = 'voicepick card';
  d.style.cssText = 'position:fixed;z-index:9999;right:12px;bottom:90px;max-width:92vw;padding:8px;box-shadow:0 6px 24px rgba(0,0,0,.4)';
  d.innerHTML = '<div class="mini" style="margin-bottom:4px"><b>Микрофон для голосового ввода</b></div>' +
    list.map(o => '<div><label style="cursor:pointer"><input type="radio" name="vsrc" value="' + esc(o.v) + '"' + (o.v === cur ? ' checked' : '') + '> ' + esc(o.t) + '</label></div>').join('') +
    '<div style="margin-top:6px"><button class="btn" id="vpClose">Готово</button></div>';
  document.body.appendChild(d);
  d.querySelectorAll('input').forEach(i => i.onchange = () => { voiceSetSrc(i.value); toast('🎙 Источник: ' + list.find(o => o.v === i.value).t); });
  $('#vpClose').onclick = () => d.remove();
}

function boot() {
  startActivityTriangles();
  setRoute(S.route);
  $('#chkLong').checked = S.long;
  const ttsBtn = $('#btnTTS');
  ttsBtn.classList.toggle('on', S.tts);

  $('#btnNewChat').onclick = newChat;
  $('#btnSend').onclick = send;
  $('#btnBurger').onclick = () => {
    $('#sidebar').classList.toggle('open');
    $('#sideVeil').classList.toggle('on');
  };
  $('#sideVeil').onclick = closeSidebar;
  const rm = $('#rightmenu');
  try { if (localStorage.getItem('n12_rmfold') === '1') rm.classList.add('fold'); } catch (e) {}
  $('#btnRmFold').onclick = () => {
    const f = rm.classList.toggle('fold');
    try { localStorage.setItem('n12_rmfold', f ? '1' : '0'); } catch (e) {}
  };
  $('#btnBurger2').onclick = () => {
    rm.classList.toggle('open');
    $('#sideVeil').classList.toggle('on', rm.classList.contains('open'));
  };
  $('#chatSearch').oninput = renderChatList;
  const vb = $('#btnVoice');                       // v3.178: голосовой ввод
  if (vb) {
    vb.onclick = voiceClick;
    vb.oncontextmenu = voicePick;
    let lp = null;
    vb.addEventListener('touchstart', () => { lp = setTimeout(() => { lp = 'done'; voicePick(); }, 650); }, { passive: true });
    vb.addEventListener('touchend', e => { if (lp === 'done') e.preventDefault(); clearTimeout(lp); lp = null; });
  }
  $('#btnTTS').onclick = () => {
    S.tts = !S.tts;
    localStorage.setItem('n12_tts', S.tts ? '1' : '0');
    ttsBtn.classList.toggle('on', S.tts);
    toast(S.tts ? 'Ответы будут озвучиваться' : 'Озвучка выключена');
  };
  $('#btnSettings').onclick = openSettings;
  $('#btnCloseSettings').onclick = closeSettings;
  $('#btnLogout').onclick = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }); }
    catch (e) {}
    location.href = '/login';
  };
  $('#settingsVeil').onclick = (e) => {
    if (e.target === $('#settingsVeil')) closeSettings();
  };
  $$('.mtabs button[data-tab]').forEach(b =>
    b.onclick = () => renderTab(b.dataset.tab));
  $$('#modeBox button').forEach(b =>
    b.onclick = () => setRoute(b.dataset.route));
  $('#chkLong').onchange = (e) => {
    S.long = e.target.checked;
    localStorage.setItem('n12_long', S.long ? '1' : '0');
  };
  $('#btnBackChat').onclick = () => showView('chat');
  $$('.sidenav button[data-view]').forEach(b =>
    b.onclick = () => {
      if (b.dataset.view === 'thoughts') openChat('thoughts');
      else showView(b.dataset.view);
    });

  const ta = $('#input');
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(180, ta.scrollHeight) + 'px';
  });

  refreshChats();
  newChat();
  refreshStatus();
  clearInterval(S.statusTimer);
  S.statusTimer = setInterval(refreshStatus, 15000);
}

document.addEventListener('DOMContentLoaded', boot);

/* v3.21: мост для ui-plus.js */
window.N12 = { S, api, esc, md, toast, send, newChat, openChat, showView,
  openSettings, renderTab, renderChatList, setRoute };


/* ---------- v3.40: бот в Telegram ---------- */
async function tabTgbot() {
  const p = $('[data-pane="tgbot"]');
  const S = (SETTINGS && SETTINGS.settings) || {};
  p.innerHTML = '<h2>Бот в Telegram</h2><div id="tgBox" class="hint">Смотрю…</div>' +
    '<div class="okbox">Бот отвечает мозгом облака, как бот в МАКС: помнит диалог, ' +
    'учится по присланным документам (docx, pdf, txt…) и <b>слушает</b> голосовые, ' +
    'аудио и видео (hear_file). В личке отвечает на всё; в группе — на обращение ' +
    '(«бот …», «мозг …», @username, ответ на его сообщение). Токен: в Telegram ' +
    'написать <b>@BotFather</b> → /newbot. Чтобы бот видел всю группу, отключи ему ' +
    'Group Privacy (/setprivacy → Disable). Если Telegram у тебя закрыт — укажи ' +
    'HTTP(S)-прокси или зеркало API.</div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tgOn"' + (S.tg_on ? ' checked' : '') +
    '><label for="tgOn">Бот включён (long polling)</label></div>' +
    '<div class="fgroup"><label>Токен бота (от @BotFather)</label>' +
    '<input type="password" id="tgToken" placeholder="123456:ABC…"></div>' +
    '<div class="fgroup"><label>HTTP(S)-прокси (если Telegram закрыт), напр. http://127.0.0.1:8080</label>' +
    '<input type="text" id="tgProxy"></div>' +
    '<div class="fgroup"><label>Зеркало Bot API (необязательно, по умолчанию https://api.telegram.org)</label>' +
    '<input type="text" id="tgBase"></div>' +
    '<div class="fgroup"><label>Белый список: id или @username через запятую (пусто — отвечать всем)</label>' +
    '<input type="text" id="tgAllowed"></div>' +
    '<div class="fgroup switch"><input type="checkbox" id="tgMem"' + (S.tg_memory !== false ? ' checked' : '') +
    '><label for="tgMem">Память диалога («а почему?», «о чём мы говорили?»)</label></div>' +
    '<button class="btn" id="tgSave">Сохранить и подключить</button> ' +
    '<button class="btn ghost" id="tgProbe">Обновить статус</button>' +
    '<div id="tgBot" class="hint"></div><div id="tgChats"></div>';
  let r;
  try { r = await api('/api/tgbot/status'); }
  catch (e) { $('#tgBox').textContent = 'не получил статус: ' + e.message; return; }
  const m = (r && r.tgbot) || {}, st = m.stats || {}, box = $('#tgBox');
  const lastOk = m.last_ok ? new Date(m.last_ok * 1000).toLocaleTimeString() : '—';
  box.innerHTML = 'Состояние: <b>' + (m.enabled ? 'включён' : 'выключен') + '</b>, токен: <b>' +
    (m.token_set ? 'задан' : 'нет') + '</b>, поток: <b>' + (m.thread ? 'жив' : 'не запущен') + '</b><br>' +
    'Сообщений: ' + (st.recv || 0) + ', ответов: ' + (st.ans || 0) + ', документов: ' + (st.docs || 0) +
    ', прослушано файлов: <b>' + (st.heard || 0) + '</b>, отказов (белый список): ' + (st.denied || 0) +
    ', ошибок: ' + (st.errors || 0) + '<br>Последний контакт: ' + lastOk + ' · API: <code>' +
    esc(m.api_base || '?') + '</code>' + (m.proxy ? ' · через прокси' : '') + '<br>Опрос: ' +
    (m.leader ? '<b>веду я</b> (процесс ' + m.pid + ')' :
      (m.enabled && m.token_set ? '<b style="color:#d19a1e">этот процесс в резерве</b> — ' +
        esc(m.leader_note || 'опрос ведёт другая копия облака') : 'не идёт'));
  if (m.last_error) { box.className = 'errbox'; box.innerHTML += '<br>Последняя ошибка: ' + esc(m.last_error); }
  else box.className = 'okbox';
  $('#tgProxy').value = S.tg_proxy || ''; $('#tgBase').value = S.tg_api_base || '';
  $('#tgAllowed').value = S.tg_allowed || '';
  if (m.token_set) $('#tgToken').placeholder = 'токен уже задан — впиши новый, чтобы заменить';
  if (m.bot && m.bot.username) $('#tgBot').innerHTML = 'Бот: <b>' + esc(m.bot.name || '') + '</b> (@' +
    esc(m.bot.username) + ')';
  else $('#tgBot').textContent = 'Бот ещё не подключался — сохрани токен.';
  const ch = m.chats || [];
  $('#tgChats').innerHTML = ch.length ? ('<h3 style="margin-top:14px">Чаты</h3><table class="tbl"><tr><th>Чат</th>' +
    '<th>Тип</th><th>Активность</th></tr>' + ch.map(c => '<tr><td>' + esc(c.title || ('чат ' + c.chat_id)) +
    '</td><td class="mini">' + esc(c.type || '?') + '</td><td class="mini">' +
    (c.seen ? new Date(c.seen * 1000).toLocaleString() : '—') + '</td></tr>').join('') + '</table>') : '';
  $('#tgSave').onclick = async () => {
    const body = { tg_on: $('#tgOn').checked, tg_proxy: $('#tgProxy').value.trim(),
      tg_api_base: $('#tgBase').value.trim(), tg_allowed: $('#tgAllowed').value.trim(),
      tg_memory: $('#tgMem').checked };
    if ($('#tgToken').value.trim()) body.tg_token = $('#tgToken').value.trim();
    box.className = 'hint'; box.textContent = 'Сохраняю и проверяю токен…';
    const rr = await api('/api/tgbot/config', { body });
    const pr = rr.probe || {};
    try { SETTINGS = await api('/api/settings'); } catch (e) {}
    if (pr.ok === false) { box.className = 'errbox'; box.textContent = 'Telegram не принял: ' + (pr.error || '?'); return; }
    toast(pr.ok ? 'Подключено: @' + ((pr.bot || {}).username || '?') : 'Сохранено', 'ok');
    renderTab('tgbot');
  };
  $('#tgProbe').onclick = async () => {
    const rr = await api('/api/tgbot/probe', { body: {} });
    if (!rr.ok) { $('#tgBot').className = 'errbox'; $('#tgBot').textContent = 'Ошибка: ' + (rr.error || '?'); }
    else renderTab('tgbot');
  };
}


/* v3.108: КОПИРОВАНИЕ ФОРМУЛ. Формула в окошке — картинка SVG, обычным
   выделением она не копировалась. Кнопка ⧉ копирует LaTeX; при выделении
   текста ответа (Ctrl+C) формула попадает в буфер читаемым текстом. */
(function () {
  const unesc = (s) => { const t = document.createElement('textarea'); t.innerHTML = s || ''; return t.value; };
  // текст ответа для буфера: формула -> её читаемый текст, картинка -> пометка
  window.n12PlainText = (src) => String(src || '')
    .replace(/\n?\[ФОРМУЛА\]([\s\S]*?)\[\/ФОРМУЛА\]\n?/g, (_, b) => {
      const mp = b.match(/data-plain="([^"]*)"/), mt = b.match(/data-tex="([^"]*)"/);
      return ' ' + (mp ? unesc(mp[1]) : (mt ? unesc(mt[1]) : '[формула]')) + ' ';
    })
    .replace(/ +([,.;:])/g, '$1')
    .replace(/\[КАРТИНКА\][\s\S]*?\[\/КАРТИНКА\]/g, '[картинка]')
    .replace(/\[ИЛЛЮСТРАЦИЯ[^\]]*\]([\s\S]*?)\[\/ИЛЛЮСТРАЦИЯ\]/g, '[иллюстрация: $1]')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/\u2063/g, '');
  window.n12CopyText = async (txt) => putText(txt);
  const putText = async (txt) => {
    try { await navigator.clipboard.writeText(txt); return true; } catch (e) { /* http — запасной путь */ }
    const ta = document.createElement('textarea');
    ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    ta.remove(); return ok;
  };
  document.addEventListener('click', async (e) => {
    const b = e.target.closest && e.target.closest('.mcopy');
    if (!b) return;
    const box = b.closest('.mathsvg');
    const tex = unesc(box && box.getAttribute('data-tex'));
    const ok = await putText(tex);
    if (typeof toast === 'function') toast(ok ? 'Формула скопирована (LaTeX): ' + tex : 'Не удалось скопировать', ok ? 'ok' : 'err');
  });
  document.addEventListener('copy', (e) => {
    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    const frag = sel.getRangeAt(0).cloneContents();
    let anc = sel.getRangeAt(0).commonAncestorContainer;
    if (anc && anc.nodeType !== 1) anc = anc.parentElement;
    const inBox = anc && anc.closest && anc.closest('.mathsvg');
    if (!frag.querySelector('.mathsvg, svg, metadata') && !inBox) return;
    const div = document.createElement('div');
    div.appendChild(frag);
    if (inBox && !div.querySelector('.mathsvg')) {          // выделена только формула
      e.clipboardData.setData('text/plain', unesc(inBox.getAttribute('data-plain')));
      e.preventDefault(); return;
    }
    div.querySelectorAll('.mathsvg').forEach(m => {
      m.replaceWith(document.createTextNode(' ' + unesc(m.getAttribute('data-plain') || m.getAttribute('data-tex')) + ' '));
    });
    div.querySelectorAll('svg').forEach(sv => {                // формула, попавшая частично
      const md_ = sv.querySelector('metadata');
      sv.replaceWith(document.createTextNode(md_ ? ' ' + (md_.getAttribute('data-plain') || '') + ' ' : ''));
    });
    div.querySelectorAll('.mcopy').forEach(x => x.remove());
    div.querySelectorAll('br').forEach(x => x.replaceWith('\n'));
    e.clipboardData.setData('text/plain', div.textContent.replace(/[ \t]{2,}/g, ' ').trim());
    e.preventDefault();
  });
})();

/* v3.146: регуляторы силы тензора модуляции (Связь мозгов) */
document.addEventListener('change', async (e) => {
  const el = e.target;
  if (!el || !el.dataset || !el.dataset.tmod) return;
  const v = parseInt(el.value, 10) || 0;
  if (el.nextElementSibling) el.nextElementSibling.textContent = v + '%';
  try {
    await api('/api/link/status', { body: { tmod_k: { [el.dataset.tmod]: v } } });
    toast('Сила «' + el.dataset.tmod + '»: ' + v + '%', 'ok');
  } catch (err) { toast('Не сохранилось: ' + err.message, 'err'); }
});
document.addEventListener('input', (e) => {
  const el = e.target;
  if (el && el.dataset && el.dataset.tmod && el.nextElementSibling)
    el.nextElementSibling.textContent = el.value + '%';
});

async function tabSsh() {
  const p = $('[data-pane="ssh"]');
  p.innerHTML = '<h2>SSH-серверы</h2><p class="hint">Добавляйте серверы для администрирования агентом. Предпочтительнее ключи, чем пароли. Команды логируются.</p>' +
    '<div id="sshList" class="mini">Загрузка…</div>' +
    '<h3 style="margin-top:18px">Добавить сервер</h3>' +
    '<div class="frow"><div class="fgroup"><label>Имя</label><input type="text" id="sshName" placeholder="prod-web"></div>' +
    '<div class="fgroup" style="flex:2"><label>Хост</label><input type="text" id="sshHost" placeholder="1.2.3.4 или example.com"></div></div>' +
    '<div class="frow"><div class="fgroup"><label>Пользователь</label><input type="text" id="sshUser" value="root"></div>' +
    '<div class="fgroup"><label>Порт</label><input type="number" id="sshPort" value="22"></div></div>' +
    '<div class="fgroup"><label>Путь к ключу (рекомендуется)</label><input type="text" id="sshKey" placeholder="/home/user/.ssh/id_ed25519"></div>' +
    '<div class="fgroup"><label>Пароль (только если нет ключа)</label><input type="password" id="sshPass"></div>' +
    '<div class="fgroup"><label>Заметка</label><input type="text" id="sshNote"></div>' +
    '<button class="btn" id="sshAdd">Добавить</button>' +
    '<div id="sshMsg" class="mini" style="margin-top:8px"></div>';

  async function loadList() {
    try {
      const r = await api('/api/ssh/servers');
      const box = $('#sshList');
      if (!r.ok || !r.servers || !r.servers.length) {
        box.innerHTML = '<span class="muted">Серверов пока нет</span>';
        return;
      }
      box.innerHTML = '<table class="tbl"><tr><th>Имя</th><th>Хост</th><th>Пользователь</th><th>Ключ</th><th></th></tr>' +
        r.servers.map(s => '<tr><td>' + esc(s.name) + '</td><td>' + esc(s.host) + ':' + s.port +
          '</td><td>' + esc(s.user) + '</td><td>' + (s.has_key ? 'да' : 'нет') +
          '</td><td><button class="btn ghost mini" data-del="' + s.id + '">удалить</button></td></tr>').join('') +
        '</table>';
      box.querySelectorAll('[data-del]').forEach(b => {
        b.onclick = async () => {
          if (!confirm('Удалить сервер?')) return;
          await api('/api/ssh/servers', { body: { action: 'remove', id: b.dataset.del } });
          loadList();
        };
      });
    } catch (e) {
      $('#sshList').textContent = 'Ошибка загрузки: ' + e.message;
    }
  }
  await loadList();

  $('#sshAdd').onclick = async () => {
    const body = {
      action: 'add',
      name: $('#sshName').value.trim(),
      host: $('#sshHost').value.trim(),
      user: $('#sshUser').value.trim() || 'root',
      port: parseInt($('#sshPort').value, 10) || 22,
      key_path: $('#sshKey').value.trim(),
      password: $('#sshPass').value,
      note: $('#sshNote').value.trim()
    };
    if (!body.name || !body.host) {
      $('#sshMsg').textContent = 'Нужны имя и хост';
      return;
    }
    try {
      const r = await api('/api/ssh/servers', { body });
      if (r.ok) {
        $('#sshMsg').textContent = 'Сервер добавлен';
        $('#sshName').value = ''; $('#sshHost').value = ''; $('#sshKey').value = ''; $('#sshPass').value = '';
        loadList();
      } else {
        $('#sshMsg').textContent = r.error || 'ошибка';
      }
    } catch (e) {
      $('#sshMsg').textContent = e.message;
    }
  };
}
