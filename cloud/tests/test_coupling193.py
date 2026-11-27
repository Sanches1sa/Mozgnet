# -*- coding: utf-8 -*-
"""v3.193: страница «Сопряжение» (воля платы ↔ воля LLM), сокращение тонких
настроек (2 селекта вместо 5 галочек пинов) и больше уроков учителя кода
(темы ИБ/администрирования/сетей, курс до 40 уроков, программа 124 урока).
Запуск: python -m unittest tests.test_coupling193 -v
"""
import copy
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

if 'NEURO_DATA' not in os.environ:
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_repairs  # noqa: F401  (ставит NEURO_DATA и поднимает общий контекст)

import breath
import brainctl
import codeteach
import coupling
import curriculum
import store


class CouplingStatusTest(unittest.TestCase):
    """Страница «Сопряжение»: воля платы и воля LLM в одном ответе."""

    def test_neutral_structure(self):
        st = coupling.status()
        self.assertTrue(st["ok"])
        self.assertIn("board", st)
        self.assertIn("llm", st)
        b, l = st["board"], st["llm"]
        for key in ("online", "boards", "zones", "gates", "gates_run",
                    "gates_stop", "strict", "lead"):
            self.assertIn(key, b)
        for key in ("free", "web_on", "fact_check", "from_thought",
                    "teacher_bank", "code_strength", "learning_strength",
                    "agent_strength", "temperature_multiplier",
                    "learning_rate_multiplier", "courses"):
            self.assertIn(key, l)
        # без плат — нейтральный режим: параметры ×1, зоны пустые
        self.assertEqual(b["online"], 0)
        self.assertFalse(b["connected"])
        self.assertEqual(b["zones"], [])
        self.assertEqual(l["code_strength"], 1.0)
        self.assertEqual(l["learning_rate_multiplier"], 1.0)

    def test_links_describe_split(self):
        st = coupling.status()
        zones = [x["zone"] for x in st["links"]]
        self.assertIn("Кирпичики", zones)
        self.assertIn("Обучение", zones)
        self.assertIn("Помощь", zones)
        for x in st["links"]:
            self.assertTrue(x["what"])
            self.assertIn("strength", x)

    def test_board_will_reaches_llm_side(self):
        """Свежая воля зон платы меняет параметры LLM (brainwill)."""
        now = time.time()
        with breath._LOCK:
            breath._WILL["плата-тест"] = {
                "z": {"Кирпичики": 1.0, "Обучение": 0.5, "Помощь": 0.2,
                      "Буквы": 0.6},
                "ts": now}
        try:
            st = coupling.status()
            b, l = st["board"], st["llm"]
            self.assertTrue(b["connected"])
            self.assertEqual(b["online"], 0)   # free_boards пуст — воля есть
            zones = {z["zone"]: z["a"] for z in b["zones"]}
            self.assertEqual(zones["Кирпичики"], 1.0)
            self.assertGreater(l["code_strength"], 1.0)
            self.assertLess(l["learning_rate_multiplier"], 1.2)
            self.assertGreater(l["agent_strength"], 0.8)
        finally:
            with breath._LOCK:
                breath._WILL.pop("плата-тест", None)

    def test_gates_listed(self):
        st = coupling.status()
        names = [g["name"] for g in st["board"]["gates"]]
        self.assertIn("Любопытство", names)

    def test_route_registered(self):
        import api_ui
        self.assertIn(("GET", "api/coupling"), api_ui.ROUTES_UI)


class CodeteachMoreLessonsTest(unittest.TestCase):
    """Уроков больше: темы 4 групп, курс до 40, банк до 500."""

    def test_topic_groups(self):
        groups = {g for g, ts in codeteach._TOPIC_GROUPS}
        self.assertEqual(groups, {"программирование",
                                  "информационная безопасность",
                                  "администрирование серверов", "сети"})
        self.assertEqual(len(codeteach._TOPICS), 27)
        self.assertEqual(len(codeteach.TOPIC_GROUP), len(codeteach._TOPICS))
        sec = [t for t, g in codeteach.TOPIC_GROUP.items()
               if g == "информационная безопасность"]
        self.assertTrue(any("Цезаря" in t for t in sec))
        adm = [t for t, g in codeteach.TOPIC_GROUP.items()
               if g == "администрирование серверов"]
        self.assertTrue(any("df" in t for t in adm))

    def test_pick_topic_returns_group(self):
        t, g = codeteach._pick_topic(0, "")
        self.assertEqual(g, "программирование")
        t, g = codeteach._pick_topic(8, "")      # первая тема ИБ
        self.assertEqual(g, "информационная безопасность")
        t, g = codeteach._pick_topic(16, "")     # первая тема админ
        self.assertEqual(g, "администрирование серверов")
        t, g = codeteach._pick_topic(23, "")     # сети
        self.assertEqual(g, "сети")
        t, g = codeteach._pick_topic(0, "шифр Цезаря")
        self.assertEqual((t, g), ("шифр Цезаря", "свои темы"))

    def test_default_and_max_lessons(self):
        codeteach._STATE = None
        store.save_json('code_teach.json', {"engine": "yandex", "bank": []})
        st = codeteach._cfg()
        self.assertEqual(st["lessons"], 12)
        out = codeteach.configure(lessons=100)
        self.assertEqual(out["lessons"], 40)
        out = codeteach.configure(lessons=0)
        self.assertEqual(out["lessons"], 1)
        codeteach.configure(lessons=12)

    def test_bank_record_carries_group(self):
        import json
        raw = json.dumps({"task": "удвой число",
                          "solution": "def double(n):\n    return n * 2",
                          "checks": ["assert double(2) == 4"]}, ensure_ascii=False)
        calls = []
        def fake_engine(st):
            def call(system, user):
                calls.append(user)
                return raw, ""
            return call, "Яндекс (YandexGPT)", ""
        def fake_boot(rows, mode, steps, seconds, source):
            return {"phase": "done", "message": "ок"}
        codeteach._STATE = None
        store.save_json('code_teach.json', {"engine": "yandex", "bank": []})
        with patch.object(codeteach, "_engine", fake_engine), \
             patch("codeboot.start", side_effect=fake_boot):
            codeteach.configure(lessons=1)
            r = codeteach.start()
            self.assertTrue(r["ok"], r)
            t0 = time.time()
            while time.time() - t0 < 10:
                st = codeteach.status()
                if not st.get("running"):
                    break
                time.sleep(0.05)
        self.assertEqual(st["phase"], "готово", st)
        bank = store.load_json('code_teach.json', {}).get("bank") or []
        self.assertEqual(len(bank), 1)
        self.assertEqual(bank[0].get("g"), "программирование")
        self.assertIn("числа и арифметика", calls[0])
        codeteach.configure(lessons=12)   # вернуть курс по умолчанию


class CurriculumMoreLessonsTest(unittest.TestCase):
    """Программа обучения расширена до 124 уроков, группы сходятся."""

    def test_total_and_groups(self):
        self.assertEqual(len(curriculum.CURRICULUM), 124)
        gs = curriculum.groups()
        self.assertEqual(sum(n for _t, n in gs), 124)
        titles = [t for t, _n in gs]
        self.assertIn("Код и данные", titles)
        self.assertIn("ИБ: облако, утечки, учения", titles)

    def test_new_lessons_content(self):
        titles = [t for t, _x in curriculum.CURRICULUM]
        for name in ("Безопасность в облаке", "Утечки данных",
                     "Ротация логов (logrotate)", "NAT", "Функция",
                     "Проверка кода (тест)"):
            self.assertIn(name, titles)
        # дубликатов заголовков нет
        self.assertEqual(len(titles), len(set(titles)))

    def test_no_import_side_effect(self):
        """Импорт модуля больше не печатает ничего в stdout."""
        import io
        import importlib
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            importlib.reload(curriculum)
        self.assertEqual(buf.getvalue(), "")


class SettingsCompactTest(unittest.TestCase):
    """Слитые селекты отправляют те же ключи настроек, что были у галочек."""

    VALUES = {  # (селект «знак», селект «кадр») -> ожидаемые ключи
        ("no", "no"): {"d6_negfreq": False, "d4_negfreq": False,
                       "d6_camframe": False, "d4_camframe": False,
                       "d6_cammode": "pure"},
        ("d6", "pure"): {"d6_negfreq": True, "d4_negfreq": False,
                         "d6_camframe": True, "d4_camframe": False,
                         "d6_cammode": "pure"},
        ("d64", "d64"): {"d6_negfreq": True, "d4_negfreq": True,
                         "d6_camframe": True, "d4_camframe": True,
                         "d6_cammode": "pure"},
        ("no", "pwm"): {"d6_negfreq": False, "d4_negfreq": False,
                        "d6_camframe": True, "d4_camframe": False,
                        "d6_cammode": "pwm"},
    }

    def test_merged_selects_map_to_same_keys(self):
        for (neg, cam), expect in self.VALUES.items():
            got = {
                "d6_negfreq": neg != 'no',
                "d4_negfreq": neg == 'd64',
                "d6_camframe": cam != 'no',
                "d6_cammode": 'pwm' if cam == 'pwm' else 'pure',
                "d4_camframe": cam == 'd64',
            }
            self.assertEqual(got, expect, (neg, cam))

    def test_settings_accept_merged_patch(self):
        base = store.settings()
        try:
            store.update_settings({"d6_negfreq": True, "d4_negfreq": True,
                                   "d6_camframe": True, "d6_cammode": "pure",
                                   "d4_camframe": True})
            s = store.settings()
            self.assertTrue(s["d6_negfreq"])
            self.assertTrue(s["d4_negfreq"])
            self.assertTrue(s["d4_camframe"])
        finally:
            store.update_settings({k: base.get(k)
                                   for k in ("d6_negfreq", "d4_negfreq",
                                             "d6_camframe", "d6_cammode",
                                             "d4_camframe")})


if __name__ == "__main__":
    unittest.main()
