/* ============================================================================
 * esp32cam_tflite.ino — КАМЕРА С НЕЙРОСЕТЬЮ ДЛЯ МОЗГА-12 (v1.4)
 * Плата: ESP32-CAM AI-Thinker (OV2640, 4 МБ PSRAM)  <->  Arduino UNO Q (neuro_12)
 * ----------------------------------------------------------------------------
 * ЧТО ДЕЛАЕТ
 *  1) КАРТИНКИ ПО UART — как esp32cam_uart v1.0: JPEG-кадры непрерывно идут по
 *     проводу на UNO Q тем же форматом «буфера», скетч хаба менять под кадры
 *     не нужно (A5 5C | тип | seq | len | crc16 | payload).
 *  2) НЕЙРОСЕТЬ НА САМОЙ КАМЕРЕ — TensorFlow Lite Micro, модель person_detect
 *     (MobileNetV1, обучена Google), КВАНТОВАННАЯ int8 и ВШИТАЯ В ПРОШИВКУ
 *     (n12_model_data.cpp, 300 КБ во flash). Раз в VIS_MS кадр уменьшается до
 *     96x96 оттенков серого, сеть отвечает «человек в кадре: 0..1». Работает
 *     на ядре 0 отдельной задачей — поток кадров на ядре 1 не тормозит.
 *  3) СЛОВА ВМЕСТЕ С МОЗГОМ neuro12 (n12_vision.cpp):
 *     - камера держит СВОЙ словарь «слово -> какая сцена» (человек/пусто,
 *       светло/темно, движение/покой) и шлёт плате слова под то, что видит;
 *     - v1.2: камера УЧАСТВУЕТ В ВЫБОРЕ КАЖДОГО СЛОВА: мозг на каждом шаге
 *       мысли шлёт ей слова-кандидаты (V<id>|*|слово|слово...), камера
 *       оценивает каждое тем, что видит; своих слов и фраз она не шлёт;
 *     - итоговую фразу мозг сообщает камере — камера привязывает её слова к
 *       текущей сцене и запоминает (NVS: переживает перезагрузку).
 *
 * ПРОТОКОЛ (камера -> UNO Q), заголовок 8 байт, как раньше:
 *   A5 5C | тип | seq | len (2, LE) | crc16/XMODEM (2, LE) | payload
 *   тип 0x01 — JPEG-кадр                     (как в esp32cam_uart)
 *   тип 0x02 — статус, текст/JSON            (как в esp32cam_uart)
 *   тип 0x03 — ЗРЕНИЕ, JSON: {"t":"vis","n":..,"p":0.94,"br":..,"mo":..,
 *              "ms":..,"f":[6 признаков],"lex":N,"w":[["человек",0.91],..]}
 *   тип 0x04 — ГОЛОС, JSON: {"t":"wvote","id":17,"s":[0.81,-1,0.12,..]}
 *              (-1 — слова нет в словаре камеры)
 * КОМАНДЫ (UNO Q -> камера):
 *   одиночные байты, как раньше: G пуск, S пауза, F кадр, I статус, Z очистить
 *   строки (до '\n', UTF-8):
 *   V<id>|*|слово1|слово2|... — оценить слова-кандидаты (до 31), ответ "wvote"
 *   V<id>|фраза1|фраза2|...   — голос за фразы (старый вид, мозг v2.89 не шлёт)
 *   W<фраза>                  — мозг сказал фразу: выучить слова со сценой
 *   A0 / A1                   — выключить / включить нейросеть
 *
 * ПОДКЛЮЧЕНИЕ (как у esp32cam_uart):
 *   ESP32-CAM GPIO13 (TX) -> RX UNO Q (D0, Serial1)
 *   ESP32-CAM GPIO14 (RX) <- TX UNO Q (D1, Serial1) — ТЕПЕРЬ НУЖЕН: по нему
 *                            идут голосования и фразы мозга
 *   GND <-> GND обязательно; логика 3.3 В с обеих сторон. SD-карту вынуть.
 *
 * СБОРКА (Arduino IDE):
 *   Плата: «AI Thinker ESP32-CAM»; PSRAM: Enabled;
 *   Partition Scheme: «Huge APP (3MB No OTA/1MB SPIFFS)» — модель 300 КБ + код;
 *   TensorFlow Lite Micro:
 *     ядро ESP32 3.x — УЖЕ ВНУТРИ ЯДРА (официальная esp-tflite-micro), ничего
 *       ставить не надо; старую TensorFlowLite_ESP32 лучше удалить;
 *     ядро ESP32 2.0.x — библиотека TensorFlowLite_ESP32 1.0.0 (Менеджер библиотек).
 *   Все 5 файлов папки (.ino, n12_vision.*, n12_model_data.*) — вместе.
 * ========================================================================== */

#if !__has_include("tensorflow/lite/micro/micro_interpreter.h")
#include <TensorFlowLite_ESP32.h>   // только ядро 2.x; в ядре 3.x TFLite Micro уже есть
#endif
#include <Preferences.h>
#include "esp_camera.h"
#include "img_converters.h"   // v1.1: fmt2rgb888 — есть в любом ядре (как в CameraWebServer)
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "n12_vision.h"

/* ------------------------------ НАСТРОЙКИ -------------------------------- */
#define FW_VER           "esp32cam_tflite v1.4"
#define CAM_BAUD         460800   // = CAMRX_BAUD скетча хаба
#define UART_TX_PIN      13       // GPIO13 (TX) -> RX UNO Q
#define UART_RX_PIN      14       // GPIO14 (RX) <- TX UNO Q (голоса, фразы)

#define CAM_SIZE         FRAMESIZE_QVGA  // 320x240 (для зрения декодируется в 160x120)
#define CAM_QUALITY      12
#define MIN_FRAME_MS     120
#define RING_N           4
#define FRAME_MAX        20480    // v1.4: = CAMRX_BUF хаба (20 КБ); QVGA q12 — 6…15 КБ
#define CHUNK            512
#define STREAM_ON_BOOT   1
#define LED_PIN          33

#define AI_ON_BOOT       1        // нейросеть сразу работает
#define VIS_MS           1500     // как часто смотреть нейросетью, мс
#define ARENA_BYTES      (96 * 1024)  // рабочая память TFLite (внутренняя RAM)
#define VIS_TOP_WORDS    6        // сколько слов камера шлёт с каждым взглядом
#define LEX_SAVE_MS      30000    // как часто сохранять выученные слова в NVS
#define LINE_MAX         720      // длина строки-команды V/W

/* Распиновка камеры AI-Thinker ESP32-CAM (не менять) */
#define PWDN_GPIO  32
#define RESET_GPIO -1
#define XCLK_GPIO   0
#define SIOD_GPIO  26
#define SIOC_GPIO  27
#define Y9_GPIO    35
#define Y8_GPIO    34
#define Y7_GPIO    39
#define Y6_GPIO    36
#define Y5_GPIO    21
#define Y4_GPIO    19
#define Y3_GPIO    18
#define Y2_GPIO     5
#define VSYNC_GPIO 25
#define HREF_GPIO  23
#define PCLK_GPIO  22

static HardwareSerial CAMUART(1);

/* --------------------------- кольцевой буфер ----------------------------- */
static uint8_t  *ringBuf[RING_N];
static uint16_t  ringLen[RING_N];
static uint8_t   ringSeq[RING_N];
static bool      ringReady[RING_N];
static uint8_t   seqGen = 0;
static uint32_t  lastCap = 0;
volatile bool    streamOn = (STREAM_ON_BOOT != 0);
volatile bool    snapReq = false;
volatile bool    statReq = false;
volatile uint32_t framesGot = 0, framesSent = 0, framesLost = 0;

/* ------------------------------- зрение ---------------------------------- */
static bool       aiOn = (AI_ON_BOOT != 0);
static bool       aiReady = false;            // модель поднялась
static char       aiErr[96] = "ещё не запускалась";
static uint8_t   *arena = nullptr;
static n12::Accum *acc = nullptr;              // накопитель 96x96 (PSRAM)
static int8_t    *prevIn = nullptr;            // прошлый вход (движение)
static uint8_t   *visJpeg = nullptr;           // копия кадра для нейросети
static uint8_t   *visRgb = nullptr;            // v1.1: кадр, разжатый в RGB888 (PSRAM)
static size_t     visRgbCap = 0;
static volatile uint32_t visLen = 0;
static volatile uint16_t visW = 0, visH = 0;
static volatile bool visBusy = false;          // задача зрения занята кадром
static volatile bool visFresh = false;         // есть новый результат
static n12::Scene visScene;                    // результат (под visMux)
static uint32_t   visN = 0, lastVis = 0;
static SemaphoreHandle_t visGo = nullptr;
static portMUX_TYPE visMux = portMUX_INITIALIZER_UNLOCKED;
static float      sceneF[n12::NF] = {0};       // признаки последней сцены (loop)
static bool       sceneOk = false;

/* ------------------------- словарь и команды ----------------------------- */
static Preferences prefs;
static bool       lexDirty = false;
static uint32_t   lexSavedAt = 0;
static uint32_t   votes = 0, learns = 0;
static char       lineBuf[LINE_MAX];
static int        lineLen = -1;                // -1 — не в режиме строки
static char       lineCmd = 0;
static uint32_t   lineAt = 0;
static char       voteReq[LINE_MAX];           // отложенные (не внутри отправки)
static bool       voteReqOn = false;
static char       sayReq[LINE_MAX];
static bool       sayReqOn = false;

/* ------------------------------ УТИЛИТЫ ---------------------------------- */
static uint16_t crc16x(const uint8_t *d, uint16_t n) {
  uint16_t crc = 0;
  while (n--) {
    crc ^= (uint16_t)(*d++) << 8;
    for (uint8_t k = 0; k < 8; k++)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

static void ledBlink(int times, int ms) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, LOW);  delay(ms);
    digitalWrite(LED_PIN, HIGH); delay(ms);
  }
}

/* ------------------------- КОМАНДЫ ОТ UNO Q ------------------------------ */
// Строки V/W только запоминаются: отвечать посреди отправки кадра нельзя.
static void lineDone() {
  lineBuf[lineLen] = 0;
  if (lineCmd == 'V') { strcpy(voteReq, lineBuf); voteReqOn = true; }
  else if (lineCmd == 'W') { strcpy(sayReq, lineBuf); sayReqOn = true; }
  else if (lineCmd == 'A') { aiOn = lineBuf[0] != '0'; }
  lineLen = -1;
}

void uartPumpCommands() {
  if (lineLen >= 0 && millis() - lineAt > 1500) lineLen = -1;   // оборванная строка
  while (CAMUART.available()) {
    char c = (char)CAMUART.read();
    if (lineLen >= 0) {
      if (c == '\n' || c == '\r') lineDone();
      else if (lineLen < LINE_MAX - 1) lineBuf[lineLen++] = c;
      continue;
    }
    switch (c) {
      case 'G': streamOn = true;  break;
      case 'S': streamOn = false; break;
      case 'F': snapReq  = true;  break;
      case 'I': statReq  = true;  break;
      case 'Z': for (int i = 0; i < RING_N; i++) ringReady[i] = false; break;
      case 'V': case 'W': case 'A':
        lineCmd = c; lineLen = 0; lineAt = millis(); break;
      default: break;
    }
  }
}

/* --------------------------- КОЛЬЦЕВОЙ БУФЕР ----------------------------- */
void ringPut(const uint8_t *data, uint16_t len) {
  int slot = -1;
  for (int i = 0; i < RING_N; i++)
    if (!ringReady[i]) { slot = i; break; }
  if (slot < 0) {
    uint8_t bestAge = 0;
    for (int i = 0; i < RING_N; i++) {
      uint8_t age = (uint8_t)(seqGen - ringSeq[i]);
      if (age > bestAge) { bestAge = age; slot = i; }
    }
    framesLost++;
  }
  memcpy(ringBuf[slot], data, len);
  ringLen[slot] = len; ringSeq[slot] = seqGen; ringReady[slot] = true;
  seqGen = (uint8_t)(seqGen + 1);
}

int ringOldest() {
  int slot = -1;
  uint8_t bestAge = 0;
  for (int i = 0; i < RING_N; i++) {
    if (!ringReady[i]) continue;
    uint8_t age = (uint8_t)(seqGen - ringSeq[i]);
    if (slot < 0 || age > bestAge) { bestAge = age; slot = i; }
  }
  return slot;
}

/* ----------------------------- ОТПРАВКА ---------------------------------- */
void sendFrame(const uint8_t *data, uint16_t len, uint8_t type, uint8_t seq) {
  uint16_t crc = crc16x(data, len);
  uint8_t h[8] = { 0xA5, 0x5C, type, seq, (uint8_t)(len & 0xFF), (uint8_t)(len >> 8),
                   (uint8_t)(crc & 0xFF), (uint8_t)(crc >> 8) };
  CAMUART.write(h, 8);
  uint16_t off = 0;
  while (off < len) {
    uint16_t n = (uint16_t)(len - off);
    if (n > CHUNK) n = CHUNK;
    CAMUART.write(data + off, n);
    off += n;
    CAMUART.flush();
    uartPumpCommands();
    delay(1);
  }
  if (type == 0x01) { digitalWrite(LED_PIN, LOW); delay(2); digitalWrite(LED_PIN, HIGH); }
}

void sendSlot(int slot) {
  sendFrame(ringBuf[slot], ringLen[slot], 0x01, ringSeq[slot]);
  ringReady[slot] = false;
  framesSent++;
}

void sendText(const char *s, uint8_t type = 0x02) {
  uint16_t len = (uint16_t)strlen(s);
  if (len > 1024) len = 1024;
  sendFrame((const uint8_t *)s, len, type, 0);
  CAMUART.flush();
}

void sendStatus() {
  char js[360];
  int ready = 0;
  for (int i = 0; i < RING_N; i++) if (ringReady[i]) ready++;
  snprintf(js, sizeof(js),
           "{\"fw\":\"%s\",\"frames\":%u,\"sent\":%u,\"lost\":%u,\"buf\":%d,\"stream\":%d,"
           "\"baud\":%u,\"ms\":%u,\"ai\":{\"on\":%d,\"ok\":%d,\"n\":%u,\"infer_ms\":%u,"
           "\"lex\":%d,\"votes\":%u,\"learns\":%u,\"err\":\"%s\"}}",
           FW_VER, (unsigned)framesGot, (unsigned)framesSent, (unsigned)framesLost, ready,
           (int)streamOn, (unsigned)CAM_BAUD, (unsigned)millis(), (int)aiOn, (int)aiReady,
           (unsigned)visN, (unsigned)visScene.ms, n12::lexCount(), (unsigned)votes,
           (unsigned)learns, aiErr);
  sendText(js, 0x02);
}

/* ------------------------- ЗРЕНИЕ (ядро 0) -------------------------------- */
static void visionTask(void *) {
  for (;;) {
    if (xSemaphoreTake(visGo, portMAX_DELAY) != pdTRUE) continue;
    n12::Scene s;
    // v1.1: JPEG -> RGB888 (fmt2rgb888, порядок байтов B,G,R) -> центр 240x240 -> 96x96 серый
    bool dec = false;
    size_t need = (size_t)visW * visH * 3;
    if (need > visRgbCap) {                 // буфер под размер кадра (QVGA — 230 КБ в PSRAM)
      free(visRgb);
      visRgb = (uint8_t *)ps_malloc(need);
      visRgbCap = visRgb ? need : 0;
    }
    if (visRgb) {
      dec = fmt2rgb888(visJpeg, visLen, PIXFORMAT_JPEG, visRgb);
      if (dec) {
        n12::accumStart(*acc, visW, visH);
        n12::accumBGR(*acc, visRgb, visW, visH);
      }
    }
    if (dec) {
      static bool first = true;            // первый кадр не с чем сравнить — не «движение»
      n12::accumFinish(*acc, n12::modelInput(), first ? nullptr : prevIn, s);
      if (first) { memcpy(prevIn, n12::modelInput(), n12::kIn * n12::kIn); first = false; }
      uint32_t t0 = millis();
      s.ok = n12::modelRun(&s.person);
      s.ms = millis() - t0;
    }
    portENTER_CRITICAL(&visMux);
    s.n = ++visN;
    visScene = s;
    visFresh = true;
    visBusy = false;
    portEXIT_CRITICAL(&visMux);
  }
}

/* Отдать кадр нейросети (из loop, когда пора и задача свободна) */
static void visOffer(camera_fb_t *fb) {
  if (!aiOn || !aiReady || visBusy || millis() - lastVis < VIS_MS) return;
  if (fb->len > FRAME_MAX || fb->format != PIXFORMAT_JPEG) return;
  memcpy(visJpeg, fb->buf, fb->len);
  visLen = fb->len; visW = fb->width; visH = fb->height;
  lastVis = millis();
  visBusy = true;
  xSemaphoreGive(visGo);
}

/* Результат зрения -> признаки сцены -> JSON слов плате (тип 0x03) */
static void visPublish() {
  if (!visFresh) return;
  n12::Scene s;
  portENTER_CRITICAL(&visMux);
  s = visScene;
  visFresh = false;
  portEXIT_CRITICAL(&visMux);
  if (!s.ok) { snprintf(aiErr, sizeof(aiErr), "кадр не распознан"); return; }
  n12::features(s, sceneF);
  sceneOk = true;
  static char js[900];
  n12::visJson(js, sizeof(js), s, sceneF, VIS_TOP_WORDS);
  sendText(js, 0x03);
}

/* V<id>|ф1|ф2|... -> голос (тип 0x04) */
static void doVote() {
  // v1.2: V<id>|*|слово1|слово2|... — ГОЛОС ЗА СЛОВА: мозг выбирает следующее
  // слово мысли, камера оценивает каждого кандидата тем, что видит (0..1;
  // -1 — слова нет в словаре камеры, тогда оно на выбор не влияет).
  // Старый вид V<id>|фраза|фраза — голос за фразы (оставлен для совместимости).
  voteReqOn = false;
  char *p = voteReq;
  uint32_t id = (uint32_t)strtoul(p, &p, 10);
  const int MAXT = 32;
  const char *tok[MAXT];
  int n = 0;
  while (*p == '|' && n < MAXT) {
    *p++ = 0;
    tok[n++] = p;
    while (*p && *p != '|') p++;
  }
  if (*p == '|') *p = 0;
  static char js[640];
  if (n > 0 && !strcmp(tok[0], "*")) {              // голос за слова
    int o = snprintf(js, sizeof(js), "{\"t\":\"wvote\",\"id\":%u,\"s\":[", (unsigned)id);
    for (int i = 1; i < n && o < (int)sizeof(js) - 16; i++) {
      float sc = sceneOk ? n12::wordScore(tok[i], sceneF) : -1;
      o += snprintf(js + o, sizeof(js) - o, "%s%.3f", i > 1 ? "," : "", sc);
    }
    snprintf(js + o, sizeof(js) - o, "]}");
  } else {                                          // голос за фразы (старый)
    if (n > 8) n = 8;
    float sc[8] = {0};
    if (sceneOk) n12::vote(tok, n, sceneF, sc);
    n12::voteJson(js, sizeof(js), id, sc, n);
  }
  sendText(js, 0x04);
  votes++;
}

/* W<фраза> -> выучить слова со сценой */
static void doLearn() {
  sayReqOn = false;
  if (!sceneOk) return;
  if (n12::lexLearn(sayReq, sceneF) > 0) { lexDirty = true; learns++; }
}

static void lexPersist(bool force) {
  if (!lexDirty || (!force && millis() - lexSavedAt < LEX_SAVE_MS)) return;
  static uint8_t blob[4096];
  size_t n = n12::lexSave(blob, sizeof(blob));
  if (n) prefs.putBytes("lex", blob, n);
  lexDirty = false;
  lexSavedAt = millis();
}

/* ------------------------------ КАМЕРА ----------------------------------- */
bool initCamera() {
  camera_config_t cfg;
  cfg.ledc_channel = LEDC_CHANNEL_0;
  cfg.ledc_timer   = LEDC_TIMER_0;
  cfg.pin_pwdn     = PWDN_GPIO;
  cfg.pin_reset    = RESET_GPIO;
  cfg.pin_xclk     = XCLK_GPIO;
  cfg.pin_sscb_sda = SIOD_GPIO;
  cfg.pin_sscb_scl = SIOC_GPIO;
  cfg.pin_d7 = Y9_GPIO;  cfg.pin_d6 = Y8_GPIO;
  cfg.pin_d5 = Y7_GPIO;  cfg.pin_d4 = Y6_GPIO;
  cfg.pin_d3 = Y5_GPIO;  cfg.pin_d2 = Y4_GPIO;
  cfg.pin_d1 = Y3_GPIO;  cfg.pin_d0 = Y2_GPIO;
  cfg.pin_vsync = VSYNC_GPIO;
  cfg.pin_href  = HREF_GPIO;
  cfg.pin_pclk  = PCLK_GPIO;
  cfg.xclk_freq_hz = 20000000;
  cfg.pixel_format = PIXFORMAT_JPEG;
  cfg.frame_size   = CAM_SIZE;
  cfg.jpeg_quality = CAM_QUALITY;
  cfg.fb_count     = 2;
  cfg.fb_location  = CAMERA_FB_IN_PSRAM;
  cfg.grab_mode    = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) { Serial.printf("[AI-CAM] init error 0x%x\n", err); return false; }
  sensor_t *s = esp_camera_sensor_get();
  if (s) { s->set_vflip(s, 0); s->set_hmirror(s, 0); }
  return true;
}

/* Нейросеть: память берётся ДО камеры — внутренняя RAM быстрее PSRAM */
static void initAI() {
  arena = (uint8_t *)heap_caps_malloc(ARENA_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (!arena) arena = (uint8_t *)ps_malloc(ARENA_BYTES);          // медленнее, но работает
  acc = (n12::Accum *)ps_malloc(sizeof(n12::Accum));
  prevIn = (int8_t *)ps_calloc(n12::kIn * n12::kIn, 1);
  visJpeg = (uint8_t *)ps_malloc(FRAME_MAX);
  if (!arena || !acc || !prevIn || !visJpeg) {
    snprintf(aiErr, sizeof(aiErr), "нет памяти под нейросеть (PSRAM включён?)");
    return;
  }
  aiReady = n12::modelInit(arena, ARENA_BYTES, aiErr, sizeof(aiErr));
  Serial.printf("[AI-CAM] нейросеть: %s\n", aiErr);
  if (!aiReady) return;
  visGo = xSemaphoreCreateBinary();
  xTaskCreatePinnedToCore(visionTask, "n12vision", 12288, nullptr, 1, nullptr, 0);
}

/* ------------------------------- СТАРТ ----------------------------------- */
void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  Serial.begin(115200);
  Serial.println("\n[AI-CAM] " FW_VER ": кадры по UART + нейросеть TFLite (int8) + слова с мозгом-12");

  n12::lexInit();
  prefs.begin("n12cam", false);
  size_t n = prefs.getBytesLength("lex");
  if (n > 0 && n <= 4096) {
    static uint8_t blob[4096];
    prefs.getBytes("lex", blob, n);
    Serial.printf("[AI-CAM] выученные слова: %s, всего слов %d\n",
                  n12::lexLoad(blob, n) ? "загружены" : "битые — с нуля", n12::lexCount());
  }

  initAI();                                  // до камеры: арена во внутренней RAM

  if (!initCamera()) {
    Serial.println("[AI-CAM] камера не поднялась — перезапуск");
    ledBlink(5, 80); delay(2000); ESP.restart();
  }

  bool ok = psramFound();
  for (int i = 0; i < RING_N; i++) {
    ringBuf[i] = (uint8_t *)ps_malloc(FRAME_MAX);
    if (!ringBuf[i]) ok = false;
    ringReady[i] = false; ringLen[i] = 0;
  }
  if (!ok) {
    Serial.println("[AI-CAM] нет PSRAM/буфера: Tools -> PSRAM: Enabled");
    ledBlink(5, 80); delay(2000); ESP.restart();
  }

  CAMUART.setRxBufferSize(2048);             // фразы для голосования длинные
  CAMUART.begin(CAM_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  Serial.printf("[AI-CAM] UART %u бод: TX=GPIO13 -> RX UNO Q, RX=GPIO14 <- TX UNO Q\n",
                (unsigned)CAM_BAUD);
  Serial.println("[AI-CAM] типы: 01 кадр, 02 статус, 03 зрение, 04 голос; команды G S F I Z, V.. W.. A0/A1");
  ledBlink(3, 120);
  sendStatus();
}

/* ------------------------------- ЦИКЛ ------------------------------------ */
void loop() {
  uartPumpCommands();
  if (statReq) { statReq = false; sendStatus(); }
  if (voteReqOn) doVote();                   // голос — сразу, мозг ждёт
  if (sayReqOn) doLearn();

  if (snapReq) {                             // «F» — свежий кадр немедленно
    snapReq = false;
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      if (fb->len <= FRAME_MAX) {
        sendFrame(fb->buf, (uint16_t)fb->len, 0x01, seqGen);
        seqGen = (uint8_t)(seqGen + 1);
        framesGot++; framesSent++;
      } else framesLost++;
      esp_camera_fb_return(fb);
    }
  }

  if (millis() - lastCap >= MIN_FRAME_MS) {  // захват: в буфер и нейросети
    lastCap = millis();
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      if (fb->len <= FRAME_MAX) { ringPut(fb->buf, (uint16_t)fb->len); framesGot++; }
      else framesLost++;
      visOffer(fb);
      esp_camera_fb_return(fb);
    }
  }

  visPublish();                              // что увидела сеть -> слова плате

  if (streamOn) {
    int slot = ringOldest();
    if (slot >= 0) sendSlot(slot);
  }
  lexPersist(false);
  delay(2);
}
