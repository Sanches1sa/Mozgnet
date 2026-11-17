/* ==========================================================================
   ui-plus.js — neuro12-cloud v3.21 «Aurora Neural»
   Надстройка над app.js (только stdlib-сервер, без внешних библиотек):
   • темы (тёмная / светлая / авто) и 5 акцентов;
   • командная палитра Ctrl+K и горячие клавиши;
   • дашборд «Пульс мозга»: живая нейросхема, метрики со спарклайнами,
     нагрузка зон, список плат;
   • чат: копирование кода, печатающийся ответ, «вниз», слэш-команды,
     повтор вопроса, экспорт в Markdown, цветные бейджи зон,
     нейро-фон пустого чата.
   ========================================================================== */
'use strict';
(function () {
  const N = window.N12;
  if (!N) return;                       // app.js не загрузился — не мешаем
  const { S, api, esc, toast } = N;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ------------------------------ цвета зон ------------------------------- */
  const ZONE_COLORS = {
    'Навык': '#2dd4bf', 'База знаний': '#60a5fa', 'Сеть материалов': '#a78bfa',
    'Википедия': '#e5e7eb', 'Память': '#f472b6', 'Плата-12': '#34d399',
    'Словарь': '#fbbf24', 'Генератор': '#fb923c', 'Облако': '#94a3b8',
    'Математика': '#38bdf8', 'Код': '#4ade80', 'Мысли платы': '#c084fc',
    'Любопытство': '#f59e0b', 'Предположение': '#fb7185',
    'Речь из словаря': '#facc15', 'Интернет-источники': '#22d3ee',
    'Воображение': '#e879f9'
  };
  const zoneColor = (n) => ZONE_COLORS[n] || '#8b93a3';

  /* -------------------------------- темы ---------------------------------- */
  const ACCENTS = [
    ['teal', '#2dd4bf', 'Бирюза'], ['violet', '#a78bfa', 'Фиолет'],
    ['blue', '#60a5fa', 'Лазурь'], ['amber', '#fbbf24', 'Янтарь'],
    ['rose', '#fb7185', 'Роза']
  ];
  const THEMES = [['dark', 'Тёмная', '☾'], ['light', 'Светлая', '☀'],
    ['auto', 'Авто', '◐']];
  const theme = {
    get mode() { return localStorage.getItem('n12_theme') || 'dark'; },
    get accent() { return localStorage.getItem('n12_accent') || 'teal'; },
    apply() {
      const m = this.mode;
      const light = m === 'light' ||
        (m === 'auto' && matchMedia('(prefers-color-scheme: light)').matches);
      document.body.classList.toggle('light', light);
      const a = this.accent;
      if (a === 'teal') document.documentElement.removeAttribute('data-accent');
      else document.documentElement.setAttribute('data-accent', a);
      const bt = $('#btnTheme');
      if (bt) bt.textContent = (THEMES.find(t => t[0] === m) || THEMES[0])[2];
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.content = light ? '#f6f7fb' : '#0f1115';
      pulseRecolor();
    },
    setMode(m) { localStorage.setItem('n12_theme', m); this.apply(); },
    setAccent(a) { localStorage.setItem('n12_accent', a); this.apply(); },
    cycle() {
      const i = THEMES.findIndex(t => t[0] === this.mode);
      const nx = THEMES[(i + 1) % THEMES.length];
      this.setMode(nx[0]);
      toast('Тема: ' + nx[1].toLowerCase(), 'ok');
    }
  };
  matchMedia('(prefers-color-scheme: light)').addEventListener &&
    matchMedia('(prefers-color-scheme: light)')
      .addEventListener('change', () => theme.mode === 'auto' && theme.apply());

  function cssVar(name, fb) {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
    return v || fb;
  }
  function rgbVar(name, fb) { return cssVar(name, fb); }

  /* --------------------------- вспомогательное ---------------------------- */
  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.0', '') + 'M';
    if (n >= 1e4) return (n / 1e3).toFixed(1).replace('.0', '') + 'k';
    return String(Math.round(n));
  }
  function fmtUp(s) {
    s = Math.round(Number(s) || 0);
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600),
      m = Math.floor(s % 3600 / 60);
    return (d ? d + ' д ' : '') + (h || d ? h + ' ч ' : '') + m + ' мин';
  }
  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? 'Доброй ночи' : h < 12 ? 'Доброе утро' :
      h < 18 ? 'Добрый день' : 'Добрый вечер';
  }
  function download(name, text, mime) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime || 'text/plain' }));
    a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  /* ------------------------- экспорт чата (Markdown) ---------------------- */
  function exportChat() {
    const c = S.chat;
    const msgs = (c && c.messages) || [];
    if (!msgs.length) return toast('Чат пуст — экспортировать нечего', 'err');
    const lines = ['# ' + ((c && c.title) || 'Чат мозг-12'), '',
      '_Экспорт: ' + new Date().toLocaleString('ru') +
      ' · neuro12-cloud_', ''];
    msgs.forEach(m => {
      const who = m.role === 'user' ? 'Вы' :
        (m.role === 'system' ? 'Облако' : 'мозг-12' +
          (m.zone ? ' · ' + m.zone : ''));
      let t = String(m.text || '')
        .replace(/\[(ФОРМУЛА|КАРТИНКА)\][\s\S]*?\[\/\1\]/g,
          (_, k) => '_[' + k.toLowerCase() + ' — см. в чате]_');
      lines.push('**' + who + ':**', '', t, '');
    });
    const name = ((c && c.title) || 'chat').replace(/[^\wа-яА-ЯёЁ\- ]+/g, '')
      .trim().slice(0, 40).replace(/\s+/g, '_') || 'chat';
    download(name + '.md', lines.join('\n'), 'text/markdown;charset=utf-8');
    toast('Чат сохранён в Markdown', 'ok');
  }

  /* ------------------------ улучшения ленты сообщений --------------------- */
  const chatState = { id: undefined, n: 0 };

  function decorateRow(row) {
    if (row.dataset.dec) return;
    row.dataset.dec = '1';
    $$('.zone', row).forEach(z => {
      z.style.setProperty('--zc', zoneColor(z.textContent.trim()));
    });
    $$('pre', row).forEach(decoratePre);
  }

  /* ---------------- блок кода: язык, копировать, файл, запуск ---------------- */
  const EXT = { python: 'py', py: 'py', javascript: 'js', js: 'js', cpp: 'ino', ino: 'ino',
    arduino: 'ino', c: 'c', html: 'html', bash: 'sh', sh: 'sh', sql: 'sql', json: 'json',
    css: 'css', yaml: 'yml' };
  const LANG_NAME = { python: 'Python', py: 'Python', javascript: 'JavaScript', js: 'JavaScript',
    cpp: 'Arduino / C++', ino: 'Arduino', html: 'HTML', bash: 'Bash', sh: 'Bash', sql: 'SQL',
    json: 'JSON', css: 'CSS', c: 'C' };
  function decoratePre(pre) {
    if ($('.pre-bar', pre)) return;
    const code = $('code', pre);
    const lang = (pre.dataset.lang || '').toLowerCase();
    const bar = document.createElement('div');
    bar.className = 'pre-bar';
    bar.setAttribute('contenteditable', 'false');
    const lab = document.createElement('span');
    lab.className = 'pre-lang';
    lab.textContent = LANG_NAME[lang] || (lang ? lang : 'код');
    bar.appendChild(lab);
    const sp = document.createElement('span'); sp.style.flex = '1'; bar.appendChild(sp);
    const mk = (txt, title, fn) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'pre-btn'; b.textContent = txt; b.title = title;
      b.onclick = (e) => { e.stopPropagation(); fn(b); };
      bar.appendChild(b); return b;
    };
    const getCode = () => (code ? code.innerText : pre.innerText).replace(/\n$/, '');
    if (lang === 'python' || lang === 'py') {
      mk('▶ Запустить', 'Выполнить в песочнице облака (без сети и файлов)', async (btn) => {
        btn.disabled = true; btn.textContent = '…';
        const prev = pre.nextElementSibling;
        let out = prev && prev.classList.contains('run-out') ? prev : null;
        if (!out) {
          out = document.createElement('div'); out.className = 'run-out';
          pre.insertAdjacentElement('afterend', out);
        }
        try {
          const r = await api('/api/code/run', { body: { code: getCode() } });
          out.classList.toggle('bad', !(r && r.ok));
          out.textContent = (r && r.ok) ? '▶ ' + r.output : '✖ ' + ((r && r.error) || 'ошибка запуска');
        } catch (e) { out.classList.add('bad'); out.textContent = '✖ ' + e.message; }
        btn.disabled = false; btn.textContent = '▶ Запустить';
      });
    }
    mk('⭳ Файл', 'Скачать как файл', () => {
      const ext = EXT[lang] || 'txt';
      const name = (ext === 'ino' ? 'sketch' : 'code') + '.' + ext;
      download(name, getCode() + '\n', 'text/plain;charset=utf-8');
      toast('Сохранён файл ' + name, 'ok');
    });
    mk('Копировать', 'Скопировать код', (b) => {
      navigator.clipboard.writeText(getCode()).then(() => {
        b.textContent = '✓ Готово'; setTimeout(() => b.textContent = 'Копировать', 1400);
      });
    });
    pre.insertBefore(bar, pre.firstChild);
  }

  function typewriter(body) {
    // печатаем ответ «живым» — только текстовые узлы; SVG/формулы не трогаем
    if (REDUCED || !body || $('svg', body) || $('pre', body)) return;
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const nodes = []; let n;
    while ((n = walker.nextNode())) {
      if (n.nodeValue.length) nodes.push([n, n.nodeValue]);
    }
    const total = nodes.reduce((a, x) => a + x[1].length, 0);
    if (!total || total > 1800) return;      // длинные ответы — сразу целиком
    nodes.forEach(x => x[0].nodeValue = '');
    body.classList.add('caret');
    const dur = Math.min(1500, 260 + total * 7);
    const t0 = performance.now();
    const box = $('#msgs');
    const step = (now) => {
      let k = Math.min(total, Math.floor(total * Math.min(1, (now - t0) / dur)));
      for (const [node, full] of nodes) {
        const take = Math.min(full.length, k);
        node.nodeValue = full.slice(0, take); k -= take;
        if (k <= 0) { /* остальные остаются пустыми */ }
      }
      if (box.scrollHeight - box.scrollTop - box.clientHeight < 220)
        box.scrollTop = box.scrollHeight;
      if (now - t0 < dur) requestAnimationFrame(step);
      else { nodes.forEach(x => x[0].nodeValue = x[1]); body.classList.remove('caret'); }
    };
    requestAnimationFrame(step);
  }

  function addRetry(row) {
    const acts = $('.msg-acts', row);
    if (!acts || $('.retry', acts)) return;
    const b = document.createElement('button');
    b.className = 'retry'; b.textContent = '↻ Спросить снова';
    b.onclick = () => {
      const ms = (S.chat && S.chat.messages) || [];
      for (let i = ms.length - 1; i >= 0; i--) {
        if (ms[i].role === 'user') {
          $('#input').value = ms[i].text; return N.send();
        }
      }
    };
    acts.appendChild(b);
  }

  function onChatRendered() {
    const inner = $('#msgsInner');
    if (!inner) return;
    const msgs = (S.chat && S.chat.messages) || [];
    const last = msgs[msgs.length - 1];
    const fresh = S.chat && S.chat.id === chatState.id &&
      msgs.length === chatState.n + 1 && last && last.role === 'bot';
    chatState.id = S.chat && S.chat.id; chatState.n = msgs.length;
    $$('.row', inner).forEach(decorateRow);
    const rows = $$('.row.bot', inner);
    if (rows.length) {
      const lr = rows[rows.length - 1];
      addRetry(lr);
      if (fresh && lr === inner.lastElementChild) {
        typewriter($('.bubble > div:not(.who):not(.msg-acts):not(.mini)', lr));
      }
    }
    // пустой чат: приветствие + нейро-фон
    const empty = $('.empty', inner);
    if (empty && !$('.hello', empty)) {
      const p = document.createElement('p');
      p.className = 'hello';
      p.style.cssText = 'font-size:13px;margin-bottom:6px;color:var(--acc)';
      p.textContent = greeting() + '!';
      empty.insertBefore(p, empty.firstChild.nextSibling);
      const ex = $('.ex', empty);
      if (ex && !$('[data-pulse]', ex)) {
        const b = document.createElement('button');
        b.dataset.pulse = '1'; b.textContent = '💗 пульс мозга';
        b.onclick = () => N.showView('pulse');
        ex.appendChild(b);
      }
    }
    if (empty) codeEmpty(empty);
    bgnet.toggle(!!empty);
    updateFab();
  }

  /* ============================ РЕЖИМ «КОД» ================================ */
  const CODE_LANGS = [['', 'Авто'], ['python', 'Python'], ['js', 'JavaScript'],
    ['arduino', 'Arduino'], ['html', 'HTML'], ['bash', 'Bash'], ['sql', 'SQL']];
  const CODE_EX = [
    ['напиши на python факториал 10', 'факториал (Python)'],
    ['скетч: мигание светодиодом на пине 7 каждые 300 мс', 'мигание светодиодом'],
    ['сделай html страницу счётчик «Клики»', 'HTML-счётчик'],
    ['сортировка на js', 'сортировка (JS)'],
    ['напиши на python простые числа до 50', 'простые числа'],
    ['скетч для дальномера hc-sr04 на пине 5', 'дальномер HC-SR04'],
    ['fizzbuzz на bash до 30', 'FizzBuzz (Bash)'],
    ['sql запрос join и группировка', 'SQL: JOIN']
  ];
  function codeLang() { return localStorage.getItem('n12_codelang') || ''; }
  function renderLangBar() {
    const bar = $('#langBar'); if (!bar) return;
    bar.hidden = S.route !== 'code';
    if (bar.hidden) return;
    bar.innerHTML = '<span class="lb-t">Язык:</span>';
    CODE_LANGS.forEach(l => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = l[1];
      b.className = codeLang() === l[0] ? 'on' : '';
      b.onclick = () => { localStorage.setItem('n12_codelang', l[0]); renderLangBar(); $('#input').focus(); };
      bar.appendChild(b);
    });
    const help = document.createElement('button');
    help.type = 'button'; help.className = 'lb-help'; help.textContent = '？что умею';
    help.onclick = async () => {
      const r = await api('/api/code/catalog').catch(() => null);
      if (!r || !r.ok) return toast('Каталог недоступен', 'err');
      const names = r.tasks.map(t => t.name).join(', ');
      S.chat = S.chat && S.chat.messages ? S.chat : { id: null, title: 'Новый чат', messages: [] };
      S.chat.messages.push({ role: 'system', text: 'Режим «Код» умеет собирать: ' + names +
        '. Языки: ' + r.langs.map(x => x.name).join(', ') + '. Вставь свой код в ``` — разберу и найду ошибки.' });
      window.N12.showView('chat'); window.renderChat && window.renderChat();
    };
    bar.appendChild(help);
  }
  function onRoute(r) {
    const ta = $('#input');
    if (ta) ta.placeholder = r === 'code'
      ? 'Опиши задачу для проекта или попроси написать код…'
      : 'Спроси мозг-12…';
    const note = $('.cnote');
    if (note && !note.dataset.orig) note.dataset.orig = note.textContent;
    if (note) note.textContent = r === 'code'
      ? 'Контур: загрузите ZIP / RAR и отправьте задачу. Действия агента видны при раскрытии строки состояния.'
      : note.dataset.orig;
    renderLangBar();
    /* v3.57: оформление «Кода» не должно оставаться в других режимах */
    const ce = $('#msgsInner .code-empty');
    if (ce && r !== 'code' && typeof window.renderChat === 'function') window.renderChat();
    if (S.chat && !(S.chat.messages || []).length) onChatRendered();
  }
  function codeEmpty(empty) {
    if (S.route !== 'code' || $('.code-empty', empty)) return;
    empty.classList.add('code-empty');
    const big = $('.big', empty); if (big) big.textContent = '⌨';
    const h2 = $('h2', empty); if (h2) h2.textContent = 'Режим «Код»';
    const p = $('p:not(.hello)', empty);
    if (p) p.textContent = 'Загрузи проект ZIP / RAR в панель «Контур» и опиши задачу целиком. Агент изучит файлы, подготовит изменения и отчёт. Можно также попросить отдельный фрагмент кода.';
    const ex = $('.ex', empty);
    if (ex) {
      ex.innerHTML = '';
      CODE_EX.forEach(x => {
        const b = document.createElement('button'); b.textContent = x[1];
        b.onclick = () => { $('#input').value = x[0]; N.send(); };
        ex.appendChild(b);
      });
    }
  }

  /* ------------------------- кнопка «вниз» -------------------------------- */
  function updateFab() {
    const m = $('#msgs'), f = $('#btnDown');
    if (!m || !f) return;
    f.classList.toggle('on',
      m.scrollHeight - m.scrollTop - m.clientHeight > 260);
  }

  /* ---------------------------- слэш-команды ------------------------------ */
  const SLASH = [
    { c: '/код', h: 'режим «Код»: мозг пишет код', run: () => { N.setRoute('code'); fill(''); } },
    { c: '/посчитай', h: 'калькулятор', run: () => fill('посчитай ') },
    { c: '/нарисуй', h: 'зона Воображения', run: () => fill('нарисуй ') },
    { c: '/график', h: 'график функции', run: () => fill('нарисуй график y=') },
    { c: '/что такое', h: 'база знаний', run: () => fill('что такое ') },
    { c: '/статья', h: 'статья из базы', run: () => fill('статья ') },
    { c: '/запомни', h: 'заметка в память', run: () => fill('запомни: ') },
    { c: '/статус', h: 'сводка облака', run: () => quick('статус') },
    { c: '/пульс', h: 'дашборд мозга', run: () => N.showView('pulse') },
    { c: '/новый', h: 'новый чат', run: () => N.newChat() },
    { c: '/экспорт', h: 'чат в Markdown', run: exportChat },
    { c: '/тема', h: 'сменить тему', run: () => theme.cycle() }
  ];
  let slashSel = 0, slashList = [];
  function fill(t) {
    const ta = $('#input'); ta.value = t; ta.focus();
    ta.dispatchEvent(new Event('input'));
  }
  function quick(t) { fill(t); N.send(); }
  function slashClose() { $('#slashBox').classList.remove('on'); slashList = []; }
  function slashRender() {
    const box = $('#slashBox');
    box.innerHTML = '';
    slashList.forEach((s, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = i === slashSel ? 'on' : '';
      b.innerHTML = '<b>' + esc(s.c) + '</b><small>' + esc(s.h) + '</small>';
      b.onmousedown = (e) => { e.preventDefault(); slashPick(i); };
      box.appendChild(b);
    });
    box.classList.toggle('on', slashList.length > 0);
  }
  function slashPick(i) {
    const s = slashList[i]; if (!s) return;
    const ta = $('#input'); ta.value = ''; slashClose(); s.run();
  }
  function slashUpdate() {
    const v = $('#input').value;
    if (v[0] !== '/' || v.indexOf('\n') >= 0) return slashClose();
    const q = v.slice(1).toLowerCase();
    slashList = SLASH.filter(s => s.c.slice(1).toLowerCase().indexOf(q) === 0 ||
      s.c.toLowerCase().indexOf(q) >= 0);
    slashSel = 0; slashRender();
  }

  /* ---------------------- нейро-фон пустого чата -------------------------- */
  const bgnet = (function () {
    let cv, ctx, pts = [], raf = 0, on = false, w = 0, h = 0, dpr = 1;
    function resize() {
      if (!cv) return;
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = cv.clientWidth; h = cv.clientHeight;
      cv.width = w * dpr; cv.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.max(18, Math.min(46, Math.round(w * h / 22000)));
      pts = Array.from({ length: n }, () => ({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - .5) * .22, vy: (Math.random() - .5) * .22,
        r: 1 + Math.random() * 1.8, k: Math.random()
      }));
    }
    function frame() {
      if (!on) { raf = 0; return; }
      ctx.clearRect(0, 0, w, h);
      const acc = cssVar('--acc-rgb', '45,212,191'),
        ac2 = cssVar('--acc2-rgb', '139,92,246');
      for (const p of pts) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;
      }
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const a = pts[i], b = pts[j];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < 130) {
            ctx.strokeStyle = 'rgba(' + acc + ',' + (0.30 * (1 - d / 130)).toFixed(3) + ')';
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
      for (const p of pts) {
        ctx.fillStyle = 'rgba(' + (p.k > .5 ? acc : ac2) + ',.75)';
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fill();
      }
      raf = requestAnimationFrame(frame);
    }
    return {
      init() {
        cv = $('#bgnet'); if (!cv) return;
        ctx = cv.getContext('2d');
        new ResizeObserver(resize).observe(cv);
        resize();
      },
      toggle(v) {
        if (!cv) return;
        cv.style.display = v ? 'block' : 'none';
        on = v && !REDUCED;
        if (v) { resize(); if (REDUCED) frame1(); }
        if (on && !raf) raf = requestAnimationFrame(frame);
      }
    };
    function frame1() { on = true; frame(); on = false; }
  })();

  /* ============================ ПУЛС МОЗГА ================================ */
  const PULSE = {
    timer: 0, raf: 0, active: false,
    hist: JSON.parse(localStorage.getItem('n12_hist') || '{}'),
    data: null, zones: [], nodes: [], links: [], sig: [], w: 0, h: 0, dpr: 1,
    cv: null, ctx: null, t0: 0
  };
  const METRICS = [
    ['boards_online', 'Платы онлайн', '🔌'], ['messages', 'Сообщений', '💬'],
    ['kb_articles', 'Статей в базе', '📚'], ['nodes', 'Узлов сети', '🕸']
  ];

  function pulseRecolor() { /* цвета берутся из CSS при каждой отрисовке */ }

  function pushHist(key, v) {
    const a = PULSE.hist[key] = PULSE.hist[key] || [];
    a.push(Number(v) || 0);
    while (a.length > 40) a.shift();
  }
  function spark(arr) {
    if (!arr || arr.length < 2) return '';
    const mx = Math.max.apply(null, arr), mn = Math.min.apply(null, arr);
    const rng = (mx - mn) || 1, W = 120, H = 38;
    const pts = arr.map((v, i) => [
      (i / (arr.length - 1)) * W, H - 4 - ((v - mn) / rng) * (H - 10)]);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');
    return '<svg class="spark" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
      '<defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1">' +
      '<stop offset="0" stop-color="var(--acc)" stop-opacity=".35"/>' +
      '<stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>' +
      '<path d="' + d + 'L' + W + ' ' + H + 'L0 ' + H + 'Z" fill="url(#sg)"/>' +
      '<path d="' + d + '" fill="none" stroke="var(--acc)" stroke-width="1.6" ' +
      'stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }

  async function pulseFetch() {
    const [st, zn] = await Promise.all([
      api('/api/status').catch(() => null), api('/api/zones').catch(() => null)]);
    if (!st || !st.ok) return false;
    PULSE.data = st;
    PULSE.zones = (zn && zn.zones) || [];
    const s = st.stats || {};
    const rd = st.cloud_readings || {};
    const vals = { boards_online: s.boards_online, messages: s.messages,
      kb_articles: s.kb_articles, nodes: rd.nodes };
    Object.keys(vals).forEach(k => pushHist(k, vals[k]));
    try { localStorage.setItem('n12_hist', JSON.stringify(PULSE.hist)); } catch (e) {}
    return true;
  }

  function buildGraph() {
    const st = PULSE.data || {}, w = PULSE.w, h = PULSE.h;
    const cx = w / 2, cy = h / 2 + 8;
    const zs = PULSE.zones.slice(0, 20);
    const nodes = [{ id: 'core', kind: 'core', x: cx, y: cy, r: 17, label: 'облако' }];
    const rz = Math.min(w * 0.30, h * 0.36);
    zs.forEach((z, i) => {
      const a = (i / Math.max(1, zs.length)) * Math.PI * 2 - Math.PI / 2;
      nodes.push({ id: 'z' + i, kind: 'zone', x: cx + Math.cos(a) * rz * (w / h > 2 ? 1.75 : 1.25),
        y: cy + Math.sin(a) * rz, a, name: z.name,
        r: 5 + Math.min(7, Math.log2(1 + (z.answers || 0)) * 1.1),
        color: zoneColor(z.name), n: z.answers || 0 });
    });
    const bs = st.boards || [];
    bs.slice(0, 14).forEach((b, i) => {
      const side = i % 2 ? 1 : -1;
      const k = Math.floor(i / 2);
      nodes.push({ id: 'b' + i, kind: 'board', online: !!b.online,
        x: cx + side * (w * 0.43 - (k % 3) * 14),
        y: h * (0.2 + 0.6 * ((k % 5) / 4.2)) + 6,
        r: 7, name: b.name || b.id || 'плата', color: b.online ? cssVar('--acc', '#2dd4bf') : '#64748b' });
    });
    const links = [];
    nodes.forEach(n => { if (n.kind !== 'core') links.push([0, nodes.indexOf(n)]); });
    PULSE.nodes = nodes; PULSE.links = links;
    PULSE.sig = PULSE.sig.filter(s => s.l < links.length);
  }

  function pulseFrame(now) {
    if (!PULSE.active) { PULSE.raf = 0; return; }
    const { ctx, w, h } = PULSE;
    if (!ctx || !w) { PULSE.raf = requestAnimationFrame(pulseFrame); return; }
    const t = (now - PULSE.t0) / 1000;
    const acc = cssVar('--acc-rgb', '45,212,191'), ac2 = cssVar('--acc2-rgb', '139,92,246');
    const txt = cssVar('--muted', '#8b93a3');
    ctx.clearRect(0, 0, w, h);
    const N_ = PULSE.nodes;
    // связи
    PULSE.links.forEach(([a, b]) => {
      const A = N_[a], B = N_[b]; if (!A || !B) return;
      const off = B.kind === 'board' && !B.online;
      ctx.strokeStyle = off ? 'rgba(120,130,150,.18)' : 'rgba(' + acc + ',.22)';
      ctx.lineWidth = 1; ctx.setLineDash(off ? [3, 4] : []);
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
    });
    ctx.setLineDash([]);
    // сигналы
    if (!REDUCED && Math.random() < 0.22 && PULSE.links.length && PULSE.sig.length < 26) {
      const li = Math.floor(Math.random() * PULSE.links.length);
      const B = N_[PULSE.links[li][1]];
      if (B && (B.kind !== 'board' || B.online))
        PULSE.sig.push({ l: li, p: 0, dir: Math.random() < .5 ? 1 : -1, sp: .006 + Math.random() * .01 });
    }
    PULSE.sig = PULSE.sig.filter(s => {
      s.p += s.sp; if (s.p >= 1) return false;
      const [a, b] = PULSE.links[s.l];
      const A = N_[a], B = N_[b]; if (!A || !B) return false;
      const q = s.dir > 0 ? s.p : 1 - s.p;
      const x = A.x + (B.x - A.x) * q, y = A.y + (B.y - A.y) * q;
      const g = ctx.createRadialGradient(x, y, 0, x, y, 9);
      g.addColorStop(0, 'rgba(' + ac2 + ',.95)'); g.addColorStop(1, 'rgba(' + ac2 + ',0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 9, 0, 6.283); ctx.fill();
      return true;
    });
    // узлы
    ctx.font = '11px system-ui,sans-serif'; ctx.textBaseline = 'middle';
    N_.forEach((n, i) => {
      const pul = 1 + Math.sin(t * 2 + i) * 0.06;
      if (n.kind === 'core') {
        for (let k = 3; k >= 1; k--) {
          ctx.fillStyle = 'rgba(' + acc + ',' + (0.05 * (4 - k) * (0.8 + 0.2 * Math.sin(t * 1.6))).toFixed(3) + ')';
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r + k * 11 * pul, 0, 6.283); ctx.fill();
        }
        const g = ctx.createLinearGradient(n.x - n.r, n.y - n.r, n.x + n.r, n.y + n.r);
        g.addColorStop(0, 'rgb(' + acc + ')'); g.addColorStop(1, 'rgb(' + ac2 + ')');
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(n.x, n.y, n.r * pul, 0, 6.283); ctx.fill();
        ctx.fillStyle = '#04211d'; ctx.textAlign = 'center'; ctx.font = '600 15px system-ui';
        ctx.fillText('🧠', n.x, n.y + 1);
        ctx.font = '11px system-ui,sans-serif';
      } else {
        const col = n.color || '#8b93a3';
        if (n.kind === 'board' && n.online) {
          ctx.fillStyle = 'rgba(' + acc + ',.16)';
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 5 + Math.sin(t * 3 + i) * 2, 0, 6283 / 1000); ctx.fill();
        }
        ctx.fillStyle = col; ctx.globalAlpha = n.kind === 'board' && !n.online ? .55 : 1;
        if (n.kind === 'board') {
          const s = n.r; ctx.beginPath();
          ctx.roundRect ? ctx.roundRect(n.x - s, n.y - s, s * 2, s * 2, 3) : ctx.rect(n.x - s, n.y - s, s * 2, s * 2);
          ctx.fill();
        } else { ctx.beginPath(); ctx.arc(n.x, n.y, n.r * pul, 0, 6.283); ctx.fill(); }
        ctx.globalAlpha = 1;
        ctx.fillStyle = txt;
        const right = n.kind === 'board' ? n.x < PULSE.w / 2 : Math.cos(n.a || 0) >= 0;
        ctx.textAlign = right ? 'left' : 'right';
        const lab = String(n.name || '').slice(0, 16);
        if (w > 520 || n.kind === 'board') ctx.fillText(lab, n.x + (right ? 1 : -1) * (n.r + 6), n.y);
      }
    });
    PULSE.raf = requestAnimationFrame(pulseFrame);
  }

  function pulseSize() {
    const cv = PULSE.cv; if (!cv) return;
    PULSE.dpr = Math.min(2, window.devicePixelRatio || 1);
    PULSE.w = cv.clientWidth; PULSE.h = cv.clientHeight;
    cv.width = PULSE.w * PULSE.dpr; cv.height = PULSE.h * PULSE.dpr;
    PULSE.ctx.setTransform(PULSE.dpr, 0, 0, PULSE.dpr, 0, 0);
    buildGraph();
  }

  function pulseDraw() {
    const box = $('#pulseBox'); if (!box || !PULSE.data) return;
    const st = PULSE.data, s = st.stats || {}, rd = st.cloud_readings || {};
    const hadCanvas = !!$('#pulseCv', box);
    const online = s.boards_online || 0;
    const vals = { boards_online: online + '/' + (s.boards_total || 0),
      messages: fmtNum(s.messages), kb_articles: fmtNum(s.kb_articles),
      nodes: fmtNum(rd.nodes) };
    const subs = { boards_online: 'плат в облаке', messages: (s.chats || 0) + ' чатов',
      kb_articles: (s.skills || 0) + ' приложений App Lab', nodes: fmtNum(s.dict_words) + ' слов в словарях' };

    const zs = PULSE.zones.slice().sort((a, b) => (b.answers || 0) - (a.answers || 0));
    const top = zs.slice(0, 10), mx = Math.max(1, ...top.map(z => z.answers || 0));
    const bs = st.boards || [];
    const tags = [];
    const cur = st.curiosity || {}, hy = st.hypo || {}, te = st.teacher || {};
    tags.push(['Любопытство', cur.on]); tags.push(['Гипотезы', hy.on]);
    tags.push(['Учитель (LLM)', te.enabled || te.on]);
    tags.push(['Связь с платами', (st.stats || {}).boards_online > 0 || bs.some(b => b.online)]);
    tags.push(['Прямой мост к плате', (st.bridge || {}).online]);

    if (!hadCanvas) {
      box.innerHTML =
        '<div class="pulse-hero"><canvas id="pulseCv"></canvas>' +
        '<div class="cap"><h2>Пульс мозга</h2><p id="pulseCap"></p></div>' +
        '<div class="live"><span class="dot ok"></span> обновляется каждые 5 с</div></div>' +
        '<div class="stats" id="pulseStats"></div>' +
        '<div class="grid2"><div class="card"><h3>⚡ Нагрузка зон мысли</h3><div class="zbars" id="pulseZones"></div></div>' +
        '<div class="card"><h3>🔌 Платы</h3><div id="pulseBoards"></div>' +
        '<h3 style="margin-top:14px">🧬 Механизмы</h3><div id="pulseTags" style="display:flex;gap:6px;flex-wrap:wrap"></div></div></div>';
      PULSE.cv = $('#pulseCv'); PULSE.ctx = PULSE.cv.getContext('2d');
      new ResizeObserver(pulseSize).observe(PULSE.cv);
      pulseSize();
    }
    $('#pulseCap').textContent = 'v' + st.version + ' · работает ' + fmtUp(st.uptime) +
      ' · ' + PULSE.zones.length + ' зон · ' + (rd.nodes || 0) + ' узлов сети';
    $('#pulseStats').innerHTML = METRICS.map(m =>
      '<div class="stat"><div class="lab">' + m[2] + ' ' + m[1] + '</div>' +
      '<div class="val">' + esc(vals[m[0]]) + '</div>' +
      '<div class="sub">' + esc(subs[m[0]]) + '</div>' + spark(PULSE.hist[m[0]]) + '</div>').join('');
    $('#pulseZones').innerHTML = top.length ? top.map(z =>
      '<div class="zb" style="--zc:' + zoneColor(z.name) + '"><span>' + esc(z.name) +
      '</span><div class="bar"><i style="width:' + Math.max(3, Math.round((z.answers || 0) / mx * 100)) +
      '%"></i></div><b>' + fmtNum(z.answers) + '</b></div>').join('') :
      '<div class="mini">Зоны ещё не отвечали — задай вопрос в чате.</div>';
    $('#pulseBoards').innerHTML = bs.length ? bs.map(b =>
      '<div class="bcard"><div class="bi">' + (b.kind === 'camera' ? '📷' : '🧠') + '</div>' +
      '<div style="flex:1;min-width:0"><div class="bn">' + esc(b.name || b.id) + '</div>' +
      '<div class="bs">' + (b.last_seen ? 'был ' + new Date(b.last_seen * 1000).toLocaleTimeString('ru') : 'ещё не выходил на связь') +
      '</div></div><span class="pill ' + (b.online ? 'ok' : 'bad') + '">' + (b.online ? 'в сети' : 'молчит') + '</span></div>').join('') :
      '<div class="mini">Плат пока нет. Подключи neuro_12 — токен во вкладке «Общие».</div>';
    $('#pulseTags').innerHTML = tags.map(t =>
      '<span class="pill ' + (t[1] ? 'ok' : '') + '">' + esc(t[0]) + ': ' + (t[1] ? 'вкл' : 'выкл') + '</span>').join('');
    buildGraph();
  }

  async function renderPulse() {
    const box = $('#pulseBox');
    if (!PULSE.data) {
      box.innerHTML = '<div class="skel" style="height:240px;border-radius:22px"></div>' +
        '<div class="skel" style="height:90px"></div><div class="skel" style="height:160px"></div>';
    }
    PULSE.active = true; PULSE.t0 = performance.now();
    if (await pulseFetch()) pulseDraw();
    else box.innerHTML = '<div class="empty"><div class="big">📡</div><h2>Нет связи с облаком</h2>' +
      '<p>Не удалось получить статус. Попробую снова через несколько секунд.</p></div>';
    if (!PULSE.raf) PULSE.raf = requestAnimationFrame(pulseFrame);
    clearInterval(PULSE.timer);
    PULSE.timer = setInterval(async () => {
      if (document.hidden || !PULSE.active) return;
      if (await pulseFetch()) pulseDraw();
    }, 5000);
  }

  function onView(name) {
    if (name !== 'pulse') {
      PULSE.active = false; clearInterval(PULSE.timer);
      const c = $('#pulseCv'); if (c) $('#pulseBox').innerHTML = '';
      PULSE.cv = null;
    }
    $$('.sidenav button[data-view]').forEach(b =>
      b.classList.toggle('on', b.dataset.view === name));
    if (name !== 'chat') bgnet.toggle(false);
    else onChatRendered();
  }

  /* ========================= командная палитра ============================ */
  const CK = { items: [], sel: 0 };
  function ckItems() {
    const it = [];
    it.push({ g: 'Действия', ic: '＋', t: 'Новый чат', k: 'Alt+N', run: () => N.newChat() });
    it.push({ g: 'Действия', ic: '⭳', t: 'Экспорт чата в Markdown', run: exportChat });
    it.push({ g: 'Действия', ic: '◐', t: 'Сменить тему (тёмная / светлая / авто)', run: () => theme.cycle() });
    it.push({ g: 'Действия', ic: '🔊', t: 'Озвучка ответов: вкл / выкл', run: () => $('#btnTTS').click() });
    it.push({ g: 'Действия', ic: '↕', t: 'Длинные ответы: вкл / выкл', run: () => $('#chkLong').click() });
    ['auto', 'cloud', 'talk', 'code', 'board'].forEach(r => it.push({ g: 'Кто отвечает', ic: r === 'code' ? '⌨' : r === 'talk' ? '💬' : '🎯',
      t: 'Режим: ' + ({ auto: 'Авто', cloud: 'Облако', talk: 'Разговор (как в живом диалоге)', code: 'Код (ИИ-агент)', board: 'Плата (живая плата)' })[r], run: () => N.setRoute(r) }));
    [['pulse', '💗', 'Пульс мозга'], ['applab', '🧩', 'App Lab'], ['chronicle', '📜', 'Хроника плат'],
      ['vision', '📷', 'Зрение']].forEach(v => it.push({ g: 'Разделы', ic: v[1], t: v[2], run: () => N.showView(v[0]) }));
    it.push({ g: 'Разделы', ic: '💭', t: 'Мысли плат', run: () => N.openChat('thoughts') });
    [['common', 'Общие'], ['devices', 'Устройства'], ['memory', 'Облачная память'], ['kb', 'База знаний'],
      ['zones', 'Зоны мысли'], ['teacher', 'Учитель (LLM)'], ['voice', 'Голос'],
      ['maxbot', 'Макс-бот'], ['security', 'Безопасность'], ['update', 'Обновление'], ['log', 'Журнал']]
      .forEach(s => it.push({ g: 'Настройки', ic: '⚙', t: s[1],
        run: () => { N.openSettings(); setTimeout(() => N.renderTab(s[0]), 30); } }));
    ACCENTS.forEach(a => it.push({ g: 'Оформление', ic: '●', t: 'Акцент: ' + a[2], run: () => theme.setAccent(a[0]) }));
    (S.chats || []).slice(0, 40).forEach(c => it.push({ g: 'Чаты', ic: '💬',
      t: c.title || 'Без названия', k: (c.count || 0) + ' сообщ.', run: () => N.openChat(c.id) }));
    return it;
  }
  function ckRender() {
    const q = $('#cmdkInput').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const all = ckItems();
    CK.items = all.filter(i => q.every(w => (i.t + ' ' + i.g).toLowerCase().indexOf(w) >= 0)).slice(0, 60);
    if (CK.sel >= CK.items.length) CK.sel = 0;
    const box = $('#cmdkList'); box.innerHTML = '';
    if (!CK.items.length) { box.innerHTML = '<div class="none">Ничего не нашлось</div>'; return; }
    let g = '';
    CK.items.forEach((i, idx) => {
      if (i.g !== g) { g = i.g; const d = document.createElement('div'); d.className = 'grp'; d.textContent = g; box.appendChild(d); }
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'it' + (idx === CK.sel ? ' on' : '');
      b.innerHTML = '<span class="ic">' + i.ic + '</span><span>' + esc(i.t) + '</span>' + (i.k ? '<small>' + esc(i.k) + '</small>' : '');
      b.onmousemove = () => { if (CK.sel !== idx) { CK.sel = idx; $$('.it', box).forEach((x, n) => x.classList.toggle('on', x === b)); } };
      b.onclick = () => ckRun(idx);
      box.appendChild(b);
    });
    const on = $('.it.on', box); if (on) on.scrollIntoView({ block: 'nearest' });
  }
  function ckRun(i) { const it = CK.items[i]; if (!it) return; ckClose(); setTimeout(it.run, 20); }
  function ckOpen() {
    $('#cmdkVeil').classList.add('on');
    const inp = $('#cmdkInput'); inp.value = ''; CK.sel = 0; ckRender(); inp.focus();
  }
  function ckClose() { $('#cmdkVeil').classList.remove('on'); }

  function showKeys() {
    const veil = $('#cmdkVeil'), box = $('#cmdkList');
    ckOpen();
    $('#cmdkInput').value = '';
    box.innerHTML = '<div class="keys">' + [
      ['Ctrl + K', 'командная палитра'], ['Alt + N', 'новый чат'], ['Alt + P', 'пульс мозга'],
      ['Alt + T', 'сменить тему'], ['/', 'фокус в поле ввода'], ['/ + слово', 'слэш-команды в поле ввода'],
      ['Shift + Enter', 'перенос строки'], ['?', 'эта подсказка'], ['Esc', 'закрыть окно']]
      .map(k => '<b class="kbd" style="justify-self:start">' + k[0] + '</b><span>' + k[1] + '</span>').join('') + '</div>';
    CK.items = []; void veil;
  }

  /* ------------------ блок «Оформление» в настройках ---------------------- */
  function appearanceHTML() {
    return '<div class="card" style="margin-bottom:16px"><h3>🎨 Оформление</h3>' +
      '<div class="fgroup"><label>Тема</label><div class="seg" id="segTheme">' +
      THEMES.map(t => '<button type="button" data-m="' + t[0] + '" class="' + (theme.mode === t[0] ? 'on' : '') + '">' + t[2] + ' ' + t[1] + '</button>').join('') +
      '</div></div><div class="fgroup" style="margin-bottom:0"><label>Акцентный цвет</label><div class="swatches" id="swAccent">' +
      ACCENTS.map(a => '<button type="button" title="' + a[2] + '" data-a="' + a[0] + '" style="--c:' + a[1] + '" class="' + (theme.accent === a[0] ? 'on' : '') + '"></button>').join('') +
      '</div></div></div>';
  }
  function wrapTabCommon() {
    const orig = window.tabCommon;
    if (typeof orig !== 'function') return;
    window.tabCommon = function () {
      orig.apply(this, arguments);
      const p = $('[data-pane="common"]'); if (!p) return;
      const h2 = $('h2', p); const wrap = document.createElement('div');
      wrap.innerHTML = appearanceHTML();
      const hint = p.querySelector(':scope > .hint');   // v3.173.1: не внутрь блока Pillow
      (hint || h2 || p).insertAdjacentElement(hint || h2 ? 'afterend' : 'afterbegin', wrap.firstChild);
      $$('#segTheme button', p).forEach(b => b.onclick = () => {
        theme.setMode(b.dataset.m);
        $$('#segTheme button', p).forEach(x => x.classList.toggle('on', x === b));
      });
      $$('#swAccent button', p).forEach(b => b.onclick = () => {
        theme.setAccent(b.dataset.a);
        $$('#swAccent button', p).forEach(x => x.classList.toggle('on', x === b));
      });
    };
  }

  /* ------------------------------ запуск ---------------------------------- */
  function init() {
    theme.apply();
    bgnet.init();
    wrapTabCommon();
    const origSetRoute = window.setRoute;
    if (typeof origSetRoute === 'function') {
      window.setRoute = function (r) { origSetRoute.apply(this, arguments); onRoute(r); };
      window.N12.setRoute = window.setRoute;
    }
    onRoute(S.route);

    const inner = $('#msgsInner');
    if (inner) new MutationObserver(() => {
      clearTimeout(init._t); init._t = setTimeout(onChatRendered, 0);
    }).observe(inner, { childList: true });
    onChatRendered();

    const msgs = $('#msgs');
    msgs.addEventListener('scroll', updateFab, { passive: true });
    $('#btnDown').onclick = () => msgs.scrollTo({ top: msgs.scrollHeight, behavior: 'smooth' });
    $('#btnTheme').onclick = () => theme.cycle();
    $('#btnCmdk').onclick = ckOpen;
    $('#btnCmdkSide').onclick = () => { $('#rightmenu').classList.remove('open'); $('#sidebar').classList.remove('open'); $('#sideVeil').classList.remove('on'); ckOpen(); };
    $('#btnExport').onclick = exportChat;

    const cmdkVeil = $('#cmdkVeil');
    cmdkVeil.addEventListener('mousedown', (e) => { if (e.target === cmdkVeil) ckClose(); });
    $('#cmdkInput').addEventListener('input', () => { CK.sel = 0; ckRender(); });
    $('#cmdkInput').addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); CK.sel = Math.min(CK.items.length - 1, CK.sel + 1); ckRender(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); CK.sel = Math.max(0, CK.sel - 1); ckRender(); }
      else if (e.key === 'Enter') { e.preventDefault(); ckRun(CK.sel); }
      else if (e.key === 'Escape') { e.preventDefault(); ckClose(); }
    });

    // слэш-команды: ловим клавиши раньше обработчика Enter из app.js
    const ta = $('#input'), cbox = $('.cbox');
    ta.addEventListener('input', slashUpdate);
    ta.addEventListener('blur', () => setTimeout(slashClose, 120));
    cbox.addEventListener('keydown', (e) => {
      if (!slashList.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); slashSel = (slashSel + 1) % slashList.length; slashRender(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); slashSel = (slashSel - 1 + slashList.length) % slashList.length; slashRender(); }
      else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); slashPick(slashSel); }
      else if (e.key === 'Escape') { e.stopPropagation(); slashClose(); }
    }, true);

    // глобальные горячие клавиши
    document.addEventListener('keydown', (e) => {
      const tag = (e.target && e.target.tagName) || '';
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K' || e.key === 'л' || e.key === 'Л')) {
        e.preventDefault(); return $('#cmdkVeil').classList.contains('on') ? ckClose() : ckOpen();
      }
      if (e.key === 'Escape') {
        if ($('#cmdkVeil').classList.contains('on')) return ckClose();
        if ($('#settingsVeil').classList.contains('on')) return $('#btnCloseSettings').click();
        // v3.149.15: Esc в разделе (пульс, App Lab, хроника, зрение) — назад в чат
        if (!$('#viewChat').classList.contains('on')) { e.preventDefault(); return N.showView('chat'); }
      }
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        const c = e.code;
        if (c === 'KeyN') { e.preventDefault(); N.newChat(); }
        else if (c === 'KeyP') { e.preventDefault(); N.showView('pulse'); }
        else if (c === 'KeyT') { e.preventDefault(); theme.cycle(); }
        return;
      }
      if (typing || e.ctrlKey || e.metaKey) return;
      if (e.key === '/') { e.preventDefault(); N.showView('chat'); $('#input').focus(); }
      else if (e.key === '?') { e.preventDefault(); showKeys(); }
    });
  }

  window.N12UI = { renderPulse, onView, theme, exportChat, openPalette: ckOpen };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init, 0));
  else setTimeout(init, 0);
})();
