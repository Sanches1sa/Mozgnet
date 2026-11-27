"""Run the real early firmware startup with a router stub, no Monitor service."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
SOURCE=(Path(__file__).resolve().parents[1]/'sketch/sketch.ino').read_text()
def function(signature):
 a=SOURCE.index(signature);start=SOURCE.index('{',a);depth=1;i=start+1
 while depth:
  if SOURCE[i]=='{':depth+=1
  elif SOURCE[i]=='}':depth-=1
  i+=1
 return SOURCE[a:i]
class StartupTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('g++'),'Needs host compiler')
 def test_boot_and_rpcs_without_monitor_and_before_peripherals(self):
  code=r'''
#include <string>
#include <vector>
#include <cassert>
#define USE_RB 1
class String{public:std::string v;String(const char*s):v(s){}String(unsigned long n):v(std::to_string(n)){}String operator+(const String&s)const{return String((v+s.v).c_str());}};
std::vector<std::string> events;
unsigned long millis(){return 3;}
struct Router{void begin(){events.push_back("begin");}template<class T>void notify(const char*n,T){events.push_back(n);}}Bridge;
void rpcRegister(){events.push_back("rpc");}void bootAt(int n){assert(n==1);events.push_back("boot");}
'''+function('static void startRouterBridge()')+r'''
int main(){startRouterBridge();assert((events==std::vector<std::string>{"begin","rpc","boot","on_sver","$/setMaxMsgSize"}));}
'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'test.cpp').write_text(code);r=subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);subprocess.run([str(p/'test')],check=True,capture_output=True)
  setup=function('void setup()');self.assertLess(setup.index('startRouterBridge();'),setup.index('bootAt(2)'))
  self.assertNotIn('Monitor.begin(',SOURCE);self.assertNotIn('Monitor.println(',SOURCE)
 def test_pwm_hardware_initialization_is_not_part_of_startup(self):
  setup=function('void setup()');self.assertNotIn('pcmInit(',setup);self.assertNotIn('pcmStart(',setup)
if __name__=='__main__':unittest.main()
