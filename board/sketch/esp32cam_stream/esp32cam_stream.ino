/* ============================================================================
 * esp32cam_stream.ino — ВИДЕО-ИСТОЧНИК ДЛЯ МОЗГА-12 (v2.3, ФИКСИРОВАННЫЙ IP)
 * Плата: ESP32-CAM (AI-Thinker, модуль OV2640)
 * ----------------------------------------------------------------------------
 * НОВОЕ v2.3 (neuro_12 v2.28): ФИКСИРОВАННЫЙ IP-АДРЕС — мозг и облако всегда
 *   знают, где камера: CAM_IP задан константой, ключ cam_url конфига мозга
 *   = "http://CAM_IP". После прошивки поменяйте только WIFI_SSID/WIFI_PASS
 *   (и CAM_IP, если ваша сеть другая) — DHCP-сканирование не нужно.
 *
 * Отдаёт MJPEG-поток по WiFi:
 *     http://<CAM_IP>:81/stream   — непрерывный поток (MJPEG)
 *     http://<CAM_IP>/snap        — одиночный JPEG-кадр (его тянет мозг)
 *     http://<CAM_IP>/stats       — JSON со счётчиком кадров
 *
 * Цепочка «мозг видит стрим»: ESP32-CAM -> (snap) -> Python-мозг main.py
 * (воркер cam_worker v2.28) -> облако (/api/vision/frame с YOLO или
 * /api/vision/raw) -> стрим в браузере (страница /cam облака) + буфер
 * детекций (/api/vision/buffer) обратно мозгу -> скетч (val_b_set) ->
 * total_val_c + частотная шина D4 (CAM_HZ_SPAN) -> нейроны.
 *
 * Библиотеки: esp32-camera входит в ядро Espressif Arduino (Board: AI Thinker ESP32-CAM).
 * ========================================================================== */

#include "esp_camera.h"
#include <WiFi.h>
#include <esp_http_server.h>

/* ----------------------------- НАСТРОЙКИ WIFI ---------------------------- */
#define WIFI_SSID  "ВАШ_WIFI"
#define WIFI_PASS  "ВАШ_ПАРОЛЬ"

/* v2.3: ФИКСИРОВАННЫЙ IP (CAM_STATIC 1) или DHCP (CAM_STATIC 0).
 * CAM_IP должен совпадать с ключом cam_url в конфиге мозга (config.json):
 * мозг тянет http://<CAM_IP>/snap и кладёт кадры в облако. */
#define CAM_STATIC  1
#define CAM_IP      "192.168.1.77"
#define CAM_GW      "192.168.1.1"     // шлюз (обычно роутер)
#define CAM_SN      "255.255.255.0"   // маска подсети

#define STREAM_PORT 81

/* Распиновка AI-Thinker ESP32-CAM */
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

httpd_handle_t streamHttpd = nullptr;
httpd_handle_t cameraHttpd = nullptr;
volatile uint32_t frameCnt = 0;

/* ----------------------------- СТАРТ КАМЕРЫ ------------------------------ */
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
  cfg.frame_size   = FRAMESIZE_SVGA;   // 600x800 — достаточно для YOLO
  cfg.jpeg_quality = 12;
  cfg.fb_count     = 2;
  cfg.fb_location  = CAMERA_FB_IN_PSRAM;
  cfg.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) { Serial.printf("[CAM] init error 0x%x\n", err); return false; }

  sensor_t *s = esp_camera_sensor_get();
  if (s) { s->set_vflip(s, 0); s->set_hmirror(s, 0); s->set_saturation(s, 0); }
  return true;
}

/* ----------------------------- ПОТОК MJPEG ------------------------------- */
esp_err_t streamHandler(httpd_req_t *req) {
  char part[64];
  httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=frame");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { Serial.println("[CAM] fb_get fail"); delay(100); continue; }
    frameCnt++;

    size_t hlen = snprintf(part, sizeof(part),
                           "\r\n--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                           (unsigned)fb->len);
    if (httpd_resp_send_chunk(req, part, hlen) != ESP_OK ||
        httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len) != ESP_OK ||
        httpd_resp_send_chunk(req, "\r\n", 2) != ESP_OK) {
      esp_camera_fb_return(fb);
      break;                              // клиент отключился (например, UNO Q)
    }
    esp_camera_fb_return(fb);
  }
  return ESP_OK;
}

esp_err_t snapHandler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return ESP_OK;
}

esp_err_t statsHandler(httpd_req_t *req) {
  char js[128];
  snprintf(js, sizeof(js),
           "{\"frames\":%u,\"uptime_ms\":%u,\"ip\":\"%s\",\"static\":%d}",
           (unsigned)frameCnt, (unsigned)millis(), CAM_IP, (int)CAM_STATIC);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, js, strlen(js));
}

void startServers() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = STREAM_PORT;

  httpd_uri_t uriStream = { .uri = "/stream", .method = HTTP_GET, .handler = streamHandler, .user_ctx = nullptr };
  if (httpd_start(&streamHttpd, &cfg) == ESP_OK) httpd_register_uri_handler(streamHttpd, &uriStream);

  cfg.server_port = 80;
  httpd_uri_t uriSnap  = { .uri = "/snap",  .method = HTTP_GET, .handler = snapHandler,  .user_ctx = nullptr };
  httpd_uri_t uriStats = { .uri = "/stats", .method = HTTP_GET, .handler = statsHandler, .user_ctx = nullptr };
  if (httpd_start(&cameraHttpd, &cfg) == ESP_OK) {
    httpd_register_uri_handler(cameraHttpd, &uriSnap);
    httpd_register_uri_handler(cameraHttpd, &uriStats);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(4, OUTPUT);            // вспышка — погашена
  digitalWrite(4, LOW);

  if (!initCamera()) { delay(3000); ESP.restart(); }

  WiFi.mode(WIFI_STA);
#if CAM_STATIC
  /* v2.3: ФИКСИРОВАННЫЙ IP — ДО WiFi.begin(): мозг и облако всегда знают,
   * где камера; адрес не «уезжает» после ребута роутера */
  IPAddress ip, gw, sn;
  ip.fromString(CAM_IP);
  gw.fromString(CAM_GW);
  sn.fromString(CAM_SN);
  WiFi.config(ip, gw, gw, sn);   // (local_ip, gateway, subnet) + DNS = шлюз
#endif
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[CAM] WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.println();
  Serial.printf("[CAM] готов (%s). STREAM: http://%s:%d/stream  SNAP: http://%s/snap\n",
                CAM_STATIC ? "фиксированный IP" : "DHCP",
                WiFi.localIP().toString().c_str(), STREAM_PORT,
                WiFi.localIP().toString().c_str());
  Serial.printf("[CAM] впишите в конфиг мозга: \"cam_url\": \"http://%s\"\n",
                WiFi.localIP().toString().c_str());
  startServers();
}

void loop() {
  delay(10000);                  // серверы работают в фоновых задачах httpd
  Serial.printf("[CAM] кадров отдано: %u\n", (unsigned)frameCnt);
}
