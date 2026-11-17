"""Short code-focused fine-tuning of existing tinyllm weights; no network/exec."""
import ast
import copy
import json
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid

import codecorpus
import brainwill
import store

_LOCK = threading.RLock()
_THREAD = None
_CANCEL = threading.Event()
_STATE = None

# Original small exercises. Evaluation definitions are not added to this course.
_CORE = [
 ('subtract numbers', 'def subtract(a, b):\n    return a - b'),
 ('multiply numbers', 'def multiply(a, b):\n    return a * b'),
 ('cube number', 'def cube(n):\n    return n * n * n'),
 ('odd number', 'def is_odd(n):\n    return n % 2 != 0'),
 ('sum list', 'def total(values):\n    return sum(values)'),
 ('average list', 'def average(values):\n    return sum(values) / len(values) if values else 0'),
 ('reverse string', 'def reverse(text):\n    return text[::-1]'),
 ('palindrome string', 'def palindrome(text):\n    return text == text[::-1]'),
 ('maximum list', 'def maximum(values):\n    return max(values)'),
 ('minimum list', 'def minimum(values):\n    return min(values)'),
 ('absolute number', 'def absolute(n):\n    return abs(n)'),
 ('count words string', 'def count_words(text):\n    return len(text.split())'),
 ('unique list', 'def unique(values):\n    return list(dict.fromkeys(values))'),
 ('sort list', 'def sort_values(values):\n    return sorted(values)'),
 ('factorial number', 'def factorial(n):\n    result = 1\n    for i in range(2, n + 1):\n        result *= i\n    return result'),
 ('gcd numbers', 'def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return abs(a)'),
 ('filter positive list', 'def positive(values):\n    return [x for x in values if x > 0]'),
 ('count letter string', 'def count_letter(text, letter):\n    return text.count(letter)'),
 ('celsius fahrenheit', 'def fahrenheit(celsius):\n    return celsius * 9 / 5 + 32'),
 ('clamp number', 'def clamp(value, low, high):\n    return max(low, min(value, high))'),
 ('first list', 'def first(values):\n    return values[0] if values else None'),
 ('last list', 'def last(values):\n    return values[-1] if values else None'),
 ('join words', 'def join_words(words):\n    return " ".join(words)'),
 ('leap year', 'def leap_year(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)'),
]
_EVAL = [
 ('square number', 'def square(n):\n    return n * n', [([3],9), ([-2],4), ([0],0)]),
 ('add numbers', 'def add(a, b):\n    return a + b', [([2,3],5), ([-1,4],3), ([0,0],0)]),
 ('even number', 'def is_even(n):\n    return n % 2 == 0', [([2],True), ([3],False), ([0],True)]),
]


def _save():
    store.save_json('code_boot.json', _STATE)


_LAST_PHASE = [None]


def _update(**values):
    with _LOCK:
        _STATE.update(values, updated=time.time())
        _save()
        ph = _STATE.get('phase')
        if ph != _LAST_PHASE[0] or values.get('running') is False:
            _LAST_PHASE[0] = ph          # v3.176.2: этапы курса — в журнал облака
            try:
                store.log("КОД", "курс кода (%s): %s" % (
                    "второй поток" if _STATE.get('source') == 'stream' else "вручную",
                    str(_STATE.get('message') or ph)[:300]))
            except Exception:
                pass


def status():
    with _LOCK:
        state = copy.deepcopy(_STATE or store.load_json('code_boot.json', {}) or {})
        if state.get('running') and not (_THREAD and _THREAD.is_alive()):
            state.update(running=False, phase='interrupted', message='Курс прерван перезапуском сервера')
        return state


def _course(rows=None):
    """rows — свои функции (v3.176: второй поток учит прочитанное в
    репозиториях по порядку); None — последние 80 изученных."""
    pairs = list(_CORE)
    for row in (rows if rows is not None else codecorpus._load_snips().get('rows', [])[-80:]):
        code = str(row.get('c') or '')
        try:
            tree = ast.parse(code)
            if len(code) > 1500 or len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
                continue
            if tree.body[0].args.args and tree.body[0].args.args[0].arg in ('self', 'cls'):
                continue
            pairs.append((str(row.get('t') or row.get('n') or '')[:120], code))
        except (SyntaxError, ValueError):
            continue
    excluded = {ast.dump(ast.parse(code), include_attributes=False) for _, code, _ in _EVAL}
    result = []; seen = set()
    for task, code in pairs:
        signature = ast.dump(ast.parse(code), include_attributes=False)
        if signature in seen or signature in excluded:
            continue
        seen.add(signature)
        result.append(codecorpus.CODE_MARK + '# task: ' + (codecorpus.ru_en(task) or task) + '\n' + code + '\n')
    return result


def start(steps=200, seconds=120, mode="dual", rows=None, source="manual"):
    global _THREAD, _STATE
    import tinyllm as tl
    if not tl.HAVE_NP:
        raise ValueError('Для дообучения кода требуется NumPy')
    if mode not in ('dual', 'code'):
        raise ValueError('Неизвестный режим обучения')
    steps = max(1, min(2000, int(steps)))
    seconds = max(5, min(600, int(seconds)))
    with _LOCK, tl._LOCK:
        if _THREAD and _THREAD.is_alive():
            raise ValueError('Курс уже запущен')
        m = tl.model()
        if not m.p or m.steps < 1 or getattr(m, '_dead', False):
            raise ValueError('Нужна существующая модель с весами. Курс не создаёт новую модель и не сбрасывает старую.')
        previous = tl._THREAD[0]
        if previous and previous.is_alive():
            m._stop = True
        _CANCEL.clear()
        _STATE = dict(id=uuid.uuid4().hex, running=True, phase='waiting', message='Дожидаюсь текущего шага обучения', source=source,
                      done=0, mode=mode, code_steps=0, text_steps=0, active_stream='ожидание', waiting_seconds=0,
                      limit_steps=steps, limit_seconds=seconds, before=None, after=None,
                      backup='', rollback=False, started=time.time(), updated=time.time())
        _save()
        m.training = True; m._code_course = True
        _THREAD = threading.Thread(target=_run, args=(m, previous, steps, seconds, mode, rows), daemon=True, name='tinyllm-code-course')
        tl._THREAD[0] = _THREAD
        _THREAD.start()
    return status()


def stop():
    _CANCEL.set()
    return status()


def _windows(m, texts, length):
    import numpy as np
    windows = []
    for text in texts:
        ids = m.tok.encode(text, add_bos=True, add_eos=True)
        for pos in range(0, max(1, len(ids)-1), max(1, length // 2)):
            chunk = ids[pos:pos + length + 1]
            if len(chunk) < 3:
                continue
            chunk += [0] * (length + 1 - len(chunk))
            windows.append(np.array(chunk, dtype=np.int64))
    return windows


def _loss(m, windows):
    import numpy as np
    total = 0.0; count = 0
    for row in windows[:12]:
        logits, _ = m.forward(row[:-1][None, :])
        log = logits[0].astype(np.float64)
        log -= log.max(axis=-1, keepdims=True)
        log -= np.log(np.exp(log).sum(axis=-1, keepdims=True))
        y = row[1:]; mask = (y != 0) & (y != 3)
        total -= float(log[np.arange(len(y)), y][mask].sum())
        count += int(mask.sum())
    return total / max(1, count)


def _simple_cases(code, cases):
    """Interpret only a single arithmetic return. Never exec model output."""
    tree = ast.parse(code)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return None
    fn = tree.body[0]
    if fn.decorator_list or len(fn.body) != 1 or not isinstance(fn.body[0], ast.Return):
        return None
    def value(node, env):
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.Constant) and type(node.value) in (int, bool) and abs(node.value) <= 10000:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -value(node.operand, env)
        if isinstance(node, ast.BinOp):
            a, b = value(node.left, env), value(node.right, env)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            if isinstance(node.op, ast.Mod): return a % b
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            a, b = value(node.left, env), value(node.comparators[0], env)
            if isinstance(node.ops[0], ast.Eq): return a == b
            if isinstance(node.ops[0], ast.NotEq): return a != b
        raise ValueError('Вне ограниченной проверки')
    try:
        names = [a.arg for a in fn.args.args]
        return all(len(args) == len(names) and value(fn.body[0].value, dict(zip(names, args))) == expected
                   for args, expected in cases)
    except Exception:
        return None


def _probe(m, deadline):
    result = []
    for i, (task, _code, cases) in enumerate(_EVAL):
        if time.monotonic() >= deadline:
            break
        raw = m.generate_code('# task: ' + codecorpus.ru_en(task) + '\n', max_new=64,
                              temperature=.4, top_k=1, seed=410+i, deadline=deadline)
        code = codecorpus.trim(raw)
        valid = bool(code)
        result.append(dict(task=task, syntax=valid, cases=_simple_cases(code, cases) if valid else False))
    return result


def _ordinary_texts():
    import kb
    import tinyllm as tl
    texts = []
    for row in kb.list_articles(limit=32, include_devices=False) or []:
        article = kb.article(row.get('id')) if row.get('id') else None
        if article and not codecorpus.is_code_title(article.get('title')):
            text = tl._article_text(article.get('title'), str(article.get('text') or '')[:1600])
            if len(text.strip()) >= 40:
                texts.append(text)
    return texts


def _wait_previous(m, previous):
    """Queue a handoff, without expiring an indivisible BLAS/Adam step."""
    started = time.monotonic()
    last_report = -2
    while previous and previous.is_alive():
        previous.join(.2)
        if _CANCEL.is_set() or getattr(m, '_dead', False):
            _update(phase='cancelled', message='Ожидание отменено; параллельное обновление весов не запускалось')
            return False
        elapsed = int(time.monotonic() - started)
        if elapsed >= last_report + 2:
            _update(waiting_seconds=elapsed, active_stream='обычный шаг',
                    message='Курс в очереди (%d с): %s. Можно отменить ожидание.' % (elapsed, getattr(m, '_train_phase', 'ожидаю безопасную границу шага')))
            last_report = elapsed
    return True


def _pool_for_step(mode, index, code, text):
    ordinary = bool(text) and (index % 2 == 1 if mode == 'dual' else index % 5 == 4)
    return ('text', text) if ordinary else ('code', code)


def _run(m, previous, steps, seconds, mode="dual", rows=None):
    import numpy as np
    import tinyllm as tl
    backup = None
    try:
        if not _wait_previous(m, previous):
            return
        if _CANCEL.is_set() or getattr(m, '_dead', False) or tl._M is not m:
            return
        m.training = True; m._stop = False
        tl._FP.stable_code = True
        _update(phase='preparing', active_stream='подготовка двух источников', message='Готовлю код и обычные тексты в двух потоках')
        # Two material producers; neither modifies tokenizer or weights.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='course-material') as producers:
            code_future = producers.submit(_course, rows)
            text_future = producers.submit(_ordinary_texts)
            texts = code_future.result()
            ordinary_texts = text_future.result()
        if _CANCEL.is_set():
            _update(phase='cancelled', message='Подготовка отменена')
            return
        _update(phase='backup', message='Сохраняю резервную копию весов', examples=len(texts))
        backup = Path(store.p('code_boot_backups', _STATE['id'], 'model'))
        backup.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(backup))
        _update(backup=str(backup))
        added = m.tok.extend(texts + ordinary_texts, max(m.tok.size, int(m.c['llm_vocab'])))
        if added:
            m.grow_vocab(m.tok.size)
        length = min(96, int(m.c['llm_ctx']))
        train = _windows(m, texts, length)
        replay = _windows(m, ordinary_texts[:-1] if len(ordinary_texts)>1 else ordinary_texts, length)
        text_control = _windows(m, ordinary_texts[-1:], length)
        control = _windows(m, [codecorpus.CODE_MARK + '# task: ' + codecorpus.ru_en(t) + '\n' + c + '\n' for t,c,_ in _EVAL], length)
        if not train:
            raise ValueError('Нет кодовых окон обучения')
        _update(phase='before', message='Замеряю кодовые ответы до курса', tokens_added=added)
        before = dict(loss=round(_loss(m, control), 5), text_loss=round(_loss(m,text_control),5) if text_control else None, probes=_probe(m, time.monotonic()+15))
        _update(before=before)
        try:                       # v3.176.1: веса уже разошлись — не учить дальше
            import codestream
            lim = codestream.loss_limit(m)
            if not math.isfinite(before['loss']) or before['loss'] > lim or \
                    (before.get('text_loss') is not None and before['text_loss'] > lim):
                _update(phase='diverged', message='Веса своей LLM разошлись ДО курса: loss кода %s, '
                        'текстов %s при пределе %.0f (наугад ≈ %.1f). Курс не начат. Верни веса '
                        '(«Вернуть веса») или «Переобучить на текущей базе».' % (
                            before['loss'], before.get('text_loss'), lim, math.log(max(10, m.tok.size))))
                return
        except ImportError:
            pass
        _update(will=brainwill.learning())
        _update(phase='training', active_stream='код', replay_windows=len(replay), code_windows=len(train),
                message=('Чередую код и обычные тексты 1:1' if mode=='dual' and replay else
                         'Обучаю код; поток обычных текстов пуст' if not replay else 'Учусь на коде с повтором текстов 4:1'))
        deadline = time.monotonic() + seconds
        rng = np.random.RandomState(231)
        learning_rate = max(1e-6, min(float(m.c['llm_lr']), 5e-4))
        done = 0
        counts = {"code": 0, "text": 0}
        for i in range(steps):
            if _CANCEL.is_set() or m._stop or getattr(m, '_dead', False) or tl._M is not m or time.monotonic() >= deadline:
                break
            stream, pool = _pool_for_step(mode, i, train, replay)
            _update(active_stream="код" if stream=="code" else "обычные тексты")
            rows = [pool[int(rng.randint(len(pool)))] for _ in range(2)]
            batch = np.stack(rows)
            will = brainwill.learning()
            effective_lr = learning_rate * (1-.5*i/max(1,steps)) * will['learning_rate_multiplier']
            loss = m.train_step(batch[:, :-1], batch[:, 1:], effective_lr)
            if not math.isfinite(loss) or not all(np.isfinite(p).all() for p in m.p.values()):
                raise ValueError('Нечисловые веса или loss; резервная копия будет восстановлена')
            m.steps += 1; m.loss = loss; done += 1; counts[stream] += 1
            _update(done=done, will=will, effective_learning_rate=effective_lr, code_steps=counts['code'], text_steps=counts['text'], loss=round(loss,5),
                    message='%s: шаг %d/%d · код %d, тексты %d' % ('Два источника' if mode=='dual' else 'Кодовый курс', done,steps,counts['code'],counts['text']))
            time.sleep(.005)
        if getattr(m, '_dead', False) or tl._M is not m:
            _update(phase='cancelled', message='Модель была удалена или заменена; курс не сохраняет старые веса')
            return
        _update(phase='after', message='Проверяю контрольные задачи после курса')
        after = dict(loss=round(_loss(m,control),5), text_loss=round(_loss(m,text_control),5) if text_control else None, probes=_probe(m,time.monotonic()+15))
        rollback = after['loss'] > before['loss'] * 1.05 or (before.get('text_loss') is not None and after['text_loss'] > before['text_loss'] * 1.05)
        if rollback:
            m.load(str(backup)); m.adam = None; tl._adam_wipe(); m.save(tl._state_path())
        else:
            m.ready = m.steps > 0
            m.meta['code_boot'] = dict(done=done, before=before['loss'], after=after['loss'], ts=time.time())
            m.save(tl._state_path())
        _update(phase='cancelled' if _CANCEL.is_set() else 'done', after=after, rollback=rollback,
                message=('Контрольный loss ухудшился более чем на 5%; исходные веса восстановлены.' if rollback else
                         'Курс завершён. Сравните loss и контрольные задачи; умение решать проекты не гарантируется.'))
    except Exception as e:
        restored = False
        if backup and backup.with_suffix('.npz').exists() and not getattr(m,'_dead',False) and tl._M is m:
            try:
                m.load(str(backup)); m.adam=None; tl._adam_wipe(); m.save(tl._state_path()); restored=True
            except Exception:
                pass
        _update(phase='error', message='%s: %s' % (type(e).__name__,e), rollback=restored)
    finally:
        tl._FP.stable_code = False
        m._code_course = False
        with tl._LOCK:
            if previous and previous.is_alive() and tl._THREAD[0] is threading.current_thread():
                tl._THREAD[0] = previous
                m.training = True
            else:
                m.training = False
        _update(running=False, active_stream='завершено')
        _prune_backups()


def _prune_backups(keep=2):
    """v3.176: второй поток гоняет курс постоянно — резервные копии весов
    не копятся: остаются 2 последние и «хорошие» (loss в норме, v3.176.1)."""
    import shutil
    try:
        good = {str(Path(g.get('path', '')).parent) for g in
                (store.load_json('code_stream.json', {}) or {}).get('good') or []}
        root = Path(store.p('code_boot_backups'))
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[keep:]:
            if str(d) in good:
                continue
            shutil.rmtree(str(d), ignore_errors=True)
    except Exception:
        pass
