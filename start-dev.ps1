<#
.SYNOPSIS
    一键启动本地开发全栈：基础设施(Docker) + API + Celery worker + Web。

.DESCRIPTION
    1. docker compose 启动 mysql / redis / agent-checkpoints（可用 -SkipInfra 跳过）。
    2. 调用 api/scripts/start_backend.ps1（默认 8001，幂等）。
    3. 调用 api/scripts/start_worker.ps1（Windows solo 池，幂等）。
    4. 若 3000 未响应，则在新窗口执行 npm run dev（可用 -SkipWeb 跳过）。

    子脚本用独立 powershell 进程调用，避免它们的 exit 提前结束本脚本。
    所有原生命令（docker / npm / powershell）统一走 Invoke-Native 包装：
    这些命令常把进度写到 stderr，在 Stop 模式下会被误判为终止错误。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File start-dev.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File start-dev.ps1 -SkipWeb
#>
[CmdletBinding()]
param(
    [switch]$SkipInfra,
    [switch]$SkipWeb
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ApiDir = Join-Path $Root "api"
$WebDir = Join-Path $Root "web"
$ApiScripts = Join-Path $ApiDir "scripts"

function Invoke-Native {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)
    # 原生命令的 stderr（进度、提示）不应被 Stop 升级为终止错误；这里局部放宽并返回退出码。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # 输出直接打到宿主，避免混进返回值（否则调用方拿到的是「输出 + 退出码」数组）。
        & $Action | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return $code
}

Write-Host "==> [1/4] 基础设施（mysql / redis / agent-checkpoints）" -ForegroundColor Cyan
if ($SkipInfra) {
    Write-Host "已跳过（-SkipInfra）。"
} else {
    $dockerExit = Invoke-Native {
        docker compose --project-directory $Root up -d mysql redis agent-checkpoints
    }
    if ($dockerExit -ne 0) {
        throw "docker compose 启动失败：请确认 Docker Desktop 已启动，且根目录 .env 已配置口令。"
    }
}

Write-Host "==> [2/4] 后端 API（默认 8001）" -ForegroundColor Cyan
$apiExit = Invoke-Native {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ApiScripts "start_backend.ps1")
}
if ($apiExit -ne 0) {
    throw "后端启动失败，请查看 api/logs/backend-8001.err.log"
}

Write-Host "==> [3/4] Celery worker" -ForegroundColor Cyan
$workerExit = Invoke-Native {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ApiScripts "start_worker.ps1")
}
if ($workerExit -ne 0) {
    throw "worker 启动失败，请查看 api/logs/worker.err.log"
}

Write-Host "==> [4/4] Web" -ForegroundColor Cyan
if ($SkipWeb) {
    Write-Host "已跳过（-SkipWeb）。"
} else {
    $webUp = $false
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        $webUp = $response.StatusCode -eq 200
    } catch {
        $webUp = $false
    }

    if ($webUp) {
        Write-Host "Web 已在 http://127.0.0.1:3000 运行，跳过启动。"
    } else {
        if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
            Write-Host "未找到 web/node_modules，先执行 npm install ..."
            Push-Location $WebDir
            try {
                $npmExit = Invoke-Native { npm install }
            } finally {
                Pop-Location
            }
            if ($npmExit -ne 0) {
                throw "npm install 失败，请检查 Node/npm 与网络。"
            }
        }
        Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory $WebDir -WindowStyle Normal
        Write-Host "已在新窗口启动 Web（npm run dev），首次编译需要几秒。"
    }
}

Write-Host ""
Write-Host "启动完成：" -ForegroundColor Green
Write-Host "  Web   http://127.0.0.1:3000"
Write-Host "  API   http://127.0.0.1:8001/health"
Write-Host "  待办  http://127.0.0.1:3000/alerts"
Write-Host "  设置  http://127.0.0.1:3000/settings"
