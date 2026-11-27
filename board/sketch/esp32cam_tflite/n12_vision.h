/* n12_vision.h — ЗРЕНИЕ КАМЕРЫ ДЛЯ МОЗГА-12 (esp32cam_tflite v1.0)
 * Переносимое ядро (собирается и для ESP32, и на ПК для проверки):
 *   1) кадр -> вход нейросети 96x96 (центр кадра, оттенки серого, int8);
 *   2) нейросеть TFLite Micro (person_detect, int8) -> «человек в кадре»;
 *   3) признаки сцены: человек / пусто / светло / темно / движение / покой;
 *   4) СЛОВАРЬ КАМЕРЫ: слово -> вектор признаков сцены. Камера предлагает
 *      слова под то, что видит, ГОЛОСУЕТ за варианты фраз мозга neuro12 и
 *      УЧИТСЯ на словах, которые мозг в итоге сказал (слово привязывается к
 *      сцене, при которой прозвучало). Так плата и камера выбирают слова
 *      ВМЕСТЕ: мозг предлагает, камера голосует тем, что видит, обе стороны
 *      запоминают итог.
 */
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace n12 {

constexpr int kIn = 96;            // сторона входа нейросети
constexpr int NF = 6;              // признаков сцены
enum { F_PERSON = 0, F_EMPTY, F_BRIGHT, F_DARK, F_MOTION, F_CALM };

struct Scene {
  float person = 0;     // вероятность «человек в кадре» от нейросети, 0..1
  float bright = 0;     // средняя яркость, 0..1
  float contrast = 0;   // разброс яркости, 0..1
  float motion = 0;     // изменение кадра относительно прошлого, 0..1
  uint32_t ms = 0;      // время работы нейросети, мс
  uint32_t n = 0;       // номер распознавания
  bool ok = false;
};

/* ---------------- нейросеть ---------------- */
// arena — рабочая память интерпретатора (ESP32: ~81 КБ внутренней RAM).
bool modelInit(uint8_t *arena, size_t arenaSize, char *err, size_t errn);
int8_t *modelInput();                       // 96*96 int8, заполнить перед modelRun
bool modelRun(float *person);               // true — успешно

/* ------------- подготовка кадра ------------- */
// Накопитель: RGB-блоки декодера JPEG -> средняя яркость клеток 96x96.
// srcW/srcH — размер декодированного изображения; берётся центральный квадрат.
struct Accum {
  uint32_t sum[kIn * kIn];
  uint16_t cnt[kIn * kIn];
  int srcW = 0, srcH = 0, side = 0, ox = 0, oy = 0;
};
void accumStart(Accum &a, int srcW, int srcH);
void accumRGB(Accum &a, int x, int y, int w, int h, const uint8_t *rgb); // RGB888 блок
void accumGray(Accum &a, const uint8_t *gray, int w, int h);              // целый серый кадр
void accumBGR(Accum &a, const uint8_t *bgr, int w, int h);                // целый кадр B,G,R (fmt2rgb888)
// Готовый вход int8 (яркость-128) + яркость/контраст; prev — прошлый вход (движение).
void accumFinish(Accum &a, int8_t *in, int8_t *prev, Scene &s);

/* ---------------- словарь камеры ---------------- */
constexpr int LEX_MAX = 96;        // слов всего (база + выученные)
constexpr int WORD_MAX = 32;       // байт UTF-8 на слово

struct Word {
  char w[WORD_MAX];
  float v[NF];        // с какой сценой слово связано
  uint16_t uses;      // сколько раз мозг его сказал при камере
  uint8_t base;       // 1 — встроенное слово
};

void features(const Scene &s, float f[NF]);
void lexInit();                                       // встроенные слова
int lexCount();
const Word &lexAt(int i);
float wordScore(const char *word, const float f[NF]); // 0..1, -1 если слова нет
// топ-k слов для сцены: idx и счёт; возвращает сколько найдено
int lexTop(const float f[NF], int k, int *idx, float *sc);
// мозг сказал фразу — слова привязываются к сцене; возвращает сколько слов выучено/усилено
int lexLearn(const char *phrase, const float f[NF]);
// голос за фразы: out[i] в 0..1
void vote(const char *const *phrases, int n, const float f[NF], float *out);
// сохранить/загрузить выученное (база не пишется)
size_t lexSave(uint8_t *buf, size_t max);
bool lexLoad(const uint8_t *buf, size_t n);

/* ---------------- JSON для UART ---------------- */
int visJson(char *out, size_t n, const Scene &s, const float f[NF], int topK);
int voteJson(char *out, size_t n, uint32_t id, const float *sc, int cnt);

/* ------------- UTF-8: нижний регистр кириллицы, основа слова ------------- */
void lowerUtf8(char *s);
// основа для сравнения форм слова: первые k букв; k = 0 — по длине слова
// (6+ букв -> 5, короче -> длина-1, не меньше 3): «мама/маму» -> «мам»
void stem(const char *w, char *out, size_t n, int k = 0);

}  // namespace n12
