"""Opt-in weight-tied latent recurrence for existing NumPy tinyllm inference.
No checkpoint conversion, optimizer changes or automatic training.
"""
import math
import store


def config(settings=None):
    s=store.settings() if settings is None else settings
    try:passes=max(1,min(4,int(s.get('llm_loop_passes',2))))
    except (ValueError,TypeError):passes=2
    try:mix=float(s.get('llm_loop_mix',.25))
    except (ValueError,TypeError):mix=.25
    if not math.isfinite(mix):mix=.25
    mix=max(.05,min(1.,mix))
    return dict(enabled=bool(s.get('llm_loop_on',False)),passes=passes,mix=mix)


def status():
    c=config()
    return dict(c,architecture='повторный проход по тем же блокам трансформера; общие неизменяемые веса',
                scope='свободная генерация tinyllm.sample (NumPy); обучение и стабильный режим кода без циклов',
                experimental=True,retraining_required=False,
                note='Качество без обучения рекуррентности не гарантировано; вычисления растут с числом проходов. Выключение возвращает обычный проход.')
