<#
.SYNOPSIS
    Настройка бота VANTAGE: токен вводится один раз и не попадает
    ни в историю команд, ни в файлы, ни в репозиторий.

.DESCRIPTION
    Токен читается через Read-Host -AsSecureString: он не отображается
    при вводе и не сохраняется в истории PowerShell. Дальше он уходит
    либо в переменную окружения текущей сессии (для локальной проверки),
    либо в секреты Fly.io (для постоянной работы).

    Почему это важно. Токен бота — это полный доступ: читать все
    сообщения жителей и писать от имени бота. Токен, отправленный
    в мессенджер, письмо или чат, надо считать утёкшим и отзывать.

.EXAMPLE
    .\setup-bot.ps1 -Local
    Настроить только текущую сессию для локального запуска.

.EXAMPLE
    .\setup-bot.ps1 -Fly
    Записать секреты в Fly.io и развернуть бота.
#>

[CmdletBinding()]
param(
    [switch]$Local,
    [switch]$Fly
)

$ErrorActionPreference = 'Stop'

if (-not $Local -and -not $Fly) {
    Write-Host ""
    Write-Host "Укажите режим:" -ForegroundColor Yellow
    Write-Host "  .\setup-bot.ps1 -Local   локальная проверка на своей машине"
    Write-Host "  .\setup-bot.ps1 -Fly     развернуть на Fly.io навсегда"
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "=== Настройка бота VANTAGE ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Токен берётся у @BotFather: /newbot или /token" -ForegroundColor DarkGray
Write-Host "При вводе он не отображается — это нормально." -ForegroundColor DarkGray
Write-Host ""

# --- Токен ---------------------------------------------------------------- #
$secureToken = Read-Host -Prompt "Токен бота" -AsSecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken))

if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "Токен пустой. Отмена." -ForegroundColor Red
    exit 1
}
if ($token -notmatch '^\d+:[A-Za-z0-9_-]{30,}$') {
    Write-Host "Не похоже на токен Telegram (ожидается вид 1234567890:AAF...)." -ForegroundColor Red
    Write-Host "Проверьте, что скопировали целиком." -ForegroundColor Red
    exit 1
}

# --- Соль ----------------------------------------------------------------- #
# Генерируется автоматически: соль нужна, чтобы хеши отправителей нельзя
# было перебрать. Придуманная руками строка вроде "salt123" эту задачу
# не решает — пространство id Telegram маленькое.
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$salt = [Convert]::ToBase64String($bytes)
Write-Host "Соль для хеширования сгенерирована автоматически." -ForegroundColor DarkGray

# --- Подписчики ----------------------------------------------------------- #
Write-Host ""
Write-Host "Кому слать оповещения о новых объектах?" -ForegroundColor DarkGray
Write-Host "Свой chat_id узнать: написать @userinfobot" -ForegroundColor DarkGray
$subscribers = Read-Host -Prompt "chat_id через запятую (можно пропустить)"

# ========================================================================== #

if ($Local) {
    $env:VANTAGE_BOT_TOKEN = $token
    $env:VANTAGE_BOT_SALT = $salt
    $env:VANTAGE_BOT_SUBSCRIBERS = $subscribers
    $env:PYTHONUTF8 = "1"

    Write-Host ""
    Write-Host "Переменные заданы для ТЕКУЩЕЙ сессии PowerShell." -ForegroundColor Green
    Write-Host "Закроете окно — они исчезнут. Это правильно." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "Запуск бота:" -ForegroundColor Cyan
    Write-Host "  cd .." -ForegroundColor White
    Write-Host "  .venv\Scripts\python.exe -m vantage.bot" -ForegroundColor White
    Write-Host ""
    Write-Host "Дальше напишите боту /start в Telegram." -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

if ($Fly) {
    $flyCmd = Get-Command fly -ErrorAction SilentlyContinue
    if (-not $flyCmd) {
        Write-Host ""
        Write-Host "Fly.io CLI не установлен." -ForegroundColor Red
        Write-Host "Установите: iwr https://fly.io/install.ps1 -useb | iex" -ForegroundColor White
        Write-Host "Затем: fly auth login" -ForegroundColor White
        Write-Host ""
        exit 1
    }

    Write-Host ""
    Write-Host "Записываю секреты в Fly.io…" -ForegroundColor Cyan

    # Секреты передаются как аргументы одной команды и не пишутся в файлы.
    fly secrets set `
        "VANTAGE_BOT_TOKEN=$token" `
        "VANTAGE_BOT_SALT=$salt" `
        "VANTAGE_BOT_SUBSCRIBERS=$subscribers"

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Не удалось записать секреты. Проверьте fly auth login." -ForegroundColor Red
        exit 1
    }

    Write-Host "Секреты записаны." -ForegroundColor Green
    Write-Host ""
    Write-Host "Разворачиваю бота…" -ForegroundColor Cyan
    fly deploy

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Бот развёрнут." -ForegroundColor Green
        Write-Host "Логи:    fly logs" -ForegroundColor White
        Write-Host "Статус:  fly status" -ForegroundColor White
        Write-Host ""
        Write-Host "Проверьте: напишите боту /start в Telegram." -ForegroundColor DarkGray
    }
    exit $LASTEXITCODE
}
