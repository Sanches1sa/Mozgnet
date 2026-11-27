"""Real-call activity is distinct from lexical relevance and board telemetry."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import llm_activity as work
import llm_zones
import breath
import tinyllm

class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.clock=1000.0
        self.timer=patch.object(work.time,'monotonic',side_effect=lambda:self.clock);self.timer.start()
        self.started=patch.object(work,'_STARTED',970.0);self.started.start()
        self.data=patch.object(work,'_DONE',[]);self.data.start()
        self.active=patch.object(work,'_ACTIVE',{});self.active.start()
        self.settings=patch('store.settings',return_value={'llm_zones_on':True,'coupling_zone_mix':50});self.settings.start()
        llm_zones._CACHE.update(path=None,data=None,saved=0);llm_zones._LIVE.clear()
    def tearDown(self):
        for p in (self.settings,self.active,self.data,self.started,self.timer):p.stop()
        llm_zones._LIVE.clear()
    def test_long_work_is_not_capped_at_two_percent(self):
        with work.operation('Обучение',['Обучение']):
            self.clock+=40
            a=work.snapshot()
            self.assertTrue(a['busy']);self.assertEqual(a['duty'],1)
            self.assertEqual(a['zones']['Обучение']['duty'],1)
        self.assertFalse(work.snapshot()['busy'])
        self.clock+=15;self.assertAlmostEqual(work.snapshot()['duty'],.5)
        self.clock+=16;self.assertEqual(work.snapshot()['duty'],0)
    def test_nested_and_parallel_intervals_are_not_double_counted(self):
        with work.operation('Генерация',['Буквы']):
            self.clock+=5
            with work.operation('Генерация',['Кирпичики']):self.clock+=5
            self.clock+=5
        a=work.snapshot();self.assertAlmostEqual(a['duty'],.5)
        self.assertAlmostEqual(a['zones']['Кирпичики']['duty'],1/6)
    def test_exception_cleans_active_and_idle_board_is_not_llm_work(self):
        with self.assertRaises(ValueError):
            with work.operation('Обучение',['Обучение']):raise ValueError('test')
        a=llm_zones.combined(board={'zones':{'Кирпичики':.02}})
        self.assertFalse(a['activity']['busy']);self.assertEqual(a['activity']['duty'],0)
        row=next(r for r in a['zones'] if r['zone']=='Кирпичики')
        self.assertEqual(row['board'],.02);self.assertIsNone(row['llm'])
    def test_live_context_held_during_real_long_call(self):
        llm_zones.route('',zone='Обучение')
        llm_zones._LIVE['Обучение']['ts']-=120
        with work.operation('Обучение',['Обучение']):
            self.clock+=40
            row=next(r for r in llm_zones.combined(board={'zones':{}})['zones'] if r['zone']=='Обучение')
            self.assertTrue(row['llm_busy']);self.assertEqual(row['llm_duty'],1)
            self.assertEqual(row['llm'],.5)
    def test_decorator_returns_results_and_cleans_errors(self):
        class Model:
            @work.tracked('Генерация',zone='Кирпичики')
            def generate(self,text):
                self.snapshot=work.snapshot()
                return text+'!'
        m=Model();self.assertEqual(m.generate('код python'),'код python!')
        self.assertTrue(m.snapshot['zones']['Кирпичики']['busy'])
        self.assertFalse(work.snapshot()['busy'])
    def test_real_model_training_and_direct_generation_instrumented(self):
        if not tinyllm.HAVE_NP:self.skipTest('NumPy not installed')
        import numpy as np
        c=dict(tinyllm.cfg(),llm_d=8,llm_heads=2,llm_layers=1,llm_ctx=8,llm_vocab=128,llm_disk_state=False)
        m=tinyllm.GPT(c);m.tok.build(['Код python функция возвращает число.']*2,128);m.init_params(m.tok.size);m.ready=True;m.steps=1
        original=m.grads
        def checked(*args,**kw):
            self.assertTrue(work.snapshot()['zones']['Обучение']['busy'])
            return original(*args,**kw)
        x=np.ones((1,4),dtype=np.int64)
        with patch.object(m,'grads',side_effect=checked):loss=m.train_step(x,x,.0001)
        self.assertTrue(np.isfinite(loss));self.assertFalse(work.snapshot()['busy'])
        before={k:v.copy() for k,v in m.p.items()}
        with patch('llm_zones.route',wraps=llm_zones.route) as routed:
            m.generate_code('def add(a,b):',max_new=2,seed=123)
        self.assertTrue(any(c.kwargs.get('zone')=='Кирпичики' for c in routed.call_args_list))
        for k,v in before.items():np.testing.assert_array_equal(v,m.p[k])
        self.assertFalse(work.snapshot()['busy'])

if __name__=='__main__':unittest.main()
