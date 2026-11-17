"""v3.57: единая точка синхронизации «запись в БД ↔ обучение».

Любая запись в базу знаний (статья, прочитанная ссылка, загрузка файла) и
кнопка «Обучить» LLM вызывают mark(). Что происходит:
  * облачный мозг (сеть материалов) уже учится в kb.add_article;
  * своя LLM дообучается (learn_new, не чаще раза в DEBOUNCE секунд);
  * счётчик seq растёт — плата видит это в /api/brain/d6 (поле sync) и
    СРАЗУ тянет пачки базы знаний и дообучает тензорную сеть.
"""
import threading
import time

DEBOUNCE = 45.0
_L = threading.Lock()
_S = {"seq": 0, "kb_ts": 0.0, "train_ts": 0.0, "why": "", "kb_n": 0,
      "llm": "", "pending": False, "last_llm": 0.0}


def _llm_later():
    try:
        import tinyllm
        r = tinyllm.learn_new()
        with _L:
            _S["llm"] = str(r)
            _S["last_llm"] = time.time()
    except Exception as e:
        with _L:
            _S["llm"] = "ошибка: %s" % str(e)[:60]
    finally:
        with _L:
            _S["pending"] = False


def mark(kind="kb", why="", llm=True, now=False):
    """kind: 'kb' (запись в БД) | 'train' (кнопка «Обучить»)."""
    t = time.time()
    with _L:
        _S["seq"] += 1
        _S["why"] = str(why or kind)[:80]
        if kind == "kb":
            _S["kb_ts"] = t
            _S["kb_n"] += 1
        else:
            _S["train_ts"] = t
        if not llm or _S["pending"]:
            return
        wait = 0.0 if now else max(0.0, DEBOUNCE - (t - _S["last_llm"]))
        _S["pending"] = True
    tm = threading.Timer(wait, _llm_later)
    tm.daemon = True
    tm.start()


def status():
    with _L:
        return {k: (round(v, 1) if isinstance(v, float) else v)
                for k, v in _S.items()}
