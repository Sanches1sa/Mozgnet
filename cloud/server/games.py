# -*- coding: utf-8 -*-
"""
games.py — v3.181: АРБИТР ИГР ОБЛАКА.

В облаке две игры: «РОК-12» (doomgame.py, /doom) и «Gubble 2» (gubble.py,
/gubble). Своя LLM облака — ОДИН игрок: она играет либо там, либо там.

Правила (заявка владельца v3.181):
  • LLM зовут в игру (кнопкой, усталостью платы, приглашением платы) —
    она идёт в ту игру, куда позвали; если та НЕДОСТУПНА (пауза, мир не
    поднялся, сломан цикл) — идёт в другую и честно пишет почему.
  • Пока LLM играет, арбитр раз в несколько секунд проверяет: игра стала
    недоступна дольше 15 с → LLM переходит в другую игру (если та доступна).
  • Плата может ПРЕДЛОЖИТЬ своей LLM сыграть в РОК-12 (want.invite_llm в
    POST api/doom/bot), а LLM — предложить плате (ставит приглашение, плата
    видит его в поле game.invite ответа /api/link и сама решает).
"""
import threading
import time

import store

_LOCK = threading.RLock()
GAMES = {"doom": "РОК-12", "gubble": "Gubble 2"}
ST = {"llm_game": None, "auto": False, "why": "", "ts": 0.0,
      "switched": 0, "bad_since": 0.0, "last_err": ""}


def _mod(name):
    import importlib
    return importlib.import_module("doomgame" if name == "doom" else "gubble")


def other(name):
    return "gubble" if name == "doom" else "doom"


def available(name):
    """Игра готова принять LLM: не на паузе и цикл мира жив."""
    try:
        return bool(_mod(name).available())
    except Exception as e:
        ST["last_err"] = "%s: %s" % (name, str(e)[:80])
        return False


def llm_where():
    return ST["llm_game"]


def llm_claim(name, why="", auto=False):
    """LLM входит в игру name (вызывается из llm_join самой игры):
    из другой игры она выходит — игрок один."""
    with _LOCK:
        cur = ST["llm_game"]
        if cur and cur != name:
            try:
                _mod(cur).llm_leave(by_arbiter=True)
            except Exception:
                pass
        ST.update(llm_game=name, auto=bool(auto), why=str(why or "")[:160],
                  ts=time.time(), bad_since=0.0)
    return True


def llm_release(name):
    with _LOCK:
        if ST["llm_game"] == name:
            ST.update(llm_game=None, auto=False, why="", bad_since=0.0)


def llm_play(prefer, why="", auto=True):
    """Позвать LLM играть: в prefer; недоступна — в другую игру."""
    prefer = prefer if prefer in GAMES else "doom"
    err = ""
    for g in (prefer, other(prefer)):
        if not available(g):
            err = "%s недоступна" % GAMES[g]
            continue
        w = why
        if g != prefer:
            w = "%s недоступна%s — играю в %s" % (GAMES[prefer], (": " + why) if why else "", GAMES[g])
            ST["switched"] += 1
            try:
                store.log("ИГРЫ", "своя LLM: " + w)
            except Exception:
                pass
        try:
            r = _mod(g).llm_join(why=w, auto=auto)
            if r.get("ok"):
                return dict(r, game=g, game_name=GAMES[g])
            err = str(r.get("error") or err)
        except Exception as e:
            err = str(e)[:100]
    return {"ok": False, "error": err or "обе игры недоступны"}


def tick():
    """Из цикла LLM каждой игры (раз в ~3 с): где LLM сейчас — доступно ли."""
    with _LOCK:
        g = ST["llm_game"]
        if not g:
            return
        if available(g):
            ST["bad_since"] = 0.0
            return
        if not ST["bad_since"]:
            ST["bad_since"] = time.time()
            return
        if time.time() - ST["bad_since"] < 15:
            return
        ST["bad_since"] = 0.0
        o = other(g)
    if available(o):
        w = "%s недоступна (пауза) — перешла в %s" % (GAMES[g], GAMES[o])
        try:
            store.log("ИГРЫ", "своя LLM: " + w)
        except Exception:
            pass
        ST["switched"] += 1
        try:
            _mod(o).llm_join(why=w, auto=True)
        except Exception:
            pass


def status():
    g = ST["llm_game"]
    return {"llm_game": g, "llm_game_name": GAMES.get(g or "", ""),
            "auto": ST["auto"], "why": ST["why"], "switched": ST["switched"],
            "doom_ok": available("doom"), "gubble_ok": available("gubble"),
            "since": ST["ts"]}
