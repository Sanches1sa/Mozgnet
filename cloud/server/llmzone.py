# -*- coding: utf-8 -*-
# ===========================================================================
# llmzone.py — ЛОКАЛЬНАЯ LLM, КОТОРАЯ ПИШЕТ ПО БАЗЕ ЗНАНИЙ (v3.192+, зонная надстройка)
# ---------------------------------------------------------------------------
# Заявка владельца: «добавь tiny LLM настоящего размера… генерацию контента
# и память устроить как у LLM… пусть пишет по делу из статей».
# Облако само модели НЕ скачивает (это сотни МБ–гигабайты и отдельная
# программа). Владелец ставит любую локальную модель с OpenAI-совместимым
# API — например Ollama (`ollama run qwen2.5:1.5b`, адрес
# http://127.0.0.1:11434/v1) — и включает зону в панели. Тогда:
#   * память как у LLM: в запрос идут последние реплики диалога (контекст)
#     и подтверждённые ответы (qalearn) — модель помнит разговор;
#   * генерация по делу (RAG): модель получает фрагменты базы знаний и
#     ответ конвейера (интернет-страницы) и пишет связный ответ ТОЛЬКО по
#     ним; фрагментов нет — честно «в материалах ответа нет»;
#   * ответ помечается «написано локальной моделью по материалам», а сами
#     материалы мозга не меняются (мозг не переучивается на тексте модели).
# Плата получает эту зону без изменения своего кода — через весь мозг
# v3.192: LLM работает как дополнение к зонной сети и сама распределяется по зонам.
# Основные шаги обучения не переписываются. Уроки усваиваются параллельно.
# облака (POST /api/brain/think).
# ===========================================================================
import json
import urllib.request

import store

_SYS = ("Ты — «Star Gate», помощник техподдержки и собеседник. Отвечай по-"
        "русски, по делу, вдумчиво и с правильной пунктуацией, как живой "
        "человек. Пиши ТОЛЬКО на основе фрагментов ниже и истории диалога; "
        "если ответа в них нет — прямо скажи, чего не хватает, и не "
        "выдумывай. Конкретику из вопроса (марки, модели, программы) "
        "обязательно учитывай. Шаги — нумерованным списком.")


def enabled():
    s = store.settings()
    return bool(s.get("llm_answer_on") and str(s.get("llm_answer_url") or "")
                .strip())


def _call(messages, timeout=90):
    s = store.settings()
    url = str(s.get("llm_answer_url") or "").rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = {"model": s.get("llm_answer_model") or "qwen2.5:1.5b",
            "messages": messages, "temperature": _br_temp(0.3), "max_tokens": 900}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return str(d["choices"][0]["message"]["content"]).strip()


def write(q, draft, ctx=None):
    """Ответ модели по материалам | "" (выключено/не ответила)."""
    if not enabled():
        return ""
    import kb
    frags = []
    try:
        for h in (kb.search(q, limit=3).get("results") or []):
            if h.get("snippet"):
                frags.append("[%s] %s" % (h.get("title") or "база",
                                          h["snippet"]))
    except Exception:
        pass
    if draft and not str(draft).lstrip().startswith("Не знаю"):
        frags.append("[собрано мозгом из базы/интернета] %s"
                     % str(draft)[:3000])
    if not frags:
        return ""
    msgs = [{"role": "system", "content": _SYS + "\n\nФрагменты:\n" +
             "\n\n".join(frags)[:9000]}]
    for m in (ctx or [])[-6:]:
        t = str(m.get("text") or "")[:800]
        if t and not t.startswith("💭"):
            msgs.append({"role": "user" if m.get("role") == "user"
                         else "assistant", "content": t})
    msgs.append({"role": "user", "content": str(q)[:2000]})
    try:
        out = _call(msgs)
    except Exception as e:
        store.log("LLM", "локальная модель не ответила: %s" % e)
        return ""
    return out


def _br_temp(t):
    """v3.132: температура локальной модели дышит вместе с платами (±0,1)."""
    try:
        import breath
        breath._mark("Локальная модель (если подключена)")
        return round(max(0.0, t + 0.1 * breath.s()), 2)
    except Exception:
        return t
