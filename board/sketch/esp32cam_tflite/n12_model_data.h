/* n12_model_data.h — нейросеть камеры, ВШИТАЯ В ПРОШИВКУ (генерируется, не править).
 * Модель: person_detect (MobileNetV1 0.25, вход 96x96x1 оттенки серого,
 *   выход 2 класса: [0] «нет человека», [1] «человек»), обучена Google
 *   (TensorFlow Lite Micro, репозиторий tensorflow/tflite-micro,
 *   tensorflow/lite/micro/models/person_detect.tflite). Лицензия Apache 2.0.
 * КВАНТОВАНИЕ: полностью целочисленная int8 (post-training full-integer):
 *   веса int8 по каналам — 207968 байт (28 тензоров со шкалой на канал),
 *   смещения int32 — 10960 байт; вход/выход int8.
 *   вход:  scale=0.007843138 zero_point=-1  -> int8 = яркость - 128
 *   выход: scale=0.00390625 zero_point=-128 -> p = (q + 128) / 256
 * Размер 300568 байт, SHA-256 808cfdfc0cf3a6fa6f6fa26bfa379ea97c16d5db7334637766e39c3408502e9d.
 * Лежит во flash (const) — в оперативную память не копируется. */
#pragma once
#include <stdint.h>
extern const unsigned char g_n12_model[];
extern const unsigned int g_n12_model_len;
#define N12_MODEL_IN_W 96
#define N12_MODEL_IN_H 96
#define N12_MODEL_PERSON 1
#define N12_MODEL_EMPTY 0
