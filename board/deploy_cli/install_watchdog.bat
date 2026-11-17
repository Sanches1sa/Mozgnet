@echo off
chcp 65001 >nul
rem ==========================================================================
rem  install_watchdog.bat — поставить СТОРОЖА neuro_12 на плату Arduino UNO Q
rem  прямо с Windows (ssh/scp встроены в Windows 10/11).
rem  Сторож живёт на плате: если приложение neuro_12 отключилось от порта 7000
rem  или зависло — сам перезапускает его (app stop + app start).
rem ==========================================================================
setlocal
set /p IP=IP-адрес платы (например 192.168.1.50): 
if "%IP%"=="" ( echo Не указан IP & pause & exit /b 1 )
set USERNAME_B=arduino
set /p USERNAME_B=Пользователь платы [arduino]: 
if "%USERNAME_B%"=="" set USERNAME_B=arduino
set T=%USERNAME_B%@%IP%

echo.
echo ==^> копирую скрипты сторожа на плату (спросит пароль платы)
ssh %T% "mkdir -p ~/neuro12_deploy_cli" || goto :err
scp "%~dp0neuro12_watchdog.sh" "%~dp0install_autostart.sh" %T%:neuro12_deploy_cli/ || goto :err

echo.
echo ==^> ставлю службу neuro12 (пароль платы спросят ещё раз — для sudo)
ssh -t %T% "sed -i 's/\r$//' ~/neuro12_deploy_cli/*.sh && sudo bash ~/neuro12_deploy_cli/install_autostart.sh watchdog && sudo systemctl restart neuro12 && sleep 2 && systemctl --no-pager status neuro12 | head -12" || goto :err

echo.
echo Готово. Сторож следит за приложением neuro_12.
echo Логи сторожа:  ssh %T% "journalctl -u neuro12 -n 50"
echo Остановить:    ssh -t %T% "sudo systemctl stop neuro12"
pause
exit /b 0

:err
echo.
echo ОШИБКА: проверь IP, пароль и что на плате включён SSH (настраивается в App Lab).
pause
exit /b 1
