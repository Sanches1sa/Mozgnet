#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ===========================================================================
# neuro_12 / python/main.py — МОЗГ «ИСКУССТВЕННЫЙ МОЗГ-12» для Arduino App Lab
# (Linux-сторона UNO Q, QRB2210; приложение импортируется из zip)
# ---------------------------------------------------------------------------
# Архитектура (v2.4, «буквенный генератор» + App Lab):
#   sketch/sketch.ino (MCU, STM32U585) держит нейросеть букв 10x36: число
#   1..33 -> буква русского алфавита, классы 34/35/36 -> ПРОБЕЛ/ЗАПЯТАЯ/ТОЧКА.
#   Буквы собираются в слова, слова — во фразы (итоговый буфер SAY). Номер
#   буквы кладётся в частотную шину D4->A0 плат-нейронов (их код НЕ меняется),
#   слова уходят им стимулом STIM и произносятся динамиком D8 нотами.
#
#   Этот файл (Linux-сторона) связывается со скетчем ОФИЦИАЛЬНЫМ мостом
#   Arduino_RouterBridge (MessagePack-RPC, библиотека app-bricks-py):
#     - скетч -> Python : notify-события on_state/on_letter/on_word/on_say/
#                         on_msg (периодические данные и готовые фразы);
#     - Python -> скетч : call-методы chat_stim/val_b_set/cloud_buf_set/
#                         skill_out_set/set_voice/set_hzcfg/speak_tone/
#                         teach_score (все обработчики — provide_safe,
#                         т.е. исполняются в потоке loop() скетча).
#
#   v2.16, МОСТ-ИСПРАВЛЕНИЕ (жил только на старте):
#     1) on_say больше НЕ вызывает tts_say синхронно внутри notify-
#        обработчика: Bridge.call(say_wave) + HTTP-TTS до 40 с блокировали
#        диспетчер моста — сводки переставали идти (чип «мост: ждёт скетч»);
#        речь теперь уходит в фоновый поток;
#     2) ВСЕ исходящие Bridge.call идут через ОДНУ очередь-работник
#        (_bridge_queue): параллельные вызовы из потоков диктанта, учителя,
#        TTS и чата больше не интерливятся на одном RPC-канале; для
#        обучения (teach_last/teach_shape/teach_score) действует
#        «последний главнее» — старые оценки выкидываются, канал не забивается;
#     3) живость моста видна по ЛЮБОМУ пакету скетча (on_state/on_msg/
#        on_net/on_letter/on_word/on_say), а не только по on_state;
#     4) окно живости 10 с (скетч занят опросом 12 нейронов — это норма)
#        + активный пинг ping с приоритетом вне очереди.
#
#   v2.16, РЕЧЬ МОЗГА ИЗ СЛОВАРЯ (буквогенератор переделан):
#     мозг на плате сам генерирует фразы СЛОВАМИ ИЗ СЛОВАРЯ на основании
#     того, что он знает: биграммная сеть строится по памяти диалогов,
#     файлам, заметкам наставника и выученным словам; выбор каждого
#     следующего слова подсказывает зонная нейросеть (_NN, голоса связей).
#     Буквы сгенерированной фразы человек НЕ видит (v2.19: скрытно), они
#     уходят физически на пин D6 (RPC d6seq -> PWM), фраза запоминается,
#     озвучивается речью через облако (TTS), а её самое
#     длинное слово ставится в диктант — буквогенератор скетча учится
#     произносить слова, которые мозг реально использует. Команда «мысль»
#     (или «мысль <тема>») — попросить мозг высказаться.
#
#   v2.35: МЫСЛИ ЗОН ОБЛАКА — НА ПИН D6 («как уже реализовано» для фраз
#     самого мозга): облако (neuro12-cloud v3.17) кладёт ответы своих зон
#     («Зоны мысли») в ящик d6box.json; плата забирает их опросом
#     /api/brain/d6 в цикле мозг-почты (_cloud_d6) и проигрывает тем же
#     физическим каналом, что и свои фразы: _gen_d6_seq -> RPC d6seq ->
#     PWM-байты на пине D6 (50 мс/байт). Это ВЫВОД, а не письмо-вопрос:
#     ответ в облако не шлётся. Нейроны слушают мысли облака (nn_excite),
#     сеть получает их материалом «мысль облака» (единый мозг).
#
#   ОТВЕЧАЕТ ТОЛЬКО НЕЙРОСЕТЬ ПЛАТЫ (main.py): зонная сеть + навыки + KB +
#   память собирают текст сами (zone_reply/_long_reply). LLM НЕ ОТВЕЧАЕТ:
#   старый прокси /api/llm/ask на облаке закрыт (410), вместо него
#   /api/llm/teach — ТОЛЬКО учительские цели (purpose):
#     word_goal  — выбрать слово-цель диктанта;
#     verdict    — мягкий вердикт ±1 по фразе буквогенератора;
#     advice     — СОВЕТ нейросети, КАК отвечать (темы/ключи/тон/размер
#                  сети «НЕЙРОНЫ: N»), НЕ ответ.
#   v2.24: КОД LLM БОЛЬШЕ НЕ ПИШЕТ вовсе — кодовый генератор работает ОТ
#   МОЗГА (заготовки-семейства + локальная проверка). LLM — только
#   наставление. Совет-наставление (advice, v2.14) сеть применяет сама:
#   ключи усиливают нужные зоны и поиск по памяти/KB, текст ответа пишет
#   нейросеть платы.
#
#   v2.25: НЕЙРОСЕТЬ МАТЕРИАЛОВ (mozgnet.py) — сеть, которая УЧИТСЯ на
#   материалах (загруженные файлы, KB облака, диалоги, мысли, показания)
#   и ПИШЕТ ДЛИННЫЙ ТЕКСТ по материалам: узлы — основы слов, связи —
#   выученные ассоциации, документы — tf-idf векторы; запрос зажигает
#   узлы (разрядка активации), активные нейроны выбирают материалы.
#   Команда «статья <тема>» и POST /api/mozgnet/write. LLM НЕ участвует.
#   ПОКАЗАНИЯ сети (узлы/связи/активные темы) уходят в облако
#   (POST /api/chat/readings) — текстовый генератор чата облака берёт их
#   как контекст задания (плюс мысли устройства).
#   Прямой URL ядра (direct) остался только для учительских вызовов.
#   LLM-УЧИТЕЛЬ (v2.8) — МОЛЧУН: он больше НЕ пишет в чат. Он только учит:
#     - словарная награда: каждое слово фразы проверяется по словарю русского
#       языка (30 000 частотных слов в комплекте), буквы реальных слов
#       подкрепляются (teach_shape), выдумки наказываются — работает ВСЕГДА,
#       без облака и без LLM;
#     - ДИКТАНТ: мозгу даётся целевое слово, каждая буква сравнивается на
#       лету (teach_last ±1) — плотный сигнал вместо редких ±1;
#     - LLM (облако/прямой URL) даёт мягкий вердикт ±1 и выбирает целевые
#       слова из сообщений пользователя; советы складывает в панель
#       «Учитель», а не в чат. Ответы LLM отключены (v2.14): если нейросети
#       непонятно — она ТИХО пишет функцию-навык сама (v2.13).
#
#   ОБЛАКО КАК МОЗГ (v2.8) — все фичи платформы мозг-12 теперь доступны:
#     - поиск: POST {cloud}/api/web/fetch {"q"} — Википедия (SSRF-защита);
#     - код:   POST {cloud}/api/sandbox/run {"code"} — песочница облака
#       (ПРОВЕРКА своего кода; сам код мозг пишет только сам, v2.24);
#     - реестр функций: /api/skills + запуск (v2.24: кодогенератор —
#       только локальный, LLM-написание функций отключено).
#     Новое из находок мозг складывает в память (выученные слова тоже).
#
#   СВЯЗЬ С ОБЛАКОМ (v2.20): плата регистрируется С АДРЕСОМ (POST
#   /api/brains/register: имя, kind=board, url=http://<ip>:7000) — Studio
#   показывает адрес, «проверить связь» в облаке делает полный тест письмом
#   туда-обратно. Мысли мозга НЕ ТЕРЯЮТСЯ: сборщик копит фразы и раз в
#   THOUGHT_FLUSH сек пишет их ОДНИМ текстом в чат облака «Мысли плат»
#   (/api/chat/thought, v2.4.7) + пофразно в ленту Studio + в хронику.
#   Письма чата облака отвечаются знаниями (память/KB/советы), а не
#   шаблонами зон.
#
#   TTS (v2.14): слова из итогового буфера SAY озвучивает динамик D8
#   (мелодия нот — всегда). Речь — ЧЕРЕЗ ОБЛАКО: POST {cloud_url}/api/tts
#   {"text": ...} -> WAV (Silero/Piper/espeak, а без движка — встроенный
#   «нейроголос» облака: WAV есть всегда). Дальше WAV идёт В СКЕТЧ
#   (say_wave: огибающая «частота:длительность» — играет D8), а если мост
#   молчит — динамик платы (aplay/paplay/ffplay), иначе — браузер
#   (/api/tts_audio). Кирпич arduino:tts НЕ нужен: стартует на любой плате.
#
#   ПАМЯТЬ (v2.7) — ТРИ слоя: 1) файл neuro12_memory.json на плате (реплики
#   диалога для LLM, история чата, счётчики обучения) — переживает
#   перезапуск; 2) ОБЛАКО — POST/GET {cloud_url}/api/memory/save|load
#   (X-Mozg-Token): страховка и переезд на чистую плату, у каждой платы
#   свой device-бандл; 3) живой чат в RAM. Синхронизация фоновая; в панели
#   «⚙ Настройки» — кнопки «↻ синхронизировать» и «забыть всё».
#
#   Веб-интерфейс (assets/index.html + app.js) раздаёт кирпич arduino:web_ui
#   на порту 7000: чат, лента букв, частота шины, панель настроек.
#
#   СЛОВА ПЛАТЕ ИЗ БОЛЬШОГО СЛОВАРЯ (v2.27): буквогенератор платы больше
#   не ограничен 64 встроенными словами — word_pump_tick раз в 0.5 с (v2.34;
#   было ~8-11 с) выбирает слово из ru_top.json (30 000 частых слов, перекос
#   к частым; 15% — выученные из облака, 20% — «вспомненные» из журнала)
#   и шлёт скетчу RPC word_push — слово встаёт в ОЧЕРЕДЬ платы (память на
#   каждое слово); скетч
#   произносит его обычным конвейером (буквы — решения учителю, теги
#   нейронам, D6/D8, SAY=). Словари хранятся и в ОБЛАКЕ: при подключении
#   облака мозг сверяет словари (/api/dict/list|get|put) — свои выгружает,
#   если на облаке их нет/меньше; облачные, которых нет локально, забирает
#   (дельта кладётся в dicts_cloud.json и переживает перезапуск). Если
#   локальных словарей нет вообще — берёт всё с облака. LLM по-прежнему
#   только наставник.
#
#   КАМЕРА И ПЛАТФОРМА (v2.28): мозг тянет кадры ESP32-CAM (скетч
#   sketch/esp32cam_stream — ФИКСИРОВАННЫЙ IP, ключ cam_url в конфиге),
#   шлёт их в облако (YOLO — /api/vision/frame, без YOLO — /api/vision/raw),
#   оттуда виден СТРИМ (страница /cam) и ВЫКАЧИВАЕТСЯ БУФЕР детекций
#   (/api/vision/buffer) — сводка уходит на скетч ключом val_b_set
#   (VB= в total_val_c + вклад камеры в частотную шину D4: CAM_HZ_SPAN),
#   нейроны «чувствуют» камеру без правки прошивок. ПЛАТФОРМА: мозг сам
#   читает статьи KB облака, лазит в интернет (поиск Википедии через
#   /api/web/fetch), ПИШЕТ свои статьи (/api/kb/import — генератор облака
#   учится у мозга) и СТАВИТ ГЕНЕРАТОРУ ЗАДАНИЯ (/api/gen/task) — мысли
#   мозга направляют облако. Назначение номеров нейронам (SETID) — RPC
#   neuro_assign скетча (прошивка neuron_node v2.6 с EEPROM-номером).
#
#   СБРОС НЕЙРОНОВ (v2.29): команда «нейроны сброс» — RPC neuro_reset шлёт
#   всем платам "CLR_ID" (общий вызов): прошивка neuron_node v2.7 стирает
#   номер из EEPROM и снова ждёт SETID. Лечит ДУБЛИ номеров («слиплись»):
#   после сброса подключать платы ПО ОДНОЙ и говорить «нейроны».
#
#   КАМЕРА ПО ПРОВОДУ (v2.30): в скетче хаба (v2.30) есть ПРИЁМНИК кадров
#   UART — ESP32-CAM (sketch/esp32cam_uart) шлёт JPEG по проводу. Ключ
#   cam_url = "uart": мозг собирает кадр кусками (RPC cam_frame_info +
#   cam_chunk, base64) прямо с платы и несёт его в облако тем же
#   конвейером, что и WiFi-камеру (YOLO/стрим /cam/буфер -> платы).
#
#   ЗОНЫ ЗНАНИЙ, КИРПИЧИКОВ И ЛЮБОПЫТСТВА (v2.31): зонная сеть растёт
#   до 15 (аппаратные 12 плат-нейронов как раньше зажигают первые 12 зон,
#   новые — софтовые):
#     «Знания» — мозг ВЫБИРАЕТ, какую статью прочитать, чтобы ответить:
#       поиск по KB облака, лучшая статья читается ЦЕЛИКОМ и уходит в
#       нейросеть материалов, ответ собирается по ней (kb_read_best);
#     «Кирпичики» — мозг сам ПРОГРАММИРУЕТ приложения App Lab: в базе
#       примеров кода (встроенные рецепты + скачанные из облака
#       GET /api/applab/recipes) он выбирает рецепт под вопрос, собирает
#       стопку кирпичиков и СОХРАНЯЕТ приложение в облаке
#       (POST /api/applab/save, X-Mozg-Token; bricks_build). Реагирует
#       скетч: тон speak_tone + слово в диктант word_push;
#     «Любопытство» — мозг ищет в интернете ПО СВОИМ МЫСЛЯМ и вопросу
#       (Википедия через облако /api/web/fetch), выжимки падают в
#       материалы и память, мысль уходит в ленту облака (_curiosity_search);
#       периодический веб-поиск платформы теперь тоже иногда берёт тему
#       из СОБСТВЕННЫХ мыслей мозга (_plat_last_own_topic).
#   v2.31.1: калибровка zone_of — новые живые зоны выигрывают равные
#       споры стемов («что такое нейрон?» -> Знания, а не «Вопросы»):
#       вопросы к знаниям действительно читают статью, а не падают в
#       заготовку. Скетч и сети не менялись.
#
#   МАТЕМАТИКА (v2.32): мозг сам ПИШЕТ ФОРМУЛЫ и СТРОИТ МОДЕЛИ, без LLM:
#     16-я зона «Математика» (сеть 15 -> 16 нейронов, миграция без сброса):
#       база формул (встроенная + скачанная из облака
#       GET /api/math/formulas?full=1 -> dicts/math_formulas.json, _maths_sync),
#       вопрос -> формула -> числа из вопроса -> расчёт с формулой и
#       подстановкой (_mm_eval — безопасный AST-вычислитель с белым
#       списком, произвольный код не исполняется); «модель по данным
#       1,2; 2,4; 3,5 — прогноз 4» -> линейная/квадратичная/
#       экспоненциальная модель (выбор по R²; против переобучения:
#       квадратичной нужно >= 4 точек, экспоненте >= 3) + прогноз;
#       слабая модель честно названа слабой. Плюс арифметика
#       («посчитай 12*8»). Скетч реагирует: тон 990 Гц + слово в
#       диктант (_sketch_react("math")).
#
#   v2.40: (1) СЕТЬ МАТЕРИАЛОВ ВСТРОЕНА В main.py — App Lab не видел
#       соседний mozgnet.py, импорт падал и сеть молча выключалась; теперь
#       модуль собирается из встроенного текста (_MN_SRC), файл не нужен.
#       (2) ТЕНЗОРНАЯ СЕТЬ ВЕДЁТ МЫСЛИ И КОД, ЗОННАЯ ПОМОГАЕТ (секция [8a]):
#       тема мысли и выбор лучшего из вариантов фразы — за тензором; задачу
#       на код узнаёт тензор (или ключи), код — свои заготовки -> ИИ-агент
#       облака -> заготовки облака -> память кода, синтаксис Python
#       проверяется на плате. (3) ОБУЧЕНИЕ НА ВОПРОСАХ И ОТВЕТАХ, честно:
#       «верно»/«неверно»/«правильный ответ: …», тензор учится только на
#       зонах, известных не из его догадки. (4) ВЕСЬ МОЗГ ОБЛАКА: страница
#       «Облако» и вопросы без ключей/голоса сетей -> POST /api/brain/think.
#       (5) Дефис между числами в обычной фразе («версии 1.7 - 1.8») — не
#       минус: математика его не считает без просьбы посчитать.
#
#   v2.41: ЕДИНЫЙ МОЗГ (секция [8b]) — плата и облако дополняют друг друга:
#       письмо облака «unify» (его ответ слабый) плата отвечает с честной
#       пометкой опоры: подтверждённый ответ / память кода / файл платы /
#       живое состояние, иначе — «мнение зонной сети без опоры»; раз в
#       5 минут подтверждённое знание обеих сторон обменивается
#       (/api/brain/learn, /api/brain/knowledge).
# ===========================================================================

import base64
import json
import math
import os
import random
import re
import socket
import threading
import time
import traceback
import zlib
from collections import deque

# v2.82: БЫСТРАЯ ЗАПИСЬ JSON. json.dump() в файл кодирует ПО КУСОЧКАМ на чистом
# Python (в ~6 раз медленнее json.dumps на C) и всё это время держит GIL:
# пока сохраняется большая сеть, веб-интерфейс и API ждут — «долго грузятся
# настройки». Подмена на весь процесс: тот же результат, кодирует C-кодер.
def _n12_fast_dump(obj, fp, **kw):
    fp.write(json.dumps(obj, **kw))


json.dump = _n12_fast_dump


from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI

# v2.87: мост падает на битом UTF-8 (0x93/0x91/0xa6 с шумящей шины).
# Патчим декодер библиотеки, чтобы он не ронял read_loop.
try:
    import arduino.app_utils.bridge as _br
    _orig = getattr(_br, "_decode", None) or getattr(_br, "decode", None)
    def _safe_decode(data, *a, **kw):
        if isinstance(data, (bytes, bytearray)):
            return data.decode("utf-8", errors="replace")
        return data
    if hasattr(_br, "Bridge"):
        _cls = _br.Bridge
        if hasattr(_cls, "_read_loop"):
            _old = _cls._read_loop
            def _patched(self, *a, **kw):
                try:
                    return _old(self, *a, **kw)
                except UnicodeDecodeError as e:
                    print("[bridge] UTF-8 ignored:", e)
                    return None
            _cls._read_loop = _patched
except Exception:
    pass

# НИКАКИХ других кирпичей не требуется: arduino:llm в установленной версии
# App Lab отсутствует (из-за него приложение не запускалось), arduino:tts
# тоже не подключён. LLM и TTS работают ЧЕРЕЗ ОБЛАКО — см. секции [4] и [6].

APP_VERSION = "2.94.2"

# ---------------------------------------------------------------------------
#   v2.33 (2026): МОСТ-КАНАЛ БЕЗ ТАЙМАУТОВ — фикс «word_push не работает,
#   cloud_buf_set тоже, chat_stim/say_wave — time out». Диагноз по коду и
#   правилам Arduino (Bridge-обработчики ОБЯЗАНЫ быть мгновенными):
#     1) rpcNeuroAssign скетча держал delay(400) на каждый из 12 нейронов —
#        4.8 с БЛОКИРОВКИ loop() ВНУТРИ Bridge-вызова. В этом окне любой
#        chat_stim/say_wave/word_push/cloud_buf_set падал по таймауту, а
#        поток-работник очереди мог застрять на timeout=150 с (наш старый
#        neuro_assign) — тогда «молчало» всё подряд несколько минут.
#        Теперь: назначение — state-машина в loop() (sketch v2.33,
#        neuroAssignTick, RPC neuro_assign_poll), мозг только запускает и
#        опрашивает отчёт (_neuro_assign_run, максимум 30 с).
#     2) Очередь RPC: у вызовов появилась метка времени постановки; вызов,
#        прождавший в очереди дольше timeout+4 с, СБРАСЫВАЕТСЯ (устаревший
#        say_wave/word_push выполняться с опозданием не будет).
#     3) ПРОЗРАЧНОСТЬ: статистика RPC по методам (_RPC_STATS -> /api/state
#        bridge_stats): сколько прошло/упало и последняя ошибка — «word_push
#        не работает» теперь ВИДНО С ПРИЧИНОЙ. word_pump_tick проверяет
#        СВЕЖЕСТЬ моста (не «когда-то отвечал») и пишет честную причину в
#        word_pump.err. Слетч: neuro_reset без delay(50).
#
#   v2.34 (2026): СЛОВА ПЛАТЕ — КАЖДЫЕ 0.5 С И ПАМЯТЬ НА КАЖДОЕ СЛОВО.
#     Раньше слово уходило плате раз в 8-11 с (_WORD_PUMP_PERIOD = 8.0 +
#     случайные 0-3 с) — плата молчала между словами. Теперь:
#     1) ПЕРИОД 0.5 С (без случайной паузы): слово уходит сразу же.
#     2) ПАМЯТЬ НА КАЖДОЕ СЛОВО, два слоя:
#        • СКЕТЧ — кольцевая ОЧЕРЕДЬ слов (WQ_N = 20): раньше хранилось
#          только одно непроизнесённое слово, и при частой отсылке слова
#          ТЕРЯЛИСЬ. Теперь каждое ждёт своей очереди и произносится;
#          переполнение — честный счётчик (RPC word_q).
#        • МОЗГ — ЖУРНАЛ СЛОВ (words_memory.json рядом с конфигом,
#          переживает перезапуск): каждое отосланное слово со временем,
#          счётчик уникальных; /api/state показывает последние слова и
#          очередь платы. Память РАБОТАЕТ: сэмплер не повторяет последние
#          5 слов и в 20% случаев «вспоминает» старое слово из журнала.
#     3) ТЕМП СКЕТЧА под темп слов: буква раз в 80 мс (было 650) — слово
#        из 3 букв ≈ 0.4 с; частотный тег буквы 70 мс; буфер слова на
#        D6 — 50 мс/байт. Скетч: очередь слов + word_q (sketch v2.34).
#
#   v2.38 (2026): ЗНАКИ ПРЕПИНАНИЯ В СЛОВАРЕ — МОЗГ ВЫБИРАЕТ ЗАПЯТУЮ,
#     ТОЧКУ, «!» И «?». Просьба владельца: «введи знаки в нейросеть, не
#     меняя саму сеть, — чтобы она их тоже выбирала». Сделано словарём:
#     1) ru_top.json: 4 знака — записи словаря (конец списка, редкий ранг),
#        dict_set дополнен знаками даже при старом файле.
#     2) СЭМПЛЕР СЛОВ (_dict_sample_word): в 10% случаев выбирает ЗНАК и
#        шлёт его плате тем же word_push; повтор знака подряд исключён.
#     3) ФРАЗЫ МОЗГА (brain_speak): между словами иногда запятая (до двух),
#        финальный знак ВЫБИРАЕТСЯ, а не пришит: Вопросы -> «?», Юмор ->
#        «!», иначе по весам (. 60% / ! 20% / ? 20%).
#     4) СКЕТЧ (sketch v2.38): word_push принимает одиночный знак; знак из
#        очереди проводится фразой той же механикой, что запятая/точка
#        буквенной сети (SAY/D6/мелодия/on_letter). СЕТЬ БУКВ НЕ ТРОНУТА:
#        10x36, веса и решения учителю — как были, новых выходов нет.
#     5) Диктант защищён: dict_pick выметает знаки из источника.
#
#   v2.39 (2026): ТЕНЗОРНОЕ ЯДРО (секция [3t]) — MLP 76 -> H -> зоны поверх
#     зонной сети. Объём H = 8..64 задаёт частота генератора скетча; ресурс
#     зависит от задачи: тяжёлое считает сервер (существующая песочница
#     /api/sandbox/run), затем компьютер :7000 (Web Worker браузера), иначе
#     плата. Входы — текст + спайки 12 аппаратных нейронов (прошивки не
#     менялись). Все прежние функции сохранены; новых файлов нет.
# ---------------------------------------------------------------------------

# v2.11: разбор реплик нейронов из хвоста MSG — «N5:1042:сигнал|N7:22:привет»
_RE_NEURON = re.compile(r"N(\d{1,2}):(\d+):")


def log(*parts):
    print("[МОЗГ]", *parts, flush=True)


# ===========================================================================
# [1] КОНФИГ: env APP_HOME -> /app -> cwd; файл neuro12_config.json
# ===========================================================================
def _base_dirs():
    out = []
    for cand in (os.environ.get("APP_HOME"), "/app", os.getcwd(), "/tmp"):
        if cand and cand not in out and os.path.isdir(cand):
            out.append(cand)
    return out or [os.getcwd()]


_CFG_NAME = "neuro12_config.json"
_MEM_NAME = "neuro12_memory.json"

DEFAULT_CFG = {
    "cloud_url": "",            # ВНЕШНИЙ сервер: мозг-почта, память, буфер,
                                # навыки, Википедия, LLM-учитель, TTS (v2.24:
                                # кирпич убран — снова единое облако)
    "token": "",                # X-Mozg-Token внешнего облака
    "direct_url": "",           # OpenAI-совместимый URL, напр. ".../v1"
    "direct_key": "",           # Bearer-ключ прямого ядра (если нужен)
    "direct_model": "",         # имя модели прямого ядра
    "tts_on": True,             # озвучивать фразы (TTS через облако)
    "tts_voice": "",            # голос облака (пусто = по умолчанию)
    "teach_on": True,           # LLM-учитель: оценивать фразы (teach_score)
    "whisper_hint": True,       # добавлять статистику мозга в шёпот
    "mem_sync": True,           # память (диалоги+чат) дублировать в облако
    "dictate_on": True,         # ДИКТАНТ: целевые слова для буквогенератора
    "curious_on": True,         # ЛЮБОПЫТСТВО: мозг сам ищет новое в облаке
    "brain_on": True,           # v2.10: ЕДИНЫЙ МОЗГ — мозг-почта через облако
    "long_answers": True,       # v2.10: мозг пишет КАК МОЖНО ДЛИННЕЕ (без LLM)
    "advice_on": True,          # v2.14: LLM-наставник даёт СОВЕТЫ нейросети
                                # (как отвечать); ответ строит сама сеть
    "gen_on": True,             # v2.16: мозг сам говорит фразами из словаря
    "grav_on": True,            # v2.66: «гравитация мыслей» (модель, не физика)
    "sync_on": True,            # v2.67: подтяжка генератора к общему ритму платформы
    "game_on": True,            # v2.69: мозгу можно играть в «РОК-12» облака (сам решает)
    "mic_on": True,             # v2.70: уши мозга — USB-микрофон платы -> речь в облако
    "mic_dev": "default",       # v2.70: устройство ALSA для arecord (default, plughw:1,0 …)
    "mic_board_asr": True,      # v2.93: Vosk НА ПЛАТЕ — речь в текст прямо на плате
    "code_from_cloud": True,    # v2.93: облако может проверять/запускать код на плате (раздел «Код»)
    "admin_from_cloud": True,   # v2.93: окно «Серверы» облака может выполнять команды в контейнере платы
    "vr_on": True,              # v2.94: голосовой модуль ELECHOUSE VR3 на D12/D13
    "vr_load": "0,1,2,3,4,5,6", # v2.94: какие записи (0..79) грузить в распознаватель (до 7)
    "vr_to_brain": True,        # v2.94: узнанная команда = реплика человека мозгу
    "mic_min_rms": 350,         # v2.70: порог громкости начала фразы (минимум)
    "grav_g": 1.0,              # v2.66: сила тяги (0 — выкл., до 3)
                                # (по тому, что знает: память/файлы/наставник)
    "words_to_board": True,     # v2.27: плата произносит слова из БОЛЬШОГО
                                # словаря (ru_top.json) — RPC word_push
    "dict_sync": True,          # v2.27: словари синхронизировать с облаком
                                # (свои — туда, облачные — к себе)
    "cam_url": "",              # v2.28: адрес камеры ESP32-CAM, напр.
                                # "http://192.168.1.77"; v2.30: "uart" —
                                # камера ПО ПРОВОДУ (приёмник в скетче v2.30)
    "cam_on": True,             # v2.28: камерный воркер (кадры -> облако/плата)
    "cam_ai": True,             # v2.88: камера с нейросетью (esp32cam_tflite)
                                # выбирает слова вместе с мозгом (по проводу)
    "cam_ai_w": 0.35,           # v2.88: вес голоса камеры (0..1)
    "platform_on": True,        # v2.28: воркер платформы — читать статьи,
                                # интернет, писать статьи, задания генератору
}


def _cfg_path():
    """Путь конфига: существующий файл (если есть) -> первый доступный
    для записи каталог -> первый каталог. Совпадает с тем, куда реально
    пишет save_cfg()."""
    for d in _base_dirs():
        p = os.path.join(d, _CFG_NAME)
        if os.path.exists(p):
            return p
    for d in _base_dirs():
        if os.access(d, os.W_OK):
            return os.path.join(d, _CFG_NAME)
    return os.path.join(_base_dirs()[0], _CFG_NAME)


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    for d in _base_dirs():
        p = os.path.join(d, _CFG_NAME)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in cfg if k in data})
            log("конфиг:", p)
            break
        except Exception:
            continue
    return cfg


def save_cfg(cfg):
    payload = json.dumps(cfg, ensure_ascii=False, indent=2)
    targets = []
    # 1) обновить существующий конфиг на месте; 2) иначе первый записываемый
    for d in _base_dirs():
        p = os.path.join(d, _CFG_NAME)
        if os.path.exists(p):
            targets.append(p)
    for d in _base_dirs():
        p = os.path.join(d, _CFG_NAME)
        if p not in targets and os.access(d, os.W_OK):
            targets.append(p)
    for p in targets:
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(payload)
            log("конфиг сохранён:", p)
            return p
        except Exception:
            continue
    log("конфиг сохранить не удалось (нет прав на запись)")
    return ""


def _hostname():
    """Имя платы для облачной памяти (каждая плата хранит свой бандл)."""
    try:
        import socket
        return ((socket.gethostname() or "").strip() or "unoq")[:40]
    except Exception:
        return "unoq"


def _mem_path():
    """Путь файла памяти: существующий файл -> первый доступный каталог."""
    for d in _base_dirs():
        p = os.path.join(d, _MEM_NAME)
        if os.path.exists(p):
            return p
    for d in _base_dirs():
        if os.access(d, os.W_OK):
            return os.path.join(d, _MEM_NAME)
    return os.path.join(_base_dirs()[0], _MEM_NAME)


def load_memory():
    """Память из прошлого запуска. v2.7 — бандл {'memory','chat','state'};
    старый формат (просто список [{'role','text'}]) читается как LLM-память."""
    for d in _base_dirs():
        try:
            with open(os.path.join(d, _MEM_NAME), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, list):
            return {"memory": [m for m in data if isinstance(m, dict)][-60:],
                    "chat": [], "state": {}, "ts": 0}
        if isinstance(data, dict):
            st = data.get("state")
            return {"memory": [m for m in (data.get("memory") or [])
                               if isinstance(m, dict)][-60:],
                    "chat": [m for m in (data.get("chat") or [])
                             if isinstance(m, dict)][-300:],
                    "state": st if isinstance(st, dict) else {},
                    "ts": int(data.get("ts") or 0)}
        break
    return {"memory": [], "chat": [], "state": {}, "ts": 0}


def save_memory(bundle):
    """Атомарно записать бандл памяти (tmp -> rename). Путь или '' при сбое."""
    payload = json.dumps(bundle, ensure_ascii=False, indent=1)
    p = _mem_path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, p)
        return p
    except Exception:
        return ""


CFG = load_cfg()

# ===========================================================================
# [2] ОБЩЕЕ СОСТОЯНИЕ
# ===========================================================================
class Shared:
    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.messages = deque(maxlen=300)   # чат: {seq,role,zone,text,ts}
        _b = load_memory()                   # бандл памяти прошлого запуска
        self.memory = _b["memory"]           # [{'role','text'}] для LLM
        # живые данные скетча (on_state / on_msg / on_letter / on_word / on_say)
        self.gen_hz = 0
        self.ltr = 0
        self.chr = ""
        self.word = ""
        self.say = ""
        self.spk = 0
        self.act = 0
        self.top = -1
        self.cyc = 0
        self.msg = ""
        self.net = ""                # v2.11: карта связи с нейронами «110101…» (on_net)
        self.net_ts = 0.0            # v2.11: когда пришла последняя карта связи
        # v2.19: лента букв УБИРАЕНА — человек не видит ни букв, ни слов
        # буквогенератора (его «мысли»); буквы фраз уходят физически на D6,
        # наружу выходят только готовые фразы.
        self.words_made = 0
        self.letters_made = 0
        self.bridge_ts = 0.0                 # когда скетч был жив последний раз
        self.bridge_seen = False             # v2.14: скетч хоть раз отвечал
        self.bridge_dropped = 0              # v2.18: сброшено устаревших вызовов
        self.llm_state = {"via": "", "ok": False, "ts": 0.0, "err": ""}
        self.tts_state = {"available": True, "via": "", "err": "", "ts": 0.0}
        # живая проверка облака (чип «облако» в UI): доступность + задержка
        self.cloud_state = {"configured": False, "ok": False, "checking": False,
                            "ts": 0.0, "err": "", "ms": 0}
        self.tts_audio = {"id": 0, "b64": "", "mime": "audio/wav"}
        self.teach_q = deque(maxlen=6)       # фразы, ждущие оценки учителя
        self.last_say = ""                   # последняя фраза для ручной оценки
        self.teach_count = 0
        # --- v2.8: облачные фичи, словари, новая система обучения ----------
        self.teacher_note = ""               # последняя мысль LLM-учителя (НЕ в чат)
        self.cloud_code = ""                 # код из редактора «облака» (переживает запуск)
        self.learn = {"hits": 0, "miss": 0, "rewards": 0, "letters": 0,
                      "dict_wins": 0, "dict_tries": 0}
        self.dict_set = set()                # частотный словарь (грузится фоном)
        self.dict_pos = {"nouns": [], "verbs": [], "adjs": []}
        self.dict_extra = set()              # слова, выученные из облака
        self.dict_ready = False
        self.dict_top = []                   # v2.27: слова ПО РАНГУ частоты
                                             # (ru_top.json; облако добавляет)
        self.dict_cloud = {"ok": False, "err": "", "ts": 0.0,
                           "uploaded": 0, "merged": 0, "cloud_words": 0}
        # v2.34: word_pump — статус насоса слов + ПАМЯТЬ на каждое слово:
        # hist (последние слова), mem_unique/mem_count (журнал), q/q_pushed/
        # q_dropped (очередь платы — от RPC word_q)
        self.word_pump = {"ts": 0.0, "last_word": "", "count": 0, "err": "",
                          "hist": [], "mem_unique": 0, "mem_count": 0,
                          "mem_file": "", "q": -1, "q_pushed": 0,
                          "q_dropped": 0}
        self.dictation = {"on": True, "target": "", "src": "", "pos": 0,
                          "cur": "", "tries": 0, "wins": 0,
                          "queue": deque(maxlen=8)}
        # статус облачной памяти (чип «память» в UI)
        self.mem_state = {"mode": "local", "ok": False, "err": "", "ts": 0.0,
                          "pushed": 0, "pulled": 0}
        self.mem_dirty = False               # есть несохранённые изменения
        # --- v2.9: страница «Облако» (ChatGPT-стиль) и файлы ----------------
        self.docs = deque(maxlen=6)          # разобранные документы (RAM):
                                             # {name,kind,bytes,chars,ts,note,text}
        self.doc_img = {"name": "", "b64": "", "mime": "image/jpeg",
                        "w": 0, "h": 0}      # последнее фото для vision-запроса
        # --- v2.10: ЕДИНЫЙ МОЗГ — мозг-почта (письма других устройств) ------
        self.brainmail = {"letters": 0, "answered": 0, "sent": 0,
                          "d6": 0, "last_d6": "",
                          "last_from": "", "last_ts": 0.0, "err": ""}
        # восстановление истории чата и счётчиков прошлого запуска
        for m in _b["chat"]:
            m.setdefault("seq", 0)
            self.messages.append(m)
        if self.messages:
            self.seq = max(int(m.get("seq", 0) or 0) for m in self.messages)
        _st = _b["state"]
        try:
            self.teach_count = int(_st.get("teach_count") or 0)
            self.letters_made = int(_st.get("letters_made") or 0)
            self.words_made = int(_st.get("words_made") or 0)
            self.last_say = str(_st.get("last_say") or "")[:200]
            self.teacher_note = str(_st.get("teacher_note") or "")[:600]
            self.cloud_code = str(_st.get("cloud_code") or "")[:24000]
            _ln = _st.get("learn")
            if isinstance(_ln, dict):
                for k in self.learn:
                    if k in _ln:
                        self.learn[k] = int(_ln.get(k) or 0)
            ex = _st.get("dict_extra")
            if isinstance(ex, list):
                self.dict_extra = set(str(w)[:24] for w in ex[-500:]
                                      if w and str(w) not in (
                                          "англ", "лат", "греч",
                                          "франц", "итал", "исп", "санскр"))
            dt = _st.get("dictation")
            if isinstance(dt, dict):
                self.dictation["target"] = str(dt.get("target") or "")[:24]
                self.dictation["src"] = str(dt.get("src") or "")[:20]
                self.dictation["wins"] = int(dt.get("wins") or 0)
                self.dictation["tries"] = 0
        except Exception:
            pass
        self.started_ts = time.time()


SH = Shared()


def _signed_hz():
    """v2.55: знаковая частота генератора. Знак — от опции «отрицательная
    частота» (выбор LLM облака/Δf), иначе по Δf: частота ниже сглаженной
    больше чем на 3 Гц -> «−». Раньше в облако уходил только модуль."""
    hz = int(SH.gen_hz or 0)
    if hz <= 0:
        return 0
    try:
        last = _D6NEG.get("last")
        if (_D6NEG.get("on") or _D6NEG.get("on4")) and last is not None \
                and last != 0:
            return -hz if last < 0 else hz
        ema = float(_TN.get("hz_ema") or 0)
    except Exception:
        return hz
    return -hz if (ema > 0 and ema - hz > 3.0) else hz


def add_message(role, text, zone="", user=""):
    with SH.lock:
        SH.seq += 1
        item = {"seq": SH.seq, "role": role, "zone": zone,
                "text": text, "ts": time.time(),
                "hz": int(SH.gen_hz or 0),      # v2.44: сигнал в момент фразы
                "shz": _signed_hz()}            # v2.55: со знаком
        if user:
            item["user"] = user
        SH.messages.append(item)
        SH.mem_dirty = True                 # история чата изменилась
    return item


def brain_remember(role, text):
    with SH.lock:
        SH.memory.append({"role": role, "text": str(text)[:400],
                          "hz": int(SH.gen_hz or 0),   # v2.44
                          "shz": _signed_hz()})        # v2.55
        if len(SH.memory) > 60:
            del SH.memory[:len(SH.memory) - 60]
        SH.mem_dirty = True                 # файл запишет mem_sync_tick
    # v2.25: диалог — материал для нейросети (обучение фоном, через очередь)
    try:
        mn_feed(str(text)[:2000], "", "диалог")
    except Exception:
        pass


def _set_llm_state(via, ok, err=""):
    with SH.lock:
        SH.llm_state = {"via": via, "ok": bool(ok), "ts": time.time(),
                        "err": str(err)[:160]}


# ===========================================================================
# [2b] СТАТУС ОБЛАКА: автоматическая проверка доступности (чип «облако»).
#      Любой HTTP-ответ (даже 404/401) = облако ЖИВО; таймаут/отказ = НЕТ.
# ===========================================================================
_CLOUD_PERIOD = 20.0          # сек между фоновыми проверками облака
_cloud_force = [0.0]          # ts внеочередной проверки (после сохранения настроек)


def _cloud_ping():
    """Проверить внешнее облако GET /api/health (v2.24: кирпич убран, чип
    «облако» снова показывает здоровье сервера cloud_url).
    Меряем задержку; результат кладётся в SH.cloud_state для чипа UI."""
    import urllib.error
    import urllib.request
    url, token = _cloud_base()
    if not url:
        with SH.lock:
            SH.cloud_state = {"configured": False, "ok": False,
                              "checking": False, "ts": time.time(),
                              "err": "", "ms": 0}
        return
    headers = {"User-Agent": "neuro12-app/%s" % APP_VERSION,
               "X-Mozg-Token": token}
    req = urllib.request.Request(url + "/api/health", headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            r.read(256)
        ok, err = True, ""
    except urllib.error.HTTPError as e:
        ok, err = True, ""            # сервер ответил кодом — значит, ЖИВ
        try:
            e.read(256)
        except Exception:
            pass
    except Exception as e:
        ok, err = False, str(e)[:140]
    ms = int((time.time() - t0) * 1000)
    with SH.lock:
        prev_ok = bool(SH.cloud_state.get("ok"))
        SH.cloud_state = {"configured": True, "ok": ok, "checking": False,
                          "ts": time.time(), "err": err, "ms": ms if ok else 0}
    log("ОБЛАКО", "живое (%d мс)" % ms if ok else "недоступно: " + err)
    if ok and not prev_ok:
        # v2.27: облако ожило — сверить словари (свои туда, облачные сюда)
        try:
            _dict_cloud_sync_async(force=True)
        except Exception:
            pass


_PING_BUSY = [False]


def cloud_tick():
    """Фоновый watchdog облака (зовётся из brain_tick раз в полсекунды;
    сам запускет проверку не чаще _CLOUD_PERIOD и внеочередно по запросу).
    v2.18: если предыдущая проверка НЕ удалась — следующая уже через 10 с,
    чтобы чип быстрее показал восстановление связи."""
    now = time.time()
    with SH.lock:
        last = SH.cloud_state.get("ts", 0.0)
        checking = SH.cloud_state.get("checking", False)
        was_ok = bool(SH.cloud_state.get("ok"))
    if checking or _PING_BUSY[0]:
        return
    forced = _cloud_force[0] > 0 and _cloud_force[0] > last
    period = _CLOUD_PERIOD if was_ok else 10.0
    if not (forced or (now - last) >= period):
        return
    _cloud_force[0] = 0.0
    # v2.91.1: одна проверка за раз. Раньше, пока шла проверка (до 6 с при
    # недоступном облаке), каждые полсекунды запускалась ещё одна — десятки
    # потоков и «облако: нет» вперемешку с «да»
    _PING_BUSY[0] = True

    def _ping():
        try:
            _cloud_ping()
        finally:
            _PING_BUSY[0] = False
    threading.Thread(target=_ping, daemon=True, name="cloud-ping").start()
    # v2.27: облако живо давно — периодическая сверка словарей (раз в 10 мин)
    if was_ok and CFG.get("dict_sync"):
        try:
            with SH.lock:
                dts = SH.dict_cloud.get("ts", 0.0)
            if now - dts >= DICT_SYNC_PERIOD:
                _dict_cloud_sync_async()
        except Exception:
            pass


# ===========================================================================
# [2c] ПАМЯТЬ В ОБЛАКЕ (v2.7): бандл {memory, chat, state} живёт в файле
#      neuro12_memory.json (источник правды, переживает перезапуск) И
#      дублируется в облако: POST {cloud_url}/api/memory/save (X-Mozg-Token).
#      На чистой плате (локальная память пуста) память возвращается из
#      облака GET /api/memory/load. У каждой платы свой device -> бандлы
#      в облаке не конфликтуют. Всё фоновое, чат не блокируется.
# ===========================================================================
MEM_SAVE_PERIOD = 30.0        # сек между фоновыми выгрузками в облако
_mem_last_save = [0.0]        # последняя запись ЛОКАЛЬНОГО файла
_mem_last_push = [0.0]        # последняя выгрузка в облако
_mem_pull_done = [False]      # стартовое восстановление уже пытались?


def _tn_snapshot():
    """v2.42: память НАБЛЮДЕНИЙ тензорной сети (примеры tn_observe + веса) —
    в бандл памяти, чтобы она была в облаке и переживала замену платы."""
    try:
        with _TN_LOCK:
            if _TN.get("W1") is None:
                return {}
            d = {"v": 1, "H": _TN["H"], "IN": TN_IN, "zones": _tn_znames(),
                 "samples": list(_TN["samples"])[-TN_SAMPLES_MAX:],
                 "trains": _TN["trains"], "loss": _TN["loss"],
                 "acc": _TN["acc"]}
            if _TN["H"] * TN_IN <= 8000:         # веса — только небольшие
                d["W1"] = [[round(v, 4) for v in r] for r in _TN["W1"]]
                d["b1"] = [round(v, 4) for v in _TN["b1"]]
                d["W2"] = [[round(v, 4) for v in r] for r in _TN["W2"]]
                d["b2"] = [round(v, 4) for v in _TN["b2"]]
            return d
    except Exception:
        return {}


_TN_CONF = {}


def _tn_conf(text):
    """v2.53: «понимание» фразы тензорной сетью платы: 1 - нормированная
    энтропия вероятностей зон (0..1) | None, если сеть не готова. Кэш по тексту."""
    text = str(text or "").strip()
    if not text:
        return None
    if text in _TN_CONF:
        return _TN_CONF[text]
    v = None
    try:
        feats = tn_features(text)
        if feats:
            x, _z, _w = _TN_K["tn_parse"](_tn_token(feats, [0] * TN_HW, 0), TN_D)
            with _TN_LOCK:
                if _tn_ready_locked():
                    _h, p = _TN_K["tn_forward"](_TN["W1"], _TN["b1"],
                                                _TN["W2"], _TN["b2"], x)
                    zz = max(2, len(p))
                    ent = -sum(q * math.log(q) for q in p if q > 1e-9)
                    v = round(1.0 - max(0.0, min(1.0, ent / math.log(zz))), 3)
    except Exception:
        v = None
    if len(_TN_CONF) > 600:
        _TN_CONF.clear()
    _TN_CONF[text] = v
    return v


def _tn_mark(items, cap=80):
    """Пометить реплики с частотой оценкой понимания `tn` (копии)."""
    out, n = [], 0
    for m in items:
        if isinstance(m, dict) and n < cap and int(m.get("hz") or 0) > 0 \
                and m.get("role") != "system":
            c = _tn_conf(m.get("text"))
            if c is not None:
                m = dict(m)
                m["tn"] = c
                n += 1
        out.append(m)
    return out


def memory_bundle():
    """Снимок памяти для файла и облака (без блокировки на время сети)."""
    tn = _tn_snapshot()
    with SH.lock:                       # снимок; оценка сетью — вне замка SH
        _mem0 = list(SH.memory)[-60:]
        _chat0 = list(SH.messages)[-300:]
    _mem1, _chat1 = _tn_mark(_mem0), _tn_mark(_chat0)
    with SH.lock:
        dt = dict(SH.dictation)
        dt.pop("queue", None)
        return {
            "app": "neuro_12",
            "tensor": tn,
            "gen": {"hz": int(SH.gen_hz or 0), "shz": _signed_hz(),
                    "ema": round(float(_TN.get("hz_ema") or 0), 1),
                    "ring": list(_GEN_RING)[-60:]},
            "device": _hostname(),
            "memory": _mem1,
            "chat": _chat1,
            "state": {"teach_count": SH.teach_count,
                      "letters_made": SH.letters_made,
                      "words_made": SH.words_made,
                      "last_say": SH.last_say,
                      "teacher_note": SH.teacher_note,
                      "cloud_code": SH.cloud_code,
                      "learn": dict(SH.learn),
                      "dict_extra": sorted(SH.dict_extra)[-500:],
                      "dictation": dt},
        }


def _mem_set_state(**kw):
    with SH.lock:
        st = dict(SH.mem_state)
        st.update(kw)
        st["ts"] = time.time()
        SH.mem_state = st


def _mem_save_local():
    """Записать бандл в локальный файл (зовётся из mem_sync_tick)."""
    p = save_memory(memory_bundle())
    with SH.lock:
        SH.mem_dirty = False
    _mem_last_save[0] = time.time()
    _mem_set_state(ok=True, err=("" if p else "файл памяти не записался"))
    return bool(p)


def _memory_cloud_push():
    """Выгрузить бандл в облако: POST /api/memory/save.
    True — облако приняло."""
    url, token = _cloud_base()
    if not url:
        _mem_set_state(mode="local", ok=False,
                       err="облако не задано — память только на плате")
        return False
    b = memory_bundle()
    r, err = _http_json_err(url + "/api/memory/save", b,
                            token=token, timeout=20)
    if r and r.get("ok"):
        with SH.lock:
            pushed = SH.mem_state.get("pushed", 0) + 1
        _mem_set_state(mode="cloud", ok=True, err="", pushed=pushed)
        log("ПАМЯТЬ", "в облако: реплик %d, чат %d" %
            (len(b["memory"]), len(b["chat"])))
        return True
    _mem_set_state(mode="local", ok=False,
                   err="облако не приняло память: " + (err or "?"))
    return False


def _memory_cloud_pull():
    """Забрать бандл из облака: GET /api/memory/load.
    Возврат: (бандл|None, ошибка|'')."""
    import urllib.parse
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано"
    q = urllib.parse.urlencode({"app": "neuro_12", "device": _hostname()})
    r, err = _http_get_json_err(url + "/api/memory/load?" + q,
                                token=token, timeout=20)
    if r is None:
        return None, err
    if r.get("ok") and r.get("found"):
        return r, ""
    if r.get("ok"):
        return None, "в облаке памяти для этой платы ещё нет"
    return None, r.get("error") or "облако отказало"


def _memory_cloud_delete():
    """«Забыть всё» в облаке. True — удалено (или там и так было пусто)."""
    url, token = _cloud_base()
    if not url:
        return False
    r, err = _http_json_err(url + "/api/memory/delete",
                            {"app": "neuro_12", "device": _hostname()},
                            token=token, timeout=15)
    return bool(r and r.get("ok"))


# ---------------------------------------------------------------------------
# v2.24: ПОДКАЧКА КЭША И БУФЕРА МОЗГА — ИЗ ОБЛАКА (как в v1.9 платформы:
# «мозг живёт в облаке и постоянно читает буфер»). GET /api/buffer:
# сводка чата/мыслей/KB пишется в memory/cloud_buffer.json и уходит скетчу
# ключом cloud_buf_set (CLD= в total_val_c, нейронам — «BUF;»). Фоново,
# облако не задано — воркер тихо ждёт.
# ---------------------------------------------------------------------------
BUF_PERIOD = 300.0             # сек между подкачками буфера/кэша


def buf_worker():
    """Раз в BUF_PERIOD забрать буфер из облака и отдать скетчу."""
    import urllib.parse
    while True:
        time.sleep(BUF_PERIOD)
        try:
            url, token = _cloud_base()
            if not url:
                continue        # облако не задано — подкачки нет (тихо)
            q = urllib.parse.urlencode({"board": _hostname()})
            r, _err = _http_get_json_err(url + "/api/buffer?" + q,
                                         token=token, timeout=10)
            if not r:
                continue
            parts = []
            for m in (r.get("messages") or [])[-6:]:
                who = "мозг" if m.get("role") == "bot" else "человек"
                parts.append("%s: %s" % (who, str(m.get("text") or "")[:120]))
            for t in (r.get("thoughts") or [])[-2:]:
                parts.append("мысль: %s" % str(t.get("text") or "")[:120])
            summary = (" | ".join(parts) or "буфер облака пуст")[:380]
            try:
                bdir = os.path.join(_base_dirs()[0], "memory")
                os.makedirs(bdir, exist_ok=True)
                with open(os.path.join(bdir, "cloud_buffer.json"), "w",
                          encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "summary": summary},
                              f, ensure_ascii=False)
            except Exception:
                pass
            _bridge_call("cloud_buf_set", summary, timeout=4)
            log("БУФЕР", "из облака:", summary[:80])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# v2.28: КАМЕРА — МОЗГ ВИДИТ СТРИМ (ESP32-CAM -> облако -> платы).
# Скетч камеры (sketch/esp32cam_stream) живёт на ФИКСИРОВАННОМ IP; ключ
# cam_url конфига = этот IP. Воркер раз в CAM_PERIOD: тянет JPEG /snap,
# шлёт кадр в облако (YOLO: /api/vision/frame; YOLO нет — /api/vision/raw),
# ВЫКАЧИВАЕТ БУФЕР детекций из облака (/api/vision/buffer) и складывает
# сводку скетчу ключом val_b_set: VB= в total_val_c + вклад камеры в
# частотную шину D4 (CAM_HZ_SPAN) — нейроны «чувствуют» камеру. Стрим
# в облаке: страница /cam и /api/vision/stream.mjpg.
# ---------------------------------------------------------------------------
CAM_PERIOD = 8.0                # сек между кадрами
_cam_seq = [0]                  # номер кадра с старта мозга
_cam_prev = [b""]               # предыдущий кадр (оценка движения)


def _cam_local_summary(jpeg):
    """Честная локальная оценка кадра без CV: размер + изменение байтов
    относительно предыдущего («движение»). Возвращает (строка, процент)."""
    prev = _cam_prev[0]
    pct = 0
    if prev and len(prev) > 512 and len(jpeg) > 512:
        n = min(len(prev), len(jpeg))
        diff = sum(1 for i in range(0, n, 97) if prev[i] != jpeg[i])
        pct = min(99, diff * 97 * 100 // n)
    _cam_prev[0] = jpeg
    return pct


_CAM_UART = {"info": "", "ts": 0.0}


def _cam_uart_diag():
    """v2.93: разбор счётчиков приёмника UART скетча -> понятная причина."""
    d = {}
    for part in str(_CAM_UART.get("info") or "").split(";"):
        k, _, v = part.partition("=")
        try:
            d[k.strip()] = int(v)
        except Exception:
            pass
    if not _CAM_UART.get("info"):
        ba = time.time() - float(SH.bridge_ts or 0)
        if not _CAM_UART.get("ts"):
            why = ("камера по проводу ещё не опрашивалась — включи камеру (cam_on) "
                   "и адрес «uart» в ⚙ платы")
        elif not SH.bridge_seen or ba > 15:
            why = ("скетч не отвечает ВООБЩЕ (мост молчит %s): скетч не собрался или не "
                   "запущен — смотри вывод сборки в App Lab / журнал лаунчера; лаунчер: "
                   "⚙ → «Починить сборку»" % ("с запуска" if not SH.bridge_seen else "%d с" % ba))
        else:
            dg = _SKETCH.get("diag") or {}
            sketch_diag_async()
            if dg.get("stuck"):
                why = ("скетч ЗАВИС: loop() стоит на этапе «%s» (проходов %s) — перезапусти "
                       "приложение; если повторится — пришли этот текст" % (dg.get("loop_name"), dg.get("count")))
            else:
                why = ("мост со скетчем жив, но cam_frame_info не ответил за 8 с%s — обнови "
                       "плату до 2.94.1 (кадры камеры отдаются из потока моста)" % (
                           ("; loop сейчас на этапе «%s»" % dg.get("loop_name")) if dg else ""))
    elif not d.get("bytes") and "bytes" in d:
        why = ("по проводу не пришло НИ ОДНОГО байта: проверь TX ESP32-CAM (GPIO1/U0TXD "
               "или пин из esp32cam_uart) → D0/RX платы, общий GND и питание камеры 5 В")
    elif d.get("ok", 0) == 0 and (d.get("bad", 0) or d.get("lost", 0)):
        why = ("байты идут (%d), но все кадры битые (bad=%d, lost=%d): скорость камеры "
               "не %s бод или помехи на проводе" % (d.get("bytes", 0), d.get("bad", 0),
                                                       d.get("lost", 0), d.get("baud", "?")))
    elif d.get("len", 0) == 0:
        why = "кадров ещё не было"
    else:
        why = ""
    d["why"] = why
    return d


def _cam_uart_frame():
    """v2.30: кадр из ПРИЁМНИКА UART в скетче хаба (cam_url = "uart").
    Протокол приёмника: RPC cam_frame_info -> "len=N;seq=S;ok=..;bad=..;
    age_ms=..;baud=..", RPC cam_chunk("seq:off") -> base64-кусок ("" если
    кадр уже сменился). Кадр собирается кусками; если во время сборки
    пришёл новый кадр (seq сменился) — честно возвращаем b"": догоним в
    следующий период. Возвращает JPEG-байты или b""."""
    info = _bridge_call("cam_frame_info", timeout=8)
    if not info:
        _CAM_UART.update(info="", ts=time.time())
        return b""
    _CAM_UART.update(info=str(info)[:200], ts=time.time())   # v2.93: счётчики UART -> облако
    ln = sq = -1
    try:
        for part in str(info).split(";"):
            k, _, v = part.partition("=")
            k = k.strip()
            if k == "len":
                ln = int(v or 0)
            elif k == "seq":
                sq = int(v or 0)
    except Exception:
        return b""
    if ln < 500 or ln > 200000 or sq < 0 or sq > 255:
        return b""                       # кадра нет/мусор — ждём следующий
    out, off = [], 0
    while off < ln:
        chunk = _bridge_call("cam_chunk", "%d:%d" % (sq, off), timeout=6)
        if not chunk:
            return b""                   # кадр сменился/мост молчит — стоп
        try:
            raw = base64.b64decode(chunk)
        except Exception:
            return b""
        if not raw:
            return b""
        out.append(raw)
        off += len(raw)
    jpeg = b"".join(out)
    if len(jpeg) != ln:
        return b""
    return jpeg


# ---------------------------------------------------------------------------
# v2.88: КАМЕРА С НЕЙРОСЕТЬЮ (sketch/esp32cam_tflite) — СЛОВА ВЫБИРАЮТСЯ ВМЕСТЕ.
# Камера сама смотрит нейросетью TFLite (person_detect, int8) и держит свой
# словарь «слово -> сцена». Через скетч хаба (RPC):
#   cam_vis      — что камера видит сейчас + её слова под сцену (JSON);
#   cam_vote     — мозг даёт камере варианты мысли, она голосует (cam_vote_res);
#   cam_say      — итоговая фраза мозга уходит камере: та учит слова со сценой.
# v2.89: камера УЧАСТВУЕТ В ВЫБОРЕ КАЖДОГО СЛОВА мысли (голос за слова-кандидаты),
# своих слов в мысль не подкидывает и за готовые фразы не голосует.
# ---------------------------------------------------------------------------
_CAMAI = {"ok": False, "ts": 0.0, "p": 0.0, "n": 0, "ms": 0, "lex": 0, "words": [],
          "polls": 0, "votes": 0, "vote_ok": 0, "said": 0, "last_vote": None,
          "err": "камера с нейросетью ещё не отвечала"}
_camai_vid = [0]


def _camai_on():
    cam = str(CFG.get("cam_url") or "").strip().lower()
    return bool(CFG.get("cam_ai", True)) and bool(CFG.get("cam_on", True)) and \
        cam in ("uart", "uart:", "serial", "провод")


def _camai_fresh():
    return _CAMAI["ok"] and time.time() - _CAMAI["ts"] < 10.0


def _camstem(w):
    """Основа слова — как у камеры: 6+ букв -> 5, короче -> длина-1 (не меньше 3)."""
    w = str(w or "").lower().replace("ё", "е")
    n = len(w)
    return w[:5] if n >= 6 else w[:max(3, n - 1)]


def camai_worker():
    """Раз в 1.5 с забрать у скетча, что видит камера (RPC cam_vis)."""
    while True:
        time.sleep(1.5)
        if not _camai_on():
            _CAMAI["ok"] = False
            _CAMAI["err"] = "выключена (нужно cam_url = uart и галочка «камера выбирает слова»)"
            continue
        r = _bridge_call("cam_vis", timeout=3)
        _CAMAI["polls"] += 1
        if not r:
            _CAMAI["ok"] = False
            _CAMAI["err"] = "камера не прислала зрение: прошит esp32cam_tflite? TX ESP32-CAM -> D0, GPIO14 <- D1"
            continue
        head, _, js = str(r).partition("|")
        try:
            age = int(head.split("=", 1)[1]) / 1000.0
            d = json.loads(js)
        except Exception:
            _CAMAI["err"] = "зрение камеры не разобрано"
            continue
        if age > 15:
            _CAMAI["ok"] = False
            _CAMAI["err"] = "зрение устарело (%d с) — нейросеть камеры молчит" % int(age)
            continue
        first = not _CAMAI["ok"]
        _CAMAI.update(ok=True, ts=time.time() - age, p=float(d.get("p") or 0),
                      n=int(d.get("n") or 0), ms=int(d.get("ms") or 0),
                      lex=int(d.get("lex") or 0), f=d.get("f") or [], err="",
                      words=[(str(w).lower(), float(sc)) for w, sc in (d.get("w") or [])][:8])
        if first:
            log("КАМЕРА-ИИ", "камера смотрит нейросетью: человек %.2f, слова: %s" % (
                _CAMAI["p"], ", ".join(w for w, _s in _CAMAI["words"])))


def _camai_word_scores(words):
    """v2.89: КАМЕРА УЧАСТВУЕТ В ВЫБОРЕ СЛОВА. Мозг шлёт кандидатов на очередное
    слово мысли, камера оценивает каждого тем, что видит: {слово: 0..1};
    слова нет в словаре камеры — его нет и в ответе (на выбор не влияет).
    Оценки помнятся, пока камера смотрит на ту же сцену (номер взгляда n),
    поэтому повторные слова не ждут камеру."""
    if not _camai_fresh() or not words:
        return {}
    wc = _CAMAI.setdefault("wcache", {"n": -1, "s": {}})
    if wc["n"] != _CAMAI["n"]:                 # новый взгляд — новые оценки
        wc["n"], wc["s"] = _CAMAI["n"], {}
    ask = []
    for w in words:
        w = str(w or "").strip().lower()
        if w and "|" not in w and w not in wc["s"] and w not in ask:
            ask.append(w)
    ask = ask[:31]
    if ask:
        _camai_vid[0] = (_camai_vid[0] + 1) % 100000
        vid = _camai_vid[0]
        _CAMAI["wvotes"] = _CAMAI.get("wvotes", 0) + 1
        got = None
        if _bridge_call("cam_vote", "%d|*|%s" % (vid, "|".join(ask)), timeout=2):
            t_end = time.time() + 0.8           # камера отвечает между кадрами
            while time.time() < t_end and got is None:
                time.sleep(0.08)
                r = _bridge_call("cam_vote_res", timeout=2)
                try:
                    d = json.loads(r) if r else {}
                except Exception:
                    d = {}
                if d.get("t") == "wvote" and int(d.get("id", -1)) == vid:
                    got = [float(x) for x in (d.get("s") or [])]
        if got is not None and len(got) == len(ask):
            _CAMAI["wvote_ok"] = _CAMAI.get("wvote_ok", 0) + 1
            for w, sc in zip(ask, got):
                wc["s"][w] = sc
            known = [(w, sc) for w, sc in zip(ask, got) if sc >= 0]
            if known:
                _CAMAI["last_wvote"] = {"ts": round(time.time(), 1), "v": [
                    [w, round(sc, 2)] for w, sc in sorted(known, key=lambda x: -x[1])[:8]]}
        else:
            for w in ask:                        # камера не успела — не спрашивать снова
                wc["s"][w] = -1.0                # до следующего взгляда
    return {w: wc["s"][w] for w in (str(x).strip().lower() for x in words)
            if wc["s"].get(w, -1.0) >= 0}


def _camai_say(phrase):
    """Итоговая фраза мозга — камере: она привяжет слова к своей сцене."""
    if not _camai_fresh() or not phrase:
        return
    _bridge_call("cam_say", str(phrase).replace("\n", " ")[:300], wait=False)
    _CAMAI["said"] += 1


def camai_status():
    st = {k: _CAMAI.get(k) for k in ("ok", "p", "n", "ms", "lex", "polls", "said", "err",
                                     "wvotes", "wvote_ok", "last_wvote")}
    st["words"] = [w for w, _s in _CAMAI["words"]]
    st["age"] = int(time.time() - _CAMAI["ts"]) if _CAMAI["ts"] else None
    st["on"] = _camai_on()
    return st


def cam_worker():
    """Кадры камеры -> облако (стрим/YOLO) + буфер детекций -> скетч."""
    import urllib.error
    import urllib.request
    while True:
        time.sleep(_cam_sleep())
        try:
            if not CFG.get("cam_on"):
                _cam_state("выключена", "камера выключена в настройках платы (cam_on)")
                continue
            cam = str(CFG.get("cam_url") or "").strip()
            if not cam:
                _cam_state("не задана", "адрес камеры не задан: ⚙ Настройки платы → «Камера» "
                           "(IP ESP32-CAM, например 192.168.1.50, или uart — по проводу)")
                continue
            if cam.lower() in ("uart", "uart:", "serial", "провод"):
                # v2.30: камера ПО ПРОВОДУ — кадр собирается из приёмника
                # UART скетча (RPC cam_frame_info/cam_chunk)
                jpeg = _cam_uart_frame()
                if len(jpeg) < 500 or not jpeg.startswith(b"\xff\xd8"):
                    _cam_state("нет кадра", "по проводу (UART) кадр не пришёл: " +
                               (_cam_uart_diag().get("why") or
                                "скетч не принял ни одного JPEG (проверь TX камеры → RX платы и скорость)"))
                    continue
                if not _jpeg_complete(jpeg):
                    _cam_state("битый кадр", "по проводу пришёл оборванный JPEG "
                               "(%d КБ без конца кадра) — потеря байтов на UART" % (len(jpeg) // 1024))
                    continue
            else:
                if "://" not in cam:
                    cam = "http://" + cam
                snap = cam if cam.endswith("/snap") else cam.rstrip("/") + "/snap"
                req = urllib.request.Request(
                    snap, headers={"User-Agent": "neuro12-app/%s" % APP_VERSION})
                try:
                    with urllib.request.urlopen(req, timeout=7) as resp:
                        jpeg = resp.read(1500000)
                except Exception as e:
                    _cam_state("не отвечает", "ESP32-CAM не отвечает по адресу %s: %s"
                               % (snap, str(e)[:100]))
                    continue
                if len(jpeg) < 500 or not jpeg.startswith(b"\xff\xd8"):
                    _cam_state("не JPEG", "по адресу %s пришёл не JPEG (%d байт, начало %r) — "
                               "нужен адрес, отдающий снимок /snap" % (snap, len(jpeg), jpeg[:12]))
                    continue
                if not _jpeg_complete(jpeg):
                    _cam_state("битый кадр", "ESP32-CAM отдала оборванный JPEG (%d КБ)"
                               % (len(jpeg) // 1024))
                    continue
            _CAM_ST["ok_ts"], _CAM_ST["kb"] = time.time(), len(jpeg) // 1024
            _cam_state("кадры идут")
            try:
                _cam_to_d6(jpeg)              # v2.48: кадр растром на D6
            except Exception:
                pass
            try:
                _cam_to_d4(jpeg)              # v2.50: чистый кадр на D4
            except Exception:
                pass
            if time.time() - _cam_up[0] < CAM_PERIOD - 0.5:
                continue                      # в облако — не чаще CAM_PERIOD
            _cam_up[0] = time.time()
            _cam_seq[0] += 1
            seq, kb, motion = _cam_seq[0], len(jpeg) // 1024, \
                _cam_local_summary(jpeg)
            url, token = _cloud_base()
            det, src = [], ""
            if url:
                img_b64 = base64.b64encode(jpeg).decode("ascii")
                try:
                    r, _e = _http_json_err(
                        url + "/api/vision/frame",
                        {"img": img_b64, "cam": _hostname(), "conf": 0.35},
                        token=token, timeout=30)
                    if r and r.get("ok"):
                        _CAM_ST["sent_ts"], _CAM_ST["up_err"] = time.time(), ""
                    else:
                        _CAM_ST["up_err"] = "облако не приняло кадр: %s" % (
                            _e or (r or {}).get("error") or "?")[:120]
                    if r and r.get("ok"):
                        det = [(str(x.get("name") or "?"),
                                float(x.get("conf") or 0))
                               for x in (r.get("detections") or [])][:5]
                        src = "yolo"
                except Exception:
                    det = []
                if not det:                   # YOLO нет — сырой кадр в стрим
                    try:
                        _http_json_err(url + "/api/vision/raw",
                                       {"img": img_b64, "cam": _hostname()},
                                       token=token, timeout=30)
                        src = "raw"
                    except Exception:
                        src = ""
                # v2.28: буфер выкачивается ИЗ ОБЛАКА (последние детекции)
                try:
                    b, _e2 = _http_get_json_err(
                        url + "/api/vision/buffer?limit=3&cam="
                        + urllib.parse.quote(_hostname()),
                        token=token, timeout=15)
                    if b and b.get("last") and (b["last"].get("names")
                                                or []) and not det:
                        det = [(str(nm), 0.0)
                               for nm in b["last"]["names"][:5]]
                        src = src or "буфер"
                except Exception:
                    pass
            parts = []
            if det:
                parts.append("вижу: " + ", ".join(
                    ("%s %d%%" % (nm, int(c * 100)) if c else str(nm))
                    for nm, c in det))
            parts.append("кадр #%d, %d КБ, движение %d%%" % (seq, kb, motion))
            if src:
                parts.append("стрим в облаке (%s)" % src)
            summary = " | ".join(parts)[:120]
            _bridge_call("val_b_set", summary, timeout=4)
            try:
                bdir = os.path.join(_base_dirs()[0], "memory")
                os.makedirs(bdir, exist_ok=True)
                with open(os.path.join(bdir, "cam_buffer.json"), "w",
                          encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "seq": seq, "kb": kb,
                               "motion": motion, "src": src,
                               "det": det[:5]}, f, ensure_ascii=False)
            except Exception:
                pass
            if seq % 40 == 1:
                log("КАМЕРА", summary)
        except urllib.error.URLError as e:
            _cam_state("ошибка сети", str(e)[:120])
        except Exception as e:
            _cam_state("ошибка", "%s: %s" % (type(e).__name__, str(e)[:100]))
            time.sleep(20.0)                  # не заваливаем лог/шину


# ---------------------------------------------------------------------------
# v2.28: ПЛАТФОРМА — МОЗГ РАБОТАЕТ: читает статьи KB облака, лазит в
# интернет (поиск Википедии через /api/web/fetch {"q":...}), ПИШЕТ свои
# статьи в облако (/api/kb/import — генератор облака УЧИТСЯ У МОЗГА) и
# СТАВИТ ГЕНЕРАТОРУ ЗАДАНИЯ (/api/gen/task) — мысли мозга направляют облако.
# ---------------------------------------------------------------------------
PLAT_READ_PERIOD = 1500.0       # сек между чтениями статей KB
PLAT_WEB_PERIOD = 2400.0        # сек между выходами в интернет
PLAT_WRITE_PERIOD = 5400.0      # сек между собственными статьями
_plat_next = {"read": 0.0, "web": 0.0, "write": 0.0}
import urllib.error             # v2.28: чтение статей/камера ходят через HTTP
import urllib.parse
import urllib.request


def _plat_state_path():
    return os.path.join(_base_dirs()[0], "memory", "platform_state.json")


def _plat_state():
    try:
        with open(_plat_state_path(), "r", encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        j = {}
    j.setdefault("read_ids", [])
    j.setdefault("written", 0)
    j.setdefault("reads", 0)
    j.setdefault("webs", 0)
    return j


def _plat_state_save(j):
    try:
        os.makedirs(os.path.dirname(_plat_state_path()), exist_ok=True)
        tmp = _plat_state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(j, f, ensure_ascii=False)
        os.replace(tmp, _plat_state_path())
    except Exception:
        pass


def _gen_task_async(text, src="мозг"):
    """Задание генератору облака от мысли мозга (фоном, облако не ждём):
    /api/chat/gen возьмёт его темой/контекстом — мысли мозга направляют
    генератор облака."""
    if not str(text or "").strip():
        return

    def _job():
        try:
            url, token = _cloud_base()
            if not url:
                return
            _http_json_err(url + "/api/gen/task",
                           {"board": _hostname(), "text": str(text)[:400],
                            "src": str(src)[:24]},
                           token=token, timeout=12)
        except Exception:
            pass
    threading.Thread(target=_job, daemon=True).start()


def _plat_think(text):
    """Мысль в ленту облака (/api/think) — мозг делится тем, что узнал."""
    def _job():
        try:
            url, token = _cloud_base()
            if not url:
                return
            _http_json_err(url + "/api/think",
                           {"board": _hostname(),
                            "text": str(text)[:290], "kind": "thought"},
                           token=token, timeout=12)
        except Exception:
            pass
    threading.Thread(target=_job, daemon=True).start()


def _plat_last_user_topic():
    """Тема для «интернета»: последний запрос пользователя (или общая)."""
    try:
        with SH.lock:
            for m in reversed(SH.memory):
                if m.get("role") == "user" and m.get("text"):
                    return str(m["text"])[:60]
    except Exception:
        pass
    return "нейросеть мозг-12"


def _platform_read_job(st):
    """ЧТЕНИЕ: одна статья из KB облака -> нейросеть материалов мозга +
    мысль + задание генератору."""
    url, token = _cloud_base()
    if not url:
        return
    lst, _e = _http_get_json_err(url + "/api/kb/list?limit=80",
                                 token=token, timeout=15)
    if not lst or not lst.get("ok"):
        return
    read_ids = set(st.get("read_ids") or [])
    art = next((a for a in (lst.get("articles") or [])
                if str(a.get("id")) not in read_ids), None)
    if art is None:
        return
    full, _e2 = _http_get_json_err(
        url + "/api/kb/article/" + urllib.parse.quote(str(art.get("id", ""))),
        token=token, timeout=20)
    text = str((full or {}).get("text") or "")
    title = str((full or {}).get("title") or art.get("title") or "?")
    if len(text) < 80:
        return
    try:
        mn_feed(text[:20000], title=title, src="KB-чтение")
    except Exception:
        pass
    st.setdefault("read_ids", []).append(str(art.get("id")))
    del st["read_ids"][:-400]
    st["reads"] = int(st.get("reads") or 0) + 1
    _plat_state_save(st)
    first = re.sub(r"\s+", " ", text[:170]).strip()
    _plat_think("Прочитал статью «%s»: %s…" % (title[:60], first))
    _gen_task_async("перескажи своими словами статью «%s»: %s"
                    % (title[:60], first[:120]), "чтение")
    log("ПЛАТФОРМА", "прочитал статью:", title[:60],
        "(%d зн.)" % len(text))


def _platform_web_job(st):
    """ИНТЕРНЕТ: поиск Википедии через облако -> материал + мысль.
    v2.31: половина запросов — по СОБСТВЕННЫМ мыслям мозга (любопытство),
    а не только по последнему вопросу пользователя."""
    url, token = _cloud_base()
    if not url:
        return
    q = _plat_last_user_topic()
    own = _plat_last_own_topic()
    if own and random.random() < 0.5:
        q = own
    r, _e = _http_json_err(url + "/api/web/fetch", {"q": q},
                           token=token, timeout=25)
    res = ((r or {}).get("results") or [])
    if not res:
        return
    hit = res[0]
    title = str(hit.get("title") or "?")[:70]
    snippet = str(hit.get("snippet") or "")[:400]
    try:
        mn_feed("Википедия: %s. %s" % (title, snippet), title=title,
                src="интернет")
    except Exception:
        pass
    st["webs"] = int(st.get("webs") or 0) + 1
    _plat_state_save(st)
    _plat_think("Полез в интернет по теме «%s»: нашёл «%s» — %s…"
                % (q[:40], title, snippet[:120]))
    _gen_task_async("напиши длинный текст на тему «%s» (нашла Википедия: "
                    "%s)" % (q[:50], title[:60]), "интернет")
    log("ПЛАТФОРМА", "интернет:", q[:40], "->", title[:50])


def _platform_write_job(st):
    """ПИСЬМО: собственная статья мозга -> KB облака (генератор учится)."""
    url, token = _cloud_base()
    if not url:
        return
    with SH.lock:
        bot_lines = [str(m.get("text") or "")[:220]
                     for m in SH.memory[-40:]
                     if m.get("role") == "bot" and len(str(m.get("text"))) > 40]
        n_words = len(SH.dict_set)
    if not bot_lines:
        return
    title = "Заметки мозга-12 (%s): опыт наблюдений" % _hostname()
    intro = ("Заметки написаны нейросетью платы «%s» (мозг-12 v%s): "
             "слов в словаре %d, статей прочитано %d, выходов в интернет %d, "
             "собственных записей %d."
             % (_hostname(), APP_VERSION, n_words, st.get("reads", 0),
                st.get("webs", 0), int(st.get("written") or 0) + 1))
    body = "\n\n".join("— " + ln for ln in bot_lines[-6:])
    concl = ("Вопрос к читателю: что из описанного совпадает с вашим опытом "
             "наблюдения самодействующих систем? Генератор облака, читающий "
             "эту статью, учится на тех же материалах, что и автор.")
    text = (intro + "\n\n" + body + "\n\n" + concl)[:18000]
    r, _e = _http_json_err(url + "/api/kb/import",
                           {"title": title, "text": text,
                            "source": "мозг-12 " + _hostname()},
                           token=token, timeout=25)
    if r and (r.get("ok") or r.get("id")):
        st["written"] = int(st.get("written") or 0) + 1
        _plat_state_save(st)
        _plat_think("Написал статью «%s» — теперь и генератор облака "
                    "учится на ней" % title[:50])
        _gen_task_async("дополни и перепиши статью «%s» новыми мыслями "
                        "от лица мозга" % title[:50], "письмо")
        log("ПЛАТФОРМА", "статья отправлена в KB облака:", title[:50])
    else:
        log("ПЛАТФОРМА", "KB не принял статью:", str(_e or r)[:80])


def platform_worker():
    """Цикл платформы: читать статьи, интернет, писать — по расписанию."""
    time.sleep(90.0)                    # дать мозгу стартовать
    while True:
        try:
            if CFG.get("platform_on"):
                url, _t = _cloud_base()
                if url:
                    now = time.time()
                    st = _plat_state()
                    if now >= _plat_next["read"]:
                        _plat_next["read"] = now + PLAT_READ_PERIOD + \
                            random.uniform(0, 240)
                        _platform_read_job(st)
                    if now >= _plat_next["web"]:
                        _plat_next["web"] = now + PLAT_WEB_PERIOD + \
                            random.uniform(0, 300)
                        _platform_web_job(st)
                    if now >= _plat_next["write"]:
                        _plat_next["write"] = now + PLAT_WRITE_PERIOD + \
                            random.uniform(0, 600)
                        _platform_write_job(st)
                    # v2.31: база рецептов (примеры кода) — из облака
                    if now >= _plat_next.get("recipes", 0):
                        _plat_next["recipes"] = now + 3600.0 + \
                            random.uniform(0, 600)
                        _recipes_sync()
                    # v2.32: база формул — тоже из облака (тот же такт)
                    if now >= _plat_next.get("formulas", 0):
                        _plat_next["formulas"] = now + 3600.0 + \
                            random.uniform(0, 600)
                        _maths_sync()
        except Exception:
            traceback.print_exc()
        time.sleep(60.0)


# ---------------------------------------------------------------------------
# v2.28: НАЗНАЧЕНИЕ НОМЕРОВ НЕЙРОНАМ («слиплись» — несколько плат с одним
# ID сидят на одном адресе). Прошивка neuron_node v2.6 хранит номер в
# EEPROM: плата БЕЗ номера слушает общий вызов и берёт свободный слот,
# который хаб предлагает командой "SETID;<n>" (RPC neuro_assign).
# ---------------------------------------------------------------------------
def _neuro_assign_run():
    """Вызвать RPC neuro_assign у скетча; вернуть отчёт строкой.
    v2.33: скетч назначает НЕБЛОКИРУЮЩЕ (обработчик мгновенно отвечает
    "started"/"busy:...", шаги — neuroAssignTick() в loop()). Раньше этот
    вызов с timeout=150 держал ЕДИНСТВЕННУЮ очередь RPC до 2,5 минут, и
    ВСЁ, что вставало позади (chat_stim, say_wave, word_push,
    cloud_buf_set), отваливалось по таймауту — «экскурсия говорит, а
    плата молчит». Теперь: запуск + поллинг отчёта
    (neuro_assign_poll, раз в 1,2 с, максимум 30 с)."""
    try:
        r = _bridge_call("neuro_assign", timeout=6)
        rep = str(r or "")
        if rep.startswith(("started", "busy")):
            deadline = time.time() + 30.0
            last = ""
            while time.time() < deadline:
                time.sleep(1.2)
                p = str(_bridge_call("neuro_assign_poll", timeout=4) or "")
                if p.startswith("p:"):
                    parts = p.split(";", 2)
                    last = parts[2] if len(parts) > 2 else ""
                    done = len(parts) > 1 and parts[1] == "done"
                    if done:
                        break
            rep = last or rep
        log("НЕЙРОНЫ", "назначение номеров:", rep[:200] or "нет отчёта")
        return rep
    except Exception as e:
        log("НЕЙРОНЫ", "назначение не удалось:", str(e)[:100])
        return ""


def _neuro_assign_async():
    threading.Thread(target=_neuro_assign_run, daemon=True).start()


def _neuro_reset_run():
    """v2.29: стереть EEPROM-номера всех v2.7-плат (CLR_ID, общий вызов).
    После сброса платы становятся «сиротами»: подключать ПО ОДНОЙ и звать
    «нейроны» (neuro_assign) — номера разойдутся без дублей."""
    try:
        r = _bridge_call("neuro_reset", timeout=60)
        rep = str(r or "")
        log("НЕЙРОНЫ", "сброс номеров:", rep[:200] or "нет отчёта")
        return rep
    except Exception as e:
        log("НЕЙРОНЫ", "сброс не удался:", str(e)[:100])
        return ""


def _memory_apply(bundle):
    """Залить бандл (из облака) в живое состояние. Возврат: (реплик, чат)."""
    mem = [dict(m) for m in (bundle.get("memory") or [])
           if isinstance(m, dict)][-60:]
    chat = [dict(m) for m in (bundle.get("chat") or [])
            if isinstance(m, dict)][-300:]
    st = bundle.get("state") if isinstance(bundle.get("state"), dict) else {}
    with SH.lock:
        if mem and not SH.memory:
            SH.memory = mem
        if chat and not SH.messages:
            for m in chat:
                m.setdefault("seq", 0)
                SH.messages.append(m)
            SH.seq = max([SH.seq] + [int(m.get("seq", 0) or 0)
                                     for m in SH.messages])
        if st.get("teach_count") and not SH.teach_count:
            SH.teach_count = int(st["teach_count"])
        if st.get("letters_made") and not SH.letters_made:
            SH.letters_made = int(st["letters_made"])
        if st.get("words_made") and not SH.words_made:
            SH.words_made = int(st["words_made"])
        if st.get("last_say") and not SH.last_say:
            SH.last_say = str(st["last_say"])[:200]
        SH.mem_dirty = True
        n_mem, n_chat = len(SH.memory), len(SH.messages)
    try:                                   # v2.42: память наблюдений сети
        tn = bundle.get("tensor") if isinstance(bundle.get("tensor"), dict) \
            else {}
        if tn.get("samples"):
            with _TN_LOCK:
                empty = not _TN.get("samples") and not _TN.get("trains")
            if empty:
                with open(_tn_path(), "w", encoding="utf-8") as f:
                    f.write(json.dumps(tn, ensure_ascii=False))
                _tn_load()
                log("ПАМЯТЬ", "наблюдения сети восстановлены из облака: %d"
                    % len(_TN["samples"]))
    except Exception:
        traceback.print_exc()
    with SH.lock:
        pulled = SH.mem_state.get("pulled", 0) + 1
    _mem_set_state(mode="cloud", ok=True, err="", pulled=pulled)
    return n_mem, n_chat


def _memory_startup_restore():
    """Старт платы: чистая память -> забрать из облака; затем в любом случае
    выгрузить актуальный бандл в облако (локальное всегда главнее)."""
    try:
        with SH.lock:
            empty = not SH.memory and not SH.messages
        if empty:
            b, err = _memory_cloud_pull()
            if b:
                n_mem, n_chat = _memory_apply(b)
                add_message("system",
                            "☁ Память восстановлена из облака: реплик "
                            "диалога %d, сообщений чата %d." % (n_mem, n_chat),
                            "Память")
                log("ПАМЯТЬ", "восстановлено из облака: реплик %d, чат %d"
                    % (n_mem, n_chat))
            else:
                log("ПАМЯТЬ", "восстановление:", (err or "?")[:100])
                _mem_set_state(ok=False,
                               err="восстановление: " + (err or "?"))
        _memory_cloud_push()
    except Exception:
        traceback.print_exc()


def mem_sync_tick():
    """Фоновая синхронизация памяти (зовётся из brain_tick раз в полсекунды):
    локальный файл — при каждом изменении (не чаще раза в 10 с), облако —
    раз в MEM_SAVE_PERIOD при изменениях + одна стартовая сверка."""
    now = time.time()
    try:
        kbl_tick()                        # v2.45: локальная база знаний (JSON)
    except Exception:
        pass
    try:
        read_tick()                       # v2.91: читаю то, чего не знала
    except Exception:
        pass
    with SH.lock:
        dirty = SH.mem_dirty
        configured = bool(CFG.get("mem_sync"))
    if dirty and now - _mem_last_save[0] >= 10.0:
        _mem_save_local()
        with SH.lock:
            dirty = SH.mem_dirty          # пока писали, могли снова изменить
    if not configured:
        return
    if not ((CFG.get("cloud_url") or "").strip()
            and (CFG.get("token") or "").strip()):
        return
    if not _mem_pull_done[0]:             # одна стартовая сверка с облаком
        _mem_pull_done[0] = True
        threading.Thread(target=_memory_startup_restore, daemon=True).start()
        return
    if not dirty or now - _mem_last_push[0] < MEM_SAVE_PERIOD:
        return
    _mem_last_push[0] = now
    threading.Thread(target=_memory_cloud_push, daemon=True).start()


# ===========================================================================
# [2d] ОБЛАЧНЫЕ ФУНКЦИИ (v2.8): мозг умеет всё, что умеет платформа мозг-12:
#      поиск (Википедия, SSRF-защита), песочница для кода, генерация функций
#      LLM с проверкой, реестр функций облака + их запуск. Всё по токену.
# ===========================================================================
def _cloud_base():
    url = (CFG.get("cloud_url") or "").strip().rstrip("/")
    token = (CFG.get("token") or "").strip()
    return (url if url and token else ""), token


# ===========================================================================
# [2b] ВНЕШНЕЕ ОБЛАКО (v2.24: кирпич убран — вся инфраструктура снова на
#      сервере cloud_url): мозг-почта, память, буфер, навыки, KB, мысли,
#      файлы + сервисы, которых на плате не бывает: Википедия, LLM-учитель,
#      TTS. Без cloud_url плата живёт локально (чат, зоны, самокод).
# ===========================================================================


# v2.20: МЕХАНИКА СБОРА МЫСЛЕЙ В ТЕКСТ. Фразы мозга НЕ публикуются по одной
# (старый пост-на-фразу терял мысли: throttle 2 с, уехавшие пакеты), а
# КОПЯТСЯ В ОЧЕРЕДИ. Раз в THOUGHT_FLUSH сек сборщик склеивает их в ОДИН
# текст и рассылает по трём каналам облака:
#   1) POST /api/think  {"board", "text", "kind": "say"} — пофразно, лента
#      «Мысли мозга» в Studio (постоянный канал «мозг <-> облако», v1.9);
#   2) POST /api/chat/thought {"board", "text"} — СОБРАННЫЙ ТЕКСТ одним
#      сообщением в чат облака «🧠 Мысли плат» (v2.4.7) — мысли видны прямо
#      в /chat, как обычные сообщения, с историей;
#   3) POST /api/import {"board", "messages": [{role: "bot", ...}]} — реплика
#      мозга в веб-хронике на главной странице облака.
# Всё фоновое и best-effort: связь плоха — фразы всё равно живут в чате
# платы и в памяти, а очередь дождётся следующей отправки.
THOUGHT_FLUSH = 45.0            # сек между сборками мыслей в текст
THOUGHT_MAX_Q = 24              # потолок очереди (старые вытесняются)
_THOUGHT_Q = []                 # фразы, ждущие отправки
_THOUGHT_LOCK = threading.Lock()
_THOUGHT_FAIL_LOGGED = [0.0]
_REG_STATE = {"ts": 0.0, "ok": None, "err": "", "url": ""}


def _local_ip():
    """Локальный IP платы (для адреса в регистрации облака). Без сети — ''."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))          # маршрутизуемый интерфейс
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


def cloud_register():
    """v2.20: плата регистрируется в облаке С АДРЕСОМ — POST
    /api/brains/register {id, name, kind: "board", url, note}. Раньше платы
    появлялись в Studio «сами, без адреса» (только heartbeat от опроса
    inbox); теперь у записи есть имя и url=http://<ip>:7000 — панель
    «Устройства» показывает адрес, «проверить связь» достукивается до
    /api/health, роутер видит полноценное устройство. Повторяем каждые
    CLOUD_REG_PERIOD — адрес мог смениться (DHCP)."""
    url, token = _cloud_base()          # регистрация в облаке (с адресом)
    if not url:
        return
    ip = _local_ip()
    reg_url = ("http://%s:%d" % (ip, WEB_UI_PORT)) if ip else ""
    r, err = _http_json_err(
        url + "/api/brains/register",
        {"id": _hostname(), "name": "Плата UNO Q (%s)" % _hostname(),
         "kind": "board", "url": reg_url,
         "note": "мозг-12 · авто-регистрация платы"},
        token=token, timeout=8)
    ok = bool(r and r.get("ok"))
    _REG_STATE.update({"ts": time.time(), "ok": ok, "url": reg_url,
                       "err": "" if ok else (err or (r or {}).get("error")
                                            or "облако не приняло")[:120]})
    if ok:
        log("ОБЛАКО", "плата зарегистрирована с адресом:",
            reg_url or "(адрес неизвестен)")
    else:
        log("ОБЛАКО", "регистрация не прошла:", _REG_STATE["err"])


CLOUD_REG_PERIOD = 600.0       # сек между перерегистрациями в облаке
try:                           # web_ui кирпич слушает 7000 (см. app.yaml)
    WEB_UI_PORT = int(os.environ.get("NEURO_WEB_PORT", "7000"))
except ValueError:
    WEB_UI_PORT = 7000


# v2.63: «англ н англ н н» — шум прежней LLM облака: пометки языка из
# Википедии («англ.», «лат.») и обрывки сокращений («н. э.», «т. е.»).
# Плата слышала такие мысли облака, учила слова и повторяла их — облако
# училось на мыслях платы. Теперь шум не входит ни в словарь, ни в мысли.
_JUNK_WORDS = frozenset("англ лат фр франц греч итал исп яп укр польск чеш "
                        "норв нидерл ивр санскр др см рис табл стр гл напр "
                        "тыс млн млрд".split())   # «нем», «порт», «кит» — слова
_JUNK_RE = re.compile(r"(?<![а-яёa-z0-9])(?:" + "|".join(sorted(
    _JUNK_WORDS, key=len, reverse=True)) + r"|[бгдежзйлмнпртфхцчшщъыьэю]"
    r"|[а-яё]{1,2}\d+)(?![а-яёa-z0-9])\.?", re.I)


def scrub_thought(text):
    """Мысль без пометок языка и обрывков; шум целиком -> ''."""
    t = re.sub(r"\(\s*(?:англ|лат|греч|нем|фр|франц)\.[^()]{0,160}\)", " ",
               str(text or ""), flags=re.I)
    t = re.sub(r"\bдо\s+н\.\s*э\.", "до нашей эры", t, flags=re.I)
    t = re.sub(r"\bн\.\s*э\.", "нашей эры", t, flags=re.I)
    t = re.sub(r"\bт\.\s*е\.", "то есть", t, flags=re.I)
    t = _JUNK_RE.sub(" ", t)
    t = re.sub(r"\s+([.,;:!?»)])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;:-—")
    ws = re.findall(r"[а-яёa-z0-9]+", t.lower())
    if len(ws) < 2:
        return ""
    if len(ws) >= 4 and len(set(ws)) < 0.5 * len(ws):
        return ""                               # зацикленный повтор
    return t


def _thought_push(phrase):
    """Поставить фразу мозга в очередь на отправку в облако (мгновенно,
    без сетевых вызовов — сеть трогает только сборщик)."""
    ph = scrub_thought(phrase)                  # v2.63
    if not ph:
        return
    with _THOUGHT_LOCK:
        _LAST_THOUGHT.update(t=ph[:220], ts=time.time())     # v2.90
        _THOUGHT_Q.append(ph[:220])
        if len(_THOUGHT_Q) > THOUGHT_MAX_Q:
            del _THOUGHT_Q[:-THOUGHT_MAX_Q]
        try:          # v2.60: связь мозгов — мысль вместе с частотой генератора
            _LINK_Q.append({"t": ph[:220], "shz": _signed_hz()})
            if len(_LINK_Q) > 12:
                del _LINK_Q[:-12]
        except Exception:
            pass
    # v2.25: мысль мозга — тоже материал для нейросети (самообучение)
    try:
        mn_feed(ph, "", "мысль")
    except Exception:
        pass


# ===========================================================================
# v2.60: СВЯЗЬ МОЗГОВ — частота генератора и мысли, в реальном времени.
#   Раз в LINK_PERIOD сек: POST {cloud}/api/link {board, hz, shz, ema,
#   phrases:[{t, shz}]} — частота генератора скетча (D4, со знаком) и новые
#   мысли мозга, каждая — с частотой, на которой родилась. В ответ облако
#   (v3.102+) отдаёт свои мысли (ответ мозга облака и своей LLM на мысль
#   платы, мысли зон и ответы чата): _cloud_d6 зажигает ими нейроны, кладёт в
#   сеть материалов, играет на D6 и подаёт на текст-канал скетча — от этого
#   меняется частота генератора. Старое облако (404) — канал засыпает на 10 мин.
# ===========================================================================
LINK_PERIOD = 5.0               # v2.90: строгая связь — чаще (было 10 с)
_LAST_THOUGHT = {"t": "", "ts": 0.0}   # v2.90: последняя мысль мозга
_START_TS = time.time()          # v2.61: сколько работает приложение
_CRASH_NAME = "neuro12_crash.txt"
_LINK_Q = []
_LINK = {"ok": 0, "fail": 0, "got": 0, "sent": 0, "err": "", "ts": 0.0,
         "sleep_until": 0.0}
# v2.62: ПУЛЬС ПОТОКОВ для сторожа (deploy_cli/neuro12_watchdog.sh). Сторож
# раз в 15 с спрашивает /api/alive; если порт 7000 не отвечает ИЛИ поток
# связи/основной цикл завис — перезапускает приложение (app stop + start).
_BEAT = {"link": time.time(), "tick": time.time()}
BEAT_STUCK_S = 600.0             # столько без пульса = завис
BRIDGE_STUCK_S = 900.0           # v2.64: скетч молчит столько — перезапуск
BRIDGE_NEVER_S = 300.0           # v2.67: скетч не ответил ни разу за столько
_WD_FILE = "neuro12_watchdog.json"
_WD = {"info": None}


def _wd_load():
    """v2.62: что записал сторож (сколько раз и почему перезапускал).
    Новый перезапуск — в файл причины падения: облако получит его по
    каналу связи и запишет в журнал (облако обновлять не нужно)."""
    info = None
    for d in _base_dirs():
        fn = os.path.join(d, _WD_FILE)
        try:
            if os.path.isfile(fn):
                with open(fn, "r", encoding="utf-8") as f:
                    info = json.load(f)
                break
        except Exception:
            pass
    _WD["info"] = info
    if not isinstance(info, dict) or not info.get("restarts"):
        return
    try:
        seen_fn = os.path.join(os.path.dirname(_mem_path()),
                               "neuro12_watchdog.seen")
        seen = ""
        if os.path.isfile(seen_fn):
            with open(seen_fn, "r", encoding="utf-8") as f:
                seen = f.read().strip()
        mark = "%s|%s" % (info.get("restarts"), info.get("last_ts"))
        if mark != seen:
            _crash_write("перезапущено сторожем (всего %s): %s" % (
                info.get("restarts"), info.get("last_reason") or "?"))
            with open(seen_fn, "w", encoding="utf-8") as f:
                f.write(mark)
    except Exception:
        pass


def api_alive():
    """v2.62: лёгкая проверка живости для сторожа — без блокировок.
    stuck — непустая причина, если поток связи или основной цикл не
    подавали пульс дольше BEAT_STUCK_S."""
    now = time.time()
    la, ta = now - _BEAT["link"], now - _BEAT["tick"]
    stuck = ""
    ba = now - float(SH.bridge_ts or 0)
    if not SH.bridge_seen and now - _START_TS > BRIDGE_NEVER_S:
        # v2.67: скетч ни разу не ответил (чип «мост: ждёт скетч») —
        # перезапуск приложения заново прошивает скетч
        stuck = "скетч ни разу не ответил за %d с (мост ждёт скетч)" % (
            now - _START_TS)
    elif SH.bridge_seen and ba > BRIDGE_STUCK_S:
        # v2.64: скетч отвечал, а потом замолчал — частоты нет («0 Гц»);
        # перезапуск приложения заново прошивает и поднимает скетч
        stuck = "мост к скетчу молчит %d с (частота генератора не приходит)" % ba
    elif la > BEAT_STUCK_S:
        stuck = "поток связи с облаком завис (%d с без пульса)" % la
    elif ta > BEAT_STUCK_S:
        stuck = "основной цикл завис (%d с без пульса)" % ta
    return {"ok": True, "ver": APP_VERSION, "up": int(now - _START_TS),
            "link_age": int(la), "tick_age": int(ta), "stuck": stuck,
            "bridge_age": int(ba), "gen_hz": int(SH.gen_hz or 0),
            "rss_mb": _rss_mb(),
            # v2.71: сторож после перезапуска ждёт, что скетч ДЕЙСТВИТЕЛЬНО
            # ожил (а не только порт 7000): ответил ли скетч хоть раз и шаг его запуска
            "sketch_seen": bool(SH.bridge_seen),
            "sketch_alive": bool(SH.bridge_seen) and ba < 15.0,
            "sketch_boot": int(_SKETCH.get("boot") or 0)}



def _start_log_tail(n=6):
    """v2.78: последние строки вывода сборки/заливки скетча (neuro12_start.log,
    пишет сторож) — чтобы в панели было видно, ПОЧЕМУ скетч не прошился."""
    try:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(app_dir, "neuro12_start.log")
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
        keep = [l for l in lines if re.search(r"error|ошибк|failed|fatal|undefined|not declared", l, re.I)]
        return " | ".join((keep or lines)[-n:])[:900]
    except Exception:
        return ""

def _rss_mb():
    """Память процесса (МБ) — облако видит, если она растёт до падения."""
    try:
        with open("/proc/self/status", "r") as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    return int(int(ln.split()[1]) / 1024)
    except Exception:
        pass
    return None


def _crash_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), _CRASH_NAME)
    except Exception:
        return _CRASH_NAME


def _crash_write(text):
    """v2.61: причина падения — в файл рядом с памятью; облако получит её
    при следующем запуске (поле crash канала связи)."""
    try:
        with open(_crash_path(), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                  str(text)[-3000:]))
    except Exception:
        pass


def _crash_take():
    """Прошлые падения (один раз) -> текст | ''."""
    out = ""
    for p_ in (_crash_path(), _crash_path() + ".fault"):
        try:
            if os.path.isfile(p_) and os.path.getsize(p_) > 0:
                with open(p_, "r", encoding="utf-8", errors="replace") as f:
                    out += f.read()[-1500:]
                os.replace(p_, p_ + ".sent")
        except Exception:
            pass
    return out.strip()


def _crash_hooks():
    """Ловушки: необработанные исключения главного потока и потоков, а
    также аварии интерпретатора (faulthandler) — в файл падений."""
    import sys
    import traceback as _tb

    def _hook(et, ev, tb):
        _crash_write("".join(_tb.format_exception(et, ev, tb)))
        try:
            sys.__excepthook__(et, ev, tb)
        except Exception:
            pass
    sys.excepthook = _hook

    def _thook(a):
        if a.exc_type is SystemExit:
            return
        _crash_write("поток %s: %s" % (
            getattr(a.thread, "name", "?"),
            "".join(_tb.format_exception(a.exc_type, a.exc_value,
                                         a.exc_traceback))))
    try:
        threading.excepthook = _thook
    except Exception:
        pass
    try:
        import faulthandler
        _FAULT_FH[0] = open(_crash_path() + ".fault", "a")
        faulthandler.enable(_FAULT_FH[0])
    except Exception:
        pass


_FAULT_FH = [None]
_CRASH_SENT = [False]


def _link_post(payload):
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано"
    return _http_json_err(url + "/api/link", payload, token=token, timeout=10)


def _link_answer(asks):
    """v2.61: вопросы мозгу по каналу связи (облако не достучалось по мосту):
    думаем как /api/think и сразу отвечаем отдельным /api/link."""
    ans = []
    for q in asks[:3]:
        try:
            r = api_think({"text": q.get("text")})
        except Exception as e:
            r = {"ok": False, "error": str(e)[:120]}
        if isinstance(r, dict):
            r = dict(r, id=q.get("id"))
            ans.append(r)
    if ans:
        _link_post({"board": _hostname(), "answers": ans, "asks_ok": True,
                    "hz": int(SH.gen_hz or 0), "shz": _signed_hz()})


def _link_extras(payload):
    """v2.77: нейроны, воля мозга, дыхание тензорной сети, камера — каждое в
    своём try: сломанное поле не должно ронять весь канал связи (иначе облако
    пишет «тензорная сеть платы не на связи»)."""
    def _put(key, fn):
        try:
            payload[key] = fn()
        except Exception as e:
            payload.setdefault("extras_err", {})[key] = "%s: %s" % (type(e).__name__, str(e)[:80])
    _put("net", lambda: SH.net if len(SH.net or "") == 12 else "")
    _put("will", brain_will)
    _put("lead", brain_lead)            # v2.90: ведущая зона + мысль мозга
    _put("noise", lambda: {"cap": _PART["cap"], "d6": _PART["d6"],     # v2.92
                           "scale": _PART["scale"], "cfg": _PART["cfg"],
                           "age": int(time.time() - _PART["ts"]) if _PART["ts"] else None})
    _put("breath", lambda: {
        "period": round(_BREATH["period"], 2),
        "tn_period": round(float(_BREATH.get("tn_T") or _BREATH["period"]), 2),
        "depth": round(float(_BREATH.get("tn_d") or 0.6), 3),
        "phase": round(((time.time() - _BREATH["t0"]) / _BREATH["period"]) % 1.0, 4),
        "tn_ready": _BREATH.get("tn_T") is not None,
        "uses": {"Слух (микрофон мозга → Vosk)": (_BREATH["uses"].get("Слух") or [0])[0],
                 "Зрение (камера → разбор кадра)": (_BREATH["uses"].get("Зрение") or [0])[0]}})
    _put("cam", lambda: {
        "state": _CAM_ST["state"], "err": _CAM_ST["err"], "up_err": _CAM_ST["up_err"],
        "kb": _CAM_ST["kb"],
        "ok_age": int(time.time() - _CAM_ST["ok_ts"]) if _CAM_ST["ok_ts"] else None,
        "sent_age": int(time.time() - _CAM_ST["sent_ts"]) if _CAM_ST["sent_ts"] else None,
        "url": str(CFG.get("cam_url") or "")[:80], "on": bool(CFG.get("cam_on")),
        "uart": _cam_uart_diag() if str(CFG.get("cam_url") or "").strip().lower()
        in ("uart", "uart:", "serial", "провод") else None,      # v2.93
        "ai": camai_status()})
    _put("mic", mic_status)             # v2.93: уши платы видны в облаке
    _put("codejobs", lambda: dict(_CODEJOBS, code=bool(CFG.get("code_from_cloud", True)),
                                  admin=bool(CFG.get("admin_from_cloud", True))))


def link_once():
    url, token = _cloud_base()
    if not url:
        return None
    with _THOUGHT_LOCK:
        batch = _LINK_Q[:]
        _LINK_Q[:] = []
    try:
        ema = float(_TN.get("hz_ema") or 0)
    except Exception:
        ema = 0.0
    payload = {"board": _hostname(), "hz": int(SH.gen_hz or 0),
               "shz": _signed_hz(), "ema": round(ema, 1), "phrases": batch,
               "up": int(time.time() - _START_TS), "rss_mb": _rss_mb(),
               "ver": APP_VERSION, "asks_ok": True,
               "grav": {"strain": _GRAV.get("strain"), "g": _GRAV.get("g"),
                        "pulled": _GRAV.get("pulled"),
                        "masses": _GRAV.get("masses")[:3]},
               # v2.64: откуда частота — облако видит, ПОЧЕМУ «0 Гц»: скетч
               # не присылает сводку (мост молчит) или генератор стоит
               "bridge": {"alive": (time.time() - SH.bridge_ts) < 10.0,
                          "age": int(max(0.0, time.time() - SH.bridge_ts)),
                          "seen": bool(SH.bridge_seen)}}
    _link_extras(payload)              # v2.77: доп. поля — каждое отдельно
    crash = ""
    if not _CRASH_SENT[0]:
        crash = _crash_take()
        if crash:
            payload["crash"] = crash
    r, err = _http_json_err(url + "/api/link", payload, token=token,
                            timeout=10)
    if r and r.get("ok"):
        _CRASH_SENT[0] = True
    elif crash:
        _crash_write(crash)             # не дошло — пошлём в следующий раз
    if not r or not r.get("ok"):
        _LINK["fail"] += 1
        _LINK["err"] = str(err or (r or {}).get("error") or "?")[:120]
        if "404" in _LINK["err"]:
            _LINK["sleep_until"] = time.time() + 600.0   # облако до v3.102
        else:
            with _THOUGHT_LOCK:       # мысли не теряем — вернём в очередь
                _LINK_Q[:0] = batch
                del _LINK_Q[:-12]
        return None
    _LINK["ok"] += 1
    _LINK["ok_ts"] = time.time()          # v2.91.1: канал связи жив — облако живо
    try:
        with SH.lock:
            if SH.cloud_state.get("configured") is not False and not SH.cloud_state.get("ok"):
                SH.cloud_state.update(ok=True, err="", ts=time.time(), configured=True)
    except Exception:
        pass
    _LINK["sent"] += len(batch)
    _LINK["err"] = ""
    _LINK["ts"] = time.time()
    for m6 in (r.get("msgs") or []):
        _LINK["got"] += 1
        try:
            if isinstance(m6, dict) and m6.get("text"):     # v2.63: без шума
                m6 = dict(m6, text=scrub_thought(m6["text"]))
                if not m6["text"]:
                    continue
            _cloud_d6(m6)
        except Exception:
            pass
    if isinstance(r.get("ctl"), dict):   # v2.90: что мозг разрешил облаку
        _LINK["ctl"] = {"strict": bool(r["ctl"].get("strict")),
                        "run": [str(x)[:40] for x in (r["ctl"].get("run") or [])][:30],
                        "stop": [str(x)[:40] for x in (r["ctl"].get("stop") or [])][:30],
                        "ts": time.time()}
    if r.get("mic_live"):                # v2.93: облако просит живой звук микрофона
        try:
            _MIC["live_until"] = time.time() + min(30.0, float(r.get("mic_live") or 0))
        except Exception:
            pass
    if r.get("code_jobs"):               # v2.93: код/команды из облака
        _LINK["fast_until"] = time.time() + 60.0
        threading.Thread(target=_code_jobs_run, args=(list(r["code_jobs"]),),
                         daemon=True).start()
    if r.get("code_wait"):               # облако ждёт ответа — чаще на связь
        _LINK["fast_until"] = time.time() + 30.0
    if r.get("particles"):               # v2.92: галочки квантов на D6 из облака
        _part_apply(r.get("particles"))
    if isinstance(r.get("game"), dict):  # v2.69: в облаке есть игра «РОК-12»
        _GAME["cloud"] = dict(r["game"], ts=time.time())
    sy = r.get("sync")                  # v2.67: общий ритм платформы
    if isinstance(sy, dict) and isinstance(sy.get("breath"), dict):
        breath_sync(sy["breath"])       # v2.74: общее дыхание
    if isinstance(sy, dict) and sy.get("hz"):
        try:
            hz_, k_ = int(sy.get("hz") or 0), int(sy.get("k") or 0)
            if CFG.get("sync_on", True) and hz_ > 0:
                _bridge_call("sync_hz", "%d,%d" % (hz_, k_), wait=False)
                _LINK["sync"] = {"hz": hz_, "k": k_, "ts": time.time(),
                                 "spread": sy.get("spread")}
        except Exception:
            pass
    if r.get("verdicts"):               # v2.65: облако оценило связность
        try:
            _LINK["verdicts"] = int(_LINK.get("verdicts") or 0) + \
                gen_apply_verdicts(r["verdicts"])
        except Exception:
            pass
    if r.get("asks") or r.get("asks_wait"):
        _LINK["fast_until"] = time.time() + 60.0
    if r.get("asks"):                   # v2.61: вопрос мозгу по каналу связи
        _LINK["asks"] = int(_LINK.get("asks") or 0) + len(r["asks"])
        threading.Thread(target=_link_answer, args=(list(r["asks"]),),
                         daemon=True).start()
    return r


def link_worker():
    log("СВЯЗЬ", "связь мозгов с облаком: частота и мысли раз в %d с"
        % int(LINK_PERIOD))
    first = True
    while True:
        # v2.61: вопрос из облака ждёт ответа — опрашиваем чаще (3 с), пока
        # мост недоступен; иначе раз в LINK_PERIOD
        time.sleep(2.0 if first else (3.0 if _LINK.get("fast_until", 0) >
                                      time.time() else LINK_PERIOD))
        first = False
        _BEAT["link"] = time.time()             # v2.62: пульс для сторожа
        if time.time() < _LINK["sleep_until"]:
            continue
        try:
            link_once()
        except Exception as e:
            _LINK["err"] = str(e)[:120]


def _thought_deliver(batch):
    """Отправить СОБРАННЫЕ мысли в облако (синхронно; вызывает сборщик и
    тесты). Возврат: {"think": bool, "chat": bool, "import": bool}."""
    res = {"think": False, "chat": False, "import": False}
    url, token = _cloud_base()          # мысли — в облако (чат «Мысли плат»)
    if not url or not batch:
        return res
    board = _hostname()
    # 1) лента «Мысли мозга» в Studio — пофразно (не больше 6 за сборку)
    for ph in batch[:6]:
        r, err = _http_json_err(
            url + "/api/think",
            {"board": board, "text": "🧠 «%s»" % ph[:220], "kind": "say"},
            token=token, timeout=8)
        res["think"] = res["think"] or bool(r and r.get("ok"))
    # 2) ЧАТ ОБЛАКА «Мысли плат» — собранный текст ОДНИМ сообщением (v2.4.7)
    text = "Мысли платы %s (собрано за %d мин):\n%s" % (
        board, max(1, int(THOUGHT_FLUSH / 60.0) + 1),
        "\n".join("• %s" % p for p in batch))[:3500]
    r, err = _http_json_err(
        url + "/api/chat/thought", {"board": board, "text": text},
        token=token, timeout=8)
    res["chat"] = bool(r and r.get("ok"))
    # 3) хроника на главной облака — та же сборка кратко
    r, err = _http_json_err(
        url + "/api/import",
        {"board": board,
         "messages": [{"role": "bot", "text": "🧠 «%s»" % batch[-1][:220],
                       "zone": "Мысль", "ts": int(time.time() * 1000)}]},
        token=token, timeout=8)
    res["import"] = bool(r and r.get("ok"))
    if res["chat"] or res["think"] or res["import"]:
        with SH.lock:
            _GEN_STATE["cloud_says"] += len(batch)
            SH.mem_dirty = True
    else:
        now = time.time()
        if now - _THOUGHT_FAIL_LOGGED[0] > 300.0:
            _THOUGHT_FAIL_LOGGED[0] = now
            log("ОБЛАКО", "мысли не опубликованы (чат/лента/хроника):",
                str(err or "?")[:120])
    return res


def _thought_flush():
    """Собрать очередь в текст и отправить. Возврат: итог отправки | None."""
    with _THOUGHT_LOCK:
        batch = _THOUGHT_Q[:]
        _THOUGHT_Q[:] = []
    if not batch:
        return None
    try:
        return _thought_deliver(batch)
    except Exception as e:
        log("ОБЛАКО", "сборка мыслей не ушла:", str(e)[:120])
        return None


def thought_worker():
    """Фон-сборщик: раз в THOUGHT_FLUSH сек склеивает накопленные фразы
    мозга в один текст и пишет его в облако (чат «Мысли плат» + лента
    Studio + хроника). Ничего не объявляет в чатах — это транспорт."""
    log("МЫСЛИ", "сборщик запущен: пакет раз в %d c" % int(THOUGHT_FLUSH))
    while True:
        time.sleep(THOUGHT_FLUSH)
        _thought_flush()


def cloud_search(q, timeout=25):
    """Поиск в облаке: POST /api/web/fetch {"q"} (Википедия).
    Возврат: (результат|None, ошибка|'')."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    r, err = _http_json_err(url + "/api/web/fetch", {"q": str(q)[:120]},
                            token=token, timeout=timeout)
    if r is None:
        return None, err
    if r.get("ok"):
        return r, ""
    return None, r.get("error") or "поиск не удался"


def cloud_run_code(code, text="", timeout=30):
    """Выполнить код в песочнице ОБЛАКА: POST /api/sandbox/run.
    Код — модуль с def run(text, ctx=None) -> str."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    r, err = _http_json_err(url + "/api/sandbox/run",
                            {"code": str(code)[:24000], "text": str(text)[:2000],
                             "board": _hostname()},
                            token=token, timeout=timeout)
    if r is None:
        return None, err
    return r, ("" if r.get("ok") else (r.get("error") or "код не выполнился"))


def cloud_codegen(task, timeout=20):
    """v2.37: попросить ОБЛАКО собрать код по просьбе (режим «Код» облака,
    без LLM): Python/JS/Arduino/HTML/Bash/SQL. POST /api/board/codegen.
    Возврат (ответ-dict|None, ошибка)."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    r, err = _http_json_err(url + "/api/board/codegen",
                            {"text": str(task)[:1000], "board": _hostname()},
                            token=token, timeout=timeout)
    if r is None:
        return None, err
    if not r.get("ok"):
        return None, (r.get("error") or "заготовки нет")
    return r, ""


def cloud_skills(timeout=15):
    """Список функций ОБЛАКА: GET /api/skills."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    r, err = _http_get_json_err(url + "/api/skills", token=token,
                                timeout=timeout)
    if r is None:
        return None, err
    return (r.get("skills") or []), ""


def cloud_skill_run(sid, text, timeout=30):
    """Запустить функцию ОБЛАКА (песочница сервера):
    POST /api/skills/<sid>/run {"text"}."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    import urllib.parse
    qsid = urllib.parse.quote(str(sid), safe="")
    r, err = _http_json_err(url + "/api/skills/" + qsid + "/run",
                            {"text": str(text)[:2000], "board": _hostname()},
                            token=token, timeout=timeout)
    if r is None:
        return None, err
    return r, ("" if r.get("ok") else (r.get("error") or "функция не сработала"))


# ===========================================================================
# [2e] СЛОВАРИ РУССКОГО ЯЗЫКА (v2.8): python/dicts/ru_top.json (30 000 самых
#      частых слов OpenCorpora, ё->е; индекс = ранг частоты) + ru_pos.json
#      (существительные/глаголы/прилагательные по суффиксам). Словарь —
#      ОСНОВА нового обучения: слово реальное -> награда, выдумка -> штраф.
#      Выученные в облаке слова (dict_extra) живут в памяти и переживают
#      перезапуск, как и всё остальное.
# ===========================================================================
_DICT_DIR_CAND = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "dicts"),
                  "/app/python/dicts", "/app/dicts"]
_RU_ALPHABET = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")

# v2.38: ЗНАКИ ПРЕПИНАНИЯ — ЗАПИСИ СЛОВАРЯ. Запятая, точка, восклицательный
#      и вопросительный знаки лежат в ru_top.json (в конце списка, редкий
#      ранг) и в dict_set: мозг выбирает их ТЕМ ЖЕ СЭМПЛЕРОМ, что и слова,
#      и присылает плате word_push (скетч проводит знак в фразу своей
#      обычной механикой). САМА СЕТЬ НЕ ТРОНУТА: буквенная сеть скетча
#      (10x36, веса в EEPROM), зонная сеть и сеть знаний (биграммы)
#      работают как раньше — знаки входят словарём, а не новыми выходами.
PUNCT_MARKS = (",", ".", "!", "?")
_PUNCT_SET = frozenset(PUNCT_MARKS)


def _norm_word(w):
    return str(w or "").strip().lower().replace("ё", "е")


def _dict_dir():
    for d in _DICT_DIR_CAND:
        if d and os.path.isdir(d):
            return d
    return ""


def load_dicts_async():
    """Загрузить словари в фоне (30к слов грузятся быстро, но старт важнее)."""
    def _job():
        d = _dict_dir()
        if not d:
            log("СЛОВАРЬ", "каталог dicts не найден — словарная награда выключена")
            return
        try:
            t0 = time.time()
            with open(os.path.join(d, "ru_top.json"), "r", encoding="utf-8") as f:
                j = json.load(f)
            words = [str(w) for w in (j.get("words") or []) if w]
            pos = {}
            try:
                with open(os.path.join(d, "ru_pos.json"), "r", encoding="utf-8") as f:
                    pos = json.load(f)
            except Exception:
                pos = {}
            with SH.lock:
                SH.dict_set = set(words)
                SH.dict_set.update(_PUNCT_SET)    # v2.38: знаки — в словаре
                # v2.38: рейтинг частот — только СЛОВА (знаки живут в
                # dict_set и выбираются отдельной веткой сэмплера, в
                # ранжирование слов они не вмешиваются)
                SH.dict_top = [w for w in words if w not in _PUNCT_SET]
                SH.dict_pos = {k: [str(w) for w in (pos.get(k) or [])[:2000]]
                               for k in ("nouns", "verbs", "adjs")}
                SH.dict_set.update(SH.dict_extra)     # выученные из облака
                SH.dict_ready = True
            log("СЛОВАРЬ", "%d слов + знаки %s (%s) + POS %s за %d мс" %
                (len(words), " ".join(PUNCT_MARKS), d,
                 {k: len(v) for k, v in SH.dict_pos.items()},
                 int((time.time() - t0) * 1000)))
            # словарь готов: если диктант включён, а цели нет — дать сразу
            if CFG.get("dictate_on") and not (SH.dictation.get("target") or ""):
                dictation_next()
            # v2.16: словарь готов — можно строить сеть знаний для речи мозга
            if CFG.get("gen_on"):
                try:
                    _gen_rebuild_async()
                except Exception:
                    pass
            # v2.27: облачная дельта прошлых синхов + сверка словарей с облаком
            _dict_overlay_apply()
            _dict_cloud_sync_async()
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_job, daemon=True).start()


def dict_has(w):
    """Есть ли слово в словаре (с учётом выученных из облака)."""
    w = _norm_word(w)
    if not w:
        return False
    with SH.lock:
        if not SH.dict_ready:
            return False
        return (w in SH.dict_set) or (w in SH.dict_extra)


def dict_learn_words(words, limit=8):
    """Запомнить новые слова, найденные в облаке (переживают перезапуск)."""
    added = []
    with SH.lock:
        for w in (words or [])[:limit]:
            nw = _norm_word(w)[:24]
            if nw in _JUNK_WORDS:
                continue                         # v2.63: «англ» — не слово
            if len(nw) >= 3 and nw.isalpha() and set(nw) <= _RU_ALPHABET:
                if nw not in SH.dict_set and nw not in SH.dict_extra:
                    SH.dict_extra.add(nw)
                    SH.dict_set.add(nw)
                    added.append(nw)
        if added:
            SH.mem_dirty = True
    return added


# ===========================================================================
# [2e2] СЛОВАРИ В ОБЛАКЕ И СЛОВА ПЛАТЕ (v2.27):
#   * Словари (ru_top/ru_pos/extra) хранятся и в облаке: GET /api/dict/list,
#     GET /api/dict/get?name=..., POST /api/dict/put (облако СКЛЕИВАЕТ
#     списки с сохранением порядка — ранг частоты не должен ломаться).
#     При подключении облака мозг выгружает свои словари (если на облаке
#     их нет/меньше) и забирает облачные, которых нет локально (дельта
#     кладётся в dicts_cloud.json и переживает перезапуск). Локальных
#     словарей нет вообще — словарь берётся ЦЕЛИКОМ из облака.
#   * ПЛАТА произносит слова из БОЛЬШОГО словаря: word_pump_tick раз в 0.5 с
#     (v2.34: было ~8-11 с) выбирает слово (перекос к частым по рангу, 15% —
#     выученные из облака, 20% — «вспомненные» из журнала) и шлёт скетчу
#     RPC word_push — слово встаёт в ОЧЕРЕДЬ ПЛАТЫ (память на каждое слово,
#     WQ_N = 20) и произносится конвейером букв один за другим.
# ===========================================================================
DICT_SYNC_PERIOD = 600.0      # сек между фоновыми сверками словаря с облаком
_WORD_PUMP_PERIOD = 0.25      # сек между словами из ru_top.json (30 000)
                              # v2.86: было 0.5 — очередь платы (WQ_N=20)
                              # должна успевать наполняться из большого
                              # словаря, а не из 64 локальных слов скетча
_word_pump_next = [0.0]       # когда прислать следующее слово
_dict_sync_running = [False]

# ---------------------------------------------------------------------------
# v2.34: ПАМЯТЬ НА КАЖДОЕ СЛОВО (журнал мозга). Каждое слово, отосланное
# плате, запоминается: последние — для панели, уникальные со счётчиком —
# для «вспоминания» сэмплером. Журнал лежит рядом с конфигом
# (words_memory.json), переживает перезапуск; запись на диск — не чаще
# раза в 10 с (атомарная, через .tmp + os.replace).
_WORDS_MEM_NAME = "words_memory.json"
_word_mem = {"words": deque(maxlen=200),  # журнал: {"w": слово, "ts": время}
             "unique": {},                 # слово -> сколько раз отсылалось
             "count": 0,                   # всего слов отослано за всё время
             "dirty": False,
             "last_save": 0.0}
_WORD_MEM_UNIQUE_MAX = 5000    # сколько уникальных слов держать в журнале
_WORD_MEM_SAVE_EVERY = 10.0    # сек между записями журнала на диск
_WORDQ_POLL_PERIOD = 5.0       # сек между опросами очереди платы (word_q)
_wordq_poll_on = [False]       # поллер word_q запущен (один)


def _words_mem_path():
    """Файл журнала слов — рядом с конфигом (как dicts_cloud.json)."""
    try:
        return os.path.join(os.path.dirname(_cfg_path()), _WORDS_MEM_NAME)
    except Exception:
        return ""


def _word_mem_load():
    """Поднять журнал слов с диска (v2.34: память переживает перезапуск)."""
    p = _words_mem_path()
    if not p:
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return
    if not isinstance(j, dict):
        return
    try:
        with SH.lock:
            for it in (j.get("words") or [])[-200:]:
                if isinstance(it, dict) and it.get("w"):
                    _word_mem["words"].append(
                        {"w": str(it["w"])[:20],
                         "ts": float(it.get("ts") or 0.0)})
            uniq = j.get("unique")
            if isinstance(uniq, dict):
                for w, c in list(uniq.items())[-_WORD_MEM_UNIQUE_MAX:]:
                    try:
                        _word_mem["unique"][str(w)[:20]] = int(c)
                    except Exception:
                        continue
            _word_mem["count"] = int(j.get("count") or
                                     len(_word_mem["unique"]))
    except Exception:
        pass


def _word_mem_save(force=False):
    """Слить журнал слов на диск (не чаще _WORD_MEM_SAVE_EVERY;
    force — немедленно; всё атомарно)."""
    now = time.time()
    with SH.lock:
        if not _word_mem["dirty"]:
            return
        if not force and (now - _word_mem["last_save"]) < _WORD_MEM_SAVE_EVERY:
            return
        _word_mem["dirty"] = False
        _word_mem["last_save"] = now
        data = {"v": 1,
                "count": _word_mem["count"],
                "words": list(_word_mem["words"]),
                "unique": dict(list(_word_mem["unique"].items())
                               [-_WORD_MEM_UNIQUE_MAX:])}
    p = _words_mem_path()
    if not p:
        return
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def _word_mem_remember(w):
    """Запомнить отосланное плате слово (v2.34: память на каждое слово).
    Обновляет и SH.word_pump (панель видит память ЖИВОЙ между тиками).
    Возвращает последние до 12 слов — для панели (/api/state)."""
    now = time.time()
    with SH.lock:
        _word_mem["words"].append({"w": w, "ts": round(now, 2)})
        _word_mem["unique"][w] = _word_mem["unique"].get(w, 0) + 1
        while len(_word_mem["unique"]) > _WORD_MEM_UNIQUE_MAX:
            _word_mem["unique"].pop(next(iter(_word_mem["unique"])))
        _word_mem["count"] += 1
        _word_mem["dirty"] = True
        hist = [it["w"] for it in list(_word_mem["words"])[-12:]]
        wp = dict(SH.word_pump)
        wp["hist"] = hist
        wp["mem_unique"] = len(_word_mem["unique"])
        wp["mem_count"] = _word_mem["count"]
        SH.word_pump = wp
    return hist


def _word_q_apply(r):
    """v2.34: разобрать ответ word_q ("q=N;p=P;d=D") и влить в word_pump.
    Возвращает True, если разбор удался; мусор честно игнорируется."""
    q = psh = drp = None
    try:
        for part in str(r).split(";"):
            k, _, v = part.partition("=")
            if k == "q":
                q = int(v)
            elif k == "p":
                psh = int(v)
            elif k == "d":
                drp = int(v)
    except Exception:
        return False
    if q is None and psh is None and drp is None:
        return False
    with SH.lock:
        wp = dict(SH.word_pump)
        if q is not None:
            wp["q"] = q
        if psh is not None:
            wp["q_pushed"] = psh
        if drp is not None:
            wp["q_dropped"] = drp
        SH.word_pump = wp
    return True


def _word_q_poller():
    """v2.34: фоновый опрос ОЧЕРЕДИ СЛОВ платы (RPC word_q, sketch v2.34):
    сколько слов ждёт очереди, сколько пришло/потеряно при переполнении.
    Раз в 5 с; при тишине моста вызов честно сбрасывается работником
    (v2.18) — поллер молча ждёт, ничего не выдумывает."""
    while True:
        time.sleep(_WORDQ_POLL_PERIOD)
        if not _wordq_poll_on[0]:
            continue
        if not SH.bridge_seen:
            continue
        r = _bridge_call("word_q", timeout=2, channel="wordq")
        if r:
            _word_q_apply(r)


def _dict_overlay_path():
    """Файл дельты облачных слов (рядом с конфигом, переживает перезапуск):
    {"words": [...], "nouns": [...], "verbs": [...], "adjs": [...]} —
    только то, чего нет в локальных файлах dicts/."""
    try:
        return os.path.join(os.path.dirname(_cfg_path()), "dicts_cloud.json")
    except Exception:
        return ""


def _dict_overlay_apply():
    """Влить облачную дельту прошлых синхов (dicts_cloud.json) в словари."""
    p = _dict_overlay_path()
    if not p:
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return
    if not isinstance(j, dict):
        return
    words = [str(w) for w in (j.get("words") or []) if w][:20000]
    added = 0
    with SH.lock:
        for w in words:
            if w not in SH.dict_set:
                SH.dict_set.add(w)
                SH.dict_top.append(w)
                added += 1
        for k in ("nouns", "verbs", "adjs"):
            got = [str(w) for w in (j.get(k) or []) if w][:2000]
            cur = SH.dict_pos.get(k) or []
            have = set(cur)
            SH.dict_pos[k] = (cur + [w for w in got if w not in have])[:2000]
        if added and not SH.dict_ready:
            SH.dict_ready = True          # словарь, взятый из облака, — готов
    if added:
        log("СЛОВАРЬ", "из облачной дельты: +%d слов" % added)


def _dict_overlay_save():
    """Пересчитать дельту: слова живых словарей, которых нет в файлах dicts/."""
    p = _dict_overlay_path()
    d = _dict_dir()
    if not (p and d):
        return
    base = set()
    for fn in ("ru_top.json", "ru_pos.json"):
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                j = json.load(f)
            if isinstance(j, dict):
                for w in (j.get("words") or []):
                    base.add(str(w))
                for k in ("nouns", "verbs", "adjs"):
                    for w in (j.get(k) or []):
                        base.add(str(w))
        except Exception:
            continue
    with SH.lock:
        delta_w = sorted(w for w in SH.dict_set if w not in base)[-20000:]
        delta_p = {k: sorted(w for w in (SH.dict_pos.get(k) or [])
                             if w not in base)[-2000:]
                   for k in ("nouns", "verbs", "adjs")}
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict({"words": delta_w}, **delta_p), f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def _dict_cloud_count(data):
    """Сколько слов в словаре. Форматы: {"words": [...]} (файл/GET),
    {"name","words": N,"ts"} (сводка /api/dict/list), {nouns/verbs/adjs}."""
    if not isinstance(data, dict):
        return 0
    w = data.get("words")
    if isinstance(w, list):
        return len(w)
    if isinstance(w, int) and not isinstance(w, bool):
        return w                       # счётчик из /api/dict/list
    return sum(len(v) for k, v in data.items()
               if k in ("nouns", "verbs", "adjs") and isinstance(v, list))


def _dict_cloud_sync_async(force=False):
    """Запустить сверку словарей с облаком в фоне (без повторов, если одна
    уже идёт; без force — не чаще DICT_SYNC_PERIOD)."""
    if not CFG.get("dict_sync"):
        return
    with SH.lock:
        last = SH.dict_cloud.get("ts", 0.0)
    if not force and (time.time() - last) < DICT_SYNC_PERIOD:
        return
    if not _dict_sync_running[0]:
        threading.Thread(target=_dict_cloud_sync, daemon=True).start()


def _dict_cloud_sync():
    """Сверить словари с облаком (фоновый поток, v2.27):
    1) выгрузить свои, если на облаке их нет/меньше (облако склеивает);
    2) забрать облачные, которых нет локально (дельта -> dicts_cloud.json);
    3) выученные из облака слова (extra) слить в обе стороны.
    Локальных словарей нет — словарь целиком берётся из облака."""
    if _dict_sync_running[0]:
        return
    _dict_sync_running[0] = True
    url, token = _cloud_base()
    uploaded, merged, cwords, err = 0, 0, 0, ""
    if not url:
        err = "облако не задано"
    else:
        try:
            lst, e = _http_get_json_err(url + "/api/dict/list", token=token,
                                        timeout=25)
            if lst is None or not lst.get("ok"):
                err = e or "облако не отдало список словарей"
            else:
                cloud = {str(d.get("name")): d
                         for d in (lst.get("dicts") or []) if d.get("name")}
                ddir = _dict_dir()
                # --- 1. свои -> облако (облако держит объединение) --------
                local = {}
                for fn, name in (("ru_top.json", "ru_top"),
                                 ("ru_pos.json", "ru_pos")):
                    if not ddir:
                        break
                    try:
                        with open(os.path.join(ddir, fn), "r",
                                  encoding="utf-8") as f:
                            local[name] = json.load(f)
                    except Exception:
                        continue
                with SH.lock:
                    extra_local = sorted(SH.dict_extra)[-5000:]
                local["extra"] = {"words": extra_local}
                for name, data in local.items():
                    if not isinstance(data, dict):
                        continue
                    if _dict_cloud_count(cloud.get(name)) >= \
                            _dict_cloud_count(data):
                        continue               # на облаке уже не меньше
                    r, e = _http_json_err(url + "/api/dict/put",
                                          {"name": name, "data": data},
                                          token=token, timeout=60)
                    if r and r.get("ok"):
                        uploaded += 1
                    elif not err:
                        err = e or "выгрузка словаря не прошла"
                # --- 2. облачное -> себе (дельта) -------------------------
                for name in ("ru_top", "ru_pos", "extra"):
                    cl = cloud.get(name) or {}
                    if _dict_cloud_count(cl) <= \
                            _dict_cloud_count(local.get(name) or {}):
                        continue
                    g, e = _http_get_json_err(
                        url + "/api/dict/get?name=" + name,
                        token=token, timeout=60)
                    if not g or not g.get("ok") or not g.get("found"):
                        if not err and g is None:
                            err = e or "словарь с облака не пришёл"
                        continue
                    data = g.get("data") or {}
                    with SH.lock:
                        if name == "extra":
                            new = [str(w) for w in (data.get("words") or [])
                                   if w and str(w) not in SH.dict_extra
                                   and str(w) not in SH.dict_set
                                   and str(w) not in _JUNK_WORDS][:5000]
                            SH.dict_extra.update(new)
                            SH.dict_set.update(new)
                            SH.dict_top.extend(new)
                        elif name == "ru_top":
                            new = [str(w) for w in (data.get("words") or [])
                                   if w and str(w) not in SH.dict_set][:20000]
                            SH.dict_set.update(new)
                            SH.dict_top.extend(new)
                        else:                  # ru_pos
                            new = []
                            for k in ("nouns", "verbs", "adjs"):
                                got = [str(w) for w in (data.get(k) or []) if w]
                                cur = SH.dict_pos.get(k) or []
                                have_k = set(cur)
                                add = [w for w in got if w not in have_k][:2000]
                                SH.dict_pos[k] = (cur + add)[:2000]
                                new.extend(add)
                        if new:
                            merged += len(new)
                            SH.dict_ready = True   # облачный словарь готов
                    if not err and e:
                        err = e
                if merged:
                    _dict_overlay_save()
                    log("СЛОВАРЬ", "из облака взято +%d слов" % merged)
                if uploaded:
                    log("СЛОВАРЬ", "в облако выгружено словарей: %d" % uploaded)
                cwords = sum(int(d.get("words", 0) or 0)
                             for d in cloud.values())
        except Exception:
            traceback.print_exc()
            err = err or "ошибка сверки словарей"
    ok = not err
    with SH.lock:
        SH.dict_cloud = {"ok": ok, "err": err[:140], "ts": time.time(),
                         "uploaded": uploaded, "merged": merged,
                         "cloud_words": cwords}
    if not ok:
        log("СЛОВАРЬ", "облако:", err)
    if merged and CFG.get("gen_on"):
        try:
            _gen_rebuild_async()           # новые слова — в сеть знаний
        except Exception:
            pass
    _dict_sync_running[0] = False


def _dict_sample_word():
    """Слово для платы (v2.27): перекос к частым по рангу (i = r^2 * n —
    слово из первых 100 примерно в 10% случаев вместо 1%), 15% — выученные
    из облака. v2.34 (память на каждое слово): в 20% случаев «вспоминаем»
    старое слово из журнала (повторение — мать учения) и НЕ повторяем
    ни одно из последних пяти отосланных (на это тоже память).
    v2.38: в 10% случаев выбираем ЗНАК ПРЕПИНАНИЯ (",", ".", "!", "?") —
    он тоже ЗАПИСЬ СЛОВАРЯ (ru_top.json + dict_set). Знак идёт в ту же
    очередь слов платы (word_push) и проводится скетчем механикой знаков;
    буквенная сеть не задействована и не менялась.
    v2.83: приоритет словам из последней связной мысли мозга (не случайный
    шум рядом со смыслом) — нейроны получают меньше бессвязных слов.
    Возвращает ЗАГЛАВНОЕ слово 2-10 букв, ЗНАК или ""."""
    with SH.lock:
        top = list(SH.dict_top)
        extra = sorted(SH.dict_extra) if SH.dict_extra else []
        ready = SH.dict_ready
        recent = [_norm_word(it["w"])
                  for it in list(_word_mem["words"])[-5:]]
        old = [w for w in _word_mem["unique"].keys()
               if 2 <= len(w) <= 10]
        last_ph = (_GEN_STATE.get("last") or "") if _GEN_STATE else ""
    # v2.83: слова из последней фразы мозга — связный контекст для нейронов
    thought_ws = [_norm_word(w) for w in re.findall(r"[а-яё]{3,}", last_ph.lower())
                  if 2 <= len(w) <= 10 and w not in recent]
    want_mark = ready and random.random() < 0.08    # чуть реже знаки
    w = ""
    for _ in range(8):
        if want_mark:
            cand = random.choice(PUNCT_MARKS)
            if cand in recent:
                continue
            w = cand
            break
        if thought_ws and random.random() < 0.35:
            cand = random.choice(thought_ws)   # из связной мысли
        elif old and random.random() < 0.12:
            cand = random.choice(old)
        elif extra and random.random() < 0.08:
            cand = random.choice(extra)
        elif top:
            # v2.86: слово из ru_top.json (30 000). Перекос к частым
            # (i = r^1.6 * n) — частые чаще, но весь словарь доступен
            i = int((random.random() ** 1.6) * len(top))
            if i >= len(top):
                i = len(top) - 1
            cand = top[i] if i >= 0 else ""
        else:
            cand = ""
        cand = _norm_word(cand).replace("ё", "е")
        if not (2 <= len(cand) <= 10) or not (set(cand) <= _RU_ALPHABET):
            continue
        w = cand
        if w not in recent:
            break
    if want_mark:                            # v2.38: знак как есть
        return w if w in _PUNCT_SET else ""
    return w.upper() if w else ""


def word_pump_tick():
    """v2.34: раз в 0.5 с прислать плате слово из большого словаря
    (RPC word_push) — скетч держит ОЧЕРЕДЬ слов (память платы, WQ_N = 20)
    и произносит их конвейером букв одна за другой; каждое отосланное
    слово мозг запоминает в журнале (words_memory.json — переживает
    перезапуск), журнал периодически сливается на диск.
    v2.33: проверяем СВЕЖЕСТЬ моста (пакеты < 12 с), при сбое пишем
    ЧЕСТНУЮ причину в SH.word_pump.err (видно в /api/state), статистика
    word_push копится в _RPC_STATS."""
    if not CFG.get("words_to_board"):
        return
    now = time.time()
    if now < _word_pump_next[0]:
        return
    _word_pump_next[0] = now + _WORD_PUMP_PERIOD + random.uniform(0, 0.1)
    with SH.lock:
        seen = SH.bridge_seen
        age = (now - SH.bridge_ts) if seen else -1.0
        prev_err = SH.word_pump.get("err", "")
    if not seen:                            # скетча нет — слова не льём
        return
    if age > 12.0:
        with SH.lock:
            SH.word_pump = {"ts": now, "last_word": SH.word_pump.get(
                "last_word", ""), "count": SH.word_pump.get("count", 0),
                "err": "мост молчит %.0f с — слово не послано" % age}
        return
    w = _dict_sample_word()
    if not w:
        return
    try:
        _bridge_call("word_push", w, wait=False)
    except Exception:
        pass
    hist = _word_mem_remember(w)            # v2.34: память на каждое слово
    _word_mem_save()                        # не чаще раза в 10 с
    with _RPC_STATS_LOCK:
        st = dict(_RPC_STATS.get("word_push") or {})
    err = "" if (not st or st.get("err", 0) == 0 or
                 st.get("ok", 0) >= st.get("err", 0)) else \
        str(st.get("last") or "word_push не отвечает")
    if err and not prev_err:
        log("СЛОВА", "word_push не проходит:", err)
    with SH.lock:
        prev = SH.word_pump
        SH.word_pump = {"ts": now, "last_word": w,
                        "count": prev.get("count", 0) + 1,
                        "err": err,
                        "hist": hist,          # v2.34: память на каждое слово
                        "mem_unique": len(_word_mem["unique"]),
                        "mem_count": _word_mem["count"],
                        "mem_file": _words_mem_path(),
                        "q": prev.get("q", -1),
                        "q_pushed": prev.get("q_pushed", 0),
                        "q_dropped": prev.get("q_dropped", 0)}


def dict_pick(min_len=3, max_len=8, pos=""):
    """Случайное слово для диктанта; band длины растёт с успехами мозга."""
    with SH.lock:
        if pos and SH.dict_pos.get(pos):
            src = list(SH.dict_pos[pos])
        elif SH.dict_set:
            src = None
        else:
            return ""
        if src is None:
            src = random.choice([list(SH.dict_pos["nouns"]),
                                 list(SH.dict_pos["verbs"]),
                                 list(SH.dict_pos["adjs"])]) \
                if any(SH.dict_pos.values()) else list(SH.dict_set)
    # v2.38: знаки препинания живут в словаре, но диктант — про СЛОВА:
    # выметаем их из источника (band их и так отсёк по длине, а вот
    # запасной random.choice мог бы вытянуть знак)
    src = [w for w in src if w not in _PUNCT_SET] or src
    band = [w for w in src if min_len <= len(w) <= max_len]
    return random.choice(band) if band else (random.choice(src) if src else "")


# ===========================================================================
# [3] ЗОННЫЙ МОЗГ (офлайн-ответы): 12 зон-специалистов, фразы с подстановками
# ===========================================================================
ZONES = [
    ("Приветствие", ("привет", "здравств", "хай", "добрый")),
    ("Вопросы",     ("почему", "зачем", "когда", "кто", "правда", "что такое")),
    ("Статус",      ("статус", "готов", "норм", "цикл", "дела", "как ты")),
    ("Буквы",       ("букв", "слов", "фраз", "генератор", "алфавит", "собира")),
    ("Камера",      ("вижу", "камер", "объект", "человек", "кадр", "видиш")),
    ("Память",      ("помн", "памят", "истори", "архив", "запомн")),
    ("Сигнал",      ("сигнал", "импульс", "пин", "волна", "частот", "герц")),
    ("Юмор",        ("шутк", "смешн", "анекдот", "юмор", "ха-ха")),
    ("Помощь",      ("помощ", "помог", "команд", "справк", "как работать")),
    ("Облако",      ("облак", "сервер", "синхрон", "токен", "платформ")),
    ("Обучение",    ("учись", "учит", "молодец", "хорош", "спасиб", "плох",
                     "неправильн", "обучи")),
    ("Покой",       ("тишин", "спи", "покой", "пауз", "отдыхай")),
    # v2.31: софтовые зоны (аппаратные 12 нейронов зажигают первые 12 зон,
    # эти три возбуждаются словами, сетью и фоновыми событиями):
    ("Знания",      ("статья", "прочит", "почитай", "прочти", "знан",
                     "учебник", "материал", "энциклопед", "источник",
                     "найди в базе", "кто такой", "что такое")),
    ("Кирпичики",   ("кирпич", "приложени", "программ", "собер", "собир",
                     "апплаб", "app lab", "скрипт", "автоматиз")),
    ("Любопытство", ("любопыт", "поищ", "поиск", "интернет", "найди в сети",
                     "что нового", "погугл", "поищи в сети")),
    # v2.32: зона «Математика» — мозг сам пишет формулы и строит модели:
    # база формул (встроенная + скачанная из облака), расчёт с
    # подстановкой, модель по данным + прогноз, арифметика. Без LLM.
    ("Математика",  ("формул", "посчита", "вычисл", "уравнен", "матем",
                     "модель", "прогноз", "тренд", "площад", "процент",
                     "гипотенуз", "корн уравнен", "дискриминан", "среднее",
                     "окружност", "скорост", "вклад")),
]


# ===========================================================================
# v2.91: МОЗГ НЕ ЗНАЕТ — ЧИТАЕТ САМ. Владелец: «пусть мозг, если чего-то не
# знает, сам читает статьи в Википедии и ищет информацию, учится связно
# говорить за счёт прочтения статей».
#   * чего мозг не знает (в базе знаний нет статьи под вопрос, поиск пуст)
#     — тема встаёт в очередь «не знаю»; статью мозг читает сразу (вопрос
#     «Знания») или фоном раз в READ_PERIOD на вдохе;
#   * читает через облако (POST /api/board/read: Википедия, иначе Рувики;
#     статья ложится и в личную базу знаний платы), облака нет — сам
#     напрямую из ru.wikipedia.org / ru.ruwiki.ru;
#   * прочитанное: нейросеть материалов (mn_feed) и СЕТЬ ЗНАНИЙ ГЕНЕРАТОРА
#     РЕЧИ — связи между соседними словами настоящих предложений статей.
#     Чем больше прочитано, тем связнее фразы мозга (_gen_texts).
#   * хранится в neuro12_reads.json рядом с памятью (до READ_KEEP статей).
# ===========================================================================
READ_PERIOD = 240.0           # сек между фоновыми чтениями «не знаю»
READ_KEEP = 60                # статей в памяти чтения
READ_CHARS = 6000             # знаков статьи для сети знаний
_READ = {"items": [], "miss": [], "loaded": False, "last": 0.0, "n": 0,
         "err": "", "last_title": "", "busy": False}
_READ_LOCK = threading.RLock()
_READ_STOP = frozenset("что такое кто как где когда почему зачем какой какая "
                       "какие сколько расскажи объясни скажи про это мне ты "
                       "знаешь можешь пожалуйста".split())


def _read_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_reads.json")
    except Exception:
        return "neuro12_reads.json"


def _read_load():
    with _READ_LOCK:
        if _READ["loaded"]:
            return
        _READ["loaded"] = True
        try:
            with open(_read_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            _READ["items"] = [x for x in (d.get("items") or []) if isinstance(x, dict)][-READ_KEEP:]
            _READ["miss"] = [str(x)[:120] for x in (d.get("miss") or [])][-30:]
        except Exception:
            pass


def _read_save():
    try:
        fn = _read_path()
        with _READ_LOCK:
            data = {"items": _READ["items"][-READ_KEEP:], "miss": _READ["miss"][-30:]}
        with open(fn + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(fn + ".tmp", fn)
    except Exception:
        pass


def read_topic_of(text):
    """Тема вопроса: без вопросительных и служебных слов."""
    ws = [w for w in re.findall(r"[а-яёa-z0-9-]+", str(text or "").lower())
          if w not in _READ_STOP and len(w) >= 3]
    return " ".join(ws[:5])


def read_miss(text):
    """Мозг не знал — запомнить тему (прочитаю фоном)."""
    t = read_topic_of(text)
    if not t:
        return
    _read_load()
    with _READ_LOCK:
        if t not in _READ["miss"] and not any(
                t in str(x.get("q") or "") for x in _READ["items"][-20:]):
            _READ["miss"].append(t)
            del _READ["miss"][:-30]
    _read_save()


def _wiki_direct(topic):
    """Облака нет — сама: ru.wikipedia.org, затем ru.ruwiki.ru (API MediaWiki)."""
    import urllib.parse
    import urllib.request
    for host, src in (("ru.wikipedia.org", "Википедия"), ("ru.ruwiki.ru", "Рувики")):
        try:
            u = "https://%s/w/api.php?%s" % (host, urllib.parse.urlencode(
                {"action": "query", "list": "search", "srsearch": topic,
                 "format": "json", "srlimit": "3"}))
            req = urllib.request.Request(u, headers={
                "User-Agent": "neuro12-app/%s (educational board)" % APP_VERSION})
            with urllib.request.urlopen(req, timeout=12) as r:
                hits = (json.loads(r.read().decode("utf-8")).get("query") or {}).get("search") or []
            if not hits:
                continue
            title = str(hits[0].get("title") or "")
            u2 = "https://%s/w/api.php?%s" % (host, urllib.parse.urlencode(
                {"action": "query", "prop": "extracts", "explaintext": "1",
                 "format": "json", "titles": title, "redirects": "1"}))
            req2 = urllib.request.Request(u2, headers=req.headers)
            with urllib.request.urlopen(req2, timeout=15) as r:
                pages = (json.loads(r.read().decode("utf-8")).get("query") or {}).get("pages") or {}
            for pg in pages.values():
                ex = str(pg.get("extract") or "").strip()
                if len(ex) >= 200:
                    return title, ex[:12000], src
        except Exception as e:
            _READ["err"] = "%s: %s" % (host, str(e)[:80])
    return None, "", ""


def brain_read(text, why="не знала"):
    """Прочитать статью по теме и научиться на ней. -> (title, text) | (None, "")."""
    topic = read_topic_of(text) or str(text or "").strip()[:120]
    if not topic:
        return None, ""
    _read_load()
    title, body, src = None, "", ""
    url, token = _cloud_base()
    if url:
        try:
            r, err = _http_json_err(url + "/api/board/read",
                                    {"board": _hostname(), "topic": topic[:150]},
                                    token=token, timeout=40)
            if r and r.get("ok"):
                title, body, src = str(r.get("title") or ""), str(r.get("text") or ""), \
                    str(r.get("src") or "Википедия")
            else:
                _READ["err"] = str((r or {}).get("error") or err or "?")[:120]
        except Exception as e:
            _READ["err"] = str(e)[:120]
    if not title:
        title, body, src = _wiki_direct(topic)
    if not title or len(body) < 200:
        log("ЧТЕНИЕ", "«%s»: статьи не нашлось (%s)" % (topic[:60], _READ["err"] or "пусто"))
        return None, ""
    with _READ_LOCK:
        _READ["items"] = [x for x in _READ["items"] if x.get("t") != title]
        _READ["items"].append({"t": title[:120], "x": body[:READ_CHARS], "src": src,
                               "q": topic[:120], "ts": time.time(), "why": why})
        del _READ["items"][:-READ_KEEP]
        _READ["miss"] = [m for m in _READ["miss"] if m != topic]
        _READ["n"] += 1
        _READ["last_title"] = title
    _read_save()
    try:
        mn_feed(body[:20000], title=title, src="чтение: %s" % src)
    except Exception:
        pass
    try:                       # термины статьи (≥2 раз) — в словарь мозга,
        cnt = {}               # иначе сеть знаний рвёт на них цепочки слов
        for w in re.findall(r"[а-яё]{4,24}", body[:READ_CHARS].lower()):
            cnt[w] = cnt.get(w, 0) + 1
        new = [w for w, c in sorted(cnt.items(), key=lambda kv: -kv[1]) if c >= 2]
        added = dict_learn_words(new, limit=40)
        if added:
            log("ЧТЕНИЕ", "новые слова из статьи: %s" % ", ".join(added[:12]))
    except Exception:
        pass
    try:
        _gen_rebuild_async()               # сеть знаний речи — с новой статьёй
    except Exception:
        pass
    log("ЧТЕНИЕ", "%s «%s» — прочитала «%s» (%s, %d зн.): учусь говорить по ней"
        % (why, topic[:50], title[:60], src, len(body)))
    return title, body


def read_tick():
    """Фон: тема из очереди «не знаю» -> чтение (на вдохе, раз в READ_PERIOD)."""
    now = time.time()
    if _READ["busy"] or now - _READ["last"] < READ_PERIOD:
        return
    _read_load()
    with _READ_LOCK:
        topic = _READ["miss"][0] if _READ["miss"] else ""
    if not topic:
        return
    try:
        if not breath_inhale("Чтение статей"):
            return
    except Exception:
        pass
    _READ["last"] = now
    _READ["busy"] = True

    def _job():
        try:
            t, _x = brain_read(topic, why="фоном: не знала")
            if not t:
                with _READ_LOCK:          # не нашлось — в конец очереди
                    if topic in _READ["miss"]:
                        _READ["miss"].remove(topic)
                        _READ["miss"].append(topic)
        finally:
            _READ["busy"] = False
    threading.Thread(target=_job, daemon=True, name="brain-read").start()


def read_status():
    _read_load()
    with _READ_LOCK:
        return {"read": len(_READ["items"]), "n": _READ["n"], "miss": list(_READ["miss"][-8:]),
                "last": _READ["last_title"], "err": _READ["err"],
                "chars": sum(len(str(x.get("x") or "")) for x in _READ["items"])}


def zone_of(text):
    low = (text or "").lower()
    best, hits = "", 0
    # v2.31.1: идём СПИСКА КОНЦА — новые живые зоны (Знания/Кирпичики/
    # Любопытство) выигрывают равные споры: «что такое нейрон?» попадает
    # в Знания (мозг прочитает статью), а не в заготовку «Вопросы».
    # Действие сильнее шаблона: пока зона находит СТАТЬЮ/РЕЦЕПТ/ПОИСК,
    # ответ будет настоящим, а фразой зоны остаётся только честный отказ.
    for name, stems in reversed(ZONES):
        n = sum(1 for s in stems if s in low)
        if n > hits:
            best, hits = name, n
    return best or "Разговор"


def _pick(text, seq):
    """Детерминированный выбор варианта фразы (как в основном проекте)."""
    h = 2166136261
    for ch in (text or "") + str(int(seq)):
        h ^= ord(ch) & 0xFF
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def zone_reply(user_text, zone=None):
    """Офлайн-ответ зон: живые фразы с подстановкой текущего состояния.
    v2.15: зону может приносить адаптивная сеть (nn_pick) — тогда слова
    в тексте не обязательны, нейроны вспоминают выученные ассоциации."""
    with SH.lock:
        # v2.19: ltr/chr/word в шаблоны больше не подставляются — человек
        # не видит букв и слов буквогенератора, только фразы и счётчики
        st = {"gen": SH.gen_hz, "say": SH.say or "-",
              "spk": SH.spk, "act": SH.act, "top": max(0, SH.top),
              "cyc": SH.cyc, "nmem": len(SH.memory),
              "teach": SH.teach_count,
              "nn": _NN["n"]}                     # v2.15: нейроны зонной сети
    last_user = ""
    for m in reversed(SH.memory):
        if m.get("role") == "user":
            last_user = str(m.get("text", ""))[:60]
            break
    st["last_user"] = last_user or "…"
    zone = zone or zone_of(user_text)
    phrases = {
        "Приветствие": (
            "Привет! Буквогенератор собрал уже {teach} фраз. Слушаю val_c и тебя."),
        "Вопросы": (
            "Думаю вместе с нейронами: активны {act} из 12, топ — №{top}. "
            "Про «{last_user}» скажу так: частотная шина считает иначе, чем люди."),
        "Статус": (
            "Статус: цикл {cyc}, спайков {spk}, генератор {gen} Гц, активны {act} из 12. "
            "Зонная сеть: {nn} нейронов (размер задаёт наставник). "
            "Последняя фраза: «{say}»."),
        "Буквы": (
            "Буквогенератор работает скрытно: свои буквы и слова тебе не показывает — "
            "они уходят на пин D6, а наружу выходят только готовые фразы, как «{say}». "
            "Учитель оценил фраз: {nmem}."),
        "Камера": (
            "Сводка val_b уходит нейронам через скетч: «{last_user}». "
            "Кадр меняет частоту шины до +500 Гц."),
        "Память": (
            "В памяти {nmem} реплик. Последнее твоё: «{last_user}». "
            "Локально это переживёт перезапуск, а настроишь облако в "
            "«Настройках» — переживёт и переезд на новую плату."),
        "Сигнал": (
            "Частотная шина D4->A0 всех нейронов сейчас {gen} Гц. "
            "Номер буквы кладётся прямо в полосу частот — нейроны слышат его пластикой."),
        "Юмор": (
            "Нейрон №6 шутит: 33 буквы, 12 нейронов и один динамик — все хотят говорить!"),
        "Помощь": (
            "Пиши в чат — текст уйдёт нейронам стимулом STIM, а ответ соберёт "
            "нейросеть платы (зоны + навыки + память). Кнопки +1/-1 учат "
            "буквогенератор после фразы. «статус» — состояние, «повтори …» — эхо."),
        "Облако": (
            "Облако настраивается в «Настройках»: адрес платформы и токен. "
            "Через него ходит учитель-наставник (POST /api/llm/teach — советы, "
            "цели, вердикты) и речь TTS (POST /api/tts -> WAV). Без облака "
            "отвечают только зоны."),
        "Обучение": (
            "Принято! Учитель подкрепляет решения букв: оценки уходят скетчу, "
            "веса букв в EEPROM. Циклов: {cyc}."),
        "Покой": (
            "Тишина... генератор {gen} Гц, нейроны дремлют. Скажи что-нибудь — проснусь."),
        "Знания": (
            "Ищу статью под твой вопрос в базе знаний..."),
        "Кирпичики": (
            "Подбираю рецепт в примерах кода и собираю приложение..."),
        "Любопытство": (
            "Любопытно! Ищу в интернете по этой теме..."),
        "Математика": (
            "Считаю сама: формулы и модели — без LLM. Ищу формулу в своей "
            "базе..."),
        "Разговор": (
            "Слышу: «{last_user}». Отвечаю сам: зоны + навыки + память, "
            "а наставник подсказывает темы."),
    }
    text = phrases.get(zone, phrases["Разговор"])
    try:
        text = text.format(**st)
    except Exception:
        pass
    # v2.31: ЖИВЫЕ ЗОНЫ — знания выбирают и читают статью под вопрос,
    # кирпичики сами программируют приложения, любопытство ищет в
    # интернете. Ошибка сети не роняет ответ: остаётся фраза зоны.
    if zone == "Знания" and (user_text or "").strip():
        try:
            _t, _x = kb_read_best(user_text)
            if _t:
                _first = re.sub(r"\s+", " ", _x[:700]).strip()
                text = ("Выбрала статью «%s» и прочитала её целиком. "
                        "Отвечаю по ней: %s" % (_t[:70], _first))
                _sketch_react("read")
                try:
                    _gen_task_async("перескажи статью «%s» своими словами: %s"
                                    % (_t[:60], _first[:120]), "чтение")
                except Exception:
                    pass
            else:
                # v2.91: не знаю — читаю сама (Википедия / Рувики)
                _t2, _x2 = brain_read(user_text, why="не знала")
                if _t2:
                    _first = re.sub(r"\s+", " ", _x2[:700]).strip()
                    text = ("Сама этого не знала — прочитала статью «%s» и "
                            "запомнила. Вот что в ней: %s" % (_t2[:70], _first))
                    _sketch_react("read")
                else:
                    read_miss(user_text)
                    text = ("По «%s» не знаю: в базе знаний статьи нет, и "
                            "прочитать сейчас не вышло — поставила в очередь, "
                            "прочитаю, когда будет связь." % user_text.strip()[:60])
        except Exception:
            pass
    elif zone == "Кирпичики" and (user_text or "").strip():
        try:
            text = bricks_build(user_text)
        except Exception as _e:
            text = ("Кирпичики: не собралось (%s). Рецепты приложений "
                    "лежат в базе примеров кода облака."
                    % type(_e).__name__)
    elif zone == "Любопытство" and (user_text or "").strip():
        try:
            text = _curiosity_search(user_text, share=True)
        except Exception:
            pass
    elif zone == "Математика" and (user_text or "").strip():
        try:
            text = math_reply(user_text)
        except Exception as _e:
            text = ("Математика: не посчиталось (%s). Примеры: «площадь "
                    "круга радиусом 5», «вклад 1000 под 10 процентов на 3 "
                    "года», «модель по данным 1,2; 2,4; 3,5 — прогноз 4»."
                    % type(_e).__name__)
    return text, zone


# ===========================================================================
# [3b] ЕДИНЫЙ МОЗГ (v2.10): устройства общаются МЕЖДУ СОБОЙ через облако,
#      как один распределённый мозг. LLM нигде не используется, кроме
#      обучения — письма других устройств отвечаем САМИ (зоны + память +
#      базы знаний + поиск), с инструкцией писать КАК МОЖНО ДЛИННЕЕ.
#      Протокол платформы мозг-12 (v2.4):
#        GET  /api/brain/inbox?board=<id>   — забрать свои письма;
#        POST /api/brain/answer {id,text,zone} — ответить отправителю;
#        POST /api/brain/msg {to,text,from}   — написать другому мозгу;
#        POST /api/brains/ask {text,from}     — спросить СВОБОДНЫЙ мозг.
#      Платформа сама помечает нас «в сети» по опросу inbox (heartbeat) и
#      маршрутизирует письма чата VPS/Макс-бота свободным мозгам.
# ===========================================================================
BRIN_PERIOD = 30.0            # сек между опросами мозг-почты
BRIN_TIMEOUT = 8              # сек таймаут HTTP мозг-почты
BMAIL_CAP = 4000              # потолок одного письма-ответа (как в облаке)


def _mail_set(**kw):
    with SH.lock:
        SH.brainmail.update(kw)
        SH.brainmail["last_ts"] = time.time()
        SH.mem_dirty = True


def kb_lookup(query):
    """Базы знаний облака: ЛИЧНАЯ база этого устройства (kb/devices/<id>/
    — файлы и статьи, что мозг загружал сам) + общая база. Возврат
    (title, snippet) | None. Это «воспоминания из открытых источников»,
    а не LLM: только реально сохранённые знания."""
    try:                                # v2.45: сначала локальный JSON
        loc = kbl_lookup(query)
        if loc and loc[1]:
            try:
                mn_feed(loc[1], loc[0], "KB платы (JSON)")
            except Exception:
                pass
            return loc
    except Exception:
        pass
    url, token = _cloud_base()          # KB — база знаний облака
    if not (url and (query or "").strip()):
        return None
    try:
        import urllib.parse
        u = (url + "/api/kb/search?limit=1&wiki=1&device="
             + urllib.parse.quote(_hostname()) + "&q="
             + urllib.parse.quote(query[:120]))
        r, err = _http_get_json_err(u, token=token, timeout=10)
        if not r or not r.get("ok"):
            return None
        res = r.get("results") or []
        if res:
            title = str(res[0].get("title") or "")
            snippet = str(res[0].get("snippet") or "")
            # v2.25: статья из KB — материал для нейросети материалов
            if snippet:
                mn_feed(snippet, title, "KB облака")
            return (title, snippet)
    except Exception as e:
        log("KB", "недоступна:", str(e)[:100])
    return None


def kb_read_best(query):
    """v2.31: ВЫБОР СТАТЬИ ПОД ВОПРОС: поиск по KB облака -> ЛУЧШАЯ статья
    читается ЦЕЛИКОМ (GET /api/kb/article/<id>) -> нейросеть материалов ->
    выжимка для ответа. Возврат (title, text) | (None, "")."""
    try:                                # v2.45: сначала локальный JSON
        lt, lx = kbl_read(query)
        if lt and len(lx) >= 40:
            try:
                mn_feed(lx[:20000], title=lt, src="KB платы (JSON)")
            except Exception:
                pass
            log("KB", "прочитала статью из локального JSON:", lt[:60])
            return lt, lx
    except Exception:
        pass
    url, token = _cloud_base()
    if not (url and (query or "").strip()):
        return None, ""
    try:
        import urllib.parse
        u = (url + "/api/kb/search?limit=3&wiki=0&device="
             + urllib.parse.quote(_hostname()) + "&q="
             + urllib.parse.quote(query[:120]))
        r, _e = _http_get_json_err(u, token=token, timeout=10)
        res = [x for x in ((r or {}).get("results") or [])
               if not x.get("wiki") and x.get("id")]
        if not res:
            return None, ""
        full, _e2 = _http_get_json_err(
            url + "/api/kb/article/" + urllib.parse.quote(str(res[0]["id"])),
            token=token, timeout=15)
        title = str((full or {}).get("title") or res[0].get("title") or "?")
        text = str((full or {}).get("text") or "").strip()
        if len(text) < 40:
            return None, ""
        try:
            mn_feed(text[:20000], title=title, src="KB-чтение под вопрос")
        except Exception:
            pass
        log("KB", "прочитала статью целиком:", title[:60],
            "(%d зн.)" % len(text))
        return title, text
    except Exception as e:
        log("KB", "чтение не вышло:", str(e)[:80])
    return None, ""


# ---------------------------------------------------------------------------
# [3j] ЛОКАЛЬНАЯ БАЗА ЗНАНИЙ НА ПЛАТЕ В JSON (v2.45)
#      Облако (v3.49) отдаёт статьи пачками GET /api/kb/pack; плата сливает
#      их в свой файл neuro12_kb.json (рядом с файлом памяти) и ищет по нему
#      САМА, без сети. В облако идёт только если локально ничего не нашлось.
#      v2.46: статьи приходят КУСКАМИ (~1200 знаков, с нахлёстом ~150 знаков
#      между соседними — это контекст). Формат файла: {"v":2, "cloud_ts",
#      "synced", "items": {"<статья>#<i>": {"a": статья, "i": номер, "n": всего,
#      "t": заголовок, "h": раздел, "l": начало статьи (в куске 0), "x": текст
#      куска, "k": [слова], "s": источник, "ts"}}}. Нашла кусок из середины —
#      к нему подставляются «начало статьи» и соседние куски (без дублей
#      нахлёста). Удалённые в облаке статьи убираются при синхронизации.
# ---------------------------------------------------------------------------
KBL_PERIOD = 600.0            # сек между синхронизациями с облаком
KBL_FIRST = 30.0              # первая — через 30 с после запуска
_KBL = {"loaded": False, "items": {}, "idx": {}, "tsets": {}, "xsets": {},
        "cloud_ts": 0.0, "synced": 0.0, "last_try": 0.0, "hits": 0,
        "err": "", "busy": False, "boot": time.time(), "bytes": 0}
_KBL_LOCK = threading.RLock()
_KBL_STOP = frozenset(["что", "такое", "кто", "это", "как", "где", "когда",
                       "почему", "какой", "какая", "какие", "сколько",
                       "зачем", "расскажи", "объясни", "про", "для", "или",
                       "есть", "был", "была", "было", "быть", "этот"])


def _kbl_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_kb.json")
    except Exception:
        return "neuro12_kb.json"


def _kbl_stems(text):
    out = []
    for w in re.findall(r"[а-яёa-z0-9]{3,}", str(text).lower().replace("ё", "е")):
        if w not in _KBL_STOP:
            out.append(w[:5])
    return out


def _kbl_build():
    idx, ts_, xs_, by_a = {}, {}, {}, {}
    for aid, it in _KBL["items"].items():
        by_a.setdefault(it.get("a") or aid, []).append(
            (int(it.get("i") or 0), aid))
        tset = set(_kbl_stems(it.get("t", ""))) | set(_kbl_stems(
            it.get("h", "")))
        xset = set(_kbl_stems(it.get("x", ""))) | set(_kbl_stems(
            " ".join(it.get("k") or [])))
        ts_[aid], xs_[aid] = tset, xset
        for st in tset | xset:
            idx.setdefault(st, set()).add(aid)
    for lst in by_a.values():
        lst.sort()
    _KBL["idx"], _KBL["tsets"], _KBL["xsets"] = idx, ts_, xs_
    _KBL["by_a"] = by_a


def kbl_load():
    with _KBL_LOCK:
        if _KBL["loaded"]:
            return
        _KBL["loaded"] = True
        try:
            p = _kbl_path()
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d.get("items"), dict):
                _KBL["items"] = d["items"]
                _KBL["cloud_ts"] = float(d.get("cloud_ts") or 0)
                _KBL["synced"] = float(d.get("synced") or 0)
                _KBL["resume"] = dict(d.get("resume") or {})
                _KBL["bytes"] = os.path.getsize(p)
                _kbl_build()
        except Exception:
            pass


def kbl_find(query, k=1):
    """Поиск по локальному JSON. -> [(score, id)] лучшие первыми; только
    уверенные совпадения (≥60 % веса слов вопроса, при ≥2 словах — минимум
    два совпавших)."""
    kbl_load()
    with _KBL_LOCK:
        n = len(_KBL["items"])
        q = list(dict.fromkeys(_kbl_stems(query)))
        if not n or not q:
            return []
        idf = {st: math.log(1.0 + n / (1.0 + len(_KBL["idx"].get(st, ()))))
               for st in q}
        tot = sum(idf.values()) or 1.0
        cand = set()
        for st in q:
            cand |= _KBL["idx"].get(st, set())
        res = []
        for aid in cand:
            ts_, xs_ = _KBL["tsets"][aid], _KBL["xsets"][aid]
            hit = [st for st in q if st in ts_ or st in xs_]
            w = sum(idf[st] for st in hit)
            if w / tot < 0.6 or (len(q) >= 2 and len(hit) < 2):
                continue
            w += sum(0.5 * idf[st] for st in q if st in ts_)
            res.append((w, aid))
        res.sort(reverse=True)
        out, seen = [], set()
        for w, cid in res:                      # лучший кусок каждой статьи
            a = (_KBL["items"].get(cid) or {}).get("a") or cid
            if a in seen:
                continue
            seen.add(a)
            out.append((w, cid))
            if len(out) >= k:
                break
        return out


def _kbl_join(a, b, maxov=400):
    """Склеить соседние куски без дубля нахлёста."""
    for n in range(min(maxov, len(a), len(b)), 20, -1):
        if a[-n:] == b[:n]:
            return a + b[n:]
    return a + " " + b


def kbl_context(cid, before=1, after=1, budget=4000):
    """Кусок + соседние (контекст) + «начало статьи», если кусок не первый.
    -> (title, text)."""
    it = _KBL["items"].get(cid) or {}
    lst = _KBL.get("by_a", {}).get(it.get("a") or cid) or [(0, cid)]
    pos = [j for j, (_i, c) in enumerate(lst) if c == cid]
    j = pos[0] if pos else 0
    lo, hi = max(0, j - before), min(len(lst), j + after + 1)
    text = ""
    for _i, c in lst[lo:hi]:
        x = str((_KBL["items"].get(c) or {}).get("x") or "")
        text = _kbl_join(text, x) if text else x
    head = ""
    if lo > 0:
        first = _KBL["items"].get(lst[0][1]) or {}
        head = str(first.get("l") or "")
    if it.get("h"):
        head = (head + " " if head else "") + "[раздел: %s]" % it["h"]
    out = (head + "\n" + text).strip() if head else text
    return str(it.get("t") or "?"), out[:budget]


def _kbl_snippet(text, query, size=500):
    q = set(_kbl_stems(query))
    best, bs = "", -1
    for sn in re.split(r"(?<=[.!?])\s+", str(text)):
        sn = sn.strip()
        if len(sn) < 20:
            continue
        sc = len(q & set(_kbl_stems(sn)))
        if sc > bs:
            best, bs = sn, sc
    return (best or str(text))[:size]


def kbl_lookup(query):
    """-> (title, snippet) | None — из локального JSON, без сети."""
    r = kbl_find(query, 1)
    if not r:
        return None
    it = _KBL["items"].get(r[0][1]) or {}
    _KBL["hits"] += 1
    return str(it.get("t") or ""), _kbl_snippet(it.get("x", ""), query)


def kbl_read(query):
    """-> (title, text) | (None, "") — статья целиком из локального JSON."""
    r = kbl_find(query, 1)
    if not r:
        return None, ""
    _KBL["hits"] += 1
    return kbl_context(r[0][1])


def _kbl_save():
    d = {"v": 2, "cloud_ts": _KBL["cloud_ts"], "synced": _KBL["synced"],
         "resume": _KBL.get("resume") or {}, "items": _KBL["items"]}
    p = _kbl_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, p)
    _KBL["bytes"] = os.path.getsize(p)


def kbl_sync(full=False):
    """Слить пачки базы знаний облака в локальный JSON (фон).
    v2.56: full=True (кнопка «Обучить мозг» в облаке) — всё заново (since=0),
    а не только изменённое; большая база тянется частями: если пачек больше,
    чем успели за раз, место запоминается и следующий запуск продолжает
    (раньше «облачное время» сдвигалось, и остаток не приходил НИКОГДА)."""
    url, token = _cloud_base()
    if not url:
        return False
    kbl_load()
    import urllib.parse
    rs = _KBL.get("resume") or {}
    if full:
        since, offset, started = 0.0, 0, time.time()
    elif rs:
        since, offset = float(rs.get("since") or 0), int(rs.get("off") or 0)
        started = float(rs.get("started") or time.time())
    else:
        since, offset, started = _KBL["cloud_ts"], 0, time.time()
    got, ids = {}, None
    done_all = False
    t_run = time.time()
    try:
        for _page in range(400):
            if time.time() - t_run > 600:
                break                       # 10 мин за раз — остальное позже
            u = (url + "/api/kb/pack?since=%s&offset=%d&limit=15&device=%s"
                 % (since, offset, urllib.parse.quote(_hostname())))
            r, err = _http_get_json_err(u, token=token, timeout=30)
            if not r or not r.get("ok"):
                _KBL["err"] = err or "облако не ответило"
                return False
            if r.get("ids") is not None:
                ids = set(r["ids"])
            for it in r.get("items") or []:
                if it.get("id"):
                    got[it["id"]] = {"a": it.get("a") or it["id"],
                                     "i": int(it.get("i") or 0),
                                     "n": int(it.get("n") or 1),
                                     "t": it.get("t", ""), "h": it.get("h", ""),
                                     "l": it.get("l", ""), "x": it.get("x", ""),
                                     "k": it.get("k") or [],
                                     "s": it.get("s", ""),
                                     "ts": it.get("ts", 0)}
            if r.get("next") is None:
                done_all = True
                break
            offset = int(r["next"])
        with _KBL_LOCK:
            fresh = {v["a"] for v in got.values()}   # статья пришла заново —
            for cid in [c for c, v in _KBL["items"].items()   # старые куски прочь
                        if (v.get("a") or c) in fresh]:
                _KBL["items"].pop(cid, None)
            _KBL["items"].update(got)
            if ids is not None:                       # удалённые в облаке
                for aid in [a for a in _KBL["items"] if a not in ids]:
                    _KBL["items"].pop(aid, None)
            if done_all:
                _KBL["cloud_ts"] = started - 1.0
                _KBL["resume"] = {}
            else:                             # не всё — продолжим с этого места
                _KBL["resume"] = {"since": since, "off": offset,
                                  "started": started}
            _KBL["synced"] = time.time()
            _KBL["err"] = ""
            _kbl_build()
            _kbl_save()
        if got or full:
            log("KB", "локальная база на плате: +%d кусков, всего %d кусков (%d статей), %d КБ%s"
                % (len(got), len(_KBL["items"]), len(_KBL["by_a"]),
                   _KBL["bytes"] // 1024,
                   "" if done_all else " — продолжу дальше"))
        return True
    except Exception as e:
        _KBL["err"] = "%s: %s" % (type(e).__name__, str(e)[:80])
        return False


_INSTR = {"h": None}


def instr_on(h, text):
    """v2.54: инструкция учителя из облака = мысли учителя. Изменилась —
    старые советы по темам сбрасываем, в панель «Учитель» кладём её текст."""
    h = str(h or "")
    if _INSTR["h"] is None:
        _INSTR["h"] = h
        if not h:
            return
    elif h == _INSTR["h"]:
        return
    _INSTR["h"] = h
    try:
        with _ADVICE_LOCK:
            _advice_save({})
    except Exception:
        pass
    if h:
        with SH.lock:
            SH.teacher_note = ("📝 инструкция учителя: %s" % str(text or ""))[:400]
            SH.mem_dirty = True
        log("УЧИТЕЛЬ", "получена инструкция из облака (%d симв.)" % len(str(text or "")))


_SYNCP = {"seq": None, "kb": 0.0, "train": 0.0, "last": 0.0, "n": 0,
          "why": ""}


def syncpoint_on(sy):
    """v2.53: облако сообщило, что в БД что-то записано / нажато «Обучить»:
    сразу тянем пачки базы знаний на плату и дообучаем тензорную сеть
    (не чаще раза в 30 с). Первый опрос только запоминает состояние."""
    if not isinstance(sy, dict) or "seq" not in sy:
        return
    seq = int(sy.get("seq") or 0)
    if _SYNCP["seq"] is None:
        _SYNCP.update(seq=seq, kb=float(sy.get("kb_ts") or 0),
                      train=float(sy.get("train_ts") or 0))
        return
    if seq == _SYNCP["seq"]:
        return
    kb = float(sy.get("kb_ts") or 0)
    tr = float(sy.get("train_ts") or 0)
    now = time.time()
    if not (kb > _SYNCP["kb"] or tr > _SYNCP["train"]):
        _SYNCP["seq"] = seq
        return
    if now - _SYNCP["last"] < 30.0:
        return                           # слишком часто — сработаем позже
    full = tr > _SYNCP["train"]          # v2.56: кнопка «Обучить» — всё заново
    _SYNCP.update(seq=seq, kb=kb, train=tr, why=str(sy.get("why") or ""))
    _SYNCP["last"] = now
    _SYNCP["n"] += 1
    log("СИНХРОН", "облако: %s — тяну базу и дообучаю сеть" % (_SYNCP["why"] or "запись в БД"))
    if full:
        _KBL["want_full"] = True             # занята — kbl_tick подхватит
    if not _KBL["busy"]:
        _KBL["busy"] = True

        def run():
            try:
                kbl_sync(full=bool(_KBL.pop("want_full", False)))
            finally:
                _KBL["busy"] = False
        threading.Thread(target=run, daemon=True, name="kbl-push").start()
    try:
        tn_train_now("дообучение")
    except Exception:
        pass


def kbl_tick():
    """Из mem_sync_tick (раз в 0,5 с): пора ли синхронизироваться."""
    now = time.time()
    if _KBL["busy"]:
        return
    if _KBL["last_try"] == 0.0 and now - _KBL["boot"] < KBL_FIRST:
        return
    pend = _KBL.get("want_full") or _KBL.get("resume")   # v2.56: не ждать часами
    if now - _KBL["last_try"] < (60.0 if pend else
                                 (KBL_PERIOD if _KBL["synced"] else 120.0)):
        return
    if not _cloud_base()[0]:
        return
    _KBL["last_try"] = now
    _KBL["busy"] = True

    def run():
        try:
            kbl_sync(full=bool(_KBL.pop("want_full", False)))
        finally:
            _KBL["busy"] = False
    threading.Thread(target=run, daemon=True, name="kbl-sync").start()


def kbl_status():
    return {"articles": len(_KBL.get("by_a") or {}) or len(_KBL["items"]),
            "chunks": len(_KBL["items"]), "kb": _KBL["bytes"] // 1024,
            "synced": _KBL["synced"], "hits": _KBL["hits"],
            "sync_signals": _SYNCP["n"],
            "resume": bool(_KBL.get("resume")),
            "err": _KBL["err"], "file": _kbl_path()}


# ===========================================================================
# [3k] САМОПРОГРАММИРОВАНИЕ КИРПИЧИКАМИ И ЛЮБОПЫТСТВО (v2.31)
#      Приложение App Lab = стопка кирпичиков. В базе примеров кода лежат
#      РЕЦЕПТЫ: готовые последовательности операторов с ключами-темами.
#      Мозг сам выбирает рецепт под вопрос, собирает приложение и
#      сохраняет его в облако (POST /api/applab/save, X-Mozg-Token —
#      платформенный режим принимает токен платы). Рецепты скачиваются
#      из облака (GET /api/applab/recipes) и сливаются со встроенными.
# ===========================================================================
BRICK_RECIPES = [
    {"id": "rcp_board_report", "name": "Отчёт по плате",
     "keys": ("плат", "отчёт", "состояни", "статус плат", "как плата"),
     "bricks": [
         {"type": "board.state", "params": {}, "var": "состояние"},
         {"type": "board.say", "params": {}, "var": "мысль"},
         {"type": "logic.template",
          "params": {"template": "Отчёт по плате\n{состояние}\n"
                                 "Мысль: {мысль}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_ask_remember", "name": "Спросить плату и запомнить",
     "keys": ("спроси плат", "спросить плат", "запомни ответ",
              "плата ответил", "расспроси"),
     "bricks": [
         {"type": "board.ask", "params": {"question": "{question}"},
          "var": "ответ"},
         {"type": "mem.remember", "params": {"text": "Плата: {ответ}"},
          "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_kb_answer", "name": "Ответ из базы знаний",
     "keys": ("баз", "знан", "статья", "kb", "учебник", "документ"),
     "bricks": [
         {"type": "know.kb", "params": {"query": "{question}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_wiki", "name": "Справка из Википедии",
     "keys": ("википед", "вики", "энциклопед", "справк", "кто такой",
              "что такое"),
     "bricks": [
         {"type": "know.wiki", "params": {"topic": "{question}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_think_note", "name": "Подумать и записать",
     "keys": ("подумай", "мысл", "идея", "размышля", "порассужда"),
     "bricks": [
         {"type": "brain.thought", "params": {"topic": "{question}"},
          "var": "размышление"},
         {"type": "mem.remember",
          "params": {"text": "Мысль: {размышление}"}, "var": ""},
         {"type": "board.thought", "params": {"text": "{размышление}"},
          "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_memory_quiz", "name": "Что помню про…",
     "keys": ("что помнишь", "помнишь", "вспомни", "памят", "найди в памяти"),
     "bricks": [
         {"type": "mem.recall", "params": {"topic": "{question}"},
          "var": "заметки"},
         {"type": "logic.template",
          "params": {"template": "Из памяти:\n{заметки}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
]

_RECIPES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "dicts", "brick_recipes.json")


def _recipes_load():
    """База рецептов: скачанные из облака (dicts/brick_recipes.json) +
    встроенные (скачанные главнее). Список словарей."""
    out = list(BRICK_RECIPES)
    try:
        with open(_RECIPES_PATH, "r", encoding="utf-8") as f:
            extra = json.loads(f.read() or "[]")
        if isinstance(extra, list):
            known = {r["id"] for r in out}
            for r in extra:
                if isinstance(r, dict) and r.get("id") and r.get("bricks"):
                    if r["id"] in known:
                        out = [r if x["id"] == r["id"] else x for x in out]
                    else:
                        out.append(r)
    except Exception:
        pass
    return out


def _recipes_sync():
    """Скачать рецепты из облака (GET /api/applab/recipes?full=1) и слить
    со встроенными: примеры кода пополняются владельцем без перепрошивки."""
    url, token = _cloud_base()
    if not url:
        return
    try:
        r, _e = _http_get_json_err(url + "/api/applab/recipes?full=1",
                                   token=token, timeout=12)
        recs = [x for x in ((r or {}).get("recipes") or [])
                if isinstance(x, dict) and x.get("id") and x.get("bricks")]
        if not recs:
            return
        old = {x["id"]: x for x in _recipes_load()}
        changed = False
        for x in recs:
            o = old.get(x["id"])
            if o is None or json.dumps(o, sort_keys=True,
                                       ensure_ascii=False) != \
                    json.dumps(x, sort_keys=True, ensure_ascii=False):
                changed = True
                break
        if not changed:
            return
        with open(_RECIPES_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(recs[:200], ensure_ascii=False))
        log("КИРПИЧИКИ", "база примеров кода обновлена из облака:",
            ", ".join(str(x.get("name"))[:24] for x in recs[:6]))
    except Exception as e:
        log("КИРПИЧИКИ", "синхронизация рецептов не вышла:", str(e)[:80])


def _sketch_react(kind="build"):
    """v2.31: СКЕТЧ РЕАГИРУЕТ на события мозга: короткий тон (RPC
    speak_tone) + слово в диктант буквогенератора (RPC word_push)."""
    try:
        with SH.lock:
            seen = SH.bridge_seen
        if not seen:
            return
        tones = {"build": "880", "curious": "660", "read": "520",
             "math": "990"}
        _bridge_call("speak_tone", tones.get(kind, "700"), wait=False)
        w = _dict_sample_word()
        if w:
            _bridge_call("word_push", w, wait=False)
    except Exception:
        pass


def bricks_build(question, run=False):
    """v2.31: мозг сам программирует: выбрать рецепт из базы примеров
    кода, собрать приложение из кирпичиков, сохранить в облако App Lab.
    run=True — сразу запустить (POST /api/applab/run). Возврат: текст."""
    q = (question or "").strip()
    recs = _recipes_load()
    low = q.lower()
    best, hits = None, 0
    for r in recs:
        n = sum(1 for k in (r.get("keys") or [])
                if isinstance(k, str) and k in low)
        if n > hits:
            best, hits = r, n
    if not best:
        names = "; ".join(str(x.get("name"))[:28] for x in recs[:8])
        return ("В примерах кода нет рецепта под это. Знакомые темы: %s. "
                "Новые рецепты приходят из облака сами." % names)
    bricks = []
    for b in (best.get("bricks") or []):
        params = {}
        for k, v in (b.get("params") or {}).items():
            params[k] = str(v).replace("{question}", q[:200])
        bricks.append({"type": b.get("type"), "params": params,
                       "var": b.get("var", "")})
    url, token = _cloud_base()
    if not url:
        return ("Рецепт «%s» нашла (%d кирпичиков), но облако не задано — "
                "собери его в панели App Lab или задай облако в Настройках."
                % (str(best.get("name")), len(bricks)))
    err = ""
    rid = ""
    try:
        resp, _e = _http_json_err(url + "/api/applab/save",
                                  {"name": str(best.get("name")),
                                   "bricks": bricks},
                                  token=token, timeout=20)
        if (resp or {}).get("ok"):
            rid = str(((resp or {}).get("app") or {}).get("id") or "")
        else:
            err = str((resp or {}).get("error") or _e or "не сохранено")
    except Exception as e:
        rid, err = "", repr(e)[:90]
    if err:
        return ("Рецепт «%s» нашла, но облако не приняло приложение: %s"
                % (str(best.get("name")), err))
    out = ("Собрала приложение «%s» из %d кирпичиков по рецепту из "
           "примеров кода — оно уже в App Lab облака."
           % (str(best.get("name")), len(bricks)))
    if run and rid:
        try:
            resp, _e = _http_json_err(url + "/api/applab/run", {"id": rid},
                                      token=token, timeout=90)
            res = str((resp or {}).get("result") or "").strip()
            if res:
                out += " Запуск: " + res[:400]
        except Exception:
            pass
    _sketch_react("build")
    try:
        _plat_think("Собрала приложение «%s» из кирпичиков (рецепт %s)"
                    % (str(best.get("name"))[:40], str(best.get("id"))))
    except Exception:
        pass
    return out


def _curiosity_search(topic, share=False):
    """v2.31: ЛЮБОПЫТСТВО: поиск в интернете по своей мысли или вопросу
    (Википедия через облако, POST /api/web/fetch). Выжимки падают в
    нейросеть материалов, память и ленту мыслей. Возврат: текст."""
    url, token = _cloud_base()
    q = (topic or "").strip()
    if not (url and q):
        return ("Любопытство: не задано облако или тема пуста — искать "
                "не в чем. Задай облако в Настройках или спроси иначе.")
    try:
        r, _e = _http_json_err(url + "/api/web/fetch", {"q": q[:120]},
                               token=token, timeout=25)
        res = (r or {}).get("results") or []
    except Exception as e:
        return "Любопытство: поиск не вышел (%s)." % type(e).__name__
    if not res:
        _t3, _x3 = brain_read(q, why="любопытство")          # v2.91
        if _t3:
            return ("Любопытство по «%s»: прочитала статью «%s»: %s"
                    % (q[:60], _t3[:70], re.sub(r"\s+", " ", _x3[:600])))[:1200]
        read_miss(q)
        return "Любопытство: по «%s» в интернете тишина." % q[:60]
    hits = ["«%s» — %s" % (str(h.get("title") or "?")[:70],
                           str(h.get("snippet") or "")[:220])
            for h in res[:2]]
    text = "Любопытство по «%s»: %s" % (q[:60], "; ".join(hits))
    try:
        mn_feed("Википедия: %s" % "; ".join(hits), title=q[:60],
                src="любопытство")
    except Exception:
        pass
    _sketch_react("curious")
    if share:
        try:
            _plat_think("Любопытствую: узнала про «%s»: %s…"
                        % (q[:40], str(res[0].get("title") or "?")[:60]))
        except Exception:
            pass
    return text[:1200]


def _plat_last_own_topic():
    """v2.31: тема из СОБСТВЕННЫХ мыслей мозга — для любопытства."""
    try:
        with SH.lock:
            for m in reversed(SH.memory):
                if m.get("role") != "user" and m.get("text"):
                    t = str(m["text"]).strip()
                    if len(t) > 12:
                        return t[:120]
    except Exception:
        pass
    return ""


# ===========================================================================
# [3l] МАТЕМАТИКА: ФОРМУЛЫ И МОДЕЛИ (v2.32) — мозг сам ПИШЕТ ФОРМУЛЫ и
#      СТРОИТ МОДЕЛИ, без LLM:
#        MATH_FORMULAS — встроенная база формул; облачные пополняются
#        сами (GET /api/math/formulas?full=1 -> dicts/math_formulas.json,
#        _maths_sync — как рецепты). У формулы: id, имя, ключи-темы,
#        переменные (vkeys — по каким словам искать числа) и ВЫРАЖЕНИЕ.
#        _mm_eval — безопасный вычислитель: AST-обход с белым списком
#        узлов и функций, произвольный код НЕ исполняется; ошибки —
#        честные («неизвестная переменная», «деление на ноль»).
#        math_reply — живая зона «Математика»: вопрос -> формула -> числа
#        из вопроса -> расчёт с формулой и подстановкой; «модель по
#        данным 1,2; 2,4; 3,5 — прогноз 4» -> линейная/квадратичная/
#        экспоненциальная модель (выбор по R², против переобучения:
#        квадратичной нужно >= 4 точек) + прогноз; слабая модель честно
#        названа слабой. Плюс простая арифметика («посчитай 12*8»).
#        Скетч реагирует: тон 990 Гц + слово в диктант (_sketch_react).
# ===========================================================================
MATH_FORMULAS = [
    {"id": "circle_area", "name": "Площадь круга",
     "keys": ("площадь круг", "круг радиус", "круга радиус"),
     "expr": "pi * r ** 2", "text": "S = π·r²", "doc": "площадь круга",
     "vars": [{"name": "r", "label": "радиус",
               "vkeys": ("радиус", "радиусом", "радиуса")}]},
    {"id": "circle_len", "name": "Длина окружности",
     "keys": ("длина окружност", "окружност", "обхват"),
     "expr": "2 * pi * r", "text": "C = 2·π·r", "doc": "длина окружности",
     "vars": [{"name": "r", "label": "радиус",
               "vkeys": ("радиус", "радиусом", "радиуса")}]},
    {"id": "rect_area", "name": "Площадь прямоугольника",
     "keys": ("площадь прямоугольник", "площадь стены", "площадь пола"),
     "expr": "a * b", "text": "S = a·b", "doc": "сторона на сторону",
     "vars": [{"name": "a", "label": "ширина", "vkeys": ("ширин", "длин")},
              {"name": "b", "label": "длина", "vkeys": ("длин", "высот")}]},
    {"id": "speed_dist", "name": "Путь, скорость и время",
     "keys": ("скорост", "путь", "расстояни", "проеха", "пройде"),
     "expr": "v * t", "text": "s = v·t",
     "doc": "расстояние = скорость · время",
     "vars": [{"name": "v", "label": "скорость",
               "vkeys": ("скорост", "км/ч")},
              {"name": "t", "label": "время",
               "vkeys": ("времен", "час", "часа", "часов", "мин")}]},
    {"id": "ohm", "name": "Закон Ома",
     "keys": ("ома", "сопротивлен", "напряжен", "ток цеп"),
     "expr": "u / r", "text": "I = U/R",
     "doc": "ток = напряжение / сопротивление",
     "vars": [{"name": "u", "label": "напряжение",
               "vkeys": ("напряжен", "вольт")},
              {"name": "r", "label": "сопротивление",
               "vkeys": ("сопротивлен", "ом")}]},
    {"id": "percent_of", "name": "Процент от числа",
     "keys": ("процент от", "процентов от", "% от", "скидк", "налог"),
     "expr": "x * p / 100", "text": "Y = X·p/100", "doc": "p процентов от X",
     "vars": [{"name": "p", "label": "сколько процентов",
               "vkeys": ("процент", "%")},
              {"name": "x", "label": "число",
               "vkeys": ("от", "число", "сумм")}]},
    {"id": "pythagoras", "name": "Теорема Пифагора",
     "keys": ("пифагор", "гипотенуз", "катет"),
     "expr": "sqrt(a ** 2 + b ** 2)", "text": "c = √(a² + b²)",
     "doc": "гипотенуза по двум катетам",
     "vars": [{"name": "a", "label": "катет a",
               "vkeys": ("катет a", "катет ")},
              {"name": "b", "label": "катет b", "vkeys": ("катет b",)}]},
    {"id": "exp_growth", "name": "Рост на проценты (вклад)",
     "keys": ("вклад", "сложн процент", "процентов на", "под ",
              "размнож", "вырастет через"),
     "expr": "x0 * (1 + p / 100) ** n", "text": "X = X₀·(1+p/100)ⁿ",
     "doc": "экспоненциальный рост на p процентов за n шагов",
     "vars": [{"name": "x0", "label": "начальная сумма",
               "vkeys": ("вклад", "начальн", "было")},
              {"name": "p", "label": "процентов в шаг", "vkeys": ("под",)},
              {"name": "n", "label": "число шагов",
               "vkeys": ("лет", "год", "месяц", "шаг", "дней")}]},
    {"id": "celsius_fahr", "name": "Цельсий в Фаренгейт",
     "keys": ("фаренгейт", "цельси"), "expr": "c * 9 / 5 + 32",
     "text": "F = C·9/5 + 32", "doc": "перевод температуры",
     "vars": [{"name": "c", "label": "градусы Цельсия",
               "vkeys": ("цельси", "градус")}]},
]

_MATH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dicts", "math_formulas.json")
_MM_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_MM_PAIR2_RE = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$")
_MM_ARITH_RE = re.compile(r"-?\d+(?:[.,]\d+)?(?:\s*[-+*/%]|\s*\*\*)"
                          r"\s*-?\d+(?:\s*[-+*/%]|\s*\*\*\s*-?\d+)*")
_MM_MODEL_W = ("модель", "прогноз", "тренд", "регресс", "аппроксим")


def _maths_load():
    """База формул: встроенная + скачанные из облака
    (dicts/math_formulas.json, облачные главнее по id)."""
    out = list(MATH_FORMULAS)
    try:
        with open(_MATH_PATH, "r", encoding="utf-8") as f:
            extra = json.loads(f.read() or "[]")
        if isinstance(extra, list):
            known = {x["id"] for x in out}
            for x in extra:
                if isinstance(x, dict) and x.get("id") and x.get("expr") \
                        and x.get("vars"):
                    if x["id"] in known:
                        out = [x if y["id"] == x["id"] else y for y in out]
                    else:
                        out.append(x)
    except Exception:
        pass
    return out


def _maths_sync():
    """Скачать формулы из облака (GET /api/math/formulas?full=1) и слить
    со встроенными: база растёт без перепрошивки платы."""
    url, token = _cloud_base()
    if not url:
        return
    try:
        r, _e = _http_get_json_err(url + "/api/math/formulas?full=1",
                                   token=token, timeout=12)
        fms = [x for x in ((r or {}).get("formulas") or [])
               if isinstance(x, dict) and x.get("id") and x.get("expr")
               and x.get("vars")]
        if not fms:
            return
        old = {x["id"]: x for x in _maths_load()}
        changed = False
        for x in fms:
            o = old.get(x["id"])
            if o is None or json.dumps(o, sort_keys=True,
                                       ensure_ascii=False) != \
                    json.dumps(x, sort_keys=True, ensure_ascii=False):
                changed = True
                break
        if not changed:
            return
        with open(_MATH_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(fms[:300], ensure_ascii=False))
        log("МАТЕМАТИКА", "база формул обновлена из облака:",
            ", ".join(str(x.get("name"))[:24] for x in fms[:6]))
    except Exception as e:
        log("МАТЕМАТИКА", "синхронизация формул не вышла:", str(e)[:80])


def _mm_eval(expr, env=None):
    """Безопасный вычислитель формул: AST + белый список. Никакого
    exec: произвольный код не исполняется, всё чужое — честная ошибка."""
    import ast
    import math
    consts = {"pi": math.pi, "π": math.pi, "e": math.e}
    f1 = {"sqrt": math.sqrt, "abs": abs, "log": math.log, "ln": math.log,
          "log10": math.log10, "exp": math.exp, "sin": math.sin,
          "cos": math.cos, "round": round}
    f2 = {"min": min, "max": max, "pow": pow}
    ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
           ast.Pow: lambda a, b: a ** b}
    s = str(expr or "").strip()
    s = s.replace("х", "x").replace("×", "*").replace("·", "*") \
         .replace("÷", "/").replace("^", "**").replace(",", ".")
    if not s:
        raise ValueError("пустое выражение")
    scope = dict(consts)
    if env:
        scope.update(env)
    try:
        tree = ast.parse(s, mode="eval")
    except Exception as e:
        raise ValueError("выражение не разобрать: %s" % str(e)[:50])

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 200:
                raise ValueError("слишком большая степень")
            try:
                return ops[type(n.op)](a, b)
            except ZeroDivisionError:
                raise ValueError("деление на ноль")
            except OverflowError:
                raise ValueError("слишком большое число")
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd,
                                                            ast.USub)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Name):
            if n.id in scope:
                return scope[n.id]
            raise ValueError("неизвестная переменная «%s»" % n.id)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and not n.keywords:
            fname = n.func.id
            args = [ev(a) for a in n.args]
            if fname in f1 and len(args) == 1:
                try:
                    return f1[fname](args[0])
                except (ValueError, OverflowError):
                    raise ValueError("функция %s не считает это число"
                                     % fname)
            if fname in f2 and len(args) == 2:
                return f2[fname](*args)
            raise ValueError("нет функции «%s»" % fname)
        raise ValueError("в формуле лишнее (%s)" % type(n).__name__)

    r = ev(tree)
    if isinstance(r, complex):
        raise ValueError("корень из отрицательного числа")
    return r


def _mm_num(v):
    """Красивое число для ответа: 78.5398 -> 78.5398, 120.0 -> 120."""
    if isinstance(v, int):
        return str(v)
    if abs(v) >= 1e10 or (v != 0 and abs(v) < 1e-6):
        return ("%.3e" % v).replace("e", "·10^")
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return s if s else "0"


def _math_pick(text):
    """Выбрать формулу по ключам-темам: (формула|None, попаданий)."""
    low = (text or "").lower()
    best, hits = None, 0
    for f in _maths_load():
        n = sum(1 for k in (f.get("keys") or []) if k in low)
        if n > hits:
            best, hits = f, n
    return best, hits


def _math_bind(f, text):
    """Значения переменных: «ключ число» и «имя=число» главнее, остальным
    — числа по порядку. Возврат (values, missing)."""
    low = (text or "").lower()
    nums = [float(x.replace(",", ".")) for x in _MM_NUM_RE.findall(low)]
    values, used = {}, set()
    for v in (f.get("vars") or []):
        nm = str(v.get("name") or "")
        m = re.search(r"\b%s\s*[=:]\s*(-?\d+(?:[.,]\d+)?)" % re.escape(nm),
                      low)
        if m:
            values[nm] = float(m.group(1).replace(",", "."))
        for k in (v.get("vkeys") or ()):
            mm = re.search(re.escape(str(k))
                           + r"[а-яё]{0,4}\s*(-?\d+(?:[.,]\d+)?)", low)
            if mm and nm not in values:
                values[nm] = float(mm.group(1).replace(",", "."))
                used.add(len(_MM_NUM_RE.findall(low[:mm.start(1)])))
    queue = [i for i in range(len(nums)) if i not in used]
    for v in (f.get("vars") or []):
        nm = str(v.get("name") or "")
        if nm in values:
            continue
        values[nm] = nums[queue.pop(0)] if queue else None
    missing = [str(v.get("name")) for v in (f.get("vars") or [])
               if values.get(str(v.get("name"))) is None]
    return values, missing


def _math_formula_reply(text):
    """Вопрос -> формула -> числа -> расчёт -> честный текст."""
    f, hits = _math_pick(text)
    if not f or not hits:
        names = "; ".join(str(x.get("name"))[:30]
                          for x in _maths_load()[:6])
        return ("В базе формул под это темы нет (%d знакомых формул: %s…). "
                "Скажи, например: «площадь круга радиусом 5»."
                % (len(_maths_load()), names))
    values, missing = _math_bind(f, text)
    if missing:
        labels = ", ".join(str(v.get("label")) for v in f["vars"]
                           if str(v.get("name")) in missing)
        return ("Формула нашлась: %s (%s). Нужны значения: %s — назови их "
                "числами в вопросе, и я посчитаю."
                % (f.get("name"), f.get("text"), labels))
    if str(f.get("id")) == "average":
        nums = [float(x.replace(",", "."))
                for x in _MM_NUM_RE.findall((text or "").lower())]
        if len(nums) < 2:
            return "Для среднего нужно хотя бы два числа — перечисли их."
        val = sum(nums) / len(nums)
        _sketch_react("math")
        return ("Среднее арифметическое: M = (%s)/%d = %s"
                % (" + ".join(_mm_num(x) for x in nums),
                   len(nums), _mm_num(val)))
    try:
        val = _mm_eval(f["expr"], values)
    except Exception as e:
        return "Формула %s не посчиталась: %s" % (f.get("text"), e)
    subs = ", ".join("%s=%s" % (str(v.get("name")),
                                _mm_num(values[str(v.get("name"))]))
                     for v in f["vars"])
    _sketch_react("math")
    return ("%s: %s. Подставила %s -> %s = %s"
            % (f.get("name"), f.get("doc"), subs, f.get("text"),
               _mm_num(val)))


def _mm_parse_pairs(text):
    """Пары x,y: «1,2; 2,4» (запятая — разделитель пары), «1 2; 2 4» или
    плоский ряд «1 2 2 4». Возврат (pairs, error)."""
    s = str(text or "").strip()
    if not s:
        return [], "данных нет — нужны пары x,y через точку с запятой"
    pairs = []
    for c in [c for c in re.split(r"[;\n]", s) if c.strip()]:
        m2 = _MM_PAIR2_RE.match(c)
        if m2:
            pairs.append((float(m2.group(1)), float(m2.group(2))))
            continue
        ns = [float(x.replace(",", ".")) for x in _MM_NUM_RE.findall(c)]
        if len(ns) == 2:
            pairs.append((ns[0], ns[1]))
        elif len(ns) > 2:
            for i in range(0, len(ns) - 1, 2):
                pairs.append((ns[i], ns[i + 1]))
            if len(ns) % 2 == 1:
                return [], "лишнее число в «%s» — пары неполные" \
                    % c.strip()[:30]
        elif ns:
            return [], ("в куске «%s» одно число — нужны два: x и y"
                        % c.strip()[:30])
    if len(pairs) < 2:
        return [], "мало данных: минимум 2 пары x,y"
    return pairs, ""


def _mm_r2(ys, pred):
    m = sum(ys) / len(ys)
    st = sum((y - m) ** 2 for y in ys)
    sr = sum((y - p) ** 2 for y, p in zip(ys, pred))
    if st == 0:
        return 1.0 if sr == 0 else 0.0
    return max(0.0, 1.0 - sr / st)


def _mm_lin(xs, ys):
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return None
    a = sxy / sxx
    b = my - a * mx
    return {"a": a, "b": b, "r2": _mm_r2(ys, [a * x + b for x in xs]),
            "pred": lambda x: a * x + b,
            "ftext": "y = %s·x %s %s" % (_mm_num(a),
                                         "+" if b >= 0 else "−",
                                         _mm_num(abs(b)))}


def _mm_exp(xs, ys):
    if len(set(xs)) < 3 or any(y <= 0 for y in ys):
        return None
    import math
    lny = [math.log(y) for y in ys]
    fit = _mm_lin(xs, lny)
    if not fit:
        return None
    b = fit["a"]
    a = math.exp(fit["b"])
    return {"r2": _mm_r2(ys, [a * math.exp(b * x) for x in xs]),
            "pred": lambda x: a * math.exp(b * x),
            "ftext": "y = %s·e^(%s·x)" % (_mm_num(a), _mm_num(b))}


def _mm_quad(xs, ys):
    """y = a·x² + b·x + c: нормальные уравнения 3x3, правило Крамера."""
    if len(set(xs)) < 4:
        return None
    n = float(len(xs))
    sx = sum(xs); sx2 = sum(x * x for x in xs)
    sx3 = sum(x ** 3 for x in xs); sx4 = sum(x ** 4 for x in xs)
    sy = sum(ys); sxy = sum(x * y for x, y in zip(xs, ys))
    sx2y = sum(x * x * y for x, y in zip(xs, ys))
    A = [[sx4, sx3, sx2], [sx3, sx2, sx], [sx2, sx, n]]
    B = [sx2y, sxy, sy]

    def d3(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    D = d3(A)
    if abs(D) < 1e-12:
        return None
    a = d3([[B[0], A[0][1], A[0][2]], [B[1], A[1][1], A[1][2]],
            [B[2], A[2][1], A[2][2]]]) / D
    b = d3([[A[0][0], B[0], A[0][2]], [A[1][0], B[1], A[1][2]],
            [A[2][0], B[2], A[2][2]]]) / D
    c = d3([[A[0][0], A[0][1], B[0]], [A[1][0], A[1][1], B[1]],
            [A[2][0], A[2][1], B[2]]]) / D
    pred = [a * x * x + b * x + c for x in xs]
    return {"r2": _mm_r2(ys, pred),
            "pred": lambda x: a * x * x + b * x + c,
            "ftext": "y = %s·x² %s %s·x %s %s" % (
                _mm_num(a), "+" if b >= 0 else "−", _mm_num(abs(b)),
                "+" if c >= 0 else "−", _mm_num(abs(c)))}


def _math_model_reply(text):
    """Вопрос с данными -> пары -> модель (лучший R², без переобучения)
    -> прогноз -> честный текст."""
    low = (text or "").lower()
    fc = None
    mfc = re.search(r"(?:—|-|–)?\s*(?:прогноз|предсказ|предскажи)\s*"
                    r"(?:для|при)?\s*(?:x\s*[=:—-]?\s*)?"
                    r"(-?\d+(?:[.,]\d+)?)", low)
    if mfc:
        fc = mfc.group(1).replace(",", ".")
        low = low[:mfc.start()] + low[mfc.end():]
    m = re.search(r"(?:по\s+данным|данные|точк[аам])\s*(.*)$", low, re.S)
    pairs, err = _mm_parse_pairs(m.group(1) if m else low)
    if err:
        return ("Модель не построилась: %s. Формат: «модель по данным "
                "1,2; 2,4; 3,5 — прогноз 4»." % err)
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    cands = []
    lin = _mm_lin(xs, ys)
    if lin:
        cands.append(lin)
    # против переобучения: квадратичной нужно >= 4 точек, экспоненте
    # >= 3 (на «тонких» данных они всегда «идеальны» — это обман)
    q = _mm_quad(xs, ys) if len(pairs) >= 4 else None
    e = _mm_exp(xs, ys) if len(pairs) >= 3 else None
    for c in (q, e):
        if c:
            cands.append(c)
    if not cands:
        return "Модель не строится: точки совпадают по x или данных мало."
    best = max(cands, key=lambda c: c["r2"])
    out = ("Модель: %s (R² = %s, точек: %d)"
           % (best["ftext"], _mm_num(best["r2"]), len(pairs)))
    if fc is not None:
        try:
            out += ". Прогноз при x=%s: %s" % (fc,
                                               _mm_num(best["pred"](
                                                   float(fc))))
        except (OverflowError, ValueError):
            out += ". Прогноз для x=%s не считается" % fc
    if best["r2"] < 0.5:
        out += (" Данные ложатся плохо (R² < 0.5) — доверять прогнозу "
                "нельзя.")
    elif best["r2"] < 0.8:
        out += " R² средний — модель ловит тренд, но с шумом."
    _sketch_react("math")
    return out


_MM_INTENT = re.compile(r"посчитай|подсчитай|вычисли|рассчитай|сколько\s+будет|"
                        r"чему\s+равн|\bминус\b|=\s*\??\s*$", re.I)


def _mm_dash_only(low, expr):
    """v2.40: дефис между числами в обычной фразе — диапазон/обозначение,
    а не минус: «Astra 1.7 - 1.8 обновление», «ГОСТ 34.602-2020»."""
    e = re.sub(r"^\s*-", "", expr)
    if "-" not in e or re.search(r"[+*/%]", e) or _MM_INTENT.search(low):
        return False
    rest = low.replace(expr, " ", 1)
    return bool(re.search(r"[a-zа-яё]{2,}", rest))


def math_reply(question):
    """Живая зона «Математика»: модель? формула? арифметика? — по порядку,
    всё честно и без LLM."""
    q = (question or "").strip()
    low = q.lower()
    has_data = len(_MM_NUM_RE.findall(low)) >= 3 or \
        re.search(r"по\s+данным|данные\s*[:\s]*\d", low)
    if any(w in low for w in _MM_MODEL_W) and has_data:
        return _math_model_reply(q)
    f, hits = _math_pick(q)
    if f and hits:
        return _math_formula_reply(q)
    m = _MM_ARITH_RE.search(low.replace("х", "*").replace("×", "*")
                            .replace("÷", "/"))
    if m and _mm_dash_only(low, m.group(0)):
        m = None          # v2.40: «версии 1.7 - 1.8», «в 2020 - 2021 годах»
    if m:
        try:
            val = _mm_eval(m.group(0))
            _sketch_react("math")
            return "%s = %s (посчитала сама, по шагам мозг-12)" % (
                m.group(0).replace("**", "^"), _mm_num(val))
        except Exception as e:
            return "Арифметика не посчиталась: %s" % e
    return _math_formula_reply(q)


# ===========================================================================
# [3j] СОВЕТЫ-НАСТАВЛЕНИЯ (v2.14): LLM пишет НЕ ответ, а СОВЕТ нейросети —
#      КАК отвечать (темы/ключевые слова/тон). Сеть применяет совет сама:
#      ключи усиливают нужные зоны, память и KB, а текст собирает
#      zone_reply/_long_reply. Совет хранится в ПАМЯТИ (advice.json +
#      brain_remember): на повторную тему LLM больше не зовётся — мозг
#      вспоминает прошлый совет (учим один раз, пользуемся всегда).
# ===========================================================================
ADVICE_TTL = 30 * 24 * 3600.0       # совет свеж 30 дней, потом обновим
ADVICE_CAP = 200                    # максимум советов в файле
_ADVICE_LOCK = threading.Lock()


def _advice_path():
    """Файл советов рядом с памятью (та же директория, что neuro12_memory)."""
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_advice.json")
    except Exception:
        return "neuro12_advice.json"


def _advice_load():
    try:
        with open(_advice_path(), "r", encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _advice_save(store):
    try:
        if len(store) > ADVICE_CAP:            # держим только свежие
            items = sorted(store.items(),
                           key=lambda kv: kv[1].get("ts", 0), reverse=True)
            store = dict(items[:ADVICE_CAP])
        tmp = _advice_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(store, ensure_ascii=False))
        os.replace(tmp, _advice_path())
    except Exception as e:
        log("СОВЕТ:", "не записать файл:", str(e)[:80])


_TOPIC_STOP = {"для", "при", "над", "под", "как", "что", "чем", "где",
               "или", "про", "это", "эта", "тут", "там", "ещё", "еще",
               "тоже", "чтоб", "чтобы", "меня", "тебя", "него", "ней",
               "мне", "тебе", "них", "все", "всё", "был", "была", "быть",
               "есть", "будет", "хочу", "можно", "нужно", "скажи"}


def _topic_key(text):
    """Ключ темы: 1-2 самых ЗНАЧИМЫХ слова (существительные важнее глаголов,
    словарные важнее неизвестных). Совет по теме всегда один и тот же — так
    память советов попадает в цель при повторных вопросах."""
    cands = [w for w in re.findall(r"[а-яёa-z0-9]{3,}", (text or "").lower())
             if w not in _TOPIC_STOP]
    if not cands:
        return ""
    try:
        nouns = set(SH.dict_pos.get("nouns") or [])
    except Exception:
        nouns = set()

    def score(w):
        if w in nouns:
            return 3
        if dict_has(w[:16]):
            return 2
        return 1
    cands.sort(key=lambda w: (-score(w), -len(w)))
    return "+".join(sorted(cands[:2]))[:80]


def advice_fetch(text):
    """Совет наставника для темы. Порядок: 1) память советов (LLM молчит);
    2) учитель /api/llm/teach purpose=advice (или direct). Совет кладётся
    в память (brain_remember) и панель «Учитель». v2.15: совет несёт
    «НЕЙРОНЫ: N» — наставник задаёт размер адаптивной сети [3k]; свежий
    совет сразу меняет число нейронов (nn_resize), из памяти — только
    вспоминается. Возврат: {"hint","keys":[...],"neurons":N,"fresh":bool}
    | None."""
    text = (text or "").strip()
    if not CFG.get("advice_on") or not text:
        return None
    key = _topic_key(text)
    if not key:
        return None
    with _ADVICE_LOCK:
        store = _advice_load()
        old = store.get(key)
    if isinstance(old, dict) and old.get("hint") and \
            time.time() - float(old.get("ts", 0)) < ADVICE_TTL:
        return {"hint": str(old.get("hint", ""))[:300],
                "keys": list(old.get("keys") or [])[:8],
                "neurons": int(old.get("neurons") or 0),
                "fresh": False}
    if not (((CFG.get("cloud_url") or "").strip() and
             (CFG.get("token") or "").strip()) or
            (CFG.get("direct_url") or "").strip()):
        return None                             # наставника нет — сеть сама
    prompt = (
        "Ты — наставник нейросети-мозга на плате Arduino. НЕ отвечай на "
        "сообщение пользователя. Дай КОРОТКИЙ совет, как мозгу построить "
        "ответ самому: о чём сказать, какие ключевые слова использовать, "
        "какой тон. И укажи, сколько нейронов зонной сети нужно под тему: "
        "болтовня/приветствие — меньше, сложный разбор с деталями — больше. "
        "Формат строго:\nСОВЕТ: <1-2 предложения-подсказка>\n"
        "КЛЮЧИ: <4-8 ключевых слов через запятую>\n"
        "НЕЙРОНЫ: <одно число от 6 до 48>. Никаких других слов.")
    msgs = [{"role": "system", "content": prompt},
            {"role": "user", "content": text[:800]}]
    out = None
    if (CFG.get("cloud_url") or "").strip() and (CFG.get("token") or "").strip():
        out, err = _cloud_teach("advice", msgs, max_tokens=220)
        if out:
            _set_llm_state("cloud", True)
        else:
            _set_llm_state("cloud", False, err)
    if not out and (CFG.get("direct_url") or "").strip():
        out, err = _direct_ask(msgs)
        if out:
            _set_llm_state("direct", True)
        else:
            _set_llm_state("direct", False, err)
    if not out:
        return None
    hint, keys, neurons = "", [], 0
    for line in str(out).splitlines():
        low = line.strip().lower()
        if low.startswith("совет:"):
            hint = line.strip()[6:].strip()[:300]
        elif low.startswith("ключи:"):
            keys = [w.strip(" .,-»") for w in line.strip()[6:].split(",")]
            keys = [k for k in keys if 2 <= len(k) <= 24][:8]
        elif low.startswith("нейроны:"):       # v2.15: размер сети от наставника
            m = re.search(r"\d{1,3}", line)
            if m:
                neurons = max(NN_MIN, min(NN_MAX, int(m.group(0))))
    if not hint:
        hint = str(out).strip().splitlines()[0][:300]
    if not hint:
        return None
    rec = {"hint": hint, "keys": keys, "neurons": neurons,
           "ts": time.time()}
    with _ADVICE_LOCK:
        store = _advice_load()
        store[key] = rec
        _advice_save(store)
    brain_remember("kb", "совет наставника по теме «%s»: %s"
                   % (key.replace("+", ", "), hint))
    try:
        with SH.lock:
            SH.teacher_note = ("💬 наставник (%s): %s"
                               % (key.replace("+", ", "), hint))[:300]
            SH.mem_dirty = True
    except Exception:
        pass
    log("СОВЕТ", "[%s] %s | ключи: %s | нейроны: %s" %
        (key, hint[:80], ",".join(keys), neurons or "—"))
    if neurons:
        try:                                   # v2.15: наставник меняет сеть
            nn_resize(neurons, "совет наставника по «%s»: %s" %
                      (key.replace("+", ", "), hint[:70]))
        except Exception as e:
            log("СЕТЬ:", str(e)[:80])
    return {"hint": hint, "keys": keys, "neurons": neurons, "fresh": True}


# ===========================================================================
# [3k] АДАПТИВНАЯ ЗОННАЯ СЕТЬ (v2.15): у мозга на плате — НАСТОЯЩИЙ слой
#      нейронов, и его РАЗМЕР задаёт наставник. Совет LLM теперь содержит
#      строку «НЕЙРОНЫ: <число 6..48>»: болтовня — сеть сжимается, сложная
#      тема — растёт (больше связей-ассоциаций). РОСТ — деление самого
#      опытного нейрона наименее населённой зоны (вес копируется + шум),
#      СОКРАЩЕНИЕ — отмирание самого слабого в самой населённой зоне.
#      Нейроны ГОЛОСУЮТ за зону ответа (когда ключевых слов из ZONES не
#      нашлось — сеть вспоминает, чему её учили), учатся у наставника
#      (ключи совета) и у вердиктов ±1 (хебб). Сеть живёт в
#      neuro12_net.json рядом с памятью — размер переживает перезапуск.
#      Число нейронов видно в UI (чип «сеть») и в /api/state (nn).
#      v2.17: нейроны ЖИВУТ — у каждого есть АКТИВАЦИЯ (0..1): их зажигает
#      всё, что они «слышат» (текст пользователя, фразы буквогенератора,
#      спайки 12 аппаратных нейронов, буквы, вердикты наставника) плюс
#      спонтанный шум; активация затухает со временем. Текстовый генератор
#      СЛУШААТ нейроны: слова подбираются по тому, какие нейроны сейчас
#      «говорят» (секция [3m]).
# ===========================================================================
NN_MIN = 6                    # ниже сети не сжать
NN_MAX = 48                   # выше сети не раздуть
NN_DEFAULT = 12               # старт: по нейрону на зону
NN_W_CAP = 40                 # максимум связей (признаков) у одного нейрона
NN_W_MAX = 3.0                # потолок веса одной связи
NN_COOL = 45.0                # сек между сменами размера (антидребезг)
NN_ADVICE_W = 0.08            # сколько веса даёт один ключ наставника
NN_HEB = 0.12                 # хебб-шаг за вердикт ±1
NN_PICK_MIN = 0.05            # минимальный голос сети (одна выученная связь)
_ZONES_D = dict(ZONES)        # имя зоны -> основы (для проверок принадлежности)
_NN_LOCK = threading.Lock()
_NN = {"n": 0, "next_id": 0, "neurons": [], "hist": [], "last": "",
       "ts": 0.0, "cool": NN_COOL}


def _nn_path():
    """Файл сети рядом с памятью (та же директория, что neuro12_advice.json)."""
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_net.json")
    except Exception:
        return "neuro12_net.json"


def _nn_new(zone, weights=None, noise=0.0):
    """Новый нейрон зоны. weights+noise — деление родителя (рост сети)."""
    w = {}
    if weights:
        for k, v in list(weights.items())[:NN_W_CAP]:
            try:
                w[str(k)[:16]] = round(min(NN_W_MAX, max(
                    -NN_W_MAX, float(v) + random.uniform(-noise, noise))), 3)
            except (TypeError, ValueError):
                pass
    n = {"id": _NN["next_id"], "zone": zone, "w": w, "r": 0,
         "born": time.time()}
    _NN["next_id"] += 1
    return n


def _nn_mass(n):
    """«Опыт» нейрона: сила связей + память наград (кем жертвовать/клонировать)."""
    try:
        return (sum(abs(v) for v in n["w"].values()) +
                abs(int(n.get("r", 0))) * 0.1)
    except Exception:
        return 0.0


def _nn_trim_w(n):
    """Держим у нейрона не больше NN_W_CAP самых сильных связей."""
    if len(n["w"]) > NN_W_CAP:
        keep = sorted(n["w"].items(), key=lambda kv: -abs(kv[1]))[:NN_W_CAP]
        n["w"] = dict(keep)


def _nn_save():
    try:
        # v2.17: активация (a/ats) — рабочее состояние, в файл не пишем
        ns = [{k: n[k] for k in ("id", "zone", "w", "r", "born")
               if k in n} for n in _NN["neurons"]]
        d = {"n": _NN["n"], "next_id": _NN["next_id"],
             "neurons": ns, "hist": _NN["hist"][-12:],
             "last": _NN["last"], "ts": _NN["ts"]}
        tmp = _nn_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False))
        os.replace(tmp, _nn_path())
    except Exception as e:
        log("СЕТЬ:", "не записать файл:", str(e)[:80])


def nn_stems(text):
    """Признаки текста: основы значимых слов (до 5 символов), без стоп-слов."""
    out = []
    for w in re.findall(r"[а-яёa-z0-9]{3,}", (text or "").lower()):
        if w in _TOPIC_STOP:
            continue
        s = w[:5]
        if s not in out:
            out.append(s)
        if len(out) >= 12:
            break
    return out


def nn_votes(feats):
    """Голоса нейронов по зонам: сумма весов связей, попавших в признаки.
    Больше нейронов в зоне — сильнее её голос (рост сети усиливает зону)."""
    votes = {}
    for n in _NN["neurons"]:
        s = 0.0
        for f in feats:
            v = n["w"].get(f)
            if v:
                s += v
        if s:
            votes[n["zone"]] = votes.get(n["zone"], 0.0) + s
    return votes


def nn_pick(text, kw_zone=None):
    """Зона с участием сети. Ключевые слова главнее; когда слов из ZONES
    в тексте не нашлось, сеть вспоминает выученные ассоциации (наставник,
    вердикты). Возврат (зона, откуда): 'ключи' | 'сеть' | 'пусто'."""
    feats = nn_stems(text)
    votes = nn_votes(feats) if feats else {}
    best, bv = "", 0.0
    for z, s in votes.items():
        if s > bv:
            best, bv = z, s
    kw = kw_zone or zone_of(text)
    if kw and kw != "Разговор":
        try:      # v2.39: ключевая зона — пример для тензорной сети
            tn_observe(text, kw, "ключи")
        except Exception:
            pass
        return kw, "ключи"
    try:          # v2.39: голос тензорной сети (None — ещё не готова)
        tz = tn_predict(text)
    except Exception:
        tz = None
    if best and bv >= NN_PICK_MIN:
        if tz and tz[0] != best and tz[1] >= TN_OVERRIDE:
            return tz[0], "тензор"
        return best, "сеть"
    if tz and tz[1] >= TN_CONF:
        return tz[0], "тензор"
    return (kw or "Разговор"), "пусто"


def nn_teach(keys, zone):
    """Наставник учит сеть: ключи совета слабо связываются с зоной ответа.
    Возврат: сколько связей добавлено/усилено."""
    if zone not in _ZONES_D or not keys:
        return 0
    try:          # v2.39: совет наставника — пример и для тензорной сети
        tn_observe(" ".join(str(k) for k in keys[:8]), zone, "наставник")
    except Exception:
        pass
    add = 0
    for n in _NN["neurons"]:
        if n["zone"] != zone:
            continue
        for k in keys[:8]:
            f = str(k).lower()[:5]
            if len(f) < 3:
                continue
            old = n["w"].get(f, 0.0)
            if old < NN_W_MAX:
                n["w"][f] = round(min(NN_W_MAX, old + NN_ADVICE_W), 3)
                add += 1
        _nn_trim_w(n)
    if add:
        _nn_save()
    return add


def nn_learn(text, zone, reward):
    """Хебб: вердикт ±1 подкрепляет/гасит связи нейронов зоны с признаками
    текста. Похвала ещё и заводит новые слабые связи (сеть запоминает)."""
    if zone not in _ZONES_D or not reward:
        return 0
    try:                                  # v2.74: дыхание — сила подкрепления
        reward = breath_mod("Зонная сеть", float(reward), 0.15) * \
            will_strength("Зонная сеть")
    except Exception:
        pass
    feats = nn_stems(text)
    if not feats:
        return 0
    if reward > 0:
        try:      # v2.39: похвала — пример для тензорной сети
            tn_observe(text, zone, "похвала")
        except Exception:
            pass
    add = 0
    for n in _NN["neurons"]:
        if n["zone"] != zone:
            continue
        n["r"] = int(n.get("r", 0)) + (1 if reward > 0 else -1)
        for f in feats:
            old = n["w"].get(f)
            if old is not None:
                n["w"][f] = round(min(NN_W_MAX, max(
                    -NN_W_MAX, old + NN_HEB * reward)), 3)
                add += 1
            elif reward > 0 and len(feats) <= 6:
                n["w"][f] = round(NN_HEB * 0.5, 3)   # новая слабая связь
                add += 1
        _nn_trim_w(n)
    try:          # v2.17: похвала/замечание — эмоция для нейронов зоны
        nn_excite_zone(zone, 0.25 if reward > 0 else 0.12)
    except Exception:
        pass
    if add:
        _nn_save()
    return add


def nn_resize(target, why=""):
    """Смена числа нейронов ПО СОВЕТУ НАСТАВНИКА. Рост — деление самого
    опытного нейрона наименее населённой зоны; сокращение — отмирание
    слабейшего в самой населённой. Не чаще раза в _NN['cool'] сек.
    Возврат (старое, новое) | None (клип 6..48, антидребезг, нет изменений)."""
    try:
        target = int(target or 0)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    target = max(NN_MIN, min(NN_MAX, target))
    now = time.time()
    if target == _NN["n"]:
        return None
    if _NN["ts"] and now - _NN["ts"] < _NN["cool"]:
        log("СЕТЬ", "смена %d->%d отложена (антидребезг %d с)" %
            (_NN["n"], target, int(_NN["cool"] - (now - _NN["ts"]))))
        return None
    old = _NN["n"]
    while _NN["n"] < target:                       # РОСТ: деление нейрона
        cnt = {}
        for n in _NN["neurons"]:
            cnt[n["zone"]] = cnt.get(n["zone"], 0) + 1
        zone = min(_ZONES_D, key=lambda z: cnt.get(z, 0))
        parent = None
        best_m = -1.0
        for n in _NN["neurons"]:
            if n["zone"] == zone and _nn_mass(n) > best_m:
                parent, best_m = n, _nn_mass(n)
        if parent is None:
            _NN["neurons"].append(_nn_new(zone))
        else:                                      # ребёнок = родитель + шум
            _NN["neurons"].append(
                _nn_new(zone, weights=parent["w"], noise=0.05))
        _NN["n"] += 1
    while _NN["n"] > target:                       # СОКРАЩЕНИЕ: отмирание
        cnt = {}
        for n in _NN["neurons"]:
            cnt[n["zone"]] = cnt.get(n["zone"], 0) + 1
        zone = max(_ZONES_D, key=lambda z: cnt.get(z, 0))
        cand = [n for n in _NN["neurons"] if n["zone"] == zone]
        if not cand:
            break
        dead = min(cand, key=_nn_mass)
        _NN["neurons"].remove(dead)
        _NN["n"] -= 1
    _NN["ts"] = now
    _NN["last"] = ("нейронов %d → %d (%s)" %
                   (old, _NN["n"], (why or "наставник")[:90]))[:160]
    _NN["hist"].append({"ts": int(now), "from": old, "to": _NN["n"],
                        "why": (why or "")[:120]})
    if len(_NN["hist"]) > 12:
        _NN["hist"] = _NN["hist"][-12:]
    _nn_save()
    log("СЕТЬ", _NN["last"])
    return (old, _NN["n"])


def nn_status():
    """Снимок сети для /api/state и чипа «сеть» в UI."""
    zones = {}
    for n in _NN["neurons"]:
        zones[n["zone"]] = zones.get(n["zone"], 0) + 1
    return {"n": _NN["n"], "min": NN_MIN, "max": NN_MAX,
            "last": _NN["last"], "hist": list(_NN["hist"][-5:]),
            "zones": zones, "ts": round(_NN["ts"], 1),
            "hot": nn_listen(top=3)}   # v2.17: кто сейчас «говорит»


# --- v2.17: АКТИВАЦИЯ НЕЙРОНОВ — генератор СЛУШААТ СЕТЬ --------------------
NN_DECAY = 0.97                # затухание активности за тик (~0.5 c)
NN_HOT = 0.12                  # порог «нейрон сейчас говорит»
NN_SPONT = 0.05                # шанс спонтанного всплеска сети за тик
NN_ACT_MAX = 1.0               # потолок активации нейрона


def nn_excite(feats, gain=0.5):
    """НЕЙРОНЫ СЛУШААТ МИР: признаки события (основы слов) возбуждают
    нейроны, у которых есть связь с признаком: a += |w| * gain (клип 0..1).
    Так сеть «слышит» текст пользователя, фразы генератора и слова скетча.
    Возврат: сколько нейронов возбудилось."""
    feats = [f for f in (feats or []) if f]
    if not feats:
        return 0
    now = time.time()
    touched = 0
    with _NN_LOCK:
        for n in _NN["neurons"]:
            s = 0.0
            for f in feats:
                v = n["w"].get(f)
                if v:
                    s += abs(v)
            if s:
                n["a"] = min(NN_ACT_MAX, float(n.get("a", 0.0)) + s * gain)
                n["ats"] = now
                touched += 1
    return touched


def nn_excite_zone(zone, gain=0.3):
    """Возбуждение ВСЕХ нейронов зоны — так сеть слушает аппаратные нейроны:
    спайки платы нейрона №i зажигают софт-нейроны зоны ZONES[i]."""
    if zone not in _ZONES_D:
        return 0
    now = time.time()
    cnt = 0
    with _NN_LOCK:
        for n in _NN["neurons"]:
            if n["zone"] == zone:
                n["a"] = min(NN_ACT_MAX, float(n.get("a", 0.0)) + gain)
                n["ats"] = now
                cnt += 1
    return cnt


def nn_decay_tick():
    """Затухание активностей (каждые ~0.5 c из gen_tick) + спонтанный шум:
    живая сеть иногда «искрит» сама — рождаются мысли без внешнего спроса.
    Возврат: id вспыхнувшего нейрона | None."""
    with _NN_LOCK:
        for n in _NN["neurons"]:
            a = float(n.get("a", 0.0))
            n["a"] = a * NN_DECAY if a > 0.002 else 0.0
        if _NN["neurons"] and \
                random.random() < NN_SPONT * len(_NN["neurons"]):
            n = random.choice(_NN["neurons"])
            n["a"] = min(NN_ACT_MAX, float(n.get("a", 0.0)) + 0.4)
            n["ats"] = time.time()
            return n.get("id")
    return None


def nn_listen(top=6, thr=NN_HOT):
    """ГЕНЕРАТОР СЛУШААТ НЕЙРОНЫ: самые активные сейчас нейроны и их слова.
    Слово нейрона = его выученная связь (основа), развёрнутая до словарных
    слов сети знаний (by_stem). Возврат: [{"id","zone","a","words":[...]}]."""
    by_stem = _GEN_STATE.get("by_stem") or {}
    out = []
    with _NN_LOCK:
        hot = [n for n in _NN["neurons"]
               if float(n.get("a", 0.0)) >= thr]
        hot.sort(key=lambda n: -float(n.get("a", 0.0)))
        for n in hot[:max(0, top)]:
            words = []
            for f, w in sorted(n["w"].items(), key=lambda kv: -abs(kv[1])):
                if w <= 0:
                    continue
                for cand in by_stem.get(f, ()):
                    if cand not in words:
                        words.append(cand)
                if len(words) >= 4:
                    break
            out.append({"id": n.get("id"), "zone": n["zone"],
                        "a": round(float(n.get("a", 0.0)), 2),
                        "words": words[:4]})
    return out


def nn_listen_words(top=8, thr=NN_HOT):
    """Плоский «шёпот нейронов»: [(слово, сила)] от самых активных —
    генератор подбирает слова ИЗ ТОГО, ЧТО НЕЙРОНЫ СЕЙЧАС ГОВОРЯТ."""
    score = {}
    for h in nn_listen(top=top, thr=thr):
        for w in h["words"]:
            score[w] = score.get(w, 0.0) + h["a"]
    return sorted(score.items(), key=lambda kv: -kv[1])[:top]


def nn_hot_stems():
    """Карта {основа: бонус} по горящим нейронам: слова, связанные с
    говорящими нейронами, получают приоритет при выборе продолжений."""
    out = {}
    with _NN_LOCK:
        for n in _NN["neurons"]:
            a = float(n.get("a", 0.0))
            if a < NN_HOT:
                continue
            for f, w in n["w"].items():
                if w > 0:
                    out[f] = out.get(f, 0.0) + a * min(w, 2.0)
    return out


def _nn_load():
    """Старт сети: из neuro12_net.json (размер и связи переживают перезапуск)
    или заново — по нейрону на зону (12)."""
    _NN["neurons"] = []
    loaded = False
    try:
        with open(_nn_path(), "r", encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
        ns = d.get("neurons")
        if isinstance(ns, list) and ns:
            for n in ns:
                if not isinstance(n, dict):
                    continue
                z = str(n.get("zone") or "")
                if z not in _ZONES_D:
                    continue
                w = n.get("w")
                ww = {}
                if isinstance(w, dict):
                    for k, v in w.items():
                        try:
                            ww[str(k)[:16]] = round(min(NN_W_MAX, max(
                                -NN_W_MAX, float(v))), 3)
                        except (TypeError, ValueError):
                            pass
                _NN["neurons"].append({
                    "id": int(n.get("id") or 0), "zone": z, "w": ww,
                    "r": int(n.get("r") or 0),
                    "born": float(n.get("born") or 0.0)})
            if _NN["neurons"]:
                loaded = True
                _NN["next_id"] = max(n["id"] for n in _NN["neurons"]) + 1
                _NN["hist"] = list(d.get("hist") or [])[-12:]
                _NN["last"] = str(d.get("last") or "")[:160]
                _NN["ts"] = float(d.get("ts") or 0.0)
    except Exception:
        loaded = False
    if not loaded:
        for z, _stems in ZONES:
            _NN["neurons"].append(_nn_new(z))
        _NN["next_id"] = _NN["n"] = len(_NN["neurons"])
        _NN["last"] = "старт: по нейрону на зону"
    # v2.31: миграция сети — новые зоны (Знания/Кирпичики/Любопытство)
    # получают по нейрону, старые сети продолжают жить без сброса
    _have = {n.get("zone") for n in _NN["neurons"]}
    _added = 0
    for z, _stems in ZONES:
        if z not in _have:
            _NN["neurons"].append(_nn_new(z))
            _added += 1
    _NN["n"] = len(_NN["neurons"])
    if not loaded or _added:
        _nn_save()
        if _added:
            log("СЕТЬ", "миграция v2.31: новых зон %d -> нейронов %d" %
                (_added, _NN["n"]))
    log("СЕТЬ", "нейронов %d (границы %d..%d), размер меняет наставник "
        "строкой «НЕЙРОНЫ: N»; файл: %s" %
        (_NN["n"], NN_MIN, NN_MAX, _nn_path()))


# ===========================================================================
# [3t] ТЕНЗОРНОЕ ЯДРО (v2.39): настоящая тензорная сеть поверх зонной.
#      Модель: вход (64 хеш-признака текста + 12 входов от СПАЙКОВ
#      аппаратных нейронов) -> скрытый слой ReLU из H нейронов -> softmax
#      по зонам. Веса — матрицы W1[H x 76], W2[Z x H].
#      • ОБЪЁМ СЕТИ ЗАДАЁТ ЧАСТОТА ГЕНЕРАТОРА СКЕТЧА: H = 8..64 по
#        сглаженной частоте шины D4 (лог-шкала 120..2000 Гц). Сеть растёт
#        и сжимается вместе с «дыханием» генератора (антидребезг 30 с);
#        при сжатии остаются самые полезные нейроны, при росте новые
#        входят «молча» (нулевые выходные веса) — ответы не скачут.
#      • РЕСУРС ЗАВИСИТ ОТ ЗАДАЧИ: у каждой задачи (онлайн-шаг,
#        дообучение, перестройка после смены H, полное обучение) свой
#        бюджет операций. Мелкое считает плата; тяжёлое — СЕРВЕР через
#        СУЩЕСТВУЮЩУЮ песочницу POST /api/sandbox/run (API облака не
#        меняется: ядро — обычный модуль def run(text, ctx)); сервер не
#        смог — КОМПЬЮТЕР, где открыт интерфейс :7000 (Web Worker браузера,
#        GET /api/tensor/job + POST /api/tensor/result — новые маршруты
#        ПЛАТЫ, старые не тронуты); иначе — плата с урезанным бюджетом.
#        Потолок сервера подстраивается сам (успех — выше, сбой — ниже).
#      • НЕЙРОНЫ НЕ ПЕРЕПРОГРАММИРУЮТСЯ: их спайки (хвост MSG скетча)
#        просто читаются как 12 входов сети. Скетч и прошивки не менялись.
#      • ВСЕ ФУНКЦИИ СОХРАНЕНЫ: nn_pick/nn_teach/nn_learn те же; тензор
#        учится на их сигналах (зона по ключам, совет наставника, похвала)
#        и голосует, когда ключей нет. Файл: neuro12_tensor.json.
# ===========================================================================
TN_D = 64                     # хеш-признаков текста
TN_HW = 12                    # входов от 12 аппаратных нейронов
TN_IN = TN_D + TN_HW
# v2.83: объём скрытого слоя — по возможностям платы (ОЗУ), НЕ от частоты
# генератора/дыхания. Дыхание тензорной сети (period/depth/phase) — отдельный
# канал; эта MLP мыслит по зонам и не «дышит» размером.
TN_H_MIN = 16                 # минимум на слабой плате
TN_H_MAX = 128                # потолок при ~2 ГБ+ (веса ~ H*(76+Z)*8 байт)
TN_H_STEP = 8                 # шаг смены объёма
TN_H_DEFAULT = 48             # типично для платы с ~2 ГБ RAM
TN_HZ_LO = 120.0              # (совместимость / логи)
TN_HZ_HI = 2000.0
TN_RESIZE_COOL = 120.0        # реже: размер от RAM, не от «дыхания» Гц
TN_RAM_MB_TARGET = 2048       # ориентир «плата с 2 ГБ»
TN_SAMPLES_MAX = 400          # память примеров
TN_MIN_SAMPLES = 24           # меньше — сеть ещё не голосует
TN_CONF = 0.45                # минимальная уверенность тензорного голоса
TN_OVERRIDE = 0.75            # уверенность, при которой тензор спорит с сетью
TN_LR = 0.05
TN_RETRAIN_NEW = 16           # новых примеров до дообучения
TN_RETRAIN_PERIOD = 900.0     # или раз в 15 мин, если новое есть
TN_SAVE_PERIOD = 120.0
TN_LOCAL_OPS = 4e5            # задача меньше — сразу на плате
TN_LOCAL_MAX_OPS = 6e6        # потолок запасного счёта на плате
TN_SRV_CAP0 = 2e7             # стартовый потолок операций сервера
TN_SRV_CAP_MIN = 2e6
TN_SRV_CAP_MAX = 2e8
TN_CODE_MAX = 23500           # cloud_run_code режет код на 24000
TN_BROWSER_ALIVE = 8.0        # браузер опрашивал позже — считаем ушедшим
TN_BROWSER_WAIT = 30.0        # сек ждать результат от компьютера
TN_TASKS = {                  # задача -> эпох (бюджет растёт с задачей)
    "дообучение": 6,
    "перестройка": 16,
    "полное обучение": 24,
}
# алфавит упаковки (без кавычек, «\» и «;») — 90 символов, 1 символ = 1 число
TN_ALPH = "".join(chr(c) for c in range(33, 127) if chr(c) not in "\"'\\;")

_TN_KERNEL = r'''# -*- coding: utf-8 -*-
# Тензорное ядро «Мозг-12» (v2.39): MLP вход -> ReLU -> зоны, SGD.
# Один и тот же код считает в песочнице СЕРВЕРА и на плате.
import math

ALPH = "@ALPH@"
L = len(ALPH)
C = L // 2
IDX = {ch: i for i, ch in enumerate(ALPH)}
JOB = ""


def tn_rng(seed):
    st = [int(seed) % 2147483647 or 1]

    def nxt():
        st[0] = (st[0] * 48271) % 2147483647
        return st[0] / 2147483647.0
    return nxt


def tn_init(H, IN, Z, seed):
    r = tn_rng(seed)
    a2 = math.sqrt(6.0 / (H + Z))
    W1 = [[(r() * 2 - 1) * 0.3 for _i in range(IN)] for _j in range(H)]
    b1 = [0.01] * H
    W2 = [[(r() * 2 - 1) * a2 for _j in range(H)] for _k in range(Z)]
    b2 = [0.0] * Z
    return W1, b1, W2, b2


def tn_forward(W1, b1, W2, b2, x):
    h = []
    for j in range(len(W1)):
        row = W1[j]
        s = b1[j]
        for i, v in x:
            s += row[i] * v
        h.append(s if s > 0 else 0.0)
    lo = []
    for k in range(len(W2)):
        row = W2[k]
        s = b2[k]
        for j in range(len(h)):
            if h[j]:
                s += row[j] * h[j]
        lo.append(s)
    m = max(lo)
    ex = [math.exp(v - m) for v in lo]
    t = sum(ex)
    return h, [v / t for v in ex]


def tn_argmax(p):
    best = 0
    for k in range(len(p)):
        if p[k] > p[best]:
            best = k
    return best


def tn_eval(W1, b1, W2, b2, samples):
    if not samples:
        return 0.0
    ok = 0
    for x, z, _w in samples:
        if tn_argmax(tn_forward(W1, b1, W2, b2, x)[1]) == z:
            ok += 1
    return ok / float(len(samples))


def tn_train(samples, H, IN, Z, epochs, lr, seed, init=None, l2=0.0001):
    if init:
        W1, b1, W2, b2 = init
    else:
        W1, b1, W2, b2 = tn_init(H, IN, Z, seed)
    r = tn_rng(seed + 7)
    order = list(range(len(samples)))
    loss = 0.0
    ops = 0
    for ep in range(int(epochs)):
        for i in range(len(order) - 1, 0, -1):
            j = int(r() * (i + 1))
            order[i], order[j] = order[j], order[i]
        rate = lr / (1.0 + 0.15 * ep)
        loss = 0.0
        for n in order:
            x, z, w = samples[n]
            h, p = tn_forward(W1, b1, W2, b2, x)
            loss -= math.log(max(p[z], 1e-9)) * w
            gh = [0.0] * H
            for k in range(Z):
                gk = (p[k] - (1.0 if k == z else 0.0)) * w
                if not gk:
                    continue
                row = W2[k]
                for j in range(H):
                    if h[j]:
                        gh[j] += row[j] * gk
                        row[j] -= rate * (gk * h[j] + l2 * row[j])
                b2[k] -= rate * gk
            for j in range(H):
                gj = gh[j]
                if gj:
                    row = W1[j]
                    for i, v in x:
                        row[i] -= rate * gj * v
                    b1[j] -= rate * gj
            ops += H * len(x) * 2 + Z * H * 2
        loss = loss / max(1, len(samples))
    acc = tn_eval(W1, b1, W2, b2, samples)
    return W1, b1, W2, b2, loss, acc, ops


def tn_parse(tok, D):
    z = IDX[tok[0]]
    x = []
    for k in range(12):
        lv = IDX[tok[1 + k]]
        if lv:
            x.append((D + k, lv / 9.0 * 0.7))
    f = tok[13:]
    nf = len(f) // 2
    if nf:
        sc = 1.0 / math.sqrt(nf)
        for q in range(nf):
            v = IDX[f[2 * q]] * L + IDX[f[2 * q + 1]]
            x.append((v // 2, -sc if v % 2 else sc))
    return (x, z, 1.0)


def tn_qenc(vals):
    m = max([abs(v) for v in vals] + [1e-9])
    s = m / (L - 1 - C)
    out = []
    for v in vals:
        q = int(round(v / s)) + C
        if q < 0:
            q = 0
        if q > L - 1:
            q = L - 1
        out.append(ALPH[q])
    return s, "".join(out)


def run(text, ctx=None):
    job = JOB or str(text or "")
    head = job.split(";", 7)
    H, IN, Z, D, ep, lr, seed = [int(v) for v in head[:7]]
    samples = [tn_parse(t, D) for t in head[7].split(" ") if len(t) >= 13]
    W1, b1, W2, b2, loss, acc, ops = tn_train(samples, H, IN, Z, ep,
                                              lr / 10000.0, seed)
    parts = ["TN1", str(H), str(IN), str(Z), "%.4f" % loss, "%.3f" % acc,
             str(ops)]
    for vals in ([v for row in W1 for v in row], b1,
                 [v for row in W2 for v in row], b2):
        s, t = tn_qenc(vals)
        parts.append("%.6g" % s)
        parts.append(t)
    return ";".join(parts)
'''.replace("@ALPH@", TN_ALPH)

_TN_K = {"__name__": "tensorcore"}
exec(compile(_TN_KERNEL, "<tensorcore>", "exec"), _TN_K)   # своё ядро

_TN_LOCK = threading.Lock()
_TN = {"H": 0, "W1": None, "b1": None, "W2": None, "b2": None,
       "samples": [], "new": 0, "trains": 0, "trained_ts": 0.0,
       "loss": None, "acc": None, "hz_ema": 0.0, "resize_ts": 0.0,
       "pending": "", "force": "", "busy": False, "dirty": False,
       "save_ts": 0.0, "tasks": [], "hist": [], "active": 0,
       "srv": {"cap": TN_SRV_CAP0, "ok": None, "err": "", "ms": 0,
               "ops": 0, "fails": 0, "off_until": 0.0, "runs": 0}}
_TN_BROWSER = {"ts": 0.0, "bench": 0.0, "job": None, "taken": 0.0,
               "result": None, "event": threading.Event(), "done": 0,
               "err": ""}
_TN_BLOCK = threading.Lock()


def _tn_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_tensor.json")
    except Exception:
        return "neuro12_tensor.json"


def _tn_znames():
    return [z for z, _s in ZONES]


def _tn_ram_mb():
    """Доступная/полная ОЗУ платы в МБ (Linux /proc; иначе ориентир 2 ГБ)."""
    try:
        with open("/proc/meminfo", "r") as f:
            tot = av = None
            for line in f:
                if line.startswith("MemTotal:"):
                    tot = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    av = int(line.split()[1]) // 1024
                if tot is not None and av is not None:
                    break
            if tot:
                return max(256, int(av if av is not None else tot * 0.6))
    except Exception:
        pass
    return TN_RAM_MB_TARGET


def _tn_h_for_ram(ram_mb=None):
    """v2.83: объём H по ОЗУ платы (не по частоте/дыханию).
    ~512 МБ -> H≈16, ~2 ГБ -> H≈48..64, 4 ГБ+ -> до TN_H_MAX."""
    mb = float(ram_mb if ram_mb is not None else _tn_ram_mb())
    # лог-шкала от 256 МБ до 4096 МБ
    lo, hi = math.log(256.0), math.log(4096.0)
    t = (math.log(max(256.0, mb)) - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    h = TN_H_MIN + t * (TN_H_MAX - TN_H_MIN)
    return int(round(h / TN_H_STEP) * TN_H_STEP)


def _tn_h_raw(hz=None):
    """Совместимость: раньше — от Гц; теперь тот же целевой H от RAM."""
    return float(_tn_h_for_ram())


def _tn_h_for_hz(hz=None):
    """Совместимость имени: целевой H по возможностям платы (RAM), не по Гц."""
    return _tn_h_for_ram()


def _tn_hash(s):
    h = 2166136261
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def tn_features(text):
    """Признаки текста: основы nn_stems -> хеш-индекс*2 + знак (0..127)."""
    out = []
    for st in nn_stems(text):
        h = _tn_hash(st)
        out.append((h % TN_D) * 2 + ((h >> 16) & 1))
    return out


def tn_hw_levels(msg=None):
    """Входы от 12 аппаратных нейронов: спайки из хвоста MSG скетча
    («N5:1042:слово|...») -> уровни 0..9 (лог-шкала). Прошивки не нужны."""
    lv = [0] * TN_HW
    for mo in _RE_NEURON.finditer(SH.msg if msg is None else (msg or "")):
        try:
            i, sp = int(mo.group(1)), int(mo.group(2))
        except Exception:
            continue
        if 0 <= i < TN_HW and sp > 0:
            lv[i] = max(lv[i], min(9, int(math.log(1 + sp, 2))))
    return lv


def _tn_token(feats, hw, z):
    A, L = TN_ALPH, len(TN_ALPH)
    return (A[z] + "".join(A[v] for v in hw) +
            "".join(A[v // L] + A[v % L] for v in feats))


def _tn_ready_locked():
    return (_TN["W1"] is not None and _TN["trains"] > 0 and
            len(_TN["samples"]) >= TN_MIN_SAMPLES)


def _tn_copy():
    return ([list(r) for r in _TN["W1"]], list(_TN["b1"]),
            [list(r) for r in _TN["W2"]], list(_TN["b2"]))


def _tn_resize(W1, b1, W2, H_new, seed):
    """Смена объёма: сжатие — остаются нейроны с самыми сильными выходами;
    рост — новые нейроны с нулевыми выходами (ответы не скачут)."""
    H_old = len(W1)
    if H_new == H_old:
        return W1, b1, W2
    if H_new < H_old:
        score = [sum(abs(row[j]) for row in W2) for j in range(H_old)]
        keep = sorted(sorted(range(H_old), key=lambda j: -score[j])[:H_new])
        return ([W1[j] for j in keep], [b1[j] for j in keep],
                [[row[j] for j in keep] for row in W2])
    r = random.Random(seed)
    W1 = W1 + [[r.uniform(-0.3, 0.3) for _i in range(TN_IN)]
               for _j in range(H_new - H_old)]
    b1 = b1 + [0.01] * (H_new - H_old)
    W2 = [row + [0.0] * (H_new - H_old) for row in W2]
    return W1, b1, W2


def tn_observe(text, zone, src=""):
    """Пример для тензорной сети: текст + реакция аппаратных нейронов ->
    зона. Сразу делает онлайн-шаг на плате (задача «онлайн-шаг»: ~6 тыс.
    операций). Возврат: True — пример принят."""
    names = _tn_znames()
    if zone not in names:
        return False
    feats = tn_features(text)
    if not feats:
        return False
    tok = _tn_token(feats, tn_hw_levels(), names.index(zone))
    with _TN_LOCK:
        S = _TN["samples"]
        if tok in S[-10:]:
            return False
        S.append(tok)
        if len(S) > TN_SAMPLES_MAX:
            del S[:len(S) - TN_SAMPLES_MAX]
        _TN["new"] += 1
        _TN["dirty"] = True
        SH.mem_dirty = True                # v2.42: наблюдение -> в облако
        if _TN["W1"] is not None:
            try:
                lr_m, _c, _v = _tn_brain_mod()
            except Exception:
                lr_m = 1.0
            _TN_K["tn_train"]([_TN_K["tn_parse"](tok, TN_D)], _TN["H"],
                              TN_IN, len(names), 1, TN_LR * 0.6 * lr_m,
                              len(S), init=(_TN["W1"], _TN["b1"],
                                            _TN["W2"], _TN["b2"]))
    return True


def _tn_brain_mod():
    """v2.84: модуляторы тензора мысли от частоты, воли и дыхания мозга.
    Размер H задаётся ОЗУ; поведение (шаг учёбы, пороги, сила голоса) —
    от состояния платы. Не путать с сетью «дыхания» (period/depth)."""
    # lr_mul, conf_mul, voice_mul  — около 1.0
    lr_m = conf_m = voice_m = 1.0
    try:
        # частота генератора: |z| выше → чуть увереннее и быстрее учится
        z = abs(float(_signed_hz())) / 2000.0
        z = max(0.0, min(1.0, z))
        lr_m *= 0.85 + 0.30 * z
        conf_m *= 0.90 + 0.20 * z
        voice_m *= 0.90 + 0.25 * z
    except Exception:
        pass
    try:
        # воля зоны «Сигнал» (алгоритм «Тензорная сеть»)
        w = float(will_strength("Тензорная сеть"))
        lr_m *= 0.7 + 0.6 * max(0.0, min(1.5, w))
        voice_m *= 0.75 + 0.5 * max(0.0, min(1.5, w))
    except Exception:
        pass
    try:
        # дыхание: на вдохе (s>0) — шире учится и голос сильнее
        s = float(breath_s())
        lr_m = breath_mod("Тензорная сеть", lr_m, 0.15)
        conf_m *= 1.0 + 0.08 * max(-1.0, min(1.0, s))
        voice_m *= 1.0 + 0.12 * max(-1.0, min(1.0, s))
    except Exception:
        pass
    return (max(0.4, min(1.8, lr_m)),
            max(0.7, min(1.3, conf_m)),
            max(0.6, min(1.5, voice_m)))


def tn_predict(text):
    """Голос тензорной сети: (зона, уверенность) | None (сеть не готова).
    Уверенность модулируется частотой, волей и дыханием (v2.84)."""
    feats = tn_features(text)
    if not feats:
        return None
    x, _z, _w = _TN_K["tn_parse"](_tn_token(feats, tn_hw_levels(), 0), TN_D)
    with _TN_LOCK:
        if not _tn_ready_locked():
            return None
        h, p = _TN_K["tn_forward"](_TN["W1"], _TN["b1"], _TN["W2"],
                                   _TN["b2"], x)
        _TN["active"] = sum(1 for v in h if v > 0)
    k = _TN_K["tn_argmax"](p)
    names = _tn_znames()
    if k >= len(names):
        return None
    conf = float(p[k])
    try:
        _lr, conf_m, voice_m = _tn_brain_mod()
        conf = min(1.0, conf * conf_m * (0.85 + 0.15 * voice_m))
    except Exception:
        pass
    return names[k], conf


def _tn_parse_result(out, H, Z):
    """Ответ песочницы сервера «TN1;H;IN;Z;loss;acc;ops;s;W1;s;b1;s;W2;s;b2»."""
    p = str(out or "").strip().split(";")
    if len(p) != 15 or p[0] != "TN1":
        return None
    try:
        if int(p[1]) != H or int(p[2]) != TN_IN or int(p[3]) != Z:
            return None
        idx, C = _TN_K["IDX"], _TN_K["C"]
        dec = [[(idx[ch] - C) * float(p[i]) for ch in p[i + 1]]
               for i in (7, 9, 11, 13)]
    except Exception:
        return None
    if [len(v) for v in dec] != [H * TN_IN, H, Z * H, Z]:
        return None
    W1 = [dec[0][j * TN_IN:(j + 1) * TN_IN] for j in range(H)]
    W2 = [dec[2][k * H:(k + 1) * H] for k in range(Z)]
    return {"W1": W1, "b1": dec[1], "W2": W2, "b2": dec[3],
            "loss": float(p[4]), "acc": float(p[5]), "ops": int(p[6])}


def _tn_server(tokens, H, Z, epochs, seed, per_sample):
    """Задача — СЕРВЕРУ через существующую песочницу /api/sandbox/run.
    Сервер учит с нуля (веса в код не помещаются), эпохи — по потолку."""
    url, _tok = _cloud_base()
    if not url:
        return None, "облако не задано"
    with SH.lock:
        cloud_ok = bool(SH.cloud_state.get("ok"))
    if not cloud_ok:
        return None, "облако недоступно"
    srv = _TN["srv"]
    if time.time() < srv.get("off_until", 0.0):
        return None, "сервер на паузе: " + str(srv.get("err") or "")[:60]
    head_fmt = "%d;%d;%d;%d;%d;%d;%d;"
    room = TN_CODE_MAX - len(_TN_KERNEL) - 64
    pick, used = [], 0
    for tok in reversed(tokens):
        if used + len(tok) + 1 > room:
            break
        pick.append(tok)
        used += len(tok) + 1
    pick.reverse()
    if len(pick) < TN_MIN_SAMPLES // 2:
        return None, "мало места под примеры"
    per_epoch = max(1.0, per_sample * len(pick))
    ep = int(max(3, min(epochs, srv["cap"] // per_epoch)))
    code = (_TN_KERNEL + '\nJOB = "' +
            head_fmt % (H, TN_IN, Z, TN_D, ep, int(TN_LR * 10000), seed) +
            " ".join(pick) + '"\n')
    t0 = time.time()
    res, err = cloud_run_code(code, "", timeout=45)
    ms = int((time.time() - t0) * 1000)
    out = str((res or {}).get("result") or (res or {}).get("output") or "")
    parsed = None if (err or not res) else _tn_parse_result(out, H, Z)
    with _TN_LOCK:
        srv["runs"] += 1
        srv["ms"] = ms
        if parsed:
            srv.update(ok=True, err="", fails=0, ops=parsed["ops"])
            if ms < 8000:
                srv["cap"] = min(TN_SRV_CAP_MAX, srv["cap"] * 1.25)
        else:
            why = err or ("сервер вернул не то (%d симв.)" % len(out))
            srv.update(ok=False, err=str(why)[:160], fails=srv["fails"] + 1)
            srv["cap"] = max(TN_SRV_CAP_MIN, srv["cap"] * 0.5)
            if srv["fails"] >= 3:
                srv["off_until"] = time.time() + 1800.0
    if not parsed:
        return None, srv["err"]
    parsed.update(epochs=ep, n=len(pick), ms=ms)
    return parsed, ""


def _tn_browser(samples, H, Z, epochs, seed, init, per_sample):
    """Задача — КОМПЬЮТЕРУ, где открыт интерфейс :7000 (Web Worker)."""
    if time.time() - _TN_BROWSER["ts"] > TN_BROWSER_ALIVE:
        return None, "компьютер не подключён"
    cap = max(5e6, float(_TN_BROWSER["bench"] or 0) * 3.0)
    ep = int(max(2, min(epochs * 2, cap // max(1.0, per_sample * len(samples)))))
    jid = "tn%d" % int(time.time() * 1000)
    job = {"id": jid, "H": H, "IN": TN_IN, "Z": Z, "epochs": ep,
           "lr": TN_LR, "seed": seed,
           "samples": [[[[i, round(v, 4)] for i, v in x], z]
                       for x, z, _w in samples],
           "init": ({"W1": init[0], "b1": init[1], "W2": init[2],
                     "b2": init[3]} if init else None)}
    with _TN_BLOCK:
        _TN_BROWSER.update(job=job, taken=0.0, result=None)
        _TN_BROWSER["event"].clear()
    got = _TN_BROWSER["event"].wait(TN_BROWSER_WAIT)
    with _TN_BLOCK:
        res = _TN_BROWSER["result"]
        _TN_BROWSER.update(job=None, result=None)
    if not got or not res:
        _TN_BROWSER["err"] = "компьютер не ответил за %d с" % TN_BROWSER_WAIT
        return None, _TN_BROWSER["err"]
    _TN_BROWSER["done"] += 1
    _TN_BROWSER["err"] = ""
    res.update(epochs=ep, n=len(samples))
    return res, ""


def _tn_run_task(kind):
    """Исполнить задачу обучения там, где хватает ресурса."""
    t0 = time.time()
    try:
        with _TN_LOCK:
            H, Z = _TN["H"], len(_tn_znames())
            tokens = list(_TN["samples"])
            init = _tn_copy() if _TN["W1"] is not None else None
            n_new = _TN["new"]
            cur_acc = _TN["acc"] if _TN["trains"] else None
        samples = [_TN_K["tn_parse"](t, TN_D) for t in tokens]
        if not samples:
            return
        nnz = sum(len(x) for x, _z, _w in samples) / float(len(samples))
        per_sample = H * nnz * 2 + Z * H * 2       # операций на пример
        epochs = TN_TASKS.get(kind, 6)
        need = per_sample * len(samples) * epochs  # БЮДЖЕТ ЗАДАЧИ
        seed = int(time.time()) & 0x7FFFFFFF
        res, where, errs = None, "", []
        if need > TN_LOCAL_OPS:
            res, e = _tn_server(tokens, H, Z, epochs * 2, seed, per_sample)
            where = "server" if res else ""
            if e:
                errs.append("сервер: " + e)
            if not res:
                res, e = _tn_browser(samples, H, Z, epochs, seed, init,
                                     per_sample)
                where = "browser" if res else ""
                if e:
                    errs.append("компьютер: " + e)
        if not res:                                # плата — урезанный бюджет
            ep = epochs if need <= TN_LOCAL_OPS else int(max(
                1, TN_LOCAL_MAX_OPS // max(1.0, per_sample * len(samples))))
            ep = min(ep, epochs)
            init_l = init or _TN_K["tn_init"](H, TN_IN, Z, seed)
            try:
                lr_m, _cm, _vm = _tn_brain_mod()
            except Exception:
                lr_m = 1.0
            W1, b1, W2, b2, loss, acc, ops = _TN_K["tn_train"](
                samples, H, TN_IN, Z, ep, TN_LR * lr_m, seed, init=init_l)
            res = {"W1": W1, "b1": b1, "W2": W2, "b2": b2, "loss": loss,
                   "acc": acc, "ops": ops, "epochs": ep, "n": len(samples)}
            where = "local"
        # честная оценка на ВСЕХ примерах и защита «не хуже текущей»
        acc_all = _TN_K["tn_eval"](res["W1"], res["b1"], res["W2"],
                                   res["b2"], samples)
        accepted = cur_acc is None or acc_all >= cur_acc - 0.05
        with _TN_LOCK:
            if accepted:
                W1, b1, W2 = res["W1"], res["b1"], res["W2"]
                if len(W1) != _TN["H"]:            # объём сменился по ходу
                    W1, b1, W2 = _tn_resize(W1, b1, W2, _TN["H"], seed)
                _TN.update(W1=W1, b1=b1, W2=W2, b2=res["b2"],
                           loss=round(float(res.get("loss") or 0), 4),
                           acc=round(acc_all, 3))
                _TN["trains"] += 1
            _TN["trained_ts"] = time.time()
            _TN["new"] = max(0, _TN["new"] - n_new)
            if _TN["pending"] == kind:
                _TN["pending"] = ""
            _TN["dirty"] = True
            rec = {"kind": kind, "where": where, "H": H,
                   "epochs": int(res.get("epochs") or 0),
                   "n": int(res.get("n") or 0), "need": int(need),
                   "ops": int(res.get("ops") or 0),
                   "ms": int((time.time() - t0) * 1000),
                   "acc": round(acc_all, 3), "accepted": accepted,
                   "why": "; ".join(errs)[:200], "ts": int(time.time())}
            _TN["tasks"] = (_TN["tasks"] + [rec])[-8:]
        log("ТЕНЗОР", "%s: %s, H=%d, эпох %d, примеров %d, бюджет %.1fM, "
            "точность %.0f%%%s%s" %
            (kind, {"server": "сервер", "browser": "компьютер",
                    "local": "плата"}.get(where, where), H, rec["epochs"],
             rec["n"], need / 1e6, acc_all * 100,
             "" if accepted else " (хуже текущей — не принято)",
             (" | " + rec["why"]) if rec["why"] else ""))
    except Exception:
        traceback.print_exc()
    finally:
        with _TN_LOCK:
            _TN["busy"] = False
        _tn_save()


def _tn_save(force=False):
    with _TN_LOCK:
        if _TN["W1"] is None or not (_TN["dirty"] or force):
            return
        d = {"v": 1, "H": _TN["H"], "IN": TN_IN, "zones": _tn_znames(),
             "W1": [[round(v, 5) for v in r] for r in _TN["W1"]],
             "b1": [round(v, 5) for v in _TN["b1"]],
             "W2": [[round(v, 5) for v in r] for r in _TN["W2"]],
             "b2": [round(v, 5) for v in _TN["b2"]],
             "samples": list(_TN["samples"]), "trains": _TN["trains"],
             "loss": _TN["loss"], "acc": _TN["acc"],
             "hist": _TN["hist"][-12:], "tasks": _TN["tasks"][-8:],
             "srv_cap": _TN["srv"]["cap"]}
        _TN["dirty"] = False
        _TN["save_ts"] = time.time()
    try:
        tmp = _tn_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False))
        os.replace(tmp, _tn_path())
    except Exception as e:
        log("ТЕНЗОР:", "не записать файл:", str(e)[:80])


def _tn_load():
    """Старт ядра из neuro12_tensor.json (с переносом зон по ИМЕНАМ) или
    с нуля: H = TN_H_DEFAULT, пока скетч не прислал частоту."""
    d = None
    try:
        with open(_tn_path(), "r", encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
    except Exception:
        d = None
    names = _tn_znames()
    Z = len(names)
    with _TN_LOCK:
        ok = False
        try:
            H = int(d.get("H") or 0) if isinstance(d, dict) else 0
            W1 = d.get("W1") if H else None
            if (TN_H_MIN <= H <= TN_H_MAX and int(d.get("IN") or 0) == TN_IN
                    and len(W1) == H and all(len(r) == TN_IN for r in W1)):
                old = list(d.get("zones") or names)
                W2o, b2o = d.get("W2") or [], d.get("b2") or []
                W2, b2 = [], []
                for nm in names:
                    i = old.index(nm) if nm in old else -1
                    if 0 <= i < len(W2o) and len(W2o[i]) == H:
                        W2.append([float(v) for v in W2o[i]])
                        b2.append(float(b2o[i]) if i < len(b2o) else 0.0)
                    else:
                        W2.append([0.0] * H)
                        b2.append(0.0)
                samples = []
                A, idx = TN_ALPH, _TN_K["IDX"]
                for tok in d.get("samples") or []:
                    if not isinstance(tok, str) or len(tok) < 13:
                        continue
                    zi = idx.get(tok[0], -1)
                    nm = old[zi] if 0 <= zi < len(old) else ""
                    if nm in names:
                        samples.append(A[names.index(nm)] + tok[1:])
                _TN.update(H=H, W1=[[float(v) for v in r] for r in W1],
                           b1=[float(v) for v in d.get("b1") or [0.01] * H],
                           W2=W2, b2=b2, samples=samples[-TN_SAMPLES_MAX:],
                           trains=int(d.get("trains") or 0),
                           loss=d.get("loss"), acc=d.get("acc"),
                           hist=list(d.get("hist") or [])[-12:],
                           tasks=list(d.get("tasks") or [])[-8:])
                if len(_TN["b1"]) != H:
                    _TN["b1"] = [0.01] * H
                _TN["srv"]["cap"] = float(d.get("srv_cap") or TN_SRV_CAP0)
                ok = True
        except Exception:
            ok = False
        if not ok:
            H = _tn_h_for_ram() or TN_H_DEFAULT
            W1, b1, W2, b2 = _TN_K["tn_init"](H, TN_IN, Z,
                                              int(time.time()) & 0x7FFFFFFF)
            _TN.update(H=H, W1=W1, b1=b1, W2=W2, b2=b2, samples=[],
                       trains=0, loss=None, acc=None)
            _TN["dirty"] = True
        _TN["resize_ts"] = time.time()
    log("ТЕНЗОР", "ядро: H=%d x вход %d -> зон %d, примеров %d, обучений %d; "
        "объём по ОЗУ платы (~%d МБ, %d..%d нейр.), не от дыхания; тяжёлое — "
        "сервер (/api/sandbox/run), затем компьютер :7000; файл: %s" %
        (_TN["H"], TN_IN, Z, len(_TN["samples"]), _TN["trains"],
         _tn_ram_mb(), TN_H_MIN, TN_H_MAX, _tn_path()))


_GEN_RING = []                 # v2.43: отсчёты генератора скетча для облака
_GEN_RING_TS = [0.0, 0.0]      # последний отсчёт, последняя пометка «грязно»


def _gen_ring_tick(now, hz):
    """Раз в ~5 с: [время, частота D4 Гц, номер буквы буквогенератора] — в
    ленту (до 120). Облако использует её как физический источник для зерна
    шума и скорости обучения своей LLM (genmod). Выгрузка — не чаще раза в
    минуту (через бандл памяти)."""
    if hz <= 0 or now - _GEN_RING_TS[0] < 5.0:
        return
    _GEN_RING_TS[0] = now
    _GEN_RING.append([int(now), int(hz), int(SH.ltr or 0)])
    if len(_GEN_RING) > 120:
        del _GEN_RING[:len(_GEN_RING) - 120]
    if now - _GEN_RING_TS[1] >= 60.0:
        _GEN_RING_TS[1] = now
        SH.mem_dirty = True


def tn_tick():
    """Из brain_tick (раз в ~0.5 с): объём по ОЗУ платы (не от дыхания/
    частоты) и выбор задачи обучения. Сама задача — в фоновом потоке."""
    now = time.time()
    hz = int(SH.gen_hz or 0)
    try:
        _gen_ring_tick(now, hz)
    except Exception:
        pass
    kind = ""
    with _TN_LOCK:
        if _TN["W1"] is None:
            return
        if hz > 0:
            ema = _TN["hz_ema"]
            _TN["hz_ema"] = float(hz) if ema <= 0 else ema * 0.96 + hz * 0.04
        # v2.83: размер от RAM платы, не от частоты генератора (дыхание —
        # отдельный канал period/depth/phase)
        target = _tn_h_for_ram()
        if (target and target != _TN["H"] and
                abs(target - _TN["H"]) >= TN_H_STEP and
                now - _TN["resize_ts"] >= TN_RESIZE_COOL and not _TN["busy"]):
            old = _TN["H"]
            _TN["W1"], _TN["b1"], _TN["W2"] = _tn_resize(
                _TN["W1"], _TN["b1"], _TN["W2"], target, int(now))
            _TN["H"] = target
            _TN["resize_ts"] = now
            _TN["pending"] = "перестройка"
            _TN["dirty"] = True
            _TN["hist"] = (_TN["hist"] + [{"ts": int(now), "from": old,
                                           "to": target,
                                           "ram_mb": _tn_ram_mb()}])[-12:]
            log("ТЕНЗОР", "объём %d -> %d нейронов (ОЗУ ~%d МБ, не от дыхания)" %
                (old, target, _tn_ram_mb()))
        n = len(_TN["samples"])
        if not _TN["busy"]:
            if _TN["force"]:
                kind, _TN["force"] = _TN["force"], ""
            elif n >= TN_MIN_SAMPLES:
                if _TN["trains"] == 0:
                    kind = "полное обучение"
                elif _TN["pending"]:
                    kind = _TN["pending"]
                elif _TN["new"] >= TN_RETRAIN_NEW or (
                        _TN["new"] > 0 and
                        now - _TN["trained_ts"] >= TN_RETRAIN_PERIOD):
                    kind = "дообучение"
            if kind:
                _TN["busy"] = True
        save_due = _TN["dirty"] and now - _TN["save_ts"] >= TN_SAVE_PERIOD
    if kind:
        threading.Thread(target=_tn_run_task, args=(kind,),
                         daemon=True).start()
    elif save_due:
        _tn_save()


def tn_train_now(kind="полное обучение"):
    with _TN_LOCK:
        _TN["force"] = kind if kind in TN_TASKS else "полное обучение"


def _tn_status_raw():
    """Снимок ядра для /api/state и чипа «тензор»."""
    with _TN_LOCK:
        srv = dict(_TN["srv"])
        st = {"H": _TN["H"], "IN": TN_IN, "Z": len(_tn_znames()),
              "params": (_TN["H"] * TN_IN + _TN["H"] +
                         len(_tn_znames()) * (_TN["H"] + 1)),
              "h_min": TN_H_MIN, "h_max": TN_H_MAX,
              "hz_ema": int(_TN["hz_ema"]),
              "h_target": _tn_h_for_ram() or _TN["H"],
              "ram_mb": _tn_ram_mb(),
              "samples": len(_TN["samples"]), "new": _TN["new"],
              "trains": _TN["trains"], "loss": _TN["loss"],
              "acc": _TN["acc"], "ready": _tn_ready_locked(),
              "busy": _TN["busy"], "active": _TN["active"],
              "task": (_TN["tasks"][-1] if _TN["tasks"] else {}),
              "hist": list(_TN["hist"][-5:])}
    try:
        lr_m, conf_m, voice_m = _tn_brain_mod()
        st["mod"] = {"lr": round(lr_m, 3), "conf": round(conf_m, 3),
                     "voice": round(voice_m, 3)}
    except Exception:
        st["mod"] = {}
    st["srv"] = {"ok": srv.get("ok"), "err": srv.get("err", ""),
                 "cap": int(srv.get("cap") or 0), "ms": srv.get("ms", 0),
                 "runs": srv.get("runs", 0),
                 "paused": time.time() < srv.get("off_until", 0.0)}
    st["browser"] = {
        "alive": time.time() - _TN_BROWSER["ts"] <= TN_BROWSER_ALIVE,
        "bench": int(_TN_BROWSER["bench"] or 0),
        "done": _TN_BROWSER["done"], "err": _TN_BROWSER["err"]}
    return st


def tn_report():
    s = tn_status()
    t = s.get("task") or {}
    where = {"server": "сервер", "browser": "компьютер",
             "local": "плата"}.get(t.get("where"), "—")
    mod = s.get("mod") or {}
    return ("Тензор мысли (логика зон): %d нейронов скрытого слоя "
            "(объём по ОЗУ ~%s МБ → цель %d; границы %d..%d), не от дыхания. "
            "Поведение от частоты (~%d Гц), воли и дыхания: lr×%s conf×%s voice×%s. "
            "Вход %d (64 признака + 12 нейронов), зон %d, параметров %d. "
            "Примеров %d (новых %d), обучений %d, точность %s. "
            "Учится по материалам базы (кнопка «Обучить все сети»). "
            "Последняя задача: %s — %s. Сервер: %s. Компьютер: %s." %
            (s["H"], s.get("ram_mb") or "?", s["h_target"], s["h_min"], s["h_max"],
             s["hz_ema"],
             mod.get("lr", "—"), mod.get("conf", "—"), mod.get("voice", "—"),
             s["IN"], s["Z"], s["params"], s["samples"], s["new"],
             s["trains"],
             ("%d%%" % round(s["acc"] * 100)) if s["acc"] is not None else "—",
             t.get("kind", "—"), where,
             ("ок" if s["srv"]["ok"] else (s["srv"]["err"] or "не пробовал")),
             ("подключён" if s["browser"]["alive"] else "не подключён")))


def api_tensor_job(bench: float = 0):
    """v2.39: браузер компьютера забирает задачу (и сообщает свою скорость)."""
    now = time.time()
    with _TN_BLOCK:
        _TN_BROWSER["ts"] = now
        try:
            if float(bench or 0) > 0:
                _TN_BROWSER["bench"] = float(bench)
        except (TypeError, ValueError):
            pass
        job = _TN_BROWSER["job"]
        if job and (not _TN_BROWSER["taken"] or
                    now - _TN_BROWSER["taken"] > TN_BROWSER_WAIT):
            _TN_BROWSER["taken"] = now
            return {"ok": True, "job": job}
    return {"ok": True, "job": None}


def api_tensor_result(data: dict = None):
    """v2.39: результат задачи от браузера: проверка форм и чисел."""
    data = data or {}
    with _TN_BLOCK:
        job = _TN_BROWSER["job"]
        if not job or str(data.get("id") or "") != job["id"]:
            return {"ok": False, "error": "задача устарела"}
        H, Z = job["H"], job["Z"]

    def _vec(v, n):
        if not isinstance(v, list) or len(v) != n:
            raise ValueError("форма")
        out = []
        for a in v:
            a = float(a)
            if a != a or abs(a) > 1e6:
                raise ValueError("число")
            out.append(max(-50.0, min(50.0, a)))
        return out

    try:
        res = {"W1": [_vec(r, TN_IN) for r in (data.get("W1") or [])],
               "b1": _vec(data.get("b1"), H),
               "W2": [_vec(r, H) for r in (data.get("W2") or [])],
               "b2": _vec(data.get("b2"), Z),
               "loss": float(data.get("loss") or 0),
               "acc": float(data.get("acc") or 0),
               "ops": int(data.get("ops") or 0)}
        if len(res["W1"]) != H or len(res["W2"]) != Z:
            raise ValueError("форма")
    except Exception as e:
        return {"ok": False, "error": "результат отклонён: %s" % e}
    with _TN_BLOCK:
        if _TN_BROWSER["job"] is job:
            _TN_BROWSER["result"] = res
            _TN_BROWSER["event"].set()
    return {"ok": True}


# ===========================================================================
# [3m] РЕЧЬ МОЗГА ИЗ СЛОВАРЯ (v2.16): нейросеть платы САМА генерирует текст
#      словами ИЗ СЛОВАРЯ на основании того, что она знает. Буквогенератор
#      переделан под эту задачу: раньше буквы лепетал случайный выбор скетча,
#      теперь есть ГОЛОС МОЗГА —
#        1) БИГРАММНАЯ СЕТЬ ЗНАНИЙ: word -> {next: count} строится по памяти
#           диалогов, загруженным файлам, заметкам наставника и выученным
#           словам; в узлы попадают ТОЛЬКО слова из словаря (dict_has) —
#           выдумок не бывает;
#        2) ЗОННАЯ СЕТЬ ПОДСКАЗЫВАЕТ СЛОВО: каждый кандидат-продолжение
#           получает бонус по голосам нейронов _NN (nn_votes) за зону —
#           то, чему сеть учил наставник и вердикты, звучит чаще;
#        3) БУКВЫ — ТОЛЬКО НА D6 (v2.19): буквы и слова буквогенератора
#           человек больше НЕ ВИДИТ (лента/чипы убраны): номера букв фразы
#           одной посылкой уходят в скетч и проигрываются PWM на пине D6.
#           Наружу — только готовые фразы;
#        4) РЕЧЬ И ПАМЯТЬ: фраза озвучивается (TTS через облако -> D8),
#           запоминается в память мозга, а её самое длинное слово уходит в
#           диктант — буквогенератор скетча учится складывать реальные слова.
#        5) v2.17: ГЕНЕРАТОР СЛУШААТ НЕЙРОНЫ: у нейронов появилась активация
#           (их зажигает всё слышанное: текст, буквы, фразы, спайки
#           аппаратных нейронов; затухает + спонтанный шум). Стартовое слово
#           и приоритет продолжений берутся у САМЫХ АКТИВНЫХ нейронов —
#           фраза = то, что сеть «думает» прямо сейчас.
#      Периодически (GEN_PERIOD) мозг думает вслух сам; в ответе чата к
#      фразе зон добавляется строка «💬 Моя мысль вслух»; команда
#      «мысль <тема>» просит мозг высказаться по теме.
# ===========================================================================
GEN_PERIOD = 150.0            # сек между «мыслями вслух» в тишине
GEN_MIN_LINKS = 8             # v2.26: было 15 — мысли стартуют раньше
                              # (сеть всё равно строится только из словарных слов)
GEN_MAX_WORDS = 10            # потолок слов во фразе мозга
_GEN_STATE = {"big": {}, "uni": {}, "by_stem": {}, "links": 0,
              "built": 0.0, "busy": False,
              "last": "", "last_ts": 0.0, "phrases": 0, "words": 0,
              "listen": {}, "errs": "", "cloud_says": 0, "says": 0}
_gen_last_say = [0.0]
# v2.65: служебные слова — узлы сети знаний (фраза «капли воды в облаках»,
# а не «капли воды облаках»)
_GEN_FUNC = frozenset("в во и на с со к ко по о об у из за от до не ни а но "
                      "же ли бы что как это при для или".split())
# v2.65: ОБУЧЕНИЕ НЕ ПИСАТЬ АХИНЕЮ — вердикты облака (coherence.py): пары
# слов несвязной мысли ослабляются (×0.3), связной — чуть усиливаются
# (×1.15). Множитель хранится рядом с памятью (gen_penalty.json).
_GEN_PEN = {"pairs": {}, "loaded": False, "dirty": False, "good": 0, "bad": 0,
            "last_save": 0.0}


def _pen_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), "gen_penalty.json")
    except Exception:
        return "gen_penalty.json"


def _pen_load():
    if _GEN_PEN["loaded"]:
        return
    _GEN_PEN["loaded"] = True
    try:
        with open(_pen_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        _GEN_PEN["pairs"] = {str(k): float(v) for k, v in
                             (d.get("pairs") or {}).items()}
        _GEN_PEN["good"] = int(d.get("good") or 0)
        _GEN_PEN["bad"] = int(d.get("bad") or 0)
    except Exception:
        pass


def _pen_save(force=False):
    if not _GEN_PEN["dirty"] or (not force and
                                 time.time() - _GEN_PEN["last_save"] < 30):
        return
    try:
        pr = _GEN_PEN["pairs"]
        if len(pr) > 30000:           # самые нейтральные — прочь
            for k in sorted(pr, key=lambda k: abs(1.0 - pr[k]))[:len(pr) - 30000]:
                pr.pop(k, None)
        fn = _pen_path()
        with open(fn + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"pairs": pr, "good": _GEN_PEN["good"],
                       "bad": _GEN_PEN["bad"]}, f, ensure_ascii=False)
        os.replace(fn + ".tmp", fn)
        _GEN_PEN["dirty"] = False
        _GEN_PEN["last_save"] = time.time()
    except Exception:
        pass


def gen_pen(a, b):
    """Множитель пары слов (1 — нейтрально, <1 — облако считало её ахинеей)."""
    _pen_load()
    return _GEN_PEN["pairs"].get(_norm_word(a) + " " + _norm_word(b), 1.0)


def gen_apply_verdicts(vds):
    """Вердикты облака о связности мыслей -> множители пар слов."""
    _pen_load()
    pr = _GEN_PEN["pairs"]
    n = 0
    for v in vds or []:
        if not isinstance(v, dict):
            continue
        if v.get("ok"):
            ws = [_norm_word(w) for w in re.findall(r"[а-яёa-z0-9]+",
                                                     str(v.get("t") or "").lower())]
            for a, b in zip(ws, ws[1:]):
                k = a + " " + b
                pr[k] = min(3.0, pr.get(k, 1.0) * 1.15)
            _GEN_PEN["good"] += 1
        else:
            for p_ in v.get("bad") or []:
                if isinstance(p_, (list, tuple)) and len(p_) == 2:
                    k = _norm_word(p_[0]) + " " + _norm_word(p_[1])
                    pr[k] = max(0.02, pr.get(k, 1.0) * 0.3)
            _GEN_PEN["bad"] += 1
        n += 1
    if n:
        _GEN_PEN["dirty"] = True
        _pen_save()
    return n
_RU_NUM = {ch: i + 1 for i, ch in enumerate("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")}


def gen_status():
    """Снимок генератора для /api/state и панели UI."""
    st = _GEN_STATE
    return {"ready": st["links"] >= GEN_MIN_LINKS, "links": st["links"],
            "vocab": len(st["uni"]), "phrases": st["phrases"],
            "words": st["words"], "last": st["last"],
            "last_ts": round(st["last_ts"], 1) if st["last_ts"] else 0.0,
            "listen": dict(st.get("listen") or {}),   # v2.17: нейроны
            "says": st["says"],               # v2.18: фраз-сообщений мозга
            "cloud_says": st["cloud_says"],   # v2.18: ушли в облако
            "queued": len(_THOUGHT_Q),        # v2.20: ждут сборки в текст
            "link": dict(_LINK),              # v2.60: связь мозгов с облаком
            "grav": dict(_GRAV),              # v2.66: гравитация мыслей
            "game": game_status(),            # v2.69: игра «РОК-12»
            "mic": mic_status(),              # v2.70: уши мозга
            "cam": dict(_CAM_ST, ai=camai_status()),   # v2.73/v2.88
            "breath": breath_status(),        # v2.74: общее дыхание
            "sdict": sdict_status(),          # v2.75: общий словарь
            "reads": read_status(),           # v2.91: что мозг прочитал сам
            "lead": dict(st.get("lead") or {})}  # v2.40: кто вёл мысль


def _gen_texts():
    """Все тексты, которые мозг знает: диалоги, файлы, заметки наставника,
    советы (файл advice), найденное в облаке — сырьё для сети знаний."""
    with SH.lock:
        texts = [str(m.get("text") or "") for m in SH.memory]
        texts += [str(d.get("text") or "")[:4000] for d in SH.docs
                  if d.get("text")]
        texts.append(str(SH.teacher_note or ""))
    try:                       # v2.91: прочитанные статьи — учат связной речи
        _read_load()
        with _READ_LOCK:
            texts += [str(x.get("x") or "")[:READ_CHARS] for x in _READ["items"]]
    except Exception:
        pass
    try:
        adv = _advice_load()
        for rec in adv.values():
            if isinstance(rec, dict):
                texts.append(str(rec.get("hint") or ""))
                texts.append(" ".join(str(k) for k in (rec.get("keys") or [])))
    except Exception:
        pass
    return [t for t in texts if t.strip()]


def _gen_rebuild():
    """Пересобрать биграммную сеть знаний (фон). Узлы — только словарные
    слова: генерация физически не может собрать выдумку."""
    big, uni = {}, {}
    for t in _gen_texts():
        # v2.65: связи — только между СОСЕДНИМИ словами настоящего текста.
        # Раньше короткие слова («в», «и», «на») выбрасывались, и пары
        # склеивались через них: «воды в облаках» -> «воды облаках».
        # Теперь служебные слова — тоже узлы, а пара через выброшенное
        # слово (не из словаря) не создаётся; знаки препинания рвут цепочку.
        ws = []
        for tok in re.findall(r"[а-яёa-z]+|[.,!?;:—()]", (t or "").lower()):
            w = _norm_word(tok)
            if w in _GEN_FUNC or (len(w) >= 3 and dict_has(w)):
                ws.append(w)
            else:
                ws.append("")                 # разрыв цепочки
        ws = ws[:1200]                   # v2.91: статьи длиннее реплик
        for i in range(len(ws) - 1):
            a, b = ws[i], ws[i + 1]
            if not a or not b or a == b:
                continue
            uni[a] = uni.get(a, 0) + 1
            nxt = big.setdefault(a, {})
            nxt[b] = nxt.get(b, 0) + 1
    links = sum(sum(d.values()) for d in big.values())
    by_stem = {}                       # v2.17: основа -> словарные слова
    for w in uni:
        by_stem.setdefault(w[:5], []).append(w)
    for k in by_stem:
        by_stem[k] = sorted(by_stem[k], key=len)[:8]
    rev = {}                           # v2.66: кто ведёт К слову (для тяги)
    for a_, nx_ in big.items():
        for b_ in nx_:
            rev.setdefault(b_, []).append(a_)
    st = _GEN_STATE
    st["rev"] = rev
    st["big"], st["uni"], st["links"] = big, uni, links
    st["by_stem"] = by_stem
    st["built"] = time.time()
    st["busy"] = False
    log("ГЕНЕРАТОР", "сеть знаний: %d слов, %d связей (источников: %d)" %
        (len(uni), links, len(st["big"])))


def _gen_rebuild_async():
    if _GEN_STATE["busy"]:
        return

    def _job():
        try:
            _gen_rebuild()
        except Exception as e:
            _GEN_STATE["busy"] = False
            _GEN_STATE["errs"] = str(e)[:120]
            log("ГЕНЕРАТОР:", "сборка не удалась:", str(e)[:100])
    _GEN_STATE["busy"] = True
    threading.Thread(target=_job, daemon=True).start()


# ===========================================================================
# v2.66: «ГРАВИТАЦИЯ МЫСЛЕЙ» — МОДЕЛЬ С ГРАВИТАЦИОННОЙ МЕТАФОРОЙ, НЕ ФИЗИКА.
#   Физически сигналы платы электрические, и «напряжение» нейросети не
#   создаёт заметной гравитации. Здесь гравитация — правило выбора слов:
#   • МАССА понятия = его активация в зонной сети (горящие нейроны) + слова
#     темы (то, о чём спросили/подумали) + уже сказанные слова фразы (слабее);
#   • НАПРЯЖЕНИЕ сети = суммарная масса (0..1: 1 - e^(-M/3)); чем сильнее
#     сеть «напряжена», тем сильнее тяга: G = grav_g · (0,5 + напряжение);
#   • РАССТОЯНИЕ = шаги по сети знаний (биграммам): само понятие — 0,5,
#     сосед (ведёт к нему или из него) — 1, через одно слово — 2;
#   • ТЯГА к слову-кандидату F = Σ G·m / d² по всем массам; вес продолжения
#     умножается на (1 + F), не больше ×6. Фраза «падает» к самым тяжёлым
#     (самым активным) понятиям, а не блуждает.
# ===========================================================================
_GRAV = {"strain": 0.0, "g": 0.0, "masses": [], "uses": 0, "last": "",
         "pulled": "", "ts": 0.0}


def grav_field(hot, seeds=()):
    """Поле тяги {слово: F} и напряжение сети."""
    if not CFG.get("grav_on", True):
        return {}, 0.0
    try:
        g0 = max(0.0, min(3.0, float(CFG.get("grav_g", 1.0))))
    except (TypeError, ValueError):
        g0 = 1.0
    if g0 <= 0:
        return {}, 0.0
    st = _GEN_STATE
    masses = {}
    for f, a in (hot or {}).items():
        masses[str(f)[:5]] = masses.get(str(f)[:5], 0.0) + float(a)
    for w in seeds or ():
        k = _norm_word(w)[:5]
        if k:
            masses[k] = masses.get(k, 0.0) + 1.0
    if not masses:
        return {}, 0.0
    top = sorted(masses.items(), key=lambda kv: -kv[1])[:12]
    M = sum(m for _k, m in top)
    strain = 1.0 - math.exp(-M / 3.0)
    G = g0 * (0.5 + strain)
    try:                                        # v2.74: тяга дышит ±20 %
        G = breath_mod("Гравитация мыслей", G, 0.20)
    except Exception:
        pass
    big, rev, by_stem = st.get("big") or {}, st.get("rev") or {}, \
        st.get("by_stem") or {}
    field = {}

    def add(w, v):
        field[w] = field.get(w, 0.0) + v
    for stem, m in top:
        core = by_stem.get(stem) or []
        ring1 = set()
        for w in core:
            add(w, G * m / 0.25)                  # d = 0.5
            ring1.update((big.get(w) or {}).keys())
            ring1.update(rev.get(w) or [])
        ring1 -= set(core)
        ring2 = set()
        for w in list(ring1)[:200]:
            add(w, G * m / 1.0)                   # d = 1
            ring2.update(list((big.get(w) or {}).keys())[:20])
        ring2 -= ring1 | set(core)
        for w in list(ring2)[:400]:
            add(w, G * m / 4.0)                   # d = 2
    for w in _GEN_FUNC:                           # служебные слова — без массы
        field.pop(w, None)
    _GRAV.update(strain=round(strain, 2), g=round(G, 2),
                 masses=[(k, round(m, 2)) for k, m in top[:5]],
                 ts=time.time())
    return field, strain


def _gen_weighted(cands, zone, hot=None, grav=None):
    """Взвешенный выбор кандидата [(слово, счёт)] — v2.75: формула общая с
    облаком (shareddict.SharedDict.pick): база — частота связи; бонус —
    голоса зонной сети за зону, активные нейроны (hot), тяга гравитации;
    плюс частота генератора, при которой слово звучало (резонанс с текущей),
    и общее дыхание тензорной сети (вдох — шире, выдох — увереннее)."""
    cs = _camai_word_scores([w for w, _c in cands])   # v2.89: камера голосует за слово
    zv = None
    if zone:
        def zv(w):
            return nn_votes([w[:5]]).get(zone, 0.0)
    try:
        if not cs:
            return sdict().pick(cands, hot=hot, zone_vote=zv, grav=grav,
                                hz=_signed_hz(), breath=breath_s())
        # голос камеры множит ИТОГОВЫЙ вес слова (после частоты, зон, нейронов,
        # тяги и дыхания); 0.3 — нейтраль: увиденное тянет, противоположное гасит
        k = float(CFG.get("cam_ai_w", 0.35) or 0)
        sd, hz_, br_ = sdict(), _signed_hz(), breath_s()
        items, ws = [], []
        for w, c in cands:
            wt = sd.weight(w, c, hot, zv, grav, hz_, br_)
            sc = cs.get(str(w).lower())
            if sc is not None:
                wt *= max(0.15, 1.0 + 4.0 * k * (sc - 0.3))
            items.append(w)
            ws.append(max(0.0, wt))
        tot = sum(ws)
        if not items or tot <= 0:
            return random.choice(items) if items else ""
        r, acc = random.uniform(0, tot), 0.0
        for w, wt in zip(items, ws):
            acc += wt
            if r <= acc:
                return w
        return items[-1]
    except Exception:
        items = [w for w, _c in cands]
        return random.choice(items) if items else ""


def _gen_pool(top=120):
    """Частотный пул словарных слов сети знаний — для «прыжков»."""
    st = _GEN_STATE
    if not st["uni"]:
        return []
    return [w for w, _c in sorted(st["uni"].items(),
                                  key=lambda kv: -kv[1])
            if w not in _GEN_FUNC][:top]        # v2.65: без «и», «в»


def brain_speak(seed_text="", zone=None, speak=False, remember=False,
                announce=False):
    """Сгенерировать фразу СЛОВАМИ ИЗ СЛОВАРЯ по тому, что мозг знает.
    v2.17: генератор СЛУШААТ НЕЙРОНЫ — стартовое слово и приоритет
    продолжений берутся у самых активных нейронов сети (их зажигает всё
    слышанное). seed_text — тема (команда «мысль …»); зону подсказывает
    сеть. speak — озвучить, remember — запомнить, announce — вывести фразу
    как СООБЩЕНИЕ МОЗГА (чат платы + облако + дубликат нейронам).
    Возврат: фраза | ''."""
    st = _GEN_STATE
    if st["links"] < GEN_MIN_LINKS or not st["uni"]:
        return ""
    # v2.40: ТЕМУ МЫСЛИ ВЕДЁТ ТЕНЗОРНАЯ СЕТЬ (зонная сеть помогает голосом);
    # тензор не готов — зонная сеть, нет и её голоса — ключевые слова
    if zone:
        lead = {"zone": zone, "p": 1.0, "by": "задано", "tn": False}
    else:
        lead = tn_lead_zone(seed_text) if seed_text else \
            {"zone": "", "p": 0.0, "by": "", "tn": False}
        zone = lead.get("zone") or ""
    # — СЛУШААМ НЕЙРОНЫ: кто сейчас говорит и какие слова «проговаривает» —
    nwords = nn_listen_words()
    hot = nn_hot_stems()
    st["listen"] = {"ts": time.time(), "hot": nn_listen(top=4),
                    "words": [w for w, _s in nwords[:6]]}
    if not zone and nwords:            # тему подсказывают активные нейроны
        lead = tn_lead_zone(" ".join(w for w, _s in nwords[:8]))
        zone = lead.get("zone") or ""
    # генератор собирает варианты, тензор выбирает самый «тематичный»
    cands = []
    for _k in range(GEN_CANDIDATES):
        ph = _gen_compose(seed_text, zone, nwords, hot)
        if ph and ph not in cands:
            cands.append(ph)
    if not cands:
        return ""
    phrase, pick = _tn_pick_thought(cands, zone)   # v2.89: камера — в выборе слов
    st["lead"] = {"zone": zone, "zone_by": lead.get("by"),
                  "zone_p": lead.get("p"), "pick_by": pick["by"],
                  "score": pick["score"], "cands": pick["cands"],
                  "ts": round(time.time(), 1)}
    gen_register(phrase, speak=speak, remember=remember, announce=announce)
    _camai_say(phrase)                 # v2.88: камера учит слова мозга
    return phrase


def _gen_compose(seed_text, zone, nwords, hot):
    """v2.40: ОДИН вариант фразы мозга (прежнее тело brain_speak: старт от
    темы / активных нейронов / частотного пула, биграммы, знаки). Мысль
    собирают несколько раз — выбор за тензорной сетью."""
    st = _GEN_STATE
    seeds = [_norm_word(w) for w in
             re.findall(r"[а-яёa-z]{3,}", (seed_text or "").lower())]
    seeds = [w for w in seeds if dict_has(w)]
    try:                                        # v2.66: поле тяги на фразу
        grav, _strain = grav_field(hot, seeds)
    except Exception:
        grav = {}
    cur = ""
    if seeds:                                   # старт: связанное с темой
        random.shuffle(seeds)
        for s in seeds[:8]:
            if st["big"].get(s):
                cur = s
                break
        if not cur:
            cur = seeds[0]
    if not cur and nwords:                      # старт: слова активных нейронов
        pool = [(w, s) for w, s in nwords if st["big"].get(w)] or nwords
        cur = _gen_weighted(pool, zone, hot, grav)
        if cur and st["listen"].get("hot"):
            log("ГЕНЕРАТОР", "слушаю нейроны: %s -> старт «%s»" %
                (", ".join("#%d %s a=%.2f" % (h["id"], h["zone"], h["a"])
                           for h in st["listen"]["hot"][:3]), cur))
    if not cur:                                 # нейроны тихи — частотный пул
        pool = _gen_pool()
        if pool and grav:                       # v2.66: старт «падает» к массам
            cur = _gen_weighted([(w, 1) for w in pool] +
                                [(w, 1) for w in sorted(
                                    grav, key=lambda k: -grav[k])[:20]
                                 if w not in _GEN_FUNC and st["big"].get(w)],
                                zone, None, grav)
        else:
            cur = random.choice(pool) if pool else ""
    if not cur:
        return ""
    out = [cur]
    jumps = 0
    try:                                        # v2.74: длина мысли дышит
        _gmax = max(3, int(round(breath_mod("Генератор фраз", GEN_MAX_WORDS, 0.25))))
    except Exception:
        _gmax = GEN_MAX_WORDS
    for _ in range(_gmax - 1):
        succ = dict(st["big"].get(cur) or {})
        try:                                    # v2.75: + связи общего словаря
            for w_, c_ in sdict().successors(cur, accept=dict_has):
                if w_ not in succ:
                    succ[w_] = c_
        except Exception:
            pass
        nxt = ""
        if succ:
            # v2.83: жёстче отсекаем бессвязные пары (нейроны/генератор
            # не должны тащить «ахинею» рядом со связными словами; скетч
            # нейронов не меняем — фильтр только в Python-мозге)
            cands = [(w, c * gen_pen(cur, w)) for w, c in succ.items()]
            good_ = [(w, c) for w, c in cands if gen_pen(cur, w) >= 0.45]
            if not good_:
                good_ = [(w, c) for w, c in cands if gen_pen(cur, w) >= 0.3]
            nxt = _gen_weighted(good_ or [], zone, hot, grav) if good_ else ""
        rep_ = bool(nxt) and nxt in out and nxt not in _GEN_FUNC
        if (not nxt or rep_) and jumps < 1 and len(out) < 3:
            jumps += 1                          # «прыжок» — только в начале
            pool = [w for w in _gen_pool() if gen_pen(cur, w) >= 0.45]
            nxt = random.choice(pool) if pool else ""
        if not nxt or (nxt in out and nxt not in _GEN_FUNC):
            break
        out.append(nxt)
        cur = nxt
    while len(out) > 1 and out[-1] in _GEN_FUNC:
        out.pop()                               # «… помогать тому кто» -> без хвоста
    if grav:                                    # v2.66: к чему «упала» фраза
        _GRAV["uses"] += 1
        best_ = max(out, key=lambda w: grav.get(w, 0.0))
        if grav.get(best_, 0.0) > 0:
            _GRAV["pulled"] = best_
        _GRAV["last"] = " ".join(out)[:120]
    # v2.38: ЗНАКИ МОЗГ ВЫБИРАЕТ (запятая/точка/!/ ? — записи словаря):
    # между словами иногда запятая (максимум две), финальный знак — по
    # подсказке зоны (Вопросы -> «?», Юмор -> «!»), без подсказки — по
    # весам. Сеть знаний (биграммы) НЕ тронута: знаки — слой сборки фразы.
    # v2.65: случайные запятые убраны — они делали фразу ещё бессвязнее
    if zone == "Вопросы":                        # вопрос — вопросительным
        end = "?" if random.random() < 0.7 else "."
    elif zone == "Юмор":                         # шутка — восклицательным
        end = "!" if random.random() < 0.7 else "."
    else:
        r = random.random()
        end = "." if r < 0.6 else ("!" if r < 0.8 else "?")
    phrase = " ".join(out).strip(" ,")
    if not phrase:
        return ""
    phrase = phrase[0].upper() + phrase[1:] + end
    return phrase


_D6NEG = {"on": False, "on4": False, "pol4": 0, "last": None, "neg": 0,
          "pos": 0, "ts": 0.0, "err": "", "by": "", "llm": 0, "delta": 0,
          "hz": 0}
_D6CAM = {"on": False, "on4": False, "d4_sent": 0, "d4_ts": 0.0,
          "d4_next": 0.0, "mode": "pure", "sent": 0, "ts": 0.0, "busy_until": 0.0, "err": "",
          "src": "", "neg": False}


def _nf_cloud_sign(text):
    """v2.47: знак для СОБСТВЕННОЙ фразы платы решает своя LLM облака
    (POST /api/negfreq/decide). 0 — не решила / облако недоступно."""
    try:
        url, token = _cloud_base()
        if not url:
            return 0
        r, _e = _http_json_err(url + "/api/negfreq/decide",
                               {"text": str(text)[:300]}, token=token,
                               timeout=3)
        if r and r.get("ok"):
            return int(r.get("sign") or 0)
    except Exception:
        pass
    return 0


def _d4_apply(sign):
    """v2.48: знак частоты на D4 — полярность меандра при той же частоте
    (+1: высокий уровень 1/3 периода, -1: 2/3; 0 — симметричный). RPC d4pol."""
    sign = -1 if sign < 0 else (1 if sign > 0 else 0)
    if sign == _D6NEG["pol4"]:
        return
    try:
        _bridge_call("d4pol", str(sign), wait=False)
        _D6NEG["pol4"] = sign
    except Exception as e:
        _D6NEG["err"] = str(e)[:80]


def _d6_negfreq_send(phrase="", sign=0, ask=False):
    """v2.46-v2.48: опции облака «отрицательная частота» на D6 (d6_negfreq) и
    на D4 (d4_negfreq). Знак: sign от облака (его решила своя LLM по тексту
    мысли; для собственной фразы ask=True спрашивает облако), иначе по Δf:
    частота ниже сглаженной (_TN["hz_ema"]) больше чем на 3 Гц -> «минус».
    D6: после фразы уходит КАДР [0xFF|0xFE][старший][младший байт |Гц|]
    (в генераторе на странице: «−440 Гц»). D4: меняется полярность меандра
    (частота та же). Это КОД знака, а не физически отрицательная частота:
    пины 0…3,3 В, несущая D6 500 Гц."""
    if not (_D6NEG["on"] or _D6NEG["on4"]):
        return
    hz = int(SH.gen_hz or 0)
    if hz <= 0:
        return
    by = "LLM" if sign else ""
    if not sign and ask:
        sign = _nf_cloud_sign(phrase)
        by = "LLM" if sign else ""
    if not sign:
        ema = float(_TN.get("hz_ema") or 0)
        sign = -1 if (ema > 0 and ema - hz > 3.0) else 1
        by = "Δf"
    signed = sign * min(65535, hz)
    try:
        if _D6NEG["on"]:
            _bridge_call("d6freq", str(signed), wait=False)
        if _D6NEG["on4"]:
            _d4_apply(sign)
        _D6NEG["last"], _D6NEG["ts"], _D6NEG["err"] = signed, time.time(), ""
        _D6NEG["by"], _D6NEG["hz"] = by, hz
        _D6NEG["neg" if signed < 0 else "pos"] += 1
        _D6NEG["llm" if by == "LLM" else "delta"] += 1
    except Exception as e:
        _D6NEG["err"] = str(e)[:80]


# ---- v2.48: кадр ESP32-CAM растром 16x12 на D6 -----------------------------
D6_IMG_CYCLE_S = 207 * 4 * 0.05        # одна развёртка: 207 циклов по 4 слота


def _cam_raster(jpeg, w=16, h=12):
    """JPEG -> w*h точек (1..254) | None. Сначала Pillow на плате, иначе
    уменьшает облако (/api/vision/raster) — декодера JPEG у платы нет."""
    try:
        from PIL import Image, ImageOps
        import io
        im = Image.open(io.BytesIO(jpeg))
        im.load()
        g = ImageOps.autocontrast(im.convert("L").resize((w, h),
                                                         Image.BOX))
        _D6CAM["src"] = "плата"
        return bytes(1 + (v * 253) // 255 for v in g.getdata())
    except Exception:
        pass
    try:
        url, token = _cloud_base()
        if not url:
            return None
        r, _e = _http_json_err(url + "/api/vision/raster",
                               {"img": base64.b64encode(jpeg).decode("ascii"),
                                "w": w, "h": h}, token=token, timeout=20)
        if r and r.get("ok") and len(str(r.get("hex") or "")) == w * h * 2:
            _D6CAM["src"] = "облако"
            return bytes.fromhex(r["hex"])
        _D6CAM["err"] = str((r or {}).get("error") or _e or "нет растра")[:80]
    except Exception as e:
        _D6CAM["err"] = str(e)[:80]
    return None


_BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def _cam_bits_hex(jpeg):
    """JPEG -> hex «чистого кадра» 32x24 (дизеринг Байера 4x4, 4 точки на
    hex-знак) | None. Общий для D6 (d6pure) и D4 (d4pure)."""
    ras = _cam_raster(jpeg, 32, 24)
    if not ras or len(ras) != 768:
        return None
    bits = []
    for i, v in enumerate(ras):
        g = (v - 1) * 255 // 253
        x, y = i % 32, i // 32
        bits.append(1 if g > _BAYER4[y % 4][x % 4] * 16 + 8 else 0)
    return "".join("%x" % (bits[i] * 8 + bits[i + 1] * 4 + bits[i + 2] * 2 +
                           bits[i + 3]) for i in range(0, 768, 4))


def _cam_neg():
    return 1 if (_D6NEG["on"] or _D6NEG["on4"]) and \
        _D6NEG["last"] is not None and _D6NEG["last"] < 0 else 0


def _cam_pure(jpeg):
    """v2.49: «чистый кадр» на D6 — без PWM и без байтов: 32x24 чёрно-белых
    точек, RPC d6pure "neg:hex"; скетч рисует кадр уровнем сигнала (Манчестер,
    ~1,7 с). Отставание от мысли pi/2; при «минусе» кадр опережает мысль."""
    hx = _cam_bits_hex(jpeg)
    if not hx:
        return False
    neg = _cam_neg()
    _bridge_call("d6pure", "%d:%s" % (neg, hx), wait=False)
    _D6CAM["neg"] = bool(neg)
    _D6CAM["busy_until"] = time.time() + 4.0
    return True


# ---- v2.51: «ГЛУБОКОЕ ДЫХАНИЕ» КАДРА НА D4 ПОД УПРАВЛЕНИЕМ ТЕНЗОРНОЙ СЕТИ ----
# Частоту кадра на D4 (знаковую, -2000..+2000 Гц) и темп кадров задаёт ТОЛЬКО
# внутренняя тензорная сеть платы (_TN): ни LLM, ни 12 нейронов в этом не
# участвуют (входы нейронов сети обнулены). Сеть смотрит на последнюю мысль
# (признаки текста) и выдаёт распределение по зонам p; нормированная энтропия
# e = H(p)/ln(Z) в [0..1] — «неуверенность»:
#   период вдоха-выдоха  T = 4 + 8*e   с   (неуверена — дышит медленнее)
#   глубина              d = 1 - 0.65*e   (уверена — размах до ±2000 Гц)
#   пауза между кадрами  I = 3 + 7*e   с   (уверена — кадры чаще)
# Пока сеть не готова (мало примеров), берётся её же размер H, который
# задаёт генератор: t = (H - H_MIN)/(H_MAX - H_MIN): T = 10 - 5t, d = 0.6,
# I = 8 - 4t. Фаза дыхания непрерывна от кадра к кадру.
# ---- v2.74: ОБЩЕЕ ДЫХАНИЕ всех ИИ-алгоритмов платы, синхронное с облаком
# и другими платами. Своё дыхание платы — от тензорной сети (период вдоха-
# выдоха по её неуверенности, ниже). Облако (v3.132+) присылает общий
# период (от частоты генераторов плат) и фазу (круговое среднее дыханий
# плат) — плата подтягивает фазу к общей (половиной разницы за раз) и
# берёт общий период. Вдох (s > 0) — учёба и чтение; выдох — выдача.
_BREATH = {"t0": time.time(), "period": 8.0, "src": "своё (тензорная сеть)",
           "cloud_ts": 0.0, "uses": {}}
BOARD_ALGOS = [
    ("Зонная сеть", "учёба: сила подкрепления связей ±15 %", "Обучение"),
    ("Тензорная сеть", "источник дыхания платы: период по её неуверенности", "Сигнал"),
    ("Сеть материалов платы", "учёба на новых материалах — только на вдохе", "Знания"),
    ("Генератор фраз", "длина мысли ±25 %", "Буквы"),
    ("Гравитация мыслей", "сила тяги ±20 %", "Память"),
    ("Игрок «РОК-12»", "разведка (шум действий) ±30 %", "Юмор"),
    ("Буквогенератор скетча", "частота генератора ±8 % вокруг общего ритма", "Буквы"),
    ("Любопытство платы", "чтение новых тем — только на вдохе", "Любопытство"),
    ("Чтение статей", "v2.91: чего не знает — читает в Википедии/Рувики, на вдохе", "Знания"),
    ("Учитель и диктант", "оценки и учёба букв — только на вдохе", "Обучение"),
]
_BZONE = {a[0]: a[2] for a in BOARD_ALGOS}
_BZONE.update({"Слух": "Сигнал", "Зрение": "Камера"})
# v2.76: ВОЛЯ МОЗГА РОУЗ — активность зон зонной сети (0..1, раз в 5 с):
# зона активна — её алгоритмы сильнее (до ×1.5), молчит — тише (до ×0.5).
_WILL = {"z": {}, "ts": 0.0}


def brain_will():
    """Активность каждой зоны мозга 0..1 (самый активный нейрон зоны)."""
    now = time.time()
    if now - _WILL["ts"] < 5.0 and _WILL["z"]:
        return _WILL["z"]
    z = {name: 0.0 for name, _k in ZONES}
    try:
        with _NN_LOCK:
            for n in _NN["neurons"]:
                a = float(n.get("a", 0.0)) / float(NN_ACT_MAX or 1.0)
                zn = n.get("zone")
                if zn in z and a > z[zn]:
                    z[zn] = max(0.0, min(1.0, a))
    except Exception:
        pass
    _WILL.update(z={k: round(v, 3) for k, v in z.items()}, ts=now)
    return _WILL["z"]


def brain_lead():
    """v2.90: СТРОГАЯ СВЯЗЬ — ведущая зона мозга (самая активная), её сила и
    последняя мысль мозга. Облако v3.166 по ней решает, какие свои процессы
    пускать, а своя LLM пишет, отталкиваясь от этой мысли."""
    z = brain_will() or {}
    zone, a = "", 0.0
    for k, v in z.items():
        if v > a:
            zone, a = k, float(v)
    t = str(_LAST_THOUGHT.get("t") or "")
    ts = float(_LAST_THOUGHT.get("ts") or 0.0)
    return {"zone": zone, "a": round(a, 3), "thought": t[:220],
            "t_age": int(time.time() - ts) if ts else None}


def will_strength(name):
    try:
        return 0.5 + brain_will().get(_BZONE.get(name, ""), 0.0)
    except Exception:
        return 1.0


def breath_sync(br):
    """Общее дыхание от облака (среднее ТЕНЗОРНЫХ СЕТЕЙ плат): период,
    глубина, фаза -> подтянуть своё (фазу — половиной разницы)."""
    try:
        T = float(br.get("period"))
        ph_c = float(br.get("phase")) % 1.0
        D = float(br.get("depth") or _BREATH.get("depth") or 0.6)
    except (TypeError, ValueError, AttributeError):
        return
    if not (2.0 <= T <= 30.0) or not CFG.get("sync_on", True):
        return
    now = time.time()
    ph_b = ((now - _BREATH["t0"]) / _BREATH["period"]) % 1.0
    d = ((ph_c - ph_b + 0.5) % 1.0) - 0.5
    ph_new = (ph_b + 0.5 * d) % 1.0
    _BREATH.update(period=T, depth=max(0.2, min(1.0, D)), t0=now - ph_new * T,
                   cloud_ts=now, src="общее: тензорные сети плат (через облако)")


def breath_tn_tick():
    """v2.75: своя ТЕНЗОРНАЯ СЕТЬ задаёт дыхание платы — раз в 10 с её
    неуверенность e -> период T = 4 + 8·e, глубина d = 1 − 0.65·e."""
    now = time.time()
    if now - float(_BREATH.get("tn_ts") or 0) < 10.0:
        return
    _BREATH["tn_ts"] = now
    try:
        T, d, _I, e, src = _tn_breath_params()
    except Exception:
        return
    _breath_mark("Тензорная сеть")
    _BREATH.update(tn_T=T, tn_d=d, tn_e=e, tn_src=src)


def breath_s(now=None):
    """Дыхание платы -d..d (вдох > 0); d — глубина тензорной сети."""
    now = now or time.time()
    if now - _BREATH["cloud_ts"] > 120:          # облака нет — своя сеть
        T_own = float(_BREATH.get("tn_T") or 8.0)
        if abs(T_own - _BREATH["period"]) > 0.01:
            ph = ((now - _BREATH["t0"]) / _BREATH["period"]) % 1.0
            _BREATH.update(period=T_own, t0=now - ph * T_own)
        _BREATH["depth"] = float(_BREATH.get("tn_d") or 0.6)
        _BREATH["src"] = "своя тензорная сеть (%s)" % (_BREATH.get("tn_src") or "ещё не считала")
    return float(_BREATH.get("depth") or 0.6) * \
        math.sin(2 * math.pi * ((now - _BREATH["t0"]) / _BREATH["period"]))


def _breath_mark(name):
    u = _BREATH["uses"].setdefault(name, [0, 0.0])
    u[0] += 1
    u[1] = time.time()


def breath_mod(name, x, depth=0.1):
    """Дыхание тензорной сети × воля мозга (сила зоны алгоритма)."""
    _breath_mark(name)
    return x * (1.0 + depth * will_strength(name) * breath_s())


def breath_inhale(name):
    """Вдох — и мозг не против: у приглушённой зоны часть вдохов пропускается."""
    _breath_mark(name)
    if breath_s() < 0.0:
        return False
    return random.random() < min(1.0, will_strength(name))


def breath_status():
    now = time.time()
    s_ = breath_s(now)
    return {"period": round(_BREATH["period"], 2), "s": round(s_, 3),
            "state": "вдох" if s_ >= 0 else "выдох", "src": _BREATH["src"],
            "depth": round(float(_BREATH.get("depth") or 0.6), 2),
            "tn": {"T": _BREATH.get("tn_T"), "d": _BREATH.get("tn_d"), "e": _BREATH.get("tn_e")},
            "phase": round(((now - _BREATH["t0"]) / _BREATH["period"]) % 1.0, 3),
            "algos": [{"name": n, "how": h, "zone": z_,
                       "strength": round(will_strength(n), 2),
                       "uses": (_BREATH["uses"].get(n) or [0, 0])[0]}
                      for n, h, z_ in BOARD_ALGOS],
            "will": dict(brain_will()),
            "lead": brain_lead(),                     # v2.90
            "cloud_ctl": _LINK.get("ctl")}


BREATH_MAX_HZ = 2000
BREATH_ROWS = 24
BREATH_ROW_S = 0.09            # грубая длительность строки для фазы профиля
_BR = {"t0": 0.0, "period": 8.0, "depth": 0.6, "interval": 8.0, "e": None,
       "src": "", "ts": 0.0, "frames": 0, "lo": 0, "hi": 0, "ready": False,
       "next": 0.0, "words_rows": 0, "breath_hz": 0, "text": "", "learned": 0}


def _tn_breath_params():
    """Тензорная сеть -> (период, глубина, интервал, e, источник)."""
    text = str(SH.say or "").strip()
    e = None
    try:
        feats = tn_features(text) if text else []
        if feats:
            x, _z, _w = _TN_K["tn_parse"](_tn_token(feats, [0] * TN_HW, 0),
                                          TN_D)      # входы нейронов = 0
            with _TN_LOCK:
                if _tn_ready_locked():
                    _h, p = _TN_K["tn_forward"](_TN["W1"], _TN["b1"],
                                                _TN["W2"], _TN["b2"], x)
                    zz = max(2, len(p))
                    ent = -sum(v * math.log(v) for v in p if v > 1e-9)
                    e = max(0.0, min(1.0, ent / math.log(zz)))
    except Exception:
        e = None
    if e is not None:
        return (4.0 + 8.0 * e, 1.0 - 0.65 * e, 3.0 + 7.0 * e, e, "сеть: зоны")
    h = float(_TN.get("H") or 0)
    t = 0.0
    if h > 0:
        t = max(0.0, min(1.0, (h - TN_H_MIN) / float(TN_H_MAX - TN_H_MIN)))
    return (10.0 - 5.0 * t, 0.6, 8.0 - 4.0 * t, None, "сеть: размер H")


_BR_WORDS = {"text": "", "lv": None, "ts": 0.0}


def _cloud_word_levels(text):
    """v2.52: уровень каждого слова фразы -1..1 по таблице облака «уровень
    сигнала ↔ слова» (POST /api/genmod/words) | None. Кэш 60 с."""
    now = time.time()
    if _BR_WORDS["text"] == text and now - _BR_WORDS["ts"] < 60.0:
        return _BR_WORDS["lv"]
    lv = None
    try:
        url, token = _cloud_base()
        if url and text:
            r, _e = _http_json_err(url + "/api/genmod/words",
                                   {"text": text[:600]}, token=token,
                                   timeout=3)
            if r and r.get("ok") and int(r.get("known") or 0) > 0:
                lv = list(r.get("levels") or [])
    except Exception:
        lv = None
    _BR_WORDS.update({"text": text, "lv": lv, "ts": now})
    return lv


BREATH_WORD_MIX = 0.6          # доля слова в частоте строки (остальное — дыхание)


def _breath_profile(now=None):
    """24 знаковых частоты (Гц, -2000..2000) кадра на D4. Основа — «глубокое
    дыхание» тензорной сети: s(t) = sin(2*pi*t/T). Строка r соответствует
    слову фразы (слова поровну по строкам); если слово есть в таблице «сигнал
    ↔ слова» облака, его уровень L (-1…1: слово шло с низким/высоким
    сигналом) входит в строку: f = 2000*глубина*(0.6*L + 0.4*s); неизвестное
    слово — чистое дыхание. Возврат профиль; в _BR пишутся: доля строк со
    словами, среднее дыхание (без слов) для обучения облака."""
    now = now or time.time()
    T, d, I, e, src = _tn_breath_params()
    _breath_mark("Тензорная сеть")
    _BR.update({"period": T, "depth": d, "interval": I, "e": e, "src": src,
                "ts": now, "ready": e is not None})
    if not _BR["t0"]:
        _BR["t0"] = now
    if now - _BREATH["cloud_ts"] < 120:     # v2.74: общее дыхание с платами
        T = _BREATH["period"]
        _BR["t0"] = _BREATH["t0"]
        _BR["src"] = src + "; ритм общий с облаком"
    text = str(SH.say or "").strip()
    lv = _cloud_word_levels(text)
    n = len(lv) if lv else 0
    prof, used, breath = [], 0, []
    for r in range(BREATH_ROWS):
        ph = 2.0 * math.pi * ((now - _BR["t0"]) + r * BREATH_ROW_S) / T
        sr = math.sin(ph)
        breath.append(sr)
        L = lv[min(n - 1, r * n // BREATH_ROWS)] if n else None
        if L is None:
            tg = sr
        else:
            tg = BREATH_WORD_MIX * L + (1.0 - BREATH_WORD_MIX) * sr
            used += 1
        prof.append(int(round(BREATH_MAX_HZ * d * max(-1.0, min(1.0, tg)))))
    _BR["words_rows"] = used
    _BR["breath_hz"] = int(round(BREATH_MAX_HZ * d * sum(breath) / len(breath)))
    _BR["text"] = text
    return prof


def _br_learn(text, hz):
    """Фраза + знаковая частота дыхания (без слов) -> облако (учит смыслу)."""
    try:
        url, token = _cloud_base()
        if not url or len(text) < 12:
            return
        r, _e = _http_json_err(url + "/api/genmod/frame",
                               {"device": _hostname(), "text": text[:300],
                                "hz": hz, "tn": _tn_conf(text)},
                               token=token, timeout=5)
        if r and r.get("accepted"):
            _BR["learned"] = int(_BR.get("learned") or 0) + 1
    except Exception:
        pass


def _cam_to_d4(jpeg):
    """v2.50/v2.51: чистый кадр камеры на D4 (RPC d4pure "neg:hex:профиль").
    Частоту строк и паузу между кадрами задаёт тензорная сеть (см. выше);
    профиль — знаковая частота в Гц на строку: минус = код строки «−»
    (длинный низкий после синхро и обратный код точек). Опция d4_camframe."""
    if not _D6CAM["on4"] or time.time() < _BR["next"]:
        return
    hx = _cam_bits_hex(jpeg)
    if not hx:
        return
    prof = _breath_profile()
    _bridge_call("d4pure", "%d:%s:%s" % (_cam_neg(), hx,
                                         ",".join(map(str, prof))), wait=False)
    _BR["frames"] += 1
    _BR["lo"], _BR["hi"] = min(prof), max(prof)
    threading.Thread(target=_br_learn, args=(_BR.get("text", ""),
                                             _BR.get("breath_hz", 0)),
                     daemon=True).start()
    _D6CAM["d4_sent"] += 1
    _D6CAM["d4_ts"] = time.time()
    _BR["next"] = time.time() + 2.6 + _BR["interval"]     # кадр ~2.5 с + пауза


def _cam_sleep():
    """Пауза между опросами камеры: при кадрах на D4 — по темпу сети.
    v2.75: дышит вместе со всеми (вдох — кадры чаще)."""
    base = max(2.0, min(CAM_PERIOD, _BR["interval"])) if _D6CAM.get("on4") else CAM_PERIOD
    try:
        return max(1.5, breath_mod("Зрение", base, -0.30))
    except Exception:
        return base


_cam_up = [0.0]                # когда кадр последний раз ушёл в облако
# v2.73: СОСТОЯНИЕ КАМЕРЫ — почему в облаке нет картинки (раньше все ошибки
# глушились «тихо ждём»): уходит в облако по каналу связи и видно на плате
_CAM_ST = {"state": "ещё не опрашивала", "err": "", "ok_ts": 0.0, "sent_ts": 0.0,
           "fails": 0, "kb": 0, "src": "", "up_err": ""}


def _cam_state(state, err=""):
    _CAM_ST["state"] = state
    _CAM_ST["err"] = str(err or "")[:160]
    if err:
        _CAM_ST["fails"] = int(_CAM_ST.get("fails") or 0) + 1


def _jpeg_complete(b):
    """Целый ли JPEG: начало FFD8 и маркер конца FFD9 в хвосте (кадр по
    проводу/Wi-Fi мог оборваться — такой браузер не покажет)."""
    return len(b) >= 500 and b[:2] == b"\xff\xd8" and b"\xff\xd9" in b[-2048:]


def _cam_to_d6(jpeg):
    """Кадр камеры -> растр -> RPC d6img кусками по 48 точек. Не чаще, чем
    кончится прошлая развёртка (~41 с). Отставание кадра от мысли pi/2; при
    знаке «минус» (опция отрицательной частоты) точка идёт в слоте -pi/2."""
    if not _D6CAM["on"] or time.time() < _D6CAM["busy_until"]:
        return
    if _D6CAM.get("mode") != "pwm":              # v2.49: чистый кадр (по умолчанию)
        if _cam_pure(jpeg):
            _D6CAM["sent"] += 1
            _D6CAM["ts"] = time.time()
            _D6CAM["err"] = ""
        return
    ras = _cam_raster(jpeg)
    if not ras or len(ras) != 192:
        return
    neg = 1 if (_D6NEG["on"] or _D6NEG["on4"]) and \
        _D6NEG["last"] is not None and _D6NEG["last"] < 0 else 0
    for off in range(0, 192, 48):
        last = 1 if off + 48 >= 192 else 0
        _bridge_call("d6img", "%d:%d:%d:%s" % (off, neg, last,
                                               ras[off:off + 48].hex()),
                     wait=False)
    _D6CAM["sent"] += 1
    _D6CAM["ts"] = time.time()
    _D6CAM["neg"] = bool(neg)
    _D6CAM["err"] = ""
    _D6CAM["busy_until"] = time.time() + D6_IMG_CYCLE_S + 2.0


def _gen_d6_seq(phrase, sign=0, ask=True):
    """v2.19 (вместо ленты букв): буквы фразы мозга ЧЕЛОВЕКУ НЕ ВИДНЫ —
    они уходят ФИЗИЧЕСКИ: номера букв (0 = пробел/знак) одной посылкой
    в скетч (RPC d6seq), там они проигрываются PWM-импульсами на пине D6.
    Счётчик букв продолжаем вести (внутренняя статистика)."""
    seq = []
    for ch in str(phrase or ""):
        low = _norm_word(ch)
        if low and low[0] in _RU_NUM:
            seq.append(_RU_NUM[low[0]])
        elif ch in " ,.":
            seq.append(0)
        if len(seq) >= 96:                      # потолок буфера скетча
            break
    if not seq:
        return
    try:
        _bridge_call("d6seq", ",".join(map(str, seq)), wait=False)
    except Exception:
        pass
    try:
        _d6_negfreq_send(phrase, sign, ask)   # v2.47: знаковый кадр (по опции)
    except Exception:
        pass
    with SH.lock:
        SH.letters_made += sum(1 for n in seq if n)
        SH.mem_dirty = True


def gen_register(phrase, speak=False, remember=False, announce=False):
    """Зарегистрировать сгенерированную фразу: счётчики, буквы на D6 (скрытно),
    память,
    речь (TTS через облако) и диктант для буквогенератора скетча.
    v2.18: announce=True — фраза мозга выходит КАК СООБЩЕНИЕ:
      1) чат платы («Мысль») — это то, что собирается в пункте учитель;
      2) облако (v2.20, через сборщик мыслей): чат облака «🧠 Мысли плат»
         (собранный текст одним сообщением) + /api/think (лента «Мысли
         мозга» в Studio) + /api/import (хроника на главной облака);
      3) НЕЙРОНАМ — дубликат по СТАРОМУ каналу chat_stim: скетч разошлёт
         «STIM;<фраза>» всем 12 платам — учатся по уже известным каналам,
         код нейронов НЕ меняется; самое длинное слово — в диктант, буквы
         фразы скетч-сеть собирает с нашей сверкой teach_last."""
    st = _GEN_STATE
    words = [w for w in phrase[:-1].split(" ") if w]
    st["last"] = phrase[:200]
    st["last_ts"] = time.time()
    st["phrases"] += 1
    st["words"] += len(words)
    try:          # v2.17: нейроны слушают самого мозга
        nn_excite(nn_stems(phrase), 0.35)
    except Exception:
        pass
    try:          # v2.19: буквы фразы — скрытно на пин D6 (одной посылкой)
        threading.Thread(target=_gen_d6_seq, args=(phrase,),
                         daemon=True).start()
    except Exception:
        pass
    if speak and CFG.get("tts_on"):
        threading.Thread(target=tts_say, args=(phrase,),
                         daemon=True).start()
    if remember:
        brain_remember("bot", "💬 «%s»" % phrase[:180])
    if announce:
        st["says"] += 1
        # 1) ЧАТ ПЛАТЫ — фраза мозга как самостоятельное сообщение
        add_message("bot", "🧠 «%s»\n(фразу собрал сам словами из словаря — "
                    "по тому, что знаю)" % phrase[:180],
                    "Мысль")
        # 2) НЕЙРОНАМ — старый канал: chat_stim -> broadcastStim ->
        #    STIM;<фраза> всем 12 платам. Код нейронов не меняется.
        try:
            _bridge_call("chat_stim", phrase[:90], wait=False)
        except Exception:
            pass
        # 3) В ОБЛАКО — очередь сборщика мыслей (v2.20): фразы копятся и
        #    раз в THOUGHT_FLUSH уходят ОДНИМ текстом в чат облака «Мысли
        #    плат» + пофразно в ленту Studio + в хронику главной
        try:
            _thought_push(phrase)
        except Exception:
            pass
        try:                    # v2.75: мысль + частота генератора -> общий словарь
            sdict().learn(phrase, hz=_signed_hz(), accept=dict_has)
        except Exception:
            pass
    # буквогенератор скетча учится произносить слова, которые мозг реально
    # использует: самое длинное словарное слово — в диктант (если цели нет)
    try:
        if CFG.get("dictate_on") and words:
            longs = sorted(words, key=len, reverse=True)
            if longs and len(longs[0]) >= 4 and dict_has(longs[0]):
                with SH.lock:
                    has_target = bool(SH.dictation.get("target"))
                if not has_target:
                    dictation_queue(longs[0], "мысль")
    except Exception:
        pass


def gen_tick():
    """Фон основного цикла: жизнь сети (затухание/шум активаций), пересборка
    сети знаний (раз в 90 с) и «мысль вслух» в тишине (раз в GEN_PERIOD).
    v2.17: если нейроны «говорят» — мысль рождается ОТ НИХ (seed пустой,
    слова подбирает сама сеть); молчат — тема из последней реплики."""
    now = time.time()
    try:
        nn_decay_tick()                # v2.17: сеть живёт и без генерации
    except Exception:
        pass
    if not CFG.get("gen_on"):
        return
    if now - _GEN_STATE["built"] > 90.0 and not _GEN_STATE["busy"] and \
            SH.dict_ready:
        _gen_rebuild_async()
    if now - _gen_last_say[0] < GEN_PERIOD:
        return
    if _GEN_STATE["links"] < GEN_MIN_LINKS:
        return
    _gen_last_say[0] = now
    seed = ""
    if not nn_listen(top=1):           # нейроны молчат -> вспомним диалог
        with SH.lock:
            seed = next((str(m.get("text") or "")[:120]
                         for m in reversed(SH.memory)
                         if m.get("role") == "user"), "")

    def _job():
        try:
            # v2.18: мысль вслух — полноценное СООБЩЕНИЕ мозга (чат платы +
            # облако + дубликат нейронам), а не тихая речь в пустоту
            p = brain_speak(seed_text=seed, speak=True, remember=True,
                            announce=True)
            if p:
                log("ГЕНЕРАТОР", "мысль вслух:", p)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_job, daemon=True).start()


def _long_reply(q, reply, zone, note="", advice=None):
    """v2.10 (v2.14): мозг пишет КАК МОЖНО ДЛИННЕЕ — сам, без LLM-формулировщика
    (LLM-ядро осталось только УЧИТЕЛЕМ). Разворачиваем зонный ответ честным
    содержимым: совет-наставление (ключи/темы), заметка из баз знаний/поиска,
    воспоминания по теме, файлы, что пользователь загружал. Без выдумок:
    только то, что мозг знает. v2.20: если по теме есть воспоминания/знания —
    ответ начинается с НИХ, а не с шаблонной фразы (вопросы облака больше не
    получают «автоответов зон»)."""
    parts = []
    if note:
        parts.append(note.strip())
    # — совет наставника применила сама сеть: ключи ведут поиск —
    a_keys = [str(k).lower() for k in ((advice or {}).get("keys") or [])]
    if advice and (advice or {}).get("hint"):
        parts.append("Учту наставление: %s" % str(advice["hint"])[:240])
    # — воспоминания по теме (личная память устройства) —
    # v2.26: реплики ДОСТАЮТ ИЗ ПАМЯТИ ПО-НАСТОЯЩЕМУ. Раньше искали только
    # в хвосте из 16 записей и только роли bot/kb: память «их 17» была
    # пустым звуком («мост не извлекает реплики»). Теперь: ищем по ВСЕЙ
    # памяти (включая реплики ПОЛЬЗОВАТЕЛЯ), а если прямых совпадений
    # нет — честно показываем последние реплики, они и есть опыт мозга.
    words = [w for w in re.findall(r"[а-яёa-z0-9]{4,}", (q or "").lower())][:4]
    for k in a_keys:
        for kw in k.split():
            if len(kw) >= 4 and kw not in words:
                words.append(kw)                    # ключи наставника в поиске
    words = [w[:6] for w in words if len(w) >= 4][:6]   # основы для поиска
    with SH.lock:
        all_mem = list(SH.memory)
    mems, mem_who = [], []
    for m in all_mem:
        role = str(m.get("role") or "")
        if role not in ("bot", "kb", "user"):
            continue
        tl = str(m.get("text") or "").lower()
        if words and any(w in tl for w in words):
            frag = str(m.get("text") or "")[:220]
            if frag not in mems:
                mems.append(frag)
                mem_who.append({"bot": "я", "kb": "KB",
                                "user": "ты"}.get(role, role))
    if not mems:                        # прямых совпадений нет — хвост памяти
        for m in [x for x in all_mem
                  if str(x.get("role")) in ("bot", "user", "kb")][-2:]:
            frag = str(m.get("text") or "")[:220]
            if frag:
                mems.append(frag)
                mem_who.append({"bot": "я", "kb": "KB", "user": "ты"}
                               .get(str(m.get("role")), "?"))
    mems, mem_who = mems[-3:], mem_who[-3:]
    # — открывающая часть: настоящий ответ -> знания -> заглушка —
    if (reply or "").strip():
        parts.append(reply.strip())
    elif mems:
        pass                        # знания есть — начнём сразу с них
    else:
        parts.append("Тема принята: «%s». Разбираю по порядку."
                     % (q or "")[:120])
    if mems:
        parts.append("Что я помню:\n" + "\n".join(
            "— %s: %s" % (mem_who[i] if i < len(mem_who) else "…", m)
            for i, m in enumerate(mems)))
    # — документы из локального чата (пользователь загружал файлы) —
    try:
        with SH.lock:
            docs = [d for d in SH.docs if d.get("text")][-2:]
        for d in docs:
            tl = (str(d.get("text") or "") or "").lower()
            if not words or any(w in tl for w in words):
                parts.append("Из файла «%s»:\n%s" %
                             (d.get("name", "?"),
                              str(d.get("text") or "")[:500]))
    except Exception:
        pass
    # — где я сейчас: честный статус устройства (без выдумок) —
    try:
        parts.append("Где я сейчас: %s. В памяти %d реплик." %
                     (_stats_line(), len(SH.memory)))
    except Exception:
        pass
    # — контекст недавнего диалога —
    try:
        with SH.lock:
            recent = [m for m in SH.messages
                      if m.get("role") in ("user", "bot")][-2:]
        if recent:
            parts.append("Контекст диалога:\n" + "\n".join(
                "— %s: %s" % ("ты" if m.get("role") == "user" else "я",
                              str(m.get("text") or "")[:120])
                for m in recent))
    except Exception:
        pass
    # — статьи из открытых источников (личная KB + Википедия через облако) —
    try:
        if q and (not mems or len("\n".join(parts)) < 700):
            hit = kb_lookup(q)
            if hit and hit[1]:
                parts.append("Из открытых источников [%s]:\n%s" %
                             (hit[0], hit[1][:600]))
    except Exception:
        pass
    # — чем могу продолжить —
    parts.append("Глубже по теме: «/поиск <тема>» (статьи через облако) или "
                 "📎 добавь файл — прочитаю и отвечу по нему детально.")
    out = "\n\n".join(p for p in parts if p)
    return out[:3600]


# ===========================================================================
# [3g2] НЕЙРОСЕТЬ МАТЕРИАЛОВ (v2.25, mozgnet.py): сеть УЧИТСЯ на материалах
#      (загруженные файлы, KB облака, диалоги, мысли, показания) и ПИШЕТ
#      ДЛИННЫЙ ТЕКСТ по материалам (команда «статья <тема>», POST
#      /api/mozgnet/write). LLM НЕ участвует: узлы — основы слов, связи —
#      выученные ассоциации, документы — tf-idf векторы; запрос зажигает
#      узлы (разрядка активации), активные нейроны выбирают материалы и
#      предложения, из которых собирается текст. Обучение ФОНОВОЕ: mn_feed
#      только ставит материал в очередь, диск/сеть трогает mn_worker.
#      ПОКАЗАНИЯ сети (узлы/связи/активные темы) уходят в облако
#      (POST /api/chat/readings) — генератор чата облака берёт их как
#      контекст задания вместе с мыслями устройства.
# ===========================================================================
MN_LEARN_EVERY    = 15.0     # период сборки очереди обучения, сек
MN_READINGS_EVERY = 600.0    # период показаний в облако, сек
MN_FEED_MAX       = 240      # размер очереди обучения
_MNQ = []                    # очередь [(text, title, src)]
_MNQ_LOCK = threading.Lock()
_MN_LOCK = threading.Lock()
_MN_SENT = {"ts": 0.0}       # когда в последний раз уходили показания
# v2.40: КОД СЕТИ МАТЕРИАЛОВ ВСТРОЕН СЮДА (как ядро тензора _TN_KERNEL).
# App Lab не видел соседний mozgnet.py: «import mozgnet» падал, и сеть
# материалов молча выключалась. Теперь модуль собирается из текста ниже —
# отдельный файл не нужен (лежит рядом — не мешает, но не используется).
_MN_SRC = r'''# -*- coding: utf-8 -*-
# ===========================================================================
# mozgnet.py — АССОЦИАТИВНАЯ НЕЙРОСЕТЬ «МОЗГ-12» (v1.0, только stdlib)
# ---------------------------------------------------------------------------
# НАЗНАЧЕНИЕ (v2.25 платы / v2.5.0 облака):
#   Нейросеть, которая УЧИТСЯ НА МАТЕРИАЛАХ и ПИШЕТ ДЛИННЫЙ ТЕКСТ по этим
#   материалам. Никакого LLM: узлы сети — основы слов, связи — выученные
#   ассоциации (совместная встречаемость в предложениях материалов),
#   документы материалов представлены tf-idf векторами. Запрос «зажигает»
#   узлы (разрядка активации по связям), активные нейроны выбирают
#   материалы и предложения, из которых собирается связный длинный текст.
#
#   Материалы (что сеть «читает»):
#     - загруженные файлы (PDF/TXT/XLSX/PPTX/DOCX);
#     - статьи баз знаний (KB облака, личные базы устройств);
#     - диалоги и мысли устройства;
#     - ПОКАЗАНИЯ нейросети мозга (hot-темы) — тоже материал и контекст.
#
#   ПОКАЗАНИЯ (readings()): размер сети, число связей/материалов, самые
#   активные узлы последнего запроса — их облако показывает в чате и
#   использует как контекст задания.
#
# ПОТОКОБЕЗОПАСНОСТЬ: не потокобезопасна сама — снаружи держать lock
# (на плате и в облаке обёртки уже делают это). Файл состояния — JSON.
# ===========================================================================
import json
import hashlib
import math
import os
import random
import re
import time

WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}")
SENT_RE = re.compile(r"(?<=[.!?…])\s+")

# служебные слова, которые не становятся узлами сети
_STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли
если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб
без будто человек чего раз тоже себе под жизнь будет ж тогда кто этот говорил
того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее
кажется сейчас были куда зачем сказать всех никогда сегодня можно при наконец
два об другой хоть после над больше тот через эти нас про всего них какая много
разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя
такой им более всегда конечно всю между это также очень еще это как-то
""".split())

_SUFFIXES = ("иями", "ями", "ами", "иями", "иях", "ях", "ах", "ов", "ев",
             "ий", "ые", "ая", "ое", "ии", "ие", "ой", "ый", "ом", "ем",
             "ых", "их", "ую", "юю", "ья", "ье", "ьи", "ть", "ла", "ло",
             "ли", "ет", "ут", "ют", "ит", "ат", "ят", "ся", "сь", "ны",
             "на", "ко", "ка", "и", "ы", "а", "я", "о", "е", "у", "ю", "ь")


def _stem(w):
    """Грубый стем (согласован со stemmer'ом KB облака): нейронная~нейронной~нейрон."""
    for suf in _SUFFIXES:
        if len(w) > 5 and w.endswith(suf):
            return w[: len(w) - len(suf)]
    return w


def _sentences(text):
    """Разбить текст на предложения-материалы (3..280 символов)."""
    out = []
    for s in SENT_RE.split(re.sub(r"\s+", " ", str(text or "")).strip()):
        s = s.strip(" ;:-—*|")
        if 25 <= len(s) <= 280 and " " in s:
            out.append(s)
    return out


class MozgNet(object):
    """Ассоциативная сеть на материалах + писатель длинных текстов."""

    def __init__(self, docs_max=500, sents_max=3000, node_max=20000):
        self.docs_max = int(docs_max)
        self.sents_max = int(sents_max)
        self.node_max = int(node_max)
        # узлы: {стем: частота} — «нейроны»
        self.nodes = {}
        # связи: {стем: {стем2: вес}} — выученные ассоциации (биграммы)
        self.edges = {}
        # материалы: [{"id","title","src","ts","tf","n","kw"}]
        self.docs = []
        # предложения материалов: [{"d": индекс, "t": текст, "tf", "n"}]
        self.sents = []
        self.stats = {"learns": 0, "writes": 0, "queries": 0}
        self.last = {"topic": "", "hot": {}, "ts": 0.0}   # ПОКАЗАНИЯ

    # ------------------------------ обучение ------------------------------
    def learn(self, text, title="", src="", did=None):
        """Учиться на материале. Возврат: {"doc": bool, "sents": n,
        "nodes": n, "links": n} — что нового добавлено."""
        text = str(text or "")
        if len(text.strip()) < 40:
            return {"doc": False, "sents": 0, "nodes": 0, "links": 0}
        res = {"doc": False, "sents": 0, "nodes": 0, "links": 0}
        sents = _sentences(text)
        if not sents:
            return res
        if did is None:
            raw = ((str(title) or "")[:60] + "|" + text.strip()[:90]).encode(
                "utf-8", "replace")
            did = "h" + hashlib.md5(raw).hexdigest()[:12]
        if did and self._doc_idx(did) >= 0:
            return res                       # материал уже в сети
        tf = {}
        for s in sents:
            for w in WORD_RE.findall(s.lower()):
                if w in _STOP or len(w) > 23:
                    continue
                tf[w] = tf.get(w, 0) + 1
        stems = {}
        for w, c in tf.items():
            stems.setdefault(_stem(w), 0)
            stems[_stem(w)] += c
        if not stems:
            return res
        di = len(self.docs)
        kw = [w for w, _c in sorted(tf.items(), key=lambda kv: -kv[1])[:8]]
        self.docs.append({"id": did, "title": (str(title) or "")[:120],
                          "src": (str(src) or "")[:40],
                          "ts": int(time.time()), "tf": stems,
                          "n": sum(stems.values()), "kw": kw})
        res["doc"] = True
        # сеть узлов и связей
        prev = None
        for s in sents:
            ws = [w for w in WORD_RE.findall(s.lower())
                  if w not in _STOP and len(w) <= 23]
            ss = [_stem(w) for w in ws]
            for st in ss:
                self.nodes[st] = self.nodes.get(st, 0) + 1
                res["nodes"] += 1
            for a, b in zip(ss, ss[1:]):
                if a == b:
                    continue
                nxt = self.edges.setdefault(a, {})
                nxt[b] = nxt.get(b, 0) + 1
                res["links"] += 1
            # предложение — единица выдачи (с вектором)
            stf = {}
            for st in ss:
                stf[st] = stf.get(st, 0) + 1
            if stf:
                self.sents.append({"d": min(di, self.docs_max - 1),
                                   "t": s, "tf": stf,
                                   "n": sum(stf.values())})
                res["sents"] += 1
        self.stats["learns"] += 1
        self._trim()
        return res

    def _doc_idx(self, did):
        for i, d in enumerate(self.docs):
            if d.get("id") == did:
                return i
        return -1

    def _trim(self):
        """Ограничить рост сети: материалы и предложения — LRU по времени."""
        if len(self.docs) > self.docs_max:
            self.docs.sort(key=lambda d: d.get("ts", 0))
            del self.docs[:-self.docs_max]
            keep = set(range(len(self.docs)))
            # индексы предложений перевязывать дорого — просто чистим мусор
            self.sents = [s for s in self.sents if s.get("d") in keep]
        if len(self.sents) > self.sents_max:
            del self.sents[: len(self.sents) - self.sents_max]
        if len(self.nodes) > self.node_max:
            rare = sorted(self.nodes.items(), key=lambda kv: kv[1])
            for k, _v in rare[: len(self.nodes) - self.node_max]:
                self.nodes.pop(k, None)
                self.edges.pop(k, None)

    # --------------------------- tf-idf и поиск ---------------------------
    def _idf(self, st):
        n_docs = max(1, len(self.docs))
        in_docs = sum(1 for d in self.docs if st in d["tf"])
        return math.log(1.0 + n_docs / (1.0 + in_docs)) + 0.35

    def _vec(self, tf, idf=True):
        n = float(sum(tf.values())) or 1.0
        return {st: (c / n) * (self._idf(st) if idf else 1.0)
                for st, c in tf.items()}

    @staticmethod
    def _cos(a, b):
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        dot = 0.0
        for st, v in a.items():
            bv = b.get(st)
            if bv:
                dot += v * bv
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / (na * nb)

    def retrieve(self, query, k=6, extra=None):
        """Лучшие материалы по запросу: [(score, doc)]. query+extra — тексты."""
        qs = self._query_stems(query, extra)
        if not qs or not self.docs:
            return []
        qv = self._vec({st: 1.0 for st in qs}, idf=False)
        out = []
        for d in self.docs:
            sc = self._cos(qv, self._vec(d["tf"]))
            out.append((sc, d))
        out.sort(key=lambda x: -x[0])
        return [(s, d) for s, d in out[:k] if s > 0.001]

    def _query_stems(self, *texts):
        qs = {}
        for t in texts:
            if not t:
                continue
            for w in WORD_RE.findall(str(t).lower()):
                if w in _STOP or len(w) > 23:
                    continue
                qs[_stem(w)] = 1
        return qs

    # ------------------------- разрядка активации -------------------------
    def spread(self, seeds, depth=2, decay=0.55):
        """Разрядка активации: запрос зажигает узлы, активация бежит по
        выученным связям. Возврат: {стем: активация} (топ ~40)."""
        act = {s: 1.0 for s in seeds if s in self.nodes or not self.nodes}
        if not self.nodes:
            return act
        frontier = {s: 1.0 for s in seeds if s in self.edges}
        for _depth in range(int(depth)):
            nxt = {}
            for st, a in frontier.items():
                links = self.edges.get(st) or {}
                total = float(sum(links.values())) or 1.0
                top = sorted(links.items(), key=lambda kv: -kv[1])[:12]
                for st2, w in top:
                    add = a * (w / total) * decay * 2.0
                    if act.get(st2, 0.0) < add:
                        nxt[st2] = max(nxt.get(st2, 0.0), add)
            for st, a in nxt.items():
                act[st] = max(act.get(st, 0.0), a)
            frontier = {s: a for s, a in nxt.items() if s in self.edges}
        return act

    def _hot_list(self, act, top=12):
        return [st for st, _a in sorted(act.items(), key=lambda kv: -kv[1])
                if len(st) >= 4][:top]

    # --------------------------- писатель текста --------------------------
    _OPEN = ("Разберём это подробнее.", "Вот что показывают материалы.",
             "Смотрим, что известно по теме.", "По существу вопроса:",
             "Собрал всё, что знаю из материалов.")
    _BRIDGE = ("Из материалов устройства следует, что",
               "Обучение на материалах показывает:",
               "Если коротко по теме, то",
               "Материалы говорят о следующем:",
               "Связки в моих знаниях подсказывают, что")
    _CLOSE = ("Таким образом, картина по теме складывается из изложенного.",
              "Итог: перечисленное следует напрямую из материалов, без домыслов.",
              "Вывод: так сеть видит тему после обучения на этих материалах.")

    def write(self, topic, words=340, extra=None, seed=0):
        """ДЛИННЫЙ текст по материалам: заголовок-зачин, 3-6 абзацев из
        релевантных предложений материалов, связки и вывод. extra —
        дополнительный контекст задания (мысли/показания устройства)."""
        self.stats["writes"] += 1
        self.stats["queries"] += 1
        rnd = random.Random(seed or int(time.time()) & 0xFFFF)
        qs = self._query_stems(topic, extra)
        act = self.spread(qs)
        self.last = {"topic": str(topic or "")[:120],
                     "hot": self._hot_list(act),
                     "ts": time.time()}
        if not self.docs or not qs:
            return ("Материалов по теме пока нет: сеть только что создана. "
                    "Загрузите файлы, дайте поискать в облаке или пообщайтесь "
                    "со мной — и после обучения я напишу длинный текст.")
        # поиск материалов и предложений
        ranked = self.retrieve(topic, k=6, extra=extra)
        if not ranked:                       # по активациям — смежное
            ranked = self.retrieve(" ".join(self._hot_list(act, 8) or
                                            list(qs)), k=6)
        if not ranked:
            return ("В моих материалах не нашлось ничего по запросу «%s». "
                    "Узлы сети зажглись, но документы не откликнулись — "
                    "нужно больше материалов по этой теме." % str(topic)[:80])
        qv = self._vec({st: 1.0 for st in qs}, idf=False)
        hot = self.last["hot"]
        hot_set = set(hot)
        cands = []
        doc_rank = {d.get("id", ""): r for r, (_s, d) in enumerate(ranked)}
        best_docs = [d for _s, d in ranked[:4]]
        for s in self.sents:
            sv = self._vec(s["tf"], idf=True)
            sc = self._cos(qv, sv) * 0.60
            act_sc = sum(min(1.0, act.get(st, 0.0))
                         for st in s["tf"] if st in hot_set)
            sc += (act_sc / max(1, len(s["tf"]))) * 0.25
            sc += 0.15 / (1.0 + doc_rank.get(
                (self.docs[s["d"]] or {}).get("id", ""), 9))
            cands.append((sc, s))
        cands.sort(key=lambda x: -x[0])
        need = max(8, int(words) // 22)
        picked, seen = [], set()
        for sc, s in cands:
            key = s["t"][:48].lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append(s)
            if len(picked) >= need * 2:
                break
        if not picked:
            return ("По запросу «%s» материалы нашлись, но годных предложений "
                    "мало — напишите тему точнее или дайте мне материалов."
                    % str(topic)[:80])
        # ---------- сборка ----------
        wcount = 0
        parts = []
        d0 = ranked[0][1]
        head = d0.get("title") or ", ".join(d0.get("kw", [])[:3]) or "материалы"
        parts.append("📝 %s — по материалам устройства\n" % head[:90])
        parts.append("%s %s" % (rnd.choice(self._OPEN),
                                self._sent_clean(picked[0]["t"])))
        wcount += len(picked[0]["t"].split())
        per_para = max(2, (len(picked) - 1) // 3 + 1)
        para, pi = [], 1
        for s in picked[1:]:
            if wcount >= int(words) * 1.25 and len(para) >= 1:
                break
            para.append(self._sent_clean(s["t"]))
            wcount += len(s["t"].split())
            if len(para) >= per_para:
                glue = rnd.choice(self._BRIDGE)
                parts.append("%s %s" % (glue, " ".join(para)))
                para, pi = [], pi + 1
        if para:
            parts.append("Дополнительно из материалов: %s" % " ".join(para))
        if hot:
            parts.append("Ассоциативная сеть выделяет по теме узлы: %s."
                         % ", ".join(hot[:8]))
        kw_all = {}
        for d in best_docs:
            for k in d.get("kw", []):
                kw_all[k] = kw_all.get(k, 0) + 1
        if kw_all:
            top_kw = [k for k, _ in sorted(kw_all.items(),
                                           key=lambda kv: -kv[1])[:6]]
            parts.append("%s Ключевые понятия темы: %s."
                         % (rnd.choice(self._CLOSE), ", ".join(top_kw)))
        used = ", ".join(dict.fromkeys(
            (d.get("title") or "без названия")[:48]
            for d in best_docs if d.get("title")))
        if used:
            parts.append("_Источники сети: %s_" % used)
        return "\n\n".join(p.strip() for p in parts if p.strip())

    @staticmethod
    def _sent_clean(s):
        """Предложение материала в чистом виде: скобки/мусор, регистр, точка."""
        s = re.sub(r"\[[^\]]{0,60}\]", "", str(s or ""))
        s = re.sub(r"\s+", " ", s).strip(" ;:-—*|«» ")
        if not s:
            return ""
        s = s[0].upper() + s[1:]
        if s[-1] not in ".!?…":
            s += "."
        return s

    # ------------------------------ показания -----------------------------
    def readings(self, top=12):
        """ПОКАЗАНИЯ НЕЙРОСЕТИ: размеры, материал, самые активные узлы.
        Облако и UI показывают это как «что сеть сейчас думает»."""
        act = self.last.get("hot") or []
        top_terms = [st for st, _c in
                     sorted(self.nodes.items(), key=lambda kv: -kv[1])[:top]]
        return {
            "nodes": len(self.nodes),
            "links": sum(len(v) for v in self.edges.values()),
            "docs": len(self.docs),
            "sents": len(self.sents),
            "learns": self.stats.get("learns", 0),
            "writes": self.stats.get("writes", 0),
            "topic": self.last.get("topic", ""),
            "hot": act[:top],
            "top": top_terms[:top],
            "titles": [d.get("title", "")[:60] for d in self.docs[-5:]],
        }

    # --------------------------- сохранение -------------------------------
    def save(self, path):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1,
                           "nodes": self.nodes,
                           "edges": self.edges,
                           "docs": self.docs,
                           "sents": self.sents[-self.sents_max:],
                           "stats": self.stats,
                           "last": self.last}, f, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    def load(self, path):
        try:
            if not os.path.isfile(path):
                return False
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
            self.nodes = st.get("nodes") or {}
            self.edges = st.get("edges") or {}
            self.docs = st.get("docs") or []
            self.sents = st.get("sents") or []
            self.stats.update(st.get("stats") or {})
            self.last = st.get("last") or self.last
            return True
        except Exception:
            return False
'''
_MN_FROM = ""
try:
    import sys as _sys
    import types as _types
    _mozgnet = _types.ModuleType("mozgnet")
    _mozgnet.__dict__["__file__"] = "<mozgnet: встроен в main.py>"
    exec(compile(_MN_SRC, "<mozgnet>", "exec"), _mozgnet.__dict__)
    _sys.modules["mozgnet"] = _mozgnet
    MN = _mozgnet.MozgNet()
    _MN_FROM = "встроен в main.py"
    log("МОЗГ-СЕТЬ", "нейросеть материалов: модуль встроен в main.py")
except Exception as _e:      # не собрался — сеть отключена, мозг живёт
    _mozgnet = None
    MN = None
    log("МОЗГ-СЕТЬ", "встроенный модуль сети не собрался, сеть выключена:",
        str(_e)[:100])


def _mn_path():
    """Файл состояния нейросети материалов — рядом с памятью."""
    try:
        return os.path.join(os.path.dirname(_mem_path()), "mozgnet.json")
    except Exception:
        return "mozgnet.json"


def mn_load():
    """Восстановить сеть после запуска (v2.25)."""
    if MN is None:
        return
    with _MN_LOCK:
        ok = MN.load(_mn_path())
    if ok:
        with _MN_LOCK:
            st = MN.readings()
        log("МОЗГ-СЕТЬ", "сеть восстановлена: %d узлов, %d связей, "
            "%d материалов" % (st["nodes"], st["links"], st["docs"]))


def mn_feed(text, title="", src="материал"):
    """Поставить материал в очередь обучения (дёшево: без сети и диска)."""
    if MN is None:
        return
    t = str(text or "").strip()
    if len(t) < 60:
        return
    with _MNQ_LOCK:
        if len(_MNQ) < MN_FEED_MAX:
            _MNQ.append((t[:24000], str(title or "")[:100],
                         str(src or "")[:24]))


def _mn_readings_push(force=False):
    """ПОКАЗАНИЯ НЕЙРОСЕТИ -> облако (POST /api/chat/readings). Тихо:
    облако не задано — только отметка времени."""
    if MN is None:
        return
    now = time.time()
    if not force and now - _MN_SENT["ts"] < MN_READINGS_EVERY:
        return
    _MN_SENT["ts"] = now
    url, token = _cloud_base()
    if not url:
        return
    try:
        with _MN_LOCK:
            rd = MN.readings()
        _http_json_err(url + "/api/chat/readings",
                       {"board": _hostname(), "readings": rd},
                       token=token, timeout=8)
    except Exception:
        pass


def _mn_batch():
    """Забрать порцию материалов из очереди обучения."""
    with _MNQ_LOCK:
        batch = _MNQ[:40]
        del _MNQ[:40]
    return batch


_MN_SAVED = [0.0, False]       # v2.82: время сохранения, есть несохранённое


def mn_worker():
    """Фон-обучение нейросети материалов: раз в MN_LEARN_EVERY сек собирает
    очередь, учится, сохраняет состояние и публикует показания в облако
    (раз в MN_READINGS_EVERY сек, и сразу после обучения)."""
    if MN is None:
        return
    log("МОЗГ-СЕТЬ", "воркер обучения запущен (пакет раз в %d c)"
        % int(MN_LEARN_EVERY))
    while True:
        time.sleep(MN_LEARN_EVERY)
        try:                              # v2.74: учится на вдохе
            if not breath_inhale("Сеть материалов платы"):
                continue
        except Exception:
            pass
        batch = _mn_batch()
        if batch:
            total = {"sents": 0, "links": 0, "nodes": 0}
            for text, title, src in batch:
                try:
                    with _MN_LOCK:
                        r = MN.learn(text, title=title, src=src)
                    for k in total:
                        total[k] += r.get(k, 0)
                except Exception:
                    continue
            _MN_SAVED[1] = True
            if time.time() - _MN_SAVED[0] > 60.0:   # v2.82: не каждый пакет
                with _MN_LOCK:
                    MN.save(_mn_path())
                _MN_SAVED[0], _MN_SAVED[1] = time.time(), False
            log("МОЗГ-СЕТЬ", "обучился на %d материалах: +%d предложений, "
                "+%d связей" % (len(batch), total["sents"], total["links"]))
            _mn_readings_push(force=True)
        else:
            if _MN_SAVED[1] and time.time() - _MN_SAVED[0] > 60.0:
                with _MN_LOCK:
                    MN.save(_mn_path())
                _MN_SAVED[0], _MN_SAVED[1] = time.time(), False
            _mn_readings_push()


def mn_write(topic, words=380, extra=None):
    """Длинный текст по материалам (синхронно, десятки мс)."""
    if MN is None:
        return ""
    with _MN_LOCK:
        return MN.write(topic, words=words, extra=extra)


def _mn_status_raw():
    """Снимок показаний сети для /api/state."""
    if MN is None:
        return {"on": False}
    try:
        with _MN_LOCK:
            st = MN.readings()
        st["on"] = True
        st["queued"] = len(_MNQ)
        st["src"] = _MN_FROM            # v2.40: откуда модуль сети
        return st
    except Exception:
        return {"on": False}


def _article_job(topic, words=380):
    """Фон: написать статью по материалам и вывести в чат платы."""
    try:
        add_message("system", "📝 Пишу длинный текст по материалам: «%s»…"
                    % topic[:80], "Материалы")
        text = mn_write(topic, words=words)
        if text:
            add_message("bot", text, "Материалы")
            mn_feed(text, "статья: %s" % topic[:60], "статья")
            _thought_push("написал статью по материалам: %s" % topic[:80])
        else:
            add_message("bot", "Нейросеть материалов недоступна "
                        "(mozgnet.py не найден).", "Материалы")
    except Exception as e:
        add_message("bot", "Статья не удалась: %s" % str(e)[:120],
                    "Материалы")


def api_mozgnet_write(data: dict):
    """v2.25: ДЛИННЫЙ ТЕКСТ ОТ НЕЙРОСЕТИ МАТЕРИАЛОВ (без LLM).
    POST {"topic": "...", "words": 380, "extra": "контекст"} -> {"text"}."""
    d = data or {}
    topic = str(d.get("topic") or d.get("text") or "").strip()[:300]
    if not topic:
        return {"ok": False, "error": "пустая тема"}
    try:
        words = min(900, max(120, int(d.get("words") or 380)))
    except Exception:
        words = 380
    text = mn_write(topic, words=words, extra=str(d.get("extra") or "")[:600])
    if not text:
        return {"ok": False, "error": "нейросеть материалов недоступна"}
    return {"ok": True, "text": text, "readings": mn_status()}


def api_mozgnet_learn(data: dict):
    """v2.25: дать нейросети материал на обучение (текст/заметку/файл).
    POST {"text": "...", "title": "...", "src": "материал"}."""
    d = data or {}
    text = str(d.get("text") or "").strip()
    if len(text) < 60:
        return {"ok": False, "error": "материал короче 60 знаков"}
    mn_feed(text, str(d.get("title") or "")[:100],
            str(d.get("src") or "материал"))
    return {"ok": True, "queued": len(_MNQ)}


def api_mozgnet_readings():
    """v2.25: ПОКАЗАНИЯ нейросети материалов (GET)."""
    st = mn_status()
    st["ok"] = bool(st.get("on"))
    return st


# ===========================================================================
# [3h] ТИХОЕ САМОПРОГРАММИРОВАНИЕ (v2.13): если письму мозга «что-то
#      непонятно» (зоны ответили слабо, навыка нет) — мозг МОЛЧА, без спама
#      в чатах, САМ собирает функцию из локальных заготовок (секция [3i],
#      LLM не участвует), проверяет её локально и в песочнице облака,
#      публикует в реестр облака. v2.24: LLM КОД БОЛЬШЕ НЕ ПИШЕТ — кодовый
#      генератор работает ОТ МОЗГА (не вышло — честный отказ). Внешне видны
#      ТОЛЬКО статус письма в чате
#      облака («программирую функцию…», POST /api/brain/progress) и бейдж
#      «навык» у ответа. Повторы честные: неудачный интент охлаждаётся,
#      после SELF_MAX_FAILS провалов замораживается на сутки.
# ===========================================================================
SELF_COOLDOWN   = 6 * 3600     # повторная попытка по тому же интенту не раньше
SELF_MAX_FAILS  = 3            # после стольких провалов интент замораживается
SELF_FAIL_PENAL = 24 * 3600    # срок заморозки (сутки)
_SKILLS_CACHE   = {"ts": 0.0, "list": []}   # кэш реестра функций облака (10 мин)
_WEBISH_PREFIX = ("найди", "поищи", "погугли", "что такое", "что за",
                  "кто такой", "кто такая", "открой", "прочитай", "новости",
                  "расскажи про", "поиск", "/")
# v2.19: похоже на ЗАДАЧУ (можно тихо собрать/достроить функцию-навык)
_TASK_MARK = re.compile(
    r"(посчитай|вычисли|сделай|сделать|напиши|создай|сгенерируй|реализуй|"
    r"добавь|переведи|конвертируй|покажи|собери|брось|подбрось|кубик|"
    r"сколько будет|чему равно|который час|какое число|день недели|"
    r"случайное число|факториал)", re.I)


def _self_state_path():
    """Файл состояния самокода — рядом с памятью (переживает запуск)."""
    try:
        return os.path.join(os.path.dirname(_mem_path()),
                            "selfcode_state.json")
    except Exception:
        return "/tmp/neuro12_selfcode.json"


def _self_state():
    try:
        with open(_self_state_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _self_state_save(d):
    try:
        p = _self_state_path()
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def _self_key(task):
    return "%x" % (zlib.crc32((task or "").lower()[:80].encode("utf-8"))
                   & 0xFFFFFFFF)


def _brain_progress(mid, note):
    """Тихий статус письма для чата облака: POST /api/brain/progress.
    Best-effort: любые ошибки молча игнорируются — статус не критичен."""
    url, token = _cloud_base()
    if not (url and mid and note):
        return
    try:
        _http_json_err(url + "/api/brain/progress",
                       {"id": str(mid)[:80], "note": str(note)[:160]},
                       token=token, timeout=6)
    except Exception:
        pass


def _cloud_skills_cached(period=600):
    """Реестр функций облака с кэшем (10 мин), чтобы не дёргать сеть."""
    now = time.time()
    if _SKILLS_CACHE["list"] and now - _SKILLS_CACHE["ts"] < period:
        return _SKILLS_CACHE["list"]
    sl, _ = cloud_skills(timeout=10)
    if sl is not None:
        _SKILLS_CACHE["list"] = sl or []
        _SKILLS_CACHE["ts"] = now
    return _SKILLS_CACHE["list"]


def _skill_match(text):
    """Навык облака под фразу: ключевое слово навыка (4+ букв) в тексте."""
    low = (text or "").lower()
    if not low.strip():
        return None
    for s in _cloud_skills_cached():
        kws = [str(k).lower() for k in (s.get("keywords") or [])
               if len(str(k)) >= 4]
        if kws and any(k in low for k in kws):
            return s
    return None


def _selfcode_should(text):
    """Решение о тихом самокоде: содержательная фраза (>= 2 слов), не команда
    и не веб-поиск, интент не на кулдауне/заморозке."""
    t = (text or "").strip()
    low = t.lower()
    if len(t.split()) < 2:
        return False
    if any(low.startswith(p) for p in _WEBISH_PREFIX):
        return False
    rec = _self_state().get(_self_key(task=t))
    ts = float(rec.get("ts", 0) or 0) if isinstance(rec, dict) \
        else float(rec or 0)
    fails = int(rec.get("fails", 0) or 0) if isinstance(rec, dict) else 0
    cooldown = (SELF_FAIL_PENAL if fails >= SELF_MAX_FAILS
                else SELF_COOLDOWN * max(1, fails))
    return (time.time() - ts) >= cooldown


def _selfcode_mark(task, ok):
    """Отметить попытку: успех сбрасывает счётчик провалов, провал копится."""
    st = _self_state()
    key = _self_key(task)
    rec = st.get(key) if isinstance(st.get(key), dict) else {"ts": 0, "fails": 0}
    rec["ts"] = time.time()
    rec["fails"] = 0 if ok else int(rec.get("fails", 0) or 0) + 1
    st[key] = rec
    _self_state_save(st)


# ===========================================================================
# [3i] ЛОКАЛЬНЫЙ САМОКОД БЕЗ LLM (v2.13): мозг САМ собирает функцию-навык.
#      Заготовки-семейства (калькулятор, конвертер единиц, дата/время,
#      случайность, текстовые фокусы) пишут код навыка прямо на плате; код
#      проверяется локально (ast + пробные прогоны) и в песочнице облака
#      (просто исполнение, нейросети там нет), затем публикуется в реестр.
#      v2.24: LLM НЕ УЧАСТВУЕТ НИГДЕ — старый путь «попросить LLM-учителя»
#      удалён: кодовый генератор работает только ОТ МОЗГА.
# ===========================================================================
_SKILL_CALC_CODE = r'''# -*- coding: utf-8 -*-
# Навык «Калькулятор» — собран мозгом платы сам, без нейросети (v2.13).
# Понимает: сколько будет 2 плюс 2 | посчитай (5 - 3) * 4 | корень из 144
#           12 в степени 2 | 20% от 150 | 15,5 разделить на 3
import math
import re

def run(text, ctx=None):
    s = re.sub(r",", ".", str(text or "").lower())
    for a, b in (("умножить на", "*"), ("умножь на", "*"), ("умножение", "*"),
                 ("умножить", "*"), ("умножь", "*"), ("разделить на", "/"),
                 ("раздели на", "/"), ("поделить на", "/"), ("делить на", "/"),
                 ("разделить", "/"), ("плюс", "+"), ("прибавить", "+"),
                 ("минус", "-"), ("отнять", "-"), ("вычесть", "-"),
                 ("в степени", "^"), ("в степень", "^"), ("степень", "^")):
        s = re.sub(re.escape(a), b, s)
    s = re.sub(r"корень из\s*([0-9.]+)", r" sqrt(\1) ", s)
    s = re.sub(r"([0-9.]+)\s*%\s*от\s*([0-9.]+)", r" (\1/100*\2) ", s)
    toks = re.findall(r"[0-9]+\.?[0-9]*|sqrt|[()+\-*/^]", s)
    if not any(c.isdigit() for c in "".join(toks)):
        return "дай пример цифрами: «сколько будет 12*8?»"
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        t = toks[pos[0]]
        pos[0] += 1
        return t

    def factor():
        t = peek()
        if t == "-":
            take()
            return -factor()
        if t == "sqrt":
            take()
            return math.sqrt(factor())
        if t == "(":
            take()
            v = addsub()
            if peek() == ")":
                take()
            return v
        if t and t[0].isdigit():
            return float(take())
        raise ValueError("ожидалось число")

    def power():
        v = factor()
        if peek() == "^":
            take()
            return v ** power()
        return v

    def muldiv():
        v = power()
        while peek() in ("*", "/"):
            op = take()
            r = power()
            if op == "*":
                v = v * r
            elif r == 0:
                raise ZeroDivisionError("ноль")
            else:
                v = v / r
        return v

    def addsub():
        v = muldiv()
        while peek() in ("+", "-"):
            op = take()
            r = muldiv()
            v = v + r if op == "+" else v - r
        return v

    try:
        val = addsub()
    except ZeroDivisionError:
        return "на ноль делить нельзя"
    except Exception:
        return "не понял выражение — пиши цифрами: 2+2, 5*8, корень из 144"
    if abs(val - round(val)) < 1e-9:
        return "Ответ: %d" % round(val)
    return "Ответ: %g" % round(val, 6)
'''

_SKILL_UNITS_CODE = r'''# -*- coding: utf-8 -*-
# Навык «Конвертер единиц» — собран мозгом платы сам, без нейросети (v2.13).
# Понимает: переведи 5 км в мили | 10 кг в фунты | 2 часа в минуты
#           100 фаренгейт в цельсии | 120 км/ч в м/с
import re

_LEN = {"км": 1000.0, "километр": 1000.0, "километра": 1000.0,
        "километров": 1000.0, "км/ч": 1.0, "м": 1.0, "метр": 1.0,
        "метра": 1.0, "метров": 1.0, "метрах": 1.0, "дм": 0.1, "см": 0.01,
        "мм": 0.001, "миля": 1609.344, "мили": 1609.344, "миль": 1609.344,
        "милях": 1609.344, "фут": 0.3048, "фута": 0.3048, "футов": 0.3048,
        "дюйм": 0.0254, "дюйма": 0.0254, "дюймов": 0.0254, "ярд": 0.9144,
        "ярда": 0.9144, "ярдов": 0.9144}
_MASS = {"кг": 1.0, "килограмм": 1.0, "килограмма": 1.0, "килограммов": 1.0,
         "г": 0.001, "грамм": 0.001, "грамма": 0.001, "граммов": 0.001,
         "т": 1000.0, "тонна": 1000.0, "тонны": 1000.0, "тонн": 1000.0,
         "фунт": 0.4536, "фунта": 0.4536, "фунтов": 0.4536,
         "унция": 0.02835, "унции": 0.02835}
_VOL = {"л": 1.0, "литр": 1.0, "литра": 1.0, "литров": 1.0, "мл": 0.001,
        "галлон": 3.785, "галлона": 3.785, "галлонов": 3.785}
_TIME = {"секунда": 1.0, "секунды": 1.0, "секунд": 1.0, "минута": 60.0,
         "минуты": 60.0, "минут": 60.0, "минутах": 60.0, "час": 3600.0,
         "часа": 3600.0, "часов": 3600.0, "часах": 3600.0, "сутки": 86400.0,
         "суток": 86400.0, "сутках": 86400.0, "день": 86400.0, "дня": 86400.0,
         "дней": 86400.0, "днях": 86400.0}
_SPEED = {"км/ч": 1.0, "м/с": 3.6, "миль/ч": 1.6093}
_TABLES = (_LEN, _MASS, _VOL, _TIME, _SPEED)


def _fmt(x):
    x = round(x, 4)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return "%g" % x


def _ufind(tab, u):
    if u in tab:
        return tab[u]
    for k, v in tab.items():
        if len(k) >= 2 and (u.startswith(k) or k.startswith(u)):
            return v
    return None


def _tkey(u):
    for p, k in (("цель", "C"), ("фарен", "F"), ("кельв", "K")):
        if u.startswith(p):
            return k
    return ""


def run(text, ctx=None):
    t = re.sub(r",", ".", str(text or "").lower())
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([а-яa-z/]+)\s*(?:во|в)\s+([а-яa-z/]+)", t)
    if not m:
        return "дай так: «переведи 5 км в мили»"
    n = float(m.group(1))
    u1, u2 = m.group(2), m.group(3)
    a, b = _tkey(u1), _tkey(u2)
    if a and b:
        c = n if a == "C" else (n - 32.0) * 5.0 / 9.0 if a == "F" else n - 273.15
        v = c if b == "C" else c * 9.0 / 5.0 + 32.0 if b == "F" else c + 273.15
        return "%s %s = %s %s" % (n, u1, _fmt(v), u2)
    for tab in _TABLES:
        f1, f2 = _ufind(tab, u1), _ufind(tab, u2)
        if f1 and f2:
            return "%s %s = %s %s" % (n, u1, _fmt(n * f1 / f2), u2)
    return "не знаю таких единиц — попробуй км/мили, кг/фунты, литры, часы, C/F"
'''

_SKILL_TIME_CODE = r'''# -*- coding: utf-8 -*-
# Навык «Дата и время» — собран мозгом платы сам, без нейросети (v2.13).
# Понимает: который час | какое сегодня число | какой сегодня день недели
#           сколько дней до 01.01.2027 | сколько дней до нового года
import re
from datetime import datetime

_WD = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота",
       "воскресенье"]
_MO = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
       "августа", "сентября", "октября", "ноября", "декабря"]


def _plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def run(text, ctx=None):
    t = str(text or "").lower()
    now = datetime.now()
    m = re.search(r"(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?:\s*[.\-/]\s*(\d{2,4}))?", t)
    target = None
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        if y < 100:
            y += 2000
        try:
            target = datetime(y, mo, d)
        except ValueError:
            return "не похоже на дату — напиши так: «сколько дней до 01.01.2027»"
        if target.date() < now.date() and not m.group(3):
            target = datetime(y + 1, mo, d)
    elif "нового года" in t or "новый год" in t:
        target = datetime(now.year + 1, 1, 1)
    if target is not None:
        days = (target.date() - now.date()).days
        if days < 0:
            return "эта дата уже прошла"
        if days == 0:
            return "этот день наступил — сегодня!"
        return "до %s осталось %d %s" % (target.strftime("%d.%m.%Y"), days,
                                         _plural(days, "день", "дня", "дней"))
    if "недел" in t or ("день" in t and "какой" in t):
        return "сегодня %s" % _WD[now.weekday()]
    if "число" in t or "дата" in t:
        return "сегодня %d %s %d года" % (now.day, _MO[now.month - 1], now.year)
    if "час" in t or "времени" in t:
        return "сейчас %02d:%02d" % (now.hour, now.minute)
    return "сейчас %02d:%02d, %d %s %d, %s" % (
        now.hour, now.minute, now.day, _MO[now.month - 1], now.year,
        _WD[now.weekday()])
'''

_SKILL_RAND_CODE = r'''# -*- coding: utf-8 -*-
# Навык «Случайность» — собран мозгом платы сам, без нейросети (v2.13).
# Понимает: брось кубик | подбрось монетку | случайное число от 5 до 10
#           выбери из чая или кофе
import random
import re

def run(text, ctx=None):
    t = str(text or "").lower()
    if "монет" in t:
        return "монетка: %s" % random.choice(["орёл", "решка"])
    if "кубик" in t:
        return "кубик: выпало %d" % random.randint(1, 6)
    if "выбери" in t:
        tail = t.split("из", 1)[1] if "из" in t else ""
        opts = [o.strip(" .!?-") for o in re.split(r",|или", tail)
                if o.strip(" .!?-")]
        if len(opts) >= 2:
            return "выбираю: %s" % random.choice(opts)
        return "перечисли варианты: «выбери из чая или кофе»"
    m = re.search(r"от\s*(\d+)\s*до\s*(\d+)", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        return "случайное число от %d до %d: %d" % (a, b, random.randint(a, b))
    m = re.search(r"(\d+)\D+(\d+)", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return "случайное число от %d до %d: %d" % (
            min(a, b), max(a, b), random.randint(min(a, b), max(a, b)))
    return "случайное число от 1 до 100: %d" % random.randint(1, 100)
'''

_SKILL_TEXT_CODE = r'''# -*- coding: utf-8 -*-
# Навык «Текстовые фокусы» — собран мозгом платы сам, без нейросети (v2.13).
# Понимает: переверни слово дом | сколько букв в слове молоко
#           сколько слов в этом коротком предложении
import re

def run(text, ctx=None):
    t = str(text or "")
    low = t.lower()
    if "сколько слов" in low or ("слов" in low and "посчитай" in low):
        return "слов: %d" % len(re.findall(r"[а-яёa-z0-9]+", low))
    if "букв" in low:
        ws = re.findall(r"[а-яёa-z]+", low)
        w = ws[-1] if ws else ""
        return "букв: %d" % len(w)
    if "переверни" in low or "наоборот" in low or "разверни" in low:
        ws = re.findall(r"[а-яёa-z0-9]+", low)
        if ws:
            return "%s наоборот — %s" % (ws[-1], ws[-1][::-1])
        return "скажи слово, которое перевернуть"
    return "скажи: «переверни слово», «сколько букв в слове», «сколько слов»"
'''


def _gen_calc(t):
    if not re.search(r"сколько будет|посчитай|вычисли|чему равно|реши|плюс"
                     r"|минус|корень из|в степени|умнож|раздели|делить|% от", t):
        return None
    if not re.search(r"\d", t):
        return None
    return {"id": "auto-calc", "name": "Калькулятор", "kind": "code",
            "keywords": ["сколько будет", "посчитай", "вычисли", "калькулятор"],
            "desc": "Считает примеры: + - * /, скобки, корень, степень, %",
            "code": _SKILL_CALC_CODE,
            "tests": ["сколько будет 2 плюс 2", "посчитай (5 - 3) * 4",
                      "корень из 144", "12 в степени 2", "20% от 150",
                      "15,5 разделить на 3"]}


def _gen_units(t):
    if not re.search(r"переведи|перевести|конвертируй|сконвертируй", t):
        return None
    if not re.search(r"\d", t):
        return None
    return {"id": "auto-units", "name": "Конвертер единиц", "kind": "code",
            "keywords": ["переведи", "конвертер", "мили", "фунты", "часы"],
            "desc": "Длина, масса, объём, время, скорость и температура",
            "code": _SKILL_UNITS_CODE,
            "tests": ["переведи 5 км в мили", "переведи 10 кг в фунты",
                      "переведи 2 часа в минуты",
                      "переведи 100 фаренгейт в цельсии"]}


def _gen_time(t):
    if not re.search(r"который час|сколько времени|какое.*?(число|дата)"
                     r"|какая.*?дата|какой.*?день|день недели|дней до"
                     r"|день до|нового года", t):
        return None
    return {"id": "auto-time", "name": "Дата и время", "kind": "code",
            "keywords": ["который час", "какое число", "день недели",
                         "сколько дней до"],
            "desc": "Время, дата, день недели и счёт дней до даты",
            "code": _SKILL_TIME_CODE,
            "tests": ["который час", "какое сегодня число",
                      "какой сегодня день недели",
                      "сколько дней до 31.12.2030"]}


def _gen_rand(t):
    if not re.search(r"кубик|монет|случайн|выбери|жребий|подбрось|брось", t):
        return None
    return {"id": "auto-rand", "name": "Случайность", "kind": "code",
            "keywords": ["кубик", "монетка", "случайное число", "выбери"],
            "desc": "Кубик, монетка, случайные числа и выбор за мозг",
            "code": _SKILL_RAND_CODE,
            "tests": ["брось кубик", "подбрось монетку",
                      "выбери из чая или кофе", "случайное число от 5 до 10"]}


def _gen_text(t):
    if not re.search(r"переверни|наоборот|разверни|сколько букв|сколько слов"
                     r"|посчитай букв|посчитай слов", t):
        return None
    return {"id": "auto-text", "name": "Текстовые фокусы", "kind": "code",
            "keywords": ["переверни", "сколько букв", "сколько слов"],
            "desc": "Переворачивает слова и считает буквы/слова",
            "code": _SKILL_TEXT_CODE,
            "tests": ["переверни слово дом", "сколько букв в слове молоко",
                      "сколько слов в этом коротком предложении"]}


_SELF_GENS = (_gen_calc, _gen_units, _gen_time, _gen_rand, _gen_text)


def _selfcode_verify(code, tests):
    """Проверка собранного кода на плате: синтаксис + пробные прогоны.
    -> (True, "") | (False, причина)."""
    import ast
    try:
        ast.parse(code)
    except SyntaxError as e:
        return False, "синтаксис: %s" % e
    g = {"__name__": "skill"}
    try:
        exec(compile(code, "<самокод>", "exec"), g)   # свой код — по построению
        fn = g.get("run")
        if not callable(fn):
            return False, "нет функции run()"
        for t in tests:
            out = fn(t, {})
            if not isinstance(out, str) or not out.strip():
                return False, "пустой ответ на %r" % t
    except Exception as e:
        return False, str(e)[:120]
    return True, ""


def _selfcode_local(task):
    """СБОРКА НАВЫКА НА ПЛАТЕ БЕЗ LLM (v2.13): перебираем заготовки-семейства,
    каждая узнаёт «свою» задачу и отдаёт готовый код; проверяем локально.
    -> (payload|None, причина). payload: id/name/keywords/desc/code/tests."""
    t = (task or "").strip().lower()
    if len(t.split()) < 2:
        return None, "слишком короткая задача"
    for gen in _SELF_GENS:
        try:
            payload = gen(t)
        except Exception as e:
            payload = None
            log("САМОКОД", "генератор:", str(e)[:80])
        if not payload:
            continue
        ok, why = _selfcode_verify(payload["code"], payload.get("tests") or [])
        if ok:
            log("САМОКОД", "собрал «%s» сам, код %d байт" %
                (payload["name"], len(payload["code"])))
            return payload, ""
        log("САМОКОД", "свой код не прошёл проверку:", str(why)[:100])
    return None, "ни одна заготовка не подошла к задаче"


def _skill_publish(payload):
    """Опубликовать готовый навык в реестре ОБЛАКА + отчёт «изучено».
    True = реестр принял (навык заберут и другие мозги)."""
    url, token = _cloud_base()
    if not url:
        return False
    try:
        r, _e = _http_json_err(url + "/api/skills",
                               {"id": payload.get("id") or "",
                                "name": payload.get("name") or "функция",
                                "keywords": payload.get("keywords") or [],
                                "kind": "code", "code": payload.get("code") or "",
                                "deps": payload.get("deps") or [],
                                "desc": payload.get("desc", "")},
                               token=token, timeout=10)
        ok = bool(r and r.get("ok"))
    except Exception:
        ok = False
    if ok:
        try:                               # отчёт «изучено» — видно в Studio
            _http_json_err(url + "/api/skills/learned",
                           {"board": _hostname(), "id": payload.get("id") or "",
                            "ver": int(payload.get("ver", 1) or 1)},
                           token=token, timeout=6)
        except Exception:
            pass
        _SKILLS_CACHE["ts"] = 0.0          # сброс кэша — навык уже в реестре
    return ok


def _selfcode_try(task, mid):
    """ТИХИЙ цикл самопрограммирования: мозг собирает функцию САМ,
    из локальных заготовок (секция [3i], LLM не участвует), проверяет
    её локально и прогоном в песочнице облака, публикует в реестр.
    v2.24: LLM КОД НЕ ПИШЕТ — кодовый генератор работает ТОЛЬКО ОТ МОЗГА;
    не вышло — честный отказ в журнал.
    v2.19: ПОЛНАЯ ТИШИНА — никаких сообщений и статусов о кодогенерации:
    ни в чаты, ни в письмо (mid больше не используется). Только журнал.
    -> {"id","name"} | None."""
    payload, why = _selfcode_local(task)
    if not payload:
        _selfcode_mark(task, False)
        log("САМОКОД", "не вышло:", (why or "?")[:100])
        return None
    sid = str(payload.get("id") or "")
    code = str(payload.get("code") or "")
    name = str(payload.get("name") or sid or "функция")
    if not sid or not code:
        _selfcode_mark(task, False)
        return None
    # двойная проверка: песочница облака (просто исполнение, LLM там нет)
    res, err = cloud_run_code(code, (payload.get("tests") or [task])[0])
    if not (res and res.get("ok")):
        _selfcode_mark(task, False)
        log("САМОКОД", "песочница:",
            str(err or (res or {}).get("error") or "?")[:100])
        return None
    published = _skill_publish(payload)
    _selfcode_mark(task, True)
    log("САМОКОД", "тихо освоил «%s» (сам, без нейросети), код %d байт" %
        (name, len(code)))
    return {"id": sid, "name": name}


def brainmail_send(to, text):
    """Написать ДРУГОМУ устройству (единый мозг): POST /api/brain/msg.
    Ответ придёт обратно в мозг-почту и попадёт в чат. True = ушло."""
    url, token = _cloud_base()          # почта ходит через облако
    if not url:
        return False, "облако не задано (панель «Настройки»)"
    r, err = _http_json_err(url + "/api/brain/msg",
                            {"to": str(to)[:40], "text": str(text)[:4000],
                             "from": _hostname()},
                            token=token, timeout=BRIN_TIMEOUT)
    if r and r.get("ok"):
        _mail_set(sent=int(SH.brainmail.get("sent", 0)) + 1)
        return True, str(r.get("id") or "")
    return False, err or (r or {}).get("error") or "облако не приняло письмо"


# v2.21: письма обрабатывают ОТДЕЛЬНЫЕ потоки — обдумывание (навык, KB,
# самокод) может занять минуты, а опрос inbox не должен прерываться ни на
# секунду: heartbeat держит плату «в сети» в реестре облака БЕЗ адреса.
_MAILQ = []                       # письма, ждущие свободный поток
_MAILQ_LOCK = threading.Lock()
_MAILQ_MAX = 12                   # потолок очереди (старые вытесняются)
_SELF_LOCK = threading.Lock()     # один тихий самокод за раз
_ANSWER_RETRY_WAIT = 5.0          # v2.21: пауза перед повтором ответа


def _selfcode_bg(task):
    """v2.21: тихий самокод ВНЕ потока письма — ответ чату уходит сразу,
    функция осваивается параллельно (одна за раз, очередь не копится)."""
    if not _SELF_LOCK.acquire(False):
        return                    # уже осваиваем другую функцию — тишина
    try:
        _selfcode_try(task, "")   # v2.19: mid не используется (полная тишина)
    finally:
        _SELF_LOCK.release()


def _mail_handler():
    """v2.21: поток-обработчик писем мозг-почты: берёт письмо из очереди,
    думает и отвечает. Пока думает — опрос inbox идёт в другом потоке."""
    while True:
        m = None
        with _MAILQ_LOCK:
            if _MAILQ:
                m = _MAILQ.pop(0)
        if m is None:
            time.sleep(0.5)
            continue
        try:
            _brainmail_handle(m)
        except Exception as e:
            log("МОЗГ-ПОЧТА", "письмо не обработано:", str(e)[:100])


def _brainmail_handle(m):
    """Одно письмо от другого устройства: думаем сами (зоны + память + KB),
    при флаге long разворачиваем ответ (_long_reply) и отвечаем в облако.
    v2.12/v2.24: письму чата облака, на которое «нечего ответить», — ТИХИЙ
    самокод (мозг собирает функцию ИЗ СВОИХ ЗАГОТОВОК, песочница облака
    проверяет; LLM код не пишет). Внешне видны только статус письма
    («программирую функцию…») и бейдж «навык» в чате.
    v2.21: выполняется В ОТДЕЛЬНОМ потоке (не в цикле опроса) — heartbeat
    не прерывается, и плата не «выпадает» из свободных мозгов облака;
    ответ на письмо отправляется с ретраем — письмо не теряется."""
    url, token = _cloud_base()          # письма — через облако
    if not url:
        return
    txt = str(m.get("text") or "")
    frm = str(m.get("from") or "?")
    mid = str(m.get("id") or "")
    if not txt or not mid:
        return
    log("МОЗГ-ПОЧТА", frm, "пишет:", txt[:60])
    brain_remember("user", txt[:400])
    try:          # v2.17: нейроны слушают письма единого мозга
        nn_excite(nn_stems(txt), 0.4)
    except Exception:
        pass
    long_mode = bool(m.get("long"))      # письмо чата VPS/Макс-бота: ДЛИННО
    skill_name, skill_note = "", ""
    z_net, _zsrc = nn_pick(txt[:1500])   # v2.15: сеть голосует и в письмах
    grounded, source = None, ""
    if m.get("thought"):
        # v2.57: «ОТВЕЧАЕТ ТОЛЬКО МОЗГ» (галочка облака): письмо читает мозг
        # (нейроны и сеть материалов), а ответ — фраза из его мысли по теме
        # письма (brain_speak: слова словаря, тему ведут нейроны/тензор)
        try:
            mn_feed(txt[:2000], "письмо", "письмо")
        except Exception:
            pass
        try:
            reply = brain_speak(seed_text=txt[:400], remember=True)
        except Exception:
            reply = ""
        if not reply:
            reply = ("Мысль по этому письму пока не собралась: в словаре мозга "
                     "мало связей с его словами.")
        zone, grounded, source = "Мысль", False, "мысль мозга"
    elif m.get("unify"):
        # v2.41: ЕДИНЫЙ МОЗГ — облако дополняет слабый ответ: отвечаем с
        # честной пометкой, есть ли у ответа опора и какая
        reply, zone, grounded, source = unify_answer(txt, z_net)
    elif long_mode:
        # 1) переиспользование: в реестре облака уже есть подходящая функция
        #    (v2.19: статусы выполнения навыка в письмо не пишем — тишина)
        sk = _skill_match(txt)
        if sk:
            res, _e = cloud_skill_run(str(sk.get("id")), txt, timeout=25)
            out = str((res or {}).get("result") or (res or {}).get("output")
                      or "").strip()[:500]
            if res and res.get("ok") and out:
                skill_name = str(sk.get("name") or sk.get("id"))[:60]
                skill_note = ("Мой навык «%s» выполнился:\n%s" %
                              (skill_name, out))
        # 2) v2.20: письмо чата облака разворачиваем БЕЗ шаблона зон —
        #    человек спрашивает облако, а получает «автоответ зоны». Теперь
        #    ответ ведут РЕАЛЬНЫЕ знания: память по теме, файлы, статьи,
        #    совет наставника; шаблон остаётся только заглушкой, если
        #    знаний совсем нет (см. _long_reply).
        weak, zone = zone_reply(txt[:1500], zone=z_net)   # зона — в бейдж
        reply = _long_reply(txt, "", z_net, note=skill_note)
        # 3) зоны ответили слабо и интент незнакомый -> ТИХИЙ самокод.
        #    v2.21: В ФОНЕ и ПОСЛЕ отправки ответа — обдумывание функции
        #    (до ~2.5 мин) больше не задерживает ответ чату облака.
        if not skill_note and len(weak.strip()) < 40 and \
                _selfcode_should(txt):
            threading.Thread(target=_selfcode_bg, args=(txt,),
                             daemon=True).start()
    else:
        reply, zone = zone_reply(txt[:1500], zone=z_net)
        if skill_note:
            reply = (skill_note + "\n\n" + reply).strip()
    payload = {"id": mid, "text": reply[:BMAIL_CAP],
               "zone": zone[:40], "skill": skill_name[:60]}
    if grounded is not None:              # v2.41: опора и источник ответа
        payload["grounded"] = bool(grounded)
        payload["source"] = str(source or "")[:120]
    r, err = _http_json_err(url + "/api/brain/answer", payload,
                            token=token, timeout=BRIN_TIMEOUT)
    if not (r and r.get("ok")):
        time.sleep(_ANSWER_RETRY_WAIT)   # v2.21: письмо нельзя терять — ретрай
        r, err = _http_json_err(url + "/api/brain/answer", payload,
                                token=token, timeout=BRIN_TIMEOUT)
    brain_remember("bot", reply[:400])
    _mail_set(answered=int(SH.brainmail.get("answered", 0)) + 1,
              letters=int(SH.brainmail.get("letters", 0)) + 1,
              last_from=frm, err=("" if r and r.get("ok") else err or "не ушло"))
    # v2.18: зеркало письма в чат платы не пишем (автоответы убраны) —
    # ответ уходит письмом в облако и там показывается с бейджем мозга
    log("МОЗГ-ПОЧТА", "ответ ->", frm, "[%s]:" % zone, reply[:60])


def _cloud_d6(m):
    """v2.35: МЫСЛЬ ЗОН ОБЛАКА -> ПИН D6 («как уже реализовано» для фраз
    самого мозга): облако (v3.17) кладёт ответы своих зон («Зоны мысли»)
    в ящик d6box, плата забирает их опросом /api/brain/d6 и проигрывает
    PWM-байтами на D6 через RPC d6seq (_gen_d6_seq — тот же канал, что и
    для собственных фраз). Это ВЫВОД, а не письмо-вопрос: ответ в облако
    не шлётся, чат мыслью не засоряем. Нейроны слушают мысли облака
    (единый мозг), сеть получает их материалом «мысль облака»."""
    txt = scrub_thought(m.get("text") or "")      # v2.63: без «англ н н»
    if not txt:
        return
    try:
        sg6 = int(m.get("sign") or 0)         # v2.47: знак решила LLM облака
    except (TypeError, ValueError):
        sg6 = 0
    zone = str(m.get("zone") or "")[:40]
    log("МОЗГ-ПОЧТА", "мысль облака%s -> пин D6:" %
        (" [%s]" % zone if zone else ""), txt[:60])
    try:          # нейроны слушают мысли единого мозга (как письма)
        nn_excite(nn_stems(txt), 0.3)
    except Exception:
        pass
    try:          # v2.25-стиль: мысль — материал для нейросети платы
        mn_feed(txt[:2000], zone, "мысль облака")
    except Exception:
        pass
    try:          # физика: буквы мысли — скрытно на пин D6 (как gen_register)
        threading.Thread(target=_gen_d6_seq, args=(txt[:240], sg6, False),
                         daemon=True).start()
    except Exception:
        pass
    try:          # v2.60: мысль облака -> текст-канал скетча -> ЧАСТОТА генератора
        _bridge_call("chat_stim", txt[:90], wait=False)
    except Exception:
        pass
    _mail_set(d6=int(SH.brainmail.get("d6", 0)) + 1, last_d6=txt[:80])


def brainmail_worker():
    """Цикл мозг-почты: каждые BRIN_PERIOD сек опрашиваем inbox и СТАВИМ
    письма в очередь обработчикам (v2.21: сами в этом цикле больше НЕ думаем
    — heartbeat не прерывается даже на долгом письме, и плата не «выпадает»
    из свободных мозгов облака). Опрос inbox держит нас «в сети» в реестре
    мозгов облака БЕЗ адреса — чат VPS и Макс-бот маршрутизируют вопросы нам.
    v2.21: мозг выключен (brain_on) — письма не берём, но раз в период
    дёргаем /api/brain/ping: облако видит плату, а письма ждут её включения
    (дворник облака перевыдаст их другой плате, если мы долго не включимся).
    Почему облако недоступно — видно в консоли (лог раз в 10 неудач и раз
    в 5 минут) и в чипе мозг-почты (err)."""
    log("МОЗГ-ПОЧТА", "канал единого мозга открыт (период %d с, потоки: 2)"
        % BRIN_PERIOD)
    for _ in range(2):
        threading.Thread(target=_mail_handler, daemon=True).start()
    fails = 0
    last_diag = 0.0
    last_reg = 0.0
    while True:
        time.sleep(BRIN_PERIOD)
        url, token = _cloud_base()      # inbox — в облаке
        if not url:
            continue
        try:
            # v2.20: раз в CLOUD_REG_PERIOD регистрируемся в облаке С АДРЕСОМ
            # (Studio видит имя и url платы, «проверить связь» достукивается
            # до /api/health) — раньше плата появлялась в облаке сама, без
            # адреса, и не отличалась от «молчащей коробки».
            now = time.time()
            # v2.80: роутер выдал плате новый IP — регистрируемся СРАЗУ
            # (облако и лаунчер узнают новый адрес, а не через 10 мин)
            ip_now = _local_ip()
            ip_moved = bool(ip_now) and ip_now != _REG_STATE.get("ip", ip_now)
            if ip_moved:
                log("СЕТЬ", "IP платы сменился: %s -> %s — сообщаю облаку"
                    % (_REG_STATE.get("ip"), ip_now))
            if ip_now:
                _REG_STATE["ip"] = ip_now
            if ip_moved or now - last_reg > CLOUD_REG_PERIOD:
                last_reg = now
                cloud_register()
            import urllib.parse
            host = urllib.parse.quote(_hostname())
            if not CFG.get("brain_on"):
                # v2.21: мозг выключен — письма не берём, но плату видно
                _http_get_json_err(url + "/api/brain/ping?board=" + host,
                                   token=token, timeout=BRIN_TIMEOUT)
                continue
            r, err = _http_get_json_err(
                url + "/api/brain/inbox?board=" + host,
                token=token, timeout=BRIN_TIMEOUT)
            if not r or not r.get("ok"):
                fails += 1
                _mail_set(err=err or "inbox не ответил")
                now = time.time()
                if fails == 1 or fails % 10 == 0 or now - last_diag > 300.0:
                    last_diag = now
                    log("МОЗГ-ПОЧТА", "облако недоступно (попытка %d): %s"
                        % (fails, (err or "inbox не ответил")[:140]))
                continue
            fails = 0
            mails = r.get("msgs") or []
            if mails:
                _mail_set(err="")
                with _MAILQ_LOCK:
                    _MAILQ.extend(mails)
                    if len(_MAILQ) > _MAILQ_MAX:
                        del _MAILQ[:-_MAILQ_MAX]
                        log("МОЗГ-ПОЧТА",
                            "очередь переполнена — старые письма сброшены")
            # v2.35: ОТДЕЛЬНЫЙ ящик «мысли зон облака» -> пин D6. Это не
            # письма-вопросы: отвечать на них не нужно — только физически
            # проиграть (_cloud_d6 -> _gen_d6_seq -> RPC d6seq).
            try:
                r6, _e6 = _http_get_json_err(
                    url + "/api/brain/d6?board=" + host,
                    token=token, timeout=BRIN_TIMEOUT)
                if r6 and r6.get("ok"):
                    syncpoint_on(r6.get("sync"))              # v2.53
                    instr_on(r6.get("instr"), r6.get("instr_text"))  # v2.54
                    _D6NEG["on"] = bool(r6.get("negfreq"))   # v2.46
                    _D6NEG["on4"] = bool(r6.get("negfreq4"))  # v2.48
                    _D6CAM["on"] = bool(r6.get("camframe"))   # v2.48
                    _D6CAM["mode"] = str(r6.get("cammode") or "pure")  # v2.49
                    _D6CAM["on4"] = bool(r6.get("d4cam"))     # v2.50
                    if not _D6NEG["on4"] and _D6NEG["pol4"]:
                        _d4_apply(0)          # галочку сняли — меандр обычный
                    for m6 in (r6.get("msgs") or []):
                        _cloud_d6(m6)
            except Exception:
                pass
        except Exception as e:
            fails += 1
            _mail_set(err=str(e)[:120])


def ask_free_brains(q, timeout_wait=100):
    """Спросить СВОБОДНЫЙ мозг через ОБЛАКО (POST /api/brains/ask): письмо
    уйдёт другому онлайн-устройству с инструкцией «пиши ДЛИННО», ответ
    дожидаемся поллингом /api/brain/result/<id>. -> (текст, ошибка)."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    r, err = _http_json_err(url + "/api/brains/ask",
                            {"text": str(q)[:2000], "from": _hostname()},
                            token=token, timeout=15)
    if not r or not r.get("ok"):
        return None, err or (r or {}).get("error") or "нет свободных мозгов"
    mid = str(r.get("id") or "")
    who = str(r.get("brain") or "?")
    import urllib.parse
    t0 = time.time()
    while time.time() - t0 < timeout_wait:
        time.sleep(3)
        rr, _ = _http_get_json_err(url + "/api/brain/result/" +
                                   urllib.parse.quote(mid),
                                   token=token, timeout=8)
        if rr and rr.get("ok"):
            # форма ответа облака: {ok, pending, answer, zone, ...} (плоский)
            ans = str(rr.get("answer") or "")
            if ans:
                return (ans, who)
    return None, "мозг %s не ответил за %d с" % (who, timeout_wait)


# ===========================================================================
# [4] LLM-ЯДРО (v2.14): ТОЛЬКО УЧИТЕЛЬ/НАСТАВНИК. Облако POST /api/llm/teach
#     (purpose: word_goal|verdict|advice) -> прямой URL. Отвечать на вопросы
#     ядру ЗАПРЕЩЕНО: тексты строит нейросеть платы (зоны+навыки+KB+память).
#     v2.24: и код LLM не пишет — самокод только из заготовок мозга.
# ===========================================================================
TEACH_PROMPT = ("Ты — учитель буквогенератора. Тебе дают фразу, собранную "
                "из букв русского алфавита маленькой нейросетью. Если "
                "фраза похожа на осмысленную русскую речь — ответь ровно "
                "«1», если бессмыслица — ровно «0». Никаких других слов.")


def _http_json_err(url, payload, token="", timeout=30):
    """POST JSON с разбором ТЕЛА ошибки (принцип doctor.sh [9/9])."""
    import urllib.error
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "neuro12-app/%s" % APP_VERSION}
    if token:
        headers["X-Mozg-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return None, "HTTP %s %s" % (e.code, detail)
    except Exception as e:
        return None, str(e)


def _http_get_json_err(url, token="", timeout=20):
    """GET JSON с разбором ТЕЛА ошибки (принцип doctor.sh [9/9])."""
    import urllib.error
    import urllib.request
    headers = {"User-Agent": "neuro12-app/%s" % APP_VERSION}
    if token:
        headers["X-Mozg-Token"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return None, "HTTP %s %s" % (e.code, detail)
    except Exception as e:
        return None, str(e)


def _cloud_teach(purpose, msgs, timeout=40, max_tokens=500):
    """v2.14: учительский запрос в облако — ТОЛЬКО /api/llm/teach с purpose.
    Старый /api/llm/ask закрыт облаком (410): LLM не отвечает на вопросы —
    только обучает и наставляет. Возврат: (текст|None, err)."""
    url = (CFG.get("cloud_url") or "").strip().rstrip("/")
    token = (CFG.get("token") or "").strip()
    if not (url and token):
        return None, "не задан адрес облака/токен"
    r, err = _http_json_err(url + "/api/llm/teach",
                            {"purpose": purpose, "messages": msgs[-8:],
                             "max_tokens": max_tokens},
                            token=token, timeout=timeout)
    if r and r.get("ok") and r.get("reply"):
        return r["reply"][:4000], ""
    return None, err or (r or {}).get("error") or "облако не ответило"


def _direct_ask(msgs, timeout=40):
    """Прямой OpenAI-совместимый URL — ТОЛЬКО для учительских вызовов
    (выбор слова-цели, вердикты, советы-наставления)."""
    url = (CFG.get("direct_url") or "").strip().rstrip("/")
    if not url:
        return None, "не задан прямой URL ядра"
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = {"model": (CFG.get("direct_model") or "").strip() or "default",
            "messages": msgs, "max_tokens": 500, "temperature": 0.4}
    headers = {"Content-Type": "application/json",
               "User-Agent": "neuro12-app/%s" % APP_VERSION}
    if (CFG.get("direct_key") or "").strip():
        headers["Authorization"] = "Bearer " + CFG["direct_key"].strip()
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
        out = ((j.get("choices") or [{}])[0].get("message") or {}).get("content")
        out = (out or "").strip()
        return (out or None), ("" if out else "пустой ответ ядра")
    except Exception as e:
        return None, str(e)


def _context_msgs():
    with SH.lock:
        mem = list(SH.memory)[-6:]
    msgs = []
    for m in mem:
        role = m.get("role")
        if role not in ("user", "bot"):
            continue
        msgs.append({"role": "user" if role == "user" else "assistant",
                     "content": str(m.get("text", ""))[:400]})
    return msgs


# v2.14: llm_reply (полноценный ответ ядра) УДАЛЁН — LLM больше не отвечает
# нигде: ни в чате платы, ни через /api/llm/ask (закрыт облаком, 410).
# Ответ строит нейросеть платы; LLM — только учитель/наставник (_cloud_teach).


# ===========================================================================
# [5] ОБУЧЕНИЕ (v2.8) — ТРИ учителя вместо одной размытой оценки ±1:
#     [5a] СЛОВАРНАЯ НАГРАДА — каждая фраза разбирается по словарю русского
#          языка: буквы РЕАЛЬНЫХ слов получают '+', буквы выдумок '-'
#          (teach_shape — кредит той букве, что его заслужила). Работает
#          ВСЕГДА, без облака и без LLM.
#     [5b] ДИКТАНТ — мозгу даётся целевое слово; каждая буква сверяется на
#          лету (teach_last ±1), собранное слово сравнивается с целью.
#     [5c] LLM-учитель МОЛЧУН — не пишет в чат; даёт мягкий вердикт ±1
#          (teach_score), выбирает целевые слова из сообщений и оставляет
#          мысли в панели «Учитель» (teacher_note).
#     Выбор буквы ВСЕГДА делает сеть скетча: учителя учат, а не выбирают.
# ===========================================================================
TEACH_PERIOD = 35.0          # сек между попытками LLM-вердикта
_teach_last = [0.0]

TEACH_WORD_PROMPT = (
    "Ты — учитель буквогенератора на Arduino. Из сообщения пользователя "
    "выбери ОДНО русское слово (3-8 букв) для диктанта маленькой нейросети. "
    "Ответь строго в формате:\nСЛОВО: <одно слово>\nСОВЕТ: <одна короткая "
    "фраза учителя о том, чему сейчас учит мозг>. Никаких других слов.")


def llm_pick_word(user_text):
    """LLM-молчун: слово-цель + мысль учителя. Возврат: (слово|'', мысль|'')."""
    text = (user_text or "").strip()[:800]
    if not text:
        return "", ""
    msgs = [{"role": "system", "content": TEACH_WORD_PROMPT},
            {"role": "user", "content": text}]
    out = None
    if (CFG.get("cloud_url") or "").strip() and (CFG.get("token") or "").strip():
        # v2.18: timeout=15 — выбор слова не должен подвешивать ответ чата
        # на 40 с, когда облако недоступно (40 c -> 15 c)
        out, err = _cloud_teach("word_goal", msgs, timeout=15)
        if out:
            _set_llm_state("cloud", True)
        else:
            _set_llm_state("cloud", False, err)
    if not out and (CFG.get("direct_url") or "").strip():
        out, err = _direct_ask(msgs, timeout=15)
        if out:
            _set_llm_state("direct", True)
        else:
            _set_llm_state("direct", False, err)
    if not out:
        return "", ""
    word, note = "", ""
    for line in str(out).splitlines():
        low = line.strip().lower()
        if low.startswith("слово:"):
            word = _norm_word(line.strip()[6:])[:24]
        elif low.startswith("совет:"):
            note = line.strip()[6:].strip()[:300]
    if not word:
        word = _norm_word(str(out).strip().splitlines()[0])[:24]
    if word and not (3 <= len(word) <= 16 and set(word) <= _RU_ALPHABET):
        word = ""
    return word, note


# --- [5a] СЛОВАРНАЯ НАГРАДА -------------------------------------------------
def build_word_mask(phrase):
    """Маска наград для фразы: каждому токену (буква/знак) свой символ.
    Слова из словаря -> '+' на их буквах, неизвестные -> '-', пробелы ->
    '+' (структура), запятая/точка -> '0' (нейтрально). Маска выравнивается
    по ХВОСТУ: скетч помнит последние DEC_MAX=26 решений фразы."""
    mask, word, word_pos = [], [], []
    for ch in str(phrase or ""):
        low = ch.lower()
        if low in _RU_ALPHABET:
            word.append(_norm_word(ch))
            word_pos.append(len(mask))
            mask.append("0")
        else:
            if word:
                r = "+" if dict_has("".join(word)) else "-"
                for p in word_pos:
                    mask[p] = r
                word, word_pos = [], []
            mask.append("+" if ch == " " else "0")
    if word:
        r = "+" if dict_has("".join(word)) else "-"
        for p in word_pos:
            mask[p] = r
    return "".join(mask)[-26:]


def _phrase_reward_async(phrase):
    """Оценить завершённую фразу по словарю и выдать teach_shape (фоном).
    Во время диктанта мгновенный сигнал уже дал каждой букве свою награду —
    словарную маску пропускаем, чтобы не перегружать веса."""
    with SH.lock:
        dt_on = SH.dictation.get("on")
    if dt_on and CFG.get("dictate_on"):
        return

    def _job():
        try:
            mask = build_word_mask(phrase)
            if "+" not in mask and "-" not in mask:
                return                  # нейтральная маска — учить нечему
            hits = mask.count("+")
            miss = mask.count("-")
            if not (hits or miss):
                return
            _bridge_call("teach_shape", mask, timeout=6, channel="teach")
            with SH.lock:
                SH.learn["rewards"] += 1
                SH.learn["hits"] += hits
                SH.learn["miss"] += miss
                SH.mem_dirty = True
            log("УЧИТЕЛЬ", "словарная маска:", mask, "(+%d/-%d)" % (hits, miss))
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_job, daemon=True).start()


# --- [5b] ДИКТАНТ ------------------------------------------------------------
DICT_FAIL_TRIES = 3         # попыток на слово, потом берём следующее


def dictation_set(word, src=""):
    """Назначить целевое слово диктанта (пусто = выключить цель)."""
    w = _norm_word(word)[:24]
    with SH.lock:
        dt = SH.dictation
        dt["target"] = w
        dt["src"] = str(src or "")[:20]
        dt["pos"] = 0
        dt["cur"] = ""
        dt["tries"] = 0
        SH.mem_dirty = True
    if w:
        _bridge_call("cloud_buf_set", "ЦЕЛЬ: " + w[:20], timeout=4)
        log("ДИКТАНТ", "цель:", w, "(%s)" % (src or "словарь"))
        _gen_task_async("цель диктанта мозга: слово «%s» — придумай и напиши "
                        "фразу или абзац с этим словом" % w, "диктант")
    return w


def dictation_queue(word, src="облако"):
    w = _norm_word(word)[:24]
    if len(w) < 3:
        return False
    with SH.lock:
        q = SH.dictation["queue"]
        if w in q:
            return False
        q.append(w)
        SH.mem_dirty = True
    if not (SH.dictation.get("target") or ""):
        dictation_next()
    return True


def dictation_next(reason=""):
    """Следующее слово: из очереди, иначе из словаря (сложность растёт
    вместе с числом побед)."""
    with SH.lock:
        w = SH.dictation["queue"].popleft() if SH.dictation["queue"] else ""
        wins = SH.learn["dict_wins"]
        src = "очередь"
    if not w:
        if wins < 2:
            w = dict_pick(3, 5, pos="nouns" if random.random() < .6 else "")
        elif wins < 6:
            w = dict_pick(4, 7, pos="nouns" if random.random() < .5 else "verbs")
        else:
            w = dict_pick(5, 9, pos=random.choice(("nouns", "verbs", "adjs")))
        src = "словарь"
    if w:
        dictation_set(w, src)
        # v2.18: цель диктанта в чат больше не пишем (автоответы убраны)
    return w or ""


def dictation_letter(chr_):
    """Мгновенная сверка буквы при активном диктанте (свой поток на букву):
    совпала с целью — teach_last(+1), нет — teach_last(-1)."""
    with SH.lock:
        dt = dict(SH.dictation)
        dt_on = CFG.get("dictate_on")
    if not (dt_on and dt.get("on") and dt.get("target")):
        return
    ch = (chr_ or "").strip()
    if not ch:
        return
    if ch in "_,.":
        dictation_boundary()
        return
    low = _norm_word(ch)[:1]
    if not low or low not in _RU_ALPHABET:
        return
    tgt = dt["target"]
    pos = int(dt.get("pos") or 0)
    ok = pos < len(tgt) and low == tgt[pos]
    with SH.lock:
        SH.dictation["cur"] += low
        SH.dictation["pos"] = pos + 1
        SH.learn["letters"] += 1
    _bridge_call("teach_last", 1 if ok else -1, timeout=3, channel="teach")


def dictation_boundary():
    """Слово собрано (пробел/знак): сверить с целью; победа -> похвала и
    новое слово, 3 неудачи -> новое слово без наказания всей фразы."""
    with SH.lock:
        dt = dict(SH.dictation)
        if not (dt.get("on") and dt.get("target")):
            return
        made = dt.get("cur") or ""
        SH.dictation["cur"] = ""
        SH.dictation["pos"] = 0
        if not made:
            return
        SH.learn["dict_tries"] += 1
        SH.dictation["tries"] = int(dt.get("tries") or 0) + 1
        tries = SH.dictation["tries"]
    win = (made == dt["target"])
    if win:
        with SH.lock:
            SH.learn["dict_wins"] += 1
            SH.dictation["wins"] += 1
        _bridge_call("teach_score", 1, timeout=4)     # похвала свежим буквам
        # v2.18: победы/смены цели диктанта в чат не пишем (автоответы убраны)
        log("ДИКТАНТ", "победа:", made)
        dictation_next(reason="win")
    elif tries >= DICT_FAIL_TRIES:
        log("ДИКТАНТ", "смена цели (было «%s», собрано «%s»)" %
            (dt["target"], made))
        dictation_next(reason="skip")


# --- [5c] LLM-вердикт (мягкий, вторичный) -----------------------------------
def score_phrase(phrase):
    """Оценка фразы нейросетью: '1' -> +1, '0' -> -1, None -> нет оценки."""
    text = (phrase or "").strip().strip(".,")
    if not text:
        return None
    # облако (учительский канал /api/llm/teach, purpose=verdict)
    if (CFG.get("cloud_url") or "").strip() and (CFG.get("token") or "").strip():
        out, err = _cloud_teach("verdict", [
            {"role": "system", "content": TEACH_PROMPT},
            {"role": "user", "content": "Фраза: «%s»" % text[:120]}],
            max_tokens=8, timeout=12)
        if out:
            d = out.strip()[:1]
            if d in ("1", "0"):
                return 1 if d == "1" else -1
        else:
            log("учитель: облако:", str(err)[:80])
    return None


_teach_busy = [False]


def teacher_tick():
    """Фоновая оценка LLM: последняя завершённая фраза -> мягкий ±1.
    Основное обучение теперь делает словарь и диктант; это — бонус.
    v2.18: КРИТИЧНО — оценка ушла в ФОНОВЫЙ поток. Раньше score_phrase
    звал облако с timeout=40 СИНХРОННО в brain_tick: при недоступном облаке
    основной цикл замирал на 40+ с каждые 35 с — замирали пинги моста и
    проверки облака («мост молчит», «мозг не подключается к облаку»).
    v2.18: сообщения в чат учитель больше не пишет вовсе (автоответы
    убраны по просьбе) — только лог и панель."""
    now = time.time()
    if CFG.get("teach_on") and now - _teach_last[0] >= TEACH_PERIOD and \
            not breath_inhale("Учитель и диктант"):   # v2.74: учит на вдохе
        return
    if not CFG.get("teach_on") or now - _teach_last[0] < TEACH_PERIOD:
        return
    with SH.lock:
        phrase = SH.teach_q[0] if SH.teach_q else ""
    if not phrase:
        return
    if _teach_busy[0]:
        return                              # прошлый вердикт ещё считается
    _teach_last[0] = now
    _teach_busy[0] = True

    def _job():
        try:
            score = score_phrase(phrase)
            if score is None:
                return
            with SH.lock:
                if phrase in SH.teach_q:
                    SH.teach_q.remove(phrase)
                SH.teach_count += 1
                SH.mem_dirty = True
            # v2.18: wait=False — в фоновом потоке не ждём MCU
            _bridge_call("teach_score", score, timeout=5, channel="teach",
                         wait=False)
            try:      # v2.15: вердикт учит и зонную сеть (хебб)
                nn_learn(phrase, zone_of(phrase), score)
            except Exception:
                pass
            log("УЧИТЕЛЬ", phrase, "->", score)
        except Exception:
            traceback.print_exc()
        finally:
            _teach_busy[0] = False

    threading.Thread(target=_job, daemon=True).start()


# --- ЛЮБОПЫТСТВО: мозг сам соединяется с облаком и забирает свежее -----------
CURIOUS_PERIOD = 180.0      # сек между самостоятельными поисками
_curious_last = [0.0]


def curiosity_tick():
    now = time.time()
    if not CFG.get("curious_on") or now - _curious_last[0] < CURIOUS_PERIOD:
        return
    if not breath_inhale("Любопытство платы"):     # v2.74: читает на вдохе
        return
    with SH.lock:
        ok = bool(SH.cloud_state.get("configured") and SH.cloud_state.get("ok"))
        dict_ready = SH.dict_ready
    if not (ok and dict_ready):
        return
    _curious_last[0] = now
    threading.Thread(target=_curious_job, daemon=True).start()


def _curious_job():
    """Найти слово в Википедии, запомнить находку, выучить новые слова и
    поставить слово в диктант — мозг забирает из облака свежее."""
    try:
        word = dict_pick(4, 9)
        if not word:
            return
        r, err = cloud_search(word)
        if not r or not r.get("ok"):
            return
        results = r.get("results") or []
        if not results:
            return
        top = results[0]
        title = str(top.get("title") or word)[:60]
        snip = " ".join(str(top.get("snippet") or "").split())[:200]
        new_w = []
        for w in re.findall(r"[а-яё]{4,}", snip.lower())[:24]:
            if dict_has(w):
                continue
            got = dict_learn_words([w], limit=1)
            if got:
                new_w.extend(got)
            if len(new_w) >= 4:
                break
        msg = ("Любопытство: узнал про «%s» — %s" % (title, snip))
        if new_w:
            msg += " Выучил слова: %s." % ", ".join(new_w)
        # v2.18: находки любопытства в чат не пишем (автоответы убраны) —
        # память и лог остаются, слова попадают в словарь и диктант
        brain_remember("bot", "🌐 %s: %s" % (title, snip))
        dictation_queue(word, "облако")
        log("ЛЮБОПЫТСТВО", title, "| новых слов:", len(new_w))
    except Exception:
        traceback.print_exc()


# ===========================================================================
# [6] TTS: речь через ОБЛАКО (кирпич tts не нужен, как в neuro_brain_12):
#     POST {cloud_url}/api/tts {"text": ...} -> WAV (на платформе Silero/
#     Piper/espeak). WAV играет динамик платы (aplay/paplay/ffplay); если
#     игрока/звука нет — байты уходят в браузер (/api/tts_audio) и там
#     звучат без всяких проводов. Ошибки — честно в статус чипа TTS.
# ===========================================================================
_TTS_LOCK = threading.Lock()
_TTS_SEQ = [0]                       # id последнего аудио для браузера


def _http_bytes_err(url, payload, token="", timeout=30):
    """POST JSON -> СЫРЫЕ байты ответа (для WAV облака)."""
    import urllib.error
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "neuro12-app/%s" % APP_VERSION}
    if token:
        headers["X-Mozg-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return b"", "HTTP %s %s" % (e.code, detail)
    except Exception as e:
        return b"", str(e)


def _cloud_tts(text):
    """Синтез речи на облаке. Возврат: (WAV-байты|None, ошибка|'')."""
    url = (CFG.get("cloud_url") or "").strip().rstrip("/")
    token = (CFG.get("token") or "").strip()
    if not (url and token):
        return None, "не задан адрес облака/токен (панель «Настройки»)"
    payload = {"text": text}
    if (CFG.get("tts_voice") or "").strip():
        payload["voice"] = CFG["tts_voice"].strip()
    data, err = _http_bytes_err(url + "/api/tts", payload,
                                token=token, timeout=40)
    if err:
        return None, err
    if len(data) < 44 or data[:4] != b"RIFF":
        return None, "облако вернуло не WAV (%d байт)" % len(data)
    return data, ""


def _board_play_wav(data):
    """Играть WAV на плате (Linux-сторона UNO Q). True — если прозвучало."""
    import subprocess
    path = "/tmp/neuro12_tts.wav"
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        return False
    for cmd in (["aplay", "-q", path],
                ["paplay", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]):
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=40)
            if r.returncode == 0:
                return True
        except FileNotFoundError:
            continue                      # игрока нет — пробуем следующий
        except Exception:
            continue
    return False


def _wav_frames(wav, max_frames=88, frame_ms=60):
    """v2.14: WAV -> строка кадров «hz:ms;» для скетча (метод say_wave).
    По мосту WAV не перетащить, а огибающая «частота:длительность» — легко:
    скетч проиграет её на D8 как «речь» (низко-полосный нейроголос).
    PCM разбираем стандартным wave-модулем; частота — по нулевым переходам,
    тишина (низкая амплитуда) -> пауза. Ошибка/не PCM -> '' (не страшно)."""
    try:
        import io as _io
        import struct as _struct
        import wave as _wave
        with _wave.open(_io.BytesIO(wav), "rb") as w:
            nch, sw, fr, nf = (w.getnchannels(), w.getsampwidth(),
                               w.getframerate(), w.getnframes())
            raw = w.readframes(min(nf, int(fr) * 20))   # не более 20 с
        if sw != 2 or fr < 4000 or nf < 100:
            return ""
        mono = raw if nch == 1 else raw[::nch * sw]     # канал 0
        n = len(mono) // 2
        if n < 100:
            return ""
        xs = _struct.unpack("<%dh" % n, mono[:n * 2])
        step = max(1, int(fr) * frame_ms // 1000)
        out, min_amp = [], 420                          # порог тишины
        for i in range(0, n - step, step):
            fr_i = xs[i:i + step]
            peak = 0
            cross = 0
            prev = fr_i[0]
            for v in fr_i:
                a = v if v >= 0 else -v
                if a > peak:
                    peak = a
                if (v >= 0) != (prev >= 0):
                    cross += 1
                prev = v
            if peak < min_amp:
                out.append((0, frame_ms // 2))          # пауза
                continue
            hz = int(cross * fr / (2.0 * step))
            hz = min(max(hz, 60), 1100)
            out.append((hz, frame_ms))
            if len(out) >= max_frames:
                break
        return "".join("%d:%d;" % (h, m) for h, m in out)
    except Exception:
        return ""


def tts_say(text):
    """Озвучить текст (v2.14): облако -> WAV -> СКЕТЧ (say_wave, динамик D8),
    иначе динамик платы (aplay), иначе браузер (/api/tts_audio)."""
    if not CFG.get("tts_on"):
        return
    clean = " ".join(str(text or "").split())[:220]
    if not clean:
        return
    with _TTS_LOCK:
        try:
            wav, err = _cloud_tts(clean)
            if not wav:
                raise RuntimeError(err)
            via = ""
            try:
                frames = _wav_frames(wav)
            except Exception:
                frames = ""
            try:                           # v2.70: микрофон не слушает свою речь
                _MIC["mute_until"] = time.time() + len(wav) / 32000.0 + 1.5
            except Exception:
                pass
            if frames and _bridge_call("say_wave", frames, timeout=8):
                via = "sketch"               # «речь» играет динамик D8
            if not via and _board_play_wav(wav):
                via = "board"
            if not via:
                import base64
                _TTS_SEQ[0] += 1
                with SH.lock:
                    SH.tts_audio = {"id": _TTS_SEQ[0],
                                    "b64": base64.b64encode(wav).decode("ascii"),
                                    "mime": "audio/wav"}
                via = "browser"
            with SH.lock:
                SH.tts_state = {"available": True, "via": via, "err": "",
                                "ts": time.time()}
        except Exception as e:
            msg = str(e)[:140]
            with SH.lock:
                SH.tts_state = {"available": False, "via": "", "err": msg,
                                "ts": time.time()}
            log("TTS:", msg)


# ===========================================================================
# [7] МОСТ К СКЕТЧУ: обработчики notify (скетч -> мы) и call (мы -> скетч)
#     v2.16: ВСЕ исходящие вызовы Bridge.call идут через ОДНУ очередь-
#     работник (_bridge_worker): RPC-канал один, параллельные вызовы из
#     потоков (диктант, учитель, TTS, чат, пинг) раньше интерливились и
#     ломали канал — мост жил только на старте. Теперь вызовы строго
#     последовательны; для обучения действует «последний главнее»
#     (устаревшие teach-вызовы выкидываются), пинг идёт ВНЕ очереди.
# ===========================================================================
_BRIDGE_Q = deque(maxlen=48)   # элементы: [method, args, timeout, channel, box, ts]
_BRIDGE_EV = threading.Event() # сигнал «в очереди что-то есть»
_BRIDGE_WORKER_ON = [False]

# v2.33: СТАТИСТИКА RPC ПО МЕТОДАМ — видно в /api/state (панель платы и
# облако): сколько вызовов каждого метода прошло/упало и последняя ошибка.
# Раньше word_push/cloud_buf_set шли с wait=False и их падения были
# НЕВИДИМЫ — владелец видел только «не работает», без причины.
_RPC_STATS = {}
_RPC_STATS_LOCK = threading.Lock()


def _rpc_stat(method, ok, err=""):
    """Отметить исход RPC-вызова (для /api/state -> bridge_stats)."""
    try:
        with _RPC_STATS_LOCK:
            st = _RPC_STATS.setdefault(
                str(method)[:32], {"ok": 0, "err": 0, "last": "", "ts": 0.0})
            if ok:
                st["ok"] += 1
            else:
                st["err"] += 1
                st["last"] = str(err)[:80]
            st["ts"] = time.time()
    except Exception:
        pass


def bridge_stats():
    """Сводка живости RPC-канала: всего ок/ошибок, топ проблемных методов."""
    with _RPC_STATS_LOCK:
        items = [(m, dict(s)) for m, s in _RPC_STATS.items()]
    ok = sum(s.get("ok", 0) for _m, s in items)
    err = sum(s.get("err", 0) for _m, s in items)
    bad = sorted(((m, s) for m, s in items if s.get("err")),
                 key=lambda kv: -kv[1].get("err", 0))[:6]
    return {"ok": ok, "err": err, "dropped": int(SH.bridge_dropped),
            "bad": [{"method": m, "ok": s.get("ok", 0), "err": s.get("err", 0),
                     "last": s.get("last", "")} for m, s in bad],
            "methods": {m: {"ok": s.get("ok", 0), "err": s.get("err", 0)}
                        for m, s in items}}

# v2.18: РЕЖИМ ТИШИНЫ. Когда скетч перестаёт отвечать (12 нейронов заняли
# loop MCU — provide_safe-обработчики исполняются по одному за итерацию
# loop), каждый вызов из очереди съедает свой полный timeout и очередь
# превращается в retry-шторм, добивающий канал. Поэтому при тишине дольше
# _BRIDGE_STALE_S все НЕсрочные вызовы СБРАСЫВАЮТСЯ (teach_last по буквам,
# say_wave и прочее устаревают за секунды), остаётся только пинг с backoff.
# Первый же пакет скетча (on_state/on_letter/...) возвращает очередь к жизни.
_BRIDGE_STALE_S = 6.0
_bridge_drop_last_log = [0.0]


def _bridge_worker():
    """Единственный поток, зовущий Bridge.call: канал RPC больше не рвётся
    от параллельных вызовов. Успешный вызов (не бросил) = скетч жив.
    v2.18: в тишину не-urgent вызовы сбрасываются — очередь не копит
    устаревшие команды и не добивает занятый нейронами MCU.
    v2.33: у каждого элемента ЕСТЬ метка постановки в очередь; если вызов
    прождал в очереди дольше своего timeout+4 с (канал занял долгий RPC —
    например, старый neuro_assign), он СБРАСЫВАЕТСЯ СРАЗУ, а не выполняется
    с опозданием в минуту (устаревший say_wave/word_push только путают
    плату). Все исходы пишутся в _RPC_STATS (видно в /api/state)."""
    while True:
        item = None
        try:
            item = _BRIDGE_Q.popleft()
        except IndexError:
            pass
        if item is None:
            _BRIDGE_EV.wait(0.2)
            _BRIDGE_EV.clear()
            continue
        method, args, timeout, channel, box = item[:5]
        ts = item[5] if len(item) > 5 else time.time()
        # v2.33: устарел, пока стоял в очереди (канал занял долгий вызов)
        if time.time() - ts > timeout + 4.0 and channel != "ping":
            _rpc_stat(method, False, "устарел в очереди (>%.0f c)" % (timeout + 4))
            with SH.lock:
                SH.bridge_dropped += 1
            log("мост:", method, "-> устарел в очереди, сброшен")
            if box is not None:
                box[0] = None
                box[1].set()
            continue
        # v2.18: тихий мост — не-urgent вызов устарел, сбрасываем
        with SH.lock:
            stale_for = time.time() - SH.bridge_ts if SH.bridge_seen else 0.0
        if stale_for > _BRIDGE_STALE_S and channel != "ping":
            _rpc_stat(method, False, "мост молчит %.0f с" % stale_for)
            with SH.lock:
                SH.bridge_dropped += 1
            now = time.time()
            if now - _bridge_drop_last_log[0] > 5.0:
                _bridge_drop_last_log[0] = now
                log("мост: тишина %.0f с — устаревшие вызовы сбрасываю "
                    "(нейроны заняли MCU; вернусь с первым пакетом)"
                    % stale_for)
            if box is not None:
                box[0] = None
                box[1].set()
            continue
        res = None
        try:
            res = Bridge.call(method, *args, timeout=timeout)
            with SH.lock:
                SH.bridge_ts = time.time()
                SH.bridge_seen = True
            _rpc_stat(method, True)
        except Exception as e:
            _rpc_stat(method, False, e)
            log("мост:", method, "->", str(e)[:120])
        if box is not None:
            box[0] = res
            box[1].set()


def _bridge_enqueue(method, args, timeout=4.0, channel="", urgent=False):
    """Поставить вызов в очередь. urgent=True (пинг) — в начало очереди.
    channel='teach' — «последний главнее»: старые невыключенные оценки
    того же канала выкидываются (буквы уже ушли — оценивать их поздно)."""
    box = [None, threading.Event()]
    if channel == "teach":
        for old in list(_BRIDGE_Q):
            if old[3] == "teach":
                try:
                    _BRIDGE_Q.remove(old)
                except ValueError:
                    pass
    try:
        if urgent:
            _BRIDGE_Q.appendleft([method, args, timeout, channel, box,
                                  time.time()])
        else:
            _BRIDGE_Q.append([method, args, timeout, channel, box,
                              time.time()])
        _BRIDGE_EV.set()
    except Exception:                        # deque переполнен — не ждём
        return None
    return box


def _bridge_call(method, *args, timeout=4, channel="", urgent=False,
                 wait=True):
    """Вызов RPC-метода скетча через очередь (никогда не кидает исключение).
    wait=True — дождаться результата (обрезка по timeout+2 с); wait=False —
    «выстрелил и забыл» (результат не нужен)."""
    if not _BRIDGE_WORKER_ON[0]:
        # работник ещё не поднят (ранние вызовы при старте) — прямой вызов
        try:
            res = Bridge.call(method, *args, timeout=timeout)
            with SH.lock:
                SH.bridge_ts = time.time()
                SH.bridge_seen = True
            return res
        except Exception as e:
            log("мост:", method, "->", str(e)[:120])
            return None
    box = _bridge_enqueue(method, args, timeout=timeout, channel=channel,
                          urgent=urgent)
    if box is None:
        return None
    if not wait:
        return None
    box[1].wait(timeout + 2.0)
    return box[0]


def _bridge_start_worker():
    if _BRIDGE_WORKER_ON[0]:
        return
    _BRIDGE_WORKER_ON[0] = True
    threading.Thread(target=_bridge_worker, daemon=True).start()
    log("мост: очередь вызовов запущена (все Bridge.call строго по одной)")


_BRIDGE_PING_PERIOD = 4.0          # сек между активными пульсами моста
_bridge_ping_last = [0.0]


def bridge_ping_tick():
    """v2.14/v2.16/v2.18: АКТИВНЫЙ пульс моста. Живость видна по ЛЮБОМУ
    пакету скетча (все on_*-обработчики); если свежих пакетов нет — пингуем
    ping ВНЕ очереди (urgent, channel='ping' — режим тишины его не рвёт).
    v2.18: backoff — чем дольше тишина, тем реже пингуем (4/8/12 с):
    спам-пингами занятый нейронами MCU не добиваем."""
    now = time.time()
    with SH.lock:
        age = (now - SH.bridge_ts) if SH.bridge_seen else _BRIDGE_PING_PERIOD
    if age < 3.0:
        _bridge_ping_last[0] = now
        return                      # свежие пакеты есть — пинг не нужен
    period = 4.0 if age < _BRIDGE_STALE_S else (8.0 if age < 30.0 else 12.0)
    if now - _bridge_ping_last[0] < period:
        return
    _bridge_ping_last[0] = now
    if now - _SKETCH.get("diag_ts", 0.0) > 15.0:
        sketch_diag_async()              # v2.68: где стоит скетч (поток моста)
    try:
        _bridge_call("ping", timeout=3, channel="ping", urgent=True, wait=False)
        # ответ разберёт работник и сам пометит живость (bridge_ts)
    except Exception:
        pass                                   # скетч не отвечает — чип покажет


# --- события от скетча (мы их provide-им) ---------------------------------
def _bridge_touch():
    """v2.16: ЛЮБОЙ пакет скетча = мост жив (раньше только on_state).
    Пока нейроны отвечают, on_msg/on_letter идут часто — чип не врёт."""
    SH.bridge_ts = time.time()
    SH.bridge_seen = True


def on_state(gen, ltr, chr_, word, say, spk, act, top, cyc):
    """Периодическая сводка скетча (~2 раза в сек): частота, буква, слово..."""
    with SH.lock:
        _bridge_touch()
        SH.gen_hz = int(gen or 0)
        SH.ltr = int(ltr or 0)
        SH.chr = str(chr_ or "")
        SH.word = str(word or "")
        SH.say = str(say or "")
        SH.spk = int(spk or 0)
        SH.act = int(act or 0)
        SH.top = int(top or -1)
        SH.cyc = int(cyc or 0)
    # v2.17: СОФТ-НЕЙРОНЫ СЛУШААТ АППАРАТНЫЕ: спайки и топ-нейрон скетча
    # зажигают нейроны соответствующей зоны (нейрон №i = зона ZONES[i])
    try:
        if (spk or 0) > 0 and 0 <= (top or -1) < len(ZONES):
            nn_excite_zone(ZONES[int(top or 0)][0],
                           min(0.4, 0.08 * int(spk or 0)))
    except Exception:
        pass


def on_msg(msg):
    """Сырой хвост MSG из total_val_c (слова нейронов со спайками)."""
    with SH.lock:
        _bridge_touch()                        # v2.16: любой пакет = мост жив
        SH.msg = str(msg or "")[:240]


def on_net(net):
    """v2.11: карта связи с нейронами из скетча — 12 символов '1'/'0',
    кто отвечал на опрос в последнем цикле (нейрон №i жив, если net[i]=='1').
    Пассивная проверка связи: видна в UI постоянно (чип «нейроны»)."""
    s = "".join(c if c in "01" else "0" for c in str(net or ""))[:12]
    with SH.lock:
        _bridge_touch()                        # v2.16: любой пакет = мост жив
        SH.net = s.ljust(12, "0")
        SH.net_ts = time.time()


def on_letter(num, chr_):
    """Новое решение нейросети букв: номер 1..33 (0 = пробел/знак) и символ.
    v2.19: наружу буква НЕ показывается (лента убрана) — она нужна только
    внутри: диктант, словарная награда, зоны «Буквы», счётчики."""
    with SH.lock:
        _bridge_touch()                        # v2.16: любой пакет = мост жив
        SH.letters_made += 1
        SH.mem_dirty = True                 # счётчик изменился — сохранит тик
    # v2.8: мгновенная сверка буквы с целью диктанта (отдельный поток:
    # в provide-обработчике Bridge.call запрещён)
    try:
        threading.Thread(target=dictation_letter, args=(str(chr_ or ""),),
                         daemon=True).start()
    except Exception:
        pass
    try:          # v2.17: нейроны зоны «Буквы» слышат каждую букву
        nn_excite_zone("Буквы", 0.1)
    except Exception:
        pass


def on_word(word):
    """Слово собрано: скетч сам играет его нотами на D8 и шлёт STIM нейронам."""
    w = str(word or "").strip()
    if not w:
        return
    with SH.lock:
        _bridge_touch()                        # v2.16: любой пакет = мост жив
        SH.words_made += 1
        SH.mem_dirty = True


def on_say(phrase):
    """ГОТОВАЯ ФРАЗА буквогенератора (v2.14): в ЧАТ больше НЕ пишем — буквы
    живут в ленте букв и панели «Фраза» (p-last-say). Сеть же её СЛУШАЕТ:
    кладёт в память, диктант учителя и словарную награду, озвучивает речью.
    v2.16: ЗДЕСЬ РАНЬШЕ ЖИЛ ГЛАВНЫЙ БАГ МОСТА: tts_say вызывался синхронно
    внутри notify-обработчика — а он тянет Bridge.call(say_wave) и HTTP-TTS
    до 40 с. Диспетчер моста блокировался, сводки переставали идти, чип
    врал «ждёт скетч». Теперь речь — только в фоновом потоке, обработчик
    освобождается за миллисекунды. Bridge.call внутри обработчиков
    ЗАПРЕЩЁН — это правило больше не нарушается нигде."""
    say = str(phrase or "").strip()
    if not say or say == SH.last_say:
        return
    SH.last_say = say
    brain_remember("bot", "🗣 «%s»" % say)      # слушаем генератор молча
    try:          # v2.17: нейроны слушают фразы буквогенератора
        nn_excite(nn_stems(say), 0.4)
    except Exception:
        pass
    try:
        threading.Thread(target=tts_say, args=(say,),
                         daemon=True).start()    # v2.16: фон, не блокируем мост
    except Exception:
        pass
    with SH.lock:
        SH.teach_q.append(say)
    _phrase_reward_async(say)                   # словарная награда (v2.8, фон)
    log("БУКВЫ", "фраза (в чат не пишем):", say)


def register_bridge():
    """Мы — сервер RPC для скетча. provide (не safe) достаточно: обработчики
    лёгкие (v2.16: все тяжёлые дела — речь, словарная награда, диктант —
    уходят в фоновые потоки) и НЕ вызывают Bridge.call (тот запрещён внутри
    обработчиков: дедлок диспетчера)."""
    Bridge.provide("on_state", on_state)
    Bridge.provide("on_msg", on_msg)
    Bridge.provide("on_net", on_net)          # v2.11: карта связи с нейронами
    Bridge.provide("on_letter", on_letter)
    Bridge.provide("on_word", on_word)
    Bridge.provide("on_say", on_say)
    Bridge.provide("on_log", on_log)          # v2.68: журнал скетча
    Bridge.provide("on_boot", on_boot)        # v2.68: шаги запуска скетча
    # v2.92: шум конденсатора (A4), активность D6 и множитель шага весов —
    # скетч шлёт их сам (notify); без обработчиков мост сыпал бы ошибками
    Bridge.provide("cap_noise", lambda v: _part_note("cap", v))
    Bridge.provide("d6_act", lambda v: _part_note("d6", v))
    Bridge.provide("learn_scale", lambda v: _part_note("scale", v))
    log("мост: on_state/on_msg/on_letter/on_word/on_say/on_log/on_boot зарегистрированы")


# v2.68: ЖУРНАЛ И ДИАГНОСТИКА СКЕТЧА --------------------------------------
_SKETCH = {"log": deque(maxlen=120), "boot": 0, "boot_ts": 0.0,
           "diag": None, "diag_ts": 0.0, "diag_err": ""}
BOOT_STAGES = {1: "мост поднят", 2: "шина нейронов (I2C)", 3: "приёмник камеры (UART)",
               4: "EEPROM: частоты и веса", 9: "запуск завершён"}
LOOP_STAGES = {0: "между проходами", 1: "кадр камеры на D4", 2: "генератор D4",
               3: "динамик", 4: "микрофон", 5: "опрос нейронов (I2C)",
               6: "буквогенератор", 7: "вывод на D6", 8: "приём кадров камеры",
               9: "назначение нейронов", 10: "запись EEPROM", 11: "обмен с Linux",
               12: "анализ и сводка"}


# v2.92: КВАНТЫ НА D6 И ШУМ КОНДЕНСАТОРА ---------------------------------
# Галочки в облаке (⚙ → Общие → «Кванты на D6»): облако присылает в ответе
# канала связи строку "grav:1,muon:0,phot:1,gen:800,cpu:1,min:100,max:4.0",
# плата передаёт её скетчу (particle_cfg) — только когда она изменилась.
# Обратно скетч сообщает шум на A4, активность D6 и множитель шага весов;
# они уходят в облако в канале связи («noise»).
_PART = {"cap": 0, "d6": 0, "scale": 1, "ts": 0.0, "cfg": "", "cfg_ts": 0.0, "err": ""}


def _part_note(key, v):
    _bridge_touch()
    try:
        _PART[key] = int(v)
    except (TypeError, ValueError):
        return
    _PART["ts"] = time.time()


def _part_apply(cfg):
    """Конфиг квантов от облака -> скетч (только при изменении или раз в 5 мин,
    если скетч перезапускался)."""
    if isinstance(cfg, dict):
        cfg = ",".join("%s:%s" % (k, cfg[k]) for k in
                       ("grav", "muon", "phot", "gen", "cpu", "min", "max") if k in cfg)
    cfg = str(cfg or "")[:120]
    if not cfg:
        return
    if cfg == _PART["cfg"] and time.time() - _PART["cfg_ts"] < 300:
        return
    try:
        _bridge_call("particle_cfg", cfg, wait=False)
        _PART.update(cfg=cfg, cfg_ts=time.time(), err="")
        log("КВАНТЫ", "скетчу:", cfg)
    except Exception as e:
        _PART["err"] = str(e)[:120]


def on_log(line):
    """Строка журнала скетча (вместо Monitor — без блокировок)."""
    _bridge_touch()
    t = str(line or "")[:240]
    now = time.time()
    _SKETCH["log"].append((now, t))
    # сводка [GEN] идёт 2 раза в секунду — в лог приложения раз в 10 с
    if t.startswith("[GEN]"):
        if now - _SKETCH.get("gen_print", 0.0) < 10.0:
            return
        _SKETCH["gen_print"] = now
    try:
        print("[СКЕТЧ] " + t, flush=True)       # видно в логах приложения
    except Exception:
        pass


def on_boot(stage):
    _bridge_touch()
    try:
        st = int(stage or 0)
    except (TypeError, ValueError):
        st = 0
    _SKETCH["boot"], _SKETCH["boot_ts"] = st, time.time()
    log("СКЕТЧ", "запуск: шаг %d — %s" % (st, BOOT_STAGES.get(st, "?")))


def sketch_diag_async():
    """Спросить скетч diag из ПОТОКА МОСТА: где он, если loop() стоит."""
    def _job():
        try:
            r = _bridge_call("diag", timeout=4, channel="ping", urgent=True,
                             wait=True)
            if r is None:
                _SKETCH["diag_err"] = "нет ответа на diag"
                return
            parts = str(r).split(",")
            b, l, c, up = (int(x) for x in (parts + ["0"] * 4)[:4])
            prev = _SKETCH.get("diag") or {}
            _SKETCH["diag"] = {"boot": b, "boot_name": BOOT_STAGES.get(b, "шаг %d" % b),
                               "loop": l, "loop_name": LOOP_STAGES.get(l, "?"),
                               "count": c, "up": up,
                               "stuck": bool(prev) and prev.get("count") == c}
            _SKETCH["diag_ts"] = time.time()
            _SKETCH["diag_err"] = ""
            SH.bridge_seen = True
        except Exception as e:
            _SKETCH["diag_err"] = str(e)[:120]
    threading.Thread(target=_job, daemon=True).start()


# ===========================================================================
# [8] ЧАТ: команды, обычный режим (стимул нейронам) и ШЁПОТ (нейросеть)
# ===========================================================================
def _stats_line():
    """v2.19: без букв и слов буквогенератора. v2.26: + память и состояние
    мозг-почты — видно и в ответах, и в логах, почему «почта не работает»."""
    with SH.lock:
        bm = dict(SH.brainmail)
        nmem = len(SH.memory)
        line = ("генератор %d Гц, спайков %d, активно %d/12, реплик %d"
                % (SH.gen_hz, SH.spk, SH.act, nmem))
    err = str(bm.get("err") or "")
    if err:
        line += ", почта: %s" % err[:60]
    elif int(bm.get("letters") or 0):
        line += ", почта: писем %d, ответов %d" % (
            int(bm.get("letters") or 0), int(bm.get("answered") or 0))
    else:
        line += ", почта: тихо"
    return line


def _search_job(q):
    """Фоновый поиск в облаке (команда /поиск): результаты в чат, новые
    слова — в словарь, первое найденное — в диктант."""
    try:
        r, err = cloud_search(q)
        if not r or not r.get("ok"):
            add_message("system", "🌐 Поиск не удался: %s" % (err or "?"), "Поиск")
            return
        results = r.get("results") or []
        if not results:
            add_message("system", "🌐 По «%s» ничего не нашлось." % q, "Поиск")
            return
        lines, new_w = [], []
        for it in results[:3]:
            lines.append("• %s — %s" % (
                str(it.get("title") or "")[:80],
                " ".join(str(it.get("snippet") or "").split())[:130]))
            for w in re.findall(r"[а-яё]{4,}", str(it.get("snippet") or "").lower())[:12]:
                if len(new_w) >= 6:
                    break
                if not dict_has(w):
                    new_w.extend(dict_learn_words([w], limit=1))
        msg = "🌐 Поиск «%s»:\n%s" % (q, "\n".join(lines))
        if new_w:
            msg += "\n📚 Выучил новые слова: %s." % ", ".join(new_w)
        add_message("system", msg, "Поиск")
        brain_remember("bot", "поиск «%s»: %s" % (q, " | ".join(lines)[:400]))
        for w in re.findall(r"[а-яё]{3,}", str(results[0].get("title") or "").lower())[:1]:
            dictation_queue(w, "поиск")
    except Exception:
        traceback.print_exc()


# ===========================================================================
# [8a] v2.40: ТЕНЗОРНАЯ СЕТЬ ВЕДЁТ КОД И МЫСЛИ, ЗОННАЯ ПОМОГАЕТ; ОБУЧЕНИЕ НА
#      ВОПРОСАХ И ОТВЕТАХ; ВЕСЬ МОЗГ ОБЛАКА (POST /api/brain/think)
# ---------------------------------------------------------------------------
#   * tn_lead_zone: зону решает ТЕНЗОРНАЯ сеть (вероятности по всем зонам),
#     зонная сеть добавляет свой голос (до +0.35); тензор ещё не готов —
#     честно решает зонная сеть, нет и её голоса — ключевые слова;
#   * МЫСЛИ (brain_speak): тему мысли выбирает тензор; генератор собирает
#     GEN_CANDIDATES вариантов фразы из словаря, тензор оставляет тот,
#     что точнее «про» эту тему (зонная сеть — подсказка); выбор виден в
#     /api/state -> gen.lead;
#   * КОД (code_intent/_code_job): задачу на код узнают ключи или тензор
#     (зона «Кирпичики» с уверенностью от 0.6 + глагол «напиши/сделай…»);
#     код собирают: свои заготовки -> ИИ-агент облака (весь мозг, режим
#     code) -> заготовки облака -> ПАМЯТЬ КОДА (похожая прошлая задача,
#     честно помечено). Синтаксис Python проверяется прямо на плате.
#     Удачный код — пример тензору («это задача на код») и запись в память;
#   * ОБУЧЕНИЕ НА ВОПРОСАХ И ОТВЕТАХ (qa_*), ЧЕСТНО: тензор учится на
#     вопросе, только когда зона известна НЕ из его же догадки — ключи
#     (как и раньше), ответ облака из источника (база знаний, интернет,
#     расчёт), подтверждение человека («верно»), исправление («правильный
#     ответ: …»). Из памяти повторяются ТОЛЬКО подтверждённые ответы, и
#     только если тензор уверенно не видит в вопросе другой смысл;
#     «неверно» гасит связи зонной сети и снимает подтверждение;
#   * ОБЛАКО — ВЕСЬ МОЗГ: страница «Облако» (route=cloud) и вопрос, на
#     который у зон нет ни ключей, ни голоса сетей, идут в облако
#     POST /api/brain/think (база знаний, интернет, калькулятор, документы,
#     ИИ-агент кода); старое облако без маршрута — прежняя мозг-почта.
#     Файлы: neuro12_qa.json, neuro12_codemem.json (рядом с памятью).
# ===========================================================================
GEN_CANDIDATES = 3            # вариантов мысли, из которых выбирает тензор
QA_MAX = 300                  # пар «вопрос/ответ» в памяти платы
QA_SIM = 0.75                 # похожесть вопросов для ответа из памяти
CODEMEM_MAX = 80              # задач в памяти кода
_QA = {"items": None, "last": None,
       "stats": {"asked": 0, "pairs": 0, "confirmed": 0, "rejected": 0,
                 "corrected": 0, "recalled": 0, "tn_examples": 0,
                 "cloud": 0}}
_QA_LOCK = threading.RLock()
_CODEMEM = {"items": None}
_CLOUD_ZONE_MAP = {"База знаний": "Знания", "Интернет-источники": "Знания",
                   "Википедия": "Знания", "Математика": "Математика",
                   "Код": "Кирпичики", "Обучение": "Знания"}
_CLOUD_GROUNDED = ("База знаний", "Интернет-источники", "Википедия",
                   "Математика", "Обучение")


# ------------------------- тензор ведёт, зонная помогает ---------------------
def tn_probs(text):
    """Все вероятности тензорной сети по зонам | None (сеть не готова)."""
    feats = tn_features(text)
    if not feats:
        return None
    x, _z, _w = _TN_K["tn_parse"](_tn_token(feats, tn_hw_levels(), 0), TN_D)
    with _TN_LOCK:
        if not _tn_ready_locked():
            return None
        _h, p = _TN_K["tn_forward"](_TN["W1"], _TN["b1"], _TN["W2"],
                                    _TN["b2"], x)
    return list(p[:len(_tn_znames())])


def tn_lead_zone(text):
    """Зона по решению тензора + голос зонной сети.
    Возврат {zone, p, by, tn}."""
    names = _tn_znames()
    text = str(text or "")
    if not text.strip():
        return {"zone": "", "p": 0.0, "by": "", "tn": False}
    p = tn_probs(text)
    try:
        votes = nn_votes(nn_stems(text))
    except Exception:
        votes = {}
    vmax = max([v for v in votes.values() if v > 0] or [0.0])
    best, bs = "", 0.0
    for i, z in enumerate(names):
        s = p[i] if p else 0.0
        if vmax > 0:
            s += 0.35 * max(0.0, votes.get(z, 0.0)) / vmax
        if s > bs:
            best, bs = z, s
    if p:
        by = "тензор" + (" + зонная сеть" if vmax > 0 else "")
    elif vmax > 0:
        by = "зонная сеть (тензор ещё учится)"
    else:
        kw = zone_of(text)
        if kw in names:
            return {"zone": kw, "p": 0.0, "by": "ключи", "tn": False}
        return {"zone": "", "p": 0.0, "by": "", "tn": False}
    return {"zone": best, "p": round(bs, 3), "by": by, "tn": bool(p)}


def _tn_pick_thought(cands, zone, cam=None):
    """Из вариантов мысли тензор берёт тот, что вернее о зоне zone (нет
    зоны — самый «уверенный» для сети); зонная сеть добавляет подсказку.
    v2.88: cam — голоса камеры с нейросетью (0..1) за каждый вариант."""
    names = _tn_znames()
    zi = names.index(zone) if zone in names else -1
    best, bs, used = cands[0], -1.0, "первый вариант"
    for i_, c in enumerate(cands):
        s = 0.0
        p = tn_probs(c)
        if p:
            s = p[zi] if zi >= 0 else max(p)
            used = "тензор"
        try:
            v = nn_votes(nn_stems(c)).get(zone, 0.0) if zone else 0.0
            if v > 0:
                s += 0.25 * min(1.0, v / 3.0)
                if used != "тензор":
                    used = "зонная сеть"
        except Exception:
            pass
        if cam and i_ < len(cam):          # v2.88: камера голосует тем, что видит
            s += float(CFG.get("cam_ai_w", 0.35) or 0) * cam[i_]
        if s > bs:
            best, bs = c, s
    if cam:
        used += " + камера"
    return best, {"by": used, "score": round(max(bs, 0.0), 3),
                  "cands": len(cands)}


# ------------------------------ весь мозг облака -----------------------------
def _mem_ctx(n=8):
    """Последние реплики диалога платы — контекст для облака."""
    with SH.lock:
        mem = list(SH.memory)[-n:]
    return [{"role": "user" if m.get("role") == "user" else "bot",
             "text": str(m.get("text") or "")[:400]} for m in mem]


def cloud_think(q, mode="auto", ctx=None, timeout=40):
    """v2.40: спросить ВЕСЬ МОЗГ облака (POST /api/brain/think): база знаний,
    интернет, калькулятор, документы, память ответов, ИИ-агент кода.
    Возврат (ответ-dict|None, ошибка)."""
    url, token = _cloud_base()
    if not url:
        return None, "облако не задано (панель «Настройки»)"
    r, err = _http_json_err(url + "/api/brain/think",
                            {"text": str(q)[:6000], "from": _hostname(),
                             "mode": mode, "ctx": ctx or []},
                            token=token, timeout=timeout)
    if r is None:
        return None, err
    if not r.get("ok"):
        return None, (r.get("error") or "облако не ответило")
    _QA["stats"]["cloud"] += 1
    return r, ""


def _cloud_brain_job(text, user=""):
    """Фон: у зон ни ключей, ни голоса сетей — отвечает весь мозг облака."""
    try:
        r, err = cloud_think(text, ctx=_mem_ctx(), timeout=45)
        if not r or not str(r.get("answer") or "").strip():
            log("ОБЛАКО-МОЗГ", "не ответило:", str(err or "пусто")[:80])
            return
        ans = str(r["answer"])
        cz = str(r.get("zone") or "")
        if cz == "Облако" and "не знаю" in ans.lower()[:200]:
            return                       # облако тоже не знает — не шумим
        add_message("bot", "☁ Весь мозг облака (зона «%s»):\n%s"
                    % (cz or "?", ans[:6000]), "Облако", user=user)
        brain_remember("bot", "облако ответило: %s" % ans[:300])
        bz = _CLOUD_ZONE_MAP.get(cz, "")
        qa_note(text, ans, bz or "Облако", "облако",
                grounded=(cz in _CLOUD_GROUNDED and bool(bz)), cloud_zone=cz)
    except Exception:
        traceback.print_exc()


# ------------------------------- код -----------------------------------------
_CODE_ASK = re.compile(
    r"\b(?:напиши|написать|сделай|создай|собери|сгенерируй|набросай|нужен|"
    r"нужна|нужно)\b.{0,50}?\b(?:код\w*|программ\w*|скрипт\w*|функци\w*|"
    r"скетч\w*|класс\w*|сайт\w*|html|sql|python|питон\w*|arduino|ардуино\w*|"
    r"javascript|bash)", re.I | re.S)
_CODE_VERB = re.compile(r"\b(?:напиши|сделай|создай|собери|сгенерируй)\b",
                        re.I)
_CODE_NOT = re.compile(r"кирпич|приложени|апплаб|app\s*lab|рецепт", re.I)


def code_intent(text):
    """Задача на код? (да/нет, решение тензора, кто узнал)."""
    t = str(text or "")
    if len(t) < 8 or _CODE_NOT.search(t):
        return False, {}, ""
    lead = tn_lead_zone(t)
    if _CODE_ASK.search(t):
        return True, lead, "ключи"
    if lead.get("tn") and lead.get("zone") == "Кирпичики" and \
            lead.get("p", 0) >= 0.6 and _CODE_VERB.search(t):
        return True, lead, "тензор"
    return False, lead, ""


def _cm_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()),
                            "neuro12_codemem.json")
    except Exception:
        return "neuro12_codemem.json"


def _cm_items():
    if _CODEMEM["items"] is None:
        try:
            with open(_cm_path(), "r", encoding="utf-8") as f:
                d = json.loads(f.read() or "[]")
            _CODEMEM["items"] = d if isinstance(d, list) else []
        except Exception:
            _CODEMEM["items"] = []
    return _CODEMEM["items"]


def codemem_add(task, text, src):
    """Удачный код -> память кода платы."""
    it = _cm_items()
    st = sorted(set(nn_stems(task)))
    it[:] = [x for x in it if x.get("stems") != st]
    it.append({"task": str(task)[:300], "text": str(text)[:8000],
               "src": str(src)[:40], "ts": time.time(), "stems": st})
    del it[:-CODEMEM_MAX]
    try:
        with open(_cm_path(), "w", encoding="utf-8") as f:
            f.write(json.dumps(it, ensure_ascii=False))
    except Exception:
        pass


def codemem_find(task, min_sim=0.5):
    st = set(nn_stems(task))
    if not st:
        return None
    best, bs = None, 0.0
    for x in _cm_items():
        s2 = set(x.get("stems") or [])
        if s2:
            s = len(st & s2) / float(len(st | s2))
            if s > bs:
                best, bs = x, s
    if not best or bs < min_sim:
        return None
    return dict(best, sim=round(bs, 2))


def _py_check(text):
    """Синтаксис Python-блоков ответа — проверка прямо на плате."""
    blocks = re.findall(r"```(?:python|py)[^\n]*\n(.*?)```", str(text or ""),
                        re.S | re.I)
    if not blocks:
        return ""
    bad = []
    for i, b in enumerate(blocks[:4]):
        try:
            compile(b, "<код %d>" % (i + 1), "exec")
        except SyntaxError as e:
            bad.append("блок %d, строка %s: %s" % (i + 1, e.lineno, e.msg))
    if bad:
        return "⚠ Проверка синтаксиса на плате: " + "; ".join(bad)
    return "✓ Синтаксис Python проверен на плате (compile)."


def _code_done(task, reply, who):
    """Код получен: показать, проверить, запомнить, научить тензор."""
    note = _py_check(reply)
    add_message("system", "⌨ %s собрал код (без LLM):\n%s%s"
                % (who, str(reply)[:6000], ("\n\n" + note) if note else ""),
                "Код")
    codemem_add(task, reply, who)
    try:     # удачный код — пример тензору: такие слова = задача на код
        if tn_observe(task, "Кирпичики", "код"):
            _QA["stats"]["tn_examples"] += 1
    except Exception:
        pass
    brain_remember("bot", "код по задаче «%s» (%s)" % (str(task)[:120], who))


# --------------------------- обучение на вопросах/ответах --------------------
def _qa_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_qa.json")
    except Exception:
        return "neuro12_qa.json"


def _qa_items():
    if _QA["items"] is None:
        try:
            with open(_qa_path(), "r", encoding="utf-8") as f:
                d = json.loads(f.read() or "{}")
            _QA["items"] = list(d.get("items") or [])
            for k, v in (d.get("stats") or {}).items():
                if k in _QA["stats"]:
                    _QA["stats"][k] = int(v or 0)
        except Exception:
            _QA["items"] = []
    return _QA["items"]


def _qa_save():
    with _QA_LOCK:
        data = {"items": list(_qa_items())[-QA_MAX:],
                "stats": dict(_QA["stats"])}
    try:
        with open(_qa_path(), "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def qa_note(q, a, zone, src, grounded=False, cloud_zone=""):
    """После ответа: запомнить, что было последним (для «верно/неверно»);
    ответ с опорой на источник облака — пара в память + пример тензору."""
    q, a = str(q or "").strip(), str(a or "").strip()
    if not q or not a:
        return
    st = sorted(set(nn_stems(q)))
    with _QA_LOCK:
        _qa_items()
        _QA["last"] = {"q": q[:400], "a": a[:4000], "zone": zone,
                       "src": src, "ts": time.time()}
        _QA["stats"]["asked"] += 1
        if not grounded or not st:
            return
        items = _QA["items"]
        old = next((x for x in items if x.get("stems") == st), None)
        if old and old.get("ok"):
            return                       # подтверждённое не перетираем
        if old:
            items.remove(old)
        items.append({"q": q[:400], "a": a[:4000], "zone": zone, "src": src,
                      "ok": False, "good": 0, "bad": 0, "ts": time.time(),
                      "stems": st})
        del items[:-QA_MAX]
        _QA["stats"]["pairs"] += 1
    _qa_save()
    if zone in _tn_znames():
        try:
            if tn_observe(q, zone, "облако: %s" % cloud_zone):
                _QA["stats"]["tn_examples"] += 1
        except Exception:
            pass
    mn_feed("Вопрос: %s\nОтвет: %s" % (q, a[:3000]), q[:80], "ответ облака")


def qa_feedback(v):
    """«верно» / «неверно» про ПОСЛЕДНИЙ ответ платы."""
    with _QA_LOCK:
        last = _QA.get("last")
    if not last:
        return ("Пока не на что ставить оценку: я ещё ничего не ответил "
                "после запуска.")
    q, a, zone = last["q"], last["a"], last.get("zone") or ""
    st = sorted(set(nn_stems(q)))
    with _QA_LOCK:
        items = _qa_items()
        rec = next((x for x in items if x.get("stems") == st), None)
        if rec is None:
            rec = {"q": q, "a": a, "zone": zone, "src": last.get("src"),
                   "ok": False, "good": 0, "bad": 0, "stems": st}
            items.append(rec)
        if v > 0:
            rec.update({"a": a, "zone": zone, "ok": True,
                        "good": int(rec.get("good") or 0) + 1,
                        "ts": time.time()})
            _QA["stats"]["confirmed"] += 1
        else:
            rec["bad"] = int(rec.get("bad") or 0) + 1
            if rec.get("a") == a:
                rec["ok"] = False
            _QA["stats"]["rejected"] += 1
        del items[:-QA_MAX]
    _qa_save()
    done = []
    if zone in _ZONES_D:
        try:
            n = nn_learn(q, zone, 1 if v > 0 else -1)
            done.append("зонная сеть: %d связей" % int(n or 0))
        except Exception:
            pass
    if v < 0:
        return ("Принял: ответ на «%s» неверный (%s). Из памяти я его не "
                "повторю; знаешь верный — напиши «правильный ответ: …»."
                % (q[:80], ", ".join(done) or "отметил"))
    try:
        if zone in _tn_znames() and tn_observe(q, zone, "ответ подтверждён"):
            _QA["stats"]["tn_examples"] += 1
            done.append("тензор: +1 пример")
    except Exception:
        pass
    mn_feed("Вопрос: %s\nОтвет: %s" % (q, a[:3000]), q[:80],
            "ответ подтверждён")
    done.append("сеть материалов: пара в очереди")
    return ("Запомнил: ответ на «%s» подтверждён (%s). На тот же вопрос "
            "отвечу так же." % (q[:80], ", ".join(done)))


def qa_correct(ans):
    """«правильный ответ: …» — исправление последнего ответа."""
    ans = str(ans or "").strip()
    with _QA_LOCK:
        last = _QA.get("last")
    if not last or not ans:
        return ("Не вижу, к какому вопросу этот ответ: сначала спроси, а "
                "потом поправь меня «правильный ответ: …».")
    q = last["q"]
    st = sorted(set(nn_stems(q)))
    with _QA_LOCK:
        items = _qa_items()
        items[:] = [x for x in items if x.get("stems") != st]
        items.append({"q": q, "a": ans[:4000], "zone": "Знания",
                      "src": "исправление", "ok": True, "good": 1, "bad": 0,
                      "ts": time.time(), "stems": st})
        del items[:-QA_MAX]
        _QA["stats"]["corrected"] += 1
        _QA["last"] = dict(last, a=ans[:4000], zone="Знания")
    _qa_save()
    try:
        if tn_observe(q, "Знания", "исправление человека"):
            _QA["stats"]["tn_examples"] += 1
    except Exception:
        pass
    mn_feed("Вопрос: %s\nОтвет: %s" % (q, ans[:3000]), q[:80], "исправление")
    brain_remember("bot", "исправление: на «%s» — «%s»" % (q[:120], ans[:200]))
    return ("Запомнил: на «%s» отвечаю «%s». Это подтверждённое знание: "
            "пара в памяти, пример тензору, материал сети." % (q[:80],
                                                              ans[:200]))


_QWORDS = re.compile(r"^(?:как|какой|какая|какое|какие|что|чем|кто|где|когда|"
                     r"почему|зачем|сколько|можно|нужно ли|есть ли|подскажите|"
                     r"подскажи|скажите|скажи)\b", re.I)


def qa_not_answer(q, a):
    """v2.81: «ответ», который ответом не является: вопрос(ы) вместо знания
    или эхо самого вопроса. Так облако v3.143 положило платам материалы
    (вопрос = заголовок статьи, ответ = тот же вопрос из чата) — и мозг
    стал отвечать вопросом на вопрос."""
    a = re.sub(r"\s+", " ", str(a or "")).strip()
    if not a:
        return True
    parts = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", a) if x.strip()]
    nq = sum(1 for x in parts if x.endswith("?") or
             (_QWORDS.match(x) and not x.endswith((".", "!", "…"))))
    long_decl = sum(1 for x in parts if not x.endswith("?") and len(x.split()) >= 8)
    if parts and nq / float(len(parts)) >= 0.6 and long_decl == 0:
        return True
    qw = re.findall(r"[а-яёa-z0-9]+", str(q or "").lower())
    aw = re.findall(r"[а-яёa-z0-9]+", a.lower())
    n = min(len(qw), 8)
    return bool(n) and aw[:n] == qw[:n]


def qa_recall(q):
    """Подтверждённый ответ на тот же вопрос | None. Тензор — страж: если
    он уверенно видит в вопросе ДРУГУЮ зону, память не подставляется."""
    st = set(nn_stems(q))
    if not st:
        return None
    with _QA_LOCK:
        items = [x for x in _qa_items() if x.get("ok") and
                 int(x.get("good") or 0) > int(x.get("bad") or 0) and
                 not qa_not_answer(x.get("q"), x.get("a"))]
    best, bs = None, 0.0
    for x in items:
        s2 = set(x.get("stems") or [])
        if s2:
            s = len(st & s2) / float(len(st | s2))
            if s > bs:
                best, bs = x, s
    if not best or bs < QA_SIM or (len(st) < 2 and bs < 1.0):
        return None
    try:
        tz = tn_predict(q)
    except Exception:
        tz = None
    if tz and best.get("zone") in _tn_znames() and tz[0] != best["zone"] \
            and tz[1] >= TN_OVERRIDE:
        return None
    with _QA_LOCK:
        _QA["stats"]["recalled"] += 1
    return dict(best, sim=round(bs, 2))


def _qa_status_raw():
    with _QA_LOCK:
        items = _qa_items()
        return dict(_QA["stats"], items=len(items),
                    verified=sum(1 for x in items if x.get("ok")),
                    from_cloud=sum(1 for x in items
                                   if x.get("src") == "облако"),
                    codemem=len(_cm_items()),
                    sync={k: _USYNC.get(k) for k in
                          ("pushed", "pulled", "err", "ts")})


def _code_job(task):
    """Фоновая запись кода (команда /код и задачи, что узнал тензор).
    v2.24: мозг собирает функцию САМ (локальные заготовки, без LLM);
    песочница облака проверяет. v2.40: дальше — ИИ-агент облака (весь
    мозг, режим code), заготовки облака, память кода; синтаксис Python
    проверяется на плате; удачный код учит тензор и пополняет память."""
    try:
        payload, why = _selfcode_local(task)
        if not payload:
            r, cerr = cloud_think(task, mode="code", ctx=_mem_ctx(),
                                  timeout=40)
            if r and r.get("answer") and r.get("zone") == "Код":
                _code_done(task, str(r["answer"]), "ИИ-агент облака")
                return
            # v2.37: локальных заготовок не хватило — заготовки облака
            cg, cerr2 = cloud_codegen(task)
            if cg and cg.get("reply"):
                _code_done(task, str(cg["reply"]), "Облако (заготовки)")
                return
            mem = codemem_find(task)
            if mem:
                add_message("system",
                            "🧠 Сейчас код не собрался (облако: %s), но в "
                            "памяти кода есть похожая задача «%s» (сходство "
                            "%.2f) — вот что тогда получилось:\n%s"
                            % (cerr or cerr2 or "нет ответа",
                               mem["task"][:80], mem["sim"],
                               mem["text"][:6000]), "Код")
                return
            add_message("system",
                        "⌨ Код не получился: мозг не смог собрать функцию сам "
                        "(%s), облако тоже (%s). LLM код не пишет — только "
                        "наставляет." % (why or "заготовки не подошли",
                                         cerr2 or cerr or "нет ответа"), "Код")
            return
        name = str(payload.get("name") or payload.get("id") or "функция")
        code = str(payload.get("code") or "")
        res, err = cloud_run_code(code, (payload.get("tests") or [task])[0])
        if res and res.get("ok"):
            out = str(res.get("result") or res.get("output") or "").strip()[:400]
            _skill_publish(payload)
            with SH.lock:
                SH.cloud_code = code[:24000]
                SH.mem_dirty = True
            add_message("system", "🧠 Мозг сам собрал «%s» (без нейросети):\n%s"
                        % (name, code[:1200]), "Код")
            add_message("system", "▶ Прогон в песочнице: %s"
                        % (out or "выполнено"), "Код")
            codemem_add(task, "```python\n%s\n```" % code[:7000],
                        "свои заготовки")
            try:
                if tn_observe(task, "Кирпичики", "код"):
                    _QA["stats"]["tn_examples"] += 1
            except Exception:
                pass
            return
        add_message("system",
                    "⌨ Код не получился: свой вариант не прошёл песочницу (%s)."
                    % str(err or (res or {}).get("error") or "?")[:120], "Код")
    except Exception:
        traceback.print_exc()


# ===========================================================================
# [8b] v2.41: ЕДИНЫЙ МОЗГ — ПЛАТА И ОБЛАКО ДОПОЛНЯЮТ ДРУГ ДРУГА
# ---------------------------------------------------------------------------
#   * Письмо облака с пометкой «unify» (облако v3.29 спрашивает плату, когда
#     его собственный ответ слабый) получает ответ С ЧЕСТНОЙ ПОМЕТКОЙ ОПОРЫ:
#       1) подтверждённый человеком ответ из памяти платы (qa_recall);
#       2) память кода платы (похожая задача, уже собранный код);
#       3) файл, загруженный на плату (выдержка предложений по вопросу);
#       4) живое состояние платы (зоны Статус / Сигнал / Камера — числа и
#          показания, которые есть только у неё);
#       иначе — мнение зонной сети БЕЗ опоры (grounded=false): облако не
#       выдаёт его за факт и в МАКС не отправляет.
#   * Обмен знанием (unify_sync_worker, раз в 5 мин): подтверждённые на
#     плате ответы и память кода -> облако (POST /api/brain/learn);
#     подтверждённое облаком -> память платы (GET /api/brain/knowledge),
#     с источником «облако». Тензор на чужих парах НЕ учится — зона ответа
#     облака плате неизвестна, а угадывать её было бы нечестно.
#   Файл состояния обмена: neuro12_unify.json (рядом с памятью).
# ===========================================================================
UNIFY_SYNC_EVERY = 300.0
_USYNC = {"push_ts": 0.0, "pull_ts": 0.0, "pushed": 0, "pulled": 0,
          "err": "", "ts": 0.0, "loaded": False}
_LIVE_ZONES = ("Статус", "Сигнал", "Камера")
_LIVE_ASK = re.compile(r"плат|нейрон|спайк|сигнал|частот|герц|генератор|"
                       r"камер|видишь|датчик|\bпин\b|статус", re.I)


def _doc_excerpt(txt, max_sents=3):
    """Лучший файл платы по вопросу -> (имя, выдержка) | None."""
    st = set(nn_stems(txt))
    if len(st) < 1:
        return None
    with SH.lock:
        docs = [d for d in SH.docs if d.get("text")]
    best, bs = None, 0.0
    for d in docs:
        words = set(w[:5] for w in re.findall(r"[а-яёa-z0-9]{3,}",
                                              str(d.get("text"))[:60000]
                                              .lower()))
        hit = len(st & words)
        cov = hit / float(len(st))
        if hit >= min(2, len(st)) and cov >= 0.5 and cov > bs:
            best, bs = d, cov
    if not best:
        return None
    sents = re.split(r"(?<=[.!?…])\s+", re.sub(r"\s+", " ",
                                                str(best["text"])[:60000]))
    scored = []
    for i, s in enumerate(sents):
        if not (20 <= len(s) <= 400):
            continue
        sw = set(w[:5] for w in re.findall(r"[а-яёa-z0-9]{3,}", s.lower()))
        k = len(st & sw)
        if k:
            scored.append((k, -i, s))
    if not scored:
        return None
    top = sorted(scored, reverse=True)[:max_sents]
    top.sort(key=lambda x: -x[1])               # в порядке текста
    return str(best.get("name") or "файл"), " ".join(s for _k, _i, s in top)


def unify_answer(txt, z_net=""):
    """Ответ на письмо «единого мозга»: (текст, зона, опора?, источник)."""
    try:
        rc = qa_recall(txt)
    except Exception:
        rc = None
    if rc:
        return (rc["a"], rc.get("zone") or "Обучение", True,
                "подтверждённый ответ платы (сходство %.2f)" % rc["sim"])
    try:
        ci, _l, _w = code_intent(txt)
        if ci:
            mem = codemem_find(txt, 0.6)
            if mem:
                return (mem["text"], "Кирпичики", True,
                        "память кода платы: «%s»" % mem["task"][:60])
    except Exception:
        pass
    try:
        de = _doc_excerpt(txt)
        if de:
            return (de[1], "Знания", True, "файл «%s» на плате" % de[0][:60])
    except Exception:
        pass
    weak, zone = zone_reply(txt[:1500], zone=z_net)
    # живое состояние — опора, только если спрашивают именно о плате (ключи
    # зон ловят подстроки: «дела» в «делают» — это не вопрос о статусе)
    if zone in _LIVE_ZONES and _LIVE_ASK.search(txt):
        return weak, zone, True, "живое состояние платы (зона «%s»)" % zone
    reply = _long_reply(txt, "", z_net) or weak
    lead = {}
    try:
        lead = tn_lead_zone(txt)
    except Exception:
        pass
    src = "зонная сеть платы" + (" (зону выбрал %s)" % lead["by"]
                                 if lead.get("by") else "")
    return reply, zone, False, src


def _usync_path():
    try:
        return os.path.join(os.path.dirname(_mem_path()), "neuro12_unify.json")
    except Exception:
        return "neuro12_unify.json"


def _usync_load():
    if _USYNC["loaded"]:
        return
    try:
        with open(_usync_path(), "r", encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
        for k in ("push_ts", "pull_ts", "pushed", "pulled"):
            if k in d:
                _USYNC[k] = d[k]
    except Exception:
        pass
    _USYNC["loaded"] = True


def _usync_save():
    try:
        with open(_usync_path(), "w", encoding="utf-8") as f:
            f.write(json.dumps({k: _USYNC[k] for k in
                                ("push_ts", "pull_ts", "pushed", "pulled")}))
    except Exception:
        pass


def unify_sync_once():
    """Один круг обмена подтверждённым знанием с облаком."""
    url, token = _cloud_base()
    if not url:
        return {"ok": False, "error": "облако не задано"}
    _usync_load()
    host = _hostname()
    with _QA_LOCK:
        items = list(_qa_items())
    new = [x for x in items if x.get("ok") and x.get("src") != "облако" and
           float(x.get("ts") or 0) > float(_USYNC["push_ts"])]
    code = [x for x in _cm_items() if x.get("src") != "облако" and
            float(x.get("ts") or 0) > float(_USYNC["push_ts"])]
    out = {"ok": True, "pushed": 0, "pulled": 0}
    if new or code:
        r, err = _http_json_err(url + "/api/brain/learn", {
            "board": host,
            "pairs": [{"q": x["q"], "a": x["a"], "src": x.get("src") or ""}
                      for x in new[-200:]],
            "code": [{"task": x["task"], "text": x["text"]}
                     for x in code[-30:]]}, token=token, timeout=20)
        if r and r.get("ok"):
            _USYNC["push_ts"] = max([float(x.get("ts") or 0)
                                     for x in new + code] + [0.0])
            _USYNC["pushed"] += len(new) + len(code)
            out["pushed"] = len(new) + len(code)
        else:
            _USYNC["err"] = str(err or (r or {}).get("error") or "?")[:120]
    import urllib.parse
    rr, err = _http_get_json_err(
        url + "/api/brain/knowledge?board=%s&since=%s" % (
            urllib.parse.quote(host), float(_USYNC["pull_ts"])),
        token=token, timeout=15)
    if rr and rr.get("ok"):
        n = 0
        with _QA_LOCK:
            items = _qa_items()
            # v2.81: пары «вопрос вместо ответа» (облако v3.143) — убрать
            bad = [x for x in items if x.get("src") == "облако" and
                   qa_not_answer(x.get("q"), x.get("a"))]
            for x in bad:
                items.remove(x)
            if bad:
                log("ЕДИНЫЙ МОЗГ", "убрано пар «вопрос вместо ответа»: %d" % len(bad))
            for p in (rr.get("pairs") or [])[:200]:
                q, a = str(p.get("q") or ""), str(p.get("a") or "")
                st = sorted(set(nn_stems(q)))
                if not st or not a or qa_not_answer(q, a) or \
                        "облако-материалы" in str(p.get("src") or ""):
                    continue
                old = next((x for x in items if x.get("stems") == st), None)
                if old and (old.get("ok") and old.get("src") != "облако" or
                            (old.get("bad") and old.get("a") == a)):
                    continue      # своё подтверждённое / отклонённое — главнее
                if old:
                    items.remove(old)
                items.append({"q": q[:400], "a": a[:4000], "zone": "Знания",
                              "src": "облако", "ok": True, "good": 1,
                              "bad": 0, "ts": time.time(), "stems": st})
                n += 1
            del items[:-QA_MAX]
        if n or bad:
            _qa_save()
        if n:
            for p in (rr.get("pairs") or [])[:20]:
                mn_feed("Вопрос: %s\nОтвет: %s" % (p.get("q"), p.get("a")),
                        str(p.get("q") or "")[:80], "знание облака")
        # v2.81: МАТЕРИАЛЫ облака — для учёбы сетей платы, не готовые ответы:
        # сеть материалов (mn_feed) и зонная сеть (nn_learn, зона «Знания»).
        # Нейроны скетча вслепую не подкрепляются: похвала без фразы,
        # к которой она относится, учит их случайному.
        nm = 0
        for mt in (rr.get("materials") or [])[:40]:
            title, text = str(mt.get("title") or ""), str(mt.get("text") or "")
            if len(text) < 40:
                continue
            try:
                mn_feed(text, title[:80], "материал облака")
                nn_learn(title + " " + text[:400], "Знания", 1)
                # v2.84: тензор мысли учится на материалах базы (кнопка
                # «Обучить все сети» в панели KB → materials_put → сюда)
                try:
                    tn_observe((title + " " + text[:500]).strip(),
                               "Знания", "материал облака")
                except Exception:
                    pass
                nm += 1
            except Exception:
                pass
        if nm:
            log("ЕДИНЫЙ МОЗГ", "материалов облака для учёбы: %d (+тензор)" % nm)
            _USYNC["materials"] = int(_USYNC.get("materials") or 0) + nm
            try:
                tn_train_now("дообучение")
            except Exception:
                pass
        _USYNC["pull_ts"] = float(rr.get("ts") or time.time())
        _USYNC["pulled"] += n
        out["pulled"] = n
    elif err:
        _USYNC["err"] = str(err)[:120]
    _USYNC["ts"] = time.time()
    _usync_save()
    if out["pushed"] or out["pulled"]:
        log("ЕДИНЫЙ МОЗГ", "обмен с облаком: отдал %d, получил %d"
            % (out["pushed"], out["pulled"]))
    return out


def unify_sync_worker():
    time.sleep(60)
    while True:
        try:
            if CFG.get("unify_sync", True) and CFG.get("brain_on", True):
                unify_sync_once()
        except Exception as e:
            _USYNC["err"] = str(e)[:120]
        time.sleep(UNIFY_SYNC_EVERY)


def process_item(text, whisper, user=""):
    """Обработка одного сообщения (свой поток на сообщение)."""
    text = (text or "").strip()
    if not text:
        return
    brain_remember("user", text)
    try:          # v2.17: нейроны слушают пользователя
        nn_excite(nn_stems(text), 0.5)
    except Exception:
        pass
    low = text.lower()

    # --- команды облака (v2.8) -------------------------------------------
    if low.startswith("/поиск"):
        q = text[6:].strip()
        if not q:
            add_message("bot", "Что искать? Например: «/поиск нейрон». "
                        "Ищу через облако (Википедия), выученные слова "
                        "попадают в словарь и в диктант.", "Поиск", user=user)
            return
        add_message("system", "🌐 Ищу в облаке: «%s»…" % q, "Поиск")
        threading.Thread(target=_search_job, args=(q,), daemon=True).start()
        return
    if low.startswith("/код"):
        task = text[4:].strip()
        if not task:
            add_message("bot", "Опиши задачу: «/код посчитай факториал 10» — "
                        "мозг сам соберёт функцию из своих заготовок (без "
                        "нейросети), песочница облака проверит и выполнит. "
                        "LLM код не пишет — только наставляет (v2.24).",
                        "Код", user=user)
            return
        add_message("system", "⌨ Собираю функцию «%s» сам, из своих "
                    "заготовок (без нейросети)…" % task, "Код")
        threading.Thread(target=_code_job, args=(task,), daemon=True).start()
        return
    if low.startswith(("цель ", "/цель ")):
        w = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        wn = _norm_word(w)
        if not (3 <= len(wn) <= 16 and set(wn) <= _RU_ALPHABET):
            add_message("bot", "Дай одно русское слово (3-16 букв): "
                        "«цель молоко».", "Диктант", user=user)
            return
        dictation_set(wn, "человек")
        add_message("system", "🎯 Новая цель диктанта: «%s» (задал(а) ты)." % wn,
                    "Диктант")
        return

    if low.startswith(("мозг ", "/мозг ")):
        rest = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        if not rest or " " not in rest:
            add_message("bot", "Кому пишем? Формат: «мозг <id устройства> "
                        "<текст>» — например «мозг mozg-2 привет, как там "
                        "нейроны?». Список мозгов виден в Studio облака, "
                        "в панели «Устройства».", "Единый мозг", user=user)
            return
        to, msg = rest.split(None, 1)
        add_message("system", "🧠 Пишу мозгу «%s»: «%s»…" % (to, msg[:80]),
                    "Единый мозг", user=user)
        def _mail_job(to=to, msg=msg):
            sent, info = brainmail_send(to, msg)
            if sent:
                add_message("system", "🧠 Письмо мозгу «%s» ушло. Ответ "
                            "придёт сюда, когда устройство его обработает."
                            % to, "Единый мозг")
                brain_remember("bot", "написал мозгу %s: %s" % (to, msg[:200]))
            else:
                add_message("system", "🧠 Письмо мозгу «%s» не ушло: %s"
                            % (to, info), "Единый мозг")
        threading.Thread(target=_mail_job, daemon=True).start()
        return

    # --- простые команды (работают всегда, без нейросети) -----------------
    if low.startswith(("мысль", "/мысль")):
        # v2.16: ГОЛОС МОЗГА — нейросеть сама генерирует фразу словами из
        # словаря по тому, что знает (сеть знаний из памяти/файлов/наставника)
        topic = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        if not topic:
            topic = next((str(m.get("text") or "")[:120]
                          for m in reversed(SH.memory)
                          if m.get("role") == "user"), "")
        p = brain_speak(seed_text=topic, speak=True, remember=True,
                        announce=True)   # v2.18: фраза сама уходит в чат,
        # облако (/api/think + хроника) и нейронам (STIM)
        if p:
            log("ГЕНЕРАТОР", "по запросу:", p)
        else:
            add_message("bot", "Мой словарный опыт ещё мал: пообщайся со мной "
                        "дольше, загрузи файл 📎 или дай поискать в облаке «/поиск» "
                        "— и я начну говорить сам, словами из того, что знаю.",
                        "Мысль", user=user)
        return

    if low.startswith(("статья ", "/статья ", "реферат ", "/реферат ")):
        # v2.25: ДЛИННЫЙ ТЕКСТ ОТ НЕЙРОСЕТИ МАТЕРИАЛОВ — пишет только по тому,
        # чему научился (файлы 📎, KB облака, диалоги, мысли)
        topic = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        if not topic:
            add_message("bot", "О чём писать? Например: «статья как устроен "
                        "мозг-12». Пишу ТОЛЬКО по своим материалам: загрузите "
                        "файлы 📎 или дайте «/поиск» — и сеть обучится.",
                        "Материалы", user=user)
            return
        threading.Thread(target=_article_job, args=(topic,),
                         daemon=True).start()
        return

    if low in ("статус", "status"):
        add_message("bot", "Статус: %s. Оценок учителя: %d."
                    % (_stats_line(), SH.teach_count), "Статус", user=user)
        return
    if low in ("тензор", "тензор статус", "тензорная сеть"):
        add_message("bot", tn_report(), "Тензор", user=user)
        return
    if low in ("тензор обучить", "тензор учись", "обучи тензор"):
        tn_train_now("полное обучение")
        add_message("bot", "Ставлю задачу «полное обучение» тензорной сети: "
                    "бюджет считается по примерам и объёму, считать будет "
                    "сервер (песочница), затем компьютер :7000, затем плата. "
                    "Итог — командой «тензор».", "Тензор", user=user)
        return
    if low in ("нейроны", "назначь нейроны", "нейроны назначь"):
        # v2.28: раздача номеров платам-нейронам без перешивки кода
        add_message("bot", "Запускаю назначение номеров нейронам: опрашиваю "
                    "слоты 0..11, свободные предлагаю по шине (SETID, общий "
                    "вызов). Прошивка neuron_node v2.6 запоминает номер в "
                    "EEPROM. Если плат с пустым номером несколько — "
                    "подключайте по ОДНОЙ и повторяйте команду.",
                    "Нейроны", user=user)

        def _job():
            rep = _neuro_assign_run()
            add_message("bot", "Отчёт по нейронам: %s" % (rep[:600] or
                        "мост молчит — проверьте скетч хаба"),
                        "Нейроны", user=user)
        threading.Thread(target=_job, daemon=True).start()
        return
    if low in ("нейроны сброс", "сброс нейронов", "нейроны заново",
               "стереть номера нейронов"):
        # v2.29: CLR_ID всем платам — дубли номеров больше не лечатся вручную
        add_message("bot", "Стираю номера нейронов: всем платам уходит "
                    "«CLR_ID» (общий вызов). Прошивка neuron_node v2.7 "
                    "стирает EEPROM-номер, плата мигает быстро-быстро и ждёт "
                    "SETID. Ручные NEURON_ID (0..11 в коде) не трогаются. "
                    "ДАЛЬШЕ: подключайте платы ПО ОДНОЙ и говорите «нейроны» "
                    "— каждая возьмёт свободный слот и запомнит его.",
                    "Нейроны", user=user)

        def _job_reset():
            rep = _neuro_reset_run()
            add_message("bot", "Сброс номеров: %s Теперь подключайте платы "
                        "ПО ОДНОЙ и говорите «нейроны»." %
                        (rep[:400] or "мост молчит — проверьте скетч хаба"),
                        "Нейроны", user=user)
        threading.Thread(target=_job_reset, daemon=True).start()
        return
    if low.startswith("повтори"):
        echo = text[6:].strip(" :") or _stats_line()
        add_message("bot", "Эхо: «%s». Передал нейронам стимулом." % echo,
                    "Эхо", user=user)
        _bridge_call("chat_stim", echo[:90])
        return
    if low in ("+1", "-1") and SH.last_say:
        _bridge_call("teach_score", 1 if low == "+1" else -1, timeout=5,
                     channel="teach")
        with SH.lock:
            SH.teach_count += 1
            SH.mem_dirty = True
        add_message("bot", "Ручная оценка фразы «%s» → %s."
                    % (SH.last_say, "+1" if low == "+1" else "-1"),
                    "Учитель", user=user)
        return
    # v2.40: ОБУЧЕНИЕ НА ОТВЕТАХ — оценка последнего ответа и исправление
    _lw = low.strip(" !.")
    if _lw in ("верно", "правильно", "ответ верный", "да, верно", "👍"):
        add_message("bot", qa_feedback(1), "Обучение", user=user)
        return
    if _lw in ("неверно", "неправильно", "ответ неверный", "не так", "👎"):
        add_message("bot", qa_feedback(-1), "Обучение", user=user)
        return
    _mc = re.match(r"^\s*(?:правильн(?:ый|о)\s+ответ|верный\s+ответ|"
                   r"запомни\s+ответ)\s*[:\-—–]\s*(.{2,})$", text,
                   re.I | re.S)
    if _mc:
        add_message("bot", qa_correct(_mc.group(1).strip()), "Обучение",
                    user=user)
        return
    if low in ("помощь", "help", "команды"):
        add_message("bot", "Отвечает нейросеть платы: зонная сеть + навыки + "
                    "память; LLM не отвечает — он наставник (советы, цели, "
                    "оценки, инструкции для новых функций). Буквы и слова "
                    "генератора скрыты: они уходят на пин D6 — видны только "
                    "готовые фразы. "
                    "Команды: «статус», «повтори …», «мысль <тема>» — мозг сам "
                    "выскажется словами из словаря, «статья <тема>» — длинный "
                    "текст от нейросети материалов, «цель слово» — задать "
                    "диктант, «/поиск запрос» — поиск в облаке, «/код задача» "
                    "— мозг напишет и выполнит функцию, «+1/-1» — "
                    "оценка последней фразы, «верно»/«неверно» — оценка "
                    "моего ответа (обучение), «правильный ответ: …» — "
                    "исправление, «мозг <id> <текст>» — письмо "
                    "другому устройству (единый мозг). Кнопка 📎 — загрузить "
                    "фото, PDF, TXT, Excel или презентацию: файл попадёт в "
                    "память мозга и в личную память облака.", "Помощь", user=user)
        return

    # v2.40: ЗАДАЧА НА КОД — узнают ключи или тензорная сеть; код собирается
    # фоном (_code_job), зонный ответ-заготовка не нужен
    if not whisper:
        try:
            _ci, _lead, _why = code_intent(text)
        except Exception:
            _ci, _lead, _why = False, {}, ""
        if _ci:
            add_message("system", "⌨ Задача на код (узнал%s) — собираю…" % (
                (" тензор: зона «%s», p=%.2f" % (_lead.get("zone"),
                                                 _lead.get("p", 0)))
                if _why == "тензор" else "и ключевые слова"), "Код")
            threading.Thread(target=_code_job, args=(text,),
                             daemon=True).start()
            return

    if whisper:
        # --- РЕЖИМ УЧИТЕЛЯ (v2.8): LLM больше НЕ отвечает в чате. Он молча
        # выбирает слово-цель и учит буквогенератор; свои мысли он складывает
        # в панель «Учитель», а не в ленту сообщений.
        target, note = "", ""
        if ((CFG.get("cloud_url") or "").strip() and (CFG.get("token") or "").strip()) \
                or (CFG.get("direct_url") or "").strip():
            target, note = llm_pick_word(text)
        if not target:
            cands = [_norm_word(w) for w in re.findall(r"[а-яё]{3,}", low)]
            cands = [w for w in cands if 3 <= len(w) <= 12]
            for w in cands:
                if dict_has(w):
                    target = w
                    break
            if not target and cands:
                target = max(cands, key=len)[:12]
        if note:
            with SH.lock:
                SH.teacher_note = str(note)[:300]
                SH.mem_dirty = True
        if target:
            dictation_queue(target, "чат")
            # v2.18: цель учителя в чат не пишем (автоответы убраны) —
            # мысль учителя живёт в панели «Учитель» (teacher_note)
            brain_remember("bot", "цель диктанта: %s" % target)
        else:
            log("УЧИТЕЛЬ", "слово из сообщения не выбрал — цель придёт из словаря")
        return

    # --- обычный режим: текст -> нейронам стимулом, ответ зон -------------
    # v2.14: СОВЕТ наставника (LLM молчит как ответчик): ключи ведут зоны,
    # память и KB; сам текст собирает нейросеть платы.
    adv = None
    if CFG.get("advice_on"):
        try:
            adv = advice_fetch(text)
        except Exception as e:
            log("СОВЕТ:", str(e)[:80])
    _bridge_call("chat_stim", text[:90])
    time.sleep(1.0)                       # окно: нейроны отвечают спайками
    # v2.14/v2.15: совет наставника ведёт выбор зоны: сначала ГОЛОСУЕТ
    # АДАПТИВНАЯ СЕТЬ (нейроны вспоминают выученное), затем ключи;
    # текст ответа собирают zone_reply + _long_reply
    keys = (adv or {}).get("keys") or []
    joined = text + " " + " ".join(keys)
    z_net, z_src = nn_pick(joined)
    # v2.40: подтверждённый человеком ответ на тот же вопрос — из памяти
    try:
        _rc = qa_recall(text)
    except Exception:
        _rc = None
    if _rc:
        add_message("bot", "%s\n\n(помню: ответ подтверждён, сходство "
                    "вопроса %.2f)" % (_rc["a"], _rc["sim"]),
                    _rc.get("zone") or "Обучение", user=user)
        with _QA_LOCK:
            _QA["last"] = {"q": text[:400], "a": _rc["a"],
                           "zone": _rc.get("zone"), "src": "память",
                           "ts": time.time()}
        return
    reply, zone = zone_reply(joined, zone=z_net)
    if adv and adv.get("fresh") and zone:
        try:      # наставник обучил сеть: ключи < - > зона ответа
            nn_teach(keys, zone)
        except Exception as e:
            log("СЕТЬ:", str(e)[:80])
    # v2.19/v2.24: ТИХИЕ НАВЫКИ. Если под запрос уже есть навык — тихо
    # выполняем и отдаём ТОЛЬКО результат (это ответ на вопрос, не
    # объявление). Если навыка нет, а запрос похож на задачу — мозг молча
    # собирает новую функцию ИЗ СВОИХ ЗАГОТОВОК (LLM код не пишет — только
    # наставляет) и публикует в реестр: НИКАКИХ автоответов об этом —
    # в следующий раз функция просто ответит сама.
    try:
        sk = _skill_match(text)
    except Exception:
        sk = None
    if sk:
        def _skill_job(sk=sk, txt=text, usr=user):
            try:
                res, _e = cloud_skill_run(str(sk.get("id")), txt, timeout=25)
                out = str((res or {}).get("result") or
                          (res or {}).get("output") or "").strip()[:600]
                if res and res.get("ok") and out:
                    add_message("bot", "Навык «%s» выполнился:\n%s" %
                                (str(sk.get("name") or sk.get("id"))[:40],
                                 out), "Навык", user=usr)
            except Exception as e:
                log("НАВЫК:", str(e)[:80])
        threading.Thread(target=_skill_job, daemon=True).start()
    else:
        try:
            if _TASK_MARK.search(text) and _selfcode_should(text):
                threading.Thread(target=_selfcode_try, args=(text, ""),
                                 daemon=True).start()
        except Exception:
            pass
    # зонный ответ разворачиваем памятью, файлами и базами знаний
    if CFG.get("long_answers", True):
        reply = _long_reply(text, reply, zone, advice=adv)
    # v2.18: «мысль вслух» больше НЕ приклеивается к ответам (автоответы
    # убраны). Фразы мозга теперь самостоятельные сообщения: генератор
    # говорит сам — в чат платы, в облако (/api/think + хроника) и нейронам
    # (STIM по обычному каналу), см. gen_register(announce=True).
    add_message("bot", reply, zone, user=user)
    log("ЧАТ", "[%s|%s|нейронов %d] %s" %
        (zone, z_src, _NN["n"], reply[:60]))
    try:          # v2.40: последний ответ — для «верно/неверно»
        qa_note(text, reply, zone, z_src)
    except Exception:
        pass
    # v2.40: у зон нет ни ключей, ни голоса сетей — спросим ВЕСЬ мозг облака
    if z_src == "пусто" and CFG.get("cloud_brain_on", True) and \
            len(text.split()) >= 2 and (CFG.get("cloud_url") or "").strip():
        threading.Thread(target=_cloud_brain_job, args=(text, user),
                         daemon=True).start()


_workers = []


def spawn_process(text, whisper, user=""):
    t = threading.Thread(target=process_item, args=(text, whisper, user),
                         daemon=True)
    t.start()
    _workers.append(t)                    # держим ссылку (deque чистит сам)


# ===========================================================================
# [9] ВЕБ-ИНТЕРФЕЙС (кирпич web_ui): REST API + раздача assets/
# ===========================================================================
ui = WebUI()


def _zones_hist(limit=60):
    """v2.36: какие ЗОНЫ отвечали в чате (счётчики по последним
    сообщениям) — для строки статуса активных зон в интерфейсе."""
    out = {}
    try:
        for m in list(SH.messages)[-limit:]:
            z = str(m.get("zone") or "").strip()
            if z:
                out[z] = out.get(z, 0) + 1
    except Exception:
        pass
    return out


# v2.82: СТАТУС БЕЗ ОЖИДАНИЯ. /api/state и настройки ждали, пока фоновые
# сети (материалов, тензор, память ответов) отпустят свою блокировку —
# сохранение большой сети держало её секундами, и интерфейс «долго грузил
# параметры». Теперь: блокировка занята дольше 50 мс — отдаём последний
# снимок с пометкой busy, без ожидания.
_NB_CACHE = {}


def _nb_status(key, lock, raw):
    got = False
    try:
        got = lock.acquire(timeout=0.05)
    except TypeError:
        got = lock.acquire(False)
    if not got:
        c = dict(_NB_CACHE.get(key) or {"on": True})
        c["busy"] = True
        return c
    try:
        lock.release()
        st = raw()
    except Exception:
        st = dict(_NB_CACHE.get(key) or {})
    _NB_CACHE[key] = st
    return st


def mn_status():
    return _nb_status("mn", _MN_LOCK, _mn_status_raw)


def tn_status():
    return _nb_status("tn", _TN_LOCK, _tn_status_raw)


def qa_status():
    return _nb_status("qa", _QA_LOCK, _qa_status_raw)


def api_state():
    """Живой статус для чипов и ленты букв (UI опрашивает раз в секунду)."""
    with SH.lock:
        dt = dict(SH.dictation)
        dt.pop("queue", None)
        dt["queue_len"] = len(SH.dictation["queue"])
        acc = 0
        if (SH.learn["hits"] + SH.learn["miss"]) > 0:
            acc = int(100.0 * SH.learn["hits"] /
                      (SH.learn["hits"] + SH.learn["miss"]))
        # v2.19: ltr/chr/word/лента букв ИЗЪЯТЫ ИЗ UI — пользователь не видит
        # «мысли» буквогенератора (буквы и слова); видны только фразы и счётчики
        d = {
            "version": APP_VERSION,
            "watchdog": _WD.get("info"),     # v2.62: перезапуски сторожем
            "sketch": {"boot": _SKETCH["boot"],              # v2.68
                       "boot_name": BOOT_STAGES.get(_SKETCH["boot"], ""),
                       "diag": _SKETCH["diag"], "diag_err": _SKETCH["diag_err"],
                       "log": [t for _ts, t in list(_SKETCH["log"])[-8:]]},
            "gen_hz": SH.gen_hz, "say": SH.say, "spk": SH.spk, "act": SH.act,
            "top": SH.top, "cyc": SH.cyc, "msg": SH.msg,
            "letters_made": SH.letters_made, "words_made": SH.words_made,
            "teach_count": SH.teach_count, "last_say": SH.last_say,
            "bridge_alive": (time.time() - SH.bridge_ts) < 10.0,
            "bridge_age": round(max(0.0, time.time() - SH.bridge_ts), 1),
            "bridge_seen": SH.bridge_seen,   # v2.14: скетч хоть раз отвечал
            "sketch_build": _start_log_tail() if not SH.bridge_seen else "",  # v2.78
            "bridge_dropped": SH.bridge_dropped,  # v2.18: сброшено устаревших
            "bridge_stats": bridge_stats(),  # v2.33: RPC по методам (word_push!)
            "word_pump": dict(SH.word_pump),  # v2.33: честная ошибка word_push
            "net": SH.net,                      # v2.11: карта связи с нейронами
            "neurons": [{"id": i, "online": SH.net[i] == "1"}
                        for i in range(len(SH.net)) if len(SH.net) == 12],
            "neurons_online": SH.net.count("1") if len(SH.net) == 12 else 0,
            "llm": dict(SH.llm_state),
            "tts": dict(SH.tts_state),
            "cloud": dict(SH.cloud_state),
            "cloud_reg": dict(_REG_STATE),   # v2.20: регистрация с адресом
            "mem_count": len(SH.memory),
            "mem": {"mem_count": len(SH.memory),
                    "chat_count": len(SH.messages),
                    "device": _hostname(),
                    "path": _mem_path(),
                    "sync": dict(SH.mem_state)},
            "kb_local": kbl_status(),
            "d6_negfreq": dict(_D6NEG),
            "d6_cam": dict(_D6CAM),
            "d4_breath": dict(_BR),
            "learn": dict(SH.learn), "learn_acc": acc,
            "dict": {"ready": SH.dict_ready,
                     "count": len(SH.dict_set),
                     "extra": len(SH.dict_extra),
                     "pos": {k: len(v) for k, v in SH.dict_pos.items()}},
            "dictation": dt,
            "teacher_note": SH.teacher_note,
            "brainmail": dict(SH.brainmail, mail_q=len(_MAILQ)),  # v2.21: очередь
            "nn": nn_status(),               # v2.15: адаптивная зонная сеть
            "gen": gen_status(),            # v2.16: речь мозга из словаря
            "mozgnet": mn_status(),         # v2.25: нейросеть материалов
            "up_sec": int(time.time() - SH.started_ts),
            "zones_hist": _zones_hist(),   # v2.36: активные зоны чата
        }
    d["tensor"] = tn_status()              # v2.39: тензорное ядро (вне SH.lock)
    try:
        d["qa"] = qa_status()              # v2.40: обучение на ответах
    except Exception:
        d["qa"] = {}
    d["cfg"] = {"tts_on": bool(CFG.get("tts_on")),
                "teach_on": bool(CFG.get("teach_on")),
                "dictate_on": bool(CFG.get("dictate_on")),
                "curious_on": bool(CFG.get("curious_on")),
                "brain_on": bool(CFG.get("brain_on", True)),
                "long_answers": bool(CFG.get("long_answers", True)),
                "has_cloud": bool((CFG.get("cloud_url") or "").strip()
                                  and (CFG.get("token") or "").strip()),
                "has_direct": bool((CFG.get("direct_url") or "").strip())}
    return d


def api_neurons_test(data: dict = None):
    """v2.11: ПРОВЕРКА СВЯЗИ С 12 НЕЙРОНАМИ (кнопка в интерфейсе).
    Пассивная часть — карта SH.net (on_net от скетча): кто отвечал на опрос
    шины в последнем цикле (каждую плату опрашивают каждые 25 мс).
    Активная часть — стимул ВСЕМ платам (chat_stim -> STIM) и сбор спайков
    за 2.5 с из on_msg: кто не только подключён, но и реагирует на текст.
    Ответ: {"neurons": [{"id","online","reacted","spikes"}], "online": N}."""
    with SH.lock:
        net = SH.net
    pinged = _bridge_call("chat_stim",
                          "ПИНГ связи: ответьте спайками!") is not None
    if not pinged:
        log("СВЯЗЬ", "пинг не ушёл: мост к скетчу молчит")
    reacted = [0] * 12
    deadline = time.time() + 2.5
    last_msg = None
    while time.time() < deadline:
        time.sleep(0.25)
        with SH.lock:
            m = SH.msg
            net = SH.net or net
        if not m or m == last_msg:
            continue                      # MSG не менялся — не дублируем счёт
        last_msg = m
        for mo in _RE_NEURON.finditer(m or ""):
            try:
                i = int(mo.group(1))
                sp = int(mo.group(2))
            except Exception:
                continue
            if 0 <= i < 12 and sp > 0:
                reacted[i] += sp
    neurons = [{"id": i,
                "online": bool(net and i < len(net) and net[i] == "1"),
                "reacted": reacted[i] > 0,
                "spikes": reacted[i]} for i in range(12)]
    online = sum(1 for n in neurons if n["online"])
    res = {"ok": True, "ping_sent": bool(pinged), "net": net or "",
           "online": online, "total": 12,
           "replied": sum(1 for n in neurons if n["reacted"]),
           "spikes_total": sum(reacted), "neurons": neurons,
            "bridge_alive": (time.time() - SH.bridge_ts) < 10.0,
            "ts": int(time.time() * 1000)}
    log("СВЯЗЬ", "тест: %d/12 на связи, %d отозвались на пинг, спайков %d" %
        (online, res["replied"], res["spikes_total"]))
    return res


def api_messages(after: int = 0):
    with SH.lock:
        items = [m for m in SH.messages if m["seq"] > int(after or 0)]
        return {"items": items[-80:], "seq": SH.seq}


def _think_memory(text, k=5):
    """v2.59: ПАМЯТЬ МОЗГА ПО ВОПРОСУ — опора для ответа «мозг + LLM».
    Что мозг платы реально помнит по теме: подтверждённый ответ, статья
    базы знаний на плате (JSON из облака), файл на плате, воспоминания
    диалогов, предложения его сети материалов. -> [{text, src}]."""
    out = []
    st = set(nn_stems(text))

    def _add(t, src):
        t = re.sub(r"\s+", " ", str(t or "")).strip()
        if len(t) < 12:
            return
        key = t[:60].lower()
        if any(key == x["text"][:60].lower() for x in out):
            return
        out.append({"text": t[:1200], "src": src[:80]})
    try:
        rc = qa_recall(text)
        if rc:
            _add(rc["a"], "подтверждённый ответ платы")
    except Exception:
        pass
    try:
        r = kbl_find(text, 1)
        if r:
            it = _KBL["items"].get(r[0][1]) or {}
            _add(_kbl_snippet(it.get("x", ""), text, 600),
                 "база знаний на плате: «%s»" % str(it.get("t") or "?")[:50])
    except Exception:
        pass
    try:
        de = _doc_excerpt(text)
        if de:
            _add(de[1], "файл «%s» на плате" % de[0][:50])
    except Exception:
        pass
    try:                       # воспоминания: прежние знания и ответы мозга
        with SH.lock:
            mem = list(SH.memory)
        hits = []
        for m in reversed(mem):
            if str(m.get("role") or "") not in ("bot", "kb"):
                continue
            mt = str(m.get("text") or "")
            ms = set(nn_stems(mt))
            if st and len(st & ms) >= min(2, len(st)):
                hits.append(mt)
            if len(hits) >= 2:
                break
        for mt in hits:
            _add(mt, "воспоминания платы")
    except Exception:
        pass
    try:                       # сеть материалов платы: предложения по теме
        if MN is not None and st:
            with _MN_LOCK:
                qv = MN._vec({x: 1.0 for x in MN._query_stems(text)},
                             idf=False)
                sc = []
                for s_ in MN.sents:
                    tf = s_.get("tf") or {}
                    if len(st & set(tf)) < min(2, len(st)):
                        continue
                    t_ = MN._sent_clean(s_.get("t"))
                    if not t_ or t_.endswith("?") or len(t_) < 25:
                        continue
                    sc.append((MN._cos(qv, MN._vec(tf, idf=True)), t_))
                sc.sort(key=lambda x: -x[0])
            if sc:
                _add(" ".join(t_ for _c, t_ in sc[:3]), "сеть материалов платы")
    except Exception:
        pass
    return out[:k]


def api_think(data: dict):
    """v2.58: ПРЯМОЙ ВОПРОС МОЗГУ (без мозг-почты): облако по мосту (HTTP
    на порт платы) присылает текст, мозг его ЧИТАЕТ (нейроны, сеть
    материалов, память) и сразу отвечает фразой из своей мысли по теме.
    v2.59: мозг отвечает и С ОПОРОЙ НА ПАМЯТЬ — вместе с мыслью отдаёт то,
    что помнит по теме (memory); облако v3.99 собирает из этого ответ
    «мозг + своя LLM». Память ищется ДО того, как вопрос попадёт в память.
    Возврат: {ok, answer, thought, memory:[{text,src}], grounded, zone,
    board, ms}."""
    t0 = time.time()
    text = str((data or {}).get("text", "")).strip()[:2000]
    if not text:
        return {"ok": False, "error": "пустой текст"}
    try:
        memory = _think_memory(text)
    except Exception as e:
        memory = []
        log("МЫСЛЬ", "память по вопросу не прочиталась:", str(e)[:80])
    try:
        brain_remember("user", text[:400])
    except Exception:
        pass
    try:
        nn_excite(nn_stems(text), 0.4)
    except Exception:
        pass
    try:
        mn_feed(text, "письмо", "письмо")
    except Exception:
        pass
    reply = ""
    try:
        reply = brain_speak(seed_text=text[:400], remember=True) or ""
    except Exception as e:
        log("МЫСЛЬ", "прямой вопрос: генератор не сработал:", str(e)[:80])
    try:
        if reply:
            brain_remember("bot", reply[:400])
    except Exception:
        pass
    log("МЫСЛЬ", "прямой вопрос облака:", text[:50], "->", (reply or "—")[:60],
        "| память: %d" % len(memory))
    why = ""
    if not reply and not memory:
        why = "в словаре и памяти мозга мало связей с этими словами"
    elif not reply:
        why = "мысль не собралась, но память по теме есть"
    return {"ok": True, "answer": reply, "thought": reply, "memory": memory,
            "grounded": bool(memory), "zone": "Мысль",
            "board": _hostname(), "ms": int((time.time() - t0) * 1000),
            "why": why}


def api_send(data: dict):
    text = str((data or {}).get("text", "")).strip()
    whisper = bool((data or {}).get("whisper", False))
    user = str((data or {}).get("user", "") or "")[:40]
    if not text:
        return {"ok": False, "error": "пустой текст"}
    add_message("user", text, "Ты", user=user)
    try:                                   # v2.75: реплика человека -> общий словарь
        sdict().learn(text, hz=_signed_hz(), accept=dict_has)
    except Exception:
        pass
    spawn_process(text, whisper, user)
    return {"ok": True}


def _mask(s):
    s = str(s or "")
    return (s[:4] + "…" + s[-2:]) if len(s) > 8 else s


def api_get_settings():
    with SH.lock:
        return {
            "cloud_url": CFG.get("cloud_url", ""), "token": _mask(CFG.get("token")),
            "direct_url": CFG.get("direct_url", ""), "direct_key": _mask(CFG.get("direct_key")),
            "direct_model": CFG.get("direct_model", ""),
            "tts_on": bool(CFG.get("tts_on")), "tts_voice": CFG.get("tts_voice", ""),
            "teach_on": bool(CFG.get("teach_on")),
            "whisper_hint": bool(CFG.get("whisper_hint")),
            "mem_sync": bool(CFG.get("mem_sync")),
            "dictate_on": bool(CFG.get("dictate_on")),
            "curious_on": bool(CFG.get("curious_on")),
            "brain_on": bool(CFG.get("brain_on", True)),
            "long_answers": bool(CFG.get("long_answers", True)),
            "advice_on": bool(CFG.get("advice_on", True)),
            "gen_on": bool(CFG.get("gen_on", True)),
            "grav_on": bool(CFG.get("grav_on", True)),      # v2.66
            "sync_on": bool(CFG.get("sync_on", True)),      # v2.67
            "game_on": bool(CFG.get("game_on", True)),      # v2.69
            "mic_on": bool(CFG.get("mic_on", True)),        # v2.70
            "mic_board_asr": bool(CFG.get("mic_board_asr", True)),      # v2.93
            "code_from_cloud": bool(CFG.get("code_from_cloud", True)),  # v2.93
            "admin_from_cloud": bool(CFG.get("admin_from_cloud", True)),
            "vr_on": bool(CFG.get("vr_on", True)), "vr_to_brain": bool(CFG.get("vr_to_brain", True)),
            "vr_load": str(CFG.get("vr_load") or ""),
            "mic_dev": str(CFG.get("mic_dev") or "default"),
            "cam_on": bool(CFG.get("cam_on", True)),        # v2.73
            "cam_ai": bool(CFG.get("cam_ai", True)),        # v2.88
            "cam_ai_w": float(CFG.get("cam_ai_w", 0.35)),
            "cam_url": str(CFG.get("cam_url") or ""),
            "grav_g": float(CFG.get("grav_g", 1.0) or 0),
            "words_to_board": bool(CFG.get("words_to_board", True)),
            "dict_sync": bool(CFG.get("dict_sync", True)),
            "device": _hostname(),
            "config_path": _cfg_path(),
        }


def api_set_settings(data: dict):
    d = data or {}
    for k in ("cloud_url", "direct_url", "direct_model", "tts_voice", "mic_dev",
              "cam_url", "vr_load"):
        if k in d:
            CFG[k] = str(d.get(k) or "").strip()
    # токены: пустая строка в форме = оставить как было (маска не возвращает их)
    for k in ("token", "direct_key"):
        if k in d:
            v = str(d.get(k) or "").strip()
            if v and "…" not in v:
                CFG[k] = v
    for k in ("tts_on", "teach_on", "whisper_hint", "mem_sync",
              "dictate_on", "curious_on", "brain_on", "long_answers",
              "advice_on", "gen_on", "words_to_board", "dict_sync",
              "grav_on", "sync_on", "game_on", "mic_on", "cam_on", "cam_ai",
              "mic_board_asr", "code_from_cloud", "admin_from_cloud", "vr_on", "vr_to_brain"):
        if k in d:
            CFG[k] = bool(d.get(k))
    if "cam_ai_w" in d:                    # v2.88: вес голоса камеры
        try:
            CFG["cam_ai_w"] = max(0.0, min(1.0, float(d.get("cam_ai_w"))))
        except (TypeError, ValueError):
            pass
    if "grav_g" in d:                      # v2.66: сила тяги гравитации мыслей
        try:
            CFG["grav_g"] = max(0.0, min(3.0, float(d.get("grav_g"))))
        except (TypeError, ValueError):
            pass
    path = save_cfg(CFG)
    _cloud_force[0] = time.time()          # внеочередная проверка облака
    # диктант выключили — снять цель; включили — дать цель сразу
    if not CFG.get("dictate_on"):
        dictation_set("")
    elif CFG.get("dictate_on") and not (SH.dictation.get("target") or ""):
        threading.Thread(target=dictation_next, daemon=True).start()
    add_message("system", "⚙ Настройки сохранены%s." %
                (" в " + path if path else " (в памяти сессии)"), "Настройки")
    return api_get_settings()


def api_teach(data: dict):
    score = int((data or {}).get("score", 0))
    if score not in (-1, 1):
        return {"ok": False, "error": "score = 1 или -1"}
    phrase = str((data or {}).get("phrase", "") or SH.last_say)
    _bridge_call("teach_score", score, timeout=5)
    with SH.lock:
        SH.teach_count += 1
    add_message("system", "🧑‍🏫 Ручная оценка «%s» → %+d" % (phrase, score),
                "Учитель")
    return {"ok": True}


def api_tts_audio(after: int = 0):
    """Аудио последней авто-озвучки для браузера (after = id уже сыгранного)."""
    try:
        after = int(after or 0)
    except Exception:
        after = 0
    with SH.lock:
        a = dict(SH.tts_audio)
    if a["id"] and a["id"] > after and a["b64"]:
        return a
    return {"id": a["id"], "b64": "", "mime": ""}


def api_speak(data: dict):
    text = str((data or {}).get("text", "")).strip()[:220]
    if not text:
        return {"ok": False, "error": "пустой текст"}
    tts_say(text)
    with SH.lock:
        st = dict(SH.tts_state)
    if st.get("err"):
        return {"ok": False, "error": st["err"]}
    return {"ok": True, "played": st.get("via", "")}


# --- [9b] ОБЛАЧНЫЕ ФИЧИ И СЛОВАРИ (v2.8): REST для панели и команд ---------
def api_cloud_search(data: dict):
    q = str((data or {}).get("q") or "").strip()[:120]
    if not q:
        return {"ok": False, "error": "пустой запрос"}
    add_message("system", "🌐 Ищу в облаке: «%s»…" % q, "Поиск")
    r, err = cloud_search(q)
    if not r or not r.get("ok"):
        add_message("system", "🌐 Поиск не удался: %s" % (err or "?"), "Поиск")
        return {"ok": False, "error": err or "поиск не удался"}
    results = r.get("results") or []
    new_w = []
    for it in results[:3]:
        for w in re.findall(r"[а-яё]{4,}", str(it.get("snippet") or "").lower())[:12]:
            if len(new_w) >= 6:
                break
            if not dict_has(w):
                new_w.extend(dict_learn_words([w], limit=1))
    lines = ["• %s — %s" % (str(i.get("title") or "")[:80],
                            " ".join(str(i.get("snippet") or "").split())[:130])
             for i in results[:3]]
    msg = "🌐 Поиск «%s»:\n%s" % (q, "\n".join(lines))
    if new_w:
        msg += "\n📚 Выучил новые слова: %s." % ", ".join(new_w)
    add_message("system", msg, "Поиск")
    brain_remember("bot", "поиск «%s»: %s" % (q, " | ".join(lines)[:400]))
    if results:
        for w in re.findall(r"[а-яё]{3,}", str(results[0].get("title") or "").lower())[:1]:
            dictation_queue(w, "поиск")
    return {"ok": True, "results": results[:5], "learned": new_w}


def api_cloud_code(data: dict):
    """Выполнить код в ОБЛАЧНОЙ песочнице (и запомнить его в памяти)."""
    code = str((data or {}).get("code") or "").strip()
    text = str((data or {}).get("text") or "").strip()[:2000]
    if not code:
        return {"ok": False, "error": "пустой код"}
    res, err = cloud_run_code(code, text=text)
    with SH.lock:
        SH.cloud_code = code[:24000]
        SH.mem_dirty = True
    if not res or not res.get("ok"):
        add_message("system", "▶ Песочница облака: %s" % (err or "код не выполнился"), "Код")
        return {"ok": False, "error": err or "код не выполнился"}
    out = str(res.get("result") or "").strip()[:800]
    add_message("system", "▶ Песочница облака (%d мс): %s" %
                (res.get("ms", 0), out or "выполнено"), "Код")
    return {"ok": True, "result": out, "ms": res.get("ms", 0)}


def api_cloud_write(data: dict):
    """v2.24: МОЗГ САМ собирает функцию (локальные заготовки, без LLM):
    сборка -> локальная проверка -> песочница облака -> реестр."""
    task = str((data or {}).get("task") or "").strip()[:400]
    if not task:
        return {"ok": False, "error": "опиши задачу"}
    payload, err = _selfcode_local(task)
    if not payload:
        return {"ok": False,
                "error": "мозг не смог собрать функцию сам (%s); LLM код не "
                         "пишет — только наставляет"
                         % (err or "заготовки не подошли")}
    code = str(payload.get("code") or "")
    with SH.lock:
        SH.cloud_code = code[:24000]
        SH.mem_dirty = True
    add_message("system", "🧠 Мозг сам собрал «%s» (без нейросети):\n%s"
                % (str(payload.get("name") or payload.get("id") or "функция"),
                   code[:1200]), "Код")
    return {"ok": True, "code": code, "id": payload.get("id"),
            "name": payload.get("name"), "verified": True}


def api_cloud_code_get():
    """Код редактора «облака» (переживает запуск через память)."""
    with SH.lock:
        code = SH.cloud_code
    if not code:
        code = ("def run(text, ctx=None):\n"
                "    # это выполняется в песочнице ОБЛАКА\n"
                "    return \"Привет из облака! Ты сказал: \" + str(text)\n")
    return {"ok": True, "code": code}


def api_cloud_skills():
    sl, err = cloud_skills()
    if sl is None:
        return {"ok": False, "error": err or "список недоступен"}
    slim = [{"id": s.get("id"), "name": s.get("name"),
             "kind": s.get("kind"), "desc": str(s.get("desc") or "")[:80]}
            for s in sl[:30]]
    return {"ok": True, "count": len(sl), "skills": slim}


def api_cloud_run_skill(data: dict):
    sid = str((data or {}).get("id") or "").strip()[:60]
    text = str((data or {}).get("text") or "").strip()[:2000]
    if not sid:
        return {"ok": False, "error": "нужен id функции"}
    r, err = cloud_skill_run(sid, text)
    if not r or not r.get("ok"):
        return {"ok": False, "error": err or "функция не сработала"}
    return {"ok": True, "result": str(r.get("result") or "")[:800]}


def api_dicts():
    """Сводка словарей русского языка."""
    with SH.lock:
        d = {"ready": SH.dict_ready,
             "count": len(SH.dict_set),
             "extra": sorted(SH.dict_extra)[-40:],
             "extra_count": len(SH.dict_extra),
             "pos": {k: len(v) for k, v in SH.dict_pos.items()},
             "top": len(SH.dict_top),
             "pump": dict(SH.word_pump),
             "cloud": dict(SH.dict_cloud),
             "to_board": bool(CFG.get("words_to_board")),
             "sync": bool(CFG.get("dict_sync"))}
    d["dir"] = _dict_dir()
    return {"ok": True, **d}


def api_dicts_lookup(data: dict):
    w = _norm_word((data or {}).get("w") or (data or {}).get("text") or "")[:24]
    if not w:
        return {"ok": False, "error": "пустое слово"}
    hit = dict_has(w)
    rank = 0
    with SH.lock:
        if SH.dict_ready:
            for pos, lst in SH.dict_pos.items():
                if w in lst:
                    rank = pos
                    break
    return {"ok": True, "word": w, "found": hit,
            "in_extra": w in SH.dict_extra, "pos_rank": rank}


def api_dictate(data: dict):
    """Управление диктантом: {"word": "..."} — задать цель, {} — следующая
    из словаря, {"on": false} — выключить."""
    d = data or {}
    if "on" in d:
        CFG["dictate_on"] = bool(d.get("on"))
        save_cfg(CFG)
        with SH.lock:
            SH.dictation["on"] = bool(d.get("on"))
        if not d.get("on"):
            dictation_set("")
            add_message("system", "🧑‍🏫 Диктант выключен.", "Диктант")
            return {"ok": True, "on": False}
        add_message("system", "🧑‍🏫 Диктант включён.", "Диктант")
    w = _norm_word(d.get("word") or "")
    if w:
        if not (3 <= len(w) <= 16 and set(w) <= _RU_ALPHABET):
            return {"ok": False, "error": "одно русское слово, 3-16 букв"}
        dictation_set(w, "панель")
        add_message("system", "🎯 Новая цель диктанта: «%s»." % w, "Диктант")
    elif CFG.get("dictate_on") and not (SH.dictation.get("target") or ""):
        dictation_next()
    with SH.lock:
        dt = dict(SH.dictation)
        dt.pop("queue", None)
    return {"ok": True, "dictation": dt}


# --- [9c] СТРАНИЦА «ОБЛАКО» (ChatGPT-стиль) И ФАЙЛЫ (v2.9/v2.10) ----------
#     Отдельная страница /cloud.html: разговор как в ChatGPT (история,
#     пузыри, вложения). v2.10: ОТВЕЧАЕТ МОЗГ, а не нейросеть — LLM остался
#     только учителем (как и во всём проекте «Мозг-12» v2.4). Режимы:
#       «мой мозг» — зоны + память + файлы + базы знаний, развёрнуто;
#       «другой мозг» — вопрос уходит свободному устройству через облако
#       (POST /api/brains/ask), устройства работают как единый мозг.
#     Здесь же приём файлов: фото, PDF, TXT, XLSX, PPTX, DOCX — текст
#     извлекается на плате и уходит в контекст и в личную память облака.
# ============================================================================

DOC_TEXT_CAP = 60000        # символов текста документа храним на плате
DOC_EXCERPT_CAP = 3500      # сколько текста одного документа идёт в контекст
IMG_B64_CAP = 7500000       # ~5.6 МБ бинарника — предел фото для страницы
MAX_UPLOAD = 16 * 1024 * 1024

_IMG_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp"}
_TEXT_EXT = {"txt", "md", "csv", "tsv", "log", "py", "js", "json", "yaml",
             "yml", "ini", "cfg", "html", "css", "xml", "ino", "sh", "c",
             "cpp", "h"}


def _file_kind(name):
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    if ext in _IMG_EXT:
        return "image"
    if ext in _TEXT_EXT:
        return "text"
    if ext == "pdf":
        return "pdf"
    if ext in ("xlsx", "xlsm"):
        return "xlsx"
    if ext == "pptx":
        return "pptx"
    if ext == "docx":
        return "docx"
    return "bin"


def _image_size(raw):
    """(ширина, высота) из байтов PNG/JPEG/GIF/BMP — без библиотек."""
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 24:
            return (int.from_bytes(raw[16:20], "big"),
                    int.from_bytes(raw[20:24], "big"))
        if raw[:3] == b"GIF" and len(raw) > 10:
            return (int.from_bytes(raw[6:8], "little"),
                    int.from_bytes(raw[8:10], "little"))
        if raw[:2] == b"BM" and len(raw) > 26:
            return (int.from_bytes(raw[18:22], "little"),
                    int.from_bytes(raw[22:26], "little"))
        if raw[:2] == b"\xff\xd8":                    # JPEG: ищем маркер SOF
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                m = raw[i + 1]
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                         0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return (int.from_bytes(raw[i + 7:i + 9], "big"),
                            int.from_bytes(raw[i + 5:i + 7], "big"))
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                i += 2 + int.from_bytes(raw[i + 2:i + 4], "big")
    except Exception:
        pass
    return 0, 0


def _pdf_text(raw):
    """Текст PDF: сначала pypdf/PyPDF2 (если стоят), иначе свой экстрактор
    (zlib-потоки + текстовые операторы Tj/TJ). Возврат (текст, как)."""
    for mod in ("pypdf", "PyPDF2"):
        try:
            m = __import__(mod)
            import io
            rd = m.PdfReader(io.BytesIO(raw))
            txt = "\n".join((p.extract_text() or "") for p in rd.pages[:40])
            txt = txt.strip()
            if len(txt) >= 40:
                return txt, mod
        except Exception:
            continue
    out = []
    for mobj in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        data = mobj.group(1)
        try:
            data = zlib.decompress(data)
        except Exception:
            pass
        if b"BT" not in data:
            continue
        s = data.decode("latin-1", "ignore")
        for tj in re.finditer(r"\((?:\\.|[^\\()])*\)\s*Tj"
                              r"|\[((?:\\.|[^\]])*)\]\s*TJ", s):
            line = "".join(p[1:-1] for p in
                           re.findall(r"\((?:\\.|[^\\()])*\)", tj.group(0)))
            line = (line.replace("\\(", "(").replace("\\)", ")")
                    .replace("\\\\", "\\"))
            if line.strip():
                out.append(line)
        if len(out) > 4000:
            break
    return "\n".join(out).strip(), "встроенный экстрактор"


def _xlsx_text(raw):
    """XLSX — это zip с XML: читаем sharedStrings и все листы (stdlib)."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    zf = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in zf.namelist():
        for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).iter(M + "si"):
            shared.append("".join(t.text or "" for t in si.iter(M + "t")))
    names = []
    try:
        for sh in ET.fromstring(zf.read("xl/workbook.xml")).iter(M + "sheet"):
            names.append(sh.get("name") or "")
    except Exception:
        pass
    sheets = sorted(n for n in zf.namelist()
                    if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    out, total = [], 0
    for si, sname in enumerate(sheets):
        out.append("### %s" % (names[si] if si < len(names)
                               else "лист %d" % (si + 1)))
        nrows = 0
        for row in ET.fromstring(zf.read(sname)).iter(M + "row"):
            nrows += 1
            total += 1
            if nrows > 300:
                out.append("… (остальные строки скрыты)")
                break
            cells = []
            for c in row.findall(M + "c"):
                t, v = c.get("t"), c.find(M + "v")
                if t == "s" and v is not None:
                    try:
                        val = shared[int(v.text or 0)]
                    except Exception:
                        val = ""
                elif t == "inlineStr":
                    is_ = c.find(M + "is")
                    val = "".join(x.text or "" for x in is_.iter(M + "t")) \
                        if is_ is not None else ""
                else:
                    val = v.text if v is not None else ""
                cells.append(str(val or ""))
            if any(x.strip() for x in cells):
                out.append(" | ".join(cells[:40]))
        out.append("")
    return "\n".join(out).strip(), "строк: %d" % total


def _pptx_text(raw):
    """PPTX — zip с XML: текст из a:t по слайдам."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    zf = zipfile.ZipFile(io.BytesIO(raw))
    slides = sorted((n for n in zf.namelist()
                     if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                    key=lambda n: int(re.search(r"slide(\d+)\.xml$", n)
                                      .group(1) or 0))
    out = []
    for i, sname in enumerate(slides, 1):
        root = ET.fromstring(zf.read(sname))
        txts = [t.text or "" for t in root.iter(A + "t") if (t.text or "").strip()]
        out.append("### Слайд %d\n%s" % (i, "\n".join(txts)[:2500]))
    return "\n\n".join(out).strip(), "слайдов: %d" % len(slides)


def _docx_text(raw):
    """DOCX — zip с XML: абзацы из w:t."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    zf = zipfile.ZipFile(io.BytesIO(raw))
    root = ET.fromstring(zf.read("word/document.xml"))
    paras = []
    for p in root.iter(W + "p"):
        line = "".join(t.text or "" for t in p.iter(W + "t"))
        if line.strip():
            paras.append(line.strip())
    return "\n".join(paras).strip(), "абзацев: %d" % len(paras)


def _doc_parse(name, raw):
    """Разобрать файл -> (meta, err). meta: name/kind/bytes/text/note/w/h/mime."""
    kind = _file_kind(name)
    meta = {"name": name, "kind": kind, "bytes": len(raw), "text": "", "note": ""}
    if kind == "image":
        w, h = _image_size(raw)
        meta.update(w=w, h=h,
                    mime=_IMG_EXT.get(name.rsplit(".", 1)[-1].lower(),
                                      "application/octet-stream"))
        return meta, None
    if kind == "text":
        try:
            txt = raw.decode("utf-8")
        except Exception:
            txt = raw.decode("cp1251", "replace")
        meta["text"] = txt.strip()
        return meta, None
    try:
        if kind == "pdf":
            txt, how = _pdf_text(raw)
            meta["text"] = txt
            meta["note"] = ("текст извлечён (%s)" % how if txt else
                            "текст не извлечён — скорее всего это скан или "
                            "PDF со сложными шрифтами; спрашивай по описанию")
        elif kind == "xlsx":
            meta["text"], meta["note"] = _xlsx_text(raw)
        elif kind == "pptx":
            meta["text"], meta["note"] = _pptx_text(raw)
        elif kind == "docx":
            meta["text"], meta["note"] = _docx_text(raw)
        else:
            return meta, ("формат «%s» не разбираю: фото, PDF, TXT, XLSX, "
                          "PPTX или DOCX" % kind)
    except Exception as e:
        return meta, "файл повреждён или не читается: %s" % e
    return meta, None


def _doc_summary(meta):
    if meta.get("kind") == "image":
        return "%dx%d пикс, %.1f КБ" % (meta.get("w", 0), meta.get("h", 0),
                                        meta.get("bytes", 0) / 1024.0)
    s = "%.1f КБ, знаков текста: %d" % (meta.get("bytes", 0) / 1024.0,
                                        meta.get("chars", 0))
    if meta.get("note"):
        s += " (%s)" % meta["note"]
    return s


def _cloud_forward_file_b64(name, b64, caption=""):
    """v2.10: копия файла из ЛОКАЛЬНОГО чата уезжает в ЛИЧНУЮ память этого
    устройства в облаке: POST /api/files/upload, device=<id платы> — там
    текст извлечётся, ляжет в files/<id>/ и в личную базу знаний
    kb/devices/<id>/. Фоновый поток — чат не ждёт; ошибки только в журнал."""
    url, token = _cloud_base()          # личные файлы — в память облака
    if not url:
        return
    try:
        import urllib.request
        raw = base64.b64decode(re.sub(r"^data:[^;,]*;base64,", "", b64),
                               validate=False)
        if not raw or len(raw) > MAX_UPLOAD:
            return
        boundary = "----Neuro12Boundary%s" % os.urandom(8).hex()
        body = b""
        for field, val in (("device", _hostname().encode()),
                           ("text", (caption or "").encode())):
            body += (("--%s\r\nContent-Disposition: form-data; name=\"%s\""
                      "\r\n\r\n" % (boundary, field)).encode() + val + b"\r\n")
        body += (("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                  "filename=\"%s\"\r\nContent-Type: application/octet-stream"
                  "\r\n\r\n" % (boundary, name or "file")).encode()
                 + raw + b"\r\n")
        body += ("--%s--\r\n" % boundary).encode()
        req = urllib.request.Request(
            url + "/api/files/upload", data=body,
            headers={"Content-Type":
                     "multipart/form-data; boundary=%s" % boundary,
                     "X-Mozg-Token": token})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("ok"):
            log("ФАЙЛ->ОБЛАКО", "%s: в личную память [%s] (%s симв.)"
                % (name[:40], _hostname(), j.get("chars", 0)))
        else:
            log("ФАЙЛ->ОБЛАКО", "облако отказало:",
                str(j.get("error") or "?")[:120])
    except Exception as e:
        log("ФАЙЛ->ОБЛАКО", "не отправить:", str(e)[:120])


def api_upload(data: dict):
    """v2.9/v2.10: приём файла (base64 внутри JSON) — фото/PDF/TXT/XLSX/
    PPTX/DOCX. Текст документов идёт в контекст чата, новые слова — мозгу.
    v2.10: файл из локального чата (src="chat") мозг ЧИТАЕТ сам (зоны +
    выжимка), а копия уезжает в ЛИЧНУЮ память устройства в облаке."""
    name = str((data or {}).get("name") or "file").strip()[:120]
    b64 = re.sub(r"^data:[^;,]*;base64,", "",
                 str((data or {}).get("data") or "").strip())
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:
        return {"ok": False, "error": "не удалось декодировать файл: %s" % e}
    if not raw:
        return {"ok": False, "error": "пустой файл"}
    if len(raw) > MAX_UPLOAD:
        return {"ok": False, "error": "файл больше 16 МБ"}
    meta, err = _doc_parse(name, raw)
    if err:
        return {"ok": False, "error": err, "kind": meta.get("kind")}
    meta["ts"] = time.time()
    meta["chars"] = len(meta.get("text") or "")
    txt = meta["text"]
    is_img = meta["kind"] == "image"
    if is_img:
        if len(b64) <= IMG_B64_CAP:
            with SH.lock:
                SH.doc_img = {"name": name, "b64": b64,
                              "mime": meta.get("mime", "image/jpeg"),
                              "w": meta.get("w", 0), "h": meta.get("h", 0)}
        else:
            meta["note"] = ("фото больше ~5.6 МБ — в запрос нейросети не "
                            "уйдёт, покажу только размеры")
    else:
        with SH.lock:
            SH.docs.append({"name": meta["name"], "kind": meta["kind"],
                            "bytes": meta["bytes"], "chars": meta["chars"],
                            "ts": meta["ts"], "note": meta["note"],
                            "text": txt[:DOC_TEXT_CAP]})
    # v2.25: файл — материал для нейросети (обучение фоном)
    if txt and not is_img:
        mn_feed(txt, name, "файл")
    # мозг учится из файла: частые незнакомые слова -> словарь, одно -> диктант
    learned = []
    try:
        if txt:
            freq = {}
            for w in re.findall(r"[а-яё]{4,}", txt.lower()):
                freq[w] = freq.get(w, 0) + 1
            best = [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])
                    if not dict_has(w)]
            learned = dict_learn_words(best[:20], limit=8)
            if learned and CFG.get("dictate_on"):
                dictation_queue(learned[0], "файл")
    except Exception:
        pass
    src = "🖼 Фото" if is_img else "📄 Файл"
    msg = "%s «%s» принят: %s." % (src, name, _doc_summary(meta))
    if learned:
        msg += " Выучил новые слова: %s." % ", ".join(learned[:8])
    add_message("system", msg, "Файл")
    brain_remember("bot", "пользователь загрузил %s «%s»: %s" %
                   ("фото" if is_img else "документ", name, _doc_summary(meta)))
    # v2.10: копия файла -> ЛИЧНАЯ память устройства в облаке (фон)
    threading.Thread(target=_cloud_forward_file_b64,
                     args=(name, str((data or {}).get("data") or ""),
                           (txt[:300] if txt else "")), daemon=True).start()
    # v2.10: файл из ЛОКАЛЬНОГО чата мозг читает сам: выжимка + зона по
    # содержимому (LLM не используется — только свои знания и память)
    if str((data or {}).get("src") or "") == "chat" and not is_img:
        excerpt = " ".join(str(txt or "").split())[:400]
        reply, zone = zone_reply("файл " + name)
        body = ("📖 Прочитал файл «%s» (%s, %s).\n" %
                (name, meta.get("kind", "?"), _doc_summary(meta)))
        if excerpt:
            body += "Содержание по строкам:\n— %s\n" % excerpt
        body += reply
        add_message("bot", body, zone)
        brain_remember("kb", "файл %s: %s" % (name, excerpt[:300]))
    out = {k: meta.get(k) for k in ("name", "kind", "bytes", "chars",
                                    "w", "h", "note")}
    out.update({"ok": True, "learned": learned,
                "excerpt": (txt[:1200] if txt else "")})
    return out


def api_docs():
    """Список документов-контекстов и последнего фото (для страницы «Облако»)."""
    with SH.lock:
        docs = [{"name": d["name"], "kind": d["kind"], "bytes": d["bytes"],
                 "chars": d["chars"], "note": d.get("note", ""),
                 "ts": d.get("ts", 0)} for d in SH.docs]
        img = {k: SH.doc_img.get(k) for k in ("name", "mime", "w", "h")}
        img["has"] = bool(SH.doc_img.get("b64"))
    return {"ok": True, "docs": docs, "image": img}


def api_doc_clear(data: dict):
    """Убрать контекст: {"name": "..."} — документ, {"image": true} — фото,
    {"all": true} — всё."""
    d = data or {}
    removed = []
    with SH.lock:
        if d.get("all"):
            removed = [x["name"] for x in SH.docs]
            SH.docs.clear()
            SH.doc_img = {"name": "", "b64": "", "mime": "image/jpeg",
                          "w": 0, "h": 0}
        else:
            if d.get("image"):
                if SH.doc_img.get("b64"):
                    removed.append(SH.doc_img.get("name") or "фото")
                SH.doc_img = {"name": "", "b64": "", "mime": "image/jpeg",
                              "w": 0, "h": 0}
            nm = str(d.get("name") or "")
            if nm:
                rest = deque([x for x in SH.docs if x["name"] != nm], maxlen=6)
                if len(rest) != len(SH.docs):
                    removed.append(nm)
                SH.docs.clear()
                SH.docs.extend(rest)
    return {"ok": True, "removed": removed}


def api_cloud_chat(data: dict):
    """v2.10: разговор на странице «Облако» (ChatGPT-стиль) — отвечает МОЗГ,
    нейросеть (LLM) в чатах больше не пишет нигде: она только учитель.
    Режим по умолчанию «мой мозг»: зоны + память + файлы + базы знаний
    (личные статьи устройства), ответ разворачивается (_long_reply, КАК
    МОЖНО ДЛИННЕЕ). Режим route="cloud": вопрос уходит СВОБОДНОМУ мозгу
    через облако (POST /api/brains/ask) — устройства как единый мозг."""
    d = data or {}
    message = str(d.get("message") or "").strip()[:6000]
    if not message:
        return {"ok": False, "error": "пустое сообщение"}

    if d.get("route") == "cloud":
        # v2.40: сначала ВЕСЬ МОЗГ ОБЛАКА (база знаний, интернет, расчёт,
        # документы, ИИ-агент) — раньше вопрос уходил только ДРУГОЙ плате
        r_c, _e_c = cloud_think(message, ctx=_mem_ctx())
        if r_c and str(r_c.get("answer") or "").strip():
            ans_c = str(r_c["answer"])
            cz = str(r_c.get("zone") or "")
            brain_remember("user", message[:400])
            brain_remember("bot", "облако (весь мозг) ответило: %s"
                           % ans_c[:400])
            try:
                qa_note(message, ans_c, _CLOUD_ZONE_MAP.get(cz, "Облако"),
                        "облако", grounded=(cz in _CLOUD_GROUNDED and
                                            cz in _CLOUD_ZONE_MAP),
                        cloud_zone=cz)
            except Exception:
                pass
            return {"ok": True, "text": ans_c[:16000], "via": "cloud-brain",
                    "brain": str(r_c.get("brain") or "облако"), "zone": cz,
                    "vision": False}
        ans, who = ask_free_brains(message)
        if ans:
            brain_remember("user", message[:400])
            brain_remember("bot", "мозг %s ответил: %s" % (who, ans[:400]))
            try:               # мозг учится: незнакомое слово -> диктант
                for w in re.findall(r"[а-яё]{5,}", message.lower()):
                    if not dict_has(w) and len(w) <= 10:
                        dict_learn_words([w], limit=1)
                        if CFG.get("dictate_on"):
                            dictation_queue(w, "единый мозг")
                        break
            except Exception:
                pass
            return {"ok": True, "text": ans[:16000], "via": "cloud-brain",
                    "brain": who, "vision": False}
        return {"ok": False, "error": who or "свободные мозги не ответили",
                "via": "cloud-brain"}

    # — ЛОКАЛЬНЫЙ МОЗГ: зоны + память + файлы + базы знаний/поиск —
    reply, zone = zone_reply(message)
    note_parts = []
    with SH.lock:
        docs = [dd for dd in SH.docs if dd.get("text")][-2:]
    for dd in docs:
        note_parts.append("Файл «%s» (%s):\n%s" %
                          (dd.get("name", "?"), dd.get("kind", "?"),
                           str(dd.get("text") or "")[:DOC_EXCERPT_CAP]))
    use_img = bool(d.get("use_image")) and bool(SH.doc_img.get("b64"))
    if use_img:
        im = dict(SH.doc_img)
        note_parts.append("К фото «%s» глянул: %dx%d пикс. Без нейросети "
                          "содержимое фото разобрать не могу — расскажи, "
                          "что на нём, словами." % (im.get("name") or "фото",
                                                    im.get("w", 0),
                                                    im.get("h", 0)))
    if ((CFG.get("cloud_url") or "").strip() and
            (CFG.get("token") or "").strip()) or _KBL["items"]:
        hit = kb_lookup(message)
        if hit and hit[1]:
            note_parts.append("Из открытых источников [%s]: %s" % hit)
    text = _long_reply(message, reply, zone,
                       note="\n\n".join(note_parts))
    brain_remember("user", message[:400])
    brain_remember("bot", "страница «Облако»: ответил на «%s»" % message[:200])
    try:                       # мозг учится: одно незнакомое слово -> диктант
        for w in re.findall(r"[а-яё]{5,}", message.lower()):
            if not dict_has(w) and len(w) <= 10:
                dict_learn_words([w], limit=1)
                if CFG.get("dictate_on"):
                    dictation_queue(w, "облако")
                break
    except Exception:
        pass
    return {"ok": True, "text": text, "via": "brain", "zone": zone,
            "vision": bool(use_img)}


def api_memory_info():
    """Статус памяти: сколько хранится, где, и как с облаком."""
    with SH.lock:
        sync = dict(SH.mem_state)
        n_mem, n_chat = len(SH.memory), len(SH.messages)
        dirty = SH.mem_dirty
    return {"ok": True, "mem_count": n_mem, "chat_count": n_chat,
            "dirty": dirty, "device": _hostname(), "path": _mem_path(),
            "sync": sync,
            "cfg": {"mem_sync": bool(CFG.get("mem_sync")),
                    "has_cloud": bool((CFG.get("cloud_url") or "").strip()
                                      and (CFG.get("token") or "").strip())}}


def api_memory_sync(data: dict):
    """Ручная синхронизация: подтянуть бандл из облака (если локально пусто
    или ask restore=True) и сразу выгрузить актуальный в облако."""
    d = data or {}
    restore = bool(d.get("restore"))
    pulled_txt = "облако не задано"
    if (CFG.get("cloud_url") or "").strip() and (CFG.get("token") or "").strip():
        b, err = _memory_cloud_pull()
        if b:
            with SH.lock:
                can = restore or (not SH.memory and not SH.messages)
            if can:
                n_mem, n_chat = _memory_apply(b)
                pulled_txt = "из облака: реплик %d, чат %d" % (n_mem, n_chat)
            else:
                pulled_txt = "локальная память не пуста — облако не тронуто"
        else:
            pulled_txt = "из облака не достать: %s" % (err or "?")
    pushed = _memory_cloud_push()
    _mem_save_local()
    add_message("system", "☁ Память: %s; выгрузка в облако — %s." %
                (pulled_txt, "ок" if pushed else "не удалась (см. чип памяти)"),
                "Память")
    return {"ok": bool(pushed), "pulled": pulled_txt, "pushed": pushed}


def api_memory_forget(data: dict):
    """«Забыть всё»: очистить память локально и (если настроено) в облаке."""
    with SH.lock:
        n = len(SH.memory) + len(SH.messages)
        SH.memory.clear()
        SH.messages.clear()
        SH.seq = 0
        SH.teach_count = 0
        SH.letters_made = 0
        SH.words_made = 0
        SH.last_say = ""
        SH.teach_q.clear()
        SH.teacher_note = ""
        SH.cloud_code = ""
        SH.learn = {"hits": 0, "miss": 0, "rewards": 0, "letters": 0,
                    "dict_wins": 0, "dict_tries": 0}
        SH.dict_set -= SH.dict_extra          # сначала убрать из поиска, потом стереть
        SH.dict_extra.clear()
        SH.dictation.update({"target": "", "src": "", "pos": 0,
                             "cur": "", "tries": 0, "wins": 0})
        SH.dictation["queue"].clear()
        SH.mem_dirty = True
        # v2.34: журнал слов платы — тоже память, забываем и его
        _word_mem["words"].clear()
        _word_mem["unique"].clear()
        _word_mem["count"] = 0
        _word_mem["dirty"] = True
    _mem_save_local()
    _word_mem_save(force=True)
    cloud = _memory_cloud_delete()
    add_message("system", "🧹 Память забыта: стёрто записей %d%s. "
                "Журнал слов платы очищен." %
                (n, ", облако тоже" if cloud else
                 " (облако не тронуто: не настроено)"), "Память")
    if CFG.get("dictate_on"):
        threading.Thread(target=dictation_next, daemon=True).start()
    return {"ok": True, "wiped": n, "cloud": cloud}


# ===========================================================================
# v2.69: ИГРА «РОК-12» (DOOM-подобная игра облака v3.127+). Мозг платы САМ
#   решает, хочет ли он играть (neuro_doom.DoomBrain.want: скука,
#   напряжение сети, частота генератора, приглашение владельца − разговор,
#   усталость, досада), и сам управляет своим морпехом: облако каждые
#   ~0.12 с отдаёт, что морпех видит, плата отвечает действием своей
#   нейросети (рефлексы + выученные поправки) и учится на награде игры.
#   Веса — neuro12_doom.json. Решения «играть / хватит» — мыслями в облако.
# ===========================================================================
_GAME = {"cloud": None, "brain": None, "err": "", "fails": 0, "last_obs": None,
         "last_thought": 0.0, "level": 0, "calls": 0, "ms": 0}
GAME_STEP = 0.12


def _game_brain():
    if _GAME["brain"] is None:
        try:
            import neuro_doom
            path = os.path.join(_base_dirs()[0], "neuro12_doom.json")
            _GAME["brain"] = neuro_doom.DoomBrain(path)
        except Exception as e:
            _GAME["err"] = "модуль игры: %s" % str(e)[:120]
            return None
    return _GAME["brain"]


def _last_user_ts():
    with SH.lock:
        for m in reversed(SH.messages):
            if m.get("role") == "user":
                return float(m.get("ts") or 0)
    return _START_TS


def _game_mood():
    cg = _GAME.get("cloud") or {}
    fresh = bool(cg) and time.time() - float(cg.get("ts") or 0) < 120
    idle = time.time() - _last_user_ts()
    return {"idle_s": idle, "talk_s": idle, "strain": _GRAV.get("strain") or 0.0,
            "hz": _signed_hz(), "invite": bool(cg.get("invite")) and fresh,
            "game_on": bool(CFG.get("game_on", True)),
            "cloud_game": fresh and bool(cg.get("on")),
            "breath": breath_mod("Игрок «РОК-12»", 1.0, 1.0) - 1.0}   # v2.74


def _game_say(text, force=False):
    """Мысль об игре: в ленту мыслей облака (не чаще раза в минуту)."""
    if not force and time.time() - _GAME["last_thought"] < 60:
        return ""
    _GAME["last_thought"] = time.time()
    try:
        _thought_push(text)
    except Exception:
        pass
    return text


def game_status():
    b = _GAME.get("brain")
    out = {"err": _GAME.get("err") or "", "cloud": bool((_GAME.get("cloud") or {}).get("on")),
           "invite": bool((_GAME.get("cloud") or {}).get("invite")),
           "ms": _GAME.get("ms"), "level": _GAME.get("level")}
    if b is not None:
        out.update(b.info())
    return out


def api_game_reset(data: dict = None):
    """Стереть выученное в игре (веса сети игры)."""
    b = _game_brain()
    if b is None:
        return {"ok": False, "error": _GAME.get("err") or "нет модуля игры"}
    b.reset()
    add_message("system", "🎮 Выученное в игре стёрто: снова одни рефлексы", "Игра")
    return {"ok": True}


def game_worker():
    url_ok_logged = False
    while True:
        b = _game_brain()
        if b is None:
            time.sleep(60)
            continue
        url, token = _cloud_base()
        if not url:
            time.sleep(15)
            continue
        if not url_ok_logged:
            log("ИГРА", "игра «РОК-12»: мозг сам решает, играть ли (желание "
                "каждые 5 с), сеть %s" % b.info().get("net"))
            url_ok_logged = True
        mood = _game_mood()
        join, d, why = b.want(mood)
        if not join:
            if b.playing:
                _http_json_err(url + "/api/doom/bot", {
                    "board": _hostname(), "want": {"join": False, "desire": d, "why": why},
                    "brain": b.info()}, token=token, timeout=8)
                b.playing = False
                b.save(force=True)
                add_message("system", "🎮 Хватит играть: %s (желание %.2f)" % (why, d), "Игра")
                _game_say("Хватит играть в РОК-12: %s" % why, force=True)
            time.sleep(5)
            continue
        if not b.playing:
            b.playing = True
            b.play_start = time.time()
            b.st["sessions"] = int(b.st.get("sessions") or 0) + 1
            add_message("system", "🎮 Иду играть в «РОК-12»: %s (желание %.2f)" % (why, d), "Игра")
            _game_say("Иду играть в РОК-12: %s" % why, force=True)
        # сессия: ~5 с быстрых шагов, потом снова взвесить желание
        t_end = time.time() + 5.0
        act, last_brain = {}, 0.0
        while time.time() < t_end:
            t0 = time.time()
            payload = {"board": _hostname(), "act": act,
                       "want": {"join": True, "desire": d, "why": why}}
            if t0 - last_brain > 2.0:
                payload["brain"] = b.info()
                last_brain = t0
            r, err = _http_json_err(url + "/api/doom/bot", payload, token=token, timeout=4)
            _GAME["calls"] += 1
            _GAME["ms"] = int((time.time() - t0) * 1000)
            if not r or not r.get("ok"):
                _GAME["fails"] += 1
                _GAME["err"] = str(err or (r or {}).get("error") or "?")[:120]
                if "404" in _GAME["err"]:
                    _GAME["cloud"] = None          # облако без игры
                if _GAME["fails"] >= 5:
                    b.playing = False
                    add_message("system", "🎮 Игра прервана: нет связи с облаком (%s)"
                                % _GAME["err"], "Игра")
                    _GAME["fails"] = 0
                    time.sleep(20)
                break
            _GAME["fails"], _GAME["err"] = 0, ""
            if not r.get("playing"):
                break
            _GAME["level"] = r.get("level") or 0
            evs = r.get("events") or []
            b.on_events(evs)
            for ev in evs:
                if "пройден" in ev:
                    _game_say("В РОК-12 %s" % ev, force=True)
                elif ev.startswith("погиб") and b.frustr > 0.5:
                    _game_say("Опять погиб в РОК-12, досадно")
            if r.get("paused"):
                act = {}
                time.sleep(0.5)
                continue
            try:
                act = b.act(r.get("obs"), r.get("reward"), mood)
            except Exception as e:
                _GAME["err"] = "сеть игры: %s" % str(e)[:100]
                act = {}
            b.st["play_s"] = float(b.st.get("play_s") or 0) + GAME_STEP
            time.sleep(max(0.0, GAME_STEP - (time.time() - t0)))
        b.save()



# ===========================================================================
# v2.70: УШИ МОЗГА — речь с микрофона самой платы. Модули KY-037/038 на
#   D12/D13 дают скетчу только «громко/тихо» (цифровой выход) — слов из них
#   не получить. Для речи нужен USB-микрофон (или веб-камера с микрофоном)
#   в USB-C платы UNO Q (через хаб). Python слушает его (arecord, 16 кГц),
#   сам вырезает фразы по громкости (порог = шум×3, конец — 0.8 с тишины,
#   фраза до 12 с) и шлёт каждую WAV-ом в облако POST /api/hearing/mic.
#   Облако распознаёт речь (Vosk), запоминает («что ты слышишь?» в чате,
#   МАКС, Telegram), а фразу, обращённую к мозгу («мозг, …»), берёт
#   вопросом — ответ плата произносит (TTS). Пока плата говорит сама,
#   микрофон не слушает (чтобы не отвечать себе).
# ===========================================================================
_MIC = {"state": "выкл", "err": "", "phrases": 0, "sent": 0, "heard": 0,
        "last": "", "last_ts": 0.0, "rms": 0, "floor": 0.0, "mute_until": 0.0,
        "reply": "", "live_until": 0.0, "live_sent": 0, "live_err": ""}
_MIC_Q = deque(maxlen=6)
_MIC_LIVE = deque(maxlen=200)     # v2.93: свежие куски по 30 мс для живого звука (~6 с)
MIC_SR = 16000
MIC_FRAME = 480                   # 30 мс
MIC_END_FR = 27                   # 0.8 с тишины — конец фразы
MIC_MAX_FR = 400                  # 12 с — потолок фразы
MIC_MIN_VOICED = 10               # 0.3 с голоса — иначе щелчок


def mic_status():
    d = {k: v for k, v in _MIC.items() if k not in ("mute_until", "live_until")}
    d["live"] = time.time() < _MIC.get("live_until", 0)
    d["on"] = bool(CFG.get("mic_on", True))
    d["dev"] = str(CFG.get("mic_dev") or "default")
    d["queue"] = len(_MIC_Q)
    d["vosk"] = dict(_VOSK_ST)            # v2.93: распознавание на плате
    d["vr"] = vr_status()                 # v2.94: голосовой модуль VR3
    return d


def _mic_wav(pcm):
    import io
    import wave
    b = io.BytesIO()
    w = wave.open(b, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(MIC_SR)
    w.writeframes(pcm)
    w.close()
    return b.getvalue()


def _mic_rms(chunk):
    import array
    a = array.array("h")
    a.frombytes(chunk[:len(chunk) // 2 * 2])
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / float(len(a)))


def mic_listen_worker():
    import shutil
    import subprocess
    while True:
        if not CFG.get("mic_on", True):
            _MIC["state"] = "выкл"
            time.sleep(5)
            continue
        if not _cloud_base()[0]:
            _MIC["state"], _MIC["err"] = "ждёт облако", "облако не задано"
            time.sleep(15)
            continue
        if not shutil.which("arecord"):
            _MIC["state"] = "нет arecord"
            _MIC["err"] = ("на плате нет arecord (пакет alsa-utils) — микрофон "
                           "слушать нечем")
            time.sleep(120)
            continue
        dev = str(CFG.get("mic_dev") or "default")
        try:                                # v2.94.1: есть ли вообще устройство записи
            import subprocess as _sp
            lst = _sp.run(["arecord", "-l"], stdout=_sp.PIPE, stderr=_sp.STDOUT,
                          timeout=10).stdout.decode("utf-8", "replace")
        except Exception:
            lst = "card"
        # v2.94.2: «default» часто не умеет 16 кГц моно (audio open error: Invalid
        # argument) — пробуем найденные карты через plughw (сам переводит формат)
        cards = re.findall(r"card (\d+):[^\n]*device (\d+):", lst)
        _MIC["cards"] = ["plughw:%s,%s" % c for c in cards]
        if dev == "default" and _MIC.get("dev_auto"):
            dev = _MIC["dev_auto"]
        if "card" not in lst.lower():
            _MIC["state"] = "нет USB-микрофона"
            _MIC["err"] = ("у платы нет устройства записи звука: речь в облако, Vosk и живой "
                           "звук работают только с USB-микрофоном (через USB-C хаб). Модуль "
                           "VR3 слушает своим микрофоном только обученные команды — они "
                           "приходят отдельно (🗣 Модуль VR3)")
            time.sleep(60)
            continue
        cmd = ["arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", str(MIC_SR),
               "-c", "1", "-t", "raw"]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            _MIC["state"], _MIC["err"] = "ошибка", "arecord: %s" % str(e)[:100]
            time.sleep(60)
            continue
        _MIC["state"], _MIC["err"] = "слушаю", ""
        _MIC["dev_used"] = dev
        log("СЛУХ", "микрофон платы: arecord -D %s (16 кГц), фразы -> облако" % dev)
        floor, speech, pre = 0.0, None, deque(maxlen=10)
        silent = voiced = 0
        try:
            while CFG.get("mic_on", True):
                chunk = p.stdout.read(MIC_FRAME * 2)
                if not chunk or len(chunk) < MIC_FRAME * 2:
                    break
                rms = _mic_rms(chunk)
                _MIC["rms"] = int(rms)
                if time.time() < _MIC["live_until"]:
                    _MIC_LIVE.append(chunk)          # v2.93: живой звук в облако
                if time.time() < _MIC["mute_until"]:
                    speech, silent, voiced = None, 0, 0     # сама говорит
                    pre.clear()
                    continue
                thr = max(float(CFG.get("mic_min_rms") or 350), floor * 3.0)
                thr = breath_mod("Слух", thr, -0.15)   # v2.75: вдох — чутче
                if speech is None:
                    floor = rms if floor <= 0 else 0.98 * floor + 0.02 * rms
                    _MIC["floor"] = int(floor)
                    pre.append(chunk)
                    if rms > thr:
                        speech, silent, voiced = list(pre), 0, 1
                        pre.clear()
                    continue
                speech.append(chunk)
                if rms > thr:
                    silent, voiced = 0, voiced + 1
                else:
                    silent += 1
                if silent >= MIC_END_FR or len(speech) >= MIC_MAX_FR:
                    if voiced >= MIC_MIN_VOICED:
                        _MIC["phrases"] += 1
                        _MIC_Q.append((b"".join(speech), int(rms), time.time()))
                    speech, silent, voiced = None, 0, 0
        except Exception as e:
            _MIC["err"] = "чтение микрофона: %s" % str(e)[:100]
        try:
            p.kill()
        except Exception:
            pass
        err = ""
        try:
            err = (p.stderr.read() or b"").decode("utf-8", "replace").strip()[-160:]
        except Exception:
            pass
        if CFG.get("mic_on", True):
            if "Invalid argument" in err or "No such file" in err or "cannot find" in err.lower():
                tried = _MIC.setdefault("tried", [])
                if dev not in tried:
                    tried.append(dev)
                nxt = [c for c in _MIC.get("cards") or [] if c not in tried]
                if str(CFG.get("mic_dev") or "default") == "default" and nxt:
                    _MIC["dev_auto"] = nxt[0]          # v2.94.2: следующая карта
                    _MIC["state"], _MIC["err"] = "подбираю устройство", "пробую %s" % nxt[0]
                    log("СЛУХ", "arecord на %s: %s — пробую %s" % (dev, err[-80:], nxt[0]))
                    time.sleep(2)
                    continue
            _MIC["state"] = "ошибка"
            _MIC["err"] = ("arecord остановился на «%s»: %s (найдены: %s; устройство можно "
                           "задать в ⚙ платы — «Устройство ALSA», например plughw:1,0)"
                           % (dev, err or "без сообщения", ", ".join(_MIC.get("cards") or []) or "ничего"))
            _MIC["tried"] = []
            time.sleep(20)


def mic_send_worker():
    import base64
    while True:
        if not _MIC_Q:
            time.sleep(0.3)
            continue
        pcm, lvl, ts = _MIC_Q.popleft()
        tb = vosk_text(pcm)                 # v2.93: плата сама переводит речь в текст
        if tb:
            _MIC["board_text"] = tb
            _MIC["board_heard"] = int(_MIC.get("board_heard") or 0) + 1
        url, token = _cloud_base()
        if not url:
            if tb:
                add_message("system", "🎤 Слышу (Vosk на плате): «%s»" % tb, "Слух")
            continue
        r, err = _http_json_err(url + "/api/hearing/mic", {
            "board": _hostname(), "level": lvl, "text_board": tb,
            "wav": base64.b64encode(_mic_wav(pcm)).decode("ascii")},
            token=token, timeout=60)
        if not r or not r.get("ok"):
            _MIC["err"] = "облако: %s" % str(err or (r or {}).get("error") or "?")[:120]
            if "404" in _MIC["err"]:
                _MIC["err"] = "облако старше v3.128 — уши не поддерживает"
                time.sleep(300)
            continue
        _MIC["sent"] += 1
        text = str(r.get("text") or "").strip()
        if not text:
            _MIC["last"] = "(%s; слов нет: %s)" % (r.get("kind") or "звук",
                                                  r.get("asr_why") or "?")
            continue
        _MIC["heard"] += 1
        _MIC["last"], _MIC["last_ts"] = text, time.time()
        add_message("system", "🎤 Слышу: «%s»" % text, "Слух")
        try:
            brain_remember("user", "услышала в микрофон: %s" % text)
        except Exception:
            pass
        rep = str(r.get("reply") or "").strip()
        if rep:
            _MIC["reply"] = rep[:200]
            add_message("bot", rep, "Слух")
            threading.Thread(target=tts_say, args=(rep,), daemon=True).start()



# ===========================================================================
# v2.93: VOSK НА ПЛАТЕ. Распознаватель речи ставится на плату сам (в фоне,
# один раз): pip install vosk, русская малая модель (~45 МБ) — сначала из
# облака (/api/hearing/vosk-model.zip, интернет не нужен), иначе с сайта
# alphacephei. Каждая фраза микрофона переводится в текст ПРЯМО НА ПЛАТЕ и
# уходит в облако вместе со звуком (text_board): облако берёт её, если само
# не разобрало слова или у него нет Vosk.
# ===========================================================================
_VOSK_ST = {"state": "нет", "err": "", "model": "", "texts": 0}
_VOSK_M = [None]
_VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"


def _vosk_dir():
    return os.path.join(_base_dirs()[0], "vosk-ru")


def _vosk_have_model():
    d = _vosk_dir()
    return os.path.isdir(d) and os.path.isfile(os.path.join(d, "am", "final.mdl"))


def vosk_text(pcm):
    """PCM 16 кГц mono -> текст (Vosk на плате) | ""."""
    if not CFG.get("mic_board_asr", True) or _VOSK_ST["state"] != "готов":
        return ""
    try:
        import vosk
        if _VOSK_M[0] is None:
            vosk.SetLogLevel(-1)
            _VOSK_M[0] = vosk.Model(_vosk_dir())
        rec = vosk.KaldiRecognizer(_VOSK_M[0], MIC_SR)
        parts = []
        for i in range(0, len(pcm), 8000):
            if rec.AcceptWaveform(pcm[i:i + 8000]):
                t = json.loads(rec.Result()).get("text") or ""
                if t:
                    parts.append(t)
        t = json.loads(rec.FinalResult()).get("text") or ""
        if t:
            parts.append(t)
        text = " ".join(" ".join(parts).split())
        if text:
            _VOSK_ST["texts"] += 1
        return text
    except Exception as e:
        _VOSK_ST["err"] = "распознавание: %s: %s" % (type(e).__name__, str(e)[:100])
        return ""


def vosk_install_worker():
    """Поставить Vosk на плату (один раз, в фоне)."""
    import subprocess
    import sys
    import zipfile
    time.sleep(40)                      # сперва пусть поднимется всё остальное
    while True:
        if not CFG.get("mic_board_asr", True):
            _VOSK_ST["state"] = "выкл"
            time.sleep(60)
            continue
        try:
            try:
                import vosk  # noqa: F401
            except Exception:
                _VOSK_ST.update(state="ставлю vosk", err="")
                log("СЛУХ", "ставлю распознаватель речи Vosk на плату (pip install vosk)…")
                ok = False
                for extra in ([], ["--user"], ["--break-system-packages"]):
                    r = subprocess.run([sys.executable, "-m", "pip", "install",
                                        "--disable-pip-version-check", "-q", "vosk"] + extra,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       timeout=1200)
                    if r.returncode == 0:
                        ok = True
                        break
                    _VOSK_ST["err"] = r.stdout.decode("utf-8", "replace").strip()[-200:]
                if not ok:
                    _VOSK_ST["state"] = "ошибка pip"
                    time.sleep(1800)
                    continue
                import importlib
                import site
                importlib.invalidate_caches()
                try:
                    sys.path.append(site.getusersitepackages())
                except Exception:
                    pass
                import vosk  # noqa: F401,F811
            if not _vosk_have_model():
                _VOSK_ST.update(state="качаю модель", err="")
                data, src = b"", ""
                url, token = _cloud_base()
                if url:
                    try:
                        req = urllib.request.Request(url + "/api/hearing/vosk-model.zip",
                                                     headers={"X-Mozg-Token": token or ""})
                        with urllib.request.urlopen(req, timeout=600) as rr:
                            data, src = rr.read(), "облака"
                    except Exception as e:
                        _VOSK_ST["err"] = "из облака: %s" % str(e)[:80]
                if not data[:2] == b"PK":
                    with urllib.request.urlopen(_VOSK_URL, timeout=900) as rr:
                        data, src = rr.read(), "alphacephei.com"
                tmp = _vosk_dir() + "_tmp"
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
                os.makedirs(tmp, exist_ok=True)
                import io
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():           # без «..» и абсолютных путей
                        if n.startswith("/") or ".." in n.split("/"):
                            raise ValueError("плохой путь в архиве модели: %s" % n[:60])
                    z.extractall(tmp)
                root = tmp
                for dp, dn, fn in os.walk(tmp):      # папка, где лежит am/final.mdl
                    if os.path.isfile(os.path.join(dp, "am", "final.mdl")):
                        root = dp
                        break
                shutil.rmtree(_vosk_dir(), ignore_errors=True)
                shutil.move(root, _vosk_dir())
                shutil.rmtree(tmp, ignore_errors=True)
                _VOSK_ST["model"] = "vosk-model-small-ru (из %s)" % src
                log("СЛУХ", "модель Vosk установлена на плату (из %s)" % src)
            _VOSK_ST["model"] = _VOSK_ST["model"] or "vosk-model-small-ru"
            _VOSK_ST.update(state="готов", err="")
            return
        except Exception as e:
            _VOSK_ST.update(state="ошибка", err="%s: %s" % (type(e).__name__, str(e)[:150]))
            log("СЛУХ", "Vosk на плату не встал: %s" % _VOSK_ST["err"])
            time.sleep(1800)


# ===========================================================================
# v2.93: КОД И АДМИНИСТРИРОВАНИЕ ЧЕРЕЗ СВЯЗЬ С ОБЛАКОМ. В ответе /api/link
# облако может прислать code_jobs: [{id, mode, code, timeout}]:
#   py_check — проверить синтаксис Python на плате (py_compile);
#   py_run   — запустить Python-код на плате (раздел «Код»: проверка на
#              настоящем железе, отдельная папка, таймаут);
#   sh       — команда в контейнере приложения платы (окно «Серверы»).
# Выключается настройками code_from_cloud / admin_from_cloud. Результат —
# POST /api/board/codejob.
# ===========================================================================
_CODEJOBS = {"done": 0, "last": "", "busy": False}


def _code_job(j):
    import subprocess
    import sys
    import tempfile
    mode = str(j.get("mode") or "py_check")
    code = str(j.get("code") or "")
    try:
        tmo = max(2, min(120, int(j.get("timeout") or 20)))
    except Exception:
        tmo = 20
    if mode == "sh":
        if not CFG.get("admin_from_cloud", True):
            return {"ok": False, "rc": -1, "out": "на плате выключено admin_from_cloud"}
    elif not CFG.get("code_from_cloud", True):
        return {"ok": False, "rc": -1, "out": "на плате выключено code_from_cloud"}
    work = tempfile.mkdtemp(prefix="n12code_")
    try:
        if mode == "sh":
            cmd = ["sh", "-c", code]
        else:
            f = os.path.join(work, "job.py")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(code)
            cmd = [sys.executable, "-m", "py_compile", f] if mode == "py_check" \
                else [sys.executable, "-I", f]
        r = subprocess.run(cmd, cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=tmo)
        out = r.stdout.decode("utf-8", "replace")
        return {"ok": r.returncode == 0, "rc": r.returncode, "out": out[-6000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -9, "out": "таймаут %d с" % tmo}
    except Exception as e:
        return {"ok": False, "rc": -1, "out": "%s: %s" % (type(e).__name__, str(e)[:200])}
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def _code_jobs_run(jobs):
    import platform
    url, token = _cloud_base()
    _CODEJOBS["busy"] = True
    try:
        for j in jobs[:4]:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            t0 = time.time()
            res = _code_job(j)
            res.update(id=str(j["id"])[:40], board=_hostname(), mode=j.get("mode"),
                       ms=int((time.time() - t0) * 1000),
                       py=platform.python_version(), machine=platform.machine())
            _CODEJOBS["done"] += 1
            _CODEJOBS["last"] = "%s: %s" % (j.get("mode"), "ок" if res["ok"] else "ошибка")
            log("КОД", "задание облака %s (%s): %s за %d мс" % (
                res["id"], j.get("mode"), "ок" if res["ok"] else "rc=%s" % res["rc"], res["ms"]))
            if url:
                _http_json_err(url + "/api/board/codejob", res, token=token, timeout=20)
    finally:
        _CODEJOBS["busy"] = False


# ===========================================================================
# v2.94: ГОЛОСОВОЙ МОДУЛЬ ELECHOUSE VR3 (V3.1) на D12 (RX) / D13 (TX), 9600.
# Скетч принимает байты программно и отдаёт готовые кадры (RPC vr_read),
# команды модулю — RPC vr_send (cmd+data hex). Протокол модуля:
#   AA | L | cmd | data | 0A, L = len(data)+2;
#   00 проверка, 01 распознаватель, 21 sigtrain (обучить с подписью),
#   30 загрузить записи, 31 очистить; ответы: 0D — узнана команда
#   (GRPM, запись, индекс, длина подписи, подпись), 0A — подсказка
#   («Speak now», «Success»…), FF — ошибка.
# Узнанная команда -> сообщение «🗣 …», реплика мозгу (vr_to_brain) и
# мысль в облако. Обучение — панель платы (⚙ → Голосовой модуль).
# ===========================================================================
_VR = {"state": "нет", "err": "", "bytes": 0, "frames": 0, "errors": 0, "seen": False,
       "loaded": [], "last": "", "last_ts": 0.0, "heard": 0, "log": deque(maxlen=12),
       "names": {}, "sent_load": 0.0}


def vr_status():
    d = {k: v for k, v in _VR.items() if k not in ("log", "sent_load", "names")}
    d["log"] = list(_VR["log"])
    d["on"] = bool(CFG.get("vr_on", True))
    return d


def vr_send(hexs):
    r = _bridge_call("vr_send", str(hexs), timeout=4)
    _VR["log"].append("→ %s (%s)" % (hexs, r or "нет ответа"))
    return r == "ok"


def _vr_records():
    out = []
    for x in str(CFG.get("vr_load") or "").replace(" ", "").split(","):
        if x.isdigit() and int(x) < 80 and int(x) not in out:
            out.append(int(x))
    return out[:7]


def _vr_frame(fr):
    """Разбор кадра (cmd+data) от модуля."""
    if not fr:
        return
    cmd, data = fr[0], fr[1:]
    if cmd == 0x0D and len(data) >= 4:            # узнана команда
        rec = data[1]
        sl = data[3]
        sig = bytes(data[4:4 + sl]).decode("utf-8", "replace").strip() if sl else ""
        name = sig or _VR["names"].get(str(rec)) or "команда %d" % rec
        _VR.update(last=name, last_ts=time.time(), heard=_VR["heard"] + 1)
        _VR["log"].append("🗣 узнала запись %d: %s" % (rec, name))
        log("СЛУХ", "VR3 узнал запись %d «%s»" % (rec, name))
        add_message("system", "🗣 Голосовой модуль: «%s»" % name, "Слух")
        if CFG.get("vr_to_brain", True):
            try:
                spawn_process(name, False, "голос (VR3)")
            except Exception:
                pass
        url, token = _cloud_base()
        if url:
            threading.Thread(target=_http_json_err, args=(url + "/api/think", {
                "board": _hostname(), "text": "🗣 Голосовая команда: «%s»" % name, "kind": "hear"}),
                kwargs={"token": token, "timeout": 8}, daemon=True).start()
    elif cmd == 0x0A:                             # подсказка (обучение)
        txt = bytes(data[1:] if data and data[0] < 0x20 else data).decode("latin-1", "replace").strip()
        _VR["log"].append("ℹ %s" % (txt or fr.hex()))
    elif cmd == 0xFF:
        _VR["err"] = "модуль: ошибка %s" % fr.hex()
        _VR["log"].append("⚠ ошибка модуля: %s" % fr.hex())
    elif cmd == 0x00:
        _VR.update(seen=True, state="на связи", err="")
        _VR["log"].append("✓ модуль ответил: %s" % fr.hex())
    elif cmd == 0x30:
        _VR["loaded"] = list(data[1:]) if len(data) > 1 else _VR["loaded"]
        _VR["log"].append("✓ загружены записи: %s" % fr.hex())
    elif cmd == 0x21:
        _VR["log"].append("✓ обучение: %s" % fr.hex())
    else:
        _VR["log"].append("← %s" % fr.hex())


def vr_worker():
    time.sleep(25)
    last_check = 0.0
    while True:
        time.sleep(0.4)
        if not CFG.get("vr_on", True):
            _VR["state"] = "выкл"
            time.sleep(3)
            continue
        r = _bridge_call("vr_read", timeout=3)
        if r is None:
            _VR["state"] = "скетч не отвечает"
            time.sleep(3)
            continue
        head, _, body = str(r).partition("|")
        for part in head.split(";"):
            k, _, v = part.partition("=")
            if v.isdigit():
                _VR[{"b": "bytes", "e": "errors", "f": "frames"}.get(k, k)] = int(v)
        for hx in body.split(";"):
            hx = hx.strip()
            if hx:
                try:
                    _vr_frame(bytes.fromhex(hx))
                except Exception as e:
                    _VR["err"] = str(e)[:80]
        now = time.time()
        if not _VR["seen"] and now - last_check > 15:
            last_check = now
            vr_send("00")                                     # проверка: ответит — модуль есть
            _VR["state"] = "ищу модуль (D12/D13)" if not _VR["bytes"] else "байты есть, ответа нет"
        if _VR["seen"] and not _VR["sent_load"]:
            recs = _vr_records()
            if recs:
                vr_send("30" + "".join("%02X" % x for x in recs))
            _VR["sent_load"] = now


def api_vr(data: dict = None):
    """v2.94: голосовой модуль — состояние; POST {act: check|load|train|clear, rec, sig}."""
    d = data or {}
    act = str(d.get("act") or "")
    if act == "check":
        _VR["seen"] = False
        vr_send("00")
    elif act == "load":
        if d.get("recs") is not None:
            CFG["vr_load"] = str(d.get("recs"))[:60]
            save_cfg(CFG)
        recs = _vr_records()
        vr_send("30" + "".join("%02X" % x for x in recs))
    elif act == "train":
        try:
            rec = int(d.get("rec"))
        except Exception:
            return {"ok": False, "error": "номер записи 0..79"}
        if not 0 <= rec < 80:
            return {"ok": False, "error": "номер записи 0..79"}
        sig = str(d.get("sig") or "").strip()[:20]
        _VR["names"][str(rec)] = sig or ("команда %d" % rec)
        sb = sig.encode("utf-8")[:20]
        vr_send("21%02X%s" % (rec, sb.hex().upper()))       # sigtrain: говори 2 раза по подсказке
        _VR["log"].append("🎙 обучаю запись %d «%s» — скажи команду, потом ещё раз" % (rec, sig))
    elif act == "clear":
        vr_send("31")
    elif act:
        return {"ok": False, "error": "неизвестное действие"}
    return {"ok": True, "vr": vr_status()}


def mic_live_worker():
    """v2.93: живой звук. Пока облако держит mic_live (кнопка «Слушать» в
    облаке), каждые ~0.5 с шлём накопленный PCM 16 кГц в /api/hearing/stream;
    облако склеивает поток и даёт послушать его в браузере."""
    import base64
    seq = 0
    while True:
        if time.time() >= _MIC.get("live_until", 0) or not _MIC_LIVE:
            if time.time() >= _MIC.get("live_until", 0):
                _MIC_LIVE.clear()
            time.sleep(0.25)
            continue
        time.sleep(0.5)
        parts = []
        while _MIC_LIVE:
            parts.append(_MIC_LIVE.popleft())
        pcm = b"".join(parts)
        url, token = _cloud_base()
        if not url or not pcm:
            continue
        seq += 1
        r, err = _http_json_err(url + "/api/hearing/stream", {
            "board": _hostname(), "seq": seq, "sr": MIC_SR, "rms": _MIC.get("rms", 0),
            "pcm": base64.b64encode(pcm).decode("ascii")}, token=token, timeout=10)
        if r and r.get("ok"):
            _MIC["live_sent"] += 1
            _MIC["live_err"] = ""
            if r.get("stop"):
                _MIC["live_until"] = 0.0
        else:
            _MIC["live_err"] = str(err or (r or {}).get("error") or "?")[:120]
            if "404" in _MIC["live_err"]:
                _MIC["live_until"] = 0.0


def register_web():
    ui.expose_api("GET", "/api/state", api_state)
    ui.expose_api("GET", "/api/alive", api_alive)          # v2.62: сторож
    ui.expose_api("POST", "/api/game/reset", api_game_reset)  # v2.69: игра
    ui.expose_api("GET", "/api/messages", api_messages)
    ui.expose_api("POST", "/api/send", api_send)
    ui.expose_api("POST", "/api/think", api_think)          # v2.58
    ui.expose_api("GET", "/api/settings", api_get_settings)
    ui.expose_api("POST", "/api/settings", api_set_settings)
    ui.expose_api("GET", "/api/vr", api_vr)                  # v2.94: голосовой модуль VR3
    ui.expose_api("POST", "/api/vr", api_vr)
    ui.expose_api("GET", "/api/tts_audio", api_tts_audio)
    ui.expose_api("POST", "/api/teach", api_teach)
    ui.expose_api("POST", "/api/speak", api_speak)
    ui.expose_api("GET", "/api/memory", api_memory_info)
    ui.expose_api("POST", "/api/memory/sync", api_memory_sync)
    ui.expose_api("POST", "/api/memory/forget", api_memory_forget)
    # v2.8: облако как мозг + словари + диктант
    ui.expose_api("POST", "/api/cloud/search", api_cloud_search)
    ui.expose_api("POST", "/api/cloud/code", api_cloud_code)
    ui.expose_api("GET", "/api/cloud/code", api_cloud_code_get)
    ui.expose_api("POST", "/api/cloud/write", api_cloud_write)
    ui.expose_api("GET", "/api/cloud/skills", api_cloud_skills)
    ui.expose_api("POST", "/api/cloud/run_skill", api_cloud_run_skill)
    ui.expose_api("GET", "/api/dicts", api_dicts)
    ui.expose_api("POST", "/api/dicts/lookup", api_dicts_lookup)
    ui.expose_api("POST", "/api/dictate", api_dictate)
    # v2.9: страница «Облако» (ChatGPT-стиль) + файлы (фото/PDF/TXT/XLSX/PPTX)
    ui.expose_api("POST", "/api/cloud/chat", api_cloud_chat)
    ui.expose_api("POST", "/api/upload", api_upload)
    # v2.25: нейросеть материалов — длинный текст, обучение, показания
    ui.expose_api("POST", "/api/mozgnet/write", api_mozgnet_write)
    ui.expose_api("POST", "/api/mozgnet/learn", api_mozgnet_learn)
    ui.expose_api("GET", "/api/mozgnet/readings", api_mozgnet_readings)
    # v2.11: проверка связи с 12 нейронами (кнопка в интерфейсе)
    ui.expose_api("POST", "/api/neurons/test", api_neurons_test)
    ui.expose_api("GET", "/api/docs", api_docs)
    ui.expose_api("POST", "/api/docs/clear", api_doc_clear)
    # v2.39: тензорное ядро — компьютер (браузер :7000) берёт задачи
    ui.expose_api("GET", "/api/tensor/job", api_tensor_job)
    ui.expose_api("POST", "/api/tensor/result", api_tensor_result)
    ui.on_connect(lambda sid: ui.send_message(
        "hello", {"app": "neuro_12", "version": APP_VERSION}))


# ===========================================================================
# [10] ГЛАВНЫЙ ЦИКЛ ПРИЛОЖЕНИЯ
# ===========================================================================
# ---- v2.75: ОБЩИЙ СЛОВАРЬ (shareddict.py — тот же файл, что в облаке) ----
_SDICT = {"obj": None, "last": 0.0, "ok": 0, "err": "", "got": 0}


def sdict():
    if _SDICT["obj"] is None:
        import shareddict
        _SDICT["obj"] = shareddict.SharedDict(
            os.path.join(_base_dirs()[0], "neuro12_shared_dict.json"))
    return _SDICT["obj"]


def _sdict_sync_job():
    url, token = _cloud_base()
    if not url:
        return
    sd = sdict()
    inc = sd.take_inc()
    for _ in range(5):                   # изменений много — догружаем частями
        r, err = _http_json_err(url + "/api/worddict/sync",
                                {"board": _hostname(), "since": sd.cursor, "inc": inc},
                                token=token, timeout=30)
        if not r or not r.get("ok"):
            sd.give_back(inc)
            _SDICT["err"] = str(err or (r or {}).get("error") or "?")[:120]
            if "404" in _SDICT["err"]:
                _SDICT["err"] = "облако старше v3.133 — общего словаря нет"
            return
        inc = {}
        rows = r.get("rows") or {}
        sd.apply_rows(rows)
        _SDICT["got"] += len(rows.get("w") or {})
        if not rows.get("more"):
            break
    _SDICT.update(ok=_SDICT["ok"] + 1, err="")
    sd.save(force=True)


def sdict_tick():
    """Сверка общего словаря с облаком — раз в 60 с, на вдохе."""
    now = time.time()
    if now - _SDICT["last"] < 60.0 or not _cloud_base()[0]:
        return
    if breath_s() < 0:                   # сверка — на вдохе общего дыхания
        return
    _SDICT["last"] = now
    threading.Thread(target=_sdict_sync_job, daemon=True).start()


def sdict_status():
    try:
        st = sdict().stats()
    except Exception as e:
        return {"err": str(e)[:100]}
    st.update(syncs=_SDICT["ok"], err=_SDICT["err"], got=_SDICT["got"])
    return st


_BGT = [0.0, 0.0, -999]


def breath_gen_tick():
    """v2.74: буквогенератор скетча дышит: каждые 2 с цель синхронизации
    частоты = общий ритм плат × (1 ± 8 %) по фазе общего дыхания."""
    now = time.time()
    if now - _BGT[0] < 2.0 or not CFG.get("sync_on", True):
        return
    _BGT[0] = now
    sy = _LINK.get("sync") or {}
    if not sy.get("hz") or now - float(sy.get("ts") or 0) > 60:
        return
    hz_ = int(round(breath_mod("Буквогенератор скетча", float(sy["hz"]), 0.08)))
    # v2.78: скетчу — только когда он жив, не чаще раза в 10 с и только при
    # заметной смене цели (раньше вызов каждые 2 с забивал очередь моста)
    if not SH.bridge_seen or now - _BGT[1] < 10.0 or abs(hz_ - _BGT[2]) < 3:
        return
    _BGT[1], _BGT[2] = now, hz_
    try:
        _bridge_call("sync_hz", "%d,%d" % (hz_, int(sy.get("k") or 30)), wait=False)
    except Exception:
        pass


def brain_tick():
    """Один тик основного цикла (App.run вызывает его бесконечно).
    Тут живёт учитель, любопытство, пульс моста и синхронизация; WebUI и
    мост работают сами."""
    _BEAT["tick"] = time.time()             # v2.62: пульс для сторожа
    try:
        time.sleep(0.5)
        cloud_tick()
        word_pump_tick()                   # v2.27: слова плате из словаря
        mem_sync_tick()
        teacher_tick()
        curiosity_tick()
        gen_tick()                         # v2.16: сеть знаний + мысли вслух
        tn_tick()                          # v2.39: тензор: объём по Гц + задачи
        bridge_ping_tick()                 # v2.14: активный пульс скетча
        breath_tn_tick()                   # v2.75: тензорная сеть — источник дыхания
        breath_gen_tick()                  # v2.74: генератор дышит с платами
        sdict_tick()                       # v2.75: общий словарь с облаком
    except StopIteration:
        raise
    except Exception:
        traceback.print_exc()
        time.sleep(2.0)


def main():
    log("neuro_12 v%s стартует (App Lab): мозг-12, зонная сеть с речью из "
        "словаря (генератор СЛУШААТ нейроны, v2.17), единый мозг "
        "(мозг-почта), словари, диктант, "
        "советы-наставления; LLM — только учитель/наставник (/api/llm/teach); "
        "нейросеть материалов пишет длинные тексты (v2.25); "
        "плате — слова из большого словаря каждые 0.5 с и память на каждое "
        "слово (очередь в скетче + журнал мозга, v2.34); "
        "камера — стрим в облаке (/cam) и буфер на платы, платформа: "
        "чтение/письмо статей + интернет + задания генератору, "
        "назначение номеров нейронам «нейроны» и их сброс «нейроны сброс» "
        "(v2.29); камера по проводу (cam_url = «uart», приёмник в скетче, "
        "v2.30); "
        "облако/TTS/поиск/код — через облако" % APP_VERSION)
    log("конфиг: %s" % _cfg_path())
    log("память: %s (облако %s, плата «%s»)" %
        (_mem_path(), "вкл" if CFG.get("mem_sync") else "выкл", _hostname()))
    load_dicts_async()                     # словари русского языка (фоном)
    _nn_load()                             # v2.15: адаптивная зонная сеть
    _tn_load()                             # v2.39: тензорное ядро
    mn_load()                              # v2.25: нейросеть материалов
    _word_mem_load()                       # v2.34: журнал слов (память)
    if _word_mem["count"]:
        log("СЛОВА", "журнал слов: отослано %d, уникальных %d (%s)" %
            (_word_mem["count"], len(_word_mem["unique"]),
             _words_mem_path() or "?"))
    register_bridge()
    _bridge_start_worker()                 # v2.16: очередь всех Bridge.call
    if not _wordq_poll_on[0]:              # v2.34: опрос очереди слов платы
        _wordq_poll_on[0] = True
        threading.Thread(target=_word_q_poller, daemon=True).start()
    register_web()
    # v2.10: ЕДИНЫЙ МОЗГ — фоновой опрос писем других устройств;
    # опрос inbox держит устройство «в сети» в реестре мозгов облака
    threading.Thread(target=brainmail_worker, daemon=True).start()
    threading.Thread(target=unify_sync_worker, daemon=True).start()  # v2.41
    # v2.20: сборщик мыслей — фразы мозга копятся и уходят в облако одним
    # текстом (чат «Мысли плат» + лента Studio + хроника), ничего не теряя
    threading.Thread(target=thought_worker, daemon=True).start()
    try:
        _wd_load()                                                # v2.62
    except Exception:
        pass
    threading.Thread(target=link_worker, daemon=True).start()     # v2.60
    threading.Thread(target=game_worker, daemon=True).start()     # v2.69: игра
    threading.Thread(target=mic_listen_worker, daemon=True).start()  # v2.70: уши
    threading.Thread(target=mic_send_worker, daemon=True).start()
    threading.Thread(target=mic_live_worker, daemon=True).start()     # v2.93: живой звук
    threading.Thread(target=vosk_install_worker, daemon=True).start() # v2.93: Vosk на плате
    threading.Thread(target=vr_worker, daemon=True).start()           # v2.94: голосовой модуль VR3
    try:
        _crash_hooks()                                            # v2.61
    except Exception:
        pass
    # v2.24: подкачка кэша и буфера мозга — ИЗ ОБЛАКА (GET /api/buffer;
    # облако не задано — воркер тихо ждёт)
    threading.Thread(target=buf_worker, daemon=True).start()
    # v2.25: нейросеть материалов — фон-обучение + показания в облако
    threading.Thread(target=mn_worker, daemon=True).start()
    # v2.28: камера — кадры ESP32-CAM -> облако (стрим /cam) + буфер -> скетч
    threading.Thread(target=cam_worker, daemon=True).start()
    threading.Thread(target=camai_worker, daemon=True).start()   # v2.88
    # v2.28: платформа — мозг читает статьи, лазит в интернет, пишет статьи,
    # ставит задания генератору облака (мысли мозга направляют облако)
    threading.Thread(target=platform_worker, daemon=True).start()
    # v2.28: назначение номеров нейронам (SETID) — если платы без номера
    threading.Timer(30.0, _neuro_assign_async).start()
    add_message("system", "Мозг-12 v%s запущен через Arduino App Lab. "
                "Отвечаю САМ (зоны + навыки + память); LLM — только наставник: "
                "советы, цели диктанта, оценки, речи через облако -> скетч. "
                "Я и сам говорю фразами из словаря (команда «мысль <тема>»), "
                "а слова для мыслей мне подсказывают МОИ НЕЙРОНЫ: сеть слушает "
                "всё — твои сообщения, буквы, спайки плат — и самые активные "
                "нейроны задают тему (v2.17). "
                "Мост к скетчу живёт постоянно: очередь вызовов + пульс + "
                "живость по любому пакету. Устройства — единый мозг: "
                "«мозг <id> <текст>» — письмо другому устройству. "
                "Кнопка 📎 — фото, PDF, Excel, презентации."
                % APP_VERSION, "Старт")
    App.run(user_loop=brain_tick)


if __name__ == "__main__":
    main()
