import io,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import store,photo_ocr
from PIL import Image,ImageDraw,ImageFont
class TrainingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA_DIR',self.tmp.name);self.p.start()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def image(self,i):
  im=Image.new('RGB',(150+i,50),'white');d=ImageDraw.Draw(im)
  font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',26)
  d.text((5+i,4),'A B A B',font=font,fill='black');b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
 def test_real_learning_save_reload_and_holdout(self):
  for i in range(6):photo_ocr.add(self.image(i),'A B A B')
  data=store.load_json('photo_ocr_samples.json',[])
  self.assertEqual(sum(r['split']=='validation' for r in data),2)
  photo_ocr._train(data,50)
  self.assertFalse(photo_ocr._STATE.get('error'),photo_ocr._STATE)
  metrics=photo_ocr.status()['model'];self.assertGreater(metrics['train_accuracy'],.95);self.assertGreater(metrics['validation_accuracy'],.95)
  r=photo_ocr.read(self.image(10));self.assertEqual(r['text'].replace(' ',''),'ABAB')
  photo_ocr._train(data,2);self.assertTrue(Path(self.tmp.name,'photo_ocr.npz').exists())
 def test_mismatch_rejected(self):
  with self.assertRaises(ValueError):photo_ocr.add(self.image(0),'ABCDE')
 def test_correction_does_not_cross_validation_split(self):
  photo_ocr.add(self.image(0),'ABAB');photo_ocr.add(self.image(0),'BABA')
  data=store.load_json('photo_ocr_samples.json',[]);self.assertEqual(len(data),1);self.assertEqual(data[0]['split'],'validation');self.assertEqual(data[0]['text'],'BABA')
 def test_untrained_does_not_claim_recognition(self):self.assertIsNone(photo_ocr.read(self.image(0)))
 def test_insufficient_data(self):
  with self.assertRaises(ValueError):photo_ocr.train()
if __name__=='__main__':unittest.main()
