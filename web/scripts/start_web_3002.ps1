<#
.SYNOPSIS
    启动（或重启）本机 3002 端口的 Next.js 开发服务器，供本地联调使用。

.DESCRIPTION
    1. 使用 web 目录的 npm run dev -- -p 3002 启动 next dev，工作目录固定为 web，确保读取 web\.env.local。
    2. 启动前用首页探测端口：已在返回 200 则不重复启动；-Restart 时只停止命令行匹配 next / npm run dev 且归属 3002 端口的进程树。
    3. 日志写入 web\logs\web-3002.out.log 与 web-3002.err.log，PID 写入 web\logs\web-3002.pid。
    4. 脚本不包含也不读取任何账号、密码、密钥等凭据，前端环境变量仍由 web\.env.local 管理。
    5. 启动时设置 NO_PROXY/no_proxy 覆盖本机地址：本机存在 HTTP_PROXY 时，
       Next 服务端渲染回访 127.0.0.1 的后端会被代理转发，导致接口返回 401、
       页面降级为客户端渲染后跳转登录页。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File web\scripts\start_web_3002.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File web\scripts\start_web_3002.ps1 -Restart
#>
[CmdletBinding()]
param(
    [int]$Port = 3002,
    [string]$HostName = "127.0.0.1",
    [switch]$Restart,
    [int]$WaitSeconds = 120
)

$ErrorActionPreference = "Stop"

$WebDir = Split-Path -Parent $PSScriptRoot
$NpmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
$LogDir = Join-Path $WebDir "logs"
$OutLog = Join-Path $LogDir "web-$Port.out.log"
$ErrLog = Join-Path $LogDir "web-$Port.err.log"
$PidFile = Join-Path $LogDir "web-$Port.pid"
$HomeUrl = "http://${HostName}:$Port/"

if (-not $NpmCmd) {
    Write-Error "未找到 npm.cmd，请确认已安装 Node.js 并加入 PATH。"
    exit 1
}

if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
    Write-Error "未找到 web\node_modules，请先在 web 目录执行 npm install。"
    exit 1
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Test-Home {
    try {
        $response = Invoke-WebRequest -Uri $HomeUrl -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-ProcessInfo {
    param([int]$ProcessId)
    return (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue)
}

function Test-NextCommandLine {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    if ($CommandLine -match "npm(\.cmd)?\s+run\s+dev") { return $true }
    return ($CommandLine -match "next")
}

function Get-PortListenerId {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return 0
}

function Get-ManagedRoot {
    # 只认可由 next dev / npm run dev 派生、且与 3002 端口相关的进程树根节点，避免误停其它 node 程序。
    $candidates = @()
    $listenerId = Get-PortListenerId
    if ($listenerId -gt 0) { $candidates += $listenerId }

    $recordedId = 0
    if (Test-Path $PidFile) {
        $raw = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ([int]::TryParse(($raw -as [string]), [ref]$recordedId) -and $recordedId -gt 0) { $candidates += $recordedId }
    }

    foreach ($candidateId in ($candidates | Select-Object -Unique)) {
        $process = Get-ProcessInfo $candidateId
        if (-not $process) { continue }
        if (-not (Test-NextCommandLine $process.CommandLine)) { continue }
        # 向上追溯父进程链，找到整棵 next dev 进程树的根，便于一次性停止。
        $root = $process
        for ($i = 0; $i -lt 4; $i++) {
            $parentId = [int]$root.ParentProcessId
            if ($parentId -le 0) { break }
            $parent = Get-ProcessInfo $parentId
            if (-not $parent) { break }
            if (Test-NextCommandLine $parent.CommandLine) { $root = $parent } else { break }
        }
        return $root
    }
    return $null
}

$healthy = Test-Home
if ($healthy -and -not $Restart) {
    Write-Host "前端已在 $HomeUrl 正常运行（端口 $Port），无需重复启动。"
    if (Test-Path $PidFile) { Write-Host "记录 PID：$((Get-Content -LiteralPath $PidFile | Select-Object -First 1))" }
    Write-Host "日志：$OutLog"
    exit 0
}

if ($Restart) {
    $managed = Get-ManagedRoot
    if ($managed) {
        Write-Host "正在停止 next dev 进程树根 PID $($managed.ProcessId)：$($managed.CommandLine)"
        & taskkill.exe /PID $managed.ProcessId /T /F | Out-Host
        Start-Sleep -Seconds 2
    } elseif ($healthy) {
        Write-Warning "端口 $Port 上已有服务在响应，但不是 next dev 进程，未做停止。请人工确认后再重启。"
        exit 1
    }
    if (Test-Path $PidFile) { Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue }
}

if (Test-Home) {
    Write-Error "端口 $Port 已有服务返回 200，但非本脚本管理的 next dev。请勿直接覆盖，先确认占用来源（Get-NetTCPConnection -LocalPort $Port）。"
    exit 1
}

# 端口可能仍处于旧进程释放中，最多等待 15 秒。
$releaseDeadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $releaseDeadline) {
    if ((Get-PortListenerId) -eq 0) { break }
    Start-Sleep -Milliseconds 500
}

Write-Host "启动前端：npm run dev -- -p $Port（工作目录：$WebDir）"

# 本机回访地址必须绕过代理，否则服务端渲染请求后端会拿到 401。
$noProxyValue = "127.0.0.1,localhost,::1"
$env:NO_PROXY = $noProxyValue
$env:no_proxy = $noProxyValue

$process = Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/c", "npm run dev -- -p $Port") `
    -WorkingDirectory $WebDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "已提交启动（启动器 PID $($process.Id)），等待 $HomeUrl 返回 200 ..."

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 1000
    if ($process.HasExited) {
        Write-Error "进程已退出（ExitCode=$($process.ExitCode)），请查看错误日志：$ErrLog"
        exit 1
    }
    if (Test-Home) { $ready = $true; break }
}

if (-not $ready) {
    Write-Error "在 $WaitSeconds 秒内首页未返回 200：$HomeUrl。请查看日志：$ErrLog"
    exit 1
}

# next dev 实际监听进程是子进程，以端口占用进程作为真实 PID 记录，保证 -Restart 能精确停止。
$realPid = Get-PortListenerId
if ($realPid -le 0) { $realPid = $process.Id }
Set-Content -LiteralPath $PidFile -Value $realPid -Encoding ASCII

Write-Host "前端就绪：$HomeUrl （PID $realPid）"
Write-Host "标准输出日志：$OutLog"
Write-Host "错误日志：$ErrLog"
Write-Host "PID 文件：$PidFile"
exit 0
