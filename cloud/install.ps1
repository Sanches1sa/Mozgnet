# ============================================================================
# install.ps1 — neuro12-cloud v3.8 · УСТАНОВЩИК ОБЛАКА «МОЗГ-12» ДЛЯ WINDOWS
# ----------------------------------------------------------------------------
# Запуск: двойной клик по install.bat, или в PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
# Опции: -Dir C:\neuro12-cloud  -Port 8077  -Token nz-...  -Password ...
#        -NoAutostart  -NoFirewall  -NoStart  -ResetPassword
#
# Что делает (права администратора НЕ обязательны):
#   1. найдёт Python 3.8+ (py/python/python3; подскажет winget, если нет);
#   2. скопирует платформу в %LOCALAPPDATA%\neuro12-cloud (или -Dir);
#   3. проверит ядро (импорт модулей, временный data-каталог);
#   4. создаст токен платы (X-Mozg-Token) и ПАРОЛЬ веб-панели;
#   5. запишет start.bat / stop.bat / uninstall.bat / cloud_bg.vbs;
#   6. поставит лаунчер neuro12-cloud.exe (двойной клик — панель);
#   7. добавит автозапуск при входе в Windows через Планировщик;
#   8. откроет порт в брандмауэре Windows 11 (спросит права, если нужно);
#   9. положит ярлык «neuro12-cloud» на рабочий стол;
#  10. запустит облако скрытно (pythonw) и проверит /api/health.
# Порт платы (7000) и облака (8077) не конфликтуют: облако само возьмёт
# 8077 или первый свободный рядом и запишет его в data\port.txt.
# ============================================================================
param(
  [string]$Dir = "",
  [int]$Port = 8077,
  [string]$Token = "",
  [string]$Password = "",
  [switch]$NoAutostart,
  [switch]$NoFirewall,
  [switch]$NoStart,
  [switch]$ResetPassword,
  [switch]$ImportData       # v3.30: полный архив — заменить data (с копией)
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($t)  { Write-Host "" ; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!!] $t" -ForegroundColor Yellow }
function Die($t)  { Write-Host "  [XX] $t" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  neuro12-cloud v3.8 — установка облака 'мозг-12' на Windows" -ForegroundColor Green
Write-Host "  ===========================================================" -ForegroundColor Green

# ------------------------------- 1. Python ----------------------------------
Say "ПОИСК PYTHON 3.8+"
$PyExe = $null; $PyArgs = @()
foreach ($c in @(@{exe="py"; a=@("-3")}, @{exe="python"; a=@()},
                 @{exe="python3"; a=@()})) {
  $cmd = Get-Command $c.exe -ErrorAction SilentlyContinue
  if ($cmd) {
    $ver = ""
    try {
      $ver = & $c.exe @($c.a + @("-c", "import sys;print('%d.%d'%sys.version_info[:2])")) 2>$null
    } catch {}
    if ($ver -match "^\s*(\d+)\.(\d+)") {
      if ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 8)) {
        $PyExe = $c.exe; $PyArgs = $c.a
        Ok ("python {0}: {1}" -f $ver.Trim(), $cmd.Source)
        break
      } else {
        Warn ("найден python {0}, но нужен 3.8+" -f $ver.Trim())
      }
    }
  }
}
if (-not $PyExe) {
  Warn "Python не найден. Установи его одним из способов:"
  Write-Host '     1) в терминале:  winget install -e --id Python.Python.3.12'
  Write-Host '     2) сайт: https://www.python.org/downloads/ (галочка "Add to PATH"!)'
  $ans = Read-Host "  Попробовать установить через winget прямо сейчас? [Y/N]"
  if ($ans -match "^[YyДд]") {
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Die "python так и не найден — переустанови и запусти install.bat снова" }
    $PyExe = "python"; $PyArgs = @()
    Ok "python установлен через winget"
  } else { Die "без Python продолжать нельзя" }
}
# реальный путь интерпретатора (для pythonw)
$PyReal = & $PyExe @($PyArgs + @("-c", "import sys;print(sys.executable)"))
$PyReal = $PyReal.Trim()
$PyWin  = $PyReal -replace "python\.exe$", "pythonw.exe"

# ------------------- 1б. numpy (необязательно, v3.33) -----------------------
# Нужен ТОЛЬКО своей LLM с вашими размерами (d_model, слои...). Без него
# облако работает полностью, но LLM — старая мини-модель на чистом Python.
Say "NUMPY (необязательно: для своей LLM с вашими размерами)"
$prevEap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$hasNp = $false
try {
  & $PyExe @($PyArgs + @("-c", "import numpy")) *> $null
  $hasNp = ($LASTEXITCODE -eq 0)
  if (-not $hasNp) {
    Write-Host "  numpy не найден — ставлю (pip, нужен интернет)..."
    & $PyExe @($PyArgs + @("-m", "pip", "install", "--disable-pip-version-check", "numpy")) *> $null
    & $PyExe @($PyArgs + @("-c", "import numpy")) *> $null
    $hasNp = ($LASTEXITCODE -eq 0)
  }
} catch { $hasNp = $false }
$ErrorActionPreference = $prevEap
if ($hasNp) { Ok "numpy есть - своя LLM работает по-настоящему" }
else { Warn "numpy не установился - облако работает, LLM будет мини-версией. Поставить позже: Настройки - API и ответы - Своя LLM - кнопка 'Установить numpy'" }

# -------------------------------- 2. Каталог --------------------------------
Say "КАТАЛОГ УСТАНОВКИ"
if (-not $Dir) { $Dir = Join-Path $env:LOCALAPPDATA "neuro12-cloud" }
# v3.89: работающее облако держит файлы (веса и Adam модели на диске) —
# останавливаем его перед копированием (запустится заново в конце)
try {
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server\app.py*' -and $_.Name -match 'python' } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    Ok ("остановлено работающее облако (процесс {0})" -f $_.ProcessId) }
  Start-Sleep -Seconds 1
} catch {}
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Dir "data") | Out-Null
foreach ($f in @("server", "web", "launcher")) {
  if (Test-Path (Join-Path $Root $f)) {
    Copy-Item -Recurse -Force -Path (Join-Path $Root $f) -Destination $Dir
  }
}
# v3.30: ярлык «Мозг-12 запуск» (облако + плата без App Lab) на рабочем столе
try {
  $exeL = Join-Path $Dir "launcher\neuro12-start.exe"
  if (Test-Path $exeL) {
    $sh = New-Object -ComObject WScript.Shell
    $lk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Мозг-12 запуск.lnk"))
    $lk.TargetPath = $exeL; $lk.WorkingDirectory = (Join-Path $Dir "launcher"); $lk.Save()
  }
} catch {}
foreach ($f in @("VERSION", "README.md")) {
  if (Test-Path (Join-Path $Root $f)) {
    Copy-Item -Force -Path (Join-Path $Root $f) -Destination $Dir
  }
}
Ok ("платформа скопирована: {0}" -f $Dir)

# v3.30: ПОЛНЫЙ АРХИВ облака («Скачать облако целиком»: код + база знаний +
# файлы + обученная сеть). Его data переносится в установку: в пустую —
# сразу, поверх существующей — только с -ImportData и после резервной копии.
$SrcData = Join-Path $Root "data"
$DstData = Join-Path $Dir "data"
if ((Test-Path (Join-Path $SrcData "settings.json")) -and
    ((Resolve-Path $SrcData).Path -ne (Resolve-Path $DstData).Path)) {
  $hasOld = Test-Path (Join-Path $DstData "settings.json")
  if ($hasOld -and -not $ImportData) {
    Warn "в архиве есть данные облака, но в $DstData уже есть свои — оставляю их."
    Write-Host '       (перенести данные из архива: install.bat -ImportData)'
  } else {
    if ($hasOld) {
      $bak = $DstData + ".bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
      Copy-Item -Recurse -Force -Path $DstData -Destination $bak
      Ok "прежние данные сохранены: $bak"
    }
    Copy-Item -Recurse -Force -Path (Join-Path $SrcData "*") -Destination $DstData
    Ok "данные облака из архива перенесены (база знаний, файлы, обученная сеть)"
  }
}

# ------------------------------ 3. Самотест ---------------------------------
Say "ПРОВЕРКА ЯДРА (импорт модулей)"
$env:NEURO_DATA = Join-Path $env:TEMP ("n12selftest-" + [guid]::NewGuid().ToString("N").Substring(0,8))
& $PyExe @($PyArgs + @("-c", "import sys; sys.path.insert(0, r'$Dir\server'); import store,skills,brain,api_board,api_ui,auth; print('ok')")) | ForEach-Object { $out = $_ }
Remove-Item -Recurse -Force -Path $env:NEURO_DATA -ErrorAction SilentlyContinue
Remove-Item Env:NEURO_DATA -ErrorAction SilentlyContinue
if ("$out" -ne "ok") { Die "ядро платформы не импортируется — см. сообщение выше" }
Ok "все модули импортируются (мозг, mozgnet, учитель)"

# -------------------------------- 4. Токен ----------------------------------
Say "ТОКЕН ПЛАТЫ (X-Mozg-Token)"
$TokenFile = Join-Path $Dir "data\token.txt"
if ($Token) {
  [IO.File]::WriteAllText($TokenFile, $Token + "`n")
  Ok "токен записан (из параметра -Token)"
} elseif (Test-Path $TokenFile) {
  $Token = (Get-Content $TokenFile -Raw).Trim()
  Ok "токен уже был — оставляю прежний"
} else {
  $Token = (& $PyExe @($PyArgs + @("-c", "import os;print('nz-'+os.urandom(12).hex())"))).Trim()
  [IO.File]::WriteAllText($TokenFile, $Token + "`n")
  Ok "сгенерирован новый токен платы"
}

# ------------------------------ 5. Пароль -----------------------------------
Say "ПАРОЛЬ ВЕБ-ПАНЕЛИ"
$AuthFile = Join-Path $Dir "data\auth.json"
$env:NEURO_DATA = Join-Path $Dir "data"
$needPw = (-not (Test-Path $AuthFile)) -or $ResetPassword -or ($Password -ne "")
if (-not $needPw) {
  Ok "пароль уже был — оставляю прежний"
  Write-Host "       (сменить: install.ps1 -Password НОВЫЙ или -ResetPassword)"
} else {
  if (-not $Password) {
    $Password = "n12-" + (& $PyExe @($PyArgs + @("-c", "import os;print(os.urandom(5).hex())"))).Trim()
  }
  $env:N12PW = $Password
  $code = "import sys,os; sys.path.insert(0, r'$Dir\server'); import auth; auth.set_password(os.environ['N12PW']); print('ok')"
  $out = & $PyExe @($PyArgs + @("-c", $code))
  if ("$out" -ne "ok") { Die "не удалось записать пароль панели" }
  Ok "пароль веб-панели установлен (PBKDF2-хеш в data\auth.json)"
  Remove-Item Env:N12PW -ErrorAction SilentlyContinue
}
Remove-Item Env:NEURO_DATA -ErrorAction SilentlyContinue

# --------------------------- 6. Файлы управления ----------------------------
Say "ФАЙЛЫ УПРАВЛЕНИЯ (start / stop / uninstall)"
$DirU = $Dir.Replace("'", "''")
$PortS = "$Port"

$startBat = @'
@echo off
chcp 65001 >nul
title neuro12-cloud - cloud
set "NEURO_PORT=__PORT__"
set "NEURO_DATA=__DIR__\data"
echo ============================================
echo   neuro12-cloud : http://localhost:__PORT__
echo   Пароль панели задан при установке.
echo   Остановка: stop.bat (или Ctrl+C здесь)
echo ============================================
"__PYEXE__" "__DIR__\server\app.py"
pause
'@
$startBat = $startBat.Replace("__PORT__", $PortS).Replace("__DIR__", $Dir).Replace("__PYEXE__", $PyReal)
[IO.File]::WriteAllText((Join-Path $Dir "start.bat"), $startBat)

$stopBat = @'
@echo off
chcp 65001 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server\app.py*' -and $_.Name -match 'python' } | ForEach-Object { Write-Host ('  stopped process ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
echo neuro12-cloud stopped.
timeout /t 2 >nul
'@
$stopBat = $stopBat.Replace("__DIR__", $Dir)
[IO.File]::WriteAllText((Join-Path $Dir "stop.bat"), $stopBat)

$bgVbs = @'
' cloud_bg.vbs — скрытый запуск облака (без окна консоли)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "__DIR__"
sh.Environment("PROCESS")("NEURO_PORT") = "__PORT__"
sh.Environment("PROCESS")("NEURO_DATA") = "__DIR__\data"
sh.Run """__PYWEXE__"" ""__DIR__\server\app.py""", 0, False
'@
$bgVbs = $bgVbs.Replace("__DIR__", $Dir).Replace("__PORT__", $PortS).Replace("__PYWEXE__", $PyWin)
[IO.File]::WriteAllText((Join-Path $Dir "cloud_bg.vbs"), $bgVbs)

$uninstallPs1 = @'
# uninstall.ps1 — убрать neuro12-cloud из Windows
param([switch]$KeepData)
$Dir = "__DIR__"
$Port = __PORT__
Write-Host "== Удаление neuro12-cloud =="
schtasks /Delete /TN "neuro12-cloud" /F 2>$null | Out-Null
schtasks /Delete /TN "neuro12-cloud-guard" /F 2>$null | Out-Null
Write-Host "  [OK] автозапуск и страж (Планировщик) удалены"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) {
  netsh advfirewall firewall delete rule name="neuro12-cloud" | Out-Null
  Write-Host "  [OK] правило брандмауэра удалено"
} else {
  Write-Host "  [!!] брандмауэр: запусти uninstall.bat от администратора, чтобы убрать правило"
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server\app.py*' -and $_.Name -match 'python' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "  [OK] процессы остановлены"
if ($KeepData) {
  Write-Host "  [OK] память облака сохранена: $Dir\data"
  Remove-Item -Recurse -Force -Path (Join-Path $Dir "server"), (Join-Path $Dir "web") -ErrorAction SilentlyContinue
  Remove-Item -Force -Path (Join-Path $Dir "start.bat"), (Join-Path $Dir "stop.bat"), (Join-Path $Dir "cloud_bg.vbs"), (Join-Path $Dir "panel.url") -ErrorAction SilentlyContinue
} else {
  $ans = Read-Host "  Удалить и ПАМЯТЬ облака (data: чаты, память плат, KB)? [y/N]"
  if ($ans -match "^[YyДд]") {
    Remove-Item -Recurse -Force -Path $Dir -ErrorAction SilentlyContinue
    Write-Host "  [OK] всё удалено"
  } else {
    Write-Host "  [OK] память облака сохранена: $Dir\data"
    Remove-Item -Recurse -Force -Path (Join-Path $Dir "server"), (Join-Path $Dir "web") -ErrorAction SilentlyContinue
  }
}
Write-Host "Готово. Платформа удалена с этого компьютера."
'@
$uninstallPs1 = $uninstallPs1.Replace("__DIR__", $Dir).Replace("__PORT__", $PortS)
[IO.File]::WriteAllText((Join-Path $Dir "uninstall.ps1"), $uninstallPs1)

$uninstallBat = @'
@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
pause
'@
[IO.File]::WriteAllText((Join-Path $Dir "uninstall.bat"), $uninstallBat)

$panelUrl = "[InternetShortcut]`r`nURL=http://localhost:$Port/`r`nIconIndex=0`r`n"
[IO.File]::WriteAllText((Join-Path $Dir "panel.url"), $panelUrl)
$desktop = [Environment]::GetFolderPath("Desktop")
# лаунчер exe: из пакета (рядом с install.ps1 или в bin\) — в каталог установки
$exeSrc = $null
foreach ($cand in @((Join-Path $PSScriptRoot "neuro12-cloud.exe"),
                    (Join-Path $PSScriptRoot "bin\neuro12-cloud.exe"))) {
  if (Test-Path $cand) { $exeSrc = $cand; break }
}
if ($exeSrc) {
  try {
    Copy-Item -Force -Path $exeSrc -Destination (Join-Path $Dir "neuro12-cloud.exe")
    Ok "лаунчер neuro12-cloud.exe установлен (двойной клик — панель)"
  } catch { Warn "не удалось скопировать neuro12-cloud.exe" }
  try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $desktop "neuro12-cloud.lnk"))
    $lnk.TargetPath = Join-Path $Dir "neuro12-cloud.exe"
    $lnk.WorkingDirectory = $Dir
    $lnk.Description = "Облако мозг-12: запустить и открыть веб-панель"
    $lnk.Save()
    Ok "ярлык «neuro12-cloud» на рабочем столе (запускает облако и панель)"
  } catch {
    try {
      Copy-Item -Force -Path (Join-Path $Dir "panel.url") -Destination (Join-Path $desktop "neuro12-cloud.url")
      Ok "ярлык-ссылка «neuro12-cloud» на рабочем столе"
    } catch { Warn "не удалось положить ярлык на рабочий стол" }
  }
} else {
  try {
    Copy-Item -Force -Path (Join-Path $Dir "panel.url") -Destination (Join-Path $desktop "neuro12-cloud.url")
    Ok "ярлык «neuro12-cloud» на рабочем столе"
  } catch { Warn "не удалось положить ярлык на рабочий стол" }
}
Ok "start.bat / stop.bat / uninstall.bat / cloud_bg.vbs записаны"

# ------------------------------ 7. Автозапуск -------------------------------
if (-not $NoAutostart) {
  Say "АВТОЗАПУСК ПРИ ВХОДЕ + СТРАЖ КАЖДЫЕ 5 МИНУТ (Планировщик)"
  $tr = 'wscript.exe "' + (Join-Path $Dir "cloud_bg.vbs") + '"'
  schtasks /Create /TN "neuro12-cloud" /TR $tr /SC ONLOGON /RL LIMITED /F | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok "задача «neuro12-cloud» создана (запуск при входе, скрытно)" }
  else { Warn "не удалось создать задачу — автозапуск можно включить вручную (Планировщик заданий)" }
  # СТРАЖ: если облако упало или не поднялось — поднимет его в течение
  # 5 минут. Второй запуск безопасен: облако видит, что уже работает,
  # и тихо выходит (single-instance), дубли на соседних портах не плодит.
  schtasks /Create /TN "neuro12-cloud-guard" /TR $tr /SC MINUTE /MO 5 /RL LIMITED /F | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok "страж «neuro12-cloud-guard»: каждые 5 минут поднимает облако, если оно упало — панель больше не будет «недоступной»" }
  else { Warn "страж создать не удалось — не критично, облако запустится при входе" }
} else {
  Say "АВТОЗАПУСК ПРОПУЩЕН (-NoAutostart)"
}

# ------------------------------- 8. Файрвол ---------------------------------
if (-not $NoFirewall) {
  Say "БРАНДМАУЭР WINDOWS (порт $Port)"
  $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($admin) {
    netsh advfirewall firewall delete rule name="neuro12-cloud" | Out-Null
    netsh advfirewall firewall add rule name="neuro12-cloud" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Ok "правило добавлено: входящие TCP $Port разрешены"
  } else {
    $ans = Read-Host "  Открыть порт $Port в брандмауэре для плат в локальной сети? [Y/N]"
    if ($ans -match "^[YyДд]") {
      try {
        $fwArgs = 'advfirewall firewall add rule name="neuro12-cloud" dir=in action=allow protocol=TCP localport=' + $Port
        Start-Process -FilePath "netsh" -ArgumentList $fwArgs -Verb RunAs -Wait
        Ok "порт $Port открыт (через запрос прав администратора)"
      } catch { Warn "не удалось — открой порт вручную (см. README, раздел Брандмауэр)" }
    } else {
      Warn "порт не открыт — платы смогут подключаться только с этого компьютера"
    }
  }
} else {
  Say "ФАЙРВОЛ ПРОПУЩЕН (-NoFirewall)"
}

# -------------------------- 9. Запуск и проверка ----------------------------
$ip = "127.0.0.1"
try {
  $cand = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1
  if ($cand) { $ip = $cand.IPAddress }
} catch {}

if (-not $NoStart) {
  Say "ЗАПУСК И ПРОВЕРКА ЗДОРОВЬЯ"
  $listening = $false
  try {
    $listening = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
  } catch {}
  if ($listening) {
    Warn "порт $Port уже слушается — облако, похоже, уже запущено (не дублирую)"
  } else {
    & wscript.exe (Join-Path $Dir "cloud_bg.vbs")
    Ok "облако запущено скрытно (pythonw, без окна)"
  }
  $health = $false
  for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 1500
    try {
      $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri ("http://127.0.0.1:{0}/api/health" -f $Port)
      if ($r.StatusCode -eq 200) { $health = $true; break }
    } catch {}
  }
  if (-not $health) {
    # облако могло перескочить на соседний порт (например, если $Port занят
    # другой программой) — проверяем фактический порт из data\port.txt
    try {
      $pf = Join-Path $Dir "data\port.txt"
      if (Test-Path $pf) {
        $realPort = (Get-Content $pf -First 1).Trim()
        if ("$realPort" -ne "$Port") {
          $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri ("http://127.0.0.1:{0}/api/health" -f $realPort)
          if ($r.StatusCode -eq 200) { $health = $true; $Port = [int]$realPort; Warn "облако поднялось на соседнем свободном порту $Port (используй его в cloud_url платы)" }
        }
      }
    } catch {}
  }
  if ($health) { Ok ("health-check пройден: /api/health отвечает") }
  else { Warn "health-check не прошёл — запусти $Dir\start.bat и посмотри ошибку" }
} else {
  Say "ЗАПУСК ПРОПУЩЕН (-NoStart); вручную: $Dir\start.bat"
}

# -------------------------------- Итог --------------------------------------
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "   neuro12-cloud установлен — ОБЛАКО «МОЗГ-12» готово" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "   Веб-панель (стиль ChatGPT):  http://localhost:$Port"
Write-Host "   Из локальной сети:           http://$ip`:$Port"
Write-Host ""
Write-Host "   Пароль веб-панели:           $Password" -ForegroundColor Yellow
if (-not $needPw) { Write-Host "   (это прежний пароль; если забыт: install.ps1 -ResetPassword)" -ForegroundColor DarkGray }
Write-Host ""
Write-Host "   Токен платы (X-Mozg-Token):  $Token" -ForegroundColor Yellow
Write-Host ""
Write-Host "   ПОДКЛЮЧЕНИЕ ПЛАТЫ neuro_12:"
Write-Host "     1. На плате: чат (порт 7000) → «⚙ Настройки»."
Write-Host "     2. Впиши: облако = http://$ip`:$Port"
Write-Host "            токен = $Token"
Write-Host "     3. Включи «мозг-почта», «словари», «платформа»."
Write-Host ""
Write-Host "   Управление:"
Write-Host "     старт:      двойной клик $Dir\neuro12-cloud.exe (или start.bat)"
Write-Host "     стоп:       $Dir\stop.bat"
Write-Host "     автозапуск: Планировщик заданий → «neuro12-cloud»"
Write-Host "     удаление:   $Dir\uninstall.bat"
Write-Host "     журнал:     $Dir\data\log.txt"
Write-Host ""
Write-Host "   ЭКСКУРСИЯ ДЛЯ ПЛАТЫ: Настройки → «Учитель» → «Начать экскурс» —"
Write-Host "     учитель сам проведёт плату по всем функциям (12 уроков)."
Write-Host ""
Write-Host "   LLM не подключён: чат отвечает мозгом без нейросетей-гигантов."
Write-Host "   LLM-учитель (только обучение) — Настройки → «Учитель (LLM)»."
Write-Host ""
