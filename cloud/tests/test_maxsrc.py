"""v3.188: чаты-источники ответов МАКС-бота (maxsrc): поиск правильного
ответа по всей истории чата, без записи в базу знаний."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
os.environ.setdefault("NEURO_DATA", tempfile.mkdtemp())
import store  # noqa: E402
store.DATA_DIR = os.environ["NEURO_DATA"]
import maxsrc  # noqa: E402
import maxbot  # noqa: E402

CID = "-70000000000001"
T0 = 1780000000.0
_N = [0]


def M(uid, name, text, dt, reply=None):
    _N[0] += 1
    m = {"sender": {"user_id": uid, "first_name": name},
         "recipient": {"chat_id": int(CID), "chat_type": "chat"},
         "timestamp": int((T0 + dt) * 1000),
         "body": {"mid": "mid.%d" % _N[0], "text": text}}
    if reply:
        m["link"] = {"type": "reply", "message": {"mid": reply}}
    return m


def history():
    h = []
    # 1) вопрос про Wi-Fi: неверный ответ опровергли, верный — подтвердили
    q = M(1, "Анна", "Как подключить плату Uno Q к wifi?", 0)
    h.append(q)
    h.append(M(2, "Борис", "Через Bluetooth настройки телефона подключи", 60, reply=q["body"]["mid"]))
    h.append(M(1, "Анна", "нет, не работает", 120, reply=h[-1]["body"]["mid"]))
    good = M(3, "Вера", "Подключи плату Uno Q к wifi командой nmcli dev wifi connect ИМЯ password ПАРОЛЬ",
             300, reply=q["body"]["mid"])
    h.append(good)
    h.append(M(1, "Анна", "Спасибо, работает!", 600, reply=good["body"]["mid"]))
    # 2) болтовня
    for i in range(30):
        h.append(M(5 + i % 3, "Гость", "сегодня отличная погода, гуляем в парке %d" % i, 1000 + i * 50))
    # 3) спросивший нашёл решение сам
    q2 = M(4, "Глеб", "Почему не собирается скетч, ошибка file truncated?", 5000)
    h.append(q2)
    h.append(M(4, "Глеб", "Разобрался: удалил папку .cache/sketch и пересобрал, сработало", 6000))
    # 4) сообщение-факт без вопроса
    h.append(M(6, "Дина", "Пароль от гостевой сети в лаборатории меняется каждый понедельник", 7000))
    return h


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.update_settings({"maxbot_src_chats": [CID], "maxbot_src_on": True,
                               "maxbot_src_scope": "all"})
        maxsrc._IDX.clear()
        ix = maxsrc._load(CID)
        ix["title"] = "Лаборатория"
        maxsrc._add(ix, [maxsrc._rec(m) for m in history()])

    def test_picks_confirmed_not_refuted(self):
        r = maxsrc.answer_for("как подключить uno q к wifi", None)
        self.assertIsNotNone(r)
        self.assertEqual(r["level"], "high")
        self.assertIn("nmcli", r["text"])
        self.assertNotIn("Bluetooth", r["text"].split("\n")[1])
        self.assertIn("Вера", r["text"])
        self.assertIn("подтвердили", r["text"])
        self.assertIn("Лаборатория", r["text"])

    def test_self_solved(self):
        r = maxsrc.answer_for("скетч не собирается file truncated что делать?", None)
        self.assertIsNotNone(r)
        self.assertIn(".cache/sketch", r["text"])

    def test_fact_statement(self):
        r = maxsrc.answer_for("когда меняется пароль гостевой сети в лаборатории?", None)
        self.assertIsNotNone(r)
        self.assertIn("понедельник", r["text"])

    def test_nothing(self):
        self.assertIsNone(maxsrc.answer_for("какая столица Австралии?", None))

    def test_scope_same(self):
        store.update_settings({"maxbot_src_scope": "same"})
        try:
            self.assertIsNone(maxsrc.answer_for("как подключить uno q к wifi", "123456789"))
            self.assertIsNotNone(maxsrc.answer_for("как подключить uno q к wifi", CID))
        finally:
            store.update_settings({"maxbot_src_scope": "all"})

    def test_live_message_not_in_kb(self):
        learned = []
        maxbot.learn_text = lambda *a, **k: learned.append(a) or {"ok": True}
        maxbot._learn_from_group = lambda *a, **k: learned.append(a)
        maxbot._claim = lambda *a, **k: True
        maxbot._see_chat = lambda *a, **k: None
        m = M(9, "Ева", "Новый адрес сервера лаборатории 10.0.0.42, порт 8077", 9000)
        maxbot._on_message(m)
        self.assertEqual(learned, [])                       # не в базу знаний
        r = maxsrc.answer_for("какой адрес сервера лаборатории?", None)
        self.assertIsNotNone(r)
        self.assertIn("10.0.0.42", r["text"])

    def test_scan_refuses_source(self):
        maxbot.token = lambda: "x"
        maxbot.enabled = lambda: True
        r = maxbot.scan_history(chat_id=CID)
        self.assertFalse(r["ok"])
        self.assertIn("источником", r["error"])


class Sync(unittest.TestCase):
    def test_full_history_paging(self):
        cid = "-70000000000002"
        store.update_settings({"maxbot_src_chats": [CID, cid]})
        allm = [M(7, "Ж", "сообщение номер %d про тему" % i, i * 10) for i in range(250)]
        for m in allm:
            m["recipient"]["chat_id"] = int(cid)
        calls = []

        def fake_call(method, path, obj=None, timeout=None):
            calls.append(path)
            import urllib.parse as up
            qs = dict(up.parse_qsl(path.split("?", 1)[1]))
            to = int(qs["to"]) if "to" in qs else None
            ms = sorted(allm, key=lambda m: -m["timestamp"])
            if to is not None:
                ms = [m for m in ms if m["timestamp"] <= to]
            return {"messages": ms[:int(qs.get("count", 50))]}
        maxbot._call = fake_call
        maxbot.chat_title = lambda c: "Большой чат"
        maxsrc.PAGE_GAP = 0
        maxsrc._IDX.pop(cid, None)
        maxsrc._SYNC.clear()
        maxsrc._sync_start = maxsrc.start_sync(cid, full=True)
        import time
        for _ in range(100):
            if not maxsrc._SYNC[cid].get("running"):
                break
            time.sleep(0.05)
        ix = maxsrc._load(cid)
        self.assertEqual(len(ix["msgs"]), 250)
        self.assertTrue(ix["complete"])
        self.assertTrue(os.path.exists(maxsrc._path(cid)))
        self.assertGreaterEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
