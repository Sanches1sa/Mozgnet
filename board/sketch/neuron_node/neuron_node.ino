/* ============================================================================
 * neuron_node.ino — НЕЙРОН-ПЛАТА «ИСКУССТВЕННОГО МОЗГА-12» (v2.7, АВТО-ID + СБРОС)
 * Плата: любой классический Arduino (UNO R3/Nano/Mega — ATmega328/2560),
 *        совместим и с R4. Прошивается ОДИНАКОВОЙ прошивкой на ВСЕ платы:
 *        номер платы больше НЕ надо менять в коде — он хранится в EEPROM
 *        и назначается хабом автоматически (v2.6).
 * ----------------------------------------------------------------------------
 * ЛЕЧЕНИЕ «СЛИПШИХСЯ» НЕЙРОНОВ (v2.6): раньше NEURON_ID зашивался в код, и
 *   платы, прошитые одним ID, «слипались» на одном I2C-адресе (0x10+ID) —
 *   хаб видел «2 из 8». Теперь:
 *     1) номер хранится в EEPROM (адрес 30 = маркер 0xC7, адрес 31 = номер);
 *     2) плата БЕЗ номера («сирота») слушает ОБЩИЙ ВЫЗОВ I2C (адрес 0) —
 *        хаб (sketch.ino v2.28, RPC neuro_assign) опрашивает слоты 0..11 и
 *        в ОБЩИЙ ВЫЗОВ шлёт "SETID;<n>" для свободных; сирота берёт номер,
 *        пишет в EEPROM и перерегистрируется на адресе 0x10+n;
 *        светодиод мигает n раз — номер видно глазами;
 *     3) ВАЖНО: сирот на шине должно быть НЕ БОЛЬШЕ ОДНОЙ (иначе несколько
 *        возьмут один номер) — подключайте по одной и запускайте назначение
 *        (в чате мозга: «нейроны»);
 *     4) ручной режим сохранён: #define NEURON_ID 0..11 — номер пишется в
 *        EEPROM при старте и дальше живёт там;
 *     5) v2.7: СБРОС НОМЕРА — команда "CLR_ID" (в общем вызове или через
 *        мультиплексор): плата СТИРАЕТ номер из EEPROM и снова становится
 *        сиротой (быстро мигает, ждёт SETID). Нужна, если платы «слиплись»
 *        ДУБЛЯМИ номеров: скажите мозгу «нейроны сброс» (хаб шлёт CLR_ID
 *        всем), затем подключайте платы ПО ОДНОЙ и запускайте «нейроны».
 *        Ручные платы (#define NEURON_ID 0..11) команду игнорируют — им
 *        сначала верните #define NEURON_ID 255.
 *
 * Нейрон живёт своей жизнью:
 *  - это ФОРМАЛЬНЫЙ ПЕРЦЕПТРОН: y = step(sum(w_i * x_i) + b), входы x_i —
 *    биты состояния сети (LFSR) плюс свежий стимул из чата, веса w — в EEPROM;
 *  - СКОРОСТЬ ОБУЧЕНИЯ задаёт ЧАСТОТНАЯ ШИНА хаба UNO Q: генератор хаба гонит
 *    меандр с пина D4 -> A0 ВСЕХ нейронов (общая земля). Нейрон САМ измеряет
 *    частоту на A0: камера/микрофон/чат ускоряют пластику весов (до 2000 Гц,
 *    в покое 120 Гц). "GEN;<частота>" по I2C — резервный канал.
 *  - при превышении порога "стреляет" спайком и выдаёт слово из словаря;
 *  - v2.96: слово выбирается по сохранённым весам и входам, не по Hz;
 *    частотный дрейф весов отключён; один переход порога = один спайк;
 *  - отдаёт хабу пакет val_c:  N<id>;<спайки>;<слово>
 *  - встроенный LED (D13) вспыхивает на каждый спайк; при старте мигает
 *    СВОЙ НОМЕР (v2.6) — карту нейронов видно без монитора;
 *  - хаб рассылает "SKIL;<имя>" — двойная вспышка и всплеск активации;
 *  - сводка буфера облака приходит как "BUF;<фрагмент>".
 *
 * Связь с хабом (режим должен совпадать с sketch.ino!):
 *  LINK_SW_WIRE = 1 : I2C-slave по Wire, адрес 0x10 + номер (EEPROM).
 *                     Хаб (SoftwareWire master) пишет "STIM;<текст>",
 *                     "BUF;<фрагмент>", "SKIL;<имя>", "GEN;<hz>", "SETID;<n>",
 *                     "CLR_ID" (v2.7, сброс номера) и читает пакет onRequest.
 *  LINK_SW_WIRE = 0 : SoftwareSerial RX=D4, TX=D5, 9600 бод через мультиплексор.
 * ========================================================================== */

#include <Arduino.h>
#include <Wire.h>

/* ---- STRL-FALLBACK (begin): strlcpy/strlcat для ядер без BSD-функций ---- */
extern "C" __attribute__((weak)) size_t strlcpy(char *dst, const char *src, size_t dsize) {
  size_t slen = strlen(src);
  if (dsize != 0) {
    size_t copy = (slen >= dsize) ? dsize - 1 : slen;
    memcpy(dst, src, copy);
    dst[copy] = '\0';
  }
  return slen;
}
extern "C" __attribute__((weak)) size_t strlcat(char *dst, const char *src, size_t dsize) {
  size_t dlen = 0;
  while (dlen < dsize && dst[dlen] != '\0') dlen++;
  if (dsize == 0) return dsize + strlen(src);
  size_t slen = strlen(src);
  size_t room = dsize - dlen;
  size_t copy = (slen >= room) ? room - 1 : slen;
  memcpy(dst + dlen, src, copy);
  dst[dlen + copy] = '\0';
  return dlen + slen;
}
/* ---- STRL-FALLBACK (end) ----------------------------------------------- */

#define NEURON_ID      255      // <<< 255 = АВТО (EEPROM/назначение хабом);
                                //     0..11 = закрепить номер вручную
#define LINK_SW_WIRE   1        // 1 = SoftwareWire/I2C-slave, 0 = SoftwareSerial
#define TEXT_MAX       16
#define PKT_MAX        32       // AVR Wire-буфер = 32 байта, пакет должен влезать

/* v2.6: EEPROM-НОМЕР ПЛАТЫ */
#define ID_MAGIC_ADDR  30       // маркер записанного номера (0xC7)
#define ID_ADDR        31       // номер 0..15
#define ID_MAGIC       0xC7
#define ID_ORPHAN      255      // номера нет — режим «сироты» (общий вызов)

/* ЧАСТОТНАЯ ШИНА (v1.5): генератор хаба (D4) заведён на A0 этой платы,
 * общая земля обязательна. Частота на шине = темп пластики весов. */
#define FREQ_PIN       A0       // вход частотной шины (D4 хаба -> A0)
#define FREQ_GATE_MS   200      // окно измерения частоты, мс
#define FREQ_MIN_HZ    30       // ниже — шина молчит (переход на резерв "GEN;<hz>")

#if LINK_SW_WIRE == 0
  #include <SoftwareSerial.h>
  SoftwareSerial Link(4, 5);    // RX=D4 (от хаба TX), TX=D5 (к хабу RX через мультиплексор)
#endif

/* LED активности */
#define LED_PIN 13
uint32_t ledUntil=0;
void ledPulse(){digitalWrite(LED_PIN,HIGH);ledUntil=millis()+60;}
void ledTick(){ if ((int32_t)(ledUntil-millis())<=0) digitalWrite(LED_PIN,LOW); }

/* ----------------------------- СОСТОЯНИЕ --------------------------------- */
uint8_t  myID = ID_ORPHAN;      // v2.6: действующий номер платы (EEPROM/SETID)
uint8_t  lastInputs[12] = {0};
int8_t   weights[12];           // веса перцептрона (связи с 12 входами, вкл. себя)
float    activation = 0.0;      // текущая активация
uint32_t spikesWin = 0;         // спайки за окно (сбрасывается после чтения хабом)
char     lastWord[TEXT_MAX + 1] = "...";
char     stim[40] = "";         // последний стимул из чата
uint32_t stimT = 0;             // время прихода стимула
char     cloudFrag[24] = "";    // фрагмент буфера облака ("BUF;...", v1.3)
uint32_t cloudT = 0;            // когда пришла сводка облака
char     skillFrag[20] = "";    // имя новой функции мозга ("SKIL;...", v1.4)
uint32_t skillT = 0;            // когда пришло имя функции
uint32_t tStep = 0, tEcho = 0;
uint16_t lfsr = 0;              // генератор шума (уникален для каждого нейрона)
uint16_t genHz = 450;           // частота из "GEN;<hz>" (резерв, если шина A0 молчит)
uint8_t  firesCnt = 0;          // счётчик спайков (для отложенной записи EEPROM)
bool     wDirty = false;        // веса менялись с последней записи в EEPROM
uint32_t stepCnt = 0;           // счётчик шагов (для кругового обхода весов)
uint16_t fqSaves = 0;           // шагов с последней записи весов (частотная пластика)
uint8_t  pendingID = ID_ORPHAN; // v2.6: номер, принятый от хаба (применяем в loop)
bool     idBlinkDone = false;   // v2.6: мигание номера при старте
bool     pendingClr = false;    // v2.7: получен CLR_ID — стереть номер в loop

/* Частотная шина A0 <- D4 хаба (v1.5) */
uint16_t busHz = 0;             // частота, измеренная на A0 (ГЛАВНЫЙ канал темпа)
uint32_t busAliveT = 0;         // когда шина последний раз «звучала»
bool     fqLast = false;        // прошлый уровень на A0
uint16_t fqEdges = 0;           // восходящих фронтов за окно измерения
uint32_t tFreq = 0;             // начало окна измерения

/* ------------------------- EEPROM: ВЕСА И НОМЕР -------------------------- */
#if defined(__AVR__)
  #include <EEPROM.h>
#endif

uint8_t loadID() {
#if defined(__AVR__)
  if (EEPROM.read(ID_MAGIC_ADDR) == ID_MAGIC) {
    uint8_t id = EEPROM.read(ID_ADDR);
    if (id <= 15) return id;                // номер сохранён раньше
  }
#else
  if (NEURON_ID <= 11) return (uint8_t)NEURON_ID;   // R4: только ручной режим
#endif
  if (NEURON_ID <= 11) return (uint8_t)NEURON_ID;   // ручной номер из кода
  return ID_ORPHAN;                         // «сирота» — ждёт SETID от хаба
}

void saveID(uint8_t id) {
#if defined(__AVR__)
  EEPROM.update(ID_MAGIC_ADDR, ID_MAGIC);
  EEPROM.update(ID_ADDR, id);
#else
  (void)id;                                 // R4: EEPROM-номер не хранится
#endif
}

void loadWeights() {
#if defined(__AVR__)
  uint8_t magic; EEPROM.get(0, magic);
  if (magic != 0xA5) {                      // первый запуск — инициализация
    for (uint8_t i = 0; i < 12; i++)
      weights[i] = (int8_t)((random(-40, 90)) );   // в основном возбуждающие
    weights[myID % 12] = 10;                // слабая связь с собой
    EEPROM.update(0, 0xA5);
    for (uint8_t i = 0; i < 12; i++) EEPROM.update(1 + i, (uint8_t)weights[i]);
  } else {
    for (uint8_t i = 0; i < 12; i++) weights[i] = (int8_t)EEPROM.read(1 + i);
  }
#else
  for (uint8_t i = 0; i < 12; i++) weights[i] = (i == myID % 12) ? 10 : (int8_t)random(-40, 90);
#endif
}

void saveWeights() {            // отложенная запись: берегём EEPROM
  if (!wDirty) return;          // пишем только если веса реально менялись
  wDirty = false;
#if defined(__AVR__)
  for (uint8_t i = 0; i < 12; i++) EEPROM.update(1 + i, (uint8_t)weights[i]);
#endif
}

/* ------------------------- СЛОВАРЬ НЕЙРОНА ------------------------------- */
/* 6 слов, живут во Flash (PROGMEM) — одинаковые у всех нейронов; слово
 * выбирается с учётом активации, шума и стимула (см. fire()). */
const char W0[] PROGMEM = "privet";
const char W1[] PROGMEM = "zdes";
const char W2[] PROGMEM = "slychu";
const char W3[] PROGMEM = "gotov";
const char W4[] PROGMEM = "tok";
const char W5[] PROGMEM = "volna";
const char* const VOCAB[6] PROGMEM = {W0, W1, W2, W3, W4, W5};

/* v2.6: зона по НОМЕРУ в рантайме (для лога; раньше — макрос от NEURON_ID) */
const char* zoneName(uint8_t id) {
  switch (id % 12) {
    case 0:  return "privet";    case 1:  return "vopros";
    case 2:  return "status";    case 3:  return "kamera";
    case 4:  return "pamyat";    case 5:  return "signal";
    case 6:  return "umor";      case 7:  return "pomosch";
    case 8:  return "eho";       case 9:  return "oblako";
    case 10: return "obuchenie"; default: return "pokoy";
  }
}

/* ------------------------- ЧАСТОТНАЯ ШИНА A0 (v1.5) ---------------------- */
void freqSense() {
  bool s = digitalRead(FREQ_PIN) == HIGH;
  if (s != fqLast) { fqLast = s; if (s && fqEdges < 60000) fqEdges++; }
  uint32_t dt = millis() - tFreq;
  if (dt >= FREQ_GATE_MS) {
    uint16_t f = (uint16_t)((uint32_t)fqEdges * 1000UL / dt);
    fqEdges = 0; tFreq = millis();
    if (f >= FREQ_MIN_HZ) { busHz = f; busAliveT = millis(); }
  }
}

uint16_t effHz() {
  if (busHz && (millis() - busAliveT < 3000)) return busHz;
  return genHz;
}

/* ----------------------------- ЛОГИКА НЕЙРОНА ---------------------------- */
uint16_t hashStr(const char *s) {
  uint16_t h = 5381;
  while (*s) h = (h * 33) ^ (uint8_t)*s++;
  return h;
}

uint8_t neuralWord() {
  int16_t scores[6] = {0};
  for(uint8_t i=0;i<12;i++) scores[i%6]+=(int16_t)weights[i]*lastInputs[i];
  uint8_t best=(myID<=11?myID:0)%6;
  for(uint8_t i=0;i<6;i++) if(scores[i]>scores[best]) best=i;
  return best;
}

void fire() {
  spikesWin += 1;                         // one threshold crossing, one actual spike
  activation = 0.30;                        // рефрактерный период
  ledPulse();                              // 60 ms, no blocking delay

  uint8_t idx = neuralWord();              // learned weights/input, no Hz-to-word code
  strcpy_P(lastWord, (char *)pgm_read_word(&VOCAB[idx]));
}

void neuronStep() {
  if (millis() - tStep < 200) return;       // шаг нейрона каждые 200 мс
  tStep = millis();

  lfsr = (lfsr << 1) | (((lfsr >> 7) ^ (lfsr >> 5) ^ (lfsr >> 3) ^ 1) & 1);
  if (!lfsr) lfsr = 0xACE1 + myID;          // v2.6: seed от рантайм-номера

  // Learning follows real input co-activation, not oscillator frequency.

  bool stimFresh = stim[0] && (millis() - stimT < 3000);
  int16_t sum = 0;
  for (uint8_t i = 0; i < 12; i++) {
    int8_t x = (int8_t)((lfsr >> i) & 1);
    if (stimFresh) x = (int8_t)min(3, (int)x + 1 + (int)((hashStr(stim) >> i) & 1));
    lastInputs[i]=(uint8_t)x;
    sum += weights[i] * x;
  }

  activation = activation * 0.92 + (lfsr % 100) / 900.0 + (float)sum / 1600.0;

  if (cloudFrag[0] && (millis() - cloudT < 10000)) activation += 0.02;
  if (skillFrag[0] && (millis() - skillT < 10000)) activation += 0.03;

  float thr = 0.95 + (myID % 3) * 0.05;     // v2.6: порог от рантайм-номера
  if (activation > thr) {
    fire();
    if(stimFresh) for (uint8_t i=0;i<12;i++) {
      if(i==myID%12)continue;
      int16_t value=(int16_t)weights[i]+(lastInputs[i]>0?1:-1);
      int8_t next=(int8_t)constrain(value,-60,90);
      if(next!=weights[i]){weights[i]=next;wDirty=true;}
    }
    if (++firesCnt >= 32) { saveWeights(); firesCnt = 0; }
  }

  // Frequency-driven random drift of language weights is intentionally disabled.

}

/* ----------------------------- ПАКЕТ val_c ------------------------------- */
void buildPacket(char *out, uint8_t maxLen) {
  snprintf(out, maxLen, "N%d;%lu;%s", myID, (unsigned long)spikesWin, lastWord);
  spikesWin = 0;                            // окно сброшено — хаб забрал данные
}

/* v2.6: ПРИЁМ НОМЕРА ("SETID;<n>") — только платой БЕЗ номера.
 * Применяем НЕ в прерывании, а в loop() (перерегистрация Wire). */
#if LINK_SW_WIRE
void onI2CReceive(int cnt);     // явные прототипы: обработчики определены ниже
void onI2CRequest();
#endif
void takeSetid(const char *args) {
  if (myID != ID_ORPHAN) return;            // номер уже есть — игнорируем
  long n = strtol(args, nullptr, 10);
  if (n < 0 || n > 15) return;
  pendingID = (uint8_t)n;
}

void applyPendingID() {
  if (pendingID == ID_ORPHAN || myID != ID_ORPHAN) return;
  myID = pendingID;
  pendingID = ID_ORPHAN;
  saveID(myID);                             // номер переживает ребут
  loadWeights();                            // веса могли не инициализироваться
#if LINK_SW_WIRE
  Wire.begin(0x10 + myID);                  // перерегистрация на своём адресе
  Wire.onReceive(onI2CReceive);
  Wire.onRequest(onI2CRequest);
#endif
  /* v2.6: номер видно глазами — myID коротких вспышек */
  for (uint8_t k = 0; k < myID; k++) {
    digitalWrite(LED_PIN, HIGH); delayMicroseconds(300);
    digitalWrite(LED_PIN, LOW); delayMicroseconds(300);
  }
  Serial.print("[NEURON] номер назначен: ");
  Serial.print(myID);
  Serial.print(" (зона ");
  Serial.print(zoneName(myID));
  Serial.println(", EEPROM)");
}

/* v2.7: СТИРАНИЕ НОМЕРА ("CLR_ID") — плата снова «сирота». Из прерывания
 * только ставим флаг: EEPROM и перерегистрацию Wire делаем в loop().
 * Ручной NEURON_ID (0..11 в коде) команду игнорирует — номер закреплён. */
void takeClr() {
#if defined(NEURON_ID)
  if (NEURON_ID <= 11) return;              // ручной номер не стираем
#endif
  pendingClr = true;
}

void applyPendingClr() {
  if (!pendingClr) return;
  pendingClr = false;
#if defined(__AVR__)
  EEPROM.update(ID_MAGIC_ADDR, 0xFF);       // маркер стёрт — номера нет
  EEPROM.update(ID_ADDR, 0xFF);
#endif
  myID = ID_ORPHAN;
  lfsr = 0xACE1 + 0x7F * 0x11;              // зерно как у сироты при старте
  Serial.println("[NEURON] номер стёрт (CLR_ID): я сирота, жду SETID от хаба");
#if LINK_SW_WIRE
  Wire.begin(0x00);                         // снова слушаю ОБЩИЙ ВЫЗОВ
  Wire.onReceive(onI2CReceive);
  Wire.onRequest(onI2CRequest);
#endif
  for (uint8_t k = 0; k < 8; k++) {         // быстрое мигание = сирота
    digitalWrite(LED_PIN, HIGH); delayMicroseconds(300);
    digitalWrite(LED_PIN, LOW); delayMicroseconds(300);
  }
}

#if LINK_SW_WIRE
void onI2CReceive(int cnt) {
  static char buf[PKT_MAX]; uint8_t n = 0;
  while (Wire.available() && n < PKT_MAX - 1) buf[n++] = (char)Wire.read();
  buf[n] = 0;
  if (!strncmp(buf, "STIM;", 5)) {          // стимул из чата
    strlcpy(stim, buf + 5, sizeof(stim));
    stimT = millis();
  } else if (!strncmp(buf, "BUF;", 4)) {    // сводка буфера облака (v1.3)
    strlcpy(cloudFrag, buf + 4, sizeof(cloudFrag));
    cloudT = millis();
    digitalWrite(LED_PIN, HIGH); delayMicroseconds(400); digitalWrite(LED_PIN, LOW);
  } else if (!strncmp(buf, "SKIL;", 5)) {   // новая функция мозга (v1.4)
    strlcpy(skillFrag, buf + 5, sizeof(skillFrag));
    skillT = millis();
    digitalWrite(LED_PIN, HIGH); delayMicroseconds(300); digitalWrite(LED_PIN, LOW);
    delayMicroseconds(300);
    digitalWrite(LED_PIN, HIGH); delayMicroseconds(300); digitalWrite(LED_PIN, LOW);
  } else if (!strncmp(buf, "GEN;", 4)) {    // РЕЗЕРВ: частота хаба (приоритет — шина A0)
    genHz = (uint16_t)strtoul(buf + 4, nullptr, 10);
    if (genHz < 50) genHz = 50;
    if (genHz > 5000) genHz = 5000;
  } else if (!strncmp(buf, "SETID;", 6)) {  // v2.6: назначение номера (сироте)
    takeSetid(buf + 6);
  } else if (!strncmp(buf, "CLR_ID", 6)) {  // v2.7: сброс номера (в сироту)
    takeClr();
  }
}
void onI2CRequest() {
  char pkt[PKT_MAX];
  buildPacket(pkt, PKT_MAX);
  Wire.write((const uint8_t *)pkt, (uint8_t)strlen(pkt));
}
#else
void serialLoop() {
  static char line[48]; static uint8_t n = 0;
  while (Link.available()) {
    char c = (char)Link.read();
    if (c == '\n') { line[n] = 0; n = 0;
      if (!strncmp(line, "POLL", 4)) {
        char pkt[PKT_MAX]; buildPacket(pkt, PKT_MAX);
        Link.print(pkt); Link.print('\n');
      } else if (!strncmp(line, "STIM;", 5)) {
        strlcpy(stim, line + 5, sizeof(stim)); stimT = millis();
      } else if (!strncmp(line, "BUF;", 4)) {
        strlcpy(cloudFrag, line + 4, sizeof(cloudFrag)); cloudT = millis();
      } else if (!strncmp(line, "SKIL;", 5)) {
        strlcpy(skillFrag, line + 5, sizeof(skillFrag)); skillT = millis();
      } else if (!strncmp(line, "GEN;", 4)) {
        genHz = (uint16_t)strtoul(line + 4, nullptr, 10);
        if (genHz < 50) genHz = 50;
        if (genHz > 5000) genHz = 5000;
      } else if (!strncmp(line, "SETID;", 6)) {   // v2.6: номер через мультиплексор
        takeSetid(line + 6);
      } else if (!strncmp(line, "CLR_ID", 6)) {   // v2.7: сброс номера
        takeClr();
      }
      return;
    }
    if (n < sizeof(line) - 1) line[n++] = c;
  }
}
#endif

/* ----------------------------- SETUP / LOOP ------------------------------ */
void setup() {
  randomSeed(analogRead(A0) ^ ((uint16_t)analogRead(A1) << 3) ^ (uint16_t)millis());
  Serial.begin(115200);
  myID = loadID();                          // v2.6: EEPROM или ручной номер
  lfsr = 0xACE1 + (myID == ID_ORPHAN ? 0x7F : myID) * 0x11;
  pinMode(LED_PIN, OUTPUT);
  pinMode(FREQ_PIN, INPUT);                 // частотная шина: D4 хаба -> A0
  tFreq = millis();
  loadWeights();

  /* v2.6: карта нейронов глазами — myID вспышек при старте;
   * «сирота» мигает быстро-быстро (ждёт SETID от хаба) */
  if (myID == ID_ORPHAN) {
    for (uint8_t k = 0; k < 8; k++) {
      digitalWrite(LED_PIN, HIGH); delay(60);
      digitalWrite(LED_PIN, LOW); delay(60);
    }
    Serial.println("[NEURON] номера нет: слушаю ОБЩИЙ ВЫЗОВ, жду SETID от хаба");
  } else {
    for (uint8_t k = 0; k < myID; k++) {
      digitalWrite(LED_PIN, HIGH); delay(150);
      digitalWrite(LED_PIN, LOW); delay(150);
    }
    Serial.print("[NEURON] мой номер: ");
    Serial.print(myID);
    Serial.print(" (зона ");
    Serial.print(zoneName(myID));
    Serial.println(")");
  }

#if LINK_SW_WIRE
  if (myID == ID_ORPHAN) {
    Wire.begin(0x00);                       // v2.6: «сирота» слушает ОБЩИЙ ВЫЗОВ
  } else {
    Wire.begin(0x10 + myID);                // I2C-slave со СВОИМ адресом
  }
  Wire.onReceive(onI2CReceive);
  Wire.onRequest(onI2CRequest);
#else
  Link.begin(9600);
#endif

  delay(300);                               // чтобы все платы стартовали
  fire();                                   // "рождение" нейрона — один спайк
}

void loop() {
  applyPendingClr();   // v2.7: стереть номер (CLR_ID), не в прерывании
  applyPendingID();    // v2.6: применить номер, принятый через SETID (не в IRQ)
  ledTick();
  freqSense();         // частотная шина: считаем фронты на A0 (D4 хаба)
  neuronStep();
#if LINK_SW_WIRE
  /* I2C-slave работает на прерываниях — в loop() остаётся "жизнь" нейрона */
#else
  serialLoop();
#endif
}
