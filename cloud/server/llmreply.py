# -*- coding: utf-8 -*-
# ===========================================================================
# llmreply.py — ОТВЕТ СВОЕЙ LLM ПО ШАБЛОНУ С ЖИРНЫМИ РАЗДЕЛАМИ (v3.45)
# ---------------------------------------------------------------------------
# Заявка владельца: «своя LLM генерирует ответ по шаблону: приветствие,
# мысли, потом отредактированный текст из Интернета или базы знаний и
# вывод; разделы подведены жирным». Для ответов из базы знаний и интернета:
#   **Приветствие** — шаблонное «Добрый день, Иван!» (replytpl), только в
#                     начале диалога;
#   **Мысли**       — мысль своей LLM по теме (слова по словарю со
#                     склонениями, фантазия, не факт) + РЕЛЕВАНТНЫЕ мысли
#                     плат: мозг облака и мозг плат думают вместе; мысль LLM
#                     уходит и в мозг, и на платы (D6);
#   **Ответ**       — текст из базы знаний/интернета (дубли строк убраны,
#                     аккуратно обрезан по концу предложения);
#   **Вывод**       — итог: фраза своей LLM по фактам (если она опирается на
#                     факты и слова настоящие), иначе самое подходящее
#                     предложение фактов.
# Не трогает ответы математики, кода, команд, «Не знаю» и короткие ответы.
# Выключатель: llm_link_tpl (по умолчанию включено).
# ===========================================================================
import re
import time

import store
import textutils

SKIP_START = ("Не знаю", "⚠")
_STOPQ = frozenset("что такое кто это как где когда почему какой какая "
                   "какие сколько зачем расскажи объясни".split())


def enabled():
    s = store.settings()
    return bool(s.get("llm_link_tpl", True)) and bool(s.get("llm_link_on",
                                                            True))


def _words(t):
    return [w for w in re.findall(r"[а-яёa-z]{4,}", str(t).lower())
            if w not in _STOPQ]


def _facts(reply):
    out = []
    for ln in str(reply).splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("_", "🤖", "📎", "Источник", "#")):
            continue
        out.append(ln)
    return " ".join(out)[:1500]


def _dedupe(text):
    seen, out = set(), []
    for ln in str(text).splitlines():
        k = re.sub(r"\W+", "", ln.lower())
        if k and k in seen:
            continue
        seen.add(k)
        out.append(ln)
    return "\n".join(out).strip()


def board_thoughts(question, k=2):
    """Релевантные мысли ПЛАТ (не своей LLM) -> [(плата, текст)]."""
    q = set(w[:5] for w in _words(question))
    if not q:
        return []
    skip = ("облако", "учитель", "Мысли облака", "своя LLM")
    res = []
    try:
        for t in store.thoughts_list(80):
            if t.get("kind") == "llm" or t.get("board") in skip:
                continue
            tx = str(t.get("text") or "").strip()
            if len(tx) < 20:
                continue
            common = q & set(w[:5] for w in _words(tx))
            if common:
                res.append((len(common), float(t.get("ts") or 0),
                            str(t.get("board") or "плата")[:30], tx[:260]))
    except Exception:
        return []
    res.sort(key=lambda x: (-x[0], -x[1]))
    out, seen = [], set()
    for _n, _ts, b, tx in res:
        key = tx[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append((b, tx))
        if len(out) >= k:
            break
    return out


def brain_lead():
    """v3.58: мозг ведёт, LLM дополняет (по умолчанию включено)."""
    return bool(store.settings().get("llm_brain_lead", True))


def own_thought(question, brain_ctx=""):
    """Мысль своей LLM (словарная генерация) -> (текст, качество) | ("",0).
    Хорошая мысль публикуется в мозг и на платы. v3.58: если есть материал
    мозга (мысли плат, найденные факты) — LLM ДОПОЛНЯЕТ его (генерация по
    этому материалу), а не фантазирует сама по теме."""
    try:                       # v3.166: LLM отталкивается от мысли мозга
        import brainctl
        if brainctl.strict():
            _th = brainctl.current_thought(question)
            if _th and _th["text"][:40] not in str(brain_ctx or ""):
                brain_ctx = (_th["text"] + " " + str(brain_ctx or "")).strip()
    except Exception:
        pass
    try:
        import llmlink
        import tinyllm
        best, bq = "", -1.0
        try:
            import strict
            _st = strict.on()
        except Exception:
            _st = False
        if brain_ctx and brain_lead():
            t, i = tinyllm.compose(str(question)[:120], brain_ctx[:1500])
            if t and len(t.split()) >= 3 and \
                    float(i.get("grounded") or 0) >= llmlink.cfg()["gen_minq"]:
                best, bq = t, float(tinyllm.quality(t))
        if _st and best and not strict.text_ok(best, question, 4):
            strict.block("мысль LLM", best)
            best, bq = "", -1.0
        for _ in range(0 if (best or _st) else 3):   # v3.122: без фантазий
            t, _i = tinyllm.sample(str(question)[:120], temp=0.7)
            if t:
                q = float(tinyllm.quality(t))
                if q > bq:
                    best, bq = t, q
        if not best or bq < llmlink.cfg()["minq"]:
            return "", 0.0
        try:
            llmlink.publish_thought(best, str(question)[:80],
                                    source="ответ", quality=bq)
        except Exception:
            pass
        return best, bq
    except Exception:
        return "", 0.0


def _key_sentence(question, facts):
    q = set(w[:5] for w in _words(question))
    best, bs = "", 0
    for sn in re.split(r"(?<=[.!?])\s+", facts):
        sn = sn.strip()
        if len(sn) < 25 or len(sn) > 300 or sn.startswith(("Именно про", "(")):
            continue
        sc = len(q & set(w[:5] for w in _words(sn)))
        if sc > bs:
            best, bs = sn, sc
    return best


def conclusion(question, facts):
    """(текст, пометка). Фраза LLM по фактам, иначе ключевое предложение."""
    try:
        import llmlink
        import tinyllm
        dl = getattr(write_answer, "deadline", 0)
        if dl and time.time() > dl:
            raise TimeoutError("лимит времени ответа")
        t, i = tinyllm.compose(question, facts)
        if t and len(t.split()) >= 3 and llmlink._words_ok(t, facts) and \
                float(i.get("grounded") or 0) >= llmlink.cfg()["gen_minq"]:
            return t, "составлено своей LLM, опора на факты %d%%" % int(
                100 * float(i["grounded"]))
    except Exception:
        pass
    k = _key_sentence(question, facts)
    if k:
        return k, "главное из найденного"
    return "", ""


def full_mode():
    """v3.68: ответ пишут мозг + своя LLM целиком, без «Мыслей» и «Вывода»."""
    return bool(store.settings().get("llm_answer_full", True))


_CITE = re.compile(r"\s*\[\s*(?:\d+|[а-яa-z]{1,3}\.?)\s*\]")


_ABBR = re.compile(r"(?:\b[А-ЯЁA-Z]|\b(?:г|гг|в|вв|им|ул|д|стр|т|тыс|млн|млрд|"
                   r"см|рис|напр|др|проф|акад|св|пр|обл|р|руб))\.$")


def _split_sents(line):
    """Предложения без разрыва на инициалах и сокращениях («А. И. Мельников»)."""
    parts = re.split(r"(?<=[.!?])\s+", str(line))
    out = []
    for p in parts:
        if out and _ABBR.search(out[-1]):
            out[-1] += " " + p
        else:
            out.append(p)
    return out



def order_ok(t, src, need=0.7):
    """v3.126: своя LLM пишет словами факта — и порядок слов должен
    сохраняться. «1713 — Карлом санкция, австрийским и императором
    эрцгерцогом VI Габсбургом» — те же слова, но перемешаны: это бред.
    Пара соседних слов ответа годится, если в факте второе идёт после
    первого не дальше чем через 3 слова."""
    tw = [w[:5] for w in _words(t)]
    sw = [w[:5] for w in _words(src)]
    if len(tw) < 3:
        return True
    pos = {}
    for i, w in enumerate(sw):
        pos.setdefault(w, []).append(i)
    good = tot = 0
    for a, b in zip(tw, tw[1:]):
        tot += 1
        ok = any(0 < j - i <= 3 for i in pos.get(a, ()) for j in pos.get(b, ()))
        good += ok
    return good >= need * tot


def _honest_mode():
    try:
        return bool(store.settings().get("answer_honest", True))
    except Exception:
        return True


def free_mode():
    """v3.175: СВОЯ LLM ПИШЕТ СВОБОДНО (по умолчанию ВКЛ.). Раньше при
    «Честном ответе» каждое предложение оставалось дословным («предложений
    от LLM: 0 из 5»), а её фразу отбрасывали за любое слово не из факта.
    Теперь LLM переписывает предложения ответа как хочет; отсеивается только
    пустое, «каша из букв» и дословный повтор. Опора на факты не запрет, а
    подпись: в конце ответа — средняя опора, источники остаются ссылками."""
    try:
        return bool(store.settings().get("llm_free", True))
    except Exception:
        return True


def summary_ok(t, facts):
    """v3.129: пересказ своей LLM — только связный: слова целиком из фактов
    (без обрубков «малокон.») и порядок слов как в фактах."""
    fw = set(re.findall(r"[а-яёa-z0-9]+", str(facts).lower()))
    tw = re.findall(r"[а-яёa-z0-9]+", str(t).lower())
    if len(tw) < 4 or any(len(w) >= 4 and w not in fw for w in tw):
        return False
    return order_ok(t, facts, 0.8)


def on_topic(question, sent):
    """v3.126: «Вывод» из остатка — только если в нём есть слово вопроса."""
    q = set(w[:5] for w in _words(question) if len(w) > 3)
    return bool(q & set(w[:5] for w in _words(sent)))

def _clean_fact(t):
    t = _CITE.sub("", str(t))
    t = re.sub(r"^Также в «[^»]+»:\s*", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _max_sents():
    try:
        import store
        return max(2, min(30, int(store.settings().get("llm_answer_sents", 10))))
    except Exception:
        return 10


def _passage_mode():
    """v3.165: ответ связным куском статьи подряд (как «Любопытство»)."""
    try:
        import store
        return bool(store.settings().get("answer_passage", True))
    except Exception:
        return True


def write_answer(question, reply, bt=(), max_sents=None, topical=True):
    """Ответ целиком: мозг выбирает самые нужные предложения (база знаний/
    интернет + мысли плат), своя LLM переписывает каждое по этим же фактам
    (слова только из факта, словарь, опора на факты); не получилось или
    получилось короче/хуже — остаётся предложение мозга как есть.
    -> (текст, всего_предложений, из_них_от_LLM)."""
    max_sents = max_sents or _max_sents()
    cand = []
    try:
        import kb as _kb
        junk = _kb.junk_sentence
    except Exception:
        junk = lambda _s: False                      # noqa: E731
    try:
        is_cmd = _kb._is_cmd
    except Exception:
        is_cmd = lambda _s: False                    # noqa: E731
    for ln in str(reply).splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("_", "🤖", "📎", "Источник", "#", "**",
                                    "Именно про", "Нашёл в интернете", "(")):
            continue
        for sn in _split_sents(_clean_fact(ln)):
            sn = sn.strip()
            if not topical:              # честный разговор: короткие фразы — тоже
                if len(sn) >= 4:
                    cand.append(sn)
                continue
            if not (25 <= len(sn) <= (900 if "$" in sn else 420)) or junk(sn):
                continue
            # v3.85: заголовок страницы («Как установить 7zip: установка,
            # настройка и работа») — не ответ: нет точки в конце и есть «:»
            # или он короткий. Команды (sudo apt …) — оставляем.
            if not re.search(r"[.!?…»)]$", sn) and not is_cmd(sn) and \
                    "$" not in sn and (":" in sn or len(sn.split()) <= 12):
                continue                 # v3.110: строка с формулой — не заголовок
            cand.append(sn)
    n_src = len(cand)                   # v3.165: предложения самого источника
    for _b, tx in bt:
        tx = _clean_fact(tx)
        if len(tx) >= 25:
            cand.append(tx)
    if not cand:
        return "", 0, 0, cand
    q = set(w[:5] for w in _words(question))
    want_f = bool(re.search(r"формул|уравнени|выражени|как (?:вычисл|рассчит|"
                            r"посчит)", str(question), re.I))
    ask_date = bool(re.search(r"\d{3,4}|год|когда|дат[аеу]|век", str(question),
                              re.I))
    ask_def = not ask_date or bool(re.search(r"кто|что\s+так|расскаж|объясни",
                                             str(question), re.I))
    sc = []
    try:
        import freqprior
        zlev = freqprior.level()
    except Exception:
        zlev = None
    for i, sn in enumerate(cand):
        rel = len(q & set(w[:5] for w in _words(sn)))
        sco = float(rel)
        if re.match(r"^\s*\d{3,4}\b", sn) and not ask_date:
            sco -= 1.5               # «1828 — …»: перечень дат, не суть вопроса
        if ask_def and re.search(r"^[^.]{3,80}?\s[—–-]\s|\b(?:был|была|является|"
                                 r"это)\b", sn[:120]):
            sco += 1.0               # определение: «Минин — староста…»
        sco -= 0.05 * i              # при равенстве — ближе к началу статьи
        if want_f and re.search(r"\$[^$]*=[^$]*\$", sn):
            sco += 2.0               # v3.110: спросили формулу — она в ответе
        try:                         # v3.73: опора на частоту плат (±0.3)
            import freqprior
            sco += 0.3 * freqprior.bonus(sn, z=zlev)
        except Exception:
            pass
        sc.append((-sco, i, sn))
    # v3.74: предложение без единого слова вопроса — не ответ («архиватор на
    # Бештау» -> про конденсаторы монитора). Остаются только по теме; нет
    # таких — ответа нет (мозг честно скажет об этом по-старому).
    if q and topical:
        rels = [len(q & set(w[:5] for w in _words(x[2]))) for x in sc]
        best = max(rels or [0])
        # и не слабее половины лучшего совпадения: «Бештау» одно не делает
        # предложение про конденсаторы ответом на вопрос про архиватор
        ok_ = [x for x, r_ in zip(sc, rels) if (r_ > 0 and r_ >= 0.5 * best and
               (len(q) < 3 or r_ >= 0.34 * len(q) or r_ >= 2)) or
               (want_f and r_ > 0 and re.search(r"\$[^$]*=[^$]*\$", x[2]))]
        if not ok_:
            # v3.94: ни одно предложение не набрало порог — берём те, где есть
            # САМОЕ РЕДКОЕ (главное) слово вопроса, что встречается в фактах:
            # «Архиватор 7-Zip сжимает файлы…» на вопрос про архиватор
            try:
                rar = {}
                for w_ in _words(question):
                    st_ = _kb._stems(w_)
                    if st_ and st_[0] not in _kb._STOP_STEMS:
                        rar[w_[:5]] = _kb.stem_idf(st_[0])
                inc = set()
                for x in sc:
                    inc |= q & set(w[:5] for w in _words(x[2]))
                # главное слово должно быть и в НАЗВАНИИ источника ответа
                # («Источник: «Архиватор»»), иначе это случайное совпадение
                ttl = set()
                for t_ in re.findall(r"Источник[^«\n]*«([^»]+)»", str(reply)):
                    ttl |= set(w[:5] for w in _words(t_))
                cand_k = [k_ for k_ in inc if k_ in rar and k_ in ttl]
                if cand_k:
                    key_ = max(cand_k, key=lambda k_: rar[k_])
                    ok_ = [x for x in sc if key_ in
                           set(w[:5] for w in _words(x[2]))]
            except Exception:
                ok_ = []
        # команды (sudo apt …) идут вместе с найденным ответом
        sc = ok_ + [x for x in sc if x not in ok_ and is_cmd(x[2])] if ok_ else []
        if not sc:
            write_answer.last_why = {}
            return "", len(cand), 0, cand
        # v3.165: КАК «ЛЮБОПЫТСТВО» — связный кусок статьи подряд: от первого
        # предложения по теме и дальше по тексту (как читает человек), а не
        # только предложения со словом вопроса вразброс. Раньше выпадали
        # «Из углекислого газа и воды образуются глюкоза и кислород» — в них
        # нет слова «фотосинтез», хотя это и есть рассказ о нём.
        if _passage_mode():
            idx = sorted(x[1] for x in sc if x[1] < n_src)
            try:                 # начало — по основам слов («дыра» = «дыры»)
                qst = set(_kb._stems(question)) - _kb._STOP_STEMS
                for j in range(0, idx[0] if idx else 0):
                    if qst & set(_kb._stems(cand[j])):
                        idx = [j] + idx
                        break
            except Exception:
                pass
            if idx:
                lim = max_sents if max_sents > 10 else min(max_sents, 7)
                win, chars = [], 0
                for j in range(idx[0], n_src):
                    sn_ = cand[j]
                    if len(win) >= lim or (win and chars + len(sn_) > 1200):
                        break
                    win.append(j)
                    chars += len(sn_)
                keep = set(win)
                for j in idx:                # дальние по теме — если есть место
                    if len(keep) >= lim:
                        break
                    keep.add(j)
                sc = [(-1.0, j, cand[j]) for j in sorted(keep)] + \
                    [x for x in sc if x[1] >= n_src]
    sc.sort()
    pick = sorted(sc[:max_sents], key=lambda x: x[1])    # в исходном порядке
    try:
        import llmlink
        import tinyllm
        minq = llmlink.cfg()["gen_minq"]
    except Exception:
        llmlink = tinyllm = None
        minq = 0.5
    out, n_llm, seen = [], 0, set()
    free = free_mode()
    try:     # v3.85: на всю работу LLM в ответе — не дольше llm_answer_budget_s
        budget = float(store.settings().get(
            "llm_free_budget_s" if free else "llm_answer_budget_s",
            15 if free else 6) or 0)
    except (TypeError, ValueError):
        budget = 15.0 if free else 6.0
    grounds = []
    t_llm0 = time.time()
    why = {}                                  # причина, почему не LLM -> сколько
    used = []
    for _r, _i, sn in pick:
        key = re.sub(r"\W+", "", sn.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        used.append(sn)
        use = sn
        if _honest_mode() and not free:   # v3.129: факт — дословно из источника
            why["дословно из источника"] = why.get("дословно из источника", 0) + 1
        elif "$" in sn or "\\" in sn:       # v3.107: формулу LLM не переписывает
            why["формула — как есть"] = why.get("формула — как есть", 0) + 1
        elif tinyllm is not None and budget and time.time() - t_llm0 > budget:
            why["лимит времени ответа"] = why.get("лимит времени ответа", 0) + 1
        elif tinyllm is not None:
            try:
                # если в факте нет слов вопроса (определение, продолжение
                # рассказа) — LLM давал «пусто»; тогда вопросом служит начало
                # самого факта: она переписывает его, а не ищет ответ
                qe = str(question)[:120]
                if not (q & set(w[:5] for w in _words(sn))):
                    qe = " ".join(sn.split()[:6])
                t, i = tinyllm.compose(qe, sn, min_frac=0.0 if free else 0.7)
                g = float(i.get("grounded") or 0)
                if free and i.get("llm") == "ok" and t:
                    # v3.175: пишет, что хочет — отсев только явного брака
                    tw = re.sub(r"\W+", "", t.lower())
                    if len(t.split()) < 3:
                        r_ = "слишком коротко"
                    elif getattr(tinyllm, "has_garbage", lambda _t: False)(t):
                        r_ = "каша из букв"
                    elif tw == re.sub(r"\W+", "", sn.lower()):
                        r_ = "повторила факт дословно"
                    else:
                        r_ = ""
                        use, n_llm = t, n_llm + 1
                        grounds.append(g)
                elif i.get("llm") == "cold":
                    r_ = "LLM ещё не обучена"
                elif i.get("llm") == "err":
                    r_ = "ошибка генерации"
                elif not t:
                    r_ = "нет слов вопроса в факте"
                elif not llmlink._words_ok(t, sn):
                    r_ = "слова вне факта"
                elif not order_ok(t, sn):
                    r_ = "перемешан порядок слов"
                elif g < minq:
                    r_ = "слабая опора на факт (%d%%<%d%%)" % (
                        int(100 * g), int(100 * minq))
                elif len(t.split()) < max(3, int(0.6 * len(sn.split()))):
                    r_ = "вышло короче факта"
                else:
                    r_ = ""
                    use, n_llm = t, n_llm + 1
                if r_:
                    why[r_] = why.get(r_, 0) + 1
            except Exception:
                why["ошибка"] = why.get("ошибка", 0) + 1
        out.append(use)
    rest = [x for x in cand if x not in used]     # не вошло в ответ — на вывод
    write_answer.last_why = why
    write_answer.ground = (sum(grounds) / len(grounds)) if grounds else None
    write_answer.deadline = t_llm0 + budget if budget else 0
    txt = ""
    for s_ in out:                       # команды — отдельной строкой
        if is_cmd(s_):
            txt = txt.rstrip() + "\n" + s_ + "\n"
        else:
            txt += ("" if not txt or txt.endswith("\n") else " ") + s_
    return txt.strip(), len(out), n_llm, rest


def build(text, reply, zone="", sender="", chat_id=None):
    """-> (новый ответ, info|None). None — шаблон не применим."""
    r = str(reply or "")
    if not enabled() or len(r) < 80 or r.lstrip().startswith(SKIP_START):
        return reply, None
    # v3.87: служебные строки и заголовки страниц — не ответ (и в запасном
    # шаблоне тоже): «Именно про … ничего нет — вот общее…», «Как установить
    # 7zip: установка, настройка и работа»
    try:
        import kb as _kb
        keep = []
        for ln in r.splitlines():
            s_ = ln.strip()
            if s_.startswith("Именно про") or (
                    s_ and not s_.startswith(("_", "•", "-", "*", "#", "`")) and
                    len(s_) < 160 and not re.search(r"[.!?…»)]$", s_) and
                    ":" in s_ and not _kb._is_cmd(s_)):
                continue
            keep.append(ln)
        r2 = "\n".join(keep).strip()
        if len(r2) >= 40:
            r = r2
    except Exception:
        pass
    facts = _facts(r)
    if len(facts) < 60:
        return reply, None
    sec = []
    try:                                   # Приветствие (в начале диалога)
        import replytpl
        key = "llm:%s" % (chat_id or sender or "web")
        if replytpl.is_new_dialog(key):
            sec.append("**Приветствие**\n" + replytpl.greet_line(sender))
            replytpl.touch(key)
    except Exception:
        pass
    th = []
    bt = board_thoughts(text)
    try:                 # v3.129: честно — мысли плат не факты для ответа
        import brain as _b_
        if _b_._honest():
            bt = []
    except Exception:
        pass
    if full_mode():                        # v3.68: мозг + LLM пишут ответ целиком
        try:
            ans, n_all, n_llm, rest = write_answer(
                text, r, () if zone == "Разговор" else bt,
                topical=(zone != "Разговор"))
        except Exception:
            ans, n_all, n_llm, rest = "", 0, 0, []
        if ans:
            srcs = [ln.strip() for ln in r.splitlines()
                    if ln.strip().startswith(("Источник", "_Источник"))]
            sec.append("**Ответ**\n" + ans)
            try:                  # Вывод: тоже мозг + LLM; из того, что не вошло
                if not rest:
                    raise ValueError("нечем сделать вывод")
                cn, note = conclusion(text, " ".join(rest))
                if not cn and rest and on_topic(text, rest[0]):
                    cn, note = rest[0], "главное из оставшегося"
                if cn and _clean_fact(cn)[:50] not in ans:
                    sec.append("**Вывод**\n%s\n_(%s)_" % (_clean_fact(cn),
                                                          note))
                elif rest:
                    cn2, note2 = conclusion(text, " ".join(rest))
                    if not cn2 and on_topic(text, rest[0]):
                        cn2, note2 = rest[0], "главное из оставшегося"
                    if cn2:
                        sec.append("**Вывод**\n%s\n_(%s)_"
                               % (_clean_fact(cn2), note2))
            except Exception:
                pass
            # v3.111: своя LLM не переписала ни одного предложения (формулы,
            # слабая опора, лимит времени) — пусть скажет СВОЁ по фактам ответа
            lsum, lwhy = None, ""
            if n_llm == 0 and not any("составлено своей LLM" in x for x in sec):
                lsum, lwhy = _llm_summary(text, ans)
                if lsum and not free_mode() and not summary_ok(lsum[0], ans):    # v3.129
                    lsum, lwhy = None, "пересказ вышел несвязным — не показываю"
                if lsum:
                    sec.append("**Своя LLM**\n%s\n_(своими словами по фактам "
                               "ответа, опора %d%%)_" % (lsum[0], lsum[1]))
            try:                  # v3.166: своя LLM пишет от мысли мозга neuro-12
                import brainctl
                bsec = brainctl.section(text, ans)
                if bsec:
                    sec.append(bsec)
            except Exception:
                pass
            foot = "_Составлено: мозг + своя LLM (предложений от LLM: %d из %d%s)_" \
                % (n_llm, n_all, "; её вывод — отдельно" if lsum else
                   ("; свой вывод LLM не дала: %s" % lwhy if lwhy else ""))
            wy = getattr(write_answer, "last_why", {}) or {}
            _g = getattr(write_answer, "ground", None)
            if n_llm and free_mode() and _g is not None and foot.endswith(")_"):
                foot = foot[:-2] + "; LLM пишет свободно, опора на факты ≈%d%%)_" % int(round(100 * _g))
            if n_llm == 0 and not lsum:    # v3.129: LLM ничего не дала — так и пишем
                try:                       # v3.169: не справилась — прочитает тему сама
                    import llmweb
                    import textutils
                    _kw = textutils.keywords(text, 3) or []
                    if _kw:
                        llmweb.want(" ".join(_kw[:3]), "промолчала в ответе")
                except Exception:
                    pass
                foot = ("_Составлено: предложения источника дословно (%d); своя "
                        "LLM в ответ ничего не добавила%s)_" % (
                            n_all, (": " + lwhy) if lwhy else ""))
                wy = {}
            elif wy and n_llm < n_all:     # честно: почему остальное — мозг
                foot = foot[:-2] + "; остальное — мозг: %s)_" % ", ".join(
                    "%s ×%d" % (k_, v_) for k_, v_ in sorted(
                        wy.items(), key=lambda kv: -kv[1])[:4])
            if zone:                       # какая зона мозга ответила
                foot = foot[:-2] + " · зона: %s)_" % zone \
                    if foot.endswith(")_") else foot
            try:                           # v3.85: на какой частоте плат собран ответ
                import freqprior
                zf = freqprior.level()
                if zf is not None and foot.endswith(")_"):
                    foot = foot[:-2] + " · частота плат: %+d Гц)_" % int(round(zf * 2000))
            except Exception:
                pass
            if srcs:
                foot = srcs[0] + "\n" + foot
            sec.append(foot)
            return "\n\n".join(sec), {"llm_tpl": True, "llm_full": True,
                                       "llm_tpl_thought": False,
                                       "llm_tpl_boards": len(bt),
                                       "llm_sents": n_llm}
    lead = brain_lead()
    ctx = ""
    if lead:                               # материал мозга: платы + факты
        ctx = " ".join([tx for _b, tx in bt] + [_key_sentence(text, facts)])
        try:                               # совет учителя — инструкция
            import teacher
            ctx = (teacher.instruction_for(text, 200) + " " + ctx).strip()
        except Exception:
            pass
    try:                                   # v3.122: мысли плат — только связные
        import strict
        _st_on = strict.on()
        if _st_on:
            bt = [(b, tx) for b, tx in bt if strict.text_ok(tx, text)]
    except Exception:
        _st_on = False
    # v3.124: «Не показывать бред» — строка «🤖 … своя LLM дополняет мозг» не
    # показывается вовсе (она собиралась из любых слов контекста: «beat italia
    # dashboard», «сына дэниел фокс»)
    own, oq = ("", 0.0) if _st_on else own_thought(text, ctx.strip())
    if lead:                               # мозг первым, LLM дополняет
        for b, tx in bt:
            th.append("💭 «%s» — плата «%s»" % (tx, b))
        if own:
            th.append("🤖 «%s» — своя LLM дополняет мозг" % own)
    else:
        if own:
            th.append("🤖 «%s» — своя LLM (фантазия, не факт)" % own)
        for b, tx in bt:
            th.append("💭 «%s» — плата «%s»" % (tx, b))
    if th:
        sec.append("**Мысли**\n" + "\n".join(th))
    ans = textutils.cut_at_sentence(_dedupe(r), 3000)
    sec.append("**Ответ**\n" + ans)
    cn, note = conclusion(text, facts)
    if cn:
        sec.append("**Вывод**\n%s\n_(%s)_" % (cn, note))
    try:                                   # v3.166: своя LLM от мысли мозга
        import brainctl
        bsec = brainctl.section(text, ans)
        if bsec:
            sec.append(bsec)
    except Exception:
        pass
    return "\n\n".join(sec), {"llm_tpl": True, "llm_tpl_thought": bool(own),
                              "llm_tpl_boards": len(bt)}



def wrap_brain(text, answer, board="", sender="", chat_id=None, grounded=None,
               source="", thought=True):
    """v3.97: ответ мозга neuro-12 — тем же шаблоном, что и ответы знаний
    (Приветствие в начале диалога, Ответ, подпись), без служебных фраз
    вроде «плата думает так…»."""
    sec = []
    try:
        import replytpl
        key = "llm:%s" % (chat_id or sender or "web")
        if replytpl.is_new_dialog(key):
            sec.append("**Приветствие**\n" + replytpl.greet_line(sender))
        replytpl.touch(key)
    except Exception:
        pass
    sec.append("**Ответ**\n" + str(answer or "").strip())
    who = "мозг neuro-12" + (" «%s»" % board if board else "")
    if thought:
        foot = "_Составлено: %s — фраза из его мысли_" % who
    elif grounded:
        foot = "_Составлено: %s (опора: %s)_" % (who, source or "его знания")
    else:
        foot = "_Составлено: %s (без источника)_" % who
    sec.append(foot)
    return "\n\n".join(sec)


def _greet(sender, chat_id):
    try:
        import replytpl
        key = "llm:%s" % (chat_id or sender or "web")
        if replytpl.is_new_dialog(key):
            replytpl.touch(key)
            return "**Приветствие**\n" + replytpl.greet_line(sender)
        replytpl.touch(key)
    except Exception:
        pass
    return ""


def _llm_extend(question, base):
    """Своя LLM дополняет мысль мозга по его же словам (слова — только из
    материала мозга, проверка опоры). -> текст | ""."""
    try:
        import llmlink
        import tinyllm
        t, i = tinyllm.compose(str(question)[:120], str(base)[:1500])
        if t and len(t.split()) >= 4 and llmlink._words_ok(t, base) and \
                float(i.get("grounded") or 0) >= llmlink.cfg()["gen_minq"] \
                and float(tinyllm.quality(t)) >= llmlink.cfg()["minq"]:
            try:
                import strict
                if strict.on() and not strict.text_ok(t, question, 4):
                    strict.block("LLM дополняет мысль", t)
                    return ""
            except Exception:
                pass
            return t.strip()
    except Exception:
        pass
    return ""


_OWN_WHY = [""]


def _llm_own(question):
    """v3.117: собственная мысль своей LLM по теме вопроса (речь словами
    корпуса, без петель; 3 попытки, лучшая по качеству). -> текст | ""."""
    _OWN_WHY[0] = ""
    try:
        import llmlink
        import tinyllm
        st = tinyllm.status()
        if not st.get("ready"):
            _OWN_WHY[0] = "ещё не обучена"
            return ""
        if int(st.get("tok_ver") or 1) < 2:
            _OWN_WHY[0] = "прежняя версия"
            return ""
        minq = float(llmlink.cfg().get("minq") or 0.3)
        best, bq = "", -1.0
        for _ in range(3):
            t, _i = tinyllm.sample(str(question)[:120], temp=0.7)
            if not t or len(t.split()) < 5:
                continue
            q = float(tinyllm.quality(t))
            if q > bq:
                best, bq = t, q
        if best and bq >= minq:
            return best
        _OWN_WHY[0] = ("мысль слабая (качество %.2f < %.2f)" % (bq, minq)
                       if best else "не сложила фразу")
    except Exception as e:
        _OWN_WHY[0] = "ошибка: %s" % str(e)[:50]
    return ""


def _llm_why():
    return _OWN_WHY[0] or "нечего добавить"


_ECHO = re.compile(r"^\s*(?:Вопрос|Ответ)\s*:|\bОтвет\s*:\s*(?:Запомнил|"
                   r"Запомнила)|^\s*Запомнил[аи]?\b", re.I)


def _llm_summary(question, answer):
    """v3.111/v3.112: фраза своей LLM по фактам готового ответа ->
    (текст, опора%) | (None, причина). Формулы ей не даём (их она не
    переписывает). Три попытки, берётся лучшая по опоре. Порог опоры для
    СВОЕЙ фразы мягче, чем для переписывания предложения (настройка
    llm_summary_minq, по умолчанию 0,35): слова всё равно только из фактов,
    повтор предложения ответа и «каша из букв» отсеиваются, а опора
    честно подписывается процентом."""
    try:
        import llmlink
        import tinyllm
        facts = re.sub(r"\$[^$]*\$|\[ФОРМУЛА\][\s\S]*?\[/ФОРМУЛА\]", " ",
                       str(answer or ""))
        facts = re.sub(r"\s+", " ", facts).strip()
        if len(facts) < 40:
            return None, "в ответе почти одни формулы"
        try:
            minq = float(store.settings().get("llm_summary_minq", 0.35))
        except (TypeError, ValueError):
            minq = 0.35
        sents = [set(re.findall(r"[а-яё]{3,}", sn.lower()))
                 for sn in re.split(r"(?<=[.!?])\s+", facts)]
        best, why = None, "LLM молчит"
        _free = free_mode()
        for k in range(1 if _honest_mode() and not _free else 3):   # v3.135: быстрее — одна попытка
            q_ = str(question)[:120] if k != 1 else " ".join(facts.split()[:8])
            t, i = tinyllm.compose(q_, facts[:1500])
            if i.get("llm") == "cold":
                return None, "LLM ещё не обучена"
            if not t or len(t.split()) < 4:
                why = "пустая фраза"
                continue
            if tinyllm.has_garbage(t):
                why = "каша из букв"
                continue
            if not _free and not llmlink._words_ok(t, facts):
                why = "слова вне фактов"
                continue
            g = float(i.get("grounded") or 0)
            wt = set(re.findall(r"[а-яё]{3,}", t.lower()))
            if wt and any(len(wt & ws) / float(len(wt)) >= 0.9 for ws in sents):
                why = "повтор предложения ответа"
                continue
            if g < minq and not _free:
                why = "опора %d%% < %d%%" % (int(100 * g), int(100 * minq))
                continue
            try:
                import strict
                if not _free and strict.on() and not strict.text_ok(t, question, 4):
                    why = "несвязно (не бред — не показываю)"
                    continue
            except Exception:
                pass
            if not best or g > best[1] / 100.0:
                best = (t.strip(), int(100 * g))
        return (best, "") if best else (None, why)
    except Exception as e:
        return None, "ошибка: %s" % str(e)[:60]


def mem_brain(text, memory, thought="", board="", sender="", chat_id=None,
              talk=False, thought_by=""):
    """v3.99: ОТВЕТ «МОЗГ + СВОЯ LLM» БЕЗ СТАТЬИ БАЗЫ ЗНАНИЙ — мозг отвечает
    сам и с опорой на память.
      memory  — [(текст, откуда)]: сеть материалов, память диалогов,
                заметки, память платы (её файлы, база на плате, воспоминания);
      thought — собственная мысль мозга по теме (плата neuro-12 или речь
                мозга облака из словаря).
    Ответ: предложения памяти, которые переписывает своя LLM (как ответы из
    базы знаний), + мысль мозга от себя, + Вывод. Памяти нет — ответ
    мыслью мозга, которую своя LLM дополняет по его же словам.
    -> (текст, info)."""
    sec = []
    g = "" if talk else _greet(sender, chat_id)   # на «привет» отвечает мозг сам
    if g:
        sec.append(g)
    mem = []
    for t, src in (memory or []):
        keep = []
        for ln in str(t or "").splitlines():
            # эхо диалогов («Вопрос: … Ответ: Запомнил…») — не память о мире
            if _ECHO.search(ln):
                continue
            keep.append(ln)
        t = "\n".join(keep).strip()
        if t:
            mem.append((t, str(src)))
    facts = "\n".join(t for t, _s in mem)
    ans, n_all, n_llm, rest = "", 0, 0, []
    if facts:
        try:
            ans, n_all, n_llm, rest = write_answer(text, facts, (),
                                                   topical=not talk)
        except Exception:
            ans = ""
    who = ("мозг neuro-12 «%s»" % board) if board else (thought_by or
                                                        "мозг облака")
    th = _clean_fact(thought) if thought else ""
    import answer_focus
    focused = answer_focus.practical(text)
    if focused and th and not answer_focus.relevant(text, th):
        th = ""
    try:                    # v3.122: «Не показывать бред» — несвязная мысль не ответ
        import strict
        _strict = strict.on()
        if _strict and th and not strict.text_ok(th, text):
            strict.block("мысль мозга", th)
            th = ""
    except Exception:
        _strict = False
    ext = ""
    body = []
    own = ""
    if ans:
        body.append(ans)
        if th and th[:40].lower() not in ans.lower():
            body.append("_%s от себя:_ %s" % (who[0].upper() + who[1:], th))
    if not ans and th:
        body.append(th)
        ext = "" if focused else _llm_extend(text, th + " " + facts)
        if ext and ext[:40].lower() not in th.lower():
            body.append(ext)
        else:
            ext = ""
            # v3.117: переработанная LLM говорит настоящими словами без
            # петель — её собственная мысль по теме идёт в ответ (подписана);
            # v3.122: при «Не показывать бред» фантазии LLM в ответ не идут
            own = "" if _strict or focused else _llm_own(text)
            if own:
                body.append("_Своя LLM от себя:_ " + own)
    if not body:
        return "", None
    sec.append(("" if talk else "**Ответ**\n") + "\n\n".join(body))
    if ans and rest:
        try:
            cn, note = conclusion(text, " ".join(rest))
            if cn and _clean_fact(cn)[:50] not in ans:
                sec.append("**Вывод**\n%s\n_(%s)_" % (_clean_fact(cn), note))
        except Exception:
            pass
    srcs = []
    for _t, src in mem:
        if src not in srcs:
            srcs.append(src)
    parts = []
    if ans:
        parts.append("память (%s)" % ", ".join(srcs[:4]))
    if th:
        parts.append(who)
    if ans or ext or own:
        parts.append("своя LLM")
    foot = "_Составлено: %s" % " + ".join(parts)
    if ans:
        foot += " (предложений от LLM: %d из %d)" % (n_llm, n_all)
    elif ext:
        foot += " (LLM дополнила мысль мозга по его словам)"
    elif own:
        foot += " (LLM добавила свою мысль по теме)"
    else:
        foot += " (своя LLM промолчала: %s)" % _llm_why()
    if not ans and not talk:
        foot += " — статьи в базе нет, это мысль мозга, не проверенный факт"
    try:
        import freqprior
        zf = freqprior.level()
        if zf is not None:
            foot += " · частота плат: %+d Гц" % int(round(zf * 2000))
    except Exception:
        pass
    sec.append(foot + "_")
    return "\n\n".join(sec), {"llm_tpl": True, "llm_full": True,
                              "mem_brain": {"memory": srcs, "board": board,
                                            "thought": bool(th),
                                            "llm_sents": n_llm,
                                            "llm_ext": bool(ext),
                                            "llm_own": bool(own)}}
