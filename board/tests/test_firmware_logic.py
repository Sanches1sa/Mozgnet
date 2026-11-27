"""Portable compile/run of changed pure MCU logic, not a target firmware build."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest

class FirmwareLogic(unittest.TestCase):
 @unittest.skipUnless(shutil.which('g++'),'Host C++ compiler not installed')
 def test_actual_led_word_selector_and_neuron_step(self):
  src=(Path(__file__).resolve().parents[1]/'sketch/neuron_node/neuron_node.ino').read_text()
  def function(signature):
   a=src.index(signature);start=src.index('{',a);depth=1;i=start+1
   while depth:
    if src[i]=='{':depth+=1
    elif src[i]=='}':depth-=1
    i+=1
   return src[a:i]
  funcs='\n'.join(function(s) for s in ['void ledPulse()','void ledTick()','uint16_t hashStr(','uint8_t neuralWord()','void fire()','void neuronStep()'])
  prefix='''#include <cstdint>
#include <cassert>
#include <cstring>
#include <algorithm>
using std::min;
#define LED_PIN 13
#define HIGH 1
#define LOW 0
#define strcpy_P strcpy
#define pgm_read_word(p) (*(p))
uint32_t clockMs=0,ledUntil=0,tStep=0,stimT=0,cloudT=0,skillT=0,spikesWin=0;
int pin=0;uint32_t millis(){return clockMs;}void digitalWrite(int,int v){pin=v;}
int constrain(int v,int a,int b){return std::min(b,std::max(a,v));}
uint8_t myID=0,lastInputs[12]={0},firesCnt=0;int8_t weights[12]={0};
uint16_t lfsr=1;float activation=0;bool wDirty=false;
char stim[40]="",cloudFrag[24]="",skillFrag[20]="",lastWord[17]="";
const char *VOCAB[6]={"one","two","three","four","five","six"};
void saveWeights(){}
'''
  main='''int main(){
ledPulse();clockMs=59;ledTick();assert(pin==HIGH);clockMs=60;ledTick();assert(pin==LOW);
clockMs=0xfffffff0u;ledPulse();clockMs=20;ledTick();assert(pin==HIGH);clockMs=44;ledTick();assert(pin==LOW);
weights[1]=5;lastInputs[1]=2;assert(neuralWord()==1);weights[2]=10;lastInputs[2]=2;assert(neuralWord()==2);
for(int i=0;i<12;i++)weights[i]=0;
clockMs=201;activation=10;neuronStep();assert(spikesWin==1);assert(!wDirty);
clockMs=500;stimT=clockMs;strcpy(stim,"signal");activation=10;neuronStep();assert(spikesWin==2);assert(wDirty);
}'''
  with tempfile.TemporaryDirectory() as d:
   path=Path(d);(path/'test.cpp').write_text(prefix+funcs+main)
   subprocess.run(['g++','-std=c++17','-Wall','-Wextra',str(path/'test.cpp'),'-o',str(path/'test')],check=True,capture_output=True)
   subprocess.run([str(path/'test')],check=True,capture_output=True)
if __name__=='__main__':unittest.main()
