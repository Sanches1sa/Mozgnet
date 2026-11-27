"""Compile real PWM RPC header with mocked Zephyr API; not a target build."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest

class PwmFirmwareTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('g++'),'Needs host C++ compiler')
 def test_supported_rpc_path_and_explicit_unsupported_path(self):
  header=Path(__file__).resolve().parents[1]/'sketch/pwm_voice.h'
  common=r'''
#include <string>
#include <cstdint>
#include <cstdlib>
#include <cassert>
class String{public:std::string v;String(const char*s=""):v(s){}String(std::string s):v(s){}String(unsigned n):v(std::to_string(n)){}String(int n):v(std::to_string(n)){}String operator+(String x)const{return String(v+x.v);}size_t length()const{return v.size();}char operator[](size_t i)const{return v[i];}int indexOf(char c)const{auto p=v.find(c);return p==std::string::npos?-1:(int)p;}String substring(int a)const{return v.substr(a);}String substring(int a,int b)const{return v.substr(a,b-a);}long toInt()const{return std::strtol(v.c_str(),nullptr,10);}};
void analogWrite(int,int){}
enum{LOW=0,HIGH=1,OUTPUT=1};
inline int g_hi=0,g_lo=0;
void pinMode(int,int){}
void digitalWrite(int,int v){if(v)g_hi++;else g_lo++;}
inline uint32_t micros(){static uint32_t t=0;return t+=30;}
'''
  fake=r'''
#pragma once
#include <cstdint>
struct device{const void*api=(const void*)1;};inline device devices[2];
#define pwm4 0
#define counter_servo 1
#define DT_NODELABEL(n) n
#define DT_NODE_HAS_STATUS(n,s) 1
#define DEVICE_DT_GET(n) (&devices[n])
inline unsigned irq_lock(){return 0;}inline void irq_unlock(unsigned){}
inline bool device_is_ready(const device*){return true;}
inline uint32_t lastPeriod=0,lastPulse=0;
inline int pwm_get_cycles_per_sec(const device*,int,uint64_t*hz){*hz=32000000;return 0;}
inline int pwm_set_cycles(const device*,int,uint32_t period,uint32_t pulse,int){lastPeriod=period;lastPulse=pulse;return 0;}
struct counter_top_cfg{uint32_t ticks;void(*callback)(const device*,void*);void*user_data;uint32_t flags;};
inline counter_top_cfg configuration;
inline uint32_t counter_get_frequency(const device*){return 32000000;}
inline uint32_t counter_get_max_top_value(const device*){return 65535;}
inline int counter_stop(const device*){return 0;}
inline int counter_start(const device*){return 0;}
inline int counter_set_top_value(const device*,const counter_top_cfg*c){configuration=*c;return 0;}
'''
  main=r'''int main(){
assert(rpcPcmBegin("0").v.find("error")==0);
assert(rpcPcmBegin("1000").v.find("buffering")==0);
std::string hex(1024,'8');assert(rpcPcmWrite(String("0:"+hex)).v.find("buffering")==0);
assert(rpcPcmWrite(String("0:"+hex)).v.find("error")==0);
assert(rpcPcmWrite(String("1:"+std::string(976,'8'))).v.find("buffering")==0);
assert(rpcPcmEnd().v.find("playing")==0);assert(configuration.ticks==3999);assert(lastPeriod==1024);
for(int i=0;i<1000;i++)configuration.callback(nullptr,nullptr);
assert(rpcPcmStatus().v.find("done:2:32768:1000:0")==0);assert(lastPulse==0);
}'''
  with tempfile.TemporaryDirectory() as folder:
   d=Path(folder);(d/'zephyr/drivers').mkdir(parents=True);(d/'zephyr/fake.h').write_text(fake)
   for name in ['kernel.h','drivers/pwm.h','drivers/counter.h']:(d/'zephyr'/name).write_text('#include <zephyr/fake.h>\n')
   source=d/'test.cpp';binary=d/'test'
   for enabled in (True,False):
    prefix='#define NEURO_PCM_ENABLE 1\n#define CONFIG_PWM 1\n#define CONFIG_COUNTER 1\n' if enabled else '#define CONFIG_PWM 1\n#define CONFIG_COUNTER 1\n'
    soft=r'''int main(){
assert(rpcPcmBegin("30000").v.find("error:too_long")==0);
assert(rpcPcmBegin("1000").v.find("buffering")==0);
std::string hex(1024,'f');assert(rpcPcmWrite(String("0:"+hex)).v.find("buffering")==0);
assert(rpcPcmWrite(String("1:"+std::string(976,'0'))).v.find("buffering")==0);
assert(rpcPcmEnd().v.find("playing")==0);
pcmSoftTick();
assert(rpcPcmStatus().v.find("done:2:")==0);
assert(g_hi>1000 && g_lo>1000);   // сигма-дельта реально двигает пин
}'''
    testmain=main if enabled else soft
    source.write_text(prefix+common+'\n#include "'+str(header)+'"\n'+testmain)
    result=subprocess.run(['g++','-std=c++17','-I',str(d),str(source),'-o',str(binary)],capture_output=True,text=True)
    self.assertEqual(result.returncode,0,result.stderr);subprocess.run([str(binary)],check=True,capture_output=True)
if __name__=='__main__':unittest.main()
