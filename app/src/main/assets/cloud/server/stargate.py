# -*- coding: utf-8 -*-
"""stargate.py — возможность для ИИ смотреть сериал «Звёздные врата».

Использует существующий media.py (субтитры, описания с YouTube/Rutube/VK).
«Смотреть» = читать субтитры + название + описание и учить в базу знаний.

Поддерживает:
- поиск эпизодов по названию / номеру сезона-серии
- целенаправленное «просмотр» и обучение
- посев базовых знаний о сериале
- API для запуска просмотра
"""
import re
import time
import store

SERIES = "Звёздные врата"
ALIASES = ("Stargate", "SG-1", "Stargate SG-1", "Звёздные врата SG-1")


def seed_knowledge():
    """Посеять базовые знания о сериале в KB."""
    try:
        import kb
        articles = [
            ("Звёздные врата (сериал)",
             "«Звёздные врата» (Stargate SG-1) — научно-фантастический сериал "
             "о команде, исследующей другие миры через устройство «звёздные врата». "
             "Главные герои: Джек О'Нилл, Саманта Картер, Дэниел Джексон, Тил'к. "
             "Сериал сочетает военную фантастику, мифологию и исследования."),
            ("Звёздные врата — устройство",
             "Звёздные врата — древнее устройство, создающее стабильную червоточину "
             "между планетами. Адрес состоит из 7 символов (шеврон). "
             "На Земле врата находятся в комплексе SGC под горой Шайенн."),
            ("Команда SG-1",
             "Основная команда: полковник Джек О'Нилл (лидер), "
             "капитан/майор Саманта Картер (астрофизик), "
             "доктор Дэниел Джексон (археолог и лингвист), "
             "Тил'к (джаффа, бывший первый помощник Апофиса)."),
        ]
        added = 0
        for title, text in articles:
            kb.add_article(title, text, source="stargate_seed")
            added += 1
        return {"ok": True, "added": added}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def search_episodes(query="", season=None, episode=None, limit=5):
    """Найти ролики/эпизоды по запросу."""
    import media
    q = SERIES
    if season and episode:
        q += f" сезон {season} серия {episode}"
    elif season:
        q += f" сезон {season}"
    if query:
        q += " " + str(query)
    # добавляем английский вариант для лучшего поиска
    q_en = "Stargate SG-1"
    if season and episode:
        q_en += f" S{int(season):02d}E{int(episode):02d}"
    vids = media.find_videos(q, limit=limit) or []
    if len(vids) < limit:
        vids += media.find_videos(q_en, limit=limit - len(vids)) or []
    # уникализировать по id
    seen = set()
    out = []
    for v in vids:
        vid = v.get("id") or v.get("url")
        if vid and vid not in seen:
            seen.add(vid)
            out.append(v)
    return out[:limit]


def watch_episode(query="", season=None, episode=None, lang="ru"):
    """Найти и «посмотреть» эпизод (прочитать субтитры и выучить)."""
    import media
    vids = search_episodes(query, season, episode, limit=3)
    if not vids:
        return {"ok": False, "error": "эпизоды не найдены", "query": query}
    results = []
    for v in vids:
        url = v.get("url") or v.get("id")
        if not url:
            continue
        watched = media.watch(url, lang=lang)
        if watched.get("ok"):
            # учить в KB
            learn = media.learn_video(watched, why="stargate")
            results.append({
                "title": watched.get("title"),
                "ok": True,
                "has_transcript": bool(watched.get("transcript")),
                "chars": len(watched.get("transcript") or ""),
                "learned": learn.get("ok", False) if isinstance(learn, dict) else False,
            })
            # достаточно одного хорошего
            if watched.get("transcript"):
                break
        else:
            results.append({"title": v.get("title"), "ok": False,
                            "error": watched.get("error")})
    if not results:
        return {"ok": False, "error": "не удалось посмотреть"}
    best = next((r for r in results if r.get("has_transcript")), results[0])
    return {"ok": best.get("ok", False), "watched": results, "best": best}


def watch_series_sample(n=3):
    """Посмотреть несколько случайных/первых эпизодов для ознакомления."""
    samples = [
        {"season": 1, "episode": 1, "query": "Children of the Gods"},
        {"season": 1, "episode": 2},
        {"query": "Stargate SG-1 best episodes"},
    ]
    out = []
    for s in samples[:n]:
        r = watch_episode(**s)
        out.append(r)
        time.sleep(1)  # не спамить
    return {"ok": True, "samples": out}


def status():
    return {
        "series": SERIES,
        "aliases": ALIASES,
        "can_watch": True,
        "method": "субтитры + описание через media.py",
    }
