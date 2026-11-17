# ============================================================================
#  install-github.ps1 — mozgnet: репозиторий на GitHub + автосборка APK
#
#  Запуск (PowerShell в папке mozgnet):
#      powershell -ExecutionPolicy Bypass -File install-github.ps1
#  Ключи:
#      -Public              репозиторий открытый (APK скачивается без входа)
#      -Name mozgnet        имя репозитория
#      -Cloud C:\путь\neuro12-cloud   положить в приложение ЭТО облако
#                           (по умолчанию — облако, что уже лежит в проекте)
#
#  Что делает: ставит Git и GitHub CLI (winget), входит в GitHub (окно
#  браузера), создаёт репозиторий, отправляет проект. GitHub сам собирает APK
#  (вкладка Actions, ~10–20 мин) и выкладывает в Releases. Скрипт ждёт сборку
#  и скачивает APK сюда же. Повторный запуск — отправить изменения и собрать
#  новую версию.
# ============================================================================
param(
    [switch]$Public,
    [string]$Name = "mozgnet",
    [string]$Cloud = ""
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Say($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t)  { Write-Host "  [OK] $t" -ForegroundColor Green }
function Die($t) { Write-Host "  [XX] $t" -ForegroundColor Red; exit 1 }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Say "ПРОГРАММЫ"
foreach ($p in @(@("git", "Git.Git"), @("gh", "GitHub.cli"))) {
    if (-not (Have $p[0])) {
        if (-not (Have "winget")) { Die "нет $($p[0]) и winget — поставь вручную: git-scm.com и cli.github.com" }
        Write-Host "  ставлю $($p[1])…"
        winget install --id $p[1] -e --accept-source-agreements --accept-package-agreements | Out-Null
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not (Have $p[0])) { Die "$($p[0]) не появился в PATH — открой новое окно PowerShell и запусти снова" }
    }
    Ok $p[0]
}

Say "ВХОД В GITHUB"
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Откроется браузер: войди в GitHub и подтверди код."
    gh auth login --web --git-protocol https --scopes "repo,workflow"
    if ($LASTEXITCODE -ne 0) { Die "вход не выполнен" }
}
$me = (gh api user --jq .login).Trim()
Ok "вошёл как $me"

if ($Cloud) {
    Say "ОБЛАКО ДЛЯ ПРИЛОЖЕНИЯ: $Cloud"
    if (-not (Test-Path (Join-Path $Cloud "server\app.py"))) { Die "в $Cloud нет server\app.py" }
    $dst = "cloud"
    foreach ($x in @("server", "web", "docs")) { if (Test-Path "$dst\$x") { Remove-Item "$dst\$x" -Recurse -Force } }
    New-Item -ItemType Directory $dst -Force | Out-Null
    foreach ($x in @("server", "web", "docs", "VERSION", "README.md", "AGENT-GUIDE.md")) {
        $s = Join-Path $Cloud $x
        if (Test-Path $s) { Copy-Item $s $dst -Recurse -Force }
    }
    Get-ChildItem $dst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    Ok "облако v$((Get-Content "$dst\VERSION").Trim())"
}

Say "РЕПОЗИТОРИЙ $me/$Name"
if (-not (Test-Path ".git")) {
    git init -q -b main
    git config user.name  2>$null | Out-Null; if ($LASTEXITCODE -ne 0) { git config user.name $me }
    git config user.email 2>$null | Out-Null; if ($LASTEXITCODE -ne 0) { git config user.email "$me@users.noreply.github.com" }
}
git add -A
git commit -q -m "mozgnet: $(Get-Date -Format 'yyyy-MM-dd HH:mm')" 2>$null
gh repo view "$me/$Name" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $vis = if ($Public) { "--public" } else { "--private" }
    gh repo create "$me/$Name" $vis --source . --remote origin --push --description "mozgnet — облако Мозг-12 в телефоне"
    if ($LASTEXITCODE -ne 0) { Die "не создал репозиторий" }
    Ok "создан $(if ($Public) {'открытый'} else {'закрытый'}) репозиторий"
} else {
    if (-not (git remote 2>$null | Select-String -Quiet "origin")) { git remote add origin "https://github.com/$me/$Name.git" }
    git push -u origin main
    if ($LASTEXITCODE -ne 0) { Die "push не прошёл" }
    Ok "изменения отправлены"
}

Say "СБОРКА APK НА GITHUB (10–20 минут)"
Start-Sleep -Seconds 8
$run = (gh run list -R "$me/$Name" --workflow build.yml --limit 1 --json databaseId --jq ".[0].databaseId")
if (-not $run) { Die "сборка не началась — открой https://github.com/$me/$Name/actions" }
Write-Host "  смотреть в браузере: https://github.com/$me/$Name/actions/runs/$run"
gh run watch $run -R "$me/$Name" --exit-status
if ($LASTEXITCODE -ne 0) { Die "сборка упала — лог: https://github.com/$me/$Name/actions/runs/$run (пришли его мне)" }
Ok "APK собран"

Say "APK"
gh release download -R "$me/$Name" --pattern "*.apk" --clobber
$apk = Get-ChildItem -Filter "mozgnet-*.apk" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($apk) { Ok "скачан: $($apk.FullName)" }
Write-Host "`n  На телефоне: https://github.com/$me/$Name/releases/latest → mozgnet-….apk → установить." -ForegroundColor Green
if (-not $Public) { Write-Host "  Репозиторий закрытый — на телефоне войди в GitHub или перекинь APK с компьютера (кабель, Telegram, МАКС)." }
