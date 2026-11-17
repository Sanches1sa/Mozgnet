/* n12_vision.cpp — зрение камеры для мозга-12 (см. n12_vision.h). */
/* v1.3: TensorFlow Lite Micro — ДВА ВАРИАНТА, выбор сам:
 *  - ядро ESP32 3.x: официальная esp-tflite-micro Espressif уже в ядре
 *    (быстрее, ускорение esp-nn) — библиотека не нужна;
 *  - ядро ESP32 2.x: библиотека TensorFlowLite_ESP32 1.0.0 (Менеджер библиотек).
 * Старая библиотека НЕ собирается компилятором GCC 14 ядра 3.x (ошибка
 * «assignment of read-only member count_» в flatbuffers/stl_emulation.h),
 * поэтому на ядре 3.x она не подключается вовсе. */
#if defined(ARDUINO) && !__has_include("tensorflow/lite/micro/micro_interpreter.h")
#include <TensorFlowLite_ESP32.h>   // ядро 2.x: подключить библиотеку
#endif
#include "n12_vision.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "n12_model_data.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#if __has_include("tensorflow/lite/micro/micro_error_reporter.h")
#include "tensorflow/lite/micro/micro_error_reporter.h"
#define N12_TFLM_OLD 1               // старый API (TensorFlowLite_ESP32 1.0.0)
#else
#define N12_TFLM_OLD 0               // новый API (esp-tflite-micro / TFLM 2023+)
#endif
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

namespace n12 {

/* ============================== нейросеть =============================== */
namespace {
#if N12_TFLM_OLD
tflite::MicroErrorReporter g_rep;
#endif
const tflite::Model *g_model = nullptr;
tflite::MicroInterpreter *g_interp = nullptr;
TfLiteTensor *g_in = nullptr;
alignas(16) uint8_t g_interpMem[sizeof(tflite::MicroInterpreter)];
tflite::MicroMutableOpResolver<5> g_ops;
}  // namespace

bool modelInit(uint8_t *arena, size_t arenaSize, char *err, size_t errn) {
  g_model = tflite::GetModel(g_n12_model);
  if (g_model->version() != TFLITE_SCHEMA_VERSION) {
    snprintf(err, errn, "схема модели %d, библиотека ждёт %d", (int)g_model->version(),
             (int)TFLITE_SCHEMA_VERSION);
    return false;
  }
  // ровно те 5 операций, из которых состоит person_detect (без AllOpsResolver)
  g_ops.AddAveragePool2D();
  g_ops.AddConv2D();
  g_ops.AddDepthwiseConv2D();
  g_ops.AddReshape();
  g_ops.AddSoftmax();
#if N12_TFLM_OLD
  g_interp = new (g_interpMem) tflite::MicroInterpreter(g_model, g_ops, arena, arenaSize, &g_rep);
#else
  g_interp = new (g_interpMem) tflite::MicroInterpreter(g_model, g_ops, arena, arenaSize);
#endif
  if (g_interp->AllocateTensors() != kTfLiteOk) {
    snprintf(err, errn, "мало памяти для нейросети (arena %u байт)", (unsigned)arenaSize);
    return false;
  }
  g_in = g_interp->input(0);
  if (!g_in || g_in->type != kTfLiteInt8 || g_in->bytes != (size_t)(kIn * kIn)) {
    snprintf(err, errn, "неожиданный вход модели");
    return false;
  }
  snprintf(err, errn, "ok: arena занято %u из %u байт", (unsigned)g_interp->arena_used_bytes(),
           (unsigned)arenaSize);
  return true;
}

int8_t *modelInput() { return g_in ? g_in->data.int8 : nullptr; }

bool modelRun(float *person) {
  if (!g_interp) return false;
  if (g_interp->Invoke() != kTfLiteOk) return false;
  TfLiteTensor *out = g_interp->output(0);
  const float sc = out->params.scale;
  const int zp = out->params.zero_point;
  *person = (out->data.int8[N12_MODEL_PERSON] - zp) * sc;   // softmax: 0..1
  if (*person < 0) *person = 0;
  if (*person > 1) *person = 1;
  return true;
}

/* =========================== подготовка кадра =========================== */
void accumStart(Accum &a, int srcW, int srcH) {
  memset(a.sum, 0, sizeof(a.sum));
  memset(a.cnt, 0, sizeof(a.cnt));
  a.srcW = srcW;
  a.srcH = srcH;
  a.side = srcW < srcH ? srcW : srcH;           // центральный квадрат
  a.ox = (srcW - a.side) / 2;
  a.oy = (srcH - a.side) / 2;
}

static inline void accPix(Accum &a, int x, int y, uint32_t g) {
  int cx = x - a.ox, cy = y - a.oy;
  if (cx < 0 || cy < 0 || cx >= a.side || cy >= a.side) return;
  int tx = cx * kIn / a.side, ty = cy * kIn / a.side;
  int k = ty * kIn + tx;
  a.sum[k] += g;
  a.cnt[k]++;
}

void accumRGB(Accum &a, int x, int y, int w, int h, const uint8_t *rgb) {
  for (int iy = 0; iy < h; iy++)
    for (int ix = 0; ix < w; ix++, rgb += 3)
      accPix(a, x + ix, y + iy, (77u * rgb[0] + 150u * rgb[1] + 29u * rgb[2]) >> 8);
}

void accumBGR(Accum &a, const uint8_t *bgr, int w, int h) {
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++, bgr += 3)
      accPix(a, x, y, (29u * bgr[0] + 150u * bgr[1] + 77u * bgr[2]) >> 8);
}

void accumGray(Accum &a, const uint8_t *gray, int w, int h) {
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) accPix(a, x, y, gray[y * w + x]);
}

void accumFinish(Accum &a, int8_t *in, int8_t *prev, Scene &s) {
  uint64_t tot = 0, tot2 = 0;
  uint32_t diff = 0;
  for (int k = 0; k < kIn * kIn; k++) {
    uint32_t g = a.cnt[k] ? a.sum[k] / a.cnt[k] : 0;
    if (g > 255) g = 255;
    tot += g;
    tot2 += g * g;
    int8_t q = (int8_t)((int)g - 128);           // вход модели: яркость - 128
    if (prev) {
      int d = (int)q - (int)prev[k];
      diff += (uint32_t)(d < 0 ? -d : d);
      prev[k] = q;
    }
    in[k] = q;
  }
  const float n = (float)(kIn * kIn);
  float mean = tot / n;
  float var = tot2 / n - mean * mean;
  s.bright = mean / 255.0f;
  s.contrast = sqrtf(var > 0 ? var : 0) / 128.0f;
  if (s.contrast > 1) s.contrast = 1;
  float md = diff / n / 255.0f;                  // средняя разница кадров
  float m = (md - 0.015f) * 10.0f;               // шум сенсора ~0.01 не в счёт
  s.motion = m < 0 ? 0 : (m > 1 ? 1 : m);
}

/* ============================ словарь камеры ============================ */
namespace {
Word g_lex[LEX_MAX];
int g_n = 0;

struct Base { const char *w; float v[NF]; };
// [человек, пусто, светло, темно, движение, покой]
const Base kBase[] = {
  {"человек", {1, 0, 0, 0, .2f, 0}},    {"лицо", {1, 0, .2f, 0, 0, .2f}},
  {"люди", {1, 0, 0, 0, .4f, 0}},       {"кто-то", {.8f, 0, 0, .3f, .3f, 0}},
  {"гость", {.9f, 0, 0, 0, .4f, 0}},    {"друг", {.9f, 0, .2f, 0, 0, .2f}},
  {"смотрит", {.9f, 0, 0, 0, 0, .4f}},  {"рядом", {.8f, 0, 0, 0, 0, .3f}},
  {"привет", {.9f, 0, .3f, 0, .3f, 0}},
  {"пусто", {0, 1, 0, 0, 0, .4f}},      {"тишина", {0, .8f, 0, .2f, 0, .8f}},
  {"комната", {0, .9f, .2f, 0, 0, .3f}},{"никого", {0, 1, 0, 0, 0, .3f}},
  {"стена", {0, .8f, 0, 0, 0, .6f}},    {"один", {0, .9f, 0, .3f, 0, .3f}},
  {"покой", {0, .6f, 0, 0, 0, 1}},
  {"свет", {0, 0, 1, 0, 0, 0}},         {"день", {0, 0, .9f, 0, .2f, 0}},
  {"солнце", {0, 0, 1, 0, 0, .2f}},     {"ясно", {0, 0, .9f, 0, 0, .3f}},
  {"светло", {0, 0, 1, 0, 0, 0}},
  {"темно", {0, 0, 0, 1, 0, 0}},        {"ночь", {0, .3f, 0, 1, 0, .3f}},
  {"тень", {.2f, 0, 0, .8f, .2f, 0}},   {"сон", {0, .3f, 0, .8f, 0, .8f}},
  {"вечер", {0, 0, 0, .8f, 0, .3f}},
  {"движение", {0, 0, 0, 0, 1, 0}},     {"идёт", {.5f, 0, 0, 0, 1, 0}},
  {"шаг", {.5f, 0, 0, 0, .9f, 0}},      {"быстро", {0, 0, 0, 0, 1, 0}},
  {"жизнь", {.5f, 0, .3f, 0, .7f, 0}},
  {"тихо", {0, .4f, 0, .2f, 0, 1}},     {"спокойно", {0, .3f, 0, 0, 0, 1}},
  {"ждать", {0, .3f, 0, 0, 0, .9f}},    {"замер", {.3f, 0, 0, 0, 0, 1}},
};
const char *kStop[] = {"что", "это", "как", "так", "для", "его", "она", "они", "оно", "мне",
                       "меня", "нас", "вас", "все", "уже", "еще", "был", "была", "или", "там",
                       "тут", "при", "про", "над", "под", "без", "кто", "где", "тот", "эта",
                       "этот", "мой", "твой", "наш", "ваш", "себя", "чтобы", "если", "когда"};

bool isStop(const char *w) {
  for (const char *s : kStop)
    if (!strcmp(w, s)) return true;
  return false;
}

int cps(const char *s) {          // кодовых точек UTF-8
  int n = 0;
  for (; *s; s++)
    if (((unsigned char)*s & 0xC0) != 0x80) n++;
  return n;
}

// следующий токен-слово (буквы UTF-8, латиница, цифры, дефис внутри слова)
const char *nextWord(const char *p, char *w, size_t n) {
  auto letter = [](unsigned char c) {
    return c >= 0x80 || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
  };
  while (*p && !letter((unsigned char)*p)) p++;
  if (!*p) return nullptr;
  size_t k = 0;
  while (*p && (letter((unsigned char)*p) || (*p == '-' && letter((unsigned char)p[1])))) {
    if (k + 1 < n) w[k++] = *p;
    p++;
  }
  // не оставлять обрезанный многобайтный символ
  while (k > 0 && ((unsigned char)w[k - 1] & 0xC0) == 0xC0) k--;
  w[k] = 0;
  lowerUtf8(w);
  return p;
}

int findStem(const char *word) {
  char a[24], b[24];
  stem(word, a, sizeof(a));
  for (int i = 0; i < g_n; i++) {
    stem(g_lex[i].w, b, sizeof(b));
    if (!strcmp(a, b)) return i;
  }
  return -1;
}

float cosine(const float *a, const float *b) {
  float ab = 0, aa = 0, bb = 0;
  for (int i = 0; i < NF; i++) {
    ab += a[i] * b[i];
    aa += a[i] * a[i];
    bb += b[i] * b[i];
  }
  if (aa < 1e-6f || bb < 1e-6f) return 0;
  return ab / sqrtf(aa * bb);
}

bool content(const char *w) { return cps(w) >= 3 && !isStop(w); }
}  // namespace

void lowerUtf8(char *s) {
  unsigned char *p = (unsigned char *)s;
  while (*p) {
    if (*p >= 'A' && *p <= 'Z') { *p += 32; p++; continue; }
    if (p[0] == 0xD0 && p[1]) {
      if (p[1] >= 0x90 && p[1] <= 0x9F) p[1] += 0x20;                    // А-П
      else if (p[1] >= 0xA0 && p[1] <= 0xAF) { p[0] = 0xD1; p[1] -= 0x20; } // Р-Я
      else if (p[1] == 0x81) { p[1] = 0xB5; }                             // Ё -> е
      p += 2; continue;
    }
    if (p[0] == 0xD1 && p[1] == 0x91) { p[0] = 0xD0; p[1] = 0xB5; p += 2; continue; } // ё -> е
    p++;
  }
}

void stem(const char *w, char *out, size_t n, int k) {
  if (k <= 0) {                      // основа по длине: «мама/маму» -> «мам»,
    int L = cps(w);                  // «человек/человека» -> «челов»
    k = L >= 6 ? 5 : (L - 1 < 3 ? 3 : L - 1);
  }
  size_t i = 0;
  int cp = 0;
  while (w[i] && i + 1 < n) {
    if (((unsigned char)w[i] & 0xC0) != 0x80) {
      if (cp == k) break;
      cp++;
    }
    out[i] = w[i];
    i++;
  }
  out[i] = 0;
}

void features(const Scene &s, float f[NF]) {
  // пары признаков «заостряются»: неясная сцена (p≈0.5) ничего не учит
  auto pair = [](float x, float &pos, float &neg) {
    pos = x > 0.5f ? (x - 0.5f) * 2 : 0;
    neg = x < 0.5f ? (0.5f - x) * 2 : 0;
  };
  pair(s.person, f[F_PERSON], f[F_EMPTY]);
  float b = (s.bright - 0.4f) / 0.4f * 0.5f + 0.5f;  // комнатный свет ≈ середина
  b = b < 0 ? 0 : (b > 1 ? 1 : b);
  pair(b, f[F_BRIGHT], f[F_DARK]);
  f[F_MOTION] = s.motion * 3 > 1 ? 1 : s.motion * 3;   // движение — заметное изменение кадра
  f[F_CALM] = s.motion < 0.02f ? 0.6f : 0;              // кадр стоит — покой
}

void lexInit() {
  g_n = 0;
  for (const Base &b : kBase) {
    if (g_n >= LEX_MAX) break;
    Word &w = g_lex[g_n++];
    snprintf(w.w, sizeof(w.w), "%s", b.w);
    lowerUtf8(w.w);
    memcpy(w.v, b.v, sizeof(w.v));
    w.uses = 0;
    w.base = 1;
  }
}

int lexCount() { return g_n; }
const Word &lexAt(int i) { return g_lex[i]; }

float wordScore(const char *word, const float f[NF]) {
  char w[WORD_MAX];
  snprintf(w, sizeof(w), "%s", word);
  lowerUtf8(w);
  int i = findStem(w);
  if (i < 0) return -1;
  float c = cosine(g_lex[i].v, f);
  float fam = g_lex[i].uses > 10 ? 1.0f : 0.7f + 0.03f * g_lex[i].uses;  // привычные слова увереннее
  return c * fam;
}

int lexTop(const float f[NF], int k, int *idx, float *sc) {
  int m = 0;                              // отсортировано по убыванию счёта
  for (int i = 0; i < g_n; i++) {
    float fam = g_lex[i].uses > 10 ? 1.0f : 0.7f + 0.03f * g_lex[i].uses;
    float s = cosine(g_lex[i].v, f) * fam;
    if (s < 0.25f) continue;
    int j;
    if (m < k) j = m++;
    else if (s > sc[k - 1]) j = k - 1;
    else continue;
    while (j > 0 && sc[j - 1] < s) {
      sc[j] = sc[j - 1];
      idx[j] = idx[j - 1];
      j--;
    }
    sc[j] = s;
    idx[j] = i;
  }
  return m;
}

int lexLearn(const char *phrase, const float f[NF]) {
  float mag = 0;
  for (int i = 0; i < NF; i++) mag += f[i];
  if (mag < 0.2f) return 0;              // сцена неясная — учить нечему
  char w[WORD_MAX];
  int learned = 0;
  const char *p = phrase;
  while ((p = nextWord(p, w, sizeof(w))) != nullptr) {
    if (!content(w)) continue;
    int i = findStem(w);
    if (i >= 0) {                        // знакомое слово тянется к сцене
      for (int k = 0; k < NF; k++) g_lex[i].v[k] = 0.85f * g_lex[i].v[k] + 0.15f * f[k];
      if (g_lex[i].uses < 60000) g_lex[i].uses++;
    } else {                             // новое слово мозга — привязать к сцене
      if (g_n < LEX_MAX) i = g_n++;
      else {                             // место занято — вытеснить редкое выученное
        int worst = -1;
        for (int k = 0; k < g_n; k++)
          if (!g_lex[k].base && (worst < 0 || g_lex[k].uses < g_lex[worst].uses)) worst = k;
        if (worst < 0) continue;
        i = worst;
      }
      Word &nw = g_lex[i];
      snprintf(nw.w, sizeof(nw.w), "%s", w);
      for (int k = 0; k < NF; k++) nw.v[k] = 0.6f * f[k];
      nw.uses = 1;
      nw.base = 0;
    }
    learned++;
  }
  return learned;
}

void vote(const char *const *phrases, int n, const float f[NF], float *out) {
  int ti[1];
  float ts[1];
  char top[WORD_MAX] = "", topStem[24] = "", w[WORD_MAX], st[24];
  if (lexTop(f, 1, ti, ts) == 1) {
    snprintf(top, sizeof(top), "%s", g_lex[ti[0]].w);
    stem(top, topStem, sizeof(topStem));
  }
  for (int i = 0; i < n; i++) {
    float sum = 0;
    int cnt = 0;
    bool hasTop = false;
    const char *p = phrases[i];
    while ((p = nextWord(p, w, sizeof(w))) != nullptr) {
      if (!content(w)) continue;
      float s = wordScore(w, f);
      sum += s < 0 ? 0.2f : s;           // слово не из словаря камеры — нейтрально
      cnt++;
      stem(w, st, sizeof(st));
      if (topStem[0] && !strcmp(st, topStem)) hasTop = true;
    }
    float v = cnt ? sum / cnt : 0.2f;
    if (hasTop) v += 0.15f;
    out[i] = v > 1 ? 1 : v;
  }
}

size_t lexSave(uint8_t *buf, size_t max) {
  size_t o = 0;
  if (max < 4) return 0;
  buf[o++] = 'N';
  buf[o++] = 'L';
  buf[o++] = 1;
  size_t cntAt = o++;
  uint8_t cnt = 0;
  for (int i = 0; i < g_n; i++) {
    const Word &w = g_lex[i];
    if (w.base && !w.uses) continue;     // нетронутую базу не храним
    size_t L = strlen(w.w);
    if (o + 1 + L + NF + 3 > max) break;
    buf[o++] = (uint8_t)L;
    memcpy(buf + o, w.w, L);
    o += L;
    for (int k = 0; k < NF; k++) buf[o++] = (uint8_t)(w.v[k] * 100 + 0.5f);
    buf[o++] = (uint8_t)(w.uses & 0xFF);
    buf[o++] = (uint8_t)(w.uses >> 8);
    buf[o++] = w.base;
    cnt++;
  }
  buf[cntAt] = cnt;
  return o;
}

bool lexLoad(const uint8_t *buf, size_t n) {
  if (n < 4 || buf[0] != 'N' || buf[1] != 'L' || buf[2] != 1) return false;
  size_t o = 4;
  for (int e = 0; e < buf[3]; e++) {
    if (o >= n) return false;
    size_t L = buf[o++];
    if (L >= WORD_MAX || o + L + NF + 3 > n) return false;
    char w[WORD_MAX];
    memcpy(w, buf + o, L);
    w[L] = 0;
    o += L;
    float v[NF];
    for (int k = 0; k < NF; k++) v[k] = buf[o++] / 100.0f;
    uint16_t uses = (uint16_t)(buf[o] | (buf[o + 1] << 8));
    o += 2;
    uint8_t base = buf[o++];
    int i = findStem(w);
    if (i < 0) {
      if (g_n >= LEX_MAX) continue;
      i = g_n++;
      snprintf(g_lex[i].w, sizeof(g_lex[i].w), "%s", w);
    }
    memcpy(g_lex[i].v, v, sizeof(v));
    g_lex[i].uses = uses;
    g_lex[i].base = base;
  }
  return true;
}

/* ================================= JSON ================================= */
int visJson(char *out, size_t n, const Scene &s, const float f[NF], int topK) {
  int idx[8];
  float sc[8];
  if (topK > 8) topK = 8;
  int m = lexTop(f, topK, idx, sc);
  int o = snprintf(out, n,
                   "{\"t\":\"vis\",\"n\":%u,\"p\":%.3f,\"br\":%.3f,\"ct\":%.3f,\"mo\":%.3f,"
                   "\"ms\":%u,\"f\":[%.2f,%.2f,%.2f,%.2f,%.2f,%.2f],\"lex\":%d,\"w\":[",
                   (unsigned)s.n, s.person, s.bright, s.contrast, s.motion, (unsigned)s.ms, f[0],
                   f[1], f[2], f[3], f[4], f[5], g_n);
  for (int i = 0; i < m && o < (int)n - 48; i++)
    o += snprintf(out + o, n - o, "%s[\"%s\",%.3f]", i ? "," : "", g_lex[idx[i]].w, sc[i]);
  o += snprintf(out + o, n - o, "]}");
  return o;
}

int voteJson(char *out, size_t n, uint32_t id, const float *sc, int cnt) {
  int best = 0;
  for (int i = 1; i < cnt; i++)
    if (sc[i] > sc[best]) best = i;
  int o = snprintf(out, n, "{\"t\":\"vote\",\"id\":%u,\"s\":[", (unsigned)id);
  for (int i = 0; i < cnt && o < (int)n - 16; i++)
    o += snprintf(out + o, n - o, "%s%.3f", i ? "," : "", sc[i]);
  o += snprintf(out + o, n - o, "],\"pick\":%d}", cnt ? best : -1);
  return o;
}

}  // namespace n12
