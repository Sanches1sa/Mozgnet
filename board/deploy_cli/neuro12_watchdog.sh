#!/bin/bash
# ===========================================================================
# neuro12_watchdog.sh — СТОРОЖ: держит приложение neuro_12 запущенным на
# Arduino UNO Q. Работает НА САМОЙ ПЛАТЕ как служба systemd (ставит её
# install_autostart.sh). Компьютер и облако для перезапуска не нужны.
#
# v2.62 — что считается «отключилась»:
#   1) порт 7000 (веб-интерфейс приложения) не отвечает — приложение упало
#      или закрылось («ошибка подключения»/WinError 10061 у облака);
#   2) порт отвечает, но приложение ЗАВИСЛО: /api/alive сообщает, что поток
#      связи с облаком или основной цикл не подавали пульс > 10 мин.
# Решение принимается не по одной неудаче: FAILS проверок подряд
# (по умолчанию 3 × 15 с ≈ 45 с). Тогда:
#   arduino-app-cli app stop  <приложение>   (на случай «висит, но числится»)
#   arduino-app-cli app start <приложение>   (сборка/прошивка скетча + Python)
# и ждём, пока порт снова ответит (до WARMUP с). Не поднялось — повтор с
# нарастающей паузой (1, 2, 5, 10 мин), чтобы не долбить плату без конца.
#
# Каждый перезапуск пишется в журнал (journalctl -u neuro12) и в файл
# <приложение>/neuro12_watchdog.json — приложение читает его при старте и
# отправляет облаку: в журнале облака видно «перезапущено сторожем: причина».
#
# Пауза сторожа без остановки службы: создать файл <приложение>/.watchdog_pause
# (например, пока сам правишь приложение) — удалить, и слежение продолжится.
#
# Переменные окружения (можно задать в юните):
#   ALIVE_URL  — лёгкая проверка (http://127.0.0.1:7000/api/alive)
#   STATE_URL  — запасная проверка для старых версий (…/api/state)
#   BOOT_DELAY — тишина после старта службы, сек (60)
#   CHECK      — интервал проверок, сек (15)
#   FAILS      — сколько неудач подряд = перезапуск (3)
#   WARMUP     — сколько ждать подъёма после app start, сек (300)
#   SKETCH_WAIT — сколько ждать, что ожил СКЕТЧ (после порта), сек (150)
#   ROUTER_SVC — служба моста MPU<->MCU на плате (arduino-router)
#
# v2.71 — «после перезапуска сторожем скетч не запускается»:
#   App Lab грузит скетч в ОЗУ микроконтроллера (flash_mode=ram); app stop
#   сбрасывает MCU, и скетч снова появляется только если app start заново
#   его соберёт и зальёт. Раньше сторож не смотрел, что ответил app start, и
#   считал перезапуск удачным, как только отвечал порт 7000 (это Python),
#   даже если скетч не залился. Теперь:
#   1) весь вывод app start пишется в <приложение>/neuro12_start.log и в
#      журнал; ошибка заливки видна словами (и уходит облаку в журнал);
#   2) между stop и start — 15 с (MCU сбрасывается, мост отпускает порт);
#   3) после старта сторож ждёт, что СКЕТЧ ответил (sketch_seen в /api/alive);
#   4) не ответил — «глубокий» перезапуск: stop, перезапуск службы моста
#      arduino-router (право дал install_autostart.sh), 20 с, start заново.
# ===========================================================================
set -u

CLI="${1:?использование: neuro12_watchdog.sh <путь к arduino-app-cli> <папка приложения>}"
APP_DIR="${2:?не указана папка приложения}"
ALIVE_URL="${ALIVE_URL:-http://127.0.0.1:7000/api/alive}"
STATE_URL="${STATE_URL:-http://127.0.0.1:7000/api/state}"
BOOT_DELAY="${BOOT_DELAY:-60}"
CHECK="${CHECK:-15}"
FAILS="${FAILS:-3}"
WARMUP="${WARMUP:-300}"
SKETCH_WAIT="${SKETCH_WAIT:-150}"
ROUTER_SVC="${ROUTER_SVC:-arduino-router}"
START_LOG="$APP_DIR/neuro12_start.log"
WD_JSON="$APP_DIR/neuro12_watchdog.json"
PAUSE="$APP_DIR/.watchdog_pause"

log() { echo "[neuro12-watchdog] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

# probe -> печатает "ok" | "down: <почему>" | "stuck: <почему>"
probe() {
  python3 - "$ALIVE_URL" "$STATE_URL" <<'PY' 2>/dev/null || echo "down: проверка не выполнилась"
import json, sys, urllib.request, urllib.error
alive, state = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(alive, timeout=8) as r:
        d = json.loads(r.read().decode("utf-8", "replace") or "{}")
    st = str(d.get("stuck") or "")
    print("stuck: " + st if st else "ok")
except urllib.error.HTTPError as e:
    if e.code == 404:               # приложение старше v2.62 — нет /api/alive
        try:
            urllib.request.urlopen(state, timeout=8).read()
            print("ok")
        except Exception as e2:
            print("down: порт 7000 не отвечает (%s)" % type(e2).__name__)
    else:
        print("down: порт 7000 ответил ошибкой HTTP %s" % e.code)
except Exception as e:
    print("down: порт 7000 не отвечает (%s: %s)" % (type(e).__name__, str(e)[:80]))
PY
}

# sketch_probe -> "yes" | "no" | "?" (скетч ответил мосту хоть раз)
sketch_probe() {
  python3 - "$ALIVE_URL" <<'PY' 2>/dev/null || echo "?"
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=8) as r:
        d = json.loads(r.read().decode("utf-8", "replace") or "{}")
    if "sketch_seen" not in d:
        print("?")              # приложение старше v2.71 — не знает
    else:
        print("yes" if d.get("sketch_seen") else "no")
except Exception:
    print("?")
PY
}

# хвост лога старта одной строкой (для журнала облака)
start_tail() {
  [ -f "$START_LOG" ] || { echo ""; return; }
  grep -E "ERROR|Error|error|Failed|failed|ошибк|timed out|Upload|upload" "$START_LOG" \
    | tail -n 4 | tr '\n' ' ' | cut -c1-300
}

restart_router() {
  if command -v systemctl >/dev/null 2>&1 && \
     systemctl list-unit-files "$ROUTER_SVC.service" 2>/dev/null | grep -q "$ROUTER_SVC"; then
    if sudo -n systemctl restart "$ROUTER_SVC" 2>/dev/null; then
      log "служба моста $ROUTER_SVC перезапущена"
      return 0
    fi
    log "нет права перезапустить $ROUTER_SVC (запусти заново: sudo bash install_autostart.sh watchdog)"
    return 1
  fi
  log "службы $ROUTER_SVC на плате нет — пропускаю"
  return 1
}

# v2.80: оба кэша сборки — приложения и общий кэш ядра arduino-cli
clean_build_cache() {
  rm -rf "$APP_DIR/.cache"
  rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/arduino" "$HOME/.cache/arduino" 2>/dev/null
  find /tmp -maxdepth 3 \( -name 'arduino-core-cache' -o -name 'arduino-sketch-*' \) -exec rm -rf {} + 2>/dev/null
  true
}

# один цикл stop -> start; $1 — 1, если перед стартом перезапустить мост
cycle() {
  log "app stop…"
  timeout 120 "$CLI" app stop "$APP_DIR" >>"$START_LOG" 2>&1 || true
  sleep 15
  if [ "${1:-0}" = "1" ]; then
    restart_router || true; sleep 20
    # v2.96.5: после перезапуска роутера сокет /var/run/arduino-router.sock
    # ПЕРЕСОЗДАН, а контейнер приложения держит старый inode (сокет пробро-
    # шен bind-mount-ом файла) — Bridge.read_loop вечно пишет «Failed to
    # connect to router: Connection refused». Контейнер надо пересоздать:
    # убираем его, app start соберёт новый уже с новым сокетом.
    if command -v docker >/dev/null 2>&1; then
      docker rm -f "$(basename "$APP_DIR")-main-1" >/dev/null 2>&1 && \
        log "контейнер приложения удалён — будет пересоздан с новым сокетом моста"
    fi
  fi
  log "app start (сборка и заливка скетча в MCU + Python)…"
  echo "===== $(date '+%F %T') app start (глубокий=${1:-0}) =====" >>"$START_LOG"
  if ! timeout 900 "$CLI" app start "$APP_DIR" >>"$START_LOG" 2>&1; then
    log "app start завершился с ошибкой: $(start_tail)"
    # v2.80: битый кэш сборки (core.a «file truncated»). arduino-cli держит
    # ДВА кэша: сборка приложения ($APP_DIR/.cache) и ОБЩИЙ кэш собранного
    # ядра (~/.cache/arduino/cores/…/core.a) — его он копирует в каждую новую
    # сборку. v2.79 чистил только первый, поэтому битый core.a возвращался.
    # Теперь при ЛЮБОЙ ошибке сборки чистим оба и пробуем ещё раз.
    log "чищу кэши сборки (приложения и ядра arduino) и собираю заново; место: $(df -h "$APP_DIR" | awk 'NR==2{print $4" свободно из "$2}')"
    clean_build_cache
    echo "===== $(date '+%F %T') app start после очистки кэшей сборки =====" >>"$START_LOG"
    if ! timeout 900 "$CLI" app start "$APP_DIR" >>"$START_LOG" 2>&1; then
      log "и после очистки кэшей app start с ошибкой: $(start_tail)"
      return 2
    fi
  fi
  # ждём порт 7000 (Python)
  local t=0
  while [ "$t" -lt "$WARMUP" ]; do
    sleep 10; t=$((t + 10))
    [ "$(probe)" = "ok" ] && break
    case "$(probe)" in stuck:*скетч*) break ;; esac
  done
  if [ "$t" -ge "$WARMUP" ]; then
    log "за ${WARMUP} с приложение так и не ответило"
    return 3
  fi
  # ждём, что ожил СКЕТЧ
  local s=0 sp="?"
  while [ "$s" -lt "$SKETCH_WAIT" ]; do
    sp="$(sketch_probe)"
    [ "$sp" != "no" ] && break
    sleep 10; s=$((s + 10))
  done
  if [ "$sp" = "no" ]; then
    log "Python поднялся, а скетч за ${SKETCH_WAIT} с не ответил: $(start_tail)"
    return 4
  fi
  log "приложение отвечает через $((t + s)) с, скетч: $sp"
  return 0
}

# записать состояние сторожа (его читает приложение и показывает облаку)
RESTARTS=0
if [ -f "$WD_JSON" ]; then
  RESTARTS="$(python3 -c 'import json,sys;print(int(json.load(open(sys.argv[1])).get("restarts",0)))' "$WD_JSON" 2>/dev/null || echo 0)"
fi
save_state() {   # $1 — причина, $2 — итог
  python3 - "$WD_JSON" "$RESTARTS" "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys, time, os
fn, n, why, res = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
d = {}
try:
    d = json.load(open(fn))
except Exception:
    pass
hist = (d.get("history") or [])[-19:]
hist.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "why": why, "res": res})
d.update({"restarts": n, "last_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
          "last_reason": why, "last_result": res, "history": hist})
tmp = fn + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=1)
os.replace(tmp, fn)
PY
}

restart_app() {   # $1 — причина
  RESTARTS=$((RESTARTS + 1))
  log "ПЕРЕЗАПУСК №$RESTARTS: $1"
  : >"$START_LOG.tmp"; tail -n 400 "$START_LOG" >"$START_LOG.tmp" 2>/dev/null; mv -f "$START_LOG.tmp" "$START_LOG" 2>/dev/null
  cycle 0; local rc=$?
  if [ "$rc" = "0" ]; then save_state "$1" "поднялось, скетч ответил"; return 0; fi
  if [ "$rc" = "2" ] || [ "$rc" = "4" ]; then
    log "обычный перезапуск не поднял скетч — глубокий: мост $ROUTER_SVC + заново"
    cycle 1; rc=$?
    if [ "$rc" = "0" ]; then save_state "$1" "поднялось после глубокого перезапуска (мост)"; return 0; fi
  fi
  case "$rc" in
    2) save_state "$1" "app start: ошибка — $(start_tail)" ;;
    3) save_state "$1" "не поднялось за ${WARMUP} с" ;;
    4) save_state "$1" "скетч не запустился — $(start_tail)" ;;
  esac
  return 1
}

log "старт: cli=$CLI app=$APP_DIR (тишина ${BOOT_DELAY}s, проверка ${CHECK}s, перезапуск после ${FAILS} неудач)"
sleep "$BOOT_DELAY"

# v2.80: сторож обновляется вместе с приложением — если в папке приложения
# лежит другая версия этого скрипта, перезапускаемся с неё
SELF_SRC="$APP_DIR/deploy_cli/neuro12_watchdog.sh"
self_sum() { [ -f "$SELF_SRC" ] && sed 's/\r$//' "$SELF_SRC" | md5sum | cut -c1-32; }
SELF_SUM="$(self_sum)"
self_update() {
  local now; now="$(self_sum)"
  if [ -n "$now" ] && [ "$now" != "$SELF_SUM" ]; then
    log "в папке приложения новая версия сторожа — перезапускаюсь с неё"
    exec bash -c "$(sed 's/\r$//' "$SELF_SRC")" neuro12_watchdog "$CLI" "$APP_DIR"
  fi
}

bad=0
backoff=(60 120 300 600)
fail_restarts=0
same_sketch=0      # v2.67: перезапусков подряд из-за молчащего скетча
while true; do
  self_update
  if [ -f "$PAUSE" ]; then
    sleep "$CHECK"; bad=0; continue
  fi
  r="$(probe)"
  if [ "$r" = "ok" ]; then
    if [ "$bad" -gt 0 ]; then log "снова отвечает (было неудач подряд: $bad)"; fi
    bad=0; fail_restarts=0
    sleep "$CHECK"
    continue
  fi
  bad=$((bad + 1))
  log "проверка $bad/$FAILS не прошла — $r"
  if [ "$bad" -lt "$FAILS" ]; then
    sleep "$CHECK"; continue
  fi
  bad=0
  case "$r" in
    *скетч*) same_sketch=$((same_sketch + 1)) ;;
    *) same_sketch=0 ;;
  esac
  if [ "$same_sketch" -gt 3 ]; then
    # скетч не оживает даже после перезапусков — не мучаем плату:
    # пауза на час (вероятно, скетч не собирается или MCU неисправен)
    log "скетч не ожил после 3 перезапусков подряд — пауза 1 ч (проверь сборку скетча в App Lab)"
    save_state "$r" "скетч не оживает — пауза 1 ч"
    sleep 3600
    same_sketch=0
    continue
  fi
  if restart_app "$r"; then
    fail_restarts=0
  else
    i=$fail_restarts; [ "$i" -gt 3 ] && i=3
    log "пауза ${backoff[$i]} с перед следующей попыткой"
    sleep "${backoff[$i]}"
    fail_restarts=$((fail_restarts + 1))
  fi
done
