import sys, io, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import imgsense, vision_agent
from PIL import Image

class PhotoTests(unittest.TestCase):
 def setUp(self):
  buf=io.BytesIO();Image.new('RGB',(64,48),'blue').save(buf,format='PNG');self.raw=buf.getvalue()
 def run_photo(self, **kwargs):
  with patch('imgsense._llm_vision',return_value=''), patch('store.settings',return_value={'llm_zones_on':True}), patch('llm_zones.route') as route, patch('llm_zones.observe') as observe, patch('kb.add_article',return_value={'ok':True}):
   r=imgsense.describe(self.raw,structured=True,**kwargs)
   return r,route.call_args,observe.call_count
 def test_photo_routes_camera(self):
  r,route,n=self.run_photo();self.assertTrue(r['ok']);self.assertTrue(r['saved']);self.assertEqual(r['zone'],'Камера');self.assertEqual(route.kwargs['zone'],'Камера');self.assertEqual(n,1);self.assertFalse(r['capabilities']['scene']);self.assertEqual(r['description']['size'],[64,48])
 def test_no_learning(self):
  r,_,n=self.run_photo(learn=False);self.assertFalse(r['saved']);self.assertEqual(n,0)
 def test_invalid_image(self):
  r=imgsense.describe(b'bad',structured=True);self.assertFalse(r['ok']);self.assertFalse(r['saved'])
 def test_api_structured(self):
  with patch('imgsense.describe',return_value={'ok':True,'saved':False,'text':'фото'}) as desc:
   r=vision_agent.see(image_bytes=self.raw);self.assertFalse(r['saved']);self.assertTrue(desc.call_args.kwargs['structured'])
 def test_invalid_base64(self):
  self.assertFalse(vision_agent.see(b64='!!!')['ok'])
 def test_scene_marked(self):
  with patch('imgsense._llm_vision',return_value='На столе чашка.'), patch('store.settings',return_value={'llm_zones_on':False}):
   r=imgsense.describe(self.raw,learn=False,structured=True);self.assertTrue(r['capabilities']['scene']);self.assertFalse(r['zone_active'])
 def test_legacy_string(self):
  with patch('imgsense._llm_vision',return_value=''), patch('store.settings',return_value={'llm_zones_on':False}):
   self.assertIsInstance(imgsense.describe(self.raw,learn=False),str)
if __name__=='__main__':unittest.main()
