# -*- coding: utf-8 -*-
"""
neuro_doom.py — v2.69: мозг платы играет в «РОК-12» (DOOM-подобная игра
облака, neuro12-cloud v3.127+).

ЖЕЛАНИЕ ИГРАТЬ (решает плата, не облако). Считается каждые несколько
секунд из состояния мозга:
  + скука     — давно никто не писал в чат платы (растёт за 2…15 мин);
  + напряжение — «гравитация мыслей»: сеть напряжена, ей нужна разрядка;
  + темперамент — частота генератора скетча: живой ритм — охотнее;
  + приглашение — владелец позвал из панели (зовёт, но не заставляет);
  − занятость — с платой сейчас разговаривают;
  − усталость — сколько уже наиграно подряд (отдых снимает её);
  − досада   — частые смерти подряд.
Порог входа 0.55, выхода 0.30 (гистерезис, чтобы не дёргаться).
Каждое решение — с причиной словами («скучно 14 мин, напряжение 0.62»).

КАК ИГРАЕТ. Каждые ~0.12 с плата получает от облака то, что видит её
морпех: 7 лучей до стен, ближайший враг (угол, дальность), предмет, куда
идти к выходу, здоровье, броня, патроны, боль, «застрял». Ответ — поворот,
шаг, шаг вбок, выстрел. Действие = РЕФЛЕКСЫ (врождённое: повернуться к
врагу и стрелять, обходить стены, идти к выходу) + ПОПРАВКА СВОЕЙ НЕЙРОСЕТИ
(20 входов → 12 скрытых → 4 выхода) + разведочный шум.
УЧЁБА — REINFORCE со следами (eligibility traces): награда игры (убил,
подобрал, продвинулся к выходу; ранен, погиб, застрял) усиливает те
поправки сети, после которых стало лучше. Веса — в neuro12_doom.json,
переживают перезапуск.
"""
import json
import math
import os
import random
import time

N_IN = 20
N_H = 12
N_OUT = 4
JOIN_AT = 0.55
LEAVE_AT = 0.30


def _clip(v, lo=-1.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


class DoomBrain:
    def __init__(self, path):
        self.path = path
        rnd = random.Random(12)
        self.W1 = [[rnd.gauss(0, 0.25) for _ in range(N_IN)] for _ in range(N_H)]
        self.b1 = [0.0] * N_H
        # выходной слой — с нуля: сначала играют рефлексы, сеть учится поправкам
        self.W2 = [[0.0] * N_H for _ in range(N_OUT)]
        self.b2 = [0.0] * N_OUT
        self.e1 = [[0.0] * N_IN for _ in range(N_H)]
        self.e2 = [[0.0] * N_H for _ in range(N_OUT)]
        self.eb2 = [0.0] * N_OUT
        self.lr = 0.004
        self.lam = 0.9
        self.base = 0.0
        self.st = {"steps": 0, "updates": 0, "reward": 0.0, "kills": 0,
                   "deaths": 0, "levels": 0, "sessions": 0, "play_s": 0.0,
                   "avg_r": 0.0, "best_avg": 0.0}
        self.playing = False
        self.desire = 0.0
        self.why = ""
        self.fatigue = 0.0          # 0..1
        self.frustr = 0.0           # 0..1
        self.play_start = 0.0
        self.last_eval = 0.0
        self.recent_deaths = []
        self.prev = None            # (x, h, mu, a, sigma) прошлого шага
        self.t = 0.0
        self.dirty = False
        self.last_save = time.time()
        self.load()

    # ------------------------------------------------------------ память
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if len(d.get("W1", [])) == N_H and len(d["W1"][0]) == N_IN:
                self.W1, self.b1 = d["W1"], d["b1"]
                self.W2, self.b2 = d["W2"], d["b2"]
                self.base = float(d.get("base", 0.0))
            self.st.update(d.get("st") or {})
        except Exception:
            pass

    def save(self, force=False):
        if not (self.dirty or force) or (not force and time.time() - self.last_save < 60):
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2,
                           "base": self.base, "st": self.st, "ver": 1}, f)
            os.replace(tmp, self.path)
            self.dirty = False
            self.last_save = time.time()
        except Exception:
            pass

    def reset(self):
        try:
            os.remove(self.path)
        except Exception:
            pass
        self.__init__(self.path)

    # ------------------------------------------------------------ желание
    def want(self, mood, now=None):
        """mood: idle_s, talk_s (с последнего сообщения человека), strain,
        hz, invite, game_on, cloud_game. -> (join: bool, desire, why)."""
        now = now or time.time()
        dt = min(30.0, now - (self.last_eval or now))
        self.last_eval = now
        if self.playing:
            self.fatigue = min(1.0, self.fatigue + dt / 900.0)    # 15 мин — устал
        else:
            self.fatigue = max(0.0, self.fatigue - dt / 600.0)    # 10 мин отдыха
        self.recent_deaths = [t for t in self.recent_deaths if now - t < 180]
        self.frustr = min(1.0, len(self.recent_deaths) / 6.0)
        if not mood.get("game_on", True):
            self.desire, self.why = 0.0, "игра выключена в настройках платы"
            return False, 0.0, self.why
        if not mood.get("cloud_game"):
            self.desire, self.why = 0.0, "в облаке нет игры (нужно облако v3.127+)"
            return False, 0.0, self.why
        idle_min = float(mood.get("idle_s") or 0) / 60.0
        bored = _clip((idle_min - 2.0) / 13.0, 0.0, 1.0)
        strain = _clip(float(mood.get("strain") or 0), 0.0, 1.0)
        hz = abs(float(mood.get("hz") or 0))
        temper = _clip(hz / 120.0, 0.0, 1.0)
        invite = 1.0 if mood.get("invite") else 0.0
        talk = 1.0 if float(mood.get("talk_s") or 1e9) < 120 else 0.0
        d = (0.45 * bored + 0.25 * strain + 0.15 * temper + 0.40 * invite
             - 0.6 * talk - 0.7 * self.fatigue - 0.35 * self.frustr)
        if self.playing:
            d += 0.10                      # раз уж начал — доиграть немного
        d = round(_clip(d, 0.0, 1.0), 3)
        parts = []
        if bored > 0.05:
            parts.append("скучно (никто не пишет %d мин)" % int(idle_min))
        if strain > 0.2:
            parts.append("сеть напряжена %.2f" % strain)
        if temper > 0.3:
            parts.append("генератор бодрый %d Гц" % int(hz))
        if invite:
            parts.append("владелец позвал")
        if talk:
            parts.append("со мной разговаривают")
        if self.fatigue > 0.2:
            parts.append("устал %d%%" % int(100 * self.fatigue))
        if self.frustr > 0.2:
            parts.append("досада: смертей за 3 мин %d" % len(self.recent_deaths))
        self.desire = d
        self.why = ", ".join(parts) or "нет причин"
        join = d >= (LEAVE_AT if self.playing else JOIN_AT)
        return join, d, self.why

    # ------------------------------------------------------------ восприятие
    def _vec(self, o, mood):
        r = o.get("rays") or [1.0] * 7
        x = [1.0 - float(v) for v in r[:7]]               # близость стен
        e = o.get("enemy")
        if e:
            x += [1.0, float(e["rel"]), _clip(1.0 - float(e["d"]) / 14.0, 0, 1),
                  min(1.0, float(o.get("enemies") or 0) / 4.0)]
        else:
            x += [0.0, 0.0, 0.0, 0.0]
        it = o.get("item")
        if it:
            x += [1.0, float(it["rel"]), _clip(1.0 - float(it["d"]) / 10.0, 0, 1)]
        else:
            x += [0.0, 0.0, 0.0]
        g = o.get("goal")
        x += [float(g["rel"]) if g else 0.0]
        x += [float(o.get("hp") or 0) / 100.0, min(1.0, float(o.get("ammo") or 0) / 40.0),
              min(1.0, float(o.get("pain") or 0) / 20.0), 1.0 if o.get("stuck") else 0.0,
              _clip(abs(float(mood.get("hz") or 0)) / 120.0, 0, 1)]
        return x[:N_IN] + [0.0] * (N_IN - len(x))

    def _reflex(self, o):
        """Врождённое: к врагу — и огонь; стены обходить; идти к выходу."""
        r = o.get("rays") or [1.0] * 7
        left, front, right = min(r[0:3]), r[3], min(r[4:7])
        e, it, g = o.get("enemy"), o.get("item"), o.get("goal")
        turn = move = strafe = 0.0
        fire = -3.0
        if e:
            rel = float(e["rel"])
            turn = _clip(rel * 4.0)
            if abs(rel) < 0.05 and (o.get("ammo") or 0) > 0:
                fire = 3.0
            d = float(e["d"])
            move = -0.5 if (e.get("kind") == "fang" and d < 3.0) else (0.5 if d > 6 else 0.0)
            strafe = 0.7 * math.sin(self.t * 1.7)
        elif it and (float(it["d"]) < 4.0 or (o.get("hp") or 100) < 50):
            turn = _clip(float(it["rel"]) * 4.0)
            move = 1.0 if abs(float(it["rel"])) < 0.25 else 0.3
        elif g:
            turn = _clip(float(g["rel"]) * 4.0)
            move = 1.0 if abs(float(g["rel"])) < 0.3 else 0.25
        if front < 0.12:
            move = min(move, 0.0)
            turn += 0.8 if right > left else -0.8
        strafe += _clip((left - right) * -1.2, -0.6, 0.6) if min(left, right) < 0.15 else 0.0
        if o.get("stuck"):
            turn += random.choice((-1.0, 1.0))
            move = -0.6
        return [_clip(turn), _clip(move), _clip(strafe), fire]

    # ------------------------------------------------------------ шаг
    def act(self, obs, reward, mood):
        """Награда — за ПРОШЛОЕ действие; -> новое действие (dict)."""
        self.t += 0.12
        if self.prev is not None and reward is not None:
            self._learn(float(reward))
        if not obs or obs.get("dead"):
            self.prev = None
            return {"turn": 0, "move": 0, "strafe": 0, "fire": False}
        x = self._vec(obs, mood)
        h = [math.tanh(sum(w * v for w, v in zip(self.W1[j], x)) + self.b1[j])
             for j in range(N_H)]
        out = [sum(w * v for w, v in zip(self.W2[k], h)) + self.b2[k] for k in range(N_OUT)]
        base = self._reflex(obs)
        mu = [_clip(base[k] + 0.6 * math.tanh(out[k]), -1.2, 1.2) for k in range(3)]
        # разведка: живой генератор (Гц) и мало опыта — больше пробует
        hzn = _clip(abs(float(mood.get("hz") or 0)) / 120.0, 0, 1)
        sigma = 0.12 + 0.10 * hzn + 0.15 * math.exp(-self.st["updates"] / 20000.0)
        sigma *= 1.0 + 0.3 * float(mood.get("breath") or 0.0)   # v2.74: дыхание
        a = [_clip(mu[k] + random.gauss(0, sigma)) for k in range(3)]
        flog = base[3] + 2.0 * math.tanh(out[3])
        pf = 1.0 / (1.0 + math.exp(-flog))
        fire = random.random() < pf
        self.prev = (x, h, mu, a, sigma, out, pf, fire)
        self.st["steps"] += 1
        weapon = 0
        ws = obs.get("weapons") or [1]
        e = obs.get("enemy")
        if 2 in ws and e and float(e["d"]) < 5 and obs.get("weapon") != 2:
            weapon = 2
        elif obs.get("weapon") == 2 and (obs.get("ammo") or 0) == 0:
            weapon = 1
        return {"turn": round(a[0], 3), "move": round(a[1], 3),
                "strafe": round(a[2], 3), "fire": bool(fire), "weapon": weapon}

    def _learn(self, r):
        """Награда пришла за действие ДО прошлого (облако применяет действие
        в момент запроса и отдаёт награду, набранную под предыдущим), поэтому
        сначала награда применяется к следам, и только потом в следы
        добавляется последнее действие."""
        self.st["reward"] += r
        self.st["avg_r"] = 0.999 * self.st["avg_r"] + 0.001 * r
        adv = r - self.base
        self.base = 0.99 * self.base + 0.01 * r
        if abs(adv) > 1e-6:
            step = _clip(self.lr * adv, -0.02, 0.02)
            for k in range(N_OUT):
                wk, ek = self.W2[k], self.e2[k]
                for j in range(N_H):
                    wk[j] = _clip(wk[j] + step * _clip(ek[j], -5, 5), -3, 3)
                self.b2[k] = _clip(self.b2[k] + step * _clip(self.eb2[k], -5, 5), -3, 3)
            for j in range(N_H):
                wj, ej = self.W1[j], self.e1[j]
                for i in range(N_IN):
                    wj[i] = _clip(wj[i] + 0.3 * step * _clip(ej[i], -5, 5), -3, 3)
            self.st["updates"] += 1
            self.dirty = True
        # след последнего действия (градиент log π по весам)
        x, h, mu, a, sigma, out, pf, fire = self.prev
        g = [0.0] * N_OUT
        for k in range(3):
            g[k] = (a[k] - mu[k]) / (sigma * sigma) * 0.6 * (1.0 - math.tanh(out[k]) ** 2)
        g[3] = ((1.0 if fire else 0.0) - pf) * 2.0 * (1.0 - math.tanh(out[3]) ** 2)
        lam = self.lam
        for k in range(N_OUT):
            ek = self.e2[k]
            for j in range(N_H):
                ek[j] = lam * ek[j] + g[k] * h[j]
            self.eb2[k] = lam * self.eb2[k] + g[k]
        for j in range(N_H):
            dh = sum(g[k] * self.W2[k][j] for k in range(N_OUT)) * (1.0 - h[j] * h[j])
            ej = self.e1[j]
            for i in range(N_IN):
                ej[i] = lam * ej[i] + dh * x[i]

    def on_events(self, events, now=None):
        now = now or time.time()
        for ev in events or ():
            s = str(ev)
            if s.startswith("убил"):
                self.st["kills"] += 1
            elif s.startswith("погиб"):
                self.st["deaths"] += 1
                self.recent_deaths.append(now)
                for row in self.e1 + self.e2:        # смерть — следы обнулить
                    for i in range(len(row)):
                        row[i] = 0.0
            elif "уровень" in s and "пройден" in s:
                self.st["levels"] += 1

    def info(self):
        s = dict(self.st)
        s.update(playing=self.playing, desire=self.desire, why=self.why,
                 fatigue=round(self.fatigue, 2), frustr=round(self.frustr, 2),
                 net="%d→%d→%d" % (N_IN, N_H, N_OUT),
                 w_norm=round(math.sqrt(sum(v * v for row in self.W2 for v in row)), 3))
        s["reward"] = round(s["reward"], 2)
        s["avg_r"] = round(s["avg_r"], 4)
        s["play_s"] = int(s["play_s"])
        return s
