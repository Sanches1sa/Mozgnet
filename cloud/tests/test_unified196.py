import sys
import time
from pathlib import Path
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import store
import brain
import breath
import brainwill
import llm_zones
import coupling
import conversation
import dialog
import webfetch

class Unified196(unittest.TestCase):
    def setUp(self):
        self.signal=patch.object(breath,'_WILL',{});self.signal.start()
        llm_zones._CACHE.update(path=None,data=None,saved=0)
        self.file=patch.object(store,'load_json',return_value={});self.file.start()
        self.save=patch.object(store,'save_json');self.save.start()
        self.settings=patch.object(store,'settings',return_value={'web_on':True,'conversation_web':True,'llm_zones_on':True,'coupling_zone_mix':50});self.settings.start()
    def tearDown(self):
        for p in (self.settings,self.save,self.file,self.signal):p.stop()
        llm_zones._CACHE.update(path=None,data=None,saved=0);llm_zones._LIVE.clear()
    def test_all_zones_union_and_weight(self):
        breath.note_will('device',{'Кирпичики':1,'Новая зона':.4})
        llm_zones.route('python функция код')
        combined=llm_zones.combined()
        rows={r['zone']:r for r in combined['zones']}
        self.assertGreaterEqual(len(rows),17)
        self.assertIn('Новая зона',rows)
        z=rows['Кирпичики']
        self.assertAlmostEqual(z['unified'],(z['board']+z['llm'])/2)
        self.assertEqual(rows['Новая зона']['unified'],.4)
    def test_profiles_are_learned_and_generation_is_conditioned(self):
        llm_zones.observe('специальный термин необычный',zone='Камера')
        self.assertEqual(llm_zones.choose('специальный термин'),'Камера')
        text,zone=llm_zones.condition('специальный термин')
        self.assertEqual(zone,'Камера')
        self.assertTrue(text.startswith('Зона Камера.'))
        self.assertEqual(next(z for z in llm_zones.combined()['zones'] if z['zone']=='Камера')['examples'],1)
    def test_expired_local_and_board_signals_not_used(self):
        llm_zones.route('код python')
        for v in llm_zones._LIVE.values():v['ts']-=91
        breath._WILL['old']={'z':{'Кирпичики':1},'ts':time.time()-91}
        self.assertFalse(llm_zones.combined()['values'])
    def test_max_and_chat_unknown_go_to_web(self):
        for sender in ('владелец','МАКС: тест'):
            with patch.object(brain,'think',return_value=('Не знаю',brain.Z['sys'],{})),patch.object(webfetch,'alice',return_value=('Установка архиватора описана в документации.',{'sources':[{'url':'https://example.com'}],'mode':'pages'})) as search:
                reply,zone,info=conversation.answer('Как установить архиватор?',sender=sender)
            search.assert_called_once()
            self.assertEqual(zone,brain.Z['web'])
            self.assertTrue(info['web_retry'])
            self.assertIn('Установка архиватора описана в документации.',reply)
    def test_no_network_when_disabled_or_answer_exists(self):
        with patch.object(brain,'think',return_value=('Не знаю',brain.Z['sys'],{})),patch.object(webfetch,'alice') as search:
            conversation.answer('Как работает телескоп?',no_web=True)
            search.assert_not_called()
        with patch.object(brain,'think',return_value=('Ответ из базы',brain.Z['kb'],{})),patch.object(webfetch,'alice') as search:
            conversation.answer('Как работает телескоп?')
            search.assert_not_called()
    def test_source_required_for_replacing_answer(self):
        with patch.object(brain,'think',return_value=('Не знаю',brain.Z['sys'],{})),patch.object(webfetch,'alice',return_value=('Выдуманный ответ',{'sources':[]})):
            reply,zone,info=conversation.answer('Как работает телескоп?')
        self.assertEqual(reply,'Не знаю')
    def test_dialog_uses_shared_online_path(self):
        with patch.object(conversation,'answer',return_value=('Сетевой ответ',brain.Z['web'],{'sources':[]})) as answer,patch.object(dialog,'trace'),patch('worddict.learn'):
            messages,zone,info=dialog.respond('Как работает телескоп?',chat_key='max:1',template=False)
        self.assertFalse(answer.call_args.kwargs['no_web'])
        self.assertIn('Сетевой ответ',' '.join(messages))
    def test_verified_web_not_blocked_by_socket_probe(self):
        with patch('strict.offline_ok',return_value=False),patch('strict.online',return_value=False) as probe,patch('strict.on',return_value=True):
            reply,zone,info=brain._strict_post('вопрос','Факт с источником',brain.Z['web'],{'sources':[{'url':'https://example.com'}]})
        self.assertEqual(reply,'Факт с источником');probe.assert_not_called()
    def test_fresh_request_not_shadowed_by_history(self):
        self.assertTrue(conversation.prefer_web('Найди в интернете актуальные сведения'))
        self.assertFalse(conversation.usable_source({'text':'Не знаю'}))
