# -*- coding: utf-8 -*-
"""
learnall.py — обучение всех нейросетей на материалах (v3.144, переделано)

Кнопка «Обучить все сети на материалах» в панели.

v3.143 (первая версия) делала три вещи, которые ломали ответы и обучение:
  • статьи базы клала платам ПОДТВЕРЖДЁННЫМИ ПАРАМИ «вопрос → ответ»
    (вопрос = заголовок статьи, ответ = её текст). Эти пары попадали в
    память ответов облака и плат. У сообщений из чатов МАКС заголовок —
    это вопрос человека, текст — тот же вопрос, поэтому бот МАКС и облако
    стали ОТВЕЧАТЬ ВОПРОСОМ НА ВОПРОС;
  • своей LLM вызывала retrain_now(), то есть УДАЛЯЛА обученную модель и
    начинала обучение с нуля (отсюда план на 135 000 шагов и 200+ суток);
  • сеть материалов учила дважды: прямо (mn_learn) и через очередь мозга
    (brain_train_start(force=True)).

Теперь:
  1) память ответов чистится от таких пар (qalearn.purge_bad);
  2) сеть материалов облака — очередь мозга, только НОВЫЕ статьи (уже
     выученные не повторяются; вопросы из чатов не учатся);
  3) своя LLM НЕ сбрасывается: обучена — дообучается только на новом
     (learn_new); не дообучена — продолжает свой план с места остановки;
  4) платам — МАТЕРИАЛЫ отдельным каналом (/api/brain/knowledge →
     materials): плата учит ими сеть материалов, зонную сеть и тензор
     мысли (логика зон; поведение от частоты/воли/дыхания), но не
     считает материалы готовыми ответами;
  5) статус честно показывает, сколько шагов LLM осталось, сколько длится
     шаг и из чего он состоит.

delete_materials() — как было: статьи базы + сеть материалов (+ очередь
материалов для плат). Веса LLM и настройки не трогает.
"""
from __future__ import print_function

import threading
import time

import store

_LOCK = threading.Lock()
_ST = {"running": False, "phase": "", "done": 0, "total": 0, "ok": True,
       "error": "", "started": 0.0, "finished": 0.0, "log": [], "result": {}}


def status():
    with _LOCK:
        st = dict(_ST)
    st["llm"] = llm_report()
    return st


def _log(msg):
    msg = str(msg)[:300]
    with _LOCK:
        _ST["log"] = (_ST.get("log") or [])[-40:] + [{"ts": time.time(), "msg": msg}]
        _ST["phase"] = msg
    try:
        store.log("ОБУЧЕНИЕ", msg)
    except Exception:
        pass


def _set(**kw):
    with _LOCK:
        _ST.update(kw)


def _fmt_dur(s):
    s = int(max(0, s))
    if s >= 2 * 86400:
        return "%.1f сут" % (s / 86400.0)
    if s >= 3600:
        return "%d ч %d мин" % (s // 3600, (s % 3600) // 60)
    return "%d мин" % max(1, s // 60)


def llm_report():
    """Честная сводка обучения своей LLM: шаги, время шага, сколько осталось
    и почему долго."""
    try:
        import tinyllm
        s = tinyllm.status() or {}
    except Exception as e:
        return {"ok": False, "text": "своя LLM недоступна: %s" % e}
    steps = int(s.get("steps") or 0)
    total = int(s.get("total_steps") or 0)
    step_s = s.get("step_s")
    opt_s = s.get("step_opt_s")
    left = max(0, total - steps)
    eta = s.get("eta_s")
    if not eta and step_s and left:
        eta = left * float(step_s)
    parts = []
    if s.get("deleted"):
        parts.append("модель удалена — учится, только когда нажмут «Переобучить»")
    elif not s.get("ready"):
        parts.append("модель ещё не обучена")
    if total:
        parts.append("шаг %d из %d" % (steps, total))
    if step_s:
        parts.append("шаг ≈ %.1f с" % float(step_s))
    if eta and left:
        parts.append("осталось ≈ %s" % _fmt_dur(eta))
    if s.get("inc_active"):
        parts.append("дообучение на новом: %s/%s" % (s.get("inc_done"), s.get("inc_total")))
    sk = s.get("corpus_skip") or {}
    if sk:
        parts.append("не вошло в обучение: вопросов из чатов %d, повторов %d, коротких реплик %d"
                     % (sk.get("q", 0), sk.get("dup", 0), sk.get("short", 0)))
    why = ""
    try:
        if opt_s and step_s and float(step_s) >= 2 and float(opt_s) > 0.5 * float(step_s):
            why = ("из %.1f с шага %.1f с уходит на обновление весов (Adam) — его время "
                   "не зависит от числа примеров: модель велика для этой памяти "
                   "(веса или состояние Adam на диске)" % (float(step_s), float(opt_s)))
    except (TypeError, ValueError):
        pass
    cap = s.get("plan_cap")
    try:
        cap_i = int(float(cap or 0))
    except (TypeError, ValueError):
        cap_i = 0
    if not cap_i and eta and float(eta) > 2 * 86400:
        why = (why + "; " if why else "") + (
            "потолок шагов плана выключен (llm_plan_steps = 0): план идёт по всему "
            "корпусу. С потолком 3000 шагов план ≈ %s, остальное доучивается "
            "по частям" % _fmt_dur(3000 * float(step_s or 10)))
    return {"ok": True, "steps": steps, "total": total, "step_s": step_s,
            "opt_s": opt_s, "eta_s": eta, "plan_cap": cap_i,
            "skip": sk, "text": " · ".join(parts), "why": why}


def _all_articles(limit=1000000):
    import kb
    try:
        return kb.list_articles(limit=limit, include_devices=True) or []
    except Exception:
        return []


def _materials_for_boards(articles, max_items=300):
    """Статьи -> материалы для плат (без вопросов из чатов и повторов)."""
    import kb
    import textutils
    out = []
    for meta in articles:
        aid = meta.get("id")
        if not aid:
            continue
        try:
            a = kb.article(aid, device=meta.get("device") or None) or {}
        except Exception:
            continue
        text = str(a.get("text") or "").strip()
        if len(text) < 80 or textutils.question_only(text[:1500]):
            continue
        out.append({"title": str(a.get("title") or "")[:160], "text": text[:3000]})
        if len(out) >= max_items:
            break
    return out


def _quality(meta, title, text):
    """Ценность статьи для обучения: обычный материал выше реплик чата,
    содержательный текст выше короткого. Ниже нуля — шум, не учить."""
    import textutils
    t = text.strip()
    if len(t) < 40 or textutils.question_only(t[:1500]) or \
            textutils.echo_of(title, t[:400]) and textutils.question_only(title):
        return -1.0
    src = str(meta.get("source") or "").lower()
    chat = any(w in src for w in ("макс", "история чата", "группа", "telegram", "телеграм"))
    q = min(1.0, len(t) / 1500.0)
    if not chat:
        q += 1.0
    if "\n" in t or ":" in t:
        q += 0.2                       # инструкция / список шагов
    return q


def _mn_noise(doc, text):
    """Материал сети — шум: вопрос или просьба вместо знания."""
    import mozgnet
    import textutils
    if not mozgnet.trusted(doc):
        return False                   # мысли плат и LLM — не трогаем
    t = (text or "").strip()
    return len(t) < 20 or textutils.question_only(t[:1500])


def _train_mozgnet(arts, full=False, step_n=20):
    """Сеть материалов облака — обучение по плану, как у LLM:
      1) проверка ДО (отложенная выборка: находит ли сеть материал по его
         заголовку в первых трёх — аналог loss у LLM);
      2) чистка: забыть материалы-вопросы и просьбы, пересчитать граф;
      3) отбор корпуса: без вопросов, повторов и реплик, по ценности;
      4) эпоха шагами по step_n статей (прогресс виден), выученное не
         повторяется (full — сбросить kb-материалы и пройти заново всё);
      5) проверка ПОСЛЕ и сохранение сети на диск."""
    import hashlib
    import brain
    import kb
    out = {"full": bool(full)}
    with brain._MN_LOCK:
        net = brain.mn()
        out["before"] = net.self_test()
        out["forgot"] = net.forget(_mn_noise)
        # v3.149.5: «переучить всё» — сначала убрать из сети ВСЕ материалы
        # из базы знаний (id kb:* или src «база знаний»), иначе learn()
        # пропускает уже известные did и галочка «заново» ничего не делает.
        if full:
            out["relearn"] = net.forget(
                lambda d, _t: (
                    str(d.get("id") or "").startswith("kb:")
                    or "база" in str(d.get("src") or "").lower()
                    or str(d.get("src") or "").lower().startswith("kb")
                ))
        have = {d.get("id") for d in net.docs}
        have_t = {str(d.get("title") or "")[:120] for d in net.docs}
        cap = int(getattr(net, "docs_max", 3000))
    if full:
        _log("сеть материалов: РЕЖИМ «переучить всё» — забыто из базы %d; "
             "проверка до — %s; шум — %d"
             % (out.get("relearn") or 0, _acc(out["before"]), out["forgot"]))
        # сбросить отметки «уже учили» для brain_train, чтобы платформа
        # тоже могла пройти статьи снова
        try:
            done = brain._bt_load()
            done.clear()
            brain._bt_save()
        except Exception:
            pass
    else:
        _log("сеть материалов: проверка до — %s; забыто материалов-вопросов: %d"
             % (_acc(out["before"]), out["forgot"]))
    # корпус
    cand, seen = [], set()
    for meta in arts:
        aid = meta.get("id")
        if not aid:
            continue
        did = "kb:" + str(aid)
        if not full and (did in have or str(meta.get("title") or "")[:120] in have_t):
            continue                   # уже в сети (статья учится и при добавлении)
        try:
            a = kb.article(aid, device=meta.get("device") or None) or {}
        except Exception:
            continue
        title, text = str(a.get("title") or ""), str(a.get("text") or "")
        q = _quality(meta, title, text)
        if q < 0:
            continue
        h = hashlib.md5(" ".join(text.lower().split())[:3000].encode("utf-8", "replace")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        cand.append((q, did, title, text))
    # лучшие — последними: сеть подрезает самые старые, ценное остаётся
    cand.sort(key=lambda x: x[0])
    cand = cand[-cap:]
    out["corpus"] = len(cand)
    # на всякий случай: перед учёбой ещё раз снять кандидатов по id
    # (если между forget и learn что-то успело записаться)
    if full and cand:
        ids = {c[1] for c in cand}
        with brain._MN_LOCK:
            n2 = brain.mn().forget(lambda d, _t: d.get("id") in ids)
            out["relearn"] = int(out.get("relearn") or 0) + int(n2 or 0)
    _set(done=0, total=len(cand))
    _log("сеть материалов: в плане %d статей (шагов %d)%s"
         % (len(cand), -(-len(cand) // step_n) if cand else 0,
            ", full=да" if full else ""))
    learned = 0
    skipped = 0
    for i in range(0, len(cand), step_n):
        with brain._MN_LOCK:
            net = brain.mn()
            for _q, did, title, text in cand[i:i + step_n]:
                # если id всё ещё есть — заменить: забыть и выучить снова
                if net._doc_idx(did) >= 0:
                    net.forget(lambda d, _t, _id=did: d.get("id") == _id)
                r = net.learn(text[:20000], title=title[:120], src="база знаний", did=did)
                if r and r.get("doc"):
                    learned += 1
                else:
                    skipped += 1
            brain._MN_DIRTY[0] = True
        try:
            done_ids = brain._bt_load()
            for _q, did, _t, _x in cand[i:i + step_n]:
                done_ids.add(did[3:])
        except Exception:
            pass
        _set(done=min(len(cand), i + step_n))
        time.sleep(0.02)               # ответы на вопросы не ждут обучения
    out["learned"] = learned
    out["skipped"] = skipped
    with brain._MN_LOCK:
        out["after"] = brain.mn().self_test()
        out["docs"] = len(brain.mn().docs)
    try:
        brain._bt_save()
        brain.mn_save(force=True)
    except Exception:
        pass
    _log("сеть материалов: выучено %d (пропущено %d); проверка после — %s%s"
         % (learned, skipped, _acc(out["after"]),
            ("; забыто перед full %d" % out["relearn"]) if full else ""))
    return out


def mozgnet_trusted(d):
    import mozgnet
    return mozgnet.trusted(d)


def _acc(t):
    if not t or t.get("acc") is None:
        return "нет материалов"
    return "%d%% (%d из %d)" % (round(100 * t["acc"]), t["hit"], t["n"])


def _run_worker(opts):
    _set(running=True, ok=True, error="", started=time.time(), finished=0.0,
         done=0, total=0, result={}, phase="старт")
    res = {}
    full = bool((opts or {}).get("full"))
    res["full"] = full
    try:
        if full:
            _log("запуск: режим «переучить все статьи заново» (full=да)")
        # 1) память ответов — без «вопроса вместо ответа»
        try:
            import qalearn
            res["purged_pairs"] = qalearn.purge_bad()
            _log("память ответов: убрано пар «вопрос вместо ответа»: %d" % res["purged_pairs"])
        except Exception as e:
            _log("память ответов: %s" % e)

        arts = _all_articles()
        _set(total=len(arts))
        res["articles"] = len(arts)

        # 2) сеть материалов облака — обучение по плану, как у LLM
        try:
            res["mn"] = _train_mozgnet(arts, full=full)
        except Exception as e:
            res["mn"] = {"error": str(e)[:200]}
            _log("сеть материалов: %s" % e)

        # 2б) память инструкций из чатов МАКС — пройти базу ещё раз
        try:
            import chatmem
            res["instr_new"] = chatmem.harvest()
            res["instr_total"] = chatmem.count()
            _log("инструкции из чатов: новых %d, всего %d" % (res["instr_new"], res["instr_total"]))
        except Exception as e:
            _log("инструкции из чатов: %s" % e)

        # 3) своя LLM — без сброса
        try:
            import tinyllm
            st = tinyllm.status() or {}
            if st.get("deleted"):
                res["llm"] = {"ok": False, "what": "модель удалена — «Переобучить» в разделе LLM"}
            elif st.get("training"):
                res["llm"] = {"ok": True, "what": "уже учится — продолжает свой план"}
            elif st.get("ready") and int(st.get("steps") or 0) >= int(st.get("total_steps") or 0):
                r = tinyllm.learn_new()
                res["llm"] = {"ok": True, "what": {
                    "started": "дообучение только на новых материалах",
                    "idle": "новых материалов нет — дообучать нечего",
                    "busy": "занята — продолжит сама",
                    "cold": "модель не обучена"}.get(r, str(r))}
            else:
                tinyllm.ensure_training()
                res["llm"] = {"ok": True, "what": "продолжает план с места остановки (без сброса)"}
            _log("своя LLM: %s" % res["llm"]["what"])
        except Exception as e:
            res["llm"] = {"ok": False, "what": str(e)[:200]}
            _log("своя LLM: %s" % e)

        # 4) платам — материалы отдельным каналом
        try:
            import unify
            mats = _materials_for_boards(arts)
            res["boards_new"] = unify.materials_put(mats)
            res["boards_total"] = len(mats)
            _log("платам: новых материалов %d (из %d годных)" % (res["boards_new"], len(mats)))
        except Exception as e:
            _log("платам: %s" % e)

        _set(result=res, ok=True, phase="готово", finished=time.time(), running=False)
        _log("готово")
    except Exception as e:
        _set(ok=False, error=str(e)[:300], running=False, finished=time.time(),
             phase="ошибка", result=res)
        _log("ошибка: %s" % e)


def start(opts=None):
    with _LOCK:
        if _ST.get("running"):
            return {"ok": False, "error": "уже идёт", "status": dict(_ST)}
        _ST["running"] = True
    th = threading.Thread(target=_run_worker, args=(opts or {},), daemon=True,
                          name="learnall")
    th.start()
    return {"ok": True, "status": status()}


def delete_materials(confirm=False):
    """Удалить ВСЕ материалы: статьи KB + сеть материалов + очередь материалов
    для плат. Веса LLM и настройки не трогает."""
    if not confirm:
        return {"ok": False, "error": "нужен confirm: true"}
    deleted = 0
    try:
        import kb
        for a in kb.list_articles(limit=1000000, include_devices=True) or []:
            try:
                if kb.delete_article(a.get("id"), a.get("device") or None):
                    deleted += 1
            except Exception:
                pass
    except Exception as e:
        return {"ok": False, "error": "kb: %s" % e, "deleted": deleted}
    mn_cleared = False
    try:
        import brain
        import mozgnet
        if brain._MN_LOCK.acquire(timeout=8.0):
            try:
                brain._MN = mozgnet.MozgNet()
                try:
                    brain._MN_PATH = brain._MN_PATH or store.p("mozgnet_state.json")
                    mozgnet.MozgNet.save_snapshot(brain._MN_PATH, brain._MN.snapshot())
                except Exception:
                    store.save_json("mozgnet_state.json", {"docs": [], "sents": [], "nodes": {},
                                                           "edges": {}, "stats": {}})
                brain._MN_DIRTY[0] = False
                mn_cleared = True
            finally:
                brain._MN_LOCK.release()
    except Exception:
        pass
    try:
        import unify
        unify.materials_clear()
    except Exception:
        pass
    store.log("МАТЕРИАЛЫ", "удалены все: статей %d, сеть очищена=%s" % (deleted, mn_cleared))
    return {"ok": True, "deleted": deleted, "mozgnet_cleared": mn_cleared}
