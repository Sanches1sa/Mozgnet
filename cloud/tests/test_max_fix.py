"""v3.176.2: скан чата МАКС считает каждое сообщение один раз; ответы не обрываются."""
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import store
import maxbot
import replytpl

MSGS = [{"timestamp": 1_700_000_000_000 + i * 1000, "sender": {"user_id": 7},
         "body": {"mid": "mid.%d" % i, "text": "Сообщение номер %d про настройку сервера" % i}}
        for i in range(73)]


def api(honour):
    def _call(method, q, *a, **k):
        if q.startswith("/chats/"):
            return {"messages_count": 0}
        m = re.search(r"&(to|from)=(\d+)", q)
        page = sorted(MSGS, key=lambda x: -x["timestamp"])
        if m and m.group(1) == honour:
            page = [x for x in page if x["timestamp"] <= int(m.group(2))]
        return {"messages": page[:50]}
    return _call


class ScanTests(unittest.TestCase):
    def run_scan(self, honour):
        st = maxbot._load(); st["scan_done"] = {}; st["bot"] = {"user_id": 1}
        st["scan"] = {"running": True}
        learned = []
        with patch.object(maxbot, "_call", api(honour)), \
                patch.object(maxbot, "_scan_eat", lambda m, *a: learned.append(m["body"]["mid"])), \
                patch.object(maxbot, "SCAN_STEP", 0), patch.object(maxbot, "_scan_watermark", lambda *a: None):
            maxbot._scan_one_chat("-1", 4000, __import__("time").time())
        return maxbot._load()["scan"], learned

    def test_api_ignores_paging_counts_once(self):
        sc, learned = self.run_scan(honour="none")
        self.assertEqual(sc["msgs"], 50)            # было: 50 × число страниц
        self.assertEqual(sc["chat_total"], 50)

    def test_paging_with_to_reads_whole_history_once(self):
        sc, learned = self.run_scan(honour="to")
        self.assertEqual(sc["msgs"], 73)
        self.assertEqual(len(set(learned)), len(learned))

    def test_paging_with_from(self):
        sc, _ = self.run_scan(honour="from")
        self.assertEqual(sc["msgs"], 73)


class SendTests(unittest.TestCase):
    def test_long_reply_not_cut(self):
        store.update_settings({"tpl_parts": 2})
        body = " ".join("Предложение номер %d о том, как настроить сеть." % i for i in range(600))
        sent = []
        with patch.object(maxbot, "send", lambda c, t: sent.append(t) or True), \
                patch.object(replytpl, "is_new_dialog", lambda *a: False):
            maxbot.send_reply("-1", body)
        joined = " ".join(sent)
        self.assertNotIn("сокращено", joined)
        self.assertIn("номер 599", joined)
        self.assertTrue(all(len(x) <= maxbot.MAX_REPLY for x in sent))

    def test_send_splits_over_limit(self):
        calls = []
        with patch.object(maxbot, "_call", lambda *a, **k: calls.append(a[2]["text"]) or {}), \
                patch.object(maxbot, "_claim", lambda *a, **k: True), patch.object(maxbot, "SEND_GAP", 0):
            maxbot.send("-2", "\n\n".join("Абзац %d. " % i + "слово " * 60 for i in range(40)))
        self.assertGreater(len(calls), 1)
        self.assertIn("Абзац 39", calls[-1])
        self.assertTrue(all(len(c) <= maxbot.MAX_REPLY + 2 for c in calls))


if __name__ == "__main__":
    unittest.main()
