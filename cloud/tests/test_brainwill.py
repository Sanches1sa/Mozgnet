from pathlib import Path
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import breath
import brainwill
import tinyllm
import boardlink

class BrainWillTests(unittest.TestCase):
    def setUp(self):
        self.signals=patch.object(breath,'_WILL',{})
        self.signals.start()
    def tearDown(self): self.signals.stop()
    def test_offline_and_stale_neutral(self):
        self.assertFalse(brainwill.state()['connected'])
        self.assertEqual(brainwill.agent(24)['step_limit'],24)
        p=brainwill.code(4,180,.5)
        self.assertEqual((p['tries'],p['max_new'],p['temperature']),(4,180,.5))
        breath._WILL['old']={'z':{'Кирпичики':1,'Обучение':1},'ts':time.time()-91}
        self.assertFalse(brainwill.state()['connected'])
        self.assertEqual(brainwill.learning()['learning_rate_multiplier'],1)
    def test_live_changes_bounded_controls(self):
        breath.note_will('neuro-12',{'Кирпичики':1,'Помощь':1,'Обучение':1})
        high=brainwill.code(4,180,.5)
        self.assertEqual((high['tries'],high['max_new']),(6,198))
        self.assertAlmostEqual(brainwill.learning()['learning_rate_multiplier'],1.1)
        self.assertLessEqual(brainwill.agent(24)['step_limit'],24)
        breath.note_will('neuro-12',{'Кирпичики':0,'Помощь':0,'Обучение':0})
        self.assertEqual(brainwill.code(4,180,.5)['tries'],2)
        self.assertEqual(brainwill.agent(24)['step_limit'],18)
        self.assertAlmostEqual(brainwill.learning()['learning_rate_multiplier'],.9)
    def test_missing_invalid_values_neutral(self):
        breath.note_will('one',{'Кирпичики':1,'Обучение':float('nan')})
        breath.note_will('two',{'Помощь':1,'Буквы':float('inf')})
        self.assertEqual(brainwill.state()['zones']['Кирпичики'],1)
        self.assertNotIn('Обучение',brainwill.state()['zones'])
        self.assertEqual(brainwill.learning()['learning_rate_multiplier'],1)
        self.assertEqual(breath.strength('Курс LLM'),1)
        self.assertEqual(breath.will_zones()['Кирпичики'],1)
    def test_text_llm_parameter_zone(self):
        breath.note_will('one',{'Буквы':1})
        import brainctl  # v3.166: строгий режим — сила 2×воля
        self.assertEqual(breath.strength('Своя LLM: параметры'),brainctl.strength(1))
        self.assertEqual(brainwill.agent(24)['agent_strength'],1)
    def test_existing_board_link_payload(self):
        with patch.object(boardlink,'_note_signal'),patch.object(boardlink,'_health'),patch.object(boardlink,'_sync_for',return_value=None):
            result=boardlink.link('will-test',will={'Кирпичики':.8,'Обучение':.9},ver='2.89.2')
        self.assertTrue(result['ok'])
        self.assertTrue(brainwill.state()['connected'])
        self.assertAlmostEqual(brainwill.learning()['learning_rate_multiplier'],1.08)
    def test_generator_actually_uses_will(self):
        if not tinyllm.HAVE_NP: self.skipTest('NumPy unavailable')
        breath.note_will('one',{'Кирпичики':1})
        generate=Mock(return_value='def f(x):\n    return x + 1\n')
        m=SimpleNamespace(ready=True,steps=5,tok=SimpleNamespace(vocab={'¢x':0}),meta={},generate_code=generate)
        with patch.object(tinyllm,'model',return_value=m):
            output=tinyllm.write_code('add one',tries=4,max_new=180)
        self.assertTrue(output)
        self.assertEqual(generate.call_count,6)
        self.assertEqual(generate.call_args.kwargs['max_new'],198)
        self.assertFalse(getattr(tinyllm._FP,'stable_code',False))
