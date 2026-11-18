/* ============================================================================
 * esp32cam_uart.ino — КАМЕРА-СТРИМЕР ПО UART ДЛЯ МОЗГА-12 (v1.0)
 * Плата: ESP32-CAM (AI-Thinker, модуль OV2640)  ->  ПРИЁМНИК: Arduino UNO Q
 * ----------------------------------------------------------------------------
 * ЧТО ДЕЛАЕТ: снимает JPEG-кадры и непрерывно шлёт их ПО ПРОВОДУ (UART)
 * сразу на Arduino UNO Q. WiFi не нужен: видео-поток идёт по проводу,
 * облако и роутер не участвуют. Дополняет esp32cam_stream.ino (та версия
 * отдаёт кадры по WiFi в облако) — выберите ОДИН вариант под свою сборку.
 *
 * ФОРМАТ «БУФЕРА» (бинарный протокол кадра):
 *   байт 0..1   магия 0xA5 0x5C          <- приёмник сканирует поток по магии
 *   байт 2      тип: 0x01 = JPEG-кадр, 0x02 = текстовая строка (статус)
 *   байт 3      seq — номер кадра по модулю 256 (у текстовых seq = 0)
 *   байт 4..5   len — длина payload, младший байт ПЕРВЫЙ
 *   байт 6..7   crc16/XMODEM payload, младший байт ПЕРВЫЙ
 *   далее       len байт payload (JPEG начинается FF D8, кончается FF D9)
 *   Заголовок всегда ровно 8 байт; кадр шлётся ЦЕЛИКОМ, обрывов внутри нет.
 *
 * КОЛЬЦЕВОЙ БУФЕР В PSRAM (RING_N слотов): камера кладёт кадры в буфер даже
 *   когда UART занят или UNO Q поставил паузу; если буфер полон — вытесняется
 *   САМЫЙ СТАРЫЙ кадр (счётчик вытеснений виден в статусе 'I').
 *
 * КОМАНДЫ (UNO Q -> камера, одиночные байты, без перевода строки):
 *   'G' — пустить поток (и выслать накопленное в буфере)
 *   'S' — пауза: поток стоит, кадры продолжают копиться в буфере
 *   'F' — прислать ОДИН свежий кадр сейчас (работает и на паузе)
 *   'I' — прислать статус текстом (кадр типа 0x02, ASCII/JSON)
 *   'Z' — очистить буфер
 *   Команды можно не слать вообще: по умолчанию поток льётся сразу
 *   (STREAM_ON_BOOT 1). Посреди кадра пришедшая 'S' не рвёт кадр —
 *   он дошливается до конца.
 *
 * ПОДКЛЮЧЕНИЕ (вариант по умолчанию — UART1 на пинах 13/14):
 *   ESP32-CAM GPIO13 (TX)  ->  RX UNO Q
 *   ESP32-CAM GPIO14 (RX)  <-  TX UNO Q   (не обязательно — без команд
 *                                          поток всё равно льётся)
 *   GND ESP32-CAM <-> GND UNO Q — ОБЯЗАТЕЛЬНО.
 *   Логика 3.3 В с обеих сторон — согласование уровней не нужно.
 *   SD-КАРТУ ИЗ СЛОТА ВЫНУТЬ (пины 13/14 принадлежат слоту SD).
 * Вариант UART0 (#define UART_USE_SERIAL0 1): пины TX=1, RX=3 (разъём
 *   прошивки/монитора). Тогда на время прошивки скетча провод «RX UNO Q <-
 *   TX ESP32-CAM» отключать, монитор порта не работает.
 *
 * СКОРОСТЬ: CAM_BAUD 460800 по умолчанию (короткие провода). Если кадры
 *   бьются — снизьте до 230400 или 115200. Ориентир: QVGA q12 (~10 КБ/кадр)
 *   при 460800 — это ~4 кадра/с.
 *
 * ПРИЁМНИК НА UNO Q (любой стороне: скетч хаба sketch.ino, Linux-сторона
 *   UNO Q, ПК): читать байты; увидели A5 5C — дочитать заголовок до 8 байт,
 *   взять len, прочитать len байт payload, сверить CRC — кадр целый.
 *   Этот формат одинаков для любого приёмника, поэтому скетч хаба сможет
 *   позже принимать кадры и передавать их Python-мозгу (main.py) без правок
 *   камеры.
 *
 * Библиотеки: esp32-camera входит в ядро Espressif Arduino
 * (Board: AI Thinker ESP32-CAM). Монитор порта (UART0, 115200) — только
 * диагностика, поток идёт через CAMUART.
 * ========================================================================== */

#include "esp_camera.h"

/* ------------------------------ НАСТРОЙКИ -------------------------------- */
#define CAM_BAUD         115200   // скорость UART на UNO Q (= CAMRX_BAUD скетча платы; v2.96.5: 115200;
                                  // v2.96.2: 230400 — на 460800 занятый loop() платы терял байты)
#define UART_USE_SERIAL0 0        // 1 = шлить через разъём прошивки (TX=1/RX=3)
#define UART_TX_PIN      13       // UART1: GPIO13 (TX) -> RX UNO Q
#define UART_RX_PIN      14       // UART1: GPIO14 (RX) <- TX UNO Q (команды)

#define CAM_SIZE         FRAMESIZE_QVGA  // 320x240; вариант: FRAMESIZE_SVGA
#define CAM_QUALITY      12        // JPEG-качество (меньше = лучше и тяжелее)
#define MIN_FRAME_MS     120       // пауза между захватами кадров (шаг fps)
#define RING_N           4         // слотов кольцевого буфера в PSRAM
#define FRAME_MAX        40960     // макс. размер кадра, байт (SVGA q12 входит)
#define CHUNK            512       // кусок отправки; между кусками слушаем 'S'
#define STREAM_ON_BOOT   1         // 1 = поток льётся сразу после включения
#define LED_PIN          33        // красный LED платы (горит при LOW)

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

/* ------------------------- СОСТОЯНИЕ СТРИМЕРА ---------------------------- */
#if UART_USE_SERIAL0
  #define CAMUART Serial
#else
  static HardwareSerial CAMUART(1);
#endif

static uint8_t  *ringBuf[RING_N];   // слоты кольцевого буфера (PSRAM)
static uint16_t  ringLen[RING_N];
static uint8_t   ringSeq[RING_N];
static bool      ringReady[RING_N];
static uint8_t   seqGen = 0;        // счётчик кадров (модуль 256)
static uint32_t  lastCap = 0;
volatile bool    streamOn = (STREAM_ON_BOOT != 0);
volatile bool    snapReq = false;   // 'F' — один свежий кадр
volatile bool    statReq = false;   // 'I' — статус текстом
volatile uint32_t framesGot = 0, framesSent = 0, framesLost = 0;

/* ------------------------------ УТИЛИТЫ ---------------------------------- */
/* CRC16/XMODEM (полином 0x1021, старт 0x0000) — тот же алгоритм на любой
 * стороне: приёмник сверяет и отбрасывает битые кадры. */
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
    digitalWrite(LED_PIN, LOW);
    delay(ms);
    digitalWrite(LED_PIN, HIGH);
    delay(ms);
  }
}

/* ------------------------- КОМАНДЫ ОТ UNO Q ------------------------------ */
void uartPumpCommands() {
  while (CAMUART.available()) {
    char c = (char)CAMUART.read();
    switch (c) {
      case 'G': streamOn = true;  break;   // пустить поток
      case 'S': streamOn = false; break;   // пауза (буфер копится)
      case 'F': snapReq  = true;  break;   // один свежий кадр
      case 'I': statReq  = true;  break;   // статус текстом
      case 'Z': for (int i = 0; i < RING_N; i++) ringReady[i] = false; break;
      default:  break;                     // мусор и переводы строк — игнор
    }
  }
}

/* --------------------------- КОЛЬЦЕВОЙ БУФЕР ----------------------------- */
/* Положить кадр в буфер; если свободных слотов нет — вытесняем самый старый */
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
    framesLost++;                        // самый старый вытеснен
  }
  memcpy(ringBuf[slot], data, len);
  ringLen[slot]   = len;
  ringSeq[slot]   = seqGen;
  ringReady[slot] = true;
  seqGen = (uint8_t)(seqGen + 1);
}

/* Самый старый готовый слот (или -1) */
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
/* Кадр: заголовок 8 байт + payload кусками CHUNK; между кусками слушаем
 * команды ('S' не рвёт начатый кадр — он дошливается). */
void sendFrame(const uint8_t *data, uint16_t len, uint8_t type, uint8_t seq) {
  uint16_t crc = crc16x(data, len);
  uint8_t h[8] = { 0xA5, 0x5C, type, seq,
                   (uint8_t)(len & 0xFF), (uint8_t)(len >> 8),
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
    delay(1);                            // дать UNO Q и себе выдохнуть
  }
  digitalWrite(LED_PIN, LOW);
  delay(2);
  digitalWrite(LED_PIN, HIGH);
}

void sendSlot(int slot) {
  sendFrame(ringBuf[slot], ringLen[slot], 0x01, ringSeq[slot]);
  ringReady[slot] = false;
  framesSent++;
}

/* Текстовая строка (кадр типа 0x02, seq = 0) */
void sendText(const char *s) {
  uint16_t len = (uint16_t)strlen(s);
  if (len > 512) len = 512;
  sendFrame((const uint8_t *)s, len, 0x02, 0);
  CAMUART.flush();
}

/* Статус: видно, сколько снято/отправлено/вытеснено и чем занят буфер */
void sendStatus() {
  char js[192];
  int ready = 0;
  for (int i = 0; i < RING_N; i++)
    if (ringReady[i]) ready++;
  snprintf(js, sizeof(js),
           "{\"frames\":%u,\"sent\":%u,\"lost\":%u,\"buf\":%d,"
           "\"stream\":%d,\"baud\":%u,\"ms\":%u}",
           (unsigned)framesGot, (unsigned)framesSent, (unsigned)framesLost,
           ready, (int)streamOn, (unsigned)CAM_BAUD, (unsigned)millis());
  sendText(js);
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
  cfg.grab_mode    = CAMERA_GRAB_LATEST;   // всегда свежий кадр

  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) { Serial.printf("[UART-CAM] init error 0x%x\n", err); return false; }

  sensor_t *s = esp_camera_sensor_get();
  if (s) { s->set_vflip(s, 0); s->set_hmirror(s, 0); }
  return true;
}

/* ------------------------------- СТАРТ ----------------------------------- */
void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);             // погашен (горит при LOW)

  Serial.begin(115200);                    // только диагностика (UART0)
  Serial.println("\n[UART-CAM] esp32cam_uart v1.0: стрим JPEG по UART на UNO Q");

  if (!initCamera()) {
    Serial.println("[UART-CAM] камера не поднялась — перезапуск");
    ledBlink(5, 80);
    delay(2000);
    ESP.restart();
  }

  /* Кольцевой буфер кадров — строго в PSRAM */
  bool ok = psramFound();
  for (int i = 0; i < RING_N; i++) {
    ringBuf[i] = (uint8_t *)ps_malloc(FRAME_MAX);
    if (!ringBuf[i]) ok = false;
    ringReady[i] = false;
    ringLen[i]   = 0;
  }
  if (!ok) {
    Serial.println("[UART-CAM] нет PSRAM/буфера: проверьте сборку платы "
                   "(PSRAM enabled) или уменьшите FRAME_MAX/RING_N");
    ledBlink(5, 80);
    delay(2000);
    ESP.restart();
  }

#if UART_USE_SERIAL0
  CAMUART.begin(CAM_BAUD);                 // UART0: TX=1, RX=3 (разъём прошивки)
#else
  CAMUART.begin(CAM_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
#endif

  Serial.printf("[UART-CAM] UART готов: %u бод; %s\n", (unsigned)CAM_BAUD,
#if UART_USE_SERIAL0
                "TX=GPIO1 RX=GPIO3 (разъём прошивки)"
#else
                "TX=GPIO13 -> RX UNO Q, RX=GPIO14 <- TX UNO Q"
#endif
               );
  Serial.println("[UART-CAM] формат кадра: A5 5C | тип | seq | len(2) | crc16(2) | payload");
  Serial.println("[UART-CAM] команды UNO Q: G=пуск S=пауза F=кадр I=статус Z=очистить");
  ledBlink(3, 120);
  sendStatus();                            // приветственный статус (тип 0x02)
}

/* ------------------------------- ЦИКЛ ------------------------------------ */
void loop() {
  uartPumpCommands();

  if (statReq) { statReq = false; sendStatus(); }

  /* 1) «F» — снять СВЕЖИЙ кадр и выслать немедленно, минуя буфер */
  if (snapReq) {
    snapReq = false;
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      if (fb->len <= FRAME_MAX) {
        sendFrame(fb->buf, (uint16_t)fb->len, 0x01, seqGen);
        seqGen = (uint8_t)(seqGen + 1);
        framesGot++;
        framesSent++;
      } else {
        framesLost++;
      }
      esp_camera_fb_return(fb);
    }
  }

  /* 2) захват кадра в кольцевой буфер (с шагом MIN_FRAME_MS) */
  if (millis() - lastCap >= MIN_FRAME_MS) {
    lastCap = millis();
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      if (fb->len <= FRAME_MAX) { ringPut(fb->buf, (uint16_t)fb->len); framesGot++; }
      else { framesLost++; }
      esp_camera_fb_return(fb);
    }
  }

  /* 3) выгрузка буфера в UART: старейший готовый кадр за итерацию */
  if (streamOn || snapReq) {
    int slot = ringOldest();
    if (slot >= 0) sendSlot(slot);
  }

  delay(2);
}
