"""v2.98: ПОЛНЫЕ МЫСЛИ — юнит-тесты реальных функций main.py.
Ключевые слова с волей мозга -> в скетч; текст со всех нейрон-плат;
zone LLM при отключённом мозге; полный текст в канал связи и в чат."""
import ast
import re
import threading
import time as _time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, Mock

SOURCE = (Path(__file__).resolve().parents[1] / 'python/main.py').read_text()
TREE = ast.parse(SOURCE)


def load(names, namespace):
    namespace.setdefault('THOUGHT_CHARS', 6000)
    namespace.setdefault('LOOP_TURNS', 37)
    selected = [n for n in TREE.body if isinstance(n, ast.FunctionDef)
                and n.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]),
                 'board:actual-functions', 'exec'), namespace)
    return namespace


class KeywordWillTests(unittest.TestCase):
    def env_kw(self, will, votes, zone_of, stop=frozenset()):
        return load({'brain_keywords'}, {
            're': re, 'time': _time,
            'brain_will': Mock(return_value=will),
            'nn_stems': Mock(return_value=[]),
            'nn_votes': Mock(return_value=votes),
            'zone_of': Mock(side_effect=zone_of),
            'dict_has': Mock(return_value=True),
            '_TOPIC_STOP': stop,
            '_KW_CACHE': {'key': None, 'res': [], 'ts': 0.0}})

    def test_keywords_weighted_by_will(self):
        env = self.env_kw({'Знания': 0.9, 'Музыка': 0.0},
                          {'Знания': 0.8, 'Музыка': 0.5},
                          lambda w: 'Знания' if w in ('датчик', 'нейрон')
                          else 'Музыка')
        res = env['brain_keywords']('датчик температура мотор нейрон', limit=2)
        self.assertEqual(len(res), 2)
        self.assertTrue(all(0.0 <= a <= 1.0 for _w, a in res))
        self.assertEqual(res[0][0], 'датчик')       # воля ведёт зону Знания
        self.assertEqual(res[0][1], 1.0)

    def test_keywords_short_and_stop_words_filtered(self):
        env = self.env_kw({}, {}, lambda w: 'Знания',
                          stop=frozenset({'этого'}))
        res = env['brain_keywords']('этогоabc да аа мотор данная так себе',
                                    limit=8)
        words = [w for w, _a in res]
        self.assertIn('мотор', words)
        self.assertIn('данная', words)
        self.assertNotIn('да', words)
        self.assertNotIn('аа', words)

    def test_keywords_cached(self):
        env = self.env_kw({'Знания': 0.5}, {}, lambda w: 'Знания')
        env['brain_keywords']('датчик мотор платформа', limit=4)
        calls = env['brain_will'].call_count
        env['brain_keywords']('датчик мотор платформа', limit=4)   # кэш 10 с
        self.assertEqual(env['brain_will'].call_count, calls)

    def test_kw_to_brain_pushes_words_and_stim(self):
        bc = Mock()
        env = load({'_kw_to_brain'}, {
            '_bridge_call': bc, 'time': _time,
            '_KW_STATS': {'pushed': 0, 'last': [], 'ts': 0.0}})
        n = env['_kw_to_brain']([('мотор', 0.9), ('датчик', 0.8), ('аа', 0.9)])
        self.assertEqual(n, 2)                       # «аа» короче 4 букв
        methods = [c.args[0] for c in bc.call_args_list]
        self.assertEqual(methods.count('word_push'), 2)
        self.assertIn('chat_stim', methods)
        pushed = [c.args[1] for c in bc.call_args_list if c.args[0] == 'word_push']
        self.assertIn('МОТОР', pushed)               # в словарь платы — ЗАГЛАВНЫМИ
        stim = [c.args[1] for c in bc.call_args_list if c.args[0] == 'chat_stim'][0]
        self.assertLessEqual(len(stim), 90)          # буфер скетча CHAT_OUT_SIZE


class FullTextFromBoardsTests(unittest.TestCase):
    def env_ft(self, board_words, gen_compose=None, cfg=None):
        return load({'brain_full_text', '_pick_next_sentence_seed'}, {
            'CFG': cfg if cfg is not None else {'full_thoughts': True},
            '_GEN_STATE': {'links': 99, 'uni': 1},
            'GEN_MIN_LINKS': 3,
            'brain_board_words': Mock(return_value=board_words),
            '_gen_compose': gen_compose or
            (lambda w, z, nw, h: 'Слово %s раскрыто.' % w),
            'scrub_thought': Mock(side_effect=lambda s: s),
            'nn_stems': Mock(return_value=[])})

    def test_text_assembled_from_all_boards(self):
        env = self.env_ft(['мотор', 'датчик', 'сеть'])
        text = env['brain_full_text']('seed', zone='Знания')
        for w in ('мотор', 'датчик', 'сеть'):
            self.assertIn(w, text)                   # со ВСЕХ плат
        self.assertEqual(text.count('.'), 3)

    def test_sentence_limit_respected(self):
        env = self.env_ft(['мотор', 'датчик', 'сеть', 'луч'])
        text = env['brain_full_text']('', max_sents=2)
        self.assertIn('мотор', text)
        self.assertIn('датчик', text)
        self.assertNotIn('сеть', text)

    def test_no_boards_no_text(self):
        env = self.env_ft([])
        self.assertEqual(env['brain_full_text']('seed'), '')

    def test_disabled_flag_keeps_short_phrases(self):
        env = self.env_ft(['мотор'], cfg={'full_thoughts': False})
        self.assertEqual(env['brain_full_text']('seed'), '')

    def test_dictionary_not_ready_no_text(self):
        env = load({'brain_full_text'}, {
            'CFG': {'full_thoughts': True},
            '_GEN_STATE': {'links': 1, 'uni': 0},
            'GEN_MIN_LINKS': 3,
            'brain_board_words': Mock(return_value=['мотор']),
            '_gen_compose': Mock(return_value='x'),
            'scrub_thought': Mock(side_effect=lambda s: s),
            'nn_stems': Mock(return_value=[])})
        self.assertEqual(env['brain_full_text']('seed'), '')


class ZoneLlmFallbackTests(unittest.TestCase):
    def test_llm_hint_and_memory_build_full_text(self):
        sh = MagicMock()
        sh.memory = [{'role': 'bot',
                      'text': 'Датчик температуры передаёт тепло в сеть.'}]
        env = load({'_zone_llm_full_text'}, {
            'advice_fetch': Mock(return_value={
                'hint': 'LLM советует разложить тему по шагам.',
                'keys': ['датчик', 'температура']}),
            'SH': sh, 're': re,
            '_gen_compose': Mock(return_value=''),
            'scrub_thought': Mock(side_effect=lambda s: s),
            'nn_stems': Mock(return_value=[])})
        out = env['_zone_llm_full_text']('датчик температура', zone='Знания')
        self.assertIn('LLM советует разложить тему по шагам.', out)
        self.assertIn('Что я помню', out)
        self.assertIn('Датчик температуры', out)

    def test_no_advice_no_memory_empty(self):
        sh = MagicMock()
        sh.memory = []
        env = load({'_zone_llm_full_text'}, {
            'advice_fetch': Mock(return_value=None),
            'SH': sh, 're': re,
            '_gen_compose': Mock(return_value=''),
            'scrub_thought': Mock(side_effect=lambda s: s),
            'nn_stems': Mock(return_value=[])})
        self.assertEqual(env['_zone_llm_full_text']('датчик мотор'), '')


class ThoughtTransportTests(unittest.TestCase):
    def test_thought_push_sends_full_text_to_link(self):
        env = load({'_thought_push', 'scrub_thought', '_scrub_prose'}, {
            're': re, 'time': _time,
            '_JUNK_RE': re.compile(r"(?!)"),   # заглушка модульной регулярки шума
            '_THOUGHT_LOCK': threading.Lock(),
            '_THOUGHT_Q': [], 'THOUGHT_MAX_Q': 24,
            '_LAST_THOUGHT': {}, '_LINK_Q': [],
            '_signed_hz': lambda: 0})
        text = ' '.join('слово%d тема%d суть%d' % (i, i, i) for i in range(120))
        self.assertGreater(len(text), 1200)
        env['_thought_push'](text)
        link = env['_LINK_Q'][0]['t']
        self.assertGreater(len(link), 220)           # v2.98: не короткая мысль
        self.assertEqual(link, text[:1200])          # полноценный текст облаку
        self.assertEqual(env['_THOUGHT_Q'][0], text[:3800])

    def test_on_word_keeps_recent_words(self):
        sh = MagicMock()
        nw = deque(maxlen=16)
        env = load({'on_word'}, {
            'SH': sh, '_bridge_touch': Mock(),
            '_NW_LOCK': threading.Lock(), '_NEURO_WORDS': nw})
        env['on_word']('датчик')
        env['on_word']('мотор')
        self.assertEqual(list(nw), ['датчик', 'мотор'])

    def test_kw_payload_for_link(self):
        env = load({'brain_keywords', 'brain_kw_payload'}, {
            're': re, 'time': _time,
            'brain_will': Mock(return_value={'Знания': 0.5}),
            'nn_stems': Mock(return_value=[]),
            'nn_votes': Mock(return_value={}),
            'zone_of': Mock(return_value='Знания'),
            'dict_has': Mock(return_value=True),
            '_TOPIC_STOP': frozenset(),
            '_KW_CACHE': {'key': None, 'res': [], 'ts': 0.0},
            '_LAST_THOUGHT': {'t': 'мысль про датчики плат', 'ts': 123.0}})
        p = env['brain_kw_payload']()
        self.assertTrue(p['words'])
        self.assertEqual(p['ts'], 123.0)
        self.assertIn('Знания', p['will'])


class ChatAnnounceTests(unittest.TestCase):
    def announce(self, full_src):
        gen_state = {'last': '', 'last_ts': 0.0, 'phrases': 0, 'words': 0,
                     'says': 0, 'full_src': full_src}
        env = load({'gen_register'}, {
            '_GEN_STATE': gen_state, 'time': _time,
            'nn_excite': Mock(), 'nn_stems': Mock(return_value=[]),
            'threading': threading, '_gen_d6_seq': Mock(),
            'tts_say': Mock(),
            'CFG': {'tts_on': False, 'dictate_on': False},
            'brain_remember': Mock(), 'add_message': Mock(),
            '_thought_push': Mock(),
            'dict_has': Mock(return_value=True),
            'dictation_queue': Mock(), 'SH': MagicMock()})
        env['gen_register']('Полный текст мысли со всех плат.', announce=True)
        return env

    def test_full_text_announced_as_full_message(self):
        env = self.announce('нейроны')
        args = env['add_message'].call_args.args
        self.assertEqual(args[0], 'bot')
        self.assertIn('Полный текст мысли со всех плат.', args[1])
        self.assertIn('текст собран со всех нейрон-плат', args[1])
        self.assertEqual(args[2], 'Мысль')
        env['_thought_push'].assert_called_once()

    def test_zone_llm_source_named_honestly(self):
        env = self.announce('zone LLM')
        self.assertIn('текст собрал zone LLM: мозг не подключен',
                      env['add_message'].call_args.args[1])

    def test_short_phrase_tail_unaware(self):
        env = self.announce('')
        self.assertIn('фразу собрал сам словами из словаря',
                      env['add_message'].call_args.args[1])


class WiringStaticTests(unittest.TestCase):
    def test_v298_wiring(self):
        self.assertIn('APP_VERSION = "2.114.0"', SOURCE)
        self.assertIn('"full_thoughts": True', SOURCE)
        self.assertIn('_put("kw", brain_kw_payload)', SOURCE)
        self.assertIn('_zone_llm_full_text(seed_words=phrase', SOURCE)
        self.assertIn('_brain_alive()', SOURCE)
        self.assertIn('brain_keywords(txt, limit=6)', SOURCE)
        sk = (Path(__file__).resolve().parents[1] / 'sketch/sketch.ino').read_text()
        self.assertEqual(sk.count('2.114.0;up='), 2)
        self.assertNotIn('2.97.4;up=', sk)


if __name__ == '__main__':
    unittest.main()
