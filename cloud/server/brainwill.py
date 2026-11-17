"""Bounded cloud policy from neuro_12's existing numeric `will` payload.

No commands are accepted from zones. Task, permissions, architecture and user
training limits remain unchanged. Missing/stale/malformed signals are neutral.
"""
import math
import time

import breath

FRESH_SECONDS = 90


def state():
    now = time.time()
    sources = []
    values = {}
    with breath._LOCK:
        boards = [(name, dict(item.get('z') or {}), item.get('ts'))
                  for name, item in breath._WILL.items()]
    for name, zones, timestamp in boards:
        try:
            age = now - float(timestamp)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(age) or not 0 <= age < FRESH_SECONDS:
            continue
        accepted = {}
        for zone, number in zones.items():
            try:
                number = float(number)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and 0 <= number <= 1:
                accepted[zone] = number
        if not accepted:
            continue
        sources.append(dict(board=name, age_seconds=round(age, 1)))
        for zone, number in accepted.items():
            values.setdefault(zone, []).append(number)
    zones = {zone: sum(numbers)/len(numbers) for zone, numbers in values.items()}
    relevant = any(z in zones for z in ('Кирпичики', 'Помощь', 'Обучение', 'Буквы'))
    return dict(connected=relevant, sources=sources, zones=zones,
                source=('neuro_12: свежая воля зон, плат %d' % len(sources) if relevant else
                        'Нет свежего сигнала нужных зон neuro_12 — нейтральный режим'))


def _strength(zones, names):
    known = [zones[name] for name in names if name in zones]
    return .5 + sum(known)/len(known) if known else 1.0


def policy(component=None):
    data = state()
    z = data['zones']
    agent = _strength(z, ('Кирпичики', 'Помощь'))
    code = _strength(z, ('Кирпичики',))
    learn = _strength(z, ('Обучение',))
    if component:
        breath._mark({'agent': 'Агент Контур', 'code': 'Своя LLM: код', 'learn': 'Курс LLM'}.get(component, component))
    return dict(data, agent_strength=round(agent,3), code_strength=round(code,3),
                learning_strength=round(learn,3), agent_effort=.5+.5*agent,
                code_effort=code, temperature_multiplier=.9+.1*code,
                learning_rate_multiplier=.8+.2*learn)


def agent(base_steps):
    p = policy('agent')
    p['step_limit'] = max(1, min(int(base_steps), round(base_steps*p['agent_effort'])))
    return p


def code(tries, max_new, temperature):
    p = policy('code')
    # Neutral means exactly the caller's settings. Live signals are bounded.
    p.update(tries=max(1,min(8,round(tries*p['code_effort']))),
             max_new=max(1,min(1024,round(max_new*(.8+.2*p['code_strength'])))),
             temperature=max(.05,min(1.2,float(temperature)*p['temperature_multiplier'])))
    return p


def learning():
    return policy('learn')
