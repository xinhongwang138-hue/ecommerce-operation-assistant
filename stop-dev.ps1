<#
.SYNOPSIS
    停止本地开发服务：API 与 Celery worker。

.DESCRIPTION
    1. 按 api/logs 下的 PID 文件停止 API（backend-8001.pid）与 worker（worker.pid），并结束整棵进程树。
    2. 加 -Infra 时额外执行 docker compose down 停止基础设施容器。
    3. Web 开发服务器需在它自己的窗口按 Ctrl+C 停止（npm 进程不归本脚本管理）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File stop-dev.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File stop-dev.ps1 -Infra
#>
[CmdletBinding()]
param(
    [switch]$Infra
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$LogDir = Join-Path $Root "api\logs"

function Invoke-Native {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)
    # 原生命令的 stderr（taskkill / docker 提示）不应被 Stop 升级为终止错误。
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

foreach ($name in @("backend-8001", "worker")) {
    $pidFile = Join-Path $LogDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "$name：无 PID 文件，跳过。"
        continue
    }
    $targetPid = (Get-Content -LiteralPath $pidFile -First 1).Trim()
    if (-not $targetPid) {
        Write-Host "$name：PID 文件为空，跳过。"
        continue
    }
    Write-Host "停止 $name（PID $targetPid）..."
    # 进程可能已退出，taskkill 报“找不到进程”属正常。
    Invoke-Native { taskkill /PID $targetPid /T /F } | Out-Null
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "提示：Web 开发服务器请在它自己的窗口按 Ctrl+C 停止。"

if ($Infra) {
    Write-Host "停止基础设施容器 ..."
    $downExit = Invoke-Native { docker compose --project-directory $Root down }
    if ($downExit -ne 0) {
        Write-Warning "docker compose down 返回非零，请手动检查。"
    }
}
