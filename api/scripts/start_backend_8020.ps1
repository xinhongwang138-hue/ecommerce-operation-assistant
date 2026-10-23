<#
.SYNOPSIS
    启动（或重启）本机 8020 端口的 FastAPI 后端，供 web 开发服务器使用。

.DESCRIPTION
    1. 使用 api\.venv 内的 Python 与 app.main:app 启动 uvicorn，工作目录固定为 api 目录，确保读取 api\.env。
    2. 启动前用 /health 探测端口：已在健康运行则不重复启动；-Restart 时只停止由本脚本记录且命令行匹配 uvicorn app.main:app 的进程。
    3. 日志追加写入 api\logs\backend-8020.out.log 与 backend-8020.err.log，PID 写入 api\logs\backend-8020.pid。
    4. 脚本不包含也不读取任何账号、密码、密钥等凭据，后端凭据仍由 api\.env 管理。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File api\scripts\start_backend_8020.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File api\scripts\start_backend_8020.ps1 -Restart
#>
[CmdletBinding()]
param(
    [int]$Port = 8020,
    [string]$HostName = "127.0.0.1",
    [switch]$Restart,
    [int]$WaitSeconds = 40
)

$ErrorActionPreference = "Stop"

$ApiDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ApiDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ApiDir "logs"
$OutLog = Join-Path $LogDir "backend-$Port.out.log"
$ErrLog = Join-Path $LogDir "backend-$Port.err.log"
$PidFile = Join-Path $LogDir "backend-$Port.pid"
$HealthUrl = "http://${HostName}:$Port/health"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境解释器：$Python。请先在 api 目录创建 .venv 并安装依赖。"
    exit 1
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Test-Health {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-ManagedCommandLine {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    return ($CommandLine -match "uvicorn") -and ($CommandLine -match "app\.main:app") -and ($CommandLine -match "--port $Port")
}

function Get-PortListenerId {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return 0
}

function Get-ManagedProcess {
    # 优先使用 PID 文件记录；若记录已失效，再按端口监听进程的命令行做二次确认。
    $candidates = @()
    if (Test-Path $PidFile) {
        $raw = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        $recordedId = 0
        if ([int]::TryParse(($raw -as [string]), [ref]$recordedId)) { $candidates += $recordedId }
    }
    $listenerId = Get-PortListenerId
    if ($listenerId -gt 0) { $candidates += $listenerId }

    foreach ($candidateId in ($candidates | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $candidateId" -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        # 只认可本项目的 uvicorn 进程，避免误停同 PID 复用的其它程序。
        if ((Test-ManagedCommandLine -CommandLine $process.CommandLine) -or ($candidateId -eq $listenerId -and $process.CommandLine -match "uvicorn")) {
            return $process
        }
    }
    return $null
}

$healthy = Test-Health
if ($healthy -and -not $Restart) {
    Write-Host "后端已在 $HealthUrl 正常运行（端口 $Port），无需重复启动。"
    if (Test-Path $PidFile) { Write-Host "记录 PID：$((Get-Content -LiteralPath $PidFile | Select-Object -First 1))" }
    Write-Host "日志：$OutLog"
    exit 0
}

if ($Restart) {
    $managed = Get-ManagedProcess
    if ($managed) {
        Write-Host "正在停止由本脚本启动的进程 PID $($managed.ProcessId) ..."
        Stop-Process -Id $managed.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    } elseif ($healthy) {
        Write-Warning "端口 $Port 上已有服务在响应，但不是本脚本记录的进程，未做停止。请人工确认后再重启（例如 Docker 端口映射）。"
        exit 1
    }
    if (Test-Path $PidFile) { Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue }
}

if (Test-Health) {
    Write-Error "端口 $Port 已有服务在响应，但非本脚本管理。请勿直接覆盖，先确认占用来源（docker ps / Get-NetTCPConnection -LocalPort $Port）。"
    exit 1
}

$startArgs = @("-m", "uvicorn", "app.main:app", "--host", $HostName, "--port", $Port)
Write-Host "启动后端：$Python $($startArgs -join ' ')"
Write-Host "工作目录：$ApiDir"

# 本机回访地址必须绕过代理，否则对外暴露的接口会被代理转发而出现 401。
$noProxyValue = "127.0.0.1,localhost,::1"
$env:NO_PROXY = $noProxyValue
$env:no_proxy = $noProxyValue

$process = Start-Process -FilePath $Python `
    -ArgumentList $startArgs `
    -WorkingDirectory $ApiDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "已提交启动（启动器 PID $($process.Id)），等待 $HealthUrl 就绪 ..."

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 800
    if ($process.HasExited) {
        Write-Error "进程已退出（ExitCode=$($process.ExitCode)），请查看错误日志：$ErrLog"
        exit 1
    }
    if (Test-Health) { $ready = $true; break }
}

if (-not $ready) {
    Write-Error "在 $WaitSeconds 秒内未通过健康检查：$HealthUrl。请查看日志：$ErrLog"
    exit 1
}

# uvicorn 实际监听进程可能与启动器 PID 不同，这里以端口占用进程作为真实 PID 记录，保证 -Restart 能精确停止。
$realPid = Get-PortListenerId
if ($realPid -le 0) { $realPid = $process.Id }
Set-Content -LiteralPath $PidFile -Value $realPid -Encoding ASCII

Write-Host "后端就绪：$HealthUrl （PID $realPid）"
Write-Host "标准输出日志：$OutLog"
Write-Host "错误日志：$ErrLog"
Write-Host "PID 文件：$PidFile"
exit 0
